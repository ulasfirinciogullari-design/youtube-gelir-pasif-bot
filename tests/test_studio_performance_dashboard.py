"""Truthful, read-only presentation; all auth/cache/provider services mocked."""
from copy import deepcopy
import json
import shutil
import subprocess
import sys
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from test_studio_ui import ui_modules, _ready_job


TASK = 'a1275f73-ed2d-4d67-9e15-f7043d2880d0'
VIDEO = 'AbCdEfGhI_1'
CHANNEL = 'UCtestChannel001'


def _job(privacy='private', release='private'):
    job = _ready_job()
    job['task_id'] = TASK
    job['spec'].update(format='shorts', language='en', production_channel_id=CHANNEL)
    job['result']['youtube'] = {'video_id': VIDEO, 'target_channel_id': CHANNEL,
        'privacy_status': privacy, 'release_status': release,
        'url': f'https://www.youtube.com/watch?v={VIDEO}', 'channel_title': 'Margin Verdict'}
    return job


def _model(**changes):
    row = {'video_id': VIDEO, 'channel_id': CHANNEL, 'view_count': 15, 'like_count': 2,
           'comment_count': 1, 'fetched_at': '2026-09-06T12:00:00+00:00', 'status': 'fresh', 'privacy_status': 'private'}
    row.update(changes)
    return {'channels': [{'channel_id': CHANNEL, 'title': 'Margin Verdict', 'subscriber_count': None,
                         'subscriber_count_hidden': False, 'video_count': 0, 'view_count': None,
                         'fetched_at': None, 'status': 'unavailable'}], 'videos': {TASK: row},
            'updated_at': row['fetched_at'], 'refresh_after_seconds': 300}


def test_private_blocked_is_already_uploaded_and_needs_action_without_reupload(ui_modules):
    studio, _ = ui_modules
    job = _job(release='blocked')
    before = deepcopy(job)
    html = studio._ready_video_card(job)
    assert 'YouTube’a gizli yüklendi' in html and 'Kontrol gerekiyor' in html
    assert '>Hazır<' not in html and '>Gizli yükle<' not in html
    assert 'Mevcut yüklemeyi kontrol et' in html and 'Henüz herkese açık değil' in html
    assert studio._history_matches(job, 'uploaded') is True
    assert studio._history_matches(job, 'private') is True
    assert studio._history_matches(job, 'public') is False
    assert studio._history_matches(job, 'library') is True
    assert studio._job_upload_allowed(job) is False and job == before


def test_public_completion_is_never_described_as_private(ui_modules):
    studio, _ = ui_modules
    job = _job(privacy='public', release='public')
    assert studio._job_status_message(job) == 'YouTube’da yayında.'
    assert 'YouTube’da yayında' in studio._ready_video_card(job)
    assert 'Gizli YouTube yüklemesi tamamlandı' not in studio._job_status_message(job)


@pytest.mark.parametrize('change', [
    {'url': 'https://evil.test/watch?v=' + VIDEO},
    {'url': 'https://youtube.com.evil.test/watch?v=' + VIDEO},
    {'url': 'javascript:alert(1)'}, {'video_id': 'not-an-id'},
    {'url': 'https://www.youtube.com/watch?v=ZbCdEfGhI_1'},
    {'target_channel_id': 'otherChannel'},
    {'url': 'https://www.youtube.com/watch?v=' + VIDEO + '&v=ZbCdEfGhI_1'},
])
def test_unbound_urls_ids_and_channels_cannot_claim_actual_upload(ui_modules, change):
    studio, _ = ui_modules
    job = _job()
    job['result']['youtube'].update(change)
    delivery = studio._video_delivery(job)
    assert delivery['key'] == 'unknown' and delivery['attention'] is True
    assert 'doğrulanamadı' in delivery['label']
    assert studio._history_matches(job, 'uploaded') is False
    assert '_youtube_metrics' not in studio._with_youtube_metrics(job, _model())
    assert "YouTube'da aç" not in studio._job_primary_action(job)


def test_legacy_canonical_youtube_url_requires_exact_channel_context(ui_modules):
    studio, _ = ui_modules
    job = _job()
    del job['result']['youtube']['video_id']
    assert studio._delivery_video_id(job) == VIDEO
    del job['result']['youtube']['target_channel_id']
    del job['spec']['production_channel_id']
    assert studio._delivery_video_id(job) == ''


