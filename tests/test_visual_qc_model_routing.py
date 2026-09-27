"""Dedicated visual-model routing keeps existing protocol and rejection gates."""
import ast
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from test_visual_cross_provider_review import (
    SERVICES, _namespace, _openai_response, _review, _run,
)


def _configured(model='gpt-6-astra'):
    namespace = _namespace()
    namespace['settings'].studio_visual_qc_openai_model = model
    namespace['settings'].studio_visual_qc_provider = 'openai'
    _openai_response(namespace, [_review(namespace)])
    return namespace


def _request(namespace, *, provider='openai', strict=True, **options):
    return namespace['_request_visual_review'](
        provider, strict, 'UNCHANGED EDITORIAL INSTRUCTION',
        [{'type': 'input_text', 'text': 'system placeholder'},
         {'type': 'input_text', 'text': 'existing frame context'},
         {'type': 'input_image', 'image_url': 'data:image/jpeg;base64,/9j/'}],
        [{'text': 'existing Gemini frame context'}],
        [0], {0: {0: {0, 1, 2, 3, 4}}},
        options.pop('model_override', None), 'medium', **options,
    )


def test_config_defaults_visual_model_to_gpt5():
    tree = ast.parse((SERVICES.parent / 'config.py').read_text(encoding='utf-8'))
    settings = next(node for node in tree.body
                    if isinstance(node, ast.ClassDef) and node.name == 'Settings')
    defaults = {node.target.id: ast.literal_eval(node.value)
                for node in settings.body if isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id in {'studio_visual_qc_openai_model', 'openai_model'}}
    assert defaults == {'studio_visual_qc_openai_model': 'gpt-5',
                        'openai_model': 'gpt-5'}


@pytest.mark.parametrize('dedicated,expected', [
    ('gpt-6-astra', 'gpt-6-astra'),
    ('  gpt-6-astra\t\n', 'gpt-6-astra'),
    ('explicit-visual-deployment', 'explicit-visual-deployment'),
])
@pytest.mark.parametrize('strict', [True, False])
def test_nonblank_visual_model_wins_without_mutating_other_workload_settings(dedicated, expected, strict):
    namespace = _configured(dedicated)
    before = deepcopy(vars(namespace['settings']))
    result = _request(namespace, strict=strict)
    transport = namespace['OpenAI'].return_value.responses.create
    transport.assert_called_once()
    assert transport.call_args.kwargs['model'] == expected
    assert result['reviews'][0]['score'] == 92
    assert vars(namespace['settings']) == before
    assert namespace['settings'].openai_model == 'existing-openai-model'
    assert namespace['settings'].gemini_model == 'test-gemini-model'
    namespace['generate_gemini_multimodal_json'].assert_not_called()


@pytest.mark.parametrize('legacy_value', ['', ' \t\n ', None])
def test_blank_or_none_visual_setting_falls_back_to_legacy_openai_model(legacy_value):
    namespace = _configured(legacy_value)
    _request(namespace)
    assert namespace['OpenAI'].return_value.responses.create.call_args.kwargs[
        'model'] == 'existing-openai-model'


def test_missing_visual_setting_remains_compatible_with_legacy_settings():
    namespace = _namespace()
    assert not hasattr(namespace['settings'], 'studio_visual_qc_openai_model')
    _openai_response(namespace, [_review(namespace)])
    _request(namespace)
    assert namespace['OpenAI'].return_value.responses.create.call_args.kwargs[
        'model'] == 'existing-openai-model'


def test_astra_request_preserves_low_effort_strict_schema_and_same_payload():
    namespace = _configured()
    original_schema = namespace['_review_json_schema']([0], {0: {0: {0, 1, 2, 3, 4}}})
    _request(namespace)
    namespace['OpenAI'].assert_called_once_with(
        api_key='mock-openai-key', timeout=120.0, max_retries=0)
    request = namespace['OpenAI'].return_value.responses.create.call_args.kwargs
    assert request['reasoning'] == {'effort': 'low'}
    assert request['instructions'] == 'UNCHANGED EDITORIAL INSTRUCTION'
    assert request['input'] == [{'role': 'user', 'content': [
        {'type': 'input_text', 'text': 'existing frame context'},
        {'type': 'input_image', 'image_url': 'data:image/jpeg;base64,/9j/'},
    ]}]
    output = request['text']['format']
    assert output['type'] == 'json_schema'
    assert output['name'] == 'visual_scene_review' and output['strict'] is True
    expected_schema = deepcopy(original_schema)
    expected_schema['properties']['reviews']['items']['properties'][
        'evidence_moment_indices'].pop('uniqueItems')
    assert output['schema'] == expected_schema
    row = output['schema']['properties']['reviews']['items']
    assert row['additionalProperties'] is False
    assert set(row['required']) == set(row['properties'])
    assert namespace['_review_json_schema']([0], {0: {0: {0, 1, 2, 3, 4}}}) == original_schema


