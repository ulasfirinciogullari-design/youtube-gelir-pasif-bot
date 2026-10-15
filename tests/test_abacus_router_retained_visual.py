"""Real picture-review assembly/normalization around the scoped router boundary.

The runtime's authorization, permanent journal and transport are tested in its
own integration suite. Here only that boundary and local frame extraction are
replaced; the original rubric, schema, routing and all hard gates execute.
"""
import base64
from copy import deepcopy
from functools import lru_cache
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import abacus_router_review_runtime as router
from app.services.production_spend import SpendBlocked
from test_visual_cross_provider_review import _namespace, _review, EVIDENCE


@lru_cache(maxsize=1)
def _jpeg():
    return subprocess.run([
        'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=64x114:d=0.04',
        '-frames:v', '1', '-threads', '1', '-c:v', 'mjpeg', '-f', 'image2pipe', 'pipe:1',
    ], check=True, capture_output=True, timeout=10).stdout


@pytest.fixture
def review_case(monkeypatch):
    ns = _namespace()
    ns['settings'].studio_visual_qc_provider = 'openai'
    ns['_frame'] = Mock(side_effect=lambda *args: SimpleNamespace(read_bytes=_jpeg))
    ns['_bounded_gemini_frame_bytes'] = Mock(side_effect=AssertionError('No router JPEG re-encoding'))
    ns['_request_visual_review'] = Mock(wraps=ns['_request_visual_review'])
    active = Mock(return_value=True)
    generate = Mock()
    observation = {'requested_model': 'route-llm', 'returned_model': 'route-llm',
                   'underlying_model_known': False, 'model_independence_verified': False}
    monkeypatch.setattr(router, 'retained_router_review_active', active)
    monkeypatch.setattr(router, 'generate_retained_router_review', generate)
    monkeypatch.setattr(router, 'retained_router_review_evidence',
                        lambda: {'retained_visual_review': deepcopy(observation)})
    return SimpleNamespace(ns=ns, active=active, generate=generate, observation=observation)


def _inputs(count):
    scenes = [{'index': index, 'narration': f'Banknote material fact {index}.',
               'visual_queries': [f'dollar banknote material {index}'], 'ai_prompt': None}
              for index in range(count)]
    visuals = [[{'path': f'selected-{index}.mp4', 'source_type': 'stock',
                 'source_id': f'original-asset-{index}'}] for index in range(count)]
    return scenes, visuals


def _run(ns, work, *, count=1, **options):
    scenes, visuals = _inputs(count)
    return ns['review_scene_visuals'](
        scenes, visuals, work, topic='TOPIC MARKER: banknote materials', story_scenes=scenes,
        content_style='documentary', evidence_sources=EVIDENCE, **options)


