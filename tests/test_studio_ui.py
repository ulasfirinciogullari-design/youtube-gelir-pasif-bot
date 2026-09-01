from __future__ import annotations

import importlib
import sys
import types

import pytest


@pytest.fixture
def ui_modules(monkeypatch):
    settings = types.SimpleNamespace(
        factory_api_token='studio-secret',
        google_redirect_uri='https://studio.example.test/studio/youtube/callback',
        openai_api_key='configured',
        elevenlabs_api_key='configured',
        pexels_api_key='configured',
        runwayml_api_secret='configured',
        bucket='bucket',
        endpoint='endpoint',
        redis_url='redis://example.test',
        gemini_critic_enabled=True,
        gemini_api_key='configured',
    )
    config_module = types.ModuleType('app.config')
    config_module.settings = settings

    celery_module = types.ModuleType('app.celery_app')
    celery_module.celery = object()

    tasks_module = types.ModuleType('app.tasks')
    tasks_module.UnsupportedLanguageError = ValueError
    tasks_module.normalize_pipeline_language = lambda value: str(value).casefold()
    tasks_module.plan_video_pipeline = types.SimpleNamespace(delay=lambda *_a, **_k: None)
    tasks_module.run_video_pipeline = types.SimpleNamespace(delay=lambda *_a, **_k: None)

    state_module = types.ModuleType('app.services.studio_state')
    state_module.consume_repair_checkpoint = lambda *_a, **_k: None
    state_module.create_job = lambda *_a, **_k: None
    state_module.get_job = lambda *_a, **_k: None
    state_module.list_jobs = lambda *_a, **_k: []
    state_module.mark_failure = lambda *_a, **_k: {}
    state_module.mark_success = lambda *_a, **_k: {}
    state_module.save_repair_checkpoint = lambda *_a, **_k: None
    state_module.update_job = lambda *_a, **_k: {}

    voice_module = types.ModuleType('app.services.voice')
    voice_module.get_selected_voice = lambda: {'name': 'Doğal ses'}

    publish_tasks_module = types.ModuleType('app.publish_tasks')
    publish_tasks_module.publish_video_pipeline = types.SimpleNamespace(
        apply_async=lambda *_a, **_k: None,
    )

    youtube_auth_module = types.ModuleType('app.services.youtube_auth')
    youtube_auth_module.STATE_TTL_SECONDS = 600
    youtube_auth_module.YouTubeAuthError = type('YouTubeAuthError', (Exception,), {})
    youtube_auth_module.build_authorization_url = lambda *_a, **_k: 'https://accounts.example.test/'
    youtube_auth_module.complete_authorization = lambda *_a, **_k: None
    youtube_auth_module.connection_status = lambda *_a, **_k: {'configured': False}
    youtube_auth_module.discard_authorization_state = lambda *_a, **_k: None
    youtube_auth_module.disconnect = lambda *_a, **_k: None

    publish_state_module = types.ModuleType('app.services.youtube_publish_state')
    publish_state_module.UploadReservationError = type(
        'UploadReservationError',
        (Exception,),
        {},
    )
    publish_state_module.mark_upload_enqueued = lambda *_a, **_k: None
    publish_state_module.mark_upload_preflight_failed = lambda *_a, **_k: None
    publish_state_module.reserve_upload = lambda *_a, **_k: ({}, True)

    for name, module in {
        'app.config': config_module,
        'app.celery_app': celery_module,
        'app.tasks': tasks_module,
        'app.services.studio_state': state_module,
        'app.services.voice': voice_module,
        'app.publish_tasks': publish_tasks_module,
        'app.services.youtube_auth': youtube_auth_module,
        'app.services.youtube_publish_state': publish_state_module,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    app_package = importlib.import_module('app')
    monkeypatch.delattr(app_package, 'youtube_routes', raising=False)
    monkeypatch.delattr(app_package, 'studio', raising=False)
    monkeypatch.delitem(sys.modules, 'app.youtube_routes', raising=False)
    monkeypatch.delitem(sys.modules, 'app.studio', raising=False)
    studio = importlib.import_module('app.studio')
    youtube_routes = importlib.import_module('app.youtube_routes')
    return studio, youtube_routes


def _long_brief() -> str:
    return (
        'Kalkışta kabin ışıklarının neden kısıldığını gerçek bir yolcu '
        've aynı uçak içinde anlat. Parlak kabinden karanlığa geçişi, acil '
        'çıkış yolunu ve pist ışıklarını birbirini takip eden altı sahnede '
        'göster. Teknik açıklamayı insan deneyiminin önüne geçirme. '
        'Referans: https://sensitive.example/video?token=should-not-render'
    )


def _ready_job() -> dict:
    return {
        'task_id': 'job-123',
        'kind': 'render',
        'state': 'SUCCESS',
        'progress': 100,
        'created_at': '2026-09-01T21:15:00+00:00',
        'spec': {
            'topic': _long_brief(),
            'mode': 'preview',
            'duration_minutes': 0.5,
            'channel_id': 'merak-belgesel-tr-01',
        },
        'result': {
            'title': 'Uçakta Işıklar Neden Kısılır?',
            'duration': 30.0,
            'video_key': 'videos/job-123/final.mp4',
        },
    }


def test_studio_job_card_is_compact_but_keeps_accessible_full_brief(ui_modules):
    studio, _ = ui_modules
    html = studio._job_row(_ready_job())

    assert '<article class="job">' in html
    assert '<div class="job-title">Uçakta Işıklar Neden Kısılır?</div>' in html
    assert '<details class="brief-details">' in html
    assert '<summary>Yaratıcı talimatı gör</summary>' in html
    assert 'Teknik açıklamayı insan deneyiminin önüne geçirme.' in html
    assert 'https://sensitive.example' not in html
    assert '[bağlantı gizlendi]' in html
    assert '30 sn' in html
    assert '2 Eyl 2026 · 00:15' in html
    assert 'Kanal: merak-belgesel-tr-01' in html
    assert 'aria-label="Video bilgileri"' in html
    assert '-webkit-line-clamp:2' in studio.BASE_CSS


def test_studio_home_prioritizes_creation_and_preserves_all_form_controls(monkeypatch, ui_modules):
    studio, _ = ui_modules
    monkeypatch.setattr(studio.settings, 'factory_api_token', 'studio-secret')
    monkeypatch.setattr(studio, 'get_selected_voice', lambda: {'name': 'Doğal ses'})
    monkeypatch.setattr(
        studio,
        '_service_statuses',
        lambda: [('OpenAI', True), ('Storage', False), ('Redis', True)],
    )
    monkeypatch.setattr(studio, 'list_jobs', lambda _limit: [_ready_job()])

    body = studio.studio_home(studio_token='studio-secret').body.decode('utf-8')

    assert '<a class="skip-link" href="#main-content">' in body
    assert '<main id="main-content" tabindex="-1">' in body
    assert 'aria-label="Ana menü"' in body
    assert 'aria-current=page' in body
    assert '<details class="control-details">' in body
    assert 'Yaratıcı ve teknik ayarlar' in body
    for field in (
        'topic', 'duration_minutes', 'language', 'content_style', 'pace',
        'visual_mix', 'workflow', 'music', 'subtitles', 'reference_url',
        'channel_id',
    ):
        assert f'name="{field}"' in body
    assert '1 servis ayarı eksik' in body
    assert '<details class="system-details" open>' in body
    assert 'Videolar önce gizli yüklenir.' in body
    assert '<article class="job compact">' in body
    assert '<summary>Yaratıcı talimatı gör</summary>' in body
    assert '.job.compact .brief-details{display:none}' not in studio.BASE_CSS


def test_long_fallback_title_is_bounded_and_never_uses_a_url(ui_modules):
    studio, _ = ui_modules
    job = _ready_job()
    job['result'].pop('title')

    title = studio._job_title(job)

    assert len(title) <= 96
    assert title.endswith('…')
    assert 'https://' not in title


def test_job_api_keeps_critical_error_visible_but_redacts_links_and_secrets(
    monkeypatch,
    ui_modules,
):
    studio, _ = ui_modules
    monkeypatch.setattr(
        studio,
        '_sync_job',
        lambda _task_id: {
            'task_id': 'failed-job',
            'state': 'FAILURE',
            'stage': 'failed',
            'message': 'Sağlayıcı yanıt vermedi.',
            'error': (
                'Sağlayıcı hatası https://provider.example/internal '
                'token=do-not-render'
            ),
        },
    )

    body = studio.studio_job_api(
        'failed-job',
        studio_token='studio-secret',
    ).body.decode('utf-8')

    assert 'Sağlayıcı hatası' in body
    assert 'provider.example' not in body
    assert 'do-not-render' not in body
    assert 'gizlendi' in body


def test_ready_videos_use_two_line_title_details_and_labeled_private_action(monkeypatch, ui_modules):
    _, youtube_routes = ui_modules
    monkeypatch.setattr(youtube_routes.settings, 'factory_api_token', 'studio-secret')
    connections = [{
        'id': 'UC_channel_one',
        'title': 'Merak Kanalı',
        'connection_id': 'connection-one',
        'subscriber_count': '25',
        'video_count': '4',
        'view_count': '900',
    }]
    monkeypatch.setattr(
        youtube_routes,
        'connection_status',
        lambda: {
            'configured': True,
            'connected': True,
            'requires_reconnect': False,
            'connections': connections,
            'connection_count': 1,
            'connection_limit': 10,
        },
    )
    monkeypatch.setattr(youtube_routes, '_completed_jobs', lambda: [_ready_job()])

    body = youtube_routes.youtube_home(
        studio_token='studio-secret',
    ).body.decode('utf-8')

    assert '<article class="video-card">' in body
    assert '<div class="video-title">Uçakta Işıklar Neden Kısılır?</div>' in body
    assert '<details class="brief-details">' in body
    assert 'https://sensitive.example' not in body
    assert '[bağlantı gizlendi]' in body
    assert 'role="status"' not in body  # only shown after a new OAuth callback
    assert 'aria-label="Hedef YouTube kanalı"' in body
    assert 'name="youtube_channel_id"' in body
    assert '>Gizli yükle</button>' in body
    assert 'privacy_status' not in body
    assert 'public' not in body.casefold()
    assert '🔒 Yalnızca gizli yükleme' in body
    assert '-webkit-line-clamp:2' in youtube_routes.CSS


def test_oauth_configuration_error_does_not_print_callback_url(monkeypatch, ui_modules):
    _, youtube_routes = ui_modules
    monkeypatch.setattr(youtube_routes.settings, 'factory_api_token', 'studio-secret')
    monkeypatch.setattr(
        youtube_routes.settings,
        'google_redirect_uri',
        'https://studio.example.test/studio/youtube/callback?internal=value',
    )
    monkeypatch.setattr(
        youtube_routes,
        'connection_status',
        lambda: {'configured': False, 'connections': []},
    )
    monkeypatch.setattr(youtube_routes, '_completed_jobs', lambda: [])

    body = youtube_routes.youtube_home(
        studio_token='studio-secret',
    ).body.decode('utf-8')

    assert 'Google bağlantı ayarları eksik.' in body
    assert 'studio.example.test' not in body
    assert '<main id="main-content" tabindex="-1">' in body


def test_upload_progress_is_keyboard_and_screen_reader_friendly(monkeypatch, ui_modules):
    _, youtube_routes = ui_modules
    monkeypatch.setattr(youtube_routes.settings, 'factory_api_token', 'studio-secret')

    body = youtube_routes.youtube_publish_status(
        'private-upload-task',
        studio_token='studio-secret',
    ).body.decode('utf-8')

    assert 'role="progressbar"' in body
    assert 'aria-valuemin="0"' in body
    assert 'aria-valuemax="100"' in body
    assert "setAttribute('aria-valuenow',String(p))" in body
    assert '🔒 Gizli' in body
