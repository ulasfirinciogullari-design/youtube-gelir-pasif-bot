"""Real HTTPX observations retain full local rules despite the wire dialect."""
from copy import deepcopy
import json

import pytest

from app.services import abacus_router_adapter as adapter
from app.services import abacus_router_schema_compat as compat
from test_abacus_router_adapter import KEY, PROMPT, SCHEMA, RESULT, parts, prepare, response, envelope


def compatible(content=None, schema=None):
    return compat.prepare_compatible_router_request(
        [{'type': 'text', 'text': PROMPT}] if content is None else content,
        api_key=KEY, system_instruction='Complete unchanged rubric.',
        json_schema=deepcopy(SCHEMA) if schema is None else schema)


def observed(prepared, result):
    payload = envelope()
    payload['choices'][0]['message']['content'] = json.dumps(result)
    return adapter.observe_router_response(prepared, response(prepared, payload=payload))


def test_explicit_wire_dialect_preserves_images_rubric_and_full_local_schema():
    content, schema = parts(), deepcopy(SCHEMA)
    p = compatible(content, schema)
    body = p.payload
    assert body['messages'][1]['content'][:-1] == content
    assert body['messages'][0]['content'] == 'Complete unchanged rubric.'
    assert body['response_format']['json_schema']['name'] == compat.SCHEMA_NAME
    native = body['response_format']['json_schema']['schema']
    assert 'uniqueItems' not in native['properties']['moments']
    assert compat.schema_for_body(body) == SCHEMA
    assert adapter.inspect_router_request(p.endpoint, p.wire_kwargs()) == p
    assert observed(p, RESULT).result == RESULT
    schema['properties']['moments']['uniqueItems'] = False
    content[0]['text'] = 'changed'
    assert compat.schema_for_body(p.payload) == SCHEMA
    assert p.payload == body


def test_legacy_request_has_identical_bytes_and_keeps_its_full_native_schema():
    p = prepare()
    assert p.payload['messages'][1]['content'] == [{'type': 'text', 'text': PROMPT}]
    assert p.payload['response_format']['json_schema']['schema'] == SCHEMA
    assert p.payload['response_format']['json_schema']['name'] == 'youtube_review'
    assert compat.schema_for_body(p.payload) == SCHEMA
    assert p._body_bytes == adapter._canonical(p.payload)
    assert observed(p, RESULT).result == RESULT
    assert p.request_sha256 != compatible().request_sha256


@pytest.mark.parametrize('damage', ['duplicate', 'missing', 'extra', 'out_of_range', 'wrong_type', 'enum'])
def test_observer_still_rejects_full_schema_violations(damage):
    schema, result = deepcopy(SCHEMA), deepcopy(RESULT)
    schema['properties']['moments']['items']['enum'] = [0, 1]
    if damage == 'duplicate': result['moments'] = [1, 1]
    elif damage == 'missing': result.pop('accepted')
    elif damage == 'extra': result['invented'] = True
    elif damage == 'out_of_range': result['moments'] = [60]
    elif damage == 'wrong_type': result['moments'] = ['1']
    elif damage == 'enum': result['moments'] = [2]
    p = compatible(schema=schema)
    with pytest.raises(adapter.AbacusRouterError, match='^abacus_router_schema_mismatch$'):
        observed(p, result)


@pytest.mark.parametrize('damage', ['missing_contract', 'wrong_name', 'contract_false', 'native_change',
                                  'duplicate_contract', 'moved_contract', 'noncanonical', 'no_rule'])
def test_request_contract_is_mandatory_and_cannot_be_detached_or_weakened(damage):
    kwargs = compatible().wire_kwargs()
    body = kwargs['json']
    content = body['messages'][1]['content']
    spec = body['response_format']['json_schema']
    if damage == 'missing_contract': content.pop()
    elif damage == 'wrong_name': spec['name'] = 'youtube_review'
    elif damage == 'contract_false':
        schema = deepcopy(SCHEMA); schema['properties']['moments']['uniqueItems'] = 0
        content[-1]['text'] = compat.SCHEMA_PREFIX + adapter._canonical(schema).decode()
    elif damage == 'native_change': spec['schema']['properties']['moments']['maxItems'] = 9
    elif damage == 'duplicate_contract': content.append(deepcopy(content[-1]))
    elif damage == 'moved_contract': content.reverse()
    elif damage == 'noncanonical': content[-1]['text'] = compat.SCHEMA_PREFIX + json.dumps(SCHEMA)
    elif damage == 'no_rule': content[-1]['text'] = compat.SCHEMA_PREFIX + adapter._canonical(spec['schema']).decode()
    with pytest.raises(adapter.AbacusRouterError, match='^abacus_router_request_invalid$'):
        adapter.inspect_router_request(adapter.ENDPOINT, kwargs)


def test_nested_rules_and_property_names_are_preserved_and_locally_enforced():
    schema = {'type': 'object', 'properties': {
        'uniqueItems': {'type': 'array', 'uniqueItems': True, 'items': {
            'type': 'array', 'uniqueItems': True, 'items': {'type': ['number', 'boolean']}}}},
        'required': ['uniqueItems'], 'additionalProperties': False}
    p = compatible(schema=schema)
    assert 'uniqueItems' in p.payload['response_format']['json_schema']['schema']['properties']
    assert compat.schema_for_body(p.payload) == schema
    assert observed(p, {'uniqueItems': [[1, True], [2, False]]}).result['uniqueItems'] == [[1, True], [2, False]]
    for value in ([[1, 1.0]], [[1], [1.0]], [[False, False]]):
        with pytest.raises(adapter.AbacusRouterError, match='^abacus_router_schema_mismatch$'):
            observed(p, {'uniqueItems': value})


def test_mutating_actual_sent_contract_after_reservation_is_rejected():
    p = compatible()
    from test_abacus_router_adapter import wire
    request = wire(p)
    body = json.loads(request.content)
    schema = compat.schema_for_body(body)
    schema['properties']['moments']['uniqueItems'] = False
    body['messages'][1]['content'][-1]['text'] = compat.SCHEMA_PREFIX + adapter._canonical(schema).decode()
    request._content = adapter._canonical(body)
    request.headers['content-length'] = str(len(request._content))
    with pytest.raises(adapter.AbacusRouterError, match='^abacus_router_response_unverified$'):
        adapter.observe_router_response(p, response(p, request=request))


def test_no_compatibility_send_without_an_actual_unsupported_rule():
    schema = deepcopy(SCHEMA)
    schema['properties']['moments'].pop('uniqueItems')
    with pytest.raises(adapter.AbacusRouterError, match='^abacus_router_request_invalid$'):
        compatible(schema=schema)
