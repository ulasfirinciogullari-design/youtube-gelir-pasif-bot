"""Offline ElevenLabs commissioning through real quote and atomic spend paths.

All tariff numbers below are fictional operator evidence. No test opens a
provider connection, infers a subscription balance or authorizes live spending.
"""
from copy import deepcopy
import datetime as datetime_module
from datetime import datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock

import fakeredis
import httpx
import pytest

from app.services.production_spend import LEDGER_KEY, SpendBlocked, SpendLedger, SpendPolicy
from app.services.elevenlabs_spend_quotes import (
    ELEVENLABS_SELECTED_VOICE_ID, ELEVENLABS_TTS_ROUTE, elevenlabs_price_revision,
)
from app.services import production_spend_quotes as quotes
from app.services import production_spend_runtime as runtime
from app.services.production_funding import validate_funding_policy
# This explicitly imported fixture compiles only the real normalizer/body
# builder; importing the worker or constructing any provider client is avoided.
from test_elevenlabs_spend_quotes import voice_body


ROOT = '11111111-1111-4111-8111-111111111111'
CHILD = '22222222-2222-4222-8222-222222222222'
CHANNEL = 'UC5v9AvNtD3PTLgo6m1jROOA'
CONNECTION = 'connection_AAAAA'
KEY = 'offline-elevenlabs-runtime-key'
CREDENTIAL = hashlib.sha256(('elevenlabs\0' + KEY).encode()).hexdigest()
ACCOUNT = 'a' * 64
EXPIRY = '2026-10-01T00:00:00Z'


def _evidence():
    models = [('multilingual_v2_continuous_v1', 'eleven_multilingual_v2'),
              ('turkish_flash_v2_5_continuous_v1', 'eleven_flash_v2_5')]
    return {
        'version': 1, 'provider': 'elevenlabs', 'currency': 'USD',
        'account_sha256': ACCOUNT, 'credential_sha256': CREDENTIAL,
        'proof_sha256': 'b' * 64, 'valid_from': '2026-09-09T00:00:00Z',
        'valid_until': EXPIRY, 'voice_id': ELEVENLABS_SELECTED_VOICE_ID,
        'route': ELEVENLABS_TTS_ROUTE,
        'profiles': [{
            'profile_id': profile, 'model_id': model, 'max_text_codepoints': 1000,
            'billing_unit_bound': {
                'basis': 'submitted_text_unicode_codepoints',
                'scope': 'normalization_model_voice_all_request_charges',
                'verified': True, 'text_unit_numerator': 3,
                'text_unit_denominator': 2, 'per_request_units': 7,
            },
            'list_rate': {'micro_usd_numerator': 11, 'billing_unit_denominator': 3},
        } for profile, model in models],
    }


def _configure(case, evidence):
    case.config.studio_elevenlabs_pricing_evidence_json = json.dumps(evidence)


def _policy(case, *, mode='covered_only', allowance=100_000, opening=0):
    revision = elevenlabs_price_revision(case.evidence, now=case.clock[0])
    funding = ({'covered_list_allowance_micro': allowance,
                'coverage_basis': 'verified_route_list_cost_usd', 'no_auto_overage': True}
               if mode == 'covered_only' else
               {'cash_factor_numerator': 11, 'cash_factor_denominator': 10,
                'cash_bound_verified': True})
    policy = {
        'version': 1, 'currency': 'USD', 'month': '2026-09',
        'valid_from': '2026-09-09T00:00:00Z', 'valid_until': EXPIRY,
        'cash_cap_micro': 10_000_000, 'opening_cash_micro': opening,
        'reconciliation_sha256': 'c' * 64,
        'accounts': [{
            'provider': 'elevenlabs', 'account_sha256': ACCOUNT,
            'credential_sha256': CREDENTIAL, 'evidence_sha256': 'd' * 64,
            'valid_until': EXPIRY, 'mode': mode, 'funding': funding,
            'routes': [{'route': ELEVENLABS_TTS_ROUTE, 'model': profile['model_id'],
                        'price_revision': revision} for profile in case.evidence['profiles']],
        }],
    }
    return validate_funding_policy(policy, now=case.clock[0])


def _request(voice_body, text='A small coin reveals the design.', *, flash=False, key=KEY):
    return {
        'json': voice_body(text, turkish_short_preview=flash, speed=1.0, seed=123),
        'headers': {'xi-api-key': key, 'Accept': 'application/json', 'Content-Type': 'application/json'},
        'params': {'output_format': 'mp3_44100_128'}, 'timeout': 180,
    }


