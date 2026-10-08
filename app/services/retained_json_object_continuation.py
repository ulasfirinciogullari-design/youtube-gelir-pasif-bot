"""One included JSON Object review sequence after reconnecting the same source.

Old controllers, unknown reservations and transport captures are permanent.
This distinct controller admits VISUAL -> blind ASR -> prosody at most once.
It uses the existing subscription RouteLLM policy with zero new cash allowance;
it neither converts native credits into dollars nor opens the USD foundation.
"""
from app.services import retained_review_captured_story_continuation as base
from app.services import retained_visual_enum_repair as previous
from app.services import retained_story_connection_rebind as rebound
from app.services import retained_transport_story_evidence as saved

_PREFIX = 'youtube_studio:{production_spend}:retained_json_object_continuation:v1:' + base.predecessor.continuity.ROOT_ID
STATE_KEY, JOURNAL_KEY, ANCHOR_KEY = (_PREFIX + s for s in (':state', ':journal', ':commissioned'))
VISUAL_KEYS = tuple(_PREFIX + ':visual' + s for s in (':state', ':journal', ':commissioned'))
AUDIO_KEYS = tuple(_PREFIX + ':audio' + s for s in (':state', ':journal', ':commissioned'))
ALL_KEYS = (STATE_KEY, JOURNAL_KEY, ANCHOR_KEY, *VISUAL_KEYS, *AUDIO_KEYS)
HISTORICAL_KEYS = (*previous.HISTORICAL_KEYS, *previous.ALL_KEYS)
KIND = 'explicit_included_json_object_continuation'
_FIXED = {**base._MANIFEST_FIXED, 'version': 4, 'kind': KIND,
          'prior_occupied_count': 8, 'prior_unknown_count': 7, 'max_total_attempts': 11,
          'request_format': 'json_object'}
_ATTESTATION_FIXED = {**base._ATTESTATION_FIXED, 'kind': 'operator_included_json_object_continuation'}
_ATTESTATION_HASHES = {*base._ATTESTATION_HASHES, 'source_bridge_sha256',
                       'json_object_probe_response_sha256'}
_CHANGED = {'valid_from', 'valid_until', 'entitlement_evidence_sha256',
            'current_connection_id', 'continuity_sha256'}


def _attestation(value):
    base._require(type(value) is dict and set(value) == {*_ATTESTATION_FIXED, *_ATTESTATION_HASHES, 'runtime_head_sha'})
    base._fixed(value, _ATTESTATION_FIXED)
    for name in _ATTESTATION_HASHES:
        base._digest(value[name])
    base._require(type(value['runtime_head_sha']) is str
        and base.re.fullmatch('[0-9a-f]{40}', value['runtime_head_sha']))
    return value


def _snapshot(value):
    base._require(type(value) is dict and set(value) == {'records', 'snapshot_sha256',
        'capture_records', 'capture_snapshot_sha256', 'previous_manifest_sha256',
        'prior_occupied_count', 'prior_unknown_count'})
    base._fixed(value, {'prior_occupied_count': 8, 'prior_unknown_count': 7})
    base._require(type(value['records']) is dict and set(value['records']) == set(HISTORICAL_KEYS)
        and value['snapshot_sha256'] == base._hash(value['records'])
        and type(value['capture_records']) is dict and len(value['capture_records']) == 8
        and value['capture_snapshot_sha256'] == base._hash(value['capture_records']))
    for h in (*value['records'].values(), *value['capture_records'].values(), value['previous_manifest_sha256']):
        base._digest(h)
    return value


