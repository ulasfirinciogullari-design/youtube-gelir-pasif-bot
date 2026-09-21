from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
import json
import re
import secrets
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import APIRouter, Cookie, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import settings
from app.publish_tasks import publish_video_pipeline
from app.services.studio_state import (
    create_job,
    get_job,
    list_jobs,
    mark_failure,
)
from app.services.youtube_auth import (
    STATE_TTL_SECONDS,
    OAuthStateError,
    YouTubeAuthError,
    build_authorization_url,
    complete_authorization,
    connection_status,
    discard_authorization_state,
    disconnect,
)
from app.services.youtube_automation import (
    ProfileConflictError,
    SeriesProfileEditError,
    YouTubeAutomationError,
    automated_quality_approved,
    build_publish_plan,
    get_channel_profile,
    list_channel_profiles,
    save_channel_profile,
)
from app.services.youtube_publish_state import (
    UploadReservationError,
    mark_upload_enqueued,
    mark_upload_preflight_failed,
    reserve_upload,
)


router = APIRouter()
COOKIE_NAME = 'youtube_studio_token'
OAUTH_BINDING_COOKIE = 'youtube_oauth_browser_binding'
OAUTH_CALLBACK_PATH = '/studio/youtube/callback'

CSS = r'''
:root{color-scheme:dark;--bg:#090c11;--surface:#111721;--surface-2:#0d131c;--line:#273142;--line-strong:#39465c;--text:#f3f6fb;--muted:#9da9ba;--accent:#f0445d;--good:#51d593;--warn:#f5cd68;--bad:#ff7d88}
*{box-sizing:border-box}html{background:var(--bg)}body{margin:0;min-height:100vh;color:var(--text);font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;line-height:1.5;background:radial-gradient(circle at 10% -10%,rgba(83,95,162,.25),transparent 34%),radial-gradient(circle at 95% 0,rgba(127,42,66,.14),transparent 28%),var(--bg)}a{color:inherit;text-decoration:none}button,select{font:inherit}.skip-link{position:fixed;left:12px;top:8px;z-index:100;transform:translateY(-160%);padding:10px 14px;border-radius:10px;background:#fff;color:#111;font-weight:800}.skip-link:focus{transform:none}.wrap{max-width:1120px;margin:auto;padding:0 22px 72px}.top{position:sticky;top:0;z-index:30;display:flex;align-items:center;justify-content:space-between;gap:18px;min-height:68px;background:rgba(9,12,17,.9);backdrop-filter:blur(18px);border-bottom:1px solid rgba(57,70,92,.7)}.brand{font-weight:900;font-size:17px;letter-spacing:-.02em}.nav{display:flex;gap:6px;flex-wrap:wrap}.nav a{padding:8px 11px;border:1px solid transparent;border-radius:10px;color:var(--muted);font-size:13px;font-weight:750}.nav a:hover{color:var(--text);background:#151c28}.nav a.active,.nav a[aria-current=page]{color:#fff;background:#281b22;border-color:#62303d}.hero{display:flex;align-items:flex-end;justify-content:space-between;gap:24px;padding:38px 0 22px}.hero-copy{max-width:720px}.hero-tools{display:flex;justify-content:flex-end;gap:6px;flex-wrap:wrap}.eyebrow{margin-bottom:8px;color:#ff9aaa;font-size:12px;font-weight:850;letter-spacing:.1em;text-transform:uppercase}.hero h1{font-size:clamp(32px,5vw,46px);line-height:1.05;margin:0 0 10px;letter-spacing:-.045em}.muted{color:var(--muted)}.tiny{font-size:12px;color:#8f9bad}.card{background:rgba(17,23,33,.95);border:1px solid var(--line);border-radius:16px;padding:20px;margin-bottom:14px;box-shadow:0 20px 55px rgba(0,0,0,.15)}.card h2,.card h3{margin:0 0 8px;letter-spacing:-.02em}.section-head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:14px}.section-kicker{display:block;margin-bottom:4px;color:#8e9aad;font-size:12px;font-weight:800}.btn,button{display:inline-flex;align-items:center;justify-content:center;gap:7px;min-height:42px;border:1px solid transparent;border-radius:11px;padding:10px 14px;background:var(--accent);color:white;font-weight:850;cursor:pointer}.btn:hover,button:hover{filter:brightness(1.08)}.btn.secondary{background:#171f2c;border-color:#364258}.btn.success{background:#167d51}.btn.danger{background:#782c36}.btn.small,button.small{min-height:36px;padding:7px 11px;font-size:12px}select{min-width:230px;border:1px solid var(--line-strong);border-radius:11px;padding:10px 12px;background:#0a1018;color:var(--text);font-weight:750;outline:none}.actions{display:flex;gap:9px;flex-wrap:wrap;margin-top:12px}.badge{display:inline-flex;align-items:center;gap:6px;padding:6px 9px;border-radius:999px;border:1px solid #354055;background:#111824;font-size:12px;font-weight:750}.badge.good{border-color:#275a42;background:#10291e;color:#9de7bc}.badge.ready{border-color:#2c6549;background:#123423;color:#8fe8b5}.channel-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.channel-card{padding:15px;border:1px solid var(--line);border-radius:13px;background:var(--surface-2)}.channel-card-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}.channel-title{font-weight:850}.channel-metrics{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}.channel-actions{margin-top:12px;padding-top:10px;border-top:1px solid var(--line)}form.inline{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.notice{border:1px solid #6b5b23;background:#29230f;color:#f5df88;border-radius:12px;padding:12px 14px;font-size:13px}.notice.success{border-color:#285d45;background:#10291e;color:#9ee7bd}.video-list{display:grid;gap:10px}.video-card{display:grid;grid-template-columns:minmax(0,1fr) minmax(260px,auto);gap:12px 18px;align-items:start;padding:15px;border:1px solid var(--line);border-radius:13px;background:var(--surface-2)}.video-card:hover{border-color:#3b4960}.video-main{min-width:0}.video-title{display:-webkit-box;overflow:hidden;-webkit-box-orient:vertical;-webkit-line-clamp:2;line-clamp:2;font-size:15px;font-weight:850;line-height:1.35}.video-meta{display:flex;gap:6px 12px;align-items:center;flex-wrap:wrap;margin-top:7px;color:#929fb0;font-size:12px}.video-meta span{white-space:nowrap}.video-actions{display:flex;align-items:center;justify-content:flex-end;gap:8px;flex-wrap:wrap}.video-actions form{justify-content:flex-end}.brief-details{grid-column:1/-1;border-top:1px solid var(--line);padding-top:9px}.brief-details>summary{width:max-content;max-width:100%;cursor:pointer;color:#9eabc0;font-size:12px;font-weight:750}.brief-full{margin-top:9px;max-height:180px;overflow:auto;padding:11px;border-radius:10px;background:#090e15;color:#c7d0dc;font-size:12px;white-space:pre-wrap;overflow-wrap:anywhere}.empty{padding:22px;border:1px dashed var(--line-strong);border-radius:13px;color:var(--muted);text-align:center}.progress{height:10px;border:1px solid #344054;background:#090e16;border-radius:999px;overflow:hidden}.bar{height:100%;width:0;background:linear-gradient(90deg,#f0445d,#f29452);transition:width .35s ease}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#090e16;border:1px solid var(--line);border-radius:12px;padding:13px;max-height:260px;overflow:auto}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}a:focus-visible,button:focus-visible,select:focus-visible,summary:focus-visible{outline:3px solid rgba(118,170,255,.62);outline-offset:3px}
@media(max-width:760px){.hero{align-items:flex-start;flex-direction:column}.hero-tools{justify-content:flex-start}.channel-grid{grid-template-columns:1fr}.video-card{grid-template-columns:1fr}.video-actions,.video-actions form{justify-content:flex-start}}
@media(max-width:650px){.wrap{padding:0 12px 56px}.top{position:static;align-items:flex-start;flex-direction:column;padding:14px 0}.nav{width:100%;overflow-x:auto;flex-wrap:nowrap}.nav a{white-space:nowrap}.hero{padding:28px 0 18px}.hero h1{font-size:32px}.card{padding:16px}.section-head{flex-direction:column}.video-actions form,.video-actions select,.video-actions button,.actions .btn{width:100%}}
@media(prefers-reduced-motion:reduce){.bar{transition:none}}
.profile-details{margin-top:12px;padding-top:10px;border-top:1px solid var(--line)}.profile-details>summary{cursor:pointer;color:#aab6c8;font-size:12px;font-weight:800}.profile-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;margin:12px 0}.profile-grid label{display:grid;gap:5px;color:#aeb9ca;font-size:11px;font-weight:750}.profile-grid .wide{grid-column:1/-1}.profile-grid input,.profile-grid textarea,.profile-grid select{width:100%;min-width:0;border:1px solid var(--line-strong);border-radius:10px;padding:9px 10px;background:#0a1018;color:var(--text);outline:none}.profile-grid input:focus-visible,.profile-grid textarea:focus-visible,.profile-grid select:focus-visible{outline:3px solid rgba(118,170,255,.62);outline-offset:2px}.profile-grid textarea{min-height:72px;resize:vertical}.profile-grid .check{display:flex;grid-column:1/-1;align-items:center;gap:8px}.profile-grid .check input{width:auto}.profile-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}@media(max-width:650px){.profile-grid{grid-template-columns:1fr}.profile-grid .wide{grid-column:auto}}
'''


