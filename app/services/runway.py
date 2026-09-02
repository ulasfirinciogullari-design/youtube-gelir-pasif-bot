import base64
import binascii
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import urljoin, urlparse

import httpx
from runwayml import BadRequestError, RateLimitError, RunwayML

from app.config import settings
from app.services.fal_video import (
    FalVideoError,
    fal_error_allows_provider_fallback,
    generate_fal_video,
    validate_fal_media_url,
)


_GEMINI_VIDEO_BASE = 'https://generativelanguage.googleapis.com/v1beta'
_GEMINI_VIDEO_MODEL = 'veo-3.1-lite-generate-preview'
_GEMINI_VIDEO_FAST_MODEL = 'veo-3.1-fast-generate-preview'
_GEMINI_VIDEO_STANDARD_MODEL = 'veo-3.1-generate-preview'
_GEMINI_IMAGE_MODEL = 'gemini-3.1-flash-image'
_GEMINI_IMAGE_ENDPOINT = f'{_GEMINI_VIDEO_BASE}/interactions'
_GEMINI_IMAGE_MIME_TYPE = 'image/jpeg'
_IMAGE_MOTION_RECIPE_VERSION = 'diagonal-push-v2'
_GEMINI_OPERATION_PATTERN = re.compile(
    r'^(?:models/[A-Za-z0-9._-]+/)?operations/[A-Za-z0-9._~/-]+$'
)
_GEMINI_VIDEO_HOSTS = {
    'generativelanguage.googleapis.com',
    'storage.googleapis.com',
}
_MAX_GENERATED_VIDEO_BYTES = 100 * 1024 * 1024
_MIN_GENERATED_IMAGE_BYTES = 10 * 1024
_MAX_GENERATED_IMAGE_BYTES = 12 * 1024 * 1024
_MAX_GENERATED_IMAGE_PIXELS = 8_388_608
_IMAGE_MOTION_FPS = 30
_GEMINI_VIDEO_QUOTA_COOLDOWN_SECONDS = 10 * 60
_GEMINI_VIDEO_QUOTA_BLOCKED_UNTIL: dict[str, float] = {}
_RUNWAY_GEN45_CREDITS_PER_SECOND = 12
_RUNWAY_SAFE_PROVIDER_FALLBACK_CODES = frozenset({
    'capacity_exhausted',
    'capacity_unavailable',
    'billing_limit_exceeded',
    'concurrency_limit_exceeded',
    'credit_balance_exhausted',
    'credits_exhausted',
    'insufficient_credits',
    'insufficient_credit_balance',
    'model_disabled',
    'model_not_available',
    'model_not_enabled',
    'model_not_supported',
    'model_temporarily_unavailable',
    'model_unavailable',
    'model_unsupported',
    'no_eligible_model',
    'not_enough_credits',
    'quota_exceeded',
    'unsupported_model',
})


class GeminiVideoTerminalError(RuntimeError):
    """A definitive completed-operation rejection that is safe to resubmit."""


class GeminiVideoQuotaError(RuntimeError):
    """A definitive model-create quota rejection with no accepted operation."""


class GeminiImageAttemptedError(RuntimeError):
    """A paid image create was attempted but yielded no usable descriptor."""


class RunwayCreateRejectedError(RuntimeError):
    """Runway definitively rejected create without authorizing provider hop."""


class RunwayCreditPreflightInsufficientError(RuntimeError):
    """A read-only balance check proved Gen-4.5 create cannot succeed."""


def _runway_gen45_credits_known_insufficient(client, seconds: int) -> bool:
    """Return true only when Runway reports a valid, insufficient balance.

    The organization request is a bounded, read-only GET. Any missing SDK
    resource, timeout, transport failure, provider rejection, or malformed
    response is deliberately treated as unknown so the existing create path
    keeps its behavior. No balance value or provider response is retained.
    """
    try:
        organization = client.organization.retrieve(timeout=5.0)
        credit_balance = getattr(organization, 'credit_balance', None)
    except Exception:
        return False
    return (
        type(credit_balance) is int
        and credit_balance >= 0
        and credit_balance < _RUNWAY_GEN45_CREDITS_PER_SECOND * int(seconds)
    )


def _normalize_runway_provider_code(value: object) -> str:
    """Normalize one bounded machine code, never an arbitrary error body."""
    if not isinstance(value, str):
        return ''
    stripped = value.strip()
    if (
        not stripped
        or len(stripped) > 96
        or re.fullmatch(r'[A-Za-z0-9 .:_-]+', stripped) is None
    ):
        return ''
    return re.sub(r'[^a-z0-9]+', '_', stripped.casefold()).strip('_')


