"""Read a saved STORY after reconnecting the same channel, without renewing it.

Only four OAuth metadata fields may differ. Every profile, topic, job, candidate,
audio and request byte remains bound to the authenticated historical source.
The result is data for a separate, explicit current-source admission. It grants
no request, cash, settlement, render or publication permission.
"""
import weakref

from app.services import retained_transport_story_evidence as saved
from app.services import retained_review_captured_story_continuation as continuation
from app.services import retained_review_completion_plan as completion
from app.services import abacus_router_review_journal as journal
from app.services import abacus_router_audio_review_journal as audio
from app.services import production_connection_continuity as continuity
from app.services import preserved_visual_recovery as recovery
from app.services import retained_cut_evidence as cuts

_OAUTH_FIELDS = frozenset({'current_connection_id', 'authorization_epoch',
                          'credential_cipher_sha256', 'channel_record_sha256'})
HISTORY_KEYS = (*completion.HISTORICAL_KEYS, *completion.ALL_KEYS)
_ISSUED = weakref.WeakKeyDictionary()
_FLAGS = {'diagnostic_only': True, 'qa_approved': False, 'publish_eligible': False,
          'request_authorized': False, 'cash_authorized': False,
          'settlement_observed': False, 'fresh_google_identity_verified': False}
_require, _raw, _hash = saved._require, saved._raw, saved._hash


def verify_unchanged_content(historical, current):
    """Compare authenticated source data, permitting only OAuth metadata drift."""
    _require(type(historical) is dict and type(current) is dict
        and set(historical) == set(current) and _OAUTH_FIELDS <= set(current),
        'retained_story_reconnect_source_invalid')
    _require(_raw({k: v for k, v in historical.items() if k not in _OAUTH_FIELDS})
        == _raw({k: v for k, v in current.items() if k not in _OAUTH_FIELDS}),
        'retained_story_reconnect_content_changed')
    for source in (historical, current):
        _require(source.get('channel_id') == continuity.CHANNEL_ID
            and source.get('original_task_id') == continuity.ROOT_ID
            and source.get('leaf_task_id') == continuity.LEAF_ID)
        continuity._identifier(source['current_connection_id'])
        for key in ('credential_cipher_sha256', 'channel_record_sha256'):
            continuation._digest(source[key])
        _require(type(source['authorization_epoch']) is str
            and continuation.re.fullmatch(r'[1-9][0-9]{0,19}', source['authorization_epoch']))
    return sorted(k for k in _OAUTH_FIELDS if _raw(historical[k]) != _raw(current[k]))


def history_records(pipe):
    pipe.watch(*HISTORY_KEYS)
    _require(all(type(n) is int and n == -1 for n in (pipe.pttl(k) for k in HISTORY_KEYS)))
    return {key: _hash(pipe.hgetall(key) if i % 3 == 1 else pipe.get(key))
            for i, key in enumerate(HISTORY_KEYS)}


def validate_record(value):
    fixed = {'version': 1, 'kind': 'retained_story_same_content_reconnection', **_FLAGS}
    _require(type(value) is dict and set(value) == {*fixed, 'historical_source', 'current_source',
        'historical_source_sha256', 'current_source_sha256', 'story_qualification_sha256',
        'history_records', 'history_sha256', 'changed_fields'})
    continuation._fixed(value, fixed)
    _require(value['historical_source_sha256'] == _hash(value['historical_source'])
        and value['current_source_sha256'] == _hash(value['current_source'])
        and value['changed_fields'] == verify_unchanged_content(value['historical_source'], value['current_source'])
        and type(value['history_records']) is dict and set(value['history_records']) == set(HISTORY_KEYS)
        and value['history_sha256'] == _hash(value['history_records']))
    for digest in (*value['history_records'].values(), value['story_qualification_sha256']):
        continuation._digest(digest)
    return value


class ReconnectedStoryEvidence:
    __slots__ = ('__weakref__',)

    def __new__(cls, *args, **kwargs):
        raise TypeError('retained_story_reconnection_private')

    def __repr__(self):
        return '<ReconnectedStoryEvidence diagnostic-only>'

    @property
    def record(self):
        _require(type(self) is ReconnectedStoryEvidence and self in _ISSUED)
        return validate_record(continuation._object(_ISSUED[self][0]))

    @property
    def story_evidence(self):
        self.record
        return _ISSUED[self][1]


def verify_current_bridge(pipe, record, qualification):
    """Watch the same source/history again; this is never a request admission."""
    bridge = validate_record(record)
    qualification = continuation._qualification(qualification)
    _require(_hash(qualification) == bridge['story_qualification_sha256']
        and qualification['commitments']['continuity_sha256'] == bridge['historical_source_sha256']
        and history_records(pipe) == bridge['history_records'])
    current = continuity._derive(pipe, bridge['current_source']['profile_revision'])
    _require(_raw(current) == _raw(bridge['current_source']),
             'retained_story_reconnect_connection_changed')
    bound = qualification['commitments']
    pipe.watch(bound['intent_key'], bound['capture_anchor_key'])
    for key, digest in ((bound['intent_key'], bound['intent_sha256']),
                        (bound['capture_anchor_key'], bound['capture_anchor_sha256'])):
        _require(pipe.pttl(key) == -1 and _hash(continuation._object(pipe.get(key))) == digest)
    return current