def test_twelve_scenes_keep_original_rubric_all_sixty_jpegs_and_schema(review_case, tmp_path):
    case = review_case
    rows = [_review(case.ns, index=index) for index in range(12)]
    case.generate.return_value = {'reviews': deepcopy(rows)}
    result = _run(case.ns, tmp_path / 'router', count=12)
    call = case.generate.call_args
    case.generate.assert_called_once()
    assert call.kwargs['purpose'] == 'retained_visual_review'
    assert call.kwargs['max_tokens'] == 8192
    assert case.ns['_frame'].call_count == 60
    case.ns['_bounded_gemini_frame_bytes'].assert_not_called()
    case.ns['OpenAI'].assert_not_called()
    case.ns['generate_gemini_multimodal_json'].assert_not_called()

    # Compare with the original provider path's complete pre-transport payload,
    # rather than a reduced hand-written copy of the picture editor's rubric.
    baseline = _namespace()
    baseline['settings'].studio_visual_qc_provider = 'openai'
    baseline['_frame'] = Mock(side_effect=lambda *args: SimpleNamespace(read_bytes=_jpeg))
    baseline['_request_visual_review'] = Mock(return_value={'reviews': deepcopy(rows)})
    case.active.return_value = False
    ordinary = _run(baseline, tmp_path / 'original', count=12)
    args = baseline['_request_visual_review'].call_args.args
    assert args[0] == 'openai' and args[1] is True
    assert call.kwargs['system_instruction'] == args[2]
    assert call.kwargs['json_schema'] == baseline['_review_json_schema'](args[5], args[6])
    schema = call.kwargs['json_schema']
    assert schema['properties']['reviews']['minItems'] == 12
    assert schema['properties']['reviews']['maxItems'] == 12
    assert schema['properties']['reviews']['items']['properties']['evidence_moment_indices']['uniqueItems'] is True
    original_parts = args[3][1:]
    parts = call.args[0]
    assert len(parts) == len(original_parts) == 133
    assert sum(part['type'] == 'image_url' for part in parts) == 60
    for original, forwarded in zip(original_parts, parts, strict=True):
        if original['type'] == 'input_text':
            assert forwarded == {'type': 'text', 'text': original['text']}
        else:
            assert forwarded == {'type': 'image_url', 'image_url': {'url': original['image_url']}}
            assert base64.b64decode(forwarded['image_url']['url'].split(',', 1)[1]) == _jpeg()
    text = '\n'.join(part['text'] for part in parts if part['type'] == 'text')
    assert 'TOPIC MARKER' in text and 'SOURCE MARKER' in text
    assert 'SERVER-AUTHORED CANDIDATE MEDIA PROVENANCE' in text
    assert all(f'Banknote material fact {index}.' in text for index in range(12))
    assert result['reviews'] == ordinary['reviews']
    assert not result['missing_review_indices']
    assert result['review_provider'] == 'abacus_router'
    assert result['review_observer'] == case.observation
    assert 'review_provider' not in ordinary and 'review_observer' not in ordinary
    assert case.ns['settings'].studio_visual_qc_provider == 'openai'

    # The real native inspector must admit this complete reviewer request;
    # transport-boundary mocks must not hide schema or metadata incompatibility.
    from app.services.abacus_router_adapter import prepare_router_request
    prepared = prepare_router_request(parts, api_key='offline-router-test-key',
        system_instruction=call.kwargs['system_instruction'], json_schema=schema, max_tokens=8192)
    assert prepared.payload['messages'] == [
        {'role': 'system', 'content': call.kwargs['system_instruction']},
        {'role': 'user', 'content': parts},
    ]
    assert prepared.payload['response_format']['json_schema']['schema'] == schema
    assert prepared.model == 'route-llm'


@pytest.mark.parametrize('field,value', [
    ('major_visual_artifact_visible', True), ('effectively_static_or_frozen', True),
    ('authored_identity_or_material_conflict_visible', True), ('subject_visible', False),
])
def test_high_router_score_keeps_existing_hard_rejection(review_case, tmp_path, field, value):
    case = review_case
    case.generate.return_value = {'reviews': [_review(case.ns, score=99, **{field: value})]}
    row = _run(case.ns, tmp_path)['reviews'][0]
    assert row[field] is value and row['score'] == 40
    case.generate.assert_called_once()


def test_temporal_missing_proof_rejects_without_second_request(review_case, tmp_path):
    case = review_case
    case.generate.return_value = {'reviews': [_review(
        case.ns, location_continuity_applicable=True, location_continuity_matches=True)]}
    row = _run(case.ns, tmp_path)['reviews'][0]
    assert row['score'] == 40 and row['evidence_gate_passed'] is False
    assert not row.get('temporal_response_repair_attempted')
    case.generate.assert_called_once()


@pytest.mark.parametrize('attempts', [0, 1])
def test_score_reason_conflict_is_not_promoted_or_relabelled_as_independent(review_case, tmp_path, attempts):
    case = review_case
    case.generate.return_value = {'reviews': [_review(case.ns, score=68)]}
    row = _run(case.ns, tmp_path, _score_reason_consistency_attempts=attempts)['reviews'][0]
    assert row['score'] == 40
    assert row['score_reason_consistency_passed'] is False
    assert row['score_reason_initial_provider'] == 'abacus_router'
    assert row['score_reason_revalidation_attempted'] is False
    assert row['score_reason_revalidated'] is False
    assert 'score_reason_revalidation_provider' not in row
    case.generate.assert_called_once()
    assert case.ns['_frame'].call_count == 5
    case.ns['OpenAI'].assert_not_called()
    case.ns['generate_gemini_multimodal_json'].assert_not_called()


