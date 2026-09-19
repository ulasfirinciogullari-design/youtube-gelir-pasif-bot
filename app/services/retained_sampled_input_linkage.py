"""Actual local JPEG extraction linked to an acknowledged retained VISUAL wire.

Only an issued, privately persisted PreparedRetainedCuts collector can record
the opt-in visual path. Its exact local events are compared with the entire
acknowledged prepared/wire body before a separate watched diagnostic anchor.
This is neither semantic acceptance nor a render, claim or publication permit.
Existing review artifacts and all request/admission journals stay unchanged.
"""
import base64
from copy import deepcopy
from functools import wraps
import hashlib
import json
import math
from pathlib import Path
import shutil
import threading
import weakref

from app.services import abacus_router_adapter as adapter
from app.services import abacus_router_review_runtime as runtime
from app.services import abacus_router_review_artifacts as artifacts
from app.services import retained_cut_evidence as cuts
from app.services import production_connection_continuity as continuity, storage
from app.services.production_spend import SpendBlocked


_COLLECTORS = weakref.WeakKeyDictionary()
_FLAGS = {'diagnostic_only': True, 'qa_approved': False, 'publish_eligible': False,
          'full_qa_complete': False, 'claim_authorized': False, 'resume_authorized': False,
          'semantic_acceptance_verified': False, 'consumer_authorized': False}
_VISUAL = 'retained_visual_review'
_STORY = 'immutable_story_review'
_ORDER = ((3, .06), (0, .18), (1, .50), (2, .82), (4, .94))


class SampledInputLinkageError(SpendBlocked):
    """Fixed local error, never provider/source/path text."""


def _require(value):
    if not value:
        raise SampledInputLinkageError('retained_sampled_input_unverified')


def _raw(value):
    return cuts._raw(value)


def _sha(value):
    return hashlib.sha256(value).hexdigest()


class SampledInputCollector:
    __slots__ = ('__weakref__',)

    def __new__(cls, *args, **kwargs):
        raise TypeError('retained_sampled_input_private')

    def __repr__(self):
        return '<SampledInputCollector diagnostic-only>'


def _checked(value):
    _require(type(value) is SampledInputCollector)
    data = _COLLECTORS.get(value)
    scope = runtime._artifact_scope()
    _require(data is not None and data['owner'] == threading.get_ident()
             and data['scope']() is scope and scope.journal is data['journal']
             and scope.journal.keys == data['keys'])
    return data, scope


def _event(function):
    @wraps(function)
    def guarded(collector, *args, **kwargs):
        try:
            return function(collector, *args, **kwargs)
        except Exception:
            data = _COLLECTORS.get(collector) if type(collector) is SampledInputCollector else None
            scope = data['scope']() if data is not None else None
            if (scope is not None and data['owner'] == threading.get_ident()
                    and runtime._SCOPE.get() is scope):
                scope.failed = True
            raise SampledInputLinkageError('retained_sampled_input_unverified') from None
    return guarded


@_event
def _abort(collector):
    raise SampledInputLinkageError('retained_sampled_input_unverified')


def begin_sampled_input_capture(prepared_cuts, package):
    """Issue for these exact current cuts and the still-open owning scope."""
    scope = None
    try:
        scope = runtime._artifact_scope()
        cuts.verify_retained_cuts(prepared_cuts)
        cut_receipt = cuts._persisted_receipt(prepared_cuts)
        record = prepared_cuts.record
        _require(record['source']['source_task_id'] == continuity.LEAF_ID
                 and _sha(_raw(package)) == record['package_sha256'])
        keys = artifacts._journal_keys(scope.journal)
        value = object.__new__(SampledInputCollector)
        data = {'owner': threading.get_ident(), 'scope': weakref.ref(scope),
            'journal': scope.journal, 'keys': keys, 'cuts': prepared_cuts, 'cut_receipt': cut_receipt,
            'package': _raw(package), 'record': record, 'events': [], 'images': [], 'pending': None,
            'entered': False, 'body': None, 'attempted': False}
        _before_visual(data, scope)
        _COLLECTORS[value] = data
        return value
    except Exception:
        if scope is not None:
            scope.failed = True
        raise SampledInputLinkageError('retained_sampled_input_unverified') from None


