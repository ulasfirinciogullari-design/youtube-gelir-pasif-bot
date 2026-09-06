"""Provider-routing regressions with isolated settings and mocked transport."""

import ast
import base64
from functools import lru_cache
import json
import math
from pathlib import Path
import re
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlsplit

import pytest


SERVICES = Path(__file__).resolve().parents[1] / 'app' / 'services'
SOURCE = SERVICES / 'visual_qc.py'
EVIDENCE = [{
    'url': 'https://www.bep.gov/currency',
    'evidence': 'SOURCE MARKER: U.S. currency paper is 75 percent cotton and 25 percent linen.',
}]


def _namespace():
    namespace = {
        'Path': Path, 're': re, 'json': json, 'math': math, 'base64': base64,
        'lru_cache': lru_cache, 'urlsplit': urlsplit,
        'GEMINI_DEFAULT_MODEL': 'test-gemini-model',
        'GeminiGenerationError': RuntimeError, 'GeminiProtocolError': ValueError,
        'routed_open_air_cooling_temporal_required': lambda scene: False,
    }
    for filename in ('source_evidence.py', 'visual_identity.py', 'visual_qc.py'):
        tree = ast.parse((SERVICES / filename).read_text(encoding='utf-8'))
        definitions = [
            node for node in tree.body
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.FunctionDef))
        ]
        exec(compile(ast.Module(body=definitions, type_ignores=[]), str(SERVICES / filename), 'exec'), namespace)
    namespace.update({
        'settings': SimpleNamespace(
            studio_plan_provider='gemini', studio_visual_qc_provider='',
            gemini_api_key='mock-gemini-key',
            gemini_model='test-gemini-model', openai_api_key='mock-openai-key',
            openai_model='existing-openai-model',
        ),
        'OpenAI': Mock(),
        'generate_gemini_multimodal_json': Mock(),
        '_frame': Mock(side_effect=lambda path, output, fraction: SimpleNamespace(
            read_bytes=lambda: b'\xff\xd8\xff' + f'frame:{path}:{fraction}'.encode(),
        )),
    })
    return namespace


def _review(namespace, *, index=0, candidate=0, score=92, **overrides):
    review = {
        field: False for field in (
            *namespace['_EVIDENCE_BOOLEAN_FIELDS'],
            *namespace['_MANUAL_QA_VISUAL_BOOLEAN_FIELDS'],
            *namespace['_IDENTITY_BOOLEAN_FIELDS'],
        )
    }
    review.update({
        'scene_index': index, 'best_candidate_index': candidate,
        'best_moment_index': 1, 'score': score,
        'reason': 'Clearly shows the named banknote as stated in the narration.',
        'retry_queries': ['close up genuine dollar banknote'],
        'subject_visible': True, 'spoken_action_visible': True,
        'evidence_moment_indices': [1],
    })
    review.update(overrides)
    return review


def _openai_response(namespace, reviews, *, status='completed'):
    namespace['OpenAI'].return_value.responses.create.return_value = SimpleNamespace(
        status=status, output_text=json.dumps({'reviews': reviews}),
    )


def _run(namespace, work, **options):
    scene = {
        'narration': 'Dolar banknotunda pamuk ve keten vardır.',
        'visual_queries': ['hands holding dollar'], 'ai_prompt': None, 'index': 0,
    }
    return namespace['review_scene_visuals'](
        [scene], [[{'path': 'unselected.mp4'}, {'path': 'selected.mp4'}]], work,
        topic='TOPIC MARKER: genuine US currency', story_scenes=[scene],
        content_style='documentary', evidence_sources=EVIDENCE,
        _missing_review_attempts=0, **options,
    )


