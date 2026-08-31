import base64
import json
import re
from typing import Any

import httpx


GEMINI_DEFAULT_MODEL = 'gemini-3.1-pro-preview'
_GEMINI_ENDPOINT = (
    'https://generativelanguage.googleapis.com/v1beta/models/'
    '{model}:generateContent'
)
_MODEL_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$')
_THINKING_LEVELS = {'low', 'medium', 'high'}
_DEFAULT_TIMEOUT = httpx.Timeout(90.0, connect=10.0)
_MAX_MULTIMODAL_PARTS = 256
_MAX_MULTIMODAL_IMAGES = 120
_MAX_IMAGE_BYTES = 2 * 1024 * 1024
_MAX_TOTAL_IMAGE_BYTES = 12 * 1024 * 1024
_MAX_TOTAL_TEXT_CHARS = 120_000
_MAX_SYSTEM_INSTRUCTION_CHARS = 20_000
_NETWORK_ERROR_TYPES = tuple(
    error_type
    for error_type in (
        getattr(httpx, 'RequestError', None),
        OSError,
        TimeoutError,
    )
    if isinstance(error_type, type)
)


class GeminiGenerationError(RuntimeError):
    """Gemini could not produce one safe, valid JSON object."""


class GeminiProtocolError(GeminiGenerationError):
    """Gemini responded, but its output did not satisfy the JSON contract."""


def _safe_json_schema(json_schema: Any) -> dict | None:
    if json_schema is None:
        return None
    if not isinstance(json_schema, dict):
        raise GeminiGenerationError('Gemini JSON schema is invalid')
    if json_schema.get('type') not in (None, 'object'):
        raise GeminiGenerationError('Gemini JSON schema must describe an object')
    try:
        # Round-tripping both proves that the schema is JSON-safe and prevents a
        # caller from mutating the body while the request is in flight.
        return json.loads(
            json.dumps(json_schema, ensure_ascii=False, allow_nan=False)
        )
    except Exception:
        raise GeminiGenerationError('Gemini JSON schema is invalid') from None


def _blocked_by_safety(payload: dict, candidate: dict) -> bool:
    prompt_feedback = payload.get('promptFeedback')
    if prompt_feedback is not None and not isinstance(prompt_feedback, dict):
        raise GeminiGenerationError(
            'Gemini returned malformed safety metadata'
        )
    if isinstance(prompt_feedback, dict):
        block_reason = prompt_feedback.get('blockReason')
        if block_reason not in (None, '', 'BLOCK_REASON_UNSPECIFIED'):
            return True
        prompt_ratings = prompt_feedback.get('safetyRatings')
        if prompt_ratings is not None and not isinstance(prompt_ratings, list):
            raise GeminiGenerationError(
                'Gemini returned malformed safety metadata'
            )
        if any(not isinstance(rating, dict) for rating in (prompt_ratings or [])):
            raise GeminiGenerationError(
                'Gemini returned malformed safety metadata'
            )
        if any(
            'blocked' in rating and type(rating.get('blocked')) is not bool
            for rating in (prompt_ratings or [])
        ):
            raise GeminiGenerationError(
                'Gemini returned malformed safety metadata'
            )
        if any(
            rating.get('blocked') is True
            for rating in (prompt_ratings or [])
        ):
            return True
    candidate_ratings = candidate.get('safetyRatings')
    if candidate_ratings is not None and not isinstance(candidate_ratings, list):
        raise GeminiGenerationError(
            'Gemini returned malformed safety metadata'
        )
    if any(not isinstance(rating, dict) for rating in (candidate_ratings or [])):
        raise GeminiGenerationError(
            'Gemini returned malformed safety metadata'
        )
    if any(
        'blocked' in rating and type(rating.get('blocked')) is not bool
        for rating in (candidate_ratings or [])
    ):
        raise GeminiGenerationError(
            'Gemini returned malformed safety metadata'
        )
    return any(
        rating.get('blocked') is True
        for rating in (candidate_ratings or [])
    )


