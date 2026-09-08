"""The opt-in Abacus edit keeps fresh-job scope, quality schema and budget fence."""
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import production_spend_runtime as runtime
from app.services.production_spend import LEDGER_KEY, SpendBlocked
from test_production_spend_runtime import case


def _run(director, *, fresh=True, correction=False):
    options = {'mode': 'production', 'format': 'shorts', 'content_style': 'documentary'}
    compact = {'sources': [{'url': 'https://www.bep.gov/currency/how-money-is-made',
                           'evidence': 'The paper composition is documented by the Bureau of Engraving and Printing.'}]}
    return director._run_director(
        Mock(), compact, 'Explain one source-backed warehouse membership story.',
        'English', 0.5, 65, 62, 66, 3, options,
        exact_scene_count=True, fresh_scheduled=fresh, correction=correction,
    )


@pytest.fixture
def editorial(monkeypatch):
    from app.services import abacus_generation, director
    settings = SimpleNamespace(studio_plan_provider='gemini',
        studio_abacus_editorial_enabled=True,
        studio_abacus_editorial_model='claude-haiku-4-5-20251001',
        abacus_api_key='FAKE-ABACUS-KEY', gemini_api_key='FAKE-GEMINI-KEY',
        gemini_model='gemini-3.1-pro-preview')
    monkeypatch.setattr(director, 'settings', settings)
    monkeypatch.setattr(director, '_studio_plan_provider', lambda: 'gemini')
    gemini = Mock(return_value={'existing_provider': True})
    openai = Mock(side_effect=AssertionError('No OpenAI fallback'))
    monkeypatch.setattr(director, 'generate_gemini_json', gemini)
    monkeypatch.setattr(director, 'paid_response', openai)
    return SimpleNamespace(module=director, abacus=abacus_generation,
                           settings=settings, gemini=gemini, openai=openai)


@pytest.mark.parametrize('fresh,enabled', [(False, True), (True, False), (True, 'true'), (True, 1)])
def test_only_fresh_job_and_literal_server_opt_in_select_abacus(editorial, monkeypatch, fresh, enabled):
    editorial.settings.studio_abacus_editorial_enabled = enabled
    abacus = Mock(side_effect=AssertionError('Abacus must remain off'))
    monkeypatch.setattr(editorial.abacus, 'generate_abacus_json', abacus)
    assert _run(editorial.module, fresh=fresh) == {'existing_provider': True}
    editorial.gemini.assert_called_once()
    abacus.assert_not_called()


@pytest.mark.parametrize('correction', [False, True])
def test_fresh_editorial_uses_same_evidence_and_exact_business_schema(editorial, monkeypatch, correction):
    abacus = Mock(return_value={'selected': 'abacus'})
    monkeypatch.setattr(editorial.abacus, 'generate_abacus_json', abacus)
    before = deepcopy(vars(editorial.settings))
    assert _run(editorial.module, correction=correction) == {'selected': 'abacus'}
    call = abacus.call_args
    assert 'Bureau of Engraving and Printing' in call.args[0]
    assert 'The master video is text-free' in call.args[0]
    assert call.kwargs['max_tokens'] == 8192
    assert call.kwargs['json_schema'] == editorial.module._director_json_schema(3, exact_scene_count=True)
    assert call.kwargs['model'] == before['studio_abacus_editorial_model']
    assert vars(editorial.settings) == before
    editorial.gemini.assert_not_called()
    editorial.openai.assert_not_called()


@pytest.mark.parametrize('reason', ['spend_day_limit', 'spend_request_already_reserved'])
def test_budget_error_never_selects_another_editorial_provider(editorial, monkeypatch, reason):
    error = SpendBlocked(reason)
    monkeypatch.setattr(editorial.abacus, 'generate_abacus_json', Mock(side_effect=error))
    with pytest.raises(SpendBlocked) as caught:
        _run(editorial.module)
    assert caught.value is error
    editorial.gemini.assert_not_called()
    editorial.openai.assert_not_called()


def test_real_editorial_adapter_records_usage_before_rejecting_bad_json(case, editorial, monkeypatch):
    client, ledger = case
    native = {'id': 'msg_editorial_bad_json', 'type': 'message', 'role': 'assistant',
              'model': editorial.settings.studio_abacus_editorial_model,
              'usage': {'input_tokens': 100, 'output_tokens': 10},
              'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': 'bad json'}]}
    transport = Mock(return_value=native)
    monkeypatch.setattr(editorial.abacus, '_post_bounded', transport)
    with pytest.raises(editorial.abacus.AbacusGenerationError):
        _run(editorial.module)
    used = ledger.snapshot()['period']['used_micro']
    assert used > 0
    receipts = [json.loads(value) for key, value in client.hgetall(LEDGER_KEY).items()
                if key.startswith('usage:')]
    assert len(receipts) == 1 and receipts[0]['actual_micro'] == 150
    with pytest.raises(SpendBlocked, match='already_reserved'):
        _run(editorial.module)
    assert transport.call_count == 1
    assert ledger.snapshot()['period']['used_micro'] == used
    editorial.gemini.assert_not_called()
    editorial.openai.assert_not_called()


def test_real_editorial_adapter_accepts_exact_schema_and_keeps_reservation(case, editorial, monkeypatch):
    client, ledger = case
    expected = {'title': 'Membership income', 'thumbnail_text': 'Member fees',
                'description': 'A source-backed membership explanation.',
                'qc_summary': ['Preserved the source institution.'],
                'scenes': [{'narration': f'Membership scene {index}.',
                            'visual_queries': ['warehouse worker sorts boxes', 'worker moves sealed parcels'],
                            'ai_prompt': None, 'pace': 'normal', 'transition': 'cut'}
                           for index in range(3)]}
    native = {'id': 'msg_editorial_valid', 'type': 'message', 'role': 'assistant',
              'model': editorial.settings.studio_abacus_editorial_model,
              'usage': {'input_tokens': 200, 'output_tokens': 100},
              'stop_reason': 'end_turn',
              'content': [{'type': 'text', 'text': json.dumps(expected)}]}
    transport = Mock(return_value=native)
    monkeypatch.setattr(editorial.abacus, '_post_bounded', transport)
    assert _run(editorial.module) == expected
    assert transport.call_count == 1
    assert ledger.snapshot()['period']['used_micro'] > 700
    receipts = [json.loads(value) for key, value in client.hgetall(LEDGER_KEY).items()
                if key.startswith('usage:')]
    assert len(receipts) == 1 and receipts[0]['actual_micro'] == 700
    editorial.gemini.assert_not_called()
    editorial.openai.assert_not_called()


def test_editorial_flag_alone_cannot_enable_paid_dispatch(case, editorial, monkeypatch):
    monkeypatch.setattr(runtime.settings, 'studio_spend_enforcement', False)
    transport = Mock()
    monkeypatch.setattr(editorial.abacus, '_post_bounded', transport)
    with pytest.raises(SpendBlocked, match='not_enabled'):
        _run(editorial.module)
    transport.assert_not_called()
    editorial.gemini.assert_not_called()
