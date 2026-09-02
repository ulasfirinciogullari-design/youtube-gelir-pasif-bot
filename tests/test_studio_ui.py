from __future__ import annotations

import importlib
import json
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
    state_module.claim_retry_dispatch = lambda *_a, **_k: None
    state_module.consume_repair_checkpoint = lambda *_a, **_k: None
    state_module.create_job = lambda *_a, **_k: None
    state_module.get_job = lambda *_a, **_k: None
    state_module.list_jobs = lambda *_a, **_k: []
    state_module.mark_failure = lambda *_a, **_k: {}
    state_module.mark_retry_dispatch = lambda *_a, **_k: True
    state_module.mark_success = lambda *_a, **_k: {}
    state_module.save_repair_checkpoint = lambda *_a, **_k: None
    state_module.sync_repair_checkpoint_state = lambda *_a, **_k: {
        'repair_available': False,
        'repair_claimed': False,
    }
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


def test_studio_job_card_is_compact_with_one_action_and_collapsed_details(ui_modules):
    studio, _ = ui_modules
    html = studio._job_row(_ready_job())

    assert '<article class="job" data-status="ready"' in html
    assert '<div class="job-title">Uçakta Işıklar Neden Kısılır?</div>' in html
    assert '<details class="job-details"><summary>Teknik ayrıntılar</summary>' in html
    assert '<b>Yaratıcı talimat</b>' in html
    assert 'Teknik açıklamayı insan deneyiminin önüne geçirme.' in html
    assert 'https://sensitive.example' not in html
    assert '[bağlantı gizlendi]' in html
    assert '<span class="state ready">Hazır</span>' in html
    assert '>Gizli yükle</a>' in html
    assert html.count('class="btn ') == 1
    assert '30 sn' in html
    assert '2 Eyl 2026 · 00:15' in html
    assert '<b>Hedef / profil</b> merak-belgesel-tr-01' in html
    assert 'Güncellendi 2 Eyl 2026 · 00:15' in html
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
    for control_id in (
        'topic', 'duration', 'language', 'content-style', 'pace',
        'visual-mix', 'workflow', 'music', 'subtitles', 'reference-url',
        'channel-id',
    ):
        assert f'for="{control_id}"' in body
        assert f'id="{control_id}"' in body
    assert 'Storage ayarı eksik' in body
    assert '<details class="system-details">' in body
    assert '<details class="system-details" open>' not in body
    assert 'Videolar önce gizli yüklenir.' in body
    assert '<article class="job compact" data-status="ready"' in body
    assert '<details class="job-details">' not in body
    assert body.count('data-status-filter=') == 4
    assert 'data-status-count="running">0</span>' in body
    assert 'data-status-count="ready">1</span>' in body
    assert 'Üretim masası' in body


def test_studio_home_names_fal_as_optional_when_runway_is_ready(
    monkeypatch,
    ui_modules,
):
    studio, _ = ui_modules
    monkeypatch.setattr(studio.settings, 'factory_api_token', 'studio-secret')
    monkeypatch.setattr(studio, 'get_selected_voice', lambda: {'name': 'Doğal ses'})
    monkeypatch.setattr(
        studio,
        '_service_statuses',
        lambda: [
            ('OpenAI', True),
            ('Runway', True),
            ('Fal video', False),
            ('Storage', True),
        ],
    )
    monkeypatch.setattr(studio, 'list_jobs', lambda _limit: [])

    body = studio.studio_home(studio_token='studio-secret').body.decode('utf-8')

    assert 'Fal video isteğe bağlı · üretim çalışır' in body
    assert 'Fal video<span class="tiny" style="margin-left:auto">İsteğe bağlı' in body
    assert '<span class="health-dot green"' in body
    assert '<span class="dot amber"' in body
    assert '3/4' in body


def test_studio_home_keeps_runway_required_when_only_fal_is_ready(
    monkeypatch,
    ui_modules,
):
    studio, _ = ui_modules
    monkeypatch.setattr(studio.settings, 'factory_api_token', 'studio-secret')
    monkeypatch.setattr(studio, 'get_selected_voice', lambda: {'name': 'Doğal ses'})
    monkeypatch.setattr(
        studio,
        '_service_statuses',
        lambda: [
            ('OpenAI', True),
            ('Runway', False),
            ('Fal video', True),
            ('Storage', True),
        ],
    )
    monkeypatch.setattr(studio, 'list_jobs', lambda _limit: [])

    body = studio.studio_home(studio_token='studio-secret').body.decode('utf-8')

    assert 'Runway ayarı eksik' in body
    assert 'Runway<span class="tiny" style="margin-left:auto">Eksik' in body
    assert '<span class="health-dot red"' in body
    assert '<span class="dot red"' in body
    assert 'Runway isteğe bağlı' not in body
    assert '3/4' in body


