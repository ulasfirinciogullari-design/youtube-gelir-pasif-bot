"""Small reviewed price catalog. Unknown request shapes NEVER mean free.

USD list rates checked 2026-09-08. Quotes expire at the UTC month boundary.
Not an invoice: prepaid credits/discounts are not deducted a second time.
Sources and intentionally blocked routes are recorded in the rollout doc.
"""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
import re
from urllib.parse import urlsplit

from app.config import settings
from app.services.production_spend import SpendBlocked, SpendQuote, usd_micro

_REVISION = 'official-2026-09-08-v3'
OPENAI_TEXT_PRICE_REVISION = 'openai-text-2026-09-09-v1'
OPENAI_SERIES_PRICE_REVISION = 'openai-series-2026-09-20-v1'
_VALID_FROM = datetime(2026, 9, 8, tzinfo=timezone.utc)
_VALID_UNTIL = datetime(2026, 10, 1, tzinfo=timezone.utc)

# GET https://routellm.abacus.ai/v1/models uses USD PER TOKEN. The public
# https://routellm-apis.abacus.ai/ table displays USD PER MILLION tokens.
# Do not apply the unit of the older listRouteLLMModels example to this route.
ABACUS_TEXT_RATES_PER_TOKEN = {
    'claude-haiku-4-5-20251001': (Decimal('0.000001'), Decimal('0.000005')),
    'claude-sonnet-5': (Decimal('0.000002'), Decimal('0.00001')),
    'claude-sonnet-4-6': (Decimal('0.000003'), Decimal('0.000015')),
}
ABACUS_INPUT_FRAMING_TOKENS = 4096
ABACUS_MAX_REQUEST_BYTES = 100_000
ABACUS_MAX_OUTPUT_TOKENS = 8192
ABACUS_GLOBAL_GEO_MODELS = frozenset({'claude-sonnet-4-6', 'claude-sonnet-5'})


def _require(condition, reason='spend_request_not_priced'):
    if not condition:
        raise SpendBlocked(reason)


def _fresh():
    _require(_VALID_FROM <= datetime.now(timezone.utc) < _VALID_UNTIL,
             'spend_price_review_expired')


def _integer(value, low, high):
    _require(type(value) is int and low <= value <= high)
    return value


def _encoded_size(value):
    try:
        return len(json.dumps(value, ensure_ascii=True, allow_nan=False).encode())
    except (TypeError, ValueError, RecursionError):
        raise SpendBlocked('spend_request_not_priced') from None


def _quote(provider, model, amount):
    _fresh()
    return SpendQuote(provider, model, usd_micro(amount), _REVISION)


def _text_bytes(value, maximum=100_000):
    _require(type(value) is str and bool(value.strip()))
    size = len(value.encode('utf-8'))
    _require(size <= maximum)
    return size


def quote_runway_video(body):
    _require(type(body) is dict and set(body) <= {'model', 'prompt_text', 'ratio', 'duration', 'audio'})
    _text_bytes(body.get('prompt_text'), 8000)
    _require(type(body.get('ratio')) is str and body['ratio'] in {'1280:720', '720:1280'})
    _require(body.get('audio', False) is False)
    seconds = _integer(body.get('duration'), 2, 10)
    model = body.get('model')
    rates = {'gen4.5': '0.12', 'seedance2_fast': '0.29'}
    _require(type(model) is str and model in rates)
    return _quote('runway', model, Decimal(rates[model]) * seconds)


def _veo(model, body):
    _require(type(body) is dict and set(body) == {'instances', 'parameters'})
    instances, parameters = body['instances'], body['parameters']
    _require(type(instances) is list and len(instances) == 1
             and type(instances[0]) is dict and set(instances[0]) == {'prompt'})
    _text_bytes(instances[0]['prompt'], 8000)
    _require(type(parameters) is dict and set(parameters) <= {
        'durationSeconds', 'aspectRatio', 'resolution', 'sampleCount'})
    _require(parameters.get('sampleCount', 1) == 1
             and type(parameters.get('sampleCount', 1)) is int)
    _require(type(parameters.get('aspectRatio')) is str and parameters['aspectRatio'] in {'9:16', '16:9'})
    _require(type(parameters.get('resolution')) is str)
    seconds = _integer(parameters.get('durationSeconds'), 4, 8)
    _require(seconds in {4, 6, 8})
    rates = {
        'veo-3.1-lite-generate-preview': {'720p': '0.05', '1080p': '0.08'},
        'veo-3.1-fast-generate-preview': {'720p': '0.10', '1080p': '0.12'},
        'veo-3.1-generate-preview': {'720p': '0.40', '1080p': '0.40'},
    }
    rate = rates.get(model, {}).get(parameters.get('resolution'))
    _require(rate is not None)
    return _quote('gemini', model, Decimal(rate) * seconds)


