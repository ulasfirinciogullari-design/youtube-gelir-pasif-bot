from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
import json
import re
from typing import Any

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
    create_job,
    get_job,
    list_jobs,
    mark_failure,
    mark_success,
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
MODE_LABELS = {'preview': 'Hızlı önizleme', 'production': 'Yayın kalitesi'}
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
    'render': 'Final kurgu',
    'upload': 'Depolamaya yükleme',
    'awaiting_approval': 'Storyboard onayı',
    'complete': 'Tamamlandı',
    'failed': 'Başarısız',
}

BASE_CSS = r'''
:root{color-scheme:dark;--bg:#090c11;--surface:#111721;--surface-2:#0d131c;--line:#273142;--line-strong:#39465c;--text:#f3f6fb;--muted:#9da9ba;--soft:#c8d0db;--accent:#7967f5;--accent-2:#5b9cf6;--good:#51d593;--warn:#f5cd68;--bad:#ff7d88;--radius:16px}
*{box-sizing:border-box}html{background:var(--bg);scroll-behavior:smooth}body{margin:0;min-height:100vh;color:var(--text);font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;line-height:1.5;background:radial-gradient(circle at 15% -10%,rgba(75,91,161,.25),transparent 34%),radial-gradient(circle at 95% 0,rgba(92,58,142,.18),transparent 30%),var(--bg)}a{color:inherit;text-decoration:none}button,input,select,textarea{font:inherit}.skip-link{position:fixed;left:12px;top:8px;z-index:100;transform:translateY(-160%);padding:10px 14px;border-radius:10px;background:#fff;color:#111;font-weight:800}.skip-link:focus{transform:none}.wrap{max-width:1180px;margin:auto;padding:0 22px 72px}.top{position:sticky;top:0;z-index:30;display:flex;align-items:center;justify-content:space-between;gap:18px;min-height:68px;background:rgba(9,12,17,.9);backdrop-filter:blur(18px);border-bottom:1px solid rgba(57,70,92,.7)}.brand{font-weight:900;font-size:17px;letter-spacing:-.02em}.nav{display:flex;gap:6px;flex-wrap:wrap}.nav a{padding:8px 11px;border:1px solid transparent;border-radius:10px;color:var(--muted);font-size:13px;font-weight:750}.nav a:hover{color:var(--text);background:#151c28}.nav a.active,.nav a[aria-current=page]{color:#fff;background:#211e3b;border-color:#4c4385}.hero{display:flex;align-items:flex-end;justify-content:space-between;gap:24px;padding:38px 0 22px}.hero-copy{max-width:720px}.eyebrow{margin-bottom:8px;color:#a89dff;font-size:12px;font-weight:850;letter-spacing:.1em;text-transform:uppercase}.hero h1{font-size:clamp(32px,5vw,46px);line-height:1.05;margin:0 0 10px;letter-spacing:-.045em}.hero-tools{display:flex;justify-content:flex-end;gap:6px;flex-wrap:wrap}.muted{color:var(--muted)}.tiny{font-size:12px;color:#8f9bad}.layout{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(290px,.72fr);gap:18px;align-items:start}.card{background:rgba(17,23,33,.94);border:1px solid var(--line);border-radius:var(--radius);padding:20px;box-shadow:0 20px 55px rgba(0,0,0,.16);margin-bottom:14px}.card h2,.card h3{margin:0 0 8px;letter-spacing:-.02em}.section-title{display:flex;justify-content:space-between;align-items:center;gap:12px}.section-kicker{display:block;margin-bottom:4px;color:#8e9aad;font-size:12px;font-weight:800}.grid2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.grid3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.choice-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin:14px 0}.choice{position:relative}.choice input{position:absolute;opacity:0}.choice label{display:block;height:100%;margin:0;padding:15px;border:1px solid var(--line-strong);border-radius:13px;background:var(--surface-2);cursor:pointer;min-height:88px}.choice input:checked+label{border-color:#8372ff;background:#201c39;box-shadow:0 0 0 3px rgba(131,114,255,.12)}.choice input:focus-visible+label{outline:3px solid rgba(118,170,255,.55);outline-offset:2px}.choice b{display:block;margin-bottom:4px}.choice span{display:block;font-size:12px;color:var(--muted);line-height:1.4}label.field{display:block;margin:14px 0 6px;color:#dfe5ee;font-size:13px;font-weight:800}input[type=text],input[type=password],input[type=url],textarea,select{width:100%;border:1px solid var(--line-strong);border-radius:11px;background:#0a1018;color:#fff;padding:12px 13px;outline:none}textarea{min-height:118px;resize:vertical}input:focus,textarea:focus,select:focus{border-color:#8271ff;box-shadow:0 0 0 3px rgba(130,113,255,.14)}button,.btn{display:inline-flex;align-items:center;justify-content:center;gap:7px;min-height:42px;border:1px solid transparent;border-radius:11px;padding:10px 14px;background:var(--accent);color:#fff;font-weight:850;cursor:pointer}.btn:hover,button:hover{filter:brightness(1.08)}.btn.secondary{background:#171f2c;border-color:#364258}.btn.success{background:#167d51}.btn.danger{background:#852f3a}.btn.small{min-height:36px;padding:7px 11px;font-size:12px}.btn.block,button.block{width:100%;margin-top:16px}a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible,summary:focus-visible{outline:3px solid rgba(118,170,255,.62);outline-offset:3px}.control-details,.system-details,.brief-details{border:1px solid var(--line);border-radius:13px;background:var(--surface-2)}.control-details{margin-top:18px}.control-details>summary,.system-details>summary{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:14px 15px;cursor:pointer;font-weight:850;list-style:none}.control-details>summary::-webkit-details-marker,.system-details>summary::-webkit-details-marker{display:none}.control-details>summary:after,.system-details>summary:after{content:'+';color:var(--muted);font-size:18px}.control-details[open]>summary:after,.system-details[open]>summary:after{content:'−'}.control-body,.system-body{padding:0 15px 15px;border-top:1px solid var(--line)}.status-summary{display:flex;align-items:center;gap:9px}.health-dot,.dot{display:inline-block;width:9px;height:9px;border-radius:50%;flex:0 0 auto}.green{background:var(--good)}.red{background:var(--bad)}.status-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;padding-top:14px}.service{display:flex;align-items:center;padding:9px 10px;border-radius:10px;border:1px solid var(--line);background:#0a1018;font-size:12px;font-weight:750}.badge{display:inline-flex;align-items:center;gap:6px;border:1px solid #354055;border-radius:999px;background:#111824;padding:6px 9px;font-size:12px;font-weight:750}.notice{border:1px solid #6b5b23;background:#29230f;color:#f5df88;border-radius:12px;padding:12px 14px;font-size:13px}.notice.error{border-color:#76313a;background:#30171c;color:#ffbac1}.progress{height:10px;border:1px solid #344054;background:#090e16;border-radius:999px;overflow:hidden}.bar{height:100%;width:0;background:linear-gradient(90deg,var(--accent),var(--accent-2));transition:width .35s ease}.stage{font-size:14px;font-weight:850}.job-list{display:grid;gap:10px}.job{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px 18px;align-items:start;padding:15px;border:1px solid var(--line);border-radius:13px;background:var(--surface-2)}.job:hover{border-color:#3b4960}.job-main{min-width:0}.job-title{display:-webkit-box;overflow:hidden;-webkit-box-orient:vertical;-webkit-line-clamp:2;line-clamp:2;font-size:14px;font-weight:850;line-height:1.35}.job-meta{display:flex;align-items:center;gap:6px 12px;flex-wrap:wrap;margin-top:7px;color:#929fb0;font-size:12px}.job-meta span{white-space:nowrap}.job-actions{display:flex;align-items:center;justify-content:flex-end;gap:8px;flex-wrap:wrap}.state{display:inline-flex;align-items:center;min-height:28px;font-size:11px;font-weight:900;padding:5px 8px;border-radius:999px;background:#222b3a;white-space:nowrap}.state.success{background:#123a28;color:#80e7ab}.state.failure{background:#3d1b22;color:#ffa1aa}.state.approval{background:#3d3418;color:#ffe187}.brief-details{grid-column:1/-1;border:0;border-top:1px solid var(--line);border-radius:0;background:transparent;padding-top:9px}.brief-details>summary{width:max-content;max-width:100%;cursor:pointer;color:#9eabc0;font-size:12px;font-weight:750}.brief-full{margin-top:9px;max-height:180px;overflow:auto;padding:11px;border-radius:10px;background:#090e15;color:#c7d0dc;font-size:12px;white-space:pre-wrap;overflow-wrap:anywhere}.job.compact{padding:12px}.job.compact .job-title{-webkit-line-clamp:2}.job.compact .job-actions{grid-column:1/-1;justify-content:space-between}.scene{display:grid;grid-template-columns:48px 1fr;gap:13px;padding:15px 0;border-bottom:1px solid var(--line)}.scene:last-child{border:0}.scene-no{width:40px;height:40px;border-radius:11px;background:#251f43;display:flex;align-items:center;justify-content:center;font-weight:900}.queries{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}.query{font-size:11px;padding:5px 7px;border-radius:8px;background:#0d151f;border:1px solid #2b394a;color:#9fb0c4}.metric{padding:12px;border:1px solid var(--line);border-radius:12px;background:var(--surface-2)}.metric b{font-size:20px;display:block}.actions{display:flex;gap:9px;flex-wrap:wrap;margin-top:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#090e16;border:1px solid var(--line);border-radius:12px;padding:13px;max-height:260px;overflow:auto}.empty{padding:22px;border:1px dashed var(--line-strong);border-radius:13px;color:var(--muted);text-align:center}.sidebar-copy{margin:0;font-size:13px;line-height:1.55}
@media(max-width:900px){.layout{grid-template-columns:1fr}.hero{align-items:flex-start;flex-direction:column}.hero-tools{justify-content:flex-start}}
@media(max-width:650px){.wrap{padding:0 12px 56px}.top{position:static;align-items:flex-start;flex-direction:column;padding:14px 0}.nav{width:100%;overflow-x:auto;flex-wrap:nowrap;padding-bottom:2px;scrollbar-width:none}.nav::-webkit-scrollbar{display:none}.nav a{white-space:nowrap}.nav a:nth-child(n+4){display:none}.hero{padding:28px 0 18px}.hero h1{font-size:32px}.grid2,.grid3,.choice-grid,.status-grid{grid-template-columns:1fr}.card{padding:16px}.job{grid-template-columns:1fr}.job-actions{justify-content:flex-start}.actions .btn{width:100%}.job .btn.small{width:auto}.control-details>summary,.system-details>summary{align-items:flex-start}.section-title{align-items:flex-start}}
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
_UI_SECRET_RE = re.compile(
    r'(?i)\b(api[_ -]?key|authorization|bearer|token|secret)\b\s*[:=]\s*\S+'
)
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
    return _UI_SECRET_RE.sub(lambda match: f'{match.group(1)}=[gizlendi]', text)


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
    raw = str(job.get('created_at') or job.get('updated_at') or '').strip()
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
    value = (
        youtube.get('channel_title')
        or spec.get('channel_id')
        or spec.get('target_channel_title')
    )
    return _ellipsize(_safe_ui_text(value), 42)


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
    return record


def _state_class(state: str) -> str:
    if state == 'SUCCESS':
        return 'success'
    if state == 'FAILURE':
        return 'failure'
    if state == 'AWAITING_APPROVAL':
        return 'approval'
    return ''


@router.get('/studio', response_class=HTMLResponse)
def studio_home(studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    authenticated = _valid_token(studio_token)
    selected_voice = escape(get_selected_voice().get('name') or 'Ses seçilmedi')
    service_states = _service_statuses()
    services = ''.join(
        f'<div class="service"><span class="dot {"green" if ok else "red"}" aria-hidden="true"></span>'
        f'{escape(name)}<span class="tiny" style="margin-left:auto">{"Hazır" if ok else "Eksik"}</span></div>'
        for name, ok in service_states
    )
    ready_services = sum(1 for _, ok in service_states if ok)
    missing_services = len(service_states) - ready_services
    health_label = (
        'Tüm servis ayarları hazır'
        if not missing_services
        else f'{missing_services} servis ayarı eksik'
    )
    recent = list_jobs(5) if authenticated else []
    recent_html = ''.join(_job_row(job, compact=True) for job in recent) or '<div class="empty">Henüz kayıtlı üretim yok.</div>'
    token_field = (
        '<div class="notice" style="border-color:#285d45;background:#10291e;color:#9ee7bd">Güvenli Studio oturumu açık.</div>'
        if authenticated else
        '<label class="field">Studio güvenlik anahtarı</label><input name="token" type="password" autocomplete="off" required placeholder="Güvenli anahtarı gir">'
    )

    body = f'''