def _receipts(case):
    return {name: json.loads(value) for name, value in
            case.client.hscan_iter(LEDGER_KEY, match='request:*')}


def _list_cost(request):
    # Independent integer arithmetic over actual transmitted normalized text.
    units = (len(request['json']['text']) * 3 + 1) // 2 + 7
    return (units * 11 + 2) // 3


def _receipt_field(request):
    # Derive the expected persisted key independently of runtime helpers.
    payload = {'lineage': ROOT, 'provider': 'elevenlabs',
               'operation': ELEVENLABS_TTS_ROUTE.removeprefix('https://api.elevenlabs.io'),
               'payload': {'json': request['json'], 'params': request['params']}}
    canonical = json.dumps(payload, sort_keys=True, separators=(',', ':'),
                           ensure_ascii=True, allow_nan=False)
    fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
    return 'request:' + hashlib.sha256(fingerprint.encode()).hexdigest()


@pytest.fixture
def case(monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=True)
    clock = [datetime(2026, 9, 9, 12, tzinfo=timezone.utc)]
    ledger = SpendLedger(client, SpendPolicy(*([100_000_000] * 6)), clock=lambda: clock[0])
    ledger.initialize()
    config = SimpleNamespace(studio_spend_enforcement=True,
                             studio_elevenlabs_pricing_evidence_json='')
    monkeypatch.setattr(runtime, 'settings', config)
    monkeypatch.setattr(quotes, 'settings', config)
    monkeypatch.setattr(runtime, 'configured_ledger', lambda: ledger)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls.fromtimestamp(clock[0].timestamp(), timezone.utc)
            return value.astimezone(tz) if tz is not None else value.replace(tzinfo=None)

    # Runtime deliberately imports datetime within paid_post; freeze that clock
    # as well as the quote module without bypassing evidence expiry validation.
    monkeypatch.setattr(datetime_module, 'datetime', FixedDateTime)
    monkeypatch.setattr(quotes, 'datetime', FixedDateTime)
    client.sadd(runtime._CHANNEL_INDEX, CHANNEL)
    client.set(runtime._CHANNEL_PREFIX + CHANNEL, json.dumps({'id': CHANNEL, 'connection_id': CONNECTION}))
    job = {'task_id': ROOT, 'parent_id': None, 'spec': {
        'production_channel_id': CHANNEL, 'production_connection_id': CONNECTION,
        'duration_minutes': 0.5}}
    client.set(runtime._JOB_PREFIX + ROOT, json.dumps(job))
    task_token = runtime._TASK_ID.set(ROOT)
    scene_token = runtime._SCENE.set(None)
    runtime.resolve_context(client, ROOT)
    value = SimpleNamespace(client=client, ledger=ledger, clock=clock, config=config,
                            evidence=_evidence(), post=Mock(return_value={'audio_base64': 'offline'}),
                            job=job)
    _configure(value, value.evidence)
    yield value
    runtime._SCENE.reset(scene_token)
    runtime._TASK_ID.reset(task_token)


@pytest.mark.parametrize('mode', ['covered_only', 'cash_only'])
@pytest.mark.parametrize('flash', [False, True])
def test_exact_voice_profiles_reserve_both_ledgers_before_one_send(case, voice_body, mode, flash):
    request = _request(voice_body, 'QR KODU ve GPS açık' if flash else 'A small coin reveals the design.', flash=flash)
    expected_request = deepcopy(request)
    cost = _list_cost(request)
    cash = (cost * 11 + 9) // 10 if mode == 'cash_only' else 0
    covered = cost if mode == 'covered_only' else 0
    case.ledger.initialize_funding(_policy(case, mode=mode))

    def observe_send(url, **kwargs):
        assert url == ELEVENLABS_TTS_ROUTE and kwargs == expected_request
        assert kwargs['json'] is not request['json']
        assert kwargs['headers'] is not request['headers']
        assert kwargs['params'] is not request['params']
        assert case.ledger.snapshot()['period']['used_micro'] == cost
        summary = case.ledger.funding_snapshot()
        assert summary['cash_reserved_micro'] == cash
        assert summary['providers'][0]['covered_reserved_micro'] == covered
        receipt = _receipts(case)[_receipt_field(request)]
        assert receipt['state'] == 'reserved_before_request'
        assert receipt['funding']['account_sha256'] == ACCOUNT
        assert receipt['funding']['list_maximum_micro'] == cost
        assert receipt['funding']['cash_micro'] == cash
        assert receipt['funding']['covered_micro'] == covered
        return {'audio_base64': 'offline'}

    case.post.side_effect = observe_send
    assert runtime.paid_post(case.post, ELEVENLABS_TTS_ROUTE, **request) == {'audio_base64': 'offline'}
    case.post.assert_called_once()
    assert request == expected_request
    serialized = json.dumps(case.client.hgetall(LEDGER_KEY))
    assert KEY not in serialized and request['json']['text'] not in serialized


