"""Explicit captured-STORY continuation; all twenty-seven predecessors stay closed.

The saved positive STORY component is a separate authenticated interpretation,
never an observed settlement. Audio reservation additionally requires the
owning scope's genuine VISUAL binding and, for PROSODY, persisted exact ASR.
No API sends, retries, initializes cash, claims a job or grants publication.
"""
import weakref
from datetime import datetime, timezone
import hashlib
import json
import os
import re

from app.services import abacus_router_review_journal as story
from app.services import abacus_router_audio_review_journal as audio
from app.services import retained_review_credential_successor as predecessor
from app.services import retained_router_protocol_probe as probe
from app.services import retained_review_completion_plan as completion
from app.services import provider_key_candidate as candidate
from app.services.production_spend import SpendBlocked


_PREFIX = 'youtube_studio:{production_spend}:retained_captured_story_continuation:v1:' + predecessor.continuity.ROOT_ID
STATE_KEY, JOURNAL_KEY, ANCHOR_KEY = (_PREFIX + part for part in (':state', ':journal', ':commissioned'))
VISUAL_KEYS = tuple(_PREFIX + ':visual' + part for part in (':state', ':journal', ':commissioned'))
AUDIO_KEYS = tuple(_PREFIX + ':audio' + part for part in (':state', ':journal', ':commissioned'))
ALL_KEYS = (STATE_KEY, JOURNAL_KEY, ANCHOR_KEY, *VISUAL_KEYS, *AUDIO_KEYS)
HISTORICAL_KEYS = (*completion.HISTORICAL_KEYS, *completion.ALL_KEYS)
_ORDER = (('story', story.PURPOSES[1]),
          ('audio', audio.PURPOSES[0].value), ('audio', audio.PURPOSES[1].value))
_ISSUED = weakref.WeakKeyDictionary()
_MAX = 1024 * 1024
_FLAGS = {'diagnostic_only': True, 'qa_approved': False, 'publish_eligible': False,
          'account_plan_verified': False, 'completion_or_funding_verified': False,
          'pre_observer_capture_verified': False, 'semantic_acceptance_verified': False,
          'full_qa_complete': False, 'claim_authorized': False, 'render_authorized': False,
          'resume_authorized': False, 'retry_authorized': False}
_ATTESTATION_HASHES = {'owner_authorization_sha256', 'operator_attempt_sha256',
    'predecessor_snapshot_sha256', 'captured_story_evidence_sha256',
    'captured_story_failure_result_sha256', 'current_credential_sha256',
    'entitlement_evidence_sha256', 'official_terms_sha256'}
_ATTESTATION_FIXED = {'version': 1, 'kind': 'operator_included_captured_story_continuation',
    'monthly_additional_cash_limit_micro': 10_000_000, 'included_cash_allowance_micro': 0,
    'historical_extra_cash_micro': None, 'account_plan_verified': False,
    'completion_or_funding_verified': False, 'pre_observer_capture_required': True,
    'source_bound_semantic_gates_required': True}
_MANIFEST_FIXED = {'version': 1, 'kind': 'explicit_included_captured_story_continuation',
    **_FLAGS,
    'original_task_id': predecessor.continuity.ROOT_ID, 'leaf_task_id': predecessor.continuity.LEAF_ID,
    'prior_occupied_count': 5, 'prior_unknown_count': 4, 'additional_attempt_limit': 3,
    'max_total_attempts': 8, 'pre_observer_capture_required': True,
    'source_bound_semantic_gates_required': True}


class CapturedStoryContinuationBlocked(SpendBlocked):
    """Fixed local messages only; no raw store/provider details."""


def _require(value, reason='captured_story_continuation_unverified'):
    if not value:
        raise CapturedStoryContinuationBlocked(reason)


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
    _require(config.studio_spend_enforcement is True, 'captured_story_continuation_cash_lock_changed')
    caps = _object(config.studio_spend_policy_json)
    _require(set(caps) == probe._CAPS and all(type(n) is int and n == 0 for n in caps.values()),
             'captured_story_continuation_cash_lock_changed')
    context = candidate._context()
    head = os.environ.get('RAILWAY_GIT_COMMIT_SHA', '')
    _require(re.fullmatch('[0-9a-f]{40}', head) is not None, 'captured_story_continuation_runtime_changed')
    return (*context, head)