@_event
def _enter(collector, scenes, inputs, work):
    data, _ = _checked(collector)
    _require(not data['entered'] and _raw(scenes) == _raw(json.loads(data['package'])['scenes'])
             and inputs == data['cuts'].inputs
             and Path(work) == Path(inputs[0][0]['path']).parent)
    cuts.verify_retained_cuts(data['cuts'])
    data['entered'] = True


@_event
def _before_frame(collector, source, target, fraction):
    data, _ = _checked(collector)
    number = len(data['events'])
    _require(data['entered'] and data['pending'] is None and data['body'] is None and number < 30)
    scene, order = divmod(number, 5)
    moment, expected_fraction = _ORDER[order]
    source_path = Path(data['cuts'].inputs[scene][0]['path'])
    expected_target = source_path.parent / 'visual_qc' / f'scene_{scene:02d}_candidate_00_moment_{moment:02d}.jpg'
    _require(type(fraction) is float and fraction == expected_fraction
             and Path(source) == source_path and Path(target) == expected_target
             and cuts._file(source_path)[0] == data['record']['cuts'][scene]['cut'])
    data['pending'] = {'scene_index': scene, 'candidate_index': 0, 'moment_index': moment,
                       'fraction': fraction, 'source': source_path, 'target': expected_target}
    executables = {name: str(Path(shutil.which(name)).resolve(strict=True)) for name in ('ffmpeg', 'ffprobe')}
    _require(all(cuts._file(path, 256 * 1024 * 1024)[0] == data['record']['build']['tools'][name]['executable']
                 for name, path in executables.items()))
    data['pending']['executables'] = executables
    return executables


@_event
def _after_frame(collector, source_duration, seconds, argv, probe_argv):
    data, _ = _checked(collector)
    pending = data['pending']
    _require(type(pending) is dict and 'recipe' not in pending
             and cuts._file(pending['source'])[0] == data['record']['cuts'][pending['scene_index']]['cut'])
    image, _ = cuts._file(pending['target'], adapter.MAX_IMAGE_BYTES)
    _require(type(argv) is list and all(type(item) is str for item in argv)
             and type(source_duration) is float and math.isfinite(source_duration) and source_duration > 0
             and type(seconds) is float and math.isfinite(seconds) and 0 <= seconds < source_duration)
    _require(all(cuts._file(path, 256 * 1024 * 1024)[0] == data['record']['build']['tools'][name]['executable']
                 for name, path in pending['executables'].items())
             and argv[0] == pending['executables']['ffmpeg'])
    _require(seconds == source_duration * pending['fraction']
             and argv == [pending['executables']['ffmpeg'], '-y', '-ss', f'{seconds:.3f}',
                          '-i', str(pending['source']), '-frames:v', '1', '-vf', 'scale=640:-2',
                          '-q:v', '5', str(pending['target'])]
             and type(probe_argv) is list and probe_argv == [pending['executables']['ffprobe'],
                 '-v', 'error', '-show_entries', 'format=duration', '-of',
                 'default=noprint_wrappers=1:nokey=1', str(pending['source'])])
    pending['recipe'] = {'source_duration': source_duration, 'seconds': seconds,
        'duration_probe_argv_sha256': _sha(_raw(probe_argv)),
        'duration_probe_argv': ['$ffprobe' if item == pending['executables']['ffprobe'] else
                               '$cut' if item == str(pending['source']) else item for item in probe_argv],
        'executed_argv_sha256': _sha(_raw(argv)),
        'argv': ['$ffmpeg' if item == pending['executables']['ffmpeg'] else
                 '$cut' if item == str(pending['source']) else
                 '$jpeg' if item == str(pending['target']) else item for item in argv]}
    pending['image'] = image


@_event
def _read_frame(collector, path):
    data, _ = _checked(collector)
    pending = data['pending']
    _require(type(pending) is dict and 'image' in pending and Path(path) == pending['target'])
    identity, raw = cuts._file(path, adapter.MAX_IMAGE_BYTES, content=True)
    _require(identity == pending['image'])
    return raw