def _extract_output_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise GeminiProtocolError('Gemini returned an invalid response envelope')
    if _blocked_by_safety(payload, {}):
        raise GeminiGenerationError('Gemini response was blocked for safety')
    candidates = payload.get('candidates')
    if not isinstance(candidates, list) or len(candidates) != 1:
        raise GeminiProtocolError('Gemini returned an invalid candidate count')
    candidate = candidates[0]
    if not isinstance(candidate, dict):
        raise GeminiProtocolError('Gemini returned an invalid candidate')
    if _blocked_by_safety(payload, candidate):
        raise GeminiGenerationError('Gemini response was blocked for safety')
    finish_reason = candidate.get('finishReason')
    if not isinstance(finish_reason, str) or not finish_reason.strip():
        raise GeminiProtocolError('Gemini returned an invalid finish reason')
    if finish_reason != 'STOP':
        raise GeminiGenerationError('Gemini response did not finish safely')

    content = candidate.get('content')
    parts = content.get('parts') if isinstance(content, dict) else None
    if not isinstance(parts, list) or not parts:
        raise GeminiProtocolError('Gemini response omitted its output')
    texts = [
        part.get('text').strip()
        for part in parts
        if (
            isinstance(part, dict)
            and part.get('thought') is not True
            and isinstance(part.get('text'), str)
            and part.get('text').strip()
        )
    ]
    if len(texts) != 1:
        raise GeminiProtocolError('Gemini returned ambiguous output')
    return texts[0]


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict:
    parsed: dict[str, Any] = {}
    for key, value in pairs:
        if key in parsed:
            raise ValueError('duplicate key')
        parsed[key] = value
    return parsed


def _reject_non_finite(_: str) -> None:
    raise ValueError('non-finite number')


def _matches_type(value: Any, expected_type: str) -> bool:
    return {
        'object': isinstance(value, dict),
        'array': isinstance(value, list),
        'string': isinstance(value, str),
        'integer': type(value) is int,
        'number': type(value) in (int, float),
        'boolean': type(value) is bool,
        'null': value is None,
    }.get(expected_type, True)


def _matches_schema(value: Any, schema: Any) -> bool:
    """Validate the common JSON-Schema subset used by generation contracts."""
    if not isinstance(schema, dict):
        return False
    expected_type = schema.get('type')
    if isinstance(expected_type, str) and not _matches_type(value, expected_type):
        return False
    if isinstance(expected_type, list):
        if not expected_type or not all(
            isinstance(item, str) for item in expected_type
        ):
            return False
        if not any(_matches_type(value, item) for item in expected_type):
            return False
    if 'enum' in schema:
        enum = schema['enum']
        if not isinstance(enum, list) or value not in enum:
            return False

    if isinstance(value, dict):
        properties = schema.get('properties', {})
        required = schema.get('required', [])
        if not isinstance(properties, dict) or not isinstance(required, list):
            return False
        if not all(isinstance(key, str) and key in value for key in required):
            return False
        if schema.get('additionalProperties') is False:
            if any(key not in properties for key in value):
                return False
        for key, child_schema in properties.items():
            if key in value and not _matches_schema(value[key], child_schema):
                return False

    if isinstance(value, list):
        min_items = schema.get('minItems')
        max_items = schema.get('maxItems')
        if type(min_items) is int and len(value) < min_items:
            return False
        if type(max_items) is int and len(value) > max_items:
            return False
        items = schema.get('items')
        if items is not None and any(
            not _matches_schema(item, items) for item in value
        ):
            return False

    if isinstance(value, str):
        min_length = schema.get('minLength')
        max_length = schema.get('maxLength')
        if type(min_length) is int and len(value) < min_length:
            return False
        if type(max_length) is int and len(value) > max_length:
            return False

    if type(value) in (int, float):
        minimum = schema.get('minimum')
        maximum = schema.get('maximum')
        if type(minimum) in (int, float) and value < minimum:
            return False
        if type(maximum) in (int, float) and value > maximum:
            return False
    return True


