"""One explicit corrected VISUAL/audio sequence after an authenticated HTTP 400.

The rejected request and its unknown slot remain in the old nine-record
controller. This separate permanent controller admits at most three distinct
diagnostics, preserving all 45 predecessors. It never sends, retries an old
request, establishes cash funding or grants production/publication authority.
"""
from app.services import retained_review_captured_story_continuation as base
from app.services import retained_visual_enum_rejection as rejected
from app.services import retained_visual_schema_repair as previous
from app.services import retained_transport_story_evidence as saved
from app.services import abacus_router_schema_compat as compatibility

_PREFIX = 'youtube_studio:{production_spend}:retained_visual_enum_repair:v1:' + base.predecessor.continuity.ROOT_ID
STATE_KEY, JOURNAL_KEY, ANCHOR_KEY = (_PREFIX + s for s in (':state', ':journal', ':commissioned'))
VISUAL_KEYS = tuple(_PREFIX + ':visual' + s for s in (':state', ':journal', ':commissioned'))
AUDIO_KEYS = tuple(_PREFIX + ':audio' + s for s in (':state', ':journal', ':commissioned'))
ALL_KEYS = (STATE_KEY, JOURNAL_KEY, ANCHOR_KEY, *VISUAL_KEYS, *AUDIO_KEYS)
HISTORICAL_KEYS = rejected.HISTORY_KEYS
KIND = 'explicit_included_visual_enum_repair'
_FIXED = {**base._MANIFEST_FIXED, 'version': 3, 'kind': KIND,
          'prior_occupied_count': 7, 'prior_unknown_count': 6, 'max_total_attempts': 10,
          'request_schema_name': compatibility.ENUM_SCHEMA_NAME}
_ATTESTATION_FIXED = {**base._ATTESTATION_FIXED, 'kind': 'operator_included_visual_enum_repair'}
_ATTESTATION_HASHES = {*base._ATTESTATION_HASHES, 'rejected_visual_result_sha256',
                       'schema_rejection_evidence_sha256'}


def _attestation(value):
    base._require(type(value) is dict and set(value) == {*_ATTESTATION_FIXED, *_ATTESTATION_HASHES, 'runtime_head_sha'})
    base._fixed(value, _ATTESTATION_FIXED)
    for name in _ATTESTATION_HASHES:
        base._digest(value[name])
    base._require(type(value['runtime_head_sha']) is str and base.re.fullmatch('[0-9a-f]{40}', value['runtime_head_sha']))
    return value


def _snapshot(qualification):
    bound = rejected.validate_record(qualification)['commitments']
    return {'records': bound['history_records'], 'snapshot_sha256': bound['history_sha256'],
        'schema_rejection_evidence_sha256': base._hash(qualification),
        'credential_sha256': bound['credential_sha256'], 'continuity_sha256': bound['continuity_sha256'],
        'capture_intent_sha256': bound['intent_sha256'], 'capture_anchor_sha256': bound['anchor_sha256'],
        'prior_occupied_count': 7, 'prior_unknown_count': 6}