def _attestation(value):
    _require(type(value) is dict and set(value) == {*_ATTESTATION_FIXED, *_ATTESTATION_HASHES, 'runtime_head_sha'})
    _fixed(value, _ATTESTATION_FIXED)
    for key in _ATTESTATION_HASHES:
        _digest(value[key])
    _require(type(value['runtime_head_sha']) is str and re.fullmatch('[0-9a-f]{40}', value['runtime_head_sha']))
    return value


def _qualification(value):
    from app.services import retained_transport_story_evidence as evidence
    from app.services import abacus_router_adapter as adapter
    expected = {'version': 1, 'kind': 'retained_transport_story_component_evidence',
        **evidence._FLAGS, 'provenance': 'authenticated_preobserver_transport',
        'component_pass': True, 'preclaim_only': True, 'read_acknowledged': True,
        'capture_anchor_read_verified': True, 'saved_header_summary_only': True,
        'historical_record_count': 27, 'selected_occupied_count': 1,
        'selected_unknown_count': 1, 'prior_occupied_count': 4, 'prior_unknown_count': 3,
        'response_status_code': 200}
    _require(type(value) is dict and set(value) == {*expected, 'response_bytes', 'commitments'})
    _fixed(value, expected)
    _require(type(value['response_bytes']) is int and 0 < value['response_bytes'] <= adapter.MAX_RESPONSE_BYTES)
    bound = value['commitments']
    hashes = {'completion_manifest_sha256', 'completion_journal_sha256', 'completion_anchor_sha256',
        'predecessor_snapshot_sha256', 'completed_probe_capture_sha256', 'journal_state_sha256',
        'policy_sha256', 'credential_sha256', 'continuity_sha256', 'source_state_sha256',
        'source_spec_sha256', 'source_journal_sha256', 'source_metadata_sha256',
        'immutable_core_sha256', 'intent_sha256', 'capture_anchor_sha256',
        'capture_context_sha256', 'reservation_sha256', 'request_sha256', 'prepared_sha256',
        'wire_sha256', 'response_sha256', 'parsed_result_sha256', 'usage_sha256',
        'semantic_diagnostic_sha256'}
    _require(type(bound) is dict and set(bound) == hashes | {
        'journal_keys', 'audio', 'intent_key', 'capture_anchor_key', 'encrypted_capture'})
    for name in hashes:
        _digest(bound[name])
    _require(bound['journal_keys'] == list(completion.STORY_KEYS))
    from app.services import abacus_router_transport_capture as capture
    from app.services import retained_transport_story_evidence as reader
    prefix = (completion.STORY_KEYS[0].rsplit(':', 1)[0] + ':transport_capture:v1:'
              + story.PURPOSES[0] + ':' + bound['reservation_sha256'])
    _require(bound['intent_key'] == prefix + ':intent' and bound['capture_anchor_key'] == prefix + ':anchor')
    pointer = bound['encrypted_capture']
    _require(type(pointer) is dict and set(pointer) == {'key', 'sha256', 'size', 'content_type'})
    _digest(pointer['sha256'])
    _require(type(pointer['size']) is int and 0 < pointer['size'] <= reader._MAX_CIPHER
        and pointer['content_type'] == capture._CONTENT_TYPE
        and pointer['key'] == f'recovery/{predecessor.continuity.LEAF_ID}/router_transport/v1/'
        f'{_hash(bound["journal_keys"])}/{story.PURPOSES[0]}/{bound["reservation_sha256"]}/{pointer["sha256"]}.fernet')
    return value


def _evidence(value):
    from app.services.retained_transport_story_evidence import RetainedTransportStoryEvidence
    _require(type(value) is RetainedTransportStoryEvidence, 'captured_story_continuation_evidence_invalid')
    # .record validates issuer-owned immutable bytes; object.__new__/copy/dicts are not authority.
    return _qualification(value.record)


