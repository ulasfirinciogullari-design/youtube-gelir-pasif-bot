"""Production-safe Fal queue client for generated video scenes.

The paid submit request is deliberately sent exactly once.  Every subsequent
request is read-only and uses the request id returned by Fal's durable queue.
"""

from __future__ import annotations

import re
import time
from urllib.parse import urlparse

import httpx

from app.config import settings


FAL_SEEDANCE_FAST_MODEL = 'bytedance/seedance-2.0/fast/text-to-video'
_FAL_QUEUE_ORIGIN = 'https://queue.fal.run'
_FAL_QUEUE_HOST = 'queue.fal.run'
_FAL_POLL_INTERVAL_SECONDS = 5.0
_FAL_MAX_WAIT_SECONDS = 600.0
_FAL_MAX_STATUS_READ_FAILURES = 3
_FAL_REQUEST_ID_PATTERN = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE,
)
_FAL_TRANSIENT_ERROR_TYPES = frozenset({
    'request_timeout',
    'startup_timeout',
    'runner_scheduling_failure',
    'runner_connection_timeout',
    'runner_disconnected',
    'runner_connection_refused',
    'runner_connection_error',
    'runner_incomplete_response',
    'runner_server_error',
    'internal_error',
    'internal_server_error',
    'generation_timeout',
})


def _fal_error_is_policy(error_types: set[str]) -> bool:
    """Recognize policy codes before HTTP auth/status classification.

    Fal may attach a moderation type to a 401 or 403 response.  The status is
    therefore not sufficient to decide that another provider may be tried.
    Only machine-readable type fields are inspected; free-form provider text
    is deliberately ignored.
    """
    for value in error_types:
        normalized = re.sub(r'[^a-z0-9]+', '_', value.casefold()).strip('_')
        tokens = set(normalized.split('_'))
        if (
            normalized.startswith('content_policy')
            or normalized.startswith('content_moderation')
            or 'safety' in tokens
            or normalized == 'unsafe_content'
        ):
            return True
    return False


class FalVideoError(RuntimeError):
    """Base error carrying only content-free provider state."""

    def __init__(
        self,
        message: str,
        *,
        request_id: str | None = None,
        safe_to_fallback: bool = False,
    ) -> None:
        super().__init__(message)
        self.request_id = request_id
        self.safe_to_fallback = bool(safe_to_fallback)


class FalVideoNotConfiguredError(FalVideoError):
    """Fal is optional and no server-side key is configured."""


class FalVideoAuthError(FalVideoError):
    """Fal rejected authentication or authorization."""


class FalVideoQuotaError(FalVideoError):
    """Fal rejected a create before accepting it for billing."""


class FalVideoPolicyError(FalVideoError):
    """Fal rejected the input under a content policy."""


class FalVideoTransientError(FalVideoError):
    """Fal encountered a transport, queue, or runner failure."""


class FalVideoRejectedError(FalVideoError):
    """Fal definitively rejected an otherwise non-policy request."""


class FalVideoProtocolError(FalVideoError):
    """Fal returned an untrusted or malformed queue response."""


def fal_error_allows_provider_fallback(exc: BaseException) -> bool:
    """Return whether another paid provider can safely be submitted.

    A policy rejection never permits provider hopping.  Ambiguous submission,
    polling, and result failures also stay pinned to the accepted Fal request.
    """
    return isinstance(exc, FalVideoError) and exc.safe_to_fallback


def _safe_json(response: object) -> object:
    try:
        return response.json()
    except Exception:
        return None


def _fal_error_types(payload: object) -> set[str]:
    """Extract only documented machine-readable types, never messages."""
    error_types: set[str] = set()
    if not isinstance(payload, dict):
        return error_types
    for field in ('error_type', 'type', 'code'):
        top_level = payload.get(field)
        if isinstance(top_level, str) and top_level.strip():
            error_types.add(top_level.strip().casefold())
    detail = payload.get('detail')
    detail_items = (
        detail[:32]
        if isinstance(detail, list)
        else [detail]
        if isinstance(detail, dict)
        else []
    )
    for item in detail_items:
        if not isinstance(item, dict):
            continue
        value = item.get('type')
        if isinstance(value, str) and value.strip():
            error_types.add(value.strip().casefold())
    error = payload.get('error')
    if isinstance(error, dict):
        for field in ('error_type', 'type', 'code'):
            value = error.get(field)
            if isinstance(value, str) and value.strip():
                error_types.add(value.strip().casefold())
    return error_types


