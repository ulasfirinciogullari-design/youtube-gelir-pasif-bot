"""One explicit included-only credential successor for the original retained root.

The operator attests the promotion backup/proof provenance and the existing
subscription. These are assertions, never an account, funding or cash lookup.
The actual permanent encrypted candidate archive and current key are verified
locally. The two old unknown attempts remain occupied and byte-for-byte intact;
at most four additional fixed purposes can be reserved. No sender, automatic
commission, retry of a provider request, financial initialization or QA grant.

All nine successor records and six predecessors must survive permanently.
Each child transition also updates independent control head commitments in the
same transaction. Coordinated external rollback of every copy of a transition
requires external recovery, never automatic initialization.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
from uuid import UUID

from app.services import abacus_router_review_journal as story
from app.services import abacus_router_audio_review_journal as audio
from app.services import production_connection_continuity as continuity
from app.services import provider_key_candidate as candidate
from app.services.production_spend import SpendBlocked


_PREFIX = 'youtube_studio:{production_spend}:retained_credential_successor:v1:' + continuity.ROOT_ID
STATE_KEY, JOURNAL_KEY, ANCHOR_KEY = (_PREFIX + suffix for suffix in (':state', ':journal', ':commissioned'))
STORY_KEYS = tuple(_PREFIX + ':story' + suffix for suffix in (':state', ':journal', ':commissioned'))
AUDIO_KEYS = tuple(_PREFIX + ':audio' + suffix for suffix in (':state', ':journal', ':commissioned'))
ALL_KEYS = (STATE_KEY, JOURNAL_KEY, ANCHOR_KEY, *STORY_KEYS, *AUDIO_KEYS)
LEGACY_KEYS = (story.STATE_KEY, story.JOURNAL_KEY, story.ANCHOR_KEY,
               audio.STATE_KEY, audio.JOURNAL_KEY, audio.ANCHOR_KEY)
_ARCHIVE_PREFIX = 'youtube_studio:{provider_key_candidate}:abacus:archive:v1:'
_MAX_BYTES = 1024 * 1024
_SHA = re.compile(r'[0-9a-f]{64}')
_SEAL = object()
_CHANGED_POLICY_FIELDS = {'credential_sha256', 'valid_from', 'valid_until', 'entitlement_evidence_sha256'}
_PURPOSE_LIMITS = {**{purpose: 1 for purpose in story.PURPOSES},
                   **{purpose.value: 1 for purpose in audio.PURPOSES}}
_ATTESTATION_HASHES = {
    'candidate_record_sha256', 'original_active_key_sha256', 'old_credential_sha256',
    'new_credential_sha256', 'predecessor_snapshot_sha256', 'promotion_plan_sha256',
    'old_vault_before_sha256', 'promotion_finalized_proof_sha256',
    'readonly_access_proof_sha256', 'owner_authorization_sha256', 'entitlement_evidence_sha256',
}
_ATTESTATION_FIELDS = _ATTESTATION_HASHES | {
    'version', 'kind', 'candidate_id', 'old_key_binding', 'historical_extra_cash_micro',
    'new_cash_allowance_micro', 'account_plan_verified', 'completion_or_funding_verified',
}
_MANIFEST_FIELDS = {'version', 'kind', 'original_task_id', 'leaf_task_id', 'created_at',
    'predecessors', 'attestation', 'policies', 'old_occupied_unknown_count',
    'new_purpose_limits', 'max_total_attempts', 'source_metadata_sha256'}


class CredentialSuccessorBlocked(SpendBlocked):
    """Fixed local failures only; never backend messages, keys or source text."""


def _require(value, reason='retained_successor_invalid'):
    if not value:
        raise CredentialSuccessorBlocked(reason)


def _raw(value):
    result = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    _require(len(result) <= _MAX_BYTES)
    return result


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _object(raw):
    _require(type(raw) in (str, bytes))
    if type(raw) is str:
        raw = raw.encode()
    _require(0 < len(raw) <= _MAX_BYTES)
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result)
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: _require(False))
    _require(type(value) is dict)
    return value


def _bytes(value):
    _require(type(value) in (str, bytes))
    return value.encode() if type(value) is str else value


def _digest(value):
    _require(type(value) is str and _SHA.fullmatch(value) is not None)


def _date(value):
    return story._date(value)


def _now(clock):
    value = clock() if clock is not None else datetime.now(timezone.utc)
    _require(isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None)
    return value.astimezone(timezone.utc).replace(microsecond=0)


def _attestation(value):
    _require(type(value) is dict and set(value) == _ATTESTATION_FIELDS)
    expected = {'version': 1, 'kind': 'operator_included_credential_successor',
        'old_key_binding': 'operator_verified_promotion_backup', 'historical_extra_cash_micro': None,
        'new_cash_allowance_micro': 0, 'account_plan_verified': False, 'completion_or_funding_verified': False}
    _require(all(type(value[k]) is type(v) and value[k] == v for k, v in expected.items()))
    _require(type(value['candidate_id']) is str and str(UUID(value['candidate_id'])) == value['candidate_id'])
    for field in _ATTESTATION_HASHES:
        _digest(value[field])
    _require(value['old_credential_sha256'] != value['new_credential_sha256'])
    return value


def _policies(value):
    _require(type(value) is dict and set(value) == {'story', 'audio'})
    first, second = story._policy(value['story']), audio._policy(value['audio'])
    for field in ('original_task_id', 'leaf_task_id', 'channel_id', 'profile_revision',
                  'old_connection_id', 'current_connection_id', 'continuity_sha256',
                  'credential_sha256', 'entitlement_evidence_sha256', 'valid_from', 'valid_until'):
        _require(first[field] == second[field])
    return first, second


def _manifest(value):
    _require(type(value) is dict and set(value) == _MANIFEST_FIELDS)
    expected = {'version': 1, 'kind': 'included_retained_credential_successor',
        'original_task_id': continuity.ROOT_ID, 'leaf_task_id': continuity.LEAF_ID,
        'old_occupied_unknown_count': 2, 'max_total_attempts': 6}
    _require(all(type(value[k]) is type(v) and value[k] == v for k, v in expected.items()))
    limits = value['new_purpose_limits']
    _require(type(limits) is dict and limits == _PURPOSE_LIMITS
             and all(type(number) is int for number in limits.values()))
    stamp = _date(value['created_at'])
    sp, ap = _policies(value['policies'])
    _require(_date(sp['valid_from']) <= stamp < _date(sp['valid_until']))
    attestation = _attestation(value['attestation'])
    _require(sp['credential_sha256'] == attestation['new_credential_sha256']
             and sp['entitlement_evidence_sha256'] == attestation['entitlement_evidence_sha256'])
    _digest(value['source_metadata_sha256'])
    _require(value['source_metadata_sha256'] == ap['source_metadata_sha256'])
    prior = value['predecessors']
    _require(type(prior) is dict and set(prior) == {'story', 'audio', 'snapshot_sha256',
                                                  'old_credential_sha256', 'occupied_unknown_count'})
    _require(type(prior['occupied_unknown_count']) is int and prior['occupied_unknown_count'] == 2
             and prior['old_credential_sha256'] == attestation['old_credential_sha256']
             and prior['snapshot_sha256'] == attestation['predecessor_snapshot_sha256'])
    for name in ('story', 'audio'):
        _require(type(prior[name]) is dict and set(prior[name]) == {'state_sha256', 'journal_sha256', 'anchor_sha256'})
        for digest in prior[name].values(): _digest(digest)
    _require(prior['snapshot_sha256'] == _sha(_raw({name: prior[name] for name in ('story', 'audio')})))
    return value


def _control_journal(manifest, states):
    return {'manifest_sha256': _sha(_raw(manifest)),
        'predecessor_snapshot_sha256': manifest['predecessors']['snapshot_sha256'],
        'story_policy_sha256': story._hash(manifest['policies']['story']),
        'audio_policy_sha256': audio._hash(manifest['policies']['audio']),
        'story_state_sha256': story._hash(states['story']),
        'audio_state_sha256': audio._hash(states['audio'])}


@dataclass(frozen=True, repr=False, init=False, slots=True)
class SuccessorAuthorization:
    _manifest_bytes: bytes
    _seal: object

    def __init__(self, *args, **kwargs):
        raise TypeError('Use watched credential successor commissioning or readback.')

    def __repr__(self):
        return '<SuccessorAuthorization included retained diagnostics only>'

    @property
    def receipt(self):
        manifest = _checked(self)
        return {'version': 1, 'manifest_sha256': _sha(self._manifest_bytes),
            'predecessor_snapshot_sha256': manifest['predecessors']['snapshot_sha256'],
            'old_occupied_unknown_count': 2, 'new_attempt_limit': 4, 'max_total_attempts': 6,
            'diagnostic_only': True, 'qa_approved': False, 'publish_eligible': False,
            'account_plan_verified': False, 'completion_or_funding_verified': False}


def _checked(authorization):
    _require(type(authorization) is SuccessorAuthorization
             and getattr(authorization, '_seal', None) is _SEAL
             and type(authorization._manifest_bytes) is bytes, 'retained_successor_capability_invalid')
    value = _manifest(_object(authorization._manifest_bytes))
    _require(_raw(value) == authorization._manifest_bytes)
    return value


def _authorization(raw):
    _manifest(_object(raw))
    value = object.__new__(SuccessorAuthorization)
    object.__setattr__(value, '_manifest_bytes', raw)
    object.__setattr__(value, '_seal', _SEAL)
    return value


def selected_keys(authorization, kind):
    _checked(authorization)
    _require(type(kind) is str and kind in ('story', 'audio'))
    return STORY_KEYS if kind == 'story' else AUDIO_KEYS


def _legacy_snapshot(pipe):
    pipe.watch(*LEGACY_KEYS)
    states = {'story': story.RouterReviewJournal(pipe)._read_records(pipe),
              'audio': audio.RouterAudioReviewJournal(pipe)._read_records(pipe)}
    _require(set(states['story']['slots']) == {story.PURPOSES[0]}
             and set(states['audio']['slots']) == {audio.PURPOSES[0].value}
             and all(slot['response'] is None for state in states.values() for slot in state['slots'].values()),
             'retained_successor_predecessor_not_two_unknowns')
    old = states['story']['policy']['credential_sha256']
    _require(states['audio']['policy']['credential_sha256'] == old)
    values = {}
    for kind, keys in (('story', LEGACY_KEYS[:3]), ('audio', LEGACY_KEYS[3:])):
        values[kind] = {'state_sha256': _sha(_bytes(pipe.get(keys[0]))),
            'journal_sha256': _sha(_raw(pipe.hgetall(keys[1]))),
            'anchor_sha256': _sha(_bytes(pipe.get(keys[2])))}
    return {**values, 'snapshot_sha256': _sha(_raw(values)), 'old_credential_sha256': old,
            'occupied_unknown_count': 2}, states


def _source(pipe, policies):
    # Predecessor windows/entitlement evidence may differ; source identity must not.
    sp, ap = story._policy(policies['story']), audio._policy(policies['audio'])
    _require(all(sp[field] == ap[field] for field in ('original_task_id', 'leaf_task_id',
        'channel_id', 'profile_revision', 'old_connection_id', 'current_connection_id', 'continuity_sha256')))
    source = continuity._derive(pipe, sp['profile_revision'])
    _require(story._hash(source) == sp['continuity_sha256']
             and all(source[field] == sp[field] for field in ('old_connection_id', 'current_connection_id')),
             'retained_successor_source_changed')


def _ping(pipe):
    pipe.multi(); pipe.ping()
    ack = pipe.execute()
    _require(type(ack) is list and len(ack) == 1 and ack[0] is True, 'retained_successor_ack_unknown')


def snapshot_predecessors(client):
    """Read-only, non-authorizing exact six-record snapshot for the operator."""
    try:
        with client.pipeline() as pipe:
            snapshot, states = _legacy_snapshot(pipe)
            _source(pipe, {name: state['policy'] for name, state in states.items()})
            _ping(pipe)
        return snapshot
    except CredentialSuccessorBlocked:
        raise
    except Exception:
        raise CredentialSuccessorBlocked('retained_successor_snapshot_unverified') from None


def _archive(pipe, attestation, *, current):
    """Authenticate the exact archived new key; old-hash bridge stays operator asserted."""
    context = candidate._context()
    key = _ARCHIVE_PREFIX + attestation['candidate_id'] + ':' + attestation['candidate_record_sha256']
    pipe.watch(key)
    raw = _bytes(pipe.get(key)); ttl = pipe.pttl(key)
    _require(0 < len(raw) <= 16384 and type(ttl) is int and ttl == -1
             and _sha(raw) == attestation['candidate_record_sha256'], 'retained_successor_archive_changed')
    plaintext = candidate._fernet(context[0]).decrypt(raw)
    _require(0 < len(plaintext) <= 16384)
    value = _object(plaintext)
    _require(set(value) == {'schema_version', 'provider', 'purpose', 'redis_key', 'candidate_id',
        'created_at', 'api_key', 'original_active_key_sha256', 'status',
        'api_access_verified', 'production_settings_changed'})
    expected = {'schema_version': 1, 'provider': 'abacus', 'purpose': 'provider_key_candidate',
        'redis_key': candidate.CANDIDATE_KEY, 'candidate_id': attestation['candidate_id'],
        'original_active_key_sha256': attestation['original_active_key_sha256'], 'status': 'pending',
        'api_access_verified': False, 'production_settings_changed': False}
    _require(all(type(value[k]) is type(v) and value[k] == v for k, v in expected.items()))
    stamp = value['created_at']
    _require(type(stamp) is str and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00', stamp)
             and datetime.fromisoformat(stamp).tzinfo is not None)
    new = candidate._key(value['api_key'])
    _require(_sha(('abacus\0' + new).encode('ascii')) == attestation['new_credential_sha256']
             and _sha(new.encode()) != attestation['original_active_key_sha256'])
    if current:
        _require(hmac.compare_digest(new.encode(), context[1].encode()), 'retained_successor_credential_changed')
    _require(candidate._context()[0] == context[0])
    return context


def _unchanged_bindings(old_states, policies):
    for kind, old in old_states.items():
        new = policies[kind]
        _require(all(type(new[field]) is type(value) and new[field] == value
                     for field, value in old['policy'].items() if field not in _CHANGED_POLICY_FIELDS),
                 'retained_successor_policy_binding_changed')
        _require(_date(new['valid_from']) >= _date(old['updated_at']))


def _read_control(pipe, *, current=False):
    pipe.watch(*ALL_KEYS)
    _require(all(type(ttl) is int and ttl == -1 for ttl in (pipe.pttl(key) for key in ALL_KEYS)),
             'retained_successor_not_durable')
    raw = _bytes(pipe.get(STATE_KEY)); manifest = _manifest(_object(raw))
    _require(raw == _raw(manifest), 'retained_successor_control_changed')
    prior, old = _legacy_snapshot(pipe)
    _require(prior == manifest['predecessors'], 'retained_successor_predecessor_changed')
    _unchanged_bindings(old, manifest['policies'])
    _source(pipe, manifest['policies'])
    context = _archive(pipe, manifest['attestation'], current=current)
    authorization = _authorization(raw)
    states = {'story': story.RouterReviewJournal(pipe, successor=authorization)._read_records(pipe),
              'audio': audio.RouterAudioReviewJournal(pipe, successor=authorization)._read_records(pipe)}
    _require(all(state['policy'] == manifest['policies'][name] for name, state in states.items())
             and sum(len(state['slots']) for state in states.values()) <= 4,
             'retained_successor_selected_journal_changed')
    journal = _control_journal(manifest, states)
    _require(pipe.hlen(JOURNAL_KEY) == 6 and pipe.hgetall(JOURNAL_KEY) == journal
             and pipe.get(ANCHOR_KEY) == _sha(_raw(journal)), 'retained_successor_control_changed')
    return manifest, states, raw, context


def guard_selected(pipe, authorization, kind, state):
    selected_keys(authorization, kind)
    _, states, raw, _ = _read_control(pipe)
    _require(raw == authorization._manifest_bytes and states[kind] == state,
             'retained_successor_capability_changed')


def guard_mutation(pipe, authorization, kind, *, reserve=False):
    if authorization is None:
        pipe.watch(*ALL_KEYS)
        _require(pipe.exists(*ALL_KEYS) == 0, 'retained_successor_legacy_fenced')
        return
    selected_keys(authorization, kind)
    _, states, raw, _ = _read_control(pipe, current=reserve)
    _require(raw == authorization._manifest_bytes, 'retained_successor_capability_changed')
    if reserve:
        _require(not any(slot['response'] is None for state in states.values() for slot in state['slots'].values()),
                 'retained_successor_previous_outcome_unacknowledged')
        _require(sum(len(state['slots']) for state in states.values()) < 4,
                 'retained_successor_attempt_limit')


def commit_selected(pipe, authorization, kind, state, old_journal):
    """Commit a selected journal and its independent heads in one watched EXEC."""
    keys = selected_keys(authorization, kind)
    manifest, states, raw, _ = _read_control(pipe)
    module = story if kind == 'story' else audio
    _require(raw == authorization._manifest_bytes and old_journal == module._journal(states[kind])
             and state['policy'] == states[kind]['policy'], 'retained_successor_capability_changed')
    states[kind] = state
    journal = module._journal(state)
    control = _control_journal(manifest, states)
    pipe.multi()
    pipe.set(keys[0], module._json(state))
    pipe.hset(keys[1], mapping=journal)
    pipe.set(keys[2], module._hash(state))
    pipe.hset(JOURNAL_KEY, mapping=control)
    pipe.set(ANCHOR_KEY, _sha(_raw(control)))
    ack = pipe.execute()
    expected = [True, len(set(journal) - set(old_journal)), True, 0, True]
    _require(type(ack) is list and len(ack) == len(expected)
             and all(type(a) is type(b) and a == b for a, b in zip(ack, expected)),
             'retained_successor_ack_unknown')


def read_credential_successor(client):
    """Explicit watched readback; historical selection does not renew request expiry."""
    try:
        with client.pipeline() as pipe:
            _, _, raw, context = _read_control(pipe, current=True)
            _ping(pipe)
        _require(candidate._context() == context, 'retained_successor_credential_changed')
        return _authorization(raw)
    except CredentialSuccessorBlocked:
        raise
    except Exception:
        raise CredentialSuccessorBlocked('retained_successor_read_unverified') from None


def verify_scope_successor(client, authorization):
    _checked(authorization)
    current = read_credential_successor(client)
    _require(current._manifest_bytes == authorization._manifest_bytes,
             'retained_successor_capability_changed')


def commission_credential_successor(client, *, story_policy, audio_policy,
                                   source_metadata_bytes, attestation, clock=None):
    """One operator transaction, never an automatic retry or provider permission.

    A successful return selects only this fixed batch. Each actual send still
    requires a fresh one-shot reserve ACK in the selected existing journal.
    """
    try:
        policies = _object(_raw({'story': story_policy, 'audio': audio_policy}))
        attestation = _attestation(_object(_raw(attestation)))
        _policies(policies)
        _require(type(source_metadata_bytes) is bytes)
        metadata = bytes(source_metadata_bytes)
        audio._metadata(metadata, policies['audio'])
        now = _now(clock)
        with client.pipeline() as pipe:
            pipe.watch(*ALL_KEYS)
            _require(pipe.exists(*ALL_KEYS) == 0, 'retained_successor_already_commissioned_or_partial')
            snapshot, old = _legacy_snapshot(pipe)
            _require(snapshot['snapshot_sha256'] == attestation['predecessor_snapshot_sha256']
                     and snapshot['old_credential_sha256'] == attestation['old_credential_sha256'],
                     'retained_successor_predecessor_changed')
            _unchanged_bindings(old, policies)
            story.RouterReviewJournal(client)._fresh(pipe, policies['story'], now)
            audio.RouterAudioReviewJournal(client)._fresh(pipe, policies['audio'], now)
            context = _archive(pipe, attestation, current=True)
            stamp = now.strftime('%Y-%m-%dT%H:%M:%SZ')
            manifest = _manifest({'version': 1, 'kind': 'included_retained_credential_successor',
                'original_task_id': continuity.ROOT_ID, 'leaf_task_id': continuity.LEAF_ID,
                'created_at': stamp, 'predecessors': snapshot, 'attestation': attestation,
                'policies': policies, 'old_occupied_unknown_count': 2,
                'new_purpose_limits': dict(_PURPOSE_LIMITS), 'max_total_attempts': 6,
                'source_metadata_sha256': _sha(metadata)})
            raw = _raw(manifest)
            initial = {name: {'policy': policy, 'slots': {}, 'updated_at': stamp}
                       for name, policy in policies.items()}
            control = _control_journal(manifest, initial)
            _require(candidate._context() == context, 'retained_successor_credential_changed')
            pipe.multi()
            pipe.set(STATE_KEY, raw.decode())
            pipe.hset(JOURNAL_KEY, mapping=control)
            pipe.set(ANCHOR_KEY, _sha(_raw(control)))
            for name, keys, module in (('story', STORY_KEYS, story), ('audio', AUDIO_KEYS, audio)):
                pipe.set(keys[0], module._json(initial[name]))
                pipe.hset(keys[1], mapping=module._journal(initial[name]))
                pipe.set(keys[2], module._hash(initial[name]))
            ack = pipe.execute()
            expected = [True, 6, True, True, 2, True, True, 2, True]
            _require(type(ack) is list and len(ack) == len(expected)
                     and all(type(a) is type(b) and a == b for a, b in zip(ack, expected)),
                     'retained_successor_ack_unknown')
        _require(candidate._context() == context, 'retained_successor_credential_changed')
        return _authorization(raw)
    except CredentialSuccessorBlocked:
        raise
    except Exception:
        raise CredentialSuccessorBlocked('retained_successor_commission_unverified') from None
