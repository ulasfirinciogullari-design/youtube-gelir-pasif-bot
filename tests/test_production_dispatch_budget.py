"""Real ledger reads and queue admission; no provider calls or live Redis."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from unittest.mock import Mock

import fakeredis
import pytest

from app.services.production_spend import LEDGER_KEY, SpendBlocked, SpendLedger, SpendPolicy, SpendQuote
from app.services import production_spend_runtime as runtime
from spending_test_support import test_funding_policy as policy_fixture
from test_channel_production import production, _profile, _save, CHANNEL, CONNECTION
from test_retained_review_credential_successor import Intercept


def snapshot(client):
    return {key: client.dump(key) for key in client.scan_iter('*')}


def setup(*, policy=None, client=None, initialize=True, funding=True, cash_used=0, covered=None):
    client = client if client is not None else fakeredis.FakeRedis(decode_responses=True)
    clock = [datetime(2026, 9, 20, 12, tzinfo=timezone.utc)]
    ledger = SpendLedger(client, policy or SpendPolicy(*([100] * 6)), clock=lambda: clock[0])
    if initialize:
        ledger.initialize()
    fund = policy_fixture(ledger)
    fund.update(cash_cap_micro=100, opening_cash_micro=cash_used)
    fund['accounts'] = fund['accounts'][:1]
    if covered is not None:
        fund['accounts'][0].update(mode='covered_only', funding={
            'covered_list_allowance_micro': covered,
            'coverage_basis': 'verified_route_list_cost_usd', 'no_auto_overage': True})
    if funding and initialize:
        ledger.initialize_funding(fund)
    return SimpleNamespace(client=client, clock=clock, ledger=ledger, funding=fund)


def spend(box, amount, *, channel=CHANNEL, number=0):
    account = box.funding['accounts'][0]
    route = account['routes'][0]
    return box.ledger.reserve(request_key=f'fixture_request_{number}', channel_id=channel,
        lineage_id=f'fixture_lineage_{number}', kind='shorts',
        quote=SpendQuote(account['provider'], route['model'], amount, route['price_revision']),
        funding={'route': route['route'], 'credential_sha256': account['credential_sha256']})


@pytest.mark.parametrize('case,code', [
    ('missing_ledger', 'spend_not_initialized'), ('missing_funding', 'spend_funding_not_initialized'),
    ('month_exhausted', 'spend_month_limit'), ('day_exhausted', 'spend_day_limit'),
    ('channel_exhausted', 'spend_channel_limit'), ('kind_disabled', 'spend_lineage_limit'),
    ('cash_exhausted', 'spend_funding_capacity_exhausted'),
    ('covered_exhausted', 'spend_funding_capacity_exhausted'),
    ('expired', 'spend_funding_month_mismatch'), ('expiring', 'spend_store_expiring'),
])
def test_blocked_preflight_preserves_every_counter_and_creates_no_attempt(case, code):
    policy = SpendPolicy(*([100] * 6))
    if case == 'day_exhausted': policy = replace(policy, daily_micro=5)
    if case == 'channel_exhausted': policy = replace(policy, channel_monthly_micro=5)
    if case == 'kind_disabled': policy = replace(policy, shorts_micro=0)
    box = setup(policy=policy, initialize=case != 'missing_ledger', funding=case != 'missing_funding',
                cash_used=100 if case == 'cash_exhausted' else 0,
                covered=0 if case == 'covered_exhausted' else None)
    if case == 'month_exhausted': spend(box, 100)
    if case in {'day_exhausted', 'channel_exhausted'}: spend(box, 5)
    if case == 'expired': box.clock[0] = datetime(2026, 10, 1, tzinfo=timezone.utc)
    if case == 'expiring': box.client.expire(LEDGER_KEY, 600)
    before = snapshot(box.client)
    with pytest.raises(SpendBlocked, match='^' + code + '$'):
        box.ledger.check_dispatch_capacity(channel_id=CHANNEL, kind='shorts')
    assert snapshot(box.client) == before


def test_included_capacity_is_separate_from_new_cash_and_empty_covered_accounts_cannot_use_cash():
    box = setup(cash_used=150, covered=20)
    before = snapshot(box.client)
    assert box.ledger.check_dispatch_capacity(channel_id=CHANNEL, kind='shorts') is None
    assert snapshot(box.client) == before
    box = setup(covered=0)
    with pytest.raises(SpendBlocked, match='spend_funding_capacity_exhausted'):
        box.ledger.check_dispatch_capacity(channel_id=CHANNEL, kind='shorts')


def test_day_rollover_can_resume_the_same_unconsumed_topic_with_existing_usage_preserved(production, monkeypatch):
    module, client = production
    profile = _profile()
    _save(module, client, profile)
    box = setup(client=client, policy=SpendPolicy(100, 5, 100, 100, 100, 100))
    spend(box, 5)
    module.settings.studio_spend_enforcement = True
    monkeypatch.setattr(runtime, 'settings', SimpleNamespace(studio_spend_enforcement=True))
    factory = Mock(return_value=box.ledger)
    monkeypatch.setattr(runtime, 'configured_ledger', factory)
    before = snapshot(client)
    for _ in range(2):
        result = module.reserve_due_production(profile, CONNECTION, now=1000)
        assert result == {'status': 'budget_blocked', 'reason_code': 'spend_day_limit'}
        assert snapshot(client) == before
    box.clock[0] += timedelta(days=1)
    enqueue = Mock()
    result = module.dispatch_due_productions([profile], [CONNECTION], enqueue, now=1000)
    assert result['status'] == 'queued' and enqueue.call_count == 1
    job = json.loads(client.get(module.JOB_PREFIX + result['task_id']))
    assert job['spec']['production_topic_index'] == 0
    assert module.get_production_state(CHANNEL)['cursor'] == '1'
    assert client.dump(LEDGER_KEY) == before[LEDGER_KEY]
    assert all(call.kwargs == {'read_timeout': 2} for call in factory.call_args_list)


def test_missing_funding_does_not_reserve_a_scheduled_job(production, monkeypatch):
    module, client = production
    profile = _profile()
    _save(module, client, profile)
    box = setup(client=client, initialize=False)
    module.settings.studio_spend_enforcement = True
    monkeypatch.setattr(runtime, 'settings', SimpleNamespace(studio_spend_enforcement=True))
    monkeypatch.setattr(runtime, 'configured_ledger', lambda **kw: box.ledger)
    before, enqueue = snapshot(client), Mock()
    result = module.dispatch_due_productions([profile], [CONNECTION], enqueue, now=1000)
    assert result == {'status': 'idle', 'channels': {CHANNEL: 'budget_blocked'}}
    assert snapshot(client) == before
    enqueue.assert_not_called()


@pytest.mark.parametrize('fault', ['race', 'lost_read', 'wrong_ack', 'numeric_ack'])
def test_ambiguous_read_never_becomes_a_dispatch_permit(fault):
    box = setup()
    before = snapshot(box.client)
    def before_exec(commands):
        assert commands == ('PING',)
        if fault == 'race':
            box.client.hset(LEDGER_KEY, 'last_day', box.client.hget(LEDGER_KEY, 'last_day'))
    def after_exec(commands, reply):
        if fault == 'lost_read': raise ConnectionError('PRIVATE backend details')
        return [False] if fault == 'wrong_ack' else [1] if fault == 'numeric_ack' else reply
    box.ledger.client = Intercept(box.client, before=before_exec, after=after_exec)
    with pytest.raises(SpendBlocked) as error:
        box.ledger.check_dispatch_capacity(channel_id=CHANNEL, kind='shorts')
    assert 'PRIVATE' not in str(error.value) and snapshot(box.client) == before
    assert box.ledger.client.executions == [('PING',)]


def test_preflight_cannot_replace_the_atomic_per_request_reservation():
    box = setup()
    box.ledger.check_dispatch_capacity(channel_id=CHANNEL, kind='shorts')
    spend(box, 100)
    with pytest.raises(SpendBlocked, match='spend_month_limit'):
        spend(box, 1, number=1)
    assert box.ledger.snapshot()['period']['used_micro'] == 100
