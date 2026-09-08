"""Offline budget safety tests; never call a provider or production Redis."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import fakeredis
import pytest

from app.services.production_spend import (
    LEDGER_KEY, SpendBlocked, SpendLedger, SpendPolicy, SpendQuote, usd_micro,
)


@pytest.fixture
def case():
    client = fakeredis.FakeRedis(decode_responses=True)
    clock = [datetime(2026, 9, 8, 12, tzinfo=timezone.utc)]
    policy = SpendPolicy(10_000_000, 4_000_000, 8_000_000,
                         2_000_000, 8_000_000, 500_000)
    ledger = SpendLedger(client, policy, clock=lambda: clock[0])
    return client, ledger, clock


def reserve(ledger, number=1, **changes):
    kwargs = dict(request_key=f'request_{number:08}', channel_id='channel_AAAAA',
                  lineage_id=f'lineage_{number:08}', kind='shorts',
                  quote=SpendQuote('fixture', 'fixture-model', 1_000_000, 'fixture-v1'))
    kwargs.update(changes)
    return ledger.reserve(**kwargs)


@pytest.mark.parametrize('bad', [True, False, 1, 0.1, '-1', '0', 'NaN', 'Infinity', '10001', None])
def test_invalid_money_never_gets_coerced(bad):
    with pytest.raises(SpendBlocked):
        usd_micro(bad)


def test_money_rounds_up_at_microdollar_boundary():
    assert usd_micro('0.0000001') == 1
    assert usd_micro(Decimal('0.3300001')) == 330001
    assert usd_micro('260') == 260_000_000


def test_missing_store_never_automatically_bootstraps(case):
    client, ledger, _ = case
    with pytest.raises(SpendBlocked, match='not_initialized'):
        reserve(ledger)
    assert not client.exists(LEDGER_KEY)


def test_explicit_initialize_is_idempotent_and_cannot_reset_spend(case):
    client, ledger, _ = case
    assert ledger.initialize() is True
    reserve(ledger)
    assert ledger.initialize() is False
    assert ledger.snapshot()['period']['used_micro'] == 1_000_000
    assert client.ttl(LEDGER_KEY) == -1


def test_global_budget_is_not_reset_by_different_channels_or_jobs(case):
    _, ledger, _ = case
    ledger.initialize()
    for i in range(4):
        reserve(ledger, i, channel_id=f'channel_{i:08}')
    with pytest.raises(SpendBlocked, match='day_limit'):
        reserve(ledger, 5)
    assert ledger.snapshot()['remaining_micro'] == 6_000_000


def test_concurrent_reservations_obey_shared_limit(case):
    _, ledger, _ = case
    ledger.initialize()
    def attempt(i):
        try:
            reserve(ledger, i)
            return True
        except SpendBlocked:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        accepted = list(pool.map(attempt, range(20)))
    assert sum(accepted) == 4
    assert ledger.snapshot()['period']['used_micro'] == 4_000_000


def test_same_request_never_authorizes_second_post(case):
    _, ledger, _ = case
    ledger.initialize()
    receipt = reserve(ledger)
    assert receipt['state'] == 'reserved_before_request'
    with pytest.raises(SpendBlocked, match='already_reserved'):
        reserve(ledger, channel_id='channel_BBBBB')
    assert ledger.snapshot()['period']['used_micro'] == 1_000_000


def test_repairs_share_lineage_cap_across_new_queue_ids(case):
    _, ledger, _ = case
    ledger.initialize()
    reserve(ledger, 1)
    reserve(ledger, 2, lineage_id='lineage_00000001')
    with pytest.raises(SpendBlocked, match='lineage_limit'):
        reserve(ledger, 3, lineage_id='lineage_00000001')


@pytest.mark.parametrize('changes', [dict(channel_id='channel_BBBBB'), dict(kind='long')])
def test_lineage_cannot_change_channel_or_upgrade_budget_kind(case, changes):
    _, ledger, _ = case
    ledger.initialize()
    reserve(ledger)
    with pytest.raises(SpendBlocked, match='lineage_binding'):
        reserve(ledger, 2, lineage_id='lineage_00000001', **changes)


def test_channel_and_month_caps_survive_new_days(case):
    _, ledger, clock = case
    ledger.initialize()
    for i in range(8):
        reserve(ledger, i)
        if i == 3:
            clock[0] += timedelta(days=1)
    clock[0] += timedelta(days=1)
    with pytest.raises(SpendBlocked, match='channel_limit'):
        reserve(ledger, 8)
    reserve(ledger, 8, channel_id='channel_BBBBB')
    reserve(ledger, 9, channel_id='channel_BBBBB')
    with pytest.raises(SpendBlocked, match='month_limit'):
        reserve(ledger, 10, channel_id='channel_BBBBB')


def test_month_rollover_retains_receipts_and_lineage_budget(case):
    client, ledger, clock = case
    ledger.initialize()
    reserve(ledger)
    reserve(ledger, 2, lineage_id='lineage_00000001')
    clock[0] = datetime(2026, 10, 1, tzinfo=timezone.utc)
    with pytest.raises(SpendBlocked, match='already_reserved'):
        reserve(ledger)
    with pytest.raises(SpendBlocked, match='lineage_limit'):
        reserve(ledger, 3, lineage_id='lineage_00000001')
    reserve(ledger, 3)
    assert ledger.snapshot()['period']['used_micro'] == 1_000_000
    assert json.loads(client.hget(LEDGER_KEY, 'period:2026-09'))['used_micro'] == 2_000_000


def test_clock_rollback_cannot_reopen_an_earlier_budget(case):
    _, ledger, clock = case
    ledger.initialize()
    reserve(ledger)
    clock[0] -= timedelta(days=1)
    with pytest.raises(SpendBlocked, match='clock_or_state'):
        reserve(ledger, 2)


@pytest.mark.parametrize('field', ['policy', 'active_month', 'last_day', 'period:2026-09'])
def test_missing_metadata_fails_closed_without_reinitialization(case, field):
    client, ledger, _ = case
    ledger.initialize()
    reserve(ledger)
    client.hdel(LEDGER_KEY, field)
    with pytest.raises(SpendBlocked):
        reserve(ledger, 2)
    with pytest.raises(SpendBlocked):
        ledger.initialize()


def test_corrupt_counter_totals_are_rejected(case):
    client, ledger, _ = case
    ledger.initialize()
    reserve(ledger)
    value = json.loads(client.hget(LEDGER_KEY, 'period:2026-09'))
    value['used_micro'] = 0
    client.hset(LEDGER_KEY, 'period:2026-09', json.dumps(value))
    with pytest.raises(SpendBlocked, match='state_invalid'):
        reserve(ledger, 2)


def test_policy_changes_cannot_silently_increase_caps(case):
    client, ledger, clock = case
    ledger.initialize()
    other = SpendLedger(client, replace(ledger.policy, monthly_micro=20_000_000), clock=lambda: clock[0])
    with pytest.raises(SpendBlocked, match='policy_mismatch'):
        other.initialize()
    with pytest.raises(SpendBlocked, match='policy_mismatch'):
        reserve(other)


def test_lost_transaction_reply_consumes_reservation_without_authorizing_post(case, monkeypatch):
    client, ledger, _ = case
    ledger.initialize()
    original = client.pipeline
    def pipeline(*args, **kwargs):
        pipe = original(*args, **kwargs)
        execute = pipe.execute
        def lose_reply(*args, **kwargs):
            execute(*args, **kwargs)
            raise ConnectionError('private connection details must not escape')
        pipe.execute = lose_reply
        return pipe
    monkeypatch.setattr(client, 'pipeline', pipeline)
    with pytest.raises(SpendBlocked, match='^spend_store_unavailable$'):
        reserve(ledger)
    monkeypatch.setattr(client, 'pipeline', original)
    assert ledger.snapshot()['period']['used_micro'] == 1_000_000
    with pytest.raises(SpendBlocked, match='already_reserved'):
        reserve(ledger)


def test_empty_quote_and_negative_limit_are_not_permits(case):
    _, ledger, _ = case
    ledger.initialize()
    for quote in [SpendQuote('', 'fixture', 1, 'fixture'),
                  SpendQuote('fixture', 'fixture', 0, 'fixture'),
                  SpendQuote('fixture', 'fixture', True, 'fixture')]:
        with pytest.raises(SpendBlocked):
            reserve(ledger, quote=quote)
    with pytest.raises(SpendBlocked):
        replace(ledger.policy, daily_micro=-1).validate()


def test_explicit_zero_paid_budget_authorizes_no_external_paid_call(case):
    client, _, clock = case
    ledger = SpendLedger(client, SpendPolicy(0, 0, 0, 0, 0, 0), clock=lambda: clock[0])
    ledger.initialize()
    with pytest.raises(SpendBlocked, match='month_limit'):
        reserve(ledger)
    assert ledger.snapshot()['remaining_micro'] == 0


def test_free_work_does_not_need_a_fake_zero_price_reservation(case):
    client, ledger, _ = case
    # Free local render is outside this paid-request ledger and remains usable
    # when no budget store exists. A zero-price external API quote is not proof.
    assert not client.exists(LEDGER_KEY)
    with pytest.raises(SpendBlocked):
        reserve(ledger, quote=SpendQuote('fixture', 'fixture', 0, 'fixture'))
