"""Immutable, offline validation for one subscription-router JSON request.

Reviewed 2026-09-09 against the official RouteLLM Chat Completions and Image
Analysis documentation. Only the exact self-serve endpoint and ``route-llm``
model are supported. This is separate from fixed-model native Messages.

There is no sender, retry, fallback, entitlement, journal, USD quote or activation
here. A future trusted caller must authorize and permanently fence one request
before sending, and bound response bytes while reading. These pure observers
only inspect already-loaded HTTPX bytes; their evidence is not a signature,
subscription proof, QA approval or proof of an independent underlying model.
"""
import base64
from dataclasses import dataclass
import hashlib
import json
import math
import re

import httpx

from app.services.abacus_generation import _json_loads
from app.services.abacus_visual_generation import _bounded_visual_schema, _unique_items_match
from app.services.abacus_visual_spend_quotes import (
    ABACUS_VISUAL_MAX_DECODED_BYTES, ABACUS_VISUAL_MAX_IMAGE_BYTES,
    ABACUS_VISUAL_MAX_IMAGES, ABACUS_VISUAL_MAX_METADATA_BYTES, _decode_jpeg,
)
from app.services.gemini_generation import _matches_schema
from app.services.production_spend import SpendBlocked


ENDPOINT = 'https://routellm.abacus.ai/v1/chat/completions'
OPERATION = '/v1/chat/completions'
MODEL = 'route-llm'
MAX_OUTPUT_TOKENS = 8192
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_METADATA_BYTES = ABACUS_VISUAL_MAX_METADATA_BYTES
MAX_IMAGES = ABACUS_VISUAL_MAX_IMAGES
MAX_IMAGE_BYTES = ABACUS_VISUAL_MAX_IMAGE_BYTES
MAX_DECODED_IMAGE_BYTES = ABACUS_VISUAL_MAX_DECODED_BYTES
_DATA_PREFIX = 'data:image/jpeg;base64,'
_MAX_BASE64_BYTES = 4 * ((MAX_IMAGE_BYTES + 2) // 3)
MAX_REQUEST_BYTES = MAX_METADATA_BYTES + MAX_IMAGES * (_MAX_BASE64_BYTES + len(_DATA_PREFIX))
_MAX_PARTS = 4 * MAX_IMAGES + 1
_MAX_TOKEN_COUNTER = 1_000_000_000  # Finite protocol sanity bound, NOT a price/context estimate.
_IDENTIFIER = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$')
_BODY_FIELDS = {'model', 'messages', 'response_format', 'max_tokens', 'stream', 'modalities'}
_JSON_OBJECT_FIELDS = (_BODY_FIELDS - {'modalities'}) | {'temperature'}
_REQUEST_ERROR = 'abacus_router_request_invalid'
_RESPONSE_ERROR = 'abacus_router_response_unverified'
_SCHEMA_ERROR = 'abacus_router_schema_mismatch'
_USAGE_ERROR = 'abacus_router_usage_invalid'


class AbacusRouterError(SpendBlocked):
    """Terminal local/protocol failure; never a fallback or new-send signal."""


def _require(condition, code):
    if not condition:
        raise AbacusRouterError(code)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      sort_keys=True, separators=(',', ':')).encode('utf-8')


def _copy_json(value, *, depth=0, remaining=None):
    """Reject coerced Python types and excessive structure before encoding."""
    if remaining is None:
        remaining = [50_000]
    remaining[0] -= 1
    _require(depth <= 32 and remaining[0] >= 0, _REQUEST_ERROR)
    if type(value) is dict:
        _require(all(type(key) is str for key in value), _REQUEST_ERROR)
        return {key: _copy_json(item, depth=depth + 1, remaining=remaining)
                for key, item in value.items()}
    if type(value) is list:
        return [_copy_json(item, depth=depth + 1, remaining=remaining) for item in value]
    _require(value is None or type(value) in (str, int, bool)
             or type(value) is float and math.isfinite(value), _REQUEST_ERROR)
    return value


def _text(value):
    _require(type(value) is str and bool(value.strip())
             and len(value.encode('utf-8')) <= MAX_METADATA_BYTES, _REQUEST_ERROR)
    return value


