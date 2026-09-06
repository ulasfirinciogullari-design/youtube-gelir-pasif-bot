"""Mocked Google and real in-memory Redis Lua; no credentials or network."""
import ast
from copy import deepcopy
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import fakeredis
import pytest


CHANNEL = 'UC_channel_A123'
CONNECTION = 'connection_one_123'
SOURCE = '11111111-1111-4111-8111-111111111111'
VIDEO = 'aB123456789'
NOW = 1788720000.0


def job(source=SOURCE, video=VIDEO, channel=CHANNEL, connection=CONNECTION):
    return {'task_id': source, 'kind': 'render', 'state': 'SUCCESS',
            'spec': {'format': 'shorts'}, 'result': {'task_id': source, 'youtube': {
                'video_id': video, 'target_channel_id': channel, 'connection_id': connection,
                'uploaded_at': '2026-09-06T12:00:00+00:00', 'privacy_status': 'public'}}}


@pytest.fixture
def case():
    path = Path(__file__).resolve().parents[1] / 'app/services/youtube_metrics.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    tree.body = [n for n in tree.body if not isinstance(n, ast.ImportFrom) or n.module != 'app.config']
    module = ModuleType('isolated_youtube_metrics')
    exec(compile(tree, str(path), 'exec'), module.__dict__)
    client = fakeredis.FakeRedis(decode_responses=True)
    module._redis = lambda: client
    clock = [NOW]
    module.time = SimpleNamespace(time=lambda: clock[0])
    credentials = SimpleNamespace(token='memory-only-token', refresh=Mock())
    auth = SimpleNamespace(CHANNEL_INDEX_KEY='oauth:index', CHANNEL_PREFIX='oauth:channel:',
                           CREDENTIAL_PREFIX='oauth:credential:', AUTH_EPOCH_KEY='oauth:epoch',
                           _decrypt_json=Mock(return_value={'version': 3, 'channel_id': CHANNEL,
                                'connection_id': CONNECTION, 'refresh_token': 'must-never-leak'}),
                           _credential_from_refresh_token=Mock(return_value=credentials), GoogleRequest=Mock(return_value=Mock()))
    module._auth = lambda: auth
    channel = {'id': CHANNEL, 'connection_id': CONNECTION, 'title': 'Channel actual name'}
    client.sadd(auth.CHANNEL_INDEX_KEY, CHANNEL)
    client.set(auth.CHANNEL_PREFIX + CHANNEL, json.dumps(channel))
    client.set(auth.CREDENTIAL_PREFIX + CHANNEL, 'encrypted-secret-do-not-expose')
    client.set(auth.AUTH_EPOCH_KEY, '12')
    channel_response = {'items': [{'id': CHANNEL, 'snippet': {'title': 'Actual channel'},
                                  'statistics': {'viewCount': '1200', 'subscriberCount': '30',
                                                 'hiddenSubscriberCount': False, 'videoCount': '2'}}]}
    video_response = {'items': [{'id': VIDEO, 'snippet': {'channelId': CHANNEL, 'title': 'Actual upload', 'publishedAt': '2026-09-06T12:00:00Z'},
                                'statistics': {'viewCount': '12', 'likeCount': '0', 'commentCount': '2'},
                                'status': {'privacyStatus': 'public'}, 'contentDetails': {'duration': 'PT30S'}}]}
    service = Mock()
    service.channels.return_value.list.return_value.execute.return_value = channel_response
    service.videos.return_value.list.return_value.execute.return_value = video_response
    module._service = Mock(return_value=service)
    return SimpleNamespace(**locals())


def _refresh(c, jobs=None):
    return c.module.refresh_dashboard_metrics(jobs if jobs is not None else [job()])


def _google_count(c):
    return c.service.channels.return_value.list.return_value.execute.call_count


def _unlock(c):
    for key in c.client.scan_iter(c.module.LOCK_PREFIX + '*'):
        c.client.delete(key)


