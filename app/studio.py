from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
import hashlib
import json
import math
import re
import secrets
import unicodedata
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

from celery.result import AsyncResult
from fastapi import APIRouter, Cookie, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.celery_app import celery
from app.config import settings
from app.tasks import (
    UnsupportedLanguageError,
    normalize_pipeline_language,
    plan_video_pipeline,
    run_video_pipeline,
)
from app.services.studio_state import (
    claim_retry_dispatch,
    create_job,
    get_job as _stored_get_job,
    list_jobs as _stored_list_jobs,
    mark_failure,
    mark_retry_dispatch,
    mark_success,
    sync_repair_checkpoint_state,
    update_job,
)
from app.services.voice import get_selected_voice
from app.services.youtube_auth import connection_status
from app.services.youtube_publish_state import get_upload_record
from app.services.youtube_automation import (
    list_channel_profiles,
    select_channel_profile,
)

router = APIRouter()
COOKIE_NAME = 'youtube_studio_token'


def _with_quality_hold_presentation(records):
    from app.services.studio_operations import held_task_ids
    held = held_task_ids(records)
    presented = []
    for record in records:
        row = {key: value for key, value in record.items() if key != 'quality_held'}
        if record.get('task_id') in held:
            row['quality_held'] = True
        presented.append(row)
    return presented


def get_job(task_id):
    record = _stored_get_job(task_id)
    return _with_quality_hold_presentation([record])[0] if isinstance(record, dict) else record


def list_jobs(limit=30):
    return _with_quality_hold_presentation(_stored_list_jobs(limit))


STYLE_LABELS = {
    'documentary': 'Belgesel',
    'technology': 'Teknoloji',
    'story': 'Hikâye',
    'cinematic': 'Sinematik',
    'explainer': 'Açıklayıcı',
}
PACE_LABELS = {'calm': 'Sakin', 'balanced': 'Dengeli', 'dynamic': 'Dinamik'}
MODE_LABELS = {
    'preview': 'Hızlı önizleme',
    'production': 'Yayın kalitesi',
    'publish': 'Gizli yükleme',
    'autonomous_publish': 'Otomatik gizli yükleme',
    'publish_recovery': 'Yükleme kurtarma',
}
STATE_LABELS = {
    'CANCELLED': 'İptal edildi',
    'PENDING': 'Kuyrukta',
    'PROGRESS': 'Devam ediyor',
    'SUCCESS': 'Hazır',
    'FAILURE': 'Başarısız',
    'AWAITING_APPROVAL': 'Storyboard onayı',
}
STAGE_LABELS = {
    'cancelled': 'Sahibi tarafından iptal edildi',
    'queued': 'Kuyrukta',
    'research': 'Araştırma',
    'director_qc': 'Senaryo yönetmeni',
    'approved_plan': 'Onaylı storyboard',
    'voice_and_visuals': 'Ses ve görsel toplama',
    'audio_qc': 'Ses ve telaffuz denetimi',
    'audio_qc_retry': 'Anlatıcı sesini iyileştirme',
    'audio_pause_recheck': 'Düzeltilen sesin son kontrolü',
    'visual_qc': 'Görsel kalite kontrolü',
    'final_visual_qc': 'Sahnelerin son kalite kontrolü',
    'final_visual_qc_rescue': 'Son kontroldeki sahneleri iyileştirme',
    'final_visual_qc_ai_repair': 'Son kontroldeki sahneyi onarma',
    'pre_runway_budget_rescue': 'Sahne kaynaklarını dengeleme',
    'audio_design': 'Müzik ve ses tasarımı',
    'ai_scene': 'Özgün AI sahneleri',
    'ai_scene_generation': 'Özgün sahne üretimi',
    'ai_scene_recovery': 'Kayıtlı sahneleri geri yükleme',
    'ai_scene_repair': 'Reddedilen sahneyi onarma',
    'render': 'Final kurgu',
    'upload': 'Depolamaya yükleme',
    'awaiting_approval': 'Storyboard onayı',
    'complete': 'Tamamlandı',
    'failed': 'Başarısız',
}
UI_STATUS_ORDER = ('running', 'ready', 'repair', 'completed', 'failed', 'cancelled')
CONSOLE_STATUS_ORDER = ('running', 'attention', 'library')
UI_STATUS_LABELS = {
    'cancelled': 'İptal edildi',
    'running': 'Devam ediyor',
    'attention': 'Dikkat gerekiyor',
    'ready': 'Hazır',
    'repair': 'Onarım gerekli',
    'completed': 'Tamamlandı',
    'failed': 'Başarısız',
    'unreviewed': 'Kalite onayı yok',
    'held': 'Deneme saklandı',
}
CONSOLE_STATUS_LABELS = {
    'running': 'Devam ediyor',
    'attention': 'Dikkat gerekiyor',
    'library': 'Videolar',
}
HISTORY_PAGE_SIZE = 12
HISTORY_SCAN_LIMIT = 500
LEGACY_RETRY_GROUP_WINDOW_SECONDS = 6 * 60 * 60
RUNNING_DUPLICATE_GROUP_WINDOW_SECONDS = 10 * 60
ATTENTION_DUPLICATE_GROUP_WINDOW_SECONDS = 6 * 60 * 60
STALE_RUNNING_SECONDS = 6 * 60 * 60
PLAN_RETRY_DISPLAY_GRACE_SECONDS = 15 * 60
OLD_STORYBOARD_SECONDS = 24 * 60 * 60
RECENT_FAILURE_SECONDS = 24 * 60 * 60
RETRY_PRESENTATION_MAX_HOPS = 16
PRODUCTION_RETRY_LABELS = {
    'retry_active': 'Yeniden üretim sürüyor · takvim sonucu bekliyor',
    'retry_queued': 'Yeniden üretim kuyrukta · takvim sonucu bekliyor',
    'retry_reserved': 'Yeniden deneme ayrıldı · gönderim bekleniyor',
    'retry_uncertain': 'Yeniden denemenin gönderimi doğrulanamadı',
}
OPTIONAL_VIDEO_GENERATION_SERVICES = frozenset({'Fal video'})

BASE_CSS = r'''
:root{color-scheme:dark;--bg:#090c11;--surface:#111721;--surface-2:#0d131c;--line:#273142;--line-strong:#39465c;--text:#f3f6fb;--muted:#9da9ba;--soft:#c8d0db;--accent:#7967f5;--accent-2:#5b9cf6;--good:#51d593;--warn:#f5cd68;--bad:#ff7d88;--radius:16px}
*{box-sizing:border-box}html{background:var(--bg);scroll-behavior:smooth}body{margin:0;min-height:100vh;color:var(--text);font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;line-height:1.5;background:radial-gradient(circle at 15% -10%,rgba(75,91,161,.22),transparent 34%),var(--bg)}[hidden]{display:none!important}a{color:inherit;text-decoration:none}button,input,select,textarea{font:inherit}.skip-link{position:fixed;left:12px;top:8px;z-index:100;transform:translateY(-160%);padding:10px 14px;border-radius:10px;background:#fff;color:#111;font-weight:800}.skip-link:focus{transform:none}.wrap{max-width:1180px;margin:auto;padding:0 22px 72px}.top{position:sticky;top:0;z-index:30;display:flex;align-items:center;justify-content:space-between;gap:18px;min-height:68px;background:rgba(9,12,17,.92);backdrop-filter:blur(18px);border-bottom:1px solid rgba(57,70,92,.7)}.brand{font-weight:900;font-size:17px;letter-spacing:-.02em}.nav{display:flex;gap:6px;flex-wrap:wrap}.nav a{padding:8px 11px;border:1px solid transparent;border-radius:10px;color:var(--muted);font-size:13px;font-weight:750}.nav a:hover{color:var(--text);background:#151c28}.nav a.active,.nav a[aria-current=page]{color:#fff;background:#211e3b;border-color:#4c4385}.hero{display:flex;align-items:flex-end;justify-content:space-between;gap:24px;padding:34px 0 20px}.hero-copy{max-width:720px}.eyebrow{margin-bottom:8px;color:#a89dff;font-size:12px;font-weight:850;letter-spacing:.1em;text-transform:uppercase}.hero h1{font-size:clamp(30px,5vw,44px);line-height:1.08;margin:0 0 10px;letter-spacing:-.04em}.hero-tools{display:flex;justify-content:flex-end;gap:6px;flex-wrap:wrap}.muted{color:var(--muted)}.tiny{font-size:12px;color:#8f9bad}.layout{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(290px,.72fr);gap:18px;align-items:start}.card{background:rgba(17,23,33,.95);border:1px solid var(--line);border-radius:var(--radius);padding:20px;box-shadow:0 18px 48px rgba(0,0,0,.14);margin-bottom:14px}.card h2,.card h3{margin:0 0 8px;letter-spacing:-.02em}.section-title{display:flex;justify-content:space-between;align-items:center;gap:12px}.section-kicker{display:block;margin-bottom:4px;color:#8e9aad;font-size:12px;font-weight:800}.grid2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.grid3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.choice-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin:14px 0}.choice{position:relative}.choice input{position:absolute;opacity:0}.choice label{display:block;height:100%;margin:0;padding:15px;border:1px solid var(--line-strong);border-radius:13px;background:var(--surface-2);cursor:pointer;min-height:88px}.choice input:checked+label{border-color:#8372ff;background:#201c39;box-shadow:0 0 0 3px rgba(131,114,255,.12)}.choice input:focus-visible+label{outline:3px solid rgba(118,170,255,.55);outline-offset:2px}.choice b{display:block;margin-bottom:4px}.choice span{display:block;font-size:12px;color:var(--muted);line-height:1.4}label.field{display:block;margin:14px 0 6px;color:#dfe5ee;font-size:13px;font-weight:800}.field-hint{display:block;margin-top:-2px;color:var(--muted);font-size:12px}.topic-input{min-height:84px}input[type=text],input[type=password],input[type=url],textarea,select{width:100%;border:1px solid var(--line-strong);border-radius:11px;background:#0a1018;color:#fff;padding:12px 13px;outline:none}textarea{min-height:118px;resize:vertical}input:focus,textarea:focus,select:focus{border-color:#8271ff;box-shadow:0 0 0 3px rgba(130,113,255,.14)}button,.btn{display:inline-flex;align-items:center;justify-content:center;gap:7px;min-height:42px;border:1px solid transparent;border-radius:11px;padding:10px 14px;background:var(--accent);color:#fff;font-weight:850;cursor:pointer}.btn:hover,button:hover{filter:brightness(1.08)}.btn.secondary{background:#171f2c;border-color:#364258}.btn.success{background:#167d51}.btn.danger{background:#852f3a}.btn.repair{background:#8a6619}.btn.small{min-height:36px;padding:7px 11px;font-size:12px}.btn.block,button.block{width:100%;margin-top:16px}a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible,summary:focus-visible{outline:3px solid rgba(118,170,255,.62);outline-offset:3px}.control-details,.system-details{border:1px solid var(--line);border-radius:13px;background:var(--surface-2)}.control-details{margin-top:18px}.control-details>summary,.system-details>summary{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:14px 15px;cursor:pointer;font-weight:850;list-style:none}.control-details>summary::-webkit-details-marker,.system-details>summary::-webkit-details-marker{display:none}.control-details>summary:after,.system-details>summary:after{content:'+';color:var(--muted);font-size:18px}.control-details[open]>summary:after,.system-details[open]>summary:after{content:'−'}.control-body,.system-body{padding:0 15px 15px;border-top:1px solid var(--line)}.guidance{margin:14px 0 4px;padding:12px;border:1px solid var(--line);border-radius:11px;background:#0a1018}.guidance b{display:block;margin-bottom:4px}.guidance p{margin:0;line-height:1.5}.status-summary{display:flex;align-items:center;gap:9px}.health-dot,.dot{display:inline-block;width:9px;height:9px;border-radius:50%;flex:0 0 auto}.green{background:var(--good)}.amber{background:var(--warn)}.red{background:var(--bad)}.status-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;padding-top:14px}.service{display:flex;align-items:center;padding:9px 10px;border-radius:10px;border:1px solid var(--line);background:#0a1018;font-size:12px;font-weight:750}.badge{display:inline-flex;align-items:center;gap:6px;border:1px solid #354055;border-radius:999px;background:#111824;padding:6px 9px;font-size:12px;font-weight:750}.notice{border:1px solid #6b5b23;background:#29230f;color:#f5df88;border-radius:12px;padding:12px 14px;font-size:13px}.notice.error{border-color:#76313a;background:#30171c;color:#ffbac1}.notice.success{border-color:#285d45;background:#10291e;color:#9ee7bd}.status-overview{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:0 0 20px}.status-filter{display:grid;gap:2px;min-height:86px;padding:14px 15px;border:1px solid var(--line);border-radius:14px;background:rgba(17,23,33,.92);transition:border-color .15s ease,transform .15s ease}.status-filter:hover{border-color:#4c5a70;transform:translateY(-1px)}.status-filter[aria-current=page]{border-color:#8271ff;box-shadow:0 0 0 3px rgba(130,113,255,.12)}.status-filter .status-count{font-size:25px;font-weight:900;line-height:1}.status-filter .status-name{color:var(--soft);font-size:12px;font-weight:800}.status-filter.running{border-left:3px solid #7499ff}.status-filter.ready{border-left:3px solid var(--good)}.status-filter.repair{border-left:3px solid var(--warn)}.status-filter.failed{border-left:3px solid var(--bad)}.history-toolbar{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;margin:2px 0 12px}.history-toolbar h2{margin:0}.job-list{display:grid;gap:10px}.job{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:11px 18px;align-items:start;padding:15px;border:1px solid var(--line);border-radius:14px;background:var(--surface-2)}.job:hover{border-color:#3b4960}.job-main{min-width:0}.job-title{display:-webkit-box;overflow:hidden;-webkit-box-orient:vertical;-webkit-line-clamp:2;line-clamp:2;font-size:15px;font-weight:850;line-height:1.35}.job-status{display:-webkit-box;overflow:hidden;-webkit-box-orient:vertical;-webkit-line-clamp:2;line-clamp:2;margin-top:6px;color:#b1bdcd;font-size:12px;line-height:1.45}.job-meta{display:flex;align-items:center;gap:5px 12px;flex-wrap:wrap;margin-top:9px;color:#8794a7;font-size:11px}.job-meta span,.job-meta time{white-space:nowrap}.job-meta b{color:#aeb9c9;font-weight:750}.job-side{display:grid;justify-items:end;gap:10px;min-width:148px}.job-side form{margin:0}.state{display:inline-flex;align-items:center;min-height:27px;font-size:11px;font-weight:900;padding:5px 8px;border-radius:999px;background:#222b3a;white-space:nowrap}.state.running{background:#192945;color:#9cb8ff}.state.ready{background:#123a28;color:#80e7ab}.state.repair{background:#3d3316;color:#ffe187}.state.failed{background:#3d1b22;color:#ffa1aa}.job-details{grid-column:1/-1;border-top:1px solid var(--line);padding-top:8px}.job-details>summary{width:max-content;max-width:100%;cursor:pointer;color:#8f9bad;font-size:11px;font-weight:750}.detail-body{display:grid;gap:10px;margin-top:9px;padding:11px;border-radius:10px;background:#090e15;color:#b9c4d2;font-size:12px}.detail-body dl{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:5px 10px;margin:0}.detail-body dt{color:#7f8da1}.detail-body dd{margin:0;min-width:0;overflow-wrap:anywhere}.detail-copy{margin:0;white-space:pre-wrap;overflow-wrap:anywhere}.job.compact{padding:12px}.job.compact .job-side{grid-column:1/-1;grid-template-columns:1fr auto;align-items:center;justify-items:start;min-width:0}.job.compact .job-side .btn{justify-self:end}.job-panel{max-width:820px}.job-panel-head{display:flex;align-items:center;justify-content:space-between;gap:12px}.job-panel .job-status{font-size:14px;margin-top:11px}.progress{height:10px;border:1px solid #344054;background:#090e16;border-radius:999px;overflow:hidden}.bar{height:100%;width:0;background:linear-gradient(90deg,var(--accent),var(--accent-2));transition:width .35s ease}.stage{font-size:14px;font-weight:850}.result-action{margin-top:16px}.result-action form{margin:0}.result-media-host{margin-top:18px}.result-media{display:grid;gap:12px;padding:14px;border:1px solid var(--line-strong);border-radius:14px;background:#080d14}.result-media-head{display:flex;align-items:center;justify-content:space-between;gap:12px}.result-media-head h2{margin:0;font-size:16px}.result-video{display:block;width:100%;max-height:480px;aspect-ratio:16/9;border:1px solid #253044;border-radius:11px;background:#000}.media-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.media-note{margin:0;color:#8f9bad;font-size:12px}.technical-details{margin-top:18px;border-top:1px solid var(--line);padding-top:10px}.technical-details>summary{cursor:pointer;color:#8f9bad;font-size:12px;font-weight:750}.technical-body{display:grid;gap:10px;margin-top:10px;padding:12px;border-radius:10px;background:#090e15;color:#b8c3d1;font-size:12px}.technical-body code{white-space:pre-wrap;overflow-wrap:anywhere}.page-links{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:18px}.page-links .btn[aria-disabled=true]{pointer-events:none;opacity:.45}.back-links{display:flex;gap:16px;flex-wrap:wrap;margin-top:16px;color:#9ba8b9;font-size:13px}.back-links a{text-decoration:underline;text-underline-offset:3px}.scene{display:grid;grid-template-columns:48px 1fr;gap:13px;padding:15px 0;border-bottom:1px solid var(--line)}.scene:last-child{border:0}.scene-no{width:40px;height:40px;border-radius:11px;background:#251f43;display:flex;align-items:center;justify-content:center;font-weight:900}.queries{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}.query{font-size:11px;padding:5px 7px;border-radius:8px;background:#0d151f;border:1px solid #2b394a;color:#9fb0c4}.metric{padding:12px;border:1px solid var(--line);border-radius:12px;background:var(--surface-2)}.metric b{font-size:20px;display:block}.actions{display:flex;gap:9px;flex-wrap:wrap;margin-top:12px}.empty{padding:26px;border:1px dashed var(--line-strong);border-radius:13px;color:var(--muted);text-align:center}.sidebar-copy{margin:0;font-size:13px;line-height:1.55}
@media(max-width:900px){.layout{grid-template-columns:1fr}.hero{align-items:flex-start;flex-direction:column}.hero-tools{justify-content:flex-start}.status-overview{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:650px){.wrap{padding:0 12px 56px}.top{position:static;align-items:flex-start;flex-direction:column;padding:14px 0}.nav{width:100%;overflow-x:auto;flex-wrap:nowrap;padding-bottom:2px;scrollbar-width:none}.nav::-webkit-scrollbar{display:none}.nav a{white-space:nowrap}.nav a:nth-child(n+4){display:none}.hero{padding:26px 0 17px}.hero h1{font-size:31px}.grid2,.grid3,.choice-grid,.status-grid{grid-template-columns:1fr}.status-overview{gap:8px}.status-filter{min-height:76px;padding:12px}.status-filter .status-count{font-size:22px}.card{padding:16px}.history-toolbar{align-items:flex-start;flex-direction:column}.job{grid-template-columns:1fr}.job-side{grid-template-columns:1fr auto;align-items:center;justify-items:start;min-width:0}.job-side .btn,.job-side form{justify-self:end}.job-side form button{width:auto}.job.compact .job-side{grid-template-columns:1fr auto}.page-links .btn{min-width:0}.actions .btn{width:100%}.job-panel-head{align-items:flex-start}.control-details>summary,.system-details>summary{align-items:flex-start}.section-title{align-items:flex-start}}
.status-overview{grid-template-columns:repeat(4,minmax(0,1fr))}.queue-group{display:grid;gap:8px}.queue-group+.queue-group{margin-top:15px;padding-top:15px;border-top:1px solid var(--line)}.queue-group-head{display:flex;align-items:center;justify-content:space-between;gap:10px}.queue-group-head h4{margin:0;font-size:12px;letter-spacing:.04em;text-transform:uppercase}.queue-group-head a{font-size:11px;color:#9eabc0;text-decoration:underline;text-underline-offset:3px}.archive-details{margin-top:14px;border:1px solid var(--line);border-radius:12px;background:#0b1119}.archive-details>summary{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:11px 12px;cursor:pointer;list-style:none}.archive-details>summary::-webkit-details-marker{display:none}.archive-label{display:grid;gap:2px}.archive-label b{font-size:12px}.archive-counts{display:flex;gap:6px}.archive-count{display:inline-flex;align-items:center;justify-content:center;min-width:28px;height:28px;border-radius:999px;font-size:12px;font-weight:900}.archive-count.completed{background:#122c25;color:#8fdab6}.archive-count.failed{background:#26171c;color:#ffabb3}.archive-body{display:flex;align-items:center;gap:8px;padding:11px 12px;border-top:1px solid var(--line)}.archive-body a{display:flex;align-items:center;justify-content:space-between;gap:16px;flex:1;padding:9px 10px;border:1px solid var(--line);border-radius:9px;font-size:12px;font-weight:800}.archive-body a[aria-current=page]{border-color:#8271ff;background:#201c39}.state.completed{background:#172b27;color:#9bd9bf}.job.compact .job-status{-webkit-line-clamp:1;line-clamp:1}
@media(max-width:900px){.status-overview{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:650px){.status-overview{grid-template-columns:1fr}.archive-body{align-items:stretch;flex-direction:column}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}.bar{transition:none}}
.state.unreviewed{background:#342d1b;color:#f1d88b}
'''

# Final media can be portrait or landscape. Let its intrinsic dimensions drive
# the player instead of stamping every result into the old 16:9 frame.
BASE_CSS = BASE_CSS.replace(
    'width:100%;max-height:480px;aspect-ratio:16/9;',
    'width:auto;max-width:100%;height:auto;max-height:min(72vh,720px);aspect-ratio:auto;object-fit:contain;',
)
BASE_CSS += r'''
.studio-primary{max-width:820px;margin:0 auto}.create-card{padding:clamp(18px,4vw,28px)}.create-card .private-note{display:flex;gap:8px;align-items:flex-start;margin:14px 0 0;color:#aeb9c8;font-size:12px}.create-card .private-note b{color:#e7ebf2}.console-details{margin-top:12px}.nav-more{position:relative}.nav-more>summary{padding:8px 11px;border:1px solid transparent;border-radius:10px;color:var(--muted);cursor:pointer;font-size:13px;font-weight:750;list-style:none;white-space:nowrap}.nav-more>summary::-webkit-details-marker{display:none}.nav-more>summary:hover,.nav-more[open]>summary{color:var(--text);background:#151c28}.nav-more-menu{position:absolute;right:0;top:calc(100% + 6px);z-index:40;display:grid;min-width:170px;padding:6px;border:1px solid var(--line-strong);border-radius:12px;background:#111721;box-shadow:0 16px 34px rgba(0,0,0,.35)}.nav-more-menu a{white-space:nowrap}.status-filter.create{border-left:3px solid var(--accent)}.status-filter.attention{border-left:3px solid var(--warn)}.status-filter.library{border-left:3px solid var(--good)}.state.attention{background:#3d3316;color:#ffe187}.archive-details{max-width:820px;margin:18px auto 0}.archive-details>summary:after{content:'+';color:var(--muted);font-size:18px}.archive-details[open]>summary:after{content:'−'}.archive-body{display:grid}.result-video-frame,.ready-media{display:flex;align-items:center;justify-content:center;min-height:180px;overflow:hidden;border:1px solid #253044;border-radius:12px;background:#030507}.result-video{border:0}.ready-grid{display:grid;gap:14px}.ready-card{display:grid;grid-template-columns:minmax(190px,260px) minmax(0,1fr);gap:18px;padding:16px;border:1px solid var(--line);border-radius:16px;background:var(--surface-2)}.ready-media{min-height:260px}.ready-video,.ready-thumbnail{display:block;width:auto;max-width:100%;height:auto;max-height:420px;object-fit:contain;background:#000}.ready-placeholder{display:grid;place-items:center;gap:6px;min-height:220px;color:#8794a7;text-align:center}.ready-placeholder span{font-size:28px}.ready-body{display:flex;min-width:0;flex-direction:column}.ready-title{font-size:18px;font-weight:900;line-height:1.3;letter-spacing:-.02em}.ready-facts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin:14px 0}.ready-fact{min-width:0;padding:9px 10px;border:1px solid var(--line);border-radius:10px;background:#090e15}.ready-fact b{display:block;color:#7f8da1;font-size:10px;letter-spacing:.05em;text-transform:uppercase}.ready-fact span{display:block;overflow:hidden;margin-top:2px;color:#dce3ed;font-size:12px;text-overflow:ellipsis;white-space:nowrap}.ready-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:auto}.ready-actions form{margin:0}.ready-card .state{margin-bottom:10px;align-self:flex-start}.history-label{margin-bottom:12px}.history-label h2{margin:0}.history-label .muted{margin-top:4px;font-size:13px}
@media(max-width:650px){.status-overview{grid-template-columns:repeat(2,minmax(0,1fr));gap:6px}.status-filter{min-height:72px;padding:10px 8px}.status-filter .status-name{font-size:11px;line-height:1.25}.status-filter .status-count{font-size:20px}.ready-card{grid-template-columns:1fr;padding:12px}.ready-media{min-height:220px}.ready-video,.ready-thumbnail{max-height:62vh}.ready-facts{grid-template-columns:1fr 1fr}.ready-actions{align-items:stretch}.ready-actions .btn,.ready-actions form,.ready-actions form button{width:100%}.nav-more-menu{position:fixed;left:12px;right:12px;top:auto}.create-card{padding:16px}}
'''