def test_metrics_are_bound_and_never_overwrite_job_or_qa(ui_modules):
    studio, _ = ui_modules
    source = _job(release='blocked')
    before = deepcopy(source)
    decorated = studio._with_youtube_metrics(source, _model(privacy_status='public'))
    assert studio._video_delivery(decorated)['key'] == 'public'
    assert studio._video_delivery(decorated)['attention'] is True
    assert studio._job_upload_allowed(decorated) is False and source == before
    for mismatch in ({'video_id': 'ZbCdEfGhI_1'}, {'channel_id': 'otherChannel'}):
        unbound = studio._with_youtube_metrics(source, _model(**mismatch))
        assert '_youtube_metrics' not in unbound
        assert studio._video_delivery(unbound)['key'] == 'private'
    stale = studio._with_youtube_metrics(source, _model(status='stale', privacy_status='public'))
    assert studio._video_delivery(stale)['key'] == 'private'


@pytest.mark.parametrize('value,expected', [(None, 'Veri bekleniyor'), (True, 'Veri bekleniyor'),
                                         (-1, 'Veri bekleniyor'), ('0', 'Veri bekleniyor'), (0, '0'), (1234, '1.234')])
def test_missing_metrics_are_not_fabricated_zeros(ui_modules, value, expected):
    studio, _ = ui_modules
    assert studio._metric_number(value) == expected


def test_channel_stats_label_public_count_and_escape_external_text(ui_modules):
    studio, _ = ui_modules
    rows = _model()['channels']
    rows[0]['title'] = '<script>alert(1)</script>'
    html = studio._channel_overview(rows)
    assert 'Herkese açık video' in html and '<b>0</b>' in html
    assert 'Veri bekleniyor' in html and '<script>' not in html
    assert 'Üretim durumu bekleniyor' in html


@pytest.mark.parametrize('views,likes,comments,expected', [
    (None, None, None, 'Performans verisi bekleniyor'), (0, 0, 0, 'Henüz izlenme yok'),
    (20, 0, 0, 'İzleniyor · henüz etkileşim yok'), (20, 1, 0, 'İzlenme ve etkileşim başladı'),
    (20, None, 1, 'İzlenme ve etkileşim başladı'), (20, None, None, 'Performans verisi bekleniyor'),
])
def test_interest_summary_reports_observations_not_success_ratings(ui_modules, views, likes, comments, expected):
    studio, _ = ui_modules
    job = studio._with_youtube_metrics(_job('public', 'public'), _model(privacy_status='public', view_count=views, like_count=likes, comment_count=comments))
    assert studio._performance_summary(job) == expected


def test_format_and_language_use_saved_spec_not_duration_or_metrics(ui_modules):
    studio, _ = ui_modules
    job = _job()
    assert 'Shorts formatı' in studio._ready_video_card(job) and 'English' in studio._ready_video_card(job)
    job['spec']['format'] = 'landscape'
    assert studio._job_format(job) == 'Normal video'
    del job['spec']['format']
    assert studio._job_format(job) == 'Biçim belirtilmedi'


@pytest.fixture
def metrics_client(ui_modules, monkeypatch):
    studio, _ = ui_modules
    app = FastAPI()
    app.include_router(studio.router)
    read = Mock(return_value=_model())
    refresh = Mock(return_value=_model())
    module = sys.modules['app.services.youtube_metrics']
    monkeypatch.setattr(module, 'get_dashboard_metrics', read)
    monkeypatch.setattr(module, 'refresh_dashboard_metrics', refresh)
    jobs = Mock(return_value=[_job(release='blocked')])
    monkeypatch.setattr(studio, 'list_jobs', jobs)
    monkeypatch.setattr(studio, 'list_channel_profiles', lambda: [])
    return TestClient(app, base_url='https://studio.example.test'), read, refresh, jobs


def test_metrics_get_is_authenticated_and_cached_only(metrics_client):
    client, read, refresh, jobs = metrics_client
    assert client.get('/studio/api/youtube-metrics').status_code == 401
    read.assert_not_called(); refresh.assert_not_called(); jobs.assert_not_called()
    client.cookies.set('youtube_studio_token', 'studio-secret')
    response = client.get('/studio/api/youtube-metrics')
    assert response.status_code == 200 and read.call_count == 1
    refresh.assert_not_called()
    assert response.headers['cache-control'] == 'private, no-store'
    assert 'video_presentations' in response.json()
    assert 'spec' not in response.json() and 'result' not in response.json()


@pytest.mark.parametrize('headers,status', [({}, 403), ({'origin': 'https://evil.test'}, 403),
                                          ({'origin': 'https://studio.example.test'}, 200)])
