"""Read authenticated pre-observer STORY bytes without settling their unknown slot.

The saved, authenticated header *summary* supports independent interpretation of
complete JSON bytes. It does not preserve all original headers or establish the
live observer's duplicate-header/transfer-encoding checks. No HTTPX observation
is reconstructed. The exact completion history and original source must still
exist before claim; neither expiry nor a later application revision renews a
request. Redis and private storage remain trusted stores: coordinated replacement
of every authenticated record, or bucket/CDN policy, is not proved by this read.
"""
import base64
import hashlib
import hmac
import json
import weakref

from cryptography.fernet import Fernet

from app.config import settings
from app.services import abacus_router_adapter as adapter
from app.services import abacus_router_audio_review_journal as audio_journal
from app.services import abacus_router_review_journal as journal
from app.services import abacus_router_transport_capture as capture
from app.services import immutable_story_review_contract as story_contract
from app.services import preserved_visual_recovery as recovery
from app.services import production_connection_continuity as continuity
from app.services import retained_audio_review_evidence as audio_reader
from app.services import retained_cut_evidence as cuts
from app.services import retained_review_completion_plan as completion
from app.services import retained_story_visual_evidence as shared
from app.services.voice_candidate_recovery import _voice_result, require_unchanged_voice_narration
from app.services.whisper_transcription import inspect_bounded_short_audio

_STORY = journal.PURPOSES[0]
_FLAGS = {**capture._FLAGS, 'claim_authorized': False, 'render_authorized': False,
          'resume_authorized': False, 'automatic_retry_permitted': False,
          'live_observer_verified': False, 'settlement_observed': False,
          'underlying_model_verified': False}
_ISSUED = weakref.WeakKeyDictionary()
_MAX_PACKET = (len(capture._DOMAIN) + 4 + capture._CONTEXT_LIMIT
               + 2 * adapter.MAX_REQUEST_BYTES + adapter.MAX_RESPONSE_BYTES)