METRICS_CSS = r'''
.channel-overview{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px;margin:0 0 22px}.channel-summary{min-width:0;border:1px solid var(--line);border-radius:14px;background:var(--surface-2);padding:15px}.channel-summary h3{font-size:15px;margin:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.channel-summary-head{display:flex;justify-content:space-between;gap:10px;align-items:center}.channel-numbers{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:7px;margin:14px 0}.channel-numbers b{display:block;font-size:18px;letter-spacing:-.03em}.channel-numbers span{font-size:10px;color:var(--muted)}.channel-numbers b.waiting{font-size:11px;letter-spacing:0;font-weight:600;color:var(--muted)}.channel-schedule{display:flex;gap:8px;flex-wrap:wrap;border-top:1px solid var(--line);padding-top:10px;font-size:11px;color:var(--soft)}.metrics-toolbar{display:flex;justify-content:space-between;align-items:center;gap:12px;margin:8px 0 12px}.metrics-toolbar h2{margin:0;font-size:18px}.metrics-toolbar p{margin:3px 0 0;font-size:11px;color:var(--muted)}.metrics-note{font-size:11px;color:var(--muted)}.metrics-toolbar .btn[disabled]{opacity:.55;cursor:wait}.performance-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin:0 0 8px}.performance-cell{min-width:0;padding:9px 10px;background:#0b1119;border:1px solid var(--line);border-radius:10px}.performance-cell b{display:block;font-size:16px;font-weight:850}.performance-cell b.waiting{font-size:11px;color:var(--muted);font-weight:600}.performance-cell span{display:block;color:var(--muted);font-size:10px;margin-top:2px}.video-performance{margin:0 0 14px}.video-performance .metrics-note{margin:0}.delivery-badges{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin:0 0 10px}.ready-card .delivery-badges .state{margin:0}.state.public{background:#123a28;color:#80e7ab}.state.private,.state.uploaded{background:#192945;color:#9cb8ff}.state.rendered{background:#25243b;color:#c4bcff}.library-filters{display:flex;gap:7px;flex-wrap:wrap;margin:0 0 16px}.library-filter{font-size:12px;padding:8px 11px;border:1px solid var(--line);border-radius:999px;color:var(--muted)}.library-filter[aria-current=page]{background:#211e3b;border-color:#8271ff;color:#fff}.video-identity{display:flex;gap:6px 12px;flex-wrap:wrap;margin:10px 0;color:var(--soft);font-size:12px}.job-panel .video-performance{margin:16px 0 0}.ready-facts{grid-template-columns:repeat(2,minmax(0,1fr))}.ready-fact span{white-space:normal;line-height:1.4}.job-meta .metrics-note{flex-basis:100%}@media(max-width:650px){.channel-overview{grid-template-columns:1fr}.metrics-toolbar{align-items:flex-start}.performance-grid{gap:5px}.performance-cell{padding:8px}.metrics-toolbar .btn{flex-shrink:0}.library-filters{gap:5px}.library-filter{padding:7px 9px}.delivery-badges .state{white-space:normal}}
'''


METRICS_CSS += '.delivery-badges .state{display:inline-flex;font-size:11px;font-weight:850;padding:5px 8px;border-radius:999px}.delivery-badges .state.attention{background:#3d3316;color:#ffe187}'
BASE_CSS += METRICS_CSS
BASE_CSS += r'''
.overview-hero{align-items:center;margin:24px 0}.overview-hero h1{font-size:clamp(28px,5vw,40px)}.overview-hero .muted{max-width:550px}.overview-top{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:18px}.automation-card{position:relative;overflow:hidden;background:linear-gradient(135deg,#211d38,#101722);border:1px solid #45405f;border-radius:18px;padding:24px}.automation-card h2{font-size:22px;margin:10px 0}.automation-card p{margin:8px 0 0;color:var(--soft);font-size:14px;line-height:1.6}.automation-card .section-kicker{color:#b8a8ff}.overview-top .notice{margin:0;padding:24px;border-radius:18px;display:flex;flex-direction:column;justify-content:center;background:#161b24;border-color:#3c3841}.overview-top .notice b{font-size:17px;line-height:1.45}.overview-top .notice p{font-size:13px;line-height:1.7;margin:10px 0 0}.overview-counts{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:18px 0 26px}.overview-count{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:18px 20px;border:1px solid var(--line);background:var(--surface);border-radius:14px}.overview-count:hover{border-color:#8876c8;background:#191b2a}.overview-count b{font-size:30px;line-height:1;font-variant-numeric:tabular-nums}.overview-count span{font-size:13px;color:var(--soft)}.overview-count small{display:block;margin-top:4px;color:var(--muted);font-size:11px}.workflow-strip{display:flex;flex-wrap:wrap;gap:9px;align-items:center;margin:18px 0 0;padding:0;list-style:none;counter-reset:step}.workflow-strip li{display:flex;align-items:center;gap:6px;color:#c0b8d7;font-size:11px;counter-increment:step}.workflow-strip li:before{content:counter(step);display:grid;place-items:center;width:18px;height:18px;border-radius:50%;border:1px solid #625579;color:#dcd0ff;font-size:10px}.overview-section{margin:24px 0}.overview-heading{display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:12px}.overview-heading h2{font-size:19px;margin:0}.overview-heading a{font-size:12px;color:#c0b2fa}.overview-recent{border:1px solid var(--line);border-radius:16px;background:var(--surface);overflow:hidden}.overview-video{display:flex;align-items:center;gap:16px;justify-content:space-between;padding:17px 20px;border-bottom:1px solid var(--line)}.overview-video:last-child{border-bottom:0}.overview-video:hover{background:#181d2a}.overview-video-title{font-weight:750;font-size:14px;line-height:1.5;overflow-wrap:anywhere}.overview-video-meta{color:var(--muted);font-size:12px;margin-top:5px}.overview-video .delivery-badges{justify-content:flex-end;margin:0;flex-shrink:0}.overview-footer{margin-top:28px;padding-top:16px;border-top:1px solid var(--line);font-size:12px;color:var(--muted)}
@media(max-width:650px){.overview-top{grid-template-columns:1fr;gap:10px}.automation-card,.overview-top .notice{padding:19px}.overview-counts{gap:7px}.overview-count{flex-direction:column;align-items:flex-start;padding:13px 10px;gap:12px}.overview-count b{font-size:26px}.overview-count small{display:none}.overview-video{align-items:flex-start;flex-direction:column;gap:10px;padding:15px}.overview-video .delivery-badges{justify-content:flex-start}.overview-hero .btn{width:100%;text-align:center}}
'''


def _valid_token(value: str | None) -> bool:
    return bool(settings.factory_api_token and value and value == settings.factory_api_token)


def _require_auth(cookie_token: str | None) -> None:
    if not _valid_token(cookie_token):
        raise HTTPException(status_code=401, detail='Studio oturumu gerekli')


async def studio_auth_exception(request: Request, error: HTTPException):
    """Browser navigation gets a login page; API and mutation failures stay 401."""
    from fastapi.exception_handlers import http_exception_handler
    route = request.scope.get('route')
    response_class = getattr(route, 'response_class', None)
    if (error.status_code == 401 and error.detail == 'Studio oturumu gerekli'
            and request.method == 'GET' and 'text/html' in request.headers.get('accept', '').lower()
            and isinstance(response_class, type) and issubclass(response_class, HTMLResponse)):
        return RedirectResponse('/studio/access', status_code=303,
                                headers={'Cache-Control': 'private, no-store', 'Referrer-Policy': 'no-referrer'})
    return await http_exception_handler(request, error)


_VISUAL_DIAGNOSTIC_MAX_BYTES = 4 * 1024 * 1024
_VISUAL_DIAGNOSTIC_HEADERS = {
    'Content-Security-Policy': "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    'Cache-Control': 'private, no-store',
    'Referrer-Policy': 'no-referrer',
    'X-Content-Type-Options': 'nosniff',
    'X-Frame-Options': 'DENY',
}


def _visual_diagnostic_pointer(job: dict, task_id: str | None = None) -> tuple[str, str] | None:
    """Accept only this job's explicitly non-reusable, content-addressed HTML."""
    if not isinstance(job, dict):
        return None
    actual_id = job.get('task_id')
    try:
        if (not isinstance(actual_id, str) or str(UUID(actual_id)) != actual_id
                or (task_id is not None and task_id != actual_id)):
            return None
    except (ValueError, AttributeError):
        return None
    pointer = job.get('visual_allocation_checkpoint')
    if (not isinstance(pointer, dict) or type(pointer.get('version')) is not int
            or pointer['version'] != 1 or pointer.get('status') != 'diagnostic_only'
            or pointer.get('qa_approved') is not False or pointer.get('reusable_for_render') is not False):
        return None
    digest = pointer.get('html_sha256')
    if not isinstance(digest, str) or re.fullmatch(r'[0-9a-f]{64}', digest) is None:
        return None
    expected_key = f'diagnostics/visual_allocation/{actual_id}/review-{digest}.html'
    if pointer.get('html_key') != expected_key:
        return None
    return expected_key, digest


def _visual_diagnostics_link(job: dict) -> str:
    if _visual_diagnostic_pointer(job) is None:
        return ''
    return (
        f'<a class="btn secondary small" href="/studio/job/{job["task_id"]}/visual-diagnostics" '
        'target="_blank" rel="noopener noreferrer">Sahne tanısını aç</a>'
    )


def _qa_workprint_path(job: dict) -> str:
    if not isinstance(job, dict) or not job.get('qa_workprint'):
        return ''
    from app.services.qa_workprint_access import validated_pointer
    if validated_pointer(job) is None:
        return ''
    return f'/studio/job/{job["task_id"]}/qa-workprint'


def _qa_workprint_banner(job: dict) -> str:
    path = _qa_workprint_path(job)
    if not path:
        return ''
    return (
        '<section class="notice" aria-label="İnceleme taslağı">'
        '<b>Bu denemenin inceleme taslağı saklandı.</b>'
        '<p>Kalite kontrolünü geçmedi. Yayına hazır değildir ve YouTube’a gönderilmez.</p>'
        f'<a class="btn secondary" href="{path}">İnceleme videosunu aç</a></section>'
    )


def _review_media(job: dict) -> dict | None:
    from app.services.studio_preview import describe
    preview = describe(job)
    if preview:
        return preview
    if isinstance(job, dict) and _job_ui_status(job) in {'ready', 'completed'}:
        result = job.get('result') if isinstance(job.get('result'), dict) else {}
        url = _safe_external_url(result.get('download_url') or result.get('video_url'))
        if url:
            return {'kind': 'video', 'variant': 'legacy', 'label': 'Üretilen video',
                    'url': url, 'note': 'Önizlemek yayın onayı vermez.'}
    return None


def _review_player(job: dict, *, preload: str = 'none') -> str:
    preview = _review_media(job)
    if not preview:
        return '<div class="review-empty">Video dosyası henüz oluşmadı.</div>'
    kind = preview['kind']
    src = escape(preview['url'], quote=True)
    return (f'<div class="review-preview {kind}"><{kind} controls playsinline preload="{preload}" '
            f'aria-label="{escape(preview["label"], quote=True)}" src="{src}"></{kind}></div>'
            '<p class="review-video-error" hidden>Dosya şu anda açılamıyor. Ayrıntıları açıp yeniden deneyebilirsin.</p>')


def _review_reason(job: dict) -> str:
    message = _failure_review_reason(job)
    if _job_display_status(job) == 'held':
        message += ' İnceleme için saklandı. Güncel üretim planını ana panelden görebilirsin.'
    return message


def _failure_review_reason(job: dict) -> str:
    if job.get('state') != 'FAILURE':
        return _job_status_message(job)
    stage = str(job.get('failure_stage') or '')
    error = str(job.get('error') or '')
    if 'source' in error or 'factual' in error:
        return 'Senaryonun kaynakları veya bazı iddiaları doğrulanamadı. Bu deneme yayımlanmadı.'
    if 'included_router_response_unverified' in error:
        return 'AI servisinin yanıtı beklenen biçimde gelmedi. Bu deneme yayımlanmadı.'
    if 'spend' in error or 'cash' in error:
        return 'Bu adım kullanılabilir üretim bütçesiyle tamamlanamadı. Sonraki ücretli işlem engellendi.'
    if 'audio' in stage:
        return 'Ses veya konuşma zamanlaması kalite kontrolünden geçemedi.'
    if 'visual' in stage:
        return 'Görüntüler kalite kontrolünden geçemedi; anlatımla uyumu yeniden değerlendirilmeli.'
    if stage == 'research':
        return 'Araştırma tamamlanamadı. Henüz video üretilmedi.'
    if stage == 'director_qc':
        return 'Senaryo kontrolü tamamlanamadı. Henüz video üretilmedi.'
    return 'Üretim tamamlanamadı. Bu deneme yayımlanmadı.'


def _preview_jobs(jobs: list[dict]) -> list[dict]:
    """Keep old watchable attempts, deduplicating only the exact same media."""
    selected, seen = [], set()
    for job in jobs:
        preview = _review_media(job)
        if not preview:
            continue
        variant = preview['variant']
        identity = ((job.get('qa_workprint') or {}).get('sha256') if variant == 'workprint'
                    else (job.get('audio_candidate_checkpoint') or {}).get('audio_sha256') if variant == 'audio'
                    else (job.get('result') or {}).get('video_key') or preview['url'])
        marker = (variant, identity)
        if marker not in seen:
            selected.append(job)
            seen.add(marker)
    return selected


def _review_card(job: dict) -> str:
    task_id = str(job.get('task_id') or '')
    if re.fullmatch(r'[A-Za-z0-9_-]{1,128}', task_id) is None:
        return ''
    preview = _review_media(job)
    title = escape(_ellipsize(_safe_ui_text(_job_title(job)).replace('[bağlantı gizleniyor]', '').strip(), 112))
    kind = preview['kind'] if preview else 'none'
    label = preview['label'] if preview else 'Üretim tamamlanmadı'
    note = preview['note'] if preview else 'Bu işte oynatılacak video veya ses dosyası yok.'
    meta = ' · '.join(filter(None, (_job_channel(job), _job_language(job), _job_date(job))))
    media = _review_player(job)
    action = 'Videoyu aç' if kind == 'video' else 'Sesi dinle' if kind == 'audio' else 'Neden durdu?'
    target = f'/studio/job/{task_id}'
    if job.get('state') == 'AWAITING_APPROVAL':
        label, action, target = 'Senaryo hazır', "Storyboard'u aç", f'/studio/plan/{task_id}'
    elif _job_ui_status(job) == 'repair':
        action = 'Onarım ayrıntılarını aç'
    elif _job_ui_status(job) == 'running':
        action = 'Durumu aç'
    grouped = job.get('_grouped_attention_attempts')
    if type(grouped) is int and grouped > 0:
        note += f' {grouped + 1} benzer deneme tek kartta toplandı.'
    failures = job.get('_grouped_failure_attempts')
    if type(failures) is int and failures > 0:
        note += f' {failures} eski başarısız deneme bu kartta toplandı.'
    publisher_action = _job_primary_action(job) if _publication_status(job) else ''
    return (f'<article class="review-card {"has-media" if kind == "video" else "has-audio" if kind == "audio" else "no-media"}" '
            f'data-review-task="{task_id}" data-status="{_job_display_status(job)}">{media}<div class="review-body">'
            f'<div class="review-meta">{escape(meta)}</div><span class="review-kind {"warning" if job.get("state") == "FAILURE" else ""}">{label}</span>'
            f'<h2>{title}</h2><p class="review-reason">{escape(_review_reason(job))}</p>'
            f'<p class="tiny">{escape(note)}</p><div class="review-actions">'
            f'<a class="btn secondary" href="{target}">{action}</a>{publisher_action}'
            '</div></div></article>')


def _review_tabs(active: str, counts: dict, preview_count: int) -> str:
    tabs = [('attention', 'İnceleme', counts.get('attention', 0)),
            ('previews', 'Önizlemeler', preview_count),
            ('library', 'Videolar', counts.get('library', 0)),
            ('running', 'Üretimde', counts.get('running', 0))]
    return '<nav class="review-tabs" aria-label="Video listeleri">' + ''.join(
        f'<a href="/studio/history?status={key}"' + (' aria-current="page"' if active == key else '')
        + f'><span class="status-name">{label}</span><b><span data-status-count="{key}">{count}</span></b></a>' for key, label, count in tabs) + '</nav>'


def _review_script() -> str:
    return '''<script>document.querySelectorAll('.review-preview video,.review-preview audio').forEach(media=>{
media.addEventListener('error',()=>{const message=media.parentElement.nextElementSibling;if(message&&message.classList.contains('review-video-error'))message.hidden=false});
media.addEventListener('play',()=>{document.querySelectorAll('video,audio').forEach(other=>{if(other!==media)other.pause()})});
});</script>'''


def _read_visual_diagnostic_html(key: str, digest: str) -> bytes:
    """Read one bounded private object, never sign, redirect, or expose its key."""
    from app.services.storage import _client

    body = None
    try:
        stored = _client().get_object(Bucket=settings.bucket, Key=key)
        body = stored.get('Body')
        length = stored.get('ContentLength')
        content_type = str(stored.get('ContentType') or '').split(';', 1)[0].strip().lower()
        if (type(length) is not int or not 0 < length <= _VISUAL_DIAGNOSTIC_MAX_BYTES
                or content_type != 'text/html' or body is None):
            raise HTTPException(status_code=404, detail='Tanı kaydı bulunamadı', headers=_VISUAL_DIAGNOSTIC_HEADERS)
        # StreamingBody does not load the object until read; ContentLength is
        # checked first and the single read itself is capped at four MiB.
        payload = body.read(_VISUAL_DIAGNOSTIC_MAX_BYTES)
        if (not isinstance(payload, bytes) or len(payload) != length
                or hashlib.sha256(payload).hexdigest() != digest):
            raise HTTPException(status_code=404, detail='Tanı kaydı bulunamadı', headers=_VISUAL_DIAGNOSTIC_HEADERS)
        payload.decode('utf-8', errors='strict')
        return payload
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail='Tanı kaydı şu anda açılamıyor', headers=_VISUAL_DIAGNOSTIC_HEADERS) from None
    finally:
        if body is not None:
            try:
                body.close()
            except Exception:
                pass


def _service_statuses() -> list[tuple[str, bool]]:
    statuses = [
        ('OpenAI', bool(settings.openai_api_key)),
        ('ElevenLabs', bool(settings.elevenlabs_api_key)),
        ('Pexels', bool(settings.pexels_api_key)),
        ('Runway', bool(settings.runwayml_api_secret)),
        ('Fal video', bool(getattr(settings, 'fal_key', ''))),
        ('Storage', bool(settings.bucket and settings.endpoint)),
        ('Redis', bool(settings.redis_url)),
    ]
    if getattr(settings, 'gemini_critic_enabled', False):
        statuses.insert(
            1,
            ('Gemini critic', bool(getattr(settings, 'gemini_api_key', ''))),
        )
    return statuses


_UI_URL_RE = re.compile(r'(?i)\bhttps?://\S+')
_UI_AUTH_RE = re.compile(
    r'(?i)\bauthorization\b\s*[:=]\s*(?:(?:bearer|basic|key)\s+)?\S+'
)
_UI_SECRET_RE = re.compile(
    r'(?i)\b(api[_ -]?key|(?:access|refresh|id)[_-]?token|client[_-]?secret|token|secret)\b\s*[:=]\s*\S+'
)
_UI_BEARER_RE = re.compile(r'(?i)\b(bearer|basic)\b\s+\S+')
_TR_MONTHS = (
    '', 'Oca', 'Şub', 'Mar', 'Nis', 'May', 'Haz',
    'Tem', 'Ağu', 'Eyl', 'Eki', 'Kas', 'Ara',
)


def _plain_text(value: Any) -> str:
    """Collapse user-authored copy without interpreting it as markup."""
    return ' '.join(str(value or '').split())


def _safe_ui_text(value: Any) -> str:
    """Keep operational links out of readable cards and expandable briefs."""
    text = _UI_URL_RE.sub('[bağlantı gizlendi]', _plain_text(value))
    text = _UI_AUTH_RE.sub('Authorization=[gizlendi]', text)
    text = _UI_SECRET_RE.sub(lambda match: f'{match.group(1)}=[gizlendi]', text)
    return _UI_BEARER_RE.sub(lambda match: f'{match.group(1)} [gizlendi]', text)


def _ellipsize(value: Any, limit: int) -> str:
    text = _plain_text(value)
    if len(text) <= limit:
        return text
    shortened = text[: max(1, limit - 1)].rsplit(' ', 1)[0].rstrip(' ,;:-')
    return f'{shortened or text[: max(1, limit - 1)]}…'


def _job_title(job: dict) -> str:
    spec = job.get('spec') if isinstance(job.get('spec'), dict) else {}
    result = job.get('result') if isinstance(job.get('result'), dict) else {}
    package = result.get('package') if isinstance(result.get('package'), dict) else {}
    for candidate in (
        result.get('title'),
        package.get('title'),
        spec.get('title'),
    ):
        title = _safe_ui_text(candidate)
        if title:
            return _ellipsize(title, 96)
    topic = _safe_ui_text(spec.get('topic'))
    return _ellipsize(topic, 96) or 'Yeni video üretimi'


def _job_brief(job: dict) -> str:
    spec = job.get('spec') if isinstance(job.get('spec'), dict) else {}
    return _safe_ui_text(spec.get('topic'))


def _job_duration(job: dict) -> str:
    result = job.get('result') if isinstance(job.get('result'), dict) else {}
    spec = job.get('spec') if isinstance(job.get('spec'), dict) else {}
    try:
        seconds = float(result.get('duration') or 0)
    except (TypeError, ValueError):
        seconds = 0
    if seconds <= 0:
        try:
            seconds = float(spec.get('duration_minutes') or 0) * 60
        except (TypeError, ValueError):
            seconds = 0
    if seconds <= 0:
        return ''
    if seconds < 90:
        return f'{round(seconds):d} sn'
    minutes = seconds / 60
    return f'{minutes:.0f} dk' if minutes.is_integer() else f'{minutes:.1f} dk'


def _job_date(job: dict) -> str:
    raw = str(job.get('updated_at') or job.get('created_at') or '').strip()
    if not raw:
        return ''
    try:
        value = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        value = value.astimezone(timezone(timedelta(hours=3)))
    except (TypeError, ValueError):
        return ''
    return f'{value.day} {_TR_MONTHS[value.month]} {value.year} · {value:%H:%M}'


def _job_channel(job: dict) -> str:
    spec = job.get('spec') if isinstance(job.get('spec'), dict) else {}
    result = job.get('result') if isinstance(job.get('result'), dict) else {}
    youtube = result.get('youtube') if isinstance(result.get('youtube'), dict) else {}
    channel = result.get('channel') if isinstance(result.get('channel'), dict) else {}
    value = (
        youtube.get('channel_title')
        or job.get('_youtube_channel_title')
        or channel.get('title')
        or spec.get('target_channel_title')
        or spec.get('channel_id')
        or spec.get('target_channel_id')
        or result.get('target_channel_id')
        or youtube.get('target_channel_id')
        or spec.get('production_channel_id')
    )
    return _ellipsize(_safe_ui_text(value), 42)


def _job_profile(job: dict) -> str:
    spec = job.get('spec') if isinstance(job.get('spec'), dict) else {}
    result = job.get('result') if isinstance(job.get('result'), dict) else {}
    snapshot = (
        spec.get('profile_snapshot')
        if isinstance(spec.get('profile_snapshot'), dict)
        else result.get('profile_snapshot')
        if isinstance(result.get('profile_snapshot'), dict)
        else {}
    )
    value = (
        spec.get('target_profile_title')
        or spec.get('route_label')
        or snapshot.get('route_label')
        or STYLE_LABELS.get(str(spec.get('content_style') or ''))
    )
    return _ellipsize(_safe_ui_text(value), 32)


def _job_language(job: dict) -> str:
    spec = job.get('spec') if isinstance(job.get('spec'), dict) else {}
    result = _job_result(job)
    code = str(result.get('language') or spec.get('language') or '').casefold().replace('_', '-').split('-')[0]
    return {'tr': 'Türkçe', 'en': 'English', 'de': 'Deutsch', 'es': 'Español', 'ar': 'العربية'}.get(code, 'Dil belirtilmedi')


def _job_format(job: dict) -> str:
    spec = job.get('spec') if isinstance(job.get('spec'), dict) else {}
    # Only the frozen production setting identifies the authored format.
    # A short duration or a remote API thumbnail does not prove Shorts.
    return {'shorts': 'Shorts formatı', 'landscape': 'Normal video'}.get(str(spec.get('format') or ''), 'Biçim belirtilmedi')


def _metric_number(value: Any) -> str:
    if type(value) is not int or value < 0:
        return 'Veri bekleniyor'
    return f'{value:,}'.replace(',', '.')


def _metrics_time(value: Any) -> str:
    if not isinstance(value, str):
        return 'Veri bekleniyor'
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            return 'Veri bekleniyor'
        stamp = stamp.astimezone(timezone(timedelta(hours=3)))
        return f'{stamp.day} {_TR_MONTHS[stamp.month]} · {stamp:%H:%M}'
    except (ValueError, OverflowError):
        return 'Veri bekleniyor'


def _known_public_video_view_subtotals(videos: dict) -> dict[str, int]:
    """Presentation only: unique, current public-video observations, not channel totals."""
    observed = {}
    for video in list(videos.values())[:5000]:
        if not isinstance(video, dict):
            continue
        channel_id, video_id = video.get('channel_id'), video.get('video_id')
        if (not isinstance(channel_id, str) or re.fullmatch(r'[A-Za-z0-9_-]{8,128}', channel_id) is None
                or not isinstance(video_id, str) or re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id) is None):
            continue
        value = video.get('view_count')
        count = value if (type(value) is int and 0 <= value <= 2**64 - 1
                          and video.get('status') == 'fresh' and video.get('privacy_status') == 'public'
                          and video.get('reason') is None
                          and _metrics_time(video.get('fetched_at')) != 'Veri bekleniyor') else None
        key = (channel_id, video_id)
        # Duplicate task/publisher rows cannot double count or choose the more
        # optimistic of contradictory observations for the same exact video.
        if key not in observed:
            observed[key] = count
        elif observed[key] != count:
            observed[key] = None
    subtotals = {}
    for (channel_id, _video_id), count in observed.items():
        if count is not None:
            subtotals[channel_id] = subtotals.get(channel_id, 0) + count
    return {channel_id: count for channel_id, count in subtotals.items() if 0 < count <= 2**64 - 1}


