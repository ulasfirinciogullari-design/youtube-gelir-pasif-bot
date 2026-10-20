"""Exact-video visibility survives bounded samples; no live Google or credentials."""
from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from test_youtube_metrics import case, job, _refresh, _unlock, CHANNEL, CONNECTION, SOURCE, VIDEO, NOW
from test_studio_workflow_presentation import ui


def _keys(c):
    context = c.module._contexts(c.client)[0]
    return c.module._cache_key(context), c.module._observation_key(context)


def _absent(c):
    c.video_response['items'] = []
    result = _refresh(c)
    assert result['videos'][SOURCE]['availability'] == 'unavailable'
    return json.loads(c.client.hget(_keys(c)[1], VIDEO))


def _next(c):
    _unlock(c)
    c.clock[0] += 301


def _recent(c, count=50):
    records = [job(source=f'{i:08d}-1111-4111-8111-111111111111', video=f'v{i:010d}')
               for i in range(1, count + 1)]
    c.video_response['items'] = [{'id': item['result']['youtube']['video_id'],
        'snippet': {'channelId': CHANNEL}, 'status': {'privacyStatus': 'public'}}
        for item in records[:50]]
    return records


def test_absence_survives_fifty_new_videos_and_statistics_expiry_for_old_card(case):
    c = case
    original = _absent(c)
    cache_key, observation_key = _keys(c)
    _next(c)
    records = _recent(c) + [job()]
    result = _refresh(c, records)
    queried = c.service.videos.return_value.list.call_args.kwargs['id'].split(',')
    assert len(queried) == 50 and VIDEO not in queried
    assert VIDEO not in json.loads(c.client.get(cache_key))['videos']
    assert json.loads(c.client.hget(observation_key, VIDEO)) == original
    assert c.client.ttl(observation_key) == -1
    assert len(result['videos']) == 51
    assert result['videos'][SOURCE]['availability_evidence'] == 'owner_api_absent'
    c.client.delete(cache_key)  # The statistics cache expires independently.
    c.clock[0] += 8 * 86400
    before = {key: c.client.dump(key) for key in c.client.scan_iter('*')}
    row = c.module.get_dashboard_metrics(records)['videos'][SOURCE]
    assert row['availability'] == 'unavailable'
    assert row['availability_checked_at'] == c.module._iso(NOW)
    assert row['view_count'] is None  # History does not invent fresh statistics.
    assert {key: c.client.dump(key) for key in c.client.scan_iter('*')} == before


def test_first_refresh_migrates_legacy_absence_outside_current_sample(case):
    c = case
    original = _absent(c)
    cache_key, observation_key = _keys(c)
    c.client.delete(observation_key)  # Existing production cache predates this feature.
    before = c.client.dump(cache_key)
    assert c.module.get_dashboard_metrics([job()])['videos'][SOURCE]['availability'] == 'unavailable'
    assert not c.client.exists(observation_key) and c.client.dump(cache_key) == before
    _next(c)
    _refresh(c, _recent(c))
    assert json.loads(c.client.hget(observation_key, VIDEO)) == original
    assert c.module.get_dashboard_metrics([job()])['videos'][SOURCE]['availability'] == 'unavailable'


def test_manual_empty_inventory_does_not_erase_existing_observations(case):
    c = case
    original = _absent(c)
    _next(c)
    _refresh(c, [])
    assert c.module.get_dashboard_metrics([job()])['videos'][SOURCE]['availability'] == 'unavailable'
    assert json.loads(c.client.hget(_keys(c)[1], VIDEO)) == original


def test_only_newer_same_video_positive_clears_absence_and_keeps_other_records(case):
    c = case
    positive = deepcopy(c.video_response)
    original = _absent(c)
    key = _keys(c)[1]
    unrelated = {**original, 'video_id': 'bB123456789'}
    c.client.hset(key, 'bB123456789', json.dumps(unrelated))
    _next(c)
    c.service.videos.return_value.list.return_value.execute.return_value = positive
    row = _refresh(c)['videos'][SOURCE]
    updated = json.loads(c.client.hget(key, VIDEO))
    assert updated['availability'] == row['availability'] == 'available'
    assert updated['availability_checked_at'] > original['availability_checked_at']
    assert row['availability_evidence'] is None and row['reason'] is None
    assert json.loads(c.client.hget(key, 'bB123456789')) == unrelated
    assert c.client.ttl(key) == -1


