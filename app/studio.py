from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
import json
import math
import re
import secrets
from typing import Any
from uuid import uuid4

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
    get_job,
    list_jobs,
    mark_failure,
    mark_retry_dispatch,
    mark_success,
    sync_repair_checkpoint_state,
    update_job,
)
from app.services.voice import get_selected_voice

router = APIRouter()
COOKIE_NAME = 'youtube_studio_token'

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
    'PENDING': 'Kuyrukta',
    'PROGRESS': 'Üretiliyor',
    'SUCCESS': 'Hazır',
    'FAILURE': 'Başarısız',
    'AWAITING_APPROVAL': 'Storyboard onayı',
}
STAGE_LABELS = {
    'queued': 'Kuyrukta',
    'research': 'Araştırma',
    'director_qc': 'Senaryo yönetmeni',
    'approved_plan': 'Onaylı storyboard',
    'voice_and_visuals': 'Ses ve görsel toplama',
    'audio_qc': 'Ses ve telaffuz denetimi',
    'visual_qc': 'Görsel kalite kontrolü',
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
UI_STATUS_ORDER = ('running', 'ready', 'repair', 'failed')
UI_STATUS_LABELS = {
    'running': 'Üretiliyor',
    'ready': 'Hazır',
    'repair': 'Onarım gerekli',
    'failed': 'Başarısız',
}
HISTORY_PAGE_SIZE = 12
HISTORY_SCAN_LIMIT = 500
LEGACY_RETRY_GROUP_WINDOW_SECONDS = 6 * 60 * 60
OPTIONAL_VIDEO_GENERATION_SERVICES = frozenset({'Fal video'})

BASE_CSS = r'''
:root{color-scheme:dark;--bg:#090c11;--surface:#111721;--surface-2:#0d131c;--line:#273142;--line-strong:#39465c;--text:#f3f6fb;--muted:#9da9ba;--soft:#c8d0db;--accent:#7967f5;--accent-2:#5b9cf6;--good:#51d593;--warn:#f5cd68;--bad:#ff7d88;--radius:16px}
*{box-sizing:border-box}html{background:var(--bg);scroll-behavior:smooth}body{margin:0;min-height:100vh;color:var(--text);font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;line-height:1.5;background:radial-gradient(circle at 15% -10%,rgba(75,91,161,.22),transparent 34%),var(--bg)}[hidden]{display:none!important}a{color:inherit;text-decoration:none}button,input,select,textarea{font:inherit}.skip-link{position:fixed;left:12px;top:8px;z-index:100;transform:translateY(-160%);padding:10px 14px;border-radius:10px;background:#fff;color:#111;font-weight:800}.skip-link:focus{transform:none}.wrap{max-width:1180px;margin:auto;padding:0 22px 72px}.top{position:sticky;top:0;z-index:30;display:flex;align-items:center;justify-content:space-between;gap:18px;min-height:68px;background:rgba(9,12,17,.92);backdrop-filter:blur(18px);border-bottom:1px solid rgba(57,70,92,.7)}.brand{font-weight:900;font-size:17px;letter-spacing:-.02em}.nav{display:flex;gap:6px;flex-wrap:wrap}.nav a{padding:8px 11px;border:1px solid transparent;border-radius:10px;color:var(--muted);font-size:13px;font-weight:750}.nav a:hover{color:var(--text);background:#151c28}.nav a.active,.nav a[aria-current=page]{color:#fff;background:#211e3b;border-color:#4c4385}.hero{display:flex;align-items:flex-end;justify-content:space-between;gap:24px;padding:34px 0 20px}.hero-copy{max-width:720px}.eyebrow{margin-bottom:8px;color:#a89dff;font-size:12px;font-weight:850;letter-spacing:.1em;text-transform:uppercase}.hero h1{font-size:clamp(30px,5vw,44px);line-height:1.08;margin:0 0 10px;letter-spacing:-.04em}.hero-tools{display:flex;justify-content:flex-end;gap:6px;flex-wrap:wrap}.muted{color:var(--muted)}.tiny{font-size:12px;color:#8f9bad}.layout{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(290px,.72fr);gap:18px;align-items:start}.card{background:rgba(17,23,33,.95);border:1px solid var(--line);border-radius:var(--radius);padding:20px;box-shadow:0 18px 48px rgba(0,0,0,.14);margin-bottom:14px}.card h2,.card h3{margin:0 0 8px;letter-spacing:-.02em}.section-title{display:flex;justify-content:space-between;align-items:center;gap:12px}.section-kicker{display:block;margin-bottom:4px;color:#8e9aad;font-size:12px;font-weight:800}.grid2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.grid3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.choice-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin:14px 0}.choice{position:relative}.choice input{position:absolute;opacity:0}.choice label{display:block;height:100%;margin:0;padding:15px;border:1px solid var(--line-strong);border-radius:13px;background:var(--surface-2);cursor:pointer;min-height:88px}.choice input:checked+label{border-color:#8372ff;background:#201c39;box-shadow:0 0 0 3px rgba(131,114,255,.12)}.choice input:focus-visible+label{outline:3px solid rgba(118,170,255,.55);outline-offset:2px}.choice b{display:block;margin-bottom:4px}.choice span{display:block;font-size:12px;color:var(--muted);line-height:1.4}label.field{display:block;margin:14px 0 6px;color:#dfe5ee;font-size:13px;font-weight:800}.field-hint{display:block;margin-top:-2px;color:var(--muted);font-size:12px}.topic-input{min-height:84px}input[type=text],input[type=password],input[type=url],textarea,select{width:100%;border:1px solid var(--line-strong);border-radius:11px;background:#0a1018;color:#fff;padding:12px 13px;outline:none}textarea{min-height:118px;resize:vertical}input:focus,textarea:focus,select:focus{border-color:#8271ff;box-shadow:0 0 0 3px rgba(130,113,255,.14)}button,.btn{display:inline-flex;align-items:center;justify-content:center;gap:7px;min-height:42px;border:1px solid transparent;border-radius:11px;padding:10px 14px;background:var(--accent);color:#fff;font-weight:850;cursor:pointer}.btn:hover,button:hover{filter:brightness(1.08)}.btn.secondary{background:#171f2c;border-color:#364258}.btn.success{background:#167d51}.btn.danger{background:#852f3a}.btn.repair{background:#8a6619}.btn.small{min-height:36px;padding:7px 11px;font-size:12px}.btn.block,button.block{width:100%;margin-top:16px}a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible,summary:focus-visible{outline:3px solid rgba(118,170,255,.62);outline-offset:3px}.control-details,.system-details{border:1px solid var(--line);border-radius:13px;background:var(--surface-2)}.control-details{margin-top:18px}.control-details>summary,.system-details>summary{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:14px 15px;cursor:pointer;font-weight:850;list-style:none}.control-details>summary::-webkit-details-marker,.system-details>summary::-webkit-details-marker{display:none}.control-details>summary:after,.system-details>summary:after{content:'+';color:var(--muted);font-size:18px}.control-details[open]>summary:after,.system-details[open]>summary:after{content:'−'}.control-body,.system-body{padding:0 15px 15px;border-top:1px solid var(--line)}.guidance{margin:14px 0 4px;padding:12px;border:1px solid var(--line);border-radius:11px;background:#0a1018}.guidance b{display:block;margin-bottom:4px}.guidance p{margin:0;line-height:1.5}.status-summary{display:flex;align-items:center;gap:9px}.health-dot,.dot{display:inline-block;width:9px;height:9px;border-radius:50%;flex:0 0 auto}.green{background:var(--good)}.amber{background:var(--warn)}.red{background:var(--bad)}.status-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;padding-top:14px}.service{display:flex;align-items:center;padding:9px 10px;border-radius:10px;border:1px solid var(--line);background:#0a1018;font-size:12px;font-weight:750}.badge{display:inline-flex;align-items:center;gap:6px;border:1px solid #354055;border-radius:999px;background:#111824;padding:6px 9px;font-size:12px;font-weight:750}.notice{border:1px solid #6b5b23;background:#29230f;color:#f5df88;border-radius:12px;padding:12px 14px;font-size:13px}.notice.error{border-color:#76313a;background:#30171c;color:#ffbac1}.notice.success{border-color:#285d45;background:#10291e;color:#9ee7bd}.status-overview{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:0 0 20px}.status-filter{display:grid;gap:2px;min-height:86px;padding:14px 15px;border:1px solid var(--line);border-radius:14px;background:rgba(17,23,33,.92);transition:border-color .15s ease,transform .15s ease}.status-filter:hover{border-color:#4c5a70;transform:translateY(-1px)}.status-filter[aria-current=page]{border-color:#8271ff;box-shadow:0 0 0 3px rgba(130,113,255,.12)}.status-filter .status-count{font-size:25px;font-weight:900;line-height:1}.status-filter .status-name{color:var(--soft);font-size:12px;font-weight:800}.status-filter.running{border-left:3px solid #7499ff}.status-filter.ready{border-left:3px solid var(--good)}.status-filter.repair{border-left:3px solid var(--warn)}.status-filter.failed{border-left:3px solid var(--bad)}.history-toolbar{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;margin:2px 0 12px}.history-toolbar h2{margin:0}.job-list{display:grid;gap:10px}.job{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:11px 18px;align-items:start;padding:15px;border:1px solid var(--line);border-radius:14px;background:var(--surface-2)}.job:hover{border-color:#3b4960}.job-main{min-width:0}.job-title{display:-webkit-box;overflow:hidden;-webkit-box-orient:vertical;-webkit-line-clamp:2;line-clamp:2;font-size:15px;font-weight:850;line-height:1.35}.job-status{display:-webkit-box;overflow:hidden;-webkit-box-orient:vertical;-webkit-line-clamp:2;line-clamp:2;margin-top:6px;color:#b1bdcd;font-size:12px;line-height:1.45}.job-meta{display:flex;align-items:center;gap:5px 12px;flex-wrap:wrap;margin-top:9px;color:#8794a7;font-size:11px}.job-meta span,.job-meta time{white-space:nowrap}.job-meta b{color:#aeb9c9;font-weight:750}.job-side{display:grid;justify-items:end;gap:10px;min-width:148px}.job-side form{margin:0}.state{display:inline-flex;align-items:center;min-height:27px;font-size:11px;font-weight:900;padding:5px 8px;border-radius:999px;background:#222b3a;white-space:nowrap}.state.running{background:#192945;color:#9cb8ff}.state.ready{background:#123a28;color:#80e7ab}.state.repair{background:#3d3316;color:#ffe187}.state.failed{background:#3d1b22;color:#ffa1aa}.job-details{grid-column:1/-1;border-top:1px solid var(--line);padding-top:8px}.job-details>summary{width:max-content;max-width:100%;cursor:pointer;color:#8f9bad;font-size:11px;font-weight:750}.detail-body{display:grid;gap:10px;margin-top:9px;padding:11px;border-radius:10px;background:#090e15;color:#b9c4d2;font-size:12px}.detail-body dl{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:5px 10px;margin:0}.detail-body dt{color:#7f8da1}.detail-body dd{margin:0;min-width:0;overflow-wrap:anywhere}.detail-copy{margin:0;white-space:pre-wrap;overflow-wrap:anywhere}.job.compact{padding:12px}.job.compact .job-side{grid-column:1/-1;grid-template-columns:1fr auto;align-items:center;justify-items:start;min-width:0}.job.compact .job-side .btn{justify-self:end}.job-panel{max-width:820px}.job-panel-head{display:flex;align-items:center;justify-content:space-between;gap:12px}.job-panel .job-status{font-size:14px;margin-top:11px}.progress{height:10px;border:1px solid #344054;background:#090e16;border-radius:999px;overflow:hidden}.bar{height:100%;width:0;background:linear-gradient(90deg,var(--accent),var(--accent-2));transition:width .35s ease}.stage{font-size:14px;font-weight:850}.result-action{margin-top:16px}.result-action form{margin:0}.technical-details{margin-top:18px;border-top:1px solid var(--line);padding-top:10px}.technical-details>summary{cursor:pointer;color:#8f9bad;font-size:12px;font-weight:750}.technical-body{display:grid;gap:10px;margin-top:10px;padding:12px;border-radius:10px;background:#090e15;color:#b8c3d1;font-size:12px}.technical-body code{white-space:pre-wrap;overflow-wrap:anywhere}.page-links{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:18px}.page-links .btn[aria-disabled=true]{pointer-events:none;opacity:.45}.back-links{display:flex;gap:16px;flex-wrap:wrap;margin-top:16px;color:#9ba8b9;font-size:13px}.back-links a{text-decoration:underline;text-underline-offset:3px}.scene{display:grid;grid-template-columns:48px 1fr;gap:13px;padding:15px 0;border-bottom:1px solid var(--line)}.scene:last-child{border:0}.scene-no{width:40px;height:40px;border-radius:11px;background:#251f43;display:flex;align-items:center;justify-content:center;font-weight:900}.queries{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}.query{font-size:11px;padding:5px 7px;border-radius:8px;background:#0d151f;border:1px solid #2b394a;color:#9fb0c4}.metric{padding:12px;border:1px solid var(--line);border-radius:12px;background:var(--surface-2)}.metric b{font-size:20px;display:block}.actions{display:flex;gap:9px;flex-wrap:wrap;margin-top:12px}.empty{padding:26px;border:1px dashed var(--line-strong);border-radius:13px;color:var(--muted);text-align:center}.sidebar-copy{margin:0;font-size:13px;line-height:1.55}
@media(max-width:900px){.layout{grid-template-columns:1fr}.hero{align-items:flex-start;flex-direction:column}.hero-tools{justify-content:flex-start}.status-overview{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:650px){.wrap{padding:0 12px 56px}.top{position:static;align-items:flex-start;flex-direction:column;padding:14px 0}.nav{width:100%;overflow-x:auto;flex-wrap:nowrap;padding-bottom:2px;scrollbar-width:none}.nav::-webkit-scrollbar{display:none}.nav a{white-space:nowrap}.nav a:nth-child(n+4){display:none}.hero{padding:26px 0 17px}.hero h1{font-size:31px}.grid2,.grid3,.choice-grid,.status-grid{grid-template-columns:1fr}.status-overview{gap:8px}.status-filter{min-height:76px;padding:12px}.status-filter .status-count{font-size:22px}.card{padding:16px}.history-toolbar{align-items:flex-start;flex-direction:column}.job{grid-template-columns:1fr}.job-side{grid-template-columns:1fr auto;align-items:center;justify-items:start;min-width:0}.job-side .btn,.job-side form{justify-self:end}.job-side form button{width:auto}.job.compact .job-side{grid-template-columns:1fr auto}.page-links .btn{min-width:0}.actions .btn{width:100%}.job-panel-head{align-items:flex-start}.control-details>summary,.system-details>summary{align-items:flex-start}.section-title{align-items:flex-start}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}.bar{transition:none}}
'''


def _valid_token(value: str | None) -> bool:
    return bool(settings.factory_api_token and value and value == settings.factory_api_token)


def _require_auth(cookie_token: str | None) -> None:
    if not _valid_token(cookie_token):
        raise HTTPException(status_code=401, detail='Studio oturumu gerekli')


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
        or channel.get('title')
        or spec.get('target_channel_title')
        or spec.get('channel_id')
        or spec.get('target_channel_id')
        or result.get('target_channel_id')
        or youtube.get('target_channel_id')
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


def _job_ui_status(job: dict) -> str:
    state = str(job.get('state') or 'PENDING').upper()
    if state == 'FAILURE':
        if _retry_claimed(job):
            return 'running'
        return 'repair' if job.get('repair_available') is True else 'failed'
    if state in {'SUCCESS', 'AWAITING_APPROVAL'}:
        return 'ready'
    return 'running'


def _job_status_message(job: dict) -> str:
    status = _job_ui_status(job)
    state = str(job.get('state') or 'PENDING').upper()
    kind = str(job.get('kind') or '')
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
            return 'Storyboard hazır; render için onayını bekliyor.'
        result = job.get('result') if isinstance(job.get('result'), dict) else {}
        youtube = result.get('youtube') if isinstance(result.get('youtube'), dict) else {}
        if kind == 'publish' or result.get('youtube_url') or youtube.get('url'):
            return 'Gizli YouTube yüklemesi tamamlandı.'
        return 'Video tamamlandı; izlemeye veya gizli yüklemeye hazır.'
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


def _job_primary_action(job: dict, *, small: bool = True) -> str:
    status = _job_ui_status(job)
    state = str(job.get('state') or 'PENDING').upper()
    task_id = escape(str(job.get('task_id') or ''), quote=True)
    size = ' small' if small else ''

    def aria(label: str) -> str:
        return escape(f'{_job_title(job)}: {label}', quote=True)

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
    if status == 'repair':
        return (
            f'<form method="post" action="/studio/retry/{task_id}">'
            f'<button class="btn repair{size}" type="submit" aria-label="{aria("Sorunlu sahneyi onar")}">Sorunlu sahneyi onar</button></form>'
        )
    if status == 'failed':
        return (
            f'<form method="post" action="/studio/retry/{task_id}">'
            f'<button class="btn danger{size}" type="submit" aria-label="{aria("Aynı ayarlarla tekrar dene")}">Aynı ayarlarla tekrar dene</button></form>'
        )
    if state == 'AWAITING_APPROVAL':
        return f'<a class="btn success{size}" href="/studio/plan/{task_id}" aria-label="{aria("Storyboard\'u aç")}">Storyboard\'u aç</a>'
    result = job.get('result') if isinstance(job.get('result'), dict) else {}
    youtube = result.get('youtube') if isinstance(result.get('youtube'), dict) else {}
    youtube_url = _safe_external_url(result.get('youtube_url') or youtube.get('url'))
    download_url = _safe_external_url(result.get('download_url'))
    if youtube_url:
        return f'<a class="btn success{size}" target="_blank" rel="noopener noreferrer" href="{escape(youtube_url, quote=True)}" aria-label="{aria("YouTube\'da aç")}">YouTube\'da aç</a>'
    if result.get('video_key') and str(job.get('kind') or '') == 'render':
        return f'<a class="btn success{size}" href="/studio/youtube" aria-label="{aria("Gizli yükle")}">Gizli yükle</a>'
    if download_url:
        return f'<a class="btn success{size}" target="_blank" rel="noopener noreferrer" href="{escape(download_url, quote=True)}" aria-label="{aria("Videoyu aç")}">Videoyu aç</a>'
    return f'<a class="btn secondary{size}" href="/studio/job/{task_id}" aria-label="{aria("Sonucu aç")}">Sonucu aç</a>'


def _job_details(job: dict) -> str:
    brief = _job_brief(job)
    error = _safe_ui_text(job.get('error'))
    task_id = escape(str(job.get('task_id') or ''))
    stage = escape(str(job.get('failure_stage') or job.get('stage') or '—'))
    internal_state = escape(str(job.get('state') or 'PENDING'))
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
        f'<dt>İş kimliği</dt><dd>{task_id}</dd><dt>İç durum</dt><dd>{internal_state}</dd>'
        f'<dt>Aşama kodu</dt><dd>{stage}</dd></dl></div></details>'
    )