def _runway_create_error_allows_provider_fallback(exc: BaseException) -> bool:
    """Allow a new paid provider only for explicit pre-acceptance codes.

    Runway documents 400 as an input error that should not be retried.  A
    BadRequest therefore fails closed unless its structured SDK ``body``
    carries an exact capacity or model-availability code.  The official
    top-level ``error`` string is accepted only when its entire normalized
    value equals one of those codes.  We never inspect ``str(exc)`` or
    detail/message fields, which can contain prompts or sensitive diagnostics.
    """
    body = getattr(exc, 'body', None)
    candidates: list[object] = []
    if isinstance(body, dict):
        for field in (
            'code',
            'errorCode',
            'error_code',
            'failureCode',
            'failure_code',
            'reason',
            'type',
        ):
            candidates.append(body.get(field))
        error = body.get('error')
        if isinstance(error, str):
            candidates.append(error)
        elif isinstance(error, dict):
            for field in (
                'code',
                'errorCode',
                'error_code',
                'failureCode',
                'failure_code',
                'reason',
                'type',
            ):
                candidates.append(error.get(field))
    codes = {
        normalized
        for value in candidates
        if (normalized := _normalize_runway_provider_code(value))
    }
    return bool(codes & _RUNWAY_SAFE_PROVIDER_FALLBACK_CODES)


def _runway_bad_request_category(exc: BaseException) -> str:
    """Return one content-free category for a rejected create request.

    The SDK body can include our full prompt and provider diagnostics, so only
    documented machine codes and allow-listed input-field names are surfaced.
    A bounded message fragment is used only to choose among fixed categories;
    it is never retained or serialized.
    """
    body = getattr(exc, 'body', None)
    if not isinstance(body, dict):
        return 'bad_request'

    safe_fields = {
        'audio': 'audio',
        'duration': 'duration',
        'model': 'model',
        'promptimage': 'prompt_image',
        'prompttext': 'prompt_text',
        'ratio': 'ratio',
    }
    issues = body.get('issues')
    if isinstance(issues, list):
        for issue in issues[:20]:
            if not isinstance(issue, dict):
                continue
            path = issue.get('path') or issue.get('loc')
            if not isinstance(path, list):
                continue
            for part in path[:8]:
                field = re.sub(r'[^a-z0-9]+', '', str(part).casefold())
                if field in safe_fields:
                    safe_field = safe_fields[field]
                    if safe_field == 'prompt_text':
                        # The message itself is never retained or serialized.
                        # It is inspected only for tightly bounded categories
                        # that distinguish contract length from safety policy.
                        raw_message = issue.get('message')
                        message = (
                            raw_message[:4096].casefold()
                            if isinstance(raw_message, str)
                            else ''
                        )
                        if re.search(
                            r'\b(?:safety|moderation|unsafe)\b|policy violation',
                            message,
                        ):
                            return 'prompt_safety'
                        if (
                            re.search(
                                r'\b(?:character|utf[ -]?16|code unit|length)\b',
                                message,
                            )
                            and re.search(
                                r'\b(?:1000|too long|at most|maximum|max(?:imum)? length)\b',
                                message,
                            )
                        ):
                            return 'prompt_too_long'
                        if re.search(
                            r'\b(?:non[ -]?empty|required|must not be empty)\b',
                            message,
                        ):
                            return 'prompt_empty'
                    return f'invalid_{safe_field}'

    allowed_codes = {
        'bad_request',
        'invalid_argument',
        'invalid_request',
        'model_not_supported',
        'parameter_unknown',
        'unsupported_model',
        'validation_error',
        'validation_of_body_failed',
    }
    candidates: list[object] = []
    for field in ('code', 'errorCode', 'error_code', 'reason', 'type'):
        candidates.append(body.get(field))
    error = body.get('error')
    if isinstance(error, str):
        candidates.append(error)
    elif isinstance(error, dict):
        for field in ('code', 'errorCode', 'error_code', 'reason', 'type'):
            candidates.append(error.get(field))
    for value in candidates:
        normalized = _normalize_runway_provider_code(value)
        if normalized in allowed_codes:
            return normalized
    return 'bad_request'


def _is_daily_gemini_quota_rejection(response: object) -> bool:
    """Recognize an explicit per-day rejection without relying on message text."""
    try:
        payload = response.json()
    except Exception:
        return False
    stack = [payload]
    inspected = 0
    while stack and inspected < 256:
        value = stack.pop()
        inspected += 1
        if isinstance(value, dict):
            for key, item in value.items():
                if (
                    str(key).casefold() in {'quotaid', 'quotametric'}
                    and isinstance(item, str)
                    and 'perday' in re.sub(
                        r'[^a-z0-9]+',
                        '',
                        item.casefold(),
                    )
                ):
                    return True
                stack.append(item)
        elif isinstance(value, list):
            stack.extend(value)
    return False


def _gemini_image_rejection_category(response: object) -> str:
    """Return one content-free provider category for safe diagnostics."""
    try:
        payload = response.json()
    except Exception:
        return 'unknown'
    error = payload.get('error') if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return 'unknown'
    allowed = {
        'invalid_argument',
        'invalid_request',
        'parameter_unknown',
        'failed_precondition',
        'permission_denied',
        'resource_exhausted',
    }
    for field in ('status', 'type'):
        value = str(error.get(field) or '').strip().casefold()
        normalized = re.sub(r'[^a-z0-9]+', '_', value).strip('_')
        if normalized in allowed:
            return normalized
    return 'unknown'