@pytest.mark.parametrize('change', ['channel', 'credential', 'epoch', 'membership'])
def test_authorization_change_rejects_both_observation_and_cache_commits(case, change):
    c = case
    original = _absent(c)
    cache_key, observation_key = _keys(c)
    before = c.client.dump(cache_key)
    _next(c)
    def changed(**_kwargs):
        if change == 'channel':
            c.client.set(c.auth.CHANNEL_PREFIX + CHANNEL,
                         json.dumps({**c.channel, 'connection_id': 'new_connection_123'}))
        elif change == 'credential':
            c.client.set(c.auth.CREDENTIAL_PREFIX + CHANNEL, 'new-encrypted-value')
        elif change == 'epoch':
            c.client.incr(c.auth.AUTH_EPOCH_KEY)
        else:
            c.client.srem(c.auth.CHANNEL_INDEX_KEY, CHANNEL)
        return {'items': [{'id': VIDEO, 'snippet': {'channelId': CHANNEL},
                           'status': {'privacyStatus': 'public'}}]}
    c.service.videos.return_value.list.return_value.execute.side_effect = changed
    _refresh(c)
    assert c.client.dump(cache_key) == before
    assert json.loads(c.client.hget(observation_key, VIDEO)) == original


def test_lost_statistics_cache_does_not_allow_slow_older_positive_to_replace_absence(case):
    c = case
    context = c.module._contexts(c.client)[0]
    proofs = c.module._proofs([job()], [context])
    real_read = c.module._read_google
    def overlapping(context, ids, previous, started):
        if started == NOW:
            _next(c)
            c.video_response['items'] = []
            c.module._refresh_channel(context, proofs)
            c.client.delete(c.module._cache_key(context))
            c.video_response['items'] = [{'id': VIDEO, 'snippet': {'channelId': CHANNEL},
                                          'status': {'privacyStatus': 'public'}}]
        return real_read(context, ids, previous, started)
    c.module._read_google = overlapping
    c.module._refresh_channel(context, proofs)
    assert not c.client.exists(c.module._cache_key(context))
    row = c.module.get_dashboard_metrics([job()])['videos'][SOURCE]
    assert row['availability'] == 'unavailable'
    assert row['availability_checked_at'] == c.module._iso(NOW + 301)


@pytest.mark.parametrize('checked', [None, True, 0, -1, '1788720301', float('inf'), float('nan'), NOW + 302])
def test_invalid_positive_timestamp_cannot_clear_a_valid_durable_absence(case, checked):
    c = case
    original = _absent(c)
    _next(c)
    real_read = c.module._read_google
    def invalid(context, ids, previous, started):
        result = real_read(context, ids, previous, started)
        result['videos'][VIDEO] = {'availability': 'available', 'availability_checked_at': checked,
                                   'privacy_status': 'public', 'fetched_at': started}
        return result
    c.module._read_google = invalid
    result = _refresh(c)
    assert json.loads(c.client.hget(_keys(c)[1], VIDEO)) == original
    assert result['videos'][SOURCE]['availability'] == 'unavailable'


@pytest.mark.parametrize('raw', ['{}', 'null', '[]', 'broken', '{"availability":"available"}'])
def test_malformed_durable_row_does_not_displace_valid_legacy_absence_or_authorize_reset(case, raw):
    c = case
    _absent(c)
    cache_key, observation_key = _keys(c)
    c.client.hset(observation_key, VIDEO, raw)
    before = c.client.dump(cache_key)
    _next(c)
    c.video_response['items'] = [{'id': VIDEO, 'snippet': {'channelId': CHANNEL},
                                'status': {'privacyStatus': 'public'}}]
    assert _refresh(c)['videos'][SOURCE]['availability'] == 'unavailable'
    assert c.client.dump(cache_key) == before and c.client.hget(observation_key, VIDEO) == raw


