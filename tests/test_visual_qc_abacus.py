"""Actual reviewer bodies, native image adapter and offline funded transport."""
import base64
from contextlib import nullcontext
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from app.services import abacus_visual_generation as generation
from app.services import production_spend_runtime as runtime
from app.services.abacus_generation import AbacusConfigurationError, AbacusGenerationError
from app.services.production_spend import SpendBlocked
from test_abacus_visual_generation import case, _jpeg, _parts, _response, KEY
from test_visual_cross_provider_review import _namespace, _review, EVIDENCE


@pytest.fixture
def reviewer(case, monkeypatch):
    # Execute all real reviewer functions. Only settings, frame extraction and
    # external transport are isolated; provider routing and QA branches run.
    ns = _namespace()
    ns['settings'].studio_visual_qc_provider = 'abacus'
    ns['settings'].abacus_api_key = KEY
    raw = base64.b64decode(_jpeg())
    ns['_frame'] = Mock(side_effect=lambda *args: SimpleNamespace(read_bytes=lambda: raw))
    ns['_bounded_gemini_frame_bytes'] = Mock(side_effect=AssertionError('Abacus frames must not shrink'))
    ns['_request_visual_review'] = Mock(wraps=ns['_request_visual_review'])
    return ns


def _queue(case, *outputs):
    case.sender.side_effect = [
        output if isinstance(output, Exception) else nullcontext(httpx.Response(200, json=_response(
            id=f'msg_visual_{index}', content=[{'type': 'text', 'text': json.dumps(output)}])))
        for index, output in enumerate(outputs)
    ]


def _run(ns, work, *, count=1, candidates=1, **options):
    scenes = [{'index': index, 'narration': f'Banknote material fact {index}.',
               'visual_queries': [f'dollar banknote material {index}'], 'ai_prompt': None}
              for index in range(count)]
    return ns['review_scene_visuals'](
        scenes, [[{'path': f'scene-{index}-candidate-{candidate}.mp4', 'source_type': 'stock',
                   'source_id': f'asset-{index}-{candidate}'} for candidate in range(candidates)]
                 for index in range(count)], work, count, _missing_review_attempts=0,
        topic='TOPIC MARKER: currency materials', story_scenes=scenes,
        content_style='documentary', evidence_sources=EVIDENCE, **options)


def _body(case, index=0):
    return case.sender.call_args_list[index].kwargs['json']


def test_twelve_scenes_sixty_exact_frames_preserve_full_rubric_schema_and_context(case, reviewer, tmp_path):
    ns = reviewer
    _queue(case, {'reviews': [_review(ns, index=index) for index in range(12)]})
    result = _run(ns, tmp_path, count=12)
    case.sender.assert_called_once()
    ns['_frame'].assert_called()
    assert ns['_frame'].call_count == 60
    ns['_bounded_gemini_frame_bytes'].assert_not_called()
    ns['OpenAI'].assert_not_called()
    ns['generate_gemini_multimodal_json'].assert_not_called()
    request = dict(zip(('provider', 'strict_review_contract', 'instruction', 'content', 'gemini_parts',
                        'included_indices', 'available_moments', 'model_override', 'thinking_level'),
                       ns['_request_visual_review'].call_args.args, strict=True))
    assert request['strict_review_contract'] is True
    body = _body(case)
    assert body['model'] == 'claude-haiku-4-5-20251001'
    assert body['thinking'] == {'type': 'disabled'} and body['stream'] is False
    instruction, encoded_schema = body['system'].split(generation._SCHEMA_PREFIX)
    assert instruction == request['instruction']
    schema = json.loads(encoded_schema)
    assert schema == ns['_review_json_schema'](request['included_indices'], request['available_moments'])
    assert schema['properties']['reviews']['minItems'] == schema['properties']['reviews']['maxItems'] == 12
    assert schema['properties']['reviews']['items']['properties']['evidence_moment_indices']['uniqueItems'] is True
    parts = body['messages'][0]['content']
    assert len(parts) == 133
    assert len(parts) == len(request['content']) - 1
    for original, native in zip(request['content'][1:], parts, strict=True):
        if original['type'] == 'input_text':
            assert native == {'type': 'text', 'text': original['text']}
        else:
            assert native == {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg',
                'data': original['image_url'].removeprefix('data:image/jpeg;base64,')}}
            assert base64.b64decode(native['source']['data']) == base64.b64decode(_jpeg())
    text = '\n'.join(part['text'] for part in parts if part['type'] == 'text')
    assert 'TOPIC MARKER' in text and 'SOURCE MARKER' in text
    assert all(f'Banknote material fact {index}.' in text for index in range(12))
    assert all(f'REVIEW SCENE ID {index}\n' in text for index in range(12))
    assert 'cross-scene continuity' in instruction and 'A score of 86+' in instruction
    assert 'SERVER-AUTHORED CANDIDATE MEDIA PROVENANCE' in text
    assert len(result['reviews']) == 12 and not result['missing_review_indices']
    assert all(row['score'] == 92 for row in result['reviews'])
    assert case.ledger.snapshot()['period']['used_micro'] == 240_960
    case.record.assert_called_once()


