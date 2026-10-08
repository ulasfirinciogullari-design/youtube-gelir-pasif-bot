"""Offline price bounds and actual immutable critic/native body compatibility."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from unittest.mock import Mock

import httpx
import pytest

from app.services import gemini_generation as generation
from app.services import gemini37_text_spend_quotes as quotes
from app.services.production_spend import SpendBlocked
from test_scheduled_shot_prompt_preflight import TOPIC, case as planning_case


NOW = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)
HEADERS = {'x-goog-api-key': 'offline-test-key', 'Content-Type': 'application/json'}


def body():
    return {
        'store': False,
        'contents': [{'role': 'user', 'parts': [{'text': 'Judge this complete immutable story.'}]}],
        'generationConfig': {
            'candidateCount': 1, 'thinkingConfig': {'thinkingLevel': 'medium'},
            'responseMimeType': 'application/json', 'maxOutputTokens': 8192,
            'responseJsonSchema': {'type': 'object', 'properties': {'approved': {'type': 'boolean'}},
                                   'required': ['approved'], 'additionalProperties': False},
        },
    }


def quote(request=None, headers=None, now=NOW):
    return quotes.quote_gemini37_text_request(
        body() if request is None else request,
        HEADERS if headers is None else headers, now=now,
    )


@pytest.mark.parametrize('output,expected', [(1, 786436), (2, 786440), (3, 786444),
                                           (4, 786447), (8192, 817152), (16384, 847872)])
def test_full_input_context_plus_ceiling_output_rate(output, expected):
    request = body()
    request['generationConfig']['maxOutputTokens'] = output
    result = quote(request)
    assert result.provider == 'gemini'
    assert result.model == 'gemini-3.7-flash'
    assert result.price_revision == 'gemini37-text-2026-09-09-v1'
    assert result.maximum_micro == expected
    assert quotes.inspect_gemini37_text_request(request)['input_tokens_upper_bound'] == 1_048_576


@pytest.mark.parametrize('thinking', ['low', 'medium', 'high'])
def test_system_schema_and_thinking_never_discount_full_context_reservation(thinking):
    request = body()
    request['systemInstruction'] = {'parts': [{'text': 'Türkçe tam ölçüt: doğruluğu, neden-sonucu ve son sahneyi denetle.'}]}
    request['generationConfig']['thinkingConfig']['thinkingLevel'] = thinking
    request['generationConfig']['responseJsonSchema']['description'] = 'Preserve the whole rubric.'
    before = deepcopy(request)
    inspected = quotes.inspect_gemini37_text_request(request)
    assert inspected['body'] == request and inspected['body'] is not request
    assert inspected['metadata_bytes'] == len(json.dumps(request, ensure_ascii=True, allow_nan=False).encode('ascii'))
    assert quote(request).maximum_micro == 817152
    inspected['body']['contents'][0]['parts'][0]['text'] = 'Changed detached text'
    assert request == before


def test_exact_metadata_boundary_does_not_truncate_or_reprice_prompt():
    request = body()
    overhead = len(json.dumps(request, ensure_ascii=True).encode('ascii')) - len(request['contents'][0]['parts'][0]['text'])
    request['contents'][0]['parts'][0]['text'] = 'x' * (100_000 - overhead)
    inspected = quotes.inspect_gemini37_text_request(request)
    assert inspected['metadata_bytes'] == 100_000
    assert inspected['body'] == request
    assert quote(request).maximum_micro == 817152
    request['contents'][0]['parts'][0]['text'] += 'x'
    with pytest.raises(SpendBlocked, match='^spend_request_not_priced$'):
        quote(request)


@pytest.mark.parametrize('patch', [
    {'tools': [{'google_search': {}}]}, {'toolConfig': {}}, {'cachedContent': 'cachedContents/x'},
    {'serviceTier': 'PRIORITY'}, {'store': True}, {'store': 0}, {'model': 'gemini-3.7-flash'},
    {'safetySettings': []}, {'labels': {}}, {'contents': []},
    {'contents': [{'role': 'model', 'parts': [{'text': 'history'}]}]},
    {'contents': [{'role': 'user', 'parts': [{'text': 'x'}]}] * 2},
    {'contents': [{'role': 'user', 'parts': [{'text': 'x'}, {'text': 'y'}]}]},
    {'contents': [{'role': 'user', 'parts': [{'text': 'x', 'thoughtSignature': 'signature'}]}]},
    {'contents': [{'role': 'user', 'parts': [{'inlineData': {'mimeType': 'audio/wav', 'data': 'AAAA'}}]}]},
    {'contents': [{'role': 'user', 'parts': [{'fileData': {'fileUri': 'gs://not-read'}}]}]},
    {'systemInstruction': {'role': 'system', 'parts': [{'text': 'x'}]}},
    {'systemInstruction': {'parts': [{'text': 'x'}, {'text': 'y'}]}},
    {'systemInstruction': {'parts': [{'text': ' '}] }},
])
def test_unreviewed_tools_media_history_or_request_fields_block(patch):
    request = body()
    request.update(deepcopy(patch))
    with pytest.raises(SpendBlocked, match='^spend_request_not_priced$'):
        quote(request)


@pytest.mark.parametrize('patch', [
    {'candidateCount': 2}, {'candidateCount': True}, {'candidateCount': 1.0},
    {'maxOutputTokens': 0}, {'maxOutputTokens': 16385}, {'maxOutputTokens': True},
    {'maxOutputTokens': '8192'}, {'maxOutputTokens': None},
    {'responseMimeType': 'text/plain'}, {'responseModalities': ['AUDIO']},
    {'temperature': 0}, {'thinkingConfig': {'thinkingBudget': 8192}},
    {'thinkingConfig': {'thinkingLevel': 'minimal'}},
    {'thinkingConfig': {'thinkingLevel': 'medium', 'includeThoughts': True}},
    {'responseSchema': {'type': 'OBJECT'}}, {'responseJsonSchema': None},
    {'responseJsonSchema': {'type': 'object', '$ref': '#/$defs/arbitrary'}},
    {'responseJsonSchema': {'type': 'object', 'properties': {'value': {'type': 'string', 'format': 'uri'}}}},
    {'responseJsonSchema': {'type': 'object', 'properties': {'value': {'type': 'number', 'enum': [float('inf')]}}}},
    {'responseJsonSchema': {'type': 'object', 'properties': {1: {'type': 'string'}}}},
])
def test_output_thinking_and_schema_contracts_are_explicit(patch):
    request = body()
    request['generationConfig'].update(deepcopy(patch))
    with pytest.raises(SpendBlocked, match='^spend_request_not_priced$'):
        quote(request)


@pytest.mark.parametrize('value', ['', ' ', 1, {}, [], '\ud800', 'x\udfffy'])
def test_invalid_or_non_utf8_prompt_is_terminal(value):
    request = body()
    request['contents'][0]['parts'][0]['text'] = value
    with pytest.raises(SpendBlocked, match='^spend_request_not_priced$'):
        quote(request)


@pytest.mark.parametrize('field', ['store', 'contents', 'generationConfig'])
def test_missing_top_level_contract_field_blocks(field):
    request = body()
    request.pop(field)
    with pytest.raises(SpendBlocked):
        quote(request)


@pytest.mark.parametrize('field', ['candidateCount', 'thinkingConfig', 'responseMimeType', 'maxOutputTokens'])
def test_missing_generation_bound_blocks(field):
    request = body()
    request['generationConfig'].pop(field)
    with pytest.raises(SpendBlocked):
        quote(request)


@pytest.mark.parametrize('headers', [
    {}, {'x-goog-api-key': 'key'}, {'Content-Type': 'application/json'},
    {'x-goog-api-key': 'key', 'X-Goog-Api-Key': 'other'},
    {**HEADERS, 'x-goog-user-project': 'alternate-billing'},
    {**HEADERS, 'Authorization': 'Bearer alternative'},
    {**HEADERS, 'X-Goog-Request-Priority': 'high'},
    {**HEADERS, 'Content-Type': 'text/plain'},
    {**HEADERS, 'x-goog-api-key': ''}, {**HEADERS, 'x-goog-api-key': 'key\nvalue'},
    {**HEADERS, 'x-goog-api-key': 'x' * 8193}, {**HEADERS, 'x-goog-api-key': 'é'},
    {**HEADERS, 'x-goog-api-key': 123},
])
def test_authentication_and_billing_headers_have_no_unpriced_defaults(headers):
    with pytest.raises(SpendBlocked, match='^spend_request_not_priced$') as caught:
        quote(headers=headers)
    assert 'offline-test-key' not in str(caught.value)


def test_canonical_case_insensitive_headers_and_schema_omission_match_native_contract():
    request = body()
    request['generationConfig'].pop('responseJsonSchema')
    assert quote(request, headers={'X-Goog-Api-Key': 'offline-test-key', 'content-type': 'application/json'}).maximum_micro == 817152


@pytest.mark.parametrize('now', [datetime(2026, 9, 8, 23, 59, 59, tzinfo=timezone.utc),
    datetime(2026, 10, 1, tzinfo=timezone.utc), datetime(2026, 9, 9), '2026-09-09', 0])
def test_review_expiry_and_aware_clock_are_independent_of_old_catalog(now):
    with pytest.raises(SpendBlocked, match='^spend_price_review_expired$'):
        quote(now=now)


def test_review_window_uses_utc_and_includes_final_microsecond():
    assert quote(now=datetime(2026, 9, 9, 3, tzinfo=timezone(timedelta(hours=3)))).maximum_micro == 817152
    assert quote(now=datetime(2026, 9, 30, 23, 59, 59, 999999, tzinfo=timezone.utc)).maximum_micro == 817152


def test_cyclic_or_overdeep_schema_fails_with_safe_code():
    request = body()
    schema = request['generationConfig']['responseJsonSchema']
    schema['properties']['cycle'] = schema
    with pytest.raises(SpendBlocked, match='^spend_request_not_priced$'):
        quote(request)


@pytest.mark.parametrize('count', [6, 12])
def test_real_immutable_story_critic_native_request_preserves_all_evidence(planning_case, monkeypatch, count):
    from app.services import director

    c = planning_case
    c.settings.gemini_model = quotes.GEMINI37_TEXT_MODEL
    c.options['production_scheduled'] = False
    if count == 12:
        scenes = [deepcopy(c.package['scenes'][i % 6]) for i in range(12)]
        for i, scene in enumerate(scenes):
            scene.update(index=i, narration='Workers carry parcels through warehouses.',
                         tts_text='Workers carry parcels through warehouses.')
        c.package['scenes'] = scenes
        c.package['narration'] = ' '.join(scene['narration'] for scene in scenes)
    before = deepcopy(c.package)
    captured = []
    monkeypatch.setattr(generation, 'enforcement_enabled', lambda: True)
    monkeypatch.setattr(director, 'generate_gemini_json', generation.generate_gemini_json)
    writer = Mock(side_effect=AssertionError('Immutable critic cannot call writer'))
    monkeypatch.setattr(director, '_run_director', writer)

    def guarded_capture(sender, url, **kwargs):
        assert sender is httpx.post and url == quotes.GEMINI37_TEXT_ROUTE
        inspected = quotes.inspect_gemini37_text_request(kwargs['json'])
        result = quotes.quote_gemini37_text_request(kwargs['json'], kwargs['headers'], now=NOW)
        assert result.maximum_micro == 817152
        prompt = kwargs['json']['contents'][0]['parts'][0]['text']
        assert 'IMMUTABLE SELECTED STORY REVIEW' in prompt
        context = json.JSONDecoder().raw_decode(prompt.split('\n', 2)[2])[0]
        assert context['complete_immutable_scenes'] == before['scenes']
        assert context['sources'] == before['sources']
        response, _ = json.JSONDecoder().raw_decode(prompt.split('Return ONLY JSON in exactly this shape:\n', 1)[1])
        captured.append(inspected)
        return httpx.Response(200, json={'candidates': [{'finishReason': 'STOP',
            'content': {'parts': [{'text': json.dumps(response)}]}}]})

    transport = Mock(side_effect=guarded_capture)
    monkeypatch.setattr(generation, 'paid_post', transport)
    out = director.revalidate_immutable_short_story(
        c.package, TOPIC, .5, 'en', c.options,
        immutable_candidate_narrations=[scene['narration'] for scene in c.package['scenes']],
        immutable_scene_fields=True,
    )
    transport.assert_called_once()
    writer.assert_not_called()
    c.model.assert_not_called()
    assert c.package == before and out['scenes'] == before['scenes']
    assert out['stock_scene_qc']['generator_calls'] == 0
    assert out['stock_scene_qc']['critic_calls'] == 1
    assert captured[0]['body']['generationConfig']['maxOutputTokens'] == 8192
    assert captured[0]['body']['generationConfig']['thinkingConfig'] == {'thinkingLevel': 'medium'}
    assert captured[0]['metadata_bytes'] <= 100_000
    assert 'tools' not in captured[0]['body']
