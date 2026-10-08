"""Pure source contract parity; all observations/providers remain synthetic."""
from copy import deepcopy
import hashlib
import json
from unittest.mock import Mock

import pytest

from app.services import director
from app.services import immutable_story_review_contract as contract
from app.services.abacus_router_adapter import prepare_router_request
from test_immutable_selected_story import case, planning_case, TOPIC, _route


# Exact prepared bodies include the cost/source rule shipped in ac5c6eb.
# Removing only that 827-byte rule reproduces every original extraction golden;
# fresh writer retry changes must not alter these immutable critic inputs.
# These are synthetic fixtures, never provider or source-authority evidence.
GOLDEN = [
    (6, 'stock', 'en', '597b03a275f87ae44cb864fb98f1f266b2d0e2d98569ece929860682927b80a3', 39602),
    (6, 'mixed', 'en', '576740d3807a0a0ced4c5aa92d6e896a23d32fa4f935040433a151d5a86fd3b6', 38119),
    (6, 'generated', 'en', '383b7989289206d5044c703ca2a0e39b4909fb2472a0c12562ce83987955bbc7', 35662),
    (12, 'stock', 'en', 'a895205aba556a512a16db654a52dd123cf49efe3efbf8c4d97d54ce5ba6f754', 47175),
    (12, 'mixed', 'en', 'b92e70d1e3b19e45e402d22e10c6ee92b915724df8ea4186cd903601d03a739b', 44345),
    (12, 'generated', 'en', 'ac9f72621541fa60363ae64fa859bdf89cd4244e67e0896de1da99d11f9c79fd', 40533),
    (6, 'generated', 'tr', '778a289d43546782e417676211b8d703ee727de1b9182d31cf52146a30704d21', 35667),
]


def derive(case, *, language='en', **kwargs):
    return contract.derive_immutable_story_review_contract(case.package, TOPIC, .5,
        language, case.options, **{'immutable_candidate_narrations': [s['narration']
        for s in case.package['scenes']], **kwargs})


def body(value):
    request = deepcopy(value['request'])
    request.pop('purpose')
    return prepare_router_request(request.pop('parts'), api_key='offline-golden-key',
                                  **request)._body_bytes


@pytest.mark.parametrize('count,kind,language,digest,size', GOLDEN)
def test_exact_source_guard_bytes_and_live_director_share_contract(case, count, kind, language, digest, size):
    _route(case, count, kind)
    before = deepcopy(case.package)
    value = derive(case, language=language)
    assert set(value) == {'candidate', 'request', 'semantic_arguments'}
    request = value['request']
    assert set(request) == {'parts', 'purpose', 'system_instruction', 'json_schema', 'max_tokens'}
    assert request['purpose'] == 'immutable_story_review' and request['max_tokens'] == 8192
    raw = body(value)
    assert hashlib.sha256(raw).hexdigest() == digest and len(raw) == size
    case.model.assert_not_called()
    director.revalidate_immutable_short_story(case.package, TOPIC, .5, language, case.options,
        immutable_candidate_narrations=[s['narration'] for s in case.package['scenes']],
        immutable_scene_fields=True)
    assert case.model.call_count == 1
    assert case.model.call_args.args[0] == request['parts'][0]['text']
    assert case.model.call_args.kwargs['json_schema'] == request['json_schema']
    assert case.package == before
    assert value['candidate'] == {k: v for k, v in before.items() if k not in ('stock_scene_qc', 'short_story_qc')}
    result = json.JSONDecoder().raw_decode(request['parts'][0]['text'].split(
        'Return ONLY JSON in exactly this shape:\n', 1)[1])[0]
    semantics = director.validate_stock_story_critic(result, **value['semantic_arguments'])
    assert not semantics['critic_global_error'] and not semantics['story_failure']
    assert not semantics['critic_failures'] and not semantics['ending_failed_checks']


def test_derivation_has_no_runtime_provider_observer_or_approval_dependency(case, monkeypatch):
    from app.services import abacus_router_review_runtime as runtime
    from app.services import abacus_router_adapter as adapter
    def forbidden(*a, **k):
        raise AssertionError('Pure contract must not acquire authority or send')
    for name in ('_retained_router_story_mode', '_studio_plan_provider', '_studio_plan_openai_model',
                 'paid_response', 'generate_gemini_json', 'short_story_package_is_approved'):
        monkeypatch.setattr(director, name, forbidden)
    for name in ('retained_router_review_active', 'retained_router_review_approval_active',
                 'generate_retained_router_review', 'retained_router_review_evidence'):
        monkeypatch.setattr(runtime, name, forbidden)
    monkeypatch.setattr(adapter, 'observe_router_response', forbidden)
    old = director._INCLUDED_STORY_APPROVAL.get()
    value = derive(case)
    assert director._INCLUDED_STORY_APPROVAL.get() is old
    assert 'qa_approved' not in value and 'stock_scene_qc' not in value['candidate']