def describe_video_request(provider, operation, payload, quote):
    """Bind the price to the actual reviewed video shape, never a prompt hint."""
    if provider == 'runway' and operation == 'text_to_video':
        checked = quote_runway_video(payload)
        descriptor = {
            'provider': provider, 'model': checked.model,
            'duration_seconds': payload['duration'],
            'aspect_ratio': '9:16' if payload['ratio'] == '720:1280' else '16:9',
            'resolution': '720p', 'audio': False, 'sample_count': 1,
            'price_revision': checked.price_revision,
        }
    elif provider == 'gemini' and type(operation) is str and (match := re.fullmatch(
            r'/v1beta/models/([A-Za-z0-9._-]+):predictLongRunning', operation)):
        checked = _veo(match[1], payload)
        params = payload['parameters']
        descriptor = {
            'provider': provider, 'model': checked.model,
            'duration_seconds': params['durationSeconds'],
            'aspect_ratio': params['aspectRatio'], 'resolution': params['resolution'],
            'audio': True, 'sample_count': 1, 'price_revision': checked.price_revision,
        }
    else:
        return None  # Text/voice review does not consume a video-scene allowance.
    _require(checked == quote, 'spend_quote_binding_invalid')
    return descriptor


def _bounded_json_schema(schema, depth=0):
    """Inline schema subset used by planning; no reference expansion/history."""
    _require(depth <= 20 and type(schema) is dict and set(schema) <= {
        'type', 'properties', 'required', 'items', 'enum', 'additionalProperties',
        'minItems', 'maxItems', 'minLength', 'maxLength', 'minimum', 'maximum',
        'description', 'title'})
    types = schema.get('type')
    types = types if type(types) is list else [types]
    _require(bool(types) and all(type(item) is str and item in {
        'object', 'array', 'string', 'number', 'integer', 'boolean', 'null'} for item in types))
    if 'properties' in schema:
        properties = schema['properties']
        _require(type(properties) is dict)
        for name, child in properties.items():
            _text_bytes(name, 1000)
            _bounded_json_schema(child, depth + 1)
    if 'items' in schema:
        _bounded_json_schema(schema['items'], depth + 1)
    if 'required' in schema:
        _require(type(schema['required']) is list and all(
            type(name) is str and name in schema.get('properties', {})
            for name in schema['required']))
    if 'additionalProperties' in schema:
        _require(type(schema['additionalProperties']) is bool)
    if 'enum' in schema:
        _require(type(schema['enum']) is list and bool(schema['enum'])
                 and all(type(item) in (str, int, float, bool, type(None))
                         for item in schema['enum']))
    for key in ('minItems', 'maxItems', 'minLength', 'maxLength'):
        if key in schema:
            _integer(schema[key], 0, 100_000)
    for key in ('minimum', 'maximum'):
        if key in schema:
            _require(type(schema[key]) in (int, float) and math.isfinite(schema[key]))
    for key in ('description', 'title'):
        if key in schema:
            _text_bytes(schema[key], 10_000)


