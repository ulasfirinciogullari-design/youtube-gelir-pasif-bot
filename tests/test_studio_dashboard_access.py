"""Owner navigation recovers safely; overview reports actual and unknown state."""
from copy import deepcopy
from unittest.mock import Mock

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from test_studio_ui import ui_modules, _ready_job


@pytest.fixture
def dashboard(ui_modules, monkeypatch):
    studio, youtube = ui_modules
    monkeypatch.setattr(studio, '_production_budget_notice', lambda: '<section>BUDGET_STATUS</section>')
    app = FastAPI()
    app.include_router(studio.router)
    app.add_exception_handler(HTTPException, studio.studio_auth_exception)
    return studio, youtube, TestClient(app, base_url='https://studio.example.test')


@pytest.mark.parametrize('path', ['/studio', '/studio/create', '/studio/history', '/studio/youtube',
                                  '/studio/job/private-task', '/studio/plan/private-task'])
def test_browser_navigation_to_missing_session_redirects_before_private_reads(dashboard, monkeypatch, path):
    studio, youtube, client = dashboard
    private = Mock(side_effect=AssertionError('private read before authentication'))
    for module, names in ((studio, ('list_jobs', 'get_selected_voice', 'get_job', '_production_budget_notice')),
                          (youtube, ('list_jobs', 'connection_status', '_completed_jobs'))):
        for name in names:
            monkeypatch.setattr(module, name, private)
    response = client.get(path, headers={'accept': 'text/html'}, follow_redirects=False)
    assert response.status_code == 303 and response.headers['location'] == '/studio/access'
    assert 'no-store' in response.headers['cache-control']
    private.assert_not_called()


@pytest.mark.parametrize('path', ['/studio/api/job/private-task', '/studio/api/youtube-metrics',
                                  '/studio/youtube/status', '/studio/youtube/production-budget'])
def test_api_auth_failure_is_still_401_even_with_html_accept(dashboard, path):
    _, _, client = dashboard
    response = client.get(path, headers={'accept': 'text/html'}, follow_redirects=False)
    assert response.status_code == 401 and 'location' not in response.headers


@pytest.mark.parametrize('token, status', [(None, 401), ('expired-session', 401), ('studio-secret', 200)])
def test_session_probe_does_not_read_mutate_or_expose_credentials(dashboard, monkeypatch, token, status):
    studio, _, client = dashboard
    reader = Mock(side_effect=AssertionError('session probe must not read backend'))
    monkeypatch.setattr(studio, 'list_jobs', reader)
    monkeypatch.setattr(studio, '_dashboard_metrics', reader)
    if token:
        client.cookies.set('youtube_studio_token', token)
    response = client.get('/studio/api/session')
    assert response.status_code == status
    assert response.json() == {'authenticated': status == 200}
    assert 'no-store' in response.headers['cache-control'] and 'set-cookie' not in response.headers
    assert 'studio-secret' not in response.text and 'expired-session' not in response.text
    reader.assert_not_called()


def test_dashboard_shows_pause_budget_and_video_without_creation_or_dispatch(dashboard, monkeypatch):
    studio, _, client = dashboard
    job = _ready_job()
    job['task_id'] = '11111111-1111-4111-8111-111111111111'
    before = deepcopy(job)
    monkeypatch.setattr(studio, 'list_jobs', lambda _: [job])
    monkeypatch.setattr(studio, '_dashboard_metrics', lambda _: {'channels': [{
        'channel_id': 'UC_test_channel', 'title': 'Kanal <script>unsafe</script>',
        'production_status': 'paused', 'remaining_topics': 3,
        'subscriber_count': None, 'video_count': 2, 'view_count': 300,
    }], 'videos': {}})
    dispatch = Mock(side_effect=AssertionError('dashboard must not start a video'))
    monkeypatch.setattr(studio.run_video_pipeline, 'delay', dispatch)
    client.cookies.set('youtube_studio_token', 'studio-secret')
    response = client.get('/studio')
    body = response.text
    assert response.status_code == 200 and 'no-store' in response.headers['cache-control']
    assert '<h1>Kontrol panelin</h1>' in body and 'Otomasyon kontrol bekliyor' in body
    assert 'BUDGET_STATUS' in body and 'Son videolar' in body and '3 konu sırada' in body
    assert 'href="/studio/create"' in body and 'id="studio-form"' not in body
    assert 'studio-secret' not in body and '<script>unsafe</script>' not in body
    assert f'href="/studio/job/{job["task_id"]}"' in body
    assert job == before
    dispatch.assert_not_called()


def test_dashboard_failed_job_read_does_not_show_zero_or_claim_active(dashboard, monkeypatch):
    studio, _, client = dashboard
    monkeypatch.setattr(studio, 'list_jobs', Mock(side_effect=RuntimeError('PRIVATE_CREDENTIAL')))
    client.cookies.set('youtube_studio_token', 'studio-secret')
    body = client.get('/studio').text
    assert 'Durum bilgisi alınamıyor' in body and 'Video listesi şu anda okunamıyor' in body
    assert body.count('<b>—</b>') == 3
    assert 'PRIVATE_CREDENTIAL' not in body and 'üretim planlandı' not in body


def test_dashboard_empty_channel_data_never_claims_automation_running(dashboard):
    _, _, client = dashboard
    client.cookies.set('youtube_studio_token', 'studio-secret')
    body = client.get('/studio').text
    assert 'Otomasyon durumu doğrulanıyor' in body and 'Kanal verileri bekleniyor' in body
    assert '7/24 çalışıyor' not in body