_UI_URL_RE = re.compile(r'(?i)\bhttps?://\S+')
_UI_SECRET_RE = re.compile(
    r'(?i)\b(api[_ -]?key|authorization|bearer|token|secret)\b\s*[:=]\s*\S+'
)
_TR_MONTHS = (
    '', 'Oca', 'Şub', 'Mar', 'Nis', 'May', 'Haz',
    'Tem', 'Ağu', 'Eyl', 'Eki', 'Kas', 'Ara',
)


def _plain_text(value) -> str:
    return ' '.join(str(value or '').split())


def _safe_ui_text(value) -> str:
    text = _UI_URL_RE.sub('[bağlantı gizlendi]', _plain_text(value))
    return _UI_SECRET_RE.sub(lambda match: f'{match.group(1)}=[gizlendi]', text)


def _ellipsize(value, limit: int) -> str:
    text = _plain_text(value)
    if len(text) <= limit:
        return text
    shortened = text[: max(1, limit - 1)].rsplit(' ', 1)[0].rstrip(' ,;:-')
    return f'{shortened or text[: max(1, limit - 1)]}…'


def _ready_title(job: dict) -> str:
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
    return _ellipsize(_safe_ui_text(spec.get('topic')), 96) or 'Hazır video'


def _ready_brief(job: dict) -> str:
    spec = job.get('spec') if isinstance(job.get('spec'), dict) else {}
    return _safe_ui_text(spec.get('topic'))


def _ready_duration(job: dict) -> str:
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


def _ready_date(job: dict) -> str:
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


def _valid_token(value: str | None) -> bool:
    return bool(settings.factory_api_token and value and value == settings.factory_api_token)


def _require_auth(value: str | None) -> None:
    if not _valid_token(value):
        raise HTTPException(status_code=401, detail='Studio oturumu gerekli')