_MAX_CIPHER = 4 * ((57 + 16 * (_MAX_PACKET // 16 + 1) + 2) // 3)


class RetainedTransportStoryEvidenceError(RuntimeError):
    """Fixed local errors; never include response, source or backend text."""


def _require(value, reason='retained_transport_story_unverified'):
    if not value:
        raise RetainedTransportStoryEvidenceError(reason)


def _raw(value):
    return capture._raw(value)


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _hash(value):
    return _sha(_raw(value))


def _same(left, right):
    _require(_raw(left) == _raw(right))


def _object(raw, maximum):
    _require(type(raw) in (str, bytes))
    raw = raw.encode('utf-8') if type(raw) is str else raw
    _require(0 < len(raw) <= maximum)
    value = adapter._json_loads(raw.decode('utf-8'))
    _require(type(value) is dict and _raw(value) == raw)
    return value


class RetainedTransportStoryEvidence:
    """Sealed detached diagnostics; no provider, claim or continuation permit."""
    __slots__ = ('__weakref__',)

    def __new__(cls, *args, **kwargs):
        raise TypeError('retained_transport_story_private')

    def __repr__(self):
        return '<RetainedTransportStoryEvidence diagnostic-only>'

    @property
    def record(self):
        _require(type(self) is RetainedTransportStoryEvidence and self in _ISSUED)
        return _object(_ISSUED[self], capture._CONTEXT_LIMIT)

    @property
    def commitments(self):
        return self.record['commitments']

    @property
    def qa_approved(self):
        return False

    @property
    def publish_eligible(self):
        return False


def _binding(state, source, keys):
    receipt = journal._receipt(state['policy'], _STORY, state['slots'][_STORY])
    reservation = {**receipt, 'reservation_sha256': journal._hash(receipt)}
    binding = {'version': 1, 'kind': 'story', 'purpose': _STORY,
        'original_task_id': continuity.ROOT_ID, 'source_task_id': continuity.LEAF_ID,
        'journal_keys': list(keys), 'reservation_sha256': reservation['reservation_sha256'],
        'request_sha256': receipt['request_sha256'],
        'credential_sha256': state['policy']['credential_sha256'],
        'policy_sha256': receipt['policy_sha256'], 'continuity_sha256': _hash(source),
        'reserved_at': receipt['reserved_at'], 'journal_state_sha256': _hash(state), **capture._FLAGS}
    return binding, reservation


def _records(pipe, s3, bucket, binding):
    prefix = (binding['journal_keys'][0].rsplit(':', 1)[0] + ':transport_capture:v1:'
              + _STORY + ':' + binding['reservation_sha256'])
    intent_key, anchor_key = prefix + ':intent', prefix + ':anchor'
    pipe.watch(intent_key, anchor_key)
    for key in (intent_key, anchor_key):
        ttl = pipe.pttl(key)
        _require(type(ttl) is int and ttl == -1, 'retained_transport_story_capture_missing')
    intent = _object(pipe.get(intent_key), capture._RECORD_LIMIT)
    _same(intent, {'binding': binding,
        'storage_endpoint_sha256': _sha(cuts.storage.settings.endpoint.encode('utf-8')),
        'storage_bucket_sha256': _sha(bucket.encode('utf-8')), 'status': 'intent', **capture._FLAGS})
    anchor = _object(pipe.get(anchor_key), capture._RECORD_LIMIT)
    _require(set(anchor) == {'binding', 'intent_sha256', 'encrypted_blob', 'summary', *capture._FLAGS})
    _same(anchor['binding'], binding)
    _same({name: anchor[name] for name in capture._FLAGS}, capture._FLAGS)
    _require(anchor['intent_sha256'] == _hash(intent))
    pointer = anchor['encrypted_blob']
    _require(type(pointer) is dict and set(pointer) == {'key', 'sha256', 'size', 'content_type'}
             and type(pointer['size']) is int and 0 < pointer['size'] <= _MAX_CIPHER)
    shared._digest(pointer['sha256'])
    namespace = _hash(binding['journal_keys'])
    _require(pointer['key'] == f'recovery/{continuity.LEAF_ID}/router_transport/v1/{namespace}/'
             f'{_STORY}/{binding["reservation_sha256"]}/{pointer["sha256"]}.fernet'
             and pointer['content_type'] == capture._CONTENT_TYPE)
    ciphertext = cuts._read_private(s3, bucket, pointer)
    return intent_key, intent, anchor_key, anchor, ciphertext


def _packet(ciphertext, material, *, binding, reservation, state, source, summary):
    _require(type(material) is str and 0 < len(material) <= 4096)
    key = base64.urlsafe_b64encode(hmac.new(material.encode('utf-8'), capture._DOMAIN,
                                          hashlib.sha256).digest())
    packet = Fernet(key).decrypt(ciphertext)
    _require(len(capture._DOMAIN) + 4 < len(packet) <= _MAX_PACKET
             and packet.startswith(capture._DOMAIN))
    offset = len(capture._DOMAIN)
    size = int.from_bytes(packet[offset:offset + 4], 'big')
    offset += 4
    _require(0 < size <= capture._CONTEXT_LIMIT and offset + size <= len(packet))
    context = _object(packet[offset:offset + size], capture._CONTEXT_LIMIT)
    offset += size
    _require(set(context) == {'binding', 'policy', 'source', 'reservation', 'summary',
        'prepared_size', 'prepared_sha256', 'wire_size', 'wire_sha256', 'response_size',
        'response_sha256', *capture._FLAGS})
    for field, expected in (('binding', binding), ('policy', state['policy']), ('source', source),
                            ('reservation', reservation), ('summary', summary)):
        _same(context[field], expected)
    _same({name: context[name] for name in capture._FLAGS}, capture._FLAGS)
    bodies = {}
    for name in ('prepared', 'wire', 'response'):
        size = context[name + '_size']
        maximum = adapter.MAX_RESPONSE_BYTES if name == 'response' else adapter.MAX_REQUEST_BYTES
        _require(type(size) is int and 0 < size <= maximum and offset + size <= len(packet))
        body = packet[offset:offset + size]
        offset += size
        _require(_sha(body) == context[name + '_sha256'])
        bodies[name] = body
    _require(offset == len(packet))
    _require(type(summary) is dict and set(summary) == {'http_status', 'response_bytes',
        'response_sha256', 'response_complete', 'response_binding_verified', 'transport_outcome',
        'headers', 'root_shape'})
    headers = summary['headers']
    _require(type(headers) is dict and set(headers) == {'content_type', 'content_encoding',
        'content_length_valid', 'content_length', 'redirect_history'}
        and headers['content_type'] in ('application/json', 'application/json; charset=utf-8')
        and headers['content_encoding'] in ('absent', 'identity')
        and headers['content_length_valid'] is True and headers['redirect_history'] is False
        and (headers['content_length'] is None or type(headers['content_length']) is int
             and headers['content_length'] == len(bodies['response'])))
    _same(summary, {'http_status': 200, 'response_bytes': len(bodies['response']),
        'response_sha256': _sha(bodies['response']), 'response_complete': True,
        'response_binding_verified': True, 'transport_outcome': 'response_received',
        'headers': headers, 'root_shape': capture._shape(bodies['response'], True)})
    prepared = _object(bodies['prepared'], adapter.MAX_REQUEST_BYTES)
    wire = adapter._json_loads(bodies['wire'].decode('utf-8'))
    _require(type(wire) is dict and _raw(wire) == bodies['prepared'])
    _require(_hash({'version': 1, 'method': 'POST', 'endpoint': adapter.ENDPOINT,
                    'body': prepared}) == binding['request_sha256'])
    return context, bodies


def _source_contract(pipe, s3, bucket, original, audio_policy):
    pointer = audio_reader._source(pipe, audio_policy)
    metadata_raw = audio_reader._blob(s3, bucket, pointer['metadata_key'], pointer['metadata_sha256'],
        audio_reader.MAX_RECORD_BYTES, content_type='application/json')
    cuts._read_private(s3, bucket, {'key': pointer['metadata_key'], 'sha256': pointer['metadata_sha256'],
        'size': len(metadata_raw), 'content_type': 'application/json'})
    audio_journal._metadata(metadata_raw, audio_policy)
    metadata = _object(metadata_raw, audio_reader.MAX_RECORD_BYTES)
    _voice_result(metadata['voice'], 6)
    audio = cuts._read_private(s3, bucket, {'key': pointer['audio_key'], 'sha256': pointer['audio_sha256'],
        'size': pointer['size'], 'content_type': 'audio/mpeg'})
    _same(inspect_bounded_short_audio(audio, 'audio/mpeg'), audio_policy['audio'])
    manifests = recovery._manifests(s3, original)
    _require(len(manifests) == 6 and all(row['voice'] == metadata['voice'] for row in manifests))
    require_unchanged_voice_narration(metadata['package'], manifests[0]['package'])
    for entry in original['generated_asset_candidates']['entries']:
        cuts._read_private(s3, bucket, {'key': entry['manifest_key'], 'sha256': entry['manifest_sha256'],
            'size': entry['manifest_size'], 'content_type': 'application/json'})
    from app import tasks
    options = recovery._options(original)
    _same(tasks._normalized_options(options, .5), options)
    package = recovery._immutable_shooting_package(manifests[0]['package'], {}, options)
    contract = story_contract.derive_immutable_story_review_contract(package, original['spec']['topic'],
        .5, original['spec']['language'], options,
        immutable_candidate_narrations=[scene['narration'] for scene in metadata['package']['scenes']])
    return contract, _sha(metadata_raw)


def read_retained_transport_story_evidence(client, s3, *, bucket, completion_plan):
    """Interpret a complete saved STORY capture; one final read ACK, no mutation."""
    try:
        keys = completion.selected_keys(completion_plan, 'story')
        _require(keys == completion.STORY_KEYS)
        ledger = journal.RouterReviewJournal(client, completion_plan=completion_plan)
        cuts._storage_guard(s3, bucket)
        material = settings.app_encryption_key
        with client.pipeline() as pipe:
            pipe.watch(*keys, *recovery._keys(continuity.LEAF_ID))
            state = ledger._read(pipe)
            control, states, manifest_raw, _ = completion._read_control(pipe)
            _require(manifest_raw == completion_plan._manifest_bytes and states['story'] == state
                     and set(state['slots']) == {_STORY} and state['slots'][_STORY]['response'] is None
                     and states['audio']['slots'] == {}, 'retained_transport_story_state_ineligible')
            source = continuity._derive(pipe, state['policy']['profile_revision'])
            _require(_hash(source) == state['policy']['continuity_sha256'])
            original, fingerprint = recovery._state(continuity.LEAF_ID, pipe)
            binding, reservation = _binding(state, source, keys)
            intent_key, intent, anchor_key, anchor, ciphertext = _records(pipe, s3, bucket, binding)
            context, bodies = _packet(ciphertext, material, binding=binding, reservation=reservation,
                state=state, source=source, summary=anchor['summary'])
            contract, metadata_sha = _source_contract(pipe, s3, bucket, original, states['audio']['policy'])
            _require(shared._request_bytes(contract['request']) == bodies['prepared'],
                     'retained_transport_story_request_changed')
            parsed = adapter._parse_response_payload(bodies['response'],
                schema=contract['request']['json_schema'], max_tokens=contract['request']['max_tokens'])
            diagnostic = shared._story_component(parsed['result'], contract)
            commitments = {'completion_manifest_sha256': _sha(manifest_raw),
                'completion_journal_sha256': _hash(pipe.hgetall(completion.JOURNAL_KEY)),
                'completion_anchor_sha256': pipe.get(completion.ANCHOR_KEY),
                'predecessor_snapshot_sha256': control['predecessors']['snapshot_sha256'],
                'completed_probe_capture_sha256': control['predecessors']['probe_capture_sha256'],
                'journal_keys': list(keys), 'journal_state_sha256': _hash(state),
                'policy_sha256': _hash(state['policy']), 'credential_sha256': binding['credential_sha256'],
                'continuity_sha256': _hash(source), 'source_state_sha256': fingerprint,
                'source_spec_sha256': _hash(original['spec']),
                'source_journal_sha256': _hash(original['generated_asset_candidates']),
                'source_metadata_sha256': metadata_sha, 'audio': states['audio']['policy']['audio'],
                'immutable_core_sha256': _hash(contract['candidate']),
                'intent_key': intent_key, 'intent_sha256': _hash(intent),
                'capture_anchor_key': anchor_key, 'capture_anchor_sha256': _hash(anchor),
                'encrypted_capture': anchor['encrypted_blob'], 'capture_context_sha256': _hash(context),
                'reservation_sha256': reservation['reservation_sha256'],
                'request_sha256': binding['request_sha256'],
                **{name + '_sha256': _sha(bodies[name]) for name in ('prepared', 'wire', 'response')},
                'parsed_result_sha256': _hash(parsed['result']), 'usage_sha256': _hash(parsed['usage']),
                'semantic_diagnostic_sha256': _hash(diagnostic)}
            record = {'version': 1, 'kind': 'retained_transport_story_component_evidence', **_FLAGS,
                'provenance': 'authenticated_preobserver_transport', 'component_pass': True,
                'preclaim_only': True, 'read_acknowledged': True, 'capture_anchor_read_verified': True,
                'saved_header_summary_only': True, 'historical_record_count': 27,
                'selected_occupied_count': 1, 'selected_unknown_count': 1,
                'prior_occupied_count': 4, 'prior_unknown_count': 3,
                'response_status_code': 200, 'response_bytes': len(bodies['response']),
                'commitments': commitments}
            encoded = _raw(record)
            _require(len(encoded) <= capture._CONTEXT_LIMIT and settings.app_encryption_key == material)
            cuts._storage_guard(s3, bucket)
            completion._ping(pipe)
        value = object.__new__(RetainedTransportStoryEvidence)
        _ISSUED[value] = encoded
        return value
    except RetainedTransportStoryEvidenceError:
        raise
    except Exception:
        raise RetainedTransportStoryEvidenceError('retained_transport_story_read_unverified') from None
