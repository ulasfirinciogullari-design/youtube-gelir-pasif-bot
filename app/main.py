from html import escape
from urllib.parse import quote
from fastapi import FastAPI, HTTPException, Query, Header, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from celery.result import AsyncResult

from app.config import settings
from app.celery_app import celery
from app.tasks import run_video_pipeline
from app.services.storage import presigned_download_url
from app.services.voice import (
    list_turkish_voice_candidates,
    save_selected_voice,
    get_selected_voice,
)

app = FastAPI(title='YouTube 7/24 Content Factory', version='1.0.0')


class JobCreate(BaseModel):
    topic: str = Field(min_length=2, max_length=500)
    duration_minutes: float = Field(default=5, ge=0.5, le=30)
    language: str = Field(default='tr', min_length=2, max_length=10)
    channel_id: str | None = None


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
        ('Storage', bool(settings.bucket and settings.endpoint)),
        ('Redis', bool(settings.redis_url)),
    ]


def _nav(active: str = 'factory') -> str:
    factory_class = 'nav active' if active == 'factory' else 'nav'
    voice_class = 'nav active' if active == 'voices' else 'nav'
    return f'''
    <div class="topbar">
      <a class="brand" href="/factory">🎬 <span>Video Fabrikası</span></a>
      <div class="navs">
        <a class="{factory_class}" href="/factory">＋ Yeni Video</a>
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
label{{display:block;margin:14px 0 6px;font-size:13px;font-weight:850}}input,textarea,select,button{{width:100%;border-radius:12px;border:1px solid #3b4557;padding:13px;font-size:16px}}input,textarea,select{{background:#0e131c;color:white;outline:none}}input:focus,textarea:focus,select:focus{{border-color:#725cff;box-shadow:0 0 0 3px rgba(114,92,255,.18)}}textarea{{resize:vertical;min-height:110px}}
.btn,button{{display:flex;align-items:center;justify-content:center;gap:8px;border:0;background:#725cff;color:white;font-weight:900;cursor:pointer;text-align:center}}button{{margin-top:16px}}.btn{{padding:12px 14px;border-radius:12px}}.btn.secondary{{background:#1a2130;border:1px solid #344054}}.btn.success{{background:#1f8b55}}.btn.danger{{background:#8f3139}}.actions{{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin-top:12px}}.actions.one{{grid-template-columns:1fr}}
.badge{{display:inline-flex;align-items:center;gap:6px;background:#151b26;border:1px solid #313b4d;border-radius:999px;padding:7px 10px;font-size:12px;font-weight:800;margin:3px 3px 3px 0}}.progress{{height:11px;background:#0e131c;border:1px solid #303949;border-radius:999px;overflow:hidden;margin:10px 0}}.bar{{height:100%;background:linear-gradient(90deg,#725cff,#9a8cff);border-radius:999px}}pre{{white-space:pre-wrap;word-break:break-word;background:#0e131c;border:1px solid #293241;border-radius:12px;padding:12px;max-height:260px;overflow:auto;font-size:12px}}.download{{font-size:17px;padding:15px}}
.stage-list{{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin-top:10px}}.stage{{font-size:11px;text-align:center;padding:8px 4px;border-radius:9px;background:#111722;border:1px solid #283142;color:#9da8b9}}.stage.current{{color:white;border-color:#725cff;background:#26203e}}
@media(max-width:560px){{main{{padding:10px 10px 88px}}.brand span{{display:none}}.topbar{{padding-top:8px}}h1{{font-size:26px}}.grid{{grid-template-columns:1fr}}.status-grid{{grid-template-columns:repeat(2,1fr)}}.actions{{grid-template-columns:1fr}}.stage-list{{grid-template-columns:repeat(2,1fr)}}}}
</style></head><body><main>{_nav(active)}{body}</main></body></html>''')


@app.get('/')
def root():
    return RedirectResponse('/factory', status_code=302)


@app.get('/health')
def health():
    return {
        'ok': True,
        'version': '1.0.0',
        'selected_voice': get_selected_voice(),
        'factory_locked': not bool(settings.factory_api_token),
        'bucket_configured': bool(settings.bucket and settings.endpoint),
    }


@app.post('/jobs')
def create_job(payload: JobCreate, x_factory_token: str | None = Header(default=None)):
    _require_factory_token(x_factory_token)
    task = run_video_pipeline.delay(payload.topic, payload.duration_minutes, payload.language, payload.channel_id)
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
<div class="hero"><h1>Yeni video üret</h1><div class="muted">Konu ver; araştırma, ses, sahne planı, B-roll, AI görüntü, kurgu ve depolama otomatik çalışsın.</div></div>
<div class="card">
  <h3>Sistem</h3><div class="status-grid">{status_html}</div>
  <div style="margin-top:10px"><span class="badge">🎙️ {selected_voice}</span><span class="badge">🎞️ 1080p</span><span class="badge">🧠 Sahne bazlı kurgu</span></div>