def _inspect_body(value):
    body = _copy_json(value)
    _require(type(body) is dict, _REQUEST_ERROR)
    object_mode = body.get('response_format') == {'type': 'json_object'}
    _require(set(body) == (_JSON_OBJECT_FIELDS if object_mode else _BODY_FIELDS)
             and body['model'] == MODEL and body['stream'] is False, _REQUEST_ERROR)
    if object_mode:
        _require(type(body['temperature']) is int and body['temperature'] == 0, _REQUEST_ERROR)
    else:
        _require(body['modalities'] == ['text'], _REQUEST_ERROR)
    _require(type(body['max_tokens']) is int
             and 1 <= body['max_tokens'] <= MAX_OUTPUT_TOKENS, _REQUEST_ERROR)
    messages = body['messages']
    _require(type(messages) is list and len(messages) == 2, _REQUEST_ERROR)
    for message, role in zip(messages, ('system', 'user')):
        _require(type(message) is dict and set(message) == {'role', 'content'}
                 and message['role'] == role, _REQUEST_ERROR)
    _text(messages[0]['content'])
    parts = messages[1]['content']
    _require(type(parts) is list and 1 <= len(parts) <= _MAX_PARTS, _REQUEST_ERROR)
    metadata_parts, images, decoded_bytes, text_count, verified = [], 0, 0, 0, set()
    for part in parts:
        _require(type(part) is dict, _REQUEST_ERROR)
        if part.get('type') == 'text':
            _require(set(part) == {'type', 'text'}, _REQUEST_ERROR)
            _text(part['text'])
            metadata_parts.append(part)
            text_count += 1
            continue
        _require(set(part) == {'type', 'image_url'} and part['type'] == 'image_url', _REQUEST_ERROR)
        image = part['image_url']
        _require(type(image) is dict and set(image) == {'url'}, _REQUEST_ERROR)
        url = image['url']
        _require(type(url) is str and url.startswith(_DATA_PREFIX)
                 and len(url) <= len(_DATA_PREFIX) + _MAX_BASE64_BYTES, _REQUEST_ERROR)
        encoded = url[len(_DATA_PREFIX):]
        raw = base64.b64decode(encoded, validate=True)
        _require(0 < len(raw) <= MAX_IMAGE_BYTES
                 and base64.b64encode(raw).decode('ascii') == encoded, _REQUEST_ERROR)
        images += 1
        decoded_bytes += len(raw)
        _require(images <= MAX_IMAGES and decoded_bytes <= MAX_DECODED_IMAGE_BYTES, _REQUEST_ERROR)
        verified.add(raw)
        metadata_parts.append({'type': 'image_url', 'image_url': {'url': _DATA_PREFIX}})
    _require(text_count > 0, _REQUEST_ERROR)
    response_format = body['response_format']
    from app.services.abacus_router_schema_compat import SCHEMA_NAME, ENUM_SCHEMA_NAME, schema_for_body
    if not object_mode:
        _require(type(response_format) is dict and set(response_format) == {'type', 'json_schema'}
                 and response_format['type'] == 'json_schema', _REQUEST_ERROR)
        spec = response_format['json_schema']
        _require(type(spec) is dict and set(spec) == {'name', 'strict', 'schema'}
                 and spec['name'] in ('youtube_review', SCHEMA_NAME, ENUM_SCHEMA_NAME) and spec['strict'] is True
                 and type(spec['schema']) is dict and spec['schema'].get('type') == 'object', _REQUEST_ERROR)
        _bounded_visual_schema(spec['schema'])
    schema_for_body(body)  # A compatibility request must retain its complete authored schema.
    metadata = {**body, 'messages': [messages[0], {'role': 'user', 'content': metadata_parts}]}
    raw = _canonical(body)
    _require(len(_canonical(metadata)) <= MAX_METADATA_BYTES
             and len(raw) <= MAX_REQUEST_BYTES, _REQUEST_ERROR)
    for image in verified:
        _decode_jpeg(image)  # Full bounded decode, no resizing/re-encoding or external media access.
    return raw


@dataclass(frozen=True, repr=False)
class PreparedRouterRequest:
    """Private snapshot; never store its credentials or body in a public job."""

    _body_bytes: bytes
    _header_pairs: tuple

    def __repr__(self):
        return '<PreparedRouterRequest abacus:route-llm redacted>'

    @property
    def provider(self):
        return 'abacus'

    @property
    def endpoint(self):
        return ENDPOINT

    @property
    def operation(self):
        return OPERATION

    @property
    def model(self):
        return MODEL

    @property
    def credential_sha256(self):
        key = dict(self._header_pairs)['authorization'][len('Bearer '):]
        return _sha(('abacus\0' + key).encode('ascii'))

    @property
    def payload(self):
        return _json_loads(self._body_bytes)

    @property
    def request_sha256(self):
        # The journal must ALSO bind account/key/root and replay ancestry. This
        # deterministic native identity is not a replacement for those fences.
        return _sha(_canonical({'version': 1, 'method': 'POST', 'endpoint': ENDPOINT,
                                'body': self.payload}))

    def wire_kwargs(self):
        """Fresh mutable transport copy; callers cannot mutate this snapshot."""
        return {'json': self.payload, 'headers': dict(self._header_pairs), 'timeout': 90.0}