def _raise_submit_rejection(response: object) -> None:
    """Classify an explicit queue rejection without leaking its body."""
    status_code = getattr(response, 'status_code', None)
    status = status_code if type(status_code) is int else 0
    error_types = _fal_error_types(_safe_json(response))
    if _fal_error_is_policy(error_types):
        raise FalVideoPolicyError('Fal video content policy rejected the input')
    if status in {401, 403}:
        raise FalVideoAuthError(
            'Fal video authentication was rejected',
            safe_to_fallback=True,
        )
    if status in {402, 429}:
        raise FalVideoQuotaError(
            'Fal video capacity or balance is unavailable',
            safe_to_fallback=True,
        )
    if status in {400, 404, 409, 422}:
        raise FalVideoRejectedError(
            'Fal video create was definitively rejected',
            safe_to_fallback=True,
        )
    # A gateway or server response can arrive after the queue accepted a paid
    # create.  Its acceptance state is therefore ambiguous and no second paid
    # provider may be started.
    raise FalVideoTransientError('Fal video submission state is ambiguous')


def _raise_accepted_request_error(
    response: object,
    request_id: str,
) -> None:
    """Classify an error while reading an already accepted request."""
    status_code = getattr(response, 'status_code', None)
    status = status_code if type(status_code) is int else 0
    error_types = _fal_error_types(_safe_json(response))
    if _fal_error_is_policy(error_types):
        raise FalVideoPolicyError(
            'Fal video content policy rejected the input',
            request_id=request_id,
        )
    if status in {401, 403}:
        raise FalVideoAuthError(
            'Fal video result authorization failed',
            request_id=request_id,
        )
    if status in {402, 429}:
        raise FalVideoQuotaError(
            'Fal video result is temporarily unavailable',
            request_id=request_id,
        )
    if error_types & _FAL_TRANSIENT_ERROR_TYPES or status >= 500:
        raise FalVideoTransientError(
            'Fal video accepted request failed transiently',
            request_id=request_id,
        )
    raise FalVideoRejectedError(
        'Fal video accepted request returned no usable result',
        request_id=request_id,
    )