def _historical(pipe, qualification):
    """Validate closed records as data; never call the expired controller's guard."""
    base._qualification(qualification)
    pipe.watch(*HISTORICAL_KEYS)
    base._require(all(type(n) is int and n == -1 for n in (pipe.pttl(k) for k in HISTORICAL_KEYS)))
    records = {key: base._hash(pipe.hgetall(key) if i % 3 == 1 else pipe.get(key))
               for i, key in enumerate(HISTORICAL_KEYS)}
    encoded = pipe.get(previous.STATE_KEY)
    manifest = previous.validate_manifest(base._object(encoded))
    cap = base._authorization(encoded.encode())
    states = {'story': base.story.RouterReviewJournal(pipe, captured_story_continuation=cap)._read_records(pipe),
              'audio': base.audio.RouterAudioReviewJournal(pipe, captured_story_continuation=cap)._read_records(pipe)}
    base._require(all(states[k]['policy'] == manifest['policies'][k] for k in states)
        and manifest['story_qualification'] == qualification)
    heads = base._control_journal(manifest, states)
    base._require(pipe.hlen(previous.JOURNAL_KEY) == 6 and pipe.hgetall(previous.JOURNAL_KEY) == heads
        and pipe.get(previous.ANCHOR_KEY) == base._hash(heads))
    visual = base.story.PURPOSES[1]
    base._require(set(states['story']['slots']) == {visual}
        and states['story']['slots'][visual]['response'] is None and states['audio']['slots'] == {},
        'json_object_predecessor_outcome_changed')
    for key, digest in manifest['predecessors']['records'].items():
        base._require(records[key] == digest, 'json_object_predecessor_changed')
    bound = qualification['commitments']
    receipt = base.story._receipt(states['story']['policy'], visual, states['story']['slots'][visual])
    prefix = previous.VISUAL_KEYS[0].rsplit(':', 1)[0] + ':transport_capture:v1:' + visual + ':' + base._hash(receipt)
    captures = (*base.rejection_capture_keys(pipe, cap), bound['intent_key'],
                bound['capture_anchor_key'], prefix + ':intent', prefix + ':anchor')
    base._require(len(captures) == len(set(captures)) == 8)
    pipe.watch(*captures)
    base._require(all(type(n) is int and n == -1 for n in (pipe.pttl(k) for k in captures)))
    capture_records = {key: base._hash(base._object(pipe.get(key))) for key in captures}
    anchor = base._object(pipe.get(prefix + ':anchor'))
    intent = base._object(pipe.get(prefix + ':intent'))
    base._require(anchor['binding'] == intent['binding']
        and anchor['intent_sha256'] == base._hash(intent)
        and anchor['binding']['reservation_sha256'] == base._hash(receipt)
        and anchor['summary']['http_status'] == 400 and anchor['summary']['response_complete'] is True)
    return _snapshot({'records': records, 'snapshot_sha256': base._hash(records),
        'capture_records': capture_records, 'capture_snapshot_sha256': base._hash(capture_records),
        'previous_manifest_sha256': base._sha(encoded.encode()),
        'prior_occupied_count': 8, 'prior_unknown_count': 7}), states


def _policies_against_bridge(old, policies, bridge):
    base.predecessor._policies(policies)
    for kind, state in old.items():
        new = policies[kind]
        base._require(all(type(new[k]) is type(v) and new[k] == v
            for k, v in state['policy'].items() if k not in _CHANGED),
            'json_object_policy_binding_changed')
        base._require(base.predecessor._date(new['valid_from']) >= base.predecessor._date(state['updated_at']))
        base._require(new['current_connection_id'] == bridge['current_source']['current_connection_id']
            and new['continuity_sha256'] == bridge['current_source_sha256']
            and state['policy']['continuity_sha256'] == bridge['historical_source_sha256'])


def validate_manifest(value):
    base._require(type(value) is dict and set(value) == {*_FIXED, 'created_at', 'predecessors',
        'attestation', 'policies', 'source_metadata_sha256', 'new_purpose_limits',
        'story_qualification', 'source_bridge'})
    base._fixed(value, _FIXED)
    snapshot = _snapshot(value['predecessors'])
    attested = _attestation(value['attestation'])
    qualification = base._qualification(value['story_qualification'])
    bridge = rebound.validate_record(value['source_bridge'])
    story, audio = base.predecessor._policies(value['policies'])
    base._require(base._hash(qualification) == bridge['story_qualification_sha256']
        == attested['captured_story_evidence_sha256']
        and qualification['commitments']['continuity_sha256'] == bridge['historical_source_sha256']
        and story['continuity_sha256'] == bridge['current_source_sha256']
        and story['current_connection_id'] == bridge['current_source']['current_connection_id']
        and base._hash(bridge) == attested['source_bridge_sha256']
        and snapshot['snapshot_sha256'] == attested['predecessor_snapshot_sha256']
        and story['credential_sha256'] == attested['current_credential_sha256']
        and story['entitlement_evidence_sha256'] == attested['entitlement_evidence_sha256']
        and value['source_metadata_sha256'] == qualification['commitments']['source_metadata_sha256']
        == audio['source_metadata_sha256'] and qualification['commitments']['audio'] == audio['audio'])
    limits = value['new_purpose_limits']
    base._require(type(limits) is dict and set(limits) == {p for _, p in base._ORDER}
        and all(type(n) is int and n == 1 for n in limits.values()))
    now = base.predecessor._date(value['created_at'])
    base._require(base.predecessor._date(story['valid_from']) <= now < base.predecessor._date(story['valid_until']))
    return value


