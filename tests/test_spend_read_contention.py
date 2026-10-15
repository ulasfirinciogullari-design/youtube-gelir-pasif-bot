"""Repeated admission reads must not invalidate unrelated provider transactions."""
import json
from unittest.mock import Mock

import pytest
from redis.exceptions import WatchError

from app.services import production_spend_runtime as runtime, production_cash_disabled as disabled
from app.services.production_spend import LEDGER_KEY, SpendBlocked
from test_production_spend_runtime import case, job, ROOT, CHILD, CHANNEL
from test_production_cash_disabled import money


@pytest.mark.parametrize('ancestry', [False, True])
def test_existing_context_reads_do_not_write_or_abort_another_watch(case, ancestry):
    client, _ = case
    if ancestry: job(client, CHILD, ROOT)
    task = CHILD if ancestry else ROOT
    expected = runtime.resolve_context(client, task)
    before = client.dump(LEDGER_KEY)
    with client.pipeline() as observing:
        observing.watch(LEDGER_KEY)
        for _ in range(8): assert runtime.resolve_context(client, task) == expected
        observing.multi(); observing.ping()
        assert observing.execute() == [True]
    assert client.dump(LEDGER_KEY) == before


def test_new_binding_remains_a_real_watched_write_and_conflicting_binding_is_not_overwritten(case):
    client, _ = case; runtime.resolve_context(client, ROOT); job(client, CHILD, ROOT)
    with client.pipeline() as observing:
        observing.watch(LEDGER_KEY)
        runtime.resolve_context(client, CHILD)
        observing.multi(); observing.ping()
        with pytest.raises(WatchError): observing.execute()
    key = 'binding:' + CHILD; value = json.loads(client.hget(LEDGER_KEY, key))
    value['channel_id'] = 'a-different-channel'; client.hset(LEDGER_KEY, key, json.dumps(value))
    before = client.dump(LEDGER_KEY)
    with pytest.raises(SpendBlocked): runtime.resolve_context(client, CHILD)
    assert client.dump(LEDGER_KEY) == before


def test_series_context_reuses_binding_without_global_ledger_write(case, monkeypatch):
    from app.services import production_series_spend as series
    client, _ = case; binding = runtime.resolve_context(client, ROOT)
    client.set(series.CONTEXT_PREFIX + ROOT, 'existing context')
    monkeypatch.setattr(series, 'read_context', lambda *args: binding)
    with client.pipeline() as observing:
        observing.watch(LEDGER_KEY)
        assert runtime.resolve_context(client, ROOT) == binding
        observing.multi(); observing.ping(); assert observing.execute() == [True]


def test_cash_foundation_checks_every_binding_in_one_snapshot(case):
    client, _ = case; client.flushdb(); foundation = money(client)
    values = {'binding:lineage-' + str(i): json.dumps({'channel_id': CHANNEL,
        'connection_id': 'connection-existing', 'lineage_id': 'lineage-' + str(i), 'kind': 'long'}) for i in range(1000)}
    client.hset(LEDGER_KEY, mapping=values)
    with client.pipeline() as pipe:
        snapshot = Mock(wraps=pipe.hgetall); pipe.hgetall = snapshot
        pipe.hget = Mock(side_effect=AssertionError('No per-binding network round trip'))
        value = disabled.read(pipe, foundation, now=foundation.clock())
        snapshot.assert_called_once_with(LEDGER_KEY)
        pipe.multi(); pipe.ping(); assert pipe.execute() == [True]
        assert value['historical_cash_micro'] is None and value['new_cash_allowance_micro'] == 0
    client.hset(LEDGER_KEY, 'binding:lineage-999', json.dumps({'kind': 'unverified'}))
    with client.pipeline() as pipe:
        with pytest.raises(SpendBlocked): disabled.read(pipe, foundation, now=foundation.clock())


def test_batched_snapshot_still_detects_a_real_policy_change_before_exec(case):
    client, _ = case; client.flushdb(); foundation = money(client)
    with client.pipeline() as pipe:
        disabled.read(pipe, foundation, now=foundation.clock())
        client.hset(LEDGER_KEY, 'policy', '{}')
        pipe.multi(); pipe.ping()
        with pytest.raises(WatchError): pipe.execute()
