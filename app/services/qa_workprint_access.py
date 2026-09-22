"""Authenticated-route helpers for an explicitly unapproved private workprint.

These helpers neither authenticate users nor read jobs. The caller must do both
before using them. Storage keys and credentials never become browser URLs.
"""
from __future__ import annotations

import re
import math
from uuid import UUID

from fastapi import Request
from starlette.background import BackgroundTask
from starlette.responses import Response, StreamingResponse

from app.services import storage


MAX_WORKPRINT_BYTES = 64 * 1024 * 1024
MAX_LONG_WORKPRINT_BYTES = 192 * 1024 * 1024
CHUNK_BYTES = 64 * 1024
_SHA256 = re.compile(r'^[0-9a-f]{64}$')
_ETAG = re.compile(r'^"[A-Za-z0-9_-]{1,128}"$')
_FIELDS = {
    'version', 'status', 'qa_approved', 'publish_eligible', 'task_id',
    'key', 'sha256', 'size', 'etag', 'reusable', 'duration_seconds',
    'frame_count', 'width', 'height', 'metadata_key', 'metadata_sha256', 'metadata_size',
}
_PRIVATE_HEADERS = {
    'Cache-Control': 'private, no-store',
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'no-referrer',
}


class _PrivateStreamingResponse(StreamingResponse):
    """Close the pinned body even if transport fails before iteration starts."""
    def __init__(self, *args, close_body, **kwargs):
        super().__init__(*args, **kwargs)
        self._close_body = close_body

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._close_body()


def _canonical_id(value: object) -> bool:
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def _valid_timing(pointer: dict) -> bool:
    duration, frames = pointer.get('duration_seconds'), pointer.get('frame_count')
    if type(duration) not in (int, float) or not math.isfinite(duration) or type(frames) is not int:
        return False
    if pointer['version'] == 1:
        return duration == 30 and frames == 900
    if pointer['version'] == 3:
        # Actual failed master, including off-by-one frame and silence/motion
        # failures. This grants private playback only; all approval flags stay false.
        return 20 <= duration <= 45 and 600 <= frames <= 1350 and abs(duration * 30 - frames) < 1e-6
    if pointer['version'] == 4:
        return 150 <= duration <= 240 and 4500 <= frames <= 7200 and abs(duration * 30 - frames) < 1e-6
    return 30 < duration <= 40 and 900 < frames <= 1200 and abs(duration * 30 - frames) < 1e-6


def _valid_pointer(pointer: object) -> bool:
    return bool(
        isinstance(pointer, dict) and set(pointer) == _FIELDS
        and type(pointer.get('version')) is int and pointer['version'] in {1, 2, 3, 4}
        and pointer.get('status') == 'qa_workprint'
        and pointer.get('qa_approved') is False
        and pointer.get('publish_eligible') is False
        and pointer.get('reusable') is False
        and _canonical_id(pointer.get('task_id'))
        and isinstance(pointer.get('sha256'), str)
        and _SHA256.fullmatch(pointer['sha256'])
        and pointer.get('key') == f"qa_workprints/{pointer['task_id']}/{pointer['sha256']}.mp4"
        and type(pointer.get('size')) is int
        and 1 <= pointer['size'] <= (MAX_LONG_WORKPRINT_BYTES if pointer['version'] == 4 else MAX_WORKPRINT_BYTES)
        and isinstance(pointer.get('etag'), str)
        and _ETAG.fullmatch(pointer['etag'])
        and _valid_timing(pointer)
        and all(type(pointer.get(key)) is int and pointer[key] == expected
                for key, expected in ((('width', 1920), ('height', 1080)) if pointer['version'] == 4
                                     else (('width', 1080), ('height', 1920))))
        and isinstance(pointer.get('metadata_sha256'), str)
        and _SHA256.fullmatch(pointer['metadata_sha256'])
        and pointer.get('metadata_key') == f"qa_workprints/{pointer['task_id']}/{pointer['metadata_sha256']}.json"
        and type(pointer.get('metadata_size')) is int
        and 1 <= pointer['metadata_size'] <= 1024 * 1024
    )


def validated_pointer(job: object, task_id: str | None = None) -> dict | None:
    """Return a detached, strictly job-bound pointer or nothing, without I/O."""
    if (
        not isinstance(job, dict) or job.get('state') != 'FAILURE'
        or job.get('kind') != 'render' or not _canonical_id(job.get('task_id'))
        or (task_id is not None and task_id != job['task_id'])
    ):
        return None
    pointer = job.get('qa_workprint')
    if not _valid_pointer(pointer) or pointer['task_id'] != job['task_id']:
        return None
    if pointer['version'] == 4:
        spec = job.get('spec') or {}
        if (spec.get('mode') != 'production' or spec.get('format') != 'landscape'
                or spec.get('duration_minutes') != 3 or not _canonical_id(spec.get('content_plan_item_id'))):
            return None
    return dict(pointer)


