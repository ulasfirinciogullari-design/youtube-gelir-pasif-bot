"""Recover actual captured JSON without transport, credit reuse or a QA permit."""
from copy import deepcopy
import hashlib
import json

import pytest

from app.services import abacus_router_audio_adapter as adapter
from app.services import production_included_router as included, production_prepaid_audio as prepaid
from app.services import production_spend_runtime as runtime, production_included_transport as transport
from app.services.production_spend import SpendBlocked
from test_production_prepaid_audio import audio_ledger, commissioned, client, prepare, CONTEXT
from test_production_credit_ledger import InterceptClient
from test_production_cash_disabled import dump
from test_abacus_router_audio_adapter import ASR, envelope, response


def captured(ledger, prepared, *, status=200, content=None):
    identity, prior = ledger.reserve(CONTEXT, 'blind_asr', prepared)
    assert prior is None
    payload = envelope(model=prepared.model)
    payload['choices'][0]['native_finish_reason'] = 'STOP'
    payload['choices'][0]['message']['content'] = content or '```json\n' + json.dumps(ASR) + '\n```'
    actual = response(prepared, payload=payload, status=status)
    ledger.record_failure(identity, prepared, actual, adapter.AbacusRouterAudioError('parse failed'))
    key = ledger.prefix + 'failure:' + identity
    raw = ledger.client.get(key)
    return identity, key, raw, hashlib.sha256(raw.encode()).hexdigest()


@pytest.mark.parametrize('fence', ['```json\n{}\n```', '```\n{}\n```', '  ```json\r\n{}\r\n```\n'])
def test_complete_single_fence_preserves_full_output_and_real_wire_proof(fence):
    prepared = prepare()
    body = envelope(model=prepared.model)
    body['choices'][0]['message']['content'] = fence.format(json.dumps(ASR))
    observed = adapter.observe_audio_router_response(prepared, response(prepared, payload=body))
    assert observed.result == ASR
    assert observed.evidence['wire_body_sha256'] is not None
    assert 'observation_kind' not in observed.evidence


@pytest.mark.parametrize('content', [
    'Here is the result:\n```json\n{}\n```', '```json\n{}\n```\nDone.',
    '```json\n{}\n```\n```json\n{}\n```', '```javascript\n{}\n```',
    '```json\n{"text":"one","text":"two"}\n```', '```json\n{"text":NaN}\n```',
    '```json\n{"text":"Merhaba dünya.","language":"xx","words":[]}\n```',
])
def test_fences_do_not_allow_extraction_ambiguous_json_or_missing_contract(content):
    prepared = prepare()
    body = envelope(model=prepared.model)
    body['choices'][0]['message']['content'] = content
    with pytest.raises(SpendBlocked):adapter.observe_audio_router_response(prepared, response(prepared, payload=body))


def test_recovered_failure_keeps_cost_count_and_provenance_then_runtime_cache_avoids_send(audio_ledger, monkeypatch):
    ledger, route, _ = audio_ledger
    prepared = prepare()
    identity, key, raw, digest = captured(ledger, prepared)
    original_router = ledger.client.get(included.JOURNAL_KEY)
    receipt = ledger.recover_saved_response(CONTEXT, 'blind_asr', prepared, failure_record_sha256=digest)
    assert included._result(prepared, receipt) == ASR
    ev = receipt['evidence']
    assert ev['observation_kind'] == 'stored_encrypted_failure_v1'
    assert ev['wire_body_sha256'] is None and ev['response_headers_verified'] is False
    assert ev['failure_record_sha256'] == digest and 'qa_approved' not in ev
    assert ledger.client.get(key) == raw
    assert len(json.loads(ledger.client.get(prepaid.JOURNAL_KEY))['requests']) == 1
    assert ledger.client.get(included.JOURNAL_KEY) == original_router
    for name, value in {'studio_spend_enforcement': True, 'studio_abacus_included_production': True,
                        'studio_abacus_prepaid_audio': True}.items():
        monkeypatch.setattr(runtime.settings, name, value, raising=False)
    monkeypatch.setattr(runtime, 'configured_ledger', lambda **kw: ledger.foundation)
    monkeypatch.setattr(runtime, 'resolve_context', lambda *a: deepcopy(CONTEXT))
    monkeypatch.setattr(transport, 'send_once', lambda *a: pytest.fail('Recovered response must be reused'))
    assert included._generate(prepared, 'blind_asr', adapter.observe_audio_router_response) == ASR
    assert ledger.foundation.snapshot()['cash_spending_enabled'] is False
    before = dump(ledger.client)
    with pytest.raises(SpendBlocked):ledger.recover_saved_response(CONTEXT, 'blind_asr', prepared, failure_record_sha256=digest)
    assert dump(ledger.client) == before


@pytest.mark.parametrize('drift', ['pin', 'record', 'delete', 'ttl', 'body', 'status', 'key', 'time', 'channel', 'timing'])
def test_capture_recovery_rejects_drift_without_writes_or_capacity_release(audio_ledger, drift):
    ledger, _, _ = audio_ledger
    prepared = prepare()
    invalid = deepcopy(ASR)
    invalid['words'][0]['end'] = 12
    identity, key, raw, digest = captured(ledger, prepared,
        content='```json\n' + json.dumps(invalid) + '\n```' if drift == 'timing' else None)
    value = json.loads(raw)
    if drift == 'pin':digest = '0' * 64
    elif drift == 'record':ledger.client.set(key, raw + ' ')
    elif drift == 'delete':ledger.client.delete(key)
    elif drift == 'ttl':ledger.client.expire(key, 60)
    elif drift == 'channel':
        ledger.client.set(runtime._CHANNEL_PREFIX + CONTEXT['channel_id'], json.dumps({'id': CONTEXT['channel_id'], 'connection_id': 'another-owner'}))
    elif drift in ('body', 'status', 'key', 'time'):
        value[{'body': 'response_sha256', 'status': 'http_status', 'key': 'credential_sha256', 'time': 'observed_at'}[drift]] = {
            'body': '0' * 64, 'status': 400, 'key': '0' * 64, 'time': '2099-01-01T00:00:00Z'}[drift]
        changed = included._raw(value)
        ledger.client.set(key, changed);digest = hashlib.sha256(changed.encode()).hexdigest()
    before = dump(ledger.client)
    with pytest.raises(SpendBlocked):ledger.recover_saved_response(CONTEXT, 'blind_asr', prepared, failure_record_sha256=digest)
    assert dump(ledger.client) == before
    assert json.loads(ledger.client.get(prepaid.JOURNAL_KEY))['requests'][identity]['outcome'] is None


def test_lost_recovery_ack_keeps_single_observation_and_never_releases_slot(audio_ledger):
    from redis.exceptions import ConnectionError
    ledger, _, _ = audio_ledger
    prepared = prepare()
    identity, key, raw, digest = captured(ledger, prepared)
    def lost(number, result):raise ConnectionError('reply lost after commit')
    ledger.client = InterceptClient(ledger.client, after=lost)
    with pytest.raises(SpendBlocked):ledger.recover_saved_response(CONTEXT, 'blind_asr', prepared, failure_record_sha256=digest)
    ledger.client = ledger.foundation.client
    journal = json.loads(ledger.client.get(prepaid.JOURNAL_KEY))
    assert len(journal['requests']) == 1 and journal['requests'][identity]['outcome'] is not None
    assert ledger.client.get(key) == raw
    with pytest.raises(SpendBlocked):ledger.recover_saved_response(CONTEXT, 'blind_asr', prepared, failure_record_sha256=digest)