def inspect_router_request(url, kwargs):
    """Validate and detach the complete native request, without authorizing it."""
    try:
        _require(type(url) is str and url == ENDPOINT, _REQUEST_ERROR)
        _require(type(kwargs) is dict and set(kwargs) == {'json', 'headers', 'timeout'}
                 and type(kwargs['timeout']) in (int, float) and kwargs['timeout'] == 90, _REQUEST_ERROR)
        headers = kwargs['headers']
        _require(type(headers) is dict and all(type(k) is str and type(v) is str for k, v in headers.items()),
                 _REQUEST_ERROR)
        normalized = {key.lower(): value for key, value in headers.items()}
        _require(len(normalized) == len(headers) and set(normalized) == {
            'authorization', 'content-type', 'accept'}
            and normalized['content-type'] == normalized['accept'] == 'application/json', _REQUEST_ERROR)
        auth = normalized['authorization']
        _require(auth.startswith('Bearer '), _REQUEST_ERROR)
        key = auth[len('Bearer '):]
        _require(1 <= len(key) <= 4096 and all(32 < ord(char) < 127 for char in key), _REQUEST_ERROR)
        return PreparedRouterRequest(_inspect_body(kwargs['json']), tuple(sorted(normalized.items())))
    except AbacusRouterError:
        raise
    except Exception:
        raise AbacusRouterError(_REQUEST_ERROR) from None


def prepare_router_request(parts, *, api_key, system_instruction, json_schema, max_tokens=MAX_OUTPUT_TOKENS):
    """Build a fixed router request preserving every supplied text/image/schema byte."""
    _require(type(api_key) is str, _REQUEST_ERROR)
    return inspect_router_request(ENDPOINT, {
        'headers': {'Authorization': 'Bearer ' + api_key, 'Content-Type': 'application/json',
                    'Accept': 'application/json'},
        'json': {'model': MODEL, 'messages': [
            {'role': 'system', 'content': system_instruction}, {'role': 'user', 'content': parts}],
            'response_format': {'type': 'json_schema', 'json_schema': {
                'name': 'youtube_review', 'strict': True, 'schema': json_schema}},
            'max_tokens': max_tokens, 'stream': False, 'modalities': ['text']},
        'timeout': 90.0,
    })


def _headers(headers, *, names=None):
    result = {}
    for name, value in headers.raw:
        name = name.decode('ascii').lower()
        if names is not None and name not in names:
            continue
        _require(name not in result, _RESPONSE_ERROR)
        result[name] = value.decode('ascii')
    return result


def _verify_request(prepared, request):
    _require(type(request) is httpx.Request and request.method == 'POST'
             and str(request.url) == ENDPOINT, _RESPONSE_ERROR)
    headers = _headers(request.headers)
    _require(set(headers) <= {'authorization', 'content-type', 'accept', 'host', 'content-length',
                             'accept-encoding', 'connection', 'user-agent'}
             and headers.get('host') == 'routellm.abacus.ai', _RESPONSE_ERROR)
    _require(all(headers.get(name) == value for name, value in prepared._header_pairs), _RESPONSE_ERROR)
    raw = request.content
    _require(type(raw) is bytes and len(raw) <= MAX_REQUEST_BYTES
             and headers.get('content-length') == str(len(raw)), _RESPONSE_ERROR)
    # UTF-8 only, duplicate keys/non-finite numbers rejected, canonical numeric
    # identity preserved (False, 0, 0.0 cannot be silently substituted).
    actual = _json_loads(raw.decode('utf-8'))
    _require(_canonical(actual) == prepared._body_bytes, _RESPONSE_ERROR)
    return _sha(raw)


def _enum_match(value, schema):
    """Complete the shared validator's enum semantics: booleans aren't numbers."""
    if 'enum' in schema and not any(
            value == member and (type(value) is type(member)
                                 or type(value) in (int, float) and type(member) in (int, float))
            for member in schema['enum']):
        return False
    if type(value) is dict:
        properties = schema.get('properties', {})
        return all(_enum_match(item, properties[name]) for name, item in value.items() if name in properties)
    if type(value) is list and 'items' in schema:
        return all(_enum_match(item, schema['items']) for item in value)
    return True


