from datetime import datetime, timedelta, timezone

from celery.beat import ScheduleEntry
import fakeredis

from app.celery_app import celery
from app.services import audience_trends as trends


def test_fresh_scheduler_reaches_durable_trend_gate_within_five_minutes(monkeypatch):
    now = datetime(2026, 9, 25, 3, tzinfo=timezone.utc)
    name, config = next((name, row) for name, row in celery.conf.beat_schedule.items()
                        if row['task'] == 'app.production_tasks.observe_audience_trends')
    entry = ScheduleEntry(name=name, app=celery, last_run_at=now - timedelta(seconds=301),
                          **config)
    entry.schedule.nowfun = lambda: now
    assert entry.is_due().is_due
    assert 0 < entry.is_due().next <= 300
    assert config['options']['expires'] < 300

    server = fakeredis.FakeServer()
    first_worker = fakeredis.FakeRedis(server=server, decode_responses=True)
    fetched = []
    def fetch(region, at):
        fetched.append(region)
        return {'region': region, 'observed_at': at.isoformat(), 'rows': []}
    monkeypatch.setattr(trends, '_fetch', fetch)
    assert trends.refresh(client=first_worker, now=now)['status'] == 'fresh'
    restarted_worker = fakeredis.FakeRedis(server=server, decode_responses=True)
    for minutes in range(5, 120, 5):
        assert trends.refresh(client=restarted_worker,
                              now=now + timedelta(minutes=minutes))['status'] == 'not_due'
    assert sorted(fetched) == sorted(trends.REGIONS)
    assert restarted_worker.ttl(trends.PREFIX + 'refresh') > 7100
