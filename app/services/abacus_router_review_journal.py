"""Two one-shot subscription reviews for the audited retained Capital episode.

This is an explicit operator API, not a sender or a spend bypass.
It never initializes USD history, purchases credits, claims a child, grants QA,
or changes production. Original financial and OAuth records remain untouched.
The operator must bind the existing subscription entitlement to the real key;
an evidence hash records that assertion, not an account lookup or cash meter.

Each purpose can be reserved once for this original root, including after an
unknown provider outcome. A reservation is permission for one send only when
its transaction acknowledgement returns. Settlement observes a real HTTPX
response and grants no resend. All three replay keys must survive permanently;
loss or rollback of all three requires external recovery, never automatic init.
"""
from datetime import datetime, timezone
import hashlib
import json
import re

from redis.exceptions import WatchError

from app.services import production_connection_continuity as continuity
from app.services.abacus_router_adapter import (
    ENDPOINT, MODEL, OPERATION, PreparedRouterRequest, inspect_router_request,
    observe_router_response,
)
from app.services.production_spend import LEDGER_KEY, SpendBlocked
from app.services.production_spend_runtime import _request_fingerprint


_PREFIX = 'youtube_studio:{production_spend}:included_router_review:v1:' + continuity.ROOT_ID
STATE_KEY = _PREFIX + ':state'
JOURNAL_KEY = _PREFIX + ':journal'
ANCHOR_KEY = _PREFIX + ':commissioned'
PURPOSES = ('immutable_story_review', 'retained_visual_review')
_MAX_BYTES = 256 * 1024
_MAX_RECONFIRMATIONS = 12
_SHA = re.compile(r'[0-9a-f]{64}')
_STATE_FIELDS = {'policy', 'slots', 'updated_at'}
_RECONFIRMABLE_FIELDS = {'valid_from', 'valid_until', 'entitlement_evidence_sha256'}
_POLICY_FIELDS = {
    'version', 'kind', 'endpoint', 'model', 'original_task_id', 'leaf_task_id',
    'channel_id', 'profile_revision', 'old_connection_id', 'current_connection_id',
    'continuity_sha256', 'credential_sha256', 'entitlement_evidence_sha256',
    'entitlement_source', 'valid_from', 'valid_until',
    'historical_extra_cash_micro', 'new_cash_allowance_micro',
}


class RouterReviewBlocked(SpendBlocked):
    """Fixed local reason only; no source text, response body or credentials."""


def _require(condition, reason='router_review_store_invalid'):
    if not condition:
        raise RouterReviewBlocked(reason)


def _json(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(',', ':'), allow_nan=False)
    _require(len(raw.encode('utf-8')) <= _MAX_BYTES)
    return raw