def _active_production_retry(profile: dict, state: dict, by_id: dict, *, now: float | None = None) -> dict | None:
    """Project a recent retry record, not execution/QA/publication permission.

    Only the supplied job snapshot is inspected. Private claim tokens are not
    loaded, and a presentation override never changes the actual paused state.
    """
    try:
        if (not isinstance(profile, dict) or not isinstance(state, dict) or not isinstance(by_id, dict)
                or state.get('paused_reason') != 'previous_render_failed'
                or state.get('last_result') != 'FAILURE' or state.get('dispatch_status') != 'finished'
                or state.get('active_task_id') or state.get('unavailable')
                or profile.get('production_enabled') is not True or profile.get('auto_publish') is not True
                or profile.get('release_mode') != 'public'):
            return None
        root_id = _canonical_task_id(state.get('last_task_id'))
        current = by_id.get(root_id)
        if (not root_id or not isinstance(current, dict) or current.get('task_id') != root_id
                or current.get('parent_id')):
            return None
        spec = current.get('spec')
        channel_id, revision = profile.get('channel_id'), profile.get('profile_revision')
        connection = state.get('connection_id')
        topics = profile.get('production_topics')
        cursor = int(state.get('cursor', '-1'))
        if (not isinstance(spec, dict) or not isinstance(channel_id, str)
                or re.fullmatch(r'[A-Za-z0-9_-]{8,128}', channel_id) is None
                or not isinstance(revision, str) or not revision or state.get('profile_revision') != revision
                or not isinstance(connection, str) or re.fullmatch(r'[A-Za-z0-9_-]{8,128}', connection) is None
                or not isinstance(topics, list) or not 1 <= len(topics) <= 60
                or not all(isinstance(topic, str) and topic.strip() and len(topic) <= 240 for topic in topics)
                or str(cursor) != state.get('cursor') or not 1 <= cursor <= len(topics)
                or spec.get('production_channel_id') != channel_id
                or spec.get('production_connection_id') != connection
                or spec.get('production_profile_revision') != revision
                or spec.get('production_scheduled') is not True or spec.get('publish_after_render') is not True
                or spec.get('mode') != 'production' or type(spec.get('production_topic_index')) is not int
                or spec['production_topic_index'] != cursor - 1
                or spec.get('language') != profile.get('default_language')
                or spec.get('channel_id') != str(profile.get('route_label') or channel_id).strip()):
            return None
        identity = str(profile.get('channel_identity') or '').strip()[:240]
        brief = topics[cursor - 1].strip() + (f'\n\nChannel editorial direction: {identity}' if identity else '')
        if spec.get('topic') != brief:
            return None

        def frozen(value):
            return json.dumps({k: v for k, v in value.items() if k not in {'workflow', 'repair_source_task_id'}},
                              sort_keys=True, ensure_ascii=False, allow_nan=False)

        expected, seen = frozen(spec), {root_id}
        for _ in range(RETRY_PRESENTATION_MAX_HOPS):
            if (current.get('kind') != 'render' or current.get('state') != 'FAILURE'
                    or current.get('retry_claimed') is not True or current.get('result')
                    or current.get('retry_dispatch_state') not in {'reserved', 'dispatched', 'uncertain'}):
                return None
            child_id = _canonical_task_id(current.get('retry_child_task_id'))
            child = by_id.get(child_id)
            if (not child_id or child_id in seen or not isinstance(child, dict)
                    or child.get('task_id') != child_id or child.get('parent_id') != current.get('task_id')
                    or child.get('kind') != 'render' or not isinstance(child.get('spec'), dict)
                    or frozen(child['spec']) != expected):
                return None
            seen.add(child_id)
            if child.get('retry_child_task_id'):
                current = child
                continue
            leaf_state = child.get('state')
            activity = _job_activity_timestamp(child)
            clock = datetime.now(timezone.utc).timestamp() if now is None else float(now)
            if (leaf_state not in {'PENDING', 'RECEIVED', 'STARTED', 'PROGRESS', 'RETRY'}
                    or child.get('retry_claimed') or child.get('result')
                    or child.get('stage') in {'failed', 'complete', 'awaiting_approval'}
                    or activity is None or not 0 <= clock - activity < STALE_RUNNING_SECONDS):
                return None
            dispatch = current['retry_dispatch_state']
            status = ('retry_active' if leaf_state in {'STARTED', 'PROGRESS'}
                      else 'retry_uncertain' if dispatch == 'uncertain'
                      else 'retry_queued' if dispatch == 'dispatched' else 'retry_reserved')
            return {'status': status, 'task_id': child_id, 'stage': str(child.get('stage') or '')}
    except (TypeError, ValueError, OverflowError):
        return None
    return None


def _dashboard_metrics(jobs: list[dict], *, refresh: bool = False) -> dict:
    """Read/refresh the bounded metrics cache; never mutate render/publish jobs."""
    fallback = {'channels': [], 'videos': {}, 'updated_at': None, 'refresh_after_seconds': 300}
    try:
        from app.services.youtube_metrics import get_dashboard_metrics, refresh_dashboard_metrics
        model = refresh_dashboard_metrics(jobs) if refresh else get_dashboard_metrics(jobs)
        if not isinstance(model, dict) or not isinstance(model.get('channels'), list) or not isinstance(model.get('videos'), dict):
            return fallback
        model = {**model, 'channels': [dict(row) for row in model['channels'][:10] if isinstance(row, dict)]}
        subtotals = _known_public_video_view_subtotals(model['videos'])
        for row in model['channels']:
            row.pop('production_retry', None)
            row.pop('production_wait', None)
            row.pop('series_preparation', None)
            row.pop('known_public_video_view_subtotal', None)
            if type(row.get('view_count')) is int and row['view_count'] == 0 and row.get('channel_id') in subtotals:
                row['known_public_video_view_subtotal'] = subtotals[row['channel_id']]
    except Exception:
        return fallback
    try:
        from app.services.channel_production import get_production_state
        profiles = {row['channel_id']: row for row in list_channel_profiles() if isinstance(row, dict) and isinstance(row.get('channel_id'), str)}
        by_id = {job.get('task_id'): job for job in jobs[:HISTORY_SCAN_LIMIT] if isinstance(job, dict)}
        for row in model['channels']:
            channel_id = row.get('channel_id')
            profile = profiles.get(channel_id, {})
            state = get_production_state(channel_id)
            if not isinstance(state, dict):
                continue
            try:
                cursor = int(state.get('cursor', '0'))
                topics = profile.get('production_topics')
                remaining = max(0, len(topics) - cursor) if isinstance(topics, list) and cursor >= 0 else None
            except (TypeError, ValueError):
                remaining = None
            production_status = (
                'paused' if state.get('paused_reason')
                else 'active' if state.get('active_task_id')
                else 'disabled' if profile.get('production_enabled') is not True or profile.get('auto_publish') is not True
                else 'exhausted' if remaining == 0 else 'scheduled'
            )
            row.update(production_status=production_status, next_due=state.get('next_due'), remaining_topics=remaining)
            if (production_status == 'paused' and state.get('paused_reason') == 'previous_render_failed'
                    and profile.get('production_enabled') is True and profile.get('auto_publish') is True):
                from app.services.studio_operations import read_quality_wait
                wait = read_quality_wait(channel_id, profile.get('profile_revision'), state.get('last_task_id'))
                if wait:
                    row.update(production_status='daily_wait', production_wait=wait)
            if (production_status == 'exhausted' and profile.get('production_enabled') is True
                    and profile.get('auto_publish') is True):
                from app.services.studio_operations import read_preparation_wait
                wait = read_preparation_wait(channel_id, profile.get('profile_revision'))
                if wait:
                    row.update(production_status='planning_wait', production_wait=wait)
            if profile.get('production_enabled') is True and remaining is not None and remaining <= 2:
                from app.services.studio_operations import read_series_preparation
                preparation = read_series_preparation(channel_id, profile.get('profile_revision'))
                if preparation:
                    row['series_preparation'] = preparation
            retry = _active_production_retry(profile, state, by_id)
            if retry:
                row.update(production_status=retry['status'], production_retry=retry)
                row.pop('production_wait', None)
    except Exception:
        # Missing schedule data cannot invent an active/healthy channel state.
        pass
    return model


def _with_youtube_metrics(job: dict, model: dict) -> dict:
    copied = dict(job)
    spec = job.get('spec') if isinstance(job.get('spec'), dict) else {}
    result = _job_result(job)
    youtube = result.get('youtube') if isinstance(result.get('youtube'), dict) else {}
    channel_id = youtube.get('target_channel_id') or result.get('target_channel_id') or spec.get('production_channel_id') or spec.get('target_channel_id')
    video_id = _delivery_video_id(job)
    metrics = (model.get('videos') or {}).get(job.get('task_id'))
    if (isinstance(metrics, dict) and video_id and channel_id
            and metrics.get('video_id') == video_id and metrics.get('channel_id') == channel_id):
        copied['_youtube_metrics'] = dict(metrics)
    else:
        copied.pop('_youtube_metrics', None)
    for row in model.get('channels') or []:
        if isinstance(row, dict) and channel_id and row.get('channel_id') == channel_id:
            copied['_youtube_channel_title'] = _safe_ui_text(row.get('title'))
            break
    return _with_verified_public_recovery(copied)


def _with_verified_public_recovery(job: dict, *, lookup=None) -> dict:
    """Presentation-only proof; never overwrite historical publication records."""
    copied = dict(job)
    copied.pop('_verified_public_recovery', None)
    result = _job_result(copied)
    youtube = result.get('youtube') if isinstance(result.get('youtube'), dict) else {}
    if (copied.get('kind') != 'render' or copied.get('state') != 'SUCCESS'
            or youtube.get('release_status') != 'blocked' or not _delivery_video_id(copied)):
        return copied
    try:
        if lookup is None:
            from app.services.blocked_public_release import get_verified_public_recovery_for_source
            lookup = get_verified_public_recovery_for_source
        proof = lookup(copied.get('task_id'))
        if isinstance(proof, dict):
            copied['_verified_public_recovery'] = dict(proof)
        if not _has_verified_public_recovery(copied):
            copied.pop('_verified_public_recovery', None)
    except Exception:
        copied.pop('_verified_public_recovery', None)
    return copied


def _has_verified_public_recovery(job: dict) -> bool:
    proof = job.get('_verified_public_recovery')
    if not isinstance(proof, dict) or job.get('kind') != 'render' or job.get('state') != 'SUCCESS':
        return False
    result = _job_result(job)
    youtube = result.get('youtube') if isinstance(result.get('youtube'), dict) else {}
    spec = job.get('spec') if isinstance(job.get('spec'), dict) else {}
    automation = result.get('youtube_automation') if isinstance(result.get('youtube_automation'), dict) else {}
    metrics = job.get('_youtube_metrics') if isinstance(job.get('_youtube_metrics'), dict) else {}
    if metrics.get('status') == 'fresh' and metrics.get('privacy_status') in {'private', 'unlisted'}:
        return False  # A newer observed privacy change remains visible.
    return bool(
        result.get('quality_disposition') == 'automated_qc_pass' and result.get('manual_qa_required') is False
        and youtube.get('release_status') == 'blocked'
        and proof.get('source_task_id') == job.get('task_id')
        and proof.get('youtube_video_id') == _delivery_video_id(job)
        and proof.get('publish_task_id') == automation.get('publish_task_id')
        and proof.get('target_channel_id') == youtube.get('target_channel_id') == spec.get('production_channel_id')
        and proof.get('profile_revision') == youtube.get('profile_revision') == spec.get('production_profile_revision')
        and proof.get('connection_id') == youtube.get('connection_id') == spec.get('production_connection_id')
        and proof.get('privacy_status') == proof.get('release_status') == 'public'
        and all(proof.get(key) is True for key in ('caption_uploaded', 'thumbnail_uploaded', 'contains_synthetic_media'))
        and re.fullmatch(r'[0-9a-f]{64}', str(proof.get('receipt_sha256') or ''))
    )


def _delivery_video_id(job: dict) -> str:
    """A URL-shaped string is not evidence of a real YouTube upload."""
    result = _job_result(job)
    youtube = result.get('youtube') if isinstance(result.get('youtube'), dict) else {}
    spec = job.get('spec') if isinstance(job.get('spec'), dict) else {}
    channel = youtube.get('target_channel_id') or result.get('target_channel_id') or spec.get('production_channel_id') or spec.get('target_channel_id')
    if not isinstance(channel, str) or re.fullmatch(r'[A-Za-z0-9_-]{8,128}', channel) is None:
        return ''
    if any(value and value != channel for value in (spec.get('production_channel_id'), spec.get('target_channel_id'), result.get('target_channel_id'))):
        return ''
    explicit = youtube.get('video_id') or result.get('youtube_video_id')
    if explicit is not None and (not isinstance(explicit, str) or re.fullmatch(r'[A-Za-z0-9_-]{11}', explicit) is None):
        return ''
    if youtube.get('video_id') and result.get('youtube_video_id') and youtube['video_id'] != result['youtube_video_id']:
        return ''
    url = youtube.get('url') or result.get('youtube_url')
    parsed_id = ''
    if url:
        try:
            parsed = urlsplit(str(url))
            if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port is not None:
                return ''
            if parsed.hostname in {'youtube.com', 'www.youtube.com', 'm.youtube.com'}:
                values = parse_qs(parsed.query).get('v', []) if parsed.path == '/watch' else []
                if len(values) == 1:
                    parsed_id = values[0]
                elif parsed.path.startswith('/shorts/'):
                    parsed_id = parsed.path.removeprefix('/shorts/').rstrip('/')
            elif parsed.hostname == 'youtu.be':
                parsed_id = parsed.path.lstrip('/').rstrip('/')
            if re.fullmatch(r'[A-Za-z0-9_-]{11}', parsed_id) is None or explicit and explicit != parsed_id:
                return ''
        except (ValueError, TypeError):
            return ''
    return explicit or parsed_id


def _video_delivery(job: dict) -> dict:
    """Display facts, not publication permission or a replacement for QA."""
    result = _job_result(job)
    youtube = result.get('youtube') if isinstance(result.get('youtube'), dict) else {}
    metrics = job.get('_youtube_metrics') if isinstance(job.get('_youtube_metrics'), dict) else {}
    uploaded = bool(_delivery_video_id(job))
    if (uploaded and metrics.get('video_id') == _delivery_video_id(job)
            and metrics.get('availability') == 'unavailable'
            and metrics.get('availability_evidence') == 'owner_api_absent'
            and _metrics_time(metrics.get('availability_checked_at')) != 'Veri bekleniyor'):
        return {'key': 'deleted', 'label': 'YouTube’dan silindi / erişilemiyor', 'attention': False}
    if (uploaded and metrics.get('video_id') == _delivery_video_id(job)
            and metrics.get('visibility_state_invalid') is True):
        return {'key': 'unknown', 'label': 'YouTube görünürlüğü doğrulanamadı', 'attention': True}
    publication = _publication_status(job)
    release = _ready_release_status(job)
    privacy = str(youtube.get('privacy_status') or result.get('privacy_status') or '').casefold()
    if _has_verified_public_recovery(job):
        privacy = 'public'
    if metrics.get('status') == 'fresh' and metrics.get('privacy_status') in {'private', 'public', 'unlisted'}:
        privacy = metrics['privacy_status']
    if uploaded:
        key = 'scheduled' if release == 'scheduled' and privacy != 'public' else privacy
        if key not in {'private', 'public', 'unlisted', 'scheduled'}:
            key = release if release in {'private', 'public', 'scheduled'} else 'uploaded'
        label = {'private': 'YouTube’a gizli yüklendi', 'public': 'YouTube’da yayında',
                 'unlisted': 'YouTube’da liste dışı', 'scheduled': 'YouTube yayını planlandı',
                 'uploaded': 'YouTube’a yüklendi · görünürlük bekleniyor'}[key]
    elif publication == 'pending':
        key, label = 'pending', 'YouTube yüklemesi sürüyor'
    elif _job_has_youtube_output(job):
        key, label = 'unknown', 'YouTube görünürlüğü doğrulanamadı'
    elif job.get('state') == 'SUCCESS' and result.get('video_key'):
        key, label = 'rendered', 'Üretildi · YouTube’a yüklenmedi'
    else:
        key, label = 'none', ''
    return {'key': key, 'label': label, 'attention': key == 'unknown' or publication in {'failed', 'blocked', 'uncertain'} or _job_requires_manual_qa(job) or _job_is_unreviewed_render(job)}


def _delivery_badges(job: dict) -> str:
    delivery = _video_delivery(job)
    if not delivery['label']:
        status = _job_display_status(job)
        return f'<span class="state {status}">{UI_STATUS_LABELS[status]}</span>'
    tone = 'public' if delivery['key'] == 'public' else 'private' if delivery['key'] in {'private', 'unlisted', 'scheduled', 'uploaded'} else 'attention' if delivery['key'] == 'unknown' else 'rendered'
    warning_label = 'Yüklemeyi kontrol et' if delivery['key'] == 'rendered' and _publication_status(job) in {'failed', 'blocked', 'uncertain'} else 'Kontrol gerekiyor'
    warning = f'<span class="state attention">{warning_label}</span>' if delivery['attention'] else ''
    return f'<span class="state {tone}" data-delivery-key="{delivery["key"]}">{escape(delivery["label"])}</span>{warning}'


def _video_identity(job: dict) -> str:
    values = (_job_channel(job) or 'Kanal seçilmedi', _job_format(job), _job_language(job), _job_duration(job))
    return '<div class="video-identity">' + ''.join(f'<span>{escape(value)}</span>' for value in values if value) + '</div>'


def _video_performance(job: dict) -> str:
    metrics = job.get('_youtube_metrics') if isinstance(job.get('_youtube_metrics'), dict) else {}
    cells = []
    for key, label in (('view_count', 'İzlenme'), ('like_count', 'Beğeni'), ('comment_count', 'Yorum')):
        text = _metric_number(metrics.get(key))
        waiting = ' class="waiting"' if text == 'Veri bekleniyor' else ''
        cells.append(f'<div class="performance-cell"><b data-metric="{key}"{waiting}>{text}</b><span>{label}</span></div>')
    note = 'Henüz YouTube’a yüklenmedi' if _video_delivery(job)['key'] in {'rendered', 'none'} else 'Son ölçüm: ' + _metrics_time(metrics.get('fetched_at'))
    if metrics.get('status') == 'stale':
        note += ' · Güncelleme bekleniyor'
    task_id = escape(str(job.get('task_id') or ''), quote=True)
    return f'<section class="video-performance" data-metrics-task="{task_id}" aria-label="YouTube performansı"><div class="performance-grid">{"".join(cells)}</div><p class="metrics-note" data-performance-summary>{escape(_performance_summary(job))}</p><p class="metrics-note" data-metric-time>{escape(note)}</p></section>'


def _performance_summary(job: dict) -> str:
    if _video_delivery(job)['key'] == 'deleted':
        return 'Video kanalın yetkili sorgusunda bulunamadı. Eski ölçümler ve yayın kaydı korunuyor; yeniden yüklenmez.'
    if _video_delivery(job)['key'] in {'private', 'scheduled', 'unlisted', 'rendered', 'none'}:
        return 'Henüz herkese açık değil'
    metrics = job.get('_youtube_metrics') if isinstance(job.get('_youtube_metrics'), dict) else {}
    views, likes, comments = (metrics.get(key) for key in ('view_count', 'like_count', 'comment_count'))
    if type(views) is not int or views < 0:
        return 'Performans verisi bekleniyor'
    if views == 0:
        return 'Henüz izlenme yok'
    if any(type(value) is int and value > 0 for value in (likes, comments)):
        return 'İzlenme ve etkileşim başladı'
    if all(type(value) is int and value == 0 for value in (likes, comments)):
        return 'İzleniyor · henüz etkileşim yok'
    return 'Performans verisi bekleniyor'


def _channel_overview(rows: list[dict]) -> str:
    cards = []
    for row in rows[:10]:
        channel_id = row.get('channel_id')
        if not isinstance(channel_id, str) or re.fullmatch(r'[A-Za-z0-9_-]{8,128}', channel_id) is None:
            continue
        title = _ellipsize(_safe_ui_text(row.get('title')), 48) or 'YouTube kanalı'
        subtotal = row.get('known_public_video_view_subtotal')
        total_pending = (type(row.get('view_count')) is int and row['view_count'] == 0
                         and type(subtotal) is int and 0 < subtotal <= 2**64 - 1)
        counts = []
        for key, label in (('subscriber_count', 'Abone'), ('video_count', 'Herkese açık video'), ('view_count', 'Toplam izlenme')):
            text = 'Gizli' if key == 'subscriber_count' and row.get('subscriber_count_hidden') is True else _metric_number(row.get(key))
            if key == 'view_count' and total_pending:
                text = 'Kanal toplamı güncelleniyor'
            css = ' class="waiting"' if text in {'Veri bekleniyor', 'Kanal toplamı güncelleniyor'} else ''
            counts.append(f'<div><b{css}>{text}</b><span>{label}</span></div>')
        retry = row.get('production_retry') if isinstance(row.get('production_retry'), dict) else {}
        retry_id = _canonical_task_id(retry.get('task_id'))
        valid_retry = bool(retry_id and retry.get('status') == row.get('production_status')
                           and retry.get('status') in PRODUCTION_RETRY_LABELS)
        production = (PRODUCTION_RETRY_LABELS[retry['status']] if valid_retry else {
            'active': 'Üretim sürüyor', 'scheduled': 'Takvim etkin', 'paused': 'Üretim durdu · kontrol gerekiyor',
            'daily_wait': 'Günlük deneme sınırı · otomatik bekleme',
            'planning_wait': 'Yeni konu planı · otomatik bekleme',
            'disabled': 'Otomatik üretim kapalı', 'exhausted': 'Konu listesi tamamlandı',
        }.get(row.get('production_status'), 'Üretim durumu bekleniyor'))
        schedule = [production]
        wait = row.get('production_wait')
        if row.get('production_status') in {'daily_wait', 'planning_wait'} and isinstance(wait, dict):
            schedule.append('Bugünkü konu planlama sınırına ulaşıldı.' if row['production_status'] == 'planning_wait'
                            else 'Bugünkü başarısız deneme sınırına ulaşıldı.')
            schedule.append('Yeniden kontrol: ' + _metrics_time(wait.get('retry_after')) + ' (Türkiye saati)')
            schedule.append('Süre dolunca bağlantı, kaynak ve kalite kontrolleri yeniden uygulanır.')
        preparation = row.get('series_preparation')
        if isinstance(preparation, dict):
            note = {
                'reserved': 'Yeni konu planı hazırlanıyor',
                'uncertain': 'Yeni konu planının sonucu doğrulanmayı bekliyor',
                'ready': 'Yeni konu planı hazır; geçiş kontrolü bekleniyor',
                'failed': 'Yeni konu planı hazırlanamadı',
            }.get(preparation.get('status'))
            if note:
                schedule.append(note + (' · Günlük deneme sınırı bekleniyor' if preparation.get('daily_wait') else ''))
                attempt_number = preparation.get('attempt_number')
                if type(attempt_number) is int and 1 <= attempt_number <= 240:
                    schedule.append(f'Planlama denemesi {attempt_number}')
        if valid_retry and retry['status'] == 'retry_active':
            schedule.append(STAGE_LABELS.get(retry.get('stage'), 'Hazırlanıyor'))
        remaining = row.get('remaining_topics')
        if type(remaining) is int and remaining >= 0:
            schedule.append(f'{remaining} konu sırada')
        try:
            due = float(row.get('next_due') or 0)
            if due > 0 and math.isfinite(due) and row.get('production_status') == 'scheduled':
                schedule.append('En erken: ' + _metrics_time(datetime.fromtimestamp(due, timezone.utc).isoformat()))
        except (ValueError, TypeError, OverflowError):
            pass
        status = 'Son ölçüm: ' + _metrics_time(row.get('fetched_at'))
        if row.get('status') == 'stale':
            status += ' · Güncelleme bekleniyor'
        if row.get('reason') == 'permission':
            status += ' · Google erişimi yenilenmeli'
            schedule.append('Kanallar sayfasından yeniden bağla')
        if total_pending:
            status += ' · Kayıtlı herkese açık videoların son ölçümü: ' + _metric_number(subtotal) + ' izlenme'
        retry_link = f'<a class="tiny" href="/studio/job/{retry_id}">Güncel denemeyi aç</a>' if valid_retry else ''
        cards.append(f'<article class="channel-summary"><div class="channel-summary-head"><h3>{escape(title)}</h3><a class="tiny" href="/studio/youtube#channel-{escape(channel_id, quote=True)}">Yönet</a></div><div class="channel-numbers">{"".join(counts)}</div><p class="metrics-note">{escape(status)}</p><div class="channel-schedule">' + ''.join(f'<span>{escape(item)}</span>' for item in schedule) + retry_link + '</div></article>')
    return '<div class="channel-overview">' + (''.join(cards) or '<div class="empty">Kanal verileri bekleniyor. <a href="/studio/youtube">YouTube bağlantılarını aç</a></div>') + '</div>'


def _metrics_header(model: dict) -> str:
    return '<div class="metrics-toolbar"><div><h2>Kanallar ve performans</h2><p>YouTube verileri gecikmeli olabilir · 5 dakikada bir yenilenir.<br><span id="metrics-updated">Son güncelleme: ' + escape(_metrics_time(model.get('updated_at'))) + '</span></p></div><button class="btn secondary small" type="button" id="metrics-refresh">Yenile</button></div><p class="metrics-note" id="metrics-feedback" role="status" aria-live="polite"></p>'


