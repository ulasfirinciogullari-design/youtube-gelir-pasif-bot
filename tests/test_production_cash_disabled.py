"""Unknown cash history stays unknown while separately funded native credits run."""
from dataclasses import replace
from datetime import timedelta
import json

import pytest
from redis.exceptions import ConnectionError

from app.services import production_cash_disabled as disabled
from app.services.production_spend import LEDGER_KEY, SpendBlocked, SpendLedger, SpendPolicy, SpendQuote
from app.services.production_credit_ledger import CreditLedger
from test_production_credit_ledger import (
    client, policy, NOW, InterceptClient, binding, intent, observation, context,
)


def money(client, *, initialize=True):
    ledger = SpendLedger(client, SpendPolicy(0, 0, 0, 0, 0, 0), clock=lambda: NOW)
    if initialize:
        ledger.initialize_cash_disabled_unknown_history(evidence_sha256='a' * 64,
                                                       additional_monthly_limit_micro=10_000_000)
    return ledger


def dump(client):
    return {key: client.dump(key) for key in client.scan_iter()}


def test_unknown_cash_never_becomes_an_empty_paid_period(client):
    ledger = money(client)
    before = dump(client)
    summary = ledger.snapshot()
    assert summary['period'] is None and summary['historical_cash_micro'] is None
    assert summary['remaining_micro'] == 0 and summary['new_cash_allowance_micro'] == 0
    assert summary['cash_spending_enabled'] is False
    assert ledger.funding_snapshot()['additional_monthly_limit_micro'] == 10_000_000
    assert all(not key.startswith('period:') for key in client.hkeys(LEDGER_KEY))
    with pytest.raises(SpendBlocked, match='spend_cash_disabled_history_unknown'):
        ledger.initialize()
    with pytest.raises(SpendBlocked, match='spend_cash_disabled_history_unknown'):
        ledger.check_dispatch_capacity(channel_id='channel-0001', kind='shorts')
    for provider in ('openai', 'gemini', 'abacus', 'elevenlabs'):
        with pytest.raises(SpendBlocked, match='spend_cash_disabled_history_unknown'):
            ledger.reserve(request_key='request-0001', channel_id='channel-0001',
                           lineage_id='lineage-0001', kind='shorts',
                           quote=SpendQuote(provider, 'synthetic', 1, 'fixture-v1'))
    assert dump(client) == before
    assert money(client).snapshot() == summary


def test_real_native_reserve_and_settle_work_without_cash_reconciliation(client, policy):
    foundation = money(client)
    live = CreditLedger(client, foundation=foundation, clock=lambda: NOW)
    assert live.initialize(policy) is True
    bound = context(client)
    receipt = live.reserve(intent=intent(), production_context=bound, **binding(policy))
    assert live.summary()['reserved_credits'] == 1000
    assert foundation.snapshot()['historical_cash_micro'] is None
    live.settle(observation=observation(policy, receipt), **binding(policy))
    summary = live.summary()
    assert summary['spent_credits'] == 248 and summary['available_credits'] == 752
    before = dump(client)
    with pytest.raises(SpendBlocked):
        live.reserve(intent=intent(), production_context=bound, **binding(policy))
    with pytest.raises(SpendBlocked, match='credit_foundation_required'):
        CreditLedger(client, clock=lambda: NOW).summary()
    assert dump(client) == before
    assert foundation.snapshot()['historical_cash_micro'] is None


def test_lost_initialization_ack_cannot_create_a_second_opening(client):
    def lost(number, result):
        raise ConnectionError('synthetic lost ACK')
    wrapped = InterceptClient(client, after=lost)
    with pytest.raises(SpendBlocked, match='cash_disabled_initialization_unverified'):
        money(wrapped)
    before = dump(client)
    assert money(client, initialize=False).initialize_cash_disabled_unknown_history(
        evidence_sha256='a' * 64, additional_monthly_limit_micro=10_000_000) is False
    assert dump(client) == before
    with pytest.raises(SpendBlocked):
        money(client, initialize=False).initialize()


@pytest.mark.parametrize('damage', ['ledger_missing', 'anchor_missing', 'invented_period', 'changed_evidence', 'changed_policy'])
def test_missing_or_conflicting_evidence_never_reopens_credit_or_cash(client, policy, damage):
    foundation = money(client)
    live = CreditLedger(client, foundation=foundation, clock=lambda: NOW)
    live.initialize(policy)
    if damage == 'ledger_missing':
        client.delete(LEDGER_KEY)
    elif damage == 'anchor_missing':
        client.delete(disabled.ANCHOR_KEY)
    elif damage == 'invented_period':
        client.hset(LEDGER_KEY, 'period:2026-09', json.dumps({'used_micro': 0}))
    elif damage == 'changed_evidence':
        record = json.loads(client.hget(LEDGER_KEY, disabled.FIELD))
        record['historical_cash_micro'] = 0
        client.hset(LEDGER_KEY, disabled.FIELD, json.dumps(record))
    else:
        foundation.policy = replace(foundation.policy, monthly_micro=1)
    before = dump(client)
    for operation in (live.summary, foundation.snapshot, foundation.initialize,
                      lambda: foundation.initialize_cash_disabled_unknown_history(
                          evidence_sha256='a' * 64, additional_monthly_limit_micro=10_000_000)):
        with pytest.raises(SpendBlocked):
            operation()
        assert dump(client) == before


def test_all_cash_limits_must_stay_zero_and_expiry_does_not_clear_native_debt(client, policy):
    bad = SpendLedger(client, SpendPolicy(1, 1, 1, 1, 1, 1), clock=lambda: NOW)
    with pytest.raises(SpendBlocked, match='cash_disabled_zero_policy_required'):
        bad.initialize_cash_disabled_unknown_history(evidence_sha256='a' * 64,
                                                     additional_monthly_limit_micro=10_000_000)
    assert client.dbsize() == 0
    foundation = money(client)
    live = CreditLedger(client, foundation=foundation, clock=lambda: NOW)
    live.initialize(policy)
    live.reserve(intent=intent(), production_context=context(client), **binding(policy))
    before = dump(client)
    later = CreditLedger(client, foundation=foundation, clock=lambda: NOW + timedelta(days=1))
    with pytest.raises(SpendBlocked, match='credit_policy_expired'):
        later.summary()
    assert dump(client) == before
