"""Presentation-only YouTube center using the real shared Studio helpers."""
import ast
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from test_studio_workflow_presentation import ROOT, ui as studio_ui


SOURCE = '5fae28c5-7113-4794-b0f7-609fb39cc7d5'
PUBLISHER = 'fc5d7efd-3987-4b06-9cd2-06607e391154'
CAPITAL = '4ec1e176-5575-4e30-90ae-c926e3564a84'
READY = '00000000-0000-0000-0000-000000000099'
CHANNEL = 'UCgvESYtYbn2w9R2ExBOF_cw'
OTHER_CHANNEL = 'UC5v9AvNtD3PTLgo6m1jROOA'


def render(task_id=SOURCE, *, video='SgsK7rcVhQ8', privacy='private', release='blocked'):
    return {
        'task_id': task_id, 'kind': 'render', 'state': 'SUCCESS', 'progress': 100,
        'spec': {'topic': 'IKEA flat-pack history', 'channel_id': 'margin-verdict',
                 'format': 'shorts', 'language': 'en', 'duration_minutes': .5,
                 'production_channel_id': CHANNEL},
        'result': {'task_id': task_id, 'title': 'Why IKEA packs furniture flat',
                   'video_key': f'videos/{task_id}/final.mp4', 'duration': 30,
                   'quality_disposition': 'automated_qc_pass', 'manual_qa_required': False,
                   'youtube_automation': {'status': 'queued', 'publish_task_id': PUBLISHER},
                   'youtube': {'video_id': video, 'url': f'https://www.youtube.com/watch?v={video}',
                               'target_channel_id': CHANNEL, 'connection_id': 'connection-one',
                               'privacy_status': privacy, 'release_status': release,
                               'caption_uploaded': release != 'blocked',
                               'release_error_code': 'HttpError_403' if release == 'blocked' else None}},
    }


def publisher(source):
    output = source['result']['youtube']
    return {
        'task_id': PUBLISHER, 'kind': 'publish', 'state': 'SUCCESS', 'progress': 100,
        'parent_id': source['task_id'], 'spec': {'source_task_id': source['task_id']},
        'result': {'task_id': PUBLISHER, 'status': 'complete', 'source_task_id': source['task_id'],
                   **{k: v for k, v in output.items() if k not in {'video_id', 'url'}},
                   'youtube_video_id': output['video_id'], 'youtube_url': output['url']},
    }


@pytest.fixture
def dashboard():
    studio = studio_ui.__wrapped__()
    model = {'channels': [{'channel_id': CHANNEL, 'title': 'Margin Verdict',
                          'subscriber_count': 0, 'subscriber_count_hidden': False,
                          'video_count': 0, 'view_count': None, 'status': 'fresh',
                          'fetched_at': '2026-09-06T00:00:00+00:00'}],
             'videos': {}, 'updated_at': '2026-09-06T00:00:00+00:00'}
    cache_read = Mock(side_effect=lambda _jobs: deepcopy(model))
    studio.ns['_dashboard_metrics'] = cache_read
    shared = SimpleNamespace(**studio.ns)
    records = {}
    forbidden = Mock(side_effect=AssertionError('No writes or provider calls from presentation'))
    channels = [{'id': CHANNEL, 'title': 'Margin Verdict', 'connection_id': 'connection-one',
                 'subscriber_count': '999999', 'video_count': '777777', 'view_count': '888888'}]
    lookup = Mock(side_effect=lambda key: deepcopy(records.get(key)))
    namespace = {
        'settings': SimpleNamespace(factory_api_token='owner-cookie', google_redirect_uri='https://studio.example/studio/youtube/callback'),
        'get_job': lookup, 'list_jobs': lambda _limit: deepcopy(list(records.values())),
        'list_channel_profiles': lambda: [], 'automated_quality_approved': shared._job_quality_approved,
        'connection_status': Mock(side_effect=lambda: {'configured': True, 'connections': deepcopy(channels), 'connection_count': len(channels)}),
        'STATE_TTL_SECONDS': 600, 'YouTubeAuthError': RuntimeError,
        'OAuthStateError': type('OAuthStateError', (RuntimeError,), {}),
        'YouTubeAutomationError': RuntimeError, 'ProfileConflictError': RuntimeError,
        'UploadReservationError': RuntimeError,
        'create_job': forbidden, 'mark_failure': forbidden, 'save_channel_profile': forbidden,
        'reserve_upload': forbidden, 'mark_upload_enqueued': forbidden, 'mark_upload_preflight_failed': forbidden,
        'publish_video_pipeline': SimpleNamespace(apply_async=forbidden),
    }
    tree = ast.parse((ROOT / 'app/youtube_routes.py').read_text(encoding='utf-8'))
    tree.body = [n for n in tree.body if not (isinstance(n, ast.ImportFrom) and (n.module or '').startswith('app'))]
    exec(compile(tree, '<youtube-dashboard>', 'exec'), namespace)
    namespace['_studio_presentation'] = lambda: shared
    app = FastAPI()
    app.include_router(namespace['router'])
    client = TestClient(app)
    client.cookies.set('youtube_studio_token', 'owner-cookie')
    return SimpleNamespace(ns=namespace, shared=shared, records=records, forbidden=forbidden,
                           model=model, read=cache_read, client=client, channels=channels, lookup=lookup)


