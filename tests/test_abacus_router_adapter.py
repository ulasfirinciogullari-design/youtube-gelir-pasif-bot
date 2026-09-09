"""Actual HTTPX wire/response and JPEG checks; no provider or ledger calls."""
import ast
import base64
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import subprocess
from unittest.mock import Mock

import httpx
import pytest

from app.services import abacus_router_adapter as adapter
from app.services.production_spend import SpendBlocked


KEY = 'private-router-fixture-key'
PROMPT = 'Özgün senaryo, tüm kritik ölçütleri ve özel kanıtlar.'
SCHEMA = {
    'type': 'object', 'properties': {
        'accepted': {'type': 'boolean'},
        'score': {'type': 'number', 'minimum': 0, 'maximum': 1},
        'moments': {'type': 'array', 'minItems': 1, 'maxItems': 3, 'uniqueItems': True,
                    'items': {'type': 'integer', 'minimum': 0, 'maximum': 59}},
        'reason': {'type': 'string', 'minLength': 1, 'maxLength': 100},
    },
    'required': ['accepted', 'score', 'moments', 'reason'], 'additionalProperties': False,
}
RESULT = {'accepted': False, 'score': .2, 'moments': [0, 1], 'reason': 'The image does not support this claim.'}


@lru_cache(maxsize=5)
def jpeg(width=64, height=96, color='blue'):
    return subprocess.run([
        'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', f'color=c={color}:s={width}x{height}:d=0.04',
        '-frames:v', '1', '-threads', '1', '-c:v', 'mjpeg', '-f', 'image2pipe', 'pipe:1',
    ], check=True, timeout=10, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout


def image(raw=None):
    return {'type': 'image_url', 'image_url': {
        'url': 'data:image/jpeg;base64,' + base64.b64encode(jpeg() if raw is None else raw).decode('ascii')}}


def parts():
    return [{'type': 'text', 'text': PROMPT}, image(),
            {'type': 'text', 'text': 'Scene 2, exact final frame.'}, image(jpeg(color='red'))]


def prepare(content=None, **overrides):
    return adapter.prepare_router_request(
        [{'type': 'text', 'text': PROMPT}] if content is None else content,
        **{'api_key': KEY, 'system_instruction': 'Full unchanged rubric. Return the whole JSON schema.',
           'json_schema': deepcopy(SCHEMA), **overrides})


def envelope(**patch):
    return {
        'id': 'chatcmpl-fixture-123', 'object': 'chat.completion', 'created': 1_783_456_789,
        'model': 'route-llm', 'choices': [{'index': 0, 'message': {
            'role': 'assistant', 'content': json.dumps(RESULT)}, 'finish_reason': 'stop'}],
        'usage': {'prompt_tokens': 200, 'completion_tokens': 30, 'total_tokens': 230}, **patch,
    }


def wire(prepared):
    kwargs = prepared.wire_kwargs()
    kwargs.pop('timeout')
    return httpx.Request('POST', prepared.endpoint, **kwargs)


def response(prepared, *, payload=None, request=None, raw=None, headers=None, status=200):
    return httpx.Response(status, request=wire(prepared) if request is None else request,
                          content=json.dumps(envelope() if payload is None else payload).encode('utf-8')
                          if raw is None else raw,
                          headers={'Content-Type': 'application/json'} if headers is None else headers)


def test_complete_original_evidence_and_schema_are_detached_without_transformation():
    inputs, schema = parts(), deepcopy(SCHEMA)
    frozen = prepare(inputs, json_schema=schema)
    original = deepcopy(frozen.payload)
    assert original['messages'][1]['content'] == inputs
    assert original['messages'][0]['content'] == 'Full unchanged rubric. Return the whole JSON schema.'
    assert original['response_format']['json_schema'] == {
        'name': 'youtube_review', 'strict': True, 'schema': SCHEMA}
    assert original['model'] == 'route-llm' and original['modalities'] == ['text']
    assert original['stream'] is False and original['max_tokens'] == 8192
    inputs[0]['text'] = 'mutated private prompt'
    inputs[1]['image_url']['url'] = 'https://bad.invalid/key'
    schema['properties']['accepted']['type'] = 'string'
    detached = frozen.wire_kwargs()
    detached['json']['messages'][0]['content'] = 'changed rubric'
    detached['headers']['authorization'] = 'Bearer changed-account'
    assert frozen.payload == original and frozen.wire_kwargs()['headers']['authorization'] == 'Bearer ' + KEY
    with pytest.raises(FrozenInstanceError):
        frozen._body_bytes = b'{}'
    assert KEY not in repr(frozen) and PROMPT not in repr(frozen)


def test_deterministic_native_identity_key_is_a_separate_binding():
    first = prepare()
    reordered = first.wire_kwargs()
    reordered['json'] = dict(reversed(list(reordered['json'].items())))
    second = adapter.inspect_router_request(adapter.ENDPOINT, reordered)
    assert first == second and first.request_sha256 == second.request_sha256
    other_key = prepare(api_key='different-fixture-key')
    assert first.request_sha256 == other_key.request_sha256
    assert first.credential_sha256 != other_key.credential_sha256
    assert first.credential_sha256 == hashlib.sha256(('abacus\0' + KEY).encode()).hexdigest()
    assert first.request_sha256 != prepare(max_tokens=100).request_sha256
    assert first.request_sha256 != prepare([{'type': 'text', 'text': PROMPT + '!'}]).request_sha256
    assert first.operation == '/v1/chat/completions'


def test_all_sixty_images_and_interleaved_labels_survive_and_duplicate_decode_is_local(monkeypatch):
    decoder = Mock(wraps=adapter._decode_jpeg)
    monkeypatch.setattr(adapter, '_decode_jpeg', decoder)
    content = [{'type': 'text', 'text': 'Original complete story'}]
    for index in range(60):
        content += [{'type': 'text', 'text': f'Exact scene/frame {index}'}, image()]
    frozen = prepare(content)
    assert frozen.payload['messages'][1]['content'] == content
    decoder.assert_called_once_with(jpeg())
    prepare(content)
    assert decoder.call_count == 2


@pytest.mark.parametrize('value', [0, -1, 8193, True, False, 1.0, float('nan'), float('inf'), '8192', None])
def test_output_bound_has_no_numeric_coercion(value):
    with pytest.raises(adapter.AbacusRouterError, match='^abacus_router_request_invalid$'):
        prepare(max_tokens=value)


@pytest.mark.parametrize('field,value', [
    ('model', 'claude-haiku-4-5-20251001'), ('stream', True), ('stream', 0), ('modalities', ['image']),
    ('tools', []), ('tool_choice', 'none'), ('abacus_tools', ['search']), ('audio', {}),
    ('search', False), ('store', False), ('n', 2), ('temperature', 0), ('thinking', {'type': 'disabled'}),
])
def test_request_rejects_every_unreviewed_route_or_execution_option(field, value):
    kwargs = prepare().wire_kwargs()
    kwargs['json'][field] = value
    with pytest.raises(adapter.AbacusRouterError):
        adapter.inspect_router_request(adapter.ENDPOINT, kwargs)


@pytest.mark.parametrize('url', [
    'http://routellm.abacus.ai/v1/chat/completions', 'https://routellm.abacus.ai/v1/messages',
    adapter.ENDPOINT + '/', adapter.ENDPOINT + '?model=haiku', adapter.ENDPOINT + '#fragment',
    'https://private:key@routellm.abacus.ai/v1/chat/completions',
    'https://routellm.abacus.ai.attacker.invalid/v1/chat/completions',
])
def test_exact_self_serve_endpoint_only(url):
    with pytest.raises(adapter.AbacusRouterError) as caught:
        adapter.inspect_router_request(url, prepare().wire_kwargs())
    assert 'private' not in str(caught.value) and 'attacker' not in str(caught.value)


@pytest.mark.parametrize('mutation', [
    lambda k: k.update(params={}), lambda k: k.update(auth=('secret', 'secret')),
    lambda k: k.update(follow_redirects=True), lambda k: k.update(timeout=None),
    lambda k: k['headers'].update(Authorization='Bearer other-key'),
    lambda k: k['headers'].update({'x-api-key': KEY}),
    lambda k: k['headers'].update({'OpenAI-Project': 'unbound-project'}),
    lambda k: k['headers'].update(authorization='Bearer '),
    lambda k: k['headers'].update(authorization='Bearer ' + KEY + '\n'),
    lambda k: k['headers'].update(authorization='Basic ' + KEY),
    lambda k: k['headers'].update(accept='text/event-stream'),
])
def test_secret_and_transport_override_inputs_are_rejected_without_echo(mutation):
    kwargs = prepare().wire_kwargs()
    mutation(kwargs)
    with pytest.raises(SpendBlocked) as caught:
        adapter.inspect_router_request(adapter.ENDPOINT, kwargs)
    assert type(caught.value) is adapter.AbacusRouterError
    assert KEY not in str(caught.value) and KEY not in repr(caught.value)


@pytest.mark.parametrize('damage', [
    'external', 'png_mime', 'invalid_base64', 'whitespace', 'padding', 'empty', 'truncated',
    'concatenated', 'header_only', 'png_bytes', 'wide', 'tall', 'too_large',
])
def test_image_requires_original_canonical_single_complete_bounded_jpeg(damage):
    raw, url = jpeg(), None
    if damage == 'external': url = 'https://attacker.invalid/private.jpg'
    elif damage == 'png_mime': url = image()['image_url']['url'].replace('image/jpeg', 'image/png')
    elif damage == 'invalid_base64': url = 'data:image/jpeg;base64,@@PRIVATE@@'
    elif damage == 'whitespace': url = image()['image_url']['url'] + '\n'
    elif damage == 'padding': url = image()['image_url']['url'] + '='
    elif damage == 'empty': raw = b''
    elif damage == 'truncated': raw = raw[:-10]
    elif damage == 'concatenated': raw += raw
    elif damage == 'header_only': raw = b'\xff\xd8\xff\xd9'
    elif damage == 'png_bytes': raw = b'\x89PNG\r\n\x1a\n' + b'x' * 100
    elif damage == 'wide': raw = jpeg(width=642)
    elif damage == 'tall': raw = jpeg(height=2002)
    elif damage == 'too_large': raw = b'x' * (adapter.MAX_IMAGE_BYTES + 1)
    block = image(raw)
    if url is not None: block['image_url']['url'] = url
    with pytest.raises(adapter.AbacusRouterError, match='^abacus_router_request_invalid$'):
        prepare([{'type': 'text', 'text': 'Full rubric'}, block])


@pytest.mark.parametrize('content', [
    [], [{'type': 'text', 'text': ''}], [{'type': 'text', 'text': ' \n\t'}],
    [{'type': 'text', 'text': '\ud800'}], [{'type': 'text', 'text': 'x' * 100_000}],
    [{'type': 'text', 'text': 'test', 'cache_control': {}}],
    [{'type': 'input_audio', 'input_audio': {'data': 'private'}}],
    [{'type': 'text', 'text': 'test'}] * 242,
])
def test_text_and_content_structure_bounds(content):
    with pytest.raises(adapter.AbacusRouterError):
        prepare(content)


def test_image_only_overcount_and_detail_downgrade_are_not_silently_rewritten():
    for content in ([image()], [{'type': 'text', 'text': 'test'}] + [image()] * 61,
                    [{'type': 'text', 'text': 'test'}, {'type': 'image_url', 'image_url': {
                        'url': image()['image_url']['url'], 'detail': 'low'}}]):
        with pytest.raises(adapter.AbacusRouterError): prepare(content)


@pytest.mark.parametrize('schema', [
    {'type': 'array', 'items': {'type': 'number'}},
    {'type': 'object', '$ref': 'https://attacker.invalid/schema'},
    {'type': 'object', 'properties': {'value': {'type': 'string', 'pattern': '.*'}}},
    {'type': 'object', 'properties': {'value': {'type': 'number', 'maximum': float('inf')}}},
    {'type': 'object', 'properties': {'value': {'type': 'array', 'minItems': True}}},
    {'type': 'object', 'properties': {'value': {'type': 'array', 'uniqueItems': 1}}},
    {'type': 'object', 'properties': {'value': {'type': 'number', 'enum': [float('nan')]}}},
    {'type': 'object', 'properties': {'value': {'type': 'string', 'enum': ('x',)}}},
    {'type': 'object', 'properties': {1: {'type': 'string'}}},
])
def test_schema_is_fully_supported_or_rejected_never_partially_stripped(schema):
    with pytest.raises(adapter.AbacusRouterError): prepare(json_schema=schema)


def test_schema_identity_and_full_real_visual_schema_are_preserved():
    # Extract the existing pure visual schema constructor without importing its
    # SDK/global application setup. This is the actual unchanged source body.
    source = Path('app/services/visual_qc.py').read_text()
    tree = ast.parse(source)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_review_json_schema')
    names = {'MOMENT_FRACTIONS', '_EVIDENCE_BOOLEAN_FIELDS', '_MANUAL_QA_VISUAL_BOOLEAN_FIELDS',
             '_IDENTITY_BOOLEAN_FIELDS'}
    namespace = {n.targets[0].id: ast.literal_eval(n.value) for n in tree.body
                 if isinstance(n, ast.Assign) and len(n.targets) == 1
                 and isinstance(n.targets[0], ast.Name) and n.targets[0].id in names}
    assert set(namespace) == names
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'visual-schema', 'exec'), namespace)
    schema = namespace['_review_json_schema']([0, 1], {0: {0: {0, 1, 2}}, 1: {0: {0, 1, 2}}})
    frozen = prepare(parts(), json_schema=schema)
    assert frozen.payload['response_format']['json_schema']['schema'] == schema
    assert 'uniqueItems' in json.dumps(schema)