@pytest.mark.parametrize('provider', ['abacus', ' ABACUS '])
def test_explicit_override_selects_only_abacus_without_changing_default(case, reviewer, tmp_path, provider):
    reviewer['settings'].studio_visual_qc_provider = ''
    _queue(case, {'reviews': [_review(reviewer)]})
    assert _run(reviewer, tmp_path, provider_override=provider)['reviews'][0]['score'] == 92
    assert reviewer['settings'].studio_plan_provider == 'gemini'
    assert reviewer['settings'].studio_visual_qc_provider == ''
    case.sender.assert_called_once()
    reviewer['generate_gemini_multimodal_json'].assert_not_called()


@pytest.mark.parametrize('problem', ['gemini_override', 'enforcement_off', 'missing_key'])
def test_abacus_configuration_blocks_before_frames_or_spend(case, reviewer, monkeypatch, tmp_path, problem):
    options = {}
    if problem == 'gemini_override': options['gemini_model_override'] = 'gemini-3.1-pro-preview'
    elif problem == 'enforcement_off': monkeypatch.setattr(runtime, 'settings', SimpleNamespace(studio_spend_enforcement=False))
    else: reviewer['settings'].abacus_api_key = ''
    with pytest.raises((AbacusConfigurationError, SpendBlocked)):
        _run(reviewer, tmp_path, **options)
    reviewer['_frame'].assert_not_called()
    case.sender.assert_not_called()
    assert case.ledger.snapshot()['period']['used_micro'] == 0


@pytest.mark.parametrize('problem', ['duplicate_moments', 'missing_flag', 'extra_row', 'string_score'])
def test_invalid_paid_response_is_terminal_without_fallback_or_hidden_retry(case, reviewer, tmp_path, problem):
    row = _review(reviewer)
    rows = [row]
    if problem == 'duplicate_moments': row['evidence_moment_indices'] = [1, 1]
    elif problem == 'missing_flag': row.pop('major_visual_artifact_visible')
    elif problem == 'extra_row': rows.append(deepcopy(row))
    else: row['score'] = '92'
    _queue(case, {'reviews': rows})
    with pytest.raises(AbacusGenerationError, match='abacus_schema_mismatch'):
        _run(reviewer, tmp_path)
    case.sender.assert_called_once()
    reviewer['OpenAI'].assert_not_called()
    reviewer['generate_gemini_multimodal_json'].assert_not_called()
    assert case.ledger.snapshot()['period']['used_micro'] == 240_960
    case.record.assert_called_once()  # usage stays recorded despite invalid content


@pytest.mark.parametrize('field,value', [('major_visual_artifact_visible', True),
    ('effectively_static_or_frozen', True), ('authored_identity_or_material_conflict_visible', True),
    ('subject_visible', False)])
def test_abacus_high_score_cannot_override_existing_visual_hard_gates(case, reviewer, tmp_path, field, value):
    _queue(case, {'reviews': [_review(reviewer, score=99, **{field: value})]})
    row = _run(reviewer, tmp_path)['reviews'][0]
    assert row[field] is value and row['score'] == 40
    case.sender.assert_called_once()


@pytest.mark.parametrize('branch', ['initial', 'temporal', 'consistency'])
@pytest.mark.parametrize('error', [AbacusGenerationError('abacus_output_invalid'),
    AbacusConfigurationError('abacus_visual_input_invalid'), SpendBlocked('spend_day_limit')])
def test_terminal_errors_keep_identity_through_actual_reviewer_catchalls(case, reviewer, monkeypatch, tmp_path, branch, error):
    initial = _review(reviewer)
    if branch == 'temporal':
        initial.update(location_continuity_applicable=True, location_continuity_matches=True)
    elif branch == 'consistency': initial['score'] = 68
    adapter = Mock(side_effect=[error] if branch == 'initial' else [{'reviews': [initial]}, error])
    monkeypatch.setattr(generation, 'generate_abacus_visual_json', adapter)
    with pytest.raises(type(error)) as raised:
        _run(reviewer, tmp_path)
    assert raised.value is error
    assert adapter.call_count == (1 if branch == 'initial' else 2)
    reviewer['OpenAI'].assert_not_called()
    reviewer['generate_gemini_multimodal_json'].assert_not_called()
    case.sender.assert_not_called()


def test_same_frame_temporal_repair_is_one_explicit_abacus_call_with_identical_evidence(case, reviewer, tmp_path):
    initial = _review(reviewer, location_continuity_applicable=True, location_continuity_matches=True)
    repaired = {**initial, 'evidence_moment_indices': [3, 4]}
    _queue(case, {'reviews': [initial]}, {'reviews': [repaired]})
    row = _run(reviewer, tmp_path)['reviews'][0]
    assert row['score'] == 92 and row['temporal_response_repair_complete'] is True
    assert case.sender.call_count == 2 and reviewer['_frame'].call_count == 5
    first, second = _body(case), _body(case, 1)
    assert first['messages'] == second['messages']
    assert 'SAME-FRAME TEMPORAL RESPONSE REPAIR (ONE ATTEMPT)' in second['system']
    assert case.ledger.snapshot()['period']['used_micro'] == 481_920
    reviewer['OpenAI'].assert_not_called()


