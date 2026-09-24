"""Queue transport and prepaid accounting exercised together without billing."""
import json
from types import SimpleNamespace

import httpx
import pytest

from app.services import kie_voice_adapter as api, kie_voice_ledger as ledger
from app.services import kie_credentials, production_continuation as continuation
from app.services.production_spend import SpendBlocked, LEDGER_KEY
from test_commissioning_audio import box
from test_whisper_transcription import CHANNEL, KEY, NOW

VOICE = 'EkK5I93UQWFDigLMpZcX'
TASK = 'kie_task_000000001'


@pytest.fixture
def kie(box, monkeypatch):
    monkeypatch.setattr(kie_credentials, 'settings', box.config)
    monkeypatch.setattr(ledger, 'CHANNELS', frozenset({CHANNEL}))
    kie_credentials.save(box.client, KEY)
    continuation.initialize(box.client, {'version': 1, 'kind': 'continuous_commissioning',
        'allowed_channels': [CHANNEL], 'authorized_at': NOW.isoformat(), 'owner_evidence_sha256': 'c' * 64})
    box.requests = []
    box.balance = 80
    def handler(request):
        box.requests.append(request)
        if request.url.path.endswith('/credit'):
            assert request.method == 'GET'
            return httpx.Response(200, json={'code': 200, 'data': box.balance})
        if request.method == 'POST':
            rows = json.loads(box.client.get(ledger.JOURNAL_KEY))['requests']
            assert rows and any(row['create'] is None for row in rows.values())
            assert request.headers['Authorization'] == 'Bearer ' + KEY
            return httpx.Response(200, json={'code': 200, 'data': {'taskId': TASK}})
        return httpx.Response(200, json={'code': 200, 'data': {
            'taskId': TASK, 'model': api.TURBO, 'state': 'success', 'creditsConsumed': 0.5,
            'resultJson': json.dumps({'resultUrls': ['https://tempfile.aiquickdraw.com/test/voice.mp3']})}})
    box.handler = handler
    original = httpx.Client
    def client(**kwargs):
        assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
        kwargs['transport'] = httpx.MockTransport(lambda request: box.handler(request))
        return original(**kwargs)
    monkeypatch.setattr(api.httpx, 'Client', client)
    box.scope = {'kind': 'connection_probe', 'language': 'tr', 'voice_id': VOICE}
    return box


def commission(kie):
    return ledger.commission(kie.foundation, owner_authorization_sha256='a' * 64)


def generate(kie, text='Bu bir ses denemesidir.', **changes):
    body, ceiling = api.request_body(text, kie.scope['voice_id'], turkish=True)
    journal = ledger.Journal(kie.foundation, kie.scope, body, ceiling, **changes)
    return api.generate(body, KEY, journal, sleep=lambda _: None)


def test_fresh_balance_allocation_preserves_other_providers_and_never_auto_activates(kie):
    before = kie.client.hgetall(LEDGER_KEY)
    assert commission(kie)['allocation_microcredits'] == 80_000_000
    assert [r.method for r in kie.requests] == ['GET']
    assert ledger.status(kie.client)['status'] == 'validation_pending'
    assert kie.client.hgetall(LEDGER_KEY) == before
    assert not kie.client.exists(ledger.ACTIVE_KEY)
    with pytest.raises(SpendBlocked, match='already_allocated'):
        commission(kie)


def test_paid_request_reserved_once_receipts_reused_and_actual_charge_recorded(kie):
    commission(kie)
    before = kie.client.hgetall(LEDGER_KEY)
    first = generate(kie)
    assert generate(kie) == first and first['task_id'] == TASK
    assert [r.method for r in kie.requests] == ['GET', 'POST', 'GET']
    status = ledger.status(kie.client)
    assert status['committed_microcredits'] == 500000 and status['requests'] == 1
    assert status['unknown_or_pending'] == 0 and status['remaining_microcredits'] == 79500000
    assert KEY not in kie.client.get(ledger.JOURNAL_KEY)
    assert 'Bu bir' not in kie.client.get(ledger.JOURNAL_KEY)
    assert kie.client.hgetall(LEDGER_KEY) == before