def _status_counts(jobs: list[dict]) -> dict[str, int]:
    counts = {key: 0 for key in UI_STATUS_ORDER}
    for job in jobs:
        counts[_job_ui_status(job)] += 1
    return counts


def _dashboard_recent_jobs(jobs: list[dict], limit: int = 3) -> list[dict]:
    """Keep the landing page focused on work a person can continue.

    The complete failure history remains available through the status filter,
    but a burst of failed retry attempts must not displace running or completed
    videos from the three compact dashboard slots.
    """
    if limit < 1:
        return []
    running = [job for job in jobs if _job_ui_status(job) == 'running']
    repairs = [job for job in jobs if _job_ui_status(job) == 'repair']
    ready = [job for job in jobs if _job_ui_status(job) == 'ready']
    # Keep active work first, but reserve one compact slot for a repair that
    # needs human action instead of allowing a full running queue to hide it.
    repair_reserve = 1 if repairs and limit > 1 else 0
    visible = running[:limit - repair_reserve]
    visible.extend(repairs[:limit - len(visible)])
    visible.extend(ready[:limit - len(visible)])
    if not visible:
        latest_failure = next(
            (job for job in jobs if _job_ui_status(job) == 'failed'),
            None,
        )
        if latest_failure is not None:
            visible.append(latest_failure)
    return visible[:limit]


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