def _object(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            _require(key not in out)
            out[key] = value
        return out
    _require(type(raw) is str and len(raw.encode('utf-8')) <= _MAX_BYTES)
    result = json.loads(raw, object_pairs_hook=pairs,
                        parse_constant=lambda _: _require(False))
    _require(type(result) is dict)
    return result


def _hash(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _digest(value):
    _require(type(value) is str and _SHA.fullmatch(value) is not None)


def _date(value):
    _require(type(value) is str and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', value))
    return datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)


def _policy(value):
    _require(type(value) is dict and set(value) == _POLICY_FIELDS, 'router_review_policy_invalid')
    expected = {'version': 1, 'kind': 'existing_subscription_retained_review',
                'endpoint': ENDPOINT, 'model': MODEL, 'original_task_id': continuity.ROOT_ID,
                'leaf_task_id': continuity.LEAF_ID, 'channel_id': continuity.CHANNEL_ID,
                'entitlement_source': 'owner_subscription_and_official_router_api_terms',
                'historical_extra_cash_micro': None, 'new_cash_allowance_micro': 0}
    _require(all(type(value[k]) is type(v) and value[k] == v for k, v in expected.items()),
             'router_review_policy_invalid')
    for name in ('credential_sha256', 'continuity_sha256', 'entitlement_evidence_sha256'):
        _digest(value[name])
    for name in ('profile_revision', 'old_connection_id', 'current_connection_id'):
        _require(type(value[name]) is str and re.fullmatch(r'[A-Za-z0-9_-]{8,128}', value[name]))
    _require(value['old_connection_id'] != value['current_connection_id'])
    seconds = (_date(value['valid_until']) - _date(value['valid_from'])).total_seconds()
    _require(0 < seconds <= 86400, 'router_review_policy_invalid')
    return value


def _receipt(policy, purpose, slot):
    return {'policy_sha256': _hash(policy), 'purpose': purpose,
            'request_sha256': slot['request_sha256'],
            'root_request_fingerprint': slot['root_request_fingerprint'],
            'reserved_at': slot['reserved_at']}


def _same_bindings(previous, current):
    _require(all(previous[name] == current[name]
                 for name in _POLICY_FIELDS - _RECONFIRMABLE_FIELDS),
             'router_review_reconfirmation_binding_changed')


def _history(state):
    """Validate flat snapshots and their complete, reconstructable state chain."""
    if 'history' not in state:
        return _date(state['policy']['valid_from'])
    history = state['history']
    _require(type(history) is list and 1 <= len(history) <= _MAX_RECONFIRMATIONS,
             'router_review_history_invalid')
    previous_reconfirmed = None
    for index, entry in enumerate(history):
        _require(type(entry) is dict and set(entry) == {
            'previous_state', 'previous_state_sha256', 'reconfirmed_at'},
            'router_review_history_invalid')
        previous = entry['previous_state']
        _require(type(previous) is dict and set(previous) == _STATE_FIELDS
                 and type(previous['slots']) is dict and previous['slots'] == {},
                 'router_review_history_invalid')
        policy = _policy(previous['policy'])
        _same_bindings(policy, state['policy'])
        stamp = _date(previous['updated_at'])
        _require(_date(policy['valid_from']) <= stamp < _date(policy['valid_until'])
                 and (previous_reconfirmed is None or stamp == previous_reconfirmed),
                 'router_review_history_invalid')
        reconstructed = dict(previous)
        if index:
            reconstructed['history'] = history[:index]
        _digest(entry['previous_state_sha256'])
        _require(entry['previous_state_sha256'] == _hash(reconstructed),
                 'router_review_history_mismatch')
        successor = history[index + 1]['previous_state'] if index + 1 < len(history) else state
        successor_policy = _policy(successor['policy'])
        reconfirmed = _date(entry['reconfirmed_at'])
        _require(_date(policy['valid_until']) <= _date(successor_policy['valid_from'])
                 <= reconfirmed < _date(successor_policy['valid_until'])
                 and reconfirmed <= _date(successor['updated_at']),
                 'router_review_history_invalid')
        previous_reconfirmed = reconfirmed
    _require(state['slots'] or _date(state['updated_at']) == previous_reconfirmed,
             'router_review_history_invalid')
    return previous_reconfirmed


def _journal(state):
    expected = {'policy_sha256': _hash(state['policy']), 'state_sha256': _hash(state)}
    for purpose, slot in state['slots'].items():
        expected['reservation:' + purpose] = _json(_receipt(state['policy'], purpose, slot))
        if slot['response'] is not None:
            expected['response:' + purpose] = _json(slot['response'])
    return expected


class RouterReviewJournal:
    """Explicit operator API using the existing decode_responses Redis client.

    No method sends network requests, renews a provider entitlement, returns a cached
    send permit, or reads/relabels the unknown historical cash foundation.
    Application runtime must additionally enforce its zero-cash mode and only
    call these purposes from the complete immutable story/visual review paths.
    """

    def __init__(self, client, *, clock=None, successor=None, completion_plan=None):
        _require(successor is None or completion_plan is None,
                 'router_review_multiple_authorizations')
        self.client = client
        self._successor = successor
        self._completion_plan = completion_plan
        if completion_plan is not None:
            from app.services.retained_review_completion_plan import selected_keys
            selected_keys(completion_plan, 'story')
        if successor is not None:
            from app.services.retained_review_credential_successor import selected_keys
            selected_keys(successor, 'story')
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    @property
    def keys(self):
        if self._completion_plan is not None:
            from app.services.retained_review_completion_plan import selected_keys
            return selected_keys(self._completion_plan, 'story')
        if self._successor is None:
            return STATE_KEY, JOURNAL_KEY, ANCHOR_KEY
        from app.services.retained_review_credential_successor import selected_keys
        return selected_keys(self._successor, 'story')

    @property
    def state_key(self): return self.keys[0]

    @property
    def journal_key(self): return self.keys[1]

    @property
    def anchor_key(self): return self.keys[2]

    def _admit(self, pipe, *, reserve=False):
        if self._completion_plan is not None:
            from app.services.retained_review_completion_plan import guard_mutation
            return guard_mutation(pipe, self._completion_plan, 'story', reserve=reserve)
        from app.services.retained_review_credential_successor import guard_mutation
        guard_mutation(pipe, self._successor, 'story', reserve=reserve)

    def _now(self):
        now = self.clock()
        _require(isinstance(now, datetime) and now.tzinfo is not None
                 and now.utcoffset() is not None, 'router_review_clock_invalid')
        return now.astimezone(timezone.utc).replace(microsecond=0)

    def _fresh(self, pipe, policy, now):
        _require(_date(policy['valid_from']) <= now < _date(policy['valid_until']),
                 'router_review_entitlement_expired')
        record = continuity._derive(pipe, policy['profile_revision'])
        _require(_hash(record) == policy['continuity_sha256']
                 and all(record[name] == policy[name] for name in (
                     'old_connection_id', 'current_connection_id')),
                 'router_review_source_changed')

    def _read(self, pipe):
        state = self._read_records(pipe)
        if self._completion_plan is not None:
            from app.services.retained_review_completion_plan import guard_selected
            guard_selected(pipe, self._completion_plan, 'story', state)
        elif self._successor is not None:
            from app.services.retained_review_credential_successor import guard_selected
            guard_selected(pipe, self._successor, 'story', state)
        return state

    def _read_records(self, pipe):
        lifetimes = [pipe.pttl(key) for key in (self.state_key, self.journal_key, self.anchor_key)]
        _require(all(type(ttl) is int and ttl == -1 for ttl in lifetimes),
                 'router_review_not_initialized_or_durable')
        state = _object(pipe.get(self.state_key))
        _require(set(state) in (_STATE_FIELDS, _STATE_FIELDS | {'history'}))
        policy = _policy(state['policy'])
        stamp = _date(state['updated_at'])
        _require(stamp >= _date(policy['valid_from']))
        _require(type(state['slots']) is dict and set(state['slots']) <= set(PURPOSES))
        _require(state['slots'] or stamp < _date(policy['valid_until']))
        activated = _history(state)
        seen = set()
        for purpose, slot in state['slots'].items():
            _require(type(slot) is dict and set(slot) == {
                'request_sha256', 'root_request_fingerprint', 'reserved_at', 'response'})
            _digest(slot['request_sha256'])
            _digest(slot['root_request_fingerprint'])
            _require(slot['request_sha256'] not in seen)
            seen.add(slot['request_sha256'])
            reserved = _date(slot['reserved_at'])
            _require(activated <= reserved < _date(policy['valid_until'])
                     and reserved <= stamp)
            response = slot['response']
            if response is not None:
                _require(type(response) is dict and set(response) == {
                    'reservation_sha256', 'evidence', 'observed_at'})
                _require(response['reservation_sha256'] == _hash(_receipt(policy, purpose, slot))
                         and reserved <= _date(response['observed_at']) <= stamp)
                evidence = response['evidence']
                _require(type(evidence) is dict
                         and evidence.get('request_sha256') == slot['request_sha256']
                         and evidence.get('credential_sha256') == policy['credential_sha256']
                         and evidence.get('requested_model') == MODEL
                         and evidence.get('endpoint') == ENDPOINT
                         and evidence.get('underlying_model_verified') is False)
                _require(evidence.get('response_proof_sha256') == _hash({
                    k: v for k, v in evidence.items() if k != 'response_proof_sha256'}))
        _require(pipe.hlen(self.journal_key) <= 6
                 and pipe.hgetall(self.journal_key) == _journal(state)
                 and pipe.get(self.anchor_key) == _hash(state), 'router_review_replay_evidence_mismatch')
        return state

    def _commit(self, pipe, state, old_journal):
        if self._completion_plan is not None:
            from app.services.retained_review_completion_plan import commit_selected
            return commit_selected(pipe, self._completion_plan, 'story', state, old_journal)
        if self._successor is not None:
            from app.services.retained_review_credential_successor import commit_selected
            return commit_selected(pipe, self._successor, 'story', state, old_journal)
        journal = _journal(state)
        pipe.multi()
        pipe.set(self.state_key, _json(state))
        pipe.hset(self.journal_key, mapping=journal)
        pipe.set(self.anchor_key, _hash(state))
        ack = pipe.execute()
        added = len(set(journal) - set(old_journal))
        _require(type(ack) is list and len(ack) == 3 and ack[0] is True
                 and type(ack[1]) is int and ack[1] == added and ack[2] is True,
                 'router_review_commit_uncertain')

    def _operate(self, action):
        for _ in range(8):
            try:
                with self.client.pipeline() as pipe:
                    pipe.watch(self.state_key, self.journal_key, self.anchor_key)
                    return action(pipe, self._now())
            except WatchError:
                continue  # EXEC did not run; there has been no provider send.
            except RouterReviewBlocked:
                raise
            except Exception:
                raise RouterReviewBlocked('router_review_state_or_outcome_unverified') from None
        raise RouterReviewBlocked('router_review_store_contention')

    def commission(self, policy):
        """One explicit empty-store creation; existing evidence is never reset."""
        try:
            checked = _policy(_object(_json(policy)))
        except RouterReviewBlocked:
            raise
        except Exception:
            raise RouterReviewBlocked('router_review_policy_invalid') from None
        def action(pipe, now):
            self._admit(pipe, reserve=False)
            _require(self._successor is None and self._completion_plan is None,
                     'router_review_successor_invalid')
            _require(pipe.exists(self.state_key, self.journal_key, self.anchor_key) == 0,
                     'router_review_already_commissioned_or_partial')
            self._fresh(pipe, checked, now)
            state = {'policy': checked, 'slots': {}, 'updated_at': now.strftime('%Y-%m-%dT%H:%M:%SZ')}
            self._commit(pipe, state, {})
            return {'policy_sha256': _hash(checked), 'qa_approved': False, 'publish_eligible': False}
        return self._operate(action)

    def reconfirm_unused(self, policy):
        """Explicitly replace an expired, entirely unused review window only.

        Preserve every prior policy/state, the original two purposes and all
        funding/source bindings. A lost acknowledgement is never retried as a
        write; the resulting active window blocks another reconfirmation.
        """
        try:
            checked = _policy(_object(_json(policy)))
        except RouterReviewBlocked:
            raise
        except Exception:
            raise RouterReviewBlocked('router_review_policy_invalid') from None
        expected_previous_sha256 = None

        def action(pipe, now):
            self._admit(pipe, reserve=False)
            _require(self._successor is None and self._completion_plan is None,
                     'router_review_successor_invalid')
            nonlocal expected_previous_sha256
            previous = self._read(pipe)
            previous_sha256 = _hash(previous)
            if expected_previous_sha256 is None:
                expected_previous_sha256 = previous_sha256
            _require(previous_sha256 == expected_previous_sha256,
                     'router_review_reconfirmation_state_changed')
            _require(previous['slots'] == {}, 'router_review_reconfirmation_already_used')
            _require(now >= _date(previous['updated_at']), 'router_review_clock_invalid')
            _require(now >= _date(previous['policy']['valid_until']),
                     'router_review_reconfirmation_still_active')
            _same_bindings(previous['policy'], checked)
            _require(_date(checked['valid_from']) >= _date(previous['policy']['valid_until']),
                     'router_review_reconfirmation_window_invalid')
            history = previous.get('history', [])
            _require(len(history) < _MAX_RECONFIRMATIONS, 'router_review_history_limit')
            self._fresh(pipe, checked, now)
            stamp = now.strftime('%Y-%m-%dT%H:%M:%SZ')
            state = {'policy': checked, 'slots': {}, 'updated_at': stamp,
                     'history': [*history, {
                         'previous_state': {name: previous[name] for name in _STATE_FIELDS},
                         'previous_state_sha256': previous_sha256, 'reconfirmed_at': stamp}]}
            _history(state)
            self._commit(pipe, state, _journal(previous))
            return {'policy_sha256': _hash(checked), 'previous_state_sha256': previous_sha256,
                    'state_sha256': _hash(state), 'reconfirmation_count': len(state['history']),
                    'qa_approved': False, 'publish_eligible': False}

        return self._operate(action)

    @staticmethod
    def _request(prepared, policy):
        _require(type(prepared) is PreparedRouterRequest, 'router_review_request_invalid')
        _require(inspect_router_request(ENDPOINT, prepared.wire_kwargs()) == prepared
                 and prepared.credential_sha256 == policy['credential_sha256'],
                 'router_review_request_invalid')

    def reserve(self, purpose, prepared):
        """Only a successful first return permits one send of the frozen request."""
        def action(pipe, now):
            self._admit(pipe, reserve=True)
            state = self._read(pipe)
            policy = state['policy']
            _require(type(purpose) is str and purpose in PURPOSES, 'router_review_purpose_invalid')
            _require(purpose not in state['slots'], 'router_review_request_already_reserved')
            self._request(prepared, policy)
            fingerprint = _request_fingerprint({'lineage_id': continuity.ROOT_ID},
                                               'abacus', OPERATION, prepared.payload)
            _require(all(slot['request_sha256'] != prepared.request_sha256 for slot in state['slots'].values()),
                     'router_review_request_already_reserved')
            # Inspect permanent existing request receipts without opening or
            # reconciling a USD foundation. Absent history still means unknown.
            pipe.watch(LEDGER_KEY)
            field = hashlib.sha256(fingerprint.encode('utf-8')).hexdigest()
            _require(not pipe.hexists(LEDGER_KEY, 'request:' + field)
                     and not pipe.hexists(LEDGER_KEY, 'native_request:' + field),
                     'router_review_cross_mode_request_conflict')
            _require(now >= _date(state['updated_at']), 'router_review_clock_invalid')
            self._fresh(pipe, policy, now)
            old_journal = _journal(state)
            state['updated_at'] = now.strftime('%Y-%m-%dT%H:%M:%SZ')
            slot = {'request_sha256': prepared.request_sha256,
                    'root_request_fingerprint': fingerprint,
                    'reserved_at': state['updated_at'], 'response': None}
            state['slots'][purpose] = slot
            self._commit(pipe, state, old_journal)
            receipt = _receipt(policy, purpose, slot)
            return {**receipt, 'reservation_sha256': _hash(receipt)}
        return self._operate(action)

    def settle(self, purpose, prepared, response):
        """Record a verified result even after expiry/cancellation; never resend."""
        def action(pipe, now):
            self._admit(pipe, reserve=False)
            state = self._read(pipe)
            _require(type(purpose) is str and purpose in state['slots'], 'router_review_reservation_missing')
            policy, slot = state['policy'], state['slots'][purpose]
            self._request(prepared, policy)
            _require(slot['request_sha256'] == prepared.request_sha256,
                     'router_review_request_invalid')
            _require(now >= _date(state['updated_at']), 'router_review_clock_invalid')
            observed = observe_router_response(prepared, response)
            if slot['response'] is not None:
                _require(_json(slot['response']['evidence']) == _json(observed.evidence),
                         'router_review_settlement_conflict')
                # A lost settlement acknowledgement can recover the exact
                # observed result, without rewriting timestamps or permitting
                # another send. WATCH also protects this readback from drift.
                pipe.multi()
                pipe.ping()
                ack = pipe.execute()
                _require(type(ack) is list and len(ack) == 1 and ack[0] is True,
                         'router_review_commit_uncertain')
                return observed
            old_journal = _journal(state)
            state['updated_at'] = now.strftime('%Y-%m-%dT%H:%M:%SZ')
            slot['response'] = {'reservation_sha256': _hash(_receipt(policy, purpose, slot)),
                                'observed_at': state['updated_at'], 'evidence': observed.evidence}
            self._commit(pipe, state, old_journal)
            return observed
        return self._operate(action)