def test_studio_home_uses_a_simple_topic_input_and_collapsed_guidance(
    monkeypatch,
    ui_modules,
):
    studio, _ = ui_modules
    monkeypatch.setattr(studio.settings, 'factory_api_token', 'studio-secret')
    monkeypatch.setattr(studio, 'get_selected_voice', lambda: {'name': 'Doğal ses'})
    monkeypatch.setattr(studio, '_service_statuses', lambda: [('OpenAI', True)])
    monkeypatch.setattr(studio, 'list_jobs', lambda _limit: [])

    body = studio.studio_home(studio_token='studio-secret').body.decode('utf-8')
    main_input, advanced = body.split('<details class="control-details">', 1)

    assert '<label class="field" for="topic">Video konusu</label>' in main_input
    assert 'Örnek: Telefon neden yastık altında ısınır?' in main_input
    assert 'placeholder="Konuyu bir cümleyle yaz"></textarea>' in main_input
    assert 'Konu ve yaratıcı talimat' not in body
    assert 'Tek bir gündelik sorun' not in main_input
    assert 'Tek bir gündelik sorun' in advanced
    assert 'Bunları yazmak zorunda değilsin' in advanced


@pytest.mark.parametrize(
    ('job', 'expected'),
    [
        ({'state': 'PENDING'}, 'running'),
        ({'state': 'PROGRESS'}, 'running'),
        ({'state': 'SUCCESS'}, 'ready'),
        ({'state': 'AWAITING_APPROVAL'}, 'ready'),
        ({'state': 'FAILURE', 'repair_available': True}, 'repair'),
        ({'state': 'FAILURE', 'repair_available': False}, 'failed'),
        ({'state': 'FAILURE', 'retry_claimed': True}, 'running'),
        ({'state': 'FAILURE', 'retry_child_task_id': 'child-task'}, 'running'),
    ],
)
def test_studio_exposes_only_four_user_statuses(job, expected, ui_modules):
    studio, _ = ui_modules

    assert studio._job_ui_status(job) == expected
    assert studio._job_ui_status(job) in studio.UI_STATUS_ORDER


def test_each_job_status_has_exactly_one_targeted_primary_action(ui_modules):
    studio, _ = ui_modules
    base = {
        'task_id': 'source-task',
        'kind': 'render',
        'spec': {'topic': 'Tek bir konu', 'mode': 'preview'},
    }
    cases = [
        ({**base, 'state': 'PROGRESS'}, 'Durumu aç'),
        ({**base, 'state': 'SUCCESS', 'result': {'video_key': 'videos/final.mp4'}}, 'Gizli yükle'),
        ({**base, 'state': 'FAILURE', 'repair_available': True}, 'Sorunlu sahneyi onar'),
        ({**base, 'state': 'FAILURE', 'repair_available': False}, 'Aynı ayarlarla tekrar dene'),
    ]

    for job, label in cases:
        html = studio._job_row(job)
        assert label in html
        assert html.count('class="btn ') == 1


def test_ready_render_prioritizes_private_upload_over_expiring_download(ui_modules):
    studio, _ = ui_modules
    job = _ready_job()
    job['result']['download_url'] = 'https://temporary.example.test/final.mp4'

    html = studio._job_row(job)

    assert '>Gizli yükle</a>' in html
    assert '>Videoyu aç</a>' not in html
    assert 'temporary.example.test' not in html
    assert html.count('class="btn ') == 1


def test_publish_mode_and_authorization_headers_are_human_safe(ui_modules):
    studio, _ = ui_modules
    publish = {
        'task_id': 'publish-task',
        'kind': 'publish',
        'state': 'PROGRESS',
        'spec': {
            'topic': 'Otomatik yayın',
            'mode': 'autonomous_publish',
        },
        'error': 'Authorization: Bearer sk-live-secret Basic dXNlcjpwYXNz',
    }

    html = studio._job_row(publish)

    assert 'Otomatik gizli yükleme' in html
    assert 'autonomous_publish' not in html
    assert 'sk-live-secret' not in html
    assert 'dXNlcjpwYXNz' not in html
    assert 'Authorization=[gizlendi]' in html


