"""Private, unapproved workprints of an actual failed visual edit.

Only existing task-local media, the ordinary renderer, and existing Storage are
used. This diagnostic never changes jobs, QA, recovery contracts or publishers.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
from pathlib import Path
import re
import stat
import subprocess
import tempfile
from uuid import UUID

from app.services import storage
from app.services.audio_checkpoint import _local_candidate_path
from app.services.render import media_duration, render_video, _notify_render_progress
from app.services.visual_allocation_checkpoint import _review, _text, _work_path


MAX_VIDEO_BYTES = 64 * 1024 * 1024
MAX_LONG_VIDEO_BYTES = 192 * 1024 * 1024
MAX_SOURCE_BYTES = 512 * 1024 * 1024
MAX_METADATA_BYTES = 1024 * 1024
_ETAG = re.compile(r'"[A-Za-z0-9_-]{1,128}"')
_PROVIDERS = {'pexels', 'runway', 'gemini_veo', 'fal', 'replicate', 'openai',
              'fal_veo_lite', 'fal_seedance_15_pro', 'fal_seedance_1_fast',
              'gemini_image_motion'}


def _number(value: object, low: float, high: float) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError('Invalid diagnostic timing')
    return float(value)


def _file(path: object, work: Path, maximum: int) -> Path:
    if not isinstance(path, (str, Path)) or '://' in str(path):
        raise ValueError('Invalid diagnostic media')
    candidate = Path(path)
    if not candidate.is_absolute() or '..' in candidate.parts or candidate.suffix.lower() != '.mp4':
        raise ValueError('Invalid diagnostic media')
    relative = candidate.relative_to(work)
    resolved = candidate.resolve(strict=True)
    if (resolved != work / relative or not stat.S_ISREG(resolved.stat().st_mode)
            or not 12 <= resolved.stat().st_size <= maximum):
        raise ValueError('Invalid diagnostic media')
    with resolved.open('rb') as incoming:
        if incoming.read(12)[4:8] != b'ftyp':
            raise ValueError('Invalid diagnostic container')
    return resolved


def _digest(path: Path, maximum: int) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    with path.open('rb') as incoming:
        while chunk := incoming.read(64 * 1024):
            size += len(chunk)
            if size > maximum:
                raise ValueError('Diagnostic media exceeded its bound')
            digest.update(chunk)
    return digest.hexdigest(), size


def _selected(specs: object, review: dict, work: Path) -> tuple[dict, dict]:
    if not isinstance(specs, list) or not 1 <= len(specs) <= 24:
        raise ValueError('Missing diagnostic candidates')
    # A singleton is the actual already-collapsed _apply_visual_review result.
    # For a remaining pool the exact review index is mandatory, never index 0.
    index = 0 if len(specs) == 1 else review.get('best_candidate_index')
    if type(index) is not int or not 0 <= index < len(specs) or not isinstance(specs[index], dict):
        raise ValueError('Ambiguous diagnostic selection')
    original = specs[index]
    path = _file(original.get('path'), work, MAX_SOURCE_BYTES)
    if original.get('preserve_start_fraction') is True and 'start_fraction' not in original:
        raise ValueError('Invalid diagnostic render identity')
    fraction = original.get('start_fraction', 0.0)
    if original.get('preserve_start_fraction') is not True:
        fraction = review.get('best_start_fraction', fraction)
    fraction = _number(fraction, 0, 1)
    spec = {'path': str(path), 'start_fraction': fraction,
            'forbid_loop': original.get('forbid_loop') is True}
    checksum, size = _digest(path, MAX_SOURCE_BYTES)
    provenance = {'selected_spec_index': index, 'start_fraction': fraction,
                  'sha256': checksum, 'size': size, 'forbid_loop': spec['forbid_loop']}
    # Keep exactly the safe identity used by the shared renderer's generated
    # action timing. Dropping it would make this workprint a different edit.
    for field in ('generated', 'preserve_start_fraction', 'synthetic_motion_only'):
        value = original.get(field)
        if value is not None and type(value) is not bool:
            raise ValueError('Invalid diagnostic render identity')
        if type(value) is bool:
            spec[field] = provenance[field] = value
    media_type = original.get('source_media_type')
    if media_type not in (None, 'image', 'video'):
        raise ValueError('Invalid diagnostic render identity')
    if media_type is not None:
        spec['source_media_type'] = provenance['source_media_type'] = media_type
    for field in ('source_type', 'stock_provider', 'generation_provider'):
        value = original.get(field)
        allowed = {'stock', 'generated', 'ai'} if field == 'source_type' else _PROVIDERS
        if value in allowed:
            spec[field] = provenance[field] = value
    if type(original.get('pexels_id')) is int and original['pexels_id'] > 0:
        provenance['pexels_id'] = original['pexels_id']
    return spec, provenance


def _probe(path: Path, target_seconds: float = 30.0, *, landscape: bool = False) -> None:
    """Check the actual local master, not a renderer's reported success flag."""
    raw = subprocess.check_output([
        'ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-count_frames',
        '-show_entries', 'stream=codec_type,width,height,nb_read_frames,avg_frame_rate:format=duration',
        '-of', 'json', str(path),
    ], text=True, stderr=subprocess.DEVNULL, timeout=60)
    if len(raw) > 64 * 1024:
        raise ValueError('Invalid diagnostic probe')
    data = json.loads(raw)
    duration = float(data['format']['duration'])
    videos = [item for item in data.get('streams', []) if item.get('codec_type') == 'video']
    audios = [item for item in data.get('streams', []) if item.get('codec_type') == 'audio']
    width, height = (1920, 1080) if landscape else (1080, 1920)
    if (len(videos) != 1 or len(audios) != 1 or videos[0].get('width') != width
            or videos[0].get('height') != height or str(videos[0].get('nb_read_frames')) != str(round(target_seconds * 30))
            or videos[0].get('avg_frame_rate') != '30/1'
            or not math.isfinite(duration) or abs(duration - target_seconds) > 0.034):
        raise ValueError('Invalid diagnostic master')