def _gemini_json(model, body, headers):
    """One tool-free, text-only Gemini 3.1 Pro JSON response at standard rates.

    Reviewed sources: https://ai.google.dev/gemini-api/docs/pricing and
    https://ai.google.dev/api/generate-content . Google's Gemini 3.0+ guide in
    https://codelabs.developers.google.com/bigquery-generative-ai-intro#3
    confirms thoughts and visible output share the maxOutputTokens pool.
    """
    _require(model == 'gemini-3.1-pro-preview')
    _require(type(headers) is dict and all(type(key) is str for key in headers))
    names = [key.lower() for key in headers]
    _require(len(set(names)) == len(names) and set(names) <= {
        'x-goog-api-key', 'content-type'})
    _require(type(body) is dict and set(body) <= {
        'store', 'contents', 'systemInstruction', 'generationConfig'})
    _require(body.get('store') is False)
    # Count the full encoded schema/settings too, keeping even the conservative
    # input ceiling below the 200,000-token higher-price threshold.
    size = _encoded_size(body)
    _require(size <= 100_000)
    contents = body.get('contents')
    _require(type(contents) is list and len(contents) == 1
             and type(contents[0]) is dict and set(contents[0]) == {'role', 'parts'}
             and contents[0]['role'] == 'user')
    inputs = [contents[0]]
    if 'systemInstruction' in body:
        system = body['systemInstruction']
        _require(type(system) is dict and set(system) == {'parts'})
        inputs.append(system)
    for content in inputs:
        parts = content['parts']
        _require(type(parts) is list and 1 <= len(parts) <= 32)
        for part in parts:
            _require(type(part) is dict and set(part) == {'text'})
            _text_bytes(part['text'])
    config = body.get('generationConfig')
    _require(type(config) is dict and set(config) <= {
        'candidateCount', 'thinkingConfig', 'responseMimeType',
        'maxOutputTokens', 'responseJsonSchema'})
    _integer(config.get('candidateCount'), 1, 1)
    _require(config.get('responseMimeType') == 'application/json')
    output = _integer(config.get('maxOutputTokens'), 1, 16_384)
    thinking = config.get('thinkingConfig')
    _require(type(thinking) is dict and set(thinking) == {'thinkingLevel'}
             and type(thinking['thinkingLevel']) is str
             and thinking['thinkingLevel'] in {'low', 'medium', 'high'})
    if 'responseJsonSchema' in config:
        _require(type(config['responseJsonSchema']) is dict
                 and config['responseJsonSchema'].get('type') == 'object')
        _bounded_json_schema(config['responseJsonSchema'])
    return _quote('gemini', model,
                  (Decimal(size + 4096) * 2 + Decimal(output) * 12) / 1_000_000)


def _abacus_messages(body, headers):
    """One native Claude text response, with thinking/tools/cache disabled.

    Abacus documents an unchanged Anthropic Messages pass-through:
    https://abacus.ai/help/developer-platform/route-llm/anthropic-messages
    Claude documents disabled thinking on these reviewed models and a strict
    max_tokens ceiling including thinking:
    https://platform.claude.com/docs/en/build-with-claude/thinking
    https://platform.claude.com/docs/en/build-with-claude/thinking-troubleshooting
    Explicit standard tier/global routing prevents workspace defaults from
    selecting other pricing; Haiku rejects geo and always uses standard rates:
    https://platform.claude.com/docs/en/api/service-tiers
    https://platform.claude.com/docs/en/manage-claude/data-residency
    Schemas stay in plain prompt text: output_config can inject billed input.
    """
    _require(type(headers) is dict and all(type(key) is str for key in headers))
    names = [key.lower() for key in headers]
    _require(len(set(names)) == len(names) and set(names) == {
        'x-api-key', 'content-type', 'anthropic-version'})
    normalized = {key.lower(): value for key, value in headers.items()}
    _require(normalized['content-type'] == 'application/json'
             and normalized['anthropic-version'] == '2023-06-01')
    key = normalized['x-api-key']
    _require(type(key) is str and 1 <= len(key) <= 4096
             and all(32 < ord(char) < 127 for char in key))
    _require(type(body) is dict)
    model = body.get('model')
    _require(type(model) is str and model in ABACUS_TEXT_RATES_PER_TOKEN)
    expected = {'model', 'messages', 'system', 'max_tokens', 'thinking', 'stream',
                'service_tier'}
    if model in ABACUS_GLOBAL_GEO_MODELS:
        expected.add('inference_geo')
        _require(body.get('inference_geo') == 'global')
    _require(set(body) == expected and body['service_tier'] == 'standard_only')
    _require(body['stream'] is False and type(body['thinking']) is dict
             and body['thinking'] == {'type': 'disabled'})
    output = _integer(body['max_tokens'], 1, ABACUS_MAX_OUTPUT_TOKENS)
    _text_bytes(body['system'], ABACUS_MAX_REQUEST_BYTES)
    messages = body['messages']
    _require(type(messages) is list and len(messages) == 1
             and type(messages[0]) is dict and set(messages[0]) == {'role', 'content'}
             and messages[0]['role'] == 'user')
    _text_bytes(messages[0]['content'], ABACUS_MAX_REQUEST_BYTES)
    size = _encoded_size(body)
    _require(size <= ABACUS_MAX_REQUEST_BYTES)
    # Full ASCII encoding overcounts the text bytes; framing allowance keeps
    # this reviewed input below the 200k long-context pricing boundary.
    input_rate, output_rate = ABACUS_TEXT_RATES_PER_TOKEN[model]
    return _quote('abacus', model,
                  Decimal(size + ABACUS_INPUT_FRAMING_TOKENS) * input_rate
                  + Decimal(output) * output_rate)


