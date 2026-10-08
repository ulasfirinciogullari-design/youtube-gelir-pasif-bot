"""Explicit prospective four-review plan; all eighteen predecessors stay closed.

The original root has four occupied requests, three with unknown outcomes. This
new immutable plan states eight prospective attempts honestly; it never edits
the earlier maximum of six. Selection is diagnostic accounting authority only.
Actual runtime capture and source-bound semantic gates remain required before
any send. No API here sends, initializes cash, retries or grants quality.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
import re

from app.services import abacus_router_review_journal as story
from app.services import abacus_router_audio_review_journal as audio
from app.services import retained_review_credential_successor as predecessor
from app.services import retained_router_protocol_probe as probe
from app.services import provider_key_candidate as candidate
from app.services.production_spend import SpendBlocked


_PREFIX = 'youtube_studio:{production_spend}:retained_completion_plan:v1:' + predecessor.continuity.ROOT_ID
STATE_KEY, JOURNAL_KEY, ANCHOR_KEY = (_PREFIX + part for part in (':state', ':journal', ':commissioned'))
STORY_KEYS = tuple(_PREFIX + ':story' + part for part in (':state', ':journal', ':commissioned'))
AUDIO_KEYS = tuple(_PREFIX + ':audio' + part for part in (':state', ':journal', ':commissioned'))
ALL_KEYS = (STATE_KEY, JOURNAL_KEY, ANCHOR_KEY, *STORY_KEYS, *AUDIO_KEYS)
HISTORICAL_KEYS = (*predecessor.LEGACY_KEYS, *predecessor.ALL_KEYS, *probe.PROBE_KEYS)
_ORDER = (('story', story.PURPOSES[0]), ('story', story.PURPOSES[1]),
          ('audio', audio.PURPOSES[0].value), ('audio', audio.PURPOSES[1].value))
_SEAL = object()
_MAX = 1024 * 1024
_FLAGS = {'diagnostic_only': True, 'qa_approved': False, 'publish_eligible': False,
          'account_plan_verified': False, 'completion_or_funding_verified': False,
          'pre_observer_capture_verified': False, 'semantic_acceptance_verified': False}
_ATTESTATION_HASHES = {'owner_authorization_sha256', 'operator_attempt_sha256',
    'predecessor_snapshot_sha256', 'completed_probe_result_sha256',
    'completed_probe_capture_sha256', 'current_credential_sha256',
    'entitlement_evidence_sha256', 'official_terms_sha256'}
_ATTESTATION_FIXED = {'version': 1, 'kind': 'operator_included_retained_completion_plan',
    'monthly_additional_cash_limit_micro': 10_000_000, 'included_cash_allowance_micro': 0,
    'historical_extra_cash_micro': None, 'account_plan_verified': False,
    'completion_or_funding_verified': False, 'pre_observer_capture_required': True,
    'source_bound_semantic_gates_required': True}
_MANIFEST_FIXED = {'version': 1, 'kind': 'explicit_included_retained_completion_plan',
    'original_task_id': predecessor.continuity.ROOT_ID, 'leaf_task_id': predecessor.continuity.LEAF_ID,
    'prior_occupied_count': 4, 'prior_unknown_count': 3, 'additional_attempt_limit': 4,
    'max_total_attempts': 8, 'pre_observer_capture_required': True,
    'source_bound_semantic_gates_required': True}


class CompletionPlanBlocked(SpendBlocked):
    """Fixed local messages only; no raw store/provider details."""


def _require(value, reason='retained_completion_plan_unverified'):
    if not value:
        raise CompletionPlanBlocked(reason)


def _raw(value):
    value = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    _require(len(value) <= _MAX)
    return value


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _hash(value):
    return _sha(_raw(value))


def _object(raw):
    _require(type(raw) in (bytes, str) and 0 < len(raw) <= _MAX)
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result)
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: _require(False))
    _require(type(value) is dict)
    return value


def _digest(value):
    _require(type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None)


def _fixed(value, expected):
    _require(all(type(value.get(key)) is type(item) and value[key] == item for key, item in expected.items()))


def _now(clock):
    value = (clock or (lambda: datetime.now(timezone.utc)))()
    _require(isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None)
    return value.astimezone(timezone.utc).replace(microsecond=0)


def _configuration():
    config = candidate.settings
    _require(config.studio_spend_enforcement is True, 'retained_completion_cash_lock_changed')
    caps = _object(config.studio_spend_policy_json)
    _require(set(caps) == probe._CAPS and all(type(n) is int and n == 0 for n in caps.values()),
             'retained_completion_cash_lock_changed')
    context = candidate._context()
    head = os.environ.get('RAILWAY_GIT_COMMIT_SHA', '')
    _require(re.fullmatch('[0-9a-f]{40}', head) is not None, 'retained_completion_runtime_changed')
    return (*context, head)


def _attestation(value):
    _require(type(value) is dict and set(value) == {*_ATTESTATION_FIXED, *_ATTESTATION_HASHES, 'runtime_head_sha'})
    _fixed(value, _ATTESTATION_FIXED)
    for key in _ATTESTATION_HASHES:
        _digest(value[key])
    _require(type(value['runtime_head_sha']) is str and re.fullmatch('[0-9a-f]{40}', value['runtime_head_sha']))
    return value


def _snapshot(value):
    _require(type(value) is dict and set(value) == {'records', 'snapshot_sha256',
        'controller_manifest_sha256', 'probe_capture_sha256', 'credential_sha256',
        'continuity_sha256', 'prior_occupied_count', 'prior_unknown_count'})
    _fixed(value, {'prior_occupied_count': 4, 'prior_unknown_count': 3})
    _require(type(value['records']) is dict and set(value['records']) == set(HISTORICAL_KEYS))
    for digest in value['records'].values():
        _digest(digest)
    for key in ('snapshot_sha256', 'controller_manifest_sha256', 'probe_capture_sha256',
                'credential_sha256', 'continuity_sha256'):
        _digest(value[key])
    _require(value['snapshot_sha256'] == _hash(value['records']))
    return value


def _historical(pipe, *, current=False):
    pipe.watch(*HISTORICAL_KEYS)
    _require(all(type(ttl) is int and ttl == -1 for ttl in (pipe.pttl(k) for k in HISTORICAL_KEYS)),
             'retained_completion_history_not_durable')
    manifest, states, raw, context = predecessor._read_control(pipe, current=current)
    _require(manifest['old_occupied_unknown_count'] == 2
             and set(states['story']['slots']) == {story.PURPOSES[0]}
             and states['story']['slots'][story.PURPOSES[0]]['response'] is None
             and states['audio']['slots'] == {}, 'retained_completion_history_shape_changed')
    records = {}
    for index, key in enumerate(HISTORICAL_KEYS):
        records[key] = _hash(pipe.hgetall(key) if index % 3 == 1 else pipe.get(key))
    probe_state = _object(pipe.get(probe.PROBE_KEYS[0]))
    reservation = probe_state['reservation']
    probe_state = probe._read(pipe, reservation)
    _require(probe_state['capture'] is not None, 'retained_completion_probe_outcome_unknown')
    _require(reservation['purpose'] == probe.PURPOSE
             and reservation['original_task_id'] == predecessor.continuity.ROOT_ID
             and reservation['source_task_id'] == predecessor.continuity.LEAF_ID
             and reservation['controller_manifest_sha256'] == _sha(raw)
             and reservation['credential_sha256'] == states['story']['policy']['credential_sha256']
             and reservation['continuity_sha256'] == states['story']['policy']['continuity_sha256']
             and reservation['policy_sha256'] == _hash(states['story']['policy'])
             and reservation['frozen_records_sha256'] == _hash({k: records[k] for k in HISTORICAL_KEYS[:-3]})
             and reservation['failure_proof_sha256'] == probe._FAILURE['failure_proof_sha256'],
             'retained_completion_probe_binding_changed')
    result = {'records': records, 'snapshot_sha256': _hash(records),
        'controller_manifest_sha256': _sha(raw), 'probe_capture_sha256': _hash(probe_state['capture']),
        'credential_sha256': states['story']['policy']['credential_sha256'],
        'continuity_sha256': states['story']['policy']['continuity_sha256'],
        'prior_occupied_count': 4, 'prior_unknown_count': 3}
    return _snapshot(result), states, context


def _ping(pipe):
    pipe.multi(); pipe.ping()
    ack = pipe.execute()
    _require(type(ack) is list and len(ack) == 1 and ack[0] is True, 'retained_completion_ack_unknown')


def snapshot_completion_predecessors(client):
    """Historical read only, including expired old windows; no send authority."""
    try:
        with client.pipeline() as pipe:
            snapshot, _, _ = _historical(pipe)
            _ping(pipe)
        return snapshot
    except CompletionPlanBlocked:
        raise
    except Exception:
        raise CompletionPlanBlocked('retained_completion_history_unverified') from None


def _manifest(value):
    _require(type(value) is dict and set(value) == {*_MANIFEST_FIXED, 'created_at', 'predecessors',
        'attestation', 'policies', 'source_metadata_sha256', 'new_purpose_limits'})
    _fixed(value, _MANIFEST_FIXED)
    limits = value['new_purpose_limits']
    _require(type(limits) is dict and set(limits) == {purpose for _, purpose in _ORDER}
             and all(type(n) is int and n == 1 for n in limits.values()))
    policies = predecessor._policies(value['policies'])
    snapshot = _snapshot(value['predecessors'])
    attestation = _attestation(value['attestation'])
    _require(snapshot['snapshot_sha256'] == attestation['predecessor_snapshot_sha256']
             and snapshot['probe_capture_sha256'] == attestation['completed_probe_capture_sha256']
             and snapshot['credential_sha256'] == attestation['current_credential_sha256']
             == policies[0]['credential_sha256']
             and snapshot['continuity_sha256'] == policies[0]['continuity_sha256']
             and policies[0]['entitlement_evidence_sha256'] == attestation['entitlement_evidence_sha256'])
    _digest(value['source_metadata_sha256'])
    _require(value['source_metadata_sha256'] == policies[1]['source_metadata_sha256'])
    stamp = predecessor._date(value['created_at'])
    _require(predecessor._date(policies[0]['valid_from']) <= stamp < predecessor._date(policies[0]['valid_until']))
    return value


@dataclass(frozen=True, repr=False, init=False, slots=True)
class CompletionPlanAuthorization:
    _manifest_bytes: bytes
    _seal: object

    def __init__(self, *args, **kwargs):
        raise TypeError('Use explicit completion plan commissioning or watched readback.')

    def __repr__(self):
        return '<CompletionPlanAuthorization included diagnostics only>'

    @property
    def receipt(self):
        value = _checked(self)
        return {'version': 1, 'manifest_sha256': _sha(self._manifest_bytes),
            'predecessor_snapshot_sha256': value['predecessors']['snapshot_sha256'],
            'prior_occupied_count': 4, 'prior_unknown_count': 3, 'additional_attempt_limit': 4,
            'max_total_attempts': 8, 'pre_observer_capture_required': True,
            'source_bound_semantic_gates_required': True, **_FLAGS}


def _checked(authorization):
    _require(type(authorization) is CompletionPlanAuthorization
             and getattr(authorization, '_seal', None) is _SEAL
             and type(getattr(authorization, '_manifest_bytes', None)) is bytes,
             'retained_completion_capability_invalid')
    value = _manifest(_object(authorization._manifest_bytes))
    _require(_raw(value) == authorization._manifest_bytes)
    return value


def _authorization(raw):
    _manifest(_object(raw))
    value = object.__new__(CompletionPlanAuthorization)
    object.__setattr__(value, '_manifest_bytes', raw)
    object.__setattr__(value, '_seal', _SEAL)
    return value


def selected_keys(authorization, kind):
    _checked(authorization)
    _require(type(kind) is str and kind in ('story', 'audio'))
    return STORY_KEYS if kind == 'story' else AUDIO_KEYS


def _control_journal(manifest, states):
    return {'manifest_sha256': _hash(manifest),
        'predecessor_snapshot_sha256': manifest['predecessors']['snapshot_sha256'],
        'story_policy_sha256': _hash(manifest['policies']['story']),
        'audio_policy_sha256': _hash(manifest['policies']['audio']),
        'story_state_sha256': _hash(states['story']), 'audio_state_sha256': _hash(states['audio'])}


def _ordered(states):
    occupied = [(kind, purpose) for kind, purpose in _ORDER if purpose in states[kind]['slots']]
    _require(occupied == list(_ORDER[:len(occupied)]), 'retained_completion_order_changed')
    for kind, purpose in occupied[:-1]:
        _require(states[kind]['slots'][purpose]['response'] is not None,
                 'retained_completion_previous_outcome_unknown')
    return occupied


def _read_control(pipe, *, current=False):
    pipe.watch(*ALL_KEYS)
    _require(all(type(ttl) is int and ttl == -1 for ttl in (pipe.pttl(k) for k in ALL_KEYS)),
             'retained_completion_not_durable')
    stored = pipe.get(STATE_KEY)
    raw = stored.encode() if type(stored) is str else stored
    manifest = _manifest(_object(raw))
    _require(raw == _raw(manifest))
    snapshot, old, context = _historical(pipe, current=current)
    _require(snapshot == manifest['predecessors'], 'retained_completion_history_changed')
    predecessor._unchanged_bindings(old, manifest['policies'])
    predecessor._source(pipe, manifest['policies'])
    cap = _authorization(raw)
    states = {'story': story.RouterReviewJournal(pipe, completion_plan=cap)._read_records(pipe),
              'audio': audio.RouterAudioReviewJournal(pipe, completion_plan=cap)._read_records(pipe)}
    _require(all(state['policy'] == manifest['policies'][kind] for kind, state in states.items()))
    _ordered(states)
    control = _control_journal(manifest, states)
    _require(pipe.hlen(JOURNAL_KEY) == 6 and pipe.hgetall(JOURNAL_KEY) == control
             and pipe.get(ANCHOR_KEY) == _hash(control), 'retained_completion_heads_changed')
    return manifest, states, raw, context


def read_retained_completion_plan(client):
    """Select verified history without renewing a window or permitting a send."""
    try:
        with client.pipeline() as pipe:
            _, _, raw, _ = _read_control(pipe)
            _ping(pipe)
        return _authorization(raw)
    except CompletionPlanBlocked:
        raise
    except Exception:
        raise CompletionPlanBlocked('retained_completion_read_unverified') from None


def verify_scope_completion_plan(client, authorization):
    _checked(authorization)
    try:
        context = _configuration()
        with client.pipeline() as pipe:
            manifest, _, raw, actual = _read_control(pipe, current=True)
            _require(raw == authorization._manifest_bytes and actual == context[:2]
                     and manifest['attestation']['runtime_head_sha'] == context[2],
                     'retained_completion_capability_changed')
            _ping(pipe)
        _require(_configuration() == context, 'retained_completion_configuration_changed')
    except CompletionPlanBlocked:
        raise
    except Exception:
        raise CompletionPlanBlocked('retained_completion_scope_unverified') from None


def guard_selected(pipe, authorization, kind, state):
    selected_keys(authorization, kind)
    _, states, raw, _ = _read_control(pipe)
    _require(raw == authorization._manifest_bytes and states[kind] == state,
             'retained_completion_capability_changed')


def guard_mutation(pipe, authorization, kind, *, reserve=False):
    selected_keys(authorization, kind)
    manifest, states, raw, context = _read_control(pipe, current=reserve)
    _require(raw == authorization._manifest_bytes, 'retained_completion_capability_changed')
    if reserve:
        config = _configuration()
        _require(config[:2] == context and config[2] == manifest['attestation']['runtime_head_sha'],
                 'retained_completion_configuration_changed')
        occupied = _ordered(states)
        _require(len(occupied) < 4 and _ORDER[len(occupied)][0] == kind,
                 'retained_completion_attempt_limit_or_order')
        _require(all(states[k]['slots'][p]['response'] is not None for k, p in occupied),
                 'retained_completion_previous_outcome_unknown')


def commit_selected(pipe, authorization, kind, state, old_journal):
    """One selected transition plus independent heads; never alter predecessors."""
    keys = selected_keys(authorization, kind)
    manifest, states, raw, _ = _read_control(pipe)
    module = story if kind == 'story' else audio
    old = states[kind]
    _require(raw == authorization._manifest_bytes and old_journal == module._journal(old)
             and state['policy'] == old['policy'], 'retained_completion_capability_changed')
    added = set(state['slots']) - set(old['slots'])
    if added:
        occupied = _ordered(states)
        _require(len(added) == 1 and len(occupied) < 4
                 and (kind, next(iter(added))) == _ORDER[len(occupied)]
                 and state['slots'][next(iter(added))]['response'] is None
                 and all(state['slots'].get(p) == slot for p, slot in old['slots'].items())
                 and all(states[k]['slots'][p]['response'] is not None for k, p in occupied),
                 'retained_completion_transition_invalid')
    else:
        _require(set(state['slots']) == set(old['slots']), 'retained_completion_transition_invalid')
        changed = [p for p in old['slots'] if state['slots'][p] != old['slots'][p]]
        _require(len(changed) == 1, 'retained_completion_transition_invalid')
        purpose = changed[0]
        before, after = old['slots'][purpose], state['slots'][purpose]
        _require(before['response'] is None and after['response'] is not None
                 and {k: v for k, v in before.items() if k != 'response'}
                 == {k: v for k, v in after.items() if k != 'response'},
                 'retained_completion_transition_invalid')
    states[kind] = state
    _ordered(states)
    journal, control = module._journal(state), _control_journal(manifest, states)
    pipe.multi()
    pipe.set(keys[0], module._json(state))
    pipe.hset(keys[1], mapping=journal)
    pipe.set(keys[2], module._hash(state))
    pipe.hset(JOURNAL_KEY, mapping=control)
    pipe.set(ANCHOR_KEY, _hash(control))
    ack = pipe.execute()
    expected = [True, len(set(journal) - set(old_journal)), True, 0, True]
    _require(type(ack) is list and len(ack) == len(expected)
             and all(type(a) is type(b) and a == b for a, b in zip(ack, expected)),
             'retained_completion_ack_unknown')


def commission_retained_completion_plan(client, *, story_policy, audio_policy,
                                        source_metadata_bytes, attestation, clock=None):
    """One explicit fixed batch; runtime capture/semantic enforcement is separate."""
    try:
        context = _configuration()
        policies = _object(_raw({'story': story_policy, 'audio': audio_policy}))
        predecessor._policies(policies)
        attestation = _attestation(_object(_raw(attestation)))
        _require(attestation['runtime_head_sha'] == context[2])
        _require(type(source_metadata_bytes) is bytes)
        audio._metadata(source_metadata_bytes, policies['audio'])
        now = _now(clock)
        with client.pipeline() as pipe:
            pipe.watch(*ALL_KEYS)
            _require(pipe.exists(*ALL_KEYS) == 0, 'retained_completion_already_commissioned_or_partial')
            snapshot, old, historical_context = _historical(pipe, current=True)
            _require(context[:2] == historical_context)
            predecessor._unchanged_bindings(old, policies)
            _require(policies['story']['credential_sha256'] == snapshot['credential_sha256'])
            story.RouterReviewJournal(client)._fresh(pipe, policies['story'], now)
            audio.RouterAudioReviewJournal(client)._fresh(pipe, policies['audio'], now)
            stamp = now.strftime('%Y-%m-%dT%H:%M:%SZ')
            manifest = _manifest({**_MANIFEST_FIXED, 'created_at': stamp, 'predecessors': snapshot,
                'attestation': attestation, 'policies': policies,
                'source_metadata_sha256': _sha(source_metadata_bytes),
                'new_purpose_limits': {purpose: 1 for _, purpose in _ORDER}})
            raw = _raw(manifest)
            states = {kind: {'policy': policy, 'slots': {}, 'updated_at': stamp}
                      for kind, policy in policies.items()}
            control = _control_journal(manifest, states)
            _require(_configuration() == context, 'retained_completion_configuration_changed')
            pipe.multi()
            pipe.set(STATE_KEY, raw.decode(), nx=True)
            pipe.hset(JOURNAL_KEY, mapping=control)
            pipe.set(ANCHOR_KEY, _hash(control), nx=True)
            for kind, keys, module in (('story', STORY_KEYS, story), ('audio', AUDIO_KEYS, audio)):
                pipe.set(keys[0], module._json(states[kind]), nx=True)
                pipe.hset(keys[1], mapping=module._journal(states[kind]))
                pipe.set(keys[2], module._hash(states[kind]), nx=True)
            ack = pipe.execute()
            expected = [True, 6, True, True, 2, True, True, 2, True]
            _require(type(ack) is list and len(ack) == len(expected)
                     and all(type(a) is type(b) and a == b for a, b in zip(ack, expected)),
                     'retained_completion_ack_unknown')
        _require(_configuration() == context, 'retained_completion_configuration_changed')
        result = read_retained_completion_plan(client)
        _require(result._manifest_bytes == raw and _configuration() == context,
                 'retained_completion_readback_changed')
        return result
    except CompletionPlanBlocked:
        raise
    except Exception:
        raise CompletionPlanBlocked('retained_completion_commission_unverified') from None