<div class="hero"><div class="hero-copy"><div class="eyebrow">Video üretimi</div><h1>Fikri ver, finali hazırla.</h1><div class="muted">Konu, süre ve kaliteyi seç. Stüdyo senaryo, ses, görsel ve kurguyu tek akışta tamamlasın.</div></div><div class="hero-tools"><span class="badge">🎙 {selected_voice}</span><span class="badge">16:9 · 1080p</span><span class="badge">Ayrı altyazı</span></div></div>
<div class="layout"><section>
<form action="/studio/start" method="post" class="card" id="studio-form">
<span class="section-kicker">YENİ ÜRETİM</span><h2>Nasıl bir video hazırlayalım?</h2>
<div class="choice-grid">
<div class="choice"><input id="mode-preview" name="mode" value="preview" type="radio" checked><label for="mode-preview"><b>⚡ Hızlı önizleme</b><span>30–60 saniyelik kalite testi. Hızlı karar vermek için.</span></label></div>
<div class="choice"><input id="mode-production" name="mode" value="production" type="radio"><label for="mode-production"><b>🏆 Yayın kalitesi</b><span>Uzun video, tam kalite denetimi ve zengin kurgu.</span></label></div>
</div>
<label class="field" for="topic">Konu ve yaratıcı talimat</label>
<textarea id="topic" name="topic" required>Telefonla ilgili, gerçek bir insanın otuz saniye izlemek isteyeceği tek bir gündelik sorunu seç. Tek bir şaşırtıcı nedeni göster; aynı kişi veya nesne ve aynı mekânda görünür, faydalı bir sonuçla bitir. Birden fazla teknoloji gerçeğini sıralama.</textarea>
<div class="grid2"><div><label class="field">Süre</label><select id="duration" name="duration_minutes"><option value="0.5" selected>30 saniye</option><option value="1">1 dakika</option><option value="3">3 dakika</option><option value="5">5 dakika</option><option value="8">8 dakika</option><option value="10">10 dakika</option></select></div><div><label class="field">Dil</label><select name="language"><option value="tr" selected>Türkçe</option><option value="en">English</option><option value="de">Deutsch</option><option value="es">Español</option><option value="ar">العربية</option></select></div></div>
<details class="control-details"><summary><span>Yaratıcı ve teknik ayarlar</span><span class="tiny">İsteğe bağlı</span></summary><div class="control-body">
<div class="grid2"><div><label class="field">İçerik tarzı</label><select name="content_style"><option value="documentary">Belgesel</option><option value="technology" selected>Teknoloji</option><option value="story">Hikâye</option><option value="cinematic">Sinematik</option><option value="explainer">Açıklayıcı</option></select></div><div><label class="field">Kurgu temposu</label><select name="pace"><option value="calm">Sakin</option><option value="balanced" selected>Dengeli</option><option value="dynamic">Dinamik</option></select></div></div>
<div class="grid2"><div><label class="field">Görsel karışımı</label><select name="visual_mix"><option value="real_first">Gerçek görüntü ağırlıklı</option><option value="balanced" selected>Dengeli: B-roll + AI</option><option value="ai_first">Özgün AI ağırlıklı</option></select></div><div><label class="field">Akış</label><select name="workflow"><option value="auto" selected>Otomatik tamamla</option><option value="storyboard">Önce storyboard göster</option></select></div></div>
<div class="grid2"><div><label class="field">Arka plan müziği</label><select name="music"><option value="off">Kapalı</option><option value="auto" selected>Uygunsa otomatik</option></select></div><div><label class="field">Altyazı</label><select name="subtitles"><option value="sidecar" selected>Ayrı SRT üret</option><option value="off">Üretme</option></select></div></div>
<label class="field">Referans video / kanal bağlantısı <span class="tiny">(yalnızca yapı ve ritim analizi)</span></label><input name="reference_url" type="url" placeholder="YouTube videosu veya kanal bağlantısı">
<label class="field">Kanal etiketi <span class="tiny">(opsiyonel)</span></label><input name="channel_id" type="text" maxlength="120" placeholder="teknoloji-tr-01">
<div class="actions"><a class="btn secondary small" href="/voice-audition">🎙 Anlatıcı sesini değiştir</a></div>
</div></details>
{token_field}
<button class="block" type="submit">Üretimi başlat →</button>
</form></section>
<aside><div class="card"><details class="system-details" {"open" if missing_services else ""}><summary><span class="status-summary"><span class="health-dot {"red" if missing_services else "green"}" aria-hidden="true"></span>{health_label}</span><span class="tiny">{ready_services}/{len(service_states)}</span></summary><div class="system-body"><div class="status-grid">{services}</div></div></details></div><div class="card"><div class="section-title"><div><span class="section-kicker">SON İŞLER</span><h3>Devam et</h3></div><a class="tiny" href="/studio/history">Tümünü gör →</a></div><div class="job-list">{recent_html}</div></div><div class="card"><span class="section-kicker">GÜVENLİ YAYIN</span><h3>Kontrol sende</h3><p class="muted sidebar-copy">Videolar önce gizli yüklenir. Kalite onayından önce herkese açık yayın yapılmaz.</p></div></aside></div>
'''
    script = r'''<script>