def test_mock_transport_actual_success_preserves_negative_qa_and_truthful_alias():
    frozen = prepare(parts())
    received = []
    def handle(request):
        received.append(request)
        return response(frozen, request=request)
    with httpx.Client(transport=httpx.MockTransport(handle), trust_env=False, follow_redirects=False) as client:
        actual = client.post(frozen.endpoint, **frozen.wire_kwargs())
    observed = adapter.observe_router_response(frozen, actual)
    assert len(received) == 1 and observed.result == RESULT and observed.result['accepted'] is False
    assert observed.returned_model == 'route-llm' and observed.underlying_model_verified is False
    assert observed.usage == {'prompt_tokens': 200, 'completion_tokens': 30, 'total_tokens': 230}
    evidence = observed.evidence
    assert evidence['request_sha256'] == frozen.request_sha256
    assert evidence['credential_sha256'] == frozen.credential_sha256
    assert evidence['response_body_sha256'] == hashlib.sha256(actual.content).hexdigest()
    assert evidence['wire_body_sha256'] == hashlib.sha256(actual.request.content).hexdigest()
    assert all(repr(value) not in repr(observed) for value in [KEY, PROMPT, RESULT])
    assert KEY not in json.dumps(evidence) and PROMPT not in json.dumps(evidence)
    assert 'chatcmpl-fixture-123' not in json.dumps(evidence)
    assert not any('cash' in key or 'credit' in key or 'micro' in key for key in evidence)
    observed.result['accepted'] = True
    observed.evidence['underlying_model_verified'] = True
    observed.usage['completion_tokens'] = 0
    assert observed.result['accepted'] is False and observed.underlying_model_verified is False
    assert observed.usage['completion_tokens'] == 30