def _collapse_retry_sources(jobs: list[dict]) -> list[dict]:
    """Show only the newest visible step of each logical video workflow."""
    task_ids = {str(job.get('task_id') or '') for job in jobs}
    # A failed upload is an issue with publishing, not with the completed
    # video. Keep its ready render parent as the safe actionable card; the
    # YouTube center owns upload reconciliation and duplicate prevention.
    hidden_failed_publishes = {
        str(job.get('task_id') or '')
        for job in jobs
        if str(job.get('kind') or '') == 'publish'
        and str(job.get('state') or '').upper() == 'FAILURE'
        and str(job.get('parent_id') or '') in task_ids
    }
    superseded_ids = {
        str(job.get('parent_id') or '')
        for job in jobs
        if str(job.get('task_id') or '') not in hidden_failed_publishes
        if str(job.get('parent_id') or '') in task_ids
        and str(job.get('parent_id') or '') != str(job.get('task_id') or '')
    }
    visible = []
    for job in jobs:
        task_id = str(job.get('task_id') or '')
        child_id = _retry_child_task_id(job)
        if task_id in hidden_failed_publishes or task_id in superseded_ids or (
            child_id and child_id != task_id and child_id in task_ids
        ):
            continue
        visible.append(job)
    return _collapse_failure_duplicates(visible)