def test_gemini_conflict_uses_one_strict_openai_review_of_exact_candidate_and_context(tmp_path):
    namespace = _namespace()
    namespace['generate_gemini_multimodal_json'].return_value = {
        'reviews': [_review(namespace, candidate=1, score=68)],
    }
    _openai_response(namespace, [_review(namespace, score=93)])
    result = _run(namespace, tmp_path)
    namespace['generate_gemini_multimodal_json'].assert_called_once()
    client = namespace['OpenAI']
    client.assert_called_once_with(api_key='mock-openai-key', timeout=120.0, max_retries=0)
    client.return_value.responses.create.assert_called_once()
    request = client.return_value.responses.create.call_args.kwargs
    assert request['model'] == 'existing-openai-model'
    output_format = request['text']['format']
    assert output_format['type'] == 'json_schema' and output_format['strict'] is True
    fields = output_format['schema']['properties']['reviews']['items']
    assert fields['additionalProperties'] is False
    assert set(fields['required']) == set(fields['properties'])
    assert 'authored_identity_or_material_conflict_visible' in fields['required']
    assert 'uniqueItems' not in fields['properties']['evidence_moment_indices']
    assert namespace['_review_json_schema']([0], {0: {0: {0, 1, 2, 3, 4}}})[
        'properties']['reviews']['items']['properties']['evidence_moment_indices']['uniqueItems'] is True
    parts = request['input'][0]['content']
    frames = [base64.b64decode(part['image_url'].split(',', 1)[1]) for part in parts if part['type'] == 'input_image']
    assert len(frames) == 5
    assert all(b'selected.mp4' in frame and b'unselected.mp4' not in frame for frame in frames)
    text = '\n'.join(part['text'] for part in parts if part['type'] == 'input_text')
    assert 'TOPIC MARKER' in text and 'SOURCE MARKER' in text
    assert 'Dolar banknotunda pamuk ve keten' in text
    assert 'A score of 86+' in request['instructions']
    review = result['reviews'][0]
    assert review['score'] == 93 and review['best_candidate_index'] == 1
    assert review['score_reason_initial_provider'] == 'gemini'
    assert review['score_reason_revalidation_provider'] == 'openai'
    assert review['score_reason_revalidation_attempted'] is True
    assert namespace['settings'].studio_plan_provider == 'gemini'


@pytest.mark.parametrize('problem', [
    'missing_identity', 'duplicate', 'string_score', 'extra_field',
    'duplicate_moments', 'missing_flag', 'out_of_range', 'incomplete',
])
def test_malformed_secondary_cannot_become_independent_approval(problem, tmp_path):
    namespace = _namespace()
    namespace['generate_gemini_multimodal_json'].return_value = {'reviews': [_review(namespace, score=68)]}
    second = _review(namespace, score=95)
    if problem == 'missing_identity':
        del second['authored_identity_or_material_conflict_visible']
        del second['manufactured_object_cues_visible']
    elif problem == 'string_score':
        second['score'] = '95'
    elif problem == 'extra_field':
        second['approved'] = True
    elif problem == 'duplicate_moments':
        second['evidence_moment_indices'] = [1, 1]
    elif problem == 'missing_flag':
        del second['major_visual_artifact_visible']
    elif problem == 'out_of_range':
        second['score'] = 101
    _openai_response(namespace, [second, dict(second)] if problem == 'duplicate' else [second],
                     status='incomplete' if problem == 'incomplete' else 'completed')
    result = _run(namespace, tmp_path)
    assert result['reviews'][0]['score'] <= 40
    assert result['reviews'][0]['score_reason_consistency_passed'] is False
    assert result['reviews'][0]['score_reason_revalidation_provider'] == 'openai'
    namespace['OpenAI'].return_value.responses.create.assert_called_once()
    namespace['generate_gemini_multimodal_json'].assert_called_once()


@pytest.mark.parametrize('overrides', [
    {'subject_visible': False},
    {'authored_identity_or_material_conflict_visible': True},
    {'major_visual_artifact_visible': True},
])
def test_secondary_still_obeys_every_hard_gate(overrides, tmp_path):
    namespace = _namespace()
    namespace['generate_gemini_multimodal_json'].return_value = {'reviews': [_review(namespace, score=68)]}
    _openai_response(namespace, [_review(namespace, score=95, **overrides)])
    result = _run(namespace, tmp_path)
    assert result['reviews'][0]['score'] <= 40
    namespace['OpenAI'].return_value.responses.create.assert_called_once()


