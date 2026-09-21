"""Real Redis CAS with mocked Google; no provider spending or network."""
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from app.services import youtube_analytics as analytics, studio_analytics, youtube_analytics_inventory as inventory
from test_youtube_metrics import case, job, _refresh, CHANNEL, CONNECTION, VIDEO, NOW


def table(rows, retention=False):
    names = ('elapsedVideoTimeRatio', 'audienceWatchRatio') if retention else analytics._HEADERS
    types = ('FLOAT', 'FLOAT') if retention else analytics._TYPES
    return {'kind': 'youtubeAnalytics#resultTable', 'columnHeaders': [
        {'name': name, 'dataType': kind, 'columnType': 'DIMENSION' if name in (
            'video', 'creatorContentType', 'elapsedVideoTimeRatio') else 'METRIC'}
        for name, kind in zip(names, types)], 'rows': rows}


def row(video=VIDEO, views=600, engaged=300, percentage=85.5):
    return [video, 'SHORTS', views, engaged, 140.5, 24.5, percentage, 3]


@pytest.fixture
def a(case, monkeypatch):
    c = case
    monkeypatch.setattr(analytics, 'metrics', c.module)
    monkeypatch.setattr(analytics, 'time', c.module.time)
    read_credentials = Mock(return_value=object())
    monkeypatch.setattr(analytics.access, 'read_credentials', read_credentials)
    service = Mock()
    query = service.reports.return_value.query.return_value.execute
    query.side_effect = [table([row()]), table([[.01, 1.1], [.1, .9], [1, .5]], True)]
    monkeypatch.setattr(analytics, '_service', Mock(return_value=service))
    owned = Mock(return_value={VIDEO: 'Owner public video'})
    monkeypatch.setattr(inventory, 'public_uploads', owned)
    _refresh(c)
    context = c.module._contexts(c.client)[0]
    return SimpleNamespace(c=c, context=context, service=service, query=query, read=read_credentials, inventory=owned)


def grant(a):
    a.c.client.set(analytics.access.PREFIX + CHANNEL, 'private-analytics-grant')


def snapshot(a):
    return {key: a.c.client.dump(key) for key in a.c.client.scan_iter('*')}


def test_no_grant_means_no_google_call_and_get_is_pure(a):
    before = snapshot(a)
    assert analytics.refresh([job()])['status'] == 'checked'
    assert analytics.dashboard()['channels'][0]['status'] == 'needs_permission'
    assert snapshot(a) == before
    a.read.assert_not_called(); analytics._service.assert_not_called()


def test_server_reads_only_public_owned_videos_and_keeps_true_metrics(a):
    grant(a)
    before = snapshot(a)
    analytics.refresh([job()])
    report = analytics.dashboard()['channels'][0]
    assert report['status'] == 'fresh'
    assert report['videos'][VIDEO]['averageViewPercentage'] == 85.5
    assert report['videos'][VIDEO]['retention'][0] == [.01, 1.1]
    args = a.service.reports.return_value.query.call_args_list[0].kwargs
    assert args['ids'] == 'channel==' + CHANNEL and args['filters'] == 'video==' + VIDEO
    assert args['dimensions'] == 'video,creatorContentType'
    assert args['endDate'] < '2026-09-07' and args['maxResults'] == 50
    assert all(call.kwargs == {'num_retries': 0} for call in a.query.call_args_list)
    assert 'private' not in json.dumps(report) and a.service.close.call_count == 1
    assert all(a.c.client.dump(key) == value for key, value in before.items())
    after = snapshot(a); analytics.dashboard(); assert snapshot(a) == after
    analytics.refresh([job()]); assert a.query.call_count == 2  # six-hour debounce


@pytest.mark.parametrize('change', ['private', 'missing', 'stale', 'different_channel'])
def test_old_job_cache_cannot_override_current_owner_inventory(a, change):
    grant(a)
    key = a.c.module._cache_key(a.context)
    cached = json.loads(a.c.client.get(key))
    if change == 'private': cached['videos'][VIDEO]['privacy_status'] = 'private'
    if change == 'missing': cached['videos'][VIDEO]['availability'] = 'unavailable'
    if change == 'stale': cached['videos'][VIDEO]['fetched_at'] = NOW - 3601
    if change == 'different_channel': cached['channel_id'] = 'other-channel'
    a.c.client.set(key, json.dumps(cached))
    a.inventory.return_value = {}
    analytics.refresh([job()])
    a.inventory.assert_called_once()
    analytics._service.assert_not_called()
    assert analytics.dashboard()['channels'][0]['videos'] == {}


def test_old_public_uploads_are_observed_without_any_current_connection_jobs(a):
    grant(a)
    analytics.refresh([])
    assert analytics.dashboard()['channels'][0]['videos'][VIDEO]['views'] == 600
    a.inventory.assert_called_once_with(a.read.return_value, CHANNEL)


