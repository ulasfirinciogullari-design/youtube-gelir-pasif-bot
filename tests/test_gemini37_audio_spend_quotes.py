"""Actual Short bytes, native audio-QC requests and offline funding/replay."""
import base64
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock

import fakeredis
import httpx
import pytest

from app.services import gemini37_audio_spend_quotes as quotes
from app.services import gemini37_text_spend_quotes as text_quotes
from app.services import gemini_generation as generation
from app.services import production_spend_quotes as routing
from app.services import production_spend_runtime as runtime
from app.services import whisper_transcription as whisper
from app.services.production_spend import LEDGER_KEY, SpendBlocked, SpendLedger, SpendPolicy
from test_audio_qc import audio_qc as qc
from test_whisper_transcription import _wav, _mp3


NOW = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)
KEY = 'offline-gemini-audio-key'
HEADERS = {'x-goog-api-key': KEY, 'Content-Type': 'application/json'}
ROOT = '11111111-1111-4111-8111-111111111111'
CHILD = '22222222-2222-4222-8222-222222222222'
CHANNEL = 'UC5v9AvNtD3PTLgo6m1jROOA'


def body(raw=None, mime='audio/wav'):
    return {
        'store': False,
        'contents': [{'role': 'user', 'parts': [
            {'text': 'Listen to the complete original narration: Hello there.'},
            {'inlineData': {'mimeType': mime, 'data': base64.b64encode(
                _wav() if raw is None else raw).decode('ascii')}},
        ]}],
        'systemInstruction': {'parts': [{'text': qc._PROSODY_SYSTEM_INSTRUCTION}]},
        'generationConfig': {
            'candidateCount': 1, 'thinkingConfig': {'thinkingLevel': 'medium'},
            'responseMimeType': 'application/json', 'maxOutputTokens': 8192,
            'responseJsonSchema': deepcopy(qc._PROSODY_REVIEW_SCHEMA),
        },
    }


def quote(request=None, headers=None, now=NOW):
    return quotes.quote_gemini37_audio_request(
        body() if request is None else request,
        HEADERS if headers is None else headers, now=now,
    )


@pytest.mark.parametrize('output,expected', [(1, 786436), (2, 786440), (3, 786444),
                                           (4, 786447), (8191, 817149), (8192, 817152)])
def test_full_input_context_and_combined_output_ceiling(output, expected):
    request = body()
    request['generationConfig']['maxOutputTokens'] = output
    result = quote(request)
    assert (result.provider, result.model, result.price_revision, result.maximum_micro) == (
        'gemini', 'gemini-3.7-flash', 'gemini37-audio-2026-09-09-v1', expected)


@pytest.mark.parametrize('mime', ['audio/wav', 'audio/mpeg'])
def test_complete_original_audio_schema_and_rubric_are_detached_and_identified(mime):
    raw = _wav(amplitude=42) if mime == 'audio/wav' else _mp3()
    request = body(raw, mime)
    before = deepcopy(request)
    inspected = quotes.inspect_gemini37_audio_request(request)
    assert inspected['body'] == before and inspected['body'] is not request
    assert inspected['input_tokens_upper_bound'] == 1_048_576
    assert inspected['max_output_tokens'] == 8192
    audio = inspected['audio']
    assert audio == whisper.inspect_bounded_short_audio(raw, mime)
    assert audio['sha256'] == hashlib.sha256(raw).hexdigest()
    assert audio['bytes'] == len(raw) and audio['mime_type'] == mime
    assert 0 < audio['decoded_samples'] <= 1_443_840
    assert audio['decoded_sample_rate'] == 48_000 and len(audio['decoded_pcm_sha256']) == 64
    assert quote(request).maximum_micro == 817152
    inspected['body']['systemInstruction']['parts'][0]['text'] = 'detached mutation'
    inspected['body']['generationConfig']['responseJsonSchema']['required'].clear()
    inspected['body']['contents'][0]['parts'][1]['inlineData']['data'] = ''
    assert request == before


