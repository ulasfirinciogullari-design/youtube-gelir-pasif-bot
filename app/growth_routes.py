"""Owner controls for current demand, narration and translated captions."""
from html import escape
import json

from fastapi import APIRouter, Cookie, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.studio import _require_auth as _auth, _shell, COOKIE_NAME
from app.content_plan_routes import _channels
from app.services import audience_strategy as strategy, audience_trends as trends, video_localization

router = APIRouter()


def _voices_active():
    try:
        from app.services import production_spend_runtime as runtime
        from app.services.production_credit_ledger import CreditLedger
        from app.services.narrator_rotation import POOL_SHA256
        foundation = runtime.configured_ledger(read_timeout=2)
        binding = CreditLedger(foundation.client, foundation=foundation, clock=foundation.clock).binding_snapshot()
        return binding.get('voice_pool_sha256') == POOL_SHA256
    except Exception:
        return False


def render(channels, selected, preferences, data, languages, voices_active, *, saved=False, cadence=None):
    channel = selected['channel_id']
    tabs = ''.join('<a class="btn'+(' success' if p['channel_id'] == channel else '')+'" href="/studio/growth?channel='
        + p['channel_id'] + '">' + escape(name) + '</a>' for p, name in channels)
    opportunities = trends.relevant(selected, data=data)
    cards = ''.join('<article class="growth-signal"><div><b>' + escape(row['term'])
        + '</b><p class="tiny">' + escape(' · '.join(row['regions'])) + ' · '
        + ('Son taramada görüldü' if row['fresh'] else 'Bu haftanın gözlemi')
        + '</p></div><div class="growth-volume">' + escape(row['traffic_label'] or '—')
        + '<small>yaklaşık arama</small></div></article>' for row in opportunities)
    settings_fields = ''.join('<label class="growth-check"><input type="checkbox" name="'+key+'" value="yes"'
        + (' checked' if preferences[key] else '') + '><span><b>'+title+'</b><small>'+detail+'</small></span></label>'
        for key, title, detail in (
            ('trend_enabled', 'Gündeme göre konu seç', 'Güncel ilgiyi kanalın konusu ve güvenilir kaynaklarla eşleştir.'),
            ('retention_enabled', 'İzleyiciden öğren', 'İlk saniyeleri ve anlatım temposunu aynı formattaki sonuçlarla geliştir.'),
            ('voice_rotation', 'Anlatıcıları çeşitlendir', 'Yeni videolarda farklı sesler; aynı videonun düzeltmelerinde aynı anlatıcı.')))
    options = ''.join('<label class="growth-language"><input type="checkbox" name="languages" value="'+lang+'"'
        + (' checked' if lang in preferences['languages'] else '') + '> '+label+'</label>' for lang, label in strategy.LANGUAGES.items())
    language_rows = []
    labels = {'pending': 'Çeviri bekliyor', 'prepared': 'Altyazı hazır', 'insert_reserved': 'YouTube yanıtı kontrol ediliyor',
        'awaiting_processing': 'YouTube işliyor', 'published': 'Yayında', 'existing_caption_preserved': 'Mevcut altyazı korunuyor',
        'uncertain': 'Gönderim sonucu kontrol ediliyor', 'rejected': 'YouTube tekrar denemesi bekleniyor', 'review_failed': 'Çeviri kontrolü gerekli'}
    for row in languages:
        if row.get('channel_id') != channel:
            continue
        pills = ''.join('<span class="growth-pill">'+escape(strategy.LANGUAGES.get(lang, lang))+' · '
            + escape(labels.get(item.get('status'), 'Kontrol ediliyor'))+'</span>' for lang, item in row['languages'].items())
        downloads = ''.join('<a href="/studio/growth/captions/'+row['video_id']+'/'+lang+'">'+escape(strategy.LANGUAGES[lang])+' altyazısı ↓</a>'
            for lang, item in row['languages'].items() if item.get('srt_key'))
        language_rows.append('<article class="growth-language-row"><a class="growth-video-link" href="/studio/job/'+row['source_task_id']
            +'">Videoyu aç ↗</a><div>'+pills+'</div><div class="growth-downloads">'+downloads+'</div></article>')
    source_note = ('Son kontrol: '+escape(str(data.get('checked_at') or '')[:16].replace('T', ' '))+' UTC'
        if data.get('checked_at') else 'İlk sunucu taraması bekleniyor.')
    stale_note = '<p class="notice">Bazı kaynaklar okunamadı; önceki gözlemler tarihleriyle korunuyor.</p>' if data.get('regions_unavailable') else ''
    body = '''<style>
.growth-grid{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(300px,1fr);gap:24px}.growth-tabs{display:flex;gap:8px;margin-bottom:24px;flex-wrap:wrap}.growth-signal{display:flex;justify-content:space-between;gap:18px;padding:16px 0;border-bottom:1px solid #e5ebed}.growth-signal:last-child{border:0}.growth-signal b{font-size:17px}.growth-signal p{margin:5px 0 0}.growth-volume{text-align:right;white-space:nowrap;font-weight:750;color:#236853}.growth-volume small{display:block;font-size:10px;font-weight:400;color:#698079}.growth-check{display:flex;gap:12px;align-items:flex-start;padding:14px 0;border-bottom:1px solid #e5ebed}.growth-check input{margin-top:5px;accent-color:#26785c}.growth-check small{display:block;font-size:12px;color:#6d7c86;margin-top:4px}.growth-language{display:inline-flex;gap:6px;align-items:center;margin:8px 10px 8px 0;font-size:13px}.growth-language input{accent-color:#26785c}.growth-pill{display:inline-block;margin:4px 5px 4px 0;padding:6px 9px;background:#eef4f0;border-radius:7px;font-size:12px;overflow-wrap:anywhere}.growth-language-row{padding:18px 0;border-bottom:1px solid #e5ebed}.growth-video-link{font-weight:750;font-size:14px}.growth-downloads{display:flex;gap:12px;flex-wrap:wrap;font-size:12px;color:#26785c;margin-top:8px}.growth-note{font-size:13px;color:#667d73;line-height:1.6}.growth-step{padding:14px;border-left:3px solid #619c81;background:#f2f7f4;margin:12px 0;font-size:14px}@media(max-width:900px){.growth-grid{grid-template-columns:1fr}.growth-tabs .btn{font-size:12px}.growth-signal b{font-size:15px}}
</style><div class="hero"><div><div class="eyebrow">BÜYÜME MERKEZİ</div><h1>İlgiyi doğru hikâyeye çevir.</h1><p class="muted">Gündem, izleyici tutma, anlatıcılar ve diller tek yerde.</p></div></div>'''
    if saved:
        body += '<p class="notice success">Ayarlar kaydedildi. Yeni üretimlerde uygulanacak.</p>'
    if cadence:
        published = cadence['counts']['published']
        limits = cadence['limits']
        mix = str(limits['shorts']) + ' Shorts / gün'
        progress = str(published['shorts']) + ' / ' + str(limits['shorts']) + ' Shorts'
        if limits['long']:
            mix = str(limits['long']) + ' uzun video + ' + mix
            progress = str(published['long']) + ' / ' + str(limits['long']) + ' uzun video · ' + progress
        previous = sum(cadence.get('previously_published_today', {}).values())
        body += ('<section class="card"><h2>Günlük yayın düzeni</h2><p><b>' + mix + '</b></p>'
            '<p>Bu planda bugün: ' + progress + '</p>'
            + ('<p>Bugün plan değişmeden önce yayımlanan ' + str(previous) + ' video yeni plana dahil değildir.</p>' if previous else '')
            + '<p class="growth-note">Türkiye saati. '
            'Sınır dolunca sıradaki gün otomatik devam eder. Sunucudaki zamanlayıcı, bu ekran ve sohbet kapalıyken de çalışır.</p></section>')
    body += '<nav class="growth-tabs">'+tabs+'</nav><div class="growth-grid"><div><section class="card"><div class="section-head"><h2>Kanalına uygun güncel ilgi</h2></div><p class="tiny">'+source_note+'</p>'+stale_note
    body += cards or '<p class="muted">Şu anda kanala uygun, doğrulanmış bir trend gözlemi yok. Kaynaklı mevcut konular üretilmeye devam eder.</p>'
    body += '<p class="growth-note">Kaynak: Google Trends. Türkiye, ABD, Birleşik Krallık, Brezilya, Hindistan ve Meksika iki saatte bir taranır; haftalık gözlemler korunur. Sayılar YouTube izlenmesi değildir.</p></section>'
    body += '<section class="card"><h2>Nasıl üretime dönüşüyor?</h2><div class="growth-step">Güncel ilgi → kanalın konusu → kaynak kontrolü → yeni seri önerisi</div><div class="growth-step">İlk saniyede net soru → sahne başına yeni bilgi → verilen sözün cevabı</div><p class="growth-note">Başladığın seriler ve yayın sırası korunur. Yeterli izlenme verisi birikince Shorts ve uzun videolar ayrı değerlendirilir. Bir videoyu uzatmak yerine izlemeye değer tutmak hedeflenir.</p><a class="btn" href="/studio/analytics">İzleyici sonuçları</a></section></div>'
    body += '<div><section class="card"><h2>Üretim tercihleri</h2><form method="post" action="/studio/growth/settings"><input type="hidden" name="channel_id" value="'+channel+'"><input type="hidden" name="revision" value="'+str(preferences['revision'])+'">'+settings_fields
    body += '<h3 style="margin-top:24px">Altyazı dilleri</h3>'+options+'<p class="growth-note">Orijinal dil korunur. Ek diller yayın sonrasında hazırlanır; çeviri kuyruğu ana yayını durdurmaz.</p><button class="btn success" type="submit">Tercihleri kaydet</button></form></section>'
    body += '<section class="card"><h2>Anlatıcılar</h2><p>'+('Dönüşümlü anlatım hazır.' if voices_active else 'Hesaptaki seslerin etkinleştirilmesi hazırlanıyor.')+'</p><p class="growth-note">Türkçe: Baran, Melek, Alp, Mustafa.<br>İngilizce: George, Alice, Daniel, Bella.</p><h3>Dublaj</h3><p class="growth-note">İki kanalda otomatik dublajı açtığını bildirdin. YouTube’un mevcut desteği: Türkçe → İngilizce; İngilizce → İspanyolca, Portekizce, Hintçe, Arapça. Uygunluk ve oluşan sesler video bazında YouTube Studio’dan kontrol edilir. Diğer dillerin özel ses dosyalarını YouTube’a eklemek Studio işlemi gerektirir.</p></section></div></div>'
    body += '<section class="card"><h2>Dil kuyruğu</h2>'+(''.join(language_rows[:20]) or '<p class="muted">Yeni yayımlanan videolar burada dilleriyle birlikte görünecek.</p>')+'</section>'
    return _shell(body, active='growth', title='Büyüme merkezi · Studio')


