from __future__ import annotations

from html import escape
import json

from fastapi import APIRouter, Cookie, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import settings
from app.publish_tasks import publish_video_pipeline
from app.services.studio_state import create_job, get_job, list_jobs
from app.services.youtube_auth import (
    build_authorization_url,
    complete_authorization,
    connection_status,
    disconnect,
)

router = APIRouter()
COOKIE_NAME = 'youtube_studio_token'

CSS = r'''
*{box-sizing:border-box}html{background:#080b11}body{margin:0;color:#eef2f8;font-family:Inter,system-ui,sans-serif;background:radial-gradient(circle at 10% 0,#263668 0,transparent 34%),#080b11;min-height:100vh}.wrap{max-width:1050px;margin:auto;padding:20px 20px 80px}a{color:inherit;text-decoration:none}.top{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:14px 0;border-bottom:1px solid #293143}.brand{font-weight:950}.nav{display:flex;gap:8px;flex-wrap:wrap}.nav a{padding:9px 12px;border:1px solid #344056;border-radius:999px;background:#111824;font-size:13px;font-weight:800}.hero{padding:34px 0 20px}.hero h1{font-size:40px;margin:0 0 8px;letter-spacing:-.04em}.muted{color:#9ba7b8}.card{background:rgba(19,24,35,.95);border:1px solid #2c3547;border-radius:20px;padding:18px;margin-bottom:14px}.btn,button{display:inline-flex;align-items:center;justify-content:center;border:0;border-radius:13px;padding:12px 15px;background:#ff0033;color:white;font:inherit;font-weight:900;cursor:pointer}.btn.secondary{background:#172030;border:1px solid #38445a}.btn.success{background:#198958}.btn.danger{background:#8e3540}.actions{display:flex;gap:9px;flex-wrap:wrap;margin-top:12px}.badge{display:inline-flex;padding:7px 10px;border-radius:999px;border:1px solid #354055;background:#101722;font-size:12px;font-weight:850;margin:3px}.job{display:grid;grid-template-columns:1fr auto;gap:12px;align-items:center;padding:14px;border:1px solid #303a4d;border-radius:14px;background:#0d131d;margin-bottom:9px}.job-title{font-weight:900}.tiny{font-size:12px;color:#8996a8}select{border:1px solid #3b465a;border-radius:12px;background:#0b111b;color:#fff;padding:11px;font:inherit}form.inline{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.notice{border:1px solid #6e5f22;background:#2a2411;color:#f4df89;border-radius:14px;padding:13px}.progress{height:13px;border:1px solid #344054;background:#090e16;border-radius:999px;overflow:hidden}.bar{height:100%;width:0;background:linear-gradient(90deg,#ff0033,#ff8b33);transition:width .35s ease}pre{white-space:pre-wrap;word-break:break-word;background:#090e16;border:1px solid #2d3748;border-radius:14px;padding:14px}@media(max-width:650px){.wrap{padding:10px 10px 70px}.hero h1{font-size:31px}.top{align-items:flex-start}.job{grid-template-columns:1fr}.actions .btn,form.inline button{width:100%}}
'''


def _valid_token(value: str | None) -> bool:
    return bool(settings.factory_api_token and value and value == settings.factory_api_token)


def _require_auth(value: str | None) -> None:
    if not _valid_token(value):
        raise HTTPException(status_code=401, detail='Studio oturumu gerekli')


def _shell(body: str, title: str = 'YouTube bağlantısı', script: str = '') -> HTMLResponse:
    return HTMLResponse(
        '<!doctype html><html lang="tr"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{escape(title)}</title><style>{CSS}</style></head><body><div class="wrap">'
        '<div class="top"><a class="brand" href="/studio">🎬 YouTube Studio V2</a><nav class="nav"><a href="/studio">Yeni üretim</a><a href="/studio/history">Geçmiş</a><a href="/studio/youtube">YouTube</a></nav></div>'
        f'{body}</div>{script}</body></html>'
    )


def _completed_jobs() -> list[dict]:
    jobs = []
    for job in list_jobs(80):
        result = job.get('result') or {}
        if job.get('state') == 'SUCCESS' and job.get('kind') == 'render' and isinstance(result, dict) and result.get('video_key'):
            jobs.append(job)
    return jobs


