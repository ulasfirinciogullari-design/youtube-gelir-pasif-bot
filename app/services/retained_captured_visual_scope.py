"""Owning-scope admission from actual captured-STORY/VISUAL and persisted ASR.

The component reader is diagnostic. This separate, explicit binding permits
only the selected three-purpose continuation, with current source/anchor reads
inside each reservation and immediately before transport. It grants no claim,
render or publication authority. Private immutable blobs are read at binding;
reservation guards use only their unchanged permanent Redis anchors.
"""
import threading
import weakref

from app.services import abacus_router_audio_adapter as adapter
from app.services import abacus_router_audio_review_journal as journal
from app.services import retained_review_captured_story_continuation as continuation
from app.services.production_spend import SpendBlocked

_ASR, _PROSODY = adapter.AudioReviewPurpose
_BINDINGS = {}
_raw, _hash, _object = continuation._raw, continuation._hash, continuation._object


def _require(value, reason='router_audio_predecessor_unverified'):
    if not value:
        raise SpendBlocked(reason)


def _scope(scope):
    from app.services import abacus_router_audio_review_runtime as runtime
    from app.services import abacus_router_transport_capture as capture
    _require(type(scope) is runtime._AudioScope and runtime._SCOPE.get() is scope
             and scope.owner_thread == threading.get_ident() and not scope.closed and not scope.failed,
             'router_audio_predecessor_scope_unusable')
    runtime._usable(scope)
    _require(type(scope.journal) is journal.RouterAudioReviewJournal
             and scope.journal._successor is None and scope.journal._completion_plan is None
             and capture._enabled(scope) is True)
    cap = scope.journal._captured_story_continuation
    _require(continuation.selected_keys(cap, 'audio') == scope.journal.keys == continuation.AUDIO_KEYS)
    return cap


def _entry(scope):
    cap = _scope(scope)
    entry = _BINDINGS.get(id(scope))
    _require(entry is not None and entry[0]() is scope and entry[1] is not None,
             'router_audio_visual_binding_required')
    data = entry[1]
    _require(data['journal'] is scope.journal and data['client'] is scope.journal.client
             and data['cap'] is cap and continuation._issued_bytes(cap) == data['manifest'],
             'router_audio_predecessor_selection_changed')
    _require(_raw(data['evidence'].record) == data['visual'], 'router_audio_visual_evidence_changed')
    return data


def _durable_object(pipe, key, digest):
    pipe.watch(key)
    ttl, stored = pipe.pttl(key), pipe.get(key)
    value = _object(stored)
    encoded = stored.encode('utf-8') if type(stored) is str else stored
    _require(type(ttl) is int and ttl == -1 and encoded == _raw(value)
             and _hash(value) == digest, 'router_audio_predecessor_anchor_changed')
    return value