def test_secondary_failure_or_repeated_conflict_never_ping_pongs(tmp_path):
    for failure in (False, True):
        namespace = _namespace()
        namespace['generate_gemini_multimodal_json'].return_value = {'reviews': [_review(namespace, score=68)]}
        _openai_response(namespace, [_review(namespace, score=68)])
        if failure:
            namespace['OpenAI'].return_value.responses.create.side_effect = RuntimeError('mock transport failure')
        result = _run(namespace, tmp_path / str(failure), _score_reason_consistency_attempts=3)
        assert result['reviews'][0]['score'] <= 40
        namespace['OpenAI'].return_value.responses.create.assert_called_once()
        namespace['generate_gemini_multimodal_json'].assert_called_once()


def test_missing_openai_key_retains_bounded_gemini_rereview(tmp_path):
    namespace = _namespace()
    namespace['settings'].openai_api_key = ''
    namespace['generate_gemini_multimodal_json'].side_effect = [
        {'reviews': [_review(namespace, score=68)]},
        {'reviews': [_review(namespace, score=92)]},
    ]
    result = _run(namespace, tmp_path)
    namespace['OpenAI'].assert_not_called()
    assert namespace['generate_gemini_multimodal_json'].call_count == 2
    assert result['reviews'][0]['score_reason_revalidation_provider'] == 'gemini'
    assert namespace['generate_gemini_multimodal_json'].call_args.kwargs['thinking_level'] == 'medium'


def test_explicit_openai_override_survives_missing_review_recursion(tmp_path):
    namespace = _namespace()
    namespace['OpenAI'].return_value.responses.create.side_effect = [
        SimpleNamespace(status='completed', output_text='{"reviews":[]}'),
        SimpleNamespace(status='completed', output_text=json.dumps({'reviews': [_review(namespace)]})),
    ]
    result = namespace['review_scene_visuals'](
        [{'narration': 'A banknote is visible.'}], [['one.mp4']], tmp_path,
        provider_override='openai', _missing_review_attempts=1,
        _score_reason_consistency_attempts=0,
    )
    assert result['reviews'][0]['score'] == 92
    assert namespace['OpenAI'].return_value.responses.create.call_count == 2
    namespace['generate_gemini_multimodal_json'].assert_not_called()
    assert namespace['settings'].studio_plan_provider == 'gemini'


def test_explicit_gemini_override_survives_batches_with_openai_primary(tmp_path):
    namespace = _namespace()
    namespace['settings'].studio_plan_provider = 'openai'
    namespace['generate_gemini_multimodal_json'].side_effect = lambda parts, **kwargs: {
        'reviews': [_review(namespace, index=index) for index in kwargs[
            'json_schema']['properties']['reviews']['items']['properties']['scene_index']['enum']],
    }
    result = namespace['review_scene_visuals'](
        [{'narration': 'A banknote is visible.', 'index': index} for index in range(5)],
        [['one.mp4'] for _ in range(5)], tmp_path,
        provider_override='gemini', _missing_review_attempts=0,
        _score_reason_consistency_attempts=0,
    )
    assert len(result['reviews']) == 5
    assert namespace['generate_gemini_multimodal_json'].call_count == 2
    namespace['OpenAI'].assert_not_called()
    assert namespace['settings'].studio_plan_provider == 'openai'


@pytest.mark.parametrize('override', ['', 'other', True, 3])
def test_invalid_provider_override_fails_before_provider_calls(override, tmp_path):
    namespace = _namespace()
    with pytest.raises(ValueError, match='provider override'):
        _run(namespace, tmp_path, provider_override=override)
    namespace['OpenAI'].assert_not_called()
    namespace['generate_gemini_multimodal_json'].assert_not_called()