def _status_overview(counts: dict[str, int], *, active: str | None = None) -> str:
    links = []
    for key in UI_STATUS_ORDER:
        current = ' aria-current="page"' if key == active else ''
        links.append(
            f'<a class="status-filter {key}" data-status-filter="{key}" '
            f'href="/studio/history?status={key}"{current}>'
            f'<span class="status-count" data-status-count="{key}">{int(counts.get(key, 0))}</span>'
            f'<span class="status-name">{UI_STATUS_LABELS[key]}</span></a>'
        )
    return '<nav class="status-overview" aria-label="Üretim durumları">' + ''.join(links) + '</nav>'


def _nav(active: str) -> str:
    links = [
        ('studio', '/studio', '＋ Yeni üretim'),
        ('history', '/studio/history', '◷ Geçmiş'),
        ('youtube', '/studio/youtube', '▶ YouTube'),
        ('voices', '/voice-audition', '🎙 Sesler'),
        ('legacy', '/factory', 'Eski panel'),
    ]
    items = ''.join(
        f'<a class="{"active" if key == active else ""}" href="{url}"'
        f'{" aria-current=page" if key == active else ""}>{label}</a>'
        for key, url, label in links
    )
    return f'<header class="top"><a class="brand" href="/studio">🎬 YouTube Studio V2</a><nav class="nav" aria-label="Ana menü">{items}</nav></header>'