@router.get('/studio/youtube', response_class=HTMLResponse)
def youtube_home(
    request: Request,
    connected: int = 0,
    studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
):
    _require_auth(studio_token)
    status = connection_status()
    channel = status.get('channel') or {}
    redirect_uri = settings.google_redirect_uri or str(request.url_for('youtube_oauth_callback'))

    if status.get('connected'):
        channel_title = escape(str(channel.get('title') or 'YouTube kanalı'))
        account_card = f'''
<div class="card"><h2>Bağlı kanal ✅</h2><p><b>{channel_title}</b></p><div><span class="badge">{escape(str(channel.get('subscriber_count') or '—'))} abone</span><span class="badge">{escape(str(channel.get('video_count') or '—'))} video</span><span class="badge">{escape(str(channel.get('view_count') or '—'))} görüntülenme</span></div><div class="actions"><form method="post" action="/studio/youtube/disconnect"><button class="btn danger" type="submit">Bağlantıyı kaldır</button></form></div></div>'''
    elif status.get('configured'):
        account_card = '<div class="card"><h2>YouTube hesabı bağlı değil</h2><p class="muted">Bir kez Google izin ekranını onayladıktan sonra Studio videoları önce gizli olarak kanalına yükleyebilir.</p><a class="btn" href="/studio/youtube/connect">Google ile YouTube’u bağla</a></div>'
    else:
        account_card = f'''
<div class="notice"><b>Google OAuth değişkenleri eksik.</b><p>Railway ana servisine <code>GOOGLE_CLIENT_ID</code>, <code>GOOGLE_CLIENT_SECRET</code> ve <code>GOOGLE_REDIRECT_URI</code> eklenmeli.</p><p>Yetkili yönlendirme adresi:</p><pre>{escape(redirect_uri)}</pre></div>'''

    rows = []
    for job in _completed_jobs():
        result = job.get('result') or {}
        spec = job.get('spec') or {}
        topic = escape(str(spec.get('topic') or result.get('title') or 'Video'))
        duration = escape(str(round(float(result.get('duration') or 0), 1)))
        youtube = result.get('youtube') or {}
        if youtube.get('url'):
            action = f'<a class="btn success" target="_blank" href="{escape(str(youtube.get("url")), quote=True)}">YouTube’da aç</a>'
        elif status.get('connected'):
            action = f'''<form class="inline" method="post" action="/studio/youtube/publish/{escape(str(job.get('task_id') or ''))}"><select name="privacy_status"><option value="private" selected>Gizli</option><option value="unlisted">Liste dışı</option><option value="public">Herkese açık</option></select><button type="submit">YouTube’a yükle</button></form>'''
        else:
            action = '<span class="tiny">Önce hesabı bağla</span>'
        rows.append(f'<div class="job"><div><div class="job-title">{topic}</div><div class="tiny">{duration} sn · {escape(str(job.get("task_id") or ""))}</div></div><div>{action}</div></div>')
    jobs_html = ''.join(rows) or '<div class="card muted">Yüklenebilir tamamlanmış video henüz yok.</div>'
    success = '<div class="notice" style="border-color:#276744;background:#112d21;color:#8be5b4">YouTube hesabı başarıyla bağlandı.</div>' if connected else ''

    body = f'''
<div class="hero"><h1>YouTube yayın merkezi</h1><div class="muted">Final videolar önce gizli yüklenir; altyazı ayrı dil parçası olarak eklenir.</div></div>{success}{account_card}<div class="card"><h2>Hazır videolar</h2><p class="muted">Yükleme sırasında başlık, açıklama, kaynaklar ve varsa SRT otomatik kullanılır.</p>{jobs_html}</div>'''
    return _shell(body)


@router.get('/studio/youtube/connect')
def youtube_connect(
    request: Request,
    studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
):
    _require_auth(studio_token)
    redirect_uri = settings.google_redirect_uri or str(request.url_for('youtube_oauth_callback'))
    try:
        return RedirectResponse(build_authorization_url(redirect_uri), status_code=302)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get('/studio/youtube/callback', name='youtube_oauth_callback')
def youtube_oauth_callback(request: Request, state: str):
    try:
        complete_authorization(str(request.url), state)
        return RedirectResponse('/studio/youtube?connected=1', status_code=303)
    except Exception as exc:
        return _shell(f'<div class="hero"><h1>Bağlantı kurulamadı</h1></div><div class="notice"><pre>{escape(str(exc))}</pre></div><div class="actions"><a class="btn secondary" href="/studio/youtube">Geri dön</a></div>')


@router.post('/studio/youtube/disconnect')
def youtube_disconnect(studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    _require_auth(studio_token)
    disconnect()
    return RedirectResponse('/studio/youtube', status_code=303)


@router.post('/studio/youtube/publish/{source_task_id}')
def youtube_publish(
    source_task_id: str,
    privacy_status: str = Form('private'),
    studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
):
    _require_auth(studio_token)
    if not connection_status().get('connected'):
        raise HTTPException(status_code=409, detail='YouTube hesabı bağlı değil')
    source = get_job(source_task_id)
    if not source or source.get('state') != 'SUCCESS':
        raise HTTPException(status_code=404, detail='Yayınlanabilir tamamlanmış video bulunamadı')
    privacy_status = privacy_status if privacy_status in {'private', 'unlisted', 'public'} else 'private'
    task = publish_video_pipeline.delay(source_task_id, privacy_status)
    source_spec = source.get('spec') or {}
    create_job(task.id, {
        'topic': source_spec.get('topic') or (source.get('result') or {}).get('title') or 'YouTube upload',
        'source_task_id': source_task_id,
        'privacy_status': privacy_status,
        'mode': 'publish',
    }, kind='publish', parent_id=source_task_id)
    return RedirectResponse(f'/studio/youtube/publish-status/{task.id}', status_code=303)


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
async function poll(){{try{{const r=await fetch(`/studio/api/job/${{encodeURIComponent(id)}}`,{{cache:'no-store'}});const j=await r.json();const p=Math.max(0,Math.min(100,Number(j.progress||0)));document.getElementById('bar').style.width=p+'%';document.getElementById('stage').innerHTML='<b>'+esc(j.stage_label||j.stage||j.state)+'</b> · %'+p;document.getElementById('message').textContent=j.message||'';if(j.state==='FAILURE'){{document.getElementById('result').innerHTML='<pre>'+esc(j.error||'Bilinmeyen hata')+'</pre>';return}}if(j.state==='SUCCESS'){{const x=j.result||{{}};document.getElementById('result').innerHTML=`<div class="actions"><a class="btn success" target="_blank" href="${{esc(x.youtube_url)}}">▶ YouTube’da aç</a><a class="btn secondary" href="/studio/youtube">Yayın merkezine dön</a></div>`;return}}setTimeout(poll,3000)}}catch(e){{document.getElementById('message').textContent=e.message;setTimeout(poll,5000)}}}}
poll();</script>'''
    return _shell(body, title='YouTube yükleme', script=script)