def _snapshot(value):
    _require(type(value) is dict and set(value) == {'records', 'snapshot_sha256',
        'completion_manifest_sha256', 'credential_sha256', 'continuity_sha256',
        'capture_intent_sha256', 'capture_anchor_sha256', 'prior_occupied_count', 'prior_unknown_count'})
    _fixed(value, {'prior_occupied_count': 5, 'prior_unknown_count': 4})
    _require(type(value['records']) is dict and set(value['records']) == set(HISTORICAL_KEYS))
    for digest in value['records'].values():
        _digest(digest)
    for key in ('snapshot_sha256', 'completion_manifest_sha256', 'credential_sha256',
                'continuity_sha256', 'capture_intent_sha256', 'capture_anchor_sha256'):
        _digest(value[key])
    _require(value['snapshot_sha256'] == _hash(value['records']))
    return value


def _historical(pipe, qualification, *, current=False):
    from app.services import retained_transport_story_evidence as evidence
    from app.services import preserved_visual_recovery as recovery
    from app.services import abacus_router_transport_capture as capture
    bound = _qualification(qualification)['commitments']
    pipe.watch(*HISTORICAL_KEYS, bound['intent_key'], bound['capture_anchor_key'])
    _require(all(type(ttl) is int and ttl == -1 for ttl in
        (pipe.pttl(k) for k in (*HISTORICAL_KEYS, bound['intent_key'], bound['capture_anchor_key']))),
        'captured_story_continuation_history_not_durable')
    manifest, states, raw, context = completion._read_control(pipe, current=current)
    state = states['story']
    _require(set(state['slots']) == {story.PURPOSES[0]}
        and state['slots'][story.PURPOSES[0]]['response'] is None and states['audio']['slots'] == {},
        'captured_story_continuation_history_shape_changed')
    source = predecessor.continuity._derive(pipe, state['policy']['profile_revision'])
    pipe.watch(*recovery._keys(predecessor.continuity.LEAF_ID))
    original, fingerprint = recovery._state(predecessor.continuity.LEAF_ID, pipe)
    binding, receipt = evidence._binding(state, source, completion.STORY_KEYS)
    actual = {'completion_manifest_sha256': _sha(raw),
        'completion_journal_sha256': _hash(pipe.hgetall(completion.JOURNAL_KEY)),
        'completion_anchor_sha256': pipe.get(completion.ANCHOR_KEY),
        'predecessor_snapshot_sha256': manifest['predecessors']['snapshot_sha256'],
        'completed_probe_capture_sha256': manifest['predecessors']['probe_capture_sha256'],
        'journal_state_sha256': _hash(state), 'policy_sha256': _hash(state['policy']),
        'credential_sha256': state['policy']['credential_sha256'], 'continuity_sha256': _hash(source),
        'source_state_sha256': fingerprint, 'source_spec_sha256': _hash(original['spec']),
        'source_journal_sha256': _hash(original['generated_asset_candidates']),
        'source_metadata_sha256': states['audio']['policy']['source_metadata_sha256'],
        'audio': states['audio']['policy']['audio'], 'reservation_sha256': receipt['reservation_sha256'],
        'request_sha256': receipt['request_sha256']}
    _require(_raw(actual) == _raw({k: bound[k] for k in actual}),
             'captured_story_continuation_evidence_binding_changed')
    intent = _object(pipe.get(bound['intent_key']))
    anchor = _object(pipe.get(bound['capture_anchor_key']))
    _require(_hash(intent) == bound['intent_sha256'] and _hash(anchor) == bound['capture_anchor_sha256']
        and intent['binding'] == binding and anchor['binding'] == binding
        and anchor['intent_sha256'] == bound['intent_sha256']
        and anchor['encrypted_blob'] == bound['encrypted_capture'],
        'captured_story_continuation_capture_changed')
    _fixed(intent, capture._FLAGS); _fixed(anchor, capture._FLAGS)
    records = {key: _hash(pipe.hgetall(key) if index % 3 == 1 else pipe.get(key))
               for index, key in enumerate(HISTORICAL_KEYS)}
    result = {'records': records, 'snapshot_sha256': _hash(records),
        'completion_manifest_sha256': _sha(raw), 'credential_sha256': bound['credential_sha256'],
        'continuity_sha256': bound['continuity_sha256'], 'capture_intent_sha256': bound['intent_sha256'],
        'capture_anchor_sha256': bound['capture_anchor_sha256'],
        'prior_occupied_count': 5, 'prior_unknown_count': 4}
    return _snapshot(result), states, context


