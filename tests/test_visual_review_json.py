from copy import deepcopy
import json

import pytest

from app.services import visual_review_json as decoder, visual_qc
from app.services.abacus_router_adapter import _matches_schema


def sample():
    schema = visual_qc._review_json_schema([0], {0: {0: {0, 1, 2}}})
    fields = schema['properties']['reviews']['items']['properties']
    row = {key: False for key, spec in fields.items() if spec == {'type': 'boolean'}}
    row.update(scene_index=0, best_candidate_index=0, best_moment_index=1, score=30,
        reason='Wrong subject in this clip.', retry_queries=['furniture cargo'], evidence_moment_indices=[1])
    return schema, {'reviews': [row]}


def test_missing_key_quote_preserves_negative_score_and_every_observed_value():
    schema, value = sample();before = deepcopy(value)
    raw = json.dumps(value).replace('"retry_queries":', 'retry_queries":')
    assert decoder.decode(raw, schema) == before and _matches_schema(before, schema)
    assert before['reviews'][0]['score'] == 30
    assert before['reviews'][0]['subject_visible'] is False


def test_text_inside_strings_including_escaped_quotes_is_never_repaired():
    schema, value = sample()
    value['reviews'][0]['reason'] = 'Evidence text ,retry_queries": and \\ paths stays literal.'
    original = json.dumps(value)
    assert decoder.decode(original, schema) == value
    raw = original.replace('"evidence_moment_indices":', 'evidence_moment_indices":')
    assert decoder.decode(raw, schema) == value


@pytest.mark.parametrize('spelling', ['candidate_index', 'candidate_index"', '"candidate_index"'])
def test_redundant_candidate_annotation_never_changes_the_selected_clip(spelling):
    schema, value = sample()
    raw = json.dumps(value).replace('"score": 30', spelling + ': 0, "score": 30')
    assert decoder.decode(raw, schema) == value
    assert value['reviews'][0]['score'] == 30


@pytest.mark.parametrize('annotation', [1, -1, True, 0.0, '0', None])
def test_candidate_annotation_must_be_an_exact_same_integer(annotation):
    schema, value = sample()
    value['reviews'][0]['candidate_index'] = annotation
    with pytest.raises(ValueError): decoder.decode(json.dumps(value), schema)


def test_candidate_annotation_cannot_supply_missing_selection_or_hide_duplicates():
    schema, value = sample()
    raw = json.dumps(value).replace('"best_candidate_index": 0', 'candidate_index: 0')
    with pytest.raises(ValueError): decoder.decode(raw, schema)
    raw = json.dumps(value).replace('"score": 30', 'candidate_index: 0, candidate_index: 0, "score": 30')
    with pytest.raises(ValueError): decoder.decode(raw, schema)


def test_bare_known_keys_preserve_literal_strings_and_false_evidence():
    schema, value = sample()
    value['reviews'][0]['reason'] = 'Literal , candidate_index: 1 and ,score: 99 remain evidence.'
    raw = json.dumps(value).replace('"subject_visible":', 'subject_visible:')
    assert decoder.decode(raw, schema) == value
    assert decoder.decode(json.dumps(value), {'type': 'object'}) == value


def test_key_repairs_are_bounded():
    schema, value = sample()
    fields = list(schema['properties']['reviews']['items']['properties'])[:9]
    raw = json.dumps(value)
    for field in fields: raw = raw.replace('"' + field + '":', field + ':')
    with pytest.raises(ValueError, match='bound'): decoder.decode(raw, schema)


@pytest.mark.parametrize('value', [False, True])
def test_identical_boolean_repetition_preserves_original_judgment(value):
    schema, data = sample()
    data['reviews'][0]['subject_visible'] = value
    raw = json.dumps(data)
    field = '"subject_visible": ' + json.dumps(value)
    assert decoder.decode(raw.replace(field, field + ', ' + field), schema) == data


@pytest.mark.parametrize('second', ['true', '0', '0.0', 'null', '"false"'])
def test_contradictory_or_wrong_typed_repeated_evidence_is_rejected(second):
    schema, data = sample()
    raw = json.dumps(data).replace('"subject_visible": false',
        '"subject_visible": false, "subject_visible": ' + second)
    with pytest.raises(ValueError): decoder.decode(raw, schema)


@pytest.mark.parametrize('damage', ['third', 'score', 'nested', 'root', 'unknown'])
def test_boolean_duplicate_exception_does_not_weaken_other_nodes(damage):
    schema, data = sample()
    raw = json.dumps(data)
    if damage == 'third':
        raw = raw.replace('"subject_visible": false', ', '.join(['"subject_visible": false'] * 3))
    elif damage == 'score': raw = raw.replace('"score": 30', '"score": 30, "score": 30')
    elif damage == 'nested': raw = raw.replace('"score": 30', '"score": 30, "nested":{"subject_visible":false,"subject_visible":false}')
    elif damage == 'root': raw = raw[:-1] + ', "subject_visible":false, "subject_visible":false}'
    else: raw = raw.replace('"subject_visible": false', '"accepted":false, "accepted":false')
    with pytest.raises(ValueError): decoder.decode(raw, schema)


@pytest.mark.parametrize('damage', ['missing_value', 'duplicate', 'unknown_key', 'wrong_scope', 'missing_closing_quote'])
def test_other_json_failures_are_not_guessed_or_accepted(damage):
    schema, value = sample();raw = json.dumps(value)
    if damage == 'missing_value': raw = raw.replace('"score": 30', '"score":')
    elif damage == 'duplicate': raw = raw.replace('"score": 30', 'score": 30, "score": 99')
    elif damage == 'unknown_key': raw = raw.replace('"score": 30', 'approved": true')
    elif damage == 'missing_closing_quote': raw = raw.replace('"score": 30', '"score: 30')
    else: schema = {'type': 'object'};raw = raw.replace('"score": 30', 'score": 30')
    with pytest.raises((ValueError, TypeError)): decoder.decode(raw, schema)