def _visual(pipe, scope, data):
    from app.services import abacus_router_review_artifacts as artifacts
    manifest, states, raw, actual = continuation._read_control(pipe, current=True)
    config = continuation._configuration()
    _require(raw == data['manifest'] and actual == config[:2]
             and manifest['attestation']['runtime_head_sha'] == config[2]
             and config == data['context'], 'router_audio_predecessor_context_changed')
    bound = _object(data['visual'])['commitments']
    qualification = manifest['story_qualification']
    state = states['story']
    visual = continuation.story.PURPOSES[1]
    _require(set(state['slots']) == {visual} and state['slots'][visual]['response'] is not None)
    baseline = {'story': state, 'audio': {'policy': manifest['policies']['audio'],
        'slots': {}, 'updated_at': manifest['created_at']}}
    baseline_sha = _hash(continuation._control_journal(manifest, baseline))
    expected = {'continuation_manifest_sha256': continuation._sha(raw),
        'continuation_journal_sha256': baseline_sha, 'continuation_anchor_sha256': baseline_sha,
        'predecessor_snapshot_sha256': manifest['predecessors']['snapshot_sha256'],
        'story_evidence_sha256': _hash(qualification), 'journal_keys': list(continuation.VISUAL_KEYS),
        'journal_state_sha256': _hash(state), 'policy_sha256': _hash(state['policy']),
        'story_predecessor': {'version': 1, 'kind': 'captured_transport_story',
            'continuation_manifest_sha256': continuation._sha(raw), 'evidence': qualification}}
    for key in ('continuity_sha256', 'source_state_sha256', 'source_spec_sha256',
                'source_journal_sha256', 'source_metadata_sha256', 'audio', 'immutable_core_sha256'):
        expected[key] = qualification['commitments'][key]
    _require(_raw(expected) == _raw({key: bound[key] for key in expected}),
             'router_audio_visual_binding_changed')
    anchor_keys = artifacts._anchor_keys(continuation.VISUAL_KEYS)
    pipe.watch(*anchor_keys.values())
    _require(pipe.exists(anchor_keys[continuation.story.PURPOSES[0]]) == 0)
    anchor = _durable_object(pipe, anchor_keys[visual], bound['visual_anchor_sha256'])
    _require(anchor['manifest'] == bound['visual_manifest']
             and anchor['journal_state_sha256'] == _hash(state)
             and anchor['story_predecessor'] == expected['story_predecessor'])
    slot = state['slots'][visual]
    receipt = continuation.story._receipt(state['policy'], visual, slot)
    link_key = continuation.VISUAL_KEYS[0].rsplit(':', 1)[0] + ':sampled_cut_link:v1:' + _hash(receipt)
    _require(bound['sampled_link_anchor_key'] == link_key)
    link = _durable_object(pipe, link_key, bound['sampled_link_anchor_sha256'])
    _require(link['pointer'] == bound['sampled_link']
             and link['visual_artifact_anchor_sha256'] == bound['visual_anchor_sha256']
             and link['journal_state_sha256'] == _hash(state)
             and link['cut_manifest_sha256'] == bound['cut_manifest']['sha256']
             and link['story_predecessor'] == expected['story_predecessor'])
    return states['audio']


def bind_captured_visual_predecessor(scope, *, visual_evidence):
    """Bind a genuine positive reader result once, before this scope's ASR."""
    cap = _scope(scope)
    identity = id(scope)
    _require(identity not in _BINDINGS and not scope.attempted and not scope.artifacts
             and scope.pending_request is None and not scope._transport_captures,
             'router_audio_predecessor_already_bound_or_attempted')
    ref = weakref.ref(scope, lambda _: _BINDINGS.pop(identity, None))
    _BINDINGS[identity] = (ref, None)
    try:
        from app.services import retained_captured_story_visual_evidence as reader
        from app.services import retained_audio_review_evidence as audio_reader
        _require(type(visual_evidence) is reader.RetainedCapturedStoryVisualEvidence,
                 'router_audio_visual_evidence_required')
        data = {'journal': scope.journal, 'client': scope.journal.client, 'cap': cap,
            'manifest': continuation._issued_bytes(cap), 'evidence': visual_evidence,
            'visual': _raw(visual_evidence.record), 'context': continuation._configuration(),
            'producer': None, 'asr': None, 'persisted': None, 'persistence_attempted': False}
        with scope.journal.client.pipeline() as pipe:
            state = _visual(pipe, scope, data)
            anchors = audio_reader._artifact_keys(scope.journal)
            pipe.watch(*anchors.values())
            _require(state['slots'] == {} and pipe.exists(*anchors.values()) == 0,
                     'router_audio_predecessor_already_attempted')
            continuation._ping(pipe)
        _require(_scope(scope) is cap and continuation._configuration() == data['context'])
        _BINDINGS[identity] = (ref, data)
    except Exception:
        scope.failed = True
        raise SpendBlocked('router_audio_visual_binding_unverified') from None


