"""Native Abacus requests and spend fences, with no provider traffic."""
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
from types import SimpleNamespace
from unittest.mock import Mock

import fakeredis
import httpx
import pytest

from app.services import abacus_generation as generation
from app.services import production_spend_quotes as quotes
from app.services import production_spend_runtime as runtime
from app.services.production_spend import LEDGER_KEY, SpendBlocked, SpendLedger, SpendPolicy


ROOT = '11111111-1111-4111-8111-111111111111'
CHANNEL = 'UC5v9AvNtD3PTLgo6m1jROOA'
URL = 'https://routellm.abacus.ai/v1/messages'
HAIKU = 'claude-haiku-4-5-20251001'
HEADERS = {'x-api-key': 'private-test-key', 'Content-Type': 'application/json',
           'anthropic-version': '2023-06-01'}


def body(model=HAIKU):
    value = {'model': model, 'system': 'Return JSON.',
             'messages': [{'role': 'user', 'content': 'Bir sonraki sahne.'}],
             'max_tokens': 8192, 'thinking': {'type': 'disabled'}, 'stream': False,
             'service_tier': 'standard_only'}
    if model != HAIKU:
        value['inference_geo'] = 'global'
    return value


def envelope(*, model=HAIKU, text='{"ok": true}'):
    return {'id': 'msg_abc123', 'type': 'message', 'role': 'assistant', 'model': model,
            'content': [{'type': 'text', 'text': text}], 'stop_reason': 'end_turn',
            'usage': {'input_tokens': 10, 'output_tokens': 20}}


@pytest.fixture
def case(monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=True)
    policy = SpendPolicy(10_000_000, 4_000_000, 8_000_000, 800_000, 8_000_000, 500_000)
    ledger = SpendLedger(client, policy, clock=lambda: datetime(2026, 9, 8, tzinfo=timezone.utc))
    ledger.initialize()
    monkeypatch.setattr(runtime, 'settings', SimpleNamespace(studio_spend_enforcement=True))
    monkeypatch.setattr(runtime, 'configured_ledger', lambda: ledger)
    monkeypatch.setattr(quotes, '_fresh', lambda: None)
    record = Mock()
    monkeypatch.setattr(runtime, 'record_abacus_usage', record, raising=False)
    client.sadd(runtime._CHANNEL_INDEX, CHANNEL)
    client.set(runtime._CHANNEL_PREFIX + CHANNEL, json.dumps({
        'id': CHANNEL, 'connection_id': 'connection_AAAAA'}))
    client.set(runtime._JOB_PREFIX + ROOT, json.dumps({
        'task_id': ROOT, 'parent_id': None, 'kind': 'render',
        'spec': {'production_channel_id': CHANNEL,
                 'production_connection_id': 'connection_AAAAA', 'duration_minutes': 0.5}}))
    token = runtime._TASK_ID.set(ROOT)
    yield client, ledger, record
    runtime._TASK_ID.reset(token)


@pytest.mark.parametrize('model,input_micro,output_micro', [
    (HAIKU, 1, 5), ('claude-sonnet-5', 2, 10), ('claude-sonnet-4-6', 3, 15),
])
def test_catalog_per_token_units_match_public_per_million_prices(case, model, input_micro, output_micro):
    request = body(model)
    provider, operation, quote = quotes.quote_http_request(URL, {'json': request, 'headers': HEADERS})
    assert (provider, operation) == ('abacus', '/v1/messages')
    # Public $1/$5 per million is exactly one/five microdollars per token.
    # This deliberately catches an extra 1e6 multiplier/divisor in /v1/models.
    encoded = len(json.dumps(request, ensure_ascii=True, allow_nan=False).encode())
    assert quote.maximum_micro == (encoded + 4096) * input_micro + 8192 * output_micro
    assert quotes.ABACUS_TEXT_RATES_PER_TOKEN[model] == (
        Decimal(input_micro) / 1_000_000, Decimal(output_micro) / 1_000_000)
    usage = generation._usage(envelope(model=model), request)
    assert usage.actual_micro == 10 * input_micro + 20 * output_micro
    assert quote.maximum_micro > usage.actual_micro