@pytest.mark.parametrize('reported', ['route-llm', 'claude-haiku-4-5-20251001', 'unrecognized-provider/model-v2'])
def test_reported_model_is_honest_and_never_independent_model_proof(reported):
    frozen = prepare()
    actual = envelope(model=reported)
    actual.pop('usage')
    observed = adapter.observe_router_response(frozen, response(frozen, payload=actual))
    assert observed.returned_model == reported
    assert observed.underlying_model_verified is False and observed.evidence['underlying_model_verified'] is False
    assert observed.usage is None


@pytest.mark.parametrize('mutation', [
    lambda r: r.headers.__setitem__('Authorization', 'Bearer other-key'),
    lambda r: r.headers.__setitem__('OpenAI-Project', 'unbound-project'),
    lambda r: r.headers.__setitem__('x-api-key', KEY),
    lambda r: r.headers.__setitem__('Host', 'attacker.invalid'),
    lambda r: r.headers.__setitem__('Content-Length', '2'),
    lambda r: r.headers.__setitem__('Content-Type', 'text/plain'),
])
def test_actual_outgoing_headers_must_match_frozen_identity(mutation):
    frozen = prepare()
    actual = wire(frozen)
    mutation(actual)
    with pytest.raises(adapter.AbacusRouterError):
        adapter.observe_router_response(frozen, response(frozen, request=actual))