def test_refresh_requires_owner_cookie_and_same_origin_before_refresh(metrics_client, headers, status):
    client, read, refresh, jobs = metrics_client
    assert client.post('/studio/api/youtube-metrics/refresh', headers=headers).status_code == 401
    jobs.assert_not_called(); refresh.assert_not_called()
    client.cookies.set('youtube_studio_token', 'studio-secret')
    response = client.post('/studio/api/youtube-metrics/refresh', headers=headers)
    assert response.status_code == status
    assert refresh.call_count == int(status == 200)
    read.assert_not_called()


def test_metrics_script_is_visible_only_bounded_and_updates_badges_without_forms(ui_modules):
    studio, _ = ui_modules
    script = studio._metrics_script()
    assert "document.visibilityState==='visible'" in script
    assert '300000' in script and '30000' in script and '60000' in script
    assert "credentials:'same-origin'" in script and "method:refresh?'POST':'GET'" in script
    assert 'data-delivery-task' in script and 'data-performance-summary' in script
    assert 'son ölçüm' in script.casefold() and 'stale?' in script
    assert 'production_enabled' not in script and 'publish_after_render' not in script


def test_metrics_script_is_valid_javascript(ui_modules):
    studio, _ = ui_modules
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is not installed in this test environment')
    script = studio._metrics_script().removeprefix('<script>').removesuffix('</script>')
    checked = subprocess.run([node, '--check', '-'], input=script, text=True, encoding='utf-8', capture_output=True, timeout=10)
    assert checked.returncode == 0, checked.stderr


@pytest.mark.parametrize('invalid,old_retry', [(False, False), (True, False), (False, True)])
def test_job_poll_uses_only_validated_delivery_identity_and_clears_old_retry(ui_modules, monkeypatch, invalid, old_retry):
    studio, _ = ui_modules
    job = _job('public', 'public')
    if invalid:
        job['result']['youtube']['url'] = 'https://foreign.test/video'
    monkeypatch.setattr(studio, '_sync_job', lambda _: job)
    monkeypatch.setattr(studio, '_with_publication_presentation', lambda record, *_a, **_k: record)
    projection = {'task_id': 'b1275f73-ed2d-4d67-9e15-f7043d2880d0', 'ui_status': 'completed',
                  'display_status': 'completed', 'stage_label': 'Tamamlandı', 'message': 'Güncel deneme tamamlandı.'}
    monkeypatch.setattr(studio, '_terminal_retry_presentation', lambda *_a, **_k: projection if old_retry else None)
    payload = json.loads(studio.studio_job_api(TASK, studio_token='studio-secret').body)
    assert payload['delivery_video_id'] == ('' if invalid or old_retry else VIDEO)
    if old_retry:
        assert payload['delivery_label'] == '' and payload['upload_allowed'] is False


@pytest.mark.parametrize('automatic', [True, False])
def test_metrics_poll_preserves_bound_failed_publisher_warning_without_ledger_reads(ui_modules, monkeypatch, automatic):
    studio, _ = ui_modules
    job = _job()
    del job['result']['youtube']
    publisher_id = 'b1275f73-ed2d-4d67-9e15-f7043d2880d0'
    publisher = {'task_id': publisher_id, 'kind': 'publish', 'state': 'FAILURE',
                 'parent_id': TASK, 'spec': {'source_task_id': TASK}, 'result': {}}
    if automatic:
        job['result']['youtube_automation'] = {'status': 'queued', 'publish_task_id': publisher_id}
    jobs = [job, publisher]
    before = deepcopy(jobs)
    monkeypatch.setattr(studio, 'get_job', Mock(side_effect=AssertionError('No extra lookup')))
    monkeypatch.setattr(studio, 'get_upload_record', Mock(side_effect=AssertionError('No per-card ledger lookup')))
    payload = json.loads(studio._youtube_metrics_response(_model(), jobs).body)
    row = payload['video_presentations'][TASK]
    assert 'Yüklemeyi kontrol et' in row['badges_html'] and 'Yükleme tamamlanamadı' == row['label']
    assert jobs == before


def test_history_separates_uploaded_private_public_and_preserves_blocked(ui_modules, monkeypatch):
    studio, _ = ui_modules
    jobs = [_job(release='blocked')]
    monkeypatch.setattr(studio, 'list_jobs', lambda _: jobs)
    body = studio.studio_history(status='private', studio_token='studio-secret').body.decode()
    assert 'YouTube’a gizli yüklendi' in body and 'Kontrol gerekiyor' in body
    assert 'status=uploaded' in body and 'status=public' in body and 'status=ready' in body
    assert '>Gizli yükle<' not in body and 'data-history-status="private"' in body
