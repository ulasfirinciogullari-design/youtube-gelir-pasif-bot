"""One bounded, budget-reserved Claude JSON request through existing Abacus credits.

No fallback, POST retry, purchase, credit conversion, or automatic refund.
Only reviewed native Messages models and plain text are accepted. Errors never
include credentials, prompts, provider error bodies, or transport exceptions.
"""
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_CEILING
import json
import math
import re

import httpx

from app.services.gemini_generation import (
    _matches_schema, _reject_duplicate_keys, _reject_non_finite,
)
from app.services.production_spend import SpendBlocked
from app.services.production_spend_quotes import (
    ABACUS_GLOBAL_GEO_MODELS, ABACUS_INPUT_FRAMING_TOKENS, ABACUS_MAX_REQUEST_BYTES,
    ABACUS_TEXT_RATES_PER_TOKEN, _bounded_json_schema, _encoded_size,
    quote_http_request,
)
from app.services.production_spend_runtime import enforcement_enabled, paid_post
from app.services import production_spend_runtime


ABACUS_DEFAULT_MODEL = 'claude-haiku-4-5-20251001'
_ENDPOINT = 'https://routellm.abacus.ai/v1/messages'
_TIMEOUT = httpx.Timeout(90.0, connect=10.0)
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_IDENTIFIER = re.compile(r'^[A-Za-z0-9_-]{1,160}$')
_SYSTEM = 'Return exactly one JSON object. Do not use Markdown fences or commentary.'


@dataclass(frozen=True)
class AbacusUsage:
    model: str
    request_id: str
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    actual_micro: int


class AbacusGenerationError(RuntimeError):
    """Terminal failure; a submitted attempt may still have consumed credits."""

    def __init__(self, code, *, usage=None):
        super().__init__(code)
        self.usage = usage


class AbacusConfigurationError(AbacusGenerationError):
    """Invalid configuration discovered before provider submission."""


def _json_loads(value):
    def finite_float(text):
        number = float(text)
        if not math.isfinite(number):
            raise ValueError('non-finite number')
        return number
    return json.loads(value, object_pairs_hook=_reject_duplicate_keys,
                      parse_constant=_reject_non_finite, parse_float=finite_float)


def _post_bounded(url, **kwargs):
    if not enforcement_enabled():
        raise SpendBlocked('spend_not_enabled')
    # HTTPX defaults to zero connection retries; disable redirect following and
    # environment proxies so the key stays on the fixed reviewed HTTPS host.
    with httpx.stream('POST', url, follow_redirects=False, trust_env=False,
                      **kwargs) as response:
        if response.status_code != 200:
            raise AbacusGenerationError('abacus_request_rejected')
        chunks, size = [], 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > _MAX_RESPONSE_BYTES:
                raise AbacusGenerationError('abacus_response_too_large')
            chunks.append(chunk)
        try:
            return _json_loads(b''.join(chunks))
        except Exception:
            raise AbacusGenerationError('abacus_response_invalid') from None


def _usage(payload, body):
    if (type(payload) is not dict or payload.get('type') != 'message'
            or payload.get('role') != 'assistant' or payload.get('model') != body['model']):
        raise AbacusGenerationError('abacus_response_identity_invalid')
    request_id = payload.get('id')
    if type(request_id) is not str or not _IDENTIFIER.fullmatch(request_id):
        raise AbacusGenerationError('abacus_response_identity_invalid')
    usage = payload.get('usage')
    if type(usage) is not dict:
        raise AbacusGenerationError('abacus_usage_missing')
    if (usage.get('service_tier', 'standard') != 'standard'
            or usage.get('speed', 'standard') != 'standard'
            or usage.get('inference_geo', 'global') != 'global'):
        raise AbacusGenerationError('abacus_unexpected_pricing_mode')
    server_tools = usage.get('server_tool_use')
    if server_tools is not None and (type(server_tools) is not dict or any(
            type(value) is not int or value != 0 for value in server_tools.values())):
        raise AbacusGenerationError('abacus_unexpected_tool_usage')
    values = {}
    for name in ('input_tokens', 'output_tokens', 'cache_creation_input_tokens',
                 'cache_read_input_tokens'):
        # Cache fields may be absent when caching was never requested; the two
        # billable token counts are mandatory and must never default to zero.
        value = usage.get(name, 0 if name.startswith('cache_') else None)
        if type(value) is not int or value < 0:
            raise AbacusGenerationError('abacus_usage_invalid')
        values[name] = value
    if values['cache_creation_input_tokens'] or values['cache_read_input_tokens']:
        raise AbacusGenerationError('abacus_unexpected_cache_usage')
    if (values['input_tokens'] > _encoded_size(body) + ABACUS_INPUT_FRAMING_TOKENS
            or values['output_tokens'] > body['max_tokens']):
        raise AbacusGenerationError('abacus_usage_exceeded_quote')
    input_rate, output_rate = ABACUS_TEXT_RATES_PER_TOKEN[body['model']]
    amount = (Decimal(values['input_tokens']) * input_rate
              + Decimal(values['output_tokens']) * output_rate)
    return AbacusUsage(
        model=body['model'], request_id=request_id, **values,
        actual_micro=int((amount * 1_000_000).to_integral_value(rounding=ROUND_CEILING)),
    )