@_event
def _record_frame(collector, label, image_bytes):
    data, _ = _checked(collector)
    pending = data['pending']
    _require(type(pending) is dict and 'recipe' in pending and type(label) is str
             and type(image_bytes) is bytes and 0 < len(image_bytes) <= adapter.MAX_IMAGE_BYTES
             and pending['image'] == {'sha256': _sha(image_bytes), 'size': len(image_bytes)}
             and cuts._file(pending['target'], adapter.MAX_IMAGE_BYTES)[0] == pending['image'])
    _require(sum(len(value) for value in data['images']) + len(image_bytes) <= adapter.MAX_DECODED_IMAGE_BYTES)
    event = {name: pending[name] for name in ('scene_index', 'candidate_index', 'moment_index', 'fraction', 'recipe', 'image')}
    event.update(label_sha256=_sha(label.encode()), label_size=len(label.encode()),
                 cut_sha256=data['record']['cuts'][pending['scene_index']]['cut']['sha256'])
    data['events'].append(event)
    data['images'].append(image_bytes)
    data['pending'] = None


@_event
def _bind_request(collector, parts, instruction, schema):
    data, scope = _checked(collector)
    _require(data['body'] is None and data['pending'] is None and len(data['events']) == 30)
    _before_visual(data, scope)
    images, labels = [], []
    for index, part in enumerate(parts):
        if part.get('type') != 'image_url':
            continue
        _require(index > 0 and parts[index-1].get('type') == 'text')
        url = part['image_url']['url']
        _require(type(url) is str and url.startswith('data:image/jpeg;base64,'))
        images.append(base64.b64decode(url.split(',', 1)[1], validate=True))
        labels.append(parts[index-1]['text'])
    _require(images == data['images'] and len(labels) == 30
             and all(_sha(label.encode()) == event['label_sha256']
                     and len(label.encode()) == event['label_size'] for label, event in zip(labels, data['events'])))
    body = {'model': adapter.MODEL, 'messages': [{'role': 'system', 'content': instruction},
            {'role': 'user', 'content': parts}], 'response_format': {'type': 'json_schema',
            'json_schema': {'name': 'youtube_review', 'strict': True, 'schema': schema}},
            'max_tokens': 8192, 'stream': False, 'modalities': ['text']}
    data['body'] = adapter._inspect_body(body)
    cuts.verify_retained_cuts(data['cuts'])


def _source(pipe, data, scope, artifact):
    state, source = artifacts._state(pipe, scope, artifact, data['keys'])
    _source_identity(pipe, data)
    if artifacts._captured_keys(data['keys']):
        tag = artifacts.captured_story_predecessor_tag(scope, data['captured_story_predecessor'])
        _require(_raw(tag) == _raw(data['story_predecessor']))
    return state, source


def _source_identity(pipe, data):
    from app.services import preserved_visual_recovery as recovery
    pipe.watch(*recovery._keys(continuity.LEAF_ID))
    actual, fingerprint = recovery._state(continuity.LEAF_ID, pipe)
    expected = data['record']['source']
    _require(fingerprint == expected['source_state_sha256']
             and recovery._digest(actual['spec']) == expected['source_spec_sha256']
             and recovery._digest(actual['generated_asset_candidates']) == expected['source_journal_sha256']
             and actual['audio_candidate_checkpoint']['metadata_sha256'] == expected['source_metadata_sha256']
             and actual['audio_candidate_checkpoint']['audio_sha256'] == expected['audio']['sha256']
             and actual['audio_candidate_checkpoint']['size'] == expected['audio']['size'])
    pointers = sorted(actual['generated_asset_candidates']['entries'], key=lambda row: row['scene_index'])
    _require(all(row['raw'] == {'sha256': pointer['raw_sha256'], 'size': pointer['raw_size'],
                               'provider': pointer['provider']} for row, pointer in zip(data['record']['cuts'], pointers)))