def _asr_snapshot(scope, artifact):
    from app.services import abacus_router_audio_review_runtime as runtime
    from app.services import abacus_router_transport_capture as capture
    _require(type(artifact) is runtime.AcknowledgedAudioReview
             and scope.artifacts.get(_ASR) is artifact and artifact.prepared.purpose is _ASR)
    observed = adapter.observe_audio_router_response(artifact.prepared, artifact.response)
    _require(adapter._canonical(observed.result) == adapter._canonical(artifact.observed.result)
             and adapter._canonical(observed.evidence) == adapter._canonical(artifact.observed.evidence)
             and _raw(artifact.settlement) == _raw({'result': observed.result,
                 'evidence': observed.evidence, **journal._FLAGS}))
    item = scope._transport_captures.get(_ASR.value)
    capture._issued(item)
    receipt = item.receipt
    _require(receipt['capture_acknowledged'] is True
             and receipt['summary']['response_complete'] is True
             and receipt['summary']['response_sha256'] == continuation._sha(artifact.response.content)
             and receipt['binding']['journal_keys'] == list(continuation.AUDIO_KEYS)
             and receipt['binding']['reservation_sha256'] == artifact.reservation['reservation_sha256']
             and receipt['binding']['request_sha256'] == artifact.prepared.request_sha256)
    return _raw({'reservation': artifact.reservation, 'settlement': artifact.settlement,
        'prepared_sha256': continuation._sha(artifact.prepared._body_bytes),
        'wire_sha256': continuation._sha(artifact.response.request.content),
        'response_sha256': continuation._sha(artifact.response.content), 'capture': receipt})


def _arm_asr_producer(scope, prepared, reservation):
    """Runtime-only producer seam after reserve/capture-intent ACK, before send."""
    data = _entry(scope)
    _require(data['producer'] is None and data['asr'] is None and scope.attempted == {_ASR}
             and prepared.purpose is _ASR and scope.pending_request is None)
    data['producer'] = (prepared, reservation)


def _register_asr_artifact(scope, artifact):
    """Match the armed request to actual transport and completed settlement ACK."""
    data = _entry(scope)
    producer = data['producer']
    # _send_once consumed pending_request before POST. Never re-arm that send
    # permission merely to register the returned acknowledged artifact.
    _require(data['asr'] is None and producer is not None and producer[0] is artifact.prepared
             and producer[1] == artifact._reservation_bytes and scope.pending_request is None
             and scope.attempted == {_ASR}, 'router_audio_asr_producer_unverified')
    data['asr'] = (artifact, _asr_snapshot(scope, artifact))
    data['producer'] = None


def _registered_asr(scope, data):
    _require(data['asr'] is not None, 'router_audio_asr_producer_required')
    artifact, encoded = data['asr']
    _require(_asr_snapshot(scope, artifact) == encoded, 'router_audio_asr_artifact_changed')
    return artifact


def _asr_state(state):
    slot = state['slots'].get(_ASR.value)
    _require(type(slot) is dict and slot['response'] is not None, 'router_audio_asr_unacknowledged')
    return {'policy': state['policy'], 'slots': {_ASR.value: slot},
            'updated_at': slot['response']['observed_at']}


def _persisted(pipe, scope, data, state):
    from app.services import retained_audio_review_evidence as reader
    artifact = _registered_asr(scope, data)
    _require(data['persisted'] is not None, 'router_audio_persisted_asr_required')
    stored = _object(data['persisted'])
    asr_state = _asr_state(state)
    keys = reader._artifact_keys(scope.journal)
    pipe.watch(*keys.values())
    _require(pipe.exists(keys['final']) == 0, 'router_audio_final_artifact_already_present')
    anchor = reader._anchor(pipe, 'asr', asr_state, scope.journal)
    _durable_object(pipe, keys['asr'], _hash(stored['anchor']))
    _require(_raw(anchor) == _raw(stored['anchor']) and anchor['asr_anchor_sha256'] is None
             and anchor['journal_state_sha256'] == _hash(asr_state),
             'router_audio_persisted_asr_changed')
    binding = journal._asr_binding(state['policy'], asr_state['slots'][_ASR.value],
                                   stored['expected'], artifact.observed.result)
    _require(_raw(binding) == _raw(stored['binding']))
    return binding