const preview=document.getElementById('mode-preview'),production=document.getElementById('mode-production'),duration=document.getElementById('duration');
function setDefaults(){if(production.checked){duration.value='5';document.querySelector('[name=workflow]').value='storyboard';document.querySelector('[name=music]').value='auto';}else{duration.value='0.5';document.querySelector('[name=workflow]').value='auto';document.querySelector('[name=music]').value='off';}}
preview.addEventListener('change',setDefaults);production.addEventListener('change',setDefaults);
</script>'''
    return _shell(body, script=script)


def _job_row(job: dict, *, compact: bool = False) -> str:
    spec = job.get('spec') if isinstance(job.get('spec'), dict) else {}
    state = str(job.get('state') or 'PENDING')
    mode = MODE_LABELS.get(str(spec.get('mode') or ''), str(spec.get('mode') or ''))
    try:
        progress = max(0, min(100, int(float(job.get('progress') or 0))))
    except (TypeError, ValueError):
        progress = 0
    raw_title = _job_title(job)
    title = escape(raw_title)
    brief = _job_brief(job)
    duration = _job_duration(job)
    date = _job_date(job)
    channel = _job_channel(job)
    task_id = escape(str(job.get('task_id') or ''), quote=True)
    metadata = [value for value in (mode, duration, date) if value]
    if channel:
        metadata.append(f'Kanal: {channel}')
    if state not in {'SUCCESS', 'FAILURE', 'AWAITING_APPROVAL'}:
        metadata.append(f'%{progress}')
    meta_html = ''.join(f'<span>{escape(value)}</span>' for value in metadata)
    details = ''
    if brief and _plain_text(brief) != _plain_text(raw_title):
        details = (
            '<details class="brief-details"><summary>Yaratıcı talimatı gör</summary>'
            f'<div class="brief-full">{escape(brief)}</div></details>'
        )
    return f'''<article class="job{" compact" if compact else ""}"><div class="job-main"><div class="job-title">{title}</div><div class="job-meta" aria-label="Video bilgileri">{meta_html}</div></div><div class="job-actions"><span class="state {_state_class(state)}">{escape(STATE_LABELS.get(state,state))}</span><a class="btn secondary small" href="/studio/job/{task_id}" aria-label="{title} işini aç">Aç</a></div>{details}</article>'''


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
    record = get_job(task_id) or {'spec': {}}
    display_title = escape(_job_title(record))
    body = f'''