def _validated_jpeg_dimensions(image_bytes: bytes) -> tuple[int, int]:
    """Validate one bounded, non-animated JPEG and return its dimensions."""
    if not isinstance(image_bytes, bytes):
        raise RuntimeError('Gemini image returned invalid media')
    if not _MIN_GENERATED_IMAGE_BYTES <= len(image_bytes) <= _MAX_GENERATED_IMAGE_BYTES:
        raise RuntimeError('Gemini image returned invalid media')
    if (
        not image_bytes.startswith(b'\xff\xd8\xff')
        or not image_bytes.endswith(b'\xff\xd9')
    ):
        raise RuntimeError('Gemini image returned invalid media')

    # JPEG dimensions live in a Start Of Frame segment before scan data. Parse
    # only the bounded header rather than asking an image decoder to open an
    # untrusted, potentially enormous raster first.
    sof_markers = {
        0xC0, 0xC1, 0xC2, 0xC3,
        0xC5, 0xC6, 0xC7,
        0xC9, 0xCA, 0xCB,
        0xCD, 0xCE, 0xCF,
    }
    position = 2
    width = height = 0
    scan_data_start = 0
    while position + 3 < len(image_bytes):
        if image_bytes[position] != 0xFF:
            raise RuntimeError('Gemini image returned invalid media')
        while position < len(image_bytes) and image_bytes[position] == 0xFF:
            position += 1
        if position >= len(image_bytes):
            break
        marker = image_bytes[position]
        position += 1
        if marker in {0x01, *range(0xD0, 0xD9)}:
            continue
        if position + 2 > len(image_bytes):
            raise RuntimeError('Gemini image returned invalid media')
        segment_length = int.from_bytes(
            image_bytes[position:position + 2],
            'big',
        )
        if segment_length < 2 or position + segment_length > len(image_bytes):
            raise RuntimeError('Gemini image returned invalid media')
        if marker == 0xDA:
            scan_data_start = position + segment_length
            break
        if marker in sof_markers:
            if segment_length < 8 or width or height:
                raise RuntimeError('Gemini image returned invalid media')
            height = int.from_bytes(
                image_bytes[position + 3:position + 5],
                'big',
            )
            width = int.from_bytes(
                image_bytes[position + 5:position + 7],
                'big',
            )
        position += segment_length

    if (
        width < 640
        or height < 360
        or width * height > _MAX_GENERATED_IMAGE_PIXELS
        or abs((width / height) - (16 / 9)) > 0.04
        or scan_data_start <= 0
        or len(image_bytes) - scan_data_start - 2 < 128
    ):
        raise RuntimeError('Gemini image returned invalid media')
    return width, height


