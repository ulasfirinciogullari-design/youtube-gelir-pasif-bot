"""V6 selected-source materialization and local exact-edit QA inputs.

This module grants no claim, quality approval, funding or publication right.
The worker supplies independently verified SOURCE ancestry and keeps all
normal QA and spending gates. Raw originals and QA-only copies stay separate.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile

from app.services import render, selected_visual_checkpoint as checkpoint, storage
from app.services.voice_candidate_recovery import _canonical_id, _voice_result, _work_directory


_COMMON = {'version', 'source_task_id', 'package_sha256', 'selected_checkpoint'}
_MEDIA = _COMMON | {'repair_only', 'repair_scene_indices'}
_PIN = {'curated_pinned': True, 'selected_recovery_pinned': True}
_UNAPPROVED = {'qa_approved': False, 'publish_eligible': False, 'reusable': False,
               'requires_full_qa': True}
_QA_TOKEN = object()


class SelectedVisualRecoveryError(RuntimeError):
    """Only a bounded, non-sensitive error leaves this adapter."""


class SelectedQAInputs(dict):
    """Private in-process preparation proof; JSON copies are not evidence.

    Public entries contain no local paths. The worker must keep this original
    object privately through QA, never persist it as a grant in a job record.
    This binds trusted Python call sites, not arbitrary Python execution.
    """

    def __init__(self, records, paths, *, _token=None):
        _require(_token is _QA_TOKEN)
        super().__init__(deepcopy(records))
        self._records = checkpoint._json(records)
        self._paths = tuple(sorted(paths.items()))
        self._token = _token


def _require(value):
    if not value:
        raise ValueError('Selected recovery evidence is invalid')


def _pointer(raw, source, package_sha, expected_binding=None):
    _require(type(raw) is dict and set(raw) == checkpoint._POINTER)
    binding = checkpoint._binding(raw['binding'])
    _require(binding['source_task_id'] == source
             and (expected_binding is None or binding == expected_binding)
             and raw['source_task_id'] == source and raw['package_sha256'] == package_sha
             and raw['status'] == 'preserved_selected_candidates'
             and all(type(raw[key]) is type(value) and raw[key] == value
                     for key, value in checkpoint.FLAGS.items()))
    digest = checkpoint._digest(raw['manifest_sha256'])
    checkpoint._integer(raw['manifest_size'], 1, checkpoint.MAX_MANIFEST_BYTES)
    _require(raw['manifest_key'] == f'selected_candidates/{source}/manifests/{digest}.json')
    return deepcopy(raw)


def _contract(raw, scene_count, expected_package_sha256, *, media):
    _require(type(raw) is dict and set(raw) == (_MEDIA if media else _COMMON)
             and type(raw['version']) is int and raw['version'] == 6
             and type(scene_count) is int and 6 <= scene_count <= 12)
    source = _canonical_id(raw['source_task_id'])
    checksum = checkpoint._digest(expected_package_sha256)
    _require(raw['package_sha256'] == checksum)
    _pointer(raw['selected_checkpoint'], source, checksum)
    if media:
        indices = raw['repair_scene_indices']
        _require(raw['repair_only'] is True and type(indices) is list and indices
                 and all(type(index) is int and 0 <= index < scene_count for index in indices)
                 and indices == sorted(set(indices)))
    return deepcopy(raw)


def validate_selected_visual_recovery(raw, scene_count, expected_package_sha256):
    """Validate wire shape only; the immutable manifest fixes the partition."""
    try:
        return _contract(raw, scene_count, expected_package_sha256, media=True)
    except Exception:
        raise SelectedVisualRecoveryError('selected_visual_recovery_contract_invalid') from None


def validate_selected_voice_recovery(raw, scene_count, expected_package_sha256):
    """The same selected pointer binds voice and package, without fresh TTS."""
    try:
        return _contract(raw, scene_count, expected_package_sha256, media=False)
    except Exception:
        raise SelectedVisualRecoveryError('selected_visual_recovery_voice_invalid') from None


def _receive(client, descriptor, mime, output=None):
    """Same bounded byte/MIME proofs as the checkpoint loader, one GET."""
    response = client.get_object(Bucket=storage.settings.bucket, Key=descriptor['key'])
    body = response.get('Body')
    try:
        size = descriptor['size']
        _require(type(response.get('ContentLength')) is int and response['ContentLength'] == size
                 and response.get('ContentType') == mime)
        digest, total, chunks = hashlib.sha256(), 0, []
        stream = output.open('xb') if output is not None else None
        try:
            while chunk := body.read(min(64 * 1024, size + 1 - total)):
                if total == 0 and mime != 'application/json':
                    _require((chunk.startswith(b'ID3') or len(chunk) >= 2 and chunk[0] == 255
                              and chunk[1] & 0xe0 == 0xe0) if mime == 'audio/mpeg'
                             else chunk[4:8] == b'ftyp')
                total += len(chunk)
                _require(total <= size)
                digest.update(chunk)
                if stream is None:
                    chunks.append(chunk)
                else:
                    stream.write(chunk)
            _require(total == size and digest.hexdigest() == descriptor['sha256'])
        finally:
            if stream is not None:
                stream.close()
        return b''.join(chunks) if output is None else output
    finally:
        if body is not None:
            body.close()


def _kind(selection):
    # A saved image-motion/manual-preview score cannot become a retained pass.
    _require(selection.get('synthetic_motion_only') in (None, False)
             and selection.get('source_media_type') in (None, 'video')
             and selection.get('generation_provider') != 'gemini_image_motion')
    if selection.get('generated') is True:
        _require(selection.get('source_type') in {'generated', 'ai'}
                 and selection.get('generation_provider') in checkpoint._PROVIDERS - {'pexels', 'gemini_image_motion'}
                 and not selection.get('stock_provider') and 'pexels_id' not in selection)
        return 'generated'
    _require(selection.get('generated') is False and selection.get('source_type') == 'stock'
             and selection.get('stock_provider') == 'pexels' and not selection.get('generation_provider')
             and type(selection.get('pexels_id')) is int and selection['pexels_id'] > 0)
    return 'stock'


def _raw_spec(scene, path):
    return {**deepcopy(scene['selection']), 'path': str(path), **_PIN}


def load_selected_recovery(media, voice, child_task_id, work_dir, *, expected_binding):
    """Materialize every unique saved object once into an exclusive child dir.

    ``expected_binding`` must be resolved from the source task, not the child.
    All rejected sources remain preserved privately but their render slots are
    empty. A materialized candidate is still unapproved by every quality gate.
    """
    directory = None
    try:
        binding = checkpoint._binding(expected_binding)
        child = _canonical_id(child_task_id)
        _require(child != binding['source_task_id'])
        work = _work_directory(child, work_dir)
        media = validate_selected_visual_recovery(media, 12, media.get('package_sha256'))
        voice = validate_selected_voice_recovery(voice, 12, media['package_sha256'])
        _require(media['source_task_id'] == voice['source_task_id'] == binding['source_task_id']
                 and media['selected_checkpoint'] == voice['selected_checkpoint'])
        pointer = _pointer(media['selected_checkpoint'], binding['source_task_id'],
                           media['package_sha256'], binding)
        client = storage._client()
        payload = _receive(client, {'key': pointer['manifest_key'], 'size': pointer['manifest_size'],
                                   'sha256': pointer['manifest_sha256']}, 'application/json')
        manifest = json.loads(payload)
        _require(checkpoint._json(manifest) == payload)
        checkpoint._validate_manifest(manifest)
        count = len(manifest['scenes'])
        validate_selected_visual_recovery(media, count, manifest['package_sha256'])
        validate_selected_voice_recovery(voice, count, manifest['package_sha256'])
        _require(manifest['binding'] == binding
                 and media['repair_scene_indices'] == manifest['rejected_scene_indices'])
        kinds = {index: _kind(manifest['scenes'][index]['selection'])
                 for index in manifest['accepted_candidate_indices']}
        # mkdir without exist_ok is the local no-overwrite fence. Never remove
        # an existing directory on failure, including an existing symlink.
        destination = work / 'selected_recovery'
        destination.mkdir(mode=0o700)
        directory = destination
        objects = {}
        for descriptor, mime in [(manifest['audio'], 'audio/mpeg')] + [
                (scene['asset'], 'video/mp4') for scene in manifest['scenes']]:
            key = descriptor['key']
            if key in objects:
                _require(objects[key][1] == descriptor and objects[key][2] == mime)
                continue
            path = directory / Path(key).name
            _receive(client, descriptor, mime, path)
            duration = checkpoint._duration(path)
            objects[key] = (path, deepcopy(descriptor), mime, duration)
        audio = objects[manifest['audio']['key']]
        _require(abs(audio[3] - manifest['timing']['voice_duration_seconds']) <= .04)
        for scene in manifest['scenes']:
            _require(abs(objects[scene['asset']['key']][3] - scene['cut']['source_duration_seconds']) <= .04)
        voice_result = _voice_result(manifest['voice'], count)
        voice_result['path'] = str(audio[0])
        pools = [[] for _ in range(count)]
        for index in kinds:
            scene = manifest['scenes'][index]
            pools[index] = [_raw_spec(scene, objects[scene['asset']['key']][0])]
        return {**_UNAPPROVED, 'version': 6, 'status': 'materialized_selected_candidates',
                'source_task_id': binding['source_task_id'], 'child_task_id': child,
                'binding': binding, 'selected_checkpoint': pointer, 'manifest': manifest,
                'package': deepcopy(manifest['package']), 'voice_result': voice_result,
                'scene_visuals': pools, 'repair_scene_indices': list(media['repair_scene_indices']),
                'retained_generated_indices': [i for i, kind in kinds.items() if kind == 'generated'],
                'retained_stock_indices': [i for i, kind in kinds.items() if kind == 'stock'],
                'timing': deepcopy(manifest['timing'])}
    except Exception:
        if directory is not None:
            shutil.rmtree(directory, ignore_errors=True)
        raise SelectedVisualRecoveryError('selected_visual_recovery_unavailable') from None


def _local_file(raw, work, maximum, *, descriptor=None):
    _require(type(raw) is str)
    path = Path(raw)
    _require(path.is_absolute() and '..' not in path.parts and not path.is_symlink()
             and path.resolve(strict=True) == path and path.is_relative_to(work)
             and stat.S_ISREG(path.stat().st_mode) and 1024 <= path.stat().st_size <= maximum)
    digest, size = hashlib.sha256(), 0
    with path.open('rb') as stream:
        while chunk := stream.read(64 * 1024):
            size += len(chunk)
            _require(size <= maximum)
            digest.update(chunk)
    checksum = digest.hexdigest()
    _require(descriptor is None or (checksum, size) == (descriptor['sha256'], descriptor['size']))
    return path, checksum, size


def _validate_local(recovery, work_dir):
    _require(type(recovery) is dict and recovery.get('version') == 6
             and recovery.get('status') == 'materialized_selected_candidates'
             and all(recovery.get(key) is value for key, value in _UNAPPROVED.items()))
    work = _work_directory(_canonical_id(recovery['child_task_id']), work_dir)
    manifest = deepcopy(recovery['manifest'])
    checkpoint._validate_manifest(manifest)
    pointer = _pointer(recovery['selected_checkpoint'], recovery['source_task_id'],
                       manifest['package_sha256'], recovery['binding'])
    payload = checkpoint._json(manifest)
    _require(hashlib.sha256(payload).hexdigest() == pointer['manifest_sha256']
             and len(payload) == pointer['manifest_size'] and manifest['binding'] == recovery['binding']
             and recovery['package'] == manifest['package'] and recovery['timing'] == manifest['timing']
             and recovery['repair_scene_indices'] == manifest['rejected_scene_indices'])
    voice = _voice_result(manifest['voice'], len(manifest['scenes']))
    path = work / 'selected_recovery' / Path(manifest['audio']['key']).name
    _require(recovery['voice_result'] == {**voice, 'path': str(path)})
    _local_file(str(path), work, checkpoint.MAX_AUDIO_BYTES, descriptor=manifest['audio'])
    _require(abs(checkpoint._duration(path) - manifest['timing']['voice_duration_seconds']) <= .04)
    expected = [[] for _ in manifest['scenes']]
    kinds = {}
    for index in manifest['accepted_candidate_indices']:
        scene = manifest['scenes'][index]
        kinds[index] = _kind(scene['selection'])
        raw = work / 'selected_recovery' / Path(scene['asset']['key']).name
        _local_file(str(raw), work, checkpoint.MAX_VIDEO_BYTES, descriptor=scene['asset'])
        _require(abs(checkpoint._duration(raw) - scene['cut']['source_duration_seconds']) <= .04)
        expected[index] = [_raw_spec(scene, raw)]
    _require(recovery['scene_visuals'] == expected
             and recovery['retained_generated_indices'] == [i for i, kind in kinds.items() if kind == 'generated']
             and recovery['retained_stock_indices'] == [i for i, kind in kinds.items() if kind == 'stock'])
    return work, manifest, expected


def _inputs(recovery, work_dir, scene_visuals):
    work, manifest, retained = _validate_local(recovery, work_dir)
    count = len(manifest['scenes'])
    pools = deepcopy(retained if scene_visuals is None else scene_visuals)
    _require(type(pools) is list and len(pools) == count)
    proofs, total = {}, 0
    for index, pool in enumerate(pools):
        if index in manifest['accepted_candidate_indices']:
            _require(pool == retained[index])
        elif scene_visuals is None:
            _require(pool == [])
            continue
        else:
            _require(type(pool) is list and len(pool) == 1 and type(pool[0]) is dict)
            spec = pool[0]
            _require(not spec.get('selected_recovery_qa_only') and not spec.get('selected_recovery_pinned'))
            _require(set(spec) <= checkpoint._SELECTION_REQUIRED | checkpoint._SELECTION_OPTIONAL | {'path', 'curated_pinned'}
                     and ('curated_pinned' not in spec or spec['curated_pinned'] is True))
            selection = {key: value for key, value in spec.items()
                         if key in checkpoint._SELECTION_REQUIRED | checkpoint._SELECTION_OPTIONAL}
            selection.setdefault('selected_spec_index', 0)
            checkpoint._selection(selection)
            _require(_kind(selection) == 'generated')
        path, checksum, size = _local_file(pool[0].get('path'), work, checkpoint.MAX_VIDEO_BYTES)
        total += size
        _require(total <= checkpoint.MAX_TOTAL_VIDEO_BYTES)
        proofs[index] = {'sha256': checksum, 'size': size, 'duration_seconds': checkpoint._duration(path)}
    return work, manifest, pools, proofs


def _identity(manifest, index, spec, proof, checksum, size):
    scene, timing = manifest['scenes'][index], manifest['timing']
    count = scene['cut']['frame_count']
    cut = checkpoint._cut(spec, proof['duration_seconds'], count,
                          scene['cut']['frame_start'], index, scene['cut']['transition'])
    retained = index in manifest['accepted_candidate_indices']
    if retained:
        _require(checkpoint._json(cut) == checkpoint._json(scene['cut']))
    tail = timing['tail_pad_frames'] if index == len(manifest['scenes']) - 1 else 0
    frames = min(count + tail, max(0, timing['target_frames'] - cut['frame_start']))
    _require(frames > 0)
    identity = {'scene_index': index, 'selected_edit_identity': scene['selected_edit_identity'] if retained else None,
                'binding': manifest['binding'], 'package_sha256': manifest['package_sha256'],
                'audio_sha256': manifest['audio']['sha256'], 'renderer': manifest['renderer'],
                'source': proof, 'selection': {key: value for key, value in spec.items() if key != 'path'},
                'cut': cut, 'tail_pad_frames': tail, 'frame_count': frames,
                'qa_sha256': checksum, 'qa_size': size}
    return {**identity, 'qa_input_identity': checkpoint._sha(identity), 'qa_approved': False}


def prepare_selected_exact_visuals(recovery, work_dir, *, scene_visuals=None):
    """Prepare retained-only, or full final, QA cuts; never mutate final inputs.

    The tail is applied to the QA copy exactly where render_video applies it:
    after normalizing the original voice-length cut. Adaptive letterbox checks
    run in normalize_clip. No historical score becomes a current QA result.
    """
    directory = None
    try:
        work, manifest, pools, source_proofs = _inputs(recovery, work_dir, scene_visuals)
        count = len(manifest['scenes'])
        timing = manifest['timing']
        frame_counts = [scene['cut']['frame_count'] for scene in manifest['scenes']]
        directory = Path(tempfile.mkdtemp(prefix='selected_exact_qa_', dir=work))
        inputs, identities, paths = {}, {}, {}
        for index, proof in source_proofs.items():
            spec, scene = pools[index][0], manifest['scenes'][index]
            cut = checkpoint._cut(spec, proof['duration_seconds'], frame_counts[index],
                                  scene['cut']['frame_start'], index, scene['cut']['transition'])
            if index in manifest['accepted_candidate_indices']:
                _require(checkpoint._json(cut) == checkpoint._json(scene['cut']))
            normal = directory / f'normalized-{index:02d}.mp4'
            render.normalize_clip(spec, normal, frame_counts[index] / render.FPS,
                                  index, cut['transition'], '1080x1920')
            tail = timing['tail_pad_frames'] if index == count - 1 else 0
            qa_frames = min(frame_counts[index] + tail,
                            max(0, timing['target_frames'] - cut['frame_start']))
            _require(qa_frames > 0)
            output = normal
            if tail or qa_frames != frame_counts[index]:
                output = directory / f'final-cut-{index:02d}.mp4'
                filters = ([f'tpad=stop_mode=clone:stop={tail}'] if tail else []) + [
                    f'trim=end_frame={qa_frames}', f'settb=expr=1/{render.FPS}',
                    'setpts=N', 'setsar=1', 'format=yuv420p']
                subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-n', '-i', str(normal),
                                '-vf', ','.join(filters), '-frames:v', str(qa_frames), '-an',
                                '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '19', str(output)],
                               check=True, timeout=60, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            _require(render.video_frame_count(output) == qa_frames)
            _, checksum, size = _local_file(str(output), work, checkpoint.MAX_VIDEO_BYTES)
            _local_file(spec['path'], work, checkpoint.MAX_VIDEO_BYTES, descriptor=proof)
            identities[index] = _identity(manifest, index, spec, proof, checksum, size)
            paths[index] = str(output)
            inputs[index] = [{**spec, 'path': str(output), 'start_fraction': 0.0,
                              'forbid_loop': True, 'selected_recovery_qa_only': True}]
        return {**_UNAPPROVED, 'scene_visuals': inputs, 'frame_counts': frame_counts,
                'target_frames': timing['target_frames'], 'tail_pad_frames': timing['tail_pad_frames'],
                'qa_inputs': SelectedQAInputs(identities, paths, _token=_QA_TOKEN)}
    except Exception:
        if directory is not None:
            shutil.rmtree(directory, ignore_errors=True)
        raise SelectedVisualRecoveryError('selected_visual_recovery_exact_cut_unavailable') from None


def verify_selected_final_inputs(recovery, work_dir, scene_visuals, qa_inputs):
    """Rehash exact prepared inputs just before render; no QA or render calls.

    A successful return only confirms identity. The worker separately needs
    current positive QA verdicts for every scene and the unchanged narration.
    """
    try:
        _require(type(qa_inputs) is SelectedQAInputs and qa_inputs._token is _QA_TOKEN
                 and checkpoint._json(qa_inputs) == qa_inputs._records)
        work, manifest, pools, proofs = _inputs(recovery, work_dir, scene_visuals)
        expected = set(range(len(manifest['scenes'])))
        _require(set(proofs) == set(qa_inputs) == expected
                 and all(type(index) is int for index in qa_inputs))
        paths = dict(qa_inputs._paths)
        _require(set(paths) == expected)
        for index in sorted(expected):
            _require(pools[index][0]['path'] not in paths.values())
            output, checksum, size = _local_file(paths[index], work, checkpoint.MAX_VIDEO_BYTES)
            actual = _identity(manifest, index, pools[index][0], proofs[index], checksum, size)
            _require(checkpoint._json(actual) == checkpoint._json(qa_inputs[index])
                     and render.video_frame_count(output) == actual['frame_count'])
        return None
    except Exception:
        raise SelectedVisualRecoveryError('selected_visual_recovery_final_inputs_changed') from None
