"""Private final-file staging after genuine retained component and render checks.

Staging performs no provider call, job claim, upload or publication. A later
admission consumer must revalidate the prepared object's source commitments.
An uncertain storage acknowledgement terminates this attempt.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import threading
import weakref

from app.services import abacus_router_review_artifacts as artifacts
from app.services import abacus_router_audio_review_journal as audio_journal
from app.services import abacus_router_review_journal as story_journal
from app.services import abacus_router_transport_capture as capture
from app.services import retained_audio_review_evidence as audio_reader
from app.services import retained_captured_story_visual_evidence as visual_reader
from app.services import retained_review_captured_story_continuation as continuation
from app.services import retained_transport_story_evidence as story_reader
from app.services import retained_render_consumer as local_render
from app.services import retained_cut_evidence as cuts
from app.services import preserved_visual_recovery as recovery
from app.services import production_connection_continuity as continuity

_ISSUED = weakref.WeakKeyDictionary()
_ATTEMPTED = weakref.WeakKeyDictionary()
_LOCK = threading.RLock()
_FLAGS = {'diagnostic_only': True, 'qa_approved': False, 'claim_authorized': False,
          'publish_eligible': False, 'resume_authorized': False,
          'new_provider_calls_authorized': False, 'automatic_retry_permitted': False}


class RetainedFinalArtifactsError(RuntimeError):
    """Fixed error text without source, provider, credential or storage details."""


def _require(value):
    if not value:
        raise RetainedFinalArtifactsError('retained_final_artifacts_unverified')


def _raw(value):
    return cuts._raw(value)


def _hash(value):
    return hashlib.sha256(_raw(value)).hexdigest()


class PreparedRetainedFinal:
    __slots__ = ('__weakref__',)

    def __new__(cls, *args, **kwargs):
        raise TypeError('retained_final_preparation_private')

    @property
    def record(self):
        return json.loads(_checked(self)['record'])

    def __repr__(self):
        return '<PreparedRetainedFinal private-files-only>'


def _checked(value):
    _require(type(value) is PreparedRetainedFinal)
    with _LOCK:
        state = _ISSUED.get(value)
    _require(state is not None and state['owner'] == threading.get_ident()
             and state['phase'] == 'prepared')
    return state


def _state(client, s3, bucket, cap, story, visual, audio):
    return {'client': client, 's3': s3, 'bucket': bucket, 'completion_plan': None,
            'captured_story_continuation': cap, 'story_evidence': story,
            'visual': _raw(visual.commitments), 'audio': _raw(audio.diagnostics)}


def _read_components(client, s3, bucket, cap, story, audit_pointer, rendered):
    _require(type(story) is story_reader.RetainedTransportStoryEvidence)
    visual = visual_reader.read_retained_captured_story_visual_evidence(client, s3,
        bucket=bucket, captured_story_continuation=cap, story_evidence=story,
        audit_pointer=audit_pointer)
    audio = audio_reader.read_retained_audio_review_evidence(client, s3, bucket=bucket,
        captured_story_continuation=cap)
    _require(visual.commitments == rendered['source_commitments']
             and _hash(audio.diagnostics) == rendered['audio_evidence_sha256']
             and visual.commitments['audio'] == rendered['original_audio']
             and rendered['local_render_verified'] is True
             and rendered['local_final_gates']['pass'] is True
             and rendered['final_aac_independently_listened'] is False)
    return visual, audio


def _snapshot(pipe, state):
    """Read actual fixed review/source records; never store credential plaintext."""
    original, _, _ = local_render._watched(pipe, state)
    visual, audio = json.loads(state['visual']), json.loads(state['audio'])
    story = state['story_evidence'].record
    ledger = audio_journal.RouterAudioReviewJournal(state['client'],
        captured_story_continuation=state['captured_story_continuation'])
    cap = state['captured_story_continuation']
    historical = continuation.historical_keys(cap)
    controls = continuation.controller_keys(cap)
    visual_keys = continuation.selected_keys(cap, 'story')
    review_keys = (*historical, *controls,
        *artifacts._anchor_keys(visual_keys).values(),
        *audio_reader._artifact_keys(ledger).values(), visual['sampled_link_anchor_key'],
        story['commitments']['intent_key'], story['commitments']['capture_anchor_key'])
    review_keys += continuation.rejection_capture_keys(pipe, cap)
    transport = _transport_records(pipe, state)
    from app.services.production_spend import LEDGER_KEY
    from app.services.production_credit_ledger import STATE_KEY, JOURNAL_KEY
    source_keys = [continuity.CONTINUITY_PREFIX + continuity.ROOT_ID,
        continuity._PROFILE + continuity.CHANNEL_ID, continuity._CHANNEL + continuity.CHANNEL_ID,
        continuity._CREDENTIAL + continuity.CHANNEL_ID, continuity._AUTH_EPOCH,
        continuity._STATE + continuity.CHANNEL_ID, continuity._ACTIVE,
        LEDGER_KEY, STATE_KEY, JOURNAL_KEY]
    for task in continuity.LINEAGE:
        source_keys.extend(prefix + task for prefix in (continuity._JOB, continuity._DISPATCH,
            continuity._CHILD_CLAIM, continuity._EXECUTION, continuity._REPAIR_CLAIM,
            continuity._PAID_CAP, *continuity._ABSENT_PREFIXES))
    keys = sorted(set((*review_keys, *transport, *source_keys)))
    pipe.watch(*keys, continuity._CHANNEL_INDEX)
    records = {}
    for key in keys:
        kind = pipe.type(key)
        _require(kind in ('none', 'string', 'hash'))
        value = pipe.get(key) if kind == 'string' else pipe.hgetall(key) if kind == 'hash' else None
        if key in review_keys or key in transport:
            # The unused STORY artifact key must stay absent. Every actual
            # evidence record is permanent, including all new transport captures.
            expected_ttl = -2 if kind == 'none' else -1
            _require(type(pipe.pttl(key)) is int and pipe.pttl(key) == expected_ttl)
        if key in transport:
            _require(kind == 'string')
        if kind != 'none' and key in (*controls, *historical):
            _require(type(pipe.pttl(key)) is int and pipe.pttl(key) == -1)
        records[key] = {'type': kind, 'value_sha256': _hash(value)}
    source = continuity._derive(pipe, original['spec']['production_profile_revision'])
    _require(_hash(source) == visual['continuity_sha256'] == audio['source']['continuity_sha256'])
    member = pipe.sismember(continuity._CHANNEL_INDEX, continuity.CHANNEL_ID)
    _require(type(member) in (bool, int) and member == 1)
    return {'source': source, 'source_state_sha256': visual['source_state_sha256'],
            'records': records, 'records_sha256': _hash(records),
            'permanent_record_keys': sorted(key for key in set((*review_keys, *transport))
                                            if records[key]['type'] != 'none'),
            'transport_capture_objects': transport}


def _transport_records(pipe, state):
    """Bind all three real capture anchors to their settled reservation history."""
    objects = {}
    for kind, keys, module, purposes in (
        ('story', continuation.selected_keys(state['captured_story_continuation'], 'story'), story_journal, (story_journal.PURPOSES[1],)),
        ('audio', continuation.selected_keys(state['captured_story_continuation'], 'audio'), audio_journal,
         tuple(purpose.value for purpose in audio_journal.PURPOSES)),
    ):
        pipe.watch(*keys)
        current = artifacts._object(pipe.get(keys[0]))
        prior = {}
        for purpose in purposes:
            slot = current['slots'][purpose]
            receipt = module._receipt(current['policy'], purpose, slot)
            reservation = _hash(receipt)
            _require(slot['response']['reservation_sha256'] == reservation)
            reserved = {'policy': current['policy'], 'slots': {**prior, purpose: {**slot, 'response': None}},
                        'updated_at': slot['reserved_at']}
            binding = {'version': 1, 'kind': kind, 'purpose': purpose,
                'original_task_id': continuity.ROOT_ID, 'source_task_id': continuity.LEAF_ID,
                'journal_keys': list(keys), 'reservation_sha256': reservation,
                'request_sha256': slot['request_sha256'],
                'credential_sha256': current['policy']['credential_sha256'],
                'policy_sha256': _hash(current['policy']),
                'continuity_sha256': current['policy']['continuity_sha256'],
                'reserved_at': slot['reserved_at'], 'journal_state_sha256': _hash(reserved),
                **capture._FLAGS}
            prefix = keys[0].rsplit(':', 1)[0] + ':transport_capture:v1:' + purpose + ':' + reservation
            intent_key, anchor_key = prefix + ':intent', prefix + ':anchor'
            pipe.watch(intent_key, anchor_key)
            intent_raw, anchor_raw = pipe.get(intent_key), pipe.get(anchor_key)
            _require(type(intent_raw) is str and type(anchor_raw) is str)
            intent, anchor = artifacts._object(intent_raw), artifacts._object(anchor_raw)
            _require(_raw(intent) == intent_raw.encode() and _raw(anchor) == anchor_raw.encode()
                and intent == {'binding': binding, 'storage_endpoint_sha256':
                    hashlib.sha256(state['s3'].meta.endpoint_url.encode()).hexdigest(),
                    'storage_bucket_sha256': hashlib.sha256(state['bucket'].encode()).hexdigest(),
                    'status': 'intent', **capture._FLAGS}
                and set(anchor) == {'binding', 'intent_sha256', 'encrypted_blob', 'summary', *capture._FLAGS}
                and anchor['binding'] == binding
                and anchor['intent_sha256'] == hashlib.sha256(intent_raw.encode()).hexdigest()
                and all(anchor.get(name) is flag for name, flag in capture._FLAGS.items()))
            summary, pointer = anchor['summary'], anchor['encrypted_blob']
            _require(type(summary['http_status']) is int and summary['http_status'] == 200
                and summary['response_complete'] is True and summary['response_binding_verified'] is True
                and type(pointer) is dict and set(pointer) == {'key', 'sha256', 'size', 'content_type'}
                and pointer['content_type'] == capture._CONTENT_TYPE
                and pointer['key'] == f'recovery/{continuity.LEAF_ID}/router_transport/v1/'
                    f'{_hash(list(keys))}/{purpose}/{reservation}/{pointer["sha256"]}.fernet')
            cuts._digest(pointer['sha256'])
            objects[intent_key] = None
            objects[anchor_key] = deepcopy(pointer)
            prior[purpose] = slot
    return objects


def stage_retained_final(client, s3, *, bucket, captured_story_continuation,
                         story_evidence, audit_pointer, rendered, workdir):
    """Persist exact final, captions, frame thumbnail and source metadata privately."""
    try:
        _require(type(rendered) is local_render.RetainedLocalRender)
        record = rendered.record  # Issuer registry validation precedes any file/storage access.
        with _LOCK:
            _require(rendered not in _ATTEMPTED)
            _ATTEMPTED[rendered] = 'attempted'
        base = local_render._directory(workdir)
        video = Path(record['path'])
        caption = Path(record['render_metrics']['srt'])
        _require(video.parent == caption.parent and video.name == 'final.mp4'
                 and caption.name == 'captions.srt')
        local_render._directory(video.parent)
        identities = [local_render._file(video, record['final']),
                      local_render._file(caption, record['captions'])]
        visual, audio = _read_components(client, s3, bucket, captured_story_continuation,
            story_evidence, audit_pointer, record)
        state = _state(client, s3, bucket, captured_story_continuation, story_evidence, visual, audio)
        with client.pipeline() as pipe:
            before = _snapshot(pipe, state)
            audit = artifacts._object(cuts._read_private(s3, bucket,
                {key: audit_pointer[key] for key in ('key', 'sha256', 'size')}
                | {'content_type': 'application/json'}))
            package = recovery._story_fields(audit['package'])
            _require(_hash(package) == visual.commitments['immutable_core_sha256'])
            video_identity, video_bytes = cuts._file(video, content=True)
            caption_identity, caption_bytes = cuts._file(caption, maximum=1024 * 1024, content=True)
            _require(video_identity == record['final'] and caption_identity == record['captions'])
            from app.services.publication_recovery_assets import _caption, _thumbnail, _probe, MAX_THUMBNAIL_BYTES
            _caption(caption, record['target_seconds'], package['narration'])
            stage = base / 'retained-final-staging'
            stage.mkdir(mode=0o700, exist_ok=False)
            thumbnail = stage / 'thumbnail.jpg'
            origin = _thumbnail({'path': str(video), **record['final']}, thumbnail)
            thumbnail.chmod(0o600)
            thumbnail_identity, thumbnail_bytes = cuts._file(thumbnail, MAX_THUMBNAIL_BYTES, content=True)
            _require(thumbnail_bytes.startswith(b'\xff\xd8\xff') and thumbnail_bytes.endswith(b'\xff\xd9'))
            probe = _probe(thumbnail, image=True)
            _require(probe['width'] == 720 and probe['height'] == 1280)
            thumbnail_record = local_render._file(thumbnail, thumbnail_identity)
            origin['origin'] = 'retained_reviewed_final_frame'
            for pointer in before['transport_capture_objects'].values():
                if pointer is not None:
                    cuts._read_private(s3, bucket, pointer)
            _require(_snapshot(pipe, state) == before)
            prefix = f'recovery/{continuity.LEAF_ID}/retained_final/v1/{_hash(record)}/'
            pointers = {
                'video': cuts._put(s3, bucket, prefix + 'final.mp4', video_bytes, 'video/mp4'),
                'captions': cuts._put(s3, bucket, prefix + 'captions.srt', caption_bytes,
                                      'application/x-subrip'),
                'thumbnail': cuts._put(s3, bucket, prefix + 'thumbnail.jpg', thumbnail_bytes, 'image/jpeg'),
            }
            metadata = {'version': 1, 'kind': 'retained_final_metadata', **_FLAGS,
                'source_task_id': continuity.LEAF_ID, 'original_task_id': continuity.ROOT_ID,
                'package': package, 'language': before['source']['lineage'][-1]['spec']['language'],
                'local_render': record, 'story_component': story_evidence.record,
                'visual_component': visual.record, 'audio_component': audio.diagnostics,
                'source_snapshot': before, 'artifacts': deepcopy(pointers), 'thumbnail': origin}
            metadata_bytes = _raw(metadata)
            pointers['metadata'] = cuts._put(s3, bucket, prefix + 'metadata.json', metadata_bytes,
                                            'application/json')
            _require([local_render._file(video, record['final']),
                      local_render._file(caption, record['captions'])] == identities)
            _require(local_render._file(thumbnail, thumbnail_identity) == thumbnail_record)
            _require(_snapshot(pipe, state) == before)
            artifacts._ack_read(pipe)
        result = object.__new__(PreparedRetainedFinal)
        safe = {'version': 1, 'kind': 'prepared_retained_final', **_FLAGS,
            'private_storage_verified': True, 'original_task_id': continuity.ROOT_ID,
            'source_task_id': continuity.LEAF_ID, 'source_snapshot': before,
            'local_render_sha256': _hash(record), 'artifacts': pointers}
        state.update(owner=threading.get_ident(), phase='prepared', record=_raw(safe),
            rendered=rendered, audit_pointer=deepcopy(audit_pointer), workdir=str(base),
            metadata=_raw(metadata))
        with _LOCK:
            _ISSUED[result] = state
            _ATTEMPTED[rendered] = 'prepared'
        return result
    except RetainedFinalArtifactsError:
        raise
    except Exception:
        raise RetainedFinalArtifactsError('retained_final_artifacts_unverified') from None
