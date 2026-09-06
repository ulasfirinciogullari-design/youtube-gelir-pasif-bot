"""Fresh Astra planning is isolated from immutable recovery and other workers."""
import ast
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services.planning_model_routing import (
    fresh_planning_route, planning_openai_model, planning_provider,
    planning_response_text,
)


@pytest.fixture
def settings(monkeypatch):
    value = SimpleNamespace(studio_plan_provider='gemini', openai_model='old-model',
                            studio_fresh_plan_openai_model='gpt-6-astra',
                            gemini_model='old-gemini-model')
    monkeypatch.setitem(sys.modules, 'app.config', SimpleNamespace(settings=value))
    return value


def test_fresh_route_uses_astra_without_changing_shared_settings(settings):
    before = dict(vars(settings))
    assert planning_provider(settings) == 'gemini'
    with fresh_planning_route(enabled=True) as marker:
        assert marker == {'provider': 'openai', 'model': 'gpt-6-astra'}
        assert planning_provider(settings) == 'openai'
        assert planning_openai_model(settings) == 'gpt-6-astra'
        marker['model'] = 'untrusted mutation'
        assert planning_openai_model(settings) == 'gpt-6-astra'
    assert planning_provider(settings) == 'gemini'
    assert planning_openai_model(settings) == 'old-model'
    assert vars(settings) == before


def test_exception_restores_legacy_route(settings):
    with pytest.raises(ValueError):
        with fresh_planning_route(enabled=True):
            raise ValueError('planning failed')
    assert planning_provider(settings) == 'gemini'


def test_concurrent_worker_does_not_inherit_fresh_route(settings):
    with fresh_planning_route(enabled=True), ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(planning_provider, settings).result() == 'gemini'
        assert planning_provider(settings) == 'openai'


@pytest.mark.parametrize('enabled', [False, None, 'true', 1, {}])
def test_only_literal_server_true_enables_route(settings, enabled):
    with fresh_planning_route(enabled=enabled) as marker:
        assert marker is None
        assert planning_provider(settings) == 'gemini'


def test_explicit_configured_fresh_model_is_preserved(settings):
    settings.studio_fresh_plan_openai_model = ' pinned-deployment '
    with fresh_planning_route(enabled=True):
        assert planning_openai_model(settings) == 'pinned-deployment'


def test_blank_fresh_model_fails_closed(settings):
    settings.studio_fresh_plan_openai_model = ' '
    with pytest.raises(RuntimeError, match='must not be empty'):
        with fresh_planning_route(enabled=True):
            pytest.fail('must reject before any work')


@pytest.mark.parametrize('status', ['incomplete', 'failed', 'in_progress', 'cancelled', None])
def test_partial_fresh_response_cannot_be_accepted(settings, status):
    with fresh_planning_route(enabled=True), pytest.raises(RuntimeError, match='did not complete'):
        planning_response_text(SimpleNamespace(status=status, output_text='{"valid": true}'))


@pytest.mark.parametrize('output', ['', ' ', None, {}])
def test_empty_or_invalid_fresh_text_rejected(settings, output):
    with fresh_planning_route(enabled=True), pytest.raises(RuntimeError, match='no text'):
        planning_response_text(SimpleNamespace(status='completed', output_text=output))


def test_completed_response_and_legacy_behavior(settings):
    with fresh_planning_route(enabled=True):
        assert planning_response_text(SimpleNamespace(status='completed', output_text='{}')) == '{}'
    assert planning_response_text(SimpleNamespace(output_text='legacy')) == 'legacy'


@pytest.mark.parametrize('filename,expected', [('research.py', 1), ('director.py', 4)])
def test_all_openai_editorial_calls_use_task_local_route(filename, expected):
    path = Path(__file__).parents[1] / 'app' / 'services' / filename
    source = path.read_text(encoding='utf-8')
    tree = ast.parse(source)
    assert 'model=settings.openai_model' not in source
    assert "'model': settings.openai_model" not in source
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == '_studio_plan_openai_model']
    assert len(calls) == expected
    readers = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
               and isinstance(node.func, ast.Name) and node.func.id == '_planning_response_text']
    assert len(readers) == expected


def test_actual_research_call_uses_astra_and_preserves_tool_budget(settings, monkeypatch):
    from app.services import research

    monkeypatch.setattr(settings, 'openai_api_key', 'mock-key', raising=False)
    monkeypatch.setattr(research, 'settings', settings)
    payload = {'title': 'One story', 'thumbnail_text': 'The reveal', 'description': 'Supported story',
               'scenes': [{'narration': f'Visible action number {i}.',
                           'visual_queries': [f'action {i} close view', f'action {i} wide view'],
                           'ai_prompt': None} for i in range(6)],
               'sources': [{'url': f'https://example.{tld}/evidence',
                            'evidence': 'The observed mechanism directly supports this particular factual explanation.'}
                           for tld in ('com', 'org')]}
    client = Mock()
    client.responses.create.return_value = SimpleNamespace(status='completed', output_text=json.dumps(payload))
    monkeypatch.setattr(research, 'OpenAI', Mock(return_value=client))
    gemini = Mock(side_effect=AssertionError('fresh planning must not silently use Gemini'))
    monkeypatch.setattr(research, 'generate_gemini_json', gemini)
    with fresh_planning_route(enabled=True):
        result = research.research_and_script('one useful phone story', .5, 'tr',
                                             {'mode': 'preview', 'pace': 'balanced'})
    request = client.responses.create.call_args.kwargs
    assert request['model'] == 'gpt-6-astra'
    assert request['max_tool_calls'] == 2
    assert request['tools'] == [{'type': 'web_search', 'search_context_size': 'low'}]
    assert request['reasoning'] == {'effort': 'medium'}
    assert 'temperature' not in request and 'top_p' not in request
    assert len(result['scenes']) == 6
    gemini.assert_not_called()


@pytest.mark.parametrize('status', ['completed', 'incomplete'])
def test_actual_director_call_uses_astra_and_requires_completion(settings, monkeypatch, status):
    from app.services import director

    monkeypatch.setattr(director, 'settings', settings)
    client = Mock()
    client.responses.create.return_value = SimpleNamespace(status=status, output_text='{"scenes": []}')
    gemini = Mock(side_effect=AssertionError('fresh director must not silently use Gemini'))
    monkeypatch.setattr(director, 'generate_gemini_json', gemini)
    with fresh_planning_route(enabled=True):
        if status == 'completed':
            assert director._run_director(client, {}, 'One shopping story', 'Turkish', .5,
                                          45, 42, 48, 5, {'mode': 'production', 'format': 'shorts'}) == {'scenes': []}
        else:
            with pytest.raises(RuntimeError, match='did not complete'):
                director._run_director(client, {}, 'One shopping story', 'Turkish', .5,
                                       45, 42, 48, 5, {'mode': 'production', 'format': 'shorts'})
    request = client.responses.create.call_args.kwargs
    assert request['model'] == 'gpt-6-astra'
    assert request['reasoning'] == {'effort': 'low'}
    assert 'temperature' not in request and 'top_p' not in request
    gemini.assert_not_called()
