from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from app.services import commissioning_reasoning as native, director, director_response_indices as indices
from app.services.abacus_router_schema_compat import prepare_json_object_router_request
from app.services.production_spend import SpendBlocked
from test_commissioning_longform import long_case, setup, commissioned, client
from test_commissioning_reasoning import payload
from test_abacus_router_adapter import KEY


def prepared_request():
    schema = indices.schema_with_indices(director._director_json_schema(30, exact_scene_count=True))
    return prepare_json_object_router_request([{'type': 'text', 'text': 'Complete this documentary.'}],
        api_key=KEY, system_instruction='Follow all constraints.', json_schema=schema), schema


def answer():
    return {'title': 'Test documentary', 'description': 'A complete documentary.', 'thumbnail_text': 'Banknotes',
        'qc_summary': ['Use the supplied primary sources.'], 'scenes': [
            {'narration': 'This sentence belongs to the complete documentary being checked.',
             'visual_queries': ['banknote close up', 'cash handling'], 'ai_prompt': None,
             'pace': 'normal', 'transition': 'cut', 'index': i} for i in range(30)]}


def test_compact_wire_keeps_typed_fields_and_full_original_schema_without_changing_shorts():
    request, schema = prepared_request(); before = deepcopy(request.payload)
    ordinary, original, ceiling = native._request(request, 'editorial')
    long, full, same_ceiling = native._request(request, 'editorial', long_form=True)
    assert original == full == schema and request.payload == before and ceiling == same_ceiling
    assert ordinary['generationConfig']['responseJsonSchema'] == schema
    scenes = long['generationConfig']['responseJsonSchema']['properties']['scenes']
    assert 'minItems' not in scenes and 'maxItems' not in scenes
    assert scenes['items']['required'] == schema['properties']['scenes']['items']['required']
    assert scenes['items']['properties']['ai_prompt'] == {'type': ['string', 'null']}
    assert scenes['items']['properties']['pace']['enum'] == ['fast', 'normal', 'slow']
    assert long['contents'][0]['parts'][-1]['text'] == native.LONG_SCHEMA_PREFIX + native._raw(schema)
    assert native._request(request, 'research', long_form=True)[0] == native._request(request, 'research')[0]


@pytest.mark.parametrize('damage', [None, 'missing_last_scene', 'extra_scene', 'wrong_enum', 'wrong_type', 'missing_field'])
def test_full_local_contract_still_rejects_invalid_long_director_results(long_case, damage):
    ledger, _, sender, context, *_ = long_case
    request, schema = prepared_request(); value = answer()
    if damage == 'missing_last_scene': value['scenes'].pop()
    if damage == 'extra_scene': value['scenes'].append(deepcopy(value['scenes'][-1]))
    if damage == 'wrong_enum': value['scenes'][-1]['transition'] = 'unapproved'
    if damage == 'wrong_type': value['scenes'][-1]['pace'] = True
    if damage == 'missing_field': del value['scenes'][-1]['narration']
    sender.return_value = (200, json.dumps(payload(value)).encode())
    if damage:
        with pytest.raises(SpendBlocked): native.generate(request, 'editorial', ledger, ledger.foundation, context)
    else:
        assert native.generate(request, 'editorial', ledger, ledger.foundation, context) == value
    sender.assert_called_once()
