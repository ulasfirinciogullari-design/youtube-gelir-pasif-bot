"""A local thumbnail from an already-approved final; no generated imagery."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import tempfile
from uuid import UUID


_TEMP_ROOT = Path(tempfile.gettempdir())
MAX_VIDEO_BYTES = 1024 * 1024 * 1024
MAX_THUMBNAIL_BYTES = 2_000_000
_GEOMETRY = {'shorts': (1080, 1920, 720, 1280), 'landscape': (1920, 1080, 1280, 720)}


class PublicationThumbnailError(RuntimeError):
    """No raw file path, encoder output or credential belongs in diagnostics."""


def _require(condition):
    if not condition:
        raise ValueError('Invalid thumbnail input')


def _checksum(path):
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            checksum.update(chunk)
    return checksum.hexdigest()


def _probe(path):
    raw = subprocess.check_output([
        'ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe',
        '-show_entries', 'stream=codec_type,codec_name,width,height,avg_frame_rate:format=duration',
        '-of', 'json', str(path),
    ], text=True, stderr=subprocess.DEVNULL, timeout=30)
    _require(isinstance(raw, str) and len(raw) <= 65536)
    value = json.loads(raw)
    _require(isinstance(value, dict) and isinstance(value.get('streams'), list)
             and all(isinstance(stream, dict) for stream in value['streams']))
    return value


def prepare_approved_final_thumbnail(task_id, video_path, output_path, *, video_format, qa_approved):
    """Extract one real frame; the caller alone decides upload/publication QA.

    The frame is not added to the video, text is not overlaid, and neither the
    source master nor any paid media is modified. Output stays task-scoped.
    """
    try:
        _require(qa_approved is True and isinstance(task_id, str) and str(UUID(task_id)) == task_id
                 and video_format in _GEOMETRY)
        video, output = Path(video_path), Path(output_path)
        root = _TEMP_ROOT.resolve(strict=True)
        _require(video.is_absolute() and output.is_absolute() and '..' not in video.parts
                 and '..' not in output.parts and video.name == 'final.mp4' and output.name == 'thumbnail.jpg'
                 and video.parent == output.parent and not video.is_symlink() and not output.exists()
                 and not output.is_symlink())
        work = video.parent.resolve(strict=True)
        relative = work.relative_to(root).as_posix()
        _require(re.fullmatch(rf'youtube_factory/{re.escape(task_id)}_attempt_[0-9]{{1,2}}', relative)
                 and video.resolve(strict=True) == work / 'final.mp4'
                 and video.is_file() and 1 <= video.stat().st_size <= MAX_VIDEO_BYTES)
        with video.open('rb') as stream:
            _require(stream.read(12)[4:8] == b'ftyp')
        geometry = _GEOMETRY[video_format]
        source = _probe(video)
        streams = source['streams']
        videos = [stream for stream in streams if stream.get('codec_type') == 'video']
        _require(len(streams) == 2 and len(videos) == 1
                 and len([stream for stream in streams if stream.get('codec_type') == 'audio']) == 1)
        visual = videos[0]
        duration = float(source.get('format', {}).get('duration', 'nan'))
        _require(visual.get('codec_name') == 'h264' and visual.get('width') == geometry[0]
                 and visual.get('height') == geometry[1] and visual.get('avg_frame_rate') == '30/1'
                 and math.isfinite(duration) and 1.1 <= duration <= 3600)
        source_sha256 = _checksum(video)
        subprocess.run([
            'ffmpeg', '-n', '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,pipe',
            '-ss', '1.0', '-i', str(video), '-map', '0:v:0', '-frames:v', '1',
            '-vf', f'scale={geometry[2]}:{geometry[3]}:flags=lanczos,setsar=1',
            '-an', '-sn', '-dn', '-c:v', 'mjpeg', '-threads', '1', '-q:v', '2',
            '-f', 'image2', str(output),
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45)
        _require(output.is_file() and not output.is_symlink()
                 and output.resolve(strict=True) == work / 'thumbnail.jpg'
                 and 1 <= output.stat().st_size <= MAX_THUMBNAIL_BYTES
                 and _checksum(video) == source_sha256)
        data = output.read_bytes()
        _require(data.startswith(b'\xff\xd8\xff') and data.endswith(b'\xff\xd9'))
        image = _probe(output)['streams']
        _require(len(image) == 1 and image[0].get('codec_type') == 'video'
                 and image[0].get('codec_name') == 'mjpeg'
                 and image[0].get('width') == geometry[2] and image[0].get('height') == geometry[3])
        return {'status': 'prepared', 'origin': 'approved_final_frame', 'frame_seconds': 1.0,
                'source_sha256': source_sha256, 'sha256': hashlib.sha256(data).hexdigest(),
                'size': len(data), 'width': geometry[2], 'height': geometry[3], 'mime_type': 'image/jpeg'}
    except Exception:
        raise PublicationThumbnailError('thumbnail_preparation_unavailable') from None