def _canonical_origin(value: str) -> str | None:
    parsed = urlparse(str(value or '').strip())
    if (
        parsed.scheme not in {'http', 'https'}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    default_port = (parsed.scheme == 'https' and port == 443) or (
        parsed.scheme == 'http' and port == 80
    )
    hostname = parsed.hostname.lower()
    if ':' in hostname:
        hostname = f'[{hostname}]'
    authority = hostname if not port or default_port else f'{hostname}:{port}'
    return f'{parsed.scheme}://{authority}'.lower()


def _require_same_origin(request: Request) -> None:
    expected = _canonical_origin(settings.google_redirect_uri)
    if not expected:
        # Logout remains usable before OAuth is configured. The request host is
        # a safe fallback here because an attacker cannot both target a
        # different host and attach this host's Studio cookie on a form POST.
        expected = _canonical_origin(str(request.base_url))
    if not expected:
        raise HTTPException(status_code=503, detail='Studio public origin is not configured')
    origin = _canonical_origin(request.headers.get('origin', ''))
    if origin:
        if origin != expected:
            raise HTTPException(status_code=403, detail='Cross-origin form submission rejected')
        return
    referer = _canonical_origin(request.headers.get('referer', ''))
    if referer != expected:
        raise HTTPException(status_code=403, detail='Same-origin form proof is required')


def _delete_oauth_binding_cookie(response):
    response.delete_cookie(
        OAUTH_BINDING_COOKIE,
        path=OAUTH_CALLBACK_PATH,
        secure=True,
        httponly=True,
        samesite='lax',
    )
    return response


def _shell(
    body: str,
    title: str = 'YouTube bağlantısı',
    script: str = '',
    *,
    status_code: int = 200,
    same_origin_forms: bool = False,
    extra_css: str = '',
) -> HTMLResponse:
    from app.studio import BASE_CSS, _nav
    from app.services.studio_console_theme import CSS as console_css
    response = HTMLResponse(
        '<!doctype html><html lang="tr"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        '<meta name="theme-color" content="#f4f6f8">'
        f'<title>{escape(title)}</title><style>{BASE_CSS}{CSS}{extra_css}{console_css}</style></head><body>'
        '<a class="skip-link" href="#main-content">İçeriğe geç</a><div class="wrap">'
        + _nav('youtube') +
        f'<main id="main-content" tabindex="-1">{body}</main></div>{script}</body></html>',
        status_code=status_code,
    )
    response.headers['Cache-Control'] = 'no-store'
    # A form page must preserve exact same-origin proof: Fetch can serialize a
    # no-referrer navigation POST's Origin as "null" while also omitting its
    # Referer. Callback/error pages keep the stricter no-referrer default so an
    # OAuth code or state in their URL is not forwarded even within Studio.
    response.headers['Referrer-Policy'] = (
        'same-origin' if same_origin_forms else 'no-referrer'
    )
    return response


def _completed_jobs() -> list[dict]:
    jobs = []
    for job in list_jobs(80):
        result = job.get('result') or {}
        youtube = (
            result.get('youtube')
            if isinstance(result, dict)
            and isinstance(result.get('youtube'), dict)
            else {}
        )
        already_uploaded = bool(
            isinstance(result, dict)
            and (
                result.get('youtube_url')
                or youtube.get('url')
                or youtube.get('video_id')
            )
        )
        if (
            job.get('state') == 'SUCCESS'
            and job.get('kind') == 'render'
            and isinstance(result, dict)
            and result.get('video_key')
            and (already_uploaded or automated_quality_approved(job))
        ):
            jobs.append(job)
    return jobs


def _studio_presentation():
    # Runtime import: Studio registers this router before defining its shared
    # display helpers. No provider or state mutation is performed by this bridge.
    from app import studio
    return studio


def _youtube_watch_url(job: dict) -> str:
    video_id = _studio_presentation()._delivery_video_id(job)
    if isinstance(video_id, str) and re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
        return 'https://www.youtube.com/watch?v=' + video_id
    return ''


def _production_status_text(profile: dict, state: dict | None, production_retry: dict | None = None) -> str:
    if profile.get('production_enabled') is not True:
        return 'Otomatik üretim kapalı'
    if profile.get('auto_publish') is not True:
        return 'Otomatik yükleme ayarı bekliyor'
    topics = profile.get('production_topics') or []
    if not topics:
        return 'Konu bekliyor'
    if state is None:
        return 'Üretim takvimi etkin'
    if state.get('unavailable'):
        return 'Üretim durumu alınamadı'
    if state.get('paused_reason') == 'previous_render_failed' and isinstance(production_retry, dict):
        presentation = _studio_presentation()
        if presentation._canonical_task_id(production_retry.get('task_id')):
            label = presentation.PRODUCTION_RETRY_LABELS.get(production_retry.get('status'))
            if label:
                return label
    if state.get('paused_reason') or state.get('dispatch_status') == 'uncertain':
        return 'Duraklatıldı · kontrol gerekiyor'
    if state.get('active_task_id'):
        return 'Üretim sıraya alındı'
    try:
        if int(state.get('cursor') or 0) >= len(topics):
            return 'Konu listesi tamamlandı'
        due = float(state.get('next_due') or 0)
        if due > datetime.now(timezone.utc).timestamp():
            label = datetime.fromtimestamp(due, timezone(timedelta(hours=3))).strftime('%d.%m %H:%M')
            return f'Sonraki video: {label}'
    except (ValueError, TypeError, OverflowError, OSError):
        return 'Üretim durumu alınamadı'
    return 'Planlama bekliyor'


def _produce_now_form(profile: dict, state: dict | None) -> str:
    state = state or {}
    topics = profile.get('production_topics')
    try:
        cursor = int(state.get('cursor', '-1'))
    except (TypeError, ValueError):
        return ''
    if not (
        profile.get('production_enabled') is True and profile.get('auto_publish') is True
        and profile.get('release_mode') == 'public' and profile.get('profile_revision')
        and isinstance(topics, list) and 1 <= cursor < len(topics)
        and state.get('dispatch_status') == 'finished'
        and not state.get('paused_reason') and not state.get('active_task_id')
    ):
        return ''
    return (
        '<form class="inline" method="post" action="/studio/youtube/produce-now/'
        + escape(str(profile.get('channel_id') or ''), quote=True) + '">'
        + '<input type="hidden" name="expected_revision" value="'
        + escape(str(profile['profile_revision']), quote=True) + '">'
        + '<button class="small" type="submit">Hemen sıradaki videoyu üret</button></form>'
    )


def _existing_release_form(job: dict, profile: dict | None) -> str:
    profile = profile or {}
    result = job.get('result') if isinstance(job.get('result'), dict) else {}
    youtube = result.get('youtube') if isinstance(result.get('youtube'), dict) else {}
    if not (
        automated_quality_approved(job) and job.get('task_id')
        and youtube.get('privacy_status') == 'private' and youtube.get('release_status') == 'private'
        and youtube.get('video_id') and not youtube.get('release_error_code')
        and profile.get('auto_publish') is True and profile.get('release_mode') == 'public'
        and profile.get('channel_id') == youtube.get('target_channel_id')
        and profile.get('profile_revision')
    ):
        return ''
    fields = {
        'expected_video_id': youtube['video_id'],
        'youtube_channel_id': profile['channel_id'],
        'expected_profile_revision': profile['profile_revision'],
    }
    hidden = ''.join(
        f'<input type="hidden" name="{name}" value="{escape(str(value), quote=True)}">'
        for name, value in fields.items()
    )
    return (
        '<form class="inline" method="post" action="/studio/youtube/release/'
        + escape(str(job['task_id']), quote=True) + '">'
        + hidden + '<button class="small" type="submit">Herkese aç</button></form>'
    )


def _profile_form(channel: dict, profile: dict | None, production_state: dict | None = None,
                  production_retry: dict | None = None) -> str:
    profile = profile or {}
    channel_id = str(channel.get('id') or '')
    connection_id = str(channel.get('connection_id') or '')
    revision = str(profile.get('profile_revision') or '')

    def value(name: str, default: str = '') -> str:
        raw = profile.get(name, default)
        if isinstance(raw, list):
            raw = ', '.join(str(item) for item in raw)
        return escape(str(raw or ''), quote=True)

    release_mode = str(profile.get('release_mode') or 'private')
    release_options = ''.join(
        f'<option value="{mode}"{" selected" if release_mode == mode else ""}>{label}</option>'
        for mode, label in (
            ('private', 'Yalnızca gizli'),
            ('public', 'Kalite geçerse otomatik herkese açık'),
            ('scheduled', 'Kalite geçerse otomatik planla'),
        )
    )
    checked = ' checked' if profile.get('auto_publish') else ''
    production_checked = ' checked' if profile.get('production_enabled') is True else ''
    production_topics = '\n'.join(profile.get('production_topics') or [])
    thumbnail_checked = ' checked' if profile.get('require_thumbnail') else ''
    status = f'<span class="badge">{escape(_production_status_text(profile, production_state, production_retry))}</span>'
    publication_label = (
        {
            'private': 'Otomatik yükleme · gizli',
            'public': 'Kalite geçerse otomatik yayında',
            'scheduled': 'Kalite geçerse otomatik planlanır',
        }.get(release_mode, 'Yayın ayarını kontrol et')
        if profile.get('auto_publish') is True else 'Otomatik yükleme kapalı'
    )
    return f'''
<div class="channel-policy"><span class="badge">{escape(publication_label)}</span></div>
<details class="profile-details"><summary>Otomatik üretim</summary>
<form method="post" action="/studio/youtube/profile/{escape(channel_id, quote=True)}">
<input type="hidden" name="connection_id" value="{escape(connection_id, quote=True)}">
<input type="hidden" name="expected_revision" value="{escape(revision, quote=True)}">
<input type="hidden" name="production_settings" value="1">
<div class="profile-grid">
<label class="check"><input name="production_enabled" type="checkbox" value="1"{production_checked}> Bu kanal için düzenli video üret</label>
<div class="wide tiny">Biçim otomatik seçilir: tek fikir için 30 saniyelik Shorts; açıkça kapsamlı anlatım veya çok boyutlu karşılaştırma için 3 dakikalık yatay video. Aynı kanalın bölümleri sırayla, en fazla iki farklı kanalın işleri paralel ilerler.</div>
<label class="wide">Üretilecek konular<textarea name="production_topics" maxlength="14459" placeholder="Her satıra bir konu yaz. Konular sırayla işlenir.">{escape(production_topics)}</textarea><span>En fazla 60 konu; her konu en fazla 240 karakter.</span></label>
<label>Yeni video aralığı (saat)<input name="production_interval_hours" type="number" min="6" max="168" value="{value('production_interval_hours', '24')}"></label>
<label>Üretim dili<input name="default_language" maxlength="24" value="{value('default_language', 'tr')}" placeholder="tr"></label>
<label class="check"><input name="auto_publish" type="checkbox" value="1"{checked}> Kalite kontrolü geçen videoları bu kanala otomatik yükle</label>
</div>
<details class="profile-details"><summary>Yayın ve seri ayarları</summary>
<div class="profile-grid">
<label class="wide">Kanal kimliği / yayın çizgisi<input name="channel_identity" maxlength="240" value="{value('channel_identity')}" placeholder="Kısa, merak uyandıran Türkçe bilim hikâyeleri"></label>
<label>Studio kanal etiketi<input name="route_label" maxlength="120" value="{value('route_label')}" placeholder="merak-belgesel-tr-01"></label>
<label>Diller<input name="languages" maxlength="180" value="{value('languages', 'tr')}" placeholder="tr, en"></label>
<label class="wide">Konu anahtarları<input name="topic_keywords" maxlength="1200" value="{value('topic_keywords')}" placeholder="havacılık, bilim, teknoloji"></label>
<label>Kategori no<input name="category_id" maxlength="3" value="{value('category_id', '28')}" inputmode="numeric"></label>
<label class="wide">Etiketler<input name="default_tags" maxlength="1600" value="{value('default_tags')}" placeholder="bilim, merak, kısa belgesel"></label>
<label class="wide">Hashtagler<input name="hashtags" maxlength="700" value="{value('hashtags')}" placeholder="Bilim, Merak, Shorts"></label>
<label class="wide">Açıklama alt bilgisi<textarea name="description_footer" maxlength="1200" placeholder="Kanal imzası ve sabit bilgi">{escape(str(profile.get('description_footer') or ''))}</textarea></label>
<label>Seri kodu<input name="series_id" maxlength="80" value="{value('series_id')}" placeholder="ucak-sirlari-1"></label>
<label>Seri adı<input name="series_name" maxlength="100" value="{value('series_name')}" placeholder="Uçak Sırları"></label>
<label>Seri toplamı<input name="series_total" type="number" min="0" max="10000" value="{value('series_total', '0')}"></label>
<label>Yayın davranışı<select name="release_mode">{release_options}</select></label>
<label>Planlama gecikmesi (dk)<input name="schedule_delay_minutes" type="number" min="15" max="43200" value="{value('schedule_delay_minutes', '60')}"></label>
<label class="check"><input name="require_thumbnail" type="checkbox" value="1"{thumbnail_checked}> Özel küçük resim yoksa herkese açma</label>
</div></details>
<div class="profile-actions"><button class="small" type="submit">Profili kaydet</button>{status}<span class="tiny">Başlık, açıklama ve etiketler otomatik hazırlanır. İlk yükleme gizlidir; kalite onayı ve gerekli içerik bildirimleri tamamlanınca seçilen yayın davranışı uygulanır.</span></div>
</form>{_produce_now_form(profile, production_state)}</details>'''


@router.get('/studio/youtube', response_class=HTMLResponse)
def youtube_home(
    connected: int = 0,
    studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
):
    _require_auth(studio_token)
    presentation = _studio_presentation()
    completed = _completed_jobs()
    recent = list_jobs(80) or []
    by_id = {job.get('task_id'): job for job in [*recent, *completed] if isinstance(job, dict)}
    metrics = presentation._dashboard_metrics([*recent, *completed])
    status = connection_status()
    connections = status.get('connections') if isinstance(status.get('connections'), list) else []
    try:
        profiles = {
            str(item.get('channel_id') or ''): item
            for item in list_channel_profiles()
            if isinstance(item, dict)
        }
    except YouTubeAutomationError:
        profiles = {}

    if status.get('configured'):
        channel_cards = []
        channel_metrics = {
            row.get('channel_id'): row for row in (metrics.get('channels') or [])
            if isinstance(row, dict)
        }
        for channel in connections:
            channel_id = str(channel.get('id') or '')
            connection_id = str(channel.get('connection_id') or '')
            profile = profiles.get(channel_id)
            production_state = {}
            if profile and profile.get('production_enabled') is True:
                try:
                    from app.services.channel_production import get_production_state
                    production_state = get_production_state(channel_id)
                except Exception:
                    production_state = {'unavailable': True}
            production_retry = presentation._active_production_retry(profile, production_state, by_id)
            profile_form = _profile_form(channel, profile, production_state, production_retry)
            measured = channel_metrics.get(channel_id, {})
            reconnect = channel.get('requires_reconnect') is True or measured.get('reason') == 'permission'
            connection_label = ('Bağlantı yenilenmeli' if reconnect else
                                '● Bağlı' if measured.get('status') == 'fresh' else 'Bağlantı kayıtlı')
            reconnect_notice = ('<p class="notice" role="status">Google erişimi yenilenmeli. '
                                'Yeniden bağla düğmesine basıp bu kanalı seç.</p>' if reconnect else '')
            channel_cards.append(f'''
<article class="channel-card" id="channel-{escape(channel_id, quote=True)}"><div class="channel-card-head"><div><div class="channel-title">{escape(_ellipsize(_safe_ui_text(channel.get('title') or 'YouTube kanalı'), 60))}</div><div class="tiny">Kanal ve otomasyon ayarları</div></div><span class="badge">{connection_label}</span></div>{reconnect_notice}{profile_form}<div class="actions channel-actions"><form method="post" action="/studio/youtube/reconnect/{escape(channel_id, quote=True)}"><button class="btn secondary small" type="submit">Yeniden bağla</button></form><form method="post" action="/studio/youtube/disconnect"><input type="hidden" name="youtube_channel_id" value="{escape(channel_id, quote=True)}"><input type="hidden" name="connection_id" value="{escape(connection_id, quote=True)}"><button class="btn danger small" type="submit">Bağlantıyı kaldır</button></form></div></article>''')
        count = int(status.get('connection_count') or len(connections))
        limit = int(status.get('connection_limit') or 10)
        connection_notice = ''
        if not connections and status.get('requires_reconnect'):
            connection_notice = '<div class="notice" role="alert">En az bir YouTube bağlantısı yenilenmeli.</div>'
        if count < limit:
            connect_action = f'<div class="actions"><form class="inline" method="post" action="/studio/youtube/connect"><button type="submit">+ Google ile kanal bağla</button></form><span class="tiny">{count}/{limit} kanal kullanılıyor</span></div>'
        else:
            connect_action = f'<div class="notice">Kanal sınırı dolu ({count}/{limit}). Mevcut kanallarını yenileyebilirsin. Yeni kanal eklemek için bir bağlantıyı kaldır.</div>'
        empty_channels = '<div class="empty">Henüz bağlı kanal yok.</div>' if not channel_cards else ''
        account_card = f'''<section class="card"><div class="section-head"><div><span class="section-kicker">HESAPLAR</span><h2>Bağlı kanallar</h2><div class="muted">Her video yükleme anında tek bir hedef kanala sabitlenir.</div></div><span class="badge">{count}/{limit}</span></div>{connection_notice}<div class="channel-grid">{''.join(channel_cards)}</div>{empty_channels}{connect_action}</section>'''
    else:
        account_card = '''
<div class="notice" role="alert"><b>Google bağlantı ayarları eksik.</b><p>Google istemcisi, yönlendirme adresi ve ayrı şifreleme anahtarı Railway’de güvenli ortam değişkenleri olarak tanımlanmalı.</p></div>'''

    uploaded_rows = []
    ready_rows = []
    for index, source in enumerate(completed, start=1):
        job = presentation._with_publication_presentation(source, by_id.get)
        job = presentation._with_youtube_metrics(job, metrics)
        result = job.get('result') or {}
        raw_title = _ready_title(job)
        title = escape(raw_title)
        brief = _ready_brief(job)
        duration = _ready_duration(job)
        created = _ready_date(job)
        youtube = result.get('youtube') if isinstance(result.get('youtube'), dict) else {}
        delivery = presentation._video_delivery(job)
        if delivery['key'] == 'deleted':
            continue  # Retained in Silinenler, never offered for another upload.
        uploaded = (
            delivery['key'] in {'private', 'public', 'unlisted', 'scheduled', 'uploaded'}
            or presentation._job_has_youtube_output(job)
        )
        watch_url = _youtube_watch_url(job)
        publication = presentation._publication_status(job)
        if uploaded:
            action = (
                f'<a class="btn secondary" target="_blank" rel="noopener noreferrer" href="{watch_url}">YouTube’da aç</a>'
                if watch_url else '<span class="tiny">Video bağlantısı doğrulanmalı</span>'
            )
            action += _existing_release_form(job, profiles.get(str(youtube.get('target_channel_id') or '')))
        elif publication:
            action = '<span class="tiny">Mevcut yükleme kaydı kontrol edilmeli; yeni yükleme başlatılmaz.</span>'
        elif connections:
            options = ''.join(
                f'<option value="{escape(str(item.get("id") or ""), quote=True)}">{escape(_ellipsize(_safe_ui_text(item.get("title") or "YouTube kanalı"), 60))}</option>'
                for item in connections
            )
            selector_id = f'target-channel-{index}'
            action = f'''<form class="inline" method="post" action="/studio/youtube/publish/{escape(str(job.get('task_id') or ''), quote=True)}"><label class="sr-only" for="{selector_id}">Hedef YouTube kanalı</label><select id="{selector_id}" name="youtube_channel_id" required aria-label="Hedef YouTube kanalı">{options}</select><button type="submit">Gizli yükle</button></form>'''
        else:
            action = '<span class="tiny">Önce bir YouTube kanalı bağla</span>'
        if publication:
            publisher_id = presentation._canonical_task_id(job.get('_publication_task_id'))
            automation = result.get('youtube_automation')
            if not publisher_id and isinstance(automation, dict):
                publisher_id = presentation._canonical_task_id(automation.get('publish_task_id'))
            if publisher_id:
                action += f'<a class="btn secondary small" href="/studio/youtube/publish-status/{publisher_id}">Yükleme durumunu aç</a>'
        ready_state = presentation._delivery_badges(job)
        metadata = [value for value in (duration, created) if value]
        meta_html = ''.join(f'<span>{escape(value)}</span>' for value in metadata)
        details = ''
        if brief and _plain_text(brief) != _plain_text(raw_title):
            details = (
                '<details class="brief-details"><summary>Yaratıcı talimatı gör</summary>'
                f'<div class="brief-full">{escape(brief)}</div></details>'
            )
        row = (
            f'<article class="video-card"><div class="video-main"><div class="delivery-badges" data-delivery-task="{presentation._canonical_task_id(job.get("task_id"))}">{ready_state}</div><div class="video-title">{title}</div>{presentation._video_identity(job)}<div class="video-meta" aria-label="Video bilgileri">{meta_html}</div>{presentation._video_performance(job)}</div><div class="video-actions">{action}</div>{details}</article>'
        )
        (uploaded_rows if uploaded else ready_rows).append(row)
    sections = ''
    for heading, rows, empty, note in (
        ('Yüklenen videolar', uploaded_rows, 'Henüz YouTube’a yüklenen video yok.',
         'Gerçek görünürlük ve yükleme uyarıları ayrı gösterilir.'),
        ('Yüklenmeye hazır', ready_rows, 'Yüklenmeyi bekleyen tamamlanmış video yok.',
         'Üretimi tamamlanan videolar. Devam eden yükleme varsa ikinci kez başlatılmaz.'),
    ):
        sections += f'<section class="card"><div class="section-head"><div><h2>{heading}</h2><div class="muted">{note}</div></div><span class="badge">{len(rows)} video</span></div><div class="video-list">' + (''.join(rows) or f'<div class="empty">{empty}</div>') + '</div></section>'
    sections += '<div class="actions"><a class="btn secondary" href="/studio/history?status=deleted">Silinenler · geçmiş yayın kayıtları</a></div>'
    overview = '<section class="card">' + presentation._metrics_header(metrics) + '<div id="channel-overview-host">' + presentation._channel_overview(metrics.get('channels') or []) + '</div></section>'
    success = '<div class="notice success" role="status">YouTube kanalı başarıyla bağlandı.</div>' if connected else ''
    budget_notice = presentation._production_budget_notice()
    body = f'''
<div class="hero"><div class="hero-copy"><div class="eyebrow">YouTube</div><h1>Yayın merkezi</h1><div class="muted">Kanallar, gerçek yayın durumu ve performans tek yerde.</div></div><div class="hero-tools"><span class="badge good">🔒 İlk yükleme daima gizli</span><span class="badge">En fazla 10 kanal</span></div></div>{success}{budget_notice}{overview}{account_card}{sections}'''
    return _shell(body, same_origin_forms=True, script=presentation._metrics_script(), extra_css=presentation.METRICS_CSS)


@router.get('/studio/youtube/status')
def youtube_connection_status(
    verify: bool = False,
    youtube_channel_id: str | None = None,
    studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
):
    _require_auth(studio_token)
    status = connection_status(channel_id=youtube_channel_id, verify=verify)
    # connection_status intentionally contains no access/refresh token or client secret.
    return status


@router.get('/studio/youtube/production-status/{youtube_channel_id}')
def youtube_production_status(
    youtube_channel_id: str,
    studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
):
    """Expose only owner-safe scheduler diagnostics; never reconcile or resume."""
    from fastapi.responses import JSONResponse
    from app.services.channel_production import get_production_state

    _require_auth(studio_token)
    if not re.fullmatch(r'UC[A-Za-z0-9_-]{22}', youtube_channel_id):
        raise HTTPException(status_code=422, detail='production_channel_invalid')
    try:
        connected = connection_status(channel_id=youtube_channel_id, verify=False)
        channel = connected.get('channel') or {}
        if channel.get('id') != youtube_channel_id:
            raise HTTPException(status_code=404, detail='production_channel_not_connected')
        profile = get_channel_profile(youtube_channel_id)
        if not isinstance(profile, dict) or profile.get('channel_id') != youtube_channel_id:
            raise HTTPException(status_code=409, detail='production_profile_unavailable')
        state = get_production_state(youtube_channel_id)
        if not isinstance(state, dict):
            raise ValueError()
        topics = profile.get('production_topics')
        if not isinstance(topics, list) or len(topics) > 60 or not all(isinstance(t, str) for t in topics):
            raise ValueError()
        cursor = state.get('cursor', '0')
        if not isinstance(cursor, str) or not re.fullmatch(r'0|[1-9][0-9]?', cursor):
            raise ValueError()
        cursor = int(cursor)
        if cursor > len(topics):
            raise ValueError()
        reason = state.get('paused_reason') or None
        if reason not in {None, 'previous_render_failed', 'previous_render_needs_review',
                          'previous_publication_blocked', 'consumed_topics_changed'}:
            reason = 'unrecognized_pause'

        def identifier(value):
            return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{8,128}', value) else None

        def task_id(value):
            return value if isinstance(value, str) and re.fullmatch(
                r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', value) else None

        next_due = state.get('next_due')
        next_due = (next_due if isinstance(next_due, str) and re.fullmatch(
            r'(?:0|[1-9][0-9]{0,12})(?:\.[0-9]{1,8})?', next_due) else None)
        digest = state.get('consumed_prefix')
        digest = digest if isinstance(digest, str) and re.fullmatch(r'[0-9a-f]{64}', digest) else None
        result = {
            'channel_id': youtube_channel_id,
            'connection_id': identifier(channel.get('connection_id')),
            'requires_reconnect': connected.get('requires_reconnect') is True or channel.get('requires_reconnect') is True,
            'profile_revision': identifier(profile.get('profile_revision')),
            'production_enabled': profile.get('production_enabled') is True,
            'auto_publish': profile.get('auto_publish') is True,
            'release_mode': profile.get('release_mode') if profile.get('release_mode') in {'public', 'private', 'scheduled'} else None,
            'series_epoch_present': 'series_epoch' in profile,
            'cursor': cursor,
            'topic_count': len(topics),
            'next_topic_index': cursor if cursor < len(topics) else None,
            'paused_reason': reason,
            'next_due': next_due,
            'last_task_id': task_id(state.get('last_task_id')),
            'active_task_id': task_id(state.get('active_task_id')),
            'last_result': state.get('last_result') if state.get('last_result') in {'SUCCESS', 'FAILURE', 'CANCELLED'} else None,
            'dispatch_status': state.get('dispatch_status') if state.get('dispatch_status') in {'reserved', 'dispatched', 'uncertain', 'finished'} else None,
            'state_profile_revision': identifier(state.get('profile_revision')),
            'state_connection_id': identifier(state.get('connection_id')),
            'consumed_prefix': digest,
        }
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail='production_state_unavailable') from None
    return JSONResponse(result, headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})


