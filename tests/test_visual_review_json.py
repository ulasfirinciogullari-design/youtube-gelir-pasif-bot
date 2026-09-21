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


@pytest.mark.parametrize('damage', ['missing_value', 'duplicate', 'unknown_key', 'wrong_scope', 'missing_closing_quote'])
def test_other_json_failures_are_not_guessed_or_accepted(damage):
    schema, value = sample();raw = json.dumps(value)
    if damage == 'missing_value': raw = raw.replace('"score": 30', '"score":')
    elif damage == 'duplicate': raw = raw.replace('"score": 30', 'score": 30, "score": 99')
    elif damage == 'unknown_key': raw = raw.replace('"score": 30', 'approved": true')
    elif damage == 'missing_closing_quote': raw = raw.replace('"score": 30', '"score: 30')
    else: schema = {'type': 'object'};raw = raw.replace('"score": 30', 'score": 30')
    with pytest.raises((ValueError, TypeError)): decoder.decode(raw, schema)