@pytest.mark.parametrize('damage', ['duplicate_auth', 'duplicate_json', 'body_change', 'numeric_change',
    'key_change', 'query', 'origin', 'method', 'path', 'fragment', 'utf16', 'unread'])
def test_actual_wire_mutation_cannot_be_accepted_after_a_frozen_reservation(damage):
    frozen = prepare()
    kwargs, url, method = frozen.wire_kwargs(), adapter.ENDPOINT, 'POST'
    kwargs.pop('timeout')
    if damage == 'duplicate_auth': kwargs['headers'] = list(kwargs['headers'].items()) + [('Authorization', 'Bearer ' + KEY)]
    elif damage == 'body_change': kwargs['json']['messages'][1]['content'][0]['text'] = 'Other charged request'
    elif damage == 'numeric_change': kwargs['json']['max_tokens'] = 8192.0
    elif damage == 'key_change': kwargs['headers']['authorization'] = 'Bearer changed-key'
    elif damage == 'query': url += '?model=haiku'
    elif damage == 'origin': url = url.replace('routellm.abacus.ai', 'attacker.invalid')
    elif damage == 'method': method = 'GET'
    elif damage == 'path': url = url.replace('chat/completions', 'messages')
    elif damage == 'fragment': url += '#hidden'
    elif damage == 'utf16': kwargs['content'] = json.dumps(kwargs.pop('json')).encode('utf-16')
    elif damage == 'unread': kwargs.pop('json'); kwargs['stream'] = iter([b'private-body'])
    else:
        raw = json.dumps(kwargs.pop('json')).encode()
        kwargs['content'] = b'{"model":"haiku",' + raw[1:]
    outgoing = httpx.Request(method, url, **kwargs)
    with pytest.raises(adapter.AbacusRouterError):
        adapter.observe_router_response(frozen, response(frozen, request=outgoing))