@pytest.mark.parametrize('changed', [
    {'channel_id': 'UC_other_channel'}, {'connection_id': 'connection_other'},
    {'video_id': 'bB123456789'}, {'version': True}, {'availability_checked_at': NOW + 1},
    {'availability_checked_at': None}, {'error': None},
])
def test_unbound_or_invalid_persisted_row_never_establishes_absence(case, changed):
    c = case
    original = _absent(c)
    cache_key, observation_key = _keys(c)
    c.client.hset(observation_key, VIDEO, json.dumps({**original, **changed}))
    c.client.delete(cache_key)
    row = c.module.get_dashboard_metrics([job()])['videos'][SOURCE]
    assert row['availability'] is None and row['availability_evidence'] is None


def test_same_timestamp_positive_cannot_replace_persisted_absence_after_cache_expiry(case):
    c = case
    original = _absent(c)
    cache_key, observation_key = _keys(c)
    c.client.delete(cache_key)
    _unlock(c)  # Same timestamp is not a newer observation.
    c.video_response['items'] = [{'id': VIDEO, 'snippet': {'channelId': CHANNEL}}]
    row = _refresh(c)['videos'][SOURCE]
    assert row['availability'] == 'unavailable'
    assert json.loads(c.client.hget(observation_key, VIDEO)) == original


def test_same_timestamp_persisted_positive_cannot_override_legacy_absence(case):
    c = case
    original = _absent(c)
    c.client.hset(_keys(c)[1], VIDEO, json.dumps({**original, 'availability': 'available', 'error': None}))
    assert c.module.get_dashboard_metrics([job()])['videos'][SOURCE]['availability'] == 'unavailable'
    _next(c)
    _refresh(c, _recent(c))
    c.client.delete(_keys(c)[0])
    assert json.loads(c.client.hget(_keys(c)[1], VIDEO)) == original
    assert c.module.get_dashboard_metrics([job()])['videos'][SOURCE]['availability'] == 'unavailable'


def test_all_proof_reads_are_bounded_and_targeted_without_history_scan_or_mutation(case, monkeypatch):
    c = case
    _absent(c)
    records = [job(source=f'{i:08d}-1111-4111-8111-111111111111', video=f'v{i:010d}')
               for i in range(1, 5002)]
    c.client.hset(_keys(c)[1], 'not-a-requested-video', 'unrelated-history')
    read = Mock(wraps=c.client.hmget)
    monkeypatch.setattr(c.client, 'hmget', read)
    for method in ('hgetall', 'hscan', 'hscan_iter', 'scan', 'scan_iter', 'keys'):
        monkeypatch.setattr(c.client, method, Mock(side_effect=AssertionError('must not enumerate history')))
    c.module._service.reset_mock()
    c.auth._decrypt_json.reset_mock()
    result = c.module.get_dashboard_metrics(records)
    assert len(result['videos']) == 5000
    assert read.call_count == 20
    assert all(len(call.args[1]) == 250 for call in read.call_args_list)
    assert [id for call in read.call_args_list for id in call.args[1]] == [f'v{i:010d}' for i in range(1, 5001)]
    c.module._service.assert_not_called()
    c.auth._decrypt_json.assert_not_called()


def test_reconnection_does_not_expose_or_modify_previous_connections_observations(case):
    c = case
    original = _absent(c)
    key = _keys(c)[1]
    c.client.set(c.auth.CHANNEL_PREFIX + CHANNEL, json.dumps({**c.channel, 'connection_id': 'connection_new_123'}))
    old = c.module.get_dashboard_metrics([job()])
    new = c.module.get_dashboard_metrics([job(connection='connection_new_123')])
    assert old['videos'] == {}
    assert new['videos'][SOURCE]['availability'] is None
    assert json.loads(c.client.hget(key, VIDEO)) == original


@pytest.mark.parametrize('unavailable', ['wrong_type', 'read_failure'])
def test_unavailable_history_read_preserves_valid_cached_absence(case, monkeypatch, unavailable):
    c = case
    _absent(c)
    cache_key, observation_key = _keys(c)
    before = c.client.dump(cache_key)
    if unavailable == 'wrong_type':
        c.client.delete(observation_key)
        c.client.set(observation_key, 'broken-history-type')
    else:
        monkeypatch.setattr(c.client, 'hmget', Mock(side_effect=ConnectionError('private-store-detail')))
    result = c.module.get_dashboard_metrics([job()])
    assert result['videos'][SOURCE]['availability'] == 'unavailable'
    assert result['error'] == 'cache_unavailable'
    assert 'private-store-detail' not in str(result) and c.client.dump(cache_key) == before