@pytest.mark.parametrize(
    'secret_field',
    ['refresh_token', 'access_token', 'id_token', 'client_secret'],
)
def test_compound_credentials_are_redacted(secret_field, ui_modules):
    studio, _ = ui_modules

    safe = studio._safe_ui_text(f'provider {secret_field}=never-show-this')

    assert 'never-show-this' not in safe
    assert '[gizlendi]' in safe


def test_claimed_retry_becomes_running_and_links_to_child_without_second_retry(ui_modules):
    studio, _ = ui_modules
    job = {
        'task_id': 'source-task',
        'kind': 'render',
        'state': 'FAILURE',
        'retry_claimed': True,
        'retry_child_task_id': 'retry-child-task',
        'retry_dispatch_state': 'uncertain',
        'spec': {'topic': 'Tek bir konu'},
    }

    html = studio._job_row(job)

    assert 'data-status="running"' in html
    assert 'href="/studio/job/retry-child-task"' in html
    assert 'ikinci kez başlatılmayacak' in html
    assert 'action="/studio/retry/' not in html
    assert html.count('class="btn ') == 1


def test_retry_source_is_collapsed_when_child_record_is_present(ui_modules):
    studio, _ = ui_modules
    source = {
        'task_id': 'source-task',
        'state': 'FAILURE',
        'retry_claimed': True,
        'retry_child_task_id': 'retry-child-task',
        'spec': {'topic': 'Eski başarısız deneme'},
    }
    child = {
        'task_id': 'retry-child-task',
        'state': 'SUCCESS',
        'kind': 'render',
        'spec': {'topic': 'Güncel deneme'},
        'result': {'video_key': 'videos/retry-child-task/final.mp4'},
    }

    visible = studio._collapse_retry_sources([source, child])

    assert visible == [child]
    assert studio._status_counts(visible) == {
        'running': 0,
        'ready': 1,
        'repair': 0,
        'failed': 0,
    }
    assert studio._collapse_retry_sources([source]) == [source]


def test_legacy_duplicate_failures_group_only_identical_recent_root_attempts(
    ui_modules,
):
    studio, _ = ui_modules
    frozen_spec = {
        'topic': 'Telefon neden yastık altında ısınır?',
        'duration_minutes': 0.5,
        'language': 'tr',
        'channel_id': 'teknoloji-tr',
        'mode': 'preview',
        'workflow': 'auto',
        'content_style': 'technology',
        'pace': 'balanced',
        'visual_mix': 'balanced',
        'music': 'off',
        'subtitles': 'sidecar',
    }
    newest = {
        'task_id': 'latest-root-failure',
        'kind': 'render',
        'state': 'FAILURE',
        'created_ts': 10_000,
        'spec': dict(frozen_spec),
    }
    older = {
        'task_id': 'older-root-failure',
        'kind': 'render',
        'state': 'FAILURE',
        'created_ts': 9_900,
        'spec': dict(frozen_spec),
    }
    different_channel = {
        'task_id': 'other-channel-failure',
        'kind': 'render',
        'state': 'FAILURE',
        'created_ts': 9_800,
        'spec': {**frozen_spec, 'channel_id': 'teknoloji-en'},
    }

    visible = studio._collapse_retry_sources(
        [newest, older, different_channel]
    )

    assert [job['task_id'] for job in visible] == [
        'latest-root-failure',
        'other-channel-failure',
    ]
    assert visible[0]['_grouped_failure_attempts'] == 1
    assert '_grouped_failure_attempts' not in newest
    assert studio._status_counts(visible)['failed'] == 2
    assert '1 eski başarısız deneme bu kartta toplandı.' in studio._job_row(
        visible[0]
    )


def test_legacy_failure_grouping_preserves_distinct_parent_linked_workflows(
    ui_modules,
):
    studio, _ = ui_modules
    spec = {
        'topic': 'Aynı konu',
        'duration_minutes': 0.5,
        'language': 'tr',
        'mode': 'preview',
    }
    first_workflow = {
        'task_id': 'child-a',
        'parent_id': 'workflow-a',
        'kind': 'render',
        'state': 'FAILURE',
        'created_ts': 20_000,
        'spec': dict(spec),
    }
    second_workflow = {
        'task_id': 'child-b',
        'parent_id': 'workflow-b',
        'kind': 'render',
        'state': 'FAILURE',
        'created_ts': 19_999,
        'spec': dict(spec),
    }
    older_first_workflow_attempt = {
        'task_id': 'child-a-old',
        'parent_id': 'workflow-a',
        'kind': 'render',
        'state': 'FAILURE',
        'created_ts': 1,
        'spec': dict(spec),
    }

    visible = studio._collapse_retry_sources([
        first_workflow,
        second_workflow,
        older_first_workflow_attempt,
    ])

    assert [job['task_id'] for job in visible] == ['child-a', 'child-b']
    assert visible[0]['_grouped_failure_attempts'] == 1
    assert studio._status_counts(visible)['failed'] == 2


