"""Conservative native Gemini 3.7 Flash reservations for Short audio review.

Standard list prices reviewed 2026-09-09: $0.75/M input and $3.75/M output,
including thinking. Reserve the FULL 1,048,576-token input context, covering
audio, complete prompt/schema and provider framing. Do not infer a complete
input bound from the documented 32 audio tokens/second. The Gemini 3.0+
maxOutputTokens pool bounds thinking and visible output together; applying
that generation setting to audio input uses the same native request contract.
At 8192 output tokens this is $0.817152 reserved, not measured usage or a bill.

https://ai.google.dev/gemini-api/docs/models/gemini-3.7-flash
https://ai.google.dev/gemini-api/docs/pricing#gemini-3.7-flash
https://ai.google.dev/gemini-api/docs/audio#technical-details-about-audio
https://ai.google.dev/api/generate-content#GenerationConfig
https://codelabs.developers.google.com/bigquery-generative-ai-intro#3

This does not commission funding, select a model, change a rubric or transmit
anything. Existing Gemini 3.1 and video revisions stay independent.
"""
import base64
import binascii
from copy import deepcopy
from datetime import datetime, timezone
import json
import math

from app.services.production_spend import SpendBlocked, SpendQuote
from app.services.whisper_transcription import (
    WHISPER_MAX_AUDIO_BYTES,
    inspect_bounded_short_audio,
)


GEMINI37_AUDIO_MODEL = 'gemini-3.7-flash'
GEMINI37_AUDIO_ROUTE = (
    'https://generativelanguage.googleapis.com/v1beta/models/'
    + GEMINI37_AUDIO_MODEL + ':generateContent'
)
GEMINI37_AUDIO_PRICE_REVISION = 'gemini37-audio-2026-09-09-v1'
GEMINI37_AUDIO_INPUT_TOKENS = 1_048_576
GEMINI37_AUDIO_MAX_OUTPUT_TOKENS = 8192
GEMINI37_AUDIO_MAX_METADATA_BYTES = 100_000
_MAX_BASE64_BYTES = 4 * ((WHISPER_MAX_AUDIO_BYTES + 2) // 3)
_INPUT_RESERVATION_MICRO = 786_432
_VALID_FROM = datetime(2026, 9, 9, tzinfo=timezone.utc)
_VALID_UNTIL = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _require(condition, code='spend_request_not_priced'):
    if not condition:
        raise SpendBlocked(code)


def _json_snapshot(body):
    """Reject Python coercion and bound the complete metadata before decoding."""
    remaining = 20_000
    remaining_bytes = _MAX_BASE64_BYTES + GEMINI37_AUDIO_MAX_METADATA_BYTES

    def text_bytes(value):
        nonlocal remaining_bytes
        _require(len(value) <= remaining_bytes)
        remaining_bytes -= len(value.encode('utf-8', errors='strict'))
        _require(remaining_bytes >= 0)

    def walk(value, depth=0):
        nonlocal remaining
        remaining -= 1
        _require(remaining >= 0 and depth <= 48)
        if type(value) is dict:
            for key, item in value.items():
                _require(type(key) is str)
                text_bytes(key)
                walk(item, depth + 1)
        elif type(value) is list:
            for item in value:
                walk(item, depth + 1)
        elif type(value) is str:
            text_bytes(value)
        elif type(value) is float:
            _require(math.isfinite(value))
        else:
            _require(type(value) in (int, bool, type(None)))

    walk(body)
    return deepcopy(body)


def _text(part):
    _require(type(part) is dict and set(part) == {'text'}
             and type(part['text']) is str and bool(part['text'].strip()))


def inspect_gemini37_audio_request(body):
    """Return a detached complete native request and original audio proof."""
    try:
        _require(type(body) is dict and set(body) == {
            'store', 'contents', 'generationConfig', 'systemInstruction'}
            and body['store'] is False)
        body = _json_snapshot(body)
        contents = body['contents']
        _require(type(contents) is list and len(contents) == 1
                 and type(contents[0]) is dict and set(contents[0]) == {'role', 'parts'}
                 and contents[0]['role'] == 'user')
        parts = contents[0]['parts']
        _require(type(parts) is list and len(parts) == 2)
        _text(parts[0])
        _require(type(parts[1]) is dict and set(parts[1]) == {'inlineData'})
        inline = parts[1]['inlineData']
        _require(type(inline) is dict and set(inline) == {'mimeType', 'data'}
                 and type(inline['mimeType']) is str
                 and inline['mimeType'] in {'audio/mpeg', 'audio/wav'})
        encoded_audio = inline['data']
        _require(type(encoded_audio) is str and 0 < len(encoded_audio) <= _MAX_BASE64_BYTES)
        system = body['systemInstruction']
        _require(type(system) is dict and set(system) == {'parts'}
                 and type(system['parts']) is list and len(system['parts']) == 1)
        _text(system['parts'][0])
        config = body['generationConfig']
        _require(type(config) is dict and set(config) == {
            'candidateCount', 'thinkingConfig', 'responseMimeType',
            'maxOutputTokens', 'responseJsonSchema'})
        _require(type(config['candidateCount']) is int and config['candidateCount'] == 1
                 and config['responseMimeType'] == 'application/json')
        output = config['maxOutputTokens']
        _require(type(output) is int and 1 <= output <= GEMINI37_AUDIO_MAX_OUTPUT_TOKENS)
        thinking = config['thinkingConfig']
        _require(type(thinking) is dict and set(thinking) == {'thinkingLevel'}
                 and thinking['thinkingLevel'] == 'medium')
        from app.services.production_spend_quotes import _bounded_json_schema

        schema = config['responseJsonSchema']
        _require(type(schema) is dict and schema.get('type') == 'object')
        _bounded_json_schema(schema)
        # Exclude only base64 audio from the metadata bound. All text, schema,
        # field names and native settings are preserved and counted in full.
        metadata = deepcopy(body)
        metadata['contents'][0]['parts'][1]['inlineData']['data'] = ''
        size = len(json.dumps(metadata, ensure_ascii=True, allow_nan=False).encode('ascii'))
        _require(size <= GEMINI37_AUDIO_MAX_METADATA_BYTES)
        raw = base64.b64decode(encoded_audio, validate=True)
        _require(0 < len(raw) <= WHISPER_MAX_AUDIO_BYTES
                 and base64.b64encode(raw).decode('ascii') == encoded_audio)
        audio = inspect_bounded_short_audio(raw, inline['mimeType'])
        return {'body': body, 'input_tokens_upper_bound': GEMINI37_AUDIO_INPUT_TOKENS,
                'max_output_tokens': output, 'metadata_bytes': size, 'audio': audio}
    except SpendBlocked:
        raise
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError,
            binascii.Error):
        raise SpendBlocked('spend_request_not_priced') from None


