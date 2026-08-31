from __future__ import annotations

from html import escape
import json
import secrets
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import APIRouter, Cookie, HTTPException, Request
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
*{box-sizing:border-box}html{background:#080b11}body{margin:0;color:#eef2f8;font-family:Inter,system-ui,sans-serif;background:radial-gradient(circle at 10% 0,#263668 0,transparent 34%),#080b11;min-height:100vh}.wrap{max-width:1050px;margin:auto;padding:20px 20px 80px}a{color:inherit;text-decoration:none}.top{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:14px 0;border-bottom:1px solid #293143}.brand{font-weight:950}.nav{display:flex;gap:8px;flex-wrap:wrap}.nav a{padding:9px 12px;border:1px solid #344056;border-radius:999px;background:#111824;font-size:13px;font-weight:800}.hero{padding:34px 0 20px}.hero h1{font-size:40px;margin:0 0 8px;letter-spacing:-.04em}.muted{color:#9ba7b8}.card{background:rgba(19,24,35,.95);border:1px solid #2c3547;border-radius:20px;padding:18px;margin-bottom:14px}.btn,button{display:inline-flex;align-items:center;justify-content:center;border:0;border-radius:13px;padding:12px 15px;background:#ff0033;color:white;font:inherit;font-weight:900;cursor:pointer}.btn.secondary{background:#172030;border:1px solid #38445a}.btn.success{background:#198958}.btn.danger{background:#8e3540}.actions{display:flex;gap:9px;flex-wrap:wrap;margin-top:12px}.badge{display:inline-flex;padding:7px 10px;border-radius:999px;border:1px solid #354055;background:#101722;font-size:12px;font-weight:850;margin:3px}.job{display:grid;grid-template-columns:1fr auto;gap:12px;align-items:center;padding:14px;border:1px solid #303a4d;border-radius:14px;background:#0d131d;margin-bottom:9px}.job-title{font-weight:900}.tiny{font-size:12px;color:#8996a8}form.inline{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.notice{border:1px solid #6e5f22;background:#2a2411;color:#f4df89;border-radius:14px;padding:13px}.progress{height:13px;border:1px solid #344054;background:#090e16;border-radius:999px;overflow:hidden}.bar{height:100%;width:0;background:linear-gradient(90deg,#ff0033,#ff8b33);transition:width .35s ease}pre{white-space:pre-wrap;word-break:break-word;background:#090e16;border:1px solid #2d3748;border-radius:14px;padding:14px}@media(max-width:650px){.wrap{padding:10px 10px 70px}.hero h1{font-size:31px}.top{align-items:flex-start}.job{grid-template-columns:1fr}.actions .btn,form.inline button{width:100%}}
'''


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
) -> HTMLResponse:
    response = HTMLResponse(
        '<!doctype html><html lang="tr"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{escape(title)}</title><style>{CSS}</style></head><body><div class="wrap">'
        '<div class="top"><a class="brand" href="/studio">🎬 YouTube Studio V2</a><nav class="nav"><a href="/studio">Yeni üretim</a><a href="/studio/history">Geçmiş</a><a href="/studio/youtube">YouTube</a></nav></div>'
        f'{body}</div>{script}</body></html>',
        status_code=status_code,
    )
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


def _completed_jobs() -> list[dict]:
    jobs = []
    for job in list_jobs(80):
        result = job.get('result') or {}
        if (
            job.get('state') == 'SUCCESS'
            and job.get('kind') == 'render'
            and isinstance(result, dict)
            and result.get('video_key')
        ):
            jobs.append(job)
    return jobs


@router.get('/studio/youtube', response_class=HTMLResponse)
def youtube_home(
    connected: int = 0,
    studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
):
    _require_auth(studio_token)
    status = connection_status()
    channel = status.get('channel') or {}

    if status.get('connected'):
        account_card = f'''
<div class="card"><h2>Bağlı kanal ✅</h2><p><b>{escape(str(channel.get('title') or 'YouTube kanalı'))}</b></p><div><span class="badge">{escape(str(channel.get('subscriber_count') or '—'))} abone</span><span class="badge">{escape(str(channel.get('video_count') or '—'))} video</span><span class="badge">{escape(str(channel.get('view_count') or '—'))} görüntülenme</span></div><div class="actions"><form method="post" action="/studio/youtube/disconnect"><button class="btn danger" type="submit">Bağlantıyı kaldır</button></form></div></div>'''
    elif status.get('configured'):
        reconnect = 'Bağlantı yenilenmeli' if status.get('requires_reconnect') else 'YouTube hesabı bağlı değil'
        account_card = f'<div class="card"><h2>{escape(reconnect)}</h2><p class="muted">Google izin ekranı tamamlandıktan sonra Studio yalnızca gizli video yükleyebilir.</p><form class="inline" method="post" action="/studio/youtube/connect"><button type="submit">Google ile YouTube’u bağla</button></form></div>'
    else:
        callback = escape(str(settings.google_redirect_uri or '/studio/youtube/callback'))
        account_card = f'''
<div class="notice"><b>Google OAuth ayarları eksik.</b><p>Google istemcisi, yönlendirme adresi ve ayrı şifreleme anahtarı güvenli ortam değişkenleri olarak tanımlanmalı.</p><p>Yönlendirme adresi:</p><pre>{callback}</pre></div>'''

    rows = []
    for job in _completed_jobs():
        result = job.get('result') or {}
        spec = job.get('spec') or {}
        topic = escape(str(spec.get('topic') or result.get('title') or 'Video'))
        duration = escape(str(round(float(result.get('duration') or 0), 1)))
        youtube = result.get('youtube') if isinstance(result.get('youtube'), dict) else {}
        if youtube.get('url'):
            action = f'<a class="btn success" target="_blank" rel="noopener noreferrer" href="{escape(str(youtube.get("url")), quote=True)}">YouTube’da aç</a>'
        elif status.get('connected'):
            action = f'''<form class="inline" method="post" action="/studio/youtube/publish/{escape(str(job.get('task_id') or ''))}"><button type="submit">Gizli olarak YouTube’a yükle</button></form>'''
        else:
            action = '<span class="tiny">Önce hesabı bağla</span>'
        rows.append(
            f'<div class="job"><div><div class="job-title">{topic}</div><div class="tiny">{duration} sn · {escape(str(job.get("task_id") or ""))}</div></div><div>{action}</div></div>'
        )
    jobs_html = ''.join(rows) or '<div class="card muted">Yüklenebilir tamamlanmış video henüz yok.</div>'
    success = '<div class="notice" style="border-color:#276744;background:#112d21;color:#8be5b4">YouTube hesabı ve kanal kimliği başarıyla doğrulandı.</div>' if connected else ''
    body = f'''
<div class="hero"><h1>YouTube yayın merkezi</h1><div class="muted">Final videolar önce gizli yüklenir; herkese açık yayın ayrı ve onaylı bir işlemdir.</div></div>{success}{account_card}<div class="card"><h2>Hazır videolar</h2><p class="muted">Aynı final için ikinci bir yükleme işi oluşturulmaz.</p>{jobs_html}</div>'''
    return _shell(body)


@router.get('/studio/youtube/status')
def youtube_connection_status(
    verify: bool = False,
    studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
):
    _require_auth(studio_token)
    status = connection_status(verify=verify)
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
    studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
):
    _require_auth(studio_token)
    _require_same_origin(request)
    disconnect(revoke=True)
    return RedirectResponse('/studio/youtube', status_code=303)


@router.post('/studio/youtube/publish/{source_task_id}')
def youtube_publish(
    source_task_id: str,
    request: Request,
    studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
):
    _require_auth(studio_token)
    _require_same_origin(request)
    status = connection_status()
    if not status.get('connected'):
        raise HTTPException(status_code=409, detail='YouTube hesabı bağlı değil')
    channel = status.get('channel') if isinstance(status.get('channel'), dict) else {}
    target_channel_id = str(channel.get('id') or '')
    connection_id = str(channel.get('connection_id') or '')
    if not target_channel_id or not connection_id:
        raise HTTPException(status_code=409, detail='YouTube bağlantısı yeniden doğrulanmalı')
    source = get_job(source_task_id)
    if not source or source.get('state') != 'SUCCESS':
        raise HTTPException(status_code=404, detail='Yayınlanabilir tamamlanmış video bulunamadı')
    source_result = source.get('result') if isinstance(source.get('result'), dict) else {}
    prior_youtube = source_result.get('youtube') if isinstance(source_result.get('youtube'), dict) else {}
    if prior_youtube.get('video_id'):
        return RedirectResponse('/studio/youtube', status_code=303)

    task_id = str(uuid4())
    try:
        reservation, created = reserve_upload(
            source_task_id,
            task_id,
            target_channel_id=target_channel_id,
            connection_id=connection_id,
        )
    except (UploadReservationError, ValueError) as exc:
        raise HTTPException(status_code=503, detail='YouTube yükleme kaydı oluşturulamadı') from exc
    if not created:
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
<div class="hero"><h1>YouTube’a yükleniyor</h1><div class="muted">Görev: {escape(task_id)}</div></div><div class="card"><div id="stage"><b>Başlatılıyor…</b></div><div class="progress" style="margin:14px 0"><div class="bar" id="bar"></div></div><div class="muted" id="message">Final master hazırlanıyor.</div><div id="result"></div></div><div class="actions"><a class="btn secondary" href="/studio/youtube">← Yayın merkezine dön</a></div>'''
    safe_id = json.dumps(task_id)
    script = f'''<script>
const id={safe_id};const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
async function poll(){{try{{const r=await fetch(`/studio/api/job/${{encodeURIComponent(id)}}`,{{cache:'no-store'}});const j=await r.json();const p=Math.max(0,Math.min(100,Number(j.progress||0)));document.getElementById('bar').style.width=p+'%';document.getElementById('stage').innerHTML='<b>'+esc(j.stage_label||j.stage||j.state)+'</b> · %'+p;document.getElementById('message').textContent=j.message||'';if(j.state==='FAILURE'){{document.getElementById('result').innerHTML='<div class="notice">Yükleme tamamlanamadı. Tekrar yükleme başlatılmadan önce sonuç güvenle doğrulanmalıdır.</div>';return}}if(j.state==='SUCCESS'){{const x=j.result||{{}};document.getElementById('result').innerHTML=`<div class="actions"><a class="btn success" target="_blank" rel="noopener noreferrer" href="${{esc(x.youtube_url)}}">▶ YouTube’da aç</a><a class="btn secondary" href="/studio/youtube">Yayın merkezine dön</a></div>`;return}}setTimeout(poll,3000)}}catch(e){{document.getElementById('message').textContent='Durum geçici olarak alınamadı.';setTimeout(poll,5000)}}}}
poll();</script>'''
    return _shell(body, title='YouTube yükleme', script=script)