def _probe_single_jpeg_frame(
    image_bytes: bytes,
    expected_dimensions: tuple[int, int],
) -> None:
    """Require FFmpeg to decode exactly one bounded JPEG frame."""
    try:
        completed = subprocess.run([
            'ffprobe', '-v', 'error',
            '-f', 'image2pipe',
            '-count_frames', '-select_streams', 'v:0',
            '-show_entries', 'stream=codec_name,width,height,nb_read_frames',
            '-of', 'json', 'pipe:0',
        ], input=image_bytes, capture_output=True, check=False, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError('Gemini image decode validation failed') from exc
    if completed.returncode != 0:
        raise RuntimeError('Gemini image returned invalid media')
    try:
        payload = json.loads(completed.stdout)
        streams = payload.get('streams') or []
        stream = streams[0] if len(streams) == 1 else {}
        width = int(stream.get('width'))
        height = int(stream.get('height'))
        frames = int(stream.get('nb_read_frames'))
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError('Gemini image returned invalid media') from exc
    if (
        stream.get('codec_name') != 'mjpeg'
        or (width, height) != expected_dimensions
        or frames != 1
    ):
        raise RuntimeError('Gemini image returned invalid media')


def _decode_gemini_image(mime_type: object, encoded_data: object) -> bytes:
    """Strictly decode the single inline image allowed by this fallback."""
    if mime_type != _GEMINI_IMAGE_MIME_TYPE or not isinstance(encoded_data, str):
        raise RuntimeError('Gemini image returned invalid media')
    maximum_encoded_length = ((_MAX_GENERATED_IMAGE_BYTES + 2) // 3) * 4
    if not encoded_data or len(encoded_data) > maximum_encoded_length:
        raise RuntimeError('Gemini image returned invalid media')
    try:
        image_bytes = base64.b64decode(encoded_data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RuntimeError('Gemini image returned invalid media') from exc
    _validated_jpeg_dimensions(image_bytes)
    return image_bytes


def _generate_gemini_image_descriptor(
    prompt_text: str,
    seconds: int,
) -> dict:
    """Submit one retry-free paid image request for a private-preview shot."""
    if not settings.gemini_api_key:
        raise RuntimeError('Gemini image fallback is not configured')
    prompt = str(prompt_text or '').strip()
    if not prompt:
        raise RuntimeError('Gemini image fallback prompt is empty')
    if len(prompt.encode('utf-16-le')) // 2 > 1000:
        raise RuntimeError('Gemini image fallback prompt is too long')
    motion_seconds = max(5, min(int(round(seconds)), 10))
    headers = {
        'x-goog-api-key': settings.gemini_api_key,
        'Content-Type': 'application/json',
    }
    request_payload = {
        'model': _GEMINI_IMAGE_MODEL,
        'store': False,
        # Keep this body identical to the current Interactions image contract.
        # The live API rejects the otherwise documented optional delivery
        # selector for this model; successful inline media is already the
        # response default.
        'input': prompt,
        'response_format': {
            'type': 'image',
            'mime_type': _GEMINI_IMAGE_MIME_TYPE,
            'aspect_ratio': '16:9',
            'image_size': '1K',
        },
    }
    with httpx.Client(
        timeout=httpx.Timeout(180.0, connect=10.0),
        follow_redirects=False,
    ) as client:
        # Never retry this paid POST. A timeout or any other transport failure
        # has an ambiguous acceptance state and must terminate the fallback.
        response = client.post(
            _GEMINI_IMAGE_ENDPOINT,
            headers=headers,
            json=request_payload,
        )
        status_code = getattr(response, 'status_code', None)
        if type(status_code) is int and not 200 <= status_code < 300:
            category = _gemini_image_rejection_category(response)
            raise RuntimeError(
                f'Gemini image request was rejected ({category})'
            )
        response.raise_for_status()
        payload = response.json()

    if not isinstance(payload, dict) or payload.get('status') != 'completed':
        raise RuntimeError('Gemini image returned an invalid response')
    steps = payload.get('steps')
    if not isinstance(steps, list):
        raise RuntimeError('Gemini image returned an invalid response')
    model_outputs = [
        step for step in steps
        if isinstance(step, dict) and step.get('type') == 'model_output'
    ]
    if len(model_outputs) != 1:
        raise RuntimeError('Gemini image returned an invalid response')
    content = model_outputs[0].get('content')
    if not isinstance(content, list) or len(content) != 1:
        raise RuntimeError('Gemini image returned an invalid response')
    image_block = content[0]
    if not isinstance(image_block, dict) or image_block.get('type') != 'image':
        raise RuntimeError('Gemini image returned an invalid response')
    image_bytes = _decode_gemini_image(
        image_block.get('mime_type'),
        image_block.get('data'),
    )
    dimensions = _validated_jpeg_dimensions(image_bytes)
    _probe_single_jpeg_frame(image_bytes, dimensions)
    normalized_data = base64.b64encode(image_bytes).decode('ascii')
    return {
        '_inline_image': {
            'mime_type': _GEMINI_IMAGE_MIME_TYPE,
            'data': normalized_data,
        },
        'motion_seconds': motion_seconds,
        'source_media_type': 'image',
        'synthetic_motion': True,
        'motion_recipe_version': _IMAGE_MOTION_RECIPE_VERSION,
        'image_sha256': hashlib.sha256(image_bytes).hexdigest(),
        'prompt_sha256': hashlib.sha256(prompt.encode('utf-8')).hexdigest(),
        'image_model': _GEMINI_IMAGE_MODEL,
    }


def _gemini_video_duration(seconds: int) -> int:
    """Map the requested single-pass shot to a supported Veo duration."""
    if seconds <= 4:
        return 4
    if seconds <= 6:
        return 6
    if seconds <= 8:
        return 8
    raise RuntimeError('Gemini video fallback cannot satisfy this shot duration')


def _generate_gemini_video_uri(
    prompt_text: str,
    seconds: int,
    model_name: str = _GEMINI_VIDEO_MODEL,
) -> str:
    """Create exactly one Gemini Veo task and return its trusted media URI."""
    if not settings.gemini_api_key:
        raise RuntimeError('Gemini video fallback is not configured')

    duration = _gemini_video_duration(seconds)
    headers = {
        'x-goog-api-key': settings.gemini_api_key,
        'Content-Type': 'application/json',
    }
    if model_name not in {
        _GEMINI_VIDEO_MODEL,
        _GEMINI_VIDEO_FAST_MODEL,
        _GEMINI_VIDEO_STANDARD_MODEL,
    }:
        raise ValueError('Unsupported Gemini video fallback model')
    endpoint = f'{_GEMINI_VIDEO_BASE}/models/{model_name}:predictLongRunning'
    request_payload = {
        'instances': [{'prompt': prompt_text}],
        'parameters': {
            'aspectRatio': '16:9',
            # The live REST endpoint rejects JSON strings here even though
            # older documentation tables displayed quoted values. Send the
            # schema's numeric duration type.
            'durationSeconds': duration,
            'resolution': '720p',
        },
    }
    with httpx.Client(
        timeout=httpx.Timeout(60.0, connect=10.0),
        follow_redirects=False,
    ) as client:
        # A create request is retried only after an explicit 429 response;
        # ambiguous network failures may mean the paid operation was accepted.
        created = client.post(
            endpoint,
            headers=headers,
            json=request_payload,
        )
        if getattr(created, 'status_code', None) == 429:
            if _is_daily_gemini_quota_rejection(created):
                raise GeminiVideoQuotaError(
                    'Gemini video model daily quota is exhausted'
                )
            # A concrete 429 response proves that no paid operation was
            # accepted. Honor the provider delay and retry this create once;
            # network errors, timeouts and every other HTTP status remain
            # non-retryable because their acceptance state may be ambiguous.
            raw_retry_after = str(
                created.headers.get('retry-after') or ''
            ).strip()
            try:
                retry_after = float(raw_retry_after)
            except ValueError:
                retry_after = 30.0
            time.sleep(max(1.0, min(retry_after, 60.0)))
            created = client.post(
                endpoint,
                headers=headers,
                json=request_payload,
            )
            if getattr(created, 'status_code', None) == 429:
                raise GeminiVideoQuotaError(
                    'Gemini video model quota is exhausted'
                )
        created.raise_for_status()
        created_payload = created.json()
        operation_name = str(
            created_payload.get('name')
            if isinstance(created_payload, dict)
            else ''
        ).strip()
        if (
            not _GEMINI_OPERATION_PATTERN.fullmatch(operation_name)
            or '..' in operation_name
        ):
            raise RuntimeError('Gemini video returned an invalid operation id')

        operation_url = f'{_GEMINI_VIDEO_BASE}/{operation_name}'
        deadline = time.monotonic() + 600.0
        while time.monotonic() < deadline:
            status = client.get(operation_url, headers=headers)
            status.raise_for_status()
            payload = status.json()
            if not isinstance(payload, dict):
                raise RuntimeError('Gemini video returned malformed status')
            if payload.get('done') is True:
                if payload.get('error'):
                    raise GeminiVideoTerminalError(
                        'Gemini video generation was rejected'
                    )
                response = payload.get('response') or {}
                video_response = response.get('generateVideoResponse') or {}
                samples = video_response.get('generatedSamples') or []
                first = samples[0] if samples and isinstance(samples[0], dict) else {}
                video = first.get('video') if isinstance(first, dict) else {}
                uri = str(
                    video.get('uri') if isinstance(video, dict) else ''
                ).strip()
                parsed = urlparse(uri)
                if (
                    parsed.scheme != 'https'
                    or (parsed.hostname or '').lower() not in _GEMINI_VIDEO_HOSTS
                ):
                    raise RuntimeError('Gemini video returned an untrusted media URI')
                return uri
            time.sleep(10.0)
    raise TimeoutError('Gemini video generation timed out')


def _create_text_to_video_task(client, prompt_text: str, seconds: int):
    """Create one paid task, falling back only after a rejected Gen-4.5 create.

    Task polling deliberately remains outside this function. A rate limit while
    polling an accepted task must never cause a second paid task submission.
    """
    try:
        return client.text_to_video.create(
            model='gen4.5',
            prompt_text=prompt_text,
            ratio='1280:720',
            duration=seconds,
        )
    except RateLimitError:
        return client.text_to_video.create(
            model='seedance2_fast',
            prompt_text=prompt_text,
            ratio='1280:720',
            duration=seconds,
            audio=False,
        )


def generate_scene(
    prompt: str,
    duration: int = 5,
    *,
    allow_image_motion: bool = False,
    image_prompt: str | None = None,
) -> dict:
    if not settings.runwayml_api_secret:
        raise RuntimeError('RUNWAYML_API_SECRET is not configured')

    prompt_text = str(prompt).strip()
    if not prompt_text:
        raise ValueError('Runway prompt is empty')
    if len(prompt_text.encode('utf-16-le')) // 2 > 1000:
        raise ValueError('Runway prompt exceeds 1000 UTF-16 code units')

    seconds = max(2, min(int(round(duration)), 10))
    # Paid task creation is never retried implicitly: an ambiguous timeout may
    # mean the provider accepted the POST even though its response was lost.
    create_client = RunwayML(
        api_key=settings.runwayml_api_secret,
        max_retries=0,
    )
    try:
        if _runway_gen45_credits_known_insufficient(
            create_client,
            seconds,
        ):
            # The read-only response proves the paid Runway create cannot
            # succeed. Reuse the same bounded provider fallback path as an
            # explicit pre-acceptance capacity rejection without submitting
            # an impossible Runway POST or exposing the account balance.
            raise RunwayCreditPreflightInsufficientError(
                'Runway Gen-4.5 credit preflight rejected create'
            )
        created = _create_text_to_video_task(
            create_client,
            prompt_text,
            seconds,
        )
    except (BadRequestError, RunwayCreditPreflightInsufficientError) as exc:
        # This catch deliberately covers only paid task creation. Once Runway
        # has accepted a task, no polling or download error may start a second
        # paid generation with another provider.
        if (
            isinstance(exc, BadRequestError)
            and not _runway_create_error_allows_provider_fallback(exc)
        ):
            rejected = RunwayCreateRejectedError(
                'Runway video create was definitively rejected'
            )
            rejected.reason_code = _runway_bad_request_category(exc)
            raise rejected from None
        fal_fallback_from = None
        if str(getattr(settings, 'fal_key', '') or '').strip():
            try:
                return generate_fal_video(prompt_text, seconds)
            except FalVideoError as exc:
                # Only an explicit pre-acceptance rejection or a definitive
                # completed-job failure may start another paid provider.  An
                # ambiguous POST, poll timeout, or result failure stays pinned
                # to Fal's accepted request id.
                if not fal_error_allows_provider_fallback(exc):
                    raise
                fal_fallback_from = 'fal_seedance_2_fast'

        def generate_with_gemini_model(
            model_name: str | None = None,
        ) -> tuple[str, int]:
            selected_model = model_name or _GEMINI_VIDEO_MODEL
            if time.monotonic() < _GEMINI_VIDEO_QUOTA_BLOCKED_UNTIL.get(
                selected_model,
                0.0,
            ):
                raise GeminiVideoQuotaError(
                    'Gemini video model is in a local quota cooldown'
                )
            provider_attempts = 1
            call_args = (
                (prompt_text, seconds)
                if model_name is None
                else (prompt_text, seconds, model_name)
            )
            try:
                try:
                    video_uri = _generate_gemini_video_uri(*call_args)
                except GeminiVideoTerminalError:
                    # The provider explicitly completed the operation with an
                    # error, so a single resubmission is not ambiguous.
                    provider_attempts = 2
                    video_uri = _generate_gemini_video_uri(*call_args)
            except GeminiVideoQuotaError:
                # A typed quota rejection is definitive rather than an
                # ambiguous create response. Remember that per-model result
                # briefly so later scenes in this worker skip the same dead
                # quota path instead of paying another backoff penalty before
                # the image fallback.
                _GEMINI_VIDEO_QUOTA_BLOCKED_UNTIL[selected_model] = (
                    time.monotonic() + _GEMINI_VIDEO_QUOTA_COOLDOWN_SECONDS
                )
                raise
            _GEMINI_VIDEO_QUOTA_BLOCKED_UNTIL.pop(selected_model, None)
            return video_uri, provider_attempts

        def generate_image_motion_result(
            *,
            fallback_reason: str,
            quota_fallback_from: str | None,
            quota_fallback_chain: list[str],
        ) -> dict:
            if not allow_image_motion or not str(image_prompt or '').strip():
                raise RuntimeError(
                    'Gemini video fallback cannot satisfy this shot duration'
                    if seconds > 8
                    else 'Gemini image-motion fallback is not allowed'
                )
            try:
                image_descriptor = _generate_gemini_image_descriptor(
                    str(image_prompt),
                    seconds,
                )
            except Exception as exc:
                # Preserve a narrow, content-free receipt so the caller can
                # reserve this scene even after an ambiguous timeout or a
                # malformed paid response. The same scene must never submit a
                # second image create during final repair.
                raise GeminiImageAttemptedError(
                    'Gemini image create returned no usable media'
                ) from exc
            result = {
                **image_descriptor,
                'provider': 'gemini_image_motion',
                'provider_attempts': 1,
                'quota_fallback_from': quota_fallback_from,
                'quota_fallback_chain': quota_fallback_chain,
                'fallback_reason': fallback_reason,
            }
            if fal_fallback_from:
                result['provider_fallback_from'] = fal_fallback_from
            return result

        # Veo accepts at most eight seconds. This deterministic capability
        # branch runs before any Veo POST, so no video operation can exist.
        # The image request remains confined to the exact private preview by
        # the caller's allow_image_motion contract.
        if seconds > 8:
            return generate_image_motion_result(
                fallback_reason='unsupported_veo_duration',
                quota_fallback_from=None,
                quota_fallback_chain=[],
            )

        provider = 'gemini_veo'
        quota_fallback_from = None
        try:
            video_uri, provider_attempts = generate_with_gemini_model()
        except GeminiVideoQuotaError:
            # Rate limits are reported per model. This switch occurs only
            # after both bounded Lite create requests were explicitly rejected,
            # so no Lite operation can exist or incur a charge.
            provider = 'gemini_veo_fast'
            quota_fallback_from = 'gemini_veo'
            try:
                video_uri, provider_attempts = generate_with_gemini_model(
                    _GEMINI_VIDEO_FAST_MODEL
                )
            except GeminiVideoQuotaError:
                # Standard is the final bounded model fallback. It is reached
                # only after two explicit Fast create rejections, before any
                # Fast operation can exist. Ambiguous failures never arrive
                # here and therefore never submit another paid generation.
                provider = 'gemini_veo_standard'
                quota_fallback_from = 'gemini_veo_fast'
                try:
                    video_uri, provider_attempts = generate_with_gemini_model(
                        _GEMINI_VIDEO_STANDARD_MODEL
                    )
                except GeminiVideoQuotaError:
                    # A still-image request is a private-preview-only escape
                    # hatch. It is reached only after all three Veo creates
                    # were explicitly rejected for quota, never after an
                    # ambiguous network, polling, safety or download failure.
                    if not allow_image_motion or not str(image_prompt or '').strip():
                        raise
                    return generate_image_motion_result(
                        fallback_reason='veo_quota_exhausted',
                        quota_fallback_from='gemini_veo_standard',
                        quota_fallback_chain=[
                            'gemini_veo',
                            'gemini_veo_fast',
                            'gemini_veo_standard',
                        ],
                    )
        result = {
            'url': video_uri,
            'provider': provider,
            'provider_attempts': provider_attempts,
            'quota_fallback_from': quota_fallback_from,
        }
        if fal_fallback_from:
            result['provider_fallback_from'] = fal_fallback_from
        return result
    task_id = str(getattr(created, 'id', '') or '').strip()
    if not task_id:
        raise RuntimeError('Runway returned no task id')

    # Retrieval is read-only, so the SDK's bounded default retries are safe.
    poll_client = RunwayML(api_key=settings.runwayml_api_secret)
    completed = poll_client.tasks.retrieve(
        task_id,
    ).wait_for_task_output(timeout=600)
    output = completed.output or []
    if not output:
        raise RuntimeError('Runway returned no video output')
    return {
        'url': str(output[0]),
        'provider': 'runway',
        'provider_attempts': 1,
    }


def _image_motion_filter(image_sha256: str, frame_count: int) -> str:
    """Return a deterministic, center-safe documentary camera move."""
    if (
        not re.fullmatch(r'[0-9a-f]{64}', str(image_sha256 or ''))
        or not 150 <= int(frame_count) <= 300
    ):
        raise RuntimeError('Gemini image-motion descriptor is invalid')
    direction = int(image_sha256[:2], 16)
    focal_x_start, focal_x_end = (
        (0.49, 0.55) if direction & 1 == 0 else (0.51, 0.45)
    )
    focal_y_start, focal_y_end = (
        (0.495, 0.52) if direction & 2 == 0 else (0.505, 0.48)
    )
    final_frame = frame_count - 1
    focal_x_delta = focal_x_end - focal_x_start
    focal_y_delta = focal_y_end - focal_y_start
    return (
        'scale=2560:1440:force_original_aspect_ratio=increase:flags=lanczos,'
        'crop=2560:1440,'
        f"zoompan=z='1.06+0.18*on/{final_frame}':"
        f"x='iw*({focal_x_start:.3f}+({focal_x_delta:.3f})*"
        f"on/{final_frame})-iw/(2*zoom)':"
        f"y='ih*({focal_y_start:.3f}+({focal_y_delta:.3f})*"
        f"on/{final_frame})-ih/(2*zoom)':"
        f'd={frame_count}:s=1280x720:fps={_IMAGE_MOTION_FPS},'
        'setsar=1,scale=in_range=full:out_range=tv,format=yuv420p'
    )


def _render_gemini_image_motion(
    descriptor: dict,
    partial: Path,
) -> None:
    """Turn one validated JPEG into a deterministic, exact-length MP4."""
    inline_image = descriptor.pop('_inline_image', None)
    if not isinstance(inline_image, dict) or set(inline_image) != {
        'mime_type',
        'data',
    }:
        raise RuntimeError('Gemini image-motion descriptor is invalid')
    image_bytes = _decode_gemini_image(
        inline_image.get('mime_type'),
        inline_image.get('data'),
    )
    try:
        seconds = int(descriptor.get('motion_seconds'))
    except (TypeError, ValueError) as exc:
        raise RuntimeError('Gemini image-motion descriptor is invalid') from exc
    expected_image_hash = str(descriptor.get('image_sha256') or '')
    if (
        descriptor.get('provider') != 'gemini_image_motion'
        or descriptor.get('source_media_type') != 'image'
        or descriptor.get('synthetic_motion') is not True
        or descriptor.get('motion_recipe_version') != _IMAGE_MOTION_RECIPE_VERSION
        or not 5 <= seconds <= 10
        or not re.fullmatch(r'[0-9a-f]{64}', expected_image_hash)
        or hashlib.sha256(image_bytes).hexdigest() != expected_image_hash
    ):
        raise RuntimeError('Gemini image-motion descriptor is invalid')

    frame_count = seconds * _IMAGE_MOTION_FPS
    source_image = partial.with_name(f'{partial.name}.source.jpg')
    source_image.unlink(missing_ok=True)
    zoom_filter = _image_motion_filter(expected_image_hash, frame_count)
    try:
        source_image.write_bytes(image_bytes)
        completed = subprocess.run([
            'ffmpeg', '-y', '-hide_banner', '-nostats',
            '-i', str(source_image),
            '-vf', zoom_filter,
            '-frames:v', str(frame_count),
            '-an', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '19',
            '-pix_fmt', 'yuv420p', '-color_range', 'tv',
            '-movflags', '+faststart',
            '-f', 'mp4', str(partial),
        ], capture_output=True, text=True, check=False, timeout=180)
        if completed.returncode != 0:
            raise RuntimeError('Gemini image-motion render failed')

        probe = subprocess.run([
            'ffprobe', '-v', 'error', '-count_frames',
            '-select_streams', 'v:0',
            '-show_entries', (
                'stream=width,height,r_frame_rate,pix_fmt,'
                'color_range,sample_aspect_ratio,nb_read_frames,duration'
            ),
            '-of', 'json', str(partial),
        ], capture_output=True, text=True, check=False, timeout=60)
        if probe.returncode != 0:
            raise RuntimeError('Gemini image-motion validation failed')
        try:
            probe_payload = json.loads(probe.stdout)
            streams = probe_payload.get('streams') or []
            stream = streams[0] if len(streams) == 1 else {}
            actual_duration = float(stream.get('duration'))
            actual_frames = int(stream.get('nb_read_frames'))
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError('Gemini image-motion validation failed') from exc
        if (
            stream.get('width') != 1280
            or stream.get('height') != 720
            or stream.get('r_frame_rate') != '30/1'
            or stream.get('pix_fmt') != 'yuv420p'
            or stream.get('color_range') != 'tv'
            or stream.get('sample_aspect_ratio') != '1:1'
            or actual_frames != frame_count
            or abs(actual_duration - seconds) > 0.04
        ):
            raise RuntimeError('Gemini image-motion validation failed')
        with partial.open('rb') as file_handle:
            header = file_handle.read(12)
        if len(header) < 12 or header[4:8] != b'ftyp':
            raise RuntimeError('Gemini image-motion validation failed')
    finally:
        source_image.unlink(missing_ok=True)


def download_generated_scene(
    source: str | dict,
    output_path: str | Path,
) -> str:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(f'{output.name}.part')

    def validate_gemini_url(candidate: str):
        parsed = urlparse(candidate)
        host = (parsed.hostname or '').lower()
        if parsed.scheme != 'https' or host not in _GEMINI_VIDEO_HOSTS:
            raise RuntimeError('Gemini video download redirected to an untrusted host')
        return host

    def write_checked_video(response) -> None:
        content_type = str(
            response.headers.get('content-type') or ''
        ).partition(';')[0].strip().lower()
        if not (
            content_type.startswith('video/')
            or content_type == 'application/octet-stream'
        ):
            raise RuntimeError('Generated video download returned a non-video response')
        raw_length = str(response.headers.get('content-length') or '').strip()
        if raw_length:
            try:
                content_length = int(raw_length)
            except ValueError as exc:
                raise RuntimeError(
                    'Generated video download returned an invalid size'
                ) from exc
            if content_length > _MAX_GENERATED_VIDEO_BYTES:
                raise RuntimeError('Generated video download exceeded the size limit')
        byte_count = 0
        with partial.open('wb') as file_handle:
            for chunk in response.iter_bytes():
                if not chunk:
                    continue
                if byte_count + len(chunk) > _MAX_GENERATED_VIDEO_BYTES:
                    raise RuntimeError(
                        'Generated video download exceeded the size limit'
                    )
                file_handle.write(chunk)
                byte_count += len(chunk)
        if byte_count < 1024:
            raise RuntimeError('Generated video download was unexpectedly small')
        with partial.open('rb') as file_handle:
            header = file_handle.read(12)
        if len(header) < 12 or header[4:8] != b'ftyp':
            raise RuntimeError('Generated video download was not a valid MP4 file')

    try:
        partial.unlink(missing_ok=True)
        if isinstance(source, dict):
            if source.get('provider') == 'gemini_image_motion':
                _render_gemini_image_motion(source, partial)
                partial.replace(output)
                return str(output)
            url = str(source.get('url') or '').strip()
            source_provider = str(source.get('provider') or '').strip()
        else:
            url = str(source).strip()
            source_provider = ''
        if not url:
            raise RuntimeError('Generated video URL is empty')
        initial_host = (urlparse(str(url)).hostname or '').lower()
        if initial_host in _GEMINI_VIDEO_HOSTS:
            current_url = str(url)
            # Follow at most five redirects manually. The API key is attached
            # only to the exact Gemini API host and is stripped before a
            # trusted Cloud Storage hop.
            for _redirect_count in range(6):
                current_host = validate_gemini_url(current_url)
                headers = {}
                if current_host == 'generativelanguage.googleapis.com':
                    if not settings.gemini_api_key:
                        raise RuntimeError(
                            'Gemini video download is not configured'
                        )
                    headers['x-goog-api-key'] = settings.gemini_api_key
                with httpx.stream(
                    'GET',
                    current_url,
                    headers=headers,
                    timeout=180,
                    follow_redirects=False,
                ) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = str(
                            response.headers.get('location') or ''
                        ).strip()
                        if not location:
                            raise RuntimeError(
                                'Gemini video download returned an invalid redirect'
                            )
                        current_url = urljoin(current_url, location)
                        validate_gemini_url(current_url)
                        continue
                    response.raise_for_status()
                    write_checked_video(response)
                    partial.replace(output)
                    return str(output)
            raise RuntimeError('Gemini video download redirected too many times')

        if source_provider == 'fal_seedance_2_fast':
            current_url = validate_fal_media_url(url)
            # Fal media carries no application secret. Follow only documented
            # Fal media hosts (and the documented legacy falserverless bucket)
            # while preserving the shared atomic MP4 validation contract.
            for _redirect_count in range(6):
                current_url = validate_fal_media_url(current_url)
                with httpx.stream(
                    'GET',
                    current_url,
                    headers={},
                    timeout=180,
                    follow_redirects=False,
                ) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = str(
                            response.headers.get('location') or ''
                        ).strip()
                        if not location:
                            raise RuntimeError(
                                'Fal video download returned an invalid redirect'
                            )
                        current_url = urljoin(current_url, location)
                        validate_fal_media_url(current_url)
                        continue
                    response.raise_for_status()
                    write_checked_video(response)
                    partial.replace(output)
                    return str(output)
            raise RuntimeError('Fal video download redirected too many times')

        # Runway output URLs carry no application secret. The client may
        # follow their CDN redirects, but the file still uses the same atomic
        # validation contract as Gemini output.
        with httpx.stream(
            'GET',
            url,
            headers={},
            timeout=180,
            follow_redirects=True,
        ) as response:
            response.raise_for_status()
            write_checked_video(response)
        partial.replace(output)
        return str(output)
    except Exception:
        partial.unlink(missing_ok=True)
        raise