def _ping(pipe):
    pipe.multi(); pipe.ping()
    ack = pipe.execute()
    _require(type(ack) is list and len(ack) == 1 and ack[0] is True, 'captured_story_continuation_ack_unknown')


def snapshot_captured_story_predecessors(client, *, story_evidence):
    """Read the exact twenty-seven records and immutable capture, without admission."""
    try:
        qualification = _evidence(story_evidence)
        with client.pipeline() as pipe:
            snapshot, _, _ = _historical(pipe, qualification)
            _ping(pipe)
        return snapshot
    except CapturedStoryContinuationBlocked:
        raise
    except Exception:
        raise CapturedStoryContinuationBlocked('captured_story_continuation_history_unverified') from None


def _manifest(value):
    if type(value) is dict and value.get('kind') == 'explicit_included_visual_schema_repair':
        from app.services.retained_visual_schema_repair import validate_manifest
        return validate_manifest(value)
    _require(type(value) is dict and set(value) == {*_MANIFEST_FIXED, 'created_at', 'predecessors',
        'attestation', 'policies', 'source_metadata_sha256', 'new_purpose_limits', 'story_qualification'})
    _fixed(value, _MANIFEST_FIXED)
    limits = value['new_purpose_limits']
    _require(type(limits) is dict and set(limits) == {purpose for _, purpose in _ORDER}
             and all(type(n) is int and n == 1 for n in limits.values()))
    policies = predecessor._policies(value['policies'])
    qualification = _qualification(value['story_qualification'])
    snapshot = _snapshot(value['predecessors'])
    attestation = _attestation(value['attestation'])
    _require(snapshot['snapshot_sha256'] == attestation['predecessor_snapshot_sha256']
             and _hash(qualification) == attestation['captured_story_evidence_sha256']
             and snapshot['credential_sha256'] == attestation['current_credential_sha256']
             == policies[0]['credential_sha256']
             and snapshot['continuity_sha256'] == policies[0]['continuity_sha256']
             and policies[0]['entitlement_evidence_sha256'] == attestation['entitlement_evidence_sha256'])
    _require(qualification['commitments']['audio'] == policies[1]['audio']
             and qualification['commitments']['source_metadata_sha256'] == policies[1]['source_metadata_sha256'])
    _digest(value['source_metadata_sha256'])
    _require(value['source_metadata_sha256'] == policies[1]['source_metadata_sha256'])
    stamp = predecessor._date(value['created_at'])
    _require(predecessor._date(policies[0]['valid_from']) <= stamp < predecessor._date(policies[0]['valid_until']))
    return value


class CapturedStoryContinuationAuthorization:
    """Issuer-owned immutable selection; never a quality or next-stage permit."""
    __slots__ = ('__weakref__',)

    def __new__(cls, *args, **kwargs):
        raise TypeError('captured_story_continuation_private')

    def __repr__(self):
        return '<CapturedStoryContinuationAuthorization diagnostic-only>'

    @property
    def receipt(self):
        value = _checked(self)
        return {'version': value['version'], 'manifest_sha256': _sha(_issued_bytes(self)),
            'predecessor_snapshot_sha256': value['predecessors']['snapshot_sha256'],
            'captured_story_evidence_sha256': _hash(value['story_qualification']),
            'prior_occupied_count': value['prior_occupied_count'],
            'prior_unknown_count': value['prior_unknown_count'], 'additional_attempt_limit': 3,
            'max_total_attempts': value['max_total_attempts'], 'pre_observer_capture_required': True,
            'source_bound_semantic_gates_required': True, **_FLAGS}


def _issued_bytes(authorization):
    _require(type(authorization) is CapturedStoryContinuationAuthorization
             and authorization in _ISSUED, 'captured_story_continuation_capability_invalid')
    return _ISSUED[authorization]


