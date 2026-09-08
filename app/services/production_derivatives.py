"""Render three private portrait candidates from one hash-bound final master.

No model, TTS, stock search or video generation is invoked. A completed render
does not inherit its parent's landscape quality approval or publish authority.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
import subprocess

from app.services.production_delivery import DeliveryPlanError, digest, file_sha256


def _require(value, message='delivery_manifest_invalid'):
    if not value:
        raise DeliveryPlanError(message)


def validate_manifest(manifest, *, source_task_id, expected_digest, expected_master_sha256):
    _require(type(manifest) is dict and set(manifest) == {
        'version', 'source_task_id', 'master_key', 'master_sha256', 'source_frame_count',
        'fps', 'scene_narration_sha256', 'shorts', 'new_voice_generations',
        'new_video_generations', 'qa_approved', 'publish_eligible', 'manifest_sha256',
    })
    _require(type(source_task_id) is str and re.fullmatch(
        r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', source_task_id))
    for value in (expected_digest, expected_master_sha256, manifest['scene_narration_sha256']):
        _require(type(value) is str and re.fullmatch(r'[0-9a-f]{64}', value))
    _require(manifest['source_task_id'] == source_task_id
             and manifest['master_key'] == f'videos/{source_task_id}/final.mp4'
             and manifest['master_sha256'] == expected_master_sha256
             and manifest['manifest_sha256'] == expected_digest
             and digest({k: v for k, v in manifest.items() if k != 'manifest_sha256'}) == expected_digest,
             'delivery_source_binding_changed')
    _require(type(manifest['version']) is int and manifest['version'] == 1
             and type(manifest['fps']) is int and manifest['fps'] == 30
             and type(manifest['source_frame_count']) is int
             and 300 * 30 <= manifest['source_frame_count'] <= 600 * 30
             and manifest['qa_approved'] is False and manifest['publish_eligible'] is False)
    _require(all(type(manifest[k]) is int and manifest[k] == 0
                 for k in ('new_voice_generations', 'new_video_generations')))
    _require(type(manifest['shorts']) is list and len(manifest['shorts']) == 3)
    ranges, indices, titles = [], set(), set()
    for number, cut in enumerate(manifest['shorts'], 1):
        _require(type(cut) is dict and set(cut) == {
            'number', 'title', 'description', 'scene_indices', 'narration',
            'narration_sha256', 'start_frame', 'end_frame', 'duration_seconds', 'status',
        })
        _require(type(cut['number']) is int and cut['number'] == number)
        for field, maximum in (('title', 100), ('description', 3000), ('narration', 6000)):
            _require(type(cut[field]) is str and 0 < len(cut[field].strip()) <= maximum)
        _require(cut['title'].casefold() not in titles)
        titles.add(cut['title'].casefold())
        spans = cut['scene_indices']
        _require(type(spans) is list and 2 <= len(spans) <= 8
                 and all(type(i) is int and 0 <= i < 70 for i in spans)
                 and spans == list(range(spans[0], spans[-1] + 1)) and not indices.intersection(spans))
        indices.update(spans)
        _require(type(cut['narration_sha256']) is str
                 and re.fullmatch(r'[0-9a-f]{64}', cut['narration_sha256']))
        start, end = cut['start_frame'], cut['end_frame']
        _require(type(start) is int and type(end) is int
                 and 0 <= start < end <= manifest['source_frame_count']
                 and 20 * 30 <= end - start <= 50 * 30)
        _require(type(cut['duration_seconds']) in (int, float)
                 and math.isfinite(cut['duration_seconds'])
                 and cut['duration_seconds'] == (end - start) / 30
                 and cut['status'] == 'awaiting_portrait_render_and_review')
        _require(all(end <= old_start or start >= old_end for old_start, old_end in ranges))
        ranges.append((start, end))
    return manifest


def probe_video(path):
    completed = subprocess.run([
        'ffprobe', '-v', 'error', '-show_entries',
        'stream=codec_type,width,height,r_frame_rate,nb_frames:format=duration',
        '-of', 'json', str(path),
    ], check=True, capture_output=True, text=True, encoding='utf-8', timeout=45)
    data = json.loads(completed.stdout)
    videos = [s for s in data.get('streams', []) if s.get('codec_type') == 'video']
    audios = [s for s in data.get('streams', []) if s.get('codec_type') == 'audio']
    _require(len(videos) == 1 and len(audios) == 1, 'delivery_media_streams_invalid')
    video = videos[0]
    return {
        'width': video['width'], 'height': video['height'], 'fps': video['r_frame_rate'],
        'frames': int(video['nb_frames']), 'duration': float(data['format']['duration']),
    }


def _render_one(master, cut, output):
    start, end = cut['start_frame'], cut['end_frame']
    duration = (end - start) / 30
    filters = (
        f'[0:v]trim=start_frame={start}:end_frame={end},setpts=PTS-STARTPTS,'
        'crop=trunc(ih*9/16/2)*2:ih:(iw-ow)/2:0,scale=1080:1920,setsar=1[v];'
        f'[0:a]atrim=start={start / 30:.9f}:end={end / 30:.9f},asetpts=PTS-STARTPTS[a]'
    )
    subprocess.run([
        'ffmpeg', '-hide_banner', '-nostdin', '-v', 'error', '-n',
        '-i', str(master), '-filter_complex_threads', '1', '-filter_complex', filters,
        '-map', '[v]', '-map', '[a]', '-map_metadata', '-1',
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-threads', '2',
        '-pix_fmt', 'yuv420p', '-r', '30', '-fps_mode', 'cfr',
        '-c:a', 'aac', '-b:a', '192k', '-t', f'{duration:.9f}',
        '-movflags', '+faststart', str(output),
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=600)


def iter_candidates(master, manifest, work, *, source_task_id, expected_digest,
                    expected_master_sha256, only_numbers=None):
    """Yield verified cuts individually so completed exports survive later errors."""
    validate_manifest(manifest, source_task_id=source_task_id,
                      expected_digest=expected_digest, expected_master_sha256=expected_master_sha256)
    if only_numbers is None:
        only_numbers = [1, 2, 3]
    _require(type(only_numbers) is list
             and all(type(number) is int and 1 <= number <= 3 for number in only_numbers)
             and only_numbers == sorted(set(only_numbers)), 'delivery_cut_selection_invalid')
    _require(file_sha256(master) == expected_master_sha256, 'delivery_master_changed')
    source = probe_video(master)
    _require(source['width'] == 1920 and source['height'] == 1080 and source['fps'] == '30/1'
             and source['frames'] == manifest['source_frame_count'], 'delivery_master_media_changed')
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    for cut in manifest['shorts']:
        if cut['number'] not in only_numbers:
            continue
        output = work / f'short_{cut["number"]}.mp4'
        _render_one(master, cut, output)
        media = probe_video(output)
        _require(media['width'] == 1080 and media['height'] == 1920 and media['fps'] == '30/1'
                 and media['frames'] == cut['end_frame'] - cut['start_frame']
                 and math.isfinite(media['duration'])
                 and abs(media['duration'] - cut['duration_seconds']) <= 0.12,
                 'delivery_portrait_media_invalid')
        yield {
            'number': cut['number'], 'path': str(output), 'sha256': file_sha256(output),
            'title': cut['title'], 'description': cut['description'], 'narration': cut['narration'],
            'duration': media['duration'], 'frame_count': media['frames'], 'resolution': '1080x1920',
            'source_task_id': source_task_id, 'source_master_sha256': expected_master_sha256,
            'delivery_manifest_sha256': expected_digest,
            'source_start_frame': cut['start_frame'], 'source_end_frame': cut['end_frame'],
            'new_voice_generations': 0, 'new_video_generations': 0,
            'quality_disposition': 'derived_portrait_review_required', 'manual_qa_required': True,
            'publish_eligible': False,
        }


def render_candidates(master, manifest, work, **kwargs):
    return list(iter_candidates(master, manifest, work, **kwargs))