@pytest.mark.parametrize('raw', [
    '{"reviews":[],"reviews":[]}',
    '{"reviews":[{"score":40,"score":95}]}',
    '{"reviews":[{"score":NaN}]}',
    '{"reviews":[{"score":Infinity}]}',
    '{"reviews":[{"score":1e999}]}',
    '{"reviews":[],"approved":true}',
])
def test_structured_decoder_rejects_duplicate_keys_nonfinite_numbers_and_extra_root(raw):
    with pytest.raises(ValueError):
        _namespace()['_parse_strict_visual_review'](raw)


def test_dedicated_openai_visual_provider_is_strict_without_changing_gemini_director(tmp_path):
    namespace = _namespace()
    namespace['settings'].studio_visual_qc_provider = ' openai '
    _openai_response(namespace, [_review(namespace, score=92)])
    result = _run(namespace, tmp_path, _score_reason_consistency_attempts=0)
    assert result['reviews'][0]['score'] == 92
    namespace['generate_gemini_multimodal_json'].assert_not_called()
    assert namespace['OpenAI'].call_args.kwargs['max_retries'] == 0
    assert namespace['OpenAI'].return_value.responses.create.call_args.kwargs['text']['format']['strict'] is True
    assert namespace['settings'].studio_plan_provider == 'gemini'
    assert namespace['settings'].gemini_model == 'test-gemini-model'


def test_override_precedes_dedicated_setting_and_dedicated_setting_precedes_plan(tmp_path):
    namespace = _namespace()
    namespace['settings'].studio_plan_provider = 'openai'
    namespace['settings'].studio_visual_qc_provider = 'gemini'
    namespace['generate_gemini_multimodal_json'].return_value = {'reviews': [_review(namespace)]}
    _run(namespace, tmp_path / 'dedicated', _score_reason_consistency_attempts=0)
    namespace['generate_gemini_multimodal_json'].assert_called_once()
    namespace['OpenAI'].assert_not_called()
    _openai_response(namespace, [_review(namespace)])
    _run(namespace, tmp_path / 'override', provider_override='openai', _score_reason_consistency_attempts=0)
    namespace['OpenAI'].return_value.responses.create.assert_called_once()
    assert namespace['generate_gemini_multimodal_json'].call_count == 1


def test_blank_dedicated_provider_preserves_legacy_openai_request(tmp_path):
    namespace = _namespace()
    namespace['settings'].studio_plan_provider = 'openai'
    _openai_response(namespace, [_review(namespace)])
    _run(namespace, tmp_path, _score_reason_consistency_attempts=0)
    assert namespace['OpenAI'].call_args.kwargs['max_retries'] == 1
    assert 'text' not in namespace['OpenAI'].return_value.responses.create.call_args.kwargs


def test_invalid_dedicated_setting_fails_unless_explicit_override_is_valid(tmp_path):
    namespace = _namespace()
    namespace['settings'].studio_visual_qc_provider = 'unknown'
    with pytest.raises(ValueError, match='STUDIO_VISUAL_QC_PROVIDER'):
        _run(namespace, tmp_path / 'invalid')
    namespace['OpenAI'].assert_not_called()
    _openai_response(namespace, [_review(namespace)])
    _run(namespace, tmp_path / 'override', provider_override='openai', _score_reason_consistency_attempts=0)
    namespace['OpenAI'].return_value.responses.create.assert_called_once()


def test_dedicated_visual_provider_setting_is_disabled_by_default():
    config = ast.parse((SERVICES.parent / 'config.py').read_text(encoding='utf-8'))
    setting = next(
        node for node in ast.walk(config)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        and node.target.id == 'studio_visual_qc_provider'
    )
    assert ast.literal_eval(setting.value) == ''


def test_strict_openai_missing_frames_remain_missing_without_a_provider_call(tmp_path):
    namespace = _namespace()
    namespace['_frame'].side_effect = None
    namespace['_frame'].return_value = None
    result = _run(namespace, tmp_path, provider_override='openai')
    assert result['reviews'] == []
    assert result['missing_review_indices'] == [0]
    namespace['OpenAI'].assert_not_called()
