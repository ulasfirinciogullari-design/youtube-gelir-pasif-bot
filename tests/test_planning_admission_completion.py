"""A completed worker with no planning reservation must not occupy the channel forever."""
import json
from unittest.mock import Mock

import pytest
from redis.exceptions import WatchError

from test_channel_production import production, CHANNEL, CONNECTION
from test_production_series_promotion import case, NOW, _answer, _snapshot
from test_production_series_scheduler import controller, fresh, _queued, _execute
from test_bounded_series_attempts import enable_multiple_attempts, tick, execute


def admission_conflict(c, monkeypatch):
    factory = c.client.pipeline
    executions = []
    def pipeline(*args, **kwargs):
        pipe = factory(*args, **kwargs); original = pipe.execute
        def perform(*args, **kwargs):
            executions.append(1)
            if len(executions) == 2:
                raise WatchError('Watched variable changed.')
            return original(*args, **kwargs)
        pipe.execute = perform
        return pipe
    monkeypatch.setattr(c.client, 'pipeline', pipeline)


def test_actual_pre_reservation_transaction_conflict_allows_next_distinct_slot(fresh, monkeypatch):
    c, sender = fresh, Mock()
    first = _queued(c, sender)
    admission_conflict(c, monkeypatch)
    assert _execute(c, first)['status'] == 'unavailable'
    dispatch_key, claim_key = c.prep['_execution_keys'](first)[:2]
    assert json.loads(c.client.get(dispatch_key))['status'] == 'finished'
    assert json.loads(c.client.get(dispatch_key))['outcome'] == 'unavailable'
    assert c.client.get(claim_key) == first['token']
    assert c.client.get(c.daily_key) is c.client.get(c.pending_key) is None
    c.prep['_generate'].assert_not_called()
    before = _snapshot(c)
    assert tick(c, sender, NOW + 1799) == 'preparation_cooldown'
    assert _snapshot(c) == before
    assert tick(c, sender, NOW + 1801) == 'preparation_queued'
    second = sender.call_args.kwargs['args'][0]
    assert second['preparation_slot'] == 2 and second['task_id'] != first['task_id']
    assert all(c.client.dump(k) == v for k,v in before.items())
    assert execute(c, second, NOW + 1802)['status'] == 'ready'
    assert c.prep['_generate'].call_count == 1
    assert execute(c, first, NOW + 1803)['status'] == 'execution_already_claimed'
    assert c.prep['_generate'].call_count == 1
    assert tick(c, sender, NOW + 1804) == 'promoted'


@pytest.mark.parametrize('damage', ['unfinished', 'broker_unknown', 'missing_claim', 'wrong_claim',
    'expired_dispatch', 'expired_claim', 'existing_daily', 'wrong_outcome', 'wrong_day', 'missing_created'])
def test_only_finished_bound_worker_without_reservation_can_advance(fresh, monkeypatch, damage):
    c, sender = fresh, Mock()
    first = _queued(c, sender); admission_conflict(c, monkeypatch)
    assert _execute(c, first)['status'] == 'unavailable'
    dispatch_key, claim_key = c.prep['_execution_keys'](first)[:2]
    dispatch = json.loads(c.client.get(dispatch_key))
    if damage == 'unfinished': dispatch['status'] = 'reserved'
    elif damage == 'broker_unknown': dispatch['status'] = 'uncertain'
    elif damage == 'missing_claim': c.client.delete(claim_key)
    elif damage == 'wrong_claim': c.client.set(claim_key, 'changed')
    elif damage == 'expired_dispatch': c.client.expire(dispatch_key, 60)
    elif damage == 'expired_claim': c.client.expire(claim_key, 60)
    elif damage == 'existing_daily': c.client.set(c.daily_key, '{}')
    elif damage == 'wrong_outcome': dispatch['outcome'] = 'failed'
    elif damage == 'wrong_day': dispatch['created_at'] = NOW + 86400
    elif damage == 'missing_created': dispatch.pop('created_at')
    if damage in {'unfinished', 'broker_unknown', 'wrong_outcome', 'wrong_day', 'missing_created'}:
        c.client.set(dispatch_key, c.prep['_json'](dispatch))
    before = _snapshot(c)
    assert tick(c, sender, NOW + 1801) in {'preparation_already_reserved', 'ineligible_or_changed'}
    assert _snapshot(c) == before and sender.call_count == 1
    c.prep['_generate'].assert_not_called()
