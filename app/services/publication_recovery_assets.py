"""Read existing approved publication assets; derive only a real-frame JPEG.

Returned hashes identify the bytes observed now, not a new QA attestation.
No Storage writes, model calls, YouTube requests, or job/ledger changes occur.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import tempfile
from uuid import UUID

from app.services import storage


MAX_VIDEO_BYTES = 64 * 1024 * 1024
MAX_CAPTION_BYTES = 256 * 1024
MAX_METADATA_BYTES = 1024 * 1024
MAX_THUMBNAIL_BYTES = 2 * 1024 * 1024
_TEMP_ROOT = Path(tempfile.gettempdir()).absolute()
_SHA = re.compile(r'^[0-9a-f]{64}$')
_ETAG = re.compile(r'^"[A-Za-z0-9_-]{1,128}"$')


class PublicationRecoveryAssetsError(RuntimeError):
    """A fixed safe failure, never an endpoint, path or provider message."""


def _require(condition):
    if not condition:
        raise ValueError('Invalid publication asset')


def _uuid(value):
    _require(isinstance(value, str) and str(UUID(value)) == value)
    return value


def _work(value, source_id):
    _require(isinstance(value, (str, Path)))
    path = Path(value)
    root = _TEMP_ROOT.resolve(strict=True)
    _require(path.is_absolute() and '..' not in path.parts)
    relative = path.relative_to(_TEMP_ROOT)
    _require(len(relative.parts) == 3 and relative.parts[:2] == ('youtube_asset_recovery', source_id))
    _uuid(relative.parts[2])
    _require(path.resolve(strict=False) == root / relative)
    path.mkdir(parents=True, exist_ok=True)
    _require(path.resolve(strict=True) == root / relative and path.is_dir() and not any(path.iterdir()))
    # Exclusive local claim also prevents two preparations sharing this folder.
    with (path / '.preparation').open('xb'):
        pass
    return path


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result)
            result[key] = value
        return result
    def invalid(_value):
        raise ValueError('Invalid JSON number')
    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)


def _download(client, key, path, maximum, content_types, expected_sha=None):
    head = client.head_object(Bucket=storage.settings.bucket, Key=key)
    size, etag = head.get('ContentLength'), head.get('ETag')
    _require(type(size) is int and 1 <= size <= maximum
             and isinstance(etag, str) and _ETAG.fullmatch(etag)
             and head.get('ContentType') in content_types and not head.get('ContentEncoding')
             and (head.get('ResponseMetadata') or {}).get('HTTPStatusCode') == 200)
    if expected_sha is not None:
        _require(isinstance(expected_sha, str) and _SHA.fullmatch(expected_sha))
    response = client.get_object(Bucket=storage.settings.bucket, Key=key, IfMatch=etag)
    body = response.get('Body')
    try:
        _require(type(response.get('ContentLength')) is int and response['ContentLength'] == size
                 and response.get('ETag') == etag and response.get('ContentType') == head['ContentType']
                 and not response.get('ContentEncoding')
                 and (response.get('ResponseMetadata') or {}).get('HTTPStatusCode') == 200
                 and callable(getattr(body, 'read', None)) and callable(getattr(body, 'close', None)))
        digest, remaining = hashlib.sha256(), size
        with path.open('xb') as target:
            while remaining:
                limit = min(64 * 1024, remaining)
                chunk = body.read(limit)
                _require(isinstance(chunk, bytes) and 0 < len(chunk) <= limit)
                target.write(chunk)
                digest.update(chunk)
                remaining -= len(chunk)
            _require(body.read(1) == b'')
        checksum = digest.hexdigest()
        for given in (expected_sha, (head.get('Metadata') or {}).get('sha256'),
                      (response.get('Metadata') or {}).get('sha256')):
            _require(given is None or isinstance(given, str) and _SHA.fullmatch(given) and given == checksum)
        return {'path': str(path), 'key': key, 'size': size, 'sha256': checksum}
    finally:
        if body is not None:
            try:
                body.close()
            except Exception:
                pass


def _metadata(path, source, source_id):
    result, spec = source['result'], source['spec']
    data = _json(path.read_text(encoding='utf-8'))
    count = result.get('scenes')
    _require(type(count) is int and 1 <= count <= 12 and isinstance(data, dict)
             and data.get('task_id') == source_id and data.get('title') == result['title']
             and data.get('topic') == spec.get('topic') and data.get('channel_id') == spec.get('channel_id')
             and type(data.get('requested_duration_minutes')) in (int, float)
             and data['requested_duration_minutes'] == .5
             and data.get('quality_disposition') == 'automated_qc_pass' and data.get('manual_qa_required') is False)
    options = data.get('studio_options')
    _require(isinstance(options, dict) and options.get('mode') == 'production' and options.get('format') == 'shorts')
    for field in ('audio_qc', 'audio_duration_qc', 'audio_prosody_qc'):
        _require(isinstance(data.get(field), dict) and data[field].get('pass') is True)
    scenes = data.get('scenes')
    _require(isinstance(scenes, list) and len(scenes) == count)
    texts = []
    for index, scene in enumerate(scenes):
        _require(isinstance(scene, dict) and type(scene.get('index')) is int and scene['index'] == index
                 and isinstance(scene.get('narration'), str) and 0 < len(scene['narration']) <= 4000)
        text = scene['narration']
        _require(text.strip() and not any(ord(c) < 32 and c not in '\t\r\n' for c in text))
        texts.append(text)
    _require(sum(map(len, texts)) <= 16000)
    return data, ' '.join(' '.join(texts).split())


def _probe(path, *, image=False, expected_duration=30.0):
    entries = 'stream=codec_type,codec_name,width,height' if image else 'stream=codec_type,codec_name,width,height,nb_read_frames,avg_frame_rate,duration'
    command = ['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe']
    if not image:
        command += ['-count_frames']
    command += ['-show_entries', entries, '-of', 'json', str(path)]
    raw = subprocess.check_output(command, text=True, stderr=subprocess.DEVNULL, timeout=60)
    _require(isinstance(raw, str) and len(raw) <= 64 * 1024)
    data = _json(raw)
    streams = data.get('streams') if isinstance(data, dict) else None
    _require(isinstance(streams, list) and all(isinstance(s, dict) for s in streams))
    videos = [s for s in streams if s.get('codec_type') == 'video']
    _require(len(videos) == 1)
    video = videos[0]
    if image:
        _require(len(streams) == 1 and video.get('codec_name') == 'mjpeg'
                 and type(video.get('width')) is int and 640 <= video['width'] <= 4096
                 and type(video.get('height')) is int and 360 <= video['height'] <= 4096)
        return video
    _require(type(expected_duration) in (int, float) and math.isfinite(expected_duration)
             and 30 <= expected_duration <= 35)
    duration = float(video.get('duration', 'nan'))
    _require(len(streams) == 2 and len([s for s in streams if s.get('codec_type') == 'audio']) == 1
             and video.get('codec_name') == 'h264' and video.get('width') == 1080 and video.get('height') == 1920
             and video.get('avg_frame_rate') == '30/1'
             and str(video.get('nb_read_frames')) == str(round(expected_duration * 30))
             and math.isfinite(duration) and abs(duration - expected_duration) <= .034)
    return duration


def _caption(path, duration, narration):
    text = path.read_text(encoding='utf-8-sig')
    _require(not any(ord(c) < 32 and c not in '\t\r\n' for c in text))
    blocks = re.split(r'\n\s*\n', text.replace('\r\n', '\n').strip())
    _require(1 <= len(blocks) <= 400)
    previous, parts = 0, []
    timestamp = r'(\d{2}):([0-5]\d):([0-5]\d),(\d{3})'
    for index, block in enumerate(blocks, 1):
        lines = block.splitlines()
        _require(3 <= len(lines) <= 8 and lines[0] == str(index))
        match = re.fullmatch(timestamp + ' --> ' + timestamp, lines[1])
        _require(match is not None)
        values = [int(n) for n in match.groups()]
        start, end = [(h * 3600 + m * 60 + s) * 1000 + ms for h, m, s, ms in (values[:4], values[4:])]
        _require(previous <= start < end <= round(duration * 1000))
        previous = end
        words = ' '.join(' '.join(lines[2:]).split())
        _require(words and len(words) <= 4000)
        parts.append(words)
    _require(' '.join(parts) == narration)


def _thumbnail(final, output):
    subprocess.run([
        'ffmpeg', '-n', '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,pipe',
        '-ss', '1.0', '-i', final['path'], '-map', '0:v:0', '-frames:v', '1',
        '-vf', 'scale=720:1280:flags=lanczos,setsar=1',
        '-an', '-sn', '-dn', '-c:v', 'mjpeg', '-threads', '1', '-q:v', '2', '-f', 'image2', str(output),
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45)
    return {'origin': 'approved_final_frame', 'frame_seconds': 1.0, 'source_sha256': final['sha256']}


def prepare_publication_recovery_assets(source_job, *, source_task_id, work_dir):
    """Prepare bounded local assets, never permission to upload or release."""
    try:
        source_id = _uuid(source_task_id)
        from app.services.youtube_automation import automated_quality_approved
        _require(isinstance(source_job, dict) and source_job.get('task_id') == source_id
                 and automated_quality_approved(source_job) and not source_job.get('retry_child_task_id'))
        spec, result = source_job.get('spec'), source_job.get('result')
        _require(isinstance(spec, dict) and isinstance(result, dict)
                 and result.get('task_id') == source_id and result.get('status') == 'complete'
                 and isinstance(result.get('title'), str) and 0 < len(result['title'].strip()) <= 500
                 and spec.get('mode') == 'production' and spec.get('format') == 'shorts'
                 and type(spec.get('duration_minutes')) in (int, float) and spec['duration_minutes'] == .5
                 and spec.get('language') in {'tr', 'en'})
        language, base = spec['language'], f'videos/{source_id}/'
        keys = {'final': base + 'final.mp4', 'caption': base + f'captions.{language}.srt', 'metadata': base + 'metadata.json'}
        _require(all(result.get(field) == keys[name] for name, field in (
            ('final', 'video_key'), ('caption', 'caption_key'), ('metadata', 'metadata_key'))))
        work, client = _work(work_dir, source_id), storage._client()
        metadata = _download(client, keys['metadata'], work / 'metadata.json', MAX_METADATA_BYTES,
                             {'application/json'}, result.get('metadata_sha256'))
        data, narration = _metadata(Path(metadata['path']), source_job, source_id)
        final = _download(client, keys['final'], work / 'final.mp4', MAX_VIDEO_BYTES,
                          {'video/mp4'}, result.get('video_sha256'))
        with Path(final['path']).open('rb') as source:
            _require(source.read(12)[4:8] == b'ftyp')
        # Modern edits include the already-reviewed speech tail. Require both
        # retained records and the real frame count to agree; legacy records
        # without a duration still require exactly the old 30-second master.
        expected_duration = result.get('duration', 30.0)
        if 'duration' in result:
            _require(isinstance(data.get('render'), dict)
                     and data['render'].get('duration') == expected_duration)
        duration = _probe(Path(final['path']), expected_duration=expected_duration)
        caption = _download(client, keys['caption'], work / f'captions.{language}.srt', MAX_CAPTION_BYTES,
                            {'application/x-subrip', 'application/octet-stream', 'text/plain'}, result.get('caption_sha256'))
        _caption(Path(caption['path']), duration, narration)
        caption['language'] = language
        publish_metadata = result.get('publish_metadata')
        candidates = [record.get('thumbnail_key') for record in (result, data, publish_metadata)
                      if isinstance(record, dict) and record.get('thumbnail_key') is not None]
        output = work / 'thumbnail.jpg'
        if candidates:
            _require(all(key == candidates[0] for key in candidates)
                     and candidates[0] in {base + 'thumbnail.jpg', base + 'thumbnail.jpeg'})
            thumbnail = _download(client, candidates[0], output, MAX_THUMBNAIL_BYTES,
                                  {'image/jpeg'}, result.get('thumbnail_sha256'))
            thumbnail['origin'] = 'authored'
        else:
            thumbnail = _thumbnail(final, output)
        _require(output.is_file() and not output.is_symlink() and 1 <= output.stat().st_size <= MAX_THUMBNAIL_BYTES)
        content = output.read_bytes()
        _require(content.startswith(b'\xff\xd8\xff') and content.endswith(b'\xff\xd9'))
        image = _probe(output, image=True)
        if thumbnail['origin'] == 'approved_final_frame':
            _require(image['width'] == 720 and image['height'] == 1280)
        thumbnail.update(path=str(output), size=len(content), sha256=hashlib.sha256(content).hexdigest())
        return {'caption': caption, 'thumbnail': thumbnail, 'final': final, 'metadata': metadata}
    except Exception:
        raise PublicationRecoveryAssetsError('publication_recovery_assets_unavailable') from None
