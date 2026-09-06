"""An owner-API absence is a display tombstone, never a new upload grant."""
from copy import deepcopy
import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from test_youtube_metrics import case, job, _refresh, _unlock, SOURCE
from test_youtube_dashboard_presentation import dashboard, render, SOURCE as UI_SOURCE
from test_studio_workflow_presentation import ui


def test_successful_owner_absence_keeps_history_without_changing_source_or_ledger(case):
    c = case
    jobs = [job()]
    original = deepcopy(jobs)
    c.client.set('publisher:retained-ledger', 'historical public upload')
    _refresh(c, jobs)
    _unlock(c)
    c.clock[0] += 61
    c.video_response['items'] = []
    row = _refresh(c, jobs)['videos'][SOURCE]
    assert row['availability'] == 'unavailable'
    assert row['availability_evidence'] == 'owner_api_absent'
    assert row['availability_checked_at'] == c.module._iso(c.clock[0])
    assert row['privacy_status'] == 'public' and row['view_count'] == 12
    assert row['reason'] == 'video_unavailable'
    assert jobs == original
    assert c.client.get('publisher:retained-ledger') == 'historical public upload'
    context = c.module._contexts(c.client)[0]
    assert c.client.ttl(c.module._cache_key(context)) == -1
    c.clock[0] += 8 * 86400
    assert c.module.get_dashboard_metrics(jobs)['videos'][SOURCE]['availability'] == 'unavailable'


def test_owner_absence_without_old_statistics_does_not_invent_counts(case):
    case.video_response['items'] = []
    row = _refresh(case)['videos'][SOURCE]
    assert row['availability'] == 'unavailable'
    assert row['view_count'] is None and row['privacy_status'] is None
    assert row['fetched_at'] is None and row['availability_checked_at']


@pytest.mark.parametrize('status', [401, 403, 429, 500, None])
def test_auth_quota_and_network_errors_never_establish_new_absence(case, status):
    c = case
    _refresh(c)
    _unlock(c)
    c.clock[0] += 61
    failure = RuntimeError('private provider detail must not appear')
    failure.resp = SimpleNamespace(status=status)
    c.service.videos.return_value.list.return_value.execute.side_effect = failure
    row = _refresh(c)['videos'][SOURCE]
    assert row['availability'] == 'available'
    assert row['availability_evidence'] is None
    assert row['privacy_status'] == 'public'
    assert row['reason'] in {'permission', 'api_unavailable'}
    assert 'private provider detail' not in str(row)


@pytest.mark.parametrize('response', [{}, {'items': None}, {'items': [{}]}, {'items': [{'id': 'wrong-id'}]}])
def test_malformed_video_response_is_not_deletion(case, response):
    case.service.videos.return_value.list.return_value.execute.return_value = response
    row = _refresh(case)['videos'][SOURCE]
    assert row['availability'] is None and row['availability_evidence'] is None
    assert row['reason'] == 'invalid_response'


def test_wrong_owner_and_changed_authorization_cannot_commit_absence(case):
    c = case
    c.channel_response['items'][0]['id'] = 'UC_another_owner'
    assert _refresh(c)['videos'][SOURCE]['availability'] is None
    c.service.videos.return_value.list.assert_not_called()
    c.channel_response['items'][0]['id'] = c.channel['id']
    _unlock(c)
    c.clock[0] += 61
    def rotate(**_kwargs):
        c.client.incr(c.auth.AUTH_EPOCH_KEY)
        return {'items': []}
    c.service.videos.return_value.list.return_value.execute.side_effect = rotate
    assert _refresh(c)['videos'][SOURCE]['availability'] is None


def test_reappearing_video_clears_observed_absence_without_new_upload(case):
    c = case
    original = deepcopy(c.video_response)
    c.video_response['items'] = []
    assert _refresh(c)['videos'][SOURCE]['availability'] == 'unavailable'
    _unlock(c)
    c.clock[0] += 61
    c.service.videos.return_value.list.return_value.execute.return_value = original
    row = _refresh(c)['videos'][SOURCE]
    assert row['availability'] == 'available' and row['availability_evidence'] is None
    assert row['reason'] is None and row['privacy_status'] == 'public'
    context = c.module._contexts(c.client)[0]
    assert 0 < c.client.ttl(c.module._cache_key(context)) <= c.module.CACHE_TTL_SECONDS


def _missing(source):
    youtube = source['result']['youtube']
    return {'video_id': youtube['video_id'], 'channel_id': youtube['target_channel_id'],
            'privacy_status': 'public', 'status': 'stale', 'reason': 'video_unavailable',
            'view_count': 100, 'availability': 'unavailable',
            'availability_evidence': 'owner_api_absent',
            'availability_checked_at': '2026-09-06T12:00:00+00:00'}