@pytest.mark.parametrize('status', [199, 301, 400, 401, 429, 500])
def test_non_success_status_is_terminal(status):
    frozen = prepare()
    with pytest.raises(adapter.AbacusRouterError):
        adapter.observe_router_response(frozen, response(frozen, status=status))


def test_redirect_history_and_unread_or_oversized_response_are_terminal():
    frozen = prepare()
    redirected = response(frozen)
    redirected.history = [httpx.Response(302, request=wire(frozen))]
    unread = httpx.Response(200, request=wire(frozen), stream=httpx.ByteStream(b'private'))
    huge = response(frozen, raw=b'x' * (adapter.MAX_RESPONSE_BYTES + 1))
    for actual in (redirected, unread, huge):
        with pytest.raises(adapter.AbacusRouterError): adapter.observe_router_response(frozen, actual)


@pytest.mark.parametrize('header', ['content-type', 'content-length', 'request-id', 'x-request-id', 'content-encoding'])
def test_ambiguous_relevant_response_headers_block_but_cdn_cookie_repetition_does_not(header):
    frozen = prepare()
    value = {'content-type': 'application/json', 'content-length': '1', 'content-encoding': 'identity'}.get(header, 'req1')
    headers = [('Content-Type', 'application/json')] if header != 'content-type' else []
    headers += [(header, value), (header.upper(), value)]
    with pytest.raises(adapter.AbacusRouterError):
        adapter.observe_router_response(frozen, response(frozen, headers=headers))
    actual = response(frozen, headers=[('Content-Type', 'application/json; charset=utf-8'),
                                      ('Set-Cookie', 'one=fixture'), ('Set-Cookie', 'two=fixture'),
                                      ('Vary', 'Origin'), ('Vary', 'Accept-Encoding')])
    assert adapter.observe_router_response(frozen, actual).result == RESULT