@pytest.mark.parametrize('patch', [
    {'model': 'route-llm'}, {'model': 'claude-latest'}, {'model': 'gemini-3.1-flash-lite'},
    {'tools': []}, {'tool_choice': {'type': 'none'}}, {'abacus_tools': []},
    {'thinking': {'type': 'adaptive'}}, {'thinking': {'type': 'disabled', 'budget_tokens': 1}},
    {'cache_control': {'type': 'ephemeral'}}, {'service_tier': 'priority'},
    {'service_tier': 'auto'}, {'inference_geo': 'global'}, {'inference_geo': 'us'},
    {'metadata': {}}, {'output_config': {'format': {'type': 'json_schema', 'schema': {}}}},
    {'max_completion_tokens': 8192}, {'temperature': 0}, {'stream': True},
    {'max_tokens': 0}, {'max_tokens': 8193}, {'max_tokens': True},
    {'system': [{'type': 'text', 'text': 'x'}]},
    {'messages': [{'role': 'assistant', 'content': 'history'}]},
    {'messages': [{'role': 'user', 'content': [{'type': 'image', 'source': {}}]}]},
    {'messages': [{'role': 'user', 'content': 'x', 'cache_control': {'type': 'ephemeral'}}]},
    {'messages': [{'role': 'user', 'content': 'x'}] * 2},
    {'messages': [{'role': 'user', 'content': 'ç' * 20_000}]},
])
def test_unknown_or_unbounded_shapes_never_reserve_or_dispatch(case, patch):
    sender = Mock()
    with pytest.raises(SpendBlocked, match='request_not_priced'):
        runtime.paid_post(sender, URL, json={**body(), **deepcopy(patch)}, headers=HEADERS)
    sender.assert_not_called()
    assert case[1].snapshot()['period']['used_micro'] == 0


@pytest.mark.parametrize('url,headers,extra', [
    (URL.replace('https:', 'http:'), HEADERS, {}),
    (URL.replace('routellm.abacus.ai', 'example.com'), HEADERS, {}),
    (URL.replace('/messages', '/chat/completions'), HEADERS, {}),
    (URL + '?api_key=private-test-key', HEADERS, {}),
    (URL + '#fragment', HEADERS, {}),
    (URL.replace('routellm.', 'private-test-key@routellm.'), HEADERS, {}),
    (URL, {**HEADERS, 'anthropic-beta': 'unreviewed'}, {}),
    (URL, {**HEADERS, 'X-Api-Key': 'another-key'}, {}),
    (URL, {**HEADERS, 'anthropic-version': 'new'}, {}),
    (URL, {**HEADERS, 'x-api-key': 'private\nkey'}, {}),
    (URL, {}, {}),
    (URL, HEADERS, {'params': {'service_tier': 'priority'}}),
    (URL, HEADERS, {'follow_redirects': True}),
])
def test_unreviewed_endpoint_or_headers_are_rejected(case, url, headers, extra):
    sender = Mock()
    with pytest.raises(SpendBlocked, match='request_not_priced'):
        runtime.paid_post(sender, url, json=body(), headers=headers, **extra)
    sender.assert_not_called()


def test_off_switch_blocks_new_provider_even_with_key(case, monkeypatch):
    monkeypatch.setattr(runtime, 'settings', SimpleNamespace(studio_spend_enforcement=False))
    stream = Mock()
    monkeypatch.setattr(generation.httpx, 'stream', stream)
    with pytest.raises(SpendBlocked, match='spend_not_enabled'):
        generation.generate_abacus_json('Draft.', api_key='private-test-key')
    stream.assert_not_called()
    assert case[1].snapshot()['period']['used_micro'] == 0


