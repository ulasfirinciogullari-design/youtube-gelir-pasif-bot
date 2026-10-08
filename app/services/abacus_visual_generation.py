"""One reserved native Claude image review with the caller's full evidence.

This adapter does not select, crop, resize, split, or retry images. Its quote
reserves Haiku's full 200K input context, independently of image estimates.
Usage observations never refund a reservation or establish QA approval.
"""
from dataclasses import asdict
import json

from app.services.abacus_generation import (
    AbacusConfigurationError, AbacusGenerationError, AbacusUsage,
    _IDENTIFIER, _TIMEOUT, _json_loads, _post_bounded,
)
from app.services.abacus_visual_spend_quotes import (
    ABACUS_VISUAL_MAX_METADATA_BYTES, ABACUS_VISUAL_MODEL, inspect_abacus_visual_request,
)
from app.services.gemini_generation import _matches_schema
from app.services.production_spend import SpendBlocked
from app.services.production_spend_quotes import _bounded_json_schema, quote_http_request
from app.services import production_spend_runtime


_ENDPOINT = 'https://routellm.abacus.ai/v1/messages'
_SCHEMA_PREFIX = '\n\nReturn exactly one JSON object satisfying this schema. Do not use Markdown fences:\n'


def _bounded_visual_schema(schema):
    """Keep the full visual schema, including its evidence uniqueness rule."""
    def shape(value, depth=0):
        if type(value) is not dict or depth > 20:
            raise ValueError('abacus_visual_schema_invalid')
        checked = dict(value)
        if 'uniqueItems' in checked:
            if type(checked['uniqueItems']) is not bool or not (
                checked.get('type') == 'array'
                or type(checked.get('type')) is list and 'array' in checked['type']
            ):
                raise ValueError('abacus_visual_schema_invalid')
            checked.pop('uniqueItems')
        if 'properties' in checked:
            if type(checked['properties']) is not dict:
                raise ValueError('abacus_visual_schema_invalid')
            checked['properties'] = {name: shape(child, depth + 1)
                                     for name, child in checked['properties'].items()}
        if 'items' in checked:
            checked['items'] = shape(checked['items'], depth + 1)
        return checked
    _bounded_json_schema(shape(schema))


def _unique_items_match(value, schema):
    def identity(item):
        if type(item) in (int, float):
            return ('number', item)  # JSON 1 and 1.0 are equal, true is distinct.
        if type(item) is list:
            return ('array', tuple(identity(child) for child in item))
        if type(item) is dict:
            return ('object', tuple(sorted((key, identity(child)) for key, child in item.items())))
        return (type(item).__name__, item)
    if type(value) is list:
        if schema.get('uniqueItems') is True and len({identity(item) for item in value}) != len(value):
            return False
        if 'items' in schema and any(not _unique_items_match(item, schema['items']) for item in value):
            return False
    if type(value) is dict:
        properties = schema.get('properties', {})
        if any(not _unique_items_match(item, properties[key]) for key, item in value.items() if key in properties):
            return False
    return True


def _usage(payload, request):
    """Validate the response against the inspected, full-context reservation."""
    body = request['body']
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
        value = usage.get(name, 0 if name.startswith('cache_') else None)
        if type(value) is not int or value < 0:
            raise AbacusGenerationError('abacus_usage_invalid')
        values[name] = value
    if values['cache_creation_input_tokens'] or values['cache_read_input_tokens']:
        raise AbacusGenerationError('abacus_unexpected_cache_usage')
    if (values['input_tokens'] > request['input_tokens_upper_bound']
            or values['output_tokens'] > request['max_tokens']):
        raise AbacusGenerationError('abacus_usage_exceeded_quote')
    # The reviewed native Haiku list rates are exactly 1 and 5 USD micro/token.
    return AbacusUsage(
        model=body['model'], request_id=request_id, **values,
        actual_micro=values['input_tokens'] + 5 * values['output_tokens'],
    )


def generate_abacus_visual_json(
    parts: list[dict],
    *,
    system_instruction: str,
    api_key: str,
    json_schema: dict,
    model: str = ABACUS_VISUAL_MODEL,
    max_tokens: int = 8192,
) -> dict:
    """Return one locally validated object after durable usage acknowledgement.

    ``parts`` are native interleaved text and inline base64 JPEG blocks. Their
    content and order are preserved in a detached request. The caller's rubric
    is preserved in full, with the counted schema appended as ordinary text.
    Provider and response failures are terminal, including malformed JSON.
    """
    if not production_spend_runtime.enforcement_enabled():
        raise SpendBlocked('spend_not_enabled')
    try:
        if (type(system_instruction) is not str or not system_instruction.strip()
                or len(system_instruction.encode('utf-8')) > ABACUS_VISUAL_MAX_METADATA_BYTES):
            raise ValueError
        if type(json_schema) is not dict or json_schema.get('type') != 'object':
            raise ValueError
        # Preserve the original Python input checks before JSON encoding can
        # coerce mapping keys or tuples into a different accepted shape.
        _bounded_visual_schema(json_schema)
        if len(json.dumps(json_schema, ensure_ascii=False, allow_nan=False).encode('utf-8')) > ABACUS_VISUAL_MAX_METADATA_BYTES:
            raise ValueError
        encoded_schema = json.dumps(json_schema, ensure_ascii=True, allow_nan=False)
        schema = _json_loads(encoded_schema)
        _bounded_visual_schema(schema)
        request = inspect_abacus_visual_request({
            'model': model,
            'system': system_instruction + _SCHEMA_PREFIX + encoded_schema,
            'messages': [{'role': 'user', 'content': parts}],
            'max_tokens': max_tokens,
            'thinking': {'type': 'disabled'},
            'stream': False,
            'service_tier': 'standard_only',
        })
    except SpendBlocked:
        raise
    except Exception:
        raise AbacusConfigurationError('abacus_visual_input_invalid') from None
    body = request['body']
    kwargs = {
        'headers': {'x-api-key': api_key, 'Content-Type': 'application/json',
                    'anthropic-version': '2023-06-01'},
        'json': body, 'timeout': _TIMEOUT,
    }
    quote_http_request(_ENDPOINT, kwargs)
    usage = None
    try:
        payload = production_spend_runtime.paid_post(_post_bounded, _ENDPOINT, **kwargs)
        usage = _usage(payload, request)
        production_spend_runtime.record_abacus_usage(body, asdict(usage))
        if payload.get('stop_reason') != 'end_turn':
            raise AbacusGenerationError('abacus_output_incomplete', usage=usage)
        content = payload.get('content')
        if (type(content) is not list or len(content) != 1
                or type(content[0]) is not dict or set(content[0]) != {'type', 'text'}
                or content[0]['type'] != 'text' or type(content[0]['text']) is not str):
            raise AbacusGenerationError('abacus_output_invalid', usage=usage)
        output = _json_loads(content[0]['text'])
        if (type(output) is not dict or not _matches_schema(output, schema)
                or not _unique_items_match(output, schema)):
            raise AbacusGenerationError('abacus_schema_mismatch', usage=usage)
        return output
    except SpendBlocked:
        raise
    except AbacusGenerationError:
        raise
    except Exception:
        raise AbacusGenerationError('abacus_generation_failed', usage=usage) from None