@pytest.mark.parametrize('damage', ['choices_zero', 'choices_two', 'length', 'filtered', 'tools', 'role',
    'index_bool', 'index_float', 'list_content', 'refusal', 'tool_calls', 'audio', 'model_none',
    'model_blank', 'id_too_long', 'created_bool', 'created_inf', 'object_stream', 'unknown_field'])
def test_protocol_must_be_exactly_one_completed_assistant_json_choice(damage):
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
    elif damage == 'list_content': message['content'] = [{'type': 'text', 'text': json.dumps(RESULT)}]
    elif damage == 'refusal': message['refusal'] = 'private rejected message'
    elif damage == 'tool_calls': message['tool_calls'] = [{'function': {'name': 'search'}}]
    elif damage == 'audio': message['audio'] = {'data': 'private'}
    elif damage == 'model_none': value['model'] = None
    elif damage == 'model_blank': value['model'] = ''
    elif damage == 'id_too_long': value['id'] = 'a' * 161
    elif damage == 'created_bool': value['created'] = True
    elif damage == 'created_inf': value['created'] = float('inf')
    elif damage == 'object_stream': value['object'] = 'chat.completion.chunk'
    else: value['unreviewed_tools_executed'] = ['search']
    with pytest.raises(adapter.AbacusRouterError):
        adapter.observe_router_response(frozen, response(frozen, payload=value))


@pytest.mark.parametrize('content', [
    '```json\n{}\n```', '{} {}', '[{}]', '{"accepted": false,"accepted":true}',
    '{"score":NaN}', '{"score":Infinity}', '{"score":1e999}', '', 'null',
    json.dumps({**RESULT, 'score': True}), json.dumps({**RESULT, 'score': 1.1}),
    json.dumps({**RESULT, 'moments': [0, 0]}), json.dumps({**RESULT, 'moments': []}),
    json.dumps({**RESULT, 'moments': [True]}), json.dumps({**RESULT, 'reason': ''}),
    json.dumps({**RESULT, 'reason': 'x' * 101}), json.dumps({**RESULT, 'extra': True}),
])
def test_full_supplied_schema_json_and_uniqueness_are_enforced(content):
    frozen, value = prepare(), envelope()
    value['choices'][0]['message']['content'] = content
    with pytest.raises(adapter.AbacusRouterError):
        adapter.observe_router_response(frozen, response(frozen, payload=value))


def test_envelope_duplicate_keys_and_non_utf8_are_rejected():
    frozen = prepare()
    raw = json.dumps(envelope()).encode()
    for changed in (b'{"model":"claude-haiku",' + raw[1:], raw.decode().encode('utf-16')):
        with pytest.raises(adapter.AbacusRouterError):
            adapter.observe_router_response(frozen, response(frozen, raw=changed))