</div>
<div class="card"><form action="/factory/start" method="post">
<label>Güvenlik tokenı</label><input name="token" type="password" required autocomplete="off" placeholder="Railway'deki FACTORY_API_TOKEN">
<div class="tiny" style="margin-top:5px">Token yalnızca üretim isteğinde kullanılır; ekranda gösterilmez.</div>
<label>Video konusu / talimat</label><textarea name="topic" rows="5" required placeholder="Örn: Telefonlarda kullandığımız teknolojilerin şaşırtıcı gerçekleri; OLED, GPS ve QR kod mutlaka olsun.">Telefonunda her gün kullandığın 7 teknolojinin şaşırtıcı gerçeği</textarea>
<div class="grid">
<div><label>Süre</label><select name="duration_minutes">
<option value="0.5">30 sn hızlı test</option><option value="1" selected>1 dk kalite testi</option><option value="3">3 dk</option><option value="5">5 dk</option><option value="8">8 dk</option><option value="10">10 dk</option><option value="15">15 dk</option><option value="20">20 dk</option><option value="30">30 dk</option>
</select></div>
<div><label>Dil</label><select name="language"><option value="tr" selected>Türkçe</option><option value="en">English</option><option value="de">Deutsch</option><option value="es">Español</option><option value="ar">العربية</option></select></div>
</div>
<label>Kanal etiketi <span class="tiny">(opsiyonel)</span></label><input name="channel_id" type="text" maxlength="120" placeholder="Örn: teknoloji-tr-01">
<button type="submit">✨ Videoyu üret</button>
</form></div>
<div class="actions"><a class="btn secondary" href="/voice-audition">🎙️ Sesi değiştir</a><a class="btn secondary" href="/health" target="_blank">🩺 Sistem durumu</a></div>
''')


@app.post('/factory/start', response_class=HTMLResponse)
def factory_start(
    token: str = Form(...),
    topic: str = Form(...),
    duration_minutes: float = Form(1.0),
    language: str = Form('tr'),
    channel_id: str = Form(''),
):
    _require_factory_token(token)
    if not (0.5 <= duration_minutes <= 30):
        raise HTTPException(status_code=400, detail='Invalid duration')
    task = run_video_pipeline.delay(topic, duration_minutes, language, channel_id.strip() or None)
    return _factory_shell(f'''
<div class="hero"><h1>Görev başladı ✅</h1><div class="muted">Video arka planda üretiliyor.</div></div>
<div class="card">
  <h3>Görev</h3><p><b>ID:</b> <span class="tiny">{escape(task.id)}</span></p>
  <div class="progress"><div class="bar" style="width:2%"></div></div>
  <div class="stage-list"><div class="stage current">Araştırma</div><div class="stage">Ses</div><div class="stage">B-roll</div><div class="stage">AI sahne</div><div class="stage">Render</div><div class="stage">Upload</div></div>
  <form action="/factory/status" method="post"><input type="hidden" name="token" value="{escape(token, quote=True)}"><input type="hidden" name="task_id" value="{escape(task.id, quote=True)}"><button type="submit">🔄 Durumu kontrol et</button></form>
</div>
<div class="actions"><a class="btn secondary" href="/factory">← Üretim paneline dön</a><a class="btn secondary" href="/voice-audition">🎙️ Sesler</a></div>
''')


def _stage_name(stage: str) -> str:
    return {
        'PENDING': 'Kuyrukta', 'research': 'Araştırma ve senaryo', 'voice': 'Seslendirme',
        'broll': 'B-roll toplama', 'ai_scene': 'AI sahneleri', 'render': 'Kurgu ve render',
        'upload': 'Depolamaya yükleme', 'complete': 'Tamamlandı'
    }.get(stage, stage)


@app.post('/factory/status', response_class=HTMLResponse)
def factory_status(token: str = Form(...), task_id: str = Form(...)):
    _require_factory_token(token)
    task = AsyncResult(task_id, app=celery)
    state = task.state
    info = task.info if isinstance(task.info, dict) else {}
    if state == 'FAILURE':
        detail = escape(str(task.result))
        return _factory_shell(f'''
