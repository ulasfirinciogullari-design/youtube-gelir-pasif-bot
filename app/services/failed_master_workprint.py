"""Keep an existing failed master privately; never render, approve or publish."""
import hashlib
import io
import json
import math
from pathlib import Path
import subprocess
from uuid import UUID

from app.services import qa_workprint as workprints


def _metrics(path):
    raw = subprocess.check_output([
        'ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-count_frames',
        '-show_entries', 'stream=codec_type,codec_name,width,height,nb_read_frames,avg_frame_rate:format=duration',
        '-of', 'json', str(path),
    ], text=True, stderr=subprocess.DEVNULL, timeout=60)
    if len(raw) > 64 * 1024:
        raise ValueError('Invalid failed-master probe')
    data = json.loads(raw)
    videos = [row for row in data.get('streams', []) if row.get('codec_type') == 'video']
    audios = [row for row in data.get('streams', []) if row.get('codec_type') == 'audio']
    if (len(videos) != 1 or len(audios) != 1 or videos[0].get('codec_name') != 'h264'
            or audios[0].get('codec_name') != 'aac' or videos[0].get('width') != 1080
            or videos[0].get('height') != 1920 or videos[0].get('avg_frame_rate') != '30/1'):
        raise ValueError('Unplayable failed master')
    count = videos[0].get('nb_read_frames')
    if type(count) is not str or not count.isascii() or not count.isdigit():
        raise ValueError('Unknown failed-master frame count')
    frames = int(count)
    duration = float(data['format']['duration'])
    if not (600 <= frames <= 1350 and math.isfinite(duration) and abs(duration - frames / 30) <= .034):
        raise ValueError('Unverified failed-master timeline')
    return {'duration_seconds': frames / 30, 'frame_count': frames, 'width': 1080, 'height': 1920}


def persist(task_id, work_dir):
    """Store exact playable bytes even when their editorial gates rejected them."""
    try:
        if str(UUID(task_id)) != task_id:
            return {}
        work = workprints._work_path(task_id, work_dir)
        output = workprints._file(work / 'final.mp4', work, workprints.MAX_VIDEO_BYTES)
        metrics = _metrics(output)
        checksum, size = workprints._digest(output, workprints.MAX_VIDEO_BYTES)
        flags = {'version': 3, 'status': 'qa_workprint', 'task_id': task_id,
                 'qa_approved': False, 'publish_eligible': False, 'reusable': False}
        metadata = {**flags, **metrics, 'failure_stage': 'render',
                    'video_sha256': checksum, 'video_size': size,
                    'source': 'existing_failed_master',
                    'note': 'Private unapproved preview. Playback validity is not quality approval.'}
        payload = json.dumps(metadata, sort_keys=True, allow_nan=False, separators=(',', ':')).encode()
        metadata_sha = hashlib.sha256(payload).hexdigest()
        prefix = f'qa_workprints/{task_id}/'
        key, metadata_key = prefix + checksum + '.mp4', prefix + metadata_sha + '.json'
        client = workprints.storage._client()
        workprints._put_immutable(client, metadata_key, io.BytesIO(payload), len(payload), metadata_sha, 'application/json')
        with output.open('rb') as incoming:
            etag = workprints._put_immutable(client, key, incoming, size, checksum, 'video/mp4')
        return {'qa_workprint': {**flags, **metrics, 'key': key, 'sha256': checksum, 'size': size,
            'etag': etag, 'metadata_key': metadata_key, 'metadata_sha256': metadata_sha,
            'metadata_size': len(payload)}}
    except Exception:
        return {}  # The original render failure always wins over diagnostics.


def checkpoint(task_id, work_dir, *, options, duration_minutes):
    """Attach only a diagnostic pointer while the real task remains in render."""
    try:
        if (type(options) is not dict or options.get('mode') != 'production'
                or options.get('format') != 'shorts' or type(duration_minutes) not in (int, float)
                or duration_minutes != .5 or not (Path(work_dir) / 'final.mp4').is_file()):
            return
        from app.services import studio_state
        from app.services.qa_workprint_access import validated_pointer
        job = studio_state.get_job(task_id)
        if (type(job) is not dict or job.get('task_id') != task_id or job.get('kind') != 'render'
                or job.get('stage') != 'render' or job.get('state') not in {'STARTED', 'PROGRESS'}
                or job.get('qa_workprint') or (job.get('result') or {}).get('video_key')):
            return
        pointer = persist(task_id, work_dir).get('qa_workprint')
        if validated_pointer({**job, 'state': 'FAILURE', 'qa_workprint': pointer}, task_id) is None:
            return
        studio_state.update_job(task_id, qa_workprint=pointer)
    except Exception:
        return
