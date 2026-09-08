"""Small reviewed price catalog. Unknown request shapes NEVER mean free.

USD list rates checked 2026-09-08. Quotes expire at the UTC month boundary.
Not an invoice: prepaid credits/discounts are not deducted a second time.
Sources and intentionally blocked routes are recorded in the rollout doc.
"""
from datetime import datetime, timezone
from decimal import Decimal
import json
import re
from urllib.parse import urlsplit

from app.services.production_spend import SpendBlocked, SpendQuote, usd_micro

_REVISION = 'official-2026-09-08-v1'
_VALID_FROM = datetime(2026, 9, 8, tzinfo=timezone.utc)
_VALID_UNTIL = datetime(2026, 10, 1, tzinfo=timezone.utc)


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
    except (TypeError, ValueError):
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


def quote_http_request(url, kwargs):
    _require(type(url) is str)
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        raise SpendBlocked('spend_request_not_priced') from None
    _require(parsed.scheme == 'https' and not parsed.username and not parsed.password
             and port in (None, 443) and not parsed.query and not parsed.fragment)
    _require(type(kwargs) is dict and set(kwargs) <= {'json', 'headers', 'timeout'})
    match = re.fullmatch(r'/v1beta/models/([A-Za-z0-9._-]+):predictLongRunning', parsed.path)
    if parsed.hostname == 'generativelanguage.googleapis.com' and match:
        return 'gemini', parsed.path, _veo(match[1], kwargs.get('json'))
    # Audio, images, Omni, grounding and fal need separate bounded quotes.
    # A consumer subscription or an apparently free model is not a price quote.
    raise SpendBlocked('spend_request_not_priced')


def quote_openai_response(body):
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
    # No search/tool/context-cache costs can enter this strictly tool-free shape.
    size = _encoded_size(body)
    _require(size <= 100_000)
    input_tokens = size + 4096
    return _quote('openai', 'gpt-6-astra',
                  (Decimal(input_tokens) * 10 + Decimal(output) * 50) / 1_000_000)
