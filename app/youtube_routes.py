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
    YouTubeAuthError,
    build_authorization_url,
    complete_authorization,
    connection_status,
    discard_authorization_state,
    disconnect,
)
from app.services.youtube_automation import (
    ProfileConflictError,
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
        # different host and attach this host's Strict Studio cookie.
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
) -> HTMLResponse:
    response = HTMLResponse(
        '<!doctype html><html lang="tr"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        '<meta name="theme-color" content="#090c11">'
        f'<title>{escape(title)}</title><style>{CSS}</style></head><body>'
        '<a class="skip-link" href="#main-content">İçeriğe geç</a><div class="wrap">'
        '<header class="top"><a class="brand" href="/studio">YouTube Studio</a>'
        '<nav class="nav" aria-label="Ana menü"><a href="/studio">Yeni video</a>'
        '<a href="/studio/history?status=library">Videolar</a>'
        '<a class="active" aria-current="page" href="/studio/youtube">YouTube</a></nav></header>'
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


def _profile_form(channel: dict, profile: dict | None) -> str:
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
    thumbnail_checked = ' checked' if profile.get('require_thumbnail') else ''
    status = (
        '<span class="badge good">Otomatik rota açık</span>'
        if profile.get('auto_publish')
        else '<span class="badge">Otomatik rota kapalı</span>'
    )
    return f'''
<details class="profile-details"><summary>Otomasyon ve yayın profili</summary>
<form method="post" action="/studio/youtube/profile/{escape(channel_id, quote=True)}">
<input type="hidden" name="connection_id" value="{escape(connection_id, quote=True)}">
<input type="hidden" name="expected_revision" value="{escape(revision, quote=True)}">
<div class="profile-grid">
<label class="wide">Kanal kimliği / yayın çizgisi<input name="channel_identity" maxlength="240" value="{value('channel_identity')}" placeholder="Kısa, merak uyandıran Türkçe bilim hikâyeleri"></label>
<label>Studio kanal etiketi<input name="route_label" maxlength="120" value="{value('route_label')}" placeholder="merak-belgesel-tr-01"></label>
<label>Diller<input name="languages" maxlength="180" value="{value('languages', 'tr')}" placeholder="tr, en"></label>
<label class="wide">Konu anahtarları<input name="topic_keywords" maxlength="1200" value="{value('topic_keywords')}" placeholder="havacılık, bilim, teknoloji"></label>
<label>Varsayılan dil<input name="default_language" maxlength="24" value="{value('default_language', 'tr')}" placeholder="tr"></label>
<label>Kategori no<input name="category_id" maxlength="3" value="{value('category_id', '28')}" inputmode="numeric"></label>
<label class="wide">Etiketler<input name="default_tags" maxlength="1600" value="{value('default_tags')}" placeholder="bilim, merak, kısa belgesel"></label>
<label class="wide">Hashtagler<input name="hashtags" maxlength="700" value="{value('hashtags')}" placeholder="Bilim, Merak, Shorts"></label>
<label class="wide">Açıklama alt bilgisi<textarea name="description_footer" maxlength="1200" placeholder="Kanal imzası ve sabit bilgi">{escape(str(profile.get('description_footer') or ''))}</textarea></label>
<label>Seri kodu<input name="series_id" maxlength="80" value="{value('series_id')}" placeholder="ucak-sirlari-1"></label>
<label>Seri adı<input name="series_name" maxlength="100" value="{value('series_name')}" placeholder="Uçak Sırları"></label>
<label>Seri toplamı<input name="series_total" type="number" min="0" max="10000" value="{value('series_total', '0')}"></label>
<label>Yayın davranışı<select name="release_mode">{release_options}</select></label>
<label>Planlama gecikmesi (dk)<input name="schedule_delay_minutes" type="number" min="15" max="43200" value="{value('schedule_delay_minutes', '60')}"></label>
<label class="check"><input name="auto_publish" type="checkbox" value="1"{checked}> Kalite kapısını geçen videoları bu rotaya otomatik gönder</label>
<label class="check"><input name="require_thumbnail" type="checkbox" value="1"{thumbnail_checked}> Özel küçük resim yoksa herkese açma</label>
</div>
<div class="profile-actions"><button class="small" type="submit">Profili kaydet</button>{status}<span class="tiny">İlk yükleme her zaman gizlidir; yalnızca tam otomatik kalite onayı yayın geçişini açar.</span></div>
</form></details>'''


@router.get('/studio/youtube', response_class=HTMLResponse)
def youtube_home(
    connected: int = 0,
    studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
):
    _require_auth(studio_token)
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
        for channel in connections:
            channel_id = str(channel.get('id') or '')
            connection_id = str(channel.get('connection_id') or '')
            profile_form = _profile_form(channel, profiles.get(channel_id))
            channel_cards.append(f'''
<article class="channel-card"><div class="channel-card-head"><div><div class="channel-title">{escape(_ellipsize(_safe_ui_text(channel.get('title') or 'YouTube kanalı'), 60))}</div><div class="tiny">Yüklemeye hazır</div></div><span class="badge good">● Bağlı</span></div><div class="channel-metrics"><span class="badge">{escape(str(channel.get('subscriber_count') or '—'))} abone</span><span class="badge">{escape(str(channel.get('video_count') or '—'))} video</span><span class="badge">{escape(str(channel.get('view_count') or '—'))} izlenme</span></div>{profile_form}<div class="channel-actions"><form method="post" action="/studio/youtube/disconnect"><input type="hidden" name="youtube_channel_id" value="{escape(channel_id, quote=True)}"><input type="hidden" name="connection_id" value="{escape(connection_id, quote=True)}"><button class="btn danger small" type="submit">Bağlantıyı kaldır</button></form></div></article>''')
        count = int(status.get('connection_count') or len(connections))
        limit = int(status.get('connection_limit') or 10)
        connection_notice = ''
        if not connections and status.get('requires_reconnect'):
            connection_notice = '<div class="notice" role="alert">En az bir YouTube bağlantısı yenilenmeli.</div>'
        if count < limit:
            connect_action = f'<div class="actions"><form class="inline" method="post" action="/studio/youtube/connect"><button type="submit">+ Google ile kanal bağla</button></form><span class="tiny">{count}/{limit} kanal kullanılıyor</span></div>'
        else:
            connect_action = f'<div class="notice">Kanal sınırı dolu ({count}/{limit}). Yeni kanal için önce bir bağlantıyı kaldır.</div>'
        empty_channels = '<div class="empty">Henüz bağlı kanal yok.</div>' if not channel_cards else ''
        account_card = f'''<section class="card"><div class="section-head"><div><span class="section-kicker">HESAPLAR</span><h2>Bağlı kanallar</h2><div class="muted">Her video yükleme anında tek bir hedef kanala sabitlenir.</div></div><span class="badge">{count}/{limit}</span></div>{connection_notice}<div class="channel-grid">{''.join(channel_cards)}</div>{empty_channels}{connect_action}</section>'''
    else:
        account_card = '''
<div class="notice" role="alert"><b>Google bağlantı ayarları eksik.</b><p>Google istemcisi, yönlendirme adresi ve ayrı şifreleme anahtarı Railway’de güvenli ortam değişkenleri olarak tanımlanmalı.</p></div>'''

    rows = []
    for index, job in enumerate(_completed_jobs(), start=1):
        result = job.get('result') or {}
        spec = job.get('spec') or {}
        raw_title = _ready_title(job)
        title = escape(raw_title)
        brief = _ready_brief(job)
        duration = _ready_duration(job)
        created = _ready_date(job)
        channel_label = _ellipsize(_safe_ui_text(spec.get('channel_id')), 42)
        youtube = result.get('youtube') if isinstance(result.get('youtube'), dict) else {}
        if youtube.get('url'):
            action = f'<a class="btn success" target="_blank" rel="noopener noreferrer" href="{escape(str(youtube.get("url")), quote=True)}">YouTube’da aç</a>'
            release_status = str(youtube.get('release_status') or 'private')
            state_label = {
                'public': 'Yayında',
                'scheduled': 'Planlandı',
                'blocked': 'Gizli · yayın durdu',
                'uncertain': 'Yayın durumu doğrulanmalı',
            }.get(release_status, 'Gizli yüklendi')
            ready_state = f'<span class="badge good">{escape(state_label)}</span>'
        elif connections:
            options = ''.join(
                f'<option value="{escape(str(item.get("id") or ""), quote=True)}">{escape(_ellipsize(_safe_ui_text(item.get("title") or "YouTube kanalı"), 60))}</option>'
                for item in connections
            )
            selector_id = f'target-channel-{index}'
            action = f'''<form class="inline" method="post" action="/studio/youtube/publish/{escape(str(job.get('task_id') or ''), quote=True)}"><label class="sr-only" for="{selector_id}">Hedef YouTube kanalı</label><select id="{selector_id}" name="youtube_channel_id" required aria-label="Hedef YouTube kanalı">{options}</select><button type="submit">Gizli yükle</button></form>'''
            ready_state = '<span class="badge ready">Hazır</span>'
        else:
            action = '<span class="tiny">Önce bir YouTube kanalı bağla</span>'
            ready_state = '<span class="badge ready">Hazır</span>'
        metadata = [value for value in (duration, created) if value]
        metadata.append(
            f'Kanal etiketi: {channel_label}'
            if channel_label else 'Hedef kanal yüklerken seçilecek'
        )
        meta_html = ''.join(f'<span>{escape(value)}</span>' for value in metadata)
        details = ''
        if brief and _plain_text(brief) != _plain_text(raw_title):
            details = (
                '<details class="brief-details"><summary>Yaratıcı talimatı gör</summary>'
                f'<div class="brief-full">{escape(brief)}</div></details>'
            )
        rows.append(
            f'<article class="video-card"><div class="video-main"><div class="video-title">{title}</div><div class="video-meta" aria-label="Video bilgileri">{meta_html}</div></div><div class="video-actions">{ready_state}{action}</div>{details}</article>'
        )
    jobs_html = ''.join(rows) or '<div class="empty">Yüklenebilir tamamlanmış video henüz yok.</div>'
    success = '<div class="notice success" role="status">YouTube kanalı başarıyla bağlandı.</div>' if connected else ''
    body = f'''
<div class="hero"><div class="hero-copy"><div class="eyebrow">YouTube</div><h1>Yayın merkezi</h1><div class="muted">Kanal rotalarını bir kez tanımla; başlık, açıklama, etiket, seri ve yayın akışı otomatik yürüsün.</div></div><div class="hero-tools"><span class="badge good">🔒 İlk yükleme daima gizli</span><span class="badge">En fazla 10 kanal</span></div></div>{success}{account_card}<section class="card"><div class="section-head"><div><span class="section-kicker">YAYINA HAZIR</span><h2>Hazır videolar</h2><div class="muted">Başlık ve temel bilgiler önde; uzun talimat istenirse açılır.</div></div><span class="badge">{len(rows)} video</span></div><div class="video-list">{jobs_html}</div></section>'''
    return _shell(body, same_origin_forms=True)


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


@router.post('/studio/youtube/connect')
def youtube_connect(
    request: Request,
    studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
):
    _require_auth(studio_token)
    # Starting a flow rotates the global authorization epoch, so it is a
    # same-origin POST rather than a CSRF-able state-changing GET.
    _require_same_origin(request)
    browser_binding = secrets.token_urlsafe(32)
    try:
        response = RedirectResponse(
            build_authorization_url(browser_binding),
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
        complete_authorization(code, state, oauth_binding or '')
        # The Studio session cookie is SameSite=Strict. Render one same-origin
        # document before navigating back so the cookie is available after the
        # cross-site Google callback without weakening CSRF protection.
        return _delete_oauth_binding_cookie(_shell(
            '<div class="hero"><h1>Kanal doğrulandı</h1></div><div class="card">YouTube bağlantısı tamamlandı. Studio’ya dönülüyor…</div><div class="actions"><a class="btn success" href="/studio/youtube?connected=1">Studio’ya dön</a></div>',
            script='<script>window.location.replace("/studio/youtube?connected=1")</script>',
        ))
    except YouTubeAuthError:
        return _delete_oauth_binding_cookie(_shell(
            '<div class="hero"><h1>Bağlantı kurulamadı</h1></div><div class="notice">OAuth yanıtı geçersiz, süresi dolmuş veya daha önce kullanılmış. Güvenli bağlantıyı yeniden başlat.</div><div class="actions"><a class="btn secondary" href="/studio/youtube">Geri dön</a></div>',
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
            },
            expected_revision=expected_revision or None,
        )
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
    if not automated_quality_approved(source):
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
    body = f'''
<div class="hero"><div class="hero-copy"><div class="eyebrow">Gizli yükleme</div><h1>YouTube’a gönderiliyor</h1><div class="muted">Video hedef kanala aktarılıyor. Bu sayfa kendiliğinden güncellenir.</div></div><div class="hero-tools"><span class="badge good">🔒 Gizli</span></div></div><div class="card" aria-live="polite"><div id="stage"><b>Başlatılıyor…</b></div><div class="progress" id="progress" role="progressbar" aria-label="YouTube yükleme ilerlemesi" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0" style="margin:14px 0"><div class="bar" id="bar"></div></div><div class="muted" id="message">Final master hazırlanıyor.</div><div id="result"></div></div><div class="actions"><a class="btn secondary" href="/studio/youtube">← Yayın merkezine dön</a></div>'''
    safe_id = json.dumps(task_id)
    script = f'''<script>
const id={safe_id};const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
async function poll(){{try{{const r=await fetch(`/studio/api/job/${{encodeURIComponent(id)}}`,{{cache:'no-store'}});const j=await r.json();const p=Math.max(0,Math.min(100,Number(j.progress||0)));document.getElementById('bar').style.width=p+'%';document.getElementById('progress').setAttribute('aria-valuenow',String(p));document.getElementById('stage').innerHTML='<b>'+esc(j.stage_label||j.stage||j.state)+'</b> · %'+p;document.getElementById('message').textContent=j.message||'';if(j.state==='FAILURE'){{document.getElementById('result').innerHTML='<div class="notice" role="alert">Yükleme tamamlanamadı. Tekrar yükleme başlatılmadan önce sonuç güvenle doğrulanmalıdır.</div>';return}}if(j.state==='SUCCESS'){{const x=j.result||{{}};document.getElementById('result').innerHTML=`<div class="actions"><a class="btn success" target="_blank" rel="noopener noreferrer" href="${{esc(x.youtube_url)}}">▶ YouTube’da aç</a><a class="btn secondary" href="/studio/youtube">Yayın merkezine dön</a></div>`;return}}setTimeout(poll,3000)}}catch(e){{document.getElementById('message').textContent='Durum geçici olarak alınamadı.';setTimeout(poll,5000)}}}}
poll();</script>'''
    return _shell(body, title='YouTube yükleme', script=script)