def _shell(body: str, *, active: str = 'studio', title: str = 'YouTube Studio V2', script: str = '') -> HTMLResponse:
    return HTMLResponse(
        '<!doctype html><html lang="tr"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        f'<meta name="theme-color" content="#080b11"><title>{escape(title)}</title>'
        f'<style>{BASE_CSS}</style></head><body><a class="skip-link" href="#main-content">İçeriğe geç</a>'
        f'<div class="wrap">{_nav(active)}<main id="main-content" tabindex="-1">{body}</main></div>{script}</body></html>'
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
    }


def _sync_job(task_id: str) -> dict:
    record = get_job(task_id) or {'task_id': task_id, 'spec': {}, 'state': 'PENDING', 'progress': 0}
    task = AsyncResult(task_id, app=celery)
    state = task.state

    if state == 'FAILURE':
        record = mark_failure(task_id, str(task.result))
    elif state == 'SUCCESS':
        result = task.result if isinstance(task.result, dict) else {'result': str(task.result)}
        if result.get('status') == 'plan_ready':
            record = mark_success(task_id, result, state='AWAITING_APPROVAL')
        else:
            record = mark_success(task_id, result)
    elif isinstance(task.info, dict):
        record = update_job(
            task_id,
            state=state,
            stage=task.info.get('stage') or record.get('stage'),
            progress=task.info.get('progress') if task.info.get('progress') is not None else record.get('progress', 0),
            message=task.info.get('message') or record.get('message'),
        )
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
        ) and not _retry_claimed(job)
        if task_id and should_refresh:
            synced = _sync_job(task_id)
            grouped_attempts = job.get('_grouped_failure_attempts')
            if grouped_attempts:
                synced = {
                    **synced,
                    '_grouped_failure_attempts': grouped_attempts,
                }
            refreshed.append(synced)
        else:
            refreshed.append(job)
    return refreshed