def _image_mime_type(image_bytes: bytes) -> str | None:
    if image_bytes.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if image_bytes.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if (
        len(image_bytes) >= 12
        and image_bytes.startswith(b'RIFF')
        and image_bytes[8:12] == b'WEBP'
    ):
        return 'image/webp'
    return None


def _safe_multimodal_parts(parts: Any) -> list[dict]:
    """Convert ordered text/image-byte parts to Gemini native REST parts."""
    if not isinstance(parts, list) or not parts:
        raise GeminiGenerationError('Gemini multimodal parts are invalid')
    if len(parts) > _MAX_MULTIMODAL_PARTS:
        raise GeminiGenerationError('Gemini multimodal part limit was exceeded')

    safe_parts: list[dict] = []
    image_count = 0
    total_image_bytes = 0
    total_text_chars = 0
    for part in parts:
        if not isinstance(part, dict):
            raise GeminiGenerationError('Gemini multimodal part is invalid')
        if set(part) == {'text'}:
            text = part.get('text')
            if not isinstance(text, str) or not text.strip():
                raise GeminiGenerationError('Gemini multimodal text is invalid')
            total_text_chars += len(text)
            if total_text_chars > _MAX_TOTAL_TEXT_CHARS:
                raise GeminiGenerationError(
                    'Gemini multimodal text limit was exceeded'
                )
            safe_parts.append({'text': text})
            continue
        if set(part) != {'image_bytes'}:
            raise GeminiGenerationError('Gemini multimodal part is invalid')
        image_bytes = part.get('image_bytes')
        if type(image_bytes) is not bytes or not image_bytes:
            raise GeminiGenerationError('Gemini image bytes are invalid')
        if len(image_bytes) > _MAX_IMAGE_BYTES:
            raise GeminiGenerationError('Gemini image size limit was exceeded')
        mime_type = _image_mime_type(image_bytes)
        if mime_type is None:
            raise GeminiGenerationError('Gemini image format is invalid')
        image_count += 1
        total_image_bytes += len(image_bytes)
        if image_count > _MAX_MULTIMODAL_IMAGES:
            raise GeminiGenerationError('Gemini image count limit was exceeded')
        if total_image_bytes > _MAX_TOTAL_IMAGE_BYTES:
            raise GeminiGenerationError(
                'Gemini total image size limit was exceeded'
            )
        safe_parts.append({
            'inlineData': {
                'mimeType': mime_type,
                'data': base64.b64encode(image_bytes).decode('ascii'),
            },
        })
    if image_count == 0:
        raise GeminiGenerationError('Gemini multimodal input requires an image')
    return safe_parts


