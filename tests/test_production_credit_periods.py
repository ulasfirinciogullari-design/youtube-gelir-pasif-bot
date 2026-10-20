"""Real Redis transactions with synthetic balances, resets, races and loss."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import json

import pytest
from redis.exceptions import ConnectionError

from app.services import production_credit_periods as periods
from app.services import production_credit_ledger as durable
from app.services.production_spend import LEDGER_KEY, SpendBlocked
from test_production_cash_disabled import money, dump
from test_production_credit_ledger import (
    client, policy, NOW, binding, intent, observation, context,
)


@pytest.fixture
def cycle(client, policy):
    clock = [NOW]
    policy['valid_until'] = '2026-09-25T00:00:00Z'
    foundation = money(client)
    foundation.clock = lambda: clock[0]
    ledger = durable.CreditLedger(client, foundation=foundation, clock=foundation.clock)
    ledger.initialize(policy)
    bound = context(client)
    receipt = ledger.reserve(intent=intent(), production_context=bound, **binding(policy))
    ledger.settle(observation=observation(policy, receipt), **binding(policy))
    old = periods.renewal_snapshot(ledger)
    clock[0] = datetime(2026, 9, 25, tzinfo=timezone.utc)
    account = {'version': 1, 'source': 'verified_GET_v1_user',
        **{key: policy[key] for key in ('account_sha256', 'credential_sha256')},
        'observed_at': periods._stamp(clock[0]), 'provider_reset_at': '2026-10-25T00:00:00Z',
        'quota_credits': 131000, 'used_credits': 50, 'status': 'active',
        'max_credit_limit_extension': 0, 'can_extend_character_limit': False,
        'response_sha256': '1' * 64}
    return SimpleNamespace(client=client, ledger=ledger, foundation=foundation, policy=policy,
        now=clock, old=old, evidence=account, context=bound)


def renew(case, **changes):
    evidence = {**case.evidence, **changes}
    return periods.renew(case.ledger, evidence, expected_policy_sha256=case.old['policy_sha256'],
                         expected_state_sha256=case.old['state_sha256'])


def test_new_provider_period_archives_exact_receipts_and_preserves_cash_closure(cycle):
    before_state = cycle.client.hgetall(durable.STATE_KEY)
    before_journal = cycle.client.hgetall(durable.JOURNAL_KEY)
    markers = durable.native_foundation_markers(cycle.client)
    assert renew(cycle)['status'] == 'renewed'
    history = json.loads(cycle.client.get(periods.HISTORY_KEY))
    archived = history['periods'][0]
    assert archived['journal'] == before_journal
    assert durable._json(archived['policy']) == before_state['policy']
    assert durable._json(archived['state']) == before_state['state']
    assert durable.native_foundation_markers(cycle.client) == markers
    summary = cycle.ledger.summary()
    assert summary['allocation_credits'] == 1000 and summary['spent_credits'] == 0
    assert summary['valid_until'] == '2026-10-01T00:00:00Z'
    assert archived['state']['spent_credits'] == 248
    cash = cycle.foundation.snapshot()
    assert cash['historical_cash_micro'] is None and cash['new_cash_allowance_micro'] == 0
    assert cash['additional_monthly_limit_micro'] == 10000000
    before = dump(cycle.client)
    assert renew(cycle)['status'] == 'already_renewed'
    assert dump(cycle.client) == before


def test_reserved_unknown_is_not_freed_by_a_real_new_balance(cycle):
    cycle.now[0] = NOW + timedelta(minutes=1)
    cycle.ledger.reserve(intent=intent(2), production_context=cycle.context, **binding(cycle.policy))
    cycle.old = periods.renewal_snapshot(cycle.ledger)
    cycle.now[0] = datetime(2026, 9, 25, tzinfo=timezone.utc)
    before = dump(cycle.client)
    with pytest.raises(SpendBlocked, match='has_uncertain_usage'):
        renew(cycle)
    assert dump(cycle.client) == before


@pytest.mark.parametrize('changes', [
    {'account_sha256': '0' * 64}, {'credential_sha256': '0' * 64},
    {'quota_credits': True}, {'used_credits': -1}, {'status': 'past_due'},
    {'max_credit_limit_extension': 1}, {'max_credit_limit_extension': False},
    {'can_extend_character_limit': True}, {'source': 'estimated_balance'},
    {'observed_at': '2026-09-24T23:59:59Z'}, {'observed_at': '2026-09-25T00:00:01Z'},
    {'provider_reset_at': '2026-09-25T00:00:00Z'},
    {'provider_reset_at': '2026-09-24T00:00:00Z'}, {'response_sha256': ''},
])
def test_invalid_balance_or_controls_never_change_ledger(cycle, changes):
    before = dump(cycle.client)
    with pytest.raises(SpendBlocked): renew(cycle, **changes)
    assert dump(cycle.client) == before


def test_stale_account_read_cannot_open_new_period(cycle):
    cycle.now[0] += timedelta(seconds=121)
    before = dump(cycle.client)
    with pytest.raises(SpendBlocked): renew(cycle)
    assert dump(cycle.client) == before


def test_calendar_rollover_carries_remaining_allocation_without_refilling(cycle):
    renew(cycle)
    current_policy = periods.renewal_snapshot(cycle.ledger)['policy']
    receipt = cycle.ledger.reserve(intent=intent(2), production_context=cycle.context, **binding(current_policy))
    cycle.ledger.settle(observation=observation(current_policy, receipt,
        observed_at=periods._stamp(cycle.now[0]), actual_credit_cost=200), **binding(current_policy))
    cycle.old = periods.renewal_snapshot(cycle.ledger)
    cycle.now[0] = datetime(2026, 10, 1, tzinfo=timezone.utc)
    cycle.evidence.update(observed_at=periods._stamp(cycle.now[0]), used_credits=250, response_sha256='2' * 64)
    assert renew(cycle)['allocation_credits'] == 800
    assert cycle.ledger.summary()['available_credits'] == 800
    # The following genuine provider reset may restore the ORIGINAL 1000 cap,
    # never a larger one, even after the intermediate calendar-only split.
    cycle.old = periods.renewal_snapshot(cycle.ledger)
    cycle.now[0] = datetime(2026, 10, 25, tzinfo=timezone.utc)
    cycle.evidence.update(observed_at=periods._stamp(cycle.now[0]), used_credits=0,
                          provider_reset_at='2026-11-25T00:00:00Z', response_sha256='3' * 64)
    assert renew(cycle)['allocation_credits'] == 1000
    assert len(json.loads(cycle.client.get(periods.HISTORY_KEY))['periods']) == 3
    assert cycle.foundation.snapshot()['cash_spending_enabled'] is False


def test_external_account_usage_reduces_covered_allocation(cycle):
    assert renew(cycle, used_credits=130300)['allocation_credits'] == 600


@pytest.mark.parametrize('key', [periods.HISTORY_KEY, periods.HISTORY_ANCHOR])
def test_history_loss_or_expiry_never_restores_capacity(cycle, key):
    renew(cycle)
    cycle.client.expire(key, 100)
    with pytest.raises(SpendBlocked): cycle.ledger.summary()
    cycle.client.delete(key)
    before = dump(cycle.client)
    with pytest.raises(SpendBlocked): cycle.ledger.summary()
    with pytest.raises(SpendBlocked): cycle.ledger.initialize(cycle.policy)
    assert dump(cycle.client) == before


def test_all_history_keys_lost_still_fail_foundation_correspondence(cycle):
    renew(cycle)
    cycle.client.delete(periods.HISTORY_KEY, periods.HISTORY_ANCHOR)
    with pytest.raises(SpendBlocked): cycle.ledger.summary()


def test_current_pair_rollback_cannot_hide_new_period(cycle):
    original = cycle.client.hgetall(durable.STATE_KEY), cycle.client.hgetall(durable.JOURNAL_KEY)
    mode = cycle.client.hget(LEDGER_KEY, durable.MODE_FIELD)
    renew(cycle)
    cycle.client.hset(durable.STATE_KEY, mapping=original[0])
    cycle.client.delete(durable.JOURNAL_KEY)
    cycle.client.hset(durable.JOURNAL_KEY, mapping=original[1])
    cycle.client.hset(LEDGER_KEY, durable.MODE_FIELD, mode)
    with pytest.raises(SpendBlocked): periods.renewal_snapshot(cycle.ledger)


def test_archived_root_request_and_provider_ids_cannot_be_replayed(cycle):
    renew(cycle)
    policy = periods.renewal_snapshot(cycle.ledger)['policy']
    with pytest.raises(SpendBlocked):
        cycle.ledger.reserve(intent=intent(), production_context=cycle.context, **binding(policy))
    with pytest.raises(SpendBlocked):
        cycle.ledger.reserve(intent=intent(8, request_sha256=intent()['request_sha256']),
                             production_context=cycle.context, **binding(policy))
    receipt = cycle.ledger.reserve(intent=intent(2), production_context=cycle.context, **binding(policy))
    old_id = cycle.old['state']['intents'][intent()['intent_id']]['settlement']['provider_request_id_sha256']
    before = dump(cycle.client)
    with pytest.raises(SpendBlocked, match='observation_conflict'):
        cycle.ledger.settle(observation=observation(policy, receipt, observed_at=periods._stamp(cycle.now[0]),
            provider_request_id_sha256=old_id), **binding(policy))
    assert dump(cycle.client) == before


def test_lost_renewal_ack_preserves_single_committed_archive(cycle, monkeypatch):
    pipeline = cycle.client.pipeline
    def wrapped(*args, **kwargs):
        pipe = pipeline(*args, **kwargs)
        execute = pipe.execute
        def lost(*args, **kwargs):
            writes = any(row[0][0] == 'SET' and row[0][1] == periods.HISTORY_KEY for row in pipe.command_stack)
            result = execute(*args, **kwargs)
            if writes: raise ConnectionError('injected reply loss')
            return result
        pipe.execute = lost
        return pipe
    monkeypatch.setattr(cycle.client, 'pipeline', wrapped)
    with pytest.raises(SpendBlocked, match='commit_uncertain'): renew(cycle)
    monkeypatch.setattr(cycle.client, 'pipeline', pipeline)
    assert cycle.ledger.summary()['available_credits'] == 1000
    before = dump(cycle.client)
    assert renew(cycle)['status'] == 'already_renewed'
    assert dump(cycle.client) == before


def test_stale_snapshot_cannot_replace_late_settlement(cycle):
    cycle.now[0] = NOW + timedelta(minutes=1)
    receipt = cycle.ledger.reserve(intent=intent(2), production_context=cycle.context, **binding(cycle.policy))
    cycle.old = periods.renewal_snapshot(cycle.ledger)
    cycle.now[0] = datetime(2026, 9, 25, tzinfo=timezone.utc)
    cycle.ledger.settle(observation=observation(cycle.policy, receipt,
        observed_at=periods._stamp(cycle.now[0])), **binding(cycle.policy))
    before = dump(cycle.client)
    with pytest.raises(SpendBlocked, match='snapshot_changed'): renew(cycle)
    assert dump(cycle.client) == before


def test_concurrent_renewals_commit_one_archive_without_retrying_transaction(cycle, monkeypatch):
    pipeline, barrier = cycle.client.pipeline, Barrier(2)
    def wrapped(*args, **kwargs):
        pipe = pipeline(*args, **kwargs)
        execute = pipe.execute
        def race(*args, **kwargs):
            if any(row[0][0] == 'SET' and row[0][1] == periods.HISTORY_KEY for row in pipe.command_stack):
                barrier.wait(timeout=5)
            return execute(*args, **kwargs)
        pipe.execute = race
        return pipe
    monkeypatch.setattr(cycle.client, 'pipeline', wrapped)
    def one(_):
        try: return renew(cycle)['status']
        except SpendBlocked as error: return str(error)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(one, range(2)))
    assert sorted(outcomes) == ['credit_period_commit_uncertain', 'renewed']
    assert len(json.loads(cycle.client.get(periods.HISTORY_KEY))['periods']) == 1
    assert cycle.ledger.summary()['available_credits'] == 1000


def test_archived_foundation_receipt_loss_blocks_new_credit_use(cycle):
    markers = durable.native_foundation_markers(cycle.client)
    renew(cycle)
    cycle.client.hdel(LEDGER_KEY, next(iter(markers)))
    before = dump(cycle.client)
    with pytest.raises(SpendBlocked, match='foundation_mismatch'):
        cycle.ledger.summary()
    assert dump(cycle.client) == before