def _metrics_script() -> str:
    return r'''<script>
(()=>{let busy=false,lastPost=0,first=true,lastUpdate=null;const visible=()=>document.visibilityState==='visible',button=document.getElementById('metrics-refresh'),feedback=document.getElementById('metrics-feedback');
const number=value=>Number.isSafeInteger(value)&&value>=0?new Intl.NumberFormat('tr-TR').format(value):'Veri bekleniyor';
const time=value=>{if(typeof value!=='string'||!value)return 'Veri bekleniyor';const d=new Date(value);return Number.isNaN(d.getTime())?'Veri bekleniyor':new Intl.DateTimeFormat('tr-TR',{timeZone:'Europe/Istanbul',day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'}).format(d)};
function apply(model){let membershipChanged=false;document.querySelectorAll('[data-delivery-task]').forEach(node=>{const p=(model.video_presentations||{})[node.dataset.deliveryTask],old=node.querySelector('[data-delivery-key]');if(p&&old&&old.dataset.deliveryKey!==p.key&&(old.dataset.deliveryKey==='deleted'||p.key==='deleted'))membershipChanged=true});if(membershipChanged){window.location.reload();return}const host=document.getElementById('channel-overview-host');if(host&&typeof model.channel_overview_html==='string')host.innerHTML=model.channel_overview_html;const updated=document.getElementById('metrics-updated');if(updated)updated.textContent='Son güncelleme: '+time(model.updated_at);document.querySelectorAll('[data-metrics-task]').forEach(node=>{const m=(model.videos||{})[node.dataset.metricsTask],p=(model.video_presentations||{})[node.dataset.metricsTask];if(p){const summary=node.querySelector('[data-performance-summary]');if(summary)summary.textContent=p.summary||'Performans verisi bekleniyor'}if(!m)return;node.querySelectorAll('[data-metric]').forEach(cell=>{const value=number(m[cell.dataset.metric]);cell.textContent=value;cell.classList.toggle('waiting',value==='Veri bekleniyor')});const note=node.querySelector('[data-metric-time]');if(note)note.textContent='Son ölçüm: '+time(m.fetched_at)+(m.status==='stale'?' · Güncelleme bekleniyor':'')});document.querySelectorAll('[data-delivery-task]').forEach(node=>{const p=(model.video_presentations||{})[node.dataset.deliveryTask];if(p&&typeof p.badges_html==='string')node.innerHTML=p.badges_html});document.querySelectorAll('[data-delivery-label]').forEach(node=>{const p=(model.video_presentations||{})[node.dataset.deliveryLabel];if(p)node.textContent=p.label||'Görünürlük bekleniyor'})}
async function load(refresh=false){if(!visible()||busy)return;if(refresh&&Date.now()-lastPost<60000)return;busy=true;if(button)button.disabled=true;if(refresh)lastPost=Date.now();try{const response=await fetch('/studio/api/youtube-metrics'+(refresh?'/refresh':''),{method:refresh?'POST':'GET',credentials:'same-origin',cache:'no-store'});if(!response.ok)throw new Error('metrics');const model=await response.json();const stale=!model.updated_at||(model.channels||[]).some(x=>x.status!=='fresh')||Object.values(model.videos||{}).some(x=>x.status!=='fresh');apply(model);if(feedback&&refresh)feedback.textContent=stale?'Son saklanan ölçümler korunuyor; güncelleme bekleniyor.':model.updated_at&&model.updated_at!==lastUpdate?'Yeni YouTube ölçümleri alındı.':'Son ölçüm korunuyor; henüz yeni veri yok.';lastUpdate=model.updated_at;if(first){first=false;if(stale)setTimeout(()=>load(true),0)}}catch(_){if(feedback)feedback.textContent='Ölçümler şu anda alınamıyor; mevcut veriler korunuyor.'}finally{busy=false;if(button)button.disabled=false}}
if(button)button.addEventListener('click',()=>load(true));setInterval(()=>load(false),30000);setInterval(()=>load(true),300000);document.addEventListener('visibilitychange',()=>{if(visible())load(false)});load(false);
})();
</script>'''


def _library_filters(active: str) -> str:
    links = []
    for key, label in (('library', 'Tüm videolar'), ('ready', 'Üretildi · yüklenmedi'), ('uploaded', 'YouTube’a yüklendi'), ('public', 'Herkese açık'), ('private', 'Gizli'), ('deleted', 'Silinenler')):
        current = ' aria-current="page"' if active == key else ''
        links.append(f'<a class="library-filter" href="/studio/history?status={key}"{current}>{label}</a>')
    return '<nav class="library-filters" aria-label="Yayın durumuna göre filtrele">' + ''.join(links) + '</nav>'


def _job_mode(job: dict) -> str:
    spec = job.get('spec') if isinstance(job.get('spec'), dict) else {}
    raw_mode = str(spec.get('mode') or '')
    if raw_mode in MODE_LABELS:
        return MODE_LABELS[raw_mode]
    workflow = str(spec.get('workflow') or '')
    if workflow == 'scene_repair':
        return 'Sahne onarımı'
    kind = str(job.get('kind') or '')
    if kind == 'plan':
        return 'Storyboard'
    if kind == 'publish':
        return 'Gizli yükleme'
    return _ellipsize(_safe_ui_text(raw_mode), 28)


def _job_progress(job: dict) -> int:
    try:
        return max(0, min(100, int(float(job.get('progress') or 0))))
    except (TypeError, ValueError):
        return 0


def _retry_child_task_id(job: dict) -> str:
    """Read the forthcoming retry linkage without requiring its producer."""
    return _plain_text(job.get('retry_child_task_id'))


def _retry_claimed(job: dict) -> bool:
    # ``repair_claimed`` is the current scene-repair flag.  The generic fields
    # are accepted now so this UI can be cherry-picked before or after the
    # atomic retry-claim change without showing a second retry button.
    return bool(
        job.get('retry_claimed')
        or job.get('repair_claimed')
        or _retry_child_task_id(job)
    )


def _job_awaits_approval(job: dict) -> bool:
    return str(job.get('state') or '').upper() == 'AWAITING_APPROVAL'


def _job_result(job: dict) -> dict:
    return job.get('result') if isinstance(job.get('result'), dict) else {}


def _job_has_youtube_output(job: dict) -> bool:
    result = _job_result(job)
    youtube = result.get('youtube') if isinstance(result.get('youtube'), dict) else {}
    return bool(
        str(job.get('kind') or '') == 'publish'
        or result.get('youtube_url')
        or youtube.get('url')
        or youtube.get('video_id')
    )


def _job_quality_approved(job: dict) -> bool:
    result = _job_result(job)
    if result.get('quality_disposition') == 'editorial_review_pass':
        from app.services.external_editorial_review import publication_quality_approved

        return publication_quality_approved(job)
    return bool(
        str(job.get('state') or '').upper() == 'SUCCESS'
        and str(job.get('kind') or '') == 'render'
        and result.get('video_key')
        and result.get('quality_disposition') == 'automated_qc_pass'
        and result.get('manual_qa_required') is False
    )


def _job_requires_manual_qa(job: dict) -> bool:
    if (
        str(job.get('state') or '').upper() != 'SUCCESS'
        or str(job.get('kind') or '') != 'render'
        or _job_has_youtube_output(job)
    ):
        return False
    result = _job_result(job)
    return bool(
        result.get('quality_disposition') == 'manual_qa_preview'
        or result.get('manual_qa_required') is True
    )


def _job_is_unreviewed_render(job: dict) -> bool:
    return bool(
        str(job.get('state') or '').upper() == 'SUCCESS'
        and str(job.get('kind') or '') == 'render'
        and not _job_has_youtube_output(job)
        and not _job_quality_approved(job)
        and not _job_requires_manual_qa(job)
    )


def _job_upload_allowed(job: dict) -> bool:
    """Expose the same fail-closed quality decision to HTML and polling."""
    return (
        _job_quality_approved(job)
        and not _job_has_youtube_output(job)
        and not _publication_status(job)
    )


def _publication_status(job: dict) -> str:
    """Presentation only: an existing upload must be reviewed, not recreated."""
    if _has_verified_public_recovery(job):
        return ''
    release = _ready_release_status(job)
    if release in {'blocked', 'uncertain'}:
        return release
    if job.get('kind') == 'publish' and job.get('state') == 'FAILURE':
        return 'failed'
    if job.get('_publication_complete') is True:
        return ''
    result = _job_result(job)
    automation = result.get('youtube_automation')
    automation = automation if isinstance(automation, dict) else {}
    status = str(automation.get('status') or '')
    if status in {
        'quality_blocked', 'no_unique_route', 'connection_missing',
        'connection_changed', 'profile_changed', 'metadata_blocked',
        'reservation_blocked', 'queue_blocked', 'queue_error', 'preflight_failed', 'failed_preflight',
    }:
        return 'blocked'
    if status == 'uncertain':
        return 'uncertain'
    # This field is calculated from a verified publish child, never persisted.
    child_status = job.get('_publication_status')
    if child_status in {'failed', 'blocked', 'uncertain', 'pending'}:
        return child_status
    if status in {'queued', 'reserved', 'uploading', 'already_reserved'} and not _job_has_youtube_output(job):
        return 'pending'
    return ''


def _canonical_task_id(value: Any) -> str:
    if not isinstance(value, str):
        return ''
    try:
        return value if str(UUID(value)) == value else ''
    except ValueError:
        return ''


def _with_publication_presentation(job: dict, lookup, *, upload_lookup=None) -> dict:
    """Read at most one explicitly bound publisher; never reconcile or enqueue."""
    result = _job_result(job)
    automation = result.get('youtube_automation')
    automation = automation if isinstance(automation, dict) else {}
    source_id = _canonical_task_id(job.get('task_id'))
    publish_id = _canonical_task_id(automation.get('publish_task_id'))
    if job.get('kind') != 'render' or not source_id:
        return job
    ledger = None
    if not publish_id and upload_lookup is not None and job.get('state') == 'SUCCESS':
        try:
            ledger = upload_lookup(source_id)
        except Exception:
            return {**job, '_publication_status': 'uncertain'}
        if ledger is not None:
            if not _publication_ledger_matches_source(ledger, job):
                return {**job, '_publication_status': 'uncertain'}
            publish_id = ledger['publish_task_id']
    if not publish_id:
        return job
    try:
        publisher = lookup(publish_id)
    except Exception:
        publisher = None
    if not _publisher_matches_source(publisher, job) or publisher.get('task_id') != publish_id:
        if not _job_has_youtube_output(job):
            return {**job, '_publication_status': 'uncertain'}
        return job
    if ledger:
        publisher_spec = publisher['spec']
        if any(publisher_spec.get(key) != ledger.get(key) for key in ('target_channel_id', 'connection_id')):
            return {**job, '_publication_status': 'uncertain'}
        plan = ledger.get('publish_plan')
        revision = plan.get('profile_revision') if isinstance(plan, dict) else None
        if revision and publisher_spec.get('profile_revision') != revision:
            return {**job, '_publication_status': 'uncertain'}
    if _publisher_matches_completed_output(publisher, job) and (
        ledger is None or (
            ledger.get('status') == 'complete'
            and ledger.get('release_status') not in {'blocked', 'uncertain'}
            and ledger.get('youtube_video_id') == _job_result(job)['youtube']['video_id']
        )
    ):
        return {**job, '_publication_complete': True}
    status = _publication_status(publisher)
    if ledger:
        if ledger.get('release_status') in {'blocked', 'uncertain'}:
            status = ledger['release_status']
        elif ledger.get('status') in {'uncertain', 'preflight_failed', 'failed_preflight'}:
            status = 'uncertain' if ledger['status'] == 'uncertain' else 'failed'
    if publisher.get('state') in {'PENDING', 'RECEIVED', 'STARTED', 'PROGRESS', 'RETRY'}:
        status = status or 'pending'
    if not status and publisher.get('state') == 'SUCCESS' and not _job_has_youtube_output(job):
        status = 'uncertain'  # Reconcile the existing upload; do not offer another.
    if status:
        return {**job, '_publication_status': status, '_publication_task_id': publish_id}
    return job


def _publication_ledger_matches_source(ledger: Any, source: dict) -> bool:
    """Check the existing immutable upload target without changing its profile."""
    if (
        not isinstance(ledger, dict) or ledger.get('source_task_id') != source.get('task_id')
        or not _canonical_task_id(ledger.get('publish_task_id'))
        or not isinstance(ledger.get('target_channel_id'), str) or not ledger['target_channel_id']
        or not isinstance(ledger.get('connection_id'), str) or not ledger['connection_id']
    ):
        return False
    spec = source.get('spec') if isinstance(source.get('spec'), dict) else {}
    youtube = _job_result(source).get('youtube')
    youtube = youtube if isinstance(youtube, dict) else {}
    for key, production_key in (
        ('target_channel_id', 'production_channel_id'),
        ('connection_id', 'production_connection_id'),
    ):
        for expected in (spec.get(production_key), youtube.get(key)):
            if expected and expected != ledger.get(key):
                return False
    revision = spec.get('production_profile_revision')
    plan = ledger.get('publish_plan')
    return not revision or (
        isinstance(plan, dict) and plan.get('profile_revision') == revision
    )


def _publisher_matches_source(publisher: Any, source: dict) -> bool:
    if (
        source.get('kind') != 'render'
        or not isinstance(publisher, dict) or publisher.get('kind') != 'publish'
    ):
        return False
    source_id = _canonical_task_id(source.get('task_id'))
    spec = publisher.get('spec') if isinstance(publisher.get('spec'), dict) else {}
    return bool(
        source_id and _canonical_task_id(publisher.get('task_id'))
        and publisher.get('parent_id') == source_id
        and spec.get('source_task_id') == source_id
    )


def _publisher_matches_completed_output(publisher: dict, source: dict) -> bool:
    if (
        not _publisher_matches_source(publisher, source)
        or publisher.get('state') != 'SUCCESS' or _publication_status(publisher)
    ):
        return False
    output = _job_result(source).get('youtube')
    result = _job_result(publisher)
    if not isinstance(output, dict) or result.get('source_task_id') != source.get('task_id'):
        return False
    return all(
        isinstance(output.get(source_key), str) and bool(output[source_key])
        and output[source_key] == result.get(publisher_key)
        for source_key, publisher_key in (
            ('video_id', 'youtube_video_id'),
            ('target_channel_id', 'target_channel_id'),
            ('connection_id', 'connection_id'),
        )
    )


def _terminal_retry_presentation(job: dict, lookup, *, upload_lookup=None) -> dict | None:
    """Follow only reciprocal render/plan retry edges, with a strict read bound.

    The original state, error, media and QA remain the original job's. The
    terminal child's status and same-origin link are the only borrowed fields.
    """
    if job.get('state') != 'FAILURE' or job.get('kind') not in {'render', 'plan'}:
        return None
    seen = {_canonical_task_id(job.get('task_id'))}
    if '' in seen:
        return None
    current = job
    for hops in range(1, RETRY_PRESENTATION_MAX_HOPS + 1):
        child_id = _canonical_task_id(current.get('retry_child_task_id'))
        if current.get('state') != 'FAILURE' or not child_id or child_id in seen:
            return None
        seen.add(child_id)
        try:
            child = lookup(child_id)
        except Exception:
            return None
        if (
            not isinstance(child, dict) or child.get('task_id') != child_id
            or child.get('parent_id') != current.get('task_id')
            or child.get('kind') != job.get('kind')
        ):
            return None
        current = child
        if current.get('retry_child_task_id'):
            continue
        if current.get('state') not in {'SUCCESS', 'FAILURE', 'AWAITING_APPROVAL', 'CANCELLED'}:
            return None
        if current.get('state') == 'FAILURE' and _retry_claimed(current):
            return None
        current = _with_publication_presentation(current, lookup, upload_lookup=upload_lookup)
        stage_key = current.get('failure_stage') or current.get('stage') or 'complete'
        return {
            'task_id': child_id,
            'hops': hops,
            'ui_status': _job_ui_status(current),
            'display_status': _job_display_status(current),
            'stage_label': STAGE_LABELS.get(str(stage_key), 'Güncel sonuç'),
            'message': 'Güncel deneme: ' + _job_status_message(current),
        }
    return None


def _job_ui_status(job: dict) -> str:
    state = str(job.get('state') or 'PENDING').upper()
    if state == 'CANCELLED':
        return 'cancelled'
    if state == 'FAILURE':
        if job.get('kind') == 'render' and job.get('quality_held') is True:
            return 'failed'
        if _retry_claimed(job):
            return 'running'
        return 'repair' if job.get('repair_available') is True else 'failed'
    if state == 'SUCCESS':
        if _job_has_youtube_output(job):
            return 'completed'
        return 'ready'
    if _job_awaits_approval(job):
        return 'ready'
    return 'running'


def _job_display_status(job: dict) -> str:
    """Return the user-facing status shared by list, detail and polling."""
    if job.get('state') == 'CANCELLED':
        return 'cancelled'
    if _publication_status(job) in {'failed', 'blocked', 'uncertain'}:
        return 'attention'
    if job.get('state') == 'FAILURE' and job.get('kind') == 'render' and job.get('quality_held') is True:
        return 'held'
    if _job_is_recent_failed_leaf(job):
        return 'attention'
    if _job_awaits_approval(job) or _job_requires_manual_qa(job):
        return 'attention'
    if _job_is_unreviewed_render(job):
        return 'unreviewed'
    status = _job_ui_status(job)
    if status == 'running' and _job_is_stale_running(job):
        return 'attention'
    return status


def _job_status_message(job: dict) -> str:
    if job.get('state') == 'CANCELLED':
        return 'Sahibi tarafından iptal edildi; mevcut çıktı ve harcama kayıtları korunuyor. Kalite onayı verilmedi.'
    if _video_delivery(job)['key'] == 'deleted':
        return 'YouTube’dan silindi veya erişilemiyor. Önceki yayın kaydı korunuyor; tekrar yükleme başlatılmaz.'
    status = _job_ui_status(job)
    display_status = _job_display_status(job)
    if display_status == 'held':
        return ('Bu deneme yayımlanmadı ve inceleme için saklandı. '
                'Otomatik üretimin güncel planını ana panelden görebilirsin.')
    state = str(job.get('state') or 'PENDING').upper()
    kind = str(job.get('kind') or '')
    publication = _publication_status(job)
    if publication:
        prefix = _video_delivery(job)['label'] + '. ' if _video_delivery(job)['key'] in {'private', 'public', 'scheduled', 'unlisted', 'uploaded'} else ''
        return prefix + {
            'failed': 'Video korunuyor; YouTube yüklemesi tamamlanamadı. Mevcut yüklemeyi kontrol et.',
            'blocked': 'YouTube işlemi durdu. Mevcut yükleme ve kanal ayarlarını kontrol et; yeni yükleme başlatılmaz.',
            'uncertain': 'YouTube sonucu doğrulanmalı. İkinci yükleme başlatmadan mevcut kaydı kontrol et.',
            'pending': 'YouTube yüklemesi işleniyor; mevcut yüklemenin durumunu aç.',
        }[publication]
    if _job_requires_manual_qa(job):
        return 'Videoyu kontrol et; onaylanmadan YouTube’a yüklenmez.'
    if display_status == 'unreviewed':
        return 'Bu eski videoda açık kalite onayı yok; YouTube yüklemesi kapalı.'
    if display_status == 'attention' and status == 'running':
        return 'Üretim durdu; durumunu kontrol et.'
    if status == 'running':
        if _retry_claimed(job):
            if str(job.get('retry_dispatch_state') or '') == 'uncertain':
                return 'Yeniden deneme kuyruğu doğrulanıyor; ikinci kez başlatılmayacak.'
            if job.get('repair_claimed') or job.get('repair_available'):
                return 'Onarım kuyruğa alındı; sağlam sahneler korunuyor.'
            return 'Yeniden deneme kuyruğa alındı.'
        stage = STAGE_LABELS.get(
            str(job.get('stage') or 'queued'),
            'Hazırlanıyor',
        )
        progress = _job_progress(job)
        return f'{stage} devam ediyor · %{progress}' if progress else f'{stage} bekleniyor.'
    if status == 'ready':
        if state == 'AWAITING_APPROVAL':
            return 'Storyboard hazır; devam etmek için aç.'
        result = job.get('result') if isinstance(job.get('result'), dict) else {}
        youtube = result.get('youtube') if isinstance(result.get('youtube'), dict) else {}
        if kind == 'publish' or result.get('youtube_url') or youtube.get('url'):
            return 'Gizli YouTube yüklemesi tamamlandı.'
        return 'Video tamamlandı; izlemeye veya gizli yüklemeye hazır.'
    if status == 'completed':
        delivery = _video_delivery(job)
        return delivery['label'] + '.' if delivery['label'] else 'YouTube yüklemesi tamamlandı; görünürlük doğrulanmalı.'
    if status == 'repair':
        return 'Yalnızca sorunlu sahne yeniden üretilecek; diğerleri korunacak.'
    try:
        grouped_attempts = max(0, int(job.get('_grouped_failure_attempts') or 0))
    except (TypeError, ValueError):
        grouped_attempts = 0
    grouped_note = (
        f' {grouped_attempts} eski başarısız deneme bu kartta toplandı.'
        if grouped_attempts else ''
    )
    failure_stage = str(job.get('failure_stage') or '').strip()
    stage = STAGE_LABELS.get(failure_stage)
    if stage and stage != 'Başarısız':
        return (
            f'{stage} aşaması tamamlanamadı. '
            f'Aynı ayarlarla yeniden deneyebilirsin.{grouped_note}'
        )
    return (
        'Üretim tamamlanamadı. Aynı ayarlarla yeniden deneyebilirsin.'
        f'{grouped_note}'
    )


def _safe_external_url(value: Any) -> str:
    text = str(value or '').strip()
    return text if re.match(r'(?i)^https?://', text) else ''


def _job_media_urls(job: dict) -> tuple[str, str]:
    """Return final media URLs only for a completed render.

    URLs are deliberately kept out of status/error copy. Callers must escape
    them at the HTML boundary and must never add them to logs.
    """
    preview = _review_media(job)
    if preview and preview['kind'] == 'video' and preview['variant'] != 'legacy':
        result = _job_result(job)
        return preview['url'], _safe_external_url(result.get('caption_url') or result.get('captions_url') or result.get('subtitle_url'))
    if _job_ui_status(job) not in {'ready', 'completed'}:
        return '', ''
    result = job.get('result') if isinstance(job.get('result'), dict) else {}
    video_url = _safe_external_url(
        result.get('download_url') or result.get('video_url')
    )
    caption_url = _safe_external_url(
        result.get('caption_url')
        or result.get('captions_url')
        or result.get('subtitle_url')
    )
    return video_url, caption_url


def _job_media_panel(job: dict) -> str:
    preview = _review_media(job)
    if preview and preview['variant'] in {'workprint', 'audio'}:
        return ('<section class="result-media"><div class="result-media-head"><h2>'
                + escape(preview['label']) + '</h2></div>' + _review_player(job, preload='metadata')
                + '<p class="media-note">' + escape(preview['note']) + '</p></section>')
    video_url, caption_url = _job_media_urls(job)
    if not video_url:
        return ''
    safe_video_url = escape(video_url, quote=True)
    caption_action = ''
    if caption_url:
        safe_caption_url = escape(caption_url, quote=True)
        caption_action = (
            '<a class="btn secondary small" target="_blank" '
            'rel="noopener noreferrer" download '
            f'href="{safe_caption_url}">Altyazıyı indir (.srt)</a>'
        )
    if _video_delivery(job)['key'] in {'private', 'public', 'scheduled', 'unlisted', 'uploaded'}:
        media_note = _video_delivery(job)['label'] + '. Bu oynatıcı üretilen final dosyayı gösterir.'
    elif _job_requires_manual_qa(job):
        media_note = 'Videoyu kontrol et; onaylanmadan YouTube’a yüklenmez.'
    elif _job_is_unreviewed_render(job):
        media_note = 'Açık kalite onayı yok; YouTube yüklemesi kapalı.'
    else:
        media_note = 'Kalite onaylanana kadar YouTube yüklemesi gizli kalır.'
    return (
        '<section class="result-media" aria-labelledby="result-media-title">'
        '<div class="result-media-head"><h2 id="result-media-title">'
        'Video önizleme</h2><span class="badge">Final dosya</span></div>'
        '<div class="result-video-frame"><video class="result-video" controls '
        'playsinline preload="metadata" '
        f'src="{safe_video_url}">Tarayıcın video oynatmayı desteklemiyor.'
        '</video></div>'
        '<div class="media-actions">'
        '<a class="btn secondary small" target="_blank" '
        'rel="noopener noreferrer" download '
        f'href="{safe_video_url}">Videoyu indir</a>{caption_action}</div>'
        f'<p class="media-note">{escape(media_note)}</p></section>'
    )


def _ready_thumbnail_url(job: dict) -> str:
    result = job.get('result') if isinstance(job.get('result'), dict) else {}
    thumbnail = (
        result.get('thumbnail')
        if isinstance(result.get('thumbnail'), dict) else {}
    )
    return _safe_external_url(
        result.get('thumbnail_url')
        or result.get('poster_url')
        or thumbnail.get('url')
        or thumbnail.get('public_url')
    )


def _ready_release_status(job: dict) -> str:
    if _has_verified_public_recovery(job):
        return 'public'
    result = job.get('result') if isinstance(job.get('result'), dict) else {}
    youtube = result.get('youtube') if isinstance(result.get('youtube'), dict) else {}
    raw = str(
        youtube.get('release_status')
        or result.get('release_status')
        or ''
    ).strip().casefold()
    return raw if raw in {
        'private', 'public', 'scheduled', 'blocked', 'uncertain',
    } else ''


def _ready_privacy_label(job: dict) -> str:
    if _has_verified_public_recovery(job):
        return 'Herkese açık'
    result = job.get('result') if isinstance(job.get('result'), dict) else {}
    youtube = result.get('youtube') if isinstance(result.get('youtube'), dict) else {}
    raw = str(
        youtube.get('privacy_status')
        or result.get('privacy_status')
        or ''
    ).casefold()
    labels = {
        'private': 'Gizli',
        'unlisted': 'Liste dışı',
        'public': 'Herkese açık',
    }
    if raw in labels:
        return labels[raw]
    release_status = _ready_release_status(job)
    if release_status == 'public':
        return 'Herkese açık'
    if release_status in {'private', 'scheduled'}:
        return 'Gizli'
    if _job_ui_status(job) == 'completed':
        return 'Görünürlük bekleniyor'
    return 'Henüz yüklenmedi'


def _ready_readiness_label(job: dict) -> str:
    delivery = _video_delivery(job)
    if delivery['key'] in {'deleted', 'unknown', 'private', 'public', 'scheduled', 'unlisted', 'uploaded'}:
        return delivery['label'] + (' · kontrol gerekiyor' if delivery['attention'] else '')
    if _publication_status(job):
        return {
            'failed': 'Yükleme tamamlanamadı', 'blocked': 'YouTube işlemi durdu',
            'uncertain': 'Yükleme doğrulanmalı', 'pending': 'Yükleme işleniyor',
        }[_publication_status(job)]
    if _job_awaits_approval(job):
        return 'Storyboard onayı bekliyor'
    if _job_ui_status(job) == 'completed':
        release_label = {
            'private': 'YouTube’da gizli',
            'public': 'YouTube’da yayında',
            'scheduled': 'YouTube’da planlandı',
            'blocked': 'Gizli · yayın durdu',
            'uncertain': 'Yayın durumu doğrulanmalı',
        }.get(_ready_release_status(job))
        if release_label:
            return release_label
        privacy = _ready_privacy_label(job).casefold()
        return {
            'gizli': 'YouTube’da gizli',
            'liste dışı': 'YouTube’da liste dışı',
            'herkese açık': 'YouTube’da yayında',
        }.get(privacy, 'YouTube’a yüklendi')
    return 'Yüklemeye hazır'


