"""A small wire grammar still requires the complete original visual contract."""
from copy import deepcopy
import json

import pytest

from app.services import commissioning_reasoning as native
from app.services.abacus_router_schema_compat import prepare_json_object_router_request
from app.services.production_spend import SpendBlocked
from app.services.visual_qc import _review_json_schema
from test_abacus_router_adapter import KEY, image
from test_commissioning_reasoning import setup, run, payload, records
from test_production_included_router import commissioned, client


def visual():
    schema = _review_json_schema(list(range(6)), {n: {0: {0, 1, 2}} for n in range(6)})
    parts = [{'type': 'text', 'text': 'Original complete story and strict rubric.'}]
    for index in range(60):
        parts.extend([{'type': 'text', 'text': f'Original exact frame {index}'}, image()])
    prepared = prepare_json_object_router_request(parts, api_key=KEY,
        system_instruction='Reject wrong scenes, never lower the quality threshold.', json_schema=schema)
    result = {'reviews': []}
    for index in range(6):
        row = {key: False for key, value in schema['properties']['reviews']['items']['properties'].items()
               if value['type'] == 'boolean'}
        row.update(scene_index=index, best_candidate_index=0, best_moment_index=0, score=12,
                   reason='Scene does not show the claimed mechanism.', retry_queries=[], evidence_moment_indices=[0])
        result['reviews'].append(row)
    return prepared, schema, result


def test_all_frames_rubric_and_complete_schema_are_bound_with_wire_shape():
    prepared, schema, _ = visual()
    original = deepcopy(prepared.payload)
    body, checked, _ = native._request(prepared, 'visual_review')
    assert checked == schema and prepared.payload == original
    assert body['generationConfig']['responseMimeType'] == 'application/json'
    shape = body['generationConfig']['responseJsonSchema']
    assert shape == native._visual_response_shape(schema) and shape != schema
    def compare(original, wire):
        assert wire['type'] == original['type']
        assert set(wire) <= {'type', 'properties', 'required', 'additionalProperties', 'items'}
        if original['type'] == 'object':
            assert wire['required'] == original['required']
            assert wire['additionalProperties'] is False
            assert set(wire['properties']) == set(original['properties'])
            for key in original['properties']:compare(original['properties'][key],wire['properties'][key])
        elif original['type'] == 'array':compare(original['items'],wire['items'])
    compare(schema,shape)
    assert body['systemInstruction']['parts'][0]['text'] == original['messages'][0]['content']
    parts = body['contents'][0]['parts']
    assert len([p for p in parts if 'inlineData' in p]) == 60
    assert [p['inlineData']['data'] for p in parts if 'inlineData' in p] == [
        p['image_url']['url'].split(',', 1)[1] for p in original['messages'][1]['content'] if p['type'] == 'image_url']
    assert json.loads(parts[-1]['text'][len(native.VISUAL_SCHEMA_PREFIX):]) == schema
    assert parts[-1]['text'].startswith(native.VISUAL_SCHEMA_PREFIX)


def test_negative_visual_verdict_is_preserved_and_reused_without_http(setup):
    prepared, _, result = visual()
    setup[2].return_value = (200, json.dumps(payload(result)).encode())
    assert run(setup, prepared, 'visual_review') == result
    assert run(setup, prepared, 'visual_review') == result
    setup[2].assert_called_once()
    assert len(records(setup)) == 1


@pytest.mark.parametrize('damage', ['duplicate_moment', 'wrong_enum', 'wrong_count',
                                   'score_overflow', 'extra_key', 'empty_reason', 'string_boolean'])
def test_wire_shape_still_rejects_every_original_visual_constraint(setup, damage):
    prepared, schema, result = visual()
    row = result['reviews'][0]
    boolean = next(key for key, value in schema['properties']['reviews']['items']['properties'].items()
                   if value['type'] == 'boolean')
    if damage == 'missing_flag': row.pop(boolean)
    if damage == 'duplicate_moment': row['evidence_moment_indices'] = [0, 0]
    if damage == 'wrong_enum': row['scene_index'] = 6
    if damage == 'wrong_count': result['reviews'].pop()
    if damage == 'score_overflow': row['score'] = 101
    if damage == 'extra_key': row['approved_by_operator'] = True
    if damage == 'empty_reason': row['reason'] = ''
    if damage == 'string_boolean': row[boolean] = 'false'
    setup[2].return_value = (200, json.dumps(payload(result)).encode())
    for _ in range(2):
        with pytest.raises(SpendBlocked, match='response_unverified'):
            run(setup, prepared, 'visual_review')
    setup[2].assert_called_once()
    assert len(records(setup)) == 1