def test_full_duration_and_one_sample_over_boundary_use_actual_decoding():
    raw = _wav(samples=1_443_840)
    inspected = quotes.inspect_gemini37_audio_request(body(raw))
    assert inspected['audio']['decoded_samples'] == 1_443_840
    assert quote(body(raw)).maximum_micro == quote(body(_wav(samples=48))).maximum_micro
    with pytest.raises(SpendBlocked, match='^spend_request_not_priced$'):
        quote(body(_wav(samples=1_443_841)))


@pytest.mark.parametrize('raw,mime', [
    (b'', 'audio/wav'), (b'RIFF invalid', 'audio/wav'), (_wav() + b'hidden', 'audio/wav'),
    (_wav(), 'audio/mpeg'), (_wav(samples=0), 'audio/wav'),
])
def test_empty_malformed_trailing_and_mislabeled_audio_never_prices(raw, mime):
    with pytest.raises(SpendBlocked, match='^spend_request_not_priced$'):
        quote(body(raw, mime))


def test_mp3_frame_walker_rejects_extra_hidden_file():
    with pytest.raises(SpendBlocked, match='^spend_request_not_priced$'):
        quote(body(_mp3() + _wav(), 'audio/mpeg'))


@pytest.mark.parametrize('encoded', ['', 'AAAA\n', '%%%not-base64', 'YR==', '====',
                                     'data:audio/wav;base64,AAAA', 'https://example.invalid/audio'])
def test_only_canonical_complete_base64_is_accepted(encoded):
    request = body()
    request['contents'][0]['parts'][1]['inlineData']['data'] = encoded
    with pytest.raises(SpendBlocked, match='^spend_request_not_priced$'):
        quote(request)


@pytest.mark.parametrize('kind', ['encoded_size', 'decoded_size', 'metadata', 'repeated_metadata'])
def test_resource_bounds_fail_before_any_decoder(kind, monkeypatch):
    request = body()
    if kind == 'encoded_size':
        request['contents'][0]['parts'][1]['inlineData']['data'] = 'A' * (quotes._MAX_BASE64_BYTES + 1)
    elif kind == 'decoded_size':
        request['contents'][0]['parts'][1]['inlineData']['data'] = base64.b64encode(
            b'x' * (whisper.WHISPER_MAX_AUDIO_BYTES + 1)).decode('ascii')
    elif kind == 'metadata':
        request['contents'][0]['parts'][0]['text'] = 'x' * 100_000
    else:
        schema = request['generationConfig']['responseJsonSchema']
        schema['properties'] = {str(i): {'type': 'string', 'description': 'x' * 9999}
                                for i in range(2000)}
    decode = Mock(side_effect=AssertionError('Unbounded request reached audio decode'))
    monkeypatch.setattr(quotes, 'inspect_bounded_short_audio', decode)
    with pytest.raises(SpendBlocked, match='^spend_request_not_priced$'):
        quote(request)
    decode.assert_not_called()


def test_exact_metadata_boundary_keeps_every_original_field():
    request = body()
    inspected = quotes.inspect_gemini37_audio_request(request)
    prompt = request['contents'][0]['parts'][0]
    prompt['text'] += 'x' * (100_000 - inspected['metadata_bytes'])
    result = quotes.inspect_gemini37_audio_request(request)
    assert result['metadata_bytes'] == 100_000 and result['body'] == request
    prompt['text'] += 'x'
    with pytest.raises(SpendBlocked):
        quote(request)


@pytest.mark.parametrize('patch', [
    {'tools': [{'google_search': {}}]}, {'cachedContent': 'cachedContents/private'},
    {'serviceTier': 'PRIORITY'}, {'toolConfig': {}}, {'safetySettings': []},
    {'store': True}, {'store': 0}, {'model': 'gemini-3.7-flash'},
    {'systemInstruction': None}, {'systemInstruction': {'role': 'system', 'parts': [{'text': 'x'}]}},
    {'systemInstruction': {'parts': [{'text': 'x'}, {'text': 'y'}]}},
    {'contents': []}, {'contents': [{'role': 'model', 'parts': []}]},
])
def test_unpriced_native_options_do_not_gain_audio_quote(patch):
    request = body()
    request.update(deepcopy(patch))
    with pytest.raises(SpendBlocked, match='^spend_request_not_priced$'):
        quote(request)