def _ready_video_card(job: dict) -> str:
    """Render a finished video as a small library item, not a task log."""
    task_id = str(job.get('task_id') or '')
    safe_task_id = escape(task_id, quote=True)
    title = escape(_job_title(job))
    dom_id = re.sub(r'[^a-zA-Z0-9_-]+', '-', task_id).strip('-') or 'video'
    status = _job_ui_status(job)
    duration = _job_duration(job) or '—'
    channel = _job_channel(job) or 'Kanal seçilmedi'
    readiness = _ready_readiness_label(job)
    video_url, _caption_url = _job_media_urls(job)
    thumbnail_url = _ready_thumbnail_url(job)
    safe_thumbnail = escape(thumbnail_url, quote=True)
    if video_url:
        poster = f' poster="{safe_thumbnail}"' if thumbnail_url else ''
        media = (
            '<video class="ready-video" controls playsinline preload="metadata"'
            f'{poster} src="{escape(video_url, quote=True)}">'
            'Tarayıcın video oynatmayı desteklemiyor.</video>'
        )
    elif thumbnail_url:
        media = (
            f'<img class="ready-thumbnail" src="{safe_thumbnail}" alt="" '
            'loading="lazy">'
        )
    else:
        media = (
            '<div class="ready-placeholder" aria-label="Önizleme henüz hazır değil">'
            '<span aria-hidden="true">▶</span><small>Önizleme ayrıntılarda</small></div>'
        )

    details_action = (
        f'<a class="btn secondary small" href="/studio/job/{safe_task_id}">'
        'Ayrıntılar</a>'
    )
    primary_action = _job_primary_action(job)
    actions = [primary_action]
    if f'href="/studio/job/{safe_task_id}"' not in primary_action:
        actions.append(details_action)
    actions = actions[:2]
    facts = (
        ('Süre', duration),
        ('Kanal', channel),
        ('Biçim', _job_format(job)),
        ('Dil', _job_language(job)),
        ('Yayın', readiness),
    )
    facts_html = ''.join(
        f'<div class="ready-fact"><b>{label}</b><span' + (f' data-delivery-label="{safe_task_id}"' if label == 'Yayın' else '') + f'>{escape(value)}</span></div>'
        for label, value in facts
    )
    return (
        f'<article class="ready-card" data-status="{status}" '
        f'aria-labelledby="ready-title-{dom_id}"><div class="ready-media">{media}</div>'
        f'<div class="ready-body"><div class="delivery-badges" data-delivery-task="{safe_task_id}">{_delivery_badges(job)}</div>'
        f'<div class="ready-title" id="ready-title-{dom_id}">{title}</div>'
        f'<div class="ready-facts" aria-label="Video bilgileri">{facts_html}</div>'
        f'{_video_performance(job)}'
        f'<div class="ready-actions" data-action-count="{len(actions)}">'
        f'{"".join(actions)}</div></div></article>'
    )


def _voice_replacement_candidate(job: dict) -> bool:
    """Presentation only; the private reservation rechecks authoritative state."""
    spec = job.get('spec') if isinstance(job.get('spec'), dict) else {}
    candidate = job.get('audio_candidate_checkpoint')
    return bool(
        job.get('state') == 'FAILURE' and job.get('kind') == 'render'
        and job.get('quality_held') is not True
        and spec.get('mode') == 'production' and spec.get('format') == 'shorts'
        and spec.get('duration_minutes') == 0.5 and spec.get('language') == 'tr'
        and str(job.get('failure_stage') or job.get('stage') or '') in {'audio_qc', 'audio_qc_retry', 'audio_pause_recheck'}
        and not job.get('retry_child_task_id') and not job.get('retry_claimed') and not job.get('voice_replacement')
        and isinstance(candidate, dict) and candidate.get('status') == 'unapproved_candidate'
        and candidate.get('qa_approved') is False and candidate.get('requires_full_qa') is True
        and isinstance(candidate.get('audio_sha256'), str)
        and re.fullmatch(r'[0-9a-f]{64}', candidate['audio_sha256'])
    )


def _job_primary_action(job: dict, *, small: bool = True) -> str:
    status = _job_ui_status(job)
    display_status = _job_display_status(job)
    state = str(job.get('state') or 'PENDING').upper()
    task_id = escape(str(job.get('task_id') or ''), quote=True)
    size = ' small' if small else ''

    def aria(label: str) -> str:
        return escape(f'{_job_title(job)}: {label}', quote=True)

    if _video_delivery(job)['key'] == 'deleted':
        return f'<a class="btn secondary{size}" href="/studio/job/{task_id}" aria-label="{aria("Yayın kaydını aç")}">Yayın kaydını aç</a>'
    if _publication_status(job):
        publisher_id = _canonical_task_id(job.get('_publication_task_id'))
        if job.get('kind') == 'publish':
            publisher_id = _canonical_task_id(job.get('task_id'))
        target = f'/studio/youtube/publish-status/{publisher_id}' if publisher_id else '/studio/youtube'
        label = 'Mevcut yüklemeyi kontrol et'
        return f'<a class="btn repair{size}" href="{target}" aria-label="{aria(label)}">{label}</a>'
    if status == 'running':
        target = escape(_retry_child_task_id(job) or str(job.get('task_id') or ''), quote=True)
        if _retry_child_task_id(job):
            label = (
                'Onarım durumunu aç'
                if job.get('repair_claimed') else 'Yeniden denemeyi aç'
            )
        else:
            label = 'Durumu aç'
        return f'<a class="btn secondary{size}" href="/studio/job/{target}" aria-label="{aria(label)}">{label}</a>'
    if job.get('quality_held') is True:
        target, label = (f'/studio/job/{task_id}', 'İncele') if small else ('/studio', 'Üretim planını aç')
        return (f'<span class="meta">Bu deneme inceleme için saklanıyor.</span>'
                f'<a class="btn secondary{size}" href="{target}" aria-label="{aria(label)}">{label}</a>')
    if status == 'repair':
        return (
            f'<form method="post" action="/studio/retry/{task_id}">'
            f'<button class="btn repair{size}" type="submit" aria-label="{aria("Sorunlu sahneyi onar")}">Sorunlu sahneyi onar</button></form>'
        )
    if status == 'failed':
        if _voice_replacement_candidate(job):
            label = 'Sesi tek denemeyle yenile'
            return f'<a class="btn repair{size}" href="/studio/voice-replacement/{task_id}" aria-label="{aria(label)}">{label}</a>'
        return (
            f'<form method="post" action="/studio/retry/{task_id}">'
            f'<button class="btn danger{size}" type="submit" aria-label="{aria("Aynı ayarlarla tekrar dene")}">Aynı ayarlarla tekrar dene</button></form>'
        )
    if state == 'AWAITING_APPROVAL':
        return f'<a class="btn success{size}" href="/studio/plan/{task_id}" aria-label="{aria("Storyboard\'u aç")}">Storyboard\'u aç</a>'
    result = job.get('result') if isinstance(job.get('result'), dict) else {}
    youtube_id = _delivery_video_id(job)
    youtube_url = 'https://www.youtube.com/watch?v=' + youtube_id if youtube_id else ''
    download_url = _job_media_urls(job)[0]
    if youtube_url:
        return f'<a class="btn success{size}" target="_blank" rel="noopener noreferrer" href="{escape(youtube_url, quote=True)}" aria-label="{aria("YouTube\'da aç")}">YouTube\'da aç</a>'
    if _job_requires_manual_qa(job):
        if small:
            return f'<a class="btn repair{size}" href="/studio/job/{task_id}" aria-label="{aria("Kaliteyi incele")}">Kaliteyi incele</a>'
        if download_url:
            return f'<a class="btn repair{size}" target="_blank" rel="noopener noreferrer" href="{escape(download_url, quote=True)}" aria-label="{aria("Videoyu incele")}">Videoyu incele</a>'
        return f'<a class="btn repair{size}" href="/studio/job/{task_id}" aria-label="{aria("Kaliteyi incele")}">Kaliteyi incele</a>'
    if _job_upload_allowed(job):
        return f'<a class="btn success{size}" href="/studio/youtube" aria-label="{aria("Gizli yükle")}">Gizli yükle</a>'
    if download_url:
        label = 'Videoyu incele' if display_status == 'unreviewed' else 'Videoyu aç'
        return f'<a class="btn secondary{size}" target="_blank" rel="noopener noreferrer" href="{escape(download_url, quote=True)}" aria-label="{aria(label)}">{label}</a>'
    return f'<a class="btn secondary{size}" href="/studio/job/{task_id}" aria-label="{aria("Sonucu aç")}">Sonucu aç</a>'


def _job_details(job: dict) -> str:
    brief = _job_brief(job)
    error = _safe_ui_text(job.get('error'))
    task_id = escape(str(job.get('task_id') or ''))
    stage = escape(str(job.get('failure_stage') or job.get('stage') or '—'))
    internal_state = escape(str(job.get('state') or 'PENDING'))
    mode = escape(_job_mode(job))
    mode_row = f'<dt>Üretim türü</dt><dd>{mode}</dd>' if mode else ''
    creative = (
        f'<div><b>Yaratıcı talimat</b><p class="detail-copy">{escape(brief)}</p></div>'
        if brief and _plain_text(brief) != _plain_text(_job_title(job))
        else ''
    )
    error_html = (
        f'<div><b>Hata kaydı</b><p class="detail-copy">{escape(error)}</p></div>'
        if error else ''
    )
    return (
        '<details class="job-details"><summary>Teknik ayrıntılar</summary>'
        f'<div class="detail-body">{creative}{error_html}<dl>'
        f'{mode_row}<dt>İş kimliği</dt><dd>{task_id}</dd>'
        f'<dt>İç durum</dt><dd>{internal_state}</dd>'
        f'<dt>Aşama kodu</dt><dd>{stage}</dd></dl>{_visual_diagnostics_link(job)}</div></details>'
    )


def _status_counts(jobs: list[dict]) -> dict[str, int]:
    counts = {key: 0 for key in UI_STATUS_ORDER}
    for job in jobs:
        counts[_job_ui_status(job)] += 1
    return counts


def _archive_counts(jobs: list[dict]) -> dict[str, int]:
    return {
        'drafts': sum(_job_is_old_storyboard(job) for job in jobs),
        'failed': sum(_history_matches(job, 'failed') for job in jobs),
        'unreviewed': sum(
            _job_display_status(job) == 'unreviewed' for job in jobs
        ),
    }


def _job_created_timestamp(job: dict) -> float | None:
    try:
        timestamp = float(job.get('created_ts'))
    except (TypeError, ValueError):
        timestamp = float('nan')
    if math.isfinite(timestamp):
        return timestamp
    raw = str(job.get('created_at') or '').strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _job_activity_timestamp(job: dict) -> float | None:
    """Return the last durable activity time without changing the record."""
    for numeric_key, iso_key in (
        ('updated_ts', 'updated_at'),
        ('created_ts', 'created_at'),
    ):
        try:
            timestamp = float(job.get(numeric_key))
        except (TypeError, ValueError):
            timestamp = float('nan')
        # Ignore placeholders used by very old imports and fixtures. Production
        # Unix timestamps are safely above the year-2000 boundary.
        if math.isfinite(timestamp) and timestamp >= 946_684_800:
            return timestamp
        raw = str(job.get(iso_key) or '').strip()
        if not raw:
            continue
        try:
            parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    return None


def _job_is_stale_running(job: dict, *, now: float | None = None) -> bool:
    """Classify an inactive running record for display; never mutate it."""
    if _job_ui_status(job) != 'running':
        return False
    last_activity = _job_activity_timestamp(job)
    if last_activity is None:
        return False
    current = datetime.now(timezone.utc).timestamp() if now is None else float(now)
    return current - last_activity >= STALE_RUNNING_SECONDS


def _job_is_recent_failed_leaf(job: dict, *, now: float | None = None) -> bool:
    if (
        job.get('state') != 'FAILURE' or job.get('kind') not in {'render', 'plan'}
        or _retry_claimed(job)
    ):
        return False
    last_activity = _job_activity_timestamp(job)
    if last_activity is None:
        return False
    current = datetime.now(timezone.utc).timestamp() if now is None else float(now)
    return 0 <= current - last_activity < RECENT_FAILURE_SECONDS


def _job_is_dormant_plan_retry(job: dict, *, now: float | None = None) -> bool:
    """Hide only abandoned planning retries; keep their durable records intact."""
    state = str(job.get('state') or 'PENDING').upper()
    if (
        state not in {'PENDING', 'RECEIVED', 'STARTED', 'PROGRESS', 'RETRY'}
        or str(job.get('stage') or '') != 'plan_retry'
    ):
        return False
    last_activity = _job_activity_timestamp(job)
    if last_activity is None:
        return False
    current = datetime.now(timezone.utc).timestamp() if now is None else float(now)
    return current - last_activity >= PLAN_RETRY_DISPLAY_GRACE_SECONDS


def _job_is_old_storyboard(job: dict, *, now: float | None = None) -> bool:
    """Move untouched storyboard approvals out of the daily attention queue."""
    if str(job.get('kind') or '') != 'plan' or not _job_awaits_approval(job):
        return False
    last_activity = _job_activity_timestamp(job)
    if last_activity is None:
        return False
    current = datetime.now(timezone.utc).timestamp() if now is None else float(now)
    return current - last_activity >= OLD_STORYBOARD_SECONDS


def _console_bucket(job: dict) -> str:
    if _video_delivery(job)['key'] == 'deleted':
        return 'archive'
    if _job_is_old_storyboard(job):
        return 'archive'
    display_status = _job_display_status(job)
    if display_status == 'held':
        return 'archive'
    if display_status == 'attention':
        return 'attention'
    if display_status == 'running':
        return 'running'
    if display_status == 'repair':
        return 'attention'
    if display_status == 'completed' or _job_quality_approved(job):
        return 'library'
    return 'archive'


def _console_counts(jobs: list[dict]) -> dict[str, int]:
    counts = {key: 0 for key in CONSOLE_STATUS_ORDER}
    for job in jobs:
        bucket = _console_bucket(job)
        if bucket in counts:
            counts[bucket] += 1
        if bucket != 'library' and _video_delivery(job)['key'] in {'private', 'public', 'scheduled', 'unlisted', 'uploaded'}:
            counts['library'] += 1
    return counts


def _history_matches(job: dict, active: str) -> bool:
    if _video_delivery(job)['key'] == 'deleted' or active == 'deleted':
        return active == 'deleted' and _video_delivery(job)['key'] == 'deleted'
    if active == 'library' and _video_delivery(job)['key'] in {'private', 'public', 'scheduled', 'unlisted', 'uploaded'}:
        return True
    if active in {'uploaded', 'public', 'private'}:
        delivery = _video_delivery(job)
        return delivery['key'] in {'private', 'public', 'scheduled', 'unlisted', 'uploaded'} if active == 'uploaded' else delivery['key'] == active
    if active == 'drafts':
        return _job_is_old_storyboard(job)
    if active in CONSOLE_STATUS_ORDER:
        return _console_bucket(job) == active
    if active in {'ready', 'unreviewed'}:
        return _job_display_status(job) == active
    if active == 'failed':
        return _job_ui_status(job) == 'failed' and _console_bucket(job) == 'archive'
    return _job_ui_status(job) == active


def _attention_action_category(job: dict) -> str | None:
    """Return the real user action without inspecting rendered HTML or URLs."""
    if _console_bucket(job) != 'attention':
        return None
    # Independent failed uploads must stay individually reviewable.
    if _publication_status(job):
        return None
    if _job_requires_manual_qa(job):
        return 'review'
    if _job_ui_status(job) == 'repair':
        return 'repair'
    if _job_ui_status(job) == 'failed':
        return 'retry'
    if _job_awaits_approval(job):
        return 'storyboard'
    if (
        _job_ui_status(job) == 'running'
        and _job_display_status(job) == 'attention'
    ):
        return 'stalled'
    return None


def _attention_title_identity(job: dict) -> str:
    spec = job.get('spec') if isinstance(job.get('spec'), dict) else {}
    result = job.get('result') if isinstance(job.get('result'), dict) else {}
    package = result.get('package') if isinstance(result.get('package'), dict) else {}
    for candidate in (
        result.get('title'),
        package.get('title'),
        spec.get('title'),
        spec.get('topic'),
    ):
        title = _plain_text(candidate)
        if title:
            return _plain_text(unicodedata.normalize('NFKC', title)).casefold()
    return ''


def _attention_channel_identity(job: dict) -> str:
    spec = job.get('spec') if isinstance(job.get('spec'), dict) else {}
    result = job.get('result') if isinstance(job.get('result'), dict) else {}
    youtube = result.get('youtube') if isinstance(result.get('youtube'), dict) else {}
    channel = result.get('channel') if isinstance(result.get('channel'), dict) else {}
    for candidate in (
        result.get('target_channel_id'),
        youtube.get('target_channel_id'),
        channel.get('id'),
        result.get('channel_id'),
        youtube.get('channel_id'),
        spec.get('target_channel_id'),
        spec.get('channel_id'),
    ):
        value = _plain_text(candidate)
        if value:
            return f'id:{value}'
    for candidate in (
        channel.get('title'),
        youtube.get('channel_title'),
        spec.get('target_channel_title'),
    ):
        value = _plain_text(candidate)
        if value:
            normalized = _plain_text(unicodedata.normalize('NFKC', value)).casefold()
            return f'title:{normalized}'
    return ''


def _attention_language_identity(job: dict) -> str:
    spec = job.get('spec') if isinstance(job.get('spec'), dict) else {}
    result = job.get('result') if isinstance(job.get('result'), dict) else {}
    value = spec.get('language') or result.get('language') or job.get('language')
    return _plain_text(value).casefold().replace('_', '-').split('-', 1)[0]


def _legacy_failure_signature(job: dict) -> tuple[str, ...] | None:
    """Identify old, unlinked retry-like failures without merging real videos."""
    if _job_ui_status(job) != 'failed' or _plain_text(job.get('parent_id')):
        return None
    spec = job.get('spec') if isinstance(job.get('spec'), dict) else {}
    topic = _plain_text(spec.get('topic')).casefold()
    if not topic:
        return None
    fields = (
        'duration_minutes', 'language', 'channel_id', 'mode', 'workflow',
        'content_style', 'pace', 'visual_mix', 'music', 'subtitles',
        'reference_url',
    )
    return (
        _plain_text(job.get('kind') or 'render').casefold(),
        topic,
        *(_plain_text(spec.get(field)).casefold() for field in fields),
    )


def _running_duplicate_signature(job: dict) -> tuple[str, ...] | None:
    """Conservatively identify concurrent duplicate clicks for display only."""
    if (
        _job_ui_status(job) != 'running'
        or _console_bucket(job) != 'running'
        or _plain_text(job.get('parent_id'))
    ):
        return None
    spec = job.get('spec') if isinstance(job.get('spec'), dict) else {}
    topic = _plain_text(spec.get('topic')).casefold()
    if not topic:
        return None
    fields = (
        'duration_minutes', 'language', 'channel_id', 'mode', 'workflow',
        'content_style', 'pace', 'visual_mix', 'music', 'subtitles',
        'reference_url',
    )
    return (
        _console_bucket(job),
        _plain_text(job.get('kind') or 'render').casefold(),
        topic,
        *(_plain_text(spec.get(field)).casefold() for field in fields),
    )


def _collapse_running_duplicates(jobs: list[dict]) -> list[dict]:
    """Group only exact, near-simultaneous root jobs without changing storage."""
    collapsed: list[dict] = []
    latest_by_signature: dict[tuple[str, ...], tuple[float, int]] = {}
    for job in jobs:
        signature = _running_duplicate_signature(job)
        created = _job_created_timestamp(job)
        previous = (
            latest_by_signature.get(signature)
            if signature and created is not None else None
        )
        if previous is not None:
            previous_created, previous_index = previous
            if abs(previous_created - created) <= RUNNING_DUPLICATE_GROUP_WINDOW_SECONDS:
                representative = dict(collapsed[previous_index])
                representative['_grouped_running_attempts'] = (
                    int(representative.get('_grouped_running_attempts') or 0) + 1
                )
                collapsed[previous_index] = representative
                continue
        collapsed.append(job)
        if signature and created is not None:
            latest_by_signature[signature] = (created, len(collapsed) - 1)
    return collapsed


def _attention_duplicate_signature(job: dict) -> tuple[str, ...] | None:
    """Identify same-title attention attempts without merging durable records."""
    action = _attention_action_category(job)
    title = _attention_title_identity(job)
    language = _attention_language_identity(job)
    if not action or not title or not language:
        return None
    return (
        title,
        action,
        _attention_channel_identity(job),
        language,
    )


def _collapse_attention_duplicates(jobs: list[dict]) -> list[dict]:
    """Show close, exact-title review attempts as one concise attention card."""
    collapsed: list[dict] = []
    latest_by_signature: dict[tuple[str, ...], tuple[float, int]] = {}
    for job in jobs:
        signature = _attention_duplicate_signature(job)
        created = _job_created_timestamp(job)
        previous = (
            latest_by_signature.get(signature)
            if signature and created is not None else None
        )
        if previous is not None:
            previous_created, previous_index = previous
            age = previous_created - created
            if 0 <= age <= ATTENTION_DUPLICATE_GROUP_WINDOW_SECONDS:
                representative = dict(collapsed[previous_index])
                representative['_grouped_attention_attempts'] = (
                    int(representative.get('_grouped_attention_attempts') or 0) + 1
                )
                collapsed[previous_index] = representative
                continue
        collapsed.append(job)
        if signature and created is not None:
            latest_by_signature[signature] = (created, len(collapsed) - 1)
    return collapsed


def _collapse_failure_duplicates(jobs: list[dict]) -> list[dict]:
    """Group retry siblings, plus close legacy failures with frozen input."""
    collapsed: list[dict] = []
    latest_by_parent: dict[str, int] = {}
    latest_by_signature: dict[tuple[str, ...], tuple[float, int]] = {}

    def group_into(index: int) -> None:
        representative = dict(collapsed[index])
        representative['_grouped_failure_attempts'] = (
            int(representative.get('_grouped_failure_attempts') or 0) + 1
        )
        collapsed[index] = representative

    for job in jobs:
        parent_id = _plain_text(job.get('parent_id'))
        if _job_ui_status(job) == 'failed' and parent_id:
            previous_parent_index = latest_by_parent.get(parent_id)
            if previous_parent_index is not None:
                group_into(previous_parent_index)
                continue
            collapsed.append(job)
            latest_by_parent[parent_id] = len(collapsed) - 1
            continue

        signature = _legacy_failure_signature(job)
        created = _job_created_timestamp(job)
        previous = (
            latest_by_signature.get(signature)
            if signature and created is not None else None
        )
        if previous is not None:
            previous_created, previous_index = previous
            age = previous_created - created
            if 0 <= age <= LEGACY_RETRY_GROUP_WINDOW_SECONDS:
                group_into(previous_index)
                continue
        collapsed.append(job)
        if signature and created is not None:
            latest_by_signature[signature] = (created, len(collapsed) - 1)
    return collapsed


def _collapse_retry_sources(
    jobs: list[dict],
    *,
    collapse_attention: bool = True,
) -> list[dict]:
    """Show only the newest visible step of each logical video workflow."""
    by_id = {str(job.get('task_id') or ''): job for job in jobs}
    jobs = [_with_publication_presentation(job, by_id.get) for job in jobs]
    # Manual publishers can predate source automation metadata. Propagate only
    # a verified child warning, leaving all durable job/result records intact.
    completed_sources = set()
    for publisher in jobs:
        parent_id = str(publisher.get('parent_id') or '')
        source = by_id.get(parent_id)
        if source and _publisher_matches_completed_output(publisher, source):
            automation = _job_result(source).get('youtube_automation')
            bound_id = automation.get('publish_task_id') if isinstance(automation, dict) else None
            if not bound_id or bound_id == publisher.get('task_id'):
                completed_sources.add(parent_id)
    jobs = [
        {**job, '_publication_complete': True} if job.get('task_id') in completed_sources else job
        for job in jobs
    ]
    warnings = {}
    for publisher in jobs:
        parent_id = str(publisher.get('parent_id') or '')
        source = by_id.get(parent_id)
        if source and _publisher_matches_source(publisher, source):
            automation = _job_result(source).get('youtube_automation')
            bound_id = automation.get('publish_task_id') if isinstance(automation, dict) else None
            if bound_id and bound_id != publisher.get('task_id'):
                continue
            if not bound_id and parent_id in completed_sources:
                continue
            status = _publication_status(publisher)
            if status in {'failed', 'blocked', 'uncertain'}:
                warnings[parent_id] = (
                    ('uncertain', '') if parent_id in warnings
                    else (status, publisher['task_id'])
                )
    jobs = [
        {**job, '_publication_status': warnings[job['task_id']][0],
         '_publication_task_id': warnings[job['task_id']][1]}
        if job.get('task_id') in warnings else job
        for job in jobs
    ]
    task_ids = set(by_id)
    # Publishing is a state of the finished video, not a second video card.
    # The worker persists successful YouTube metadata on the render source;
    # failed uploads remain owned by the YouTube center. In both terminal
    # cases keep the rich render parent so its preview is not lost.
    hidden_terminal_publishes = {
        str(job.get('task_id') or '')
        for job in jobs
        if str(job.get('kind') or '') == 'publish'
        and str(job.get('state') or '').upper() in {'FAILURE', 'SUCCESS'}
        and str(job.get('parent_id') or '') in task_ids
    }
    superseded_ids = {
        str(job.get('parent_id') or '')
        for job in jobs
        if str(job.get('task_id') or '') not in hidden_terminal_publishes
        if str(job.get('parent_id') or '') in task_ids
        and str(job.get('parent_id') or '') != str(job.get('task_id') or '')
    }
    visible = []
    for job in jobs:
        task_id = str(job.get('task_id') or '')
        child_id = _retry_child_task_id(job)
        if (
            _job_is_dormant_plan_retry(job)
            or task_id in hidden_terminal_publishes
            or task_id in superseded_ids
            or (child_id and child_id != task_id and child_id in task_ids)
        ):
            continue
        visible.append(job)
    collapsed = _collapse_failure_duplicates(visible)
    collapsed = _collapse_running_duplicates(collapsed)
    return (
        _collapse_attention_duplicates(collapsed)
        if collapse_attention else collapsed
    )


