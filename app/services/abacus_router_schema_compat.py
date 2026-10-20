"""Explicit RouteLLM schema compatibility without weakening local validation.

The actual 2026-09-19 visual request rejected ``uniqueItems`` before a model
result. This distinct wire format keeps the complete authored schema in a
bound text part and sends only that unsupported keyword as a local constraint.
The observer still validates every result against the complete original schema.
Legacy request bytes are unchanged. This pure module grants no send or retry.
The explicit JSON Object builder similarly retains every authored constraint
in the bound prompt and leaves validation to the unchanged local observer.
"""
from app.services import abacus_router_adapter as adapter

SCHEMA_NAME = 'youtube_review_unique_items_v1'
ENUM_SCHEMA_NAME = 'youtube_review_enum_constraints_v2'
SCHEMA_PREFIX = (
    'YOUTUBE_REVIEW_COMPLETE_SCHEMA_V1\n'
    'Return JSON conforming to this complete schema. In particular, arrays with '
    'uniqueItems=true must contain distinct values. All constraints are checked '
    'locally before this response can be accepted.\n'
)
ENUM_SCHEMA_PREFIX = (
    'YOUTUBE_REVIEW_COMPLETE_SCHEMA_V2\n'
    'Return JSON conforming to this complete schema. Enum values retain their '
    'original JSON types and arrays with uniqueItems=true contain distinct values. '
    'All constraints are checked locally before this response can be accepted.\n'
)
OBJECT_SCHEMA_PREFIX = (
    'YOUTUBE_REVIEW_COMPLETE_SCHEMA_JSON_OBJECT_V1\n'
    'Return only a JSON object conforming to this complete schema. Preserve all '
    'required fields, types, bounds, enum values and uniqueItems constraints. '
    'The complete schema is enforced locally before any response is accepted.\n'
)


def _require(value):
    adapter._require(value, adapter._REQUEST_ERROR)


def _lower(schema):
    """Visit schema nodes only, preserving properties named uniqueItems."""
    result, count = {}, 0
    for name, value in schema.items():
        if name == 'uniqueItems':
            count += 1
        elif name == 'properties':
            result[name] = {}
            for field, child in value.items():
                result[name][field], added = _lower(child)
                count += added
        elif name == 'items':
            result[name], added = _lower(value)
            count += added
        else:
            result[name] = value
    return result, count


def _lower_enums(schema):
    """The observed provider enum field accepts a string, not a JSON Schema list."""
    native, _ = _lower(schema)
    def visit(node):
        result, count = {}, 0
        for name, value in node.items():
            if name == 'enum':
                count += 1
            elif name == 'properties':
                result[name] = {}
                for field, child in value.items():
                    result[name][field], added = visit(child)
                    count += added
            elif name == 'items':
                result[name], added = visit(value)
                count += added
            else:
                result[name] = value
        return result, count
    return visit(native)


def _format(name):
    _require(name in (SCHEMA_NAME, ENUM_SCHEMA_NAME))
    return (SCHEMA_PREFIX, _lower) if name == SCHEMA_NAME else (ENUM_SCHEMA_PREFIX, _lower_enums)


def schema_for_body(body):
    """Recover the complete validation schema from the immutable wire body."""
    parts = body['messages'][1]['content']
    records = [index for index, part in enumerate(parts) if part.get('type') == 'text'
               and part['text'].startswith((SCHEMA_PREFIX, ENUM_SCHEMA_PREFIX, OBJECT_SCHEMA_PREFIX))]
    if body['response_format'] == {'type': 'json_object'}:
        return _complete_schema(parts, records, OBJECT_SCHEMA_PREFIX)
    spec = body['response_format']['json_schema']
    if spec['name'] == 'youtube_review':
        _require(not records)
        return spec['schema']
    prefix, lower = _format(spec['name'])
    original = _complete_schema(parts, records, prefix)
    native, count = lower(original)
    _require(count > 0 and adapter._canonical(native) == adapter._canonical(spec['schema']))
    return original


def _complete_schema(parts, records, prefix):
    _require(records == [len(parts) - 1] and parts[-1]['text'].startswith(prefix))
    encoded = parts[-1]['text'][len(prefix):].encode('utf-8')
    _require(0 < len(encoded) <= adapter.MAX_METADATA_BYTES)
    original = adapter._json_loads(encoded.decode('utf-8'))
    _require(type(original) is dict and original.get('type') == 'object'
             and adapter._canonical(original) == encoded)
    adapter._bounded_visual_schema(original)
    return original


def prepare_json_object_router_request(parts, *, api_key, system_instruction,
                                       json_schema, max_tokens=adapter.MAX_OUTPUT_TOKENS):
    """Explicit alternative envelope; never a retry, fallback or send authority.

    All supplied text, ordered JPEGs and the complete schema remain bound to
    the request. The existing observer enforces the same local schema rules.
    Existing retained controllers keep their original formats and closed slots.
    """
    try:
        _require(type(api_key) is str)
        original = adapter._copy_json(json_schema)
        _require(type(original) is dict and original.get('type') == 'object')
        adapter._bounded_visual_schema(original)
        content = adapter._copy_json(parts)
        _require(type(content) is list and content)
        content.append({'type': 'text', 'text': OBJECT_SCHEMA_PREFIX + adapter._canonical(original).decode('utf-8')})
        body = {'model': adapter.MODEL, 'messages': [
            {'role': 'system', 'content': system_instruction}, {'role': 'user', 'content': content}],
            'response_format': {'type': 'json_object'}, 'max_tokens': max_tokens,
            'stream': False, 'temperature': 0}
        return adapter.inspect_router_request(adapter.ENDPOINT, {
            'json': body, 'headers': {'Authorization': 'Bearer ' + api_key,
                'Content-Type': 'application/json', 'Accept': 'application/json'}, 'timeout': 90.0})
    except adapter.AbacusRouterError:
        raise
    except Exception:
        raise adapter.AbacusRouterError(adapter._REQUEST_ERROR) from None


def prepare_compatible_router_request(parts, *, api_key, system_instruction,
                                      json_schema, max_tokens=adapter.MAX_OUTPUT_TOKENS,
                                      schema_name=SCHEMA_NAME):
    """Opt in to a different, fully bound request; never rewrite an old request."""
    try:
        _require(type(api_key) is str)
        original = adapter._copy_json(json_schema)
        _require(type(original) is dict and original.get('type') == 'object')
        adapter._bounded_visual_schema(original)
        prefix, lower = _format(schema_name)
        native, count = lower(original)
        _require(count > 0)
        content = adapter._copy_json(parts)
        _require(type(content) is list and content)
        content.append({'type': 'text', 'text': prefix + adapter._canonical(original).decode('utf-8')})
        body = {'model': adapter.MODEL, 'messages': [
            {'role': 'system', 'content': system_instruction}, {'role': 'user', 'content': content}],
            'response_format': {'type': 'json_schema', 'json_schema': {
                'name': schema_name, 'strict': True, 'schema': native}},
            'max_tokens': max_tokens, 'stream': False, 'modalities': ['text']}
        return adapter.inspect_router_request(adapter.ENDPOINT, {
            'json': body, 'headers': {'Authorization': 'Bearer ' + api_key,
                'Content-Type': 'application/json', 'Accept': 'application/json'}, 'timeout': 90.0})
    except adapter.AbacusRouterError:
        raise
    except Exception:
        raise adapter.AbacusRouterError(adapter._REQUEST_ERROR) from None