def _headers(headers):
    _require(type(headers) is dict and len(headers) == 2 and all(
        type(key) is str and type(value) is str for key, value in headers.items()))
    normalized = {key.lower(): value for key, value in headers.items()}
    _require(set(normalized) == {'x-goog-api-key', 'content-type'}
             and normalized['content-type'] == 'application/json')
    key = normalized['x-goog-api-key']
    _require(1 <= len(key) <= 8192 and all(32 < ord(char) < 127 for char in key))


def quote_gemini37_audio_request(body, headers, *, now=None):
    """An expiring full-context reservation; account funding is checked later."""
    try:
        now = datetime.now(timezone.utc) if now is None else now
        _require(isinstance(now, datetime) and now.tzinfo is not None
                 and now.utcoffset() is not None, 'spend_price_review_expired')
        _require(_VALID_FROM <= now.astimezone(timezone.utc) < _VALID_UNTIL,
                 'spend_price_review_expired')
    except (TypeError, ValueError, OverflowError):
        raise SpendBlocked('spend_price_review_expired') from None
    _headers(headers)
    inspected = inspect_gemini37_audio_request(body)
    return SpendQuote('gemini', GEMINI37_AUDIO_MODEL,
                      _INPUT_RESERVATION_MICRO + (inspected['max_output_tokens'] * 15 + 3) // 4,
                      GEMINI37_AUDIO_PRICE_REVISION).validate()