def _status_overview(counts: dict[str, int], *, active: str | None = None) -> str:
    current_bucket = (
        'create' if active is None
        else 'library' if active in {'library', 'ready', 'completed'}
        else 'attention' if active in {'attention', 'repair'}
        else active
    )
    create_current = ' aria-current="page"' if current_bucket == 'create' else ''
    links = [
        '<a class="status-filter create" data-status-filter="new" '
        f'href="/studio/create"{create_current}><span class="status-count" '
        'aria-hidden="true">＋</span><span class="status-name">Yeni video</span></a>'
    ]
    for key in CONSOLE_STATUS_ORDER:
        current = ' aria-current="page"' if key == current_bucket else ''
        links.append(
            f'<a class="status-filter {key}" data-status-filter="{key}" '
            f'href="/studio/history?status={key}"{current}>'
            f'<span class="status-count" data-status-count="{key}">{int(counts.get(key, 0))}</span>'
            f'<span class="status-name">{CONSOLE_STATUS_LABELS[key]}</span></a>'
        )
    return '<nav class="status-overview" aria-label="Video durumları">' + ''.join(links) + '</nav>'


def _history_archive(
    counts: dict[str, int],
    *,
    active: str | None = None,
) -> str:
    """Keep old drafts and technical records reachable without daily noise."""
    failed = int(counts.get('failed', 0))
    unreviewed = int(counts.get('unreviewed', 0))
    drafts = int(counts.get('drafts', 0))
    opened = ' open' if active in {'drafts', 'failed', 'unreviewed'} else ''
    drafts_current = ' aria-current="page"' if active == 'drafts' else ''
    failed_current = ' aria-current="page"' if active == 'failed' else ''
    unreviewed_current = ' aria-current="page"' if active == 'unreviewed' else ''
    return (
        f'<details class="archive-details"{opened}><summary>'
        '<span class="archive-label"><b>Eski işler</b>'
        '<span class="tiny">Günlük görünümden ayrı tutulur</span></span>'
        '</summary><div class="archive-body">'
        f'<a href="/studio/history?status=drafts"{drafts_current}>'
        f'Eski storyboard taslakları <b>{drafts}</b></a>'
        f'<a href="/studio/history?status=failed"{failed_current}>'
        f'Başarısız denemeler <b>{failed}</b></a>'
        f'<a href="/studio/history?status=unreviewed"{unreviewed_current}>'
        f'Eski kalite kayıtları <b>{unreviewed}</b></a>'
        '</div></details>'
    )


def _nav(active: str) -> str:
    if active in {'providers', 'growth', 'create', 'voices'}:
        active = 'settings'
    elif active == 'review':
        active = 'history'
    primary_links = [
        ('studio', '/studio', 'Genel bakış'),
        ('plan', '/studio/plan', 'Yayın planı'),
        ('history', '/studio/history?status=library', 'Videolar'),
        ('youtube', '/studio/youtube', 'Kanallar'),
        ('analytics', '/studio/analytics', 'Performans'),
        ('costs', '/studio/costs', 'Maliyet'),
        ('settings', '/studio/settings', 'Ayarlar'),
    ]
    items = ''.join(
        f'<a class="{"active" if key == active else ""}" href="{url}"'
        f'{" aria-current=page" if key == active else ""}>{label}</a>'
        for key, url, label in primary_links
    )
    return f'<header class="top"><a class="brand" href="/studio">YouTube Studio</a><nav class="nav" aria-label="Ana menü">{items}</nav></header>'


def _shell(body: str, *, active: str = 'studio', title: str = 'YouTube Studio V2', script: str = '') -> HTMLResponse:
    from app.services.studio_console_theme import CSS as console_css
    return HTMLResponse(
        '<!doctype html><html lang="tr"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        f'<meta name="theme-color" content="#f4f6f8"><title>{escape(title)}</title>'
        f'<style>{BASE_CSS}\n{console_css}</style></head><body><a class="skip-link" href="#main-content">İçeriğe geç</a>'
        f'<div class="wrap">{_nav(active)}<main id="main-content" tabindex="-1">{body}</main></div>{script}</body></html>',
        headers={'Cache-Control': 'private, no-store', 'Referrer-Policy': 'same-origin',
                 'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY'},
    )


def _normalize_spec(
    topic: str,
    duration_minutes: float,
    language: str,
    channel_id: str,
    mode: str,
    workflow: str,
    content_style: str,
    pace: str,
    visual_mix: str,
    music: str,
    subtitles: str,
    reference_url: str,
    format: str = 'landscape',
    publish_after_render: bool = False,
) -> dict:
    try:
        language = normalize_pipeline_language(language)
    except UnsupportedLanguageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    mode = mode if mode in {'preview', 'production'} else 'preview'
    workflow = workflow if workflow in {'auto', 'storyboard'} else 'auto'
    content_style = content_style if content_style in STYLE_LABELS else 'documentary'
    pace = pace if pace in PACE_LABELS else 'balanced'
    visual_mix = visual_mix if visual_mix in {'real_first', 'balanced', 'ai_first'} else 'balanced'
    music = music if music in {'auto', 'off'} else 'off'
    subtitles = subtitles if subtitles in {'sidecar', 'off'} else 'sidecar'
    if mode == 'preview':
        music = 'off'
    return {
        'topic': topic.strip(),
        'duration_minutes': float(duration_minutes),
        'language': language,
        'channel_id': channel_id.strip() or None,
        'mode': mode,
        'workflow': workflow,
        'content_style': content_style,
        'pace': pace,
        'visual_mix': visual_mix,
        'music': music,
        'subtitles': subtitles,
        'reference_url': reference_url.strip() or None,
        'quality_threshold': 84 if mode == 'production' else 86,
        'format': format if format in {'shorts', 'landscape'} else 'landscape',
        'publish_after_render': publish_after_render is True and mode == 'production',
    }


def _production_publish_options(
    mode: str,
    publish_after_render: bool,
    channel_id: str,
    language: str,
) -> dict:
    if mode != 'production' or publish_after_render is not True:
        return {'publish_after_render': False}
    channel_id = str(channel_id or '').strip()
    if not channel_id:
        raise HTTPException(status_code=422, detail='Otomatik yükleme için bir YouTube kanalı seç')
    try:
        status = connection_status()
        connections = status.get('connections') or []
        channel = next((
            item for item in connections
            if isinstance(item, dict) and str(item.get('id') or '') == channel_id
        ), None)
        profile = select_channel_profile(
            {'spec': {'language': language, 'production_channel_id': channel_id}},
            list_channel_profiles(),
            connected_channel_ids={str((channel or {}).get('id') or '')},
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail='Kanal ayarları şu anda doğrulanamıyor') from exc
    if not channel or not channel.get('connection_id'):
        raise HTTPException(status_code=409, detail='Seçilen YouTube kanalını yeniden bağla')
    if not profile:
        raise HTTPException(
            status_code=422,
            detail='Kanal ayarlarında otomatik yüklemeyi ve videonun dilini etkinleştir',
        )
    return {
        'publish_after_render': True,
        'production_channel_id': channel_id,
        'production_connection_id': str(channel['connection_id']),
        'production_profile_revision': str(profile.get('profile_revision') or ''),
    }


def _production_channel_choices(authenticated: bool) -> str:
    choices = ['<option value="">Kanal seçilmedi</option>']
    if authenticated:
        try:
            connections = connection_status().get('connections') or []
        except Exception:
            connections = []
        for channel in connections:
            if not isinstance(channel, dict) or not channel.get('id'):
                continue
            channel_id = str(channel['id'])
            title = _safe_ui_text(channel.get('title') or 'YouTube kanalı')
            choices.append(
                f'<option value="{escape(channel_id, quote=True)}">'
                f'{escape(title)} · {escape(channel_id[-8:])}</option>'
            )
    return ''.join(choices)


def _sync_job(task_id: str) -> dict:
    record = get_job(task_id) or {'task_id': task_id, 'spec': {}, 'state': 'PENDING', 'progress': 0}
    if record.get('state') == 'CANCELLED':
        return record  # An old Celery result is not authority to undo owner cancellation.
    spec = record.get('spec') if isinstance(record.get('spec'), dict) else {}
    framecase_render = (record.get('kind', 'render') == 'render'
        and spec.get('framecase_animation') is True)
    if (framecase_render and type(record.get('framecase_resume_attempt')) is int
            and record['framecase_resume_attempt'] > 0):
        # Continuations have separate Celery IDs and persist into the original
        # source. Its first delivery's backend result is permanently stale.
        return record
    task = AsyncResult(task_id, app=celery)
    state = task.state

    if state == 'FAILURE':
        error = str(task.result)
        if (
            str(record.get('state') or '').upper() != 'FAILURE'
            or str(record.get('error') or '') != error
        ):
            record = mark_failure(task_id, error)
    elif state == 'SUCCESS':
        result = task.result if isinstance(task.result, dict) else {'result': str(task.result)}
        if framecase_render and result.get('status') in {'stopped', 'already_running'}:
            return record  # Completing the delivery is not completing a film.
        if result.get('task_id') is not None and result['task_id'] != task_id:
            return record
        if result.get('source_task_id') is not None:
            spec = record.get('spec') if isinstance(record.get('spec'), dict) else {}
            if (
                record.get('kind') != 'publish'
                or result['source_task_id'] != spec.get('source_task_id')
                or result['source_task_id'] != record.get('parent_id')
            ):
                return record
        persisted_result = record.get('result')
        comparable_result = result
        if record.get('kind', 'render') == 'render' and isinstance(persisted_result, dict):
            publisher_fields = {'youtube', 'youtube_automation'}
            persisted_result = {k: v for k, v in persisted_result.items() if k not in publisher_fields}
            comparable_result = {k: v for k, v in result.items() if k not in publisher_fields}
        target_state = (
            'AWAITING_APPROVAL'
            if result.get('status') == 'plan_ready' else 'SUCCESS'
        )
        target_stage = (
            'awaiting_approval'
            if target_state == 'AWAITING_APPROVAL' else 'complete'
        )
        target_message = (
            'Storyboard onay bekliyor.'
            if target_state == 'AWAITING_APPROVAL' else 'Video hazır.'
        )
        if any((
            str(record.get('state') or '') != target_state,
            str(record.get('stage') or '') != target_stage,
            _job_progress(record) != 100,
            persisted_result != comparable_result,
            record.get('error') is not None,
            str(record.get('message') or '') != target_message,
        )):
            record = mark_success(task_id, result, state=target_state)
    elif (
        isinstance(task.info, dict)
        and record.get('state') not in {'SUCCESS', 'AWAITING_APPROVAL'}
    ):
        # The worker persists success before routing a publish and returning to
        # Celery. Its previous progress snapshot cannot downgrade that result.
        next_fields = {
            'state': state,
            'stage': task.info.get('stage') or record.get('stage'),
            'progress': (
                task.info.get('progress')
                if task.info.get('progress') is not None
                else record.get('progress', 0)
            ),
            'message': task.info.get('message') or record.get('message'),
        }
        if any(record.get(key) != value for key, value in next_fields.items()):
            # set_stage owns durable progress. A Celery snapshot may already
            # be stale after this GET, so it is presentation-only and must not
            # overwrite a concurrent success or publisher update.
            record = {**record, **next_fields}
    if str(record.get('state') or state) == 'FAILURE':
        try:
            repair_state = sync_repair_checkpoint_state(task_id)
        except Exception:
            # A dashboard probe must not expose a possibly stale repair action
            # when Redis cannot prove the single-use checkpoint still exists.
            repair_state = {
                'repair_available': False,
                'repair_claimed': bool(record.get('repair_claimed')),
            }
        refreshed = get_job(task_id)
        if refreshed:
            record = refreshed
        record = {**record, **repair_state}
    return record


def _refresh_active_jobs(jobs: list[dict]) -> list[dict]:
    refreshed = []
    for job in jobs:
        task_id = str(job.get('task_id') or '')
        state = str(job.get('state') or '').upper()
        should_refresh = (
            _job_ui_status(job) == 'running' or state == 'FAILURE'
        ) and not _retry_claimed(job) and not _job_is_stale_running(job)
        if task_id and should_refresh:
            synced = _sync_job(task_id)
            grouped_fields = {
                key: job[key]
                for key in (
                    '_grouped_failure_attempts',
                    '_grouped_running_attempts',
                    '_grouped_attention_attempts',
                )
                if job.get(key)
            }
            if grouped_fields:
                synced = {**synced, **grouped_fields}
            refreshed.append(synced)
        else:
            refreshed.append(job)
    return refreshed


