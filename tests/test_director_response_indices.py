from copy import deepcopy
from unittest.mock import Mock

import pytest

from app.services import director, director_response_indices as indices, production_included_router as included
from app.services.abacus_router_adapter import _matches_schema
from app.services.production_failures import ProductionContentError


def value():
    return {'title': 'A title', 'thumbnail_text': 'One hook', 'description': 'A description',
        'qc_summary': ['A writer claim, not independent approval'], 'scenes': [
            {'index': i, 'narration': 'An unchanged complete sentence.', 'ai_prompt': None,
             'visual_queries': ['first stock query', 'second stock query'], 'pace': 'normal', 'transition': 'cut'}
            for i in range(6)]}


def test_observed_array_order_and_all_actual_words_preserved_without_trusting_writer_indices():
    schema = director._director_json_schema(6, exact_scene_count=True)
    original = deepcopy(schema); data = value(); before = deepcopy(data)
    assert not _matches_schema(data, schema)
    assert _matches_schema(data, indices.schema_with_indices(schema))
    decoded = indices.decode(data)
    assert schema == original and data == before and _matches_schema(decoded, schema)
    assert decoded == {**data, 'scenes': [{k: v for k, v in row.items() if k != 'index'} for row in data['scenes']]}
    assert 'qa_approved' not in decoded
    assert indices.decode(decoded) == decoded


@pytest.mark.parametrize('index', [True, '0', 1, -1, None])
def test_conflicting_indices_are_never_reordered_or_inferred(index):
    data = value();data['scenes'][0]['index'] = index;before = deepcopy(data)
    with pytest.raises(ProductionContentError): indices.decode(data)
    assert data == before


def test_unknown_fields_and_approval_claims_still_fail_full_schema():
    data = value();schema = indices.schema_with_indices(director._director_json_schema(6, exact_scene_count=True))
    data['scenes'][0]['qa_approved'] = True
    assert not _matches_schema(data, schema)


def test_actual_included_director_normalizes_observed_indices_once_before_independent_review(monkeypatch):
    monkeypatch.setattr(director, '_studio_plan_provider', lambda: 'abacus_included')
    output = value();before = deepcopy(output)
    call = Mock(return_value=output);monkeypatch.setattr(included, 'generate_text_json', call)
    result = director._run_director(None, {'current_word_count': 66, 'scenes': []},
        'Business documentary', 'English', .5, 65, 62, 66, 6, {'content_style': 'documentary'},
        exact_scene_count=True, fresh_scheduled=True)
    assert result == indices.decode(before) and output == before and call.call_count == 1
    assert _matches_schema(output, call.call_args.args[1])
    assert not any('approved' in key for key in result)
