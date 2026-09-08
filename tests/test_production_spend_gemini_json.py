"""Bounded Gemini planning quotes exercised without any provider traffic."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import Mock

import fakeredis
import httpx
import pytest

from app.services import gemini_generation as generation
from app.services import production_spend_quotes as quotes
from app.services import production_spend_runtime as runtime
from app.services.production_spend import LEDGER_KEY, SpendBlocked, SpendLedger, SpendPolicy


ROOT = '11111111-1111-4111-8111-111111111111'
CHANNEL = 'UC5v9AvNtD3PTLgo6m1jROOA'
URL = ('https://generativelanguage.googleapis.com/v1beta/models/'
       'gemini-3.1-pro-preview:generateContent')


def body():
    return {
        'store': False,
        'contents': [{'role': 'user', 'parts': [{'text': 'Bir sonraki sahne.'}]}],
        'generationConfig': {
            'candidateCount': 1,
            'responseMimeType': 'application/json',
            'thinkingConfig': {'thinkingLevel': 'medium'},
            'maxOutputTokens': 8192,
        },
    }


def expected_micro(request_body):
    # The complete ASCII JSON size overcounts UTF-8 tokens, including schemas.
    encoded_bytes = len(json.dumps(request_body, ensure_ascii=True).encode())
    return 2 * (encoded_bytes + 4096) + 12 * request_body['generationConfig']['maxOutputTokens']


@pytest.fixture
def case(monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=True)
    policy = SpendPolicy(10_000_000, 4_000_000, 8_000_000, 120_000, 8_000_000, 500_000)
    ledger = SpendLedger(client, policy, clock=lambda: datetime(2026, 9, 8, tzinfo=timezone.utc))
    ledger.initialize()
    monkeypatch.setattr(runtime, 'settings', SimpleNamespace(studio_spend_enforcement=True))
    monkeypatch.setattr(runtime, 'configured_ledger', lambda: ledger)
    monkeypatch.setattr(quotes, '_fresh', lambda: None)
    client.sadd(runtime._CHANNEL_INDEX, CHANNEL)
    client.set(runtime._CHANNEL_PREFIX + CHANNEL, json.dumps({
        'id': CHANNEL, 'connection_id': 'connection_AAAAA'}))
    client.set(runtime._JOB_PREFIX + ROOT, json.dumps({
        'task_id': ROOT, 'parent_id': None, 'kind': 'render',
        'spec': {'production_channel_id': CHANNEL,
                 'production_connection_id': 'connection_AAAAA', 'duration_minutes': 0.5}}))
    token = runtime._TASK_ID.set(ROOT)
    yield client, ledger
    runtime._TASK_ID.reset(token)


def test_text_system_instruction_and_inline_schema_are_in_upper_bound(case):
    request_body = body()
    request_body['systemInstruction'] = {'parts': [{'text': 'Türkçe yanıtla.'}]}
    request_body['generationConfig']['responseJsonSchema'] = {
        'type': 'object', 'properties': {
            'scenes': {'type': 'array', 'minItems': 1, 'maxItems': 3,
                       'items': {'type': 'string', 'minLength': 1, 'maxLength': 500}},
        }, 'required': ['scenes'], 'additionalProperties': False,
    }
    provider, operation, quote = quotes.quote_http_request(URL, {'json': request_body})
    assert provider == 'gemini'
    assert operation == '/v1beta/models/gemini-3.1-pro-preview:generateContent'
    assert quote.maximum_micro == expected_micro(request_body)
    assert quote.maximum_micro > expected_micro(body())


@pytest.mark.parametrize('thinking', ['low', 'medium', 'high'])
def test_thinking_level_never_discounts_reserved_output_tokens(case, thinking):
    request_body = body()
    request_body['generationConfig']['thinkingConfig']['thinkingLevel'] = thinking
    quote = quotes.quote_http_request(URL, {'json': request_body})[2]
    assert quote.maximum_micro == expected_micro(request_body)


def test_current_eight_minute_director_schema_with_three_shorts_is_priced(case):
    from app.services.director import _director_json_schema
    request_body = body()
    request_body['generationConfig']['responseJsonSchema'] = _director_json_schema(
        56, exact_scene_count=True, delivery_family=True)
    quote = quotes.quote_http_request(URL, {'json': request_body})[2]
    assert quote.maximum_micro == expected_micro(request_body)


@pytest.mark.parametrize('patch', [
    {'tools': [{'google_search': {}}]},
    {'toolConfig': {'functionCallingConfig': {'mode': 'AUTO'}}},
    {'cachedContent': 'cachedContents/private'},
    {'serviceTier': 'PRIORITY'},
    {'store': True},
    {'contents': [{'role': 'user', 'parts': [{'fileData': {'fileUri': 'gs://hidden'}}]}]},
    {'contents': [{'role': 'user', 'parts': [{'inlineData': {'data': 'aW1hZ2U='}}]}]},
    {'contents': [{'role': 'model', 'parts': [{'text': 'history'}]}]},
    {'contents': [{'role': 'user', 'parts': [{'text': 'x', 'thoughtSignature': 'hidden'}]}]},
    {'contents': [{'role': 'user', 'parts': [{'text': 'x'}]}] * 2},
    {'contents': [{'role': 'user', 'parts': [{'text': 'x'}] * 33}]},
    {'contents': [{'role': 'user', 'parts': [{'text': 'ç' * 20_000}]}]},
    {'systemInstruction': {'parts': [{'fileData': {'fileUri': 'gs://hidden'}}]}},
])
def test_unpriced_inputs_never_reserve_or_reach_transport(case, patch):
    sender = Mock()
    with pytest.raises(SpendBlocked, match='request_not_priced'):
        runtime.paid_post(sender, URL, json={**body(), **patch})
    sender.assert_not_called()
    assert case[1].snapshot()['period']['used_micro'] == 0


@pytest.mark.parametrize('patch', [
    {'candidateCount': 2}, {'candidateCount': True},
    {'maxOutputTokens': None}, {'maxOutputTokens': 0},
    {'maxOutputTokens': 16_385}, {'maxOutputTokens': True},
    {'responseMimeType': 'audio/mpeg'}, {'responseModalities': ['AUDIO']},
    {'thinkingConfig': {'thinkingBudget': -1}},
    {'thinkingConfig': {'thinkingLevel': 'high', 'includeThoughts': True}},
    {'responseJsonSchema': {'type': 'object', '$ref': 'https://example.com/schema.json'}},
    {'responseJsonSchema': {'type': 'object', 'properties': {'x': {'$ref': '#'}}}},
    {'responseJsonSchema': {'type': 'object', 'description': 'x' * 100_000}},
    {'responseJsonSchema': {'type': 'object', 'minimum': float('nan')}},
])
def test_unbounded_output_and_schema_variants_never_dispatch(case, patch):
    request_body = body()
    request_body['generationConfig'].update(deepcopy(patch))
    sender = Mock()
    with pytest.raises(SpendBlocked, match='request_not_priced'):
        runtime.paid_post(sender, URL, json=request_body)
    sender.assert_not_called()


@pytest.mark.parametrize('url,kwargs', [
    (URL.replace('gemini-3.1-pro-preview', 'gemini-latest'), {}),
    (URL.replace(':generateContent', ':streamGenerateContent'), {}),
    (URL.replace('generativelanguage.googleapis.com', 'example.com'), {}),
    (URL + '?key=test-key', {}),
    (URL, {'params': {'serviceTier': 'PRIORITY'}}),
    (URL, {'headers': {'x-goog-api-key': 'test-key', 'x-goog-request-params': 'priority'}}),
    (URL, {'headers': {'x-goog-api-key': 'first', 'X-Goog-Api-Key': 'second'}}),
])
def test_unknown_endpoint_and_transport_variants_stay_blocked(case, url, kwargs):
    sender = Mock()
    with pytest.raises(SpendBlocked, match='request_not_priced'):
        runtime.paid_post(sender, url, json=body(), **kwargs)
    sender.assert_not_called()


def test_real_json_helper_reserves_before_one_create_and_keeps_prompt_private(case, monkeypatch):
    def post(url, **kwargs):
        assert url == URL
        assert case[1].snapshot()['period']['used_micro'] == expected_micro(kwargs['json'])
        assert kwargs['json']['generationConfig']['maxOutputTokens'] == 8192
        return httpx.Response(200, json={'candidates': [{
            'finishReason': 'STOP', 'content': {'parts': [{'text': '{"ok": true}'}]},
        }]})
    sender = Mock(side_effect=post)
    monkeypatch.setattr(generation.httpx, 'post', sender)
    assert generation.generate_gemini_json('Private planning prompt.', api_key='private-test-key') == {'ok': True}
    persisted = json.dumps(case[0].hgetall(LEDGER_KEY))
    assert 'Private planning prompt.' not in persisted
    assert 'private-test-key' not in persisted
    with pytest.raises(SpendBlocked, match='already_reserved'):
        generation.generate_gemini_json('Private planning prompt.', api_key='private-test-key')
    assert sender.call_count == 1


@pytest.mark.parametrize('response', [
    TimeoutError('unknown acceptance'),
    httpx.Response(503),
    httpx.Response(200, json={'candidates': [{'finishReason': 'STOP',
                                           'content': {'parts': [{'text': '{invalid'}]}}]}),
])
def test_existing_helper_retry_cannot_replay_an_uncertain_paid_create(case, monkeypatch, response):
    sender = Mock(side_effect=response) if isinstance(response, Exception) else Mock(return_value=response)
    monkeypatch.setattr(generation.httpx, 'post', sender)
    with pytest.raises(SpendBlocked, match='already_reserved'):
        generation.generate_gemini_json('One planning attempt.', api_key='test-key', retry_once=True)
    assert sender.call_count == 1
    assert case[1].snapshot()['period']['used_micro'] > 0


def test_changed_repair_prompt_still_uses_original_family_allowance(case, monkeypatch):
    sender = Mock(return_value=httpx.Response(200, json={'candidates': [{
        'finishReason': 'STOP', 'content': {'parts': [{'text': '{}'}]},
    }]}))
    monkeypatch.setattr(generation.httpx, 'post', sender)
    generation.generate_gemini_json('First plan.', api_key='test-key')
    with pytest.raises(SpendBlocked, match='lineage_limit'):
        generation.generate_gemini_json('Repair the plan.', api_key='test-key')
    assert sender.call_count == 1


def test_gemini_catalog_expires_at_review_boundary(monkeypatch):
    class Clock:
        @staticmethod
        def now(_):
            return datetime(2026, 10, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(quotes, 'datetime', Clock)
    monkeypatch.setattr(runtime, 'settings', SimpleNamespace(studio_spend_enforcement=True))
    sender = Mock()
    with pytest.raises(SpendBlocked, match='expired'):
        runtime.paid_post(sender, URL, json=body())
    sender.assert_not_called()