def test_cached_get_never_calls_google_or_decrypts_or_writes(case):
    c = case
    before = {key: c.client.dump(key) for key in c.client.scan_iter('*')}
    result = c.module.get_dashboard_metrics([job()])
    assert result['channels'][0]['title'] == 'Channel actual name'
    assert result['channels'][0]['view_count'] is None
    assert result['channels'][0]['subscriber_count'] is None
    assert result['channels'][0]['status'] == 'unavailable'
    assert result['videos'][SOURCE]['view_count'] is None
    assert result['updated_at'] is None and result['refresh_after_seconds'] == 300
    c.module._service.assert_not_called()
    c.auth._decrypt_json.assert_not_called()
    assert before == {key: c.client.dump(key) for key in c.client.scan_iter('*')}


def test_refresh_reads_only_exact_owned_channel_and_verified_ids_and_closes_service(case):
    c = case
    original_jobs = [job()]
    before = deepcopy(original_jobs)
    result = _refresh(c, original_jobs)
    assert original_jobs == before
    assert result['channels'][0]['view_count'] == 1200
    assert result['channels'][0]['video_count'] == 2  # API public-video count, not application job count.
    assert result['channels'][0]['subscriber_count'] == 30
    assert result['channels'][0]['status'] == 'fresh'
    assert result['videos'][SOURCE]['view_count'] == 12
    assert result['videos'][SOURCE]['like_count'] == 0
    assert result['videos'][SOURCE]['channel_title'] == 'Actual channel'
    assert result['videos'][SOURCE]['privacy_status'] == 'public'
    assert result['videos'][SOURCE]['duration'] == 'PT30S'
    assert 'format' not in result['videos'][SOURCE]  # Data API does not certify Shorts.
    assert c.service.channels.return_value.list.call_args.kwargs['mine'] is True
    assert c.service.videos.return_value.list.call_args.kwargs['id'] == VIDEO
    assert c.service.videos.return_value.list.call_args.kwargs['part'] == 'snippet,statistics,status,contentDetails'
    c.service.channels.return_value.list.return_value.execute.assert_called_once_with(num_retries=0)
    c.service.videos.return_value.list.return_value.execute.assert_called_once_with(num_retries=0)
    c.service.close.assert_called_once()
    stored_keys = set(c.client.scan_iter('*'))
    assert all(k.startswith(('oauth:', c.module.CACHE_PREFIX, c.module.LOCK_PREFIX)) for k in stored_keys)
    assert c.client.get(c.auth.CREDENTIAL_PREFIX + CHANNEL) == 'encrypted-secret-do-not-expose'
    assert 'must-never-leak' not in json.dumps(result)


def test_debounce_is_per_channel_connection_and_manual_refresh_can_refresh_inside_300_seconds(case):
    c = case
    _refresh(c)
    _refresh(c)
    assert _google_count(c) == 1
    _unlock(c)  # Simulate real Redis 60-second lock expiry, without deleting cached values.
    c.clock[0] += 61
    c.video_response['items'][0]['statistics']['viewCount'] = '20'
    result = _refresh(c)
    assert _google_count(c) == 2
    assert result['videos'][SOURCE]['view_count'] == 20


def test_expired_cache_is_stale_with_last_known_values_and_get_stays_offline(case):
    c = case
    _refresh(c)
    c.clock[0] += 301
    result = c.module.get_dashboard_metrics([job()])
    assert result['channels'][0]['status'] == 'stale'
    assert result['videos'][SOURCE]['status'] == 'stale'
    assert result['videos'][SOURCE]['view_count'] == 12
    assert _google_count(c) == 1


class ProviderError(Exception):
    def __init__(self, status, reason):
        self.resp = SimpleNamespace(status=status)
        self.content = json.dumps({'error': {'message': 'secret-provider-body', 'errors': [{'reason': reason}]}}).encode()
        super().__init__('secret-provider-body')


