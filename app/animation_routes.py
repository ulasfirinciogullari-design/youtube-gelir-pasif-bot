"""Owner-only animation catalogue and social account setup in existing Studio."""
from html import escape

from fastapi import APIRouter, Cookie, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from app.services import animation_studio as animation
from app.services import social_accounts as social
from app.studio import _shell
from app.youtube_routes import COOKIE_NAME, _require_auth, _require_same_origin

router = APIRouter()
HEADERS = {'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
           'Referrer-Policy': 'same-origin', 'X-Frame-Options': 'DENY'}
ERRORS = {
    'animation_changed': 'Ayarlar başka bir işlemde değişti. Sayfayı yenileyip tekrar dene.',
    'buffer_access_required': 'Buffer API anahtarı veya hesap erişimi doğrulanamadı.',
    'buffer_key_invalid': 'Buffer API anahtarının biçimini kontrol et.',
    'buffer_unavailable': 'Buffer şu anda yanıt vermiyor. Son kayıtlar korunuyor.',
    'buffer_rate_limited': 'Buffer kısa süreli istek sınırına ulaştı. Biraz sonra yeniden kontrol edebilirsin.',
    'buffer_response_invalid': 'Buffer yanıtı beklenen biçimde değil. Bağlantı etkinleştirilmedi.',
    'social_channel_changed': 'Seçilen hesap Buffer’da bulunamadı. Bağlantıları yenileyip hesabı tekrar seç.',
    'social_changed': 'Bağlantı başka bir işlemde değişti. Güncel hesapları kontrol et.',
}


def _authorize(request, token, *, write=False):
    _require_auth(token)
    if write:
        _require_same_origin(request)
    if write and request.url.query:
        raise HTTPException(status_code=400, detail='Bu işlemi Studio formundan yap.')


def _page(body, active='animation', notice='', error=False):
    flash = '<div class="ani-flash' + (' error' if error else '') + f'" role="status">{escape(notice)}</div>' if notice else ''
    tabs = ''.join(f'<a class="{"current" if key == active else ""}" href="/studio/{key}">{title}</a>'
                   for key, title in [('animation', 'Puppy’nin dünyası'), ('social', 'Sosyal yayınlar')])
    css = (animation.ASSETS / 'studio.css').read_text(encoding='utf-8')
    return _shell('<style>'+css+'</style><div class="ani"><nav class="ani-tabs" aria-label="Animasyon merkezi">'+tabs+'</nav>'+flash+body+'</div>',
                  active=active, title=('Puppy’nin Dünyası' if active == 'animation' else 'Sosyal yayınlar')+' · Studio')


def _read(operation):
    try:
        return operation()
    except Exception:
        raise HTTPException(status_code=503, detail='Kayıtlar şu anda okunamıyor. Biraz sonra sayfayı yenile.') from None


def _notice(saved, problem):
    return ERRORS.get(problem, 'İşlem tamamlanamadı. Son kaydı kontrol edip tekrar dene.') if problem else \
        'Değişiklik kaydedildi.' if saved == '1' else ''