@pytest.mark.parametrize('patch', [
    {'candidateCount': 2}, {'candidateCount': True}, {'candidateCount': 1.0},
    {'maxOutputTokens': 0}, {'maxOutputTokens': 8193}, {'maxOutputTokens': True},
    {'maxOutputTokens': '8192'}, {'maxOutputTokens': None}, {'responseMimeType': 'audio/wav'},
    {'responseModalities': ['AUDIO']}, {'thinkingConfig': {'thinkingLevel': 'high'}},
    {'thinkingConfig': {'thinkingLevel': 'low'}}, {'thinkingConfig': {'thinkingBudget': 8192}},
    {'thinkingConfig': {'thinkingLevel': 'medium', 'includeThoughts': True}},
    {'responseJsonSchema': None}, {'responseJsonSchema': {'type': 'object', '$ref': '#/x'}},
    {'responseJsonSchema': {'type': 'object', 'properties': {1: {'type': 'string'}}}},
    {'responseJsonSchema': {'type': 'object', 'required': ('pass',)}},
    {'responseJsonSchema': {'type': 'object', 'properties': {'x': {'type': 'number', 'enum': [float('inf')]}}}},
])
def test_generation_bounds_and_complete_schema_are_strict(patch):
    request = body()
    request['generationConfig'].update(deepcopy(patch))
    with pytest.raises(SpendBlocked, match='^spend_request_not_priced$'):
        quote(request)


@pytest.mark.parametrize('value', ['', ' ', 123, [], {}, '\ud800'])
def test_prompt_and_system_text_require_valid_utf8(value):
    for section in ('contents', 'systemInstruction'):
        request = body()
        parts = request[section][0]['parts'] if section == 'contents' else request[section]['parts']
        parts[0]['text'] = value
        with pytest.raises(SpendBlocked, match='^spend_request_not_priced$'):
            quote(request)


@pytest.mark.parametrize('field', ['store', 'contents', 'generationConfig', 'systemInstruction'])
def test_all_audio_review_sections_are_required(field):
    request = body()
    request.pop(field)
    with pytest.raises(SpendBlocked):
        quote(request)


@pytest.mark.parametrize('field', ['candidateCount', 'thinkingConfig', 'responseMimeType',
                                  'maxOutputTokens', 'responseJsonSchema'])
def test_all_audio_generation_settings_are_required(field):
    request = body()
    request['generationConfig'].pop(field)
    with pytest.raises(SpendBlocked):
        quote(request)


@pytest.mark.parametrize('headers', [
    {}, {'x-goog-api-key': KEY}, {'Content-Type': 'application/json'},
    {'x-goog-api-key': KEY, 'X-Goog-Api-Key': 'alternate'},
    {**HEADERS, 'x-goog-user-project': 'other-account'}, {**HEADERS, 'Authorization': 'Bearer other'},
    {**HEADERS, 'X-Goog-Request-Priority': 'high'}, {**HEADERS, 'Content-Type': 'text/plain'},
    {**HEADERS, 'x-goog-api-key': ''}, {**HEADERS, 'x-goog-api-key': 'key\nvalue'},
    {**HEADERS, 'x-goog-api-key': 1}, {**HEADERS, 'x-goog-api-key': 'x' * 8193},
])
def test_alternative_account_headers_and_unpriced_options_block(headers):
    with pytest.raises(SpendBlocked, match='^spend_request_not_priced$') as caught:
        quote(headers=headers)
    assert KEY not in str(caught.value)


