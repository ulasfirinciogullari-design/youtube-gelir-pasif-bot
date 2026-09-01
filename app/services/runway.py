from pathlib import Path
import re
import time
from urllib.parse import urljoin, urlparse

import httpx
from runwayml import BadRequestError, RateLimitError, RunwayML

from app.config import settings


_GEMINI_VIDEO_BASE = 'https://generativelanguage.googleapis.com/v1beta'
_GEMINI_VIDEO_MODEL = 'veo-3.1-lite-generate-preview'
_GEMINI_VIDEO_FAST_MODEL = 'veo-3.1-fast-generate-preview'
_GEMINI_VIDEO_STANDARD_MODEL = 'veo-3.1-generate-preview'
_GEMINI_OPERATION_PATTERN = re.compile(
    r'^(?:models/[A-Za-z0-9._-]+/)?operations/[A-Za-z0-9._~/-]+$'
)
_GEMINI_VIDEO_HOSTS = {
    'generativelanguage.googleapis.com',
    'storage.googleapis.com',
}
_MAX_GENERATED_VIDEO_BYTES = 100 * 1024 * 1024


class GeminiVideoTerminalError(RuntimeError):
    """A definitive completed-operation rejection that is safe to resubmit."""


class GeminiVideoQuotaError(RuntimeError):
    """A definitive model-create quota rejection with no accepted operation."""


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


def generate_scene(prompt: str, duration: int = 5) -> dict:
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
        created = _create_text_to_video_task(
            create_client,
            prompt_text,
            seconds,
        )
    except BadRequestError:
        # This catch deliberately covers only paid task creation. Once Runway
        # has accepted a task, no polling or download error may start a second
        # paid generation with another provider.
        def generate_with_gemini_model(
            model_name: str | None = None,
        ) -> tuple[str, int]:
            provider_attempts = 1
            call_args = (
                (prompt_text, seconds)
                if model_name is None
                else (prompt_text, seconds, model_name)
            )
            try:
                return _generate_gemini_video_uri(*call_args), provider_attempts
            except GeminiVideoTerminalError:
                # The provider explicitly completed the operation with an
                # error, so a single resubmission is not ambiguous.
                provider_attempts = 2
                return _generate_gemini_video_uri(*call_args), provider_attempts

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
                video_uri, provider_attempts = generate_with_gemini_model(
                    _GEMINI_VIDEO_STANDARD_MODEL
                )
        return {
            'url': video_uri,
            'provider': provider,
            'provider_attempts': provider_attempts,
            'quota_fallback_from': quota_fallback_from,
        }
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


def download_generated_scene(url: str, output_path: str | Path) -> str:
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