@pytest.mark.parametrize('raw', ['', None, '\ud800', '{}', 'null', '{', '{"version":1,"version":1}',
                                  '{"sharing":{"rate":1.0}}', '{"price":NaN}'])
def test_uncommissioned_or_malformed_config_cannot_send(case, voice_body, raw):
    case.ledger.initialize_funding(_policy(case))
    case.config.studio_elevenlabs_pricing_evidence_json = raw
    before = case.client.hgetall(LEDGER_KEY)
    with pytest.raises(SpendBlocked):
        runtime.paid_post(case.post, ELEVENLABS_TTS_ROUTE, **_request(voice_body))
    assert case.client.hgetall(LEDGER_KEY) == before
    case.post.assert_not_called()


@pytest.mark.parametrize('change', ['proof', 'rate', 'expired'])
def test_changed_or_expired_evidence_never_uses_the_old_funding_revision(case, voice_body, change):
    case.ledger.initialize_funding(_policy(case))
    if change == 'proof':
        case.evidence['proof_sha256'] = 'e' * 64
    elif change == 'rate':
        case.evidence['profiles'][0]['list_rate']['micro_usd_numerator'] += 1
    else:
        case.evidence['valid_until'] = '2026-09-09T12:00:00Z'
    _configure(case, case.evidence)
    before = case.client.hgetall(LEDGER_KEY)
    code = 'spend_elevenlabs_evidence_expired' if change == 'expired' else 'spend_funding_route_not_covered'
    with pytest.raises(SpendBlocked, match='^' + code + '$'):
        runtime.paid_post(case.post, ELEVENLABS_TTS_ROUTE, **_request(voice_body))
    assert case.client.hgetall(LEDGER_KEY) == before
    case.post.assert_not_called()


def test_evidence_changes_between_quote_and_admission_block_before_reserving(case, voice_body, monkeypatch):
    case.ledger.initialize_funding(_policy(case))
    real_quote = quotes.quote_http_request

    def quote_then_change(*args, **kwargs):
        result = real_quote(*args, **kwargs)
        case.evidence['proof_sha256'] = 'e' * 64
        _configure(case, case.evidence)
        return result

    monkeypatch.setattr(quotes, 'quote_http_request', quote_then_change)
    before = case.client.hgetall(LEDGER_KEY)
    with pytest.raises(SpendBlocked, match='^spend_funding_evidence_changed$'):
        runtime.paid_post(case.post, ELEVENLABS_TTS_ROUTE, **_request(voice_body))
    assert case.client.hgetall(LEDGER_KEY) == before
    case.post.assert_not_called()


@pytest.mark.parametrize('mismatch', ['account', 'credential', 'route', 'revision', 'model'])
def test_account_credential_and_exact_route_revision_must_match_funding(case, voice_body, mismatch):
    policy = _policy(case)
    account = policy['accounts'][0]
    if mismatch in ('account', 'credential'):
        account[mismatch + '_sha256'] = 'e' * 64
    else:
        for route in account['routes']:
            if mismatch == 'route':
                route['route'] = ELEVENLABS_TTS_ROUTE.removesuffix('/with-timestamps')
            elif mismatch == 'revision':
                route['price_revision'] = 'unreviewed-price'
            else:
                route['model'] += '-unpriced'
    case.ledger.initialize_funding(policy)
    before = case.client.hgetall(LEDGER_KEY)
    code = ('spend_funding_account_mismatch' if mismatch in ('account', 'credential')
            else 'spend_funding_route_not_covered')
    with pytest.raises(SpendBlocked, match='^' + code + '$'):
        runtime.paid_post(case.post, ELEVENLABS_TTS_ROUTE, **_request(voice_body))
    assert case.client.hgetall(LEDGER_KEY) == before
    case.post.assert_not_called()


