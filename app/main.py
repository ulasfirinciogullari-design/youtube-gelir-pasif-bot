from html import escape
from typing import Literal
from urllib.parse import quote
from fastapi import FastAPI, HTTPException, Query, Header, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field, StrictBool
from celery.result import AsyncResult

from app.config import settings
from app.celery_app import celery
from app.tasks import (
    UnsupportedLanguageError,
    normalize_pipeline_language,
    run_video_pipeline,
)
from app.services.storage import presigned_download_url
from app.services.voice import (
    list_turkish_voice_candidates,
    save_selected_voice,
    get_selected_voice,
)
from app.studio import router as studio_router, _production_publish_options, studio_auth_exception
from app.external_routes import router as external_router
from app.editorial_routes import router as editorial_router
from app.episode_delivery_routes import router as episode_delivery_router
from app.publication_hold_routes import router as publication_hold_router
from app.held_render_cancellation_routes import router as held_render_cancellation_router
from app.deleted_episode_replacement_routes import router as deleted_episode_replacement_router
from app.provider_key_routes import router as provider_key_router
from app.studio_access_routes import router as studio_access_router
from app.content_plan_routes import router as content_plan_router
from app.animation_routes import router as animation_router

app = FastAPI(title='YouTube 7/24 Content Factory', version='2.0.0')
app.include_router(studio_router)
app.include_router(external_router)
app.include_router(editorial_router)
app.include_router(episode_delivery_router)
app.include_router(publication_hold_router)
app.include_router(held_render_cancellation_router)
app.include_router(deleted_episode_replacement_router)
app.include_router(provider_key_router)
app.include_router(studio_access_router)
app.include_router(content_plan_router)
app.include_router(animation_router)
app.add_exception_handler(HTTPException, studio_auth_exception)


class JobCreate(BaseModel):
    topic: str = Field(min_length=2, max_length=500)
    duration_minutes: float = Field(default=5, ge=0.5, le=30)
    language: str = Field(default='tr', min_length=2, max_length=10)
    channel_id: str | None = None
    mode: Literal['preview', 'production'] | None = None
    format: Literal['shorts', 'landscape'] = 'landscape'
    publish_after_render: StrictBool = False
    production_channel_id: str | None = Field(default=None, max_length=128)


def _require_factory_token(x_factory_token: str | None):
    if not settings.factory_api_token:
        raise HTTPException(status_code=503, detail='FACTORY_API_TOKEN is not configured')
    if x_factory_token != settings.factory_api_token:
        raise HTTPException(status_code=401, detail='Invalid factory token')


def _service_statuses() -> list[tuple[str, bool]]:
    return [
        ('OpenAI', bool(settings.openai_api_key)),
        ('ElevenLabs', bool(settings.elevenlabs_api_key)),
        ('Pexels', bool(settings.pexels_api_key)),
        ('Runway', bool(settings.runwayml_api_secret)),
        ('Fal video', bool(getattr(settings, 'fal_key', ''))),
        ('Storage', bool(settings.bucket and settings.endpoint)),
        ('Redis', bool(settings.redis_url)),
    ]


def _nav(active: str = 'factory') -> str:
    factory_class = 'nav active' if active == 'factory' else 'nav'
    voice_class = 'nav active' if active == 'voices' else 'nav'
    return f'''
    <div class="topbar">
      <a class="brand" href="/studio">🎬 <span>Video Fabrikası</span></a>
      <div class="navs">
        <a class="nav" href="/studio">🏆 Studio V2</a>
        <a class="{factory_class}" href="/factory">Eski panel</a>
        <a class="{voice_class}" href="/voice-audition">🎙️ Sesler</a>
      </div>
    </div>'''


