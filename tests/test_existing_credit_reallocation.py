"""Existing subscription reserve use must retain every prior charge and fence."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import Barrier
import json

import pytest
from redis.exceptions import ConnectionError

from app.services import production_credit_periods as periods, production_credit_ledger as durable
from app.services.production_spend import SpendBlocked, LEDGER_KEY
from test_production_credit_periods import cycle
from test_production_credit_ledger import client, policy, NOW, intent, observation, binding
from test_production_cash_disabled import dump


@pytest.fixture
def current(cycle):
    cycle.now[0] = NOW + timedelta(minutes=1)
    cycle.evidence.update(observed_at=periods._stamp(cycle.now[0]), used_credits=130050,
        provider_reset_at=cycle.policy['balance']['provider_reset_at'])
    return cycle


def allocate(case, *, changes=None, **kwargs):
    values = {'authorization_sha256': 'a' * 64, 'withheld_credits': 50,
        'allocation_cap_credits': 1000, 'expected_policy_sha256': case.old['policy_sha256'],
        'expected_state_sha256': case.old['state_sha256'], **kwargs}
    return periods.reallocate_existing_balance(case.ledger, {**case.evidence, **(changes or {})}, **values)


def test_verified_existing_credits_archive_exact_old_spend_and_do_not_refill_from_old_quota(current):
    c = current.client; previous = c.hgetall(durable.STATE_KEY); journal = c.hgetall(durable.JOURNAL_KEY)
    markers = durable.native_foundation_markers(c); cash = current.foundation.snapshot()
    assert allocate(current) == {'status': 'reallocated', 'allocation_credits': 900,
        'archived_periods': 1, 'valid_until': current.policy['valid_until']}
    archived = json.loads(c.get(periods.HISTORY_KEY))['periods'][0]
    assert durable._json(archived['state']) == previous['state']
    assert durable._json(archived['policy']) == previous['policy']
    assert archived['journal'] == journal and archived['state']['spent_credits'] == 248
    assert archived['reallocation']['authorization_sha256'] == 'a' * 64
    assert durable.native_foundation_markers(c) == markers
    assert current.foundation.snapshot() == cash
    assert current.ledger.summary()['available_credits'] == 900  # actual 950 minus reserve 50, not 1000+752
    before = dump(c)
    assert allocate(current)['status'] == 'already_reallocated'
    assert dump(c) == before
    for replay in (intent(), intent(99, request_sha256=intent()['request_sha256'])):
        with pytest.raises(SpendBlocked):
            current.ledger.reserve(intent=replay, production_context=current.context, **binding(current.policy))
    assert dump(c) == before


@pytest.mark.parametrize('changes', [
    {'account_sha256': 'b' * 64}, {'credential_sha256': 'b' * 64}, {'quota_credits': 132000},
    {'used_credits': 71384}, {'used_credits': 130999}, {'used_credits': True},
    {'provider_reset_at': '2026-10-25T00:00:00Z'}, {'can_extend_character_limit': True},
    {'max_credit_limit_extension': 1}, {'status': 'past_due'}, {'source': 'estimated_balance'},
    {'observed_at': '2026-09-09T14:01:01Z'}, {'observed_at': '2026-09-09T13:58:59Z'},
])
def test_bad_new_or_unreconciled_account_evidence_cannot_allocate(current, changes):
    before = dump(current.client)
    with pytest.raises(SpendBlocked): allocate(current, changes=changes)
    assert dump(current.client) == before


@pytest.mark.parametrize('values', [{'withheld_credits': -1}, {'withheld_credits': True},
    {'allocation_cap_credits': 1001}, {'allocation_cap_credits': True}, {'allocation_cap_credits': 0},
    {'authorization_sha256': ''}, {'expected_state_sha256': 'f' * 64}])
def test_invalid_operator_boundary_or_stale_snapshot_changes_nothing(current, values):
    before = dump(current.client)
    with pytest.raises(SpendBlocked): allocate(current, **values)
    assert dump(current.client) == before


def test_unknown_request_cannot_be_freed_by_reallocation(current):
    current.ledger.reserve(intent=intent(2), production_context=current.context, **binding(current.policy))
    current.old = periods.renewal_snapshot(current.ledger); before = dump(current.client)
    with pytest.raises(SpendBlocked, match='has_uncertain_usage'): allocate(current)
    assert dump(current.client) == before


def test_known_overrun_is_not_erased(current):
    receipt = current.ledger.reserve(intent=intent(2), production_context=current.context, **binding(current.policy))
    current.ledger.settle(observation=observation(current.policy, receipt, actual_credit_cost=800,
        observed_at=periods._stamp(current.now[0])), **binding(current.policy))
    current.old = periods.renewal_snapshot(current.ledger); before = dump(current.client)
    with pytest.raises(SpendBlocked, match='has_overrun'): allocate(current)
    assert dump(current.client) == before


def test_only_one_explicit_allocation_per_actual_provider_period(current):
    allocate(current); current.old = periods.renewal_snapshot(current.ledger)
    current.now[0] += timedelta(seconds=1)
    current.evidence.update(observed_at=periods._stamp(current.now[0]), response_sha256='b' * 64)
    before = dump(current.client)
    with pytest.raises(SpendBlocked, match='reallocation_already_used'): allocate(current)
    assert dump(current.client) == before


@pytest.mark.parametrize('key', [periods.HISTORY_KEY, periods.HISTORY_ANCHOR])
def test_archive_loss_never_releases_spent_credits(current, key):
    allocate(current); current.client.delete(key); before = dump(current.client)
    with pytest.raises(SpendBlocked): current.ledger.summary()
    assert dump(current.client) == before


def test_whole_old_active_state_rollback_cannot_bypass_archived_fences(current):
    old_state = current.client.hgetall(durable.STATE_KEY)
    old_journal = current.client.hgetall(durable.JOURNAL_KEY)
    mode = current.client.hget(LEDGER_KEY, durable.MODE_FIELD)
    allocate(current)
    current.client.hset(durable.STATE_KEY, mapping=old_state)
    current.client.delete(durable.JOURNAL_KEY); current.client.hset(durable.JOURNAL_KEY, mapping=old_journal)
    current.client.hset(LEDGER_KEY, durable.MODE_FIELD, mode)
    with pytest.raises(SpendBlocked): current.ledger.summary()


def test_lost_ack_leaves_one_complete_archive_and_idempotent_readback(current, monkeypatch):
    pipeline = current.client.pipeline
    def wrapped(*args, **kwargs):
        pipe = pipeline(*args, **kwargs); execute = pipe.execute
        def lost(*args, **kwargs):
            writes = any(row[0][0] == 'SET' and row[0][1] == periods.HISTORY_KEY for row in pipe.command_stack)
            result = execute(*args, **kwargs)
            if writes: raise ConnectionError('lost reply')
            return result
        pipe.execute = lost; return pipe
    monkeypatch.setattr(current.client, 'pipeline', wrapped)
    with pytest.raises(SpendBlocked, match='commit_uncertain'): allocate(current)
    monkeypatch.setattr(current.client, 'pipeline', pipeline)
    before = dump(current.client)
    assert allocate(current)['status'] == 'already_reallocated'
    assert dump(current.client) == before
    assert len(json.loads(current.client.get(periods.HISTORY_KEY))['periods']) == 1


def test_concurrent_allocations_commit_once_and_never_retry_uncertain_transaction(current, monkeypatch):
    pipeline, barrier = current.client.pipeline, Barrier(2)
    def wrapped(*args, **kwargs):
        pipe = pipeline(*args, **kwargs); execute = pipe.execute
        def race(*args, **kwargs):
            if any(row[0][0] == 'SET' and row[0][1] == periods.HISTORY_KEY for row in pipe.command_stack):
                barrier.wait(timeout=5)
            return execute(*args, **kwargs)
        pipe.execute = race; return pipe
    monkeypatch.setattr(current.client, 'pipeline', wrapped)
    def one(_):
        try: return allocate(current)['status']
        except SpendBlocked as error: return str(error)
    with ThreadPoolExecutor(max_workers=2) as pool: outcomes = list(pool.map(one, range(2)))
    assert sorted(outcomes) == ['credit_period_commit_uncertain', 'reallocated']
    assert current.ledger.summary()['available_credits'] == 900


def test_genuine_next_reset_restores_original_operator_cap_and_original_reserve(current):
    allocate(current); current.old = periods.renewal_snapshot(current.ledger)
    current.now[0] = datetime(2026, 9, 25, tzinfo=timezone.utc)
    current.evidence.update(observed_at=periods._stamp(current.now[0]), used_credits=0,
        provider_reset_at='2026-10-25T00:00:00Z', response_sha256='c' * 64)
    outcome = periods.renew(current.ledger, current.evidence,
        expected_policy_sha256=current.old['policy_sha256'], expected_state_sha256=current.old['state_sha256'])
    assert outcome['allocation_credits'] == 1000
    policy = periods.renewal_snapshot(current.ledger)['policy']
    assert policy['balance']['withheld_credits'] == current.policy['balance']['withheld_credits'] == 100
    assert current.foundation.snapshot()['cash_spending_enabled'] is False


def test_reallocated_old_root_is_never_mistaken_for_unvoiced_research(current, monkeypatch):
    from app.services import content_plan_research_resume as resume, production_spend_runtime as runtime
    from app.services import production_included_router as included, content_plan as plan
    allocate(current)
    monkeypatch.setattr(runtime, 'configured_ledger', lambda **kw: current.foundation)
    monkeypatch.setattr(included.IncludedRouterLedger, '_read', lambda *a: ({}, {'requests': {}}))
    before = dump(current.client)
    with pytest.raises(plan.ContentPlanError):
        resume._provider_free(current.client, intent()['root_lineage_id'])
    assert dump(current.client) == before


def test_archived_voice_assignment_remains_original_voice(current, monkeypatch):
    from app.services import narrator_rotation as rotation, production_spend_runtime as runtime
    allocate(current)
    monkeypatch.setattr(runtime, 'enforcement_enabled', lambda: True)
    monkeypatch.setattr(runtime, 'configured_ledger', lambda **kw: current.foundation)
    monkeypatch.setattr(runtime, 'resolve_context', lambda *a: current.context)
    token = runtime._TASK_ID.set('existing-root-retry')
    before = dump(current.client)
    try:
        assert rotation.assigned('en') == {'voice_id': current.policy['voice_id'], 'name': 'Mustafa'}
    finally:
        runtime._TASK_ID.reset(token)
    assert dump(current.client) == before