def test_margin_private_blocked_is_uploaded_attention_not_ready(dashboard):
    source = render()
    dashboard.records.update({SOURCE: source, PUBLISHER: publisher(source)})
    before = deepcopy(dashboard.records)
    body = dashboard.client.get('/studio/youtube').text
    uploaded, ready = body.split('<h2>Yüklenen videolar</h2>')[1].split('<h2>Yüklenmeye hazır</h2>')
    assert 'Why IKEA packs furniture flat' in uploaded and 'Why IKEA packs furniture flat' not in ready
    assert 'YouTube’a gizli yüklendi' in uploaded and 'Kontrol gerekiyor' in uploaded
    assert 'Shorts' in uploaded and 'English' in uploaded and 'Margin Verdict' in uploaded
    assert 'YouTube’da yayında' not in uploaded
    assert f'action="/studio/youtube/publish/{SOURCE}"' not in body
    assert f'/studio/youtube/publish-status/{PUBLISHER}' in body
    assert dashboard.records == before
    dashboard.forbidden.assert_not_called()
    dashboard.read.assert_called_once()


def test_public_video_and_not_uploaded_video_are_separate(dashboard):
    public = render(CAPITAL, video='hGsspOyWEIU', privacy='public', release='public')
    public['result']['youtube_automation'] = {}
    public['result']['title'] = 'Doların kâğıdı'
    public['spec'].update(format='landscape', language='tr')
    ready = render(READY)
    ready['result'].pop('youtube')
    ready['result'].pop('youtube_automation')
    ready['result']['title'] = 'Next documentary'
    dashboard.records.update({CAPITAL: public, READY: ready})
    body = dashboard.client.get('/studio/youtube').text
    uploaded, waiting = body.split('<h2>Yüklenen videolar</h2>')[1].split('<h2>Yüklenmeye hazır</h2>')
    assert 'YouTube’da yayında' in uploaded and 'Doların kâğıdı' in uploaded
    assert 'Normal video' in uploaded and 'Türkçe' in uploaded
    assert 'Next documentary' not in uploaded and 'Next documentary' in waiting
    assert f'action="/studio/youtube/publish/{READY}"' in waiting
    assert f'action="/studio/youtube/publish/{CAPITAL}"' not in body


def test_cached_metrics_zero_unknown_and_management_forms_outside_refresh_host(dashboard):
    body = dashboard.client.get('/studio/youtube').text
    assert '<b>0</b><span>Abone</span>' in body
    assert '<b>0</b><span>Herkese açık video</span>' in body
    assert 'Veri bekleniyor' in body
    assert all(stale not in body for stale in ('999999', '777777', '888888'))
    assert f'id="channel-{CHANNEL}"' in body
    assert f'href="/studio/youtube#channel-{CHANNEL}"' in body
    start = body.index('<div id="channel-overview-host">')
    end = body.index('</section>', start)
    assert '<form' not in body[start:end]
    assert f'action="/studio/youtube/disconnect"' in body[end:]
    assert '300000' in body and 'visibilityState' in body
    assert '/studio/api/youtube-metrics' in body
    assert body.count('id="metrics-refresh"') == 1