def test_empty_report_is_unknown_not_zero(a):
    grant(a); a.query.side_effect = [table([])]
    analytics.refresh([job()])
    assert analytics.dashboard()['channels'][0]['videos'] == {}
    assert analytics.editorial_guidance(CHANNEL) is None
    assert a.query.call_count == 1


def test_real_lowercase_shorts_report_and_integer_duration_headers(a):
    grant(a)
    value = table([[VIDEO, 'shorts', 1241, 630, 216, 19, 62.419999999999995, 4]])
    for position in (4, 5):
        value['columnHeaders'][position]['dataType'] = 'INTEGER'
    a.query.side_effect = [value, table([[.01, 1.02], [1, .48]], True)]
    analytics.refresh([])
    report = analytics.dashboard()['channels'][0]
    assert report['status'] == 'fresh'
    assert report['videos'][VIDEO] == {'title': 'Owner public video', 'content_type': 'SHORTS',
        'views': 1241, 'engagedViews': 630, 'estimatedMinutesWatched': 216, 'averageViewDuration': 19,
        'averageViewPercentage': 62.419999999999995, 'subscribersGained': 4,
        'retention': [[.01, 1.02], [1, .48]]}
    stored = json.loads(a.c.client.get(analytics._key(a.context)))
    assert stored['videos'][VIDEO]['content_type'] == 'SHORTS'


@pytest.mark.parametrize('kind', ['SHORTS', 'VIDEO_ON_DEMAND', 'LIVE_STREAM', 'STORY', 'UNSPECIFIED'])
def test_documented_and_observed_content_type_spellings_agree(kind):
    assert analytics._videos(table([[VIDEO, kind, 1, 1, 1, 1, 1, 0]]), [VIDEO]) == analytics._videos(
        table([[VIDEO, kind.lower(), 1, 1, 1, 1, 1, 0]]), [VIDEO])


@pytest.mark.parametrize('kind', ['short', ' shorts', 'Shorts', 'unknown_type', None, {}, 1])
def test_unrecognized_content_type_cannot_become_a_shorts_observation(kind):
    with pytest.raises(analytics.metrics.YouTubeMetricsError):
        analytics._videos(table([[VIDEO, kind, 1, 1, 1, 1, 1, 0]]), [VIDEO])


@pytest.mark.parametrize('damage', ['duplicate', 'foreign', 'negative', 'nan', 'boolean', 'headers', 'fractional_count', 'missing_column'])
def test_invalid_report_never_publishes_partial_metrics(a, damage):
    grant(a); value = table([row()])
    if damage == 'duplicate': value['rows'].append(row())
    if damage == 'foreign': value['rows'][0][0] = 'x1234567890'
    if damage == 'negative': value['rows'][0][4] = -1
    if damage == 'nan': value['rows'][0][4] = float('nan')
    if damage == 'boolean': value['rows'][0][4] = True
    if damage == 'headers': value['columnHeaders'][4]['name'] = 'fakeMinutes'
    if damage == 'fractional_count': value['rows'][0][2] = .5
    if damage == 'missing_column': value['rows'][0].pop()
    a.query.side_effect = [value]
    analytics.refresh([job()]); report = analytics.dashboard()['channels'][0]
    assert report['status'] == 'unavailable' and report['videos'] == {}
    assert analytics.editorial_guidance(CHANNEL) is None


@pytest.mark.parametrize('which', ['connection', 'credential', 'epoch', 'grant', 'membership'])
def test_inflight_connection_change_discards_whole_report(a, which):
    grant(a)
    def query(**kwargs):
        if which == 'connection': a.c.client.set(a.c.auth.CHANNEL_PREFIX + CHANNEL, '{}')
        if which == 'credential': a.c.client.set(a.c.auth.CREDENTIAL_PREFIX + CHANNEL, 'new-credential')
        if which == 'epoch': a.c.client.incr(a.c.auth.AUTH_EPOCH_KEY)
        if which == 'grant': a.c.client.set(analytics.access.PREFIX + CHANNEL, 'new-grant')
        if which == 'membership': a.c.client.srem(a.c.auth.CHANNEL_INDEX_KEY, CHANNEL)
        return table([])
    a.query.side_effect = query
    analytics.refresh([job()]); assert a.c.client.get(analytics._key(a.context)) is None


def test_retention_failure_preserves_successful_aggregate(a):
    grant(a); a.query.side_effect = [table([row()]), table([[.2, 1], [.1, .9]], True)]
    analytics.refresh([job()]); report = analytics.dashboard()['channels'][0]
    assert report['status'] == 'fresh' and report['videos'][VIDEO]['views'] == 600
    assert 'retention' not in report['videos'][VIDEO]


