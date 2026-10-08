from datetime import datetime, timedelta, timezone
import json

import fakeredis
import pytest

from app.services import audience_trends as trends, audience_strategy as strategy, youtube_analytics as analytics

NOW = datetime(2026, 9, 23, 22, tzinfo=timezone.utc)
CHANNEL = 'UC5v9AvNtD3PTLgo6m1jROOA'


def feed(title='Nvidia earnings', date='Wed, 23 Sep 2026 14:00:00 -0700'):
    return (f'<rss xmlns:ht="https://trends.google.com/trending/rss"><channel><item><title>{title}</title>'
        f'<pubDate>{date}</pubDate><ht:approx_traffic>20K+</ht:approx_traffic></item></channel></rss>').encode()


def test_feed_has_observed_search_demand_not_view_counts():
    row = trends.parse_feed(feed(), 'US', NOW)[0]
    assert row['traffic_lower_bound'] == 20000 and row['region'] == 'US'
    assert row['source'] == 'google_trends_rss' and 'views' not in row
    assert trends.parse_feed(feed(date='Wed, 23 Sep 2020 14:00:00 -0700'), 'US', NOW) == []
    with pytest.raises(ValueError):
        trends.parse_feed(b'<!DOCTYPE rss>' + feed(), 'US', NOW)


def test_refresh_partial_failure_preserves_history_and_never_runs_paid_requests(monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=True)
    calls = []
    def fetch(region, now):
        calls.append(region)
        if region == 'IN':
            raise OSError('source unavailable')
        return {'region': region, 'observed_at': now.isoformat(), 'rows': trends.parse_feed(feed(), region, now)}
    monkeypatch.setattr(trends, '_fetch', fetch)
    assert trends.refresh(client=client, now=NOW)['regions_unavailable'] == ['IN']
    assert trends.refresh(client=client, now=NOW)['status'] == 'not_due'
    assert len(calls) == 6
    first = trends.snapshot(client=client, now=NOW)
    assert len(first['signals']) == 1 and len(first['signals'][0]['regions']) == 5
    assert first['signals'][0]['observed_days'] == ['2026-09-23']
    assert first['signals'][0]['fresh']
    client.delete(trends.PREFIX + 'refresh')
    def fail(*args): raise OSError()
    monkeypatch.setattr(trends, '_fetch', fail)
    assert trends.refresh(client=client, now=NOW + timedelta(days=1))['status'] == 'unavailable'
    old = trends.snapshot(client=client, now=NOW + timedelta(days=1))
    assert len(old['signals']) == 1 and not old['signals'][0]['fresh']
    assert not any('production_spend' in key for key in client.scan_iter())


def test_niche_filter_excludes_unrelated_sports_and_substring_accidents():
    rows = [{'term': title, 'fresh': True} for title in ['Nvidia earnings', 'football final', 'river banknote', 'bank lending']]
    matched = trends.relevant({'channel_id': CHANNEL}, data={'signals': rows})
    assert [r['term'] for r in matched] == ['Nvidia earnings', 'bank lending']


def test_settings_compare_and_swap_does_not_overwrite_another_edit():
    client = fakeredis.FakeRedis(decode_responses=True)
    assert strategy.read_settings(CHANNEL, client=client)['languages'] == ['en', 'es', 'pt', 'hi', 'ar']
    saved = strategy.save_settings(CHANNEL, 0, {'trend_enabled': False}, client=client)
    assert saved['revision'] == 1
    with pytest.raises(ValueError, match='changed'):
        strategy.save_settings(CHANNEL, 0, {'trend_enabled': True}, client=client)
    assert strategy.read_settings(CHANNEL, client=client) == saved


def test_same_format_retention_feedback_never_mixes_short_and_long(monkeypatch):
    def row(kind, p):
        return {'content_type': kind, 'views': 2000, 'engagedViews': 500,
            'title': kind, 'averageViewPercentage': p, 'averageViewDuration': 22,
            'retention': [[.01, 1], [.1, .8], [.2, .45], [1, .4]]}
    videos = {str(i): row('SHORTS' if i < 3 else 'VIDEO_ON_DEMAND', 70 + i) for i in range(6)}
    monkeypatch.setattr(analytics, 'dashboard', lambda: {'channels': [{'channel_id': CHANNEL, 'status': 'fresh', 'videos': videos}]})
    short = analytics.editorial_guidance(CHANNEL)
    long = analytics.editorial_guidance(CHANNEL, content_type='VIDEO_ON_DEMAND')
    assert short['sample_size'] == long['sample_size'] == 3
    assert all(r['title'] == 'SHORTS' for r in short['higher_retention_examples'])
    assert all(r['title'] == 'VIDEO_ON_DEMAND' for r in long['higher_retention_examples'])
    assert short['observed_drop_examples'][0]['from_fraction'] == .1
    videos['0']['engagedViews'] = 10
    assert analytics.editorial_guidance(CHANNEL) is None


def test_writer_keeps_duration_facts_and_quality_requirements(monkeypatch):
    monkeypatch.setattr(strategy, 'read_settings', lambda channel: strategy.DEFAULT)
    monkeypatch.setattr(analytics, 'editorial_guidance', lambda *a, **k: None)
    short = strategy.writer_rule(CHANNEL, .5)
    assert 'preserve the requested duration' in short and 'Do not invent claims' in short
    assert 'turning point' in strategy.writer_rule(CHANNEL, 3)