def generate_abacus_json(
    prompt: str,
    *,
    api_key: str,
    model: str = ABACUS_DEFAULT_MODEL,
    json_schema: dict | None = None,
    max_tokens: int = 8192,
) -> dict:
    """Return one locally validated object; every submission error is terminal.

    Safe usage metadata is durably recorded before checking generated JSON.
    This observation never refunds or settles the original reservation.
    """
    if not enforcement_enabled():
        raise SpendBlocked('spend_not_enabled')
    if type(prompt) is not str or not prompt.strip():
        raise AbacusConfigurationError('abacus_input_invalid')
    try:
        if len(prompt.encode('utf-8')) > ABACUS_MAX_REQUEST_BYTES:
            raise ValueError
    except (ValueError, UnicodeError):
        raise AbacusConfigurationError('abacus_input_invalid') from None
    system, schema = _SYSTEM, None
    if json_schema is not None:
        try:
            if type(json_schema) is not dict or json_schema.get('type') != 'object':
                raise ValueError
            _bounded_json_schema(json_schema)
            encoded = json.dumps(json_schema, ensure_ascii=True, allow_nan=False)
            schema = _json_loads(encoded)
            system += '\nThe JSON object must satisfy this schema:\n' + encoded
        except SpendBlocked:
            raise
        except Exception:
            raise AbacusConfigurationError('abacus_schema_invalid') from None
    body = {
        'model': model,
        'system': system,
        'messages': [{'role': 'user', 'content': prompt}],
        'max_tokens': max_tokens,
        'thinking': {'type': 'disabled'},
        'stream': False,
        'service_tier': 'standard_only',
    }
    if type(model) is str and model in ABACUS_GLOBAL_GEO_MODELS:
        body['inference_geo'] = 'global'
    kwargs = {
        'headers': {'x-api-key': api_key, 'Content-Type': 'application/json',
                    'anthropic-version': '2023-06-01'},
        'json': body, 'timeout': _TIMEOUT,
    }
    # Preflight before opening any transport. paid_post independently prices
    # and reserves the identical body, then fences replay in the durable ledger.
    quote_http_request(_ENDPOINT, kwargs)
    usage = None
    try:
        payload = paid_post(_post_bounded, _ENDPOINT, **kwargs)
        usage = _usage(payload, body)
        production_spend_runtime.record_abacus_usage(body, asdict(usage))
        if payload.get('stop_reason') != 'end_turn':
            raise AbacusGenerationError('abacus_output_incomplete', usage=usage)
        content = payload.get('content')
        if (type(content) is not list or len(content) != 1
                or type(content[0]) is not dict or content[0].get('type') != 'text'
                or type(content[0].get('text')) is not str):
            raise AbacusGenerationError('abacus_output_invalid', usage=usage)
        output = _json_loads(content[0]['text'])
        if type(output) is not dict or (schema is not None and not _matches_schema(output, schema)):
            raise AbacusGenerationError('abacus_schema_mismatch', usage=usage)
        return output
    except SpendBlocked:
        raise
    except AbacusGenerationError:
        raise
    except Exception:
        raise AbacusGenerationError('abacus_generation_failed', usage=usage) from None