def test_feedback_needs_three_meaningful_shorts_and_never_uses_stale_report(a):
    grant(a); analytics.refresh([job()])
    key = analytics._key(a.context); report = json.loads(a.c.client.get(key))
    for number in (1, 2):
        report['videos'][f'b{number}234567890'] = {**deepcopy(report['videos'][VIDEO]),
            'title': f'Public title {number}', 'averageViewPercentage': 70 + number}
    a.c.client.set(key, json.dumps(report))
    advice = analytics.editorial_guidance(CHANNEL)
    assert advice['sample_size'] == 3 and 'causality' in advice['instruction']
    assert 'channel_id' not in json.dumps(advice) and 'video_id' not in json.dumps(advice)
    report['videos'][VIDEO]['engagedViews'] = 99
    a.c.client.set(key, json.dumps(report)); assert analytics.editorial_guidance(CHANNEL) is None
    report['videos'][VIDEO]['engagedViews'] = 300
    report['videos'][VIDEO]['content_type'] = 'VIDEO_ON_DEMAND'
    a.c.client.set(key, json.dumps(report)); assert analytics.editorial_guidance(CHANNEL) is None
    a.c.clock[0] += analytics.STALE_SECONDS + 1
    assert analytics.dashboard()['channels'][0]['status'] == 'stale'
    assert analytics.editorial_guidance(CHANNEL) is None


def test_ui_escapes_titles_and_shows_only_needed_consent(a):
    grant(a); analytics.refresh([job()]); report = analytics.dashboard()
    report['channels'][0]['title'] = '<script>bad</script>'
    html = studio_analytics.render(report)
    assert '<script>bad' not in html and '&lt;script&gt;' in html
    assert 'izleyici tutma eğrisi' in html and 'Google ile' not in html
    a.c.client.delete(analytics.access.PREFIX + CHANNEL)
    html = studio_analytics.render(analytics.dashboard())
    assert '/analytics/connect/' + CHANNEL in html and 'Henüz doğrulanmış' in html


def google_failure(reason):
    error = RuntimeError('Private upstream message must not appear in Studio')
    error.resp = SimpleNamespace(status=403 if reason != 'unavailable' else 503)
    error.content = json.dumps({'error': {'errors': [{'reason': reason}]}}).encode()
    return error


@pytest.mark.parametrize('reason,interval', [
    ('accessNotConfigured', analytics.RECOVERY_SECONDS),
    ('unavailable', analytics.RECOVERY_SECONDS),
    ('quotaExceeded', analytics.REFRESH_SECONDS),
    ('forbidden', analytics.REFRESH_SECONDS),
])
def test_service_recovers_without_consent_or_immediate_retry(a, reason, interval):
    grant(a)
    before = snapshot(a)
    a.query.side_effect = [google_failure(reason), table([])]
    analytics.refresh([])
    failed = analytics.dashboard()['channels'][0]
    assert failed['status'] != 'fresh' and not failed['videos']
    assert 'Private upstream' not in json.dumps(failed)
    assert failed['next_check_at'] == a.c.module._iso(NOW + interval)
    assert all(a.c.client.dump(key) == value for key, value in before.items())
    # The fixture clock moves independently of FakeRedis' real TTL clock.
    a.c.client.delete(analytics.LOCK_PREFIX + CHANNEL)
    a.c.clock[0] = NOW + interval - 1
    analytics.refresh([])
    assert a.query.call_count == 1
    a.c.clock[0] += 1
    analytics.refresh([])
    assert a.query.call_count == 2
    assert analytics.dashboard()['channels'][0]['status'] == 'fresh'
    assert a.c.client.get(analytics.access.PREFIX + CHANNEL) == 'private-analytics-grant'
    a.c.client.delete(analytics.LOCK_PREFIX + CHANNEL)
    a.c.clock[0] += analytics.RECOVERY_SECONDS
    analytics.refresh([])
    assert a.query.call_count == 2  # Success restores the normal six-hour interval.


def test_failed_refresh_retains_old_observations_without_editorial_advice(a):
    grant(a)
    analytics.refresh([])
    old = analytics.dashboard()['channels'][0]['videos']
    a.c.clock[0] += analytics.REFRESH_SECONDS
    a.c.client.delete(analytics.LOCK_PREFIX + CHANNEL)
    a.query.side_effect = google_failure('accessNotConfigured')
    analytics.refresh([])
    value = analytics.dashboard()['channels'][0]
    assert value['status'] == 'unavailable' and value['reason'] == 'api_disabled'
    assert value['videos'] == old and analytics.editorial_guidance(CHANNEL) is None
    before = snapshot(a)
    html = studio_analytics.render(analytics.dashboard())
    assert 'henüz açık görmüyor' in html and 'Sonraki otomatik kontrol' in html
    assert 'Google ile izleyici analizini bağla' not in html
    assert snapshot(a) == before
