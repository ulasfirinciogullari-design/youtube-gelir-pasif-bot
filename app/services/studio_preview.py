"""Owner playback of existing media, independent of publication approval.

No generation, retry, job update, budget change or publication is performed.
Only canonical job-bound pointers are used; object keys stay out of UI JSON.
"""
import re
from uuid import UUID

from fastapi.responses import JSONResponse, RedirectResponse

from app.services import qa_workprint_access, storage

MAX_VIDEO_BYTES = 512 * 1024 * 1024
MAX_AUDIO_BYTES = 32 * 1024 * 1024
HEADERS = {'Cache-Control': 'private, no-store', 'Referrer-Policy': 'no-referrer',
           'X-Content-Type-Options': 'nosniff'}


def _id(value):
    try:
        return type(value) is str and str(UUID(value)) == value
    except (ValueError, TypeError):
        return False


def _source(job):
    if type(job) is not dict or not _id(job.get('task_id')) or job.get('kind') != 'render':
        return None
    workprint = qa_workprint_access.validated_pointer(job)
    if workprint:
        return {'kind': 'video', 'variant': 'workprint', 'pointer': workprint,
                'label': 'İnceleme videosu', 'note': 'Kalite kontrolünü geçmedi. Yayımlanmaz.'}
    result = job.get('result') if type(job.get('result')) is dict else {}
    key = result.get('video_key')
    if (type(key) is str and len(key) <= 512 and '..' not in key.split('/')
            and re.fullmatch(r'(?:videos/[A-Za-z0-9_/-]+\.mp4|external-masters/v1/[0-9a-f]{64}/master\.mp4)', key)):
        return {'kind': 'video', 'variant': 'render', 'key': key, 'label': 'Üretilen video',
                'note': 'Önizlemek yayın onayı vermez.', 'size': result.get('expected_video_size')}
    candidate = job.get('audio_candidate_checkpoint')
    if type(candidate) is not dict:
        return None
    digest = candidate.get('audio_sha256')
    if (candidate.get('status') == 'unapproved_candidate' and candidate.get('qa_approved') is False
            and candidate.get('requires_full_qa') is True and type(digest) is str
            and re.fullmatch(r'[0-9a-f]{64}', digest)
            and candidate.get('audio_key') == f"audio_candidates/{job['task_id']}/{digest}/candidate.mp3"
            and type(candidate.get('size')) is int and 0 < candidate['size'] <= MAX_AUDIO_BYTES):
        return {'kind': 'audio', 'variant': 'audio', 'key': candidate['audio_key'],
                'size': candidate['size'], 'label': 'Ses taslağı',
                'note': 'Video henüz tamamlanmadı. Kaydedilen sesi dinleyebilirsin; yayın onayı yok.'}
    return None


def describe(job):
    source = _source(job)
    if not source:
        return None
    return {key: source[key] for key in ('kind', 'variant', 'label', 'note')} | {
        'url': f"/studio/job/{job['task_id']}/preview-media"}


def media_response(job, request):
    """Caller must authenticate the owner before loading this job or storage."""
    source = _source(job)
    if not source:
        return JSONResponse({'detail': 'Bu denemede izlenebilir bir dosya henüz oluşmadı.'},
                            status_code=404, headers=HEADERS)
    if source['variant'] == 'workprint':
        return qa_workprint_access.stream_response(source['pointer'], request)
    try:
        client = storage._client(single_attempt=True)
        head = client.head_object(Bucket=storage.settings.bucket, Key=source['key'])
        size = head.get('ContentLength')
        limit = MAX_AUDIO_BYTES if source['kind'] == 'audio' else MAX_VIDEO_BYTES
        mime = 'audio/mpeg' if source['kind'] == 'audio' else 'video/mp4'
        if (type(size) is not int or not 0 < size <= limit
                or head.get('ContentType') != mime or head.get('ContentEncoding') not in (None, '')
                or source.get('size') is not None and (type(source['size']) is not int or source['size'] != size)):
            raise ValueError('unverified_media')
        # Generate a fresh short-lived owner playback URL each time. Old job
        # URLs may have expired; never overwrite the original delivery record.
        url = client.generate_presigned_url('get_object', Params={
            'Bucket': storage.settings.bucket, 'Key': source['key'],
            'ResponseContentDisposition': 'inline; filename="preview.' + ('mp3' if source['kind'] == 'audio' else 'mp4') + '"'},
            ExpiresIn=300)
        return RedirectResponse(url, status_code=307, headers=HEADERS)
    except Exception:
        return JSONResponse({'detail': 'Önizleme dosyası şu anda açılamıyor. Biraz sonra tekrar dene.'},
                            status_code=503, headers=HEADERS)