@pytest.mark.parametrize('status', [None, 'incomplete', 'failed', 'cancelled', 'in_progress'])
def test_noncompleted_strict_astra_response_never_becomes_approval(status):
    namespace = _configured()
    response = SimpleNamespace(output_text=json.dumps({'reviews': [_review(namespace, score=99)]}))
    if status is not None:
        response.status = status
    namespace['OpenAI'].return_value.responses.create.return_value = response
    assert _request(namespace) == {'reviews': []}
    namespace['OpenAI'].return_value.responses.create.assert_called_once()
    namespace['generate_gemini_multimodal_json'].assert_not_called()


@pytest.mark.parametrize('raw', [
    '{"reviews":[],"reviews":[]}',
    '{"reviews":[{"score":40,"score":99}]}',
    '{"reviews":[{"score":NaN}]}',
    '{"reviews":[{"score":Infinity}]}',
    '{"reviews":[{"score":1e999}]}',
    '{"reviews":[],"approved":true}',
    '[]',
    '```json\n{"reviews":[]}\n```',
    'not JSON',
])
def test_completed_astra_response_keeps_strict_decoder_rejections(raw):
    namespace = _configured()
    namespace['OpenAI'].return_value.responses.create.return_value = SimpleNamespace(
        status='completed', output_text=raw)
    with pytest.raises(ValueError):
        _request(namespace)
    namespace['OpenAI'].return_value.responses.create.assert_called_once()
    namespace['generate_gemini_multimodal_json'].assert_not_called()


def test_astra_transport_error_does_not_silently_fall_back_to_another_model():
    namespace = _configured()
    namespace['OpenAI'].return_value.responses.create.side_effect = RuntimeError('mock failure')
    with pytest.raises(RuntimeError, match='mock failure'):
        _request(namespace)
    namespace['OpenAI'].return_value.responses.create.assert_called_once()
    assert namespace['OpenAI'].return_value.responses.create.call_args.kwargs['model'] == 'gpt-6-astra'
    namespace['generate_gemini_multimodal_json'].assert_not_called()


@pytest.mark.parametrize('overrides', [
    {'subject_visible': False},
    {'spoken_action_visible': False},
    {'major_visual_artifact_visible': True},
    {'authored_identity_or_material_conflict_visible': True},
])
def test_dedicated_astra_model_cannot_raise_a_hard_gate_rejection_to_pass(tmp_path, overrides):
    namespace = _configured()
    _openai_response(namespace, [_review(namespace, score=99,
        reason='The supplied images fail the required visual evidence.', **overrides)])
    result = _run(namespace, tmp_path, _score_reason_consistency_attempts=0,
                  _temporal_response_repair_attempts=0)
    assert result['reviews'][0]['score'] <= 40
    namespace['OpenAI'].return_value.responses.create.assert_called_once()
    assert namespace['OpenAI'].return_value.responses.create.call_args.kwargs['model'] == 'gpt-6-astra'
    namespace['generate_gemini_multimodal_json'].assert_not_called()


@pytest.mark.parametrize('override,expected', [(None, 'test-gemini-model'), ('explicit-gemini-model', 'explicit-gemini-model')])
def test_dedicated_openai_visual_model_does_not_migrate_gemini_requests(override, expected):
    namespace = _configured()
    namespace['generate_gemini_multimodal_json'].return_value = {'reviews': [_review(namespace)]}
    result = _request(namespace, provider='gemini', model_override=override)
    namespace['OpenAI'].assert_not_called()
    request = namespace['generate_gemini_multimodal_json'].call_args.kwargs
    assert request['model'] == expected and request['thinking_level'] == 'medium'
    assert request['retry_once'] is False and request['timeout'] == 120.0
    assert request['json_schema']['properties']['reviews']['items']['properties'][
        'evidence_moment_indices']['uniqueItems'] is True
    assert result['reviews'][0]['score'] == 92