@router.post('/studio/youtube/continue-after-owner-cancellation/{youtube_channel_id}')
async def youtube_continue_after_owner_cancellation(
    youtube_channel_id: str,
    request: Request,
    studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
):
    from starlette.concurrency import run_in_threadpool
    from fastapi.responses import JSONResponse
    from app.services.external_artifact_import import _object
    from app.services.owner_cancelled_continuation import (
        OwnerContinuationError, continue_after_owner_cancellation, validate_request,
    )

    _require_auth(studio_token)
    _require_same_origin(request)
    if request.headers.get('content-type', '').split(';', 1)[0].lower() != 'application/json':
        raise HTTPException(status_code=415, detail='owner_continuation_json_required')
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 8192:
            raise HTTPException(status_code=413, detail='owner_continuation_request_too_large')
        body.extend(chunk)
    try:
        value = validate_request(youtube_channel_id, _object(bytes(body), limit=8192))
    except Exception:
        raise HTTPException(status_code=422, detail='owner_continuation_schema_invalid') from None
    try:
        result = await run_in_threadpool(continue_after_owner_cancellation, youtube_channel_id, **value)
    except OwnerContinuationError:
        raise HTTPException(status_code=409, detail='owner_continuation_not_eligible') from None
    except Exception:
        raise HTTPException(status_code=503, detail='owner_continuation_unavailable') from None
    return JSONResponse(result, headers={'Cache-Control': 'no-store'})


