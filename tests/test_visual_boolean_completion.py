"""Missing evidence is independently completed; no existing judgment changes."""
from copy import deepcopy
from pathlib import Path
import json

import pytest

from test_included_visual_completion import case, run, journal
from test_production_included_router import commissioned
from test_production_credit_ledger import client
from test_abacus_router_adapter import image
from test_production_cash_disabled import dump
from app.services import included_visual_completion as completion
from app.services.production_spend import SpendBlocked


def configure(case, missing):
    case.partial.clear();case.partial.update(deepcopy(case.complete))
    for row in case.partial['reviews']:
        for field in missing:
            row.pop(field)
    case.fields.clear();case.fields.update({'reviews': [
        {field: row[field] for field in ('scene_index', *missing)} for row in case.complete['reviews']]})


@pytest.mark.parametrize('missing', [
    {'state_change_applicable', 'state_changed_after_action'},
    {'subject_visible'}, {'spoken_action_visible', 'target_contact_visible'},
    {'major_visual_artifact_visible', 'effectively_static_or_frozen'},
    {*completion.OMITTABLE, 'state_change_applicable', 'state_changed_after_action'},
])
def test_each_missing_boolean_set_gets_one_same_frame_observation_and_cached_reuse(case, missing):
    configure(case, missing)
    original = deepcopy(case.partial)
    assert run(case) == case.complete
    assert case.partial == original and len(case.calls) == 2
    initial, repair = case.calls
    assert repair.payload['messages'][0]['content'].startswith('VISUAL_MISSING_BOOLEAN_FIELDS_V3\n')
    assert initial.payload['messages'][1]['content'][:-1] == repair.payload['messages'][1]['content'][:-1]
    source = next(row for row in journal(case)['requests'].values() if row['outcome'] is None)
    assert source['completion']['format'] == 'missing_fields_v3'
    before = dump(case.ledger.client)
    assert run(case) == case.complete and len(case.calls) == 2
    assert dump(case.ledger.client) == before


@pytest.mark.parametrize('field', ['score', 'reason', 'best_candidate_index', 'best_moment_index',
    'retry_queries', 'evidence_moment_indices', 'scene_index'])
def test_missing_judgment_or_selection_never_enters_boolean_completion(case, field):
    configure(case, {'state_change_applicable'})
    case.partial['reviews'][0].pop(field)
    with pytest.raises(SpendBlocked, match='response_unverified'):
        run(case)
    assert len(case.calls) == 1 and len(journal(case)['requests']) == 1


def test_existing_negative_evidence_cannot_be_changed_when_other_scenes_omit_it(case):
    configure(case, {'subject_visible'})
    case.partial['reviews'][0]['subject_visible'] = False
    case.fields['reviews'][0]['subject_visible'] = True
    with pytest.raises(SpendBlocked, match='completion_unverified'):
        run(case)
    assert len(case.calls) == 2
    assert all('completion' not in row for row in journal(case)['requests'].values())


def test_second_missing_field_reply_does_not_start_another_completion(case):
    configure(case, {'state_change_applicable'})
    case.fields['reviews'][0].pop('state_change_applicable')
    with pytest.raises(SpendBlocked):
        run(case)
    with pytest.raises(SpendBlocked, match='previous_outcome_unknown'):
        run(case)
    assert len(case.calls) == 2


def test_actual_margin_review_preserves_both_rejections_after_observed_completion(case):
    fixture = json.loads((Path(__file__).parent/'fixtures/margin_incomplete_visual_booleans_20260922.json').read_text())
    partial = fixture['original_provider_review']
    before = deepcopy(partial)
    fields = {'reviews': [{'scene_index': row['scene_index'], 'state_change_applicable': False,
        'state_changed_after_action': False} for row in partial['reviews']]}
    case.values[:] = [partial, fields]
    content = [{'type': 'input_text', 'text': 'Original six-scene documentary rubric.'},
        {'type': 'input_text', 'text': 'Unaltered selected scene frames.'},
        {'type': 'input_image', 'image_url': image()['image_url']['url']}]
    def request():
        return case.ns['_request_visual_review']('abacus_included', True, 'Full original rubric.',
            content, [], list(range(6)), {i: {0: {0, 1, 2}} for i in range(6)}, None, 'low')
    result = request()
    assert partial == before and len(case.calls) == 2
    for prior, current in zip(partial['reviews'], result['reviews']):
        assert all(current[key] == value and type(current[key]) is type(value) for key, value in prior.items())
    assert result['reviews'][0]['score'] == 32 and result['reviews'][5]['score'] == 38
    assert result['reviews'][0]['subject_visible'] is False
    assert result['reviews'][5]['subject_visible'] is False
    assert request() == result and len(case.calls) == 2
