from html import escape
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from app.tasks import run_video_pipeline
from app.services.voice import list_turkish_voice_candidates, synthesize_voice_with_id

app = FastAPI(title='YouTube 7/24 Content Factory', version='0.2.0')

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
    return {'ok': True}

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

@app.get('/voice-audition/sample/{voice_id}')
def voice_sample(voice_id: str, text: str = Query(default=AUDITION_TEXT, min_length=10, max_length=500)):
    try:
        audio = synthesize_voice_with_id(text, voice_id)
        return Response(content=audio, media_type='audio/mpeg')
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                'Voice generation failed. If this is a Voice Library voice and the ElevenLabs account is on the Free plan, '
                'upgrade the ElevenLabs tier and retry. Error: ' + str(exc)
            ),
        ) from exc

@app.get('/voice-audition', response_class=HTMLResponse)
def voice_audition_page():
    try:
        voices = list_turkish_voice_candidates(16)
    except Exception as exc:
        return HTMLResponse(
            f'<h2>Voice audition could not load</h2><pre>{escape(str(exc))}</pre>',
            status_code=502,
        )

    cards = []
    for idx, voice in enumerate(voices, start=1):
        name = escape(voice.get('name') or 'Unnamed voice')
        desc = escape(voice.get('description') or '')
        gender = escape(str(voice.get('gender') or ''))
        age = escape(str(voice.get('age') or ''))
        use_case = escape(str(voice.get('use_case') or ''))
        voice_id = escape(voice.get('voice_id') or '')
        preview = escape(voice.get('preview_url') or '')
        cards.append(f'''
        <article class="card">
          <div class="rank">#{idx}</div>
          <h2>{name}</h2>
          <div class="meta">{gender} · {age} · {use_case}</div>
          <p>{desc}</p>
          <audio controls preload="none" src="{preview}"></audio>
          <div class="id"><b>Voice ID:</b> {voice_id}</div>
          <a class="same" href="/voice-audition/sample/{voice_id}" target="_blank">Aynı test metnini bu sesle üret</a>
        </article>
        ''')

    html = f'''
    <!doctype html>
    <html lang="tr">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>Turkish Voice Audition</title>
      <style>
        body {{ font-family: system-ui, sans-serif; margin: 0; background:#101217; color:#f4f5f7; }}
        main {{ max-width:900px; margin:auto; padding:20px; }}
        .note {{ background:#1d2330; padding:14px; border-radius:12px; margin-bottom:18px; }}
        .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:14px; }}
        .card {{ background:#171a22; border:1px solid #2c3240; padding:16px; border-radius:16px; }}
        .rank {{ opacity:.55; font-weight:700; }}
        h2 {{ margin:.25rem 0; }}
        .meta,.id {{ opacity:.72; font-size:.9rem; margin:.4rem 0; word-break:break-all; }}
        audio {{ width:100%; margin:8px 0; }}
        .same {{ display:block; text-decoration:none; background:#f4f5f7; color:#101217; padding:10px 12px; border-radius:10px; text-align:center; margin-top:10px; font-weight:700; }}
      </style>
    </head>
    <body><main>
      <h1>Türkçe Voice Audition</h1>
      <div class="note">
        Önce her karttaki hazır Türkçe preview'i dinle. “Aynı test metni” bağlantısı, Voice Library API erişimi olan ücretli ElevenLabs hesaplarında aynı metni o sese okutmayı dener.
      </div>
      <div class="grid">{''.join(cards)}</div>
    </main></body></html>
    '''
    return HTMLResponse(html)