@router.get('/studio/youtube/production-budget')
def youtube_production_budget(
    studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
):
    """Read budget reservations only; no initialization, refund or resumption."""
    from fastapi.responses import JSONResponse
    from app.services.production_spend_runtime import budget_status

    _require_auth(studio_token)
    return JSONResponse(budget_status(read_timeout=2), headers={'Cache-Control': 'no-store'})


def _begin_youtube_connect(
    request: Request,
    studio_token: str | None,
    *,
    target_channel_id: str | None = None,
    analytics: bool = False,
):
    _require_auth(studio_token)
    # Starting a flow rotates the global authorization epoch, so it is a
    # same-origin POST rather than a CSRF-able state-changing GET.
    _require_same_origin(request)
    browser_binding = secrets.token_urlsafe(32)
    try:
        options = {'target_channel_id': target_channel_id} if target_channel_id is not None else {}
        if analytics:
            options['analytics'] = True
        response = RedirectResponse(
            build_authorization_url(browser_binding, **options),
            status_code=302,
        )
    except YouTubeAuthError as exc:
        raise HTTPException(status_code=503, detail='Google OAuth başlatılamadı') from exc
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.set_cookie(
        OAUTH_BINDING_COOKIE,
        browser_binding,
        max_age=STATE_TTL_SECONDS,
        httponly=True,
        secure=True,
        samesite='lax',
        path=OAUTH_CALLBACK_PATH,
    )
    return response