def _checked(authorization):
    raw = _issued_bytes(authorization)
    value = _manifest(_object(raw))
    _require(_raw(value) == raw)
    return value


def _authorization(raw):
    _manifest(_object(raw))
    value = object.__new__(CapturedStoryContinuationAuthorization)
    _ISSUED[value] = raw
    return value


def selected_keys(authorization, kind):
    value = _checked(authorization)
    _require(type(kind) is str and kind in ('story', 'audio'))
    if value['version'] == 2:
        from app.services import retained_visual_schema_repair as repair
        return repair.VISUAL_KEYS if kind == 'story' else repair.AUDIO_KEYS
    return VISUAL_KEYS if kind == 'story' else AUDIO_KEYS


def uses_schema_compatibility(authorization):
    return _checked(authorization)['version'] == 2


def controller_keys(authorization):
    if uses_schema_compatibility(authorization):
        from app.services.retained_visual_schema_repair import ALL_KEYS as keys
        return keys
    return ALL_KEYS


def historical_keys(authorization):
    if uses_schema_compatibility(authorization):
        from app.services.retained_visual_schema_repair import HISTORICAL_KEYS as keys
        return keys
    return HISTORICAL_KEYS


def _control_journal(manifest, states):
    return {'manifest_sha256': _hash(manifest),
        'predecessor_snapshot_sha256': manifest['predecessors']['snapshot_sha256'],
        'story_policy_sha256': _hash(manifest['policies']['story']),
        'audio_policy_sha256': _hash(manifest['policies']['audio']),
        'story_state_sha256': _hash(states['story']), 'audio_state_sha256': _hash(states['audio'])}


def _ordered(states):
    _require(set(states['story']['slots']) <= {story.PURPOSES[1]}
             and set(states['audio']['slots']) <= {p.value for p in audio.PURPOSES},
             'captured_story_continuation_story_forbidden')
    occupied = [(kind, purpose) for kind, purpose in _ORDER if purpose in states[kind]['slots']]
    _require(occupied == list(_ORDER[:len(occupied)]), 'captured_story_continuation_order_changed')
    for kind, purpose in occupied[:-1]:
        _require(states[kind]['slots'][purpose]['response'] is not None,
                 'captured_story_continuation_previous_outcome_unknown')
    return occupied


def _read_control(pipe, *, current=False, authorization=None):
    if authorization is not None and uses_schema_compatibility(authorization):
        from app.services.retained_visual_schema_repair import read_control
        return read_control(pipe, authorization, current=current)
    pipe.watch(*ALL_KEYS)
    _require(all(type(ttl) is int and ttl == -1 for ttl in (pipe.pttl(k) for k in ALL_KEYS)),
             'captured_story_continuation_not_durable')
    stored = pipe.get(STATE_KEY)
    raw = stored.encode() if type(stored) is str else stored
    manifest = _manifest(_object(raw))
    _require(raw == _raw(manifest))
    snapshot, old, context = _historical(pipe, manifest['story_qualification'], current=current)
    _require(snapshot == manifest['predecessors'], 'captured_story_continuation_history_changed')
    predecessor._unchanged_bindings(old, manifest['policies'])
    _require(manifest['policies']['story']['credential_sha256'] == snapshot['credential_sha256'])
    predecessor._source(pipe, manifest['policies'])
    cap = _authorization(raw)
    states = {'story': story.RouterReviewJournal(pipe, captured_story_continuation=cap)._read_records(pipe),
              'audio': audio.RouterAudioReviewJournal(pipe, captured_story_continuation=cap)._read_records(pipe)}
    _require(all(state['policy'] == manifest['policies'][kind] for kind, state in states.items()))
    _ordered(states)
    control = _control_journal(manifest, states)
    _require(pipe.hlen(JOURNAL_KEY) == 6 and pipe.hgetall(JOURNAL_KEY) == control
             and pipe.get(ANCHOR_KEY) == _hash(control), 'captured_story_continuation_heads_changed')
    return manifest, states, raw, context