@pytest.mark.parametrize('case', ['covered'], indirect=True)
@pytest.mark.parametrize('branch', ['temporal', 'consistency'])
def test_exhausted_actual_coverage_stops_secondary_review_before_second_post(case, reviewer, tmp_path, branch):
    initial = _review(reviewer)
    if branch == 'temporal':
        initial.update(location_continuity_applicable=True, location_continuity_matches=True)
    else:
        initial.update(score=68, best_candidate_index=1)
    _queue(case, {'reviews': [initial]})
    with pytest.raises(SpendBlocked, match='spend_funding_covered_limit'):
        _run(reviewer, tmp_path, candidates=2 if branch == 'consistency' else 1)
    case.sender.assert_called_once()
    case.record.assert_called_once()
    assert case.ledger.snapshot()['period']['used_micro'] == 240_960
    assert case.ledger.funding_snapshot()['cash_reserved_micro'] == 0
    reviewer['OpenAI'].assert_not_called()
    reviewer['generate_gemini_multimodal_json'].assert_not_called()


def test_identical_single_candidate_consistency_request_obeys_existing_replay_fence(case, reviewer, tmp_path):
    _queue(case, {'reviews': [_review(reviewer, score=68)]})
    with pytest.raises(SpendBlocked, match='spend_request_already_reserved'):
        _run(reviewer, tmp_path)
    case.sender.assert_called_once()
    assert case.ledger.snapshot()['period']['used_micro'] == 240_960


def test_abacus_does_not_shrink_or_drop_over_bound_frames(case, reviewer, tmp_path):
    oversized = base64.b64decode(_jpeg()) + b'x' * (180 * 1024)
    reviewer['_frame'] = Mock(return_value=SimpleNamespace(read_bytes=lambda: oversized))
    with pytest.raises(SpendBlocked):
        _run(reviewer, tmp_path)
    reviewer['_bounded_gemini_frame_bytes'].assert_not_called()
    case.sender.assert_not_called()
    assert case.ledger.snapshot()['period']['used_micro'] == 0


@pytest.mark.parametrize('unique,values,accepted', [
    (True, [1, 2], True), (True, [1, 1.0], False),
    (True, [True, 1], True), (False, [1, 1], True),
    (True, [{'x': [1, 2]}, {'x': [1.0, 2]}], False),
    (True, [{'x': [1, 2]}, {'x': [2, 1]}], True),
])
def test_native_schema_unique_items_enforces_deep_json_equality(case, unique, values, accepted):
    schema = {'type': 'object', 'properties': {'proof': {'type': 'array', 'uniqueItems': unique,
        'items': {'type': ['number', 'boolean', 'object']}}}, 'required': ['proof'], 'additionalProperties': False}
    original = deepcopy(schema)
    _queue(case, {'proof': values})
    def run():
        return generation.generate_abacus_visual_json(_parts(), system_instruction='Full rubric.', api_key=KEY,
                                                      json_schema=schema)
    if accepted:
        assert run() == {'proof': values}
    else:
        with pytest.raises(AbacusGenerationError, match='abacus_schema_mismatch'):
            run()
    assert schema == original
    assert json.loads(_body(case)['system'].split(generation._SCHEMA_PREFIX)[1]) == schema
    case.sender.assert_called_once()
    assert case.ledger.snapshot()['period']['used_micro'] == 240_960


@pytest.mark.parametrize('damage', ['integer', 'string', 'non_array', 'unsupported_keyword',
                                   'non_string_property', 'tuple_enum', 'tuple_required'])
def test_schema_extension_does_not_admit_other_schema_shapes(case, damage):
    proof = {'type': 'array', 'items': {'type': 'integer'}, 'uniqueItems': True}
    if damage == 'integer': proof['uniqueItems'] = 1
    elif damage == 'string': proof['uniqueItems'] = 'true'
    elif damage == 'non_array': proof['type'] = 'object'
    elif damage == 'unsupported_keyword': proof['contains'] = {'type': 'integer'}
    schema = {'type': 'object', 'properties': {'proof': proof}}
    if damage == 'non_string_property': schema['properties'] = {1: proof}
    elif damage == 'tuple_enum': proof['items']['enum'] = (1, 2)
    elif damage == 'tuple_required': schema['required'] = ('proof',)
    with pytest.raises((AbacusConfigurationError, SpendBlocked)):
        generation.generate_abacus_visual_json(_parts(), system_instruction='Full rubric.', api_key=KEY,
                                               json_schema=schema)
    case.sender.assert_not_called()
    assert case.ledger.snapshot()['period']['used_micro'] == 0