def bind_persisted_asr_predecessor(scope, s3, *, bucket):
    """Verify the actual private ASR record once; never accept a caller receipt."""
    data = _entry(scope)
    _require(not data['persistence_attempted'], 'router_audio_asr_persistence_already_attempted')
    data['persistence_attempted'] = True
    try:
        from app.services import retained_audio_review_evidence as reader
        from app.services import retained_cut_evidence as cuts
        artifact = _registered_asr(scope, data)
        _require(scope.attempted == {_ASR} and scope.pending_request is None)
        cuts._storage_guard(s3, bucket)
        with scope.journal.client.pipeline() as pipe:
            state = _visual(pipe, scope, data)
            _require(set(state['slots']) == {_ASR.value})
            asr_state = _asr_state(state)
            _require(state == asr_state)
            keys = reader._artifact_keys(scope.journal)
            pipe.watch(*keys.values())
            _require(pipe.exists(keys['final']) == 0)
            anchor = reader._anchor(pipe, 'asr', state, scope.journal)
            _durable_object(pipe, keys['asr'], _hash(anchor))
            _require(anchor['asr_anchor_sha256'] is None and anchor['journal_state_sha256'] == _hash(state))
            record = reader._object(reader._private_blob(s3, bucket, anchor['pointer']))
            _require(record['kind'] == 'asr')
            reader._validate_record(record, state)
            review = record['reviews'][_ASR.value]
            _require(_raw(review['reservation']) == _raw(artifact.reservation)
                     and _raw(review['settlement']) == _raw(artifact.settlement))
            expected = record['source']['expected_narration']
            binding = journal._asr_binding(state['policy'], state['slots'][_ASR.value],
                                           expected, artifact.observed.result)
            _require(_registered_asr(scope, data) is artifact)
            cuts._storage_guard(s3, bucket)
            continuation._ping(pipe)
        _require(_scope(scope) is data['cap'] and continuation._configuration() == data['context'])
        data['persisted'] = _raw({'anchor': anchor, 'expected': expected, 'binding': binding})
    except Exception:
        scope.failed = True
        raise SpendBlocked('router_audio_asr_persistence_unverified') from None


def require_audio_predecessors(pipe, authorization, purpose, *, reserved=False, proposed_slot=None):
    """Same-WATCH reserve/pre-send gate; storage and provider calls are forbidden."""
    from app.services import abacus_router_audio_review_runtime as runtime
    scope = runtime._SCOPE.get()
    try:
        data = _entry(scope)
        _require(authorization is data['cap'] and type(purpose) is str
                 and purpose in (_ASR.value, _PROSODY.value))
        state = _visual(pipe, scope, data)
        expected = set() if purpose == _ASR.value else {_ASR.value}
        _require(set(state['slots']) == expected | ({purpose} if reserved else set()),
                 'router_audio_predecessor_order_changed')
        if reserved:
            _require(state['slots'][purpose]['response'] is None)
        if purpose == _ASR.value:
            from app.services import retained_audio_review_evidence as reader
            anchors = reader._artifact_keys(scope.journal)
            pipe.watch(*anchors.values())
            _require(pipe.exists(*anchors.values()) == 0, 'router_audio_artifact_already_present')
        if purpose == _PROSODY.value:
            binding = _persisted(pipe, scope, data, state)
            slot = state['slots'][purpose] if reserved else proposed_slot
            if slot is not None:
                _require(_raw(slot['asr_binding']) == _raw(binding), 'router_audio_asr_binding_changed')
        _require(_scope(scope) is data['cap'] and continuation._configuration() == data['context'])
    except Exception:
        if type(scope) is runtime._AudioScope:
            scope.failed = True
        raise SpendBlocked('router_audio_predecessor_admission_unverified') from None
