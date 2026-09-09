"""Actual native HTTPX shapes and terminal metering; all bytes are synthetic."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import hashlib
import json

import httpx
import pytest

from app.services import elevenlabs_credit_adapter as adapter
from app.services import production_credit_funding as credit
from app.services.production_spend import SpendBlocked
from test_elevenlabs_spend_quotes import voice_body
from test_production_credit_funding import policy as native_policy, NOW, intent


KEY = 'private-test-key'
REQUEST_ID = 'private-test-provider-request-id'


@pytest.fixture
def kwargs(voice_body):
    return {'json': voice_body('Şeffaf bir örnek: QR KODU.', seed=123, speed=1.01),
            'headers': {'xi-api-key': KEY, 'Accept': 'application/json', 'Content-Type': 'application/json'},
            'params': {'output_format': 'mp3_44100_128'}, 'timeout': 180}


def prepared(kwargs):
    return adapter.inspect_credit_request(credit.ROUTE, kwargs)


def response(request, *, cost='17', request_id=REQUEST_ID, status=200, content=b'qa-invalid-json', headers=None):
    outgoing = httpx.Request('POST', request.route, **{k: v for k, v in request.wire_kwargs().items() if k != 'timeout'})
    raw_headers = headers if headers is not None else {
        'character-cost': cost, 'request-id': request_id}
    return httpx.Response(status, content=content, request=outgoing,
                          headers=httpx.Headers(raw_headers, encoding='utf-8'))


def observe(request, reply, held=1000):
    return adapter.observe_credit_response(request, reply, reserved_credits=held)


def test_actual_voice_builder_snapshot_is_detached_and_redacted(kwargs):
    original = deepcopy(kwargs)
    item = prepared(kwargs)
    assert item.provider == 'elevenlabs' and item.model == 'eleven_multilingual_v2'
    assert item.operation == '/v1/text-to-speech/' + credit.VOICE_ID + '/with-timestamps'
    assert item.route == credit.ROUTE and item.voice_id == credit.VOICE_ID
    assert item.credential_sha256 == hashlib.sha256(('elevenlabs\0' + KEY).encode()).hexdigest()
    assert item.payload == {'json': original['json'], 'params': original['params']}
    assert KEY not in repr(item) and original['json']['text'] not in repr(item)
    kwargs['headers']['xi-api-key'] = 'mutated-private-key'
    kwargs['json']['voice_settings']['speed'] = .7
    payload = item.payload
    payload['json']['text'] = 'Changed text.'
    wire = item.wire_kwargs()
    wire['headers']['xi-api-key'] = 'changed-clone'
    wire['json']['voice_settings']['speed'] = .8
    assert item.payload == {'json': original['json'], 'params': original['params']}
    assert item.wire_kwargs()['headers']['xi-api-key'] == KEY
    with pytest.raises(FrozenInstanceError):
        item._body_bytes = b'{}'


@pytest.mark.parametrize('speed', [.7, 1.01, 1.2])
@pytest.mark.parametrize('seed', [None, 0, 4_294_967_295])
def test_current_numeric_boundaries_preserve_exact_actual_body(voice_body, kwargs, speed, seed):
    kwargs['json'] = voice_body('A bounded voice.', speed=speed, seed=seed)
    assert prepared(kwargs).payload['json'] == kwargs['json']


@pytest.mark.parametrize('change', [
    lambda k: k.update(params=None),
    lambda k: k.update(timeout=True),
    lambda k: k.update(timeout=181),
    lambda k: k.update(follow_redirects=True),
    lambda k: k['params'].update(output_format='pcm_44100'),
    lambda k: k['params'].update(enable_logging=False),
    lambda k: k['headers'].update(Authorization='Bearer other-account'),
    lambda k: k['headers'].update({'XI-API-KEY': KEY}),
    lambda k: k['headers'].update({'xi-api-key': ' key-with-space'}),
    lambda k: k['headers'].update({'xi-api-key': ''}),
    lambda k: k['headers'].update({'Accept': 'audio/mpeg'}),
    lambda k: k['json'].update(model_id='eleven_flash_v2_5'),
    lambda k: k['json'].update(language_code='tr'),
    lambda k: k['json'].update(previous_text='Prior context.'),
    lambda k: k['json'].update(next_request_ids=['prior-provider-id']),
    lambda k: k['json'].update(apply_text_normalization='off'),
    lambda k: k['json'].update(text=''),
    lambda k: k['json'].update(text='   '),
    lambda k: k['json'].update(text='x' * 10_001),
    lambda k: k['json'].update(text='\ud800'),
    lambda k: k['json'].update(seed=True),
    lambda k: k['json'].update(seed=-1),
    lambda k: k['json'].update(seed=4_294_967_296),
    lambda k: k['json']['voice_settings'].update(speed=float('nan')),
    lambda k: k['json']['voice_settings'].update(speed=float('inf')),
    lambda k: k['json']['voice_settings'].update(speed=True),
    lambda k: k['json']['voice_settings'].update(style=False),
    lambda k: k['json']['voice_settings'].update(use_speaker_boost=1),
    lambda k: k['json']['voice_settings'].update(extra='ignored'),
])
def test_unknown_shapes_are_terminal_without_price_or_fallback(kwargs, change):
    change(kwargs)
    with pytest.raises(SpendBlocked, match='credit_tts_request_invalid') as caught:
        prepared(kwargs)
    assert KEY not in str(caught.value)


@pytest.mark.parametrize('url', [credit.ROUTE + '?x=1', credit.ROUTE + '#fragment',
                               credit.ROUTE.replace('https:', 'http:'),
                               credit.ROUTE.replace('api.elevenlabs.io', 'other.invalid'),
                               credit.ROUTE.replace(credit.VOICE_ID, 'another-voice'),
                               credit.ROUTE.replace('/with-timestamps', '')])
def test_only_exact_reviewed_route(kwargs, url):
    with pytest.raises(SpendBlocked):
        adapter.inspect_credit_request(url, kwargs)


def test_wire_header_snapshot_accepts_normal_httpx_transport_and_exact_charge(kwargs):
    item = prepared(kwargs)
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, content=b'not usable audio JSON', headers={
            'Character-Cost': '17', 'Request-ID': REQUEST_ID})

    with httpx.Client(transport=httpx.MockTransport(handle), trust_env=False, follow_redirects=False) as client:
        reply = client.post(item.route, **item.wire_kwargs())
        meter = observe(item, reply)
    assert len(seen) == 1
    assert meter['actual_credit_cost'] == 17
    assert meter['provider_request_id_sha256'] == hashlib.sha256(
        ('elevenlabs\0request\0' + REQUEST_ID).encode()).hexdigest()
    assert len(meter['response_proof_sha256']) == 64
    assert set(meter) == {'actual_credit_cost', 'provider_request_id_sha256', 'response_proof_sha256'}
    assert KEY not in repr(meter) and REQUEST_ID not in repr(meter)
    # Metering complete bytes does not parse/approve audio or invent a text rate.
    with pytest.raises(ValueError):
        reply.json()
    assert meter['actual_credit_cost'] != len(kwargs['json']['text'])


@pytest.mark.parametrize('cost', ['', '0', '-1', '1.0', '1e2', 'true', ' 17', '17 ', '+17',
                                '017', '17,17', '1001', '10000000001', '１２'])
def test_missing_or_ambiguous_meter_cannot_release_hold(kwargs, cost):
    item = prepared(kwargs)
    with pytest.raises(SpendBlocked, match='credit_tts_.*unverified'):
        observe(item, response(item, cost=cost))


@pytest.mark.parametrize('request_id', ['', ' ', 'a b', 'a,b', 'é', 'x' * 257, 'a\nb'])
def test_provider_request_identity_is_bounded_unambiguous(kwargs, request_id):
    item = prepared(kwargs)
    with pytest.raises(SpendBlocked):
        observe(item, response(item, request_id=request_id))


@pytest.mark.parametrize('headers', [
    {}, {'request-id': REQUEST_ID}, {'character-cost': '17'},
    [('character-cost', '17'), ('Character-Cost', '17'), ('request-id', REQUEST_ID)],
    [('character-cost', '17'), ('request-id', REQUEST_ID), ('Request-Id', REQUEST_ID)],
    [('character-cost', '17'), ('request-id', REQUEST_ID),
     ('content-type', 'application/json'), ('Content-Type', 'application/json')],
])
def test_absent_and_duplicate_raw_response_headers_fail_closed(kwargs, headers):
    item = prepared(kwargs)
    with pytest.raises(SpendBlocked):
        observe(item, response(item, headers=headers))


def test_real_httpx_repeated_cdn_headers_do_not_make_exact_meter_uncertain(kwargs):
    item = prepared(kwargs)

    def handle(request):
        return httpx.Response(200, content=b'complete charged bytes', headers=[
            ('character-cost', '17'), ('request-id', REQUEST_ID),
            ('set-cookie', 'cdn_first=one; Secure'), ('Set-Cookie', 'cdn_second=two; Secure'),
            ('vary', 'Origin'), ('Vary', 'Accept-Encoding'),
        ])

    with httpx.Client(transport=httpx.MockTransport(handle), trust_env=False) as client:
        reply = client.post(item.route, **item.wire_kwargs())
        assert len(reply.headers.get_list('set-cookie')) == 2
        assert len(reply.headers.get_list('vary')) == 2
        meter = observe(item, reply)
    assert meter == observe(item, response(item, content=b'complete charged bytes'))
    assert meter['actual_credit_cost'] == 17


@pytest.mark.parametrize('held', [None, True, 0, -1, 1.5, 1_000_000_001])
def test_hold_requires_positive_bounded_native_integer(kwargs, held):
    item = prepared(kwargs)
    with pytest.raises(SpendBlocked):
        observe(item, response(item), held=held)


@pytest.mark.parametrize('status', [199, 301, 307, 400, 429, 500])
def test_non_success_is_unknown_without_retry_or_zero_charge(kwargs, status):
    item = prepared(kwargs)
    with pytest.raises(SpendBlocked):
        observe(item, response(item, status=status))


@pytest.mark.parametrize('change', [
    lambda r: setattr(r, 'method', 'GET'),
    lambda r: setattr(r, 'url', httpx.URL(credit.ROUTE.replace('api.elevenlabs.io', 'other.invalid'))),
    lambda r: setattr(r, 'url', httpx.URL(credit.ROUTE + '?output_format=mp3_44100_128&output_format=mp3_44100_128')),
    lambda r: r.headers.update({'xi-api-key': 'different-private-key'}),
    lambda r: r.headers.update({'Authorization': 'Bearer other-account'}),
    lambda r: r.headers.update({'host': 'other.invalid'}),
    lambda r: r.headers.update({'content-length': '1'}),
    lambda r: r.headers.update({'x-unknown-account': 'different-account'}),
])
def test_actual_wire_identity_must_match_reservation_input(kwargs, change):
    item = prepared(kwargs)
    reply = response(item)
    change(reply.request)
    with pytest.raises(SpendBlocked) as caught:
        observe(item, reply)
    assert KEY not in str(caught.value) and 'different-private-key' not in str(caught.value)


def test_actual_body_modification_or_duplicate_json_cannot_share_proof(kwargs):
    item = prepared(kwargs)
    changed = item.wire_kwargs()
    changed['json']['seed'] += 1
    request = httpx.Request('POST', item.route, **{k: v for k, v in changed.items() if k != 'timeout'})
    reply = response(item)
    reply.request = request
    with pytest.raises(SpendBlocked):
        observe(item, reply)
    wire = item.wire_kwargs()
    duplicate = json.dumps(wire['json']).replace('"seed": 123', '"seed": 123, "seed": 123').encode()
    reply.request = httpx.Request('POST', item.route, params=wire['params'], headers=wire['headers'], content=duplicate)
    with pytest.raises(SpendBlocked):
        observe(item, reply)


def test_duplicate_actual_auth_header_is_not_hidden_by_httpx_lookup(kwargs):
    item = prepared(kwargs)
    wire = item.wire_kwargs()
    headers = list(wire['headers'].items()) + [('XI-API-KEY', KEY)]
    reply = response(item)
    reply.request = httpx.Request('POST', item.route, params=wire['params'],
                                 headers=headers, json=wire['json'])
    with pytest.raises(SpendBlocked):
        observe(item, reply)


def test_proof_commits_wire_encoding_even_when_native_json_is_unchanged(kwargs):
    item = prepared(kwargs)
    normal = observe(item, response(item))
    wire = item.wire_kwargs()
    raw = json.dumps(wire['json'], ensure_ascii=True, indent=2).encode()
    reply = response(item)
    reply.request = httpx.Request('POST', item.route, params=wire['params'],
                                 headers=wire['headers'], content=raw)
    alternate = observe(item, reply)
    assert alternate['actual_credit_cost'] == normal['actual_credit_cost']
    assert alternate['provider_request_id_sha256'] == normal['provider_request_id_sha256']
    assert alternate['response_proof_sha256'] != normal['response_proof_sha256']


def test_redirect_history_unread_and_oversized_response_are_not_terminal_proof(kwargs, monkeypatch):
    item = prepared(kwargs)
    reply = response(item)
    reply.history = [httpx.Response(307)]
    with pytest.raises(SpendBlocked):
        observe(item, reply)
    unread = httpx.Response(200, stream=httpx.ByteStream(b'unread'), request=response(item).request,
                            headers={'character-cost': '17', 'request-id': REQUEST_ID})
    with pytest.raises(SpendBlocked):
        observe(item, unread)
    assert not unread.is_stream_consumed  # The validator cannot read a network stream.
    monkeypatch.setattr(adapter, 'MAX_RESPONSE_BYTES', 8)
    with pytest.raises(SpendBlocked):
        observe(item, response(item, content=b'x' * 9))


def test_proof_binds_actual_status_headers_request_and_bytes(kwargs):
    item = prepared(kwargs)
    base = observe(item, response(item))
    assert base == observe(item, response(item))
    variants = [response(item, cost='18'), response(item, request_id='another-request'),
                response(item, status=201), response(item, content=b'different charged bytes')]
    assert all(observe(item, r)['response_proof_sha256'] != base['response_proof_sha256'] for r in variants)
    kwargs['json']['seed'] += 1
    another = prepared(kwargs)
    assert observe(another, response(another))['response_proof_sha256'] != base['response_proof_sha256']


def test_exact_meter_settles_once_unknown_meter_keeps_whole_pool_and_replay_stays(kwargs, native_policy):
    item = prepared(kwargs)
    native_policy['credential_sha256'] = item.credential_sha256
    binding = {'actual_account_sha256': native_policy['account_sha256'],
               'actual_credential_sha256': item.credential_sha256}
    initial = credit.initial_credit_state(native_policy, now=NOW)
    held, receipt = credit.reserve_credit_intent(native_policy, initial, intent=intent(), now=NOW, **binding)
    before = deepcopy(held)
    with pytest.raises(SpendBlocked):
        observe(item, response(item, headers={'request-id': REQUEST_ID}), held=receipt['reserved_credits'])
    assert held == before and held['reserved_credits'] == native_policy['allocation_credits']
    with pytest.raises(SpendBlocked, match='credit_pool_has_uncertain_intent'):
        credit.reserve_credit_intent(native_policy, held, intent=intent(2), now=NOW, **binding)
    meter = observe(item, response(item, content=b'charged but invalid alignment'), held=receipt['reserved_credits'])
    seen = {'version': 1, 'terminal': True, 'source': 'verified_provider_meter',
            **{k: receipt['intent'][k] for k in ('intent_id', 'root_lineage_id', 'channel_id',
                                                'source_connection_id', 'request_sha256')},
            'reservation_sha256': receipt['reservation_sha256'],
            'account_sha256': native_policy['account_sha256'], 'credential_sha256': item.credential_sha256,
            'observed_at': '2026-09-09T14:00:00Z', **meter}
    settled, _ = credit.settle_credit_intent(native_policy, held, observation=seen, now=NOW, **binding)
    assert settled['spent_credits'] == 17 and settled['reserved_credits'] == 0
    repeated, _ = credit.settle_credit_intent(native_policy, settled, observation=seen, now=NOW, **binding)
    assert repeated == settled
    with pytest.raises(SpendBlocked, match='credit_request_already_reserved'):
        credit.reserve_credit_intent(native_policy, settled,
                                     intent=intent(intent_id='f' * 64), now=NOW, **binding)


def test_validator_has_no_network_or_money_write_entry_points(kwargs, monkeypatch):
    def forbidden(*args, **options):
        pytest.fail('Pure validation tried to dispatch')
    monkeypatch.setattr(httpx, 'post', forbidden)
    monkeypatch.setattr(httpx, 'stream', forbidden)
    monkeypatch.setattr(httpx.Client, 'send', forbidden)
    item = prepared(kwargs)
    assert observe(item, response(item))['actual_credit_cost'] == 17
