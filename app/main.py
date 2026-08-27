from html import escape
from urllib.parse import quote
from fastapi import FastAPI, HTTPException, Query, Header
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

app = FastAPI(title='YouTube 7/24 Content Factory', version='0.8.0')


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


@app.get('/health')
def health():
    return {
        'ok': True,
        'version': '0.8.0',
        'selected_voice': get_selected_voice(),
        'factory_locked': not bool(settings.factory_api_token),
        'bucket_configured': bool(settings.bucket and settings.endpoint),
    }


@app.post('/jobs')
def create_job(payload: JobCreate, x_factory_token: str | None = Header(default=None)):
    _require_factory_token(x_factory_token)
    task = run_video_pipeline.delay(
        payload.topic,
        payload.duration_minutes,
        payload.language,
        payload.channel_id,
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
    selected_voice = get_selected_voice().get('name') or 'seçilmedi'
    return HTMLResponse(f'''<!doctype html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>YouTube Video Fabrikası</title>
<style>
body{{font-family:system-ui;margin:0;background:#0f1116;color:#f5f7fb}}main{{max-width:720px;margin:auto;padding:20px}}
.card{{background:#181c25;border:1px solid #2b3240;border-radius:16px;padding:18px;margin-bottom:14px}}
label{{display:block;margin:12px 0 5px;font-weight:700}}input,textarea,select,button{{width:100%;box-sizing:border-box;border-radius:10px;border:1px solid #3b4352;padding:12px;font-size:16px}}
input,textarea,select{{background:#10141c;color:white}}button{{margin-top:16px;background:#725cff;color:white;border:0;font-weight:800}}
button:disabled{{opacity:.55}}#status{{white-space:pre-wrap;word-break:break-word;line-height:1.45}}a{{color:#9dc1ff}}
.small{{opacity:.7;font-size:.9rem}}
</style></head><body><main>
<h1>🎬 Video Fabrikası</h1>
<div class="card"><b>Seçili ses:</b> {escape(selected_voice)}<br><span class="small">Token tarayıcıdan sadece X-Factory-Token header'ı olarak gönderilir; URL'ye yazılmaz.</span></div>
<div class="card">
<label>FACTORY_API_TOKEN</label><input id="token" type="password" autocomplete="off" placeholder="Railway'e koyduğun token">
<label>Konu</label><textarea id="topic" rows="4">Telefonunda her gün kullandığın 7 teknolojinin şaşırtıcı gerçeği</textarea>
<label>Süre</label><select id="duration"><option value="1">1 dk test</option><option value="3">3 dk</option><option value="5" selected>5 dk</option></select>
<label>Dil</label><select id="lang"><option value="tr" selected>Türkçe</option><option value="en">English</option></select>
<button id="start" onclick="startJob()">Videoyu üret</button>
</div>
<div class="card"><h3>Durum</h3><div id="status">Hazır.</div></div>
<script>
const s=document.getElementById('status');
async function api(url,options={{}}){{
  options.headers=Object.assign({{'X-Factory-Token':document.getElementById('token').value}},options.headers||{{}});
  const r=await fetch(url,options); const text=await r.text(); let data; try{{data=JSON.parse(text)}}catch{{data={{raw:text}}}}
  if(!r.ok) throw new Error(data.detail||data.error||text||('HTTP '+r.status)); return data;
}}
async function startJob(){{
 const b=document.getElementById('start'); b.disabled=true; s.textContent='Görev oluşturuluyor…';
 try{{
   const body={{topic:document.getElementById('topic').value,duration_minutes:Number(document.getElementById('duration').value),language:document.getElementById('lang').value}};
   const d=await api('/jobs',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(body)}});
   s.textContent='Görev: '+d.task_id+'\nKuyrukta…'; poll(d.task_id);
 }}catch(e){{s.textContent='HATA: '+e.message;b.disabled=false;}}
}}
async function poll(id){{
 try{{
   const d=await api('/jobs/'+id); s.textContent=JSON.stringify(d,null,2);
   if(d.state==='SUCCESS'){{
      document.getElementById('start').disabled=false;
      if(d.download_url) s.innerHTML='<b>Video hazır ✅</b><br><a href="'+d.download_url+'" target="_blank">Videoyu aç / indir</a><pre>'+JSON.stringify(d,null,2)+'</pre>';
      return;
   }}
   if(d.state==='FAILURE'){{document.getElementById('start').disabled=false;return;}}
   setTimeout(()=>poll(id),5000);
 }}catch(e){{s.textContent='Durum hatası: '+e.message;document.getElementById('start').disabled=false;}}
}}
</script></main></body></html>''')


@app.get('/voice-audition/candidates')
def voice_candidates(limit: int = Query(default=12, ge=1, le=50)):
    try:
        return {'voices': list_turkish_voice_candidates(limit)}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f'ElevenLabs voice lookup failed: {exc}') from exc


@app.get('/voice-audition/select/{public_owner_id}/{voice_id}')
def select_voice(
    public_owner_id: str,
    voice_id: str,
    name: str = Query(default='Selected voice', max_length=100),
):
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
        return HTMLResponse(
            '<html><body style="font-family:system-ui;padding:20px">'
            '<h2>Ses listesi yüklenemedi</h2>'
            f'<pre style="white-space:pre-wrap">{escape(str(exc))}</pre>'
            '</body></html>',
            status_code=502,
        )

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
        badge = '<div class="chosen">✓ SEÇİLİ</div>' if chosen else ''
        cards.append(f'''
        <article class="card {'is-chosen' if chosen else ''}">
          {badge}<div class="rank">#{idx}</div><h2>{name}</h2>
          <div class="meta">{gender} · {age} · {use_case}</div><p>{desc}</p>
          <audio controls preload="none" src="{preview}"></audio>
          <a class="select" href="{select_url}">{'Seçili ses' if chosen else 'Bu sesi seç'}</a>
        </article>''')

    success = (
        f'<div class="success">✓ <b>{selected_name}</b> varsayılan ses olarak kaydedildi.</div>'
        if selected and selected_name else ''
    )
    current = (
        f'<div class="current">Şu an seçili ses: <b>{selected_name}</b></div>'
        if selected_name else '<div class="current">Henüz varsayılan ses seçilmedi.</div>'
    )
    return HTMLResponse(f'''
    <!doctype html><html lang="tr"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Türkçe Ses Karşılaştırma</title><style>
    body{{font-family:system-ui;margin:0;background:#0f1116;color:#f5f7fb}} main{{max-width:900px;margin:auto;padding:18px}}
    .current,.success{{padding:14px;border-radius:12px;margin-bottom:14px}} .current{{background:#222835}} .success{{background:#15351f}}
    .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px}} .card{{position:relative;background:#171a22;border:1px solid #2c3240;padding:16px;border-radius:16px}}
    .is-chosen{{border:2px solid #78e08f}} .chosen{{position:absolute;right:12px;top:12px;background:#78e08f;color:#102016;padding:5px 8px;border-radius:8px;font-size:.75rem;font-weight:800}}
    .rank,.meta{{opacity:.65}} h2{{margin:.25rem 0}} audio{{width:100%;margin:8px 0}} .select{{display:block;text-decoration:none;background:#725cff;color:white;padding:11px;border-radius:10px;text-align:center;font-weight:700}}
    </style></head><body><main><h1>Türkçe Ses Karşılaştırma</h1>{success}{current}<div class="grid">{''.join(cards)}</div></main></body></html>
    ''')