def _before_visual(data, scope):
    # Events and their complete body binding must precede the actual VISUAL
    # reservation. A later re-extraction is not an input-production witness.
    if artifacts._captured_keys(data['keys']):
        from app.services.retained_captured_story_scope import captured_story_predecessor
        actual = captured_story_predecessor(scope)
        tag = artifacts.captured_story_predecessor_tag(scope, actual)
        if 'story_predecessor' in data:
            _require(data['captured_story_predecessor'] is actual
                     and _raw(data['story_predecessor']) == _raw(tag))
        with scope.journal.client.pipeline() as pipe:
            pipe.watch(*data['keys'], *artifacts._anchor_keys(data['keys']).values())
            state = scope.journal._read(pipe)
            source = continuity._derive(pipe, state['policy']['profile_revision'])
            _require(state['slots'] == {} and artifacts._hash(source) == state['policy']['continuity_sha256']
                     and pipe.exists(*artifacts._anchor_keys(data['keys']).values()) == 0)
            _source_identity(pipe, data)
            from app.services.preserved_visual_recovery import _story_fields
            _require(_sha(_raw(_story_fields(json.loads(data['package']))))
                     == tag['evidence']['commitments']['immutable_core_sha256'])
            artifacts._ack_read(pipe)
        data['captured_story_predecessor'] = actual
        data['story_predecessor'] = tag
        return
    actual = runtime.retained_router_review_artifacts().get(_STORY)
    _require(type(actual) is runtime.AcknowledgedRouterReview)
    with scope.journal.client.pipeline() as pipe:
        state, _ = _source(pipe, data, scope, actual)
        _require(set(state['slots']) == {_STORY}
                 and actual.evidence == state['slots'][_STORY]['response']['evidence'])
        artifacts._ack_read(pipe)


def _stored_cuts(data, s3):
    record = data['record']
    prefix = f"recovery/{continuity.LEAF_ID}/retained_cuts/v1/"
    pointers = [{'key': prefix + f"scene-{row['scene_index']:02d}/" + row['cut']['sha256'] + '.mp4',
                 **row['cut'], 'content_type': 'video/mp4'} for row in record['cuts']]
    expected = _raw({**record, 'objects': pointers})
    _require(cuts._read_private(s3, storage.settings.bucket, data['cut_receipt']['manifest']) == expected)
    for pointer in pointers:
        cuts._read_private(s3, storage.settings.bucket, pointer)