def _factory_shell(body: str, *, active: str = 'factory', title: str = 'Video Fabrikası') -> HTMLResponse:
    return HTMLResponse(f'''<!doctype html><html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0b0d12"><title>{escape(title)}</title>
<style>
*{{box-sizing:border-box}}html{{background:#0b0d12}}body{{font-family:Inter,ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif;margin:0;background:linear-gradient(180deg,#0b0d12,#111621 55%,#0b0d12);color:#f5f7fb;min-height:100vh}}
a{{color:inherit;text-decoration:none}}main{{max-width:820px;margin:auto;padding:14px 14px 96px}}
.topbar{{position:sticky;top:0;z-index:20;display:flex;gap:10px;align-items:center;justify-content:space-between;background:rgba(11,13,18,.92);backdrop-filter:blur(12px);padding:10px 2px 12px;border-bottom:1px solid #262d3a;margin-bottom:16px}}
.brand{{font-weight:900;display:flex;gap:8px;align-items:center;white-space:nowrap}}.brand span{{display:inline}}
.navs{{display:flex;gap:7px;overflow:auto}}.nav{{font-size:13px;font-weight:800;padding:9px 11px;border:1px solid #303949;border-radius:999px;background:#151a24;white-space:nowrap}}.nav.active{{background:#725cff;border-color:#725cff}}
.hero{{padding:7px 2px 14px}}h1{{font-size:30px;line-height:1.08;margin:0 0 8px}}h2{{font-size:20px;margin:0 0 10px}}h3{{font-size:15px;margin:0 0 7px}}p{{line-height:1.5}}.muted{{color:#9ca8ba}}.tiny{{font-size:12px;color:#8d98a9}}
.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}}.card{{background:rgba(24,28,37,.96);border:1px solid #2b3442;border-radius:17px;padding:16px;margin-bottom:12px;box-shadow:0 10px 30px rgba(0,0,0,.12)}}
.status-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}}.status{{background:#121722;border:1px solid #293241;border-radius:12px;padding:10px;font-size:12px;font-weight:800}}.dot{{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px}}.ok{{background:#55d68b}}.bad{{background:#ff6b75}}
label{{display:block;margin:14px 0 6px;font-size:13px;font-weight:850}}input,textarea,select,button{{width:100%;border-radius:12px;border:1px solid #3b4352;padding:12px;font-size:16px}}input,textarea,select{{background:#10141c;color:white}}button{{margin-top:16px;background:#725cff;color:white;border:0;font-weight:800}}
input,textarea,select{{background:#10141c;color:white}}button{{margin-top:16px;background:#725cff;color:white;border:0;font-weight:800}}pre{{white-space:pre-wrap;word-break:break-word}}a{{color:#9dc1ff}}
</style></head><body><main><h1>🎬 Video Fabrikası</h1>{body}</main></body></html>''')


@app.get('/')
def root():
    return RedirectResponse('/studio', status_code=302)


@app.get('/health')
def health():
    return {
        'ok': True,
        'version': '2.0.0',
        'selected_voice': get_selected_voice(),
        'factory_locked': not bool(settings.factory_api_token),
        'bucket_configured': bool(settings.bucket and settings.endpoint),
        'studio_url': '/studio',
    }


@app.post('/jobs')
def create_job(payload: JobCreate, x_factory_token: str | None = Header(default=None)):
    _require_factory_token(x_factory_token)
    try:
        language = normalize_pipeline_language(payload.language)
    except UnsupportedLanguageError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    mode = payload.mode or ('preview' if payload.duration_minutes <= 1 else 'production')
    options = {
        'mode': mode,
        'format': payload.format,
        **_production_publish_options(
            mode, payload.publish_after_render,
            payload.production_channel_id or '', language,
        ),
    }
    task = run_video_pipeline.delay(
        payload.topic,
        payload.duration_minutes,
        language,
        payload.channel_id,
        options,
    )
    return {'task_id': task.id, 'status': 'queued'}


@app.get('/jobs/{task_id}')
def job_status(task_id: str, x_factory_token: str | None = Header(default=None)):
    _require_factory_token(x_factory_token)
    task = AsyncResult(task_id, app=celery)
    state = task.state
    if state == 'FAILURE':
        return {'task_id': task_id, 'state': state, 'error': str(task.result)}
    if state == 'SUCCESS':
        result = task.result if isinstance(task.result, dict) else {'result': str(task.result)}
        if result.get('video_key'):
            try:
                result['download_url'] = presigned_download_url(result['video_key'], 86400)
            except Exception:
                pass
        return {'task_id': task_id, 'state': state, **result}
    info = task.info if isinstance(task.info, dict) else {}
    return {'task_id': task_id, 'state': state, **info}


@app.get('/factory', response_class=HTMLResponse)
def factory_panel():
    selected_voice = escape(get_selected_voice().get('name') or 'seçilmedi')
    status_html = ''.join(
        f'<div class="status"><span class="dot {"ok" if ok else "bad"}"></span>{escape(name)}</div>'
        for name, ok in _service_statuses()
    )
    return _factory_shell(f'''
<div class="card"><b>Bu panel eski sürümdür.</b><p>Yeni masaüstü kontrol merkezi için <a href="/studio">Studio V2'yi aç</a>.</p></div>
<div class="card"><b>Seçili ses:</b> {selected_voice}</div>
<div class="card"><form action="/factory/start" method="post">
<label>FACTORY_API_TOKEN</label><input name="token" type="password" required autocomplete="off" placeholder="Railway'e koyduğun token">
<label>Konu</label><textarea name="topic" rows="4" required>Telefonunda her gün kullandığın 7 teknolojinin şaşırtıcı gerçeği</textarea>
<label>Süre</label><select name="duration_minutes"><option value="0.5">30 sn test</option><option value="1" selected>1 dk test</option><option value="3">3 dk</option><option value="5">5 dk</option></select>
<label>Dil</label><select name="language"><option value="tr" selected>Türkçe</option><option value="en">English</option></select>
<button type="submit">Videoyu üret</button>
</form></div>''')