<div class="hero"><h1>Üretim başarısız ❌</h1><div class="muted">Hata ayrıntısı aşağıda. Yeni denemeye dönüp tekrar başlatabilirsin.</div></div>
<div class="card"><pre>{detail}</pre></div>
<div class="actions"><a class="btn secondary" href="/factory">← Üretim paneli</a><a class="btn danger" href="/factory">↻ Yeni deneme</a></div>''')
    if state == 'SUCCESS':
        result = task.result if isinstance(task.result, dict) else {'result': str(task.result)}
        url = result.get('download_url')
        if not url and result.get('video_key'):
            try:
                url = presigned_download_url(result['video_key'], 86400)
            except Exception:
                url = None
        title = escape(str(result.get('title') or 'Video'))
        duration = escape(str(round(float(result.get('duration') or 0), 1)))
        shots = escape(str(result.get('shots') or '-'))
        scenes = escape(str(result.get('scenes') or '-'))
        resolution = escape(str(result.get('resolution') or '1920x1080'))
        download = f'<a class="btn success download" href="{escape(url, quote=True)}" target="_blank">▶ Videoyu aç / indir</a>' if url else '<div class="muted">İndirme bağlantısı oluşturulamadı.</div>'
        return _factory_shell(f'''
<div class="hero"><h1>Video hazır ✅</h1><div class="muted">{title}</div></div>
<div class="card">{download}<div style="margin-top:13px"><span class="badge">⏱️ {duration} sn</span><span class="badge">🎬 {shots} shot</span><span class="badge">🧩 {scenes} sahne</span><span class="badge">🖥️ {resolution}</span></div></div>
<div class="actions"><a class="btn secondary" href="/factory">＋ Yeni video üret</a><a class="btn secondary" href="/voice-audition">🎙️ Sesi değiştir</a></div>
<details class="card"><summary class="tiny">Teknik ayrıntıları göster</summary><pre>{escape(str(result))}</pre></details>''')

    stage_raw = str(info.get('stage') or state)
    progress_num = info.get('progress')
    try:
        progress = max(0, min(100, int(progress_num))) if progress_num is not None else 0
    except Exception:
        progress = 0
    stage = escape(_stage_name(stage_raw))
    names = [('research','Araştırma'),('voice','Ses'),('broll','B-roll'),('ai_scene','AI sahne'),('render','Render'),('upload','Upload')]
    stage_html = ''.join(f'<div class="stage {"current" if key == stage_raw else ""}">{label}</div>' for key, label in names)
    return _factory_shell(f'''
<div class="hero"><h1>Üretim devam ediyor…</h1><div class="muted">Sayfadan çıksan bile görev worker'da devam eder.</div></div>
<div class="card"><h2>{stage}</h2><div class="progress"><div class="bar" style="width:{progress}%"></div></div><div><b>%{progress}</b></div><div class="stage-list">{stage_html}</div>
<form action="/factory/status" method="post"><input type="hidden" name="token" value="{escape(token, quote=True)}"><input type="hidden" name="task_id" value="{escape(task_id, quote=True)}"><button type="submit">🔄 Tekrar kontrol et</button></form></div>
<div class="actions"><a class="btn secondary" href="/factory">← Üretim paneline dön</a><a class="btn secondary" href="/voice-audition">🎙️ Sesler</a></div>''')


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
        return _factory_shell(f'<div class="hero"><h1>Ses listesi yüklenemedi</h1></div><div class="card"><pre>{escape(str(exc))}</pre></div><a class="btn secondary" href="/factory">← Üretim paneli</a>', active='voices', title='Sesler')
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
        badge = '<span class="badge">✓ SEÇİLİ</span>' if chosen else ''
        cards.append(f'''<article class="card" style="border-color:{'#55d68b' if chosen else '#2b3442'}"><div class="tiny">#{idx}</div><h2>{name}</h2>{badge}<div class="muted">{gender} · {age} · {use_case}</div><p>{desc}</p><audio style="width:100%;margin:8px 0 12px" controls preload="none" src="{preview}"></audio><a class="btn {'success' if chosen else ''}" href="{select_url}">{'Seçili ses' if chosen else 'Bu sesi seç'}</a></article>''')
    success = f'<div class="card"><b>✓ {selected_name}</b> varsayılan ses olarak kaydedildi.</div>' if selected and selected_name else ''
    current = f'<span class="badge">🎙️ Şu an: {selected_name}</span>' if selected_name else '<span class="badge">Henüz ses seçilmedi</span>'
    return _factory_shell(f'''<div class="hero"><h1>Türkçe sesler</h1><div class="muted">Önizlemeyi dinle ve video fabrikasının varsayılan anlatıcısını seç.</div></div>{success}<div style="margin-bottom:12px">{current}</div>{''.join(cards)}<a class="btn secondary" href="/factory">← Üretim paneline dön</a>''', active='voices', title='Sesler')