def test_actual_key_rotation_cannot_borrow_the_evidenced_voice_price(case, voice_body):
    case.ledger.initialize_funding(_policy(case))
    before = case.client.hgetall(LEDGER_KEY)
    with pytest.raises(SpendBlocked, match='^spend_funding_credential_mismatch$') as caught:
        runtime.paid_post(case.post, ELEVENLABS_TTS_ROUTE,
                          **_request(voice_body, key='another-offline-key'))
    assert 'another-offline-key' not in str(caught.value) and KEY not in str(caught.value)
    assert case.client.hgetall(LEDGER_KEY) == before
    case.post.assert_not_called()


@pytest.mark.parametrize('url', [
    ELEVENLABS_TTS_ROUTE.removesuffix('/with-timestamps'),
    ELEVENLABS_TTS_ROUTE.replace(ELEVENLABS_SELECTED_VOICE_ID, 'different-voice'),
    ELEVENLABS_TTS_ROUTE + '?output_format=mp3_44100_128',
    ELEVENLABS_TTS_ROUTE.replace('https:', 'http:'),
    ELEVENLABS_TTS_ROUTE.replace('.io/', '.io:443/'),
])
def test_unpriced_url_never_reaches_sender(case, voice_body, url):
    case.ledger.initialize_funding(_policy(case))
    before = case.client.hgetall(LEDGER_KEY)
    with pytest.raises(SpendBlocked):
        runtime.paid_post(case.post, url, **_request(voice_body))
    assert case.client.hgetall(LEDGER_KEY) == before
    case.post.assert_not_called()


@pytest.mark.parametrize('change', ['format', 'query', 'headers', 'context', 'normalization'])
def test_unpriced_request_fields_block_before_reserving(case, voice_body, change):
    case.ledger.initialize_funding(_policy(case))
    request = _request(voice_body)
    if change == 'format':
        request['params']['output_format'] = 'pcm_44100'
    elif change == 'query':
        request['params']['enable_logging'] = False
    elif change == 'headers':
        request['headers']['Authorization'] = 'Bearer another-offline-key'
    elif change == 'context':
        request['json']['previous_text'] = 'Additional billable context.'
    else:
        request['json']['apply_text_normalization'] = 'off'
    before = case.client.hgetall(LEDGER_KEY)
    with pytest.raises(SpendBlocked):
        runtime.paid_post(case.post, ELEVENLABS_TTS_ROUTE, **request)
    assert case.client.hgetall(LEDGER_KEY) == before
    case.post.assert_not_called()


def test_missing_funding_never_becomes_a_free_voice_call(case, voice_body):
    before = case.client.hgetall(LEDGER_KEY)
    with pytest.raises(SpendBlocked, match='^spend_funding_not_initialized$'):
        runtime.paid_post(case.post, ELEVENLABS_TTS_ROUTE, **_request(voice_body))
    assert case.client.hgetall(LEDGER_KEY) == before
    case.post.assert_not_called()


@pytest.mark.parametrize('mode', ['covered_only', 'cash_only'])
def test_exhausted_voice_allowance_cannot_fall_back_or_exceed_owner_cash(case, voice_body, mode):
    request = _request(voice_body)
    cost = _list_cost(request)
    cash = (cost * 11 + 9) // 10
    case.ledger.initialize_funding(_policy(case, mode=mode, allowance=cost,
                                           opening=10_000_000 - cash if mode == 'cash_only' else 0))
    runtime.paid_post(case.post, ELEVENLABS_TTS_ROUTE, **request)
    before = case.client.hgetall(LEDGER_KEY)
    code = 'spend_funding_covered_limit' if mode == 'covered_only' else 'spend_funding_cash_limit'
    with pytest.raises(SpendBlocked, match='^' + code + '$'):
        runtime.paid_post(case.post, ELEVENLABS_TTS_ROUTE, **_request(voice_body, 'Another distinct narration.'))
    assert case.client.hgetall(LEDGER_KEY) == before
    case.post.assert_called_once()
    summary = case.ledger.funding_snapshot()
    assert summary['cash_remaining_micro'] == (10_000_000 if mode == 'covered_only' else 0)