@pytest.mark.parametrize('problem', ['missing', 'duplicate', 'missing_flag', 'string_score'])
def test_incomplete_or_malformed_review_never_uses_missing_review_retry(review_case, tmp_path, problem):
    case = review_case
    row = _review(case.ns)
    rows = [row]
    if problem == 'missing': rows = []
    elif problem == 'duplicate': rows.append(deepcopy(row))
    elif problem == 'missing_flag': row.pop('major_visual_artifact_visible')
    else: row['score'] = '92'
    case.generate.return_value = {'reviews': rows}
    result = _run(case.ns, tmp_path)
    assert result['reviews'] == [] and result['missing_review_indices'] == [0]
    case.generate.assert_called_once()
    assert case.ns['_frame'].call_count == 5


@pytest.mark.parametrize('code', ['router_review_scope_unusable', 'router_review_response_rejected',
                                 'router_review_commit_uncertain'])
def test_router_error_is_same_terminal_error_without_any_other_provider(review_case, tmp_path, code):
    case = review_case
    error = SpendBlocked(code)
    case.generate.side_effect = error
    with pytest.raises(SpendBlocked) as caught:
        _run(case.ns, tmp_path)
    assert caught.value is error
    case.generate.assert_called_once()
    case.ns['OpenAI'].assert_not_called()
    case.ns['generate_gemini_multimodal_json'].assert_not_called()


@pytest.mark.parametrize('options', [{'provider_override': 'openai'},
                                    {'provider_override': 'gemini'},
                                    {'provider_override': 'abacus'},
                                    {'gemini_model_override': 'specific-required-gemini'}])
def test_explicit_provider_requirement_blocks_before_frames(review_case, tmp_path, options):
    with pytest.raises(SpendBlocked, match='abacus_router_visual_override_invalid'):
        _run(review_case.ns, tmp_path, **options)
    review_case.generate.assert_not_called()
    review_case.ns['_frame'].assert_not_called()


@pytest.mark.parametrize('problem', ['truncated_scenes', 'too_many_scenes', 'extra_candidate',
                                   'missing_selected', 'empty_path'])
def test_retained_scope_cannot_drop_scenes_or_candidates(review_case, tmp_path, problem):
    scenes, visuals = _inputs(13 if problem == 'too_many_scenes' else 2)
    maximum = 1 if problem == 'truncated_scenes' else len(scenes)
    if problem == 'extra_candidate': visuals[0].append({'path': 'unselected.mp4'})
    elif problem == 'missing_selected': visuals.pop()
    elif problem == 'empty_path': visuals[0] = [{'path': ''}]
    with pytest.raises(SpendBlocked, match='abacus_router_visual_scope_invalid'):
        review_case.ns['review_scene_visuals'](scenes, visuals, tmp_path, maximum)
    review_case.generate.assert_not_called()
    review_case.ns['_frame'].assert_not_called()


def test_missing_original_frame_blocks_instead_of_reviewing_reduced_evidence(review_case, tmp_path):
    review_case.ns['_frame'].side_effect = [SimpleNamespace(read_bytes=_jpeg), None]
    with pytest.raises(SpendBlocked, match='abacus_router_visual_frame_missing'):
        _run(review_case.ns, tmp_path)
    review_case.generate.assert_not_called()


def test_inactive_scope_keeps_normal_gemini_provider_and_normal_result(review_case, tmp_path):
    case = review_case
    case.active.return_value = False
    case.ns['settings'].studio_visual_qc_provider = ''
    case.ns['_bounded_gemini_frame_bytes'] = Mock(return_value=_jpeg())
    case.ns['generate_gemini_multimodal_json'].return_value = {'reviews': [_review(case.ns)]}
    result = _run(case.ns, tmp_path)
    assert result['reviews'][0]['score'] == 92
    case.ns['generate_gemini_multimodal_json'].assert_called_once()
    case.generate.assert_not_called()
    assert 'review_provider' not in result and 'review_observer' not in result


def test_router_is_not_an_ordinary_provider_override(review_case, tmp_path):
    review_case.active.return_value = False
    with pytest.raises(ValueError, match='override must be'):
        _run(review_case.ns, tmp_path, provider_override='abacus_router')
    review_case.generate.assert_not_called()
    review_case.ns['_frame'].assert_not_called()