def read_captured_story_continuation(client):
    """Select verified history without renewing a window or permitting a send."""
    try:
        with client.pipeline() as pipe:
            _, _, raw, _ = _read_control(pipe)
            _ping(pipe)
        return _authorization(raw)
    except CapturedStoryContinuationBlocked:
        raise
    except Exception:
        raise CapturedStoryContinuationBlocked('captured_story_continuation_read_unverified') from None


def verify_scope_captured_story_continuation(client, authorization):
    _checked(authorization)
    try:
        context = _configuration()
        with client.pipeline() as pipe:
            manifest, _, raw, actual = _read_control(pipe, current=True, authorization=authorization)
            _require(raw == _issued_bytes(authorization) and actual == context[:2]
                     and manifest['attestation']['runtime_head_sha'] == context[2],
                     'captured_story_continuation_capability_changed')
            _ping(pipe)
        _require(_configuration() == context, 'captured_story_continuation_configuration_changed')
    except CapturedStoryContinuationBlocked:
        raise
    except Exception:
        raise CapturedStoryContinuationBlocked('captured_story_continuation_scope_unverified') from None


def guard_selected(pipe, authorization, kind, state):
    selected_keys(authorization, kind)
    _, states, raw, _ = _read_control(pipe, authorization=authorization)
    _require(raw == _issued_bytes(authorization) and states[kind] == state,
             'captured_story_continuation_capability_changed')


def guard_mutation(pipe, authorization, kind, *, reserve=False):
    selected_keys(authorization, kind)
    manifest, states, raw, context = _read_control(pipe, current=reserve, authorization=authorization)
    _require(raw == _issued_bytes(authorization), 'captured_story_continuation_capability_changed')
    if reserve:
        config = _configuration()
        _require(config[:2] == context and config[2] == manifest['attestation']['runtime_head_sha'],
                 'captured_story_continuation_configuration_changed')
        occupied = _ordered(states)
        _require(len(occupied) < 3 and _ORDER[len(occupied)][0] == kind,
                 'captured_story_continuation_attempt_limit_or_order')
        _require(all(states[k]['slots'][p]['response'] is not None for k, p in occupied),
                 'captured_story_continuation_previous_outcome_unknown')
        if kind == 'audio':
            from app.services.retained_captured_visual_scope import require_audio_predecessors
            require_audio_predecessors(pipe, authorization, _ORDER[len(occupied)][1])


def commit_selected(pipe, authorization, kind, state, old_journal):
    """One selected transition plus independent heads; never alter predecessors."""
    keys = selected_keys(authorization, kind)
    manifest, states, raw, _ = _read_control(pipe, authorization=authorization)
    module = story if kind == 'story' else audio
    old = states[kind]
    _require(raw == _issued_bytes(authorization) and old_journal == module._journal(old)
             and state['policy'] == old['policy'], 'captured_story_continuation_capability_changed')
    added = set(state['slots']) - set(old['slots'])
    if added:
        occupied = _ordered(states)
        _require(len(added) == 1 and len(occupied) < 3
                 and (kind, next(iter(added))) == _ORDER[len(occupied)]
                 and state['slots'][next(iter(added))]['response'] is None
                 and all(state['slots'].get(p) == slot for p, slot in old['slots'].items())
                 and all(states[k]['slots'][p]['response'] is not None for k, p in occupied),
                 'captured_story_continuation_transition_invalid')
        if kind == 'audio':
            from app.services.retained_captured_visual_scope import require_audio_predecessors
            purpose = next(iter(added))
            require_audio_predecessors(pipe, authorization, purpose, proposed_slot=state['slots'][purpose])
    else:
        _require(set(state['slots']) == set(old['slots']), 'captured_story_continuation_transition_invalid')
        changed = [p for p in old['slots'] if state['slots'][p] != old['slots'][p]]
        _require(len(changed) == 1, 'captured_story_continuation_transition_invalid')
        purpose = changed[0]
        before, after = old['slots'][purpose], state['slots'][purpose]
        _require(before['response'] is None and after['response'] is not None
                 and {k: v for k, v in before.items() if k != 'response'}
                 == {k: v for k, v in after.items() if k != 'response'},
                 'captured_story_continuation_transition_invalid')
    states[kind] = state
    _ordered(states)
    journal, control = module._journal(state), _control_journal(manifest, states)
    pipe.multi()
    pipe.set(keys[0], module._json(state))
    pipe.hset(keys[1], mapping=journal)
    pipe.set(keys[2], module._hash(state))
    controls = controller_keys(authorization)
    pipe.hset(controls[1], mapping=control)
    pipe.set(controls[2], _hash(control))
    ack = pipe.execute()
    expected = [True, len(set(journal) - set(old_journal)), True, 0, True]
    _require(type(ack) is list and len(ack) == len(expected)
             and all(type(a) is type(b) and a == b for a, b in zip(ack, expected)),
             'captured_story_continuation_ack_unknown')