<div class="hero"><div class="hero-copy"><div class="eyebrow">Üretim durumu</div><h1>{display_title}</h1><div class="muted">Aşamalar tamamlandıkça bu sayfa kendiliğinden güncellenir.</div></div></div>
<div class="card" id="job-card" aria-live="polite"><div class="stage" id="stage">Yükleniyor…</div><div class="progress" id="progress" role="progressbar" aria-label="Üretim ilerlemesi" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0" style="margin:14px 0"><div class="bar" id="bar"></div></div><div class="muted" id="message">Görev durumu alınıyor.</div><div id="result"></div></div>
<div class="actions"><a class="btn secondary" href="/studio">← Yeni üretim</a><a class="btn secondary" href="/studio/history">◷ Geçmiş</a></div>
'''
    safe_task_id = json.dumps(task_id)
    script = f'''<script>
const taskId={safe_task_id};let timer=null;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
function badge(t){{return `<span class="badge">${{esc(t)}}</span>`}}
async function poll(){{
 try{{const r=await fetch(`/studio/api/job/${{encodeURIComponent(taskId)}}`,{{cache:'no-store'}});if(!r.ok)throw new Error(await r.text());const j=await r.json();
 const state=j.state||'PENDING',stage=j.stage||'queued',p=Math.max(0,Math.min(100,Number(j.progress||0)));
 document.getElementById('bar').style.width=p+'%';document.getElementById('progress').setAttribute('aria-valuenow',String(p));document.getElementById('stage').textContent=(j.stage_label||stage)+' · %'+p;document.getElementById('message').textContent=j.message||'';
 const out=document.getElementById('result');
 if(state==='FAILURE'){{out.innerHTML=`<div class="notice error" style="margin-top:14px"><b>Üretim tamamlanamadı</b><pre>${{esc(j.error||'Bilinmeyen hata')}}</pre></div><div class="actions"><form method="post" action="/studio/retry/${{taskId}}"><button class="btn danger">↻ Aynı ayarlarla tekrar dene</button></form></div>`;return;}}
 if(state==='AWAITING_APPROVAL'){{out.innerHTML=`<div class="notice" style="margin-top:14px"><b>Storyboard hazır.</b> Render başlamadan sahneleri inceleyebilirsin.</div><div class="actions"><a class="btn success" href="/studio/plan/${{taskId}}">Storyboard'u aç →</a></div>`;return;}}
 if(state==='SUCCESS'){{const x=j.result||{{}};let links='';if(x.download_url)links+=`<a class="btn success" target="_blank" href="${{esc(x.download_url)}}">▶ Final videoyu aç</a>`;if(x.caption_url)links+=`<a class="btn secondary" target="_blank" href="${{esc(x.caption_url)}}">SRT indir</a>`;out.innerHTML=`<div class="grid3" style="margin-top:16px">${{badge((x.duration||0).toFixed?x.duration.toFixed(1)+' sn':(x.duration||'-')+' sn')}}${{badge((x.scenes||'-')+' sahne')}}${{badge((x.shots||'-')+' shot')}}</div><div class="actions">${{links}}<a class="btn secondary" href="/studio">＋ Yeni üretim</a></div>`;return;}}
 timer=setTimeout(poll,3000);
 }}catch(e){{document.getElementById('message').textContent='Durum alınamadı: '+e.message;timer=setTimeout(poll,5000);}}
}}
poll();
</script>'''
    return _shell(body, title='Üretim kontrolü', script=script)


@router.get('/studio/api/job/{task_id}')
def studio_job_api(task_id: str, studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    _require_auth(studio_token)
    record = _sync_job(task_id)
    payload = dict(record)
    payload['state_label'] = STATE_LABELS.get(str(payload.get('state')), str(payload.get('state') or ''))
    payload['stage_label'] = STAGE_LABELS.get(str(payload.get('stage')), str(payload.get('stage') or ''))
    for key in ('message', 'error'):
        if payload.get(key):
            payload[key] = _safe_ui_text(payload[key])
    # The full approved package is rendered on the storyboard page, not polled every three seconds.
    if isinstance(payload.get('result'), dict) and payload['result'].get('package'):
        result = dict(payload['result'])
        result['package'] = {'scene_count': len(result['package'].get('scenes') or []), 'title': result['package'].get('title')}
        payload['result'] = result
    return JSONResponse(payload)


@router.get('/studio/history', response_class=HTMLResponse)
def studio_history(studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    _require_auth(studio_token)
    jobs = [_sync_job(str(job.get('task_id'))) for job in list_jobs(60)]
    rows = ''.join(_job_row(job) for job in jobs) or '<div class="empty">Henüz üretim yok.</div>'
    body = f'<div class="hero"><div class="hero-copy"><div class="eyebrow">ARŞİV</div><h1>Üretim geçmişi</h1><div class="muted">Hazır, devam eden ve yeniden denenmesi gereken videolar tek yerde.</div></div><div class="hero-tools"><span class="badge">{len(jobs)} iş</span></div></div><div class="job-list">{rows}</div>'
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
    if record.get('kind') == 'plan':
        task = plan_video_pipeline.delay(spec.get('topic') or '', float(spec.get('duration_minutes') or 1), spec.get('language') or 'tr', spec.get('channel_id'), options)
        kind = 'plan'
    else:
        task = run_video_pipeline.delay(spec.get('topic') or '', float(spec.get('duration_minutes') or 1), spec.get('language') or 'tr', spec.get('channel_id'), options, None)
        kind = 'render'
    create_job(task.id, spec, kind=kind, parent_id=task_id)
    return RedirectResponse(f'/studio/job/{task.id}', status_code=303)


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