def _error(status: int, *, size: int | None = None) -> Response:
    headers = dict(_PRIVATE_HEADERS)
    if status == 416 and size is not None:
        headers['Content-Range'] = f'bytes */{size}'
    if status == 405:
        headers['Allow'] = 'GET, HEAD'
    return Response('Private QA workprint unavailable.', status_code=status,
                    media_type='text/plain', headers=headers)


def _byte_range(value: str, size: int) -> tuple[int, int] | None:
    if len(value) > 100:
        return None
    match = re.fullmatch(r'bytes=([0-9]{0,20})-([0-9]{0,20})', value)
    if not match or not any(match.groups()):
        return None
    first, last = match.groups()
    if not first:
        suffix = int(last)
        return (max(0, size - suffix), size - 1) if suffix > 0 else None
    start = int(first)
    end = min(size - 1, int(last)) if last else size - 1
    return (start, end) if start < size and start <= end else None


def stream_response(pointer: dict, request: Request) -> Response:
    """Stream one pinned private object; require caller authentication first."""
    if not _valid_pointer(pointer):
        return _error(404)
    method = request.method.upper()
    if method not in {'GET', 'HEAD'}:
        return _error(405)
    size = pointer['size']
    ranges = request.headers.getlist('range') if method == 'GET' else []
    selected_range = None
    if ranges:
        if len(ranges) != 1:
            return _error(416, size=size)
        selected_range = _byte_range(ranges[0], size)
        if selected_range is None:
            return _error(416, size=size)
    start, end = selected_range if selected_range else (0, size - 1)
    expected_size = end - start + 1
    kwargs = {'Bucket': storage.settings.bucket, 'Key': pointer['key'],
              'IfMatch': pointer['etag']}
    if selected_range:
        kwargs['Range'] = f'bytes={start}-{end}'
    body = None
    closed = False

    def close_body():
        nonlocal closed
        if not closed:
            closed = True
            try:
                if body is not None:
                    body.close()
            except Exception:
                pass

    try:
        result = storage._client().get_object(**kwargs)
        body = result.get('Body')
        expected_status = 206 if selected_range else 200
        expected_range = f'bytes {start}-{end}/{size}' if selected_range else None
        if (
            not callable(getattr(body, 'read', None))
            or not callable(getattr(body, 'close', None))
            or result.get('ETag') != pointer['etag']
            or (result.get('Metadata') or {}).get('sha256') != pointer['sha256']
            or type(result.get('ContentLength')) is not int
            or result['ContentLength'] != expected_size
            or result.get('ContentRange') != expected_range
            or (result.get('ResponseMetadata') or {}).get('HTTPStatusCode') != expected_status
            or result.get('ContentType') != 'video/mp4'
            or result.get('ContentEncoding') not in (None, '')
        ):
            close_body()
            return _error(503)
    except Exception:
        close_body()
        return _error(503)
    headers = {**_PRIVATE_HEADERS, 'Accept-Ranges': 'bytes',
               'Content-Length': str(expected_size),
               'Content-Disposition': 'inline; filename="qa-workprint.mp4"'}
    if selected_range:
        headers['Content-Range'] = expected_range
    if method == 'HEAD':
        close_body()
        return Response(status_code=200, media_type='video/mp4', headers=headers)

    def chunks():
        remaining = expected_size
        try:
            while remaining:
                chunk = body.read(min(CHUNK_BYTES, remaining))
                if not isinstance(chunk, bytes) or not chunk or len(chunk) > min(CHUNK_BYTES, remaining):
                    raise ValueError('Invalid private media stream')
                remaining -= len(chunk)
                yield chunk
            if body.read(1) != b'':
                raise ValueError('Invalid private media stream')
        except Exception:
            # A failure after headers terminates the response; never append
            # an upstream exception, object key, URL or provider body to it.
            raise RuntimeError('Private QA workprint stream interrupted.') from None
        finally:
            close_body()

    return _PrivateStreamingResponse(chunks(), status_code=206 if selected_range else 200,
                                     media_type='video/mp4', headers=headers,
                                     background=BackgroundTask(close_body), close_body=close_body)