def test_reservation_precedes_one_transport_and_metadata_precedes_output(case, monkeypatch):
    def stream(method, url, **kwargs):
        assert (method, url) == ('POST', URL)
        assert kwargs['follow_redirects'] is False and kwargs['trust_env'] is False
        assert case[1].snapshot()['period']['used_micro'] > 0
        assert kwargs['json']['thinking'] == {'type': 'disabled'}
        assert kwargs['json']['max_tokens'] == 8192
        assert kwargs['json']['service_tier'] == 'standard_only'
        assert 'inference_geo' not in kwargs['json']
        assert 'output_config' not in kwargs['json']
        assert 'required' in kwargs['json']['system']
        return nullcontext(httpx.Response(200, json=envelope()))
    sender = Mock(side_effect=stream)
    monkeypatch.setattr(generation.httpx, 'stream', sender)
    schema = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}},
              'required': ['ok'], 'additionalProperties': False}
    assert generation.generate_abacus_json('Private planning prompt.', api_key='private-test-key',
                                           json_schema=schema) == {'ok': True}
    case[2].assert_called_once()
    payload, usage = case[2].call_args.args
    assert usage == {'model': HAIKU, 'request_id': 'msg_abc123', 'input_tokens': 10,
                     'output_tokens': 20, 'cache_creation_input_tokens': 0,
                     'cache_read_input_tokens': 0, 'actual_micro': 110}
    assert payload['messages'][0]['content'] == 'Private planning prompt.'
    persisted = json.dumps(case[0].hgetall(LEDGER_KEY))
    assert 'Private planning prompt.' not in persisted and 'private-test-key' not in persisted
    with pytest.raises(SpendBlocked, match='already_reserved'):
        generation.generate_abacus_json('Private planning prompt.', api_key='private-test-key',
                                        json_schema=schema)
    assert sender.call_count == 1


@pytest.mark.parametrize('text', ['{invalid', '[]', 'null', '{"ok": true, "ok": false}',
                                 '{"ok": NaN}', '{"ok": "yes"}', '{"extra": true}'])
def test_paid_invalid_json_keeps_usage_and_reservation_without_retry(case, monkeypatch, text):
    sender = Mock(return_value=nullcontext(httpx.Response(200, json=envelope(text=text))))
    monkeypatch.setattr(generation.httpx, 'stream', sender)
    schema = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}},
              'required': ['ok'], 'additionalProperties': False}
    with pytest.raises(generation.AbacusGenerationError) as caught:
        generation.generate_abacus_json('Draft.', api_key='private-test-key', json_schema=schema)
    assert caught.value.usage.actual_micro == 110
    case[2].assert_called_once()
    assert case[1].snapshot()['period']['used_micro'] > 0 and sender.call_count == 1


@pytest.mark.parametrize('schema', [None, {'type': 'object', 'properties': {'amount': {'type': 'number'}}}])
def test_overflowing_json_number_is_rejected_after_usage_is_recorded(case, monkeypatch, schema):
    sender = Mock(return_value=nullcontext(httpx.Response(200, json=envelope(text='{"amount": 1e999}'))))
    monkeypatch.setattr(generation.httpx, 'stream', sender)
    with pytest.raises(generation.AbacusGenerationError) as caught:
        generation.generate_abacus_json('Draft.', api_key='private-test-key', json_schema=schema)
    assert caught.value.usage.actual_micro == 110
    case[2].assert_called_once()
    assert case[1].snapshot()['period']['used_micro'] > 0 and sender.call_count == 1