@router.post('/studio/youtube/connect')
def youtube_connect(
    request: Request,
    studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
):
    return _begin_youtube_connect(request, studio_token)


@router.post('/studio/youtube/reconnect/{youtube_channel_id}')
def youtube_reconnect(
    request: Request,
    youtube_channel_id: str,
    studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
):
    return _begin_youtube_connect(request, studio_token, target_channel_id=youtube_channel_id)


@router.post('/studio/youtube/analytics/connect/{youtube_channel_id}')
def youtube_analytics_connect(request: Request, youtube_channel_id: str,
        studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    return _begin_youtube_connect(request, studio_token,
        target_channel_id=youtube_channel_id, analytics=True)


@router.get('/studio/analytics', response_class=HTMLResponse)
def youtube_analytics_home(studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    _require_auth(studio_token)
    from app.services.youtube_analytics import dashboard
    from app.services.studio_analytics import render
    from app.studio import _shell as studio_shell
    return studio_shell(render(dashboard()), active='analytics', title='İzleyici analizi · Studio')


@router.get('/studio/youtube/callback', name='youtube_oauth_callback')
def youtube_oauth_callback(
    request: Request,
    state: str = '',
    code: str = '',
    error: str = '',
    oauth_binding: str | None = Cookie(default=None, alias=OAUTH_BINDING_COOKIE),
):
    del request
    try:
        if error:
            discard_authorization_state(state, oauth_binding or '')
            return _delete_oauth_binding_cookie(_shell(
                '<div class="hero"><h1>Google izni tamamlanmadı</h1></div><div class="notice">Bağlantı kurulmadı; istersen güvenli giriş akışını yeniden başlatabilirsin.</div><div class="actions"><a class="btn secondary" href="/studio/youtube">Geri dön</a></div>',
                status_code=400,
            ))
        channel = complete_authorization(code, state, oauth_binding or '')
        if isinstance(channel, dict) and channel.get('analytics_connected') is True:
            return _delete_oauth_binding_cookie(_shell(
                '<div class="hero"><h1>İzleyici analizi bağlandı</h1></div>'
                '<div class="card">İzlenme süresi ve izleyici tutma raporları sunucuda düzenli okunacak. '
                'İlk rapor bir sonraki saatlik kontrolde görünecek.</div><div class="actions">'
                '<a class="btn" href="/studio/analytics">Performansa dön</a></div>'))
        # Older Studio session cookies are SameSite=Strict. Render a same-origin
        # document before navigating back so the cookie is available after the
        # cross-site Google callback without weakening CSRF protection.
        return _delete_oauth_binding_cookie(_shell(
            '<div class="hero"><h1>Kanal doğrulandı</h1></div><div class="card">YouTube bağlantısı tamamlandı. Studio’ya dönülüyor…</div><div class="actions"><a class="btn success" href="/studio/youtube?connected=1">Studio’ya dön</a></div>',
            script='<script>window.location.replace("/studio/youtube?connected=1")</script>',
        ))
    except YouTubeAuthError as exc:
        if str(exc) == 'youtube_oauth_channel_mismatch':
            detail = getattr(exc, 'channel_mismatch', None)
            selected = ''
            back = '/studio/youtube'
            if type(detail) is dict and set(detail) == {'target_id', 'target_title', 'selected_title'}:
                selected = ('<p>Yenilenecek kanal: <strong>' + escape(detail['target_title'])
                    + '</strong><br>Google’dan gelen kanal: <strong>'
                    + escape(detail['selected_title']) + '</strong></p>')
                if re.fullmatch(r'UC[A-Za-z0-9_-]{22}', detail['target_id']):
                    back += '#channel-' + detail['target_id']
            return _delete_oauth_binding_cookie(_shell(
                '<div class="hero"><h1>Farklı bir kanal seçildi</h1></div><div class="notice">'
                'Yenilemek istediğin kanal seçilmediği için bu bağlantı kaydedilmedi.' + selected
                + '<p>Kanallara dön, ilgili kanalın <strong>Yeniden bağla</strong> düğmesine bas. '
                'Google’da o kanalı yönettiğin hesabı ve ardından kanal adını seç.</p>'
                '<p>Kanal seçimi çıkmıyorsa YouTube’da profil menüsünden istediğin kanala geç. '
                'YouTube → Ayarlar → Gelişmiş ayarlar bölümünde bu kanalı hesabın varsayılan kanalı '
                'yapıp bağlantıyı yeniden başlat. '
                '<a href="https://support.google.com/youtube/answer/6019090?hl=tr" '
                'target="_blank" rel="noopener noreferrer">Google’ın kanal seçimi açıklaması</a></p>'
                '</div><div class="actions"><a class="btn secondary" href="' + back
                + '">Kanallara dön</a></div>',
                status_code=400,
            ))
        detail = ('Bağlantı oturumu sona ermiş veya başka bir bağlantı işlemiyle değişmiş. '
            'İlgili sayfadan bağlantıyı yeniden başlat.' if isinstance(exc, OAuthStateError) else
            'Google izni kaydedilemedi. Mevcut kanal bağlantın korunuyor. '
            'Bağlantıyı yeniden başlatıp istenen izinleri işaretle; sorun sürerse bize bildir.')
        return _delete_oauth_binding_cookie(_shell(
            '<div class="hero"><h1>Bağlantı kurulamadı</h1></div><div class="notice">' + detail
            + '</div><div class="actions"><a class="btn secondary" href="/studio/analytics">Performansa dön</a>'
            '<a class="btn secondary" href="/studio/youtube">Kanallara dön</a></div>',
            status_code=400,
        ))


@router.post('/studio/youtube/disconnect')
def youtube_disconnect(
    request: Request,
    youtube_channel_id: str = Form(...),
    connection_id: str = Form(...),
    studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
):
    _require_auth(studio_token)
    _require_same_origin(request)
    try:
        disconnect(
            youtube_channel_id,
            expected_connection_id=connection_id,
            revoke=True,
        )
    except YouTubeAuthError as exc:
        raise HTTPException(status_code=409, detail='YouTube bağlantısı değişti; sayfayı yenile') from exc
    return RedirectResponse('/studio/youtube', status_code=303)


@router.post('/studio/youtube/profile/{youtube_channel_id}')
def youtube_save_profile(
    youtube_channel_id: str,
    request: Request,
    connection_id: str = Form(...),
    expected_revision: str = Form(''),
    channel_identity: str = Form(''),
    route_label: str = Form(''),
    languages: str = Form('tr'),
    default_language: str = Form('tr'),
    topic_keywords: str = Form(''),
    default_tags: str = Form(''),
    hashtags: str = Form(''),
    category_id: str = Form('28'),
    description_footer: str = Form(''),
    series_id: str = Form(''),
    series_name: str = Form(''),
    series_total: int = Form(0),
    release_mode: str = Form('private'),
    schedule_delay_minutes: int = Form(60),
    auto_publish: str = Form(''),
    require_thumbnail: str = Form(''),
    production_settings: str = Form(''),
    production_enabled: str = Form(''),
    production_topics: str = Form(''),
    production_interval_hours: int = Form(24),
    studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
):
    _require_auth(studio_token)
    _require_same_origin(request)
    status = connection_status(channel_id=youtube_channel_id)
    channel = status.get('channel') if isinstance(status.get('channel'), dict) else {}
    if (
        str(channel.get('id') or '') != youtube_channel_id
        or str(channel.get('connection_id') or '') != connection_id
    ):
        raise HTTPException(
            status_code=409,
            detail='YouTube bağlantısı değişti; sayfayı yenile',
        )
    try:
        save_channel_profile(
            youtube_channel_id,
            {
                'channel_identity': channel_identity,
                'route_label': route_label,
                'languages': languages,
                'default_language': default_language,
                'topic_keywords': topic_keywords,
                'default_tags': default_tags,
                'hashtags': hashtags,
                'category_id': category_id,
                'description_footer': description_footer,
                'series_id': series_id,
                'series_name': series_name,
                'series_total': series_total,
                'release_mode': release_mode,
                'schedule_delay_minutes': schedule_delay_minutes,
                'auto_publish': auto_publish == '1',
                'require_thumbnail': require_thumbnail == '1',
                **({
                    'production_enabled': production_enabled == '1',
                    'production_topics': production_topics,
                    'production_interval_hours': production_interval_hours,
                } if production_settings == '1' else {}),
            },
            expected_revision=expected_revision or None,
        )
    except SeriesProfileEditError as exc:
        raise HTTPException(
            status_code=409,
            detail=('Bu profil otomatik seri geçmişine bağlı; değişiklik yeni bir seri geçişi gerektiriyor. '
                    'Üretimi veya otomatik yayını kapatabilirsiniz.'),
        ) from exc
    except ProfileConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail='Kanal profili başka bir işlemde değişti; sayfayı yenile',
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail='Kanal profili geçersiz') from exc
    except YouTubeAutomationError as exc:
        raise HTTPException(status_code=503, detail='Kanal profili kaydedilemedi') from exc
    return RedirectResponse('/studio/youtube', status_code=303)


@router.post('/studio/youtube/release/{source_task_id}')
def youtube_release_existing(
    source_task_id: str,
    request: Request,
    expected_video_id: str = Form(...),
    youtube_channel_id: str = Form(...),
    expected_profile_revision: str = Form(...),
    studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
):
    _require_auth(studio_token)
    _require_same_origin(request)
    from app.services.existing_video_release import ExistingVideoReleaseError, release_existing_private_video

    try:
        release_existing_private_video(
            source_task_id,
            expected_video_id=expected_video_id,
            expected_channel_id=youtube_channel_id,
            expected_profile_revision=expected_profile_revision,
        )
    except ExistingVideoReleaseError as exc:
        raise HTTPException(
            status_code=409,
            detail='YouTube yayını doğrulanamadı; mevcut video korunuyor, tekrar yükleme yapılmadı.',
        ) from exc
    return RedirectResponse('/studio/youtube', status_code=303)


@router.post('/studio/youtube/produce-now/{youtube_channel_id}')
def youtube_produce_next_now(
    youtube_channel_id: str,
    request: Request,
    expected_revision: str = Form(...),
    studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
):
    _require_auth(studio_token)
    _require_same_origin(request)
    from app.services.production_schedule_control import ProductionScheduleControlError, expedite_next_production

    try:
        expedite_next_production(youtube_channel_id, expected_revision)
    except ProductionScheduleControlError as exc:
        raise HTTPException(
            status_code=409,
            detail='Üretim sırası değişti veya kanal meşgul; sayfayı yenile. Tekrar üretim başlatılmadı.',
        ) from exc
    return RedirectResponse('/studio/youtube', status_code=303)


@router.post('/studio/youtube/publish/{source_task_id}')
def youtube_publish(
    source_task_id: str,
    request: Request,
    youtube_channel_id: str = Form(...),
    studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
):
    _require_auth(studio_token)
    _require_same_origin(request)
    status = connection_status(channel_id=youtube_channel_id)
    channel = status.get('channel') if isinstance(status.get('channel'), dict) else {}
    target_channel_id = str(channel.get('id') or '')
    connection_id = str(channel.get('connection_id') or '')
    if target_channel_id != str(youtube_channel_id) or not connection_id:
        raise HTTPException(status_code=409, detail='YouTube bağlantısı yeniden doğrulanmalı')
    source = get_job(source_task_id)
    if not source or source.get('state') != 'SUCCESS':
        raise HTTPException(status_code=404, detail='Yayınlanabilir tamamlanmış video bulunamadı')
    source_result = source.get('result') if isinstance(source.get('result'), dict) else {}
    prior_youtube = source_result.get('youtube') if isinstance(source_result.get('youtube'), dict) else {}
    if prior_youtube.get('video_id'):
        prior_target = str(prior_youtube.get('target_channel_id') or '')
        if prior_target and prior_target != target_channel_id:
            raise HTTPException(
                status_code=409,
                detail='Bu final başka bir YouTube kanalına yüklenmiş',
            )
        return RedirectResponse('/studio/youtube', status_code=303)
    quality_approved = automated_quality_approved(source)
    if source_result.get('quality_disposition') == 'editorial_review_pass':
        from app.services.external_editorial_review import publication_quality_approved

        quality_approved = (
            publication_quality_approved(source)
            and (source.get('spec') or {}).get('production_channel_id') == target_channel_id
        )
    if not quality_approved:
        raise HTTPException(
            status_code=409,
            detail='Kalite onayı olmayan video YouTube’a yüklenemez',
        )

    publish_plan = None
    if isinstance(source_result.get('publish_metadata'), dict):
        try:
            profile = get_channel_profile(target_channel_id) or {
                'schema_version': 1,
                'channel_id': target_channel_id,
                'profile_revision': 'manual-private',
                'languages': [(source.get('spec') or {}).get('language') or 'tr'],
                'default_language': (source.get('spec') or {}).get('language') or 'tr',
                'category_id': '28',
                'release_mode': 'private',
            }
            profile = dict(profile)
            # The explicit dashboard action remains a private-upload action.
            # Autonomous release policy is applied only by the post-QA router.
            profile['release_mode'] = 'private'
            publish_plan = build_publish_plan(source_task_id, source, profile)
        except YouTubeAutomationError as exc:
            raise HTTPException(
                status_code=503,
                detail='YouTube metadata planı oluşturulamadı',
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail='YouTube metadata planı geçersiz',
            ) from exc

    task_id = str(uuid4())
    try:
        reservation, created = reserve_upload(
            source_task_id,
            task_id,
            target_channel_id=target_channel_id,
            connection_id=connection_id,
            publish_plan=publish_plan,
        )
    except (UploadReservationError, ValueError) as exc:
        raise HTTPException(status_code=503, detail='YouTube yükleme kaydı oluşturulamadı') from exc
    if not created:
        if (
            str(reservation.get('target_channel_id') or '') != target_channel_id
        ):
            raise HTTPException(
                status_code=409,
                detail='Bu final başka bir YouTube kanalına ayrılmış',
            )
        if reservation.get('status') == 'complete' and reservation.get('youtube_video_id'):
            # A worker may have died after persisting the remote video ID but
            # before updating the Studio source job. A new task can safely
            # reconcile that record; its complete branch never calls insert.
            create_job(
                task_id,
                {
                    'topic': source_result.get('title') or 'YouTube upload recovery',
                    'source_task_id': source_task_id,
                    'privacy_status': 'private',
                    'mode': 'publish_recovery',
                    'target_channel_id': reservation.get('target_channel_id'),
                    'connection_id': reservation.get('connection_id'),
                },
                kind='publish',
                parent_id=source_task_id,
            )
            try:
                publish_video_pipeline.apply_async(
                    args=(source_task_id, 'private'),
                    task_id=task_id,
                )
            except Exception as exc:
                mark_failure(task_id, 'YouTube upload recovery could not be queued')
                raise HTTPException(
                    status_code=503,
                    detail='YouTube yükleme kaydı toparlanamadı',
                ) from exc
            return RedirectResponse(
                f'/studio/youtube/publish-status/{task_id}',
                status_code=303,
            )
        existing_task_id = str(reservation.get('publish_task_id') or '')
        if existing_task_id:
            return RedirectResponse(
                f'/studio/youtube/publish-status/{existing_task_id}',
                status_code=303,
            )
        raise HTTPException(status_code=409, detail='Bu video için yükleme zaten ayrılmış')

    source_spec = source.get('spec') or {}
    create_job(
        task_id,
        {
            'topic': source_spec.get('topic') or source_result.get('title') or 'YouTube upload',
            'source_task_id': source_task_id,
            'privacy_status': 'private',
            'mode': 'publish',
            'target_channel_id': target_channel_id,
            'connection_id': connection_id,
            'profile_revision': (
                publish_plan.get('profile_revision') if publish_plan else None
            ),
            'series': publish_plan.get('series') if publish_plan else None,
        },
        kind='publish',
        parent_id=source_task_id,
    )
    try:
        publish_video_pipeline.apply_async(
            args=(source_task_id, 'private'),
            task_id=task_id,
        )
    except Exception as exc:
        mark_upload_preflight_failed(source_task_id, task_id, 'queue_unavailable')
        mark_failure(task_id, 'YouTube private upload could not be queued')
        raise HTTPException(status_code=503, detail='YouTube yüklemesi kuyruğa alınamadı') from exc
    try:
        mark_upload_enqueued(source_task_id, task_id)
    except UploadReservationError:
        # apply_async already succeeded. The worker either owns the reservation
        # now or will fail it safely; never enqueue a second insert here.
        pass
    return RedirectResponse(
        f'/studio/youtube/publish-status/{task_id}',
        status_code=303,
    )


@router.get('/studio/youtube/publish-status/{task_id}', response_class=HTMLResponse)
def youtube_publish_status(
    task_id: str,
    studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
):
    _require_auth(studio_token)
    presentation = _studio_presentation()
    canonical_id = presentation._canonical_task_id(task_id)
    publisher = get_job(canonical_id) if canonical_id else None
    if not isinstance(publisher, dict) or publisher.get('task_id') != canonical_id or publisher.get('kind') != 'publish':
        publisher = {'kind': 'publish', 'state': 'PENDING', 'task_id': canonical_id}
    displayed = publisher
    spec = publisher.get('spec') if isinstance(publisher.get('spec'), dict) else {}
    source_id = presentation._canonical_task_id(spec.get('source_task_id'))
    if source_id and publisher.get('parent_id') == source_id:
        source = get_job(source_id)
        source_result = source.get('result') if isinstance(source, dict) else None
        output = source_result.get('youtube') if isinstance(source_result, dict) else None
        delivered = publisher.get('result')
        if (
            isinstance(source, dict) and source.get('task_id') == source_id and source.get('kind') == 'render'
            and isinstance(output, dict) and isinstance(delivered, dict)
            and delivered.get('source_task_id') == source_id
            and all(isinstance(output.get(a), str) and output[a] and output[a] == delivered.get(b)
                    for a, b in (('video_id', 'youtube_video_id'), ('target_channel_id', 'target_channel_id'),
                                 ('connection_id', 'connection_id')))
        ):
            # Current source attribution can reflect an explicit later release;
            # the original publisher's private result remains historical.
            displayed = presentation._with_publication_presentation(source, lambda value: publisher if value == canonical_id else None)
    metrics = presentation._dashboard_metrics([displayed])
    displayed = presentation._with_youtube_metrics(displayed, metrics)
    delivery = presentation._video_delivery(displayed)
    label = delivery['label'] or 'YouTube yükleme durumu'
    publication = presentation._publication_status(displayed)
    stage = 'Yayın durdu' if publication == 'blocked' else 'Sonuç doğrulanmalı' if publication == 'uncertain' else label
    message = presentation._job_status_message(displayed)
    try:
        progress = max(0, min(100, int(publisher.get('progress') or 0)))
    except (TypeError, ValueError, OverflowError):
        progress = 0
    watch_url = _youtube_watch_url(displayed)
    result_html = (
        f'<a class="btn secondary" target="_blank" rel="noopener noreferrer" href="{watch_url}">YouTube’da aç</a>'
        if watch_url else ''
    )
    poll_id = presentation._canonical_task_id(displayed.get('task_id'))
    body = f'''
<div class="hero"><div class="hero-copy"><div class="eyebrow">YouTube teslimatı</div><h1 id="delivery-title">{escape(label)}</h1><div class="muted">Dosyanın yüklenmesi ve herkese açık yayın durumu ayrı izlenir.</div></div><div class="hero-tools"><span class="badge">İlk yükleme: 🔒 Gizli</span></div></div><div class="card" aria-live="polite"><div class="delivery-badges" id="delivery-badges" data-delivery-task="{poll_id}">{presentation._delivery_badges(displayed)}</div>{presentation._video_identity(displayed)}<div id="stage"><b>{escape(stage)}</b></div><div class="progress" id="progress" role="progressbar" aria-label="YouTube dosya aktarımı" aria-valuemin="0" aria-valuemax="100" aria-valuenow="{progress}" style="margin:14px 0"><div class="bar" id="bar" style="width:{progress}%"></div></div><div class="muted" id="message">{escape(message)}</div><div id="result" class="actions">{result_html}</div>{presentation._video_performance(displayed)}</div><div class="actions"><a class="btn secondary" href="/studio/youtube">← Yayın merkezine dön</a></div>'''
    safe_id = json.dumps(poll_id)
    script = f'''<script>
const id={safe_id};const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
async function poll(){{if(!id||document.visibilityState==='hidden'){{if(id)setTimeout(poll,5000);return}}try{{const r=await fetch(`/studio/api/job/${{encodeURIComponent(id)}}`,{{credentials:'same-origin',cache:'no-store'}});if(!r.ok)throw new Error('status');const j=await r.json();if(j.task_id!==id)throw new Error('identity');const raw=Number(j.progress||0),p=Number.isFinite(raw)?Math.max(0,Math.min(100,raw)):0;document.getElementById('bar').style.width=p+'%';document.getElementById('progress').setAttribute('aria-valuenow',String(p));const warning={{blocked:'Yayın durdu',uncertain:'Sonuç doğrulanmalı',failed:'Yükleme tamamlanamadı'}}[j.publication_status];document.getElementById('stage').textContent=warning||j.delivery_label||j.stage_label||'Durum bekleniyor';document.getElementById('delivery-title').textContent=j.delivery_label||'YouTube yükleme durumu';document.getElementById('message').textContent=j.ui_status_message||j.message||'';document.getElementById('delivery-badges').innerHTML=(j.delivery_label?'<span class="state private">'+esc(j.delivery_label)+'</span>':'')+(warning?'<span class="state attention">'+esc(warning)+'</span>':'');const video=j.delivery_video_id;document.getElementById('result').innerHTML=/^[A-Za-z0-9_-]{{11}}$/.test(video||'')?`<a class="btn secondary" target="_blank" rel="noopener noreferrer" href="https://www.youtube.com/watch?v=${{video}}">YouTube’da aç</a>`:'';if(j.state==='FAILURE'||j.state==='SUCCESS')return;setTimeout(poll,3000)}}catch(e){{document.getElementById('message').textContent='Durum geçici olarak alınamadı; mevcut video korunuyor.';setTimeout(poll,5000)}}}}
poll();</script>'''
    return _shell(body, title='YouTube teslimat durumu', script=script + presentation._metrics_script(), extra_css=presentation.METRICS_CSS)