def commission_captured_story_continuation(client, *, story_evidence, story_policy, audio_policy,
                                           attestation, clock=None):
    """Create nine records once, after a real saved-STORY reader and watched revalidation."""
    try:
        qualification = _evidence(story_evidence)
        context = _configuration()
        policies = _object(_raw({'story': story_policy, 'audio': audio_policy}))
        predecessor._policies(policies)
        attestation = _attestation(_object(_raw(attestation)))
        _require(attestation['runtime_head_sha'] == context[2])
        _require(policies['audio']['source_metadata_sha256'] == qualification['commitments']['source_metadata_sha256']
                 and policies['audio']['audio'] == qualification['commitments']['audio'])
        now = _now(clock)
        with client.pipeline() as pipe:
            pipe.watch(*ALL_KEYS)
            _require(pipe.exists(*ALL_KEYS) == 0, 'captured_story_continuation_already_commissioned_or_partial')
            snapshot, old, historical_context = _historical(pipe, qualification, current=True)
            _require(context[:2] == historical_context)
            predecessor._unchanged_bindings(old, policies)
            _require(policies['story']['credential_sha256'] == snapshot['credential_sha256'])
            story.RouterReviewJournal(client)._fresh(pipe, policies['story'], now)
            audio.RouterAudioReviewJournal(client)._fresh(pipe, policies['audio'], now)
            stamp = now.strftime('%Y-%m-%dT%H:%M:%SZ')
            manifest = _manifest({**_MANIFEST_FIXED, 'created_at': stamp, 'predecessors': snapshot,
                'attestation': attestation, 'policies': policies,
                'source_metadata_sha256': qualification['commitments']['source_metadata_sha256'],
                'story_qualification': qualification,
                'new_purpose_limits': {purpose: 1 for _, purpose in _ORDER}})
            raw = _raw(manifest)
            states = {kind: {'policy': policy, 'slots': {}, 'updated_at': stamp}
                      for kind, policy in policies.items()}
            control = _control_journal(manifest, states)
            _require(_configuration() == context, 'captured_story_continuation_configuration_changed')
            pipe.multi()
            pipe.set(STATE_KEY, raw.decode(), nx=True)
            pipe.hset(JOURNAL_KEY, mapping=control)
            pipe.set(ANCHOR_KEY, _hash(control), nx=True)
            for kind, keys, module in (('story', VISUAL_KEYS, story), ('audio', AUDIO_KEYS, audio)):
                pipe.set(keys[0], module._json(states[kind]), nx=True)
                pipe.hset(keys[1], mapping=module._journal(states[kind]))
                pipe.set(keys[2], module._hash(states[kind]), nx=True)
            ack = pipe.execute()
            expected = [True, 6, True, True, 2, True, True, 2, True]
            _require(type(ack) is list and len(ack) == len(expected)
                     and all(type(a) is type(b) and a == b for a, b in zip(ack, expected)),
                     'captured_story_continuation_ack_unknown')
        _require(_configuration() == context, 'captured_story_continuation_configuration_changed')
        result = read_captured_story_continuation(client)
        _require(_issued_bytes(result) == raw and _configuration() == context,
                 'captured_story_continuation_readback_changed')
        return result
    except CapturedStoryContinuationBlocked:
        raise
    except Exception:
        raise CapturedStoryContinuationBlocked('captured_story_continuation_commission_unverified') from None