@pytest.mark.parametrize('patch', [
    {'usage': None}, {'usage': {}}, {'usage': {'input_tokens': 10}},
    {'usage': {'input_tokens': True, 'output_tokens': 10}},
    {'usage': {'input_tokens': -1, 'output_tokens': 10}},
    {'usage': {'input_tokens': 10, 'output_tokens': 8193}},
    {'usage': {'input_tokens': 200_001, 'output_tokens': 10}},
    {'usage': {'input_tokens': 10, 'output_tokens': 10, 'cache_creation_input_tokens': 1}},
    {'usage': {'input_tokens': 10, 'output_tokens': 10, 'cache_read_input_tokens': 1}},
    {'usage': {'input_tokens': 10, 'output_tokens': 10, 'service_tier': 'priority'}},
    {'usage': {'input_tokens': 10, 'output_tokens': 10, 'inference_geo': 'us'}},
    {'usage': {'input_tokens': 10, 'output_tokens': 10, 'speed': 'fast'}},
    {'usage': {'input_tokens': 10, 'output_tokens': 10, 'server_tool_use': {'web_search_requests': 1}}},
    {'id': None}, {'id': 'secret\nline'}, {'model': 'claude-sonnet-4-6'},
    {'type': 'completion'}, {'role': 'user'},
])
def test_missing_or_unquoted_usage_never_becomes_zero_cost(case, monkeypatch, patch):
    sender = Mock(return_value=nullcontext(httpx.Response(200, json={**envelope(), **patch})))
    monkeypatch.setattr(generation.httpx, 'stream', sender)
    with pytest.raises(generation.AbacusGenerationError):
        generation.generate_abacus_json('Draft.', api_key='private-test-key')
    case[2].assert_not_called()
    assert case[1].snapshot()['period']['used_micro'] > 0 and sender.call_count == 1


@pytest.mark.parametrize('patch', [
    {'stop_reason': 'max_tokens'}, {'stop_reason': 'refusal'}, {'stop_reason': None},
    {'stop_reason': 'tool_use'}, {'stop_reason': 'model_context_window_exceeded'},
    {'content': []}, {'content': [{'type': 'thinking', 'thinking': 'hidden'}]},
    {'content': [{'type': 'text', 'text': '{}'}, {'type': 'text', 'text': '{}'}]},
])
def test_nonfinal_or_nontext_output_records_observed_usage_but_stops(case, monkeypatch, patch):
    sender = Mock(return_value=nullcontext(httpx.Response(200, json={**envelope(), **patch})))
    monkeypatch.setattr(generation.httpx, 'stream', sender)
    with pytest.raises(generation.AbacusGenerationError):
        generation.generate_abacus_json('Draft.', api_key='private-test-key')
    case[2].assert_called_once()
    assert sender.call_count == 1


@pytest.mark.parametrize('status', [301, 400, 401, 429, 500, 503])
def test_rejected_http_status_never_retries_or_leaks_provider_body(case, monkeypatch, status):
    sender = Mock(return_value=nullcontext(httpx.Response(status, text='private-test-key Private prompt')))
    monkeypatch.setattr(generation.httpx, 'stream', sender)
    with pytest.raises(generation.AbacusGenerationError) as caught:
        generation.generate_abacus_json('Draft.', api_key='private-test-key')
    assert str(caught.value) == 'abacus_request_rejected'
    assert caught.value.__cause__ is None
    assert sender.call_count == 1 and case[1].snapshot()['period']['used_micro'] > 0


@pytest.mark.parametrize('model', ['claude-sonnet-4-6', 'claude-sonnet-5'])
def test_sonnet_pins_global_standard_pricing(case, monkeypatch, model):
    sender = Mock(return_value=nullcontext(httpx.Response(200, json=envelope(model=model))))
    monkeypatch.setattr(generation.httpx, 'stream', sender)
    generation.generate_abacus_json('Draft.', api_key='private-test-key', model=model)
    request = sender.call_args.kwargs['json']
    assert request['inference_geo'] == 'global' and request['service_tier'] == 'standard_only'
    for geo in (None, 'us'):
        changed = {**request, 'inference_geo': geo}
        with pytest.raises(SpendBlocked, match='request_not_priced'):
            quotes.quote_http_request(URL, {'json': changed, 'headers': HEADERS})


