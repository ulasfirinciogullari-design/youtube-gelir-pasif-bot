"""The free account reader and worker timer cannot send generation requests."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import hashlib
import json

import httpx
import pytest

from app.services import production_credit_renewal as renewal
from app.services import production_credit_periods as periods
from app.services import production_spend_runtime as runtime
from app.services.production_spend import SpendBlocked
from test_production_credit_periods import cycle
from test_production_credit_ledger import client, policy, NOW, intent, binding
from test_production_cash_disabled import dump

KEY, ACCOUNT = 'synthetic-key-never-live', 'synthetic-account-123'
HASH_KEY = hashlib.sha256(('elevenlabs\0' + KEY).encode()).hexdigest()
HASH_ACCOUNT = hashlib.sha256(('elevenlabs\0account\0' + ACCOUNT).encode()).hexdigest()


def account_body():
    return {'user_id': ACCOUNT, 'subscription': {'character_limit': 131000,
        'character_count': 50, 'next_character_count_reset_unix': 1760000000,
        'status': 'active', 'max_credit_limit_extension': 0, 'can_extend_character_limit': False}}


def mock_get(monkeypatch, body=None, *, status=200, raw=None):
    requests = []
    real_client = httpx.Client
    def response(request):
        requests.append(request)
        assert request.method == 'GET' and str(request.url) == renewal.ENDPOINT
        assert request.headers['xi-api-key'] == KEY
        assert not request.content
        return httpx.Response(status, json=body if body is not None else account_body()) if raw is None else httpx.Response(status, content=raw)
    def client(**kwargs):
        assert kwargs == {'timeout': 20, 'follow_redirects': False, 'trust_env': False}
        return real_client(transport=httpx.MockTransport(response), **kwargs)
    monkeypatch.setattr(renewal.httpx, 'Client', client)
    return requests


def read(**changes):
    return renewal.read_allowance(KEY, expected_account_sha256=HASH_ACCOUNT,
        expected_credential_sha256=HASH_KEY, clock=lambda: NOW, **changes)


def test_actual_get_projects_private_identity_and_does_not_claim_auto_topup_observation(monkeypatch):
    requests = mock_get(monkeypatch)
    observed = read()
    assert len(requests) == 1 and observed['account_sha256'] == HASH_ACCOUNT
    assert observed['credential_sha256'] == HASH_KEY and observed['quota_credits'] == 131000
    assert observed['used_credits'] == 50 and observed['max_credit_limit_extension'] == 0
    encoded = json.dumps(observed)
    assert KEY not in encoded and ACCOUNT not in encoded and 'auto_top_up' not in encoded


@pytest.mark.parametrize('field,value', [('character_count', True), ('character_limit', -1),
    ('max_credit_limit_extension', 'unlimited'), ('max_credit_limit_extension', True),
    ('can_extend_character_limit', True), ('status', 'past_due'),
    ('next_character_count_reset_unix', None), ('next_character_count_reset_unix', True)])
def test_bad_or_missing_account_fields_fail_closed(monkeypatch, field, value):
    body = account_body();body['subscription'][field] = value
    requests = mock_get(monkeypatch, body)
    with pytest.raises(SpendBlocked): read()
    assert len(requests) == 1


@pytest.mark.parametrize('status', [301, 401, 429, 500])
def test_rejection_redirect_and_rate_limit_have_no_automatic_get_retry(monkeypatch, status):
    requests = mock_get(monkeypatch, status=status)
    with pytest.raises(SpendBlocked): read()
    assert len(requests) == 1


@pytest.mark.parametrize('raw', [b'{"user_id":"a","user_id":"b"}', b'not JSON', b'x' * 2000001])
def test_malformed_duplicate_or_oversized_response_has_no_authority(monkeypatch, raw):
    requests = mock_get(monkeypatch, raw=raw)
    with pytest.raises(SpendBlocked): read()
    assert len(requests) == 1


def test_wrong_local_key_stops_before_network(monkeypatch):
    requests = mock_get(monkeypatch)
    with pytest.raises(SpendBlocked):
        renewal.read_allowance('another-synthetic-key', expected_account_sha256=HASH_ACCOUNT,
            expected_credential_sha256=HASH_KEY, clock=lambda: NOW)
    assert requests == []


def test_wrong_provider_account_is_never_accepted(monkeypatch):
    body = account_body();body['user_id'] = 'another-account-123'
    mock_get(monkeypatch, body)
    with pytest.raises(SpendBlocked): read()


@pytest.fixture
def timer(cycle, monkeypatch):
    monkeypatch.setattr(runtime, 'settings', SimpleNamespace(studio_spend_enforcement=True,
        studio_elevenlabs_native_credits=True, elevenlabs_api_key=KEY))
    monkeypatch.setattr(runtime, 'configured_ledger', lambda **kwargs: cycle.foundation)
    gets = []
    def observe(key, **kwargs):
        assert key == KEY and kwargs['expected_account_sha256'] == cycle.policy['account_sha256']
        assert kwargs['expected_credential_sha256'] == cycle.policy['credential_sha256']
        gets.append(1)
        return dict(cycle.evidence)
    monkeypatch.setattr(renewal, 'read_allowance', observe)
    return cycle, gets


def test_normal_unexpired_tick_is_read_only_and_never_contacts_provider(timer):
    cycle, gets = timer;cycle.now[0] = NOW
    before = dump(cycle.client)
    assert renewal.maintain_native_credit_period()['status'] == 'current'
    assert gets == [] and dump(cycle.client) == before


def test_expired_worker_tick_renews_once_and_second_tick_never_replays_get(timer):
    cycle, gets = timer
    assert renewal.maintain_native_credit_period()['status'] == 'renewed'
    assert len(gets) == 1
    before = dump(cycle.client)
    assert renewal.maintain_native_credit_period()['status'] == 'current'
    assert dump(cycle.client) == before and len(gets) == 1
    assert cycle.foundation.snapshot()['cash_spending_enabled'] is False


def test_provider_has_not_reset_yet_preserves_old_ledger_and_throttles_only_get(timer):
    cycle, gets = timer
    cycle.evidence['provider_reset_at'] = cycle.policy['balance']['provider_reset_at']
    before = periods.renewal_snapshot(cycle.ledger)
    assert renewal.maintain_native_credit_period()['status'] == 'blocked'
    assert periods.renewal_snapshot(cycle.ledger) == before
    assert renewal.maintain_native_credit_period()['status'] == 'waiting_for_account_check'
    assert len(gets) == 1 and not cycle.client.exists(periods.HISTORY_KEY)


def test_unknown_reservation_does_not_even_query_for_a_new_allowance(timer):
    cycle, gets = timer
    cycle.now[0] = NOW + timedelta(minutes=1)
    cycle.ledger.reserve(intent=intent(2), production_context=cycle.context, **binding(cycle.policy))
    cycle.now[0] = datetime(2026, 9, 25, tzinfo=timezone.utc)
    before = dump(cycle.client)
    assert renewal.maintain_native_credit_period() == {'status': 'blocked', 'reason': 'credit_period_has_uncertain_usage'}
    assert dump(cycle.client) == before and gets == []