def render_animation(view, notice='', error=False):
    config = view['config']; arc = view['pilot']
    queued = {r['id']: r for r in view['episodes']}
    cards = ''
    for ep in arc['episodes']:
        identity = arc['id']+'-'+str(ep['number'])
        row = queued.get(identity); phase = row['phase'] if row else 'script_ready'
        cards += (f'<article class="ani-episode"><span class="ani-number">{ep["number"]:02}</span><div>'
            f'<h3>{escape(ep["title_tr"])}</h3><p>{escape(ep["hook"])}</p>'
            f'<a href="/studio/animation/episode/{identity}">Bölümü incele · 32 saniye →</a></div>'
            f'<span class="ani-state">{animation.PHASES[phase]}</span></article>')
    extras = [r for r in view['episodes'] if r['adventure_id'] != arc['id']]
    if extras:
        cards += '<details style="margin-top:20px"><summary>Sonraki maceralar · '+str(len(extras))+' bölüm</summary>'
        cards += ''.join(f'<article class="ani-post"><b>{escape(r["title"])}</b><p>{escape(r["adventure_title"])} · {animation.PHASES[r["phase"]]}</p></article>' for r in extras)
        cards += '</details>'
    stats = ''.join(f'<div class="ani-stat"><b>{value:02}</b><span>{label}</span></div>' for value, label in [
        (len(view['episodes']), 'SIRAYA EKLENEN'), (view['counts']['ready'], 'İZLENEBİLİR / HAZIR'), (view['counts']['published'], 'YAYIMLANAN')])
    daily = ''.join(f'<option value="{n}"'+(' selected' if config['episodes_per_day'] == n else '')+f'>{n} bölüm / macera</option>' for n in (4, 5))
    langs = ''.join(f'<span lang="{code}">{escape(animation.LANGUAGES[code])}</span>' for code in config['subtitle_languages'])
    body = f'''<section class="ani-hero"><div><div class="eyebrow">ÖZGÜN BİR ANİMASYON DÜNYASI</div>
      <h1>Küçük patiler.<br>Büyük maceralar.</h1><p>Başrolde senin köpeğin <strong>Puppy, yani Papi</strong>.
      Merakın, arkadaşlığın ve küçük keşiflerin dünyanın her yerinde anlaşılabildiği hikâyeler.</p>
      <span class="ani-badge">İlk animasyon ve ses denemesi bekliyor</span>
      <div class="ani-actions"><a class="ani-btn" href="#episodes">İlk 5 bölümü gör</a><a class="ani-btn secondary" href="/studio/social">Sosyal hesaplar</a></div></div>
      <img src="/studio/animation/assets/pilot-poster-v1.png" alt="Papi ve Mika, yosunların arasında minik bir ışık keşfediyor. Konsept görseli."></section>
      <div class="ani-stats">{stats}</div><div class="ani-grid"><section><div class="ani-section" id="episodes">
      <h2>Minik Işığın Yolculuğu</h2><small>5 bölüm · Bir tamamlanmış hikâye</small></div>{cards}
      <div class="ani-box" style="margin-top:24px"><h2>Papi her sahnede aynı Papi.</h2>
      <p>Krem-beyaz tüyler, küçük dik kulaklar, koyu yuvarlak gözler ve kabarık kuyruk.
      Dört patisi üzerinde hareket eder; adı her dilde “Papi” diye okunur.</p>
      <img class="ani-cast" src="/studio/animation/assets/puppy-model-sheet-v1.png" alt="Papi’nin önden ve yandan görünümü ile farklı yüz ifadeleri." loading="lazy">
      <small class="note">Karakter tasarımıdır. Hareketli video, ses ve bölüm sürekliliği kontrolleri henüz tamamlanmadı.</small></div>
      <div class="ani-box"><h2>Dünyanın birçok dilinde</h2><p>10 dilde altyazı taslağı hazır. Toplam 20 dil hedefleniyor.
      Dublajlar henüz kaydedilmedi; altyazılar gerçek ses zamanlamasıyla kontrol edilecek.</p><div class="ani-tags">{langs}</div>
      <small class="note">Her bölümün sayfasından altyazı paketini indirebilirsin. Hazırlanan dosya ile YouTube’a yüklenen dil ayrı takip edilir.</small></div></section>
      <aside><section class="ani-box"><h2>Günlük akış</h2><form method="post" action="/studio/animation/settings">
      <input type="hidden" name="revision" value="{escape(config['revision'], quote=True)}">
      <label class="ani-check"><input type="checkbox" name="planning_enabled" value="yes"{' checked' if config['planning_enabled'] else ''}> Yeni maceraları sıraya al</label>
      <label for="cadence">Günlük hedef</label><select id="cadence" name="episodes_per_day">{daily}</select>
      <label for="stock">Bekleyen bölüm hedefi</label><input id="stock" name="stock_target" type="number" min="5" max="20" value="{config['stock_target']}">
      <button type="submit">Ayarları kaydet</button><small class="note">Senaryo taslakları da bekleyen sayısına dahildir. İlk hikâyenin finali için beş bölüm birlikte korunur.</small></form>
      <form method="post" action="/studio/animation/prepare"><button type="submit">Bugünün macerasını sıraya ekle</button></form>
      <small class="note">Bu aşama planlama yapar. Ücretli video üretimi veya yayın başlatmaz.</small></section>
      <section class="ani-box"><h2>Sıradaki adımlar</h2><ol class="ani-list"><li>Papi’nin hareket ve ses denemesi</li>
      <li>İlk bölümün görüntü, kurgu ve ses kontrolü</li><li>Animasyon kanalının bağlanması</li><li>Instagram ve TikTok bağlantısı</li>
      <li>Gerçek yayınla otomatik akışın doğrulanması</li></ol></section>
      <section class="ani-box"><h3>Ailece izlenecek bir dünya</h3><p>Çocukları da hedeflediği için bu serinin YouTube yayınları çocuklara özel olarak işaretlenecek. Bu videolarda YouTube yorumları kapalı olur.</p></section></aside></div>'''
    return _page(body, notice=notice, error=error)


