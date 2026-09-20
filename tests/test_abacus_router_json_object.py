"""Explicit JSON-object transport retains all existing local QA constraints."""
from copy import deepcopy
import json

import pytest

from app.services import abacus_router_adapter as adapter
from app.services import abacus_router_schema_compat as compat
from test_abacus_router_adapter import KEY, PROMPT, SCHEMA, RESULT, parts, prepare, response, envelope, wire


def prepared(schema=None, content=None):
    return compat.prepare_json_object_router_request(
        parts() if content is None else content, api_key=KEY,
        system_instruction='The complete original visual rubric.',
        json_schema=deepcopy(SCHEMA) if schema is None else schema)


def observed(request, result):
    payload = envelope()
    payload['choices'][0]['message']['content'] = json.dumps(result)
    return adapter.observe_router_response(request, response(request, payload=payload))


def test_explicit_format_preserves_full_rubric_ordered_images_and_original_schema():
    content, schema = parts(), deepcopy(SCHEMA)
    request = prepared(schema, content)
    body = request.payload
    assert body['messages'][0]['content'] == 'The complete original visual rubric.'
    assert body['messages'][1]['content'][:-1] == content
    assert body['response_format'] == {'type': 'json_object'}
    assert body['temperature'] == 0 and 'modalities' not in body
    assert compat.schema_for_body(body) == SCHEMA
    assert adapter.inspect_router_request(request.endpoint, request.wire_kwargs()) == request
    assert observed(request, RESULT).result == RESULT
    schema['properties']['moments']['uniqueItems'] = False
    content[0]['text'] = 'mutated input'
    assert compat.schema_for_body(request.payload) == SCHEMA and request.payload == body
    assert request.request_sha256 != prepare(parts()).request_sha256


@pytest.mark.parametrize('damage', ['duplicate', 'missing', 'extra', 'range', 'type', 'enum', 'boolean_enum'])
def test_json_object_output_never_weakens_local_schema_validation(damage):
    schema, result = deepcopy(SCHEMA), deepcopy(RESULT)
    schema['properties']['moments']['items']['enum'] = [0, 1]
    if damage == 'duplicate': result['moments'] = [1, 1]
    elif damage == 'missing': result.pop('accepted')
    elif damage == 'extra': result['extra'] = True
    elif damage == 'range': result['score'] = 1.1
    elif damage == 'type': result['moments'] = ['1']
    elif damage == 'enum': result['moments'] = [2]
    elif damage == 'boolean_enum': result['moments'] = [True]
    with pytest.raises(adapter.AbacusRouterError, match='^abacus_router_schema_mismatch$'):
        observed(prepared(schema), result)


@pytest.mark.parametrize('damage', ['missing', 'duplicate', 'moved', 'noncanonical', 'invalid_schema',
                                  'legacy_marker', 'extra_native_schema', 'temperature', 'modalities'])
def test_full_schema_contract_and_explicit_envelope_are_required(damage):
    kwargs = prepared().wire_kwargs()
    body = kwargs['json']; content = body['messages'][1]['content']
    if damage == 'missing': content.pop()
    elif damage == 'duplicate': content.append(deepcopy(content[-1]))
    elif damage == 'moved': content.reverse()
    elif damage == 'noncanonical': content[-1]['text'] = compat.OBJECT_SCHEMA_PREFIX + json.dumps(SCHEMA)
    elif damage == 'invalid_schema':
        schema = deepcopy(SCHEMA); schema['properties']['moments']['uniqueItems'] = 0
        content[-1]['text'] = compat.OBJECT_SCHEMA_PREFIX + adapter._canonical(schema).decode()
    elif damage == 'legacy_marker': content[-1]['text'] = compat.SCHEMA_PREFIX + adapter._canonical(SCHEMA).decode()
    elif damage == 'extra_native_schema': body['response_format']['json_schema'] = {}
    elif damage == 'temperature': body['temperature'] = False
    elif damage == 'modalities': body['modalities'] = ['text']
    with pytest.raises(adapter.AbacusRouterError, match='^abacus_router_request_invalid$'):
        adapter.inspect_router_request(adapter.ENDPOINT, kwargs)


def test_actual_wire_schema_downgrade_is_rejected_against_frozen_request():
    request = prepared(); actual = wire(request)
    body = json.loads(actual.content); schema = compat.schema_for_body(body)
    schema['properties']['moments']['uniqueItems'] = False
    body['messages'][1]['content'][-1]['text'] = compat.OBJECT_SCHEMA_PREFIX + adapter._canonical(schema).decode()
    actual._content = adapter._canonical(body)
    actual.headers['content-length'] = str(len(actual._content))
    with pytest.raises(adapter.AbacusRouterError, match='^abacus_router_response_unverified$'):
        adapter.observe_router_response(request, response(request, request=actual))


def test_default_native_request_remains_unchanged_and_has_no_object_contract():
    request = prepare()
    assert request.payload['response_format']['type'] == 'json_schema'
    assert request.payload['response_format']['json_schema']['name'] == 'youtube_review'
    assert request.payload['messages'][1]['content'] == [{'type': 'text', 'text': PROMPT}]
    assert 'temperature' not in request.payload and request.payload['modalities'] == ['text']
    assert compat.schema_for_body(request.payload) == SCHEMA


def test_three_formats_do_not_share_the_same_request_identity():
    native = prepare(parts())
    lowered = compat.prepare_compatible_router_request(parts(), api_key=KEY,
        system_instruction='Full unchanged rubric. Return the whole JSON schema.', json_schema=SCHEMA)
    object_request = compat.prepare_json_object_router_request(parts(), api_key=KEY,
        system_instruction='Full unchanged rubric. Return the whole JSON schema.', json_schema=SCHEMA)
    assert len({p.request_sha256 for p in (native, lowered, object_request)}) == 3