def test_legacy_duplicate_failures_outside_retry_window_remain_separate(
    ui_modules,
):
    studio, _ = ui_modules
    spec = {
        'topic': 'Aylık tekrar üretimi',
        'duration_minutes': 0.5,
        'language': 'tr',
        'mode': 'preview',
    }
    current = {
        'task_id': 'current',
        'state': 'FAILURE',
        'created_ts': 50_000,
        'spec': dict(spec),
    }
    earlier = {
        'task_id': 'earlier',
        'state': 'FAILURE',
        'created_ts': 50_000 - studio.LEGACY_RETRY_GROUP_WINDOW_SECONDS - 1,
        'spec': dict(spec),
    }

    assert studio._collapse_retry_sources([current, earlier]) == [current, earlier]


def test_workflow_history_shows_only_the_latest_child_step(ui_modules):
    studio, _ = ui_modules
    plan = {
        'task_id': 'plan-task',
        'kind': 'plan',
        'state': 'AWAITING_APPROVAL',
    }
    render = {
        'task_id': 'render-task',
        'parent_id': 'plan-task',
        'kind': 'render',
        'state': 'SUCCESS',
    }
    publish = {
        'task_id': 'publish-task',
        'parent_id': 'render-task',
        'kind': 'publish',
        'state': 'PROGRESS',
    }

    assert studio._collapse_retry_sources([publish, render, plan]) == [publish]

    failed_publish = {**publish, 'state': 'FAILURE'}
    assert studio._collapse_retry_sources(
        [failed_publish, render, plan]
    ) == [render]


def test_history_filters_on_server_and_paginates_at_twelve(monkeypatch, ui_modules):
    studio, _ = ui_modules
    ready_jobs = []
    for index in range(13):
        job = _ready_job()
        job['task_id'] = f'ready-{index}'
        job['result'] = dict(job['result'], title=f'Hazır video {index}')
        ready_jobs.append(job)
    failed = {
        'task_id': 'failed-one',
        'kind': 'render',
        'state': 'FAILURE',
        'spec': {'topic': 'Başarısız video'},
    }
    monkeypatch.setattr(studio, 'list_jobs', lambda _limit: [*ready_jobs, failed])
    monkeypatch.setattr(
        studio,
        '_sync_job',
        lambda task_id: next(
            job for job in [*ready_jobs, failed] if job['task_id'] == task_id
        ),
    )

    first = studio.studio_history(
        status='ready',
        page=1,
        studio_token='studio-secret',
    ).body.decode('utf-8')
    second = studio.studio_history(
        status='ready',
        page=2,
        studio_token='studio-secret',
    ).body.decode('utf-8')

    assert 'data-history-status="ready"' in first
    assert first.count('<article class="job"') == 12
    assert 'Başarısız video' not in first
    assert 'data-status-count="ready">13</span>' in first
    assert 'data-status-count="failed">1</span>' in first
    assert 'Sayfa 1 / 2' in first
    assert 'status=ready&amp;page=2' in first
    assert second.count('<article class="job"') == 1
    assert 'Sayfa 2 / 2' in second


def test_history_default_explains_running_first_and_uses_same_collapsed_counts(
    monkeypatch,
    ui_modules,
):
    studio, _ = ui_modules
    duplicate_spec = {
        'topic': 'Aynı hızlı test',
        'duration_minutes': 0.5,
        'language': 'tr',
        'mode': 'preview',
    }
    jobs = [
        {
            'task_id': 'running-one',
            'kind': 'render',
            'state': 'PROGRESS',
            'progress': 25,
            'created_ts': 3_000,
            'spec': {'topic': 'Devam eden video', 'mode': 'preview'},
        },
        {
            'task_id': 'failed-new',
            'kind': 'render',
            'state': 'FAILURE',
            'created_ts': 2_000,
            'spec': dict(duplicate_spec),
        },
        {
            'task_id': 'failed-old',
            'kind': 'render',
            'state': 'FAILURE',
            'created_ts': 1_900,
            'spec': dict(duplicate_spec),
        },
    ]
    job_by_id = {job['task_id']: job for job in jobs}
    monkeypatch.setattr(studio, 'list_jobs', lambda _limit: jobs)
    monkeypatch.setattr(studio, '_sync_job', lambda task_id: job_by_id[task_id])

    body = studio.studio_history(
        studio_token='studio-secret',
    ).body.decode('utf-8')
    failed_body = studio.studio_history(
        status='failed',
        studio_token='studio-secret',
    ).body.decode('utf-8')

    assert 'data-history-status="running"' in body
    assert 'Önce devam eden videolar gösterilir.' in body
    assert 'Bu görünüm: Üretiliyor · en fazla 12 iş' in body
    assert 'Toplam 2 görünür video' in body
    assert 'data-status-count="running">1</span>' in body
    assert 'data-status-count="failed">1</span>' in body
    assert 'Aynı hızlı test' not in body
    assert failed_body.count('<article class="job"') == 1
    assert 'data-status-count="failed">1</span>' in failed_body
    assert '1 eski başarısız deneme bu kartta toplandı.' in failed_body


