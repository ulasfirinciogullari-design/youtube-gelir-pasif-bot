"""Actual scheduler -> Celery task -> included ledger, with synthetic HTTP only."""
from copy import deepcopy
from datetime import timedelta
import json

import fakeredis
import pytest

from app.services import production_included_router as included
from app.services import production_next_series as planning, production_spend_runtime as runtime
from app.services import included_research_sources as sources
from app.services.production_credit_ledger import CreditLedger
from app.services.production_spend import LEDGER_KEY, SpendLedger, SpendPolicy
from test_production_series_spend import box, NOW, CHANNEL, enqueue, execute, snapshot
from spending_test_support import installed_sdk_modules
from test_production_credit_ledger import policy
from test_abacus_router_adapter import KEY, response, envelope
from test_production_included_router import request

URL1 = 'https://www.federalreserve.gov/a.htm'
URL2 = 'https://www.ecb.europa.eu/b.htm'


@pytest.fixture
def native(box, policy, monkeypatch):
    from app import config
    from app.services import abacus_router_review_runtime as transport, production_scheduler as scheduler
    # An independent synthetic store; never reset an existing financial ledger.
    store = fakeredis.FakeRedis(decode_responses=True)
    for key, value in snapshot(box.client).items():
        if key != LEDGER_KEY:
            store.restore(key, 0, value)
    box.client = store
    box.ledger = SpendLedger(store, SpendPolicy(0, 0, 0, 0, 0, 0), clock=lambda: NOW)
    box.ledger.initialize_cash_disabled_unknown_history(evidence_sha256='a' * 64,
        additional_monthly_limit_micro=10_000_000)
    box.settings.studio_abacus_included_production = True
    box.settings.studio_elevenlabs_native_credits = True
    box.settings.abacus_api_key = KEY
    box.settings.app_encryption_key = 'synthetic encryption secret with at least 32 characters'
    monkeypatch.setattr(config, 'settings', box.settings)
    monkeypatch.setattr(runtime, 'configured_ledger', lambda **kw: box.ledger)
    monkeypatch.setattr(scheduler, '_client', lambda: store)
    monkeypatch.setattr(planning.redis.Redis, 'from_url', lambda *a, **kw: store)
    monkeypatch.setattr(scheduler, '_now', lambda value: NOW.timestamp() if value is None else value)
    policy = deepcopy(policy)
    policy['valid_from'] = policy['balance']['observed_at'] = included._stamp(NOW)
    policy['valid_until'] = included._stamp(NOW + timedelta(days=1))
    CreditLedger(store, foundation=box.ledger, clock=lambda: NOW).initialize(policy)
    prepared = request()
    included.IncludedRouterLedger(box.ledger).initialize({
        'version': 1, 'kind': 'existing_subscription_included_router',
        'endpoint': prepared.endpoint, 'model': prepared.model,
        'credential_sha256': prepared.credential_sha256, 'owner_evidence_sha256': 'b' * 64,
        'terms_evidence_sha256': 'c' * 64,
        'valid_from': included._stamp(NOW), 'valid_until': included._stamp(NOW + timedelta(days=1)),
        'automatic_purchase_enabled': False, 'new_cash_allowance_micro': 0, 'historical_cash_micro': None,
        'allowed_channels': [CHANNEL], 'max_requests_per_lineage': 20, 'max_requests_per_day': 40})
    monkeypatch.setattr(sources, 'feed_candidates', lambda: [
        {'url': url, 'published_at': included._stamp(NOW)} for url in (URL1, URL2)])
    monkeypatch.setattr(sources, 'fetch_page', lambda url: {
        'url': url, 'text': 'Synthetic actually retrieved source text.', 'text_sha256': 'd' * 64})
    box.answer = {'can_prepare': True, 'language': 'en', 'series_title': 'Why Money Moves',
        'briefs': [{'brief': 'Why do central banks publish meeting decisions? ' + URL1 + ' ' + URL2,
            'sources': [{'url': url, 'evidence': 'Synthetic source describes publication of policy decisions.'}
                        for url in (URL1, URL2)]}]}
    box.calls = []
    def send(prepared):
        box.calls.append(prepared)
        assert len(json.loads(store.get(included.JOURNAL_KEY))['requests']) == 1
        body = envelope()
        body['choices'][0]['message']['content'] = json.dumps(box.answer)
        return response(prepared, payload=body)
    monkeypatch.setattr(transport, '_send_once', send)
    original = planning._generate
    box.errors = []
    def generate(*args):
        try:
            return original(*args)
        except Exception as error:
            import traceback
            box.errors.append(traceback.format_exc())
            raise
    monkeypatch.setattr(planning, '_generate', generate)
    return box


def test_scheduler_actual_series_task_uses_subscription_and_never_opens_cash(native):
    before = snapshot(native.client)
    assert runtime.preflight_scheduled_production(CHANNEL, kind='shorts') is not None
    assert snapshot(native.client) == before
    binding = enqueue(native)
    result = execute(binding)
    assert result['status'] == 'ready', (result, native.errors, len(native.calls))
    assert len(native.calls) == 1
    assert result['qa_approved'] is result['publish_eligible'] is result['media_budget_approved'] is False
    assert result['requires_full_research_and_critic'] is True
    assert native.ledger.snapshot()['historical_cash_micro'] is None
    assert native.ledger.snapshot()['cash_spending_enabled'] is False
    assert not any(key.startswith('period:') for key in native.client.hkeys(LEDGER_KEY))
    after = snapshot(native.client)
    execute(binding)
    assert snapshot(native.client) == after and len(native.calls) == 1


def test_source_not_retrieved_cannot_become_a_series(native):
    native.answer['briefs'][0]['sources'][1]['url'] = 'https://www.usmint.gov/not-consulted'
    result = execute(enqueue(native))
    assert result['status'] != 'ready' and len(native.calls) == 1


def test_scheduler_can_preflight_without_holding_a_model_key(native):
    native.settings.abacus_api_key = ''
    assert runtime.preflight_scheduled_production(CHANNEL, kind='shorts')['new_cash_allowance_micro'] == 0
    assert enqueue(native)['channel_id'] == CHANNEL
    assert native.calls == []