def _usage(value, max_tokens):
    native_fields = {'input_tokens', 'output_tokens', 'raw_input_tokens'}
    if type(value) is dict and set(value) in (native_fields, native_fields | {'reasoning_tokens'}):
        # Keep RouteLLM's actual counters. No reported total or relationship
        # between raw/effective input or reasoning/output is inferred.
        _require(all(type(count) is int and 0 <= count <= _MAX_TOKEN_COUNTER
                     for count in value.values()) and value['output_tokens'] <= max_tokens, _USAGE_ERROR)
        return value
    _require(type(value) is dict and {'prompt_tokens', 'completion_tokens', 'total_tokens'} <= set(value)
             and set(value) <= {'prompt_tokens', 'completion_tokens', 'total_tokens',
                               'prompt_tokens_details', 'completion_tokens_details'}, _USAGE_ERROR)
    for name in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
        _require(type(value[name]) is int and 0 <= value[name] <= _MAX_TOKEN_COUNTER, _USAGE_ERROR)
    _require(value['completion_tokens'] <= max_tokens
             and value['total_tokens'] == value['prompt_tokens'] + value['completion_tokens'], _USAGE_ERROR)
    for name, keys, total in (
        ('prompt_tokens_details', {'cached_tokens', 'audio_tokens'}, value['prompt_tokens']),
        ('completion_tokens_details', {'reasoning_tokens', 'audio_tokens', 'accepted_prediction_tokens',
                                       'rejected_prediction_tokens'}, value['completion_tokens']),
    ):
        if name not in value:
            continue
        details = value[name]
        _require(type(details) is dict and set(details) <= keys, _USAGE_ERROR)
        for field, count in details.items():
            _require(type(count) is int and 0 <= count <= total, _USAGE_ERROR)
            if field in {'audio_tokens', 'accepted_prediction_tokens', 'rejected_prediction_tokens'}:
                _require(count == 0, _USAGE_ERROR)
    return value


@dataclass(frozen=True, repr=False)
class ObservedRouterResult:
    _result_bytes: bytes
    _evidence_bytes: bytes

    def __repr__(self):
        return '<ObservedRouterResult abacus:route-llm redacted>'

    @property
    def result(self):
        return _json_loads(self._result_bytes)

    @property
    def evidence(self):
        return _json_loads(self._evidence_bytes)

    @property
    def returned_model(self):
        return self.evidence['returned_model']

    @property
    def underlying_model_verified(self):
        return False

    @property
    def usage(self):
        return self.evidence['usage']


def _parse_response_payload(raw, *, schema, max_tokens):
    """Parse bounded bytes only; transport provenance and QA remain unverified.

    This returns fresh JSON values, never an observation, receipt or approval.
    A stored-response consumer must independently authenticate the exact raw
    bytes, original request, reservation and source before using this parser.
    """
    try:
        _require(type(raw) is bytes and 0 < len(raw) <= MAX_RESPONSE_BYTES
                 and type(schema) is dict and type(max_tokens) is int
                 and 1 <= max_tokens <= MAX_OUTPUT_TOKENS, _RESPONSE_ERROR)
        payload = _json_loads(raw.decode('utf-8'))
        _require(type(payload) is dict and {'model', 'choices'} <= set(payload)
                 and set(payload) <= {'id', 'object', 'created', 'model', 'choices', 'usage', 'system_fingerprint'},
                 _RESPONSE_ERROR)
        # RouteLLM can omit OpenAI-style identity metadata. Absence is not an
        # invented provider ID; present metadata must still have its exact type.
        if 'object' in payload:
            _require(type(payload['object']) is str and payload['object'] == 'chat.completion',
                     _RESPONSE_ERROR)
        for field in ('model', *(['id'] if 'id' in payload else [])):
            _require(type(payload[field]) is str and _IDENTIFIER.fullmatch(payload[field]) is not None,
                     _RESPONSE_ERROR)
        if 'created' in payload:
            _require(type(payload['created']) is int and 0 <= payload['created'] <= 253_402_300_799,
                     _RESPONSE_ERROR)
        if 'system_fingerprint' in payload:
            fingerprint = payload['system_fingerprint']
            _require(fingerprint is None or type(fingerprint) is str
                     and _IDENTIFIER.fullmatch(fingerprint) is not None, _RESPONSE_ERROR)
        choices = payload['choices']
        _require(type(choices) is list and len(choices) == 1 and type(choices[0]) is dict
                 and {'index', 'message', 'finish_reason'} <= set(choices[0])
                 and set(choices[0]) <= {'index', 'message', 'finish_reason', 'logprobs', 'native_finish_reason'},
                 _RESPONSE_ERROR)
        choice = choices[0]
        if 'native_finish_reason' in choice:
            _require(type(choice['native_finish_reason']) is str
                     and choice['native_finish_reason'] in {'STOP', 'stop'}, _RESPONSE_ERROR)
        _require(type(choice['index']) is int and choice['index'] == 0
                 and choice['finish_reason'] == 'stop' and choice.get('logprobs') is None, _RESPONSE_ERROR)
        message = choice['message']
        _require(type(message) is dict and {'role', 'content'} <= set(message)
                 and set(message) <= {'role', 'content', 'refusal', 'tool_calls', 'function_call'}
                 and message['role'] == 'assistant' and type(message['content']) is str
                 and message.get('refusal') is None and message.get('function_call') is None
                 and (message.get('tool_calls') is None or message['tool_calls'] == []), _RESPONSE_ERROR)
        output = _json_loads(message['content'])
        _require(type(output) is dict and _matches_schema(output, schema)
                 and _unique_items_match(output, schema) and _enum_match(output, schema), _SCHEMA_ERROR)
        usage = _usage(payload['usage'], max_tokens) if 'usage' in payload else None
        return {'payload': payload, 'result': output, 'usage': usage}
    except AbacusRouterError:
        raise
    except Exception:
        raise AbacusRouterError(_RESPONSE_ERROR) from None


