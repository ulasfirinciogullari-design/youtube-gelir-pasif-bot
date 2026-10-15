"""Fal credentials, scene budgets and dispatch share one durable reservation."""
from datetime import datetime, timezone
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock

import fakeredis
import pytest

from app.services import production_spend_runtime as runtime, production_spend_quotes as quotes
from app.services import fal_video_catalog as catalog
from app.services.production_spend import SpendBlocked, SpendLedger, SpendPolicy
from test_production_spend_runtime import CHANNEL, ROOT, job
from test_fal_video_routing import reviewed_date

KEY = 'offline-fal-key'


@pytest.fixture
def funded(monkeypatch, reviewed_date):
    client = fakeredis.FakeRedis(decode_responses=True)
    now = datetime(2026, 9, 23, tzinfo=timezone.utc)
    ledger = SpendLedger(client, SpendPolicy(*([10_000_000] * 6)), clock=lambda: now)
    ledger.initialize()
    settings = SimpleNamespace(studio_spend_enforcement=True, studio_video_provider='fal')
    monkeypatch.setattr(runtime, 'settings', settings)
    monkeypatch.setattr(runtime, 'configured_ledger', lambda **_: ledger)
    monkeypatch.setattr(quotes, '_fresh', lambda: None)
    ledger.initialize_funding({
        'version': 1, 'currency': 'USD', 'month': '2026-09',
        'valid_from': '2026-09-23T00:00:00Z', 'valid_until': '2026-10-01T00:00:00Z',
        'cash_cap_micro': 10_000_000, 'opening_cash_micro': 0, 'reconciliation_sha256': 'a' * 64,
        'accounts': [{'provider': 'fal', 'account_sha256': 'b' * 64,
            'credential_sha256': hashlib.sha256(('fal\0' + KEY).encode()).hexdigest(),
            'evidence_sha256': 'c' * 64, 'valid_until': '2026-10-01T00:00:00Z',
            'mode': 'cash_only', 'funding': {'cash_factor_numerator': 1,
                'cash_factor_denominator': 1, 'cash_bound_verified': True},
            'routes': [{'route': catalog.ORIGIN + '/' + model, 'model': model,
                        'price_revision': catalog.PRICE_REVISION} for model in catalog.MODELS.values()]}],
    })
    client.sadd(runtime._CHANNEL_INDEX, CHANNEL)
    client.set(runtime._CHANNEL_PREFIX + CHANNEL, json.dumps({'id': CHANNEL, 'connection_id': 'connection_AAAAA'}))
    job(client)
    token = runtime._TASK_ID.set(ROOT)
    prepared = runtime.prepare_video_scene_budget('a' * 64, [4.5], '9:16', scene_count=1)
    try:
        with runtime.spending_scene(prepared, 0):
            yield ledger
    finally:
        runtime._TASK_ID.reset(token)


def send(sender, *, alias='veo_lite', key=KEY, body_change=None, headers_change=None):
    model = catalog.MODELS[alias]
    body = {**catalog.build_request(model, 'Approved shot', 5, '9:16'), **(body_change or {})}
    return runtime.paid_post(sender, catalog.ORIGIN + '/' + model, json=body,
        headers={'Authorization': 'Key ' + key, 'Content-Type': 'application/json',
                 'X-Fal-Request-Timeout': '570', **(headers_change or {})})


@pytest.mark.parametrize('alias,cost', [('veo_lite', 180000), ('seedance_pro', 130000),
                                     ('seedance_fast', 110000)])
def test_fal_reserves_before_send_and_refuses_duplicate(funded, alias, cost):
    def transport(*args, **kwargs):
        assert funded.snapshot()['period']['used_micro'] == cost
        return 'accepted'
    sender = Mock(side_effect=transport)
    assert send(sender, alias=alias) == 'accepted'
    with pytest.raises(SpendBlocked, match='already_reserved'):
        send(sender, alias=alias)
    assert sender.call_count == 1


@pytest.mark.parametrize('kwargs', [
    {'key': 'other-account'}, {'headers_change': {'Authorization': 'Bearer ' + KEY}},
    {'headers_change': {'authorization': 'Key ' + KEY}},
    {'headers_change': {'X-Fal-Request-Timeout': '99999'}},
    {'body_change': {'generate_audio': True}}, {'body_change': {'resolution': '1080p'}},
])
def test_unbound_or_unpriced_requests_never_spend(funded, kwargs):
    sender = Mock()
    with pytest.raises(SpendBlocked):
        send(sender, **kwargs)
    sender.assert_not_called()
    assert funded.snapshot()['period']['used_micro'] == 0


def test_unknown_failure_never_refunds_or_retries(funded):
    sender = Mock(side_effect=TimeoutError('unknown outcome'))
    with pytest.raises(TimeoutError):
        send(sender)
    with pytest.raises(SpendBlocked, match='already_reserved'):
        send(sender)
    assert sender.call_count == 1
    assert funded.snapshot()['period']['used_micro'] == 180000