@pytest.mark.parametrize('status,reason,code', [(403, 'quotaExceeded', 'quota'), (403, 'forbidden', 'permission'),
                                              (401, 'unauthorized', 'permission'), (500, 'backendError', 'api_unavailable')])
def test_refresh_error_keeps_values_stale_and_never_clears_credentials_or_jobs(case, status, reason, code):
    c = case
    _refresh(c)
    _unlock(c)
    c.clock[0] += 61
    protected = {k: c.client.dump(k) for k in c.client.scan_iter('oauth:*')}
    c.service.videos.return_value.list.return_value.execute.side_effect = ProviderError(status, reason)
    result = _refresh(c)
    assert result['channels'][0]['status'] == result['videos'][SOURCE]['status'] == 'stale'
    assert result['channels'][0]['reason'] == code
    assert result['videos'][SOURCE]['view_count'] == 12
    assert result['updated_at'] == c.module._iso(NOW)
    assert 'secret' not in json.dumps(result)
    assert protected == {k: c.client.dump(k) for k in c.client.scan_iter('oauth:*')}


@pytest.mark.parametrize('value', [None, True, False, -1, 1.5, '-2', 'nan', '9' * 21, '18446744073709551616', {}])
def test_missing_or_invalid_counts_are_not_zero(case, value):
    assert case.module._count(value) is None


def test_hidden_subscriber_and_omitted_engagement_counts_remain_unknown(case):
    c = case
    c.channel_response['items'][0]['statistics'].update(hiddenSubscriberCount=True, subscriberCount='30')
    c.video_response['items'][0]['statistics'] = {'viewCount': '0'}
    result = _refresh(c)
    assert result['channels'][0]['subscriber_count'] is None
    assert result['channels'][0]['subscriber_count_hidden'] is True
    assert result['videos'][SOURCE]['view_count'] == 0
    assert result['videos'][SOURCE]['like_count'] is None
    assert result['videos'][SOURCE]['comment_count'] is None


@pytest.mark.parametrize('change', [{'state': 'FAILURE'}, {'kind': 'plan'}, {'result': {}},
                                   {'task_id': '../task'}, {'state': 'PENDING'}])
def test_unverified_job_never_becomes_video_api_query(case, change):
    record = job()
    record.update(change)
    result = _refresh(case, [record])
    assert result['videos'] == {}
    case.service.videos.return_value.list.assert_not_called()


@pytest.mark.parametrize('field,value', [('video_id', 'https://evil.invalid'), ('target_channel_id', 'different_channel'),
                                       ('connection_id', 'old_connection'), ('uploaded_at', None)])
def test_wrong_video_or_connection_binding_never_becomes_query(case, field, value):
    record = job()
    record['result']['youtube'][field] = value
    assert _refresh(case, [record])['videos'] == {}
    case.service.videos.return_value.list.assert_not_called()


def test_conflicting_same_source_history_is_not_resolved_by_arbitrary_order(case):
    first, second = job(), job(video='bB123456789')
    result = _refresh(case, [first, second, first])
    assert result['videos'] == {}
    case.service.videos.return_value.list.assert_not_called()


def test_successful_publisher_maps_to_render_source_not_publish_task(case):
    publisher = {'task_id': '22222222-2222-4222-8222-222222222222', 'kind': 'publish', 'state': 'SUCCESS', 'parent_id': SOURCE,
                 'result': {'status': 'complete', 'source_task_id': SOURCE, 'youtube_video_id': VIDEO,
                            'target_channel_id': CHANNEL, 'connection_id': CONNECTION, 'uploaded_at': '2026-09-06T12:00:00Z'}}
    result = _refresh(case, [publisher])
    assert set(result['videos']) == {SOURCE}