@pytest.mark.parametrize('enum,value,accepted', [([True], 1, False), ([1], True, False),
    ([1], 1.0, True), ([False], 0, False), (['yes'], 'yes', True)])
def test_json_schema_enum_does_not_treat_boolean_as_number(enum, value, accepted):
    schema = {'type': 'object', 'properties': {'v': {'type': ['boolean', 'number', 'string'], 'enum': enum}},
              'required': ['v'], 'additionalProperties': False}
    frozen, payload = prepare(json_schema=schema), envelope()
    payload['choices'][0]['message']['content'] = json.dumps({'v': value})
    if accepted:
        assert adapter.observe_router_response(frozen, response(frozen, payload=payload)).result == {'v': value}
    else:
        with pytest.raises(adapter.AbacusRouterError, match='schema_mismatch'):
            adapter.observe_router_response(frozen, response(frozen, payload=payload))


@pytest.mark.parametrize('usage', [None, {}, {'prompt_tokens': 1},
    {'prompt_tokens': True, 'completion_tokens': 0, 'total_tokens': 1},
    {'prompt_tokens': 1.0, 'completion_tokens': 0, 'total_tokens': 1},
    {'prompt_tokens': -1, 'completion_tokens': 1, 'total_tokens': 0},
    {'prompt_tokens': 1_000_000_001, 'completion_tokens': 0, 'total_tokens': 1_000_000_001},
    {'prompt_tokens': 1, 'completion_tokens': 8193, 'total_tokens': 8194},
    {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 1},
    {'prompt_tokens': 1, 'completion_tokens': float('nan'), 'total_tokens': 1},
    {'prompt_tokens': 1, 'completion_tokens': 0, 'total_tokens': 1, 'credits': 0},
    {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2, 'completion_tokens_details': {'reasoning_tokens': 2}},
    {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2, 'prompt_tokens_details': {'audio_tokens': 1}},
    {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2, 'completion_tokens_details': {'audio_tokens': True}},
])
def test_optional_present_usage_is_strict_finite_and_never_a_cash_or_credit_meter(usage):
    frozen = prepare()
    with pytest.raises(adapter.AbacusRouterError):
        adapter.observe_router_response(frozen, response(frozen, payload=envelope(usage=usage)))


def test_optional_token_details_and_large_input_counter_are_observed_not_priced():
    frozen = prepare(max_tokens=2)
    usage = {'prompt_tokens': 999_999_998, 'completion_tokens': 2, 'total_tokens': 1_000_000_000,
             'prompt_tokens_details': {'cached_tokens': 1, 'audio_tokens': 0},
             'completion_tokens_details': {'reasoning_tokens': 1, 'audio_tokens': 0}}
    observed = adapter.observe_router_response(frozen, response(frozen, payload=envelope(usage=usage)))
    assert observed.usage == usage
    assert observed.evidence['underlying_model_verified'] is False


def test_forged_prepared_snapshot_and_error_messages_are_not_authority_or_content_leaks():
    frozen = prepare()
    forged = replace(frozen, _body_bytes=b'{"private": "' + KEY.encode() + b'"}')
    with pytest.raises(adapter.AbacusRouterError) as caught:
        adapter.observe_router_response(forged, response(frozen))
    assert KEY not in repr(caught.value) and PROMPT not in str(caught.value)
    bad = envelope()
    bad['choices'][0]['message']['content'] = KEY + PROMPT
    with pytest.raises(adapter.AbacusRouterError) as caught:
        adapter.observe_router_response(frozen, response(frozen, payload=bad))
    assert KEY not in repr(caught.value) and PROMPT not in str(caught.value)


def test_module_has_no_send_ledger_quote_or_activation_calls():
    tree = ast.parse(Path(adapter.__file__).read_text())
    forbidden = {'post', 'send', 'request', 'stream', 'paid_post', 'reserve', 'settle', 'initialize',
                 'quote_http_request', 'SpendQuote', 'generate_abacus_json'}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ''
            assert name not in forbidden
