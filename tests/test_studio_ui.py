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
    publish_state_module.get_upload_record = lambda *_a, **_k: None
    publish_state_module.mark_upload_preflight_failed = lambda *_a, **_k: None
    publish_state_module.reserve_upload = lambda *_a, **_k: ({}, True)
    metrics_module = types.ModuleType('app.services.youtube_metrics')
    metrics_module.get_dashboard_metrics = lambda *_a, **_k: {'channels': [], 'videos': {}, 'updated_at': None, 'refresh_after_seconds': 300}
    metrics_module.refresh_dashboard_metrics = metrics_module.get_dashboard_metrics

    for name, module in {
        'app.config': config_module,
        'app.celery_app': celery_module,
        'app.tasks': tasks_module,
        'app.services.studio_state': state_module,
        'app.services.voice': voice_module,
        'app.publish_tasks': publish_tasks_module,
        'app.services.youtube_auth': youtube_auth_module,
        'app.services.youtube_publish_state': publish_state_module,
        'app.services.youtube_metrics': metrics_module,
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
            'quality_disposition': 'automated_qc_pass',
            'manual_qa_required': False,
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
    assert '<span class="state rendered">Üretildi · YouTube’a yüklenmedi</span>' in html
    assert '>Gizli yükle</a>' in html
    assert html.count('class="btn ') == 1
    assert '30 sn' in html
    assert '2 Eyl 2026 · 00:15' in html
    assert '<span>merak-belgesel-tr-01</span>' in html
    assert 'Güncellendi 2 Eyl 2026 · 00:15' not in html
    assert 'aria-label="Video bilgileri"' in html
    assert '-webkit-line-clamp:2' in studio.BASE_CSS


def test_channel_automation_ui_defaults_disabled_and_escapes_topics(ui_modules):
    _, youtube = ui_modules
    channel = {'id': 'UC_channel_test', 'connection_id': 'current-connection'}
    html = youtube._profile_form(channel, None)
    assert '<summary>Otomatik üretim</summary>' in html
    assert 'name="production_enabled" type="checkbox" value="1">' in html
    assert 'name="production_interval_hours" type="number" min="6" max="168" value="24"' in html
    assert 'Otomatik üretim kapalı' in html
    html = youtube._profile_form(channel, {
        'production_enabled': True, 'auto_publish': True,
        'production_topics': ['<script>bad</script>', 'Kabin ışıkları'],
    })
    assert 'name="production_enabled" type="checkbox" value="1" checked' in html
    assert '&lt;script&gt;bad&lt;/script&gt;\nKabin ışıkları' in html
    assert '<script>bad</script>' not in html


def test_production_upload_requires_explicit_opt_in_and_captures_exact_channel(ui_modules, monkeypatch):
    studio, _ = ui_modules
    monkeypatch.setattr(studio, 'connection_status', lambda: {'connections': [{
        'id': 'UC_channel_chosen', 'connection_id': 'oauth-generation-one',
    }]})
    monkeypatch.setattr(studio, 'list_channel_profiles', lambda: [{
        'channel_id': 'UC_channel_chosen', 'profile_revision': 'profile-one',
        'auto_publish': True, 'languages': ['tr', 'en'], 'default_language': 'tr',
    }])
    for mode, flag in [('preview', True), ('production', False), ('production', 'true')]:
        assert studio._production_publish_options(mode, flag, 'UC_channel_chosen', 'en') == {
            'publish_after_render': False,
        }
    assert studio._production_publish_options('production', True, 'UC_channel_chosen', 'en') == {
        'publish_after_render': True,
        'production_channel_id': 'UC_channel_chosen',
        'production_connection_id': 'oauth-generation-one',
        'production_profile_revision': 'profile-one',
    }
    with pytest.raises(studio.HTTPException) as missing:
        studio._production_publish_options('production', True, '', 'en')
    assert missing.value.status_code == 422
    with pytest.raises(studio.HTTPException) as language:
        studio._production_publish_options('production', True, 'UC_channel_chosen', 'de')
    assert language.value.status_code == 422


def test_studio_normalization_preserves_shorts_and_never_auto_uploads_preview(ui_modules):
    studio, _ = ui_modules
    args = ('Konu', 0.5, 'tr', '', 'preview', 'auto', 'documentary',
            'balanced', 'real_first', 'off', 'sidecar', '')
    spec = studio._normalize_spec(*args, format='shorts', publish_after_render=True)
    assert spec['format'] == 'shorts'
    assert spec['publish_after_render'] is False
    production_args = (*args[:4], 'production', *args[5:])
    assert studio._normalize_spec(*production_args)['publish_after_render'] is False
    assert studio._normalize_spec(*production_args, publish_after_render=True)['publish_after_render'] is True


def test_production_status_reports_paused_exhausted_and_pending_without_fake_running(ui_modules):
    _, youtube = ui_modules
    profile = {'production_enabled': True, 'auto_publish': True, 'production_topics': ['Konu']}
    assert youtube._production_status_text(profile, {}) == 'Planlama bekliyor'
    assert youtube._production_status_text(profile, {'cursor': '1'}) == 'Konu listesi tamamlandı'
    assert youtube._production_status_text(profile, {'active_task_id': 'queued'}) == 'Üretim sıraya alındı'
    assert youtube._production_status_text(profile, {
        'dispatch_status': 'uncertain', 'active_task_id': 'unknown',
    }) == 'Duraklatıldı · kontrol gerekiyor'
    assert youtube._production_status_text(profile, {'paused_reason': 'previous_render_failed'}) == 'Duraklatıldı · kontrol gerekiyor'
    assert youtube._production_status_text(profile, {'unavailable': True}) == 'Üretim durumu alınamadı'


def test_job_row_omits_empty_target_metadata_and_legacy_panel_link(ui_modules):
    studio, _ = ui_modules
    job = {
        'task_id': 'no-target',
        'kind': 'render',
        'state': 'PROGRESS',
        'spec': {'topic': 'Hedefsiz üretim'},
    }

    row = studio._job_row(job)
    nav = studio._nav('history')

    assert 'Hedef / profil' not in row
    assert 'Seçilmedi' not in row
    assert 'Eski panel' not in nav
    assert 'href="/factory"' not in nav
    assert 'Anlatıcı sesleri' in nav


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
    assert '<summary><span>Ayarlar</span>' in body
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
    assert '<details class="system-details console-details">' in body
    assert '<details class="system-details console-details" open>' not in body
    assert 'YouTube yüklemeleri önce gizli oluşturulur.' in body
    assert '<article class="job' not in body
    assert 'Uçakta Işıklar Neden Kısılır?' not in body
    assert body.count('data-status-filter=') == 4
    assert 'data-status-filter="new"' in body
    assert '<span class="status-name">Yeni video</span>' in body
    assert 'data-status-filter="failed"' not in body
    assert 'data-status-filter="attention"' in body
    assert 'data-status-filter="library"' in body
    assert '<details class="archive-details">' in body
    assert 'data-status-count="running">0</span>' in body
    assert 'data-status-count="library">1</span>' in body
    assert '<h1>Yeni video oluştur</h1>' in body
    assert body.count('type="submit"') == 1
    assert '>Videoyu oluştur</button>' in body
    assert '16:9' not in body
    assert 'aspect-ratio:16/9' not in studio.BASE_CSS
    archive_summary = body.split('<details class="archive-details">', 1)[1].split(
        '</summary>', 1,
    )[0]
    assert 'başarısız' not in archive_summary.casefold()
    assert 'tamamlanan' not in archive_summary.casefold()


def test_studio_home_uses_status_cards_without_a_duplicate_job_queue(
    monkeypatch,
    ui_modules,
):
    studio, _ = ui_modules
    monkeypatch.setattr(studio.settings, 'factory_api_token', 'studio-secret')
    monkeypatch.setattr(studio, 'get_selected_voice', lambda: {'name': 'Doğal ses'})
    monkeypatch.setattr(studio, '_service_statuses', lambda: [('OpenAI', True)])

    def job(task_id, state, title, created_ts):
        payload = {
            'task_id': task_id,
            'kind': 'render',
            'state': state,
            'progress': 35 if state == 'PROGRESS' else 100,
            'created_ts': created_ts,
            'spec': {
                'topic': title,
                'mode': 'preview',
                'duration_minutes': 0.5,
            },
        }
        if state == 'SUCCESS':
            payload['result'] = {
                'title': title,
                'video_key': f'videos/{task_id}/final.mp4',
                'quality_disposition': 'automated_qc_pass',
                'manual_qa_required': False,
            }
        return payload

    jobs = [
        job('failed-new', 'FAILURE', 'Tekrarlanan başarısız A', 600),
        job('failed-mid', 'FAILURE', 'Tekrarlanan başarısız B', 590),
        job('failed-old', 'FAILURE', 'Tekrarlanan başarısız C', 580),
        job('running-a', 'PROGRESS', 'Çalışan belgesel A', 570),
        job('running-b', 'PROGRESS', 'Çalışan belgesel B', 560),
        job('ready-a', 'SUCCESS', 'Hazır belgesel', 550),
    ]
    by_id = {item['task_id']: item for item in jobs}
    synced = []
    monkeypatch.setattr(studio, 'list_jobs', lambda _limit: jobs)
    monkeypatch.setattr(
        studio,
        '_sync_job',
        lambda task_id: synced.append(task_id) or by_id[task_id],
    )

    body = studio.studio_home(
        studio_token='studio-secret',
    ).body.decode('utf-8')
    assert '<article class="job' not in body
    assert '<aside>' not in body
    assert 'Çalışan belgesel A' not in body
    assert 'Hazır belgesel' not in body
    assert 'Tekrarlanan başarısız' not in body
    assert 'ŞİMDİ' not in body
    assert synced == []
    assert 'data-status-count="running">2</span>' in body
    assert 'data-status-count="attention">0</span>' in body
    assert 'data-status-count="library">1</span>' in body
    assert 'data-status-filter="failed"' not in body
    archive_summary, archive_body = body.split(
        '<details class="archive-details">', 1,
    )[1].split('</summary>', 1)
    assert '3' not in archive_summary
    assert 'Başarısız denemeler <b>3</b>' in archive_body


def test_stale_running_job_moves_to_attention_without_mutating_or_retrying(
    monkeypatch,
    ui_modules,
):
    studio, _ = ui_modules
    stale = {
        'task_id': 'stale-running',
        'kind': 'render',
        'state': 'PROGRESS',
        'stage': 'ai_scene_generation',
        'progress': 6,
        'updated_at': '2020-01-01T00:00:00+00:00',
        'spec': {'topic': 'Takılan kısa video', 'mode': 'preview'},
    }
    fresh = {
        'task_id': 'fresh-running',
        'kind': 'render',
        'state': 'PROGRESS',
        'progress': 40,
        'created_ts': 1_600_000_000,
        'updated_at': '2099-01-01T00:00:00+00:00',
        'spec': {'topic': 'İlerleyen kısa video', 'mode': 'preview'},
    }
    snapshot = json.loads(json.dumps(stale))
    synced = []
    monkeypatch.setattr(studio, 'list_jobs', lambda _limit: [stale, fresh])
    monkeypatch.setattr(
        studio,
        '_sync_job',
        lambda task_id: synced.append(task_id) or stale,
    )

    body = studio.studio_history(
        status='attention',
        studio_token='studio-secret',
    ).body.decode('utf-8')

    assert studio._job_is_stale_running(stale, now=1_700_000_000)
    assert studio._console_counts([stale, fresh]) == {
        'running': 1,
        'attention': 1,
        'library': 0,
    }
    assert 'data-history-status="attention"' in body
    assert 'data-status="attention"' in body
    assert 'Takılan kısa video' in body
    assert 'İlerleyen kısa video' not in body
    assert 'Üretim durdu; durumunu kontrol et.' in body
    assert 'href="/studio/job/stale-running"' in body
    assert 'action="/studio/retry/' not in body
    assert synced == []
    assert stale == snapshot


def test_storyboard_approval_counts_and_renders_as_attention_not_library(
    monkeypatch,
    ui_modules,
):
    studio, _ = ui_modules
    approval = {
        'task_id': 'approval-needed',
        'kind': 'plan',
        'state': 'AWAITING_APPROVAL',
        'spec': {'topic': 'Onay bekleyen storyboard', 'mode': 'production'},
        'result': {'package': {'title': 'Onay bekleyen storyboard', 'scenes': []}},
    }
    monkeypatch.setattr(studio, 'list_jobs', lambda _limit: [approval])

    attention = studio.studio_history(
        status='attention',
        studio_token='studio-secret',
    ).body.decode('utf-8')
    library = studio.studio_history(
        status='library',
        studio_token='studio-secret',
    ).body.decode('utf-8')

    assert studio._console_bucket(approval) == 'attention'
    assert studio._console_counts([approval]) == {
        'running': 0,
        'attention': 1,
        'library': 0,
    }
    assert 'data-status-count="attention">1</span>' in attention
    assert 'data-status-count="library">0</span>' in attention
    assert 'data-history-status="attention"' in attention
    assert '<article class="job" data-status="attention"' in attention
    assert '<span class="state attention">Dikkat gerekiyor</span>' in attention
    assert 'Storyboard hazır; devam etmek için aç.' in attention
    assert 'href="/studio/plan/approval-needed"' in attention
    assert 'Onay bekleyen storyboard' not in library
    assert '<h2>0 video</h2>' in library


def test_old_storyboard_leaves_daily_attention_but_stays_reachable(
    monkeypatch,
    ui_modules,
):
    studio, _ = ui_modules
    old = {
        'task_id': 'old-storyboard',
        'kind': 'plan',
        'state': 'AWAITING_APPROVAL',
        'updated_at': '2020-01-01T00:00:00+00:00',
        'spec': {'topic': 'Eski storyboard', 'mode': 'production'},
        'result': {'package': {'title': 'Eski storyboard', 'scenes': []}},
    }
    current = {
        'task_id': 'current-storyboard',
        'kind': 'plan',
        'state': 'AWAITING_APPROVAL',
        'updated_at': '2099-01-01T00:00:00+00:00',
        'spec': {'topic': 'Güncel storyboard', 'mode': 'production'},
        'result': {'package': {'title': 'Güncel storyboard', 'scenes': []}},
    }
    monkeypatch.setattr(studio, 'list_jobs', lambda _limit: [current, old])
    monkeypatch.setattr(studio, 'get_job', lambda _task_id: old)

    attention = studio.studio_history(
        status='attention',
        studio_token='studio-secret',
    ).body.decode('utf-8')
    drafts = studio.studio_history(
        status='drafts',
        studio_token='studio-secret',
    ).body.decode('utf-8')
    detail = studio.studio_job(
        old['task_id'],
        studio_token='studio-secret',
    ).body.decode('utf-8')

    assert studio._job_is_old_storyboard(old, now=1_700_000_000)
    assert studio._console_bucket(old) == 'archive'
    assert studio._console_counts([current, old]) == {
        'running': 0,
        'attention': 1,
        'library': 0,
    }
    assert 'Güncel storyboard' in attention
    assert '<div class="job-title">Eski storyboard</div>' not in attention
    assert 'Eski storyboard taslakları <b>1</b>' in attention
    assert 'data-history-status="drafts"' in drafts
    assert '<details class="archive-details" open>' in drafts
    assert 'Eski storyboard' in drafts
    assert "Storyboard'u aç" in drafts
    assert 'href="/studio/history?status=drafts"' in detail


def test_same_title_attention_attempts_group_without_hiding_other_actions(
    monkeypatch,
    ui_modules,
):
    studio, _ = ui_modules
    spec = {
        'topic': 'Aynı konu',
        'duration_minutes': 0.5,
        'language': 'tr',
        'channel_id': 'channel-a',
        'mode': 'preview',
        'content_style': 'documentary',
        'pace': 'balanced',
        'visual_mix': 'balanced',
    }

    def manual(task_id, created_ts, **spec_overrides):
        return {
            'task_id': task_id,
            'kind': 'render',
            'state': 'SUCCESS',
            'created_ts': created_ts,
            'spec': {**spec, **spec_overrides},
            'result': {
                'title': 'Aynı görünen başlık',
                'video_key': f'videos/{task_id}/final.mp4',
                'quality_disposition': 'manual_qa_preview',
                'manual_qa_required': True,
            },
        }

    newest = manual('manual-new', 2_000_000_100)
    duplicate = manual(
        'manual-old',
        2_000_000_000,
        duration_minutes=5,
        language='tr_TR',
        mode='production',
        content_style='cinematic',
        pace='dynamic',
        visual_mix='ai_first',
    )
    duplicate['result']['title'] = '  Aynı   görünen başlık  '
    duplicate['parent_id'] = 'source-not-in-history'
    repair = {
        'task_id': 'repair-action',
        'kind': 'render',
        'state': 'FAILURE',
        'repair_available': True,
        'created_ts': 2_000_000_050,
        'spec': dict(spec),
        'result': {'title': 'Aynı görünen başlık'},
    }
    storyboard = {
        'task_id': 'storyboard-action',
        'kind': 'plan',
        'state': 'AWAITING_APPROVAL',
        'created_ts': 2_000_000_040,
        'spec': dict(spec),
        'result': {'package': {'title': 'Aynı görünen başlık', 'scenes': []}},
    }
    stalled = {
        'task_id': 'stalled-action',
        'kind': 'render',
        'state': 'PROGRESS',
        'created_ts': 2_000_000_030,
        'updated_at': '2020-01-01T00:00:00+00:00',
        'spec': dict(spec),
        'result': {'title': 'Aynı görünen başlık'},
    }
    other_channel = manual(
        'other-channel',
        2_000_000_020,
        channel_id='channel-b',
    )
    other_language = manual(
        'other-language',
        2_000_000_010,
        language='en',
    )
    jobs = [
        newest, repair, storyboard, stalled, other_channel, other_language,
        duplicate,
    ]
    by_id = {job['task_id']: job for job in jobs}
    monkeypatch.setattr(studio, 'list_jobs', lambda _limit: jobs)
    monkeypatch.setattr(studio, '_sync_job', lambda task_id: by_id[task_id])

    body = studio.studio_history(
        status='attention',
        studio_token='studio-secret',
    ).body.decode('utf-8')

    assert studio._attention_action_category(newest) == 'review'
    assert studio._attention_action_category(repair) == 'repair'
    assert studio._attention_action_category(storyboard) == 'storyboard'
    assert studio._attention_action_category(stalled) == 'stalled'
    assert studio._attention_duplicate_signature(newest) == (
        studio._attention_duplicate_signature(duplicate)
    )
    assert studio._attention_duplicate_signature(newest) != (
        studio._attention_duplicate_signature(other_channel)
    )
    assert studio._attention_duplicate_signature(newest) != (
        studio._attention_duplicate_signature(other_language)
    )
    actual_channel_a = manual('actual-channel-a', 2_000_000_005)
    actual_channel_a['result']['target_channel_id'] = 'UC_actual_A'
    actual_channel_b = manual('actual-channel-b', 2_000_000_004)
    actual_channel_b['result']['target_channel_id'] = 'UC_actual_B'
    assert studio._attention_duplicate_signature(actual_channel_a) != (
        studio._attention_duplicate_signature(actual_channel_b)
    )
    assert body.count('<article class="job"') == 6
    assert body.count('Aynı görünen başlık') >= 6
    assert '2 benzer deneme tek kartta toplandı.' in body
    assert '>Kaliteyi incele</a>' in body
    assert '>Sorunlu sahneyi onar</button>' in body
    assert "Storyboard'u aç</a>" in body
    assert '>Durumu aç</a>' in body
    assert 'data-status-count="attention">6</span>' in body

    outside_window = manual('outside-window', 2_000_000_100 - 6 * 60 * 60 - 1)
    assert len(studio._collapse_attention_duplicates([newest, outside_window])) == 2


def test_repair_group_promotes_older_action_when_refreshed_newest_is_resolved(
    monkeypatch,
    ui_modules,
):
    studio, _ = ui_modules
    spec = {
        'topic': 'Aynı onarım konusu',
        'duration_minutes': 0.5,
        'language': 'tr',
        'channel_id': 'channel-a',
    }

    def repair(task_id, created_ts):
        return {
            'task_id': task_id,
            'kind': 'render',
            'state': 'FAILURE',
            'repair_available': True,
            'created_ts': created_ts,
            'spec': dict(spec),
            'result': {'title': 'Aynı onarım başlığı'},
        }

    newest = repair('repair-new', 2_000_000_100)
    older = repair('repair-old', 2_000_000_000)
    resolved_newest = {**newest, 'repair_available': False}
    synced = []
    monkeypatch.setattr(studio, 'list_jobs', lambda _limit: [newest, older])
    monkeypatch.setattr(
        studio,
        '_sync_job',
        lambda task_id: synced.append(task_id) or resolved_newest,
    )

    body = studio.studio_history(
        status='attention',
        studio_token='studio-secret',
    ).body.decode('utf-8')

    assert synced == ['repair-new']
    assert 'action="/studio/retry/repair-old"' in body
    assert 'action="/studio/retry/repair-new"' not in body
    assert 'data-status-count="attention">1</span>' in body
    assert 'Başarısız denemeler <b>1</b>' in body


def test_stalled_jobs_are_grouped_only_by_attention_identity(ui_modules):
    studio, _ = ui_modules
    spec = {
        'topic': 'Aynı teknik istek',
        'duration_minutes': 0.5,
        'language': 'tr',
        'channel_id': 'channel-a',
        'mode': 'preview',
    }
    first = {
        'task_id': 'stalled-first',
        'kind': 'render',
        'state': 'PROGRESS',
        'created_ts': 2_000_000_100,
        'updated_at': '2020-01-01T00:00:00+00:00',
        'spec': dict(spec),
        'result': {'title': 'Görünen başlık A'},
    }
    second = {
        **first,
        'task_id': 'stalled-second',
        'created_ts': 2_000_000_000,
        'spec': dict(spec),
        'result': {'title': 'Görünen başlık B'},
    }

    visible = studio._collapse_retry_sources([first, second])

    assert studio._running_duplicate_signature(first) is None
    assert studio._attention_action_category(first) == 'stalled'
    assert [job['task_id'] for job in visible] == [
        'stalled-first', 'stalled-second',
    ]
    assert studio._console_counts(visible)['attention'] == 2


def test_each_attention_action_groups_only_its_matching_attempts(ui_modules):
    studio, _ = ui_modules
    spec = {
        'topic': 'Ortak konu',
        'language': 'tr',
        'channel_id': 'channel-a',
    }

    def attempt(action, task_id, created_ts):
        job = {
            'task_id': task_id,
            'kind': 'render',
            'state': 'SUCCESS',
            'created_ts': created_ts,
            'spec': dict(spec),
            'result': {'title': f'{action} ortak başlığı'},
        }
        if action == 'review':
            job['result'].update({
                'video_key': f'videos/{task_id}/final.mp4',
                'quality_disposition': 'manual_qa_preview',
                'manual_qa_required': True,
            })
        elif action == 'repair':
            job.update({'state': 'FAILURE', 'repair_available': True})
        elif action == 'storyboard':
            job.update({'kind': 'plan', 'state': 'AWAITING_APPROVAL'})
            job['result'] = {
                'package': {'title': f'{action} ortak başlığı', 'scenes': []},
            }
        else:
            job.update({
                'state': 'PROGRESS',
                'updated_at': '2020-01-01T00:00:00+00:00',
            })
        return job

    for action in ('review', 'repair', 'storyboard', 'stalled'):
        newest = attempt(action, f'{action}-new', 2_000_000_100)
        older = attempt(action, f'{action}-old', 2_000_000_000)

        visible = studio._collapse_retry_sources([newest, older])

        assert studio._attention_action_category(newest) == action
        assert [job['task_id'] for job in visible] == [f'{action}-new']
        assert visible[0]['_grouped_attention_attempts'] == 1
        assert '_grouped_running_attempts' not in visible[0]
        assert '_grouped_attention_attempts' not in newest
        assert '_grouped_attention_attempts' not in older


def test_attention_group_window_is_anchored_to_the_newest_attempt(ui_modules):
    studio, _ = ui_modules

    def review(task_id, created_ts):
        return {
            'task_id': task_id,
            'kind': 'render',
            'state': 'SUCCESS',
            'created_ts': created_ts,
            'spec': {'language': 'tr', 'channel_id': 'channel-a'},
            'result': {
                'title': 'Zaman penceresi başlığı',
                'video_key': f'videos/{task_id}/final.mp4',
                'quality_disposition': 'manual_qa_preview',
                'manual_qa_required': True,
            },
        }

    newest = review('newest', 2_000_000_000)
    six_hours = review(
        'six-hours',
        newest['created_ts'] - studio.ATTENTION_DUPLICATE_GROUP_WINDOW_SECONDS,
    )
    five_hours = review('five-hours', newest['created_ts'] - 5 * 60 * 60)
    ten_hours = review('ten-hours', newest['created_ts'] - 10 * 60 * 60)
    missing_time = review('missing-time', None)
    future_first = review('future-first', newest['created_ts'] + 1)

    assert len(studio._collapse_attention_duplicates([newest, six_hours])) == 1
    chained = studio._collapse_attention_duplicates([
        newest, five_hours, ten_hours,
    ])
    assert [job['task_id'] for job in chained] == ['newest', 'ten-hours']
    assert chained[0]['_grouped_attention_attempts'] == 1
    assert len(studio._collapse_attention_duplicates([newest, missing_time])) == 2
    assert len(studio._collapse_attention_duplicates([newest, future_first])) == 2


def test_attention_identity_fails_closed_for_missing_and_long_titles(ui_modules):
    studio, _ = ui_modules
    base = {
        'kind': 'render',
        'state': 'SUCCESS',
        'created_ts': 2_000_000_100,
        'spec': {'language': 'tr', 'channel_id': 'channel-a'},
        'result': {
            'video_key': 'videos/example/final.mp4',
            'quality_disposition': 'manual_qa_preview',
            'manual_qa_required': True,
        },
    }
    missing_a = {**base, 'task_id': 'missing-a'}
    missing_b = {**base, 'task_id': 'missing-b', 'created_ts': 2_000_000_000}
    long_a = {
        **base,
        'task_id': 'long-a',
        'result': {**base['result'], 'title': 'A' * 120 + ' bir'},
    }
    long_b = {
        **base,
        'task_id': 'long-b',
        'created_ts': 2_000_000_000,
        'result': {**base['result'], 'title': 'A' * 120 + ' iki'},
    }
    unknown_language = {
        **base,
        'task_id': 'unknown-language',
        'spec': {'channel_id': 'channel-a', 'topic': 'Başlığı var'},
        'result': {**base['result'], 'title': 'Başlığı var'},
    }

    assert studio._attention_duplicate_signature(missing_a) is None
    assert studio._attention_duplicate_signature(missing_b) is None
    assert studio._attention_duplicate_signature(unknown_language) is None
    assert len(studio._collapse_attention_duplicates([missing_a, missing_b])) == 2
    assert studio._attention_duplicate_signature(long_a) != (
        studio._attention_duplicate_signature(long_b)
    )


def test_identical_concurrent_running_jobs_group_only_in_the_display(ui_modules):
    studio, _ = ui_modules
    spec = {
        'topic': 'Tokio Express nasıl çalışır?',
        'duration_minutes': 0.5,
        'language': 'tr',
        'mode': 'preview',
        'workflow': 'auto',
    }
    newest = {
        'task_id': 'tokio-new',
        'kind': 'render',
        'state': 'PROGRESS',
        'stage': 'plan_retry',
        'progress': 6,
        'created_ts': 2_000_000_100,
        'spec': dict(spec),
    }
    duplicate = {
        **newest,
        'task_id': 'tokio-old',
        'created_ts': 2_000_000_000,
        'spec': dict(spec),
    }
    distinct = {
        **newest,
        'task_id': 'other-video',
        'created_ts': 2_000_000_050,
        'spec': {**spec, 'topic': 'Başka bir video'},
    }

    visible = studio._collapse_retry_sources([newest, duplicate, distinct])

    assert [job['task_id'] for job in visible] == ['tokio-new', 'other-video']
    assert visible[0]['_grouped_running_attempts'] == 1
    assert '_grouped_running_attempts' not in newest
    assert '_grouped_running_attempts' not in duplicate
    assert studio._console_counts(visible)['running'] == 2
    assert '2 eş üretim tek kartta gösteriliyor.' in studio._job_row(visible[0])

    much_later = {
        **duplicate,
        'task_id': 'tokio-later',
        'created_ts': (
            newest['created_ts']
            + studio.RUNNING_DUPLICATE_GROUP_WINDOW_SECONDS
            + 1
        ),
    }
    assert len(studio._collapse_retry_sources([newest, much_later])) == 2


def test_dormant_plan_retries_are_hidden_without_touching_other_jobs(ui_modules):
    studio, _ = ui_modules
    old_retry_a = {
        'task_id': 'old-plan-retry-a',
        'kind': 'render',
        'state': 'RETRY',
        'stage': 'plan_retry',
        'progress': 6,
        'updated_at': '2020-01-01T00:00:00+00:00',
        'spec': {'topic': 'Eski plan A', 'language': 'tr'},
    }
    old_retry_b = {
        **old_retry_a,
        'task_id': 'old-plan-retry-b',
        'spec': {'topic': 'Eski plan B', 'language': 'en'},
    }
    fresh_retry = {
        **old_retry_a,
        'task_id': 'fresh-plan-retry',
        'updated_at': '2099-01-01T00:00:00+00:00',
        'spec': {'topic': 'Taze plan'},
    }
    stale_normal = {
        **old_retry_a,
        'task_id': 'stale-normal-stage',
        'stage': 'ai_scene_generation',
        'spec': {'topic': 'Eski ama normal üretim'},
    }
    jobs = [old_retry_a, old_retry_b, fresh_retry, stale_normal]
    snapshot = json.loads(json.dumps(jobs))

    visible = studio._collapse_retry_sources(jobs)

    assert [job['task_id'] for job in visible] == [
        'fresh-plan-retry',
        'stale-normal-stage',
    ]
    assert studio._console_bucket(fresh_retry) == 'running'
    assert studio._console_bucket(stale_normal) == 'attention'
    assert jobs == snapshot


def test_dormant_plan_retry_grace_boundary_is_exact(ui_modules):
    studio, _ = ui_modules
    now = 1_800_000_000
    base = {
        'task_id': 'boundary-retry',
        'kind': 'render',
        'state': 'RETRY',
        'stage': 'plan_retry',
        'updated_ts': now - studio.PLAN_RETRY_DISPLAY_GRACE_SECONDS + 1,
    }

    assert not studio._job_is_dormant_plan_retry(base, now=now)
    assert studio._job_is_dormant_plan_retry(
        {
            **base,
            'updated_ts': now - studio.PLAN_RETRY_DISPLAY_GRACE_SECONDS,
        },
        now=now,
    )
    assert not studio._job_is_dormant_plan_retry(
        {**base, 'state': 'FAILURE'},
        now=now,
    )
    assert not studio._job_is_dormant_plan_retry(
        {**base, 'state': 'REVOKED'},
        now=now,
    )
    assert not studio._job_is_dormant_plan_retry(
        {**base, 'stage': 'visual_qc'},
        now=now,
    )


def test_sync_job_does_not_refresh_activity_for_unchanged_task_info(
    monkeypatch,
    ui_modules,
):
    studio, _ = ui_modules
    record = {
        'task_id': 'stuck-task',
        'kind': 'render',
        'state': 'RETRY',
        'stage': 'plan_retry',
        'progress': 6,
        'message': 'Plan yeniden deneniyor.',
        'updated_at': '2026-09-01T00:00:00+00:00',
        'spec': {'topic': 'Takılan iş'},
    }

    class UnchangedResult:
        state = 'RETRY'
        info = {
            'stage': 'plan_retry',
            'progress': 6,
            'message': 'Plan yeniden deneniyor.',
        }

    updates = []
    monkeypatch.setattr(studio, 'get_job', lambda _task_id: record)
    monkeypatch.setattr(studio, 'AsyncResult', lambda *_a, **_k: UnchangedResult())
    monkeypatch.setattr(
        studio,
        'update_job',
        lambda task_id, **fields: updates.append((task_id, fields)) or {
            **record,
            **fields,
        },
    )

    result = studio._sync_job('stuck-task')

    assert result == record
    assert result['updated_at'] == '2026-09-01T00:00:00+00:00'
    assert updates == []

    class ChangedResult:
        state = 'RETRY'
        info = {**UnchangedResult.info, 'progress': 7}

    monkeypatch.setattr(studio, 'AsyncResult', lambda *_a, **_k: ChangedResult())
    changed = studio._sync_job('stuck-task')

    assert changed['progress'] == 7
    # Worker set_stage owns durable progress; stale Celery snapshots are only
    # presented and must not overwrite a success/publication arriving meanwhile.
    assert changed['updated_at'] == record['updated_at']
    assert updates == []


def test_dashboard_failure_only_state_stays_out_of_action_queue(
    monkeypatch,
    ui_modules,
):
    studio, _ = ui_modules
    monkeypatch.setattr(studio.settings, 'factory_api_token', 'studio-secret')
    monkeypatch.setattr(studio, 'get_selected_voice', lambda: {'name': 'Doğal ses'})
    monkeypatch.setattr(studio, '_service_statuses', lambda: [('OpenAI', True)])
    monkeypatch.setattr(
        studio,
        'list_jobs',
        lambda _limit: [
            {
                'task_id': 'failed-only',
                'kind': 'render',
                'state': 'FAILURE',
                'spec': {'topic': 'Eski başarısız video'},
            },
            {
                'task_id': 'completed-only',
                'kind': 'publish',
                'state': 'SUCCESS',
                'spec': {'topic': 'Yayınlanmış video'},
                'result': {'youtube_url': 'https://youtu.be/completed'},
            },
        ],
    )

    body = studio.studio_home(
        studio_token='studio-secret',
    ).body.decode('utf-8')
    assert '<article class="job' not in body
    assert 'Eski başarısız video' not in body
    assert 'Yayınlanmış video' not in body
    assert '<details class="archive-details">' in body
    assert '<details class="archive-details" open>' not in body
    assert 'data-status-count="running">0</span>' in body
    assert 'data-status-count="attention">0</span>' in body
    assert 'data-status-count="library">1</span>' in body
    archive_summary, archive_body = body.split(
        '<details class="archive-details">', 1,
    )[1].split('</summary>', 1)
    assert '1' not in archive_summary
    assert 'Başarısız denemeler <b>1</b>' in archive_body
    assert 'tamamlanan' not in archive_body.casefold()


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
        ({'kind': 'publish', 'state': 'SUCCESS'}, 'completed'),
        ({'state': 'SUCCESS', 'result': {'youtube_url': 'https://youtu.be/video'}}, 'completed'),
        ({'state': 'AWAITING_APPROVAL'}, 'ready'),
        ({'state': 'FAILURE', 'repair_available': True}, 'repair'),
        ({'state': 'FAILURE', 'repair_available': False}, 'failed'),
        ({'state': 'FAILURE', 'retry_claimed': True}, 'running'),
        ({'state': 'FAILURE', 'retry_child_task_id': 'child-task'}, 'running'),
    ],
)
def test_studio_exposes_five_clear_user_statuses(job, expected, ui_modules):
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
        ({
            **base,
            'state': 'SUCCESS',
            'result': {
                'video_key': 'videos/final.mp4',
                'quality_disposition': 'automated_qc_pass',
                'manual_qa_required': False,
            },
        }, 'Gizli yükle'),
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
        'result': {
            'video_key': 'videos/retry-child-task/final.mp4',
            'quality_disposition': 'automated_qc_pass',
            'manual_qa_required': False,
        },
    }

    visible = studio._collapse_retry_sources([source, child])

    assert visible == [child]
    assert studio._status_counts(visible) == {
        'running': 0,
        'ready': 1,
        'repair': 0,
        'completed': 0,
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

    published_render = {
        **render,
        'result': {
            'download_url': 'https://media.example.test/final.mp4',
            'youtube': {
                'url': 'https://www.youtube.com/watch?v=AbCdEfGhI_1',
                'video_id': 'AbCdEfGhI_1',
                'target_channel_id': 'UCchannel_test',
                'privacy_status': 'private',
            },
        },
    }
    successful_publish = {
        **publish,
        'state': 'SUCCESS',
        'result': {'youtube_url': 'https://youtube.example.test/watch?v=private'},
    }
    assert studio._collapse_retry_sources(
        [successful_publish, published_render, plan]
    ) == [published_render]


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
    assert first.count('<article class="ready-card"') == 12
    assert 'Başarısız video' not in first
    assert 'data-status-count="library">13</span>' in first
    assert 'Başarısız denemeler <b>1</b>' in first
    assert 'Sayfa 1 / 2' in first
    assert 'status=ready&amp;page=2' in first
    assert second.count('<article class="ready-card"') == 1
    assert 'Sayfa 2 / 2' in second


def test_library_keeps_ready_and_private_videos_rich_but_failures_separate(
    monkeypatch,
    ui_modules,
):
    studio, _ = ui_modules
    ready = _ready_job()
    ready['result'].update({
        'download_url': 'https://media.example.test/portrait.mp4',
        'thumbnail_url': 'https://media.example.test/portrait.jpg',
    })
    uploaded = {
        'task_id': 'uploaded-video',
        'kind': 'render',
        'state': 'SUCCESS',
        'spec': {
            'topic': 'Gizli video',
            'duration_minutes': 1,
            'channel_id': 'fallback-channel',
        },
        'result': {
            'title': 'YouTube’a Gizli Yüklenen Video',
            'duration': 58,
            'download_url': 'https://media.example.test/uploaded.mp4',
            'youtube': {
                'url': 'https://www.youtube.com/watch?v=AbCdEfGhI_1',
                'video_id': 'AbCdEfGhI_1',
                'target_channel_id': 'UCchannel_test',
                'privacy_status': 'private',
                'channel_title': 'Merak Kanalı',
            },
        },
    }
    failed = {
        'task_id': 'failed-video',
        'kind': 'render',
        'state': 'FAILURE',
        'spec': {'topic': 'Başarısız video'},
    }
    monkeypatch.setattr(studio, 'list_jobs', lambda _limit: [ready, uploaded, failed])

    body = studio.studio_history(
        status='library',
        studio_token='studio-secret',
    ).body.decode('utf-8')

    assert 'data-history-status="library"' in body
    assert 'data-status-count="library">2</span>' in body
    assert '<span class="status-name">Videolar</span>' in body
    assert '<h1>Videolar</h1>' in body
    assert 'Üretilen ve YouTube’a yüklenen videolar ayrı durumlarla gösterilir.' in body
    assert 'Hazır / gizli' not in body
    assert body.count('<article class="ready-card"') == 2
    assert body.count('<video class="ready-video"') == 2
    assert 'poster="https://media.example.test/portrait.jpg"' in body
    assert 'Uçakta Işıklar Neden Kısılır?' in body
    assert 'YouTube’a Gizli Yüklenen Video' in body
    assert '<b>Süre</b><span>30 sn</span>' in body
    assert '<b>Kanal</b><span>Merak Kanalı</span>' in body
    assert '<b>Yayın</b><span data-delivery-label="job-123">Yüklemeye hazır</span>' in body
    assert '<b>Yayın</b><span data-delivery-label="uploaded-video">YouTube’a gizli yüklendi</span>' in body
    assert '<b>Gizlilik</b>' not in body
    assert body.count('data-action-count="2"') == 2
    assert '>Gizli yükle</a>' in body
    assert ">YouTube'da aç</a>" in body
    assert 'Başarısız video' not in body
    assert 'action="/studio/retry/' not in body
    assert 'action="/studio/youtube/public' not in body.casefold()


def test_library_is_quality_qualified_and_routes_manual_and_legacy_outputs(
    monkeypatch,
    ui_modules,
):
    studio, _ = ui_modules
    approved = _ready_job()
    approved['task_id'] = 'approved-video'
    approved['result'] = {
        **approved['result'],
        'title': 'Kalitesi Onaylı Video',
        'download_url': 'https://media.example.test/approved.mp4',
    }
    manual = _ready_job()
    manual['task_id'] = 'manual-video'
    manual['result'] = {
        **manual['result'],
        'title': 'İnsan İncelemesi Gereken Video',
        'download_url': 'https://media.example.test/manual.mp4',
        'quality_disposition': 'manual_qa_preview',
        'manual_qa_required': True,
    }
    unreviewed = _ready_job()
    unreviewed['task_id'] = 'legacy-video'
    unreviewed['result'] = {
        'title': 'Eski Kalite Kaydı Olmayan Video',
        'video_key': 'videos/legacy-video/final.mp4',
        'video_url': 'https://media.example.test/legacy.mp4',
    }
    uploaded = _ready_job()
    uploaded['task_id'] = 'already-uploaded'
    uploaded['result'] = {
        'title': 'Önceden YouTube’a Yüklenmiş Video',
        'video_key': 'videos/already-uploaded/final.mp4',
        'youtube': {
            'url': 'https://youtube.example.test/watch?v=already',
            'video_id': 'already',
            'privacy_status': 'private',
        },
    }
    jobs = [approved, manual, unreviewed, uploaded]
    monkeypatch.setattr(studio, 'list_jobs', lambda _limit: jobs)

    library = studio.studio_history(
        status='library',
        studio_token='studio-secret',
    ).body.decode('utf-8')
    attention = studio.studio_history(
        status='attention',
        studio_token='studio-secret',
    ).body.decode('utf-8')
    archive = studio.studio_history(
        status='unreviewed',
        studio_token='studio-secret',
    ).body.decode('utf-8')

    assert studio._console_counts(jobs) == {
        'running': 0,
        'attention': 1,
        'library': 2,
    }
    assert studio._job_upload_allowed(approved)
    assert not studio._job_upload_allowed(manual)
    assert not studio._job_upload_allowed(unreviewed)
    assert not studio._job_upload_allowed(uploaded)
    assert 'Kalitesi Onaylı Video' in library
    assert 'Önceden YouTube’a Yüklenmiş Video' in library
    assert 'İnsan İncelemesi Gereken Video' not in library
    assert 'Eski Kalite Kaydı Olmayan Video' not in library
    assert library.count('<article class="ready-card"') == 2
    assert 'data-status-count="library">2</span>' in library
    assert 'Eski kalite kayıtları <b>1</b>' in library

    assert 'İnsan İncelemesi Gereken Video' in attention
    assert 'Videoyu kontrol et; onaylanmadan YouTube’a yüklenmez.' in attention
    assert '>Kaliteyi incele</a>' in attention
    assert '>Gizli yükle</a>' not in attention
    assert 'Eski Kalite Kaydı Olmayan Video' not in attention

    assert 'data-history-status="unreviewed"' in archive
    assert '<details class="archive-details" open>' in archive
    assert 'Eski Kalite Kaydı Olmayan Video' in archive
    assert 'Bu eski videoda açık kalite onayı yok' in archive
    assert '>Videoyu incele</a>' in archive
    assert '>Gizli yükle</a>' not in archive

    contradictory = _ready_job()
    contradictory['result']['manual_qa_required'] = True
    assert studio._job_display_status(contradictory) == 'attention'
    assert not studio._job_upload_allowed(contradictory)


def test_manual_quality_detail_and_polling_fail_closed_consistently(
    monkeypatch,
    ui_modules,
):
    studio, _ = ui_modules
    manual = _ready_job()
    manual['task_id'] = 'manual-review'
    manual['result'] = {
        **manual['result'],
        'download_url': None,
        'video_url': 'https://media.example.test/manual-review.mp4',
        'quality_disposition': 'manual_qa_preview',
        'manual_qa_required': True,
    }
    monkeypatch.setattr(studio, 'get_job', lambda _task_id: manual)
    monkeypatch.setattr(studio, '_sync_job', lambda _task_id: manual)

    detail = studio.studio_job(
        'manual-review',
        studio_token='studio-secret',
    ).body.decode('utf-8')
    payload = json.loads(
        studio.studio_job_api(
            'manual-review',
            studio_token='studio-secret',
        ).body.decode('utf-8')
    )

    assert '<article class="card job-panel" id="job-card" data-status="attention">' in detail
    assert '<span class="state attention" id="state-label">Dikkat gerekiyor</span>' in detail
    assert 'Videoyu kontrol et; onaylanmadan YouTube’a yüklenmez.' in detail
    assert '>Videoyu incele</a>' in detail
    assert '>Gizli yükle</a>' not in detail
    assert 'status=attention' in detail
    assert payload['ui_status'] == 'ready'
    assert payload['display_status'] == 'attention'
    assert payload['display_status_label'] == 'Dikkat gerekiyor'
    assert payload['upload_allowed'] is False
    assert payload['ui_status_message'] == (
        'Videoyu kontrol et; onaylanmadan YouTube’a yüklenmez.'
    )
    assert "j.upload_allowed===true" in detail
    assert "displayUi=String(j.display_status||ui)" in detail


def test_approved_polling_payload_is_the_only_render_upload_allowed(
    monkeypatch,
    ui_modules,
):
    studio, _ = ui_modules
    approved = _ready_job()
    monkeypatch.setattr(studio, '_sync_job', lambda _task_id: approved)

    payload = json.loads(
        studio.studio_job_api(
            approved['task_id'],
            studio_token='studio-secret',
        ).body.decode('utf-8')
    )

    assert payload['display_status'] == 'ready'
    assert payload['upload_allowed'] is True


@pytest.mark.parametrize(
    (
        'release_status', 'privacy_status', 'pill_label', 'readiness_label',
    ),
    [
        ('private', 'private', 'private', 'YouTube’a gizli yüklendi'),
        ('public', 'public', 'public', 'YouTube’da yayında'),
        ('scheduled', 'private', 'private', 'YouTube yayını planlandı'),
    ],
)
def test_ready_card_presents_youtube_release_state_without_calling_it_all_private(
    release_status,
    privacy_status,
    pill_label,
    readiness_label,
    ui_modules,
):
    studio, _ = ui_modules
    job = _ready_job()
    job['result']['youtube'] = {
        'url': 'https://www.youtube.com/watch?v=AbCdEfGhI_1',
        'video_id': 'AbCdEfGhI_1',
        'target_channel_id': 'UCchannel_test',
        'release_status': release_status,
        'privacy_status': privacy_status,
    }

    card = studio._ready_video_card(job)

    assert studio._console_bucket(job) == 'library'
    assert f'<span class="state {pill_label}">{readiness_label}</span>' in card
    assert f'<b>Yayın</b><span data-delivery-label="job-123">{readiness_label}</span>' in card


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
    assert 'Şu anda hazırlanan videolar.' in body
    assert 'Bu görünüm:' not in body
    assert 'Toplam 2 görünür video' not in body
    assert 'data-status-count="running">1</span>' in body
    assert 'data-status-count="attention">0</span>' in body
    assert 'Başarısız denemeler <b>1</b>' in body
    assert 'Aynı hızlı test' not in body
    assert failed_body.count('<article class="job"') == 1
    assert '<details class="archive-details" open>' in failed_body
    assert 'Başarısız denemeler <b>1</b>' in failed_body
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
    assert 'Başarısız denemeler <b>500</b>' in body


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


def test_ready_job_view_renders_inline_video_download_and_captions(
    monkeypatch,
    ui_modules,
):
    studio, _ = ui_modules
    record = _ready_job()
    record['result'].update({
        'download_url': (
            'https://media.example.test/final.mp4?X-Amz-Signature=signed'
            '&response-content-disposition=attachment'
        ),
        'caption_url': (
            'https://media.example.test/captions.tr.srt?X-Amz-Signature=caption'
            '&X-Amz-Expires=86400'
        ),
    })
    monkeypatch.setattr(studio, 'get_job', lambda _task_id: record)

    body = studio.studio_job(
        'job-123',
        studio_token='studio-secret',
    ).body.decode('utf-8')

    assert '<video class="result-video" controls playsinline preload="metadata"' in body
    assert (
        'src="https://media.example.test/final.mp4?X-Amz-Signature=signed'
        '&amp;response-content-disposition=attachment"'
    ) in body
    assert '>Videoyu indir</a>' in body
    assert '>Altyazıyı indir (.srt)</a>' in body
    assert 'caption&amp;X-Amz-Expires=86400"' in body
    assert 'target="_blank" rel="noopener noreferrer" download' in body
    assert '>Gizli yükle</a>' in body
    assert 'Kalite onaylanana kadar YouTube yüklemesi gizli kalır.' in body


def test_job_media_panel_accepts_video_url_and_escapes_signed_attributes(ui_modules):
    studio, _ = ui_modules
    record = _ready_job()
    record['result'].update({
        'video_url': (
            'https://media.example.test/final.mp4?value="'
            '><img src=x onerror=alert(1)>'
        ),
        'caption_url': 'javascript:alert(2)',
    })

    panel = studio._job_media_panel(record)

    assert '<video class="result-video" controls playsinline preload="metadata"' in panel
    assert '&quot;&gt;&lt;img src=x onerror=alert(1)&gt;' in panel
    assert '<img src=x' not in panel
    assert 'javascript:' not in panel
    assert 'Altyazıyı indir' not in panel


def test_video_players_keep_intrinsic_portrait_ratio(ui_modules):
    studio, _ = ui_modules
    record = _ready_job()
    record['result']['download_url'] = 'https://media.example.test/portrait.mp4'

    panel = studio._job_media_panel(record)
    card = studio._ready_video_card(record)

    assert '<div class="result-video-frame"><video class="result-video"' in panel
    assert '<video class="ready-video"' in card
    assert 'aspect-ratio:16/9' not in studio.BASE_CSS
    assert 'aspect-ratio:auto' in studio.BASE_CSS
    assert 'width:auto;max-width:100%;height:auto' in studio.BASE_CSS


def test_incomplete_job_never_renders_stale_result_media(ui_modules):
    studio, _ = ui_modules
    record = _ready_job()
    record['state'] = 'PROGRESS'
    record['result']['download_url'] = 'https://media.example.test/stale.mp4'

    assert studio._job_media_panel(record) == ''


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


def test_existing_video_and_storyboard_pages_keep_videos_navigation_active(
    monkeypatch,
    ui_modules,
):
    studio, _ = ui_modules
    ready = _ready_job()
    monkeypatch.setattr(studio, 'get_job', lambda _task_id: ready)

    job_body = studio.studio_job(
        ready['task_id'],
        studio_token='studio-secret',
    ).body.decode('utf-8')

    approval = {
        'task_id': 'approval-plan',
        'kind': 'plan',
        'state': 'AWAITING_APPROVAL',
        'spec': {'topic': 'Onaylanacak plan'},
        'result': {
            'package': {'title': 'Onaylanacak plan', 'scenes': []},
        },
    }
    monkeypatch.setattr(studio, '_sync_job', lambda _task_id: approval)
    plan_body = studio.studio_plan(
        approval['task_id'],
        studio_token='studio-secret',
    ).body.decode('utf-8')

    active_videos = (
        '<a class="active" href="/studio/history?status=library" '
        'aria-current=page>Videolar</a>'
    )
    for body in (job_body, plan_body):
        assert active_videos in body
        assert '<a class="active" href="/studio"' not in body


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
    assert 'id="metrics-feedback" role="status"' in body
    assert 'YouTube bağlantısı tamamlandı' not in body  # no fabricated OAuth callback
    assert 'aria-label="Hedef YouTube kanalı"' in body
    assert 'name="youtube_channel_id"' in body
    assert '>Gizli yükle</button>' in body
    assert 'privacy_status' not in body
    assert 'action="/studio/youtube/public' not in body.casefold()
    assert 'İlk yükleme daima gizli' in body
    assert '🔒 İlk yükleme daima gizli' in body
    assert '-webkit-line-clamp:2' in youtube_routes.CSS
    assert '<a class="brand" href="/studio">YouTube Studio</a>' in body
    assert '<a href="/studio">Yeni video</a>' in body
    assert '<a href="/studio/history?status=library">Videolar</a>' in body
    assert '🎬 YouTube Studio V2' not in body
    assert '>Yeni üretim</a>' not in body
    assert '>Geçmiş</a>' not in body


def test_youtube_center_hides_nonapproved_outputs_but_keeps_uploaded_video(
    monkeypatch,
    ui_modules,
):
    _, youtube_routes = ui_modules
    approved = _ready_job()
    approved['task_id'] = 'approved'
    manual = _ready_job()
    manual['task_id'] = 'manual'
    manual['result'] = {
        **manual['result'],
        'quality_disposition': 'manual_qa_preview',
        'manual_qa_required': True,
    }
    unreviewed = _ready_job()
    unreviewed['task_id'] = 'unreviewed'
    unreviewed['result'] = {
        'video_key': 'videos/unreviewed/final.mp4',
    }
    uploaded = _ready_job()
    uploaded['task_id'] = 'uploaded'
    uploaded['result'] = {
        'video_key': 'videos/uploaded/final.mp4',
        'youtube': {
            'url': 'https://youtube.example.test/watch?v=uploaded',
            'video_id': 'uploaded',
        },
    }
    monkeypatch.setattr(
        youtube_routes,
        'list_jobs',
        lambda _limit: [approved, manual, unreviewed, uploaded],
    )

    visible = youtube_routes._completed_jobs()

    assert [job['task_id'] for job in visible] == ['approved', 'uploaded']


@pytest.mark.parametrize(
    'result',
    [
        {
            'video_key': 'videos/manual/final.mp4',
            'quality_disposition': 'manual_qa_preview',
            'manual_qa_required': True,
        },
        {'video_key': 'videos/unreviewed/final.mp4'},
    ],
)
def test_direct_youtube_post_rejects_nonapproved_source_before_reservation(
    result,
    monkeypatch,
    ui_modules,
):
    _, youtube_routes = ui_modules
    request = types.SimpleNamespace(
        headers={'origin': 'https://studio.example.test'},
        base_url='https://studio.example.test/',
    )
    monkeypatch.setattr(
        youtube_routes,
        'connection_status',
        lambda **_kwargs: {
            'channel': {
                'id': 'UC_quality_target',
                'connection_id': 'connection-quality-target',
            },
        },
    )
    monkeypatch.setattr(
        youtube_routes,
        'get_job',
        lambda _task_id: {
            'task_id': 'nonapproved-source',
            'kind': 'render',
            'state': 'SUCCESS',
            'result': result,
        },
    )
    reservations = []
    monkeypatch.setattr(
        youtube_routes,
        'reserve_upload',
        lambda *_args, **_kwargs: reservations.append((_args, _kwargs)),
    )

    with pytest.raises(youtube_routes.HTTPException) as rejection:
        youtube_routes.youtube_publish(
            'nonapproved-source',
            request,
            youtube_channel_id='UC_quality_target',
            studio_token='studio-secret',
        )

    assert rejection.value.status_code == 409
    assert 'Kalite onayı olmayan video' in rejection.value.detail
    assert reservations == []


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