def test_at_most_fifty_unique_uploaded_ids_per_channel(case):
    records = [job(source=f'{i:08d}-1111-4111-8111-111111111111', video=f'v{i:010d}') for i in range(60)]
    case.video_response['items'] = []
    result = _refresh(case, records)
    ids = case.service.videos.return_value.list.call_args.kwargs['id'].split(',')
    assert len(ids) == 50 and ids == [f'v{i:010d}' for i in range(50)]
    assert len(result['videos']) == 50


@pytest.mark.parametrize('kind', ['wrong_channel', 'duplicate', 'unrequested'])
def test_api_cannot_inject_other_channel_or_video_metrics(case, kind):
    c = case
    if kind == 'wrong_channel':
        c.video_response['items'][0]['snippet']['channelId'] = 'UC_other_channel'
    elif kind == 'duplicate':
        c.video_response['items'] *= 2
    else:
        c.video_response['items'][0]['id'] = 'cB123456789'
    result = _refresh(c)
    assert result['videos'][SOURCE]['view_count'] is None
    assert result['channels'][0]['reason'] in {'connection_changed', 'invalid_response'}


def test_channel_mine_response_must_contain_exactly_one_bound_channel(case):
    case.channel_response['items'][0]['id'] = 'UC_another_channel'
    result = _refresh(case)
    assert result['channels'][0]['view_count'] is None
    assert result['channels'][0]['reason'] == 'connection_changed'
    case.service.videos.return_value.list.assert_not_called()


def test_missing_api_video_retains_old_values_but_marks_that_video_stale(case):
    c = case
    _refresh(c)
    _unlock(c)
    c.clock[0] += 61
    c.video_response['items'] = []
    result = _refresh(c)
    assert result['channels'][0]['status'] == 'fresh'
    assert result['videos'][SOURCE]['status'] == 'stale'
    assert result['videos'][SOURCE]['reason'] == 'video_unavailable'
    assert result['videos'][SOURCE]['view_count'] == 12


@pytest.mark.parametrize('change', ['channel', 'credential', 'epoch', 'membership'])
def test_actual_redis_lua_rejects_cache_commit_after_reconnect_race(case, change):
    c = case
    def race(**_):
        if change == 'channel':
            c.client.set(c.auth.CHANNEL_PREFIX + CHANNEL, json.dumps({**c.channel, 'connection_id': 'connection_new_123'}))
        elif change == 'credential':
            c.client.set(c.auth.CREDENTIAL_PREFIX + CHANNEL, 'different-encrypted-value')
        elif change == 'epoch':
            c.client.incr(c.auth.AUTH_EPOCH_KEY)
        else:
            c.client.srem(c.auth.CHANNEL_INDEX_KEY, CHANNEL)
        return c.video_response
    c.service.videos.return_value.list.return_value.execute.side_effect = race
    _refresh(c)
    assert list(c.client.scan_iter(c.module.CACHE_PREFIX + '*')) == []


@pytest.mark.parametrize('payload', [{'version': 2}, {'version': 3, 'channel_id': 'other', 'connection_id': CONNECTION, 'refresh_token': 'x'},
                                    {'version': 3, 'channel_id': CHANNEL, 'connection_id': 'other', 'refresh_token': 'x'}])
def test_credentials_must_match_exact_v3_binding_before_google(case, payload):
    case.auth._decrypt_json.return_value = payload
    result = _refresh(case)
    assert result['channels'][0]['reason'] == 'connection_changed'
    case.module._service.assert_not_called()
    assert case.client.exists(case.auth.CREDENTIAL_PREFIX + CHANNEL)


def test_refresh_error_never_calls_mutating_legacy_load_credentials(case):
    c = case
    c.auth.load_credentials = Mock(side_effect=AssertionError('must not call mutating loader'))
    c.auth.list_connections = Mock(side_effect=AssertionError('must not migrate'))
    c.credentials.refresh.side_effect = type('RefreshError', (Exception,), {})('secret revoked token')
    result = _refresh(c)
    assert result['channels'][0]['reason'] == 'permission'
    c.auth.load_credentials.assert_not_called()
    c.auth.list_connections.assert_not_called()
    assert c.client.exists(c.auth.CREDENTIAL_PREFIX + CHANNEL)