def persist_sampled_input_link(collector, artifact, visual_receipt):
    """Exact ACKed wire linkage, separate private object/NX diagnostic anchor."""
    scope = None
    try:
        data, scope = _checked(collector)
        _require(not data['attempted'])
        data['attempted'] = True
        _require(type(artifact) is runtime.AcknowledgedRouterReview and artifact.purpose == _VISUAL
                 and type(visual_receipt) is artifacts.PersistedRouterReviewArtifact
                 and type(data['body']) is bytes and artifact.prepared_body_bytes == data['body']
                 and adapter._canonical(adapter._json_loads(artifact.request_body_bytes.decode())) == data['body'])
        cuts.verify_retained_cuts(data['cuts'])
        s3 = storage._client(single_attempt=True)
        sink = artifacts.RetainedRouterReviewArtifactSink(s3, bucket=storage.settings.bucket)
        sink._keys = data['keys']
        reservation = artifact.reservation
        key = data['keys'][0].rsplit(':', 1)[0] + ':sampled_cut_link:v1:' + reservation['reservation_sha256']
        def current(pipe, expected_anchor=None):
            _checked(collector)
            state, source = _source(pipe, data, scope, artifact)
            pipe.watch(key)
            if expected_anchor is None:
                _require(pipe.exists(key) == 0)
            else:
                ttl = pipe.pttl(key)
                _require(type(ttl) is int and ttl == -1 and pipe.get(key) == expected_anchor.decode())
            anchor = sink._read_anchor(pipe, _VISUAL)
            _require(_raw(visual_receipt.receipt) == _raw(artifacts._receipt(anchor))
                     and anchor['journal_state_sha256'] == artifacts._hash(state)
                     and anchor['continuity_sha256'] == artifacts._hash(source)
                     and anchor['policy_sha256'] == artifacts._hash(state['policy'])
                     and artifact.evidence == state['slots'][_VISUAL]['response']['evidence'])
            if artifacts._captured_keys(data['keys']):
                _require(_raw(anchor['story_predecessor']) == _raw(data['story_predecessor'])
                         and anchor['prior_story_anchor_sha256'] is None)
            return state, source, anchor
        with scope.journal.client.pipeline() as pipe:
            state, source, visual_anchor = current(pipe)
            artifacts._ack_read(pipe)
        _stored_cuts(data, s3)
        bodies = {'prepared': artifact.prepared_body_bytes, 'wire': artifact.request_body_bytes,
                  'response': artifact.response_body_bytes,
                  'result': artifacts._raw(artifact.result, artifacts._LIMITS['result'])}
        manifest = {'version': 1, 'kind': 'retained_router_review_artifact', 'purpose': _VISUAL,
            'source': source, 'journal_state': state, 'reservation': reservation, 'evidence': artifact.evidence,
            'response_status_code': artifact.response_status_code,
            'bodies': {kind: artifacts._pointer(_VISUAL, kind, raw, data['keys']) for kind, raw in bodies.items()},
            'prior_story_anchor_sha256': visual_anchor['prior_story_anchor_sha256'],
            'journal_keys': list(data['keys']), **artifacts._FLAGS}
        if artifacts._captured_keys(data['keys']):
            manifest.update(version=2, story_predecessor=data['story_predecessor'])
        _require(sink._read_blob(visual_anchor['manifest'], _VISUAL, 'manifest') == artifacts._raw(manifest))
        for kind, raw in bodies.items():
            _require(sink._read_blob(manifest['bodies'][kind], _VISUAL, kind) == raw)
        record = {'version': 1, 'kind': 'retained_sampled_cut_link',
            'source': source, 'cut_manifest': data['cut_receipt']['manifest'],
            'cut_package_sha256': data['record']['package_sha256'],
            'cut_build': data['record']['build'], 'journal_keys': list(data['keys']),
            'journal_state_sha256': artifacts._hash(state), 'reservation': reservation,
            'visual_artifact_anchor_sha256': artifacts._hash(visual_anchor),
            'visual_artifact_manifest': visual_anchor['manifest'], 'evidence': artifact.evidence,
            'events': data['events'], 'actual_sampling_events_verified': True,
            'sampled_wire_linkage_verified': True, **_FLAGS}
        if artifacts._captured_keys(data['keys']):
            record.update(version=2, story_predecessor=data['story_predecessor'])
        raw = _raw(record)
        prefix = f'recovery/{continuity.LEAF_ID}/sampled_cut_link/v1/'
        pointer = cuts._put(s3, storage.settings.bucket, prefix + _sha(raw) + '.json', raw, 'application/json')
        cuts.verify_retained_cuts(data['cuts'])
        anchor = {'version': 1, 'kind': 'retained_sampled_cut_link_anchor', 'pointer': pointer,
            'journal_keys': list(data['keys']), 'journal_state_sha256': artifacts._hash(state),
            'visual_artifact_anchor_sha256': artifacts._hash(visual_anchor),
            'cut_manifest_sha256': data['cut_receipt']['manifest']['sha256'],
            'sampled_wire_linkage_verified': True, **_FLAGS}
        if artifacts._captured_keys(data['keys']):
            anchor.update(version=2, story_predecessor=data['story_predecessor'])
        encoded = _raw(anchor)
        with scope.journal.client.pipeline() as pipe:
            after, actual_source, actual_anchor = current(pipe)
            _require(_raw(after) == _raw(state) and _raw(actual_source) == _raw(source)
                     and _raw(actual_anchor) == _raw(visual_anchor))
            pipe.multi(); pipe.set(key, encoded.decode(), nx=True)
            result = pipe.execute()
            _require(type(result) is list and len(result) == 1 and result[0] is True)
        with scope.journal.client.pipeline() as pipe:
            after, actual_source, actual_anchor = current(pipe, encoded)
            _require(_raw(after) == _raw(state) and _raw(actual_source) == _raw(source)
                     and _raw(actual_anchor) == _raw(visual_anchor))
            artifacts._ack_read(pipe)
        _checked(collector)
        receipt = {'version': 1, 'kind': 'retained_sampled_cut_link_receipt', 'anchor_key': key,
                'anchor_sha256': _sha(encoded), 'pointer': pointer,
                'sampled_wire_linkage_verified': True, **_FLAGS}
        if artifacts._captured_keys(data['keys']):
            receipt.update(version=2, story_predecessor=data['story_predecessor'])
        return receipt
    except Exception:
        if scope is not None:
            scope.failed = True
        raise SampledInputLinkageError('retained_sampled_input_unverified') from None