def _put_immutable(client, key: str, body, size: int, checksum: str, content_type: str) -> str:
    """No ACL, signed URL, overwrite or new provider. An identical retry is safe."""
    try:
        response = client.put_object(
            Bucket=storage.settings.bucket, Key=key, Body=body, ContentLength=size,
            ContentType=content_type, CacheControl='private, no-store',
            Metadata={'sha256': checksum}, IfNoneMatch='*',
        )
    except Exception as exc:
        error = getattr(exc, 'response', {})
        if not isinstance(error, dict) or str(error.get('Error', {}).get('Code')) not in {'412', 'PreconditionFailed'}:
            raise ValueError('Diagnostic storage unavailable') from None
        response = client.head_object(Bucket=storage.settings.bucket, Key=key)
        if (response.get('ContentLength') != size or response.get('ContentType') != content_type
                or response.get('Metadata', {}).get('sha256') != checksum):
            raise ValueError('Diagnostic storage conflict')
    etag = response.get('ETag')
    if not isinstance(etag, str) or _ETAG.fullmatch(etag) is None:
        raise ValueError('Invalid diagnostic storage receipt')
    return etag


def persist_qa_workprint(
    task_id: str, work_dir: str | Path, *, scenes: list[dict],
    scene_visuals: list[list[dict]], final_reviews: dict[int, dict],
    voice_result: dict, scene_durations: list[float], narration: str,
    options: dict, voice_quality_passed: bool, target_seconds: float = 30.0,
    progress_callback=None,
) -> dict:
    """Return only ``qa_workprint``; unavailable diagnostics return an empty dict.

    The caller must pass the actual final-review mapping and explicitly attest
    that ordinary narration/prosody gates already passed. Nothing here asserts
    visual approval, skips a gate, queues a retry or publishes a video.
    """
    try:
        long = (isinstance(options, dict) and options.get('format') == 'landscape'
                and type(options.get('content_plan_item_id')) is str
                and str(UUID(options['content_plan_item_id'])) == options['content_plan_item_id'])
        if (not isinstance(task_id, str) or str(UUID(task_id)) != task_id
                or voice_quality_passed is not True or not isinstance(options, dict)
                or options.get('mode') != 'production' or (not long and options.get('format') != 'shorts')
                or not (150 <= _number(target_seconds, 150, 240) <= 240 if long else
                        30 <= _number(target_seconds, 30, 40 if options.get('production_scheduled') is True else 30) <= 40)
                or abs(target_seconds * 30 - round(target_seconds * 30)) > 1e-6
                or not isinstance(scenes, list) or (len(scenes) != 30 if long else not 6 <= len(scenes) <= 12)
                or not isinstance(scene_visuals, list) or len(scene_visuals) != len(scenes)
                or not isinstance(final_reviews, dict) or set(final_reviews) != set(range(len(scenes)))
                or any(type(index) is not int for index in final_reviews)
                or not isinstance(voice_result, dict) or not isinstance(scene_durations, list)
                or len(scene_durations) != len(scenes) or not isinstance(narration, str)):
            return {}
        work = _work_path(task_id, work_dir)
        voice = _local_candidate_path(task_id, voice_result)
        # Recovered audio belongs to this precise attempt, not another sibling.
        if voice.name == 'recovered_voice.mp3' and voice.parent != work:
            return {}
        with voice.open('rb') as incoming:
            header = incoming.read(3)
        if not (header.startswith(b'ID3') or (len(header) >= 2 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0)):
            return {}
        measured_voice = _number(media_duration(voice), target_seconds - (.13 if long else 1.30), target_seconds + .08)
        durations = [_number(value, 0.1, 15) for value in scene_durations]
        if abs(sum(durations) - measured_voice) > 0.12:
            return {}
        if voice_result.get('scene_durations') != scene_durations:
            return {}
        clean_scenes, selected, details = [], [], []
        total_source_bytes = 0
        for index, scene in enumerate(scenes):
            if not isinstance(scene, dict) or not isinstance(final_reviews[index], dict):
                return {}
            text = scene.get('narration')
            if not isinstance(text, str) or not text.strip() or len(text) > 4000:
                return {}
            spec, provenance = _selected(scene_visuals[index], final_reviews[index], work)
            total_source_bytes += provenance['size']
            if total_source_bytes > MAX_SOURCE_BYTES:
                return {}
            transition = scene.get('transition')
            clean_scenes.append({'narration': text, 'transition': transition if transition in {'cut', 'dip'} else 'cut'})
            selected.append([spec])
            details.append({'scene_index': index, 'narration': _text(text, 4000),
                            'duration_seconds': durations[index], 'selection': provenance,
                            'review': _review(final_reviews[index])})
        if narration != ' '.join(scene['narration'] for scene in scenes):
            return {}
        voice_sha, voice_size = _digest(voice, 14 * 1024 * 1024)
        resolution = '1920x1080' if long else '1080x1920'
        maximum_video = MAX_LONG_VIDEO_BYTES if long else MAX_VIDEO_BYTES
        with tempfile.TemporaryDirectory(prefix='qa_workprint_', dir=work) as temporary:
            output = Path(temporary) / 'qa-workprint.mp4'
            render_video(
                voice_path=voice, visual_paths=[spec[0] for spec in selected],
                narration=narration, output_path=output, scenes=clean_scenes,
                scene_durations=durations, scene_visual_paths=selected,
                target_duration=target_seconds, output_resolution=resolution,
                progress_callback=progress_callback,
            )
            output = _file(output, work, maximum_video)
            if long:
                _probe(output, target_seconds, landscape=True)
            else:
                _probe(output) if target_seconds == 30 else _probe(output, target_seconds)
            checksum, size = _digest(output, maximum_video)
            prefix = f'qa_workprints/{task_id}/'
            key = f'{prefix}{checksum}.mp4'
            flags = {'version': 4 if long else 1 if target_seconds == 30 else 2, 'status': 'qa_workprint', 'qa_approved': False,
                     'publish_eligible': False, 'reusable': False, 'task_id': task_id}
            metrics = {'duration_seconds': target_seconds, 'frame_count': round(target_seconds * 30),
                       'width': 1920 if long else 1080, 'height': 1080 if long else 1920}
            metadata = {**flags, **metrics, 'failure_stage': 'final_visual_qc',
                        'voice': {'sha256': voice_sha, 'size': voice_size,
                                  'existing_voice_quality_passed': True},
                        'scenes': details, 'video_sha256': checksum, 'video_size': size,
                        'note': 'Unapproved diagnostic edit; all publication and recovery gates remain required.'}
            payload = json.dumps(metadata, ensure_ascii=False, sort_keys=True, allow_nan=False,
                                 separators=(',', ':')).encode('utf-8')
            if not 1 <= len(payload) <= MAX_METADATA_BYTES:
                return {}
            metadata_sha = hashlib.sha256(payload).hexdigest()
            metadata_key = f'{prefix}{metadata_sha}.json'
            _notify_render_progress(progress_callback, 'upload', len(selected), len(selected))
            client = storage._client()
            _put_immutable(client, metadata_key, io.BytesIO(payload), len(payload), metadata_sha, 'application/json')
            with output.open('rb') as incoming:
                etag = _put_immutable(client, key, incoming, size, checksum, 'video/mp4')
            return {'qa_workprint': {**flags, **metrics, 'key': key, 'sha256': checksum,
                                    'size': size, 'etag': etag, 'metadata_key': metadata_key,
                                    'metadata_sha256': metadata_sha, 'metadata_size': len(payload)}}
    except Exception:
        # Diagnostic failure must not hide the original visual-QA failure or
        # expose provider errors, paths, credentials or Storage response data.
        return {}