def test_history_reconciles_only_the_visible_page(monkeypatch, ui_modules):
    studio, _ = ui_modules
    jobs = [
        {
            'task_id': f'failed-{index}',
            'kind': 'render',
            'state': 'FAILURE',
            'spec': {'topic': f'Başarısız video {index}'},
        }
        for index in range(500)
    ]
    job_by_id = {job['task_id']: job for job in jobs}
    synced = []
    monkeypatch.setattr(studio, 'list_jobs', lambda _limit: jobs)
    monkeypatch.setattr(
        studio,
        '_sync_job',
        lambda task_id: synced.append(task_id) or job_by_id[task_id],
    )

    body = studio.studio_history(
        status='failed',
        page=1,
        studio_token='studio-secret',
    ).body.decode('utf-8')

    assert len(synced) == studio.HISTORY_PAGE_SIZE
    assert body.count('<article class="job"') == studio.HISTORY_PAGE_SIZE
    assert 'data-status-count="failed">500</span>' in body


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
    payload = json.loads(body)

    assert 'Sağlayıcı hatası' in body
    assert 'provider.example' not in body
    assert 'do-not-render' not in body
    assert 'gizlendi' in body
    assert payload['ui_status'] == 'failed'
    assert payload['ui_status_label'] == 'Başarısız'
    assert payload['ui_status_message'].startswith('Üretim tamamlanamadı')


def test_job_view_keeps_error_inside_closed_technical_details(monkeypatch, ui_modules):
    studio, _ = ui_modules
    record = {
        'task_id': 'repair-job',
        'kind': 'render',
        'state': 'FAILURE',
        'stage': 'failed',
        'failure_stage': 'visual_qc',
        'repair_available': True,
        'error': 'provider_error token=secret-value',
        'spec': {'topic': 'Uçak videosu'},
    }
    monkeypatch.setattr(studio, 'get_job', lambda _task_id: record)

    body = studio.studio_job(
        'repair-job',
        studio_token='studio-secret',
    ).body.decode('utf-8')

    assert '<details class="technical-details">' in body
    assert '<details class="technical-details" open>' not in body
    assert '<pre>' not in body
    assert 'Yalnızca sorunlu sahne yeniden üretilecek' in body
    assert '>Sorunlu sahneyi onar</button>' in body
    assert 'secret-value' not in body
    assert 'token=[gizlendi]' in body


def test_claimed_repair_ui_hides_duplicate_form_and_links_child(
    monkeypatch,
    ui_modules,
):
    studio, _ = ui_modules
    monkeypatch.setattr(
        studio,
        'get_job',
        lambda _task_id: {
            'task_id': 'failed-job',
            'kind': 'render',
            'state': 'FAILURE',
            'repair_claimed': True,
            'retry_claimed': True,
            'retry_child_task_id': 'repair-child',
            'spec': {'topic': 'Onarılan video'},
        },
    )

    body = studio.studio_job(
        'failed-job',
        studio_token='studio-secret',
    ).body.decode('utf-8')
    initial_html = body.split('<script>', 1)[0]

    assert 'href="/studio/job/repair-child"' in initial_html
    assert 'Onarım durumunu aç' in initial_html
    assert 'action="/studio/retry/' not in initial_html
    assert initial_html.count('class="btn ') == 1
    assert "const child=String(j.retry_child_task_id||'').trim()" in body
    assert "setAction('running:'+target" in body


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
    assert 'action="/studio/youtube/public' not in body.casefold()
    assert 'İlk yükleme daima gizli' in body
    assert '🔒 İlk yükleme daima gizli' in body
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