def test_detached_output_and_old_markers_cannot_change_rederived_inputs(case):
    before = deepcopy(case.package)
    first = derive(case)
    expected = deepcopy(first)
    first['candidate']['scenes'][0]['custom_identity']['shirt'] = 'changed'
    first['request']['json_schema'].clear()
    first['semantic_arguments']['stock_positions'].clear()
    assert derive(case) == expected and case.package == before
    case.package['stock_scene_qc'] = {'caller_forged': True}
    case.package['short_story_qc'] = {'approved': True}
    assert derive(case) == expected


@pytest.mark.parametrize('damage', ['narration', 'partial', 'missing', 'five_scenes',
    'mode', 'format', 'sources', 'budget_marker', 'total_words', 'scene_words'])
def test_existing_deterministic_source_eligibility_is_required_without_sender(case, damage):
    _route(case, 6, 'generated')
    narrations = [s['narration'] for s in case.package['scenes']]
    if damage == 'narration': narrations[0] += ' changed'
    if damage == 'partial': narrations.pop()
    if damage == 'missing': narrations = None
    if damage == 'five_scenes':
        case.package['scenes'].pop(); narrations.pop()
    if damage == 'mode': case.options['mode'] = 'other'
    if damage == 'format': case.options['format'] = 'landscape'
    if damage == 'sources': case.package['sources'] = []
    if damage == 'budget_marker': case.package['spoken_word_budget'] = {'minimum_words': 1}
    if damage in ('total_words', 'scene_words'):
        for row in case.package['scenes']:
            row['narration'] = 'Parcel.' if damage == 'total_words' else ('Many parcels arrive ' * 20).strip() + '.'
        narrations = [s['narration'] for s in case.package['scenes']]
    with pytest.raises((RuntimeError, ValueError)):
        derive(case, immutable_candidate_narrations=narrations)
    case.model.assert_not_called()


@pytest.mark.parametrize('damage', ['two_sentences', 'technical', 'abstract', 'query_count',
    'duplicate_query', 'nonenglish_query', 'long_query', 'trailing_query'])
def test_locked_stock_row_gates_match_live_precritic_rejection(case, damage):
    _route(case, 6, 'stock')
    row = case.package['scenes'][1]
    if damage == 'two_sentences': row['narration'] = 'Workers carry parcels. Buyers pay the membership fee.'
    if damage == 'technical': row['narration'] = 'Workers carry GPS parcels across the ordinary warehouse floor.'
    if damage == 'abstract': row['narration'] = 'Magic parcels bring hidden systems into the ordinary warehouse.'
    if damage == 'query_count': row['visual_queries'] = ['warehouse worker moves parcels']
    if damage == 'duplicate_query': row['visual_queries'] = ['warehouse worker moves parcels'] * 2
    if damage == 'nonenglish_query': row['visual_queries'][0] = 'çalışan kutuları sıraya dizer'
    if damage == 'long_query': row['visual_queries'][0] = 'warehouse worker carries very many ordinary cardboard parcels across the whole large warehouse'
    if damage == 'trailing_query': row['visual_queries'][0] += ' '
    with pytest.raises((RuntimeError, ValueError)):
        derive(case)
    with pytest.raises(RuntimeError):
        director.revalidate_immutable_short_story(case.package, TOPIC, .5, 'en', case.options,
            immutable_candidate_narrations=[s['narration'] for s in case.package['scenes']],
            immutable_scene_fields=True)
    case.model.assert_not_called()


@pytest.mark.parametrize('field', ['title', 'description', 'sources', 'custom_scene', 'narration'])
def test_every_complete_immutable_input_is_visible_in_request_binding(case, field):
    original = body(derive(case))
    if field in ('title', 'description'): case.package[field] += ' Changed original context.'
    if field == 'sources': case.package['sources'][0]['evidence'] += ' Another supplied fact.'
    if field == 'custom_scene': case.package['scenes'][0]['custom_identity']['shirt'] = 'green'
    if field == 'narration':
        case.package['scenes'][0]['narration'] = 'Members pay the annual fee before entering the warehouse.'
        case.package['scenes'][0]['tts_text'] = case.package['scenes'][0]['narration']
        case.package['narration'] = case.package['tts_narration'] = ' '.join(
            row['narration'] for row in case.package['scenes'])
    assert body(derive(case)) != original
    case.model.assert_not_called()