def observe_router_response(prepared, response):
    """Observe one complete bound JSON response; errors never permit another send."""
    try:
        _require(type(prepared) is PreparedRouterRequest, _RESPONSE_ERROR)
        # Revalidate privately constructed snapshots as well as normal builders.
        _require(inspect_router_request(ENDPOINT, prepared.wire_kwargs()) == prepared, _RESPONSE_ERROR)
        _require(type(response) is httpx.Response and 200 <= response.status_code < 300
                 and not response.history and response.is_stream_consumed, _RESPONSE_ERROR)
        wire_sha = _verify_request(prepared, response.request)
        raw = response.content
        _require(type(raw) is bytes and 0 < len(raw) <= MAX_RESPONSE_BYTES, _RESPONSE_ERROR)
        headers = _headers(response.headers, names={
            'content-type', 'content-length', 'content-encoding', 'transfer-encoding', 'request-id', 'x-request-id'})
        mime = headers.get('content-type', '').lower().replace(' ', '')
        _require(mime in {'application/json', 'application/json;charset=utf-8'}, _RESPONSE_ERROR)
        _require(not ('content-length' in headers and 'transfer-encoding' in headers), _RESPONSE_ERROR)
        if 'content-length' in headers:
            length = headers['content-length']
            _require(re.fullmatch(r'[0-9]{1,9}', length) is not None, _RESPONSE_ERROR)
            if headers.get('content-encoding', 'identity') == 'identity':
                _require(int(length) == len(raw), _RESPONSE_ERROR)
        from app.services.abacus_router_schema_compat import schema_for_body
        parsed = _parse_response_payload(raw,
            schema=schema_for_body(prepared.payload),
            max_tokens=prepared.payload['max_tokens'])
        payload, output, usage = (parsed[key] for key in ('payload', 'result', 'usage'))
        evidence = {
            'version': 1, 'provider': 'abacus', 'endpoint': ENDPOINT, 'operation': OPERATION,
            'requested_model': MODEL, 'returned_model': payload['model'], 'underlying_model_verified': False,
            'credential_sha256': prepared.credential_sha256, 'request_sha256': prepared.request_sha256,
            'wire_body_sha256': wire_sha, 'response_body_sha256': _sha(raw), 'status_code': response.status_code,
            'provider_request_id_sha256': (_sha(('abacus\0router-request\0' + payload['id']).encode('ascii'))
                                          if 'id' in payload else None),
            'parsed_result_sha256': _sha(_canonical(output)), 'usage': usage,
        }
        evidence['response_proof_sha256'] = _sha(_canonical(evidence))
        return ObservedRouterResult(_canonical(output), _canonical(evidence))
    except AbacusRouterError:
        raise
    except Exception:
        # Never leak transport exceptions, response body, prompts, image data or credentials.
        raise AbacusRouterError(_RESPONSE_ERROR) from None
