from copy import deepcopy
import hashlib
import json

import pytest

from app.services import abacus_router_adapter as adapter, response_object_metadata as metadata
from test_abacus_router_adapter import KEY, response, envelope
from app.services.abacus_router_schema_compat import prepare_json_object_router_request


def contract():
    row = {'type': 'object', 'properties': {'accepted': {'type': 'boolean'},
        'reason': {'type': 'string'}}, 'required': ['accepted', 'reason'], 'additionalProperties': False}
    return {'type': 'object', 'properties': {'reviews': {'type': 'array', 'items': row}},
        'required': ['reviews'], 'additionalProperties': False}


def observe(value, schema=None, raw=None):
    prepared = prepare_json_object_router_request([{'type': 'text', 'text': 'Unapproved test review'}],
        api_key=KEY, system_instruction='Evaluate independently.', json_schema=schema or contract())
    body = envelope();body['choices'][0]['message']['content'] = raw or json.dumps(value)
    wire = response(prepared, payload=body)
    return adapter.observe_router_response(prepared, wire), wire


def test_only_exact_schema_annotation_is_removed_and_negative_verdict_is_unchanged():
    value = {'type': 'object', 'reviews': [{'type': 'object', 'accepted': False, 'reason': 'Rejected.'}]}
    original = deepcopy(value);observed, wire = observe(value)
    assert value == original
    assert observed.result == {'reviews': [{'accepted': False, 'reason': 'Rejected.'}]}
    assert observed.evidence['response_body_sha256'] == hashlib.sha256(wire.content).hexdigest()
    assert json.loads(json.loads(wire.content)['choices'][0]['message']['content']) == original


@pytest.mark.parametrize('damage', ['wrong_type', 'boolean_type', 'new_verdict', 'missing_verdict', 'duplicate', 'unknown_field'])
def test_annotations_cannot_fill_missing_values_change_types_or_admit_unknown_fields(damage):
    value = {'reviews': [{'type': 'object', 'accepted': False, 'reason': 'Rejected.'}]}
    row = value['reviews'][0]
    if damage == 'wrong_type': row['type'] = 'array'
    elif damage == 'boolean_type': row['type'] = True
    elif damage == 'new_verdict': row['qa_approved'] = True
    elif damage == 'missing_verdict': row.pop('accepted')
    elif damage == 'unknown_field': row['schema'] = {'approved': True}
    raw = json.dumps(value)
    if damage == 'duplicate': raw = raw.replace('"type": "object"', '"type": "object", "type": "object"')
    with pytest.raises(adapter.AbacusRouterError): observe(value, raw=raw)


def test_type_is_never_removed_if_it_is_an_actual_authored_content_field():
    schema = {'type': 'object', 'properties': {'type': {'type': 'string'}},
        'required': ['type'], 'additionalProperties': False}
    value = {'type': 'object'}
    assert observe(value, schema)[0].result == value
    assert metadata.decode(value, schema) == value


def test_metadata_never_coerces_an_object_to_a_required_array():
    with pytest.raises(adapter.AbacusRouterError):
        observe({'reviews': {'type': 'array', 'items': []}})
