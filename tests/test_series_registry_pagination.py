"""Real Redis: history beyond the UI window cannot hide live deliveries."""
import json
from uuid import UUID

import pytest

from test_production_series_promotion import case, production, _run, _snapshot, _write, CHANNEL


def history(case, count=520):
    ids = []
    for number in range(count):
        task = str(UUID(int=100_000 + number))
        row = {'task_id': task, 'state': 'SUCCESS',
               'spec': {'production_channel_id': CHANNEL, 'channel_id': CHANNEL},
               'result': {'old_media_diagnostics': 'preserved-' * 300}}
        _write(case.client, case.ns['JOB_PREFIX'] + task, row)
        case.client.zadd(case.ns['JOB_INDEX'], {task: number})
        ids.append(task)
    return ids


def test_terminal_history_beyond_500_promotes_without_removing_old_jobs(case, monkeypatch):
    ids = history(case)
    before = _snapshot(case)
    index = case.client.zrange(case.ns['JOB_INDEX'], 0, -1)
    original = case.client.zrange
    reads = []
    def bounded(key, start, stop, *args, **kwargs):
        reads.append((start, stop))
        return original(key, start, stop, *args, **kwargs)
    monkeypatch.setattr(case.client, 'zrange', bounded)
    assert _run(case)['status'] == 'promoted'
    assert reads and all(0 < stop - start + 1 <= 500 for start, stop in reads)
    assert original(case.ns['JOB_INDEX'], 0, -1) == index
    assert all(case.client.dump(case.ns['JOB_PREFIX'] + task) == before[case.ns['JOB_PREFIX'] + task]
               for task in ids)


@pytest.mark.parametrize('kind', ['active', 'delivery', 'derived', 'manifest'])
def test_late_active_or_retained_delivery_cannot_be_hidden_by_old_history(case, kind):
    ids = history(case)
    key = case.ns['JOB_PREFIX'] + ids[-1]
    row = json.loads(case.client.get(key))
    if kind == 'active':
        row['state'] = 'PROGRESS'
    elif kind == 'delivery':
        row['spec']['production_delivery'] = False  # Presence itself is a fence.
    elif kind == 'derived':
        row['spec']['production_derived_from'] = ids[0]
    else:
        row['result']['delivery_manifest_key'] = 'retained-private-delivery'
    _write(case.client, key, row)
    before = _snapshot(case)
    code = 'series_channel_busy' if kind == 'active' else 'series_delivery_family_pending'
    with pytest.raises(case.ns['SeriesPromotionError'], match=code):
        _run(case)
    assert _snapshot(case) == before


def test_compact_idle_snapshot_keeps_all_guards_without_retaining_media_payloads(case):
    ids = history(case)
    snap = case.ns['_Snapshot'](case.client)
    case.ns['_idle'](snap, CHANNEL, CHANNEL)
    assert 'old_media_diagnostics' not in repr(snap.values)
    key = case.ns['JOB_PREFIX'] + ids[-1]
    assert snap.values[key][0] == 'idle_job'
    full = snap.object(key)
    assert full['result']['old_media_diagnostics']
    assert snap.values[key] == ('string', case.client.get(key))
    with case.client.pipeline() as pipe:
        pipe.watch(*snap.values)
        snap.compare(pipe)
    # Once a source is used as full publication evidence, bind all its bytes.
    full['result']['old_media_diagnostics'] = 'changed'
    _write(case.client, key, full)
    with case.client.pipeline() as pipe:
        pipe.watch(*snap.values)
        with pytest.raises(case.ns['SeriesPromotionError'], match='series_state_changed'):
            snap.compare(pipe)


@pytest.mark.parametrize('change', ['active', 'destination', 'delivery', 'append', 'replace'])
def test_registry_and_late_job_changes_are_rechecked_before_commit(case, change):
    ids = history(case)
    snap = case.ns['_Snapshot'](case.client)
    case.ns['_idle'](snap, CHANNEL, CHANNEL)
    key = case.ns['JOB_PREFIX'] + ids[-1]
    row = json.loads(case.client.get(key))
    if change == 'active':
        row['state'] = 'PROGRESS'
    elif change == 'destination':
        row['spec']['production_channel_id'] = 'another-channel'
    elif change == 'delivery':
        row['spec']['production_delivery'] = True
    else:
        if change == 'replace':
            case.client.zrem(case.ns['JOB_INDEX'], ids[-1])
        case.client.zadd(case.ns['JOB_INDEX'], {str(UUID(int=999_999)): 1000})
    _write(case.client, key, row)
    with case.client.pipeline() as pipe:
        pipe.watch(*snap.values)
        with pytest.raises(case.ns['SeriesPromotionError'], match='series_state_changed'):
            snap.compare(pipe)


def test_changing_registry_during_pagination_is_not_a_complete_snapshot(case, monkeypatch):
    history(case)
    original = case.client.zrange
    changed = False
    def read(key, start, stop, *args, **kwargs):
        nonlocal changed
        result = original(key, start, stop, *args, **kwargs)
        if not changed:
            changed = True
            case.client.zadd(key, {str(UUID(int=999_999)): 1000})
        return result
    monkeypatch.setattr(case.client, 'zrange', read)
    with pytest.raises(case.ns['SeriesPromotionError'], match='series_state_changed'):
        case.ns['_Snapshot'](case.client).read(case.ns['JOB_INDEX'], 'zset')
