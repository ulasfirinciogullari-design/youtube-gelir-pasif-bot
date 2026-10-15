"""Native reasoning uses real Redis admission and observed HTTP-shaped results."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from unittest.mock import Mock

import httpx
import pytest

from app.services import commissioning_reasoning as native, production_continuation as continuation
from app.services import production_included_router as included, production_spend_runtime as runtime
from app.services.production_spend import SpendBlocked, LEDGER_KEY
from app.services.abacus_router_adapter import observe_router_response
from test_production_included_router import commissioned, client, request, CHANNEL, CONTEXT
from test_abacus_router_adapter import KEY, RESULT, response
from test_production_cash_disabled import dump

NOW = datetime(2026, 9, 22, tzinfo=timezone.utc)


def payload(result=None, **changes):
    return {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [
        {'text': json.dumps(RESULT if result is None else result)}]}}],
        'modelVersion': native.MODEL, 'usageMetadata': {
            'promptTokenCount': 1200, 'candidatesTokenCount': 100, 'thoughtsTokenCount': 40}, **changes}


@pytest.fixture
def setup(commissioned, monkeypatch):
    ledger, policy, prepared = commissioned
    for key, value in {'studio_spend_enforcement': True, 'studio_abacus_included_production': True,
                       'studio_commissioning_reasoning': True, 'gemini_api_key': KEY}.items():
        monkeypatch.setattr(runtime.settings, key, value, raising=False)
    ledger.foundation.clock = lambda: NOW
    continuation.initialize(ledger.client, {'version': 1, 'kind': 'continuous_commissioning',
        'allowed_channels': [CHANNEL], 'authorized_at': NOW.isoformat(), 'owner_evidence_sha256': 'c' * 64})
    sender = Mock(return_value=(200, json.dumps(payload()).encode()))
    monkeypatch.setattr(native, '_send', sender)
    return ledger, prepared, sender


def run(setup, prepared=None, purpose='editorial'):
    ledger, default, _ = setup
    return native.generate(prepared or default, purpose, ledger, ledger.foundation, CONTEXT)


def records(setup):
    c = setup[0].client
    return [json.loads(c.get(key)) for key in c.scan_iter(match=native.PREFIX + 'request:*')]


def test_reserves_native_route_before_http_and_replays_without_touching_legacy(setup):
    ledger, _, sender = setup
    before = dump(ledger.client)
    def send(body):
        rows = records(setup)
        assert len(rows) == 1 and rows[0]['max_list_cost_micro_usd'] == 817152
        assert rows[0]['provider'] == 'gemini' and rows[0]['model'] == native.MODEL
        assert body['generationConfig']['responseJsonSchema']
        assert not {'tools', 'cachedContent', 'store'} & set(body)
        return 200, json.dumps(payload()).encode()
    sender.side_effect = send
    assert run(setup) == RESULT and run(setup) == RESULT
    sender.assert_called_once()
    after = dump(ledger.client)
    assert all(after[key] == value for key, value in before.items())
    observed = included._LAST_OBSERVED.get()
    assert observed['evidence']['observed_list_cost_micro_usd'] == 1425
    assert observed['evidence']['cost_basis'] == 'standard_list_estimate_not_invoice'
    assert all(KEY not in json.dumps(row) and row['historical_cash_micro'] is None for row in records(setup))


def test_unknown_http_outcome_never_repeats_request(setup):
    setup[2].side_effect = httpx.ReadTimeout('private request detail')
    with pytest.raises(SpendBlocked, match='outcome_unknown'): run(setup)
    with pytest.raises(SpendBlocked, match='previous_outcome_unknown'): run(setup)
    setup[2].assert_called_once()
    assert len(records(setup)) == 1


@pytest.mark.parametrize('damage', ['wrong_model', 'false_finish', 'missing_usage', 'extra_output',
                                   'wrong_schema', 'http_quota', 'overflow_usage', 'duplicate_key'])
def test_invalid_response_remains_recorded_and_cannot_trigger_new_http(setup, damage):
    p = payload()
    if damage == 'wrong_model': p['modelVersion'] = 'unapproved-model'
    if damage == 'false_finish': p['candidates'][0]['finishReason'] = 'MAX_TOKENS'
    if damage == 'missing_usage': p.pop('usageMetadata')
    if damage == 'extra_output': p['candidates'][0]['content']['parts'].append({'text': '{}'})
    if damage == 'wrong_schema': p['candidates'][0]['content']['parts'][0]['text'] = '{}'
    if damage == 'overflow_usage': p['usageMetadata']['promptTokenCount'] = native.MAX_INPUT + 1
    if damage == 'duplicate_key': p['candidates'][0]['content']['parts'][0]['text'] = '{"pass":true,"pass":false}'
    setup[2].return_value = (429 if damage == 'http_quota' else 200, json.dumps(p).encode())
    for _ in range(2):
        with pytest.raises(SpendBlocked, match='response_unverified'): run(setup)
    setup[2].assert_called_once()
    assert len(records(setup)) == 1


@pytest.mark.parametrize('damage', ['authority_off', 'wrong_connection', 'reconnect', 'expired_price', 'enforcement_off'])
def test_changed_authority_never_reaches_http(setup, monkeypatch, damage):
    ledger, _, sender = setup
    if damage == 'authority_off': ledger.client.delete(continuation.ACTIVE_KEY)
    if damage in {'wrong_connection', 'reconnect'}:
        c = json.loads(ledger.client.get(runtime._CHANNEL_PREFIX + CHANNEL))
        c['connection_id' if damage == 'wrong_connection' else 'requires_reconnect'] = (
            'different-connection' if damage == 'wrong_connection' else True)
        ledger.client.set(runtime._CHANNEL_PREFIX + CHANNEL, json.dumps(c))
    if damage == 'expired_price': ledger.foundation.clock = lambda: native.PRICE_UNTIL
    if damage == 'enforcement_off': monkeypatch.setattr(runtime.settings, 'studio_spend_enforcement', False)
    with pytest.raises(SpendBlocked): run(setup)
    sender.assert_not_called()
    assert records(setup) == []


def test_disabled_native_switch_preserves_original_router_path(setup, monkeypatch):
    monkeypatch.setattr(runtime.settings, 'studio_commissioning_reasoning', False)
    assert run(setup) is native.UNHANDLED
    setup[2].assert_not_called()


def test_old_unknown_request_is_not_bought_again_from_another_provider(setup):
    ledger, prepared, sender = setup
    # Old fixture policy is still valid at its own original clock.
    current = ledger.foundation.clock
    ledger.foundation.clock = ledger.clock
    ledger.reserve(CONTEXT, 'editorial', prepared)
    ledger.foundation.clock = current
    before = dump(ledger.client)
    with pytest.raises(SpendBlocked, match='legacy_outcome_unknown'): run(setup)
    assert dump(ledger.client) == before
    sender.assert_not_called()


def test_old_completed_result_keeps_its_original_provenance(setup):
    ledger, prepared, sender = setup
    current = ledger.foundation.clock
    ledger.foundation.clock = ledger.clock
    identity, _ = ledger.reserve(CONTEXT, 'editorial', prepared)
    ledger.settle(identity, prepared, observe_router_response(prepared, response(prepared)))
    ledger.foundation.clock = current
    before = dump(ledger.client)
    assert run(setup) == RESULT and dump(ledger.client) == before
    assert included._LAST_OBSERVED.get()['evidence']['credential_sha256'] == prepared.credential_sha256
    sender.assert_not_called()


def test_lost_request_record_cannot_reset_a_used_identity(setup):
    run(setup)
    client = setup[0].client
    key = next(client.scan_iter(match=native.PREFIX + 'request:*'))
    client.delete(key)
    with pytest.raises(SpendBlocked): run(setup)
    setup[2].assert_called_once()


def test_per_lineage_capacity_counts_unknown_requests(setup, monkeypatch):
    monkeypatch.setattr(native, 'MAX_LINEAGE', 1)
    setup[2].side_effect = httpx.ReadTimeout('unknown')
    with pytest.raises(SpendBlocked): run(setup)
    with pytest.raises(SpendBlocked, match='capacity'): run(setup, request('Second distinct attempt'))
    setup[2].assert_called_once()


def test_prosody_preserves_actual_audio_bytes_and_complete_schema(setup):
    from app.services.abacus_router_audio_adapter import prepare_prepaid_prosody_request
    from app.services.audio_qc import _PROSODY_REVIEW_SCHEMA, _PROSODY_SYSTEM_INSTRUCTION
    from test_abacus_router_audio_adapter import mp3
    audio = mp3()
    prepared = prepare_prepaid_prosody_request(audio, api_key=KEY, language='en',
        expected_narration='The product changed.', system_instruction=_PROSODY_SYSTEM_INSTRUCTION,
        json_schema=_PROSODY_REVIEW_SCHEMA)
    body, schema, _ = native._request(prepared, 'prosody')
    import base64
    assert base64.b64decode(body['contents'][0]['parts'][0]['inlineData']['data']) == audio
    assert body['contents'][0]['parts'][0]['inlineData']['mimeType'] == 'audio/mpeg'
    assert schema == _PROSODY_REVIEW_SCHEMA


def test_native_transport_uses_one_exact_authorized_endpoint(setup, monkeypatch):
    # Exercise the real HTTP boundary independently of the result tests' fake.
    import importlib
    actual_send = importlib.reload(native)._send
    real_client = httpx.Client
    calls = []
    def handle(r):
        calls.append(r)
        assert str(r.url) == native.ENDPOINT and r.method == 'POST'
        assert r.headers['x-goog-api-key'] == KEY
        assert not r.headers.get('authorization')
        return httpx.Response(200, stream=httpx.ByteStream(json.dumps(payload()).encode()))
    def client(**kwargs):
        assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
        return real_client(**{**kwargs, 'transport': httpx.MockTransport(handle)})
    monkeypatch.setattr(native.httpx, 'Client', client)
    body, _, _ = native._request(setup[1], 'editorial')
    status, raw = actual_send(body)
    assert status == 200 and json.loads(raw) == payload() and len(calls) == 1


def test_committed_response_survives_lost_capture_ack_without_resending(setup, monkeypatch):
    client = setup[0].client
    real_set = client.set
    lost = []
    def set_once(key, value, **kwargs):
        result = real_set(key, value, **kwargs)
        if key.startswith(native.PREFIX + 'response:') and not lost:
            lost.append(True)
            raise ConnectionError('lost local acknowledgement')
        return result
    monkeypatch.setattr(client, 'set', set_once)
    with pytest.raises(SpendBlocked, match='outcome_unknown'): run(setup)
    assert run(setup) == RESULT
    setup[2].assert_called_once()
