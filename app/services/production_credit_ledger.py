"""Atomic native ElevenLabs credit persistence; explicit opt-in, no auto-init.

The state and an independent non-expiring journal commit together. Every read
checks their full correspondence, so losing policy/state, an intent, a request
index or a settlement cannot turn spent or uncertain capacity into new credit.
No request method initializes or renews anything. initialize() is an explicit
operator action after balance and outstanding-use reconciliation. Complete loss
of both durable Redis keys requires external recovery, never automatic restart.

This module does not send requests, authenticate account evidence, inspect jobs,
grant publication or convert credits into USD. The trusted runtime adapter must
derive the actual credential, original root and immutable native request before
reserve(), and validate the actual provider meter before settle(). Only an
acknowledged reserve return may precede one provider send.
"""
from datetime import datetime, timezone
import hashlib
import json

from redis.exceptions import WatchError

from app.services.production_spend import LEDGER_KEY, SpendBlocked, SpendLedger
from app.services.production_credit_funding import (
    credit_funding_summary, initial_credit_state, reserve_credit_intent,
    settle_credit_intent, validate_credit_policy, validate_credit_history,
)


STATE_KEY = 'youtube_studio:{production_spend}:native_credit:v1:elevenlabs:state'
JOURNAL_KEY = 'youtube_studio:{production_spend}:native_credit:v1:elevenlabs:journal'
MODE_FIELD = 'native_credit_policy:elevenlabs'
_MAX_BYTES = 8 * 1024 * 1024
_MAX_FIELDS = 4100


def _require(condition, reason):
    if not condition:
        raise SpendBlocked(reason)


def _json(value):
    try:
        raw = json.dumps(value, sort_keys=True, ensure_ascii=False,
                         separators=(',', ':'), allow_nan=False)
        _require(len(raw.encode('utf-8')) <= _MAX_BYTES, 'credit_store_size_limit')
        return raw
    except SpendBlocked:
        raise
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise SpendBlocked('credit_store_invalid') from None


def _object(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, 'credit_store_invalid')
            result[key] = value
        return result

    def nonfinite(value):
        raise SpendBlocked('credit_store_invalid')

    try:
        _require(type(raw) is str and len(raw.encode('utf-8')) <= _MAX_BYTES,
                 'credit_store_invalid')
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite)
        _require(type(value) is dict, 'credit_store_invalid')
        return value
    except SpendBlocked:
        raise
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise SpendBlocked('credit_store_invalid') from None