@router.get('/studio/growth')
def growth(channel: str = '', saved: str = '', studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    _auth(studio_token)
    channels = _channels()
    if not channels:
        return RedirectResponse('/studio/youtube', status_code=303)
    selected = next((p for p, _ in channels if p['channel_id'] == channel), channels[0][0])
    try:
        client = strategy._client()
        from app.services.channel_cadence import snapshot
        return render(channels, selected, strategy.read_settings(selected['channel_id'], client=client),
            trends.snapshot(client=client), video_localization.dashboard(client), _voices_active(), saved=saved == '1',
            cadence=snapshot(selected['channel_id'], client=client))
    except Exception:
        raise HTTPException(status_code=503, detail='Büyüme bilgileri şu anda okunamıyor. Biraz sonra yenile.') from None


@router.post('/studio/growth/settings')
def save(request: Request, channel_id: str = Form(...), revision: int = Form(...),
         trend_enabled: str = Form(''), voice_rotation: str = Form(''), retention_enabled: str = Form(''),
         languages: list[str] = Form(default=[]), studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    from app.youtube_routes import _require_same_origin
    _auth(studio_token); _require_same_origin(request)
    if channel_id not in {p['channel_id'] for p, _ in _channels()}:
        raise HTTPException(status_code=422, detail='Kanal bağlantısını kontrol et.')
    try:
        strategy.save_settings(channel_id, revision, {'trend_enabled': trend_enabled == 'yes',
            'voice_rotation': voice_rotation == 'yes', 'retention_enabled': retention_enabled == 'yes', 'languages': languages})
    except ValueError:
        raise HTTPException(status_code=409, detail='Ayarlar değişmiş olabilir. Sayfayı yenileyip tekrar dene.') from None
    return RedirectResponse('/studio/growth?channel='+channel_id+'&saved=1', status_code=303)


@router.get('/studio/growth/captions/{video}/{language}')
def captions(video: str, language: str, studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    _auth(studio_token)
    if not video_localization._VIDEO.fullmatch(video) or language not in strategy.LANGUAGES:
        raise HTTPException(status_code=404)
    client = strategy._client()
    row = json.loads(client.get(video_localization.PREFIX + 'video:' + video) or '{}')
    item = row.get('languages', {}).get(language, {})
    key = f"videos/{row.get('source_task_id')}/languages/{language}.srt"
    if item.get('srt_key') != key or row.get('channel_id') not in {p['channel_id'] for p, _ in _channels()}:
        raise HTTPException(status_code=404)
    from app.services.storage import presigned_download_url
    return RedirectResponse(presigned_download_url(key, 600), status_code=303,
        headers={'Cache-Control': 'private, no-store', 'Referrer-Policy': 'no-referrer'})