@router.get('/studio', response_class=HTMLResponse)
def studio_home(studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    authenticated = _valid_token(studio_token)
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
        health_label = f'{optional_names} isteğe bağlı · üretim çalışır'
    else:
        health_label = 'Tüm servis ayarları hazır'
    jobs = _collapse_retry_sources(list_jobs(HISTORY_SCAN_LIMIT)) if authenticated else []
    # Reconcile only the priority candidates. This keeps the landing page
    # truthful without probing all retained Celery results as history grows.
    priority_candidates = _dashboard_recent_jobs(jobs)
    if priority_candidates:
        refreshed_by_id = {
            str(job.get('task_id') or ''): job
            for job in _refresh_active_jobs(priority_candidates)
        }
        jobs = [
            refreshed_by_id.get(str(job.get('task_id') or ''), job)
            for job in jobs
        ]
    recent = _dashboard_recent_jobs(jobs)
    recent_html = ''.join(_job_row(job, compact=True) for job in recent) or '<div class="empty">Henüz kayıtlı üretim yok.</div>'
    counts = _status_counts(jobs)
    overview = _status_overview(counts) if authenticated else ''
    failure_history = (
        '<div class="tiny">'
        f'{counts["failed"]} başarısız iş geçmişte saklanıyor. '
        '<a href="/studio/history?status=failed">Yalnızca gerekirse aç →</a>'
        '</div>'
        if authenticated and counts['failed']
        else ''
    )
    token_field = (
        '<div class="notice success">Güvenli Studio oturumu açık.</div>'
        if authenticated else
        '<label class="field" for="studio-token">Studio güvenlik anahtarı</label><input id="studio-token" name="token" type="password" autocomplete="off" required placeholder="Güvenli anahtarı gir">'
    )

    body = f'''
<div class="hero"><div class="hero-copy"><div class="eyebrow">STUDIO</div><h1>Üretim masası</h1><div class="muted">Devam eden, hazır ve ilgi isteyen videoları durumuna göre takip et; yeni üretimi aynı yerden başlat.</div></div><div class="hero-tools"><span class="badge">🎙 {selected_voice}</span><span class="badge">16:9 · 1080p</span></div></div>
{overview}
<div class="layout"><section>
<form action="/studio/start" method="post" class="card" id="studio-form">
<span class="section-kicker">YENİ ÜRETİM</span><h2>Nasıl bir video hazırlayalım?</h2>
<div class="choice-grid">
<div class="choice"><input id="mode-preview" name="mode" value="preview" type="radio" checked><label for="mode-preview"><b>⚡ Hızlı önizleme</b><span>30–60 saniyelik kalite testi. Hızlı karar vermek için.</span></label></div>
<div class="choice"><input id="mode-production" name="mode" value="production" type="radio"><label for="mode-production"><b>🏆 Yayın kalitesi</b><span>Uzun video, tam kalite denetimi ve zengin kurgu.</span></label></div>
</div>
<label class="field" for="topic">Video konusu</label>
<span class="field-hint">Bir cümle yeterli. Örnek: Telefon neden yastık altında ısınır?</span>
<textarea class="topic-input" id="topic" name="topic" required placeholder="Konuyu bir cümleyle yaz"></textarea>
<div class="grid2"><div><label class="field" for="duration">Süre</label><select id="duration" name="duration_minutes"><option value="0.5" selected>30 saniye</option><option value="1">1 dakika</option><option value="3">3 dakika</option><option value="5">5 dakika</option><option value="8">8 dakika</option><option value="10">10 dakika</option></select></div><div><label class="field" for="language">Dil</label><select id="language" name="language"><option value="tr" selected>Türkçe</option><option value="en">English</option><option value="de">Deutsch</option><option value="es">Español</option><option value="ar">العربية</option></select></div></div>
<details class="control-details"><summary><span>Yaratıcı ve teknik ayarlar</span><span class="tiny">İsteğe bağlı</span></summary><div class="control-body">
<div class="guidance"><b>İyi sonuç için ayrıntı eklemek istersen</b><p class="tiny">Tek bir gündelik sorun, tek bir şaşırtıcı neden, aynı kişi veya nesne, aynı mekân ve görünür bir sonuç tarif et. Bunları yazmak zorunda değilsin; sistem kısa konu cümleni otomatik olarak yönetmen planına dönüştürür.</p></div>
<div class="grid2"><div><label class="field" for="content-style">İçerik tarzı</label><select id="content-style" name="content_style"><option value="documentary">Belgesel</option><option value="technology" selected>Teknoloji</option><option value="story">Hikâye</option><option value="cinematic">Sinematik</option><option value="explainer">Açıklayıcı</option></select></div><div><label class="field" for="pace">Kurgu temposu</label><select id="pace" name="pace"><option value="calm">Sakin</option><option value="balanced" selected>Dengeli</option><option value="dynamic">Dinamik</option></select></div></div>
<div class="grid2"><div><label class="field" for="visual-mix">Görsel karışımı</label><select id="visual-mix" name="visual_mix"><option value="real_first">Gerçek görüntü ağırlıklı</option><option value="balanced" selected>Dengeli: B-roll + AI</option><option value="ai_first">Özgün AI ağırlıklı</option></select></div><div><label class="field" for="workflow">Akış</label><select id="workflow" name="workflow"><option value="auto" selected>Otomatik tamamla</option><option value="storyboard">Önce storyboard göster</option></select></div></div>
<div class="grid2"><div><label class="field" for="music">Arka plan müziği</label><select id="music" name="music"><option value="off">Kapalı</option><option value="auto" selected>Uygunsa otomatik</option></select></div><div><label class="field" for="subtitles">Altyazı</label><select id="subtitles" name="subtitles"><option value="sidecar" selected>Ayrı SRT üret</option><option value="off">Üretme</option></select></div></div>
<label class="field" for="reference-url">Referans video / kanal bağlantısı <span class="tiny">(yalnızca yapı ve ritim analizi)</span></label><input id="reference-url" name="reference_url" type="url" placeholder="YouTube videosu veya kanal bağlantısı">
<label class="field" for="channel-id">Kanal etiketi <span class="tiny">(opsiyonel)</span></label><input id="channel-id" name="channel_id" type="text" maxlength="120" placeholder="teknoloji-tr-01">
<div class="actions"><a class="btn secondary small" href="/voice-audition">🎙 Anlatıcı sesini değiştir</a></div>
</div></details>
{token_field}
<button class="block" type="submit">Üretimi başlat →</button>
</form></section>
<aside><div class="card"><details class="system-details"><summary><span class="status-summary"><span class="health-dot {"red" if required_missing else "green"}" aria-hidden="true"></span>{health_label}</span><span class="tiny">{ready_services}/{len(service_states)}</span></summary><div class="system-body"><div class="status-grid">{services}</div></div></details></div><div class="card"><div class="section-title"><div><span class="section-kicker">ÖNCELİKLİ İŞLER</span><h3>Devam et</h3></div><a class="tiny" href="/studio/history">Tüm durumlar →</a></div><div class="job-list">{recent_html}</div>{failure_history}</div><div class="card"><span class="section-kicker">GÜVENLİ YAYIN</span><h3>Kontrol sende</h3><p class="muted sidebar-copy">Videolar önce gizli yüklenir. Kalite onayından önce herkese açık yayın yapılmaz.</p></div></aside></div>
'''
    script = r'''<script>
const preview=document.getElementById('mode-preview'),production=document.getElementById('mode-production'),duration=document.getElementById('duration');
function setDefaults(){if(production.checked){duration.value='5';document.querySelector('[name=workflow]').value='storyboard';document.querySelector('[name=music]').value='auto';}else{duration.value='0.5';document.querySelector('[name=workflow]').value='auto';document.querySelector('[name=music]').value='off';}}
preview.addEventListener('change',setDefaults);production.addEventListener('change',setDefaults);
</script>'''
    return _shell(body, script=script)


def _job_row(job: dict, *, compact: bool = False) -> str:
    status = _job_ui_status(job)
    mode = _job_mode(job)
    raw_title = _job_title(job)
    title = escape(raw_title)
    duration = _job_duration(job)
    date = _job_date(job)
    channel = _job_channel(job)
    profile = _job_profile(job)
    if channel and profile and channel.casefold() != profile.casefold():
        target = f'{channel} / {profile}'
    else:
        target = channel or profile or 'Seçilmedi'
    raw_updated = str(job.get('updated_at') or job.get('created_at') or '')
    metadata = [f'<span><b>Hedef / profil</b> {escape(target)}</span>']
    if mode:
        metadata.append(f'<span>{escape(mode)}</span>')
    if duration:
        metadata.append(f'<span>{escape(duration)}</span>')
    if date:
        metadata.append(
            f'<time datetime="{escape(raw_updated, quote=True)}">Güncellendi {escape(date)}</time>'
        )
    meta_html = ''.join(metadata)
    details = '' if compact else _job_details(job)
    return (
        f'<article class="job{" compact" if compact else ""}" data-status="{status}" '
        f'aria-label="{title}: {UI_STATUS_LABELS[status]}"><div class="job-main">'
        f'<div class="job-title">{title}</div><div class="job-status">{escape(_job_status_message(job))}</div>'
        f'<div class="job-meta" aria-label="Video bilgileri">{meta_html}</div></div>'
        f'<div class="job-side"><span class="state {status}">{UI_STATUS_LABELS[status]}</span>'
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
    )
    options = {key: value for key, value in spec.items() if key not in {'topic', 'duration_minutes', 'language', 'channel_id'}}
    if workflow == 'storyboard':
        task = plan_video_pipeline.delay(topic, duration_minutes, language, channel_id.strip() or None, options)
        kind = 'plan'
    else:
        task = run_video_pipeline.delay(topic, duration_minutes, language, channel_id.strip() or None, options, None)
        kind = 'render'
    create_job(task.id, spec, kind=kind)
    response = RedirectResponse(f'/studio/job/{task.id}', status_code=303)
    response.set_cookie(COOKIE_NAME, credential, max_age=60 * 60 * 24 * 30, httponly=True, secure=True, samesite='strict')
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
    display_title = escape(_job_title(record))
    status = _job_ui_status(record)
    progress = _job_progress(record)
    stage_code = str(record.get('failure_stage') or record.get('stage') or 'queued')
    stage_label = escape(STAGE_LABELS.get(stage_code, stage_code))
    initial_error = _safe_ui_text(record.get('error'))
    error_hidden = '' if initial_error else ' hidden'
    progress_hidden = '' if status == 'running' else ' hidden'
    body = f'''
<div class="hero"><div class="hero-copy"><div class="eyebrow">Üretim durumu</div><h1>{display_title}</h1><div class="muted">Yalnızca karar vermen gereken durum ve sonraki adım burada gösterilir.</div></div></div>
<article class="card job-panel" id="job-card" data-status="{status}">
<div class="job-panel-head"><div class="stage" id="stage">{stage_label}{f' · %{progress}' if status == 'running' else ''}</div><span class="state {status}" id="state-label">{UI_STATUS_LABELS[status]}</span></div>
<div class="job-status" id="status-message" role="status" aria-live="polite" aria-atomic="true">{escape(_job_status_message(record))}</div>
<div class="progress" id="progress" role="progressbar" aria-label="Üretim ilerlemesi" aria-valuemin="0" aria-valuemax="100" aria-valuenow="{progress}" style="margin:14px 0"{progress_hidden}><div class="bar" id="bar" style="width:{progress}%"></div></div>
<div class="result-action" id="result">{_job_primary_action(record, small=False)}</div>
<details class="technical-details"><summary>Teknik ayrıntılar</summary><div class="technical-body"><div><b>İş kimliği</b><br><code>{escape(task_id)}</code></div><div><b>Aşama kodu</b><br><code id="technical-stage">{escape(stage_code)}</code></div><div id="technical-error-row"{error_hidden}><b>Hata kaydı</b><br><code id="technical-error">{escape(initial_error)}</code></div></div></details>
</article>
<nav class="back-links" aria-label="Geri dön"><a href="/studio/history?status={status}">Üretim listesine dön</a><a href="/studio">Yeni üretim başlat</a></nav>
'''
    script = r'''<script>
const taskId=__TASK_ID__;let timer=null;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels={running:'Üretiliyor',ready:'Hazır',repair:'Onarım gerekli',failed:'Başarısız'};
function safeExternal(value){try{const u=new URL(String(value||''),window.location.origin);return ['http:','https:'].includes(u.protocol)?u.href:''}catch(_){return ''}}
function linkAction(href,label,kind='secondary',external=false){return `<a class="btn ${kind}" ${external?'target="_blank" rel="noopener noreferrer" ':''}href="${esc(href)}">${esc(label)}</a>`}
function retryAction(label,kind){return `<form method="post" action="/studio/retry/${encodeURIComponent(taskId)}"><button class="btn ${kind}" type="submit">${esc(label)}</button></form>`}
function setAction(signature,html){const out=document.getElementById('result');if(out.dataset.actionSignature===signature)return;out.innerHTML=html;out.dataset.actionSignature=signature}
function setStatusMessage(message){const out=document.getElementById('status-message'),next=String(message||'');if(out.textContent!==next)out.textContent=next}
function showTechnical(j){const stage=String(j.failure_stage||j.stage||'—');document.getElementById('technical-stage').textContent=stage;const error=String(j.error||'').trim();document.getElementById('technical-error').textContent=error;document.getElementById('technical-error-row').hidden=!error}
async function poll(){
 try{const r=await fetch(`/studio/api/job/${encodeURIComponent(taskId)}`,{cache:'no-store'});if(!r.ok)throw new Error('status');const j=await r.json();
 const state=String(j.state||'PENDING'),ui=String(j.ui_status||'running'),stage=String(j.stage_label||j.stage||'Hazırlanıyor'),p=Math.max(0,Math.min(100,Number(j.progress||0)));
 const panel=document.getElementById('job-card'),progress=document.getElementById('progress'),out=document.getElementById('result'),pill=document.getElementById('state-label');
 panel.dataset.status=ui;pill.className='state '+ui;pill.textContent=labels[ui]||labels.running;
 document.getElementById('bar').style.width=p+'%';progress.setAttribute('aria-valuenow',String(p));progress.hidden=ui!=='running';document.getElementById('stage').textContent=stage+(ui==='running'?' · %'+p:'');setStatusMessage(j.ui_status_message);showTechnical(j);
 if(ui==='repair'){setAction('repair',retryAction('Sorunlu sahneyi onar','repair'));return}
 if(ui==='failed'){setAction('failed',retryAction('Aynı ayarlarla tekrar dene','danger'));return}
 if(ui==='ready'&&state==='AWAITING_APPROVAL'){setAction('storyboard',linkAction(`/studio/plan/${encodeURIComponent(taskId)}`,"Storyboard'u aç",'success'));return}
 if(ui==='ready'){const x=j.result||{},youtube=safeExternal(x.youtube_url||(x.youtube||{}).url),download=safeExternal(x.download_url);if(youtube)setAction('youtube:'+youtube,linkAction(youtube,"YouTube'da aç",'success',true));else if(x.video_key&&j.kind==='render')setAction('private-upload',linkAction('/studio/youtube','Gizli yükle','success'));else if(download)setAction('download:'+download,linkAction(download,'Videoyu aç','success',true));else setAction('ready-refresh',linkAction(`/studio/job/${encodeURIComponent(taskId)}`,'Sonucu yenile'));return}
 const child=String(j.retry_child_task_id||'').trim(),target=child||taskId,label=child?(j.repair_claimed?'Onarım durumunu aç':'Yeniden denemeyi aç'):'Durumu yenile';setAction('running:'+target,linkAction(`/studio/job/${encodeURIComponent(target)}`,label));timer=setTimeout(poll,3000);
 }catch(_){setStatusMessage('Durum geçici olarak alınamıyor. Tekrar denenecek.');timer=setTimeout(poll,5000)}
 }
poll();
</script>'''.replace('__TASK_ID__', json.dumps(task_id))
    return _shell(body, title='Üretim kontrolü', script=script)


@router.get('/studio/api/job/{task_id}')
def studio_job_api(task_id: str, studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    _require_auth(studio_token)
    record = _sync_job(task_id)
    payload = dict(record)
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
    payload['ui_status_message'] = _job_status_message(payload)
    return JSONResponse(payload)


@router.get('/studio/history', response_class=HTMLResponse)
def studio_history(
    status: str = 'running',
    page: int = 1,
    studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
):
    _require_auth(studio_token)
    active = status if status in UI_STATUS_ORDER else 'running'
    page = max(1, int(page))
    jobs = _collapse_retry_sources(list_jobs(HISTORY_SCAN_LIMIT))
    # Reconcile only the records that can appear on this page. The registry is
    # retained at 500 jobs; probing each Celery result would create an N+1 read
    # storm just to render the overview counts.
    stored_filtered = [job for job in jobs if _job_ui_status(job) == active]
    stored_page_count = max(1, math.ceil(len(stored_filtered) / HISTORY_PAGE_SIZE))
    page = min(page, stored_page_count)
    offset = (page - 1) * HISTORY_PAGE_SIZE
    candidates = stored_filtered[offset:offset + HISTORY_PAGE_SIZE]
    refreshed_by_id = {
        str(job.get('task_id') or ''): job
        for job in _refresh_active_jobs(candidates)
    }
    jobs = [
        refreshed_by_id.get(str(job.get('task_id') or ''), job)
        for job in jobs
    ]
    counts = _status_counts(jobs)
    filtered = [job for job in jobs if _job_ui_status(job) == active]
    total = len(filtered)
    page_count = max(1, math.ceil(total / HISTORY_PAGE_SIZE))
    page = min(page, page_count)
    start = (page - 1) * HISTORY_PAGE_SIZE
    visible = filtered[start:start + HISTORY_PAGE_SIZE]
    empty_copy = {
        'running': 'Devam eden üretim yok.',
        'ready': 'Hazır video yok.',
        'repair': 'Onarım bekleyen video yok.',
        'failed': 'Başarısız üretim yok.',
    }[active]
    rows = ''.join(_job_row(job) for job in visible) or f'<div class="empty">{empty_copy}</div>'
    previous = (
        f'<a class="btn secondary small" href="/studio/history?status={active}&amp;page={page - 1}">← Önceki</a>'
        if page > 1 else '<span class="btn secondary small" aria-disabled="true">← Önceki</span>'
    )
    following = (
        f'<a class="btn secondary small" href="/studio/history?status={active}&amp;page={page + 1}">Sonraki →</a>'
        if page < page_count else '<span class="btn secondary small" aria-disabled="true">Sonraki →</span>'
    )
    pagination = (
        f'<nav class="page-links" aria-label="Geçmiş sayfaları">{previous}'
        f'<span class="tiny">Sayfa {page} / {page_count}</span>{following}</nav>'
        if total > HISTORY_PAGE_SIZE else ''
    )
    history_context = (
        'Önce devam eden videolar gösterilir. Hazır, onarım ve başarısız '
        'kayıtlara yukarıdaki durum kartlarından geçebilirsin.'
        if active == 'running'
        else f'Şu anda yalnızca {UI_STATUS_LABELS[active].lower()} videolar gösteriliyor.'
    )
    body = f'''
<div class="hero"><div class="hero-copy"><div class="eyebrow">ÜRETİM TAKİBİ</div><h1>Videolar</h1><div class="muted">{history_context}</div></div><div class="hero-tools"><span class="badge">Toplam {len(jobs)} görünür video</span></div></div>
{_status_overview(counts, active=active)}
<div class="history-toolbar"><div><span class="section-kicker">{UI_STATUS_LABELS[active].upper()}</span><h2>{counts[active]} video</h2></div><span class="tiny">Bu görünüm: {UI_STATUS_LABELS[active]} · en fazla {HISTORY_PAGE_SIZE} iş</span></div>
<div class="job-list" data-history-status="{active}">{rows}</div>{pagination}
'''
    return _shell(body, active='history', title='Üretim geçmişi')


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
    return _shell(body, title='Storyboard')


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
