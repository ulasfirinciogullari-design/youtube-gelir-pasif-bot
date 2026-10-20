"""Actual planner queue transactions alongside an untouched unfunded daily film."""
from concurrent.futures import ThreadPoolExecutor
import json
from unittest.mock import Mock

import pytest

from app.services import production_scheduler as scheduler, production_next_series as planning
from app.services import content_plan as plan, channel_production as production
from app.services import production_series_spend
from test_daily_voice_priority import waiting, case, policy, CHANNEL, NOW, dump


@pytest.fixture
def exhausted(waiting, monkeypatch):
    w = waiting
    w.c.hset(production.CHANNEL_STATE_PREFIX + CHANNEL, mapping={
        'cursor': '1', 'consumed_prefix': production._prefix_digest(w.profile['production_topics']),
        'profile_revision': w.profile['profile_revision'], 'connection_id': w.connection['connection_id'],
        'dispatch_status': 'finished', 'last_result': 'FAILURE'})
    monkeypatch.setattr(scheduler, '_client', lambda: w.c)
    # This test checks planner admission and queue ownership. No model runs;
    # the full real provider funding route has separate integration coverage.
    monkeypatch.setattr(production_series_spend, 'prepare_dispatch_context', Mock(return_value=None))
    return w


def maintain(w, enqueue):
    return scheduler.maintain_production_series([w.profile], [w.connection], enqueue, now=NOW.timestamp())


def test_exhausted_short_topics_can_prepare_once_beside_original_waiting_long(exhausted):
    w = exhausted; enqueue = Mock(); original = dump(w.c)
    assert maintain(w, enqueue)['channels'][CHANNEL] == 'preparation_queued'
    enqueue.assert_called_once()
    binding = enqueue.call_args.kwargs['args'][0]
    dispatch_key, execution_key = planning._execution_keys(binding)[:2]
    record = json.loads(w.c.get(dispatch_key))
    assert record['daily_voice_priority_plan_sha256'] == plan._sha(plan.read(CHANNEL, client=w.c))
    with w.c.pipeline() as pipe:
        pipe.watch(production.PROFILE_PREFIX + CHANNEL)
        profile, channel, state, _ = scheduler._current(pipe, CHANNEL)
        assert planning._execution_guard(pipe, binding, profile, channel, state,
                                        require_execution=False) == record
        pipe.multi(); pipe.ping(); assert pipe.execute() == [True]
    assert maintain(w, enqueue)['channels'][CHANNEL] == 'preparation_already_reserved'
    assert not w.c.exists(execution_key, plan.DISPATCH_PREFIX + w.item, plan.COMPLETION_PREFIX + w.item)
    assert all(w.c.dump(k) == v for k, v in original.items())


@pytest.mark.parametrize('change', ['paused', 'owner_edit', 'low_credit'])
def test_worker_rechecks_owner_plan_and_credit_before_any_model_request(exhausted, monkeypatch, change):
    w = exhausted; enqueue = Mock()
    assert maintain(w, enqueue)['channels'][CHANNEL] == 'preparation_queued'
    binding = enqueue.call_args.kwargs['args'][0]
    if change == 'low_credit':
        from test_production_credit_ledger import intent, binding as credit_binding, observation
        receipt = w.ledger.reserve(intent=intent(2), production_context=w.context, **credit_binding(w.policy))
        w.ledger.settle(observation=observation(w.policy, receipt, actual_credit_cost=3200), **credit_binding(w.policy))
    else:
        doc = plan.read(CHANNEL, client=w.c)
        if change == 'paused': doc['enabled'] = False
        else: doc['items'][-1]['brief'] += ' Changed by owner'
        w.c.set(plan.PLAN_PREFIX + CHANNEL, plan._raw(doc))
    before = dump(w.c); planner = Mock(side_effect=AssertionError('No model after revoked authority'))
    monkeypatch.setattr(scheduler, 'prepare_next_series', planner)
    assert scheduler.run_series_preparation(binding, binding['task_id'], now=NOW.timestamp())['status'] == 'unavailable'
    planner.assert_not_called()
    assert dump(w.c) == before


def test_owner_pause_racing_queue_commit_does_not_send_a_preparation(exhausted, monkeypatch):
    w = exhausted; enqueue = Mock(); original = w.c.pipeline
    def pipeline(*args, **kwargs):
        pipe = original(*args, **kwargs); execute = pipe.execute
        def race(*a, **kw):
            if any(row[0][0] == 'SET' and str(row[0][1]).startswith(planning.PREPARATION_DISPATCH_PREFIX)
                   for row in pipe.command_stack):
                doc = plan.read(CHANNEL, client=w.c); doc['enabled'] = False
                w.c.set(plan.PLAN_PREFIX + CHANNEL, plan._raw(doc))
            return execute(*a, **kw)
        pipe.execute = race; return pipe
    monkeypatch.setattr(w.c, 'pipeline', pipeline)
    assert maintain(w, enqueue)['channels'][CHANNEL] == 'ineligible_or_changed'
    enqueue.assert_not_called()
    assert not w.c.keys(planning.PREPARATION_DISPATCH_PREFIX + '*')


def test_competing_ticks_keep_one_preparation_and_one_long(exhausted):
    w = exhausted; enqueue = Mock()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: maintain(w, enqueue)['channels'][CHANNEL], range(16)))
    assert results.count('preparation_queued') == 1 and enqueue.call_count == 1
    assert not w.c.exists(plan.DISPATCH_PREFIX + w.item)
    assert w.ledger.summary()['spent_credits'] == 2000
