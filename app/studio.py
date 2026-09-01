from __future__ import annotations

from html import escape
import json
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
*{box-sizing:border-box}html{background:#080b11}body{margin:0;min-height:100vh;color:#eef2f8;font-family:Inter,ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif;background:radial-gradient(circle at 15% -10%,#21315d 0,transparent 35%),radial-gradient(circle at 90% 0,#331e55 0,transparent 30%),#080b11}a{color:inherit;text-decoration:none}.wrap{max-width:1240px;margin:auto;padding:18px 20px 80px}.top{position:sticky;top:0;z-index:30;display:flex;align-items:center;justify-content:space-between;gap:18px;padding:14px 0;background:rgba(8,11,17,.86);backdrop-filter:blur(14px);border-bottom:1px solid #252d3c}.brand{font-weight:950;font-size:18px;letter-spacing:-.02em}.nav{display:flex;gap:8px;flex-wrap:wrap}.nav a{padding:9px 13px;border:1px solid #313b4e;border-radius:999px;background:#121824;font-size:13px;font-weight:800}.nav a.active{background:#735cff;border-color:#735cff}.hero{display:flex;align-items:end;justify-content:space-between;gap:20px;padding:34px 0 20px}.hero h1{font-size:42px;line-height:1.03;margin:0 0 10px;letter-spacing:-.045em}.muted{color:#9ca8ba}.tiny{font-size:12px;color:#8d98aa}.layout{display:grid;grid-template-columns:minmax(0,1.65fr) minmax(310px,.8fr);gap:16px}.card{background:rgba(19,24,35,.93);border:1px solid #2c3546;border-radius:20px;padding:18px;box-shadow:0 18px 50px rgba(0,0,0,.18);margin-bottom:14px}.card h2,.card h3{margin:0 0 10px}.section-title{display:flex;justify-content:space-between;align-items:center;gap:10px}.grid2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.grid3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.choice-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.choice{position:relative}.choice input{position:absolute;opacity:0;pointer-events:none}.choice label{display:block;margin:0;padding:14px;border:1px solid #354055;border-radius:15px;background:#0e141f;cursor:pointer;min-height:82px}.choice input:checked+label{border-color:#806dff;background:#211b3b;box-shadow:0 0 0 3px rgba(128,109,255,.13)}.choice b{display:block;margin-bottom:4px}.choice span{font-size:12px;color:#9ca8ba;line-height:1.35}label.field{display:block;margin:14px 0 6px;font-size:13px;font-weight:850}input[type=text],input[type=password],input[type=url],textarea,select{width:100%;border:1px solid #3a4559;border-radius:13px;background:#0b111b;color:#fff;padding:13px 14px;font:inherit;outline:none}textarea{min-height:132px;resize:vertical}input:focus,textarea:focus,select:focus{border-color:#806dff;box-shadow:0 0 0 3px rgba(128,109,255,.15)}button,.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;border:0;border-radius:13px;padding:13px 16px;background:#735cff;color:#fff;font-weight:900;font:inherit;cursor:pointer}.btn.secondary{background:#171f2d;border:1px solid #374258}.btn.success{background:#188957}.btn.danger{background:#91353e}.btn.block,button.block{width:100%}.status-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.service{padding:10px;border-radius:12px;border:1px solid #2d3748;background:#0d131d;font-size:12px;font-weight:800}.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:7px}.green{background:#4bd28a}.red{background:#ff6775}.badge{display:inline-flex;align-items:center;gap:6px;border:1px solid #354055;border-radius:999px;background:#111824;padding:7px 10px;font-size:12px;font-weight:800;margin:3px 3px 3px 0}.notice{border:1px solid #705e1d;background:#2b2410;color:#f6df81;border-radius:14px;padding:12px 14px;font-size:13px}.progress{height:13px;border:1px solid #344054;background:#090e16;border-radius:999px;overflow:hidden}.bar{height:100%;width:0;background:linear-gradient(90deg,#735cff,#33c1ff);transition:width .35s ease}.stage{font-size:13px;font-weight:850}.job-list{display:grid;gap:9px}.job{display:grid;grid-template-columns:1fr auto;gap:12px;align-items:center;padding:13px;border:1px solid #303a4c;border-radius:14px;background:#0d131d}.job-title{font-weight:850;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:700px}.state{font-size:11px;font-weight:900;padding:6px 9px;border-radius:999px;background:#222b3a}.state.success{background:#153b2a;color:#78e5a6}.state.failure{background:#411c23;color:#ff9aa5}.state.approval{background:#403618;color:#ffe187}.scene{display:grid;grid-template-columns:54px 1fr;gap:14px;padding:16px 0;border-bottom:1px solid #293244}.scene:last-child{border:0}.scene-no{width:44px;height:44px;border-radius:13px;background:#251f43;display:flex;align-items:center;justify-content:center;font-weight:950}.queries{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}.query{font-size:11px;padding:5px 7px;border-radius:8px;background:#0d151f;border:1px solid #2b394a;color:#9fb0c4}.metric{padding:12px;border:1px solid #303a4d;border-radius:14px;background:#0e141e}.metric b{font-size:20px;display:block}.actions{display:flex;gap:9px;flex-wrap:wrap;margin-top:12px}pre{white-space:pre-wrap;word-break:break-word;background:#090e16;border:1px solid #2d3748;border-radius:14px;padding:14px;max-height:340px;overflow:auto}@media(max-width:900px){.layout{grid-template-columns:1fr}.hero{display:block}.hero h1{font-size:34px}}@media(max-width:620px){.wrap{padding:10px 10px 70px}.top{align-items:flex-start}.brand{font-size:0}.brand:before{content:'🎬';font-size:20px}.nav{justify-content:flex-end}.grid2,.grid3,.choice-grid{grid-template-columns:1fr}.hero{padding-top:22px}.hero h1{font-size:30px}.job{grid-template-columns:1fr}.actions .btn{width:100%}}
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


def _nav(active: str) -> str:
    links = [
        ('studio', '/studio', '＋ Yeni üretim'),
        ('history', '/studio/history', '◷ Geçmiş'),
        ('youtube', '/studio/youtube', '▶ YouTube'),
        ('voices', '/voice-audition', '🎙 Sesler'),
        ('legacy', '/factory', 'Eski panel'),
    ]
    items = ''.join(
        f'<a class="{"active" if key == active else ""}" href="{url}">{label}</a>'
        for key, url, label in links
    )
    return f'<div class="top"><a class="brand" href="/studio">🎬 YouTube Studio V2</a><nav class="nav">{items}</nav></div>'


def _shell(body: str, *, active: str = 'studio', title: str = 'YouTube Studio V2', script: str = '') -> HTMLResponse:
    return HTMLResponse(
        '<!doctype html><html lang="tr"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        f'<meta name="theme-color" content="#080b11"><title>{escape(title)}</title>'
        f'<style>{BASE_CSS}</style></head><body><div class="wrap">{_nav(active)}{body}</div>{script}</body></html>'
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
    services = ''.join(
        f'<div class="service"><span class="dot {"green" if ok else "red"}"></span>{escape(name)}</div>'
        for name, ok in _service_statuses()
    )
    recent = list_jobs(5) if authenticated else []
    recent_html = ''.join(_job_row(job) for job in recent) or '<div class="muted">Henüz kayıtlı üretim yok.</div>'
    token_field = (
        '<div class="notice">Bu cihazda Studio oturumu açık. Token yeniden istenmeyecek.</div>'
        if authenticated else
        '<label class="field">FACTORY_API_TOKEN</label><input name="token" type="password" autocomplete="off" required placeholder="Railway’deki güvenlik tokenı">'
    )

    body = f'''
<div class="hero"><div><h1>İçerik stüdyosu</h1><div class="muted">Hızlı taslak, storyboard onayı veya yayın kalitesinde tam üretim.</div></div><div><span class="badge">🎙 {selected_voice}</span><span class="badge">16:9 · 1080p</span><span class="badge">Altyazı ayrı SRT</span></div></div>
<div class="layout"><section>
<form action="/studio/start" method="post" class="card" id="studio-form">
<h2>1. Üretim seviyesi</h2>
<div class="choice-grid">
<div class="choice"><input id="mode-preview" name="mode" value="preview" type="radio" checked><label for="mode-preview"><b>⚡ Hızlı önizleme</b><span>30–60 saniye. Müzik kapalı; 30 saniyelik testte yalnız stokla dürüstçe anlatılamayan en fazla 3 sahnede Runway kullanılabilir.</span></label></div>
<div class="choice"><input id="mode-production" name="mode" value="production" type="radio"><label for="mode-production"><b>🏆 Yayın kalitesi</b><span>4–5 dakikalık profesyonel akış, sıkı QC, özgün AI sahneleri ve mümkünse müzik tasarımı.</span></label></div>
</div>
<label class="field">Video konusu ve yönetmen talimatı</label>
<textarea name="topic" required>Telefonla ilgili, gerçek bir insanın otuz saniye izlemek isteyeceği tek bir gündelik sorunu seç. Tek bir şaşırtıcı nedeni göster; aynı kişi veya nesne ve aynı mekânda görünür, faydalı bir sonuçla bitir. Birden fazla teknoloji gerçeğini sıralama.</textarea>
<div class="grid2"><div><label class="field">Süre</label><select id="duration" name="duration_minutes"><option value="0.5" selected>30 saniye</option><option value="1">1 dakika</option><option value="3">3 dakika</option><option value="5">5 dakika</option><option value="8">8 dakika</option><option value="10">10 dakika</option></select></div><div><label class="field">Dil</label><select name="language"><option value="tr" selected>Türkçe</option><option value="en">English</option><option value="de">Deutsch</option><option value="es">Español</option><option value="ar">العربية</option></select></div></div>
<h2 style="margin-top:24px">2. Yaratıcı yönetim</h2>
<div class="grid2"><div><label class="field">İçerik tarzı</label><select name="content_style"><option value="documentary">Belgesel</option><option value="technology" selected>Teknoloji</option><option value="story">Hikâye</option><option value="cinematic">Sinematik</option><option value="explainer">Açıklayıcı</option></select></div><div><label class="field">Kurgu temposu</label><select name="pace"><option value="calm">Sakin</option><option value="balanced" selected>Dengeli</option><option value="dynamic">Dinamik</option></select></div></div>
<div class="grid2"><div><label class="field">Görsel karışımı</label><select name="visual_mix"><option value="real_first">Gerçek görüntü ağırlıklı</option><option value="balanced" selected>Dengeli: B-roll + AI</option><option value="ai_first">Özgün AI ağırlıklı</option></select></div><div><label class="field">Akış</label><select name="workflow"><option value="auto" selected>Otomatik tamamla</option><option value="storyboard">Önce storyboard göster</option></select></div></div>
<div class="grid2"><div><label class="field">Arka plan müziği</label><select name="music"><option value="off">Kapalı</option><option value="auto" selected>Uygunsa otomatik</option></select></div><div><label class="field">Altyazı</label><select name="subtitles"><option value="sidecar" selected>Ayrı SRT üret</option><option value="off">Üretme</option></select></div></div>
<label class="field">Referans video / kanal bağlantısı <span class="tiny">(yalnızca yapı ve ritim analizi)</span></label><input name="reference_url" type="url" placeholder="https://www.youtube.com/watch?v=...">
<label class="field">Kanal etiketi <span class="tiny">(opsiyonel)</span></label><input name="channel_id" type="text" maxlength="120" placeholder="teknoloji-tr-01">
{token_field}
<button class="block" type="submit">✨ Stüdyo üretimini başlat</button>
</form></section>
<aside><div class="card"><h3>Sistem</h3><div class="status-grid">{services}</div></div><div class="card"><div class="section-title"><h3>Son işler</h3><a class="tiny" href="/studio/history">Tümünü gör →</a></div><div class="job-list">{recent_html}</div></div><div class="card"><h3>Yayın standardı</h3><p class="muted">Varsayılan yayın modu: doğal anlatıcı, temiz 16:9 master, gömülü yazı yok, ayrı altyazı dosyası, sahne bazlı görsel kalite kapısı.</p></div></aside></div>
'''
    script = r'''<script>
const preview=document.getElementById('mode-preview'),production=document.getElementById('mode-production'),duration=document.getElementById('duration');
function setDefaults(){if(production.checked){duration.value='5';document.querySelector('[name=workflow]').value='storyboard';document.querySelector('[name=music]').value='auto';}else{duration.value='0.5';document.querySelector('[name=workflow]').value='auto';document.querySelector('[name=music]').value='off';}}
preview.addEventListener('change',setDefaults);production.addEventListener('change',setDefaults);
</script>'''
    return _shell(body, script=script)


def _job_row(job: dict) -> str:
    spec = job.get('spec') or {}
    state = str(job.get('state') or 'PENDING')
    topic = escape(str(spec.get('topic') or job.get('task_id') or 'Görev'))
    mode = MODE_LABELS.get(str(spec.get('mode') or ''), str(spec.get('mode') or ''))
    progress = escape(str(job.get('progress') or 0))
    return f'''<a class="job" href="/studio/job/{escape(str(job.get('task_id') or ''))}"><div><div class="job-title">{topic}</div><div class="tiny">{escape(mode)} · %{progress}</div></div><span class="state {_state_class(state)}">{escape(STATE_LABELS.get(state,state))}</span></a>'''


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
    body = f'''
<div class="hero"><div><h1>Üretim kontrolü</h1><div class="muted">Görev: <span class="tiny">{escape(task_id)}</span></div></div></div>
<div class="card" id="job-card"><div class="stage" id="stage">Yükleniyor…</div><div class="progress" style="margin:14px 0"><div class="bar" id="bar"></div></div><div class="muted" id="message">Görev durumu alınıyor.</div><div id="result"></div></div>
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
 document.getElementById('bar').style.width=p+'%';document.getElementById('stage').textContent=(j.stage_label||stage)+' · %'+p;document.getElementById('message').textContent=j.message||'';
 const out=document.getElementById('result');
 if(state==='FAILURE'){{out.innerHTML=`<div class="notice" style="margin-top:14px;border-color:#7e3038;background:#33171b;color:#ffb2ba"><b>Üretim başarısız</b><pre>${{esc(j.error||'Bilinmeyen hata')}}</pre></div><div class="actions"><form method="post" action="/studio/retry/${{taskId}}"><button class="btn danger">↻ Aynı ayarlarla tekrar dene</button></form></div>`;return;}}
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
    rows = ''.join(_job_row(job) for job in jobs) or '<div class="card muted">Henüz iş yok.</div>'
    body = f'<div class="hero"><div><h1>Üretim geçmişi</h1><div class="muted">Planlar, renderlar, başarısız işler ve final çıktılar.</div></div></div><div class="job-list">{rows}</div>'
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
<div class="hero"><div><h1>Storyboard</h1><div class="muted">{escape(str(package.get('title') or spec.get('topic') or ''))}</div></div><div><span class="badge">{len(scenes)} sahne</span><span class="badge">{escape(STYLE_LABELS.get(str(spec.get('content_style')),str(spec.get('content_style') or '')))}</span></div></div>
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