def test_public_center_hides_absent_source_but_retains_audit_and_reupload_block(dashboard):
    source = render(privacy='public', release='public')
    source['result']['youtube_automation'] = {}
    dashboard.records[UI_SOURCE] = source
    dashboard.model['videos'][UI_SOURCE] = _missing(source)
    original = deepcopy(dashboard.records)
    body = dashboard.client.get('/studio/youtube').text
    assert source['result']['title'] not in body
    assert 'status=deleted' in body and 'Silinenler' in body
    decorated = dashboard.shared._with_youtube_metrics(source, dashboard.model)
    assert dashboard.shared._video_delivery(decorated)['key'] == 'deleted'
    assert dashboard.shared._job_upload_allowed(decorated) is False
    assert dashboard.shared._delivery_video_id(decorated) == source['result']['youtube']['video_id']
    assert dashboard.shared._console_counts([decorated])['library'] == 0
    assert dashboard.shared._history_matches(decorated, 'deleted') is True
    for active in ('library', 'ready', 'uploaded', 'public', 'private', 'completed'):
        assert dashboard.shared._history_matches(decorated, active) is False
    assert dashboard.records == original
    dashboard.forbidden.assert_not_called()


def test_direct_reupload_of_deleted_source_still_returns_existing_record(dashboard):
    source = render(privacy='public', release='public')
    dashboard.records[UI_SOURCE] = source
    original = deepcopy(source)
    channel = source['result']['youtube']['target_channel_id']
    dashboard.ns['connection_status'] = lambda **_kwargs: {
        'channel': {'id': channel, 'connection_id': 'connection-one'},
    }
    response = dashboard.client.post(
        '/studio/youtube/publish/' + UI_SOURCE, data={'youtube_channel_id': channel},
        headers={'Origin': 'https://studio.example'}, follow_redirects=False,
    )
    assert response.status_code == 303 and response.headers['location'] == '/studio/youtube'
    assert source == original
    dashboard.forbidden.assert_not_called()


@pytest.mark.parametrize('previous,current,reloads', [
    ('public', 'deleted', 1), ('deleted', 'public', 1),
    ('deleted', 'deleted', 0), ('public', 'private', 0),
])
def test_metrics_refresh_reloads_only_changed_deleted_membership(ui, previous, current, reloads):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is not installed')
    script = ui.ns['_metrics_script']()
    apply = 'function apply(model)' + script.split('function apply(model)', 1)[1].split('\nasync function load', 1)[0]
    harness = '''let reloads=0;
const window={location:{reload(){reloads++}}};
const row={dataset:{deliveryTask:'source'},querySelector(){return {dataset:{deliveryKey:PREVIOUS}}}};
const document={getElementById(){return null},querySelectorAll(s){return s==='[data-delivery-task]'?[row]:[]}};
''' .replace('PREVIOUS', json.dumps(previous))
    checked = subprocess.run(
        [node, '-e', harness + apply + '\napply(' + json.dumps({'video_presentations': {
            'source': {'key': current, 'badges_html': 'safe test badge'},
        }}) + ');if(reloads!==' + str(reloads) + ')process.exit(1);'],
        capture_output=True, text=True, timeout=10,
    )
    assert checked.returncode == 0, checked.stderr


@pytest.mark.parametrize('changes', [
    {'availability_evidence': None}, {'availability_checked_at': None},
    {'video_id': 'AnotherID12'}, {'channel_id': 'UC_other_channel'},
    {'availability': None, 'reason': 'permission'},
])
def test_unproven_or_mismatched_absence_never_hides_public_history(dashboard, changes):
    source = render(privacy='public', release='public')
    source['result']['youtube_automation'] = {}
    dashboard.model['videos'][UI_SOURCE] = {**_missing(source), **changes}
    displayed = dashboard.shared._with_youtube_metrics(source, dashboard.model)
    assert dashboard.shared._video_delivery(displayed)['key'] == 'public'
    assert dashboard.shared._history_matches(displayed, 'deleted') is False


def test_deleted_history_page_and_polling_are_truthful_read_only(ui):
    source = render(privacy='public', release='public')
    source['result']['youtube_automation'] = {}
    ui.records[UI_SOURCE] = source
    model = {'channels': [], 'videos': {UI_SOURCE: _missing(source)}, 'updated_at': None}
    ui.ns['_dashboard_metrics'] = lambda _jobs: deepcopy(model)
    original = deepcopy(ui.records)
    page = ui.client.get('/studio/history?status=deleted')
    assert page.status_code == 200
    assert 'Silinenler' in page.text and source['result']['title'] in page.text
    assert 'YouTube’dan silindi / erişilemiyor' in page.text
    assert 'Yayın kaydını aç' in page.text and 'YouTube’da yayında' not in page.text
    payload = ui.client.get('/studio/api/job/' + UI_SOURCE).json()
    assert payload['delivery_video_id'] == '' and payload['upload_allowed'] is False
    assert payload['result']['youtube']['video_id'] == source['result']['youtube']['video_id']
    assert payload['result']['youtube']['release_status'] == 'public'
    assert 'silindi' in payload['delivery_label']
    response = ui.client.get('/studio/api/youtube-metrics').json()
    assert response['video_presentations'][UI_SOURCE]['key'] == 'deleted'
    assert 'data-delivery-key="deleted"' in response['video_presentations'][UI_SOURCE]['badges_html']
    assert ui.records == original
    ui.forbidden.assert_not_called()