def read_reconnected_story_evidence(client, s3, *, bucket, qualification):
    """Reinterpret authenticated historical bytes against the unchanged media.

    A stored qualification is an untrusted index, not authority. Its complete
    contents are recomputed from journals, encrypted transport and source media.
    Neither an expired controller nor an unknown reservation is reopened.
    """
    try:
        qualification = continuation._qualification(continuation._object(_raw(qualification)))
        bound = qualification['commitments']
        cuts._storage_guard(s3, bucket)
        material = saved.settings.app_encryption_key
        with client.pipeline() as pipe:
            records = history_records(pipe)
            raw = pipe.get(completion.STATE_KEY)
            control = completion._manifest(completion._object(raw))
            cap = completion._authorization(raw.encode())
            state = journal.RouterReviewJournal(client, completion_plan=cap)._read_records(pipe)
            states = {'story': state,
                'audio': audio.RouterAudioReviewJournal(client, completion_plan=cap)._read_records(pipe)}
            _require(all(states[k]['policy'] == control['policies'][k] for k in states))
            heads = completion._control_journal(control, states)
            _require(pipe.hlen(completion.JOURNAL_KEY) == 6
                and pipe.hgetall(completion.JOURNAL_KEY) == heads
                and pipe.get(completion.ANCHOR_KEY) == _hash(heads))
            _require(set(state['slots']) == {saved._STORY}
                and state['slots'][saved._STORY]['response'] is None and states['audio']['slots'] == {})
            # Authentication happens before any historical metadata is trusted.
            cipher = cuts._read_private(s3, bucket, bound['encrypted_capture'])
            captured, _, _ = saved._authenticated_context(cipher, material)
            historical = captured['source']
            current = continuity._derive(pipe, state['policy']['profile_revision'])
            changed = verify_unchanged_content(historical, current)
            _require(_hash(historical) == state['policy']['continuity_sha256'])
            binding, reservation = saved._binding(state, historical, completion.STORY_KEYS)
            intent_key, intent, anchor_key, anchor, verified_cipher = saved._records(
                pipe, s3, bucket, binding)
            _require(cipher == verified_cipher)
            context, bodies = saved._packet(cipher, material, binding=binding,
                reservation=reservation, state=state, source=historical, summary=anchor['summary'])
            original, fingerprint = recovery._state(continuity.LEAF_ID, pipe)
            pointer = saved.audio_reader._source_pointer(historical, states['audio']['policy'])
            contract, metadata_sha = saved._source_contract_for_pointer(
                s3, bucket, original, states['audio']['policy'], pointer)
            _require(saved.shared._request_bytes(contract['request']) == bodies['prepared'])
            parsed = saved.adapter._parse_response_payload(bodies['response'],
                schema=contract['request']['json_schema'], max_tokens=contract['request']['max_tokens'])
            diagnostic = saved.shared._story_component(parsed['result'], contract)
            actual = {'completion_manifest_sha256': saved._sha(raw.encode()),
                'completion_journal_sha256': _hash(heads),
                'completion_anchor_sha256': pipe.get(completion.ANCHOR_KEY),
                'predecessor_snapshot_sha256': control['predecessors']['snapshot_sha256'],
                'completed_probe_capture_sha256': control['predecessors']['probe_capture_sha256'],
                'journal_keys': list(completion.STORY_KEYS), 'journal_state_sha256': _hash(state),
                'policy_sha256': _hash(state['policy']), 'credential_sha256': binding['credential_sha256'],
                'continuity_sha256': _hash(historical), 'source_state_sha256': fingerprint,
                'source_spec_sha256': _hash(original['spec']),
                'source_journal_sha256': _hash(original['generated_asset_candidates']),
                'source_metadata_sha256': metadata_sha, 'audio': states['audio']['policy']['audio'],
                'immutable_core_sha256': _hash(contract['candidate']),
                'intent_key': intent_key, 'intent_sha256': _hash(intent),
                'capture_anchor_key': anchor_key, 'capture_anchor_sha256': _hash(anchor),
                'encrypted_capture': anchor['encrypted_blob'], 'capture_context_sha256': _hash(context),
                'reservation_sha256': reservation['reservation_sha256'],
                'request_sha256': binding['request_sha256'],
                **{name + '_sha256': saved._sha(bodies[name]) for name in ('prepared', 'wire', 'response')},
                'parsed_result_sha256': _hash(parsed['result']), 'usage_sha256': _hash(parsed['usage']),
                'semantic_diagnostic_sha256': _hash(diagnostic)}
            _require(_raw(actual) == _raw(bound) and qualification['response_bytes'] == len(bodies['response']),
                     'retained_story_reconnect_qualification_changed')
            # The predecessor snapshots remain closed, including their unknowns.
            for key, digest in control['predecessors']['records'].items():
                _require(records[key] == digest)
            record = validate_record({'version': 1, 'kind': 'retained_story_same_content_reconnection',
                **_FLAGS, 'historical_source': historical, 'current_source': current,
                'historical_source_sha256': _hash(historical), 'current_source_sha256': _hash(current),
                'story_qualification_sha256': _hash(qualification), 'history_records': records,
                'history_sha256': _hash(records), 'changed_fields': changed})
            verify_current_bridge(pipe, record, qualification)
            _require(material == saved.settings.app_encryption_key)
            cuts._storage_guard(s3, bucket)
            completion._ping(pipe)
        story = object.__new__(saved.RetainedTransportStoryEvidence)
        saved._ISSUED[story] = _raw(qualification)
        result = object.__new__(ReconnectedStoryEvidence)
        _ISSUED[result] = (_raw(record), story)
        return result
    except saved.RetainedTransportStoryEvidenceError:
        raise
    except Exception:
        raise saved.RetainedTransportStoryEvidenceError('retained_story_reconnect_read_unverified') from None