@app.post('/factory/start', response_class=HTMLResponse)
def factory_start(
    token: str = Form(...),
    topic: str = Form(...),
    duration_minutes: float = Form(1.0),
    language: str = Form('tr'),
):
    _require_factory_token(token)
    if not (0.5 <= duration_minutes <= 30):
        raise HTTPException(status_code=400, detail='Invalid duration')
    try:
        language = normalize_pipeline_language(language)
    except UnsupportedLanguageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    task = run_video_pipeline.delay(topic, duration_minutes, language, None)
    return _factory_shell(f'''
<div class="card"><h2>Görev başladı ✅</h2><p><b>Görev ID:</b> {escape(task.id)}</p>
<p>Yeni Studio sistemiyle üretim devam ediyor.</p>
<form action="/factory/status" method="post">
<input type="hidden" name="token" value="{escape(token, quote=True)}">
<input type="hidden" name="task_id" value="{escape(task.id, quote=True)}">
<button type="submit">Durumu kontrol et</button>
</form></div><a href="/studio">Studio V2'ye geç</a>''')


@app.post('/factory/status', response_class=HTMLResponse)
def factory_status(token: str = Form(...), task_id: str = Form(...)):
    _require_factory_token(token)
    task = AsyncResult(task_id, app=celery)
    state = task.state
    info = task.info if isinstance(task.info, dict) else {}
    if state == 'FAILURE':
        detail = escape(str(task.result))
        return _factory_shell(f'''<div class="card"><h2>Üretim başarısız ❌</h2><pre>{detail}</pre><a href="/studio">Studio V2</a></div>''')
    if state == 'SUCCESS':
        result = task.result if isinstance(task.result, dict) else {'result': str(task.result)}
        url = result.get('download_url')
        if not url and result.get('video_key'):
            try:
                url = presigned_download_url(result['video_key'], 86400)
            except Exception:
                url = None
        link = f'<p><a href="{escape(url, quote=True)}" target="_blank"><b>Videoyu aç / indir</b></a></p>' if url else ''
        return _factory_shell(f'''<div class="card"><h2>Video hazır ✅</h2>{link}<pre>{escape(str(result))}</pre><a href="/studio">Studio V2'ye dön</a></div>''')
    stage = escape(str(info.get('stage') or state))
    progress = escape(str(info.get('progress') or ''))
    message = escape(str(info.get('message') or ''))
    return _factory_shell(f'''<div class="card"><h2>Üretim devam ediyor…</h2><p><b>Aşama:</b> {stage}</p><p><b>İlerleme:</b> {progress}%</p><p>{message}</p>
<form action="/factory/status" method="post"><input type="hidden" name="token" value="{escape(token, quote=True)}"><input type="hidden" name="task_id" value="{escape(task_id, quote=True)}"><button type="submit">Tekrar kontrol et</button></form></div><a href="/studio">Studio V2</a>''')


@app.get('/voice-audition/candidates')
def voice_candidates(limit: int = Query(default=12, ge=1, le=50)):
    try:
        return {'voices': list_turkish_voice_candidates(limit)}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f'ElevenLabs voice lookup failed: {exc}') from exc


@app.get('/voice-audition/select/{public_owner_id}/{voice_id}')
def select_voice(public_owner_id: str, voice_id: str, name: str = Query(default='Selected voice', max_length=100)):
    try:
        save_selected_voice(public_owner_id, voice_id, name)
        return RedirectResponse('/voice-audition?selected=1', status_code=303)
    except Exception as exc:
        raise HTTPException(status_code=502, detail='Voice selection failed: ' + str(exc)) from exc


@app.get('/voice-audition', response_class=HTMLResponse)
def voice_audition_page(selected: int = Query(default=0)):
    try:
        voices = list_turkish_voice_candidates(24)
    except Exception as exc:
        return _factory_shell('<h2>Ses listesi yüklenemedi</h2>' + f'<pre>{escape(str(exc))}</pre><a href="/studio">Studio V2</a>')
    selected_voice = get_selected_voice()
    selected_id = selected_voice.get('voice_id')
    selected_name = escape(selected_voice.get('name') or '')
    cards = []
    for idx, voice in enumerate(voices, start=1):
        name_raw = voice.get('name') or 'Unnamed voice'
        name = escape(name_raw)
        desc = escape(voice.get('description') or '')
        gender = escape(str(voice.get('gender') or ''))
        age = escape(str(voice.get('age') or ''))
        use_case = escape(str(voice.get('use_case') or ''))
        voice_id_raw = voice.get('voice_id') or ''
        owner_id_raw = voice.get('public_owner_id') or ''
        preview = escape(voice.get('preview_url') or '')
        select_url = f'/voice-audition/select/{quote(owner_id_raw)}/{quote(voice_id_raw)}?name={quote(name_raw)}'
        chosen = voice_id_raw == selected_id
        cards.append(f'''<article class="card"><div>#{idx}</div><h2>{name}</h2><div>{gender} · {age} · {use_case}</div><p>{desc}</p><audio style="width:100%" controls preload="none" src="{preview}"></audio><a href="{select_url}">{'✓ Seçili ses' if chosen else 'Bu sesi seç'}</a></article>''')
    success = f'<div class="card"><b>✓ {selected_name}</b> kaydedildi.</div>' if selected and selected_name else ''
    return _factory_shell(f'''{success}<p><a href="/studio">← Studio V2'ye dön</a></p>{''.join(cards)}''', title='Sesler')
