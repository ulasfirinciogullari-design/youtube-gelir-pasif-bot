"""Full generated MP3 decode and actual HTTPX identity, entirely offline."""
import ast
import base64
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from functools import lru_cache
import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import Mock

import httpx
import pytest

from app.services import abacus_router_adapter as text_adapter
from app.services import abacus_router_audio_adapter as adapter
from app.services import audio_qc
from app.services.production_spend import SpendBlocked


KEY = 'private-offline-router-audio-key'
EXPECTED = 'Merhaba dünya.'
ASR = {'text': EXPECTED, 'language': 'tr', 'words': [
    {'word': 'Merhaba', 'start': .05, 'end': .4},
    {'word': 'dünya.', 'start': .4, 'end': .9},
]}
PROSODY = {'pass': False, 'summary': 'A concrete audible defect remains.',
           'scores': {'pronunciation': 40, 'naturalness': 70, 'pacing': 80,
                      'sentence_flow': 80, 'emphasis': 70, 'roboticness': 20},
           'issues': [{'code': 'mispronunciation', 'start_seconds': .4, 'end_seconds': .9,
                       'phrase': 'dünya', 'detail': 'The ending is not intelligible.'}]}


@lru_cache(maxsize=5)
def mp3(duration=1, frequency=440):
    return subprocess.run([
        'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
        f'sine=frequency={frequency}:sample_rate=48000:duration={duration}',
        '-c:a', 'libmp3lame', '-b:a', '128k', '-threads', '1', '-write_xing', '0',
        '-f', 'mp3', 'pipe:1',
    ], check=True, timeout=10, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout


@pytest.fixture(autouse=True)
def no_transport(monkeypatch):
    blocked = Mock(side_effect=AssertionError('The audio adapter cannot send a request.'))
    for name in ('post', 'request', 'stream', 'get', 'Client', 'AsyncClient'):
        monkeypatch.setattr(httpx, name, blocked)
    yield
    blocked.assert_not_called()


def prepare(**overrides):
    return adapter.prepare_blind_asr_request(mp3(), **{'api_key': KEY, **overrides})


def prosody(**overrides):
    return adapter.prepare_audio_prosody_request(mp3(), **{
        'api_key': KEY, 'expected_narration': EXPECTED,
        'system_instruction': audio_qc._PROSODY_SYSTEM_INSTRUCTION,
        'json_schema': deepcopy(audio_qc._PROSODY_REVIEW_SCHEMA), **overrides})


def envelope(result=None, **patch):
    return {'id': 'chatcmpl-private-fixture-123', 'object': 'chat.completion',
            'created': 1783456789, 'model': 'route-llm', 'choices': [{'index': 0,
            'message': {'role': 'assistant', 'content': json.dumps(ASR if result is None else result)},
            'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 200,
            'completion_tokens': 30, 'total_tokens': 230,
            'prompt_tokens_details': {'audio_tokens': 150}}, **patch}


def wire(prepared):
    kwargs = prepared.wire_kwargs()
    kwargs.pop('timeout')
    return httpx.Request('POST', prepared.endpoint, **kwargs)


def response(prepared, *, payload=None, request=None, raw=None, headers=None, status=200):
    return httpx.Response(status, request=wire(prepared) if request is None else request,
                          content=json.dumps(envelope() if payload is None else payload).encode('utf-8')
                          if raw is None else raw,
                          headers={'Content-Type': 'application/json'} if headers is None else headers)


def test_complete_original_mp3_has_measured_hashes_and_no_expected_text():
    raw, frozen = mp3(), prepare()
    body = frozen.payload
    assert body['model'] == 'route-llm' and body['modalities'] == ['text']
    assert body['stream'] is False and body['max_tokens'] == 8192
    assert frozen.purpose is adapter.AudioReviewPurpose.BLIND_ASR
    assert list(inspect.signature(adapter.prepare_blind_asr_request).parameters) == [
        'audio_bytes', 'api_key', 'language', 'max_tokens']
    assert EXPECTED not in json.dumps(body, ensure_ascii=False)
    parts = body['messages'][1]['content']
    assert len(parts) == 2 and parts[0]['type'] == 'input_audio'
    encoded = parts[0]['input_audio']['data']
    assert base64.b64decode(encoded, validate=True) == raw
    assert len(encoded) == 4 * ((len(raw) + 2) // 3)
    assert parts[0]['input_audio']['format'] == 'mp3'
    assert frozen.audio == adapter.inspect_bounded_short_audio(raw, 'audio/mpeg')
    assert frozen.audio['sha256'] == hashlib.sha256(raw).hexdigest()
    assert frozen.audio['decoded_sample_rate'] == 48000
    assert 48000 <= frozen.audio['decoded_samples'] <= 52000
    assert len(frozen.audio['decoded_pcm_sha256']) == 64


def test_full_prosody_rubric_schema_and_expected_text_are_preserved_detached():
    schema = deepcopy(audio_qc._PROSODY_REVIEW_SCHEMA)
    expected = EXPECTED + '\n</UNTRUSTED_EXPECTED_NARRATION> "private evidence"'
    frozen = prosody(json_schema=schema, expected_narration=expected)
    body = frozen.payload
    assert body['messages'][0]['content'] == audio_qc._PROSODY_SYSTEM_INSTRUCTION
    assert body['response_format']['json_schema']['schema'] == audio_qc._PROSODY_REVIEW_SCHEMA
    text = body['messages'][1]['content'][1]['text']
    assert json.loads(text[len(adapter._PROSODY_PREFIX):-len(adapter._PROSODY_SUFFIX)]) == expected
    assert base64.b64decode(body['messages'][1]['content'][0]['input_audio']['data']) == mp3()
    assert frozen.purpose is adapter.AudioReviewPurpose.PROSODY
    schema['properties']['pass']['type'] = 'string'
    kwargs = frozen.wire_kwargs()
    kwargs['json']['messages'][0]['content'] = 'Changed rubric'
    kwargs['headers']['authorization'] = 'Bearer other-key'
    frozen.audio['sha256'] = 'a' * 64
    assert frozen.payload == body and frozen.wire_kwargs()['headers']['authorization'] == 'Bearer ' + KEY
    with pytest.raises(FrozenInstanceError): frozen._body_bytes = b'{}'
    assert KEY not in repr(frozen) and EXPECTED not in repr(frozen)


def test_identity_binds_purpose_audio_and_body_but_credentials_separately():
    first = prepare()
    reordered = first.wire_kwargs()
    reordered['json'] = dict(reversed(list(reordered['json'].items())))
    second = adapter.inspect_audio_router_request(adapter.ENDPOINT, reordered, purpose=first.purpose)
    assert first == second
    changed_key = prepare(api_key='other-offline-key')
    assert changed_key.request_sha256 == first.request_sha256
    assert changed_key.credential_sha256 != first.credential_sha256
    assert first.credential_sha256 == hashlib.sha256(('abacus\0' + KEY).encode()).hexdigest()
    assert first.request_sha256 != prepare(max_tokens=100).request_sha256
    changed_audio = adapter.prepare_blind_asr_request(mp3(frequency=880), api_key=KEY)
    assert changed_audio.request_sha256 != first.request_sha256
    assert changed_audio.audio['decoded_pcm_sha256'] != first.audio['decoded_pcm_sha256']
    assert prosody().request_sha256 != first.request_sha256
    assert first.endpoint == adapter.ENDPOINT and first.operation == '/v1/chat/completions'


@pytest.mark.parametrize('language', [None, '', 'en', 'TR', True, ['tr']])
def test_only_exact_reviewed_turkish_locale(language):
    with pytest.raises(adapter.AbacusRouterAudioError): prepare(language=language)


@pytest.mark.parametrize('value', [0, -1, 8193, True, 1.0, float('nan'), float('inf'), '8192', None])
def test_output_ceiling_is_strict_integer(value):
    with pytest.raises(adapter.AbacusRouterAudioError): prepare(max_tokens=value)


@pytest.mark.parametrize('value', ['', 'with space', 'line\nbreak', 'ü', 'x' * 4097, None, b'key'])
def test_keys_are_bounded_ascii_and_never_leaked(value):
    with pytest.raises(adapter.AbacusRouterAudioError, match='^abacus_router_audio_request_invalid$'):
        prepare(api_key=value)


@pytest.mark.parametrize('field,value', [
    ('model', 'gpt-4o-audio-preview'), ('model', None), ('stream', True), ('stream', 0),
    ('modalities', ['text', 'audio']), ('audio', {'voice': 'alloy'}), ('tools', []),
    ('tool_choice', 'none'), ('abacus_tools', ['search']), ('n', 2), ('temperature', 0),
    ('response_format', {'type': 'json_object'}), ('max_tokens', 8192.0),
])
def test_unreviewed_route_audio_output_or_options_rejected(field, value):
    frozen = prepare()
    kwargs = frozen.wire_kwargs()
    kwargs['json'][field] = value
    with pytest.raises(adapter.AbacusRouterAudioError):
        adapter.inspect_audio_router_request(adapter.ENDPOINT, kwargs, purpose=frozen.purpose)


@pytest.mark.parametrize('damage', ['extra_message', 'expected_text', 'changed_system', 'changed_schema',
    'extra_part', 'duplicate_audio', 'text_first', 'url', 'wav', 'invalid_base64', 'noncanonical_base64',
    'empty_audio', 'data_url', 'wrong_purpose', 'purpose_string', 'extra_audio_field'])
def test_blind_audio_boundary_rejects_contamination_and_alternative_media(damage):
    frozen = prepare()
    kwargs, purpose = frozen.wire_kwargs(), frozen.purpose
    body = kwargs['json']
    parts = body['messages'][1]['content']
    if damage == 'extra_message': body['messages'].append({'role': 'user', 'content': EXPECTED})
    elif damage == 'expected_text': parts[1]['text'] += EXPECTED
    elif damage == 'changed_system': body['messages'][0]['content'] += EXPECTED
    elif damage == 'changed_schema': body['response_format']['json_schema']['schema']['properties']['text']['description'] = EXPECTED
    elif damage == 'extra_part': parts.append({'type': 'text', 'text': EXPECTED})
    elif damage == 'duplicate_audio': parts[1] = deepcopy(parts[0])
    elif damage == 'text_first': parts.reverse()
    elif damage == 'url': parts[0] = {'type': 'audio_url', 'audio_url': 'https://private.invalid/audio.mp3'}
    elif damage == 'wav': parts[0]['input_audio']['format'] = 'wav'
    elif damage == 'invalid_base64': parts[0]['input_audio']['data'] = '?not-base64'
    elif damage == 'noncanonical_base64': parts[0]['input_audio']['data'] += '\n'
    elif damage == 'empty_audio': parts[0]['input_audio']['data'] = ''
    elif damage == 'data_url': parts[0]['input_audio']['data'] = 'data:audio/mpeg;base64,' + parts[0]['input_audio']['data']
    elif damage == 'wrong_purpose': purpose = adapter.AudioReviewPurpose.PROSODY
    elif damage == 'purpose_string': purpose = purpose.value
    else: parts[0]['input_audio']['url'] = 'https://private.invalid/audio.mp3'
    with pytest.raises(adapter.AbacusRouterAudioError):
        adapter.inspect_audio_router_request(adapter.ENDPOINT, kwargs, purpose=purpose)


@pytest.mark.parametrize('url', [
    'http://routellm.abacus.ai/v1/chat/completions', 'https://routellm.abacus.ai/v1/messages',
    adapter.ENDPOINT + '/', adapter.ENDPOINT + '?model=haiku', adapter.ENDPOINT + '#fragment',
    'https://user:key@routellm.abacus.ai/v1/chat/completions',
    'https://routellm.abacus.ai.attacker.invalid/v1/chat/completions',
])
def test_exact_origin_and_path(url):
    frozen = prepare()
    with pytest.raises(adapter.AbacusRouterAudioError):
        adapter.inspect_audio_router_request(url, frozen.wire_kwargs(), purpose=frozen.purpose)


@pytest.mark.parametrize('damage', ['cookie', 'extra_header', 'duplicate_auth', 'content_type', 'accept',
    'redirect', 'files', 'params', 'timeout_none', 'timeout_infinite'])
def test_exact_request_headers_and_kwargs(damage):
    frozen = prepare()
    kwargs = frozen.wire_kwargs()
    if damage == 'cookie': kwargs['headers']['Cookie'] = 'private=token'
    elif damage == 'extra_header': kwargs['headers']['X-Proxy-Key'] = KEY
    elif damage == 'duplicate_auth': kwargs['headers']['Authorization'] = 'Bearer ' + KEY
    elif damage == 'content_type': kwargs['headers']['content-type'] = 'audio/mpeg'
    elif damage == 'accept': kwargs['headers']['accept'] = 'audio/mpeg'
    elif damage == 'redirect': kwargs['follow_redirects'] = True
    elif damage == 'files': kwargs['files'] = {'audio': b'private'}
    elif damage == 'params': kwargs['params'] = {'model': 'haiku'}
    elif damage == 'timeout_none': kwargs['timeout'] = None
    else: kwargs['timeout'] = float('inf')
    with pytest.raises(adapter.AbacusRouterAudioError):
        adapter.inspect_audio_router_request(adapter.ENDPOINT, kwargs, purpose=frozen.purpose)


@pytest.mark.parametrize('damage', ['empty', 'bytearray', 'memoryview', 'text', 'over_bytes', 'random',
                                     'truncated', 'trailing_payload', 'over_duration'])
def test_entire_original_audio_must_fully_decode_without_truncation(damage):
    raw = mp3()
    if damage == 'empty': raw = b''
    elif damage == 'bytearray': raw = bytearray(raw)
    elif damage == 'memoryview': raw = memoryview(raw)
    elif damage == 'text': raw = 'private audio'
    elif damage == 'over_bytes': raw = b'x' * (adapter.MAX_AUDIO_BYTES + 1)
    elif damage == 'random': raw = b'not audio'
    elif damage == 'truncated': raw = raw[:-1]
    elif damage == 'trailing_payload': raw += b'hidden second file'
    else: raw = mp3(duration=30.2)
    with pytest.raises(adapter.AbacusRouterAudioError):
        adapter.prepare_blind_asr_request(raw, api_key=KEY)


def test_complete_thirty_second_mp3_with_real_encoder_padding_is_preserved():
    raw = mp3(duration=30)
    frozen = adapter.prepare_blind_asr_request(raw, api_key=KEY)
    assert 30 <= frozen.audio['decoded_samples'] / 48000 <= 30.08
    assert base64.b64decode(frozen.payload['messages'][1]['content'][0]['input_audio']['data']) == raw
    assert frozen.audio['sha256'] == hashlib.sha256(raw).hexdigest()


def test_read_original_file_is_bounded_regular_snapshot_and_rejects_link(tmp_path):
    path = tmp_path / 'narration.mp3'
    path.write_bytes(mp3())
    assert adapter.read_original_mp3(path) == mp3()
    linked = tmp_path / 'linked.mp3'
    linked.symlink_to(path)
    wrong = tmp_path / 'wrong.wav'
    wrong.write_bytes(mp3())
    pipe = tmp_path / 'pipe.mp3'
    os.mkfifo(pipe)
    directory = tmp_path / 'directory.mp3'
    directory.mkdir()
    for bad in (linked, wrong, pipe, directory, tmp_path / 'missing.mp3'):
        with pytest.raises(adapter.AbacusRouterAudioError): adapter.read_original_mp3(bad)


@pytest.mark.parametrize('damage', ['rubric_large', 'expected_large', 'schema_large', 'combined_large',
    'schema_tuple', 'schema_extra', 'schema_nonfinite', 'schema_bad_key', 'schema_cycle', 'schema_depth'])
def test_bounded_metadata_rejects_oversize_and_coerced_structure(damage):
    values = {}
    if damage == 'rubric_large': values['system_instruction'] = 'ü' * 50001
    elif damage == 'expected_large': values['expected_narration'] = 'ü' * 50001
    elif damage == 'schema_large': values['json_schema'] = {**audio_qc._PROSODY_REVIEW_SCHEMA, 'description': 'x' * 100001}
    elif damage == 'combined_large': values.update(system_instruction='x' * 55000, expected_narration='x' * 55000)
    else:
        schema = deepcopy(audio_qc._PROSODY_REVIEW_SCHEMA)
        if damage == 'schema_tuple': schema['required'] = tuple(schema['required'])
        elif damage == 'schema_extra': schema['anyOf'] = []
        elif damage == 'schema_nonfinite': schema['properties']['scores']['properties']['pacing']['maximum'] = float('inf')
        elif damage == 'schema_bad_key': schema[1] = 'value'
        elif damage == 'schema_cycle': schema['properties']['cycle'] = schema
        else:
            nested = {'type': 'string'}
            for _ in range(35): nested = {'type': 'array', 'items': nested}
            schema['properties']['nested'] = nested
        values['json_schema'] = schema
    with pytest.raises(adapter.AbacusRouterAudioError): prosody(**values)


def test_observer_uses_actual_request_response_and_preserves_honest_audio_usage():
    frozen = prepare()
    actual = response(frozen, payload=envelope(model='upstream-alias/not-verified'))
    observed = adapter.observe_audio_router_response(frozen, actual)
    assert observed.result == ASR
    evidence = observed.evidence
    assert evidence['audio'] == frozen.audio and evidence['purpose'] == frozen.purpose.value
    assert evidence['wire_body_sha256'] == hashlib.sha256(actual.request.content).hexdigest()
    assert evidence['response_body_sha256'] == hashlib.sha256(actual.content).hexdigest()
    assert evidence['request_sha256'] == frozen.request_sha256
    assert evidence['credential_sha256'] == frozen.credential_sha256
    assert observed.returned_model == 'upstream-alias/not-verified'
    assert observed.underlying_model_verified is False
    assert observed.usage['prompt_tokens_details']['audio_tokens'] == 150
    assert not {'cash', 'credits', 'actual_micro', 'qa_approved', 'pass'} & set(evidence)
    assert KEY not in json.dumps(evidence) and EXPECTED not in json.dumps(evidence, ensure_ascii=False)
    saved_proof = evidence.pop('response_proof_sha256')
    assert saved_proof == hashlib.sha256(adapter._canonical(evidence)).hexdigest()
    observed.result['text'] = 'changed'
    observed.evidence['audio']['sha256'] = 'a' * 64
    assert observed.result == ASR and observed.evidence['audio'] == frozen.audio
    assert KEY not in repr(observed) and EXPECTED not in repr(observed)


def test_original_full_prosody_negative_is_preserved_without_qa_approval():
    frozen = prosody()
    payload = deepcopy(PROSODY)
    payload['issues'][0]['code'] = audio_qc._PROSODY_REASON_CODES[0]
    observed = adapter.observe_audio_router_response(frozen, response(frozen, payload=envelope(payload)))
    assert observed.result == payload
    assert observed.result['pass'] is False
    assert observed.evidence['purpose'] == 'retained_audio_prosody'
    assert 'qa_approved' not in observed.evidence


def test_missing_usage_remains_unknown_and_does_not_mean_free():
    frozen = prepare()
    payload = envelope()
    del payload['usage']
    assert adapter.observe_audio_router_response(frozen, response(frozen, payload=payload)).usage is None


@pytest.mark.parametrize('damage', ['duplicate_auth', 'duplicate_json', 'audio_change', 'numeric_change',
    'key_change', 'query', 'origin', 'method', 'path', 'fragment', 'utf16', 'unread', 'cookie', 'length'])
def test_actual_wire_must_match_the_frozen_request(damage):
    frozen = prepare()
    kwargs, url, method = frozen.wire_kwargs(), adapter.ENDPOINT, 'POST'
    kwargs.pop('timeout')
    if damage == 'duplicate_auth': kwargs['headers'] = list(kwargs['headers'].items()) + [('Authorization', 'Bearer ' + KEY)]
    elif damage == 'audio_change': kwargs['json']['messages'][1]['content'][0]['input_audio']['data'] = base64.b64encode(mp3(frequency=880)).decode()
    elif damage == 'numeric_change': kwargs['json']['max_tokens'] = 8192.0
    elif damage == 'key_change': kwargs['headers']['authorization'] = 'Bearer other-key'
    elif damage == 'query': url += '?model=haiku'
    elif damage == 'origin': url = url.replace('routellm.abacus.ai', 'private.invalid')
    elif damage == 'method': method = 'GET'
    elif damage == 'path': url = url.replace('chat/completions', 'messages')
    elif damage == 'fragment': url += '#hidden'
    elif damage == 'utf16': kwargs['content'] = json.dumps(kwargs.pop('json')).encode('utf-16')
    elif damage == 'unread': kwargs.pop('json'); kwargs['stream'] = iter([b'private-body'])
    elif damage == 'cookie': kwargs['headers']['Cookie'] = 'private=token'
    elif damage == 'length': kwargs['headers']['Content-Length'] = '1'
    else:
        raw = json.dumps(kwargs.pop('json')).encode()
        kwargs['content'] = b'{"model":"haiku",' + raw[1:]
    actual = httpx.Request(method, url, **kwargs)
    with pytest.raises(adapter.AbacusRouterAudioError):
        adapter.observe_audio_router_response(frozen, response(frozen, request=actual))


@pytest.mark.parametrize('status', [199, 301, 400, 401, 429, 500])
def test_non_success_is_terminal(status):
    frozen = prepare()
    with pytest.raises(adapter.AbacusRouterAudioError):
        adapter.observe_audio_router_response(frozen, response(frozen, status=status))


def test_redirect_unread_and_oversized_actual_responses_are_terminal():
    frozen = prepare()
    redirected = response(frozen)
    redirected.history = [httpx.Response(302, request=wire(frozen))]
    unread = httpx.Response(200, request=wire(frozen), stream=httpx.ByteStream(b'private'))
    huge = response(frozen, raw=b'x' * (adapter.MAX_RESPONSE_BYTES + 1))
    for actual in (redirected, unread, huge, {'content': ASR}):
        with pytest.raises(adapter.AbacusRouterAudioError):
            adapter.observe_audio_router_response(frozen, actual)


@pytest.mark.parametrize('header', ['content-type', 'content-length', 'request-id', 'x-request-id', 'content-encoding'])
def test_ambiguous_relevant_response_headers_are_terminal(header):
    frozen = prepare()
    value = {'content-type': 'application/json', 'content-length': '1', 'content-encoding': 'identity'}.get(header, 'req1')
    headers = [('Content-Type', 'application/json')] if header != 'content-type' else []
    headers += [(header, value), (header.upper(), value)]
    with pytest.raises(adapter.AbacusRouterAudioError):
        adapter.observe_audio_router_response(frozen, response(frozen, headers=headers))


def test_non_identity_encoding_wrong_length_and_ambiguous_transfer_rejected():
    frozen = prepare()
    for headers in ({'Content-Type': 'text/plain'},
                    {'Content-Type': 'application/json', 'Content-Length': '1'},
                    {'Content-Type': 'application/json', 'Content-Encoding': 'br'},
                    {'Content-Type': 'application/json', 'Content-Length': '1', 'Transfer-Encoding': 'chunked'}):
        actual = response(frozen)
        actual.headers.clear()
        actual.headers.update(headers)
        with pytest.raises(adapter.AbacusRouterAudioError):
            adapter.observe_audio_router_response(frozen, actual)
    actual = response(frozen, headers=[('Content-Type', 'application/json; charset=utf-8'),
                                      ('Set-Cookie', 'one=fixture'), ('Set-Cookie', 'two=fixture')])
    assert adapter.observe_audio_router_response(frozen, actual).result == ASR


@pytest.mark.parametrize('damage', ['choices_zero', 'choices_two', 'length', 'filtered', 'tools', 'role',
    'index_bool', 'index_float', 'list_content', 'refusal', 'tool_calls', 'audio', 'model_none',
    'model_blank', 'id_too_long', 'created_bool', 'created_inf', 'object_stream', 'unknown_field'])
def test_exactly_one_completed_text_json_response_without_generated_audio(damage):
    frozen, value = prepare(), envelope()
    choice, message = value['choices'][0], value['choices'][0]['message']
    if damage == 'choices_zero': value['choices'] = []
    elif damage == 'choices_two': value['choices'] *= 2
    elif damage == 'length': choice['finish_reason'] = 'length'
    elif damage == 'filtered': choice['finish_reason'] = 'content_filter'
    elif damage == 'tools': choice['finish_reason'] = 'tool_calls'
    elif damage == 'role': message['role'] = 'system'
    elif damage == 'index_bool': choice['index'] = False
    elif damage == 'index_float': choice['index'] = 0.0
    elif damage == 'list_content': message['content'] = [{'type': 'text', 'text': json.dumps(ASR)}]
    elif damage == 'refusal': message['refusal'] = 'private rejected response'
    elif damage == 'tool_calls': message['tool_calls'] = [{'function': {'name': 'search'}}]
    elif damage == 'audio': message['audio'] = {'data': 'private'}
    elif damage == 'model_none': value['model'] = None
    elif damage == 'model_blank': value['model'] = ''
    elif damage == 'id_too_long': value['id'] = 'x' * 161
    elif damage == 'created_bool': value['created'] = True
    elif damage == 'created_inf': value['created'] = float('inf')
    elif damage == 'object_stream': value['object'] = 'chat.completion.chunk'
    else: value['unreviewed_execution'] = ['search']
    with pytest.raises(adapter.AbacusRouterAudioError):
        adapter.observe_audio_router_response(frozen, response(frozen, payload=value))


@pytest.mark.parametrize('content', ['', 'null', '[{}]', '{} {}', '```json\n{}\n```',
    '{"text":"a","text":"b"}', '{"text":NaN}', '{"text":Infinity}', '{"text":1e999}',
    json.dumps({**ASR, 'language': 'en'}), json.dumps({**ASR, 'text': ''}),
    json.dumps({**ASR, 'words': []}), json.dumps({**ASR, 'words': ASR['words'] * 129}),
    json.dumps({**ASR, 'extra': True}),
])
def test_strict_json_and_complete_transcription_schema(content):
    frozen, value = prepare(), envelope()
    value['choices'][0]['message']['content'] = content
    with pytest.raises(adapter.AbacusRouterAudioError):
        adapter.observe_audio_router_response(frozen, response(frozen, payload=value))


def test_response_duplicate_keys_and_non_utf8_are_rejected_before_observation():
    frozen = prepare()
    raw = json.dumps(envelope()).encode()
    for changed in (b'{"model":"haiku",' + raw[1:], raw.decode().encode('utf-16')):
        with pytest.raises(adapter.AbacusRouterAudioError):
            adapter.observe_audio_router_response(frozen, response(frozen, raw=changed))


@pytest.mark.parametrize('damage', ['boolean_score', 'score_bounds', 'missing_dimension',
                                     'unknown_issue', 'extra_field'])
def test_full_original_prosody_schema_remains_strict(damage):
    frozen, result = prosody(), deepcopy(PROSODY)
    result['issues'][0]['code'] = audio_qc._PROSODY_REASON_CODES[0]
    if damage == 'boolean_score': result['scores']['pronunciation'] = True
    elif damage == 'score_bounds': result['scores']['roboticness'] = 101
    elif damage == 'missing_dimension': del result['scores']['naturalness']
    elif damage == 'unknown_issue': result['issues'][0]['code'] = 'unreviewed_reason'
    else: result['extra'] = True
    with pytest.raises(adapter.AbacusRouterAudioError, match='schema_mismatch'):
        adapter.observe_audio_router_response(frozen, response(frozen, payload=envelope(result)))


@pytest.mark.parametrize('damage', ['blank_text', 'blank_word', 'phrase_interval', 'joined_phrase',
    'wrong_sequence', 'omitted_word', 'overlap', 'zero_interval', 'negative', 'past_audio_end',
    'bool_time', 'nan_time', 'extra_field', 'word_too_long'])
def test_real_ordered_word_intervals_must_agree_with_transcript_and_full_audio(damage):
    frozen, result = prepare(), deepcopy(ASR)
    first, last = result['words']
    if damage == 'blank_text': result['text'] = '   '
    elif damage == 'blank_word': first['word'] = ' '
    elif damage == 'phrase_interval': result['words'] = [{'word': EXPECTED, 'start': .05, 'end': .9}]
    elif damage == 'joined_phrase': result['words'] = [{'word': 'Merhaba,dünya.', 'start': .05, 'end': .9}]
    elif damage == 'wrong_sequence': first['word'] = 'Başka'
    elif damage == 'omitted_word': result['words'] = [first]
    elif damage == 'overlap': last['start'] = .3
    elif damage == 'zero_interval': first['end'] = first['start']
    elif damage == 'negative': first['start'] = -.1
    elif damage == 'past_audio_end': last['end'] = 2
    elif damage == 'bool_time': first['start'] = True
    elif damage == 'nan_time': first['end'] = float('nan')
    elif damage == 'extra_field': first['confidence'] = .9
    else: first['word'] = 'x' * 129
    with pytest.raises(adapter.AbacusRouterAudioError):
        adapter.observe_audio_router_response(frozen, response(frozen, payload=envelope(result)))


def test_bound_decoded_audio_end_and_touching_word_intervals_are_preserved():
    frozen, result = prepare(), deepcopy(ASR)
    result['words'][-1]['end'] = frozen.audio['decoded_samples'] / 48000
    observed = adapter.observe_audio_router_response(frozen, response(frozen, payload=envelope(result)))
    assert observed.result == result
    assert 'qa_approved' not in observed.evidence


@pytest.mark.parametrize('usage', [None, {}, {'prompt_tokens': 1},
    {'prompt_tokens': True, 'completion_tokens': 0, 'total_tokens': 1},
    {'prompt_tokens': 1.0, 'completion_tokens': 0, 'total_tokens': 1},
    {'prompt_tokens': -1, 'completion_tokens': 1, 'total_tokens': 0},
    {'prompt_tokens': 1, 'completion_tokens': 8193, 'total_tokens': 8194},
    {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 1},
    {'prompt_tokens': 1, 'completion_tokens': 0, 'total_tokens': 1, 'credits': 0},
    {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2, 'prompt_tokens_details': {'audio_tokens': 2}},
    {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2, 'completion_tokens_details': {'audio_tokens': 1}},
    {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2, 'completion_tokens_details': {'reasoning_tokens': True}},
    {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2, 'completion_tokens_details': {'accepted_prediction_tokens': 1}},
])
def test_usage_cannot_claim_audio_output_or_invent_cash_or_credits(usage):
    frozen = prepare()
    with pytest.raises(adapter.AbacusRouterAudioError):
        adapter.observe_audio_router_response(frozen, response(frozen, payload=envelope(usage=usage)))


def test_forged_audio_descriptor_body_or_purpose_is_not_observer_authority():
    frozen = prepare()
    for forged in (replace(frozen, _body_bytes=b'{"private":"' + KEY.encode() + b'"}'),
                   replace(frozen, _audio_bytes=adapter._canonical({**frozen.audio, 'sha256': 'a' * 64})),
                   replace(frozen, purpose=adapter.AudioReviewPurpose.PROSODY)):
        with pytest.raises(adapter.AbacusRouterAudioError) as caught:
            adapter.observe_audio_router_response(forged, response(frozen))
        assert KEY not in str(caught.value) and EXPECTED not in str(caught.value)
    bad = envelope()
    bad['choices'][0]['message']['content'] = KEY + EXPECTED
    with pytest.raises(adapter.AbacusRouterAudioError) as caught:
        adapter.observe_audio_router_response(frozen, response(frozen, payload=bad))
    assert KEY not in repr(caught.value) and EXPECTED not in str(caught.value)


def test_existing_text_image_adapter_still_rejects_audio_and_audio_snapshot():
    frozen = prepare()
    with pytest.raises(SpendBlocked):
        text_adapter.inspect_router_request(adapter.ENDPOINT, frozen.wire_kwargs())
    with pytest.raises(SpendBlocked):
        text_adapter.prepare_router_request(frozen.payload['messages'][1]['content'], api_key=KEY,
            system_instruction='Existing full rubric', json_schema=deepcopy(audio_qc._PROSODY_REVIEW_SCHEMA))
    with pytest.raises(SpendBlocked):
        text_adapter.observe_router_response(frozen, response(frozen))


def test_no_sender_journal_funding_initialization_or_audio_output_calls():
    tree = ast.parse(Path(adapter.__file__).read_text())
    forbidden = {'post', 'send', 'request', 'stream', 'paid_post', 'reserve', 'settle', 'initialize',
                 'commission', 'quote_http_request', 'SpendQuote', 'Client', 'AsyncClient',
                 'generate_abacus_json', 'transcribe_whisper_bounded', 'verify_audio_prosody'}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ''
            assert name not in forbidden