@router.get('/studio/animation')
def animation_home(saved: str = '', problem: str = '', studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    _require_auth(studio_token)
    return render_animation(_read(animation.presentation), _notice(saved, problem), bool(problem))


@router.get('/studio/animation/assets/{filename}')
def animation_asset(filename: str, studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    _require_auth(studio_token)
    if filename not in {'puppy-model-sheet-v1.png', 'pilot-poster-v1.png'}:
        raise HTTPException(status_code=404, detail='Görsel bulunamadı.')
    return FileResponse(animation.ASSETS / filename, media_type='image/png', headers=HEADERS)


@router.get('/studio/api/animation')
def animation_state(studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    _require_auth(studio_token)
    return JSONResponse(_read(animation.presentation), headers=HEADERS)


def _change(operation, path):
    try:
        operation()
    except (animation.AnimationError, social.SocialError) as error:
        code = str(error) if str(error) in ERRORS else 'invalid'
        return RedirectResponse(path+'?problem='+code, status_code=303, headers=HEADERS)
    except Exception:
        raise HTTPException(status_code=503, detail='İşlemin sonucu doğrulanamadı. Tekrar denemeden önce son kaydı kontrol et.') from None
    return RedirectResponse(path+'?saved=1', status_code=303, headers=HEADERS)


@router.post('/studio/animation/settings')
def animation_settings(request: Request, revision: str = Form(..., max_length=50),
                       episodes_per_day: int = Form(...), stock_target: int = Form(...),
                       planning_enabled: str = Form(''), studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    _authorize(request, studio_token, write=True)
    return _change(lambda: animation.save_config(revision, {'episodes_per_day': episodes_per_day,
        'stock_target': stock_target, 'planning_enabled': planning_enabled == 'yes'}), '/studio/animation')


@router.post('/studio/animation/prepare')
def animation_prepare(request: Request, studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    _authorize(request, studio_token, write=True)
    result = _read(animation.prepare_day)
    messages = {'prepared': 'Macera sıraya eklendi.', 'already_prepared': 'Bugünün macerası zaten sırada.',
        'stock_full': 'Bekleyen bölüm hedefi dolu. Mevcut bölümler tamamlanınca yeni macera eklenebilir.',
        'paused': 'Yeni macera planlama duraklatılmış.', 'new_story_required': 'Yeni macera senaryosu gerekiyor.',
        'pilot_requires_five_stock_slots': 'İlk maceranın finalini korumak için en az beş boş yer gerekli.'}
    return render_animation(_read(animation.presentation), messages.get(result['status'], 'Planın son durumunu kontrol et.'))


@router.get('/studio/animation/episode/{episode_id}')
def episode_page(episode_id: str, studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    _require_auth(studio_token)
    try:
        ep = animation.episode_script(episode_id)
    except animation.AnimationError:
        raise HTTPException(status_code=404, detail='Bölüm senaryosu bulunamadı.') from None
    config = _read(animation.read_config)
    translated = {'source_script': 'Kaynak senaryo', 'draft': 'Taslak hazır', 'translation_pending': 'Hazırlanacak'}
    language_rows = ''.join(f'<tr><td lang="{row["code"]}">{escape(row["name"])}</td><td>{translated[row["subtitles"]]}</td><td>'
        +('Ses denemesi bekliyor' if row['dubbing'] == 'voice_review_pending' else 'Planlanmadı')+'</td><td>Yüklenmedi</td></tr>'
        for row in animation.language_status(ep, config))
    shots = ''.join(f'<article class="ani-shot"><small>SAHNE {i+1} · {i*8}–{i*8+8} SN</small><p lang="en">{escape(s["action"])}</p>'
        +''.join(f'<p><b>{escape(c["speaker"].title())}:</b> <span lang="en">{escape(c["text"])}</span></p>' for c in s['dialogue'])+'</article>'
        for i,s in enumerate(ep['shots']))
    body = f'''<a class="ani-setup-link" href="/studio/animation">← Bütün bölümler</a><h1>{escape(ep['title_tr'])}</h1>
      <p>Bölüm {ep['number']} / 5 · Minik Işığın Yolculuğu · 32 saniyelik senaryo</p>
      <div class="ani-flash">Bu bölümün hareketli videosu henüz üretilmedi. Aşağıdaki kayıtlar senaryo ve tasarım hazırlığıdır.</div>
      <div class="ani-box"><h2>Başlangıç ve sonuç</h2><p><b>İlk an:</b> {escape(ep['hook'])}</p>
      <p><b>Bölümün karşılığı:</b> {escape(ep['payoff'])}</p></div>
      <div class="ani-section"><h2>Sahne akışı</h2><small>Üretim yönergeleri · İngilizce</small></div><div class="ani-shots">{shots}</div>
      <section class="ani-box" style="margin-top:24px"><div class="ani-section"><h2>Dillerin durumu</h2>
      <a class="ani-btn secondary" href="/studio/animation/episode/{episode_id}/subtitles.zip">Altyazı taslaklarını indir</a></div>
      <p>10 dil · SRT ve VTT. Zamanlar senaryo planına göre; gerçek ses ve ana dil kontrolü bekliyor.</p>
      <table class="ani-subtable"><thead><tr><th>Dil</th><th>Altyazı</th><th>Dublaj</th><th>YouTube</th></tr></thead><tbody>{language_rows}</tbody></table></section>'''
    return _page(body)


@router.get('/studio/animation/episode/{episode_id}/subtitles.zip')
def subtitle_download(episode_id: str, studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    _require_auth(studio_token)
    try:
        data = animation.subtitle_package(episode_id)
    except animation.AnimationError:
        raise HTTPException(status_code=404, detail='Altyazı paketi bulunamadı veya senaryo değişti.') from None
    return Response(data, media_type='application/zip', headers={**HEADERS,
        'Content-Disposition': f'attachment; filename="{episode_id}-draft-subtitles.zip"'})


def render_social(state, notice='', error=False):
    connections = ''.join(f'<div class="ani-connection"><b>{label}</b><span>'
        +escape(next((c['name'] for c in state['channels'] if c['id'] == state['selected'].get(platform)), 'Puppy hesabı seçilmedi'))+'</span></div>'
        for platform,label in social.PLATFORMS.items())
    form = ''
    if state['connected']:
        for platform,label in social.PLATFORMS.items():
            choices = [c for c in state['channels'] if c['platform'] == platform]
            options = '<option value="">Şimdilik bağlama</option>'+''.join(
                f'<option value="{escape(c["id"],quote=True)}"'+(' selected' if state['selected'].get(platform)==c['id'] else '')+
                f'>{escape(c["name"])}'+(' · Sıra duraklatılmış' if c['paused'] else '')+'</option>' for c in choices)
            form += f'<label for="social-{platform}">{label} hesabı</label><select id="social-{platform}" name="{platform}">{options}</select>'
        form = f'<form method="post" action="/studio/social/bind"><input type="hidden" name="revision" value="{escape(state["revision"],quote=True)}">'+form+'<button type="submit">Puppy hesaplarını seç</button></form>'
        form += '<form method="post" action="/studio/social/refresh"><button type="submit">Hesapları ve yayınları yenile</button></form>'
    observation = state.get('observation')
    rows = observation['posts'] if observation else []
    posts = ''.join(f'<article class="ani-post"><span class="ani-state">{escape(social.PLATFORMS[r["platform"]])} · {social.STATUS[r["status"]]}</span><p>{escape(r["text"])}</p>'
                    +(f'<small class="note">{escape(r["due_at"])}</small>' if r['due_at'] else '')+'</article>' for r in rows)
    if not posts:
        posts = '<p>Henüz doğrulanmış bir sosyal yayın kaydı yok. Bağlı Puppy hesaplarının Buffer kayıtları burada görünecek.</p>'
    if observation:
        posts += f'<small class="note">Son kontrol: {escape(observation["checked_at"])}'+(' · İlk 50 kayıt / hesap; daha eski kayıtlar Buffer’da.' if observation['truncated'] else '')+'</small>'
        if observation['missing_platforms']:
            posts += '<div class="ani-flash error">Bazı hesapların bağlantısı artık bulunamıyor. Buffer’da yeniden bağla ve hesap seçimini yenile.</div>'
    body = f'''<div class="eyebrow">PUPPY &amp; FRIENDS</div><h1>Sosyal yayınlar</h1>
      <p>Instagram ve TikTok aynı ekranda. YouTube yayınları mevcut <a class="ani-setup-link" href="/studio/plan">YouTube planından</a> devam eder.</p>
      <div class="ani-grid"><section><div class="ani-box"><h2>Yayın takibi</h2>
      <span class="ani-badge">Otomatik gönderim henüz devrede değil</span><p>Hesap bağlantısı, gerçek animasyon videosu ve ilk yayın doğrulandıktan sonra gönderim açılacak.</p>{posts}</div>
      <div class="ani-box"><h2>Bağlantıyı hazırla</h2><ol class="ani-list">
      <li>Puppy için yetişkinler tarafından yönetilen Instagram profesyonel ve TikTok hesaplarını aç.</li>
      <li><a class="ani-setup-link" href="https://buffer.com" target="_blank" rel="noopener noreferrer">Buffer hesabında</a> bu iki sosyal hesabı bağla.</li>
      <li>Buffer’ın API ayarlarından oluşturduğun anahtarı aşağıdaki güvenli alana kaydet.</li><li>Bulunan hesaplardan Puppy’ye ait olanları seç.</li></ol>
      <p>Hesap açma ve platform izinleri hesap sahibinin oturumunu gerektirir. Kullanıcı adı önerisi <b>@puppyandfriends</b>; uygunluğu henüz doğrulanmadı.</p>
      <form method="post" action="/studio/social/connect"><label for="buffer-key">Buffer API anahtarı</label>
      <input id="buffer-key" name="api_key" type="password" autocomplete="off" spellcheck="false" required minlength="16" maxlength="2048" aria-describedby="buffer-note">
      <button type="submit">{'Anahtarı güncelle ve kontrol et' if state['connected'] else 'Güvenle bağla'}</button>
      <small id="buffer-note" class="note">Anahtar şifrelenerek saklanır ve ekranda gösterilmez. Sohbete yazmana gerek yok. Bu işlem gönderi yayımlamaz.</small></form></div></section>
      <aside><section class="ani-box"><h2>Hesaplar</h2><div class="ani-connection"><b>YouTube</b><span>Mevcut otomasyon · Animasyon kanalı ayrıca bağlanacak</span></div>{connections}{form}</section>
      <section class="ani-box"><h3>Tek içerik, uygun format</h3><p>Temiz dikey video → YouTube Shorts, Instagram Reels ve TikTok.
      Her platformun başlığı ve açıklaması ayrı hazırlanır. Bölüm sırası korunur.</p>
      <small class="note">Buffer’daki “sırada” kaydı yayın kanıtı değildir. Yalnız “sent” yanıtı yayımlandı olarak gösterilir.</small></section></aside></div>'''
    return _page(body, active='social', notice=notice, error=error)


@router.get('/studio/social')
def social_home(saved: str = '', problem: str = '', studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    _require_auth(studio_token)
    return render_social(_read(social.public_state), _notice(saved, problem), bool(problem))


@router.post('/studio/social/connect')
async def social_connect(request: Request):
    _authorize(request, request.cookies.get(COOKIE_NAME), write=True)
    from app.provider_key_routes import _form_key
    key = await _form_key(request)
    return await run_in_threadpool(_change, lambda: social.connect(key), '/studio/social')


@router.post('/studio/social/bind')
def social_bind(request: Request, revision: str = Form(..., max_length=50), instagram: str = Form('', max_length=128),
                tiktok: str = Form('', max_length=128), studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    _authorize(request, studio_token, write=True)
    return _change(lambda: social.bind(revision, {p:i for p,i in [('instagram', instagram), ('tiktok', tiktok)] if i}), '/studio/social')


@router.post('/studio/social/refresh')
def social_refresh(request: Request, studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    _authorize(request, studio_token, write=True)
    return _change(social.observe, '/studio/social')