def _safe_system_instruction(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise GeminiGenerationError('Gemini system instruction is invalid')
    if len(value) > _MAX_SYSTEM_INSTRUCTION_CHARS:
        raise GeminiGenerationError(
            'Gemini system instruction limit was exceeded'
        )
    return value


def _decode_gemini_json_response(
    response: Any,
    safe_schema: dict | None,
) -> dict:
    try:
        response_payload = response.json()
        output_text = _extract_output_text(response_payload)
        output = json.loads(
            output_text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except GeminiGenerationError:
        raise
    except Exception:
        raise GeminiProtocolError('Gemini returned invalid JSON') from None
    if not isinstance(output, dict):
        raise GeminiProtocolError('Gemini JSON output is not an object')
    if safe_schema is not None and not _matches_schema(output, safe_schema):
        raise GeminiProtocolError('Gemini JSON output violated its schema')
    return output


def _generate_gemini_json_from_parts(
    parts: list[dict],
    *,
    api_key: str,
    model: str,
    json_schema: dict | None,
    google_search: bool,
    thinking_level: str,
    timeout: Any,
    retry_once: bool,
    system_instruction: str | None = None,
) -> dict:
    clean_api_key = str(api_key or '').strip()
    clean_model = str(model or '').strip()
    clean_thinking_level = str(thinking_level or '').strip().casefold()
    if not clean_api_key:
        raise GeminiGenerationError('GEMINI_API_KEY is required')
    if not _MODEL_PATTERN.fullmatch(clean_model):
        raise GeminiGenerationError('GEMINI_MODEL is invalid')
    if clean_thinking_level not in _THINKING_LEVELS:
        raise GeminiGenerationError('Gemini thinking level is invalid')
    if type(google_search) is not bool:
        raise GeminiGenerationError('Gemini Google Search option is invalid')
    if type(retry_once) is not bool:
        raise GeminiGenerationError('Gemini retry option is invalid')

    safe_schema = _safe_json_schema(json_schema)
    generation_config: dict[str, Any] = {
        'candidateCount': 1,
        'thinkingConfig': {'thinkingLevel': clean_thinking_level},
        'responseMimeType': 'application/json',
    }
    if safe_schema is not None:
        generation_config['responseJsonSchema'] = safe_schema
    request_body: dict[str, Any] = {
        'store': False,
        'contents': [{'role': 'user', 'parts': parts}],
        'generationConfig': generation_config,
    }
    if system_instruction is not None:
        request_body['systemInstruction'] = {
            'parts': [{'text': system_instruction}],
        }
    if google_search:
        request_body['tools'] = [{'google_search': {}}]

    response = None
    attempts = 2 if retry_once else 1
    for attempt in range(attempts):
        try:
            response = httpx.post(
                _GEMINI_ENDPOINT.format(model=clean_model),
                headers={
                    'x-goog-api-key': clean_api_key,
                    'Content-Type': 'application/json',
                },
                json=request_body,
                timeout=timeout,
            )
        except _NETWORK_ERROR_TYPES:
            if attempt + 1 < attempts:
                continue
            raise GeminiGenerationError(
                'Gemini generation request failed'
            ) from None
        except Exception:
            raise GeminiGenerationError(
                'Gemini generation request failed'
            ) from None

        status_code = getattr(response, 'status_code', None)
        retryable_status = (
            status_code == 429
            or (type(status_code) is int and 500 <= status_code <= 599)
        )
        if retryable_status and attempt + 1 < attempts:
            continue
        if type(status_code) is not int or not 200 <= status_code < 300:
            raise GeminiGenerationError('Gemini generation request was rejected')
        try:
            return _decode_gemini_json_response(response, safe_schema)
        except GeminiProtocolError:
            if attempt + 1 < attempts:
                continue
            raise

    raise GeminiGenerationError('Gemini generation request failed')


def generate_gemini_json(
    prompt: str,
    *,
    api_key: str,
    model: str = GEMINI_DEFAULT_MODEL,
    json_schema: dict | None = None,
    google_search: bool = False,
    thinking_level: str = 'medium',
    timeout: Any = _DEFAULT_TIMEOUT,
    retry_once: bool = True,
) -> dict:
    """Generate exactly one JSON object through Gemini's native REST API."""
    clean_prompt = str(prompt or '').strip()
    if not clean_prompt:
        raise GeminiGenerationError('Gemini prompt is required')
    return _generate_gemini_json_from_parts(
        [{'text': clean_prompt}],
        api_key=api_key,
        model=model,
        json_schema=json_schema,
        google_search=google_search,
        thinking_level=thinking_level,
        timeout=timeout,
        retry_once=retry_once,
        system_instruction=None,
    )


def generate_gemini_multimodal_json(
    parts: list[dict],
    *,
    api_key: str,
    model: str = GEMINI_DEFAULT_MODEL,
    json_schema: dict | None = None,
    thinking_level: str = 'low',
    timeout: Any = _DEFAULT_TIMEOUT,
    retry_once: bool = True,
    system_instruction: str | None = None,
) -> dict:
    """Generate JSON from ordered text and locally-read image byte parts."""
    safe_parts = _safe_multimodal_parts(parts)
    safe_system_instruction = _safe_system_instruction(system_instruction)
    return _generate_gemini_json_from_parts(
        safe_parts,
        api_key=api_key,
        model=model,
        json_schema=json_schema,
        google_search=False,
        thinking_level=thinking_level,
        timeout=timeout,
        retry_once=retry_once,
        system_instruction=safe_system_instruction,
    )