def _validate_queue_url(
    candidate: object,
    *,
    request_id: str,
    suffix: str,
) -> str:
    """Accept only the exact Fal queue URL for this model and request."""
    value = str(candidate or '').strip()
    try:
        parsed = urlparse(value)
        host = (parsed.hostname or '').casefold()
        port = parsed.port
    except ValueError as exc:
        raise FalVideoProtocolError(
            'Fal video returned an untrusted queue URL',
            request_id=request_id,
        ) from exc
    expected_path = f'/{FAL_SEEDANCE_FAST_MODEL}/requests/{request_id}{suffix}'
    if (
        parsed.scheme != 'https'
        or host != _FAL_QUEUE_HOST
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.path != expected_path
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise FalVideoProtocolError(
            'Fal video returned an untrusted queue URL',
            request_id=request_id,
        )
    return value


def validate_fal_media_url(candidate: object) -> str:
    """Validate the documented Fal media hosts before any download."""
    value = str(candidate or '').strip()
    try:
        parsed = urlparse(value)
        host = (parsed.hostname or '').casefold()
        port = parsed.port
    except ValueError as exc:
        raise FalVideoProtocolError(
            'Fal video returned an untrusted media URL'
        ) from exc
    fal_media_host = host == 'fal.media' or host.endswith('.fal.media')
    legacy_storage = (
        host == 'storage.googleapis.com'
        and parsed.path.startswith('/falserverless/')
    )
    if (
        parsed.scheme != 'https'
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.fragment
        or not (fal_media_host or legacy_storage)
    ):
        raise FalVideoProtocolError('Fal video returned an untrusted media URL')
    return value


def _seedance_duration(seconds: int) -> int:
    """Map pipeline footage to Seedance's exact 4-15 second enum."""
    return max(4, min(int(round(seconds)), 15))


def _completed_error(
    payload: dict,
    request_id: str,
) -> FalVideoError | None:
    error_types = _fal_error_types(payload)
    if not payload.get('error') and not error_types:
        return None
    if _fal_error_is_policy(error_types):
        return FalVideoPolicyError(
            'Fal video content policy rejected the input',
            request_id=request_id,
        )
    if error_types & _FAL_TRANSIENT_ERROR_TYPES:
        return FalVideoTransientError(
            'Fal video generation completed with a transient failure',
            request_id=request_id,
            safe_to_fallback=True,
        )
    return FalVideoRejectedError(
        'Fal video generation was definitively rejected',
        request_id=request_id,
        safe_to_fallback=True,
    )


def _parse_video_result(payload: object, request_id: str) -> dict:
    if not isinstance(payload, dict):
        raise FalVideoProtocolError(
            'Fal video returned a malformed result',
            request_id=request_id,
        )
    video = payload.get('video')
    if not isinstance(video, dict):
        raise FalVideoProtocolError(
            'Fal video returned no media descriptor',
            request_id=request_id,
        )
    content_type = str(video.get('content_type') or '').strip().casefold()
    if content_type and content_type != 'video/mp4':
        raise FalVideoProtocolError(
            'Fal video returned an invalid media descriptor',
            request_id=request_id,
        )
    raw_size = video.get('file_size')
    if raw_size is not None:
        if (
            type(raw_size) is not int
            or not 1024 <= raw_size <= 100 * 1024 * 1024
        ):
            raise FalVideoProtocolError(
                'Fal video returned an invalid media descriptor',
                request_id=request_id,
            )
    try:
        url = validate_fal_media_url(video.get('url'))
    except FalVideoProtocolError as exc:
        raise FalVideoProtocolError(
            'Fal video returned an untrusted media URL',
            request_id=request_id,
        ) from exc
    return {
        'url': url,
        'provider': 'fal_seedance_2_fast',
        'provider_attempts': 1,
        'provider_request_id': request_id,
    }


def generate_fal_video(
    prompt: str,
    seconds: int,
    *,
    aspect_ratio: str = '16:9',
) -> dict:
    """Submit one Seedance Fast job, poll it, and return trusted media metadata."""
    api_key = str(getattr(settings, 'fal_key', '') or '').strip()
    if not api_key:
        raise FalVideoNotConfiguredError('FAL_KEY is not configured')
    prompt_text = str(prompt or '').strip()
    if not prompt_text:
        raise ValueError('Fal video prompt is empty')
    if len(prompt_text.encode('utf-16-le')) // 2 > 1000:
        raise ValueError('Fal video prompt exceeds 1000 UTF-16 code units')
    aspect_ratio = str(aspect_ratio or '').strip()
    if aspect_ratio not in {'16:9', '9:16'}:
        raise ValueError('Fal video aspect ratio must be 16:9 or 9:16')

    duration = _seedance_duration(seconds)
    submit_url = f'{_FAL_QUEUE_ORIGIN}/{FAL_SEEDANCE_FAST_MODEL}'
    headers = {
        'Authorization': f'Key {api_key}',
        'Content-Type': 'application/json',
        # Bound Fal's durable queue lifecycle without enabling client-side POST
        # retries.  Fal may recover infrastructure failures under this same id.
        'X-Fal-Request-Timeout': str(int(_FAL_MAX_WAIT_SECONDS - 30)),
    }
    request_body = {
        'prompt': prompt_text,
        'resolution': '720p',
        'duration': str(duration),
        'aspect_ratio': aspect_ratio,
        # ElevenLabs remains the only narration source for deterministic QA.
        'generate_audio': False,
        'bitrate_mode': 'standard',
    }

    with httpx.Client(
        timeout=httpx.Timeout(30.0, connect=10.0),
        follow_redirects=False,
    ) as client:
        try:
            # Never retry this paid POST. A timeout may hide an accepted job.
            created = client.post(
                submit_url,
                headers=headers,
                json=request_body,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise FalVideoTransientError(
                'Fal video submission state is ambiguous'
            ) from exc
        status_code = getattr(created, 'status_code', None)
        if type(status_code) is not int or not 200 <= status_code < 300:
            _raise_submit_rejection(created)
        created_payload = _safe_json(created)
        request_id = str(
            created_payload.get('request_id')
            if isinstance(created_payload, dict)
            else ''
        ).strip()
        if not _FAL_REQUEST_ID_PATTERN.fullmatch(request_id):
            # A successful POST with a malformed response may still have queued
            # work. It is intentionally not safe to start another provider.
            raise FalVideoProtocolError(
                'Fal video returned an invalid request id'
            )

        default_status_url = (
            f'{_FAL_QUEUE_ORIGIN}/{FAL_SEEDANCE_FAST_MODEL}/requests/'
            f'{request_id}/status'
        )
        default_result_url = (
            f'{_FAL_QUEUE_ORIGIN}/{FAL_SEEDANCE_FAST_MODEL}/requests/'
            f'{request_id}'
        )
        status_url = _validate_queue_url(
            (
                created_payload.get('status_url')
                if isinstance(created_payload, dict)
                else None
            ) or default_status_url,
            request_id=request_id,
            suffix='/status',
        )
        raw_response_url = (
            created_payload.get('response_url')
            if isinstance(created_payload, dict)
            else None
        )
        if raw_response_url:
            try:
                result_url = _validate_queue_url(
                    raw_response_url,
                    request_id=request_id,
                    suffix='/response',
                )
            except FalVideoProtocolError:
                # The REST docs also document the canonical request URL as the
                # result endpoint. Accept that exact alternative, nothing else.
                result_url = _validate_queue_url(
                    raw_response_url,
                    request_id=request_id,
                    suffix='',
                )
        else:
            result_url = default_result_url

        deadline = time.monotonic() + _FAL_MAX_WAIT_SECONDS
        read_failures = 0
        while time.monotonic() < deadline:
            try:
                status_response = client.get(status_url, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError):
                read_failures += 1
                if read_failures >= _FAL_MAX_STATUS_READ_FAILURES:
                    raise FalVideoTransientError(
                        'Fal video status remained unavailable',
                        request_id=request_id,
                    )
                time.sleep(_FAL_POLL_INTERVAL_SECONDS)
                continue
            status_code = getattr(status_response, 'status_code', None)
            if type(status_code) is not int or not 200 <= status_code < 300:
                if type(status_code) is int and status_code >= 500:
                    read_failures += 1
                    if read_failures < _FAL_MAX_STATUS_READ_FAILURES:
                        time.sleep(_FAL_POLL_INTERVAL_SECONDS)
                        continue
                _raise_accepted_request_error(status_response, request_id)
            read_failures = 0
            status_payload = _safe_json(status_response)
            if not isinstance(status_payload, dict):
                raise FalVideoProtocolError(
                    'Fal video returned malformed queue status',
                    request_id=request_id,
                )
            echoed_id = status_payload.get('request_id')
            if echoed_id is not None and str(echoed_id).strip() != request_id:
                raise FalVideoProtocolError(
                    'Fal video returned mismatched queue status',
                    request_id=request_id,
                )
            status = str(status_payload.get('status') or '').strip().upper()
            if status in {'IN_QUEUE', 'IN_PROGRESS'}:
                time.sleep(_FAL_POLL_INTERVAL_SECONDS)
                continue
            if status != 'COMPLETED':
                raise FalVideoProtocolError(
                    'Fal video returned an invalid queue status',
                    request_id=request_id,
                )
            terminal_error = _completed_error(status_payload, request_id)
            if terminal_error is not None:
                raise terminal_error

            for result_attempt in range(_FAL_MAX_STATUS_READ_FAILURES):
                try:
                    result_response = client.get(result_url, headers=headers)
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    if result_attempt + 1 >= _FAL_MAX_STATUS_READ_FAILURES:
                        raise FalVideoTransientError(
                            'Fal video result remained unavailable',
                            request_id=request_id,
                        ) from exc
                    time.sleep(_FAL_POLL_INTERVAL_SECONDS)
                    continue
                result_status = getattr(result_response, 'status_code', None)
                if (
                    type(result_status) is int
                    and result_status >= 500
                    and result_attempt + 1 < _FAL_MAX_STATUS_READ_FAILURES
                ):
                    time.sleep(_FAL_POLL_INTERVAL_SECONDS)
                    continue
                if (
                    type(result_status) is not int
                    or not 200 <= result_status < 300
                ):
                    _raise_accepted_request_error(result_response, request_id)
                return _parse_video_result(
                    _safe_json(result_response),
                    request_id,
                )

    raise FalVideoTransientError(
        'Fal video generation timed out',
        request_id=request_id,
    )