@router.get('/studio/api/session')
def studio_session(studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    """Same-origin cookie check only; no grant, provider, Redis or OAuth action."""
    if not _valid_token(studio_token):
        return JSONResponse({'authenticated': False}, status_code=401,
                            headers={'Cache-Control': 'private, no-store'})
    return JSONResponse({'authenticated': True}, headers={'Cache-Control': 'private, no-store'})


@router.get('/studio', response_class=HTMLResponse)
def studio_home(studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    if not _valid_token(studio_token):
        return RedirectResponse('/studio/access', status_code=303, headers={'Cache-Control': 'private, no-store'})
    jobs_available = True
    try:
        stored_jobs = list_jobs(HISTORY_SCAN_LIMIT)
        if not isinstance(stored_jobs, list):
            raise ValueError('jobs_unavailable')
    except Exception:
        stored_jobs, jobs_available = [], False
    metrics = _dashboard_metrics(stored_jobs)
    jobs = [_with_youtube_metrics(job, metrics) for job in _collapse_retry_sources(stored_jobs)]
    counts = _console_counts(jobs)
    previews = _preview_jobs(stored_jobs)
    channels = metrics.get('channels') or []
    states = {row.get('production_status') for row in channels}
    if not jobs_available:
        heading, detail = 'Durum bilgisi alınamıyor', 'Video kayıtları şu anda okunamıyor. Sayfayı biraz sonra yenileyebilirsin.'
    elif counts.get('running', 0):
        heading, detail = 'Üretim sürüyor', 'Devam eden videoların aşamaları aşağıda.'
    elif states & {'paused', 'retry_uncertain'}:
        heading, detail = 'Bir işlem gerekiyor', 'Nedeni ilgili kanalda ve kontrol bekleyen videolarda görebilirsin.'
    elif states & {'daily_wait', 'planning_wait'}:
        heading, detail = 'Kanallar kendi takviminde', 'Sınırına ulaşan kanal yeni günü bekler. Sıradaki üretim zamanı kanal kartında.'
    elif 'scheduled' in states:
        heading, detail = 'Bir sonraki üretim planlandı', 'Başlama zamanı kanalda görünür. Üretim öncesinde bütçe ve bağlantılar yeniden kontrol edilir.'
    elif states and states <= {'disabled', 'exhausted'}:
        heading, detail = 'Yeni üretim bekleniyor', 'Kanal ayarlarından otomatik üretimi ve sıradaki konuları görebilirsin.'
    else:
        heading, detail = 'Otomasyon durumu doğrulanıyor', 'Kanal ve video kayıtları hazır oldukça burada gösterilir.'
    cards = []
    for key, label, note in (('running', 'Üretimde', 'Devam eden işler'),
                             ('attention', 'Kontrol bekleyen', 'İlgilenilmesi gerekenler'),
                             ('previews', 'Önizlemeler', 'Video ve ses taslaklarını aç')):
        count = str(len(previews) if key == 'previews' else counts.get(key, 0)) if jobs_available else '—'
        cards.append(f'<a class="overview-count" href="/studio/history?status={key}"><span>{label}<small>{note}</small></span><b>{count}</b></a>')
    recent = []
    for job in jobs:
        task_id = _canonical_task_id(job.get('task_id'))
        if not task_id or _console_bucket(job) not in {'running', 'attention', 'library'}:
            continue
        title = escape(_ellipsize(_safe_ui_text(_job_title(job)), 100))
        meta = ' · '.join(filter(None, (_job_channel(job), _job_format(job), _job_date(job))))
        recent.append(f'<a class="overview-video" href="/studio/job/{task_id}"><div><div class="overview-video-title">{title}</div><div class="overview-video-meta">{escape(meta)}</div></div><div class="delivery-badges">{_delivery_badges(job)}</div></a>')
        if len(recent) == 5:
            break
    empty = 'Henüz gösterilecek video yok. İlk videonu oluşturabilir veya kanal ayarlarına bakabilirsin.' if jobs_available else 'Video listesi şu anda okunamıyor.'
    body = (
        '<div class="hero overview-hero"><div class="hero-copy">'
        '<h1>Genel bakış</h1><p class="muted">Kanalların bugün ne yapıyor?</p></div>'
        '<a class="btn" href="/studio/plan">Yayın planını aç</a></div>'
        '<div class="overview-top"><section class="automation-card" aria-label="Otomasyon durumu">'
        '<span class="section-kicker">OTOMASYON</span><h2>' + heading + '</h2><p>' + detail + '</p>'
        + _operations_status() + '</section>'
        + '<section class="card overview-shortcuts"><h2>Hızlı erişim</h2><div class="settings-links">'
        '<a href="/studio/plan"><b>Sıradaki videolar</b><span>Yayın sırasını ve konuları düzenle →</span></a>'
        '<a href="/studio/settings"><b>Bağlantılar ve bakiye</b><span>Kie.ai, yapay zekâlar ve giriş →</span></a>'
        '</div></section></div><nav class="overview-counts" aria-label="Video durumları">'
        + ''.join(cards) + '</nav><details class="funding-details"><summary>Bütçe ve harcama ayrıntıları</summary>'
        + _production_budget_notice() + '</details><section class="overview-section">' + _metrics_header(metrics)
        + '<div id="channel-overview-host">' + _channel_overview(channels) + '</div></section>'
        '<section class="overview-section"><div class="overview-heading"><h2>Son üretimler</h2>'
        '<a href="/studio/history?status=library">Tüm videolar →</a></div><div class="overview-recent">'
        + (''.join(recent) or '<div class="empty">' + empty + '</div>') + '</div></section>'
        '<p class="overview-footer">Yayın ve performans bilgileri son doğrulanan kayıtları gösterir. Kanal bazında ayrıntılar Kanallar sayfasında.</p>'
    )
    return _shell(body, title='Kontrol paneli · Studio', script=_metrics_script())


@router.get('/studio/create', response_class=HTMLResponse)
def studio_create(studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    authenticated = _valid_token(studio_token)
    if not authenticated:
        return RedirectResponse('/studio/access', status_code=303, headers={'Cache-Control': 'private, no-store'})
    selected_voice = escape(get_selected_voice().get('name') or 'Ses seçilmedi')
    service_states = _service_statuses()
    service_by_name = dict(service_states)
    runway_ready = service_by_name.get('Runway') is True
    optional_missing = {
        name for name, ok in service_states
        if not ok
        and runway_ready
        and name in OPTIONAL_VIDEO_GENERATION_SERVICES
    }
    required_missing = [
        name for name, ok in service_states
        if not ok and name not in optional_missing
    ]
    service_rows = []
    for name, ok in service_states:
        dot_class = 'green' if ok else 'amber' if name in optional_missing else 'red'
        state_label = 'Hazır' if ok else 'İsteğe bağlı' if name in optional_missing else 'Eksik'
        service_rows.append(
            f'<div class="service"><span class="dot {dot_class}" aria-hidden="true"></span>'
            f'{escape(name)}<span class="tiny" style="margin-left:auto">{state_label}</span></div>'
        )
    services = ''.join(service_rows)
    ready_services = sum(1 for _, ok in service_states if ok)
    if required_missing:
        health_label = (
            f'{required_missing[0]} ayarı eksik'
            if len(required_missing) == 1
            else f'Eksik servisler: {", ".join(required_missing)}'
        )
    elif optional_missing:
        optional_names = ', '.join(
            name for name, _ok in service_states if name in optional_missing
        )
        health_label = f'{optional_names} isteğe bağlı'
    else:
        health_label = 'Tüm bağlantı ayarları hazır'
    stored_jobs = list_jobs(HISTORY_SCAN_LIMIT) if authenticated else []
    metrics = _dashboard_metrics(stored_jobs) if authenticated else {}
    jobs = [_with_youtube_metrics(job, metrics) for job in _collapse_retry_sources(stored_jobs)]
    channel_dashboard = ''
    console_counts = _console_counts(jobs)
    overview = _status_overview(console_counts) if authenticated else ''
    archive = _history_archive(_archive_counts(jobs)) if authenticated else ''
    token_field = (
        '' if authenticated else
        '<label class="field" for="studio-token">Studio güvenlik anahtarı</label><input id="studio-token" name="token" type="password" autocomplete="off" required placeholder="Güvenli anahtarı gir">'
    )
    channel_choices = _production_channel_choices(authenticated)
    budget_notice = _production_budget_notice() if authenticated else ''

    body = f'''
<div class="hero"><div class="hero-copy"><div class="eyebrow">STUDIO</div><h1>Yeni video oluştur</h1><div class="muted">Konuyu yaz; üretim ve güvenli yükleme adımlarını Studio yönetsin.</div></div></div>
{budget_notice}
{overview}
{channel_dashboard}
<div class="studio-primary">
<form action="/studio/start" method="post" class="card create-card" id="studio-form">
<span class="section-kicker">YENİ VİDEO</span><h2>Ne anlatalım?</h2>
<div class="choice-grid">
<div class="choice"><input id="mode-preview" name="mode" value="preview" type="radio" checked><label for="mode-preview"><b>Hızlı test</b><span>30 saniyelik kısa önizleme.</span></label></div>
<div class="choice"><input id="mode-production" name="mode" value="production" type="radio"><label for="mode-production"><b>Yayın kalitesi</b><span>Tam üretim ve kalite kontrolü.</span></label></div>
</div>
<label class="field" for="topic">Video konusu</label>
<span class="field-hint">Bir cümle yeterli. Örnek: Telefon neden yastık altında ısınır?</span>
<textarea class="topic-input" id="topic" name="topic" required placeholder="Konuyu bir cümleyle yaz"></textarea>
<label class="field" for="production-channel">YouTube kanalı</label><select id="production-channel" name="production_channel_id">{channel_choices}</select>
<label class="field"><input id="publish-after-render" name="publish_after_render" type="checkbox" value="1" disabled> Kalite kontrolü geçince seçili kanala otomatik yükle</label>
<span class="field-hint">Yayın kalitesinde kullanılabilir. Kanalın yayın ayarları uygulanır; ilk yükleme gizlidir.</span>
<details class="control-details"><summary><span>Ayarlar</span><span class="tiny">İsteğe bağlı</span></summary><div class="control-body">
<label class="field" for="video-format">Video biçimi</label><select id="video-format" name="format"><option value="landscape">Yatay video</option><option value="shorts">Dikey Shorts</option></select>
<div class="grid2"><div><label class="field" for="duration">Süre</label><select id="duration" name="duration_minutes"><option value="0.5" selected>30 saniye</option><option value="1">1 dakika</option><option value="3">3 dakika</option><option value="5">5 dakika</option><option value="8">8 dakika</option><option value="10">10 dakika</option></select></div><div><label class="field" for="language">Dil</label><select id="language" name="language"><option value="tr" selected>Türkçe</option><option value="en">English</option><option value="de">Deutsch</option><option value="es">Español</option><option value="ar">العربية</option></select></div></div>
<div class="guidance"><b>İyi sonuç için ayrıntı eklemek istersen</b><p class="tiny">Tek bir gündelik sorun, tek bir şaşırtıcı neden, aynı kişi veya nesne, aynı mekân ve görünür bir sonuç tarif et. Bunları yazmak zorunda değilsin; sistem kısa konu cümleni otomatik olarak yönetmen planına dönüştürür.</p></div>
<div class="grid2"><div><label class="field" for="content-style">İçerik tarzı</label><select id="content-style" name="content_style"><option value="documentary">Belgesel</option><option value="technology" selected>Teknoloji</option><option value="story">Hikâye</option><option value="cinematic">Sinematik</option><option value="explainer">Açıklayıcı</option></select></div><div><label class="field" for="pace">Kurgu temposu</label><select id="pace" name="pace"><option value="calm">Sakin</option><option value="balanced" selected>Dengeli</option><option value="dynamic">Dinamik</option></select></div></div>
<div class="grid2"><div><label class="field" for="visual-mix">Görsel karışımı</label><select id="visual-mix" name="visual_mix"><option value="real_first">Gerçek görüntü ağırlıklı</option><option value="balanced" selected>Dengeli: B-roll + AI</option><option value="ai_first">Özgün AI ağırlıklı</option></select></div><div><label class="field" for="workflow">Akış</label><select id="workflow" name="workflow"><option value="auto" selected>Otomatik tamamla</option><option value="storyboard">Önce storyboard göster</option></select></div></div>
<div class="grid2"><div><label class="field" for="music">Arka plan müziği</label><select id="music" name="music"><option value="off">Kapalı</option><option value="auto" selected>Uygunsa otomatik</option></select></div><div><label class="field" for="subtitles">Altyazı</label><select id="subtitles" name="subtitles"><option value="sidecar" selected>Ayrı SRT üret</option><option value="off">Üretme</option></select></div></div>
<label class="field" for="reference-url">Referans video / kanal bağlantısı <span class="tiny">(yalnızca yapı ve ritim analizi)</span></label><input id="reference-url" name="reference_url" type="url" placeholder="YouTube videosu veya kanal bağlantısı">
<label class="field" for="channel-id">Kanal etiketi <span class="tiny">(opsiyonel)</span></label><input id="channel-id" name="channel_id" type="text" maxlength="120" placeholder="teknoloji-tr-01">
<div class="actions"><a class="btn secondary small" href="/voice-audition">Anlatıcı: {selected_voice}</a></div>
</div></details>
{token_field}
<p class="private-note"><b>Güvenli yayın:</b> YouTube yüklemeleri önce gizli oluşturulur.</p>
<button class="block" type="submit">Videoyu oluştur</button>
</form>
<details class="system-details console-details"><summary><span class="status-summary"><span class="health-dot {"red" if required_missing else "green"}" aria-hidden="true"></span>Sistem durumu</span><span class="tiny">{ready_services}/{len(service_states)}</span></summary><div class="system-body"><p class="tiny">{health_label}</p><div class="status-grid">{services}</div></div></details>
</div>
{archive}
'''
    script = r'''<script>
const preview=document.getElementById('mode-preview'),production=document.getElementById('mode-production'),duration=document.getElementById('duration');
const publishAfter=document.getElementById('publish-after-render');
function setDefaults(){publishAfter.disabled=!production.checked;if(production.checked){duration.value=document.getElementById('video-format').value==='shorts'?'0.5':'5';document.querySelector('[name=workflow]').value='auto';document.querySelector('[name=music]').value='auto';}else{publishAfter.checked=false;duration.value='0.5';document.querySelector('[name=workflow]').value='auto';document.querySelector('[name=music]').value='off';}}
preview.addEventListener('change',setDefaults);production.addEventListener('change',setDefaults);
document.getElementById('video-format').addEventListener('change',()=>{if(document.getElementById('video-format').value==='shorts'){duration.value='0.5';}});
</script>'''
    return _shell(body, active='create', title='Video oluştur · Studio', script=script)


def _job_row(job: dict) -> str:
    display_status = _job_display_status(job)
    status_message = _job_status_message(job)
    try:
        grouped_running = max(0, int(job.get('_grouped_running_attempts') or 0))
    except (TypeError, ValueError):
        grouped_running = 0
    if grouped_running:
        status_message += (
            f' {grouped_running + 1} eş üretim tek kartta gösteriliyor.'
        )
    try:
        grouped_attention = max(0, int(job.get('_grouped_attention_attempts') or 0))
    except (TypeError, ValueError):
        grouped_attention = 0
    if grouped_attention:
        status_message += (
            f' {grouped_attention + 1} benzer deneme tek kartta toplandı.'
        )
    raw_title = _job_title(job)
    title = escape(raw_title)
    duration = _job_duration(job)
    date = _job_date(job)
    channel = _job_channel(job)
    profile = _job_profile(job)
    if channel and profile and channel.casefold() != profile.casefold():
        target = f'{channel} / {profile}'
    else:
        target = channel or profile
    raw_updated = str(job.get('updated_at') or job.get('created_at') or '')
    metadata = []
    if target:
        metadata.append(f'<span>{escape(target)}</span>')
    if duration:
        metadata.append(f'<span>{escape(duration)}</span>')
    metadata.extend(f'<span>{escape(value)}</span>' for value in (_job_format(job), _job_language(job)))
    if date:
        metadata.append(
            f'<time datetime="{escape(raw_updated, quote=True)}">{escape(date)}</time>'
        )
    meta_html = ''.join(metadata)
    details = _job_details(job)
    return (
        f'<article class="job" data-status="{display_status}" '
        f'aria-label="{title}: {UI_STATUS_LABELS[display_status]}"><div class="job-main">'
        f'<div class="job-title">{title}</div><div class="job-status">{escape(status_message)}</div>'
        f'<div class="job-meta" aria-label="Video bilgileri">{meta_html}</div></div>'
        f'<div class="job-side"><div class="delivery-badges" data-delivery-task="{escape(str(job.get("task_id") or ""), quote=True)}">{_delivery_badges(job)}</div>'
        f'{_job_primary_action(job)}</div>{details}</article>'
    )


@router.post('/studio/start')
def studio_start(
    token: str = Form(default=''),
    topic: str = Form(...),
    duration_minutes: float = Form(...),
    language: str = Form('tr'),
    channel_id: str = Form(''),
    mode: str = Form('preview'),
    workflow: str = Form('auto'),
    content_style: str = Form('documentary'),
    pace: str = Form('balanced'),
    visual_mix: str = Form('balanced'),
    music: str = Form('off'),
    subtitles: str = Form('sidecar'),
    reference_url: str = Form(''),
    format: str = Form('landscape'),
    publish_after_render: str = Form(''),
    production_channel_id: str = Form(''),
    studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
):
    credential = studio_token if _valid_token(studio_token) else token
    if not _valid_token(credential):
        raise HTTPException(status_code=401, detail='Geçersiz FACTORY_API_TOKEN')
    if not topic.strip() or not (0.5 <= duration_minutes <= 30):
        raise HTTPException(status_code=400, detail='Konu veya süre geçersiz')

    spec = _normalize_spec(
        topic, duration_minutes, language, channel_id, mode, workflow,
        content_style, pace, visual_mix, music, subtitles, reference_url,
        format, publish_after_render == '1',
    )
    spec.update(_production_publish_options(
        spec['mode'], spec['publish_after_render'], production_channel_id,
        spec['language'],
    ))
    options = {key: value for key, value in spec.items() if key not in {'topic', 'duration_minutes', 'language', 'channel_id'}}
    if workflow == 'storyboard':
        task = plan_video_pipeline.delay(topic, duration_minutes, language, channel_id.strip() or None, options)
        kind = 'plan'
    else:
        task = run_video_pipeline.delay(topic, duration_minutes, language, channel_id.strip() or None, options, None)
        kind = 'render'
    create_job(task.id, spec, kind=kind)
    response = RedirectResponse(f'/studio/job/{task.id}', status_code=303)
    response.set_cookie(COOKIE_NAME, credential, max_age=60 * 60 * 24 * 30, httponly=True, secure=True, samesite='lax')
    return response


@router.get('/studio/job/{task_id}', response_class=HTMLResponse)
def studio_job(task_id: str, studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    _require_auth(studio_token)
    record = get_job(task_id) or {
        'task_id': task_id,
        'spec': {},
        'state': 'PENDING',
        'stage': 'queued',
        'progress': 0,
    }
    record = _with_publication_presentation(record, get_job, upload_lookup=get_upload_record)
    metrics = _dashboard_metrics([record])
    record = _with_youtube_metrics(record, metrics)
    retry_presentation = _terminal_retry_presentation(record, get_job, upload_lookup=get_upload_record)
    display_title = escape(_job_title(record))
    status = retry_presentation['ui_status'] if retry_presentation else _job_ui_status(record)
    display_status = retry_presentation['display_status'] if retry_presentation else _job_display_status(record)
    bucket = _console_bucket(record)
    back_status = (
        'drafts'
        if _job_is_old_storyboard(record)
        else 'unreviewed' if display_status == 'unreviewed'
        else bucket if bucket in CONSOLE_STATUS_ORDER else status
    )
    progress = _job_progress(record)
    stage_code = str(record.get('failure_stage') or record.get('stage') or 'queued')
    stage_label = escape(STAGE_LABELS.get(stage_code, stage_code))
    message = _review_reason(record)
    primary_action = _job_primary_action(record, small=False)
    if retry_presentation:
        stage_label = escape(retry_presentation['stage_label'])
        message = 'Bu sayfa önceki denemenin kaydıdır. ' + retry_presentation['message']
        primary_action = (
            '<a class="btn secondary" href="/studio/job/'
            f'{retry_presentation["task_id"]}">Güncel sonucu aç</a>'
        )
        if record.get('quality_held') is True:
            primary_action += '<a class="btn secondary" href="/studio">Üretim planını aç</a>'
    initial_error = _safe_ui_text(record.get('error'))
    error_hidden = '' if initial_error else ' hidden'
    progress_hidden = '' if status == 'running' else ' hidden'
    media_panel = _job_media_panel(record)
    media_hidden = '' if media_panel else ' hidden'
    delivery = _video_delivery(record)
    badge_label = delivery['label'] if delivery['label'] and not retry_presentation and not (delivery['key'] == 'rendered' and display_status in {'attention', 'unreviewed'}) else UI_STATUS_LABELS[display_status]
    performance = _video_performance(record) if record.get('state') == 'SUCCESS' and not retry_presentation else ''
    body = f'''
<div class="hero"><div class="hero-copy"><div class="eyebrow">Üretim durumu</div><h1>{display_title}</h1><div class="muted">Bu denemenin sonucu, varsa önizlemesi ve sonraki adım.</div></div></div>
<article class="card job-panel" id="job-card" data-status="{display_status}">
<div class="job-panel-head"><div class="stage" id="stage">{stage_label}{f' · %{progress}' if status == 'running' else ''}</div><span class="state {display_status}" id="state-label">{escape(badge_label)}</span></div>
{_video_identity(record)}
<div class="job-status" id="status-message" role="status" aria-live="polite" aria-atomic="true">{escape(message)}</div>
{_metrics_header(metrics) if performance else ''}{performance}
<div class="progress" id="progress" role="progressbar" aria-label="Üretim ilerlemesi" aria-valuemin="0" aria-valuemax="100" aria-valuenow="{progress}" style="margin:14px 0"{progress_hidden}><div class="bar" id="bar" style="width:{progress}%"></div></div>
<div class="result-media-host" id="result-media"{media_hidden}>{media_panel}</div>
<details class="review-options"{' open' if status not in {'failed', 'repair'} or retry_presentation else ''}><summary>Sonraki adım ve seçenekler</summary><div class="result-action" id="result">{primary_action}</div></details>
<div id="qa-workprint-host"{' hidden' if _review_media(record) else ''}>{_qa_workprint_banner(record)}</div>
<details class="technical-details"><summary>Teknik ayrıntılar</summary><div class="technical-body"><div><b>İş kimliği</b><br><code>{escape(task_id)}</code></div><div><b>Aşama kodu</b><br><code id="technical-stage">{escape(stage_code)}</code></div><div id="technical-error-row"{error_hidden}><b>Hata kaydı</b><br><code id="technical-error">{escape(initial_error)}</code></div>{_visual_diagnostics_link(record)}</div></details>
</article>
<nav class="back-links" aria-label="Geri dön"><a href="/studio/history?status={back_status}">Video listesine dön</a><a href="/studio/create">Yeni video oluştur</a></nav>
'''
    script = r'''<script>
const taskId=__TASK_ID__;let timer=null,ownerPreview=null;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels={running:'Devam ediyor',attention:'Dikkat gerekiyor',ready:'Hazır',repair:'Onarım gerekli',completed:'Tamamlandı',failed:'Başarısız',unreviewed:'Kalite onayı yok',held:'Deneme saklandı'};
function safeExternal(value){const text=String(value||'').trim();if(!/^https?:\/\//i.test(text))return '';try{const u=new URL(text);return ['http:','https:'].includes(u.protocol)?u.href:''}catch(_){return ''}}
function linkAction(href,label,kind='secondary',external=false){return `<a class="btn ${kind}" ${external?'target="_blank" rel="noopener noreferrer" ':''}href="${esc(href)}">${esc(label)}</a>`}
function retryAction(label,kind){return `<form method="post" action="/studio/retry/${encodeURIComponent(taskId)}"><button class="btn ${kind}" type="submit">${esc(label)}</button></form>`}
function setAction(signature,html){const out=document.getElementById('result');if(out.dataset.actionSignature===signature)return;out.innerHTML=html;out.dataset.actionSignature=signature}
function mediaMarkup(video,captions,note){const captionAction=captions?`<a class="btn secondary small" target="_blank" rel="noopener noreferrer" download href="${esc(captions)}">Altyazıyı indir (.srt)</a>`:'';return `<section class="result-media" aria-labelledby="result-media-title"><div class="result-media-head"><h2 id="result-media-title">Video önizleme</h2><span class="badge">Final dosya</span></div><div class="result-video-frame"><video class="result-video" controls playsinline preload="metadata" src="${esc(video)}">Tarayıcın video oynatmayı desteklemiyor.</video></div><div class="media-actions"><a class="btn secondary small" target="_blank" rel="noopener noreferrer" download href="${esc(video)}">Videoyu indir</a>${captionAction}</div><p class="media-note">${esc(note)}</p></section>`}
function setMedia(result,note='Kalite onaylanana kadar YouTube yüklemesi gizli kalır.'){const out=document.getElementById('result-media');const expected=`/studio/job/${encodeURIComponent(taskId)}/preview-media`;if(ownerPreview&&ownerPreview.url===expected&&['audio','video'].includes(ownerPreview.kind)){const current=out.querySelector('video,audio');if(!current||current.getAttribute('src')!==expected){const kind=ownerPreview.kind;out.innerHTML=`<section class="result-media"><div class="result-media-head"><h2>${esc(ownerPreview.label)}</h2></div><div class="review-preview ${kind}"><${kind} controls playsinline preload="metadata" src="${esc(expected)}"></${kind}></div><p class="media-note">${esc(ownerPreview.note)}</p></section>`}out.hidden=false;return}const x=result||{},video=safeExternal(x.download_url||x.video_url),captions=safeExternal(x.caption_url||x.captions_url||x.subtitle_url),current=out.querySelector('video');if(!video){out.replaceChildren();out.hidden=true;return}if(current&&current.src===video){const noteNode=out.querySelector('.media-note');if(noteNode&&noteNode.textContent!==note)noteNode.textContent=note;out.hidden=false;return}out.innerHTML=mediaMarkup(video,captions,note);out.hidden=false}
function setStatusMessage(message){const out=document.getElementById('status-message'),next=String(message||'');if(out.textContent!==next)out.textContent=next}
function showTechnical(j){const stage=String(j.failure_stage||j.stage||'—');document.getElementById('technical-stage').textContent=stage;const error=String(j.error||'').trim();document.getElementById('technical-error').textContent=error;document.getElementById('technical-error-row').hidden=!error}
function showWorkprint(j){const host=document.getElementById('qa-workprint-host'),path=`/studio/job/${encodeURIComponent(taskId)}/qa-workprint`;if(j.qa_workprint_path!==path){host.replaceChildren();return}if(host.querySelector('a'))return;host.innerHTML=`<section class="notice" aria-label="İnceleme taslağı"><b>Bu denemenin inceleme taslağı saklandı.</b><p>Kalite kontrolünü geçmedi. Yayına hazır değildir ve YouTube’a gönderilmez.</p>${linkAction(path,'İnceleme videosunu aç')}</section>`}
async function poll(){
 try{const r=await fetch(`/studio/api/job/${encodeURIComponent(taskId)}`,{cache:'no-store'});if(!r.ok)throw new Error('status');const j=await r.json();ownerPreview=j.owner_preview||null;
 const state=String(j.state||'PENDING'),ui=String(j.ui_status||'running'),stage=String(j.stage_label||j.stage||'Hazırlanıyor'),p=Math.max(0,Math.min(100,Number(j.progress||0)));
 const panel=document.getElementById('job-card'),progress=document.getElementById('progress'),out=document.getElementById('result'),pill=document.getElementById('state-label'),displayUi=String(j.display_status||ui);showWorkprint(j);
 panel.dataset.status=displayUi;pill.className='state '+displayUi;pill.textContent=j.delivery_label||labels[displayUi]||labels.running;
 document.getElementById('bar').style.width=p+'%';progress.setAttribute('aria-valuenow',String(p));progress.hidden=ui!=='running';document.getElementById('stage').textContent=stage+(ui==='running'?' · %'+p:'');setStatusMessage(j.ui_status_message);showTechnical(j);
 if(j.retry_presentation){const latest=j.retry_presentation;setMedia({});setAction('latest:'+latest.task_id+':'+Boolean(j.quality_held),linkAction(`/studio/job/${encodeURIComponent(latest.task_id)}`,'Güncel sonucu aç')+(j.quality_held===true?linkAction('/studio','Üretim planını aç'):''));return}
 if(j.publication_status){setMedia(['ready','completed'].includes(ui)?j.result:{},j.delivery_label||'YouTube yüklemesinin durumu doğrulanıyor.');setAction('publication-review',linkAction(j.publication_review_path||'/studio/youtube','Mevcut yüklemeyi kontrol et','repair'));if(j.publication_status==='pending')timer=setTimeout(poll,3000);return}
 if(j.quality_held===true){setMedia({});setAction('quality-held','<p>Bu deneme inceleme için saklanıyor.</p>'+linkAction('/studio','Üretim planını aç'));return}
 if(ui==='repair'){setMedia({});setAction('repair',retryAction('Sorunlu sahneyi onar','repair'));return}
 if(ui==='failed'){setMedia({});if(j.voice_replacement_available===true)setAction('voice-replacement',linkAction(`/studio/voice-replacement/${encodeURIComponent(taskId)}`,'Sesi tek denemeyle yenile','repair'));else setAction('failed',retryAction('Aynı ayarlarla tekrar dene','danger'));return}
 if(ui==='ready'&&state==='AWAITING_APPROVAL'){setAction('storyboard',linkAction(`/studio/plan/${encodeURIComponent(taskId)}`,"Storyboard'u aç",'success'));return}
 if(ui==='ready'||ui==='completed'){const x=j.result||{},youtube=/^[A-Za-z0-9_-]{11}$/.test(j.delivery_video_id||'')?'https://www.youtube.com/watch?v='+j.delivery_video_id:'',download=(ownerPreview&&ownerPreview.kind==='video'&&ownerPreview.url===`/studio/job/${encodeURIComponent(taskId)}/preview-media`)?ownerPreview.url:safeExternal(x.download_url||x.video_url),mediaNote=j.delivery_label||(displayUi==='attention'?'Videoyu kontrol et; onaylanmadan YouTube’a yüklenmez.':displayUi==='unreviewed'?'Açık kalite onayı yok; YouTube yüklemesi kapalı.':'Kalite onaylanana kadar YouTube yüklemesi gizli kalır.');setMedia(x,mediaNote);if(youtube)setAction('youtube',linkAction(youtube,"YouTube'da aç",'success',true));else if(ui==='ready'&&j.upload_allowed===true)setAction('private-upload',linkAction('/studio/youtube','Gizli yükle','success'));else if(download&&displayUi==='attention')setAction('manual-review',linkAction(download,'Videoyu incele','repair',true));else if(download&&displayUi==='unreviewed')setAction('unreviewed',linkAction(download,'Videoyu incele','secondary',true));else if(download)setAction('download',linkAction(download,'Videoyu aç','secondary',true));else setAction('ready-refresh',linkAction(`/studio/job/${encodeURIComponent(taskId)}`,'Sonucu yenile'));return}
 const child=String(j.retry_child_task_id||'').trim(),target=child||taskId,label=child?(j.repair_claimed?'Onarım durumunu aç':'Yeniden denemeyi aç'):'Durumu yenile';setAction('running:'+target,linkAction(`/studio/job/${encodeURIComponent(target)}`,label));timer=setTimeout(poll,3000);
 }catch(_){setStatusMessage('Durum geçici olarak alınamıyor. Tekrar denenecek.');timer=setTimeout(poll,5000)}
 }
poll();
</script>'''.replace('__TASK_ID__', json.dumps(task_id))
    return _shell(body, active='history', title='Video inceleme', script=script + _review_script() + (_metrics_script() if performance else ''))


@router.get('/studio/job/{task_id}/preview-media')
@router.head('/studio/job/{task_id}/preview-media')
def studio_preview_media(task_id: str, request: Request, studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    _require_auth(studio_token)
    if not _canonical_task_id(task_id):
        raise HTTPException(status_code=404, detail='Önizleme bulunamadı')
    record = get_job(task_id)
    if not record or record.get('task_id') != task_id:
        raise HTTPException(status_code=404, detail='Önizleme bulunamadı')
    from app.services.studio_preview import media_response
    return media_response(record, request)


@router.get('/studio/job/{task_id}/visual-diagnostics', response_class=HTMLResponse)
def studio_visual_diagnostics(task_id: str, studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    _require_auth(studio_token)
    # Owner Studio authentication precedes even UUID/job lookup. Possessing
    # this same-origin URL is never sufficient authority to read the object.
    try:
        if str(UUID(task_id)) != task_id:
            raise ValueError('Noncanonical task ID')
    except (ValueError, AttributeError):
        raise HTTPException(status_code=404, detail='Tanı kaydı bulunamadı', headers=_VISUAL_DIAGNOSTIC_HEADERS) from None
    try:
        record = get_job(task_id)
    except Exception:
        raise HTTPException(status_code=503, detail='Tanı kaydı şu anda açılamıyor', headers=_VISUAL_DIAGNOSTIC_HEADERS) from None
    pointer = _visual_diagnostic_pointer(record, task_id)
    if pointer is None:
        raise HTTPException(status_code=404, detail='Tanı kaydı bulunamadı', headers=_VISUAL_DIAGNOSTIC_HEADERS)
    payload = _read_visual_diagnostic_html(*pointer)
    return HTMLResponse(content=payload, headers=_VISUAL_DIAGNOSTIC_HEADERS)


def _owner_qa_workprint(task_id: str, studio_token: str | None):
    _require_auth(studio_token)
    if not _canonical_task_id(task_id):
        raise HTTPException(status_code=404, detail='İnceleme taslağı bulunamadı')
    from app.services.qa_workprint_access import validated_pointer
    try:
        record = get_job(task_id)
    except Exception:
        raise HTTPException(status_code=503, detail='İnceleme taslağı şu anda açılamıyor') from None
    pointer = validated_pointer(record, task_id)
    if pointer is None:
        raise HTTPException(status_code=404, detail='İnceleme taslağı bulunamadı')
    return record, pointer


@router.get('/studio/job/{task_id}/qa-workprint', response_class=HTMLResponse)
def studio_qa_workprint(task_id: str, studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    record, _pointer = _owner_qa_workprint(task_id, studio_token)
    body = (
        '<div class="hero"><div class="hero-copy"><div class="eyebrow">Yalnızca özel inceleme</div>'
        f'<h1>{escape(_job_title(record))}</h1></div></div>'
        '<section class="card"><div class="section-title"><h2>İnceleme taslağı</h2>'
        '<span class="badge">Yayınlanamaz</span></div>'
        '<p class="notice">Bu çalışma kopyası kalite kontrolünü geçmedi. '
        'Onaylı final değildir ve YouTube’a gönderilmez. Ses, sahne geçişleri ve '
        'görüntü–anlatım uyumu bu kopya üzerinden incelenebilir.</p>'
        '<div class="result-video-frame"><video class="result-video" controls playsinline '
        f'preload="metadata" src="/studio/job/{task_id}/qa-workprint/video">'
        'Tarayıcın video oynatmayı desteklemiyor.</video></div></section>'
        f'<nav class="back-links"><a href="/studio/job/{task_id}">Deneme kaydına dön</a></nav>'
    )
    response = _shell(body, active='history', title='Özel inceleme taslağı')
    response.headers.update({
        'Cache-Control': 'private, no-store', 'Referrer-Policy': 'no-referrer',
        'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
        'Content-Security-Policy': "default-src 'none'; media-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    })
    return response


@router.get('/studio/job/{task_id}/qa-workprint/video')
@router.head('/studio/job/{task_id}/qa-workprint/video')
def studio_qa_workprint_video(task_id: str, request: Request, studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    _record, pointer = _owner_qa_workprint(task_id, studio_token)
    from app.services.qa_workprint_access import stream_response
    return stream_response(pointer, request)


@router.get('/studio/api/job/{task_id}')
def studio_job_api(task_id: str, studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    _require_auth(studio_token)
    record = _with_publication_presentation(_sync_job(task_id), get_job, upload_lookup=get_upload_record)
    record = _with_youtube_metrics(record, _dashboard_metrics([record]))
    retry_presentation = _terminal_retry_presentation(record, get_job, upload_lookup=get_upload_record)
    payload = dict(record)
    payload['voice_replacement_available'] = _voice_replacement_candidate(record)
    # Poll only a same-origin owner route, never private object keys or a bearer URL.
    payload.pop('qa_workprint', None)
    payload['owner_preview'] = _review_media(record)
    workprint_path = _qa_workprint_path(record)
    if workprint_path:
        payload['qa_workprint_path'] = workprint_path
    payload['state_label'] = STATE_LABELS.get(str(payload.get('state')), str(payload.get('state') or ''))
    stage_key = (
        payload.get('failure_stage')
        if str(payload.get('state') or '').upper() == 'FAILURE'
        else payload.get('stage')
    )
    payload['stage_label'] = STAGE_LABELS.get(str(stage_key), str(stage_key or ''))
    for key in ('message', 'error'):
        if payload.get(key):
            payload[key] = _safe_ui_text(payload[key])
    # The full approved package is rendered on the storyboard page, not polled every three seconds.
    if isinstance(payload.get('result'), dict) and payload['result'].get('package'):
        result = dict(payload['result'])
        result['package'] = {'scene_count': len(result['package'].get('scenes') or []), 'title': result['package'].get('title')}
        payload['result'] = result
    payload['ui_status'] = _job_ui_status(payload)
    payload['ui_status_label'] = UI_STATUS_LABELS[payload['ui_status']]
    payload['display_status'] = _job_display_status(payload)
    payload['display_status_label'] = UI_STATUS_LABELS[payload['display_status']]
    payload['upload_allowed'] = _job_upload_allowed(payload)
    payload['ui_status_message'] = _review_reason(record)
    payload['delivery_label'] = _video_delivery(record)['label']
    payload['delivery_video_id'] = '' if _video_delivery(record)['key'] == 'deleted' else _delivery_video_id(record)
    if _video_delivery(record)['key'] == 'rendered' and payload['display_status'] in {'attention', 'unreviewed'}:
        payload['delivery_label'] = ''
    payload['publication_status'] = _publication_status(record)
    if payload['publication_status']:
        publisher_id = _canonical_task_id(record.get('_publication_task_id'))
        if record.get('kind') == 'publish':
            publisher_id = _canonical_task_id(record.get('task_id'))
        payload['publication_review_path'] = (
            f'/studio/youtube/publish-status/{publisher_id}' if publisher_id else '/studio/youtube'
        )
    if retry_presentation:
        payload['delivery_label'] = ''
        payload['delivery_video_id'] = ''
        payload['retry_presentation'] = retry_presentation
        for field in ('ui_status', 'display_status'):
            payload[field] = retry_presentation[field]
            payload[field + '_label'] = UI_STATUS_LABELS[payload[field]]
        payload['stage_label'] = retry_presentation['stage_label']
        payload['ui_status_message'] = (
            'Bu sayfa önceki denemenin kaydıdır. ' + retry_presentation['message']
        )
        payload['upload_allowed'] = False
    return JSONResponse(payload)


def _operations_status() -> str:
    status, observed_at = 'unavailable', None
    try:
        from app.services.studio_operations import read_tick
        value = read_tick()
        status, observed_at = value['status'], value['observed_at']
    except Exception:
        pass
    label = {'checked': 'Otomasyon sunucusu yanıt veriyor',
             'blocked': 'Sunucu çalışıyor; üretim kontrolü tamamlanamadı',
             'stale': 'Sunucudan güncel otomasyon sinyali gelmedi'}.get(status, 'Sunucu kontrol bilgisi bekleniyor')
    when = 'Son kontrol: ' + _metrics_time(observed_at) if observed_at else 'Son kontrol zamanı henüz doğrulanmadı.'
    return '<div class="operations-status ' + escape(status, quote=True) + '"><b>' + label + '</b><br>' + escape(when) + '</div>'


def _production_budget_notice() -> str:
    """Show read-only funding evidence without equating configured keys to readiness."""
    title = 'Bütçe durumu doğrulanamadı'
    detail = 'Üretim bütçesi şu anda okunamıyor. Kullanılabilir tutar bilinmiyor.'
    try:
        from app.services.production_spend_runtime import budget_status
        status = budget_status(read_timeout=2)
        if type(status) is not dict:
            raise ValueError('invalid_budget_status')
        if status.get('enforced') is False and status.get('status') == 'not_enabled':
            title = 'Bütçe koruması kapalı'
            detail = 'Yeni üretim için uygulamanın harcama sınırı etkin değil.'
        elif status.get('enforced') is True and status.get('status') == 'blocked':
            title = 'Yeni ücretli üretim bütçe kontrolünü bekliyor'
            reason = status.get('reason_code')
            if reason in {'spend_not_initialized', 'spend_funding_not_initialized'}:
                detail = 'Harcama geçmişi ve kullanılabilir bakiye doğrulanmayı bekliyor. Bilinmeyen tutarlar sıfır kabul edilmiyor.'
            elif reason in {'spend_funding_policy_expired', 'spend_funding_account_expired',
                            'spend_funding_month_mismatch'}:
                detail = 'Kayıtlı bütçe veya abonelik bilgisi güncel değil; yeniden doğrulanması gerekiyor.'
            else:
                detail = 'Bütçe kaydı doğrulanamadığı için yeni ücretli üretim başlatılamıyor.'
        elif status.get('enforced') is True and status.get('status') == 'active':
            funding = status.get('funding')
            commissioning = status.get('commissioning_audio')
            if (type(commissioning) is dict and commissioning.get('mode') == 'commissioning'
                    and type(commissioning.get('reserved_list_cost_micro_usd')) is int
                    and 0 <= commissioning['reserved_list_cost_micro_usd'] <= 600_000):
                title = 'Kurulum ve test modu'
                amount = f"{commissioning['reserved_list_cost_micro_usd'] / 1_000_000:.3f}"
                detail = ('Otomatik ses kontrolleri için ayrılan tutar: ' + amount
                    + ' USD; bu bir fatura toplamı değildir. Abonelik kredileri ayrı takip edilir. '
                    'Aylık işletme bütçesini kurulum tamamlandıktan sonra belirleyeceksin.')
            elif (type(funding) is dict and funding.get('mode') == 'cash_disabled_unknown_history'
                    and funding.get('historical_cash_micro') is None
                    and funding.get('cash_spending_enabled') is False
                    and type(funding.get('new_cash_allowance_micro')) is int
                    and funding['new_cash_allowance_micro'] == 0):
                title = 'Ek API harcaması kapalı'
                detail = ('Mevcut abonelik kredileri ayrı takip edilir. Önceki API faturaları '
                          'henüz bilinmediği için yeni ek ücretli işlem başlatılmaz.')
            elif (type(funding) is not dict or funding.get('currency') != 'USD'
                    or funding.get('accounting') != 'reserved_cash_upper_bound_not_invoice'
                    or type(funding.get('cash_remaining_micro')) is not int
                    or abs(funding['cash_remaining_micro']) > 10_000_000_000):
                raise ValueError('invalid_budget_funding')
            else:
                remaining = funding['cash_remaining_micro']
                amount = f'{remaining / 1_000_000:.2f}'
                title = 'Ek API harcaması için kalan pay: ' + amount + ' USD'
                detail = 'Bu tutar ayrılmış harcamaları içerir; fatura toplamı değildir. Her üretim öncesinde bütçe yeniden kontrol edilir.'
                if remaining <= 0:
                    detail = 'Yeni ek harcama payı yok. Abonelik kredileri ayrıca doğrulanır.'
    except Exception:
        pass
    return ('<section class="notice" role="status" aria-label="Üretim bütçesi">'
            + '<b>' + escape(title) + '</b><p>' + escape(detail) + '</p></section>')


def _youtube_metrics_response(model: dict, jobs: list[dict]) -> JSONResponse:
    payload = {key: model.get(key) for key in ('channels', 'videos', 'updated_at', 'refresh_after_seconds')}
    payload['channel_overview_html'] = _channel_overview(model.get('channels') or [])
    payload['video_presentations'] = {}
    # Match the initial cards: a cache poll must not erase a bound publisher's
    # failure warning. All lookups stay inside this already-loaded job list.
    by_id = {job.get('task_id'): job for job in jobs if isinstance(job, dict)}
    projected = {job.get('task_id'): job for job in _collapse_retry_sources(jobs)}
    for job in jobs[:HISTORY_SCAN_LIMIT]:
        task_id = _canonical_task_id(job.get('task_id')) if isinstance(job, dict) else ''
        if task_id:
            displayed = _with_publication_presentation(projected.get(task_id, job), by_id.get)
            decorated = _with_youtube_metrics(displayed, model)
            payload['video_presentations'][task_id] = {'badges_html': _delivery_badges(decorated), 'summary': _performance_summary(decorated), 'label': _ready_readiness_label(decorated), 'key': _video_delivery(decorated)['key']}
    return JSONResponse(payload, headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})


@router.get('/studio/api/youtube-metrics')
def studio_youtube_metrics(studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    _require_auth(studio_token)
    jobs = list_jobs(HISTORY_SCAN_LIMIT)
    return _youtube_metrics_response(_dashboard_metrics(jobs), jobs)


@router.post('/studio/api/youtube-metrics/refresh')
def studio_youtube_metrics_refresh(request: Request, studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    _require_auth(studio_token)
    from app.youtube_routes import _require_same_origin
    _require_same_origin(request)
    jobs = list_jobs(HISTORY_SCAN_LIMIT)
    return _youtube_metrics_response(_dashboard_metrics(jobs, refresh=True), jobs)


@router.get('/studio/history', response_class=HTMLResponse)
def studio_history(
    status: str = 'running',
    page: int = 1,
    q: str = '',
    media: str = 'all',
    studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
):
    _require_auth(studio_token)
    allowed_statuses = (
        *UI_STATUS_ORDER, 'attention', 'previews', 'library', 'unreviewed', 'drafts', 'uploaded', 'public', 'private', 'deleted',
    )
    active = status if status in allowed_statuses else 'running'
    page = max(1, int(page))
    raw_jobs = list_jobs(HISTORY_SCAN_LIMIT)
    metrics = _dashboard_metrics(raw_jobs)
    preview_jobs = _preview_jobs([_with_youtube_metrics(job, metrics) for job in raw_jobs])
    stored_jobs = _collapse_retry_sources(
        [_with_youtube_metrics(job, metrics) for job in raw_jobs],
        collapse_attention=False,
    )
    jobs = _collapse_attention_duplicates(stored_jobs)
    # Reconcile only the records that can appear on this page. The registry is
    # retained at 500 jobs; probing each Celery result would create an N+1 read
    # storm just to render the overview counts.
    query = str(q or '').strip()[:120]
    media = media if media in {'all', 'video', 'audio'} else 'all'
    def matches(job):
        if query and query.casefold() not in (_job_title(job) + ' ' + _job_channel(job)).casefold():
            return False
        if active == 'previews':
            preview = _review_media(job)
            return bool(preview and (media == 'all' or preview['kind'] == media))
        return _history_matches(job, active)
    if active == 'previews':
        jobs = preview_jobs
    stored_filtered = [job for job in jobs if matches(job)]
    stored_page_count = max(1, math.ceil(len(stored_filtered) / HISTORY_PAGE_SIZE))
    page = min(page, stored_page_count)
    offset = (page - 1) * HISTORY_PAGE_SIZE
    candidates = []
    for job in stored_filtered[offset:offset + HISTORY_PAGE_SIZE]:
        candidate = dict(job)
        candidate.pop('_grouped_attention_attempts', None)
        candidates.append(candidate)
    refreshed_by_id = {} if active == 'previews' else {
        str(job.get('task_id') or ''): job
        for job in _refresh_active_jobs(candidates)
    }
    stored_jobs = [
        refreshed_by_id.get(str(job.get('task_id') or ''), job)
        for job in stored_jobs
    ]
    console_jobs = _collapse_attention_duplicates(stored_jobs)
    console_counts = _console_counts(console_jobs)
    jobs = preview_jobs if active == 'previews' else console_jobs
    filtered = [job for job in jobs if matches(job)]
    total = len(filtered)
    page_count = max(1, math.ceil(total / HISTORY_PAGE_SIZE))
    page = min(page, page_count)
    start = (page - 1) * HISTORY_PAGE_SIZE
    visible = filtered[start:start + HISTORY_PAGE_SIZE]
    empty_copy = {
        'running': 'Devam eden üretim yok.',
        'attention': 'Dikkat gerektiren güncel video yok.',
        'previews': 'Bu filtreyle eşleşen video veya ses taslağı yok.',
        'library': 'Henüz tamamlanmış video yok.',
        'uploaded': 'Henüz YouTube’a yüklenmiş video yok.',
        'public': 'Henüz herkese açık video yok.',
        'private': 'Henüz gizli yüklenmiş video yok.',
        'deleted': 'Silinmiş veya erişilemeyen YouTube videosu yok.',
        'ready': 'Hazır video yok.',
        'repair': 'Onarım bekleyen video yok.',
        'completed': 'Tamamlanan video yok.',
        'failed': 'Başarısız üretim yok.',
        'unreviewed': 'Kalite onayı olmayan eski video yok.',
        'drafts': 'Eski storyboard taslağı yok.',
    }[active]
    rich_library = active in {'library', 'ready', 'completed', 'uploaded', 'public', 'private', 'deleted'}
    review_mode = active in {'attention', 'previews', 'failed', 'repair', 'unreviewed'}
    rows = ''.join(
        _review_card(job) if review_mode else
        _ready_video_card(job)
        if rich_library or (_publication_status(job) and _job_media_urls(job)[0])
        else _job_row(job)
        for job in visible
    ) or f'<div class="empty">{empty_copy}</div>'
    from urllib.parse import urlencode
    filter_params = {'status': active}
    if query:
        filter_params['q'] = query
    if media != 'all':
        filter_params['media'] = media
    filters = escape(urlencode(filter_params), quote=True)
    previous = (
        f'<a class="btn secondary small" href="/studio/history?{filters}&amp;page={page - 1}">← Önceki</a>'
        if page > 1 else '<span class="btn secondary small" aria-disabled="true">← Önceki</span>'
    )
    following = (
        f'<a class="btn secondary small" href="/studio/history?{filters}&amp;page={page + 1}">Sonraki →</a>'
        if page < page_count else '<span class="btn secondary small" aria-disabled="true">Sonraki →</span>'
    )
    pagination = (
        f'<nav class="page-links" aria-label="Geçmiş sayfaları">{previous}'
        f'<span class="tiny">Sayfa {page} / {page_count}</span>{following}</nav>'
        if total > HISTORY_PAGE_SIZE else ''
    )
    history_context = {
        'running': 'Şu anda hazırlanan videolar.',
        'attention': 'Yalnızca karar veya kontrol bekleyen güncel videolar.',
        'previews': 'Kaliteyi geçmeyen taslaklar dahil, saklanan video ve sesleri burada açabilirsin.',
        'library': 'Üretilen ve YouTube’a yüklenen videolar ayrı durumlarla gösterilir.',
        'uploaded': 'Gizli, planlı ve herkese açık YouTube yüklemeleri. Kontrol uyarıları korunur.',
        'public': 'YouTube’da herkese açık görünen videolar.',
        'private': 'YouTube’a yüklenmiş, henüz herkese açık olmayan gizli videolar.',
        'deleted': 'Kanalın yetkili sorgusunda artık bulunmayan videolar. Geçmiş yayın ve dosyalar korunur; otomatik tekrar yüklenmez.',
        'ready': 'Yüklemeye hazır videolar.',
        'repair': 'Onarım kararı bekleyen üretimler.',
        'completed': 'YouTube yüklemesi tamamlanan videolar.',
        'failed': 'Eski başarısız denemeler; günlük listeden ayrı tutulur.',
        'unreviewed': 'Açık kalite onayı olmayan eski çıktılar; YouTube yüklemesi kapalıdır.',
        'drafts': 'Bir günden uzun süredir bekleyen storyboard taslakları.',
    }[active]
    history_archive = _history_archive(_archive_counts(jobs), active=active)
    active_label = {'attention': 'Videoları incele', 'previews': 'Önizlemelerin', 'uploaded': 'YouTube’a yüklenenler', 'public': 'Herkese açık videolar', 'private': 'Gizli videolar', 'deleted': 'Silinenler'}.get(active) or (
        'Eski storyboard taslakları'
        if active == 'drafts'
        else CONSOLE_STATUS_LABELS[active]
        if active in CONSOLE_STATUS_LABELS else UI_STATUS_LABELS[active]
    )
    media_select = ('<select name="media" aria-label="Önizleme türü">' + ''.join(
        f'<option value="{key}"' + (' selected' if media == key else '') + f'>{label}</option>'
        for key, label in [('all', 'Video ve ses'), ('video', 'Yalnız video'), ('audio', 'Yalnız ses')]) + '</select>') if active == 'previews' else ''
    search = (f'<form class="review-search" method="get" action="/studio/history"><input type="hidden" name="status" value="{active}">'
              f'<input name="q" type="search" maxlength="120" value="{escape(query, quote=True)}" placeholder="Video veya kanal ara" aria-label="Video veya kanal ara">'
              + media_select + '<button type="submit">Ara</button></form>')
    body = f'''
<div class="hero"><div class="hero-copy"><div class="eyebrow">VİDEOLAR</div><h1>{active_label}</h1><div class="muted">{history_context}</div></div></div>
{_review_tabs(active, console_counts, len(preview_jobs))}
{_library_filters(active) if rich_library else ''}
{search}
<div class="review-summary"><div><h2>{total} {'önizleme' if active == 'previews' else 'video' if rich_library else 'üretim'}</h2><p>İzlemek veya dinlemek, videoyu yayımlamaz.</p></div><a href="/studio">Sistem durumunu gör →</a></div>
<div class="{"review-grid" if review_mode else "ready-grid" if rich_library else "job-list"}" data-history-status="{active}">{rows}</div>{pagination}
<details class="technical-details console-details"><summary>Kanal performansı</summary>{_metrics_header(metrics)}<div id="channel-overview-host">{_channel_overview(metrics.get('channels') or [])}</div></details>
{history_archive}
'''
    return _shell(body, active='review' if active == 'previews' else 'history', title='Videolar · Studio', script=_metrics_script() + _review_script())


@router.get('/studio/plan/{task_id}', response_class=HTMLResponse)
def studio_plan(task_id: str, studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    _require_auth(studio_token)
    record = _sync_job(task_id)
    result = record.get('result') or {}
    package = result.get('package') if isinstance(result, dict) else None
    if not isinstance(package, dict):
        raise HTTPException(status_code=404, detail='Storyboard henüz hazır değil')
    scenes = package.get('scenes') or []
    scene_html = ''
    for idx, scene in enumerate(scenes, start=1):
        queries = ''.join(f'<span class="query">{escape(str(q))}</span>' for q in (scene.get('visual_queries') or []))
        ai = '<span class="badge">AI sahne planlandı</span>' if scene.get('ai_prompt') else ''
        scene_html += f'''<div class="scene"><div class="scene-no">{idx}</div><div><b>{escape(str(scene.get('narration') or ''))}</b><div class="queries">{queries}</div>{ai}</div></div>'''
    spec = record.get('spec') or {}
    body = f'''
<div class="hero"><div class="hero-copy"><div class="eyebrow">Yönetmen planı</div><h1>{escape(_ellipsize(_safe_ui_text(package.get('title') or spec.get('topic')), 120) or 'Storyboard')}</h1><div class="muted">Sahne akışını onayladığında render başlar.</div></div><div class="hero-tools"><span class="badge">{len(scenes)} sahne</span><span class="badge">{escape(STYLE_LABELS.get(str(spec.get('content_style')),str(spec.get('content_style') or '')))}</span></div></div>
<div class="card"><h2>Yönetmen planı</h2>{scene_html}</div>
<div class="card"><h3>Render kararı</h3><p class="muted">Onaylandığında bu senaryo kilitlenir; araştırma yeniden yapılmadan ses, görsel QC, AI sahneleri, müzik ve final kurgu başlar.</p><form action="/studio/plan/{escape(task_id)}/render" method="post"><button class="block" type="submit">✓ Storyboard'u onayla ve render et</button></form></div>
<div class="actions"><a class="btn secondary" href="/studio">← Yeni plan</a><a class="btn secondary" href="/studio/job/{escape(task_id)}">Göreve dön</a></div>
'''
    return _shell(body, active='history', title='Storyboard')


@router.post('/studio/plan/{task_id}/render')
def studio_render_plan(task_id: str, studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    _require_auth(studio_token)
    record = _sync_job(task_id)
    result = record.get('result') or {}
    package = result.get('package') if isinstance(result, dict) else None
    spec = record.get('spec') or {}
    if not isinstance(package, dict):
        raise HTTPException(status_code=409, detail='Onaylanabilir storyboard bulunamadı')
    options = {key: value for key, value in spec.items() if key not in {'topic', 'duration_minutes', 'language', 'channel_id'}}
    options['workflow'] = 'approved'
    task = run_video_pipeline.delay(
        spec.get('topic') or '',
        float(spec.get('duration_minutes') or 1),
        spec.get('language') or 'tr',
        spec.get('channel_id'),
        options,
        package,
    )
    child_spec = dict(spec)
    child_spec['workflow'] = 'approved'
    create_job(task.id, child_spec, kind='render', parent_id=task_id)
    return RedirectResponse(f'/studio/job/{task.id}', status_code=303)


@router.post('/studio/retry/{task_id}')
def studio_retry(task_id: str, studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    _require_auth(studio_token)
    record = get_job(task_id)
    if not record:
        raise HTTPException(status_code=404, detail='Görev bulunamadı')
    spec = record.get('spec') or {}
    options = {key: value for key, value in spec.items() if key not in {'topic', 'duration_minutes', 'language', 'channel_id'}}
    kind = 'plan' if record.get('kind') == 'plan' else 'render'
    try:
        retry_duration = float(spec.get('duration_minutes') or 1)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=409,
            detail='Kaynak görevin süre kaydı geçersiz',
        ) from exc
    retry_topic = str(spec.get('topic') or '')
    retry_language = str(spec.get('language') or 'tr')
    retry_channel_id = spec.get('channel_id')
    child_task_id = str(uuid4())
    dispatch_token = secrets.token_urlsafe(32)
    try:
        dispatch = claim_retry_dispatch(
            task_id,
            child_task_id,
            dispatch_token,
            allow_repair=kind == 'render',
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail='Yeniden deneme durumu güvenle ayrılamadı',
        ) from exc
    if not dispatch.get('claimed'):
        existing_child = str(dispatch.get('child_task_id') or '').strip()
        if existing_child:
            return RedirectResponse(
                f'/studio/job/{existing_child}',
                status_code=303,
            )
        raise HTTPException(
            status_code=409,
            detail='Yeniden deneme zaten kuyruğa alındı',
        )

    checkpoint = dispatch.get('checkpoint')
    approved_package = (
        checkpoint.get('approved_package')
        if isinstance(checkpoint, dict)
        else None
    )
    curated_stock_manifest = None
    if isinstance(checkpoint, dict) and 'curated_stock_manifest' in checkpoint:
        # Only the already-claimed private checkpoint can supply this pointer;
        # never read it from a form, options, model output or a public job field.
        if (
            kind != 'render' or dispatch.get('mode') != 'repair'
            or not isinstance(approved_package, dict)
            or not isinstance(checkpoint['curated_stock_manifest'], dict)
        ):
            raise HTTPException(status_code=409, detail='Sabit sahne kurtarma kaydı geçersiz')
        curated_stock_manifest = dict(checkpoint['curated_stock_manifest'])
    child_spec = dict(spec)
    if approved_package is not None:
        child_spec['workflow'] = 'scene_repair'
        child_spec['repair_source_task_id'] = task_id
    create_job(
        child_task_id,
        child_spec,
        kind=kind,
        parent_id=task_id,
    )
    if kind == 'plan':
        task_callable = plan_video_pipeline
        task_args = (
            retry_topic,
            retry_duration,
            retry_language,
            retry_channel_id,
            options,
            task_id,
        )
    else:
        task_callable = run_video_pipeline
        task_args = (
            retry_topic,
            retry_duration,
            retry_language,
            retry_channel_id,
            options,
            approved_package,
            task_id,
        )
        if curated_stock_manifest is not None:
            task_args = (*task_args, curated_stock_manifest)
    try:
        task_callable.apply_async(args=task_args, task_id=child_task_id)
    except Exception:
        # Broker timeouts are ambiguous: the message may already be durable.
        # Preserve the one-shot claim and deterministic task id; never reopen
        # the checkpoint or issue a second paid submission.
        try:
            mark_retry_dispatch(task_id, dispatch_token, 'uncertain')
        except Exception:
            pass
        update_job(
            child_task_id,
            state='PENDING',
            stage='dispatch_uncertain',
            message='Kuyruk kabulü doğrulanıyor; aynı iş tekrar gönderilmeyecek.',
        )
    else:
        try:
            mark_retry_dispatch(task_id, dispatch_token, 'dispatched')
        except Exception:
            pass
    return RedirectResponse(f'/studio/job/{child_task_id}', status_code=303)


@router.get('/studio/voice-replacement/{task_id}', response_class=HTMLResponse)
def studio_voice_replacement_page(task_id: str, studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    _require_auth(studio_token)
    record = get_job(task_id)
    if not isinstance(record, dict) or not _voice_replacement_candidate(record):
        raise HTTPException(status_code=409, detail='Bu deneme için yeni ses kaydı açılamıyor')
    safe_id = escape(task_id, quote=True)
    checksum = record['audio_candidate_checkpoint']['audio_sha256']
    body = (
        '<section class="card"><h1>Ses kaydını yenile</h1>'
        '<p>Senaryo ve kanal ayarları korunur. Farklı bir anlatım modeliyle yalnız bir yeni kayıt alınır; '
        'metin, telaffuz ve doğallık kontrolleri yeniden çalışır. Eski denemeler silinmez.</p>'
        f'<form method="post" action="/studio/voice-replacement/{safe_id}">'
        f'<input type="hidden" name="expected_audio_sha256" value="{checksum}">'
        '<button class="btn repair" type="submit">Tek yeni ses kaydını başlat</button></form></section>'
    )
    response = _shell(body, active='history', title='Ses kaydını yenile')
    response.headers['Referrer-Policy'] = 'same-origin'
    return response


@router.post('/studio/voice-replacement/{task_id}')
def studio_voice_replacement(
    task_id: str, request: Request, expected_audio_sha256: str = Form(...),
    studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
):
    _require_auth(studio_token)
    from app.youtube_routes import _require_same_origin
    from app.services.voice_replacement import VoiceReplacementError, reserve_voice_replacement

    _require_same_origin(request)
    record = get_job(task_id)
    if not isinstance(record, dict):
        raise HTTPException(status_code=404, detail='Görev bulunamadı')
    child_id, token = str(uuid4()), secrets.token_urlsafe(32)
    try:
        dispatch = reserve_voice_replacement(task_id, child_id, token, expected_audio_sha256)
    except VoiceReplacementError:
        raise HTTPException(status_code=409, detail='Ses yenileme kaydı güvenle ayrılamadı') from None
    except Exception:
        raise HTTPException(status_code=503, detail='Ses yenileme durumu doğrulanamıyor') from None
    if not dispatch.get('claimed'):
        existing = _canonical_task_id(dispatch.get('child_task_id'))
        if existing:
            return RedirectResponse(f'/studio/job/{existing}', status_code=303)
        raise HTTPException(status_code=409, detail='Ses yenileme daha önce ayrıldı')
    spec = dict(dispatch['spec'])
    options = {key: value for key, value in spec.items() if key not in {'topic', 'duration_minutes', 'language', 'channel_id'}}
    create_job(child_id, spec, kind='render', parent_id=task_id)
    update_job(child_id, voice_replacement=dispatch['voice_replacement'])
    args = (spec['topic'], spec['duration_minutes'], spec['language'], spec.get('channel_id'),
            options, None, task_id, None, task_id)
    try:
        run_video_pipeline.apply_async(args=args, task_id=child_id)
    except Exception:
        try:
            mark_retry_dispatch(task_id, token, 'uncertain')
        except Exception:
            pass
        update_job(child_id, state='PENDING', stage='dispatch_uncertain',
                   message='Kuyruk kabulü doğrulanıyor; ikinci ses kaydı başlatılmayacak.')
    else:
        try:
            mark_retry_dispatch(task_id, token, 'dispatched')
        except Exception:
            pass
    return RedirectResponse(f'/studio/job/{child_id}', status_code=303)


@router.post('/studio/logout')
def studio_logout(
    request: Request,
    studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
):
    _require_auth(studio_token)
    # Logout mutates the global OAuth generation, so it needs the same strict
    # same-origin proof as disconnect/publish rather than being a CSRF-able GET.
    from app.youtube_routes import _require_same_origin

    _require_same_origin(request)
    # A Studio logout also revokes every still-pending OAuth callback.  The
    # short-lived callback cookie is removed as a second, browser-local guard;
    # logout itself must remain available if Redis is temporarily unavailable.
    try:
        from app.services.youtube_auth import invalidate_pending_authorizations

        invalidate_pending_authorizations()
    except Exception:
        pass
    response = RedirectResponse('/studio', status_code=303)
    response.delete_cookie(COOKIE_NAME)
    response.delete_cookie(
        'youtube_oauth_browser_binding',
        path='/studio/youtube/callback',
        secure=True,
        httponly=True,
        samesite='lax',
    )
    return response


# The application already mounts this Studio router in app.main. Nesting the
# YouTube router here keeps the OAuth callback and private-upload lifecycle
# available without a second, easy-to-forget mount point.
from app.youtube_routes import router as youtube_router  # noqa: E402

router.include_router(youtube_router)