def test_unknown_paid_request_stays_reserved_and_cannot_be_rebought(kie):
    commission(kie)
    def timeout(request):
        kie.requests.append(request)
        raise httpx.ReadTimeout('unknown outcome')
    kie.handler = timeout
    with pytest.raises(api.KieVoiceError, match='outcome_unknown'):
        generate(kie)
    with pytest.raises(SpendBlocked, match='previous_outcome_unknown'):
        generate(kie)
    status = ledger.status(kie.client)
    assert status['committed_microcredits'] == 6000000 and status['unknown_or_pending'] == 1
    assert [r.method for r in kie.requests] == ['GET', 'POST']


def test_accepted_request_only_polls_after_read_outage(kie):
    commission(kie)
    original = kie.handler
    def outage(request):
        if request.url.path.endswith('/recordInfo'):
            kie.requests.append(request)
            raise httpx.ReadTimeout('offline')
        return original(request)
    kie.handler = outage
    with pytest.raises(api.KieVoiceError, match='accepted_task_pending'):
        generate(kie)
    kie.handler = original
    assert generate(kie)['task_id'] == TASK
    assert [r.method for r in kie.requests].count('POST') == 1


def test_observed_charge_above_reservation_is_retained_and_exhausts_allocation(kie):
    commission(kie)
    original = kie.handler
    def overrun(request):
        response = original(request)
        if request.url.path.endswith('/recordInfo'):
            data = response.json(); data['data']['creditsConsumed'] = 81
            return httpx.Response(200, json=data)
        return response
    kie.handler = overrun
    generate(kie)
    assert ledger.status(kie.client)['committed_microcredits'] == 81000000
    assert ledger.status(kie.client)['remaining_microcredits'] == 0
    kie.scope = {**kie.scope, 'voice_id': 'hpp4J3VqNfWAUOO0d1Us'}
    with pytest.raises(SpendBlocked, match='balance_exhausted'):
        generate(kie)


@pytest.mark.parametrize('damage', ['journal', 'anchor', 'credential', 'ttl'])
def test_partial_or_tampered_ledger_never_sends(kie, damage):
    commission(kie)
    if damage == 'journal':
        kie.client.delete(ledger.JOURNAL_KEY)
    if damage == 'anchor':
        kie.client.set(ledger.ANCHOR_KEY, 'f' * 64)
    if damage == 'credential':
        kie.client.delete(kie_credentials.KEY)
    if damage == 'ttl':
        kie.client.expire(ledger.JOURNAL_KEY, 300)
    with pytest.raises(SpendBlocked):
        generate(kie)
    assert all(r.method == 'GET' for r in kie.requests)


def test_changed_body_or_attempt_cannot_rebuy_a_connection_probe(kie):
    commission(kie); generate(kie)
    with pytest.raises(SpendBlocked, match='attempt_limit'):
        generate(kie, text='Değişmiş bir ses denemesi.')
    with pytest.raises(SpendBlocked, match='attempt_limit'):
        generate(kie, attempt=1)
    assert [r.method for r in kie.requests].count('POST') == 1


@pytest.mark.parametrize('data', [True, None, -1, 'NaN', 'Infinity', '0.0000001'])
def test_invalid_credit_observation_is_not_funding(data):
    with pytest.raises(api.KieVoiceError):
        api.microcredits(data)


@pytest.mark.parametrize('text', ['', ' ' * 20, 'x' * 5001, '😀' * 2501])
def test_model_input_is_bounded_before_any_paid_request(text):
    with pytest.raises(api.KieVoiceError):
        api.request_body(text, VOICE)


def test_wrong_task_or_model_result_is_not_accepted(kie):
    commission(kie)
    original = kie.handler
    def wrong(request):
        response = original(request)
        if request.url.path.endswith('/recordInfo'):
            data = response.json(); data['data']['taskId'] = 'different_task'
            return httpx.Response(200, json=data)
        return response
    kie.handler = wrong
    with pytest.raises(api.KieVoiceError, match='binding_invalid'):
        generate(kie)
    assert ledger.status(kie.client)['unknown_or_pending'] == 1
