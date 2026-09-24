from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from uuid import uuid4

import fakeredis
import pytest

from app.services import channel_cadence as cadence, studio_state as jobs

C, M = 'UC5v9AvNtD3PTLgo6m1jROOA', 'UCgvESYtYbn2w9R2ExBOF_cw'
NOW = datetime(2026, 9, 24, 10, tzinfo=timezone.utc).timestamp()


def source(channel=C, kind='shorts'):
    return {'task_id': str(uuid4()), 'parent_id': None,
        'spec': {'production_channel_id': channel, 'format': kind}}


def test_five_shorts_one_long_are_independent_for_each_channel():
    c = fakeredis.FakeRedis(decode_responses=True)
    for channel in (C, M):
        for _ in range(5): assert cadence.publication_slot(source(channel), client=c, now=NOW)
        assert not cadence.publication_slot(source(channel), client=c, now=NOW)
        assert cadence.publication_slot(source(channel, 'landscape'), client=c, now=NOW)
        assert not cadence.publication_slot(source(channel, 'landscape'), client=c, now=NOW)
    assert cadence.publication_slot(source('separate-animation', 'landscape'), client=c, now=NOW)


def test_midnight_pending_guard_prevents_carryover_exceeding_next_day():
    c = fakeredis.FakeRedis(decode_responses=True)
    before = datetime(2026, 9, 24, 20, 59, tzinfo=timezone.utc).timestamp()
    after = before + 120
    old = source(kind='landscape')
    assert cadence.publication_slot(old, client=c, now=before)
    assert not cadence.publication_slot(source(kind='landscape'), client=c, now=after)
    cadence.publication_completed(old, client=c, now=after)
    assert not cadence.publication_slot(source(kind='landscape'), client=c, now=after)
    assert cadence.publication_slot(source(kind='landscape'), client=c, now=after + 86400)


def test_lost_enqueue_claim_does_not_expire_and_repeated_source_uses_same_slot():
    c = fakeredis.FakeRedis(decode_responses=True); row = source(kind='landscape')
    assert cadence.publication_slot(row, client=c, now=NOW)
    assert cadence.publication_slot(row, client=c, now=NOW + 86400)
    assert not cadence.publication_slot(source(kind='landscape'), client=c, now=NOW + 86400)
    assert len(c.hgetall(cadence.keys(C, now=NOW)[2])) == 1


def test_concurrent_publishers_cannot_take_sixth_slot():
    c = fakeredis.FakeRedis(decode_responses=True)
    def run(_):
        try: return cadence.publication_slot(source(), client=c, now=NOW)
        except Exception: return False  # WATCH conflict is a closed admission.
    with ThreadPoolExecutor(max_workers=12) as pool:
        passed = list(pool.map(run, range(30)))
    assert sum(passed) == 5


def test_production_counts_today_publications_including_yesterdays_repaired_root():
    c = fakeredis.FakeRedis(decode_responses=True); row = source(kind='landscape')
    assert cadence.publication_slot(row, client=c, now=NOW - 86400)
    cadence.publication_completed(row, client=c, now=NOW)
    with c.pipeline() as p:
        assert cadence.production_slot(p, C, 'long', str(uuid4()), now=NOW) is False
        assert cadence.production_slot(p, M, 'long', str(uuid4()), now=NOW)


def test_retry_lineage_does_not_multiply_root_allowance():
    c = fakeredis.FakeRedis(decode_responses=True); parent = source()
    child = source(); child['parent_id'] = parent['task_id']; parent['retry_child_task_id'] = child['task_id']
    c.set(jobs.JOB_PREFIX + parent['task_id'], json.dumps(parent))
    assert cadence.publication_slot(child, client=c, now=NOW)
    pending = c.hgetall(cadence.keys(C, now=NOW)[2])
    assert pending == {parent['task_id']: 'shorts'}
    with pytest.raises(ValueError): cadence.publication_slot(parent, client=c, now=NOW)