def read_control(pipe, authorization, *, current=False):
    encoded = base._issued_bytes(authorization)
    manifest = validate_manifest(base._object(encoded))
    pipe.watch(*ALL_KEYS)
    base._require(all(type(n) is int and n == -1 for n in (pipe.pttl(k) for k in ALL_KEYS)))
    base._require(pipe.get(STATE_KEY) == encoded.decode())
    snapshot, old = _historical(pipe, manifest['story_qualification'])
    base._require(snapshot == manifest['predecessors'], 'json_object_predecessor_changed')
    rebound.verify_current_bridge(pipe, manifest['source_bridge'], manifest['story_qualification'])
    _policies_against_bridge(old, manifest['policies'], manifest['source_bridge'])
    context = base._configuration()
    base._require(base._sha(('abacus\0' + context[1]).encode()) == manifest['policies']['story']['credential_sha256'])
    if current:
        base._require(context[2] == manifest['attestation']['runtime_head_sha'])
    states = {'story': base.story.RouterReviewJournal(pipe, captured_story_continuation=authorization)._read_records(pipe),
              'audio': base.audio.RouterAudioReviewJournal(pipe, captured_story_continuation=authorization)._read_records(pipe)}
    base._require(all(states[k]['policy'] == manifest['policies'][k] for k in states))
    base._ordered(states)
    heads = base._control_journal(manifest, states)
    base._require(pipe.hlen(JOURNAL_KEY) == 6 and pipe.hgetall(JOURNAL_KEY) == heads
        and pipe.get(ANCHOR_KEY) == base._hash(heads), 'json_object_heads_changed')
    return manifest, states, encoded, context[:2]


def read_json_object_continuation(client):
    try:
        with client.pipeline() as pipe:
            pipe.watch(STATE_KEY)
            encoded = pipe.get(STATE_KEY)
            base._require(type(encoded) is str)
            cap = base._authorization(encoded.encode())
            base._require(base._checked(cap)['kind'] == KIND)
            read_control(pipe, cap)
            base._ping(pipe)
        return cap
    except base.CapturedStoryContinuationBlocked:
        raise
    except Exception:
        raise base.CapturedStoryContinuationBlocked('json_object_continuation_read_unverified') from None


def commission_json_object_continuation(client, *, reconnected_story, story_policy,
                                        audio_policy, attestation, clock=None):
    """Explicit single-use admission; no API, retry, cash initialization or claim."""
    try:
        base._require(type(reconnected_story) is rebound.ReconnectedStoryEvidence)
        bridge = reconnected_story.record
        qualification = base._evidence(reconnected_story.story_evidence)
        context = base._configuration()
        policies = base._object(base._raw({'story': story_policy, 'audio': audio_policy}))
        attested = _attestation(base._object(base._raw(attestation)))
        base._require(attested['runtime_head_sha'] == context[2])
        now = base._now(clock)
        with client.pipeline() as pipe:
            pipe.watch(*ALL_KEYS)
            base._require(pipe.exists(*ALL_KEYS) == 0, 'json_object_already_commissioned_or_partial')
            snapshot, old = _historical(pipe, qualification)
            rebound.verify_current_bridge(pipe, bridge, qualification)
            _policies_against_bridge(old, policies, bridge)
            base.story.RouterReviewJournal(client)._fresh(pipe, policies['story'], now)
            base.audio.RouterAudioReviewJournal(client)._fresh(pipe, policies['audio'], now)
            base._require(base._sha(('abacus\0' + context[1]).encode()) == policies['story']['credential_sha256'])
            stamp = now.strftime('%Y-%m-%dT%H:%M:%SZ')
            manifest = validate_manifest({**_FIXED, 'created_at': stamp, 'predecessors': snapshot,
                'attestation': attested, 'policies': policies, 'story_qualification': qualification,
                'source_bridge': bridge, 'source_metadata_sha256': policies['audio']['source_metadata_sha256'],
                'new_purpose_limits': {p: 1 for _, p in base._ORDER}})
            encoded = base._raw(manifest)
            states = {kind: {'policy': p, 'slots': {}, 'updated_at': stamp} for kind, p in policies.items()}
            heads = base._control_journal(manifest, states)
            base._require(base._configuration() == context)
            pipe.multi()
            pipe.set(STATE_KEY, encoded.decode(), nx=True)
            pipe.hset(JOURNAL_KEY, mapping=heads)
            pipe.set(ANCHOR_KEY, base._hash(heads), nx=True)
            for kind, keys, module in (('story', VISUAL_KEYS, base.story), ('audio', AUDIO_KEYS, base.audio)):
                pipe.set(keys[0], module._json(states[kind]), nx=True)
                pipe.hset(keys[1], mapping=module._journal(states[kind]))
                pipe.set(keys[2], module._hash(states[kind]), nx=True)
            ack = pipe.execute()
            expected = [True, 6, True, True, 2, True, True, 2, True]
            base._require(type(ack) is list and len(ack) == len(expected)
                and all(type(a) is type(b) and a == b for a, b in zip(ack, expected)), 'json_object_ack_unknown')
        result = read_json_object_continuation(client)
        base._require(base._issued_bytes(result) == encoded and base._configuration() == context)
        return result
    except base.CapturedStoryContinuationBlocked:
        raise
    except Exception:
        raise base.CapturedStoryContinuationBlocked('json_object_commission_unverified') from None