def test_invalid_existing_row_aborts_all_observation_and_cache_writes(case):
    c = case
    _absent(c)
    cache_key, observation_key = _keys(c)
    records = _recent(c, count=2)
    c.client.hset(observation_key, 'v0000000002', 'invalid-history')
    before = {key: c.client.dump(key) for key in (cache_key, observation_key)}
    _next(c)
    _refresh(c, records)
    assert {key: c.client.dump(key) for key in (cache_key, observation_key)} == before
    assert c.client.hget(observation_key, 'v0000000001') is None


def test_future_existing_positive_cannot_replace_valid_cached_absence(case):
    c = case
    original = _absent(c)
    cache_key, observation_key = _keys(c)
    bad = {**original, 'availability': 'available', 'error': None,
           'availability_checked_at': NOW + 1000}
    c.client.hset(observation_key, VIDEO, json.dumps(bad))
    before = c.client.dump(cache_key)
    _next(c)
    c.video_response['items'] = [{'id': VIDEO, 'snippet': {'channelId': CHANNEL}}]
    result = _refresh(c)
    assert result['videos'][SOURCE]['availability'] == 'unavailable'
    assert c.client.dump(cache_key) == before
    assert json.loads(c.client.hget(observation_key, VIDEO)) == bad


@pytest.mark.parametrize('broken', ['malformed', 'future', 'wrong_binding', 'missing_timestamp', 'missing_field',
                                    'wrong_type', 'read_failure'])
def test_actual_delivery_ui_reports_unknown_for_unreadable_existing_visibility(case, ui, monkeypatch, broken):
    c = case
    original = _absent(c)
    cache_key, observation_key = _keys(c)
    c.client.delete(cache_key)
    if broken == 'malformed':
        c.client.hset(observation_key, VIDEO, '{}')
    elif broken == 'wrong_type':
        c.client.delete(observation_key)
        c.client.set(observation_key, 'invalid-type')
    elif broken == 'read_failure':
        monkeypatch.setattr(c.client, 'hmget', Mock(side_effect=ConnectionError('private-store-detail')))
    else:
        record = {**original, 'availability': 'available', 'error': None}
        if broken == 'future':
            record['availability_checked_at'] = NOW + 1
        elif broken == 'wrong_binding':
            record['connection_id'] = 'connection_other'
        elif broken == 'missing_timestamp':
            record.pop('availability_checked_at')
        else:
            record.pop('privacy_status')
        c.client.hset(observation_key, VIDEO, json.dumps(record))
    source = job()
    before = deepcopy(source)
    model = c.module.get_dashboard_metrics([source])
    assert model['videos'][SOURCE]['visibility_state_invalid'] is True
    displayed = ui.ns['_with_youtube_metrics'](source, model)
    assert ui.ns['_video_delivery'](displayed) == {
        'key': 'unknown', 'label': 'YouTube görünürlüğü doğrulanamadı', 'attention': True}
    assert ui.ns['_job_upload_allowed'](displayed) is False
    assert source == before
    ui.forbidden.assert_not_called()


def test_absent_and_never_observed_history_are_distinct_in_actual_delivery_ui(case, ui):
    c = case
    source = job()
    model = c.module.get_dashboard_metrics([source])
    assert model['videos'][SOURCE]['visibility_state_invalid'] is False
    assert ui.ns['_video_delivery'](ui.ns['_with_youtube_metrics'](source, model))['key'] == 'public'
    _absent(c)
    c.client.hset(_keys(c)[1], VIDEO, '{}')
    model = c.module.get_dashboard_metrics([source])
    assert model['videos'][SOURCE]['visibility_state_invalid'] is False
    assert ui.ns['_video_delivery'](ui.ns['_with_youtube_metrics'](source, model))['key'] == 'deleted'