def test_google_refresh_request_is_bounded_and_uses_existing_scoped_constructor(case):
    c = case
    def refresh(request):
        request('https://oauth2.googleapis.com/token', method='POST', timeout=120)
    c.credentials.refresh.side_effect = refresh
    _refresh(c)
    c.auth._credential_from_refresh_token.assert_called_once_with('must-never-leak')
    assert c.auth.GoogleRequest.return_value.call_args.kwargs['timeout'] == 20


def test_more_than_ten_connected_channels_fails_closed_without_provider_calls(case):
    case.client.sadd(case.auth.CHANNEL_INDEX_KEY, *[f'channel_{i:02d}' for i in range(10)])
    result = _refresh(case)
    assert result['channels'] == [] and result['error'] == 'cache_unavailable'
    case.module._service.assert_not_called()


def test_old_connection_cache_is_not_exposed_after_reconnect(case):
    c = case
    _refresh(c)
    c.client.set(c.auth.CHANNEL_PREFIX + CHANNEL, json.dumps({**c.channel, 'connection_id': 'new_connection_123'}))
    result = c.module.get_dashboard_metrics([job()])
    assert result['channels'][0]['view_count'] is None
    assert result['videos'] == {}


def test_cache_and_api_whitelist_never_emit_unknown_fields(case):
    c = case
    c.video_response['items'][0]['snippet']['description'] = 'secret bearer string'
    c.video_response['items'][0]['status']['privacyStatus'] = 'secret-unknown-status'
    c.video_response['items'][0]['contentDetails']['duration'] = {'secret': 'raw payload'}
    result = _refresh(c)
    serialized = json.dumps(result)
    assert 'secret' not in serialized and 'bearer' not in serialized
    assert result['videos'][SOURCE]['privacy_status'] is None
    assert result['videos'][SOURCE]['duration'] is None


def test_cache_read_error_does_not_turn_missing_statistics_into_zero(case):
    case.module._redis = Mock(side_effect=RuntimeError('secret Redis URL'))
    assert case.module.get_dashboard_metrics([job()]) == {
        'channels': [], 'videos': {}, 'updated_at': None, 'refresh_after_seconds': 300, 'error': 'cache_unavailable'}


@pytest.mark.parametrize('older_fails', [False, True])
def test_slow_old_refresh_cannot_overwrite_newer_result_after_debounce_expires(case, older_fails):
    c = case
    context = c.module._contexts(c.client)[0]
    proofs = c.module._proofs([job()], [context])
    real_read = c.module._read_google
    def overlapping_read(context, ids, previous, started):
        if started == NOW:
            # The first HTTP request is still pending when the 60-second lease
            # expires. A second refresh commits its later statistics first.
            _unlock(c)
            c.clock[0] += 61
            c.video_response['items'][0]['statistics']['viewCount'] = '99'
            c.module._refresh_channel(context, proofs)
            if older_fails:
                raise ProviderError(500, 'backendError')
            c.video_response['items'][0]['statistics']['viewCount'] = '12'
        return real_read(context, ids, previous, started)
    c.module._read_google = overlapping_read
    c.module._refresh_channel(context, proofs)
    result = c.module.get_dashboard_metrics([job()])
    assert result['videos'][SOURCE]['view_count'] == 99
    assert result['videos'][SOURCE]['fetched_at'] == c.module._iso(NOW + 61)
    assert result['videos'][SOURCE]['status'] == 'fresh'
    assert result['videos'][SOURCE]['reason'] is None


def test_unsigned_counter_maximum_has_same_validation_for_string_and_integer(case):
    maximum = 2**64 - 1
    assert case.module._count(str(maximum)) == case.module._count(maximum) == maximum
    assert case.module._count(str(maximum + 1)) is case.module._count(maximum + 1) is None
