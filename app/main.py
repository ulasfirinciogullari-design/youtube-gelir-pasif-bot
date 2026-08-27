from html import escape
from urllib.parse import quote
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, Response, RedirectResponse
from pydantic import BaseModel, Field
from app.tasks import run_video_pipeline
from app.services.voice import (
    list_turkish_voice_candidates,
    audition_shared_voice,
    save_selected_voice,
    get_selected_voice,
)

app = FastAPI(title='YouTube 7/24 Content Factory', version='0.4.0')

AUDITION_TEXT = (
    'Bazen her gün kullandığımız teknolojilerin arkasında, fark etmediğimiz kadar şaşırtıcı bir dünya vardır. '
    'Bugün, telefonunuzdan internete kadar günlük hayatın içinde saklanan ilginç ayrıntılara birlikte bakacağız.'
)

class JobCreate(BaseModel):
    topic: str = Field(min_length=2)
    duration_minutes: float = 5
    language: str = 'tr'
    channel_id: str | None = None

@app.get('/health')
def health():
    return {'ok': True, 'version': '0.4.0', 'selected_voice': get_selected_voice()}

@app.post('/jobs')
def create_job(payload: JobCreate):
    task = run_video_pipeline.delay(
        payload.topic,
        payload.duration_minutes,
        payload.language,
        payload.channel_id,
    )
    return {'task_id': task.id, 'status': 'queued'}

@app.get('/voice-audition/candidates')
def voice_candidates(limit: int = Query(default=12, ge=1, le=50)):
    try:
        return {'voices': list_turkish_voice_candidates(limit)}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f'ElevenLabs voice lookup failed: {exc}') from exc

@app.get('/voice-audition/sample/{public_owner_id}/{voice_id}')
def voice_sample(
    public_owner_id: str,
    voice_id: str,
    name: str = Query(default='Audition voice', max_length=100),
    text: str = Query(default=AUDITION_TEXT, min_length=10, max_length=500),
):
    try:
        audio = audition_shared_voice(text, public_owner_id, voice_id, name)
        return Response(content=audio, media_type='audio/mpeg')
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                'Voice audition failed. The ElevenLabs API key must have Text to Speech Access and Voices Write. '
                'Error: ' + str(exc)
            ),
        ) from exc

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
            '<p>ElevenLabs API anahtarının <b>Voices Write</b> yetkisini kontrol et.</p>'
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
        voice_id = escape(voice_id_raw)
        owner_id = escape(owner_id_raw)
        preview = escape(voice.get('preview_url') or '')
        sample_url = f'/voice-audition/sample/{owner_id}/{voice_id}?name={quote(name_raw)}'
        select_url = f'/voice-audition/select/{owner_id}/{voice_id}?name={quote(name_raw)}'
        chosen = voice_id_raw == selected_id
        chosen_badge = '<div class="chosen">✓ SEÇİLİ</div>' if chosen else ''
        select_label = 'Seçili ses' if chosen else 'Bu sesi seç'
        cards.append(f'''
        <article class="card {'is-chosen' if chosen else ''}">
          <div class="rank">#{idx}</div>
          {chosen_badge}
          <h2>{name}</h2>
          <div class="meta">{gender} · {age} · {use_case}</div>
          <p>{desc}</p>
          <div class="label">Hazır Türkçe önizleme</div>
          <audio controls preload="none" src="{preview}"></audio>
          <a class="same" href="{sample_url}" target="_blank">Aynı paragrafı bu sesle dinle</a>
          <a class="select" href="{select_url}">{select_label}</a>
        </article>
        ''')

    success = ''
    if selected and selected_name:
        success = f'<div class="success">✓ <b>{selected_name}</b> varsayılan ses olarak kaydedildi. Worker artık bu sesi kullanacak.</div>'

    current = (
        f'<div class="current">Şu an seçili ses: <b>{selected_name}</b></div>'
        if selected_name else
        '<div class="current">Henüz varsayılan ses seçilmedi.</div>'
    )

    html = f'''
    <!doctype html>
    <html lang="tr">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>Türkçe Ses Karşılaştırma</title>
      <style>
        body {{ font-family: system-ui, sans-serif; margin:0; background:#0f1116; color:#f5f7fb; }}
        main {{ max-width:900px; margin:auto; padding:18px; }}
        .note,.current,.success {{ padding:14px; border-radius:12px; margin-bottom:14px; line-height:1.45; }}
        .note {{ background:#1b2230; }}
        .current {{ background:#222835; }}
        .success {{ background:#15351f; }}
        .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:14px; }}
        .card {{ position:relative; background:#171a22; border:1px solid #2c3240; padding:16px; border-radius:16px; }}
        .card.is-chosen {{ border:2px solid #78e08f; }}
        .chosen {{ position:absolute; right:12px; top:12px; background:#78e08f; color:#102016; padding:5px 8px; border-radius:8px; font-size:.75rem; font-weight:800; }}
        .rank {{ opacity:.55; font-weight:700; }}
        h2 {{ margin:.25rem 0; font-size:1.2rem; }}
        .meta {{ opacity:.72; font-size:.9rem; margin:.4rem 0; }}
        .label {{ font-size:.82rem; opacity:.65; margin-top:8px; }}
        audio {{ width:100%; margin:6px 0 10px; }}
        .same,.select {{ display:block; text-decoration:none; padding:11px 12px; border-radius:10px; text-align:center; margin-top:7px; font-weight:700; }}
        .same {{ background:#f4f5f7; color:#101217; }}
        .select {{ background:#725cff; color:white; }}
      </style>
    </head>
    <body><main>
      <h1>Türkçe Ses Karşılaştırma</h1>
      {success}
      {current}
      <div class="note">
        Önizlemeyi dinle. İstersen aynı paragrafla test et. Beğendiğin seste <b>Bu sesi seç</b> düğmesine bas; seçim Redis'e kaydolur ve video worker aynı sesi kullanır.
      </div>
      <div class="grid">{''.join(cards)}</div>
    </main></body></html>
    '''
    return HTMLResponse(html)