def _hash(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def native_foundation_markers(pipe):
    """Bounded complete scan; an uninspected tail can never mean no receipts."""
    markers, cursor = {}, 0
    for _ in range(128):
        cursor, page = pipe.hscan(LEDGER_KEY, cursor=cursor, match='native_request:*', count=256)
        _require(type(cursor) is int and cursor >= 0 and type(page) is dict,
                 'credit_foundation_invalid')
        for key, raw in page.items():
            _require(type(key) is str and key.startswith('native_request:')
                     and (key not in markers or markers[key] == raw), 'credit_foundation_invalid')
            markers[key] = raw
        _require(len(markers) <= 1024, 'credit_foundation_history_limit')
        if cursor == 0:
            return markers
    raise SpendBlocked('credit_foundation_history_limit')


def _journal(policy, state, genesis):
    """The complete expected immutable receipts plus current-state commitment."""
    expected = {'genesis': _json(genesis), 'policy_sha256': _hash(policy),
                'state_sha256': _hash(state), 'revision': str(state['revision'])}
    for intent_id, entry in state['intents'].items():
        receipt = entry['reservation']
        expected['reservation:' + intent_id] = _json(receipt)
        expected['request:' + receipt['request_identity_sha256']] = intent_id
        if entry['settlement'] is not None:
            expected['settlement:' + intent_id] = _json(entry['settlement'])
    for root, binding in state['roots'].items():
        expected['root:' + root] = _json(binding)
    _require(len(expected) <= _MAX_FIELDS and
             sum(len(k.encode()) + len(v.encode()) for k, v in expected.items()) <= _MAX_BYTES,
             'credit_store_size_limit')
    return expected


class CreditLedger:
    """A supplied native Redis client with decode_responses=True is required."""

    def __init__(self, client, *, clock=None, foundation=None):
        _require(foundation is None or isinstance(foundation, SpendLedger)
                 and foundation.client is client, 'credit_foundation_invalid')
        self.client = client
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.foundation = foundation

    def _watch(self, pipe):
        from app.services.production_cash_disabled import ANCHOR_KEY
        from app.services.production_credit_periods import HISTORY_KEY, HISTORY_ANCHOR
        pipe.watch(STATE_KEY, JOURNAL_KEY, LEDGER_KEY, ANCHOR_KEY, HISTORY_KEY, HISTORY_ANCHOR)

    def _foundation_base(self, pipe, now):
        if self.foundation is None:
            from app.services.production_cash_disabled import present
            _require(not present(pipe), 'credit_foundation_required')
            _require(not pipe.hexists(LEDGER_KEY, MODE_FIELD) and not native_foundation_markers(pipe),
                     'credit_foundation_required')
            return
        self.foundation.validate_native_credit_foundation(pipe, now=now)
        lifetime = pipe.pttl(LEDGER_KEY)
        _require(type(lifetime) is int and lifetime == -1, 'credit_foundation_expiring')

    @staticmethod
    def _mode(policy):
        return {'version': 1, 'provider': 'elevenlabs', 'policy_sha256': _hash(policy),
                'state_key': STATE_KEY, 'journal_key': JOURNAL_KEY}

    @staticmethod
    def _context(intent):
        return {'channel_id': intent['channel_id'], 'lineage_id': intent['root_lineage_id'],
                'connection_id': intent['source_connection_id'], 'kind': 'shorts'}

    @staticmethod
    def _request_field(intent):
        # The trusted runtime supplies the existing USD request fingerprint.
        # Its second hash is the pre-existing SpendLedger request field format.
        return hashlib.sha256(intent['request_sha256'].encode()).hexdigest()

    def _production_marker(self, policy, receipt):
        return {'version': 1, 'provider': 'elevenlabs', 'policy_sha256': _hash(policy),
                'reservation_sha256': receipt['reservation_sha256'],
                **self._context(receipt['intent'])}

    def _foundation_links(self, pipe, policy, state, history=()):
        if self.foundation is None:
            return
        _require(_object(pipe.hget(LEDGER_KEY, MODE_FIELD)) == self._mode(policy),
                 'credit_foundation_mismatch')
        expected = {}
        for bound_policy, bound_state in [*((row['policy'], row['state']) for row in history), (policy, state)]:
            for entry in bound_state['intents'].values():
                receipt, request = entry['reservation'], entry['reservation']['intent']
                identity = self._request_field(request)
                _require(not pipe.hexists(LEDGER_KEY, 'request:' + identity)
                         and 'native_request:' + identity not in expected,
                         'credit_cross_mode_request_conflict')
                expected['native_request:' + identity] = _json(self._production_marker(bound_policy, receipt))
        # Also reject surviving receipts absent from a rolled-back state/journal.
        # One-way lookup would incorrectly restore already committed capacity.
        _require(native_foundation_markers(pipe) == expected, 'credit_foundation_mismatch')

    def _validate_records(self, raw, journal, now, *, historical=False):
        """Check a complete current or archived pair without rewriting receipts."""
        _require(set(raw) == {'policy', 'state'} and type(journal) is dict,
                 'credit_store_invalid')
        policy, state = _object(raw['policy']), _object(raw['state'])
        if historical:
            policy = validate_credit_history(policy, state, now=now)
        else:
            policy = validate_credit_policy(policy, now=now)
            credit_funding_summary(policy, state, now=now)
        genesis = _object(journal.get('genesis'))
        _require(set(genesis) == {'version', 'policy_sha256', 'initialized_at', 'foundation_bound'}
                 and type(genesis['version']) is int and genesis['version'] == 1
                 and genesis['policy_sha256'] == _hash(policy)
                 and genesis['foundation_bound'] is (self.foundation is not None),
                 'credit_journal_invalid')
        try:
            stamp = datetime.strptime(genesis['initialized_at'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            raise SpendBlocked('credit_journal_invalid') from None
        _require(stamp <= now and genesis['initialized_at'] <= state['last_updated_at'],
                 'credit_journal_invalid')
        initial_credit_state(policy, now=stamp)
        _require(journal == _journal(policy, state, genesis), 'credit_journal_mismatch')
        return policy, state, genesis, journal

    def _read(self, pipe, now, *, historical=False):
        self._foundation_base(pipe, now)
        _require(pipe.hlen(STATE_KEY) == 2 and 4 <= pipe.hlen(JOURNAL_KEY) <= _MAX_FIELDS,
                 'credit_not_initialized_or_partial')
        # An externally assigned TTL survives HSET. Never admit a request while
        # either permanent replay record is scheduled to disappear.
        lifetimes = (pipe.pttl(STATE_KEY), pipe.pttl(JOURNAL_KEY))
        _require(all(type(value) is int and value == -1 for value in lifetimes),
                 'credit_store_expiring')
        raw = pipe.hgetall(STATE_KEY)
        journal = pipe.hgetall(JOURNAL_KEY)
        policy, state, genesis, journal = self._validate_records(raw, journal, now, historical=historical)
        from app.services.production_credit_periods import read_history
        history = read_history(self, pipe, policy, state, now)
        self._foundation_links(pipe, policy, state, history['periods'])
        return policy, state, genesis, journal

    @staticmethod
    def _ping(pipe):
        pipe.multi()
        pipe.ping()
        result = pipe.execute()
        _require(type(result) is list and len(result) == 1 and result[0] is True,
                 'credit_commit_uncertain')

    def initialize(self, policy):
        """Explicit one-time operator commissioning; existing use never resets."""
        policy = _object(_json(policy))
        for _ in range(8):
            now = self.clock()
            checked = validate_credit_policy(policy, now=now)
            try:
                with self.client.pipeline() as pipe:
                    self._watch(pipe)
                    self._foundation_base(pipe, now)
                    if pipe.exists(STATE_KEY, JOURNAL_KEY):
                        prior, _, _, _ = self._read(pipe, now)
                        _require(prior == checked, 'credit_policy_mismatch')
                        self._ping(pipe)
                        return False
                    _require(not pipe.hexists(LEDGER_KEY, MODE_FIELD)
                             and not native_foundation_markers(pipe),
                             'credit_not_initialized_or_partial')
                    from app.services.production_credit_periods import HISTORY_KEY, HISTORY_ANCHOR, HISTORY_FIELD
                    _require(not pipe.exists(HISTORY_KEY, HISTORY_ANCHOR)
                        and not pipe.hexists(LEDGER_KEY, HISTORY_FIELD), 'credit_period_history_missing')
                    state = initial_credit_state(checked, now=now)
                    genesis = {'version': 1, 'policy_sha256': _hash(checked),
                               'initialized_at': state['last_updated_at'],
                               'foundation_bound': self.foundation is not None}
                    journal = _journal(checked, state, genesis)
                    pipe.multi()
                    pipe.hset(STATE_KEY, mapping={'policy': _json(checked), 'state': _json(state)})
                    pipe.hset(JOURNAL_KEY, mapping=journal)
                    expected_ack = [2, len(journal)]
                    if self.foundation is not None:
                        pipe.hset(LEDGER_KEY, MODE_FIELD, _json(self._mode(checked)))
                        expected_ack.append(1)
                    result = pipe.execute()
                    _require(type(result) is list and len(result) == len(expected_ack)
                             and all(type(v) is int for v in result)
                             and result == expected_ack, 'credit_commit_uncertain')
                    return True
            except WatchError:
                continue
            except SpendBlocked:
                raise
            except Exception:
                raise SpendBlocked('credit_store_unavailable') from None
        raise SpendBlocked('credit_store_contention')

    def summary(self):
        for _ in range(8):
            now = self.clock()
            try:
                with self.client.pipeline() as pipe:
                    self._watch(pipe)
                    policy, state, _, _ = self._read(pipe, now)
                    result = credit_funding_summary(policy, state, now=now)
                    self._ping(pipe)
                    return result
            except WatchError:
                continue
            except SpendBlocked:
                raise
            except Exception:
                raise SpendBlocked('credit_store_unavailable') from None
        raise SpendBlocked('credit_store_contention')

    def binding_snapshot(self):
        """Read the commissioned account/credential mapping, never infer one.

        The operator evidence binds a known account to a digest of the real
        API key. The runtime must compare its immutable outgoing key with this
        mapping. This read is not a fresh provider account API observation.
        """
        _require(self.foundation is not None, 'credit_foundation_required')
        for _ in range(8):
            try:
                with self.client.pipeline() as pipe:
                    self._watch(pipe)
                    policy, _, _, _ = self._read(pipe, self.clock())
                    result = {key: policy[key] for key in (
                        'account_sha256', 'credential_sha256', 'route', 'model', 'voice_id', 'valid_until')}
                    if policy['version'] == 2:
                        result['additional_models'] = list(policy['additional_models'])
                    result['policy_sha256'] = _hash(policy)
                    self._ping(pipe)
                    return result
            except WatchError:
                continue
            except SpendBlocked:
                raise
            except Exception:
                raise SpendBlocked('credit_store_unavailable') from None
        raise SpendBlocked('credit_store_contention')

    def _transition(self, operation, values, production_context=None):
        values = _object(_json(values))
        for _ in range(8):
            now = self.clock()
            try:
                with self.client.pipeline() as pipe:
                    self._watch(pipe)
                    policy, state, genesis, journal = self._read(
                        pipe, now, historical=operation is settle_credit_intent)
                    from app.services.production_credit_periods import check_prior_identity
                    check_prior_identity(self, pipe, policy, state, now, operation, values)
                    production_field = None
                    if operation is reserve_credit_intent and self.foundation is not None:
                        request = values['intent']
                        _require(production_context == self._context(request),
                                 'credit_production_context_invalid')
                        _require(_object(pipe.hget(LEDGER_KEY, 'binding:' + request['root_lineage_id']))
                                 == production_context, 'credit_production_context_invalid')
                        identity = self._request_field(request)
                        _require(not pipe.hexists(LEDGER_KEY, 'request:' + identity)
                                 and not pipe.hexists(LEDGER_KEY, 'native_request:' + identity),
                                 'credit_cross_mode_request_conflict')
                        production_field = 'native_request:' + identity
                    elif production_context is not None:
                        raise SpendBlocked('credit_production_context_invalid')
                    updated, receipt = operation(policy, state, now=now, **values)
                    if updated == state:
                        self._ping(pipe)
                        return receipt
                    expected = _journal(policy, updated, genesis)
                    _require(all(k in expected and (k in {'state_sha256', 'revision'}
                                 or expected[k] == v) for k, v in journal.items()),
                             'credit_journal_mutation_forbidden')
                    delta = {k: v for k, v in expected.items() if journal.get(k) != v}
                    new_fields = sum(k not in journal for k in delta)
                    pipe.multi()
                    pipe.hset(STATE_KEY, 'state', _json(updated))
                    pipe.hset(JOURNAL_KEY, mapping=delta)
                    expected_ack = [0, new_fields]
                    if production_field is not None:
                        pipe.hset(LEDGER_KEY, production_field,
                                  _json(self._production_marker(policy, receipt)))
                        expected_ack.append(1)
                    result = pipe.execute()
                    _require(type(result) is list and len(result) == len(expected_ack)
                             and all(type(v) is int for v in result)
                             and result == expected_ack, 'credit_commit_uncertain')
                    return receipt
            except WatchError:
                continue
            except SpendBlocked:
                raise
            except Exception:
                raise SpendBlocked('credit_store_unavailable') from None
        raise SpendBlocked('credit_store_contention')

    def reserve(self, *, intent, actual_account_sha256, actual_credential_sha256,
                production_context=None):
        if production_context is not None:
            production_context = _object(_json(production_context))
        return self._transition(reserve_credit_intent, {
            'intent': intent, 'actual_account_sha256': actual_account_sha256,
            'actual_credential_sha256': actual_credential_sha256}, production_context)

    def settle(self, *, observation, actual_account_sha256, actual_credential_sha256):
        return self._transition(settle_credit_intent, {
            'observation': observation, 'actual_account_sha256': actual_account_sha256,
            'actual_credential_sha256': actual_credential_sha256})
