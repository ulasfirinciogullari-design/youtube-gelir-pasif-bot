"""Private selected-edit candidates, never QA, dispatch or spending authority.

The final critic normally reviewed a raw candidate pool, not the later edit.
Its historical observation is kept separately from the selected edit identity.
No reviewer, generation, renderer, Redis, or publication API is called here.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
from pathlib import Path
import re
import subprocess
import tempfile
from uuid import UUID

from app.services import render, storage
from app.services.audio_checkpoint import _candidate_voice, _local_candidate_path
from app.services.generated_asset_checkpoint import _put_immutable, _snapshot, _verify_existing
from app.services.qa_workprint import _selected
from app.services.visual_allocation_checkpoint import _review, _work_path


MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_VIDEO_BYTES = 100 * 1024 * 1024
MAX_TOTAL_VIDEO_BYTES = 512 * 1024 * 1024
MAX_AUDIO_BYTES = 14 * 1024 * 1024
FLAGS = {'version': 1, 'accepted': False, 'qa_approved': False, 'reusable': False,
         'publish_eligible': False, 'requires_full_qa': True,
         'qa_input_verified': False, 'exact_cut_qa_required': True}
_BINDING = {'source_task_id', 'lineage_id', 'channel_id', 'connection_id', 'ancestry_sha256'}
_POINTER = set(FLAGS) | {'status', 'source_task_id', 'binding', 'package_sha256',
                         'manifest_key', 'manifest_sha256', 'manifest_size'}
_SELECTION_REQUIRED = {'selected_spec_index', 'start_fraction', 'forbid_loop'}
_SELECTION_OPTIONAL = {'generated', 'preserve_start_fraction', 'synthetic_motion_only',
                       'source_media_type', 'source_type', 'stock_provider',
                       'generation_provider', 'generation_provider_attempts',
                       'motion_recipe_version', 'pexels_id'}
_PROVIDERS = {'pexels', 'runway', 'gemini_veo', 'fal', 'replicate', 'openai', 'gemini_image_motion',
              'fal_veo_lite', 'fal_seedance_15_pro', 'fal_seedance_1_fast',
              'gemini_veo_fast', 'gemini_veo_standard', 'gemini_omni', 'fal_seedance_2_fast'}


class SelectedVisualCheckpointError(RuntimeError):
    """A bounded failure message without private paths or provider responses."""


def _require(condition):
    if not condition:
        raise ValueError('Invalid selected candidate')


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def _sha(value):
    return hashlib.sha256(_json(value)).hexdigest()


def _digest(value):
    _require(type(value) is str and re.fullmatch(r'[0-9a-f]{64}', value) is not None)
    return value


def _number(value, low, high):
    _require(type(value) in (int, float) and math.isfinite(value) and low <= value <= high)
    return float(value)


def _integer(value, low, high):
    _require(type(value) is int and low <= value <= high)
    return value


def _binding(value):
    _require(type(value) is dict and set(value) == _BINDING)
    for key in ('source_task_id', 'lineage_id'):
        _require(type(value[key]) is str and str(UUID(value[key])) == value[key])
    _require(type(value['channel_id']) is str
             and re.fullmatch(r'UC[A-Za-z0-9_-]{22}', value['channel_id']) is not None)
    _require(type(value['connection_id']) is str
             and re.fullmatch(r'[A-Za-z0-9_-]{8,128}', value['connection_id']) is not None)
    _digest(value['ancestry_sha256'])
    return dict(value)


def _package(value):
    _require(type(value) is dict)
    material = {key: item for key, item in value.items()
                if key not in {'_recovered_generated_media', '_recovered_voice'}}
    encoded = _json(material)
    _require(len(encoded) <= MAX_MANIFEST_BYTES)
    # Only JSON data is supported; never silently stringify an arbitrary object.
    result = json.loads(encoded)
    _require(result == material)
    scenes = result.get('scenes')
    _require(type(scenes) is list and 6 <= len(scenes) <= 12)
    for index, scene in enumerate(scenes):
        _require(type(scene) is dict and type(scene.get('narration')) is str
                 and 0 < len(scene['narration'].strip()) <= 4000
                 and ('index' not in scene or type(scene['index']) is int and scene['index'] == index))
    narration = ' '.join(scene['narration'] for scene in scenes)
    _require('narration' not in result or result['narration'] == narration)
    return result


def _duration(path):
    # Probe the copied bytes, bypassing the renderer's path-based duration cache.
    value = subprocess.check_output([
        'ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe',
        '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', str(path),
    ], text=True, stderr=subprocess.DEVNULL, timeout=15)
    _require(len(value) <= 128)
    return _number(float(value.strip()), .01, 7200)


def _selection(value):
    _require(type(value) is dict and _SELECTION_REQUIRED <= set(value)
             and not set(value) - _SELECTION_REQUIRED - _SELECTION_OPTIONAL)
    _integer(value['selected_spec_index'], 0, 23)
    _number(value['start_fraction'], 0, 1)
    for field in ('forbid_loop', 'generated', 'preserve_start_fraction', 'synthetic_motion_only'):
        _require(field not in value or type(value[field]) is bool)
    if 'source_media_type' in value:
        _require(value['source_media_type'] in {'image', 'video'})
    if 'source_type' in value:
        _require(value['source_type'] in {'stock', 'generated', 'ai'})
    for field in ('stock_provider', 'generation_provider'):
        _require(field not in value or value[field] in _PROVIDERS)
    if 'pexels_id' in value:
        _integer(value['pexels_id'], 1, 2**63 - 1)
    if 'generation_provider_attempts' in value:
        _integer(value['generation_provider_attempts'], 1, 10)
    if 'motion_recipe_version' in value:
        _require(value['motion_recipe_version'] == 'diagonal-push-v2')
    return dict(value)


def _renderer_identity():
    payload = Path(render.__file__).read_bytes()
    _require(len(payload) <= MAX_MANIFEST_BYTES)
    return {'source_sha256': hashlib.sha256(payload).hexdigest(),
            'adaptive_letterbox_crop_verified': False}


def _cut(selection, source_seconds, frames, start_frame, index, transition):
    source_seconds = _number(source_seconds, .01, 7200)
    seconds = frames / render.FPS
    fraction = render._spec_start_fraction(selection)
    start = min(max(0.0, source_seconds - seconds - .08),
                max(0.0, source_seconds * fraction - seconds * .40))
    speed = render._clip_speed(selection, source_seconds, seconds, index)
    return {'source_duration_seconds': source_seconds, 'start_seconds': start,
            'playback_speed': speed, 'frame_start': start_frame, 'frame_count': frames,
            'segment_seconds': seconds, 'transition': transition,
            'shot_index': index, 'crop_selector_index': index % 5,
            'single_pass_sufficient': source_seconds + .04 >= start + seconds * speed + .04,
            'output_resolution': '1080x1920'}


def _edit_identity(manifest, scene):
    return _sha({'binding': manifest['binding'], 'package_sha256': manifest['package_sha256'],
                 'audio_sha256': manifest['audio']['sha256'], 'timing': manifest['timing'],
                 'renderer': manifest['renderer'],
                 'scene_index': scene['scene_index'], 'asset': scene['asset'],
                 'selection': scene['selection'], 'cut': scene['cut']})


def _object(prefix, kind, checksum, size):
    extension = 'mp3' if kind == 'audio' else 'mp4'
    return {'key': f'{prefix}{kind}/{checksum}.{extension}', 'sha256': checksum, 'size': size}


def _validate_manifest(manifest):
    _require(type(manifest) is dict and set(manifest) == set(FLAGS) | {
        'status', 'source_task_id', 'binding', 'package', 'package_sha256', 'voice', 'audio',
        'timing', 'renderer', 'quality_threshold', 'accepted_candidate_indices', 'rejected_scene_indices', 'scenes'})
    _require(all(type(manifest.get(key)) is type(value) and manifest[key] == value for key, value in FLAGS.items())
             and manifest['status'] == 'preserved_selected_candidates')
    binding = _binding(manifest['binding'])
    _require(manifest['source_task_id'] == binding['source_task_id'])
    package = _package(manifest['package'])
    _require(package == manifest['package'] and _sha(package) == _digest(manifest['package_sha256']))
    # A changed render recipe needs a new edit review, never automatic reuse.
    _require(type(manifest['renderer']) is dict
             and _json(manifest['renderer']) == _json(_renderer_identity()))
    count = len(package['scenes'])
    voice = manifest['voice']
    _require(type(voice) is dict and type(voice.get('voice_profile')) is dict)
    flat_voice = {**{key: value for key, value in voice.items() if key != 'voice_profile'}, **voice['voice_profile']}
    _require(_candidate_voice(flat_voice, count) == voice and all(voice['spoken_texts']))
    timing = manifest['timing']
    _require(type(timing) is dict and set(timing) == {
        'requested_seconds', 'voice_duration_seconds', 'effective_edit_target_seconds',
        'fps', 'target_frames', 'voice_frames', 'tail_pad_frames'})
    measured = _number(timing['voice_duration_seconds'], .1, 30.08)
    target = _number(timing['effective_edit_target_seconds'], .1, 30)
    _require(timing['requested_seconds'] == 30 and type(timing['requested_seconds']) is int
             and type(timing['fps']) is int and timing['fps'] == render.FPS == 30
             and abs(target * render.FPS - round(target * render.FPS)) < 1e-6
             and measured <= target + .08
             and abs(voice['duration_after_fit'] - measured) <= .12
             and abs(sum(voice['scene_durations']) - measured) <= .12)
    for value in voice['scene_durations']:
        _number(value, .1, 15)
    threshold = _integer(manifest['quality_threshold'], 1, 100)
    rejected = manifest['rejected_scene_indices']
    _require(type(rejected) is list and 1 <= len(rejected) <= count
             and all(type(index) is int and 0 <= index < count for index in rejected)
             and rejected == sorted(set(rejected)))
    accepted = [index for index in range(count) if index not in rejected]
    _require(type(manifest['accepted_candidate_indices']) is list
             and all(type(index) is int for index in manifest['accepted_candidate_indices'])
             and manifest['accepted_candidate_indices'] == accepted)
    scenes = manifest['scenes']
    _require(type(scenes) is list and len(scenes) == count)
    prefix = f"selected_candidates/{binding['source_task_id']}/"
    audio = manifest['audio']
    _require(type(audio) is dict and audio == _object(prefix, 'audio', _digest(audio.get('sha256')),
                                                      _integer(audio.get('size'), 1024, MAX_AUDIO_BYTES)))
    timeline = render._scene_timeline(package['scenes'], [[{'path': 'verified-candidate'}]] * count,
                                      voice['scene_durations'], measured, [])
    frames = render._timeline_frame_counts(timeline, measured)
    target_frames = max(1, round(target * render.FPS))
    _require(timing['target_frames'] == target_frames and timing['voice_frames'] == sum(frames)
             and timing['tail_pad_frames'] == max(0, target_frames - sum(frames))
             and all(type(timing[field]) is int for field in ('target_frames', 'voice_frames', 'tail_pad_frames')))
    total, cursor = 0, 0
    for index, scene in enumerate(scenes):
        _require(type(scene) is dict and set(scene) == {
            'scene_index', 'accepted_candidate', 'asset', 'selection', 'cut',
            'qa_observation', 'selected_edit_identity'}
                 and type(scene['scene_index']) is int and scene['scene_index'] == index
                 and type(scene['accepted_candidate']) is bool and scene['accepted_candidate'] == (index in accepted))
        asset = scene['asset']
        _require(type(asset) is dict and asset == _object(prefix, 'video', _digest(asset.get('sha256')),
                                                          _integer(asset.get('size'), 1024, MAX_VIDEO_BYTES)))
        total += asset['size']
        _require(total <= MAX_TOTAL_VIDEO_BYTES)
        selection = _selection(scene['selection'])
        cut = scene['cut']
        _require(type(cut) is dict and _json(cut) == _json(_cut(selection, cut.get('source_duration_seconds'),
                                                              frames[index], cursor, index, timeline[index][2])))
        cursor += frames[index]
        observation = scene['qa_observation']
        _require(type(observation) is dict and type(observation.get('gates')) is dict)
        flattened = {**{key: value for key, value in observation.items() if key != 'gates'}, **observation['gates']}
        _require(_review(flattened) == observation)
        _number(observation.get('score'), 0, 100)
        if index in accepted:
            _number(observation.get('score'), threshold, 100)
        _require(scene['selected_edit_identity'] == _edit_identity(manifest, scene))
    return manifest


def persist_selected_visual_checkpoint(
    task_id: str, work_dir: str | Path, *, binding: dict, package: dict,
    voice_result: dict, scene_visuals: list, final_reviews: dict,
    rejected_scene_indices: list, quality_threshold: int, options: dict,
    effective_edit_target_seconds: float, duration_minutes: float = .5,
) -> dict:
    """Save exact existing bytes, then publish a private content-addressed pointer.

    ``binding`` and the final selected/rejected mapping must come from verified
    server state. This helper does not resolve ancestry or attest historic QA
    input frames. Even an accepted_candidate remains unapproved and unusable
    by automation until a separate complete recovery/QA/budget admission.
    """
    try:
        binding = _binding(binding)
        _require(task_id == binding['source_task_id'] and type(options) is dict
                 and options.get('mode') == 'production' and options.get('format') == 'shorts'
                 and options.get('music') == 'off'
                 and type(duration_minutes) in (int, float) and duration_minutes == .5)
        package = _package(package)
        count = len(package['scenes'])
        _require(type(scene_visuals) is list and len(scene_visuals) == count
                 and type(final_reviews) is dict
                 and set(final_reviews) == set(range(count))
                 and all(type(index) is int for index in final_reviews)
                 and type(rejected_scene_indices) is list and 1 <= len(rejected_scene_indices) <= count)
        work = _work_path(task_id, work_dir)
        voice_path = _local_candidate_path(task_id, voice_result)
        _require(voice_path.name != 'recovered_voice.mp3' or voice_path.parent == work)
        voice = _candidate_voice(voice_result, count)
        with tempfile.TemporaryDirectory(prefix='selected_checkpoint_', dir=work) as directory:
            directory = Path(directory)
            audio_path = directory / 'voice.mp3'
            audio_sha, audio_size = _snapshot(voice_path, audio_path, MAX_AUDIO_BYTES, audio=True)
            measured = _duration(audio_path)
            prefix = f'selected_candidates/{task_id}/'
            manifest = {**FLAGS, 'status': 'preserved_selected_candidates', 'source_task_id': task_id,
                        'binding': binding, 'package': package, 'package_sha256': _sha(package),
                        'voice': voice, 'audio': _object(prefix, 'audio', audio_sha, audio_size),
                        'renderer': _renderer_identity(),
                        'quality_threshold': quality_threshold,
                        'rejected_scene_indices': list(rejected_scene_indices),
                        'accepted_candidate_indices': [i for i in range(count) if i not in rejected_scene_indices],
                        'scenes': []}
            selected, paths = [], []
            for index in range(count):
                review = final_reviews[index]
                _require(type(review) is dict)
                _number(review.get('score'), 0, 100)
                _require('scene_index' not in review
                         or type(review['scene_index']) is int and review['scene_index'] == index)
                spec, provenance = _selected(scene_visuals[index], review, work)
                # _selected's legacy provider whitelist predates some current routes.
                original = scene_visuals[index][provenance['selected_spec_index']]
                for field in ('stock_provider', 'generation_provider'):
                    if field in original:
                        _require(original[field] in _PROVIDERS)
                        provenance[field] = original[field]
                for field in ('generation_provider_attempts', 'motion_recipe_version'):
                    if field in original and original[field] is not None:
                        provenance[field] = original[field]
                path = directory / f'scene-{index:02d}.mp4'
                checksum, size = _snapshot(Path(spec['path']), path, MAX_VIDEO_BYTES)
                _require((checksum, size) == (provenance.pop('sha256'), provenance.pop('size')))
                selection = _selection(provenance)
                selected.append([{**selection, 'path': str(path)}])
                paths.append(path)
                manifest['scenes'].append({'scene_index': index, 'accepted_candidate': index not in rejected_scene_indices,
                    'asset': _object(prefix, 'video', checksum, size), 'selection': selection,
                    'qa_observation': _review(review)})
            timeline = render._scene_timeline(package['scenes'], selected, voice['scene_durations'], measured, [])
            _require(len(timeline) == count and [item[3] for item in timeline] == list(range(count)))
            frames = render._timeline_frame_counts(timeline, measured)
            target = _number(effective_edit_target_seconds, .1, 30)
            target_frames = max(1, round(target * render.FPS))
            manifest['timing'] = {'requested_seconds': 30, 'voice_duration_seconds': measured,
                'effective_edit_target_seconds': target, 'fps': render.FPS,
                'target_frames': target_frames, 'voice_frames': sum(frames),
                'tail_pad_frames': max(0, target_frames - sum(frames))}
            cursor = 0
            for index, scene in enumerate(manifest['scenes']):
                scene['cut'] = _cut(scene['selection'], _duration(paths[index]), frames[index], cursor,
                                     index, timeline[index][2])
                cursor += frames[index]
                scene['selected_edit_identity'] = _edit_identity(manifest, scene)
            _validate_manifest(manifest)
            payload = _json(manifest)
            _require(len(payload) <= MAX_MANIFEST_BYTES)
            checksum = hashlib.sha256(payload).hexdigest()
            key = f'{prefix}manifests/{checksum}.json'
            client = storage._client()
            for path, descriptor, mime in [(audio_path, manifest['audio'], 'audio/mpeg')] + [
                (path, scene['asset'], 'video/mp4') for path, scene in zip(paths, manifest['scenes'])]:
                with path.open('rb') as incoming:
                    _put_immutable(client, descriptor['key'], incoming, descriptor['sha256'], descriptor['size'], mime)
            _put_immutable(client, key, io.BytesIO(payload), checksum, len(payload), 'application/json')
        return {**FLAGS, 'status': 'preserved_selected_candidates', 'source_task_id': task_id,
                'binding': binding, 'package_sha256': manifest['package_sha256'],
                'manifest_key': key, 'manifest_sha256': checksum, 'manifest_size': len(payload)}
    except Exception:
        raise SelectedVisualCheckpointError('selected_visual_checkpoint_unavailable') from None


def load_selected_visual_checkpoint(pointer: dict, *, expected_binding: dict,
                                    expected_package_sha256: str) -> dict:
    """Read and verify every saved object; return candidates, never permission.

    Expected binding/hash must be independently recovered from private server
    state. This GET-only loader neither creates local render inputs nor reads,
    initializes, resets or extends a funding/scene/attempt/dispatch ledger.
    """
    try:
        expected_binding = _binding(expected_binding)
        _digest(expected_package_sha256)
        _require(type(pointer) is dict and set(pointer) == _POINTER
                 and all(type(pointer.get(key)) is type(value) and pointer[key] == value for key, value in FLAGS.items())
                 and pointer['status'] == 'preserved_selected_candidates'
                 and pointer['source_task_id'] == expected_binding['source_task_id']
                 and pointer['binding'] == expected_binding
                 and pointer['package_sha256'] == expected_package_sha256)
        size = _integer(pointer['manifest_size'], 1, MAX_MANIFEST_BYTES)
        checksum = _digest(pointer['manifest_sha256'])
        _require(pointer['manifest_key'] == f"selected_candidates/{expected_binding['source_task_id']}/manifests/{checksum}.json")
        client = storage._client()
        response = client.get_object(Bucket=storage.settings.bucket, Key=pointer['manifest_key'])
        body = response['Body']
        try:
            _require(type(response.get('ContentLength')) is int and response['ContentLength'] == size
                     and response.get('ContentType') == 'application/json')
            payload = body.read(size + 1)
            _require(len(payload) == size and hashlib.sha256(payload).hexdigest() == checksum)
        finally:
            body.close()
        manifest = json.loads(payload)
        _require(_json(manifest) == payload)
        _validate_manifest(manifest)
        _require(manifest['binding'] == expected_binding and manifest['package_sha256'] == expected_package_sha256)
        for descriptor, mime in [(manifest['audio'], 'audio/mpeg')] + [
            (scene['asset'], 'video/mp4') for scene in manifest['scenes']]:
            _verify_existing(client, descriptor['key'], descriptor['sha256'], descriptor['size'], mime)
        return {**manifest, 'status': 'verified_selected_candidates'}
    except Exception:
        raise SelectedVisualCheckpointError('selected_visual_checkpoint_ineligible') from None