def configured_elevenlabs_pricing(*, now=None):
    """Read explicit operator evidence, never subscription-price arithmetic."""
    from app.services.elevenlabs_spend_quotes import validate_elevenlabs_pricing_evidence
    raw = getattr(settings, 'studio_elevenlabs_pricing_evidence_json', '')
    try:
        _require(type(raw) is str and 0 < len(raw.encode('utf-8')) <= 32 * 1024,
                 'spend_elevenlabs_pricing_uncommissioned')
    except UnicodeError:
        raise SpendBlocked('spend_elevenlabs_pricing_uncommissioned') from None
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate evidence field')
            result[key] = value
        return result
    try:
        evidence = json.loads(raw, object_pairs_hook=unique_object)
    except (ValueError, TypeError, RecursionError):
        raise SpendBlocked('spend_elevenlabs_pricing_uncommissioned') from None
    return validate_elevenlabs_pricing_evidence(
        evidence, now=now or datetime.now(timezone.utc))


def _elevenlabs_quote(url, kwargs):
    from app.services.elevenlabs_spend_quotes import quote_elevenlabs_tts
    now = datetime.now(timezone.utc)
    evidence = configured_elevenlabs_pricing(now=now)
    headers = kwargs.get('headers')
    _require(type(headers) is dict and all(type(key) is str for key in headers))
    keys = [value for key, value in headers.items() if key.lower() == 'xi-api-key']
    _require(len(keys) == 1 and type(keys[0]) is str and 1 <= len(keys[0]) <= 8192
             and all(32 < ord(char) < 127 for char in keys[0]))
    credential = hashlib.sha256(('elevenlabs\0' + keys[0]).encode()).hexdigest()
    return quote_elevenlabs_tts(url, kwargs, evidence=evidence,
                                credential_sha256=credential, now=now)


def quote_http_request(url, kwargs):
    _require(type(url) is str)
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        raise SpendBlocked('spend_request_not_priced') from None
    _require(parsed.scheme == 'https' and not parsed.username and not parsed.password
             and port in (None, 443) and not parsed.query and not parsed.fragment)
    if parsed.hostname == 'api.elevenlabs.io':
        _require(type(kwargs) is dict and set(kwargs) <= {'json', 'headers', 'params', 'timeout'})
        return 'elevenlabs', parsed.path, _elevenlabs_quote(url, kwargs)
    _require(type(kwargs) is dict and set(kwargs) <= {'json', 'headers', 'timeout'})
    if parsed.hostname == 'routellm.abacus.ai' and parsed.path == '/v1/messages':
        body = kwargs.get('json')
        messages = body.get('messages') if type(body) is dict else None
        if (type(messages) is list and len(messages) == 1 and type(messages[0]) is dict
                and type(messages[0].get('content')) is list):
            from app.services.abacus_visual_spend_quotes import quote_abacus_visual_request
            return 'abacus', parsed.path, quote_abacus_visual_request(body, kwargs.get('headers', {}))
        return 'abacus', parsed.path, _abacus_messages(
            kwargs.get('json'), kwargs.get('headers', {}))
    match = re.fullmatch(r'/v1beta/models/([A-Za-z0-9._-]+):predictLongRunning', parsed.path)
    if parsed.hostname == 'generativelanguage.googleapis.com' and match:
        return 'gemini', parsed.path, _veo(match[1], kwargs.get('json'))
    match = re.fullmatch(r'/v1beta/models/([A-Za-z0-9._-]+):generateContent', parsed.path)
    if parsed.hostname == 'generativelanguage.googleapis.com' and match:
        if match[1] == 'gemini-3.7-flash':
            body = kwargs.get('json')
            contents = body.get('contents') if type(body) is dict else None
            _require(type(contents) is list and len(contents) == 1
                     and type(contents[0]) is dict)
            parts = contents[0].get('parts')
            _require(type(parts) is list)
            if len(parts) == 1 and type(parts[0]) is dict and set(parts[0]) == {'text'}:
                from app.services.gemini37_text_spend_quotes import quote_gemini37_text_request
                quote = quote_gemini37_text_request(body, kwargs.get('headers', {}))
            elif (len(parts) == 2 and type(parts[0]) is dict and set(parts[0]) == {'text'}
                  and type(parts[1]) is dict and set(parts[1]) == {'inlineData'}):
                from app.services.gemini37_audio_spend_quotes import quote_gemini37_audio_request
                quote = quote_gemini37_audio_request(body, kwargs.get('headers', {}))
            else:
                raise SpendBlocked('spend_request_not_priced')
            return 'gemini', parsed.path, quote
        return 'gemini', parsed.path, _gemini_json(
            match[1], kwargs.get('json'), kwargs.get('headers', {}))
    # Audio, images, Omni, grounding and fal need separate bounded quotes.
    # A consumer subscription or an apparently free model is not a price quote.
    raise SpendBlocked('spend_request_not_priced')