@pytest.mark.parametrize('mode', ['covered_only', 'cash_only'])
def test_timeout_keeps_both_reservations_and_blocks_same_request_retry(case, voice_body, mode):
    case.ledger.initialize_funding(_policy(case, mode=mode))
    request = _request(voice_body)
    case.post.side_effect = httpx.ReadTimeout('offline transport timeout')
    with pytest.raises(httpx.ReadTimeout):
        runtime.paid_post(case.post, ELEVENLABS_TTS_ROUTE, **request)
    after_timeout = case.client.hgetall(LEDGER_KEY)
    assert case.ledger.snapshot()['period']['used_micro'] == _list_cost(request)
    receipt = _receipts(case)[_receipt_field(request)]
    assert receipt['funding']['cash_micro'] + receipt['funding']['covered_micro'] > 0
    with pytest.raises(SpendBlocked, match='^spend_request_already_reserved$'):
        runtime.paid_post(case.post, ELEVENLABS_TTS_ROUTE, **request)
    assert case.client.hgetall(LEDGER_KEY) == after_timeout
    case.post.assert_called_once()


def test_repair_child_cannot_buy_the_same_voice_take_again(case, voice_body):
    case.ledger.initialize_funding(_policy(case))
    request = _request(voice_body)
    runtime.paid_post(case.post, ELEVENLABS_TTS_ROUTE, **request)
    child = {**case.job, 'task_id': CHILD, 'parent_id': ROOT}
    case.client.set(runtime._JOB_PREFIX + CHILD, json.dumps(child))
    token = runtime._TASK_ID.set(CHILD)
    try:
        runtime.resolve_context(case.client, CHILD)
        before = case.client.hgetall(LEDGER_KEY)
        with pytest.raises(SpendBlocked, match='^spend_request_already_reserved$'):
            runtime.paid_post(case.post, ELEVENLABS_TTS_ROUTE, **request)
        assert case.client.hgetall(LEDGER_KEY) == before
    finally:
        runtime._TASK_ID.reset(token)
    case.post.assert_called_once()


def test_fingerprint_binds_format_and_actual_normalized_text(case, voice_body):
    case.ledger.initialize_funding(_policy(case))
    raw = 'QR KODU ve GPS açık'
    first = _request(voice_body, raw, flash=True)
    assert first['json']['text'] != raw
    second = _request(voice_body, raw + ' ve harita hazır', flash=True)
    for request in (first, second):
        runtime.paid_post(case.post, ELEVENLABS_TTS_ROUTE, **request)
    assert set(_receipts(case)) == {_receipt_field(first), _receipt_field(second)}
    hypothetical = deepcopy(first)
    hypothetical['json']['text'] = raw
    assert _receipt_field(hypothetical) not in _receipts(case)
    hypothetical = deepcopy(first)
    hypothetical['params'] = {}
    assert _receipt_field(hypothetical) not in _receipts(case)
    assert case.ledger.snapshot()['period']['used_micro'] == _list_cost(first) + _list_cost(second)
    assert case.post.call_count == 2


def test_caller_mutation_after_reservation_cannot_change_the_sent_voice_take(case, voice_body, monkeypatch):
    case.ledger.initialize_funding(_policy(case))
    request = _request(voice_body)
    frozen = deepcopy(request)
    real_reserve = case.ledger.reserve

    def reserve_then_mutate(**kwargs):
        receipt = real_reserve(**kwargs)
        request['json']['text'] = 'Unquoted replacement narration.'
        request['headers']['xi-api-key'] = 'unbound-replacement-key'
        request['params']['output_format'] = 'pcm_44100'
        return receipt

    monkeypatch.setattr(case.ledger, 'reserve', reserve_then_mutate)
    runtime.paid_post(case.post, ELEVENLABS_TTS_ROUTE, **request)
    case.post.assert_called_once_with(ELEVENLABS_TTS_ROUTE, **frozen)
    assert set(_receipts(case)) == {_receipt_field(frozen)}
    assert case.ledger.snapshot()['period']['used_micro'] == _list_cost(frozen)


def test_advancing_clock_expires_the_tariff_without_refresh_or_send(case, voice_body):
    case.evidence['valid_until'] = '2026-09-09T13:00:00Z'
    _configure(case, case.evidence)
    case.ledger.initialize_funding(_policy(case))
    case.clock[0] += timedelta(hours=1)
    before = case.client.hgetall(LEDGER_KEY)
    with pytest.raises(SpendBlocked, match='^spend_elevenlabs_evidence_expired$'):
        runtime.paid_post(case.post, ELEVENLABS_TTS_ROUTE, **_request(voice_body))
    assert case.client.hgetall(LEDGER_KEY) == before
    case.post.assert_not_called()