@pytest.mark.parametrize('now', [datetime(2026, 9, 8, 23, 59, 59, tzinfo=timezone.utc),
    datetime(2026, 10, 1, tzinfo=timezone.utc), datetime(2026, 9, 9), 0, '2026-09-09'])
def test_review_window_rejects_stale_or_untrusted_clock(now):
    with pytest.raises(SpendBlocked, match='^spend_price_review_expired$'):
        quote(now=now)


def test_utc_window_and_case_insensitive_headers_are_supported():
    assert quote(now=datetime(2026, 9, 9, 3, tzinfo=timezone(timedelta(hours=3)))).maximum_micro == 817152
    assert quote(now=datetime(2026, 9, 30, 23, 59, 59, 999999, tzinfo=timezone.utc), headers={
        'X-Goog-Api-Key': KEY, 'content-type': 'application/json'}).maximum_micro == 817152


@pytest.fixture
def freeze_prices(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, _):
            return cls(2026, 9, 9, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(quotes, 'datetime', Clock)
    monkeypatch.setattr(text_quotes, 'datetime', Clock)


def test_fixed_native_route_selects_distinct_audio_and_text_revisions(freeze_prices):
    request = body()
    provider, operation, result = routing.quote_http_request(quotes.GEMINI37_AUDIO_ROUTE,
        {'json': request, 'headers': HEADERS, 'timeout': 120})
    assert provider == 'gemini' and operation.endswith('/gemini-3.7-flash:generateContent')
    assert result.price_revision == quotes.GEMINI37_AUDIO_PRICE_REVISION
    request['contents'][0]['parts'].pop()
    result = routing.quote_http_request(quotes.GEMINI37_AUDIO_ROUTE,
        {'json': request, 'headers': HEADERS})[2]
    assert result.price_revision == text_quotes.GEMINI37_TEXT_PRICE_REVISION


@pytest.mark.parametrize('change', ['swapped', 'only_audio', 'extra_text', 'video', 'image',
                                   'file_url', 'mime_alias', 'extra_inline', 'mixed_part'])
def test_unknown_media_never_falls_back_to_text(change, freeze_prices, monkeypatch):
    request = body()
    parts = request['contents'][0]['parts']
    if change == 'swapped': parts.reverse()
    elif change == 'only_audio': parts.pop(0)
    elif change == 'extra_text': parts.append({'text': 'extra'})
    elif change == 'video': parts[1]['inlineData']['mimeType'] = 'video/mp4'
    elif change == 'image': parts[1]['inlineData']['mimeType'] = 'image/jpeg'
    elif change == 'mime_alias': parts[1]['inlineData']['mimeType'] = 'audio/x-wav'
    elif change == 'file_url': parts[1] = {'fileData': {'fileUri': 'gs://not-read'}}
    elif change == 'extra_inline': parts[1]['inlineData']['displayName'] = 'extra'
    else: parts[0]['inlineData'] = parts[1]['inlineData']
    text = Mock(side_effect=AssertionError('Unknown media fell back to text'))
    monkeypatch.setattr(text_quotes, 'quote_gemini37_text_request', text)
    with pytest.raises(SpendBlocked, match='^spend_request_not_priced$'):
        routing.quote_http_request(quotes.GEMINI37_AUDIO_ROUTE, {'json': request, 'headers': HEADERS})
    text.assert_not_called()


@pytest.mark.parametrize('suffix', ['?alt=json', '#fragment', '?key=private'])
def test_undocumented_endpoint_options_never_price(suffix, freeze_prices):
    with pytest.raises(SpendBlocked):
        routing.quote_http_request(quotes.GEMINI37_AUDIO_ROUTE + suffix,
                                   {'json': body(), 'headers': HEADERS})


@pytest.mark.parametrize('model', ['gemini-3.7-flash-preview', 'gemini-3.1-pro-preview', 'other-model'])
def test_audio_quote_cannot_price_another_model(model, freeze_prices):
    with pytest.raises(SpendBlocked):
        routing.quote_http_request(quotes.GEMINI37_AUDIO_ROUTE.replace('gemini-3.7-flash', model),
                                   {'json': body(), 'headers': HEADERS})


def _review():
    return {'pass': True, 'summary': 'Natural narration.', 'scores': {
        'pronunciation': 92, 'naturalness': 88, 'pacing': 86, 'sentence_flow': 90,
        'emphasis': 84, 'roboticness': 12}, 'issues': []}


def _response(review=None):
    return httpx.Response(200, json={'candidates': [{'finishReason': 'STOP',
        'content': {'parts': [{'text': json.dumps(_review() if review is None else review)}]}}]})


@pytest.fixture
def funded(freeze_prices, monkeypatch, tmp_path):
    client = fakeredis.FakeRedis(decode_responses=True)
    ledger = SpendLedger(client, SpendPolicy(*([10_000_000] * 6)), clock=lambda: NOW)
    ledger.initialize()
    monkeypatch.setattr(runtime, 'settings', SimpleNamespace(studio_spend_enforcement=True))
    monkeypatch.setattr(runtime, 'configured_ledger', lambda: ledger)
    ledger.initialize_funding({
        'version': 1, 'currency': 'USD', 'month': '2026-09',
        'valid_from': '2026-09-09T00:00:00Z', 'valid_until': '2026-10-01T00:00:00Z',
        'cash_cap_micro': 10_000_000, 'opening_cash_micro': 0, 'reconciliation_sha256': 'a' * 64,
        'accounts': [{'provider': 'gemini', 'account_sha256': 'b' * 64,
            'credential_sha256': hashlib.sha256(('gemini\0' + KEY).encode()).hexdigest(),
            'evidence_sha256': 'c' * 64, 'valid_until': '2026-10-01T00:00:00Z', 'mode': 'cash_only',
            'funding': {'cash_factor_numerator': 1, 'cash_factor_denominator': 1,
                        'cash_bound_verified': True},
            'routes': [{'route': quotes.GEMINI37_AUDIO_ROUTE, 'model': 'gemini-3.7-flash',
                        'price_revision': quotes.GEMINI37_AUDIO_PRICE_REVISION}],
        }],
    })
    client.sadd(runtime._CHANNEL_INDEX, CHANNEL)
    client.set(runtime._CHANNEL_PREFIX + CHANNEL, json.dumps({
        'id': CHANNEL, 'connection_id': 'connection_AAAAA'}))
    for task, parent in ((ROOT, None), (CHILD, ROOT)):
        client.set(runtime._JOB_PREFIX + task, json.dumps({
            'task_id': task, 'parent_id': parent, 'kind': 'render',
            'spec': {'production_channel_id': CHANNEL,
                     'production_connection_id': 'connection_AAAAA', 'duration_minutes': 0.5}}))
    sender = Mock(return_value=_response())
    monkeypatch.setattr(generation.httpx, 'post', sender)
    monkeypatch.setattr(qc, 'settings', SimpleNamespace(gemini_api_key=KEY, gemini_model='gemini-3.7-flash'))
    monkeypatch.setattr(qc, 'generate_gemini_audio_json', generation.generate_gemini_audio_json)
    path = tmp_path / 'original-narration.wav'
    path.write_bytes(_wav())
    token = runtime._TASK_ID.set(ROOT)
    try:
        yield SimpleNamespace(client=client, ledger=ledger, sender=sender, path=path)
    finally:
        runtime._TASK_ID.reset(token)


def _run(funded, raw=None, **options):
    return generation.generate_gemini_audio_json(
        funded.path.read_bytes() if raw is None else raw, 'audio/wav', 'Hello there.',
        **{'api_key': KEY, 'model': 'gemini-3.7-flash', 'json_schema': qc._PROSODY_REVIEW_SCHEMA,
           'thinking_level': 'medium', 'retry_once': False,
           'system_instruction': qc._PROSODY_SYSTEM_INSTRUCTION, **options})


def _receipts(funded):
    return [json.loads(value) for key, value in funded.client.hgetall(LEDGER_KEY).items()
            if key.startswith('request:')]


def test_actual_prosody_qc_preserves_full_audio_and_rubric_with_reserve_before_send(funded):
    raw = funded.path.read_bytes()
    def send(url, **kwargs):
        assert url == quotes.GEMINI37_AUDIO_ROUTE and kwargs['headers'] == HEADERS
        request = kwargs['json']
        assert request['generationConfig']['responseJsonSchema'] == qc._PROSODY_REVIEW_SCHEMA
        assert request['generationConfig']['maxOutputTokens'] == 8192
        assert request['systemInstruction'] == {'parts': [{
            'text': qc._PROSODY_SYSTEM_INSTRUCTION.replace('Turkish', 'English')}]}
        assert request['contents'][0]['parts'][1] == {'inlineData': {
            'mimeType': 'audio/wav', 'data': base64.b64encode(raw).decode('ascii')}}
        assert '<UNTRUSTED_EXPECTED_NARRATION>' in request['contents'][0]['parts'][0]['text']
        assert 'Hello there.' in request['contents'][0]['parts'][0]['text']
        assert funded.ledger.snapshot()['period']['used_micro'] == 817152
        assert funded.ledger.funding_snapshot()['cash_reserved_micro'] == 817152
        return _response()
    funded.sender.side_effect = send
    result = qc.verify_audio_prosody(funded.path, 'Hello there.', audio_duration_seconds=1, language='en')
    assert result['available'] is True and result['pass'] is True and result['review_attempts'] == 1
    funded.sender.assert_called_once()
    assert funded.path.read_bytes() == raw
    receipt = _receipts(funded)[0]
    assert receipt['quote']['price_revision'] == quotes.GEMINI37_AUDIO_PRICE_REVISION
    saved = json.dumps(funded.client.hgetall(LEDGER_KEY))
    assert KEY not in saved and 'Hello there.' not in saved and base64.b64encode(raw).decode('ascii') not in saved


def test_child_and_filename_cannot_refresh_same_audio_request_allowance(funded):
    assert _run(funded) == _review()
    other = funded.path.with_name('copied.wav')
    other.write_bytes(funded.path.read_bytes())
    funded.path = other
    token = runtime._TASK_ID.set(CHILD)
    try:
        with pytest.raises(SpendBlocked, match='already_reserved'):
            _run(funded)
    finally:
        runtime._TASK_ID.reset(token)
    assert funded.sender.call_count == 1 and len(_receipts(funded)) == 1
    assert funded.ledger.funding_snapshot()['cash_reserved_micro'] == 817152


def test_original_bytes_and_full_rubric_are_part_of_replay_identity(funded):
    assert _run(funded) == _review()
    assert _run(funded, raw=_wav(amplitude=99)) == _review()
    assert _run(funded, system_instruction=qc._PROSODY_SYSTEM_INSTRUCTION + ' Independent review.') == _review()
    assert funded.sender.call_count == 3 and len(_receipts(funded)) == 3
    assert funded.ledger.funding_snapshot()['cash_reserved_micro'] == 3 * 817152


@pytest.mark.parametrize('problem', ['timeout', 'invalid_json', 'http_503'])
def test_existing_native_retry_cannot_resend_reserved_request(funded, problem):
    if problem == 'timeout': funded.sender.side_effect = httpx.ReadTimeout('private transport detail')
    elif problem == 'invalid_json': funded.sender.return_value = httpx.Response(200, text='private malformed reply')
    else: funded.sender.return_value = httpx.Response(503, text='private provider detail')
    with pytest.raises(SpendBlocked, match='already_reserved'):
        _run(funded, retry_once=True)
    assert funded.sender.call_count == 1 and len(_receipts(funded)) == 1
    assert funded.ledger.funding_snapshot()['cash_reserved_micro'] == 817152


@pytest.mark.parametrize('failure', ['bad_audio', 'wrong_key', 'zero_budget', 'unfunded_text_revision'])
def test_unfunded_or_unbounded_native_request_has_zero_send_and_reservation(funded, failure):
    if failure == 'bad_audio': options = {'raw': b'not-audio'}
    elif failure == 'wrong_key': options = {'api_key': 'unbound-other-key'}
    elif failure == 'zero_budget':
        funded.ledger.policy = SpendPolicy(*([0] * 6))
        funded.client.hset(LEDGER_KEY, 'policy', json.dumps(asdict(funded.ledger.policy)))
        options = {}
    else:
        # The actual same-model text route does not grant the audio revision.
        request = body()
        request['contents'][0]['parts'].pop()
        with pytest.raises(SpendBlocked):
            runtime.paid_post(funded.sender, quotes.GEMINI37_AUDIO_ROUTE, json=request, headers=HEADERS)
        options = None
    if options is not None:
        with pytest.raises(SpendBlocked):
            _run(funded, **options)
    funded.sender.assert_not_called()
    assert _receipts(funded) == []
    assert funded.ledger.funding_snapshot()['cash_reserved_micro'] == 0


@pytest.mark.parametrize('problem', ['timeout', 'invalid_json', 'http_503'])
def test_actual_prosody_outer_retry_cannot_send_same_reserved_audio(funded, problem):
    if problem == 'timeout': funded.sender.side_effect = httpx.ReadTimeout('private transport detail')
    elif problem == 'invalid_json': funded.sender.return_value = httpx.Response(200, text='private malformed reply')
    else: funded.sender.return_value = httpx.Response(503, text='private provider detail')
    with pytest.raises(SpendBlocked, match='already_reserved'):
        qc.verify_audio_prosody(funded.path, 'Hello there.', audio_duration_seconds=1, language='en')
    assert funded.sender.call_count == 1 and len(_receipts(funded)) == 1
    assert funded.ledger.funding_snapshot()['cash_reserved_micro'] == 817152


def test_semantic_protocol_repair_reserves_each_complete_review_and_keeps_rejection(funded):
    rejected = _review()
    rejected['pass'] = False
    rejected['scores'].update(naturalness=35, pacing=30, sentence_flow=30, emphasis=40, roboticness=75)
    grounded = deepcopy(rejected)
    grounded['issues'] = [{'code': 'unnatural_internal_pause', 'start_seconds': 0.0,
        'end_seconds': 0.4, 'phrase': 'Hello', 'detail': 'An unnatural pause interrupts the phrase.'}]
    funded.sender.side_effect = [_response(rejected), _response(grounded)]
    raw = funded.path.read_bytes()
    result = qc.verify_audio_prosody(funded.path, 'Hello there.', audio_duration_seconds=1, language='en',
        transcript_evidence={'available': True, 'pass': True, 'provider': 'openai',
            'mismatch_details': {'timestamp_sequence_match': True}, 'word_timestamps': [
                {'text': 'Hello', 'start': 0.0, 'end': 0.4},
                {'text': 'there.', 'start': 0.4, 'end': 0.9}]})
    assert result['available'] is True and result['pass'] is False
    assert result['reason'] == 'unnatural_internal_pause' and result['review_attempts'] == 2
    assert funded.sender.call_count == 2 and len(_receipts(funded)) == 2
    first, second = [call.kwargs['json'] for call in funded.sender.call_args_list]
    assert first['contents'][0]['parts'][1] == second['contents'][0]['parts'][1] == {
        'inlineData': {'mimeType': 'audio/wav', 'data': base64.b64encode(raw).decode('ascii')}}
    assert first['systemInstruction'] == second['systemInstruction']
    assert first['generationConfig'] == second['generationConfig']
    assert 'do not change it merely to satisfy the schema' in second['contents'][0]['parts'][0]['text']
    assert first['contents'][0]['parts'][0]['text'] != second['contents'][0]['parts'][0]['text']
    assert funded.ledger.funding_snapshot()['cash_reserved_micro'] == 2 * 817152
    assert funded.path.read_bytes() == raw