def _historical(pipe, qualification, *, current=False):
    bound = rejected.validate_record(qualification)['commitments']
    pipe.watch(previous.STATE_KEY)
    encoded_previous = pipe.get(previous.STATE_KEY)
    base._require(type(encoded_previous) is str)
    previous_cap = base._authorization(encoded_previous.encode())
    manifest, states, encoded, context = base._read_control(pipe, current=current, authorization=previous_cap)
    purpose = rejected.PURPOSE
    base._require(base._sha(encoded) == bound['controller_manifest_sha256']
        and set(states['story']['slots']) == {purpose}
        and states['story']['slots'][purpose]['response'] is None and states['audio']['slots'] == {},
        'enum_repair_predecessor_changed')
    source = base.predecessor.continuity._derive(pipe, states['story']['policy']['profile_revision'])
    binding, reservation = saved._binding(states['story'], source, previous.VISUAL_KEYS, purpose=purpose)
    base._require(base._hash(source) == bound['continuity_sha256']
        and base._hash(states['story']) == bound['journal_state_sha256']
        and base._hash(states['story']['policy']) == bound['policy_sha256']
        and base._hash(manifest['story_qualification']) == bound['captured_story_qualification_sha256']
        and binding == bound['binding'] and reservation['reservation_sha256'] == bound['reservation_sha256']
        and binding['credential_sha256'] == bound['credential_sha256']
        and binding['request_sha256'] == bound['request_sha256'])
    records = rejected.history_records(pipe)
    base._require(records == bound['history_records'], 'enum_repair_predecessor_changed')
    pipe.watch(bound['intent_key'], bound['anchor_key'])
    for key in (bound['intent_key'], bound['anchor_key']):
        ttl = pipe.pttl(key)
        base._require(type(ttl) is int and ttl == -1)
    intent = saved._object(pipe.get(bound['intent_key']), saved.capture._RECORD_LIMIT)
    anchor = saved._object(pipe.get(bound['anchor_key']), saved.capture._RECORD_LIMIT)
    base._require(base._hash(intent) == bound['intent_sha256'] and base._hash(anchor) == bound['anchor_sha256']
        and intent['binding'] == anchor['binding'] == binding
        and anchor['intent_sha256'] == bound['intent_sha256']
        and anchor['encrypted_blob'] == bound['encrypted_capture'] and anchor['summary'] == bound['summary'])
    return _snapshot(qualification), states, context


def validate_manifest(value):
    base._require(type(value) is dict and set(value) == {*_FIXED, 'created_at', 'predecessors',
        'attestation', 'policies', 'source_metadata_sha256', 'new_purpose_limits', 'story_qualification',
        'schema_rejection'})
    base._fixed(value, _FIXED)
    qualification = rejected.validate_record(value['schema_rejection'])
    snapshot = _snapshot(qualification)
    attested = _attestation(value['attestation'])
    story = base._qualification(value['story_qualification'])
    policies = base.predecessor._policies(value['policies'])
    base._require(value['predecessors'] == snapshot
        and attested['predecessor_snapshot_sha256'] == snapshot['snapshot_sha256']
        and attested['schema_rejection_evidence_sha256'] == base._hash(qualification)
        and attested['captured_story_evidence_sha256'] == base._hash(story)
        and qualification['commitments']['captured_story_qualification_sha256'] == base._hash(story)
        and snapshot['credential_sha256'] == attested['current_credential_sha256'] == policies[0]['credential_sha256']
        and snapshot['continuity_sha256'] == policies[0]['continuity_sha256']
        and attested['entitlement_evidence_sha256'] == policies[0]['entitlement_evidence_sha256']
        and value['source_metadata_sha256'] == story['commitments']['source_metadata_sha256'] == policies[1]['source_metadata_sha256']
        and story['commitments']['audio'] == policies[1]['audio'])
    limits = value['new_purpose_limits']
    base._require(type(limits) is dict and set(limits) == {p for _, p in base._ORDER}
                  and all(type(n) is int and n == 1 for n in limits.values()))
    stamp = base.predecessor._date(value['created_at'])
    base._require(base.predecessor._date(policies[0]['valid_from']) <= stamp < base.predecessor._date(policies[0]['valid_until']))
    return value


def read_control(pipe, authorization, *, current=False):
    encoded = base._issued_bytes(authorization)
    expected = validate_manifest(base._object(encoded))
    pipe.watch(*ALL_KEYS)
    base._require(all(type(t) is int and t == -1 for t in (pipe.pttl(k) for k in ALL_KEYS)))
    stored = pipe.get(STATE_KEY)
    base._require(type(stored) is str and stored.encode() == encoded)
    snapshot, old, context = _historical(pipe, expected['schema_rejection'], current=current)
    base._require(snapshot == expected['predecessors'])
    base.predecessor._unchanged_bindings(old, expected['policies'])
    states = {'story': base.story.RouterReviewJournal(pipe, captured_story_continuation=authorization)._read_records(pipe),
              'audio': base.audio.RouterAudioReviewJournal(pipe, captured_story_continuation=authorization)._read_records(pipe)}
    base._require(all(states[k]['policy'] == expected['policies'][k] for k in states))
    base._ordered(states)
    control = base._control_journal(expected, states)
    base._require(pipe.hlen(JOURNAL_KEY) == 6 and pipe.hgetall(JOURNAL_KEY) == control
        and pipe.get(ANCHOR_KEY) == base._hash(control), 'enum_repair_heads_changed')
    return expected, states, encoded, context


