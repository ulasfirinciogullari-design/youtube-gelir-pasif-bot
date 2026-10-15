"""Conservative reservations for one native Gemini 3.7 Flash text JSON call.

This reserves the documented FULL 1,048,576-token input context, including
prompt, complete system instructions, schema and provider framing. It is not
measured usage, an expected bill, a free-tier assertion or funding approval.
Standard list rates reviewed 2026-09-09 are $0.75/M input and $3.75/M output
including thinking. The existing native maxOutputTokens pool bounds thinking
and visible output together; this scope allows at most 16,384 output tokens.

Sources:
https://ai.google.dev/gemini-api/docs/models/gemini-3.7-flash
https://ai.google.dev/gemini-api/docs/pricing#gemini-3.7-flash
https://ai.google.dev/api/generate-content#GenerationConfig
https://codelabs.developers.google.com/bigquery-generative-ai-intro#3

Only the caller's exact native route may select this model. No network,
credential storage, reservation, retry, model alias or price fallback exists
here. Existing Gemini 3.1 pricing and schema validation remain unchanged.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
import math

from app.services.production_spend import SpendBlocked, SpendQuote


GEMINI37_TEXT_MODEL = 'gemini-3.7-flash'
GEMINI37_TEXT_ROUTE = (
    'https://generativelanguage.googleapis.com/v1beta/models/'
    + GEMINI37_TEXT_MODEL + ':generateContent'
)
GEMINI37_TEXT_PRICE_REVISION = 'gemini37-text-2026-09-09-v1'
GEMINI37_TEXT_INPUT_TOKENS = 1_048_576
GEMINI37_TEXT_MAX_OUTPUT_TOKENS = 16_384
GEMINI37_TEXT_MAX_METADATA_BYTES = 100_000
_INPUT_RESERVATION_MICRO = 786_432
_VALID_FROM = datetime(2026, 9, 9, tzinfo=timezone.utc)
_VALID_UNTIL = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _require(condition, code='spend_request_not_priced'):
    if not condition:
        raise SpendBlocked(code)


def _json_snapshot(body):
    """Reject coercion/nonfinite text before copying the complete private body."""
    remaining = 20_000
    remaining_characters = GEMINI37_TEXT_MAX_METADATA_BYTES

    def walk(value, depth=0):
        nonlocal remaining, remaining_characters
        remaining -= 1
        _require(remaining >= 0 and depth <= 48)
        if type(value) is dict:
            for key, item in value.items():
                _require(type(key) is str)
                remaining_characters -= len(key)
                _require(remaining_characters >= 0)
                key.encode('utf-8', errors='strict')
                walk(item, depth + 1)
        elif type(value) is list:
            for item in value:
                walk(item, depth + 1)
        elif type(value) is str:
            remaining_characters -= len(value)
            _require(remaining_characters >= 0)
            value.encode('utf-8', errors='strict')
        elif type(value) is float:
            _require(math.isfinite(value))
        else:
            _require(type(value) in (int, bool, type(None)))

    try:
        walk(body)
        # ASCII JSON overcounts non-ASCII wire bytes; it never drops or trims
        # any supplied prompt/schema. Input pricing uses the full model limit.
        encoded = json.dumps(body, ensure_ascii=True, allow_nan=False).encode('ascii')
        _require(len(encoded) <= GEMINI37_TEXT_MAX_METADATA_BYTES)
        return deepcopy(body), len(encoded)
    except SpendBlocked:
        raise
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError):
        raise SpendBlocked('spend_request_not_priced') from None


def _text_part(parts):
    _require(type(parts) is list and len(parts) == 1
             and type(parts[0]) is dict and set(parts[0]) == {'text'}
             and type(parts[0]['text']) is str and bool(parts[0]['text'].strip()))


def inspect_gemini37_text_request(body):
    """Return an exact detached request and bounds; never grants a paid call."""
    _require(type(body) is dict and set(body) in (
        {'store', 'contents', 'generationConfig'},
        {'store', 'contents', 'generationConfig', 'systemInstruction'},
    ) and body['store'] is False)
    body, size = _json_snapshot(body)
    contents = body['contents']
    _require(type(contents) is list and len(contents) == 1
             and type(contents[0]) is dict and set(contents[0]) == {'role', 'parts'}
             and contents[0]['role'] == 'user')
    _text_part(contents[0]['parts'])
    if 'systemInstruction' in body:
        system = body['systemInstruction']
        _require(type(system) is dict and set(system) == {'parts'})
        _text_part(system['parts'])
    config = body['generationConfig']
    required = {'candidateCount', 'thinkingConfig', 'responseMimeType', 'maxOutputTokens'}
    _require(type(config) is dict and set(config) in (required, required | {'responseJsonSchema'}))
    _require(type(config['candidateCount']) is int and config['candidateCount'] == 1
             and config['responseMimeType'] == 'application/json')
    output = config['maxOutputTokens']
    _require(type(output) is int and 1 <= output <= GEMINI37_TEXT_MAX_OUTPUT_TOKENS)
    thinking = config['thinkingConfig']
    _require(type(thinking) is dict and set(thinking) == {'thinkingLevel'}
             and type(thinking['thinkingLevel']) is str
             and thinking['thinkingLevel'] in {'low', 'medium', 'high'})
    if 'responseJsonSchema' in config:
        from app.services.production_spend_quotes import _bounded_json_schema

        schema = config['responseJsonSchema']
        _require(type(schema) is dict and schema.get('type') == 'object')
        _bounded_json_schema(schema)
    return {'body': body, 'input_tokens_upper_bound': GEMINI37_TEXT_INPUT_TOKENS,
            'max_output_tokens': output, 'metadata_bytes': size}


def _headers(headers):
    _require(type(headers) is dict and len(headers) == 2
             and all(type(key) is str and type(value) is str for key, value in headers.items()))
    lowered = {key.lower(): value for key, value in headers.items()}
    _require(set(lowered) == {'x-goog-api-key', 'content-type'}
             and lowered['content-type'] == 'application/json')
    key = lowered['x-goog-api-key']
    _require(1 <= len(key) <= 8192 and all(32 < ord(char) < 127 for char in key))


def quote_gemini37_text_request(body, headers, *, now=None):
    """Fixed-model standard list upper bound, independent of existing credits."""
    try:
        now = datetime.now(timezone.utc) if now is None else now
        _require(isinstance(now, datetime) and now.tzinfo is not None
                 and now.utcoffset() is not None, 'spend_price_review_expired')
        _require(_VALID_FROM <= now.astimezone(timezone.utc) < _VALID_UNTIL,
                 'spend_price_review_expired')
    except (TypeError, ValueError, OverflowError):
        raise SpendBlocked('spend_price_review_expired') from None
    _headers(headers)
    inspected = inspect_gemini37_text_request(body)
    output_micro = (inspected['max_output_tokens'] * 15 + 3) // 4
    return SpendQuote('gemini', GEMINI37_TEXT_MODEL,
                      _INPUT_RESERVATION_MICRO + output_micro,
                      GEMINI37_TEXT_PRICE_REVISION).validate()