def quote_openai_response(body):
    if type(body) is dict and body.get('model') == 'gpt-4.1-mini':
        return quote_openai_series_search(body)
    _require(type(body) is dict and set(body) <= {
        'model', 'input', 'instructions', 'store', 'reasoning', 'text',
        'max_output_tokens', 'temperature', 'top_p', 'service_tier'})
    _require(body.get('model') == 'gpt-6-astra')
    _require(body.get('store') is False)
    _require(body.get('service_tier', 'default') == 'default')
    _text_bytes(body.get('input'))  # Only plain text; no hidden files/history.
    if 'instructions' in body:
        _text_bytes(body['instructions'])
    output = _integer(body.get('max_output_tokens'), 1, 16384)
    # UTF-8 bytes plus framing/schema reserve overestimates plain-text tokens.
    # Astra uses implicit caching by default, including when store=False and
    # no cache options are supplied. Reserve the 1.25x cache-write input rate
    # for every input token; ordinary input and cache reads cost less.
    # Reviewed 2026-09-09: https://developers.openai.com/api/docs/guides/prompt-caching
    # and https://developers.openai.com/api/docs/models/gpt-6-astra .
    size = _encoded_size(body)
    _require(size <= 100_000)
    input_tokens = size + 4096
    _fresh()
    amount = (Decimal(input_tokens) * Decimal('12.5') + Decimal(output) * 50) / 1_000_000
    return SpendQuote('openai', 'gpt-6-astra', usd_micro(amount), OPENAI_TEXT_PRICE_REVISION)


def quote_openai_series_search(body):
    """One bounded, stateless research response, including hosted search.

    Reviewed 2026-09-20: /api/docs/pricing, /api/docs/models/gpt-4.1-mini
    and Responses create on developers.openai.com. Non-preview search costs
    $0.01/call and a fixed 8,000 input-token block/call for this model only.
    Reserve all prompt/schema bytes, all search blocks and generated text at
    every possible round (tool calls + final answer), not just the last one.
    The 25% input cushion also covers a cache-write premium. No discounts or
    usage-based refunds are assumed. 'low' alone is NOT a token/cost bound.
    """
    _require(type(body) is dict and set(body) == {
        'model', 'input', 'store', 'tools', 'tool_choice', 'max_tool_calls',
        'max_output_tokens', 'text', 'service_tier', 'include'})
    _require(body['model'] == 'gpt-4.1-mini' and body['store'] is False
        and body['service_tier'] == 'default' and body['tool_choice'] == 'required'
        and body['tools'] == [{'type': 'web_search', 'search_context_size': 'low'}]
        and body['include'] == ['web_search_call.action.sources'])
    _text_bytes(body['input'], 30000)
    calls = _integer(body['max_tool_calls'], 1, 2)
    output = _integer(body['max_output_tokens'], 1, 3600)
    size = _encoded_size(body)
    _require(size <= 50000)
    text = body['text']
    _require(type(text) is dict and set(text) == {'format'})
    form = text['format']
    _require(type(form) is dict and set(form) == {'type', 'name', 'strict', 'schema'}
        and form['type'] == 'json_schema' and form['strict'] is True
        and form['name'] == 'pending_next_series' and type(form['schema']) is dict)
    _fresh()
    rounds = calls + 1
    input_bound = (size + 4096 + 8000 * calls + output) * rounds
    amount = ((Decimal(input_bound) * Decimal('.5')
              + Decimal(output * rounds) * Decimal('1.6')) / 1_000_000
              + Decimal(calls) * Decimal('.01'))
    return SpendQuote('openai', 'gpt-4.1-mini', usd_micro(amount), OPENAI_SERIES_PRICE_REVISION)
