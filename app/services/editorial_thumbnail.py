"""One local cover from an editorially reviewed frame; no QA/publication grant."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
from uuid import UUID

from app.services.external_artifact_import import _fingerprint, _no_link, MAX_VIDEO_BYTES


_PUBLISH_ROOT = Path('/tmp/youtube_publish')
MAX_THUMBNAIL_BYTES = 2_000_000


class EditorialThumbnailError(RuntimeError):
    pass


def _require(value):
    if not value:
        raise EditorialThumbnailError('editorial_thumbnail_unavailable')


def _binding(receipt):
    expected = receipt['evidence']['manifest']['files']['video']
    frames = receipt['evidence']['frames']
    selected = [row for row in frames if type(row) is dict and type(row.get('frame_index')) is int
                and row['frame_index'] == 60] if type(frames) is list else []
    _require(len(selected) == 1 and type(selected[0].get('sha256')) is str
             and re.fullmatch(r'[0-9a-f]{64}', selected[0]['sha256'])
             and type(expected['sha256']) is str and re.fullmatch(r'[0-9a-f]{64}', expected['sha256'])
             and receipt['server_proof']['video_sha256'] == expected['sha256']
             and type(expected['size']) is int and 0 < expected['size'] <= MAX_VIDEO_BYTES
             and type(receipt['receipt_sha256']) is str and re.fullmatch(r'[0-9a-f]{64}', receipt['receipt_sha256']))
    return expected, selected[0]['sha256']


def _paths(video_path, output_path):
    video, output = Path(video_path), Path(output_path)
    _require(video.is_absolute() and output.is_absolute() and '..' not in video.parts
             and '..' not in output.parts and video.name == 'final.mp4'
             and output.name == 'editorial-thumbnail.jpg' and video.parent == output.parent)
    _no_link(_PUBLISH_ROOT)
    _no_link(video.parent)
    root, work = _PUBLISH_ROOT.resolve(strict=True), video.parent.resolve(strict=True)
    _require(work.parent == root and str(UUID(work.name)) == work.name
             and video.parent == work and video.resolve(strict=True) == work / 'final.mp4')
    return video, output


def validate_editorial_thumbnail(video_path, output_path, receipt, thumbnail):
    """Recheck the exact prepared JPEG, not a second extraction or download."""
    try:
        video, output = _paths(video_path, output_path)
        expected, frame_sha = _binding(receipt)
        _require(thumbnail['source_sha256'] == expected['sha256'] and thumbnail['frame_index'] == 60
                 and thumbnail['reviewed_frame_sha256'] == frame_sha
                 and thumbnail['editorial_review_sha256'] == receipt['receipt_sha256']
                 and thumbnail['origin'] == 'editorially_reviewed_master_frame'
                 and thumbnail['width'] == 720 and thumbnail['height'] == 1280
                 and thumbnail['mime_type'] == 'image/jpeg' and type(thumbnail['size']) is int
                 and 0 < thumbnail['size'] <= MAX_THUMBNAIL_BYTES)
        _fingerprint(output, thumbnail)
    except Exception:
        raise EditorialThumbnailError('editorial_thumbnail_changed_or_unavailable') from None


def prepare_editorial_thumbnail(video_path, output_path, receipt):
    """Caller must first validate the private receipt and local MP4/SRT.

    Fixed frame 60 (2s at the verified master's 30fps), no caller frame choice,
    overlay, image generation, Storage write or automated-QA assertion.
    """
    try:
        video, output = _paths(video_path, output_path)
        _require(not output.exists() and not output.is_symlink())
        expected, frame_sha = _binding(receipt)
        media = receipt['server_proof']['media_structure']
        _require(media['frame_rate'] == '30/1' and media['frame_count'] == 900
                 and type(media['width']) is int and type(media['height']) is int
                 and abs(media['width'] / media['height'] - 9 / 16) <= .002)
        _, before = _fingerprint(video, expected)
        subprocess.run([
            'ffmpeg', '-n', '-nostdin', '-v', 'error', '-max_alloc', '134217728', '-threads', '1',
            '-protocol_whitelist', 'file', '-enable_drefs', '0', '-use_absolute_path', '0',
            '-f', 'mov', '-i', str(video), '-map', '0:v:0', '-frames:v', '1',
            '-vf', 'select=eq(n\\,60),scale=720:1280:flags=lanczos,setsar=1',
            '-an', '-sn', '-dn', '-c:v', 'mjpeg', '-threads', '1', '-q:v', '2', '-f', 'image2', str(output),
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        details = _no_link(output)
        _require(0 < details.st_size <= MAX_THUMBNAIL_BYTES)
        with output.open('rb') as stream:
            data = stream.read(MAX_THUMBNAIL_BYTES + 1)
        _require(len(data) == details.st_size and data.startswith(b'\xff\xd8\xff') and data.endswith(b'\xff\xd9'))
        raw = subprocess.check_output([
            'ffprobe', '-v', 'error', '-f', 'image2', '-protocol_whitelist', 'file',
            '-show_entries', 'stream=codec_name,width,height', '-of', 'json', str(output),
        ], stderr=subprocess.DEVNULL, timeout=15, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        _require(len(raw) <= 65536 and json.loads(raw)['streams'] == [
            {'codec_name': 'mjpeg', 'width': 720, 'height': 1280}])
        _, after = _fingerprint(video, expected)
        _require(before == after)
        thumbnail = {'origin': 'editorially_reviewed_master_frame', 'frame_index': 60, 'frame_seconds': 2.0,
                     'source_sha256': expected['sha256'], 'reviewed_frame_sha256': frame_sha,
                     'editorial_review_sha256': receipt['receipt_sha256'],
                     'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data),
                     'width': 720, 'height': 1280, 'mime_type': 'image/jpeg'}
        validate_editorial_thumbnail(video, output, receipt, thumbnail)
        return thumbnail
    except Exception:
        raise EditorialThumbnailError('editorial_thumbnail_preparation_unavailable') from None