@pytest.mark.parametrize('kind', ['channel', 'title', 'url', 'publisher_pointer'])
def test_untrusted_display_fields_never_inject_html_or_links(dashboard, kind):
    source = render()
    evil = '\"><img src=x onerror=alert(1)>'
    if kind == 'channel':
        dashboard.channels[0]['title'] = evil
        dashboard.model['channels'][0]['title'] = evil
    elif kind == 'title':
        source['result']['title'] = evil
    elif kind == 'url':
        source['result']['youtube']['url'] = 'javascript:alert(1)'
    else:
        source['result']['youtube_automation']['publish_task_id'] = '../' + evil
    dashboard.records[SOURCE] = source
    body = dashboard.client.get('/studio/youtube').text
    assert '<img src=x' not in body and 'href="javascript:' not in body
    if kind != 'url':
        assert 'https://www.youtube.com/watch?v=SgsK7rcVhQ8' in body
    else:
        assert 'YouTube görünürlüğü doğrulanamadı' in body
        assert f'action="/studio/youtube/publish/{SOURCE}"' not in body
    assert '../' + evil not in body


def test_publisher_success_blocked_page_reports_real_private_delivery(dashboard):
    source = render()
    dashboard.records.update({SOURCE: source, PUBLISHER: publisher(source)})
    response = dashboard.client.get('/studio/youtube/publish-status/' + PUBLISHER)
    assert response.status_code == 200
    body = response.text
    assert '<h1 id="delivery-title">YouTube’a gizli yüklendi</h1>' in body
    assert '<b>Yayın durdu</b>' in body and 'Kontrol gerekiyor' in body
    assert 'aria-valuenow="100"' in body and 'Dosyanın yüklenmesi' in body
    assert 'Gizli yükleme</div><h1>' not in body
    assert 'j.publication_status' in body and 'j.delivery_label' in body
    assert 'x.youtube_url' not in body and 'j.task_id!==id' in body
    assert '<form' not in body and '>Gizli yükle<' not in body
    dashboard.forbidden.assert_not_called()


def test_verified_current_source_release_wins_over_historical_private_publisher(dashboard):
    source = render(privacy='public', release='public')
    child = publisher(source)
    child['result'].update(privacy_status='private', release_status='private')
    dashboard.records.update({SOURCE: source, PUBLISHER: child})
    body = dashboard.client.get('/studio/youtube/publish-status/' + PUBLISHER).text
    assert '<h1 id="delivery-title">YouTube’da yayında</h1>' in body
    assert f'const id="{SOURCE}"' in body
    assert dashboard.records[PUBLISHER]['result']['privacy_status'] == 'private'


@pytest.mark.parametrize('damage', ['source_task', 'parent', 'video', 'channel', 'connection'])
def test_status_page_does_not_borrow_unrelated_public_source(dashboard, damage):
    source = render(privacy='public', release='public')
    child = publisher(source)
    child['result'].update(privacy_status='private', release_status='blocked')
    if damage == 'source_task': source['task_id'] = READY
    if damage == 'parent': child['parent_id'] = READY
    if damage == 'video': child['result']['youtube_video_id'] = 'Different12'
    if damage == 'channel': child['result']['target_channel_id'] = OTHER_CHANNEL
    if damage == 'connection': child['result']['connection_id'] = 'different-generation'
    dashboard.records.update({SOURCE: source, PUBLISHER: child})
    body = dashboard.client.get('/studio/youtube/publish-status/' + PUBLISHER).text
    assert '<h1 id="delivery-title">YouTube’da yayında</h1>' not in body
    assert f'const id="{PUBLISHER}"' in body


@pytest.mark.parametrize('path', ['/studio/youtube', '/studio/youtube/publish-status/' + PUBLISHER])
def test_owner_auth_precedes_any_lookup_or_metrics(dashboard, path):
    dashboard.client.cookies.clear()
    assert dashboard.client.get(path).status_code == 401
    dashboard.read.assert_not_called()
    dashboard.lookup.assert_not_called()
    dashboard.ns['connection_status'].assert_not_called()


def test_noncanonical_status_id_never_enters_inline_script_or_lookup(dashboard):
    response = dashboard.ns['youtube_publish_status']('</script><script>alert(1)</script>', studio_token='owner-cookie')
    body = response.body.decode()
    assert '</script><script>alert(1)' not in body and 'const id=""' in body
    dashboard.lookup.assert_not_called()


def test_missing_metrics_remain_unknown_and_safe(dashboard):
    dashboard.model.update(channels=[], videos={}, updated_at=None)
    dashboard.records[SOURCE] = render()
    body = dashboard.client.get('/studio/youtube').text
    assert 'Veri bekleniyor' in body and 'YouTube’a gizli yüklendi' in body
    assert 'Kontrol gerekiyor' in body
    dashboard.forbidden.assert_not_called()