def read_visual_enum_repair(client):
    """Read one fixed controller without extending its request window."""
    try:
        with client.pipeline() as pipe:
            pipe.watch(*ALL_KEYS)
            stored = pipe.get(STATE_KEY)
            base._require(type(stored) is str)
            cap = base._authorization(stored.encode())
            base._require(base._checked(cap)['kind'] == KIND)
            read_control(pipe, cap)
            base._ping(pipe)
        return cap
    except base.CapturedStoryContinuationBlocked:
        raise
    except Exception:
        raise base.CapturedStoryContinuationBlocked('enum_repair_read_unverified') from None


def commission_visual_enum_repair(client, *, story_evidence, rejection_evidence,
                                    story_policy, audio_policy, attestation, clock=None):
    """Reserve a new bounded correction after two real sealed readers, once."""
    try:
        story = base._evidence(story_evidence)
        base._require(type(rejection_evidence) is rejected.RetainedVisualEnumRejection)
        qualification = rejected.validate_record(rejection_evidence.record)
        base._require(base._hash(story) == qualification['commitments']['captured_story_qualification_sha256'])
        context = base._configuration()
        policies = base._object(base._raw({'story': story_policy, 'audio': audio_policy}))
        base.predecessor._policies(policies)
        attested = _attestation(base._object(base._raw(attestation)))
        base._require(attested['runtime_head_sha'] == context[2])
        now = base._now(clock)
        with client.pipeline() as pipe:
            pipe.watch(*ALL_KEYS)
            base._require(pipe.exists(*ALL_KEYS) == 0, 'enum_repair_already_commissioned_or_partial')
            snapshot, old, actual = _historical(pipe, qualification, current=True)
            base._require(context[:2] == actual)
            base.predecessor._unchanged_bindings(old, policies)
            base.story.RouterReviewJournal(client)._fresh(pipe, policies['story'], now)
            base.audio.RouterAudioReviewJournal(client)._fresh(pipe, policies['audio'], now)
            stamp = now.strftime('%Y-%m-%dT%H:%M:%SZ')
            manifest = validate_manifest({**_FIXED, 'created_at': stamp, 'predecessors': snapshot,
                'attestation': attested, 'policies': policies, 'story_qualification': story,
                'schema_rejection': qualification, 'source_metadata_sha256': policies['audio']['source_metadata_sha256'],
                'new_purpose_limits': {p: 1 for _, p in base._ORDER}})
            encoded = base._raw(manifest)
            states = {kind: {'policy': policy, 'slots': {}, 'updated_at': stamp} for kind, policy in policies.items()}
            control = base._control_journal(manifest, states)
            base._require(base._configuration() == context)
            pipe.multi()
            pipe.set(STATE_KEY, encoded.decode(), nx=True)
            pipe.hset(JOURNAL_KEY, mapping=control)
            pipe.set(ANCHOR_KEY, base._hash(control), nx=True)
            for kind, keys, module in (('story', VISUAL_KEYS, base.story), ('audio', AUDIO_KEYS, base.audio)):
                pipe.set(keys[0], module._json(states[kind]), nx=True)
                pipe.hset(keys[1], mapping=module._journal(states[kind]))
                pipe.set(keys[2], module._hash(states[kind]), nx=True)
            ack = pipe.execute()
            expected = [True, 6, True, True, 2, True, True, 2, True]
            base._require(type(ack) is list and len(ack) == len(expected)
                and all(type(a) is type(b) and a == b for a, b in zip(ack, expected)), 'enum_repair_ack_unknown')
        base._require(base._configuration() == context)
        cap = read_visual_enum_repair(client)
        base._require(base._issued_bytes(cap) == encoded and base._configuration() == context)
        return cap
    except base.CapturedStoryContinuationBlocked:
        raise
    except Exception:
        raise base.CapturedStoryContinuationBlocked('enum_repair_commission_unverified') from None