def test_timeout_leaves_reservation_and_identical_attempt_is_fenced(case, monkeypatch):
    sender = Mock(side_effect=httpx.ReadTimeout('private-test-key Private prompt'))
    monkeypatch.setattr(generation.httpx, 'stream', sender)
    with pytest.raises(generation.AbacusGenerationError, match='abacus_generation_failed'):
        generation.generate_abacus_json('Draft.', api_key='private-test-key')
    with pytest.raises(SpendBlocked, match='already_reserved'):
        generation.generate_abacus_json('Draft.', api_key='private-test-key')
    case[2].assert_not_called()
    assert sender.call_count == 1 and case[1].snapshot()['period']['used_micro'] > 0


def test_response_byte_limit_is_enforced_before_json_decode(case, monkeypatch):
    sender = Mock(return_value=nullcontext(httpx.Response(200, content=b'x' * (2 * 1024 * 1024 + 1))))
    monkeypatch.setattr(generation.httpx, 'stream', sender)
    with pytest.raises(generation.AbacusGenerationError, match='response_too_large'):
        generation.generate_abacus_json('Draft.', api_key='private-test-key')
    case[2].assert_not_called()
    assert sender.call_count == 1


def test_receipt_store_failure_is_original_terminal_spend_block(case, monkeypatch):
    failure = SpendBlocked('spend_usage_uncertain')
    case[2].side_effect = failure
    sender = Mock(return_value=nullcontext(httpx.Response(200, json=envelope())))
    monkeypatch.setattr(generation.httpx, 'stream', sender)
    with pytest.raises(SpendBlocked) as caught:
        generation.generate_abacus_json('Draft.', api_key='private-test-key')
    assert caught.value is failure and sender.call_count == 1
    assert case[1].snapshot()['period']['used_micro'] > 0


def test_budget_refusal_preserves_exception_and_never_dispatches(case, monkeypatch):
    failure = SpendBlocked('spend_lineage_limit')
    monkeypatch.setattr(runtime, 'reserve_request', Mock(side_effect=failure))
    sender = Mock()
    monkeypatch.setattr(generation.httpx, 'stream', sender)
    with pytest.raises(SpendBlocked) as caught:
        generation.generate_abacus_json('Draft.', api_key='private-test-key')
    assert caught.value is failure
    sender.assert_not_called()


@pytest.mark.parametrize('prompt,schema', [
    ('', None), ('x' * 100_001, None), ('\ud800', None),
    ('Draft.', {'type': 'array'}), ('Draft.', {'type': 'object', '$ref': 'https://example.com'}),
    ('Draft.', {'type': 'object', 'description': 'x' * 100_001}),
])
def test_preflight_failures_never_spend(case, monkeypatch, prompt, schema):
    sender = Mock()
    monkeypatch.setattr(generation.httpx, 'stream', sender)
    with pytest.raises((SpendBlocked, generation.AbacusConfigurationError)):
        generation.generate_abacus_json(prompt, api_key='private-test-key', json_schema=schema)
    sender.assert_not_called()
    assert case[1].snapshot()['period']['used_micro'] == 0


def test_director_schema_is_accepted_before_any_generation(case, monkeypatch):
    from app.services.director import _director_json_schema
    schema = _director_json_schema(56, exact_scene_count=True, delivery_family=True)
    failure = SpendBlocked('spend_lineage_limit')
    monkeypatch.setattr(runtime, 'reserve_request', Mock(side_effect=failure))
    with pytest.raises(SpendBlocked) as caught:
        generation.generate_abacus_json('Draft.', api_key='private-test-key', json_schema=schema)
    assert caught.value is failure


def test_price_review_expires_at_october_boundary(monkeypatch):
    class Clock:
        @staticmethod
        def now(_):
            return datetime(2026, 10, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(quotes, 'datetime', Clock)
    with pytest.raises(SpendBlocked, match='expired'):
        quotes.quote_http_request(URL, {'json': body(), 'headers': HEADERS})
