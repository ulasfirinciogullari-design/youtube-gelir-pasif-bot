"""One next-day master may start after six public deliveries, never six more uploads."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from uuid import uuid4

import pytest
from redis.exceptions import WatchError

from app.services import channel_cadence as cadence, content_plan as plan, studio_state as jobs
from test_content_plan import case, CHANNEL

NOW = datetime(2026, 9, 24, 18, tzinfo=timezone.utc).timestamp()
MIDNIGHT = datetime(2026, 9, 24, 21, tzinfo=timezone.utc).timestamp()


def complete(case, kinds=('landscape', *(['shorts'] * 5))):
    for kind in kinds:
        row = {'task_id': str(uuid4()), 'parent_id': None,
               'spec': {'format': kind, 'production_channel_id': CHANNEL}}
        assert cadence.publication_slot(row, client=case.client, now=NOW)
        cadence.publication_completed(row, client=case.client, now=NOW)


def prepare(case):
    doc = plan.change(CHANNEL, case.document['revision'], 'settings',
        payload={'enabled': True, 'after_queue': 'auto_shorts'})
    for entry in doc['items']:
        case.client.set(plan.COMPLETION_PREFIX + entry['id'], plan._raw({'public_receipt': str(uuid4())}))


def snapshot(client): return {key: client.dump(key) for key in client.scan_iter()}


def test_full_public_day_starts_one_tomorrow_long_and_waits_for_istanbul_midnight(case):
    c = case.client; prepare(case); complete(case)
    published = c.hgetall(cadence.keys(CHANNEL, now=NOW)[1]); before = snapshot(c)
    selected = cadence.daily_editorial(CHANNEL, {}, client=c, now=NOW)
    assert selected['reason_code'] == 'owner_next_day_stock' and selected['format'] == 'landscape'
    planned = cadence.install_daily_long(case.profile, ['Verified source-backed topic'],
        client=c, now=NOW, advance=True)
    assert planned['status'] == 'daily_long_planned'
    marker = json.loads(c.get(cadence.ADVANCE_PREFIX + planned['item_id']))
    assert marker['source_day'] == '2026-09-24' and marker['day'] == '2026-09-25'
    assert marker['completed_publications'] == published
    reservation = plan._reserve(CHANNEL, now=NOW)
    assert reservation['status'] == 'reserved'
    task = reservation['task_id']; row = json.loads(c.get(jobs.JOB_PREFIX + task))
    assert c.hgetall(cadence.keys(CHANNEL, now=NOW)[0]) == {}
    assert c.hgetall(cadence.keys(CHANNEL, now=MIDNIGHT)[0]) == {task: 'long'}
    assert not cadence.publication_slot(row, client=c, now=MIDNIGHT - .01)
    assert c.sismember(cadence.WAITING_KEY, task)
    assert not c.exists(cadence.PREFIX + 'publication:' + task)
    assert cadence.publication_slot(row, client=c, now=MIDNIGHT)
    assert not c.sismember(cadence.WAITING_KEY, task)
    assert cadence.publication_slot(row, client=c, now=MIDNIGHT + 1)
    cadence.publication_completed(row, client=c, now=MIDNIGHT + 2)
    after = cadence.snapshot(CHANNEL, client=c, now=MIDNIGHT + 3)
    assert after['counts']['published'] == {'long': 1, 'shorts': 0}
    assert cadence.daily_editorial(CHANNEL, {}, client=c, now=MIDNIGHT + 3)['format'] == 'shorts'
    for key, value in before.items():
        if key != plan.PLAN_PREFIX + CHANNEL: assert c.dump(key) == value


@pytest.mark.parametrize('kinds', [(), ('landscape',), tuple(['shorts'] * 5), ('landscape', *(['shorts'] * 4))])
def test_partial_or_merely_started_day_cannot_preproduce_tomorrow(case, kinds):
    c = case.client; prepare(case); complete(case, kinds)
    c.hset(cadence.keys(CHANNEL, now=NOW)[0], mapping={str(uuid4()): kind for kind in ['long', *(['shorts'] * 5)]})
    before = snapshot(c)
    assert cadence.daily_editorial(CHANNEL, {}, client=c, now=NOW)['reason_code'] == 'owner_daily_mix'
    with pytest.raises(ValueError):
        cadence.install_daily_long(case.profile, ['Verified topic'], client=c, now=NOW, advance=True)
    assert snapshot(c) == before


def test_repeated_concurrent_installation_and_dispatch_do_not_expand_stock_or_today_limits(case):
    c = case.client; prepare(case); complete(case)
    def install(_):
        return cadence.install_daily_long(case.profile, ['Same verified topic'], client=c, now=NOW, advance=True)
    with ThreadPoolExecutor(max_workers=6) as pool: outcomes = list(pool.map(install, range(12)))
    assert all(row['status'] == 'daily_long_planned' for row in outcomes)
    assert len(plan.read(CHANNEL)['items']) == 3
    def reserve(_):
        try: return plan._reserve(CHANNEL, now=NOW)
        except WatchError: return {'status': 'concurrent_reservation_rejected'}
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(reserve, range(12)))
    assert sum(row['status'] == 'reserved' for row in results) == 1
    assert len(c.hgetall(cadence.keys(CHANNEL, now=MIDNIGHT)[0])) == 1
    for kind in ('shorts', 'landscape'):
        row = {'task_id': str(uuid4()), 'parent_id': None, 'spec': {'format': kind, 'production_channel_id': CHANNEL}}
        assert not cadence.publication_slot(row, client=c, now=NOW)


@pytest.mark.parametrize('damage', ['day', 'channel', 'receipt', 'public_proof', 'brief'])
def test_changed_advance_identity_or_missing_original_publications_blocks_dispatch(case, damage):
    c = case.client; prepare(case); complete(case)
    outcome = cadence.install_daily_long(case.profile, ['Verified topic'], client=c, now=NOW, advance=True)
    item = outcome['item_id']; key = cadence.ADVANCE_PREFIX + item
    row = json.loads(c.get(key))
    if damage == 'day': row['day'] = '2026-09-26'; c.set(key, plan._raw(row))
    if damage == 'channel': row['channel_id'] = 'separate'; c.set(key, plan._raw(row))
    if damage == 'receipt': c.delete(cadence.PREFIX + 'daily_plan:' + CHANNEL + ':2026-09-25')
    if damage == 'public_proof': c.hdel(cadence.keys(CHANNEL, now=NOW)[1], next(iter(row['completed_publications'])))
    if damage == 'brief':
        document = plan.read(CHANNEL); document['items'][-1]['brief'] = 'Changed by another writer'
        c.set(plan.PLAN_PREFIX + CHANNEL, plan._raw(document))
    before = snapshot(c)
    with pytest.raises((ValueError, plan.ContentPlanError)):
        plan._reserve(CHANNEL, now=NOW)
    assert snapshot(c) == before


def test_missed_start_day_uses_current_allowance_without_reopening_an_old_day(case):
    c = case.client; prepare(case); complete(case)
    cadence.install_daily_long(case.profile, ['Verified topic'], client=c, now=NOW, advance=True)
    late = MIDNIGHT + 86400
    reservation = plan._reserve(CHANNEL, now=late)
    assert reservation['status'] == 'reserved'
    assert c.hgetall(cadence.keys(CHANNEL, now=MIDNIGHT)[0]) == {}
    assert c.hgetall(cadence.keys(CHANNEL, now=late)[0]) == {reservation['task_id']: 'long'}