def test_malformed_or_unrelated_parent_fails_closed():
    c = fakeredis.FakeRedis(decode_responses=True); row = source(); row['parent_id'] = str(uuid4())
    with pytest.raises(ValueError): cadence.publication_slot(row, client=c, now=NOW)
    assert not c.keys(cadence.PREFIX + '*')


def test_daily_mix_uses_real_long_queue_then_short_topics_without_resetting_cursor(monkeypatch):
    from app.services import content_plan as plan, channel_production as production
    c = fakeredis.FakeRedis(decode_responses=True)
    profile = {'channel_id': C, 'profile_revision': str(uuid4()), 'default_language': 'tr'}
    c.set(production.PROFILE_PREFIX + C, plan._raw(profile))
    c.hset(production.CHANNEL_STATE_PREFIX + C, mapping={'cursor': '0', 'spent': 'preserved'})
    topics = ['Kaynaklı hikâye ' + str(i) for i in range(5)]
    assert cadence.daily_editorial(C, {}, client=c, now=NOW)['format'] == 'landscape'
    result = cadence.install_daily_long(profile, topics, client=c, now=NOW)
    document = plan.read(C, client=c)
    assert document['items'][0]['id'] == result['item_id']
    assert document['items'][0]['format'] == 'long' and document['items'][0]['series'] is None
    assert all(t in document['items'][0]['brief'] for t in topics)
    assert plan.owns_channel(C, client=c)
    assert cadence.install_daily_long(profile, topics, client=c, now=NOW)['status'] == 'daily_long_planned'
    assert len(plan.read(C, client=c)['items']) == 1
    assert c.hgetall(production.CHANNEL_STATE_PREFIX + C) == {'cursor': '0', 'spent': 'preserved'}
    c.hset(cadence.keys(C, now=NOW)[0], str(uuid4()), 'long')
    assert cadence.daily_editorial(C, {}, client=c, now=NOW)['format'] == 'shorts'


def test_daily_long_never_overwrites_an_unfinished_owner_queue():
    from app.services import content_plan as plan, channel_production as production
    c = fakeredis.FakeRedis(decode_responses=True)
    profile = {'channel_id': C, 'profile_revision': str(uuid4()), 'default_language': 'tr'}
    c.set(production.PROFILE_PREFIX + C, plan._raw(profile))
    cadence.install_daily_long(profile, ['Cited topic'], client=c, now=NOW)
    before = c.get(plan.PLAN_PREFIX + C)
    with pytest.raises(ValueError): cadence.install_daily_long(profile, ['New topic'], client=c, now=NOW + 86400)
    assert c.get(plan.PLAN_PREFIX + C) == before


def test_full_completed_plan_is_archived_without_breaking_series_dependencies():
    from app.services import content_plan as plan, channel_production as production
    c = fakeredis.FakeRedis(decode_responses=True)
    profile = {'channel_id': C, 'profile_revision': str(uuid4()), 'default_language': 'tr'}
    c.set(production.PROFILE_PREFIX + C, plan._raw(profile))
    entries = []
    for i in range(plan.MAX_ITEMS):
        entry = plan.item('Completed ' + str(i), 'Verified previous topic',
            depends_on=[entries[-1]['id']] if entries else [])
        entries.append(entry)
        c.set(plan.COMPLETION_PREFIX + entry['id'], plan._raw({'immutable_receipt': i}))
    document = {'version': 1, 'channel_id': C, 'revision': str(uuid4()), 'enabled': True,
        'after_queue': 'auto_shorts', 'items': entries, 'updated_at': entries[-1]['created_at']}
    before = plan._raw(document); c.set(plan.PLAN_PREFIX + C, before)
    original = {key: c.get(key) for key in c.scan_iter(match=plan.COMPLETION_PREFIX + '*')}
    result = cadence.install_daily_long(profile, ['Source-backed topic'], client=c, now=NOW)
    assert [row['id'] for row in plan.read(C, client=c)['items']] == [result['item_id']]
    audit = json.loads(c.get(cadence.PREFIX + 'daily_plan:' + C + ':2026-09-24'))
    assert audit['original_plan'] == before and audit['completed_history'] == original
    assert {key: c.get(key) for key in original} == original
