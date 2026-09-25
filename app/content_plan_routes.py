"""Small owner console for persistent, server-executed editorial plans."""
from html import escape
from uuid import uuid4

from fastapi import APIRouter, Cookie, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.services import content_plan as plans
from app.services.youtube_automation import list_channel_profiles
from app.services.youtube_auth import connection_status

router = APIRouter()
COOKIE_NAME = 'youtube_studio_token'

CSS = '''
.planner-head{display:flex;align-items:end;justify-content:space-between;gap:24px;padding:34px 0 25px}.planner-head h1{font-size:38px;letter-spacing:-1.5px;margin:5px 0 9px}.planner-head p{margin:0;max-width:630px;color:#68746e;font-size:14px}.planner-head .eyebrow{color:#608573;letter-spacing:2px}.planner-head .btn{white-space:nowrap}.plan-channels{display:flex;gap:8px;margin-bottom:24px;overflow:auto;padding:2px}.plan-channel{display:flex;gap:11px;align-items:center;padding:12px 17px;border:1px solid #dce5df;border-radius:12px;background:#fff;white-space:nowrap;color:#50615a;font-size:13px;font-weight:700}.plan-channel[aria-current=page]{border-color:#426a55;background:#edf4ee;color:#214331;box-shadow:0 0 0 1px #426a55}.channel-monogram{display:grid;place-items:center;width:29px;height:29px;background:#edf1ee;border-radius:9px;color:#64746a;font-size:10px;letter-spacing:1px}.plan-channel[aria-current=page] .channel-monogram{background:#365d47;color:#fff}.planner-layout{display:grid;grid-template-columns:minmax(0,1fr) 285px;align-items:start;gap:24px}.planner-main,.planner-side{min-width:0}.plan-live{position:relative;background:#233d32;border-radius:17px;padding:25px;color:#fff;margin-bottom:26px}.plan-live .section-kicker{color:#b2c7b6;font-size:10px;letter-spacing:1.6px}.plan-live h2{font-size:22px;line-height:1.35;letter-spacing:-.5px;margin:9px 0}.plan-live p{color:#c2d0c6;font-size:13px;margin:8px 0 0}.plan-live a{display:inline-flex;margin-top:16px;color:#fff;font-size:12px;font-weight:700;text-decoration:underline;text-underline-offset:4px}.plan-live-heading{display:flex;justify-content:space-between;gap:16px}.live-dot{background:#c9eed2;width:7px;height:7px;border-radius:50%;display:inline-block;margin-right:6px}.plan-number{white-space:nowrap;flex-shrink:0;font-variant-numeric:tabular-nums;font-size:34px;font-weight:600;opacity:.9}.plan-progress{height:4px;background:#ffffff25;border-radius:8px;overflow:hidden;margin-top:20px}.plan-progress i{height:100%;display:block;background:#bedabe}.plan-section-head{display:flex;align-items:center;justify-content:space-between;gap:15px;margin-bottom:12px}.plan-section-head h2{font-size:16px;color:#293e33;margin:0}.plan-section-head span{color:#77817b;font-size:11px}.plan-list{list-style:none;padding:0;margin:0;border:1px solid #e0e7e1;border-radius:15px;background:#fff;overflow:hidden}.plan-item{display:grid;grid-template-columns:35px minmax(0,1fr) auto;gap:12px;padding:20px 19px;border-bottom:1px solid #edf0ed;align-items:start}.plan-item:last-child{border-bottom:0}.plan-position{font-size:12px;color:#89958c;padding-top:5px;font-variant-numeric:tabular-nums}.plan-item h3{font-size:14px;line-height:1.5;color:#293e33;letter-spacing:-.1px;margin:6px 0}.plan-item-meta{display:flex;gap:7px 11px;align-items:center;flex-wrap:wrap;color:#808b83;font-size:11px}.format-chip{display:inline-flex;align-items:center;gap:4px;font-size:10px;letter-spacing:.5px;font-weight:750;color:#6b795e;background:#f0f3e9;border-radius:5px;padding:3px 6px}.format-chip.long{background:#e9eef5;color:#526d8a}.format-chip.animation{background:#f4eefa;color:#856b9c}.plan-state{font-size:10px;border:1px solid #e3e8e4;border-radius:20px;padding:4px 8px;white-space:nowrap;color:#77847c;display:inline-flex}.plan-state.published{background:#edf6ef;border-color:#d8e9db;color:#426d4c}.plan-state.running,.plan-state.publishing{background:#eef2fa;border-color:#dce4f3;color:#506c9e}.plan-state.blocked,.plan-state.waiting{background:#fff6e9;border-color:#eee2cc;color:#99723c}.plan-state.preparation{background:#f4f0fa;border-color:#e7ddf1;color:#8b749f}.plan-item-actions{display:flex;gap:4px;justify-content:flex-end;margin-top:10px}.plan-item-actions form{margin:0}.plan-item-actions button{height:28px;min-height:28px;width:28px;padding:0;border:1px solid #e4e9e5;background:#fff;border-radius:7px;font-size:12px;color:#7b8880;box-shadow:none}.plan-item-actions button:hover{background:#f0f4ef;color:#284c35}.plan-item-details{margin-top:7px}.plan-item-details summary{font-size:11px;color:#849086;cursor:pointer}.plan-brief{font-size:12px;line-height:1.65;color:#6e7b72;white-space:pre-wrap;overflow-wrap:anywhere;margin:9px 0 3px}.plan-watch{font-size:11px;color:#4d6e5c;text-decoration:underline;text-underline-offset:3px}.planner-box{background:#fff;border:1px solid #e0e7e1;border-radius:15px;padding:20px;margin-bottom:16px}.planner-box h2{font-size:14px;color:#293e33;margin:0 0 14px}.planner-box p,.planner-box li{font-size:12px;line-height:1.7;color:#7b857e}.plan-stats{display:grid;grid-template-columns:1fr 1fr;gap:15px;margin-bottom:19px}.plan-stats b{font-size:25px;color:#304f3b;font-weight:600;display:block}.plan-stats span{font-size:10px;color:#869087}.planner-box label{font-size:11px;color:#687a6d;margin:13px 0 6px;display:block;font-weight:700}.planner-box select,.planner-box input,.planner-box textarea{width:100%;min-width:0;background:#f9fbf8;color:#344d3b;border:1px solid #dde5df;border-radius:8px;font-size:12px;padding:11px;box-shadow:none}.planner-box textarea{min-height:105px;resize:vertical}.planner-box .btn,.planner-box button{width:100%;font-size:12px;min-height:38px;margin-top:14px;border-radius:8px}.planner-box .check-label{display:flex;gap:8px;align-items:center;line-height:1.5}.planner-box input[type=checkbox]{width:15px;height:15px;margin:0}.planner-box .save-note{font-size:10px;color:#8a948c;margin:10px 0 0}.planner-box ol{padding-left:18px;margin:0}.planner-box li+li{margin-top:8px}.plan-completed{margin-top:22px}.plan-completed>summary{color:#718175;font-size:12px;cursor:pointer;margin-bottom:12px}.plan-empty{padding:42px 20px;text-align:center;border:1px dashed #d6e0d7;border-radius:15px;background:#fafcf8}.plan-empty h2{font-size:17px;margin:0 0 8px;color:#3c5946}.plan-empty p{font-size:13px;color:#7d8c80;margin:0 auto;max-width:390px;line-height:1.7}.plan-flash{margin:0 0 20px;padding:13px 16px;border-radius:10px;background:#edf5ef;color:#3e6148;font-size:13px}.plan-flash.error{background:#fff3e5;color:#8c663a}.planner-bottom{padding:17px 0 0;font-size:10px;color:#8b958d;line-height:1.6}.plan-add-series{margin-top:14px;border-top:1px solid #e6ece5;padding-top:14px}.plan-add-series summary{font-size:12px;color:#5b7864;cursor:pointer}.plan-live .plan-state{color:#dcebdc;border-color:#617f69}.planner-box :focus-visible,.plan-item :focus-visible{outline:2px solid #72937b;outline-offset:3px}.plan-refresh-note{font-size:10px;color:#829189;min-height:15px;margin:0 0 10px}
@media(max-width:850px){.planner-layout{grid-template-columns:1fr 255px;gap:16px}.planner-head h1{font-size:32px}.plan-item{padding:16px 12px;gap:8px;grid-template-columns:24px minmax(0,1fr) auto}}
@media(max-width:680px){.planner-layout{display:flex;flex-direction:column}.planner-main,.planner-side{width:100%}.planner-head{align-items:start;padding-top:27px;gap:12px}.planner-head h1{font-size:30px}.planner-head p{font-size:12px}.planner-head .btn{font-size:11px;min-height:36px;padding:8px 11px}.plan-channels{margin-bottom:18px}.plan-channel{padding:10px 12px;font-size:12px}.plan-live{padding:21px}.plan-live h2{font-size:20px}.plan-number{font-size:26px}.plan-item{grid-template-columns:22px minmax(0,1fr)}.plan-item-side{grid-column:2;display:flex;gap:12px;align-items:center;justify-content:space-between}.plan-item-actions{margin:0}.plan-item h3{font-size:14px}.planner-side{display:flex;flex-direction:column}.planner-box#add-video{order:-1}.plan-section-head span{font-size:10px}.plan-progress{margin-top:17px}}
'''

ERRORS = {
    'plan_format_disabled': 'Bu kanal yalnız Shorts üretiyor. Shorts formatını seç.',
    'plan_changed': 'Plan başka bir işlemde güncellendi. Son sırayı kontrol edip tekrar deneyebilirsin.',
    'plan_item_started': 'Üretimi başlayan bir videonun yeri değiştirilemez. Sonraki videoları düzenleyebilirsin.',
    'plan_order_invalid': 'Önce tamamlanması gereken bir bölüm var. Seri sırasını koruyarak taşı.',
    'plan_series_order_invalid': 'Serinin bölümleri kendi sırasıyla ilerlemeli.',
    'plan_channel_missing': 'Bu kanalın bağlantısı veya ayarları doğrulanamadı.',
}


def _auth(token):
    from app.studio import _require_auth
    _require_auth(token)


def _channels():
    profiles = list_channel_profiles()
    connected = (connection_status(verify=False).get('connections') or [])
    names = {v['id']: v.get('title') or v['id'] for v in connected}
    return [(p, names[p['channel_id']]) for p in profiles if p.get('channel_id') in names]


def _hidden(channel, revision, item_id=None):
    result = f'<input type="hidden" name="channel_id" value="{escape(channel, quote=True)}"><input type="hidden" name="revision" value="{escape(revision, quote=True)}">'
    if item_id:
        result += f'<input type="hidden" name="item_id" value="{escape(item_id, quote=True)}">'
    return result


def _row(entry, position, channel, revision):
    series = entry['series']
    meta = f'{escape(series["name"])} · Bölüm {series["number"]}/{series["total"]}' if series else 'Bağımsız video'
    kind = entry['format']; status = entry['status']
    task = entry.get('task_id'); watch = ''
    if task:
        watch = f'<a class="plan-watch" href="/studio/job/{task}">Videoyu ve işi aç ↗</a>'
    if entry.get('url'):
        watch = f'<a class="plan-watch" href="{escape(entry["url"], quote=True)}" target="_blank" rel="noopener noreferrer">YouTube’da izle ↗</a>'
    actions = ''
    if not task:
        for action, symbol, label in [('up', '↑', 'Öne al'), ('down', '↓', 'Geri al'), ('remove', '×', 'Sıradan çıkar')]:
            actions += f'<form method="post" action="/studio/plan/action">{_hidden(channel, revision, entry["id"])}<input type="hidden" name="action" value="{action}"><button type="submit" aria-label="{label}: {escape(entry["title"], quote=True)}" title="{label}">{symbol}</button></form>'
    return (f'<li class="plan-item" data-plan-item="{entry["id"]}"><span class="plan-position">{position:02d}</span>'
            f'<div><div class="plan-item-meta"><span class="format-chip {kind}">{plans.FORMATS[kind][0]}</span><span>{meta}</span></div>'
            f'<h3>{escape(entry["title"])}</h3>{watch}<details class="plan-item-details"><summary>İçerik notu</summary><p class="plan-brief">{escape(entry["brief"])}</p></details></div>'
            f'<div class="plan-item-side"><span class="plan-state {status}" data-plan-status>{escape(entry["label"])}</span><div class="plan-item-actions">{actions}</div></div></li>')


def render_page(channels, selected, view, *, notice='', error=False):
    from app.studio import _shell
    from app.services.channel_formats import shorts_only, daily_limits
    channel_id = selected['channel_id']; revision = view['revision'] if view else 'new'
    only_shorts = shorts_only(channel_id)
    from app.services.framecase_cadence import CHANNEL_ID
    framecase = channel_id == CHANNEL_ID
    rows = view['items'] if view else []
    pending = [v for v in rows if v['status'] != 'published']; published = [v for v in rows if v['status'] == 'published']
    current = pending[0] if pending else None
    tabs = ''.join(f'<a class="plan-channel" href="/studio/plan?channel={p["channel_id"]}"' + (' aria-current="page"' if p['channel_id'] == channel_id else '') + f'><span class="channel-monogram">{escape("".join(w[0] for w in name.split()[:2]))}</span>{escape(name)}</a>' for p, name in channels)
    enabled = view['enabled'] if view else True
    if current:
        heading = current['title']; subtitle = current['label']
        detail = 'Bu bölüm yayımlandıktan sonra listedeki sıradaki video başlayacak.'
        if not enabled:
            subtitle = 'Yeni üretimler duraklatıldı'; detail = 'Başlamış işler korunur. Hazır olduğunda otomatik üretimi yeniden açabilirsin.'
        elif current['status'] == 'preparation':
            detail = 'Bu formatın üretim ve kalite kontrolleri hazırlanıyor. Sırası korunuyor.'
        elif current['status'] == 'waiting':
            detail = 'Görüntü sağlayıcısının kotası bekleniyor. Gösterilen zamanda sunucu otomatik olarak yeniden deneyecek; sıra korunuyor.'
        elif current['status'] == 'blocked':
            detail = 'Bu bölüm tamamlanmadan sonraki bölüme geçilmeyecek. İş kaydından videoyu ve mevcut durumu inceleyebilirsin.'
        target = '/studio/job/' + current['task_id'] if current.get('task_id') else '#queue'
        live = f'<section class="plan-live"><div class="plan-live-heading"><span class="section-kicker"><span class="live-dot"></span>{escape(subtitle.upper())}</span><span class="plan-number">{len(published):02d}<small style="font-size:12px;opacity:.55"> / {len(rows):02d}</small></span></div><h2>{escape(heading)}</h2><p>{detail}</p><div class="plan-progress"><i style="width:{min(100,max(0,int(current.get("progress")or 0))) if current["status"] not in {"blocked", "waiting"} else 0}%"></i></div><a href="{target}">Üretimi takip et →</a></section>'
    else:
        live = '<section class="plan-live"><span class="section-kicker">YAYIN AKIŞI</span><h2>' + ('Plan tamamlandı.' if published else 'Bir sonraki hikâyeyi planla.') + '</h2><p>' + ('Shorts serileri ve yeni fikirler aynı sırada.' if only_shorts else 'Seri bölümleri, uzun videolar ve yeni fikirler aynı sırada.') + ' Üretim sunucuda devam eder.</p></section>'
    queue = '<ol class="plan-list">' + ''.join(_row(v, i + 1, channel_id, revision) for i, v in enumerate(pending)) + '</ol>' if pending else '<div class="plan-empty"><h2>Sıradaki video için yer hazır.</h2><p>Bir konu ekle veya bir serinin bölümlerini birlikte planla. Başlamamış içerikleri daha sonra yeniden sıralayabilirsin.</p></div>'
    finished = '<details class="plan-completed"><summary>Yayımlananları göster · ' + str(len(published)) + '</summary><ol class="plan-list">' + ''.join(_row(v, i+1, channel_id, revision) for i,v in enumerate(published)) + '</ol></details>' if published else ''
    attention = (view or {}).get('attention') or []
    attention_card = ('<section class="planner-box"><h2>İnceleme bekleyen taslaklar</h2>'
        '<p>Bu bağımsız videolar kontrolü geçemedi. Kayıtları saklandı; günlük üretim devam ediyor.</p><ul>'
        + ''.join('<li><a href="/studio/job/' + escape(row['task_id'], quote=True) + '">'
            + escape(row['title']) + ' · Kaydı ve varsa videoyu aç</a></li>' for row in attention)
        + '</ul></section>') if attention else ''
    hidden = _hidden(channel_id, revision)
    after = view['after_queue'] if view else 'pause'
    cadence_card = ''
    if framecase:
        counts = ((view or {}).get('daily_cadence') or {}).get('counts') or {}
        published_today = counts.get('published') or {}
        cadence_card = ('<section class="planner-box"><h2>Günlük yayın sınırı</h2>'
            '<div class="plan-stats"><div><b>' + str(published_today.get('shorts', 0)) + '/10</b><span>SHORTS</span></div>'
            '<div><b>' + str(published_today.get('long', 0)) + '/1</b><span>UZUN VİDEO</span></div></div>'
            '<p>Türkiye saatiyle her gün. Sınır dolduğunda sıra ertesi gün devam eder.</p>'
            '<p>Beş bölümlük özgün animasyon → tamamlanmış uzun hikâye → yeni seri. Sunucuda otomatik ilerler.</p></section>')
    settings_form = (f'<section class="planner-box"><h2>Akış ayarları</h2><div class="plan-stats"><div><b>{len(pending)}</b><span>SIRADAKİ İÇERİK</span></div><div><b>{len(published)}</b><span>YAYIMLANDI</span></div></div>'
        f'<form method="post" action="/studio/plan/settings">{hidden}<label class="check-label"><input type="checkbox" name="enabled" value="yes"'+(' checked' if enabled else '')+'><span>Sırayı otomatik üret</span></label>'
        '<label for="after-queue">Bu sıra tamamlanınca</label><select id="after-queue" name="after_queue">'
        + ''.join(f'<option value="{key}"'+(' selected' if key == after else '')+f'>{label}</option>' for key,label in [('pause','Yeni planımı bekle'),('auto_shorts','Otomatik Shorts ile devam et')])
        + '</select><button type="submit">Ayarları kaydet</button><p class="save-note">Duraklatma yeni işleri durdurur; başlamış üretimi iptal etmez.</p></form></section>')
    if framecase:
        settings_form = settings_form.replace('Yeni planımı bekle', 'Yeni animasyon serisi hazırla')
    if only_shorts:
        cadence = (view or {}).get('daily_cadence') or {}
        published_today = (cadence.get('counts') or {}).get('published') or {}
        limit = daily_limits(channel_id, {})['shorts']
        previous = sum(cadence.get('previously_published_today', {}).values())
        cadence_card = ('<section class="planner-box"><h2>Yalnız Shorts</h2>'
            '<div class="plan-stats"><div><b>' + str(published_today.get('shorts', 0))
            + '/' + str(limit) + '</b><span>BU PLANDA BUGÜN YAYIMLANAN</span></div></div>'
            '<p>Uzun video üretimi kapalı. Bu kanal Türkiye saatiyle günde en fazla ' + str(limit) + ' Shorts yayımlar. Sınır dolunca ertesi gün otomatik devam eder.</p>'
            + ('<p>Plan değişikliğinden önce bugün yayımlanan ' + str(previous) + ' video geçmişte saklanır; yeni plana dahil değildir.</p>' if previous else '') + '</section>')
    format_labels = {'shorts': 'Shorts · yaklaşık 30 saniye', 'long': 'Uzun video · 3 dakika',
                     'animation': 'Animasyon pilotu · hazırlık sırasına ekle'}
    if framecase:
        format_labels['animation'] = 'Animasyon Shorts · yaklaşık 30 saniye'
    if only_shorts:
        format_labels = {'shorts': format_labels['shorts']}
    options = ''.join(f'<option value="{key}">{label}</option>' for key,label in format_labels.items())
    create = (f'<section class="planner-box" id="add-video"><h2>Sıraya içerik ekle</h2><form method="post" action="/studio/plan/add">{hidden}'
        '<label for="plan-title">Video başlığı veya fikir</label><input id="plan-title" name="title" maxlength="140" required placeholder="Örn. Bir banknotun gizli yolculuğu">'
        '<label for="plan-format">Format</label><select id="plan-format" name="format">'+options+'</select>'
        '<label for="plan-brief">Nasıl anlatalım?</label><textarea id="plan-brief" name="brief" maxlength="4000" required placeholder="Anlatılacak hikâye, kaynaklar ve özellikle istediğin ayrıntılar…"></textarea>'
        '<button type="submit">+ Sıraya ekle</button></form>'
        '<details class="plan-add-series"><summary>Bir seriyi birlikte planla</summary>'
        f'<form method="post" action="/studio/plan/series">{hidden}<label for="series-name">Serinin adı</label><input id="series-name" name="name" maxlength="100" required>'
        '<label for="series-format">Bölümlerin formatı</label><select id="series-format" name="format">'+options+'</select>'
        '<label for="series-episodes">Her satıra bir bölüm fikri</label><textarea id="series-episodes" name="episodes" maxlength="12000" required placeholder="İlk bölümün konusu\nİkinci bölümün konusu\nFinal bölümü"></textarea>'
        '<button type="submit">Seriyi sıraya ekle</button></form></details></section>')
    explain = '<section class="planner-box"><h2>Sıra nasıl ilerler?</h2><ol><li>Seri bölümleri belirlenen sırayla hazırlanır.</li><li>Ses, görüntü ve içerik kontrollerinden geçen video yayımlanır.</li><li>Yayın doğrulanınca sıradaki içerik başlar.</li></ol></section>'
    flash = f'<div class="plan-flash'+(' error' if error else '')+f'" role="status">{escape(notice)}</div>' if notice else ''
    body = ('<style>'+CSS+'</style><header class="planner-head"><div><div class="eyebrow">İÇERİK MERKEZİ</div><h1>Yayın planı</h1><p>Önce seriyi tamamla. Sonraki videonun ne olacağını sen belirle.</p></div><a class="btn" href="#add-video">+ İçerik ekle</a></header>'
        +flash+'<nav class="plan-channels" aria-label="Planlanacak kanal">'+tabs+'</nav><div class="planner-layout"><section class="planner-main">'+live
        +'<div class="plan-section-head" id="queue"><h2>Üretim sırası</h2><span>Yukarıdan aşağıya ilerler</span></div><p class="plan-refresh-note" id="plan-refresh-note" aria-live="polite"></p>'
        +queue+attention_card+finished+'<p class="planner-bottom">Kaliteyi geçemeyen bir video yayımlanmış sayılmaz. Durumlar son sunucu kaydını gösterir.</p></section><aside class="planner-side">'+cadence_card+settings_form+create+explain+'</aside></div>')
    script = '''<script>(()=>{const channel=CHANNEL;let busy=false;
async function refresh(){
 const main=document.querySelector('.planner-main');
 if(document.hidden||busy||main.contains(document.activeElement))return;
 busy=true;
 try{
  const r=await fetch('/studio/plan?channel='+encodeURIComponent(channel),{cache:'no-store',credentials:'same-origin'});
  if(!r.ok)throw Error();
  const doc=new DOMParser().parseFromString(await r.text(),'text/html');
  const next=doc.querySelector('.planner-main');if(!next)throw Error();
  const expanded=new Set(Array.from(main.querySelectorAll('[data-plan-item]')).filter(row=>row.querySelector('details[open]')).map(row=>row.dataset.planItem));
  for(const row of next.querySelectorAll('[data-plan-item]'))if(expanded.has(row.dataset.planItem)){const details=row.querySelector('details');if(details)details.open=true;}
  if(main.querySelector('.plan-completed[open]')&&next.querySelector('.plan-completed'))next.querySelector('.plan-completed').open=true;
  main.replaceChildren(...next.childNodes);
  const currentStats=document.querySelectorAll('.plan-stats b'),nextStats=doc.querySelectorAll('.plan-stats b');
  if(currentStats.length===nextStats.length)currentStats.forEach((value,i)=>value.textContent=nextStats[i].textContent);
  document.getElementById('plan-refresh-note').textContent='Canlı durum · '+new Date().toLocaleTimeString('tr-TR',{hour:'2-digit',minute:'2-digit'});
 }catch(_){document.getElementById('plan-refresh-note').textContent='Son kayıtlar gösteriliyor; bağlantı yeniden kontrol edilecek.'}
 finally{busy=false}
}
setInterval(refresh,30000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh()});
})();</script>'''.replace('CHANNEL', '"'+channel_id+'"')
    return _shell(body, active='plan', title='Yayın planı · Studio', script=script)


@router.get('/studio/plan')
def plan_home(channel: str = '', saved: str = '', problem: str = '',
              studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    _auth(studio_token)
    channels = _channels()
    if not channels:
        return RedirectResponse('/studio/youtube', status_code=303)
    selected = next((p for p,_ in channels if p['channel_id'] == channel), channels[0][0])
    try:
        plan = plans.read(selected['channel_id'])
        view = plans.project(plan) if plan else None
    except Exception:
        raise HTTPException(status_code=503, detail='Yayın planı şu anda okunamıyor. Biraz sonra tekrar dene.') from None
    notice = ERRORS.get(problem, 'Bu değişiklik kaydedilemedi. Planı kontrol edip tekrar dene.') if problem else 'Yayın planı kaydedildi.' if saved == '1' else ''
    return render_page(channels, selected, view, notice=notice, error=bool(problem))


@router.get('/studio/api/content-plan')
def plan_status(channel: str, studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    _auth(studio_token)
    try:
        plan = plans.read(channel)
        return JSONResponse(plans.project(plan) if plan else {'items': []}, headers={'Cache-Control': 'private, no-store'})
    except Exception:
        raise HTTPException(status_code=503, detail='Plan şu anda okunamıyor.') from None


def _save(request, token, channel, revision, action, payload):
    from app.youtube_routes import _require_same_origin
    _auth(token); _require_same_origin(request)
    if not plans.CHANNEL.fullmatch(channel):
        raise HTTPException(status_code=422, detail='Kanal geçersiz.')
    try:
        plans.change(channel, revision, action, payload=payload)
        suffix = 'saved=1'
    except plans.ContentPlanError as error:
        suffix = 'problem=' + (str(error) if str(error) in ERRORS else 'plan_invalid')
    except Exception:
        raise HTTPException(status_code=503, detail='Kaydın durumu doğrulanamadı. Yeniden eklemeden önce planı kontrol et.') from None
    return RedirectResponse('/studio/plan?channel='+channel+'&'+suffix, status_code=303)


@router.post('/studio/plan/add')
def plan_add(request: Request, channel_id: str = Form(...), revision: str = Form(...),
             title: str = Form(..., max_length=140), brief: str = Form(..., max_length=4000),
             format: str = Form('shorts'), studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    _auth(studio_token)
    try:
        entry = plans.item(title, brief, format)
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail='Başlık, içerik notu ve formatı kontrol et.') from None
    return _save(request, studio_token, channel_id, revision, 'add', entry)


@router.post('/studio/plan/series')
def plan_series(request: Request, channel_id: str = Form(...), revision: str = Form(...),
                name: str = Form(..., max_length=100), episodes: str = Form(..., max_length=12000),
                format: str = Form('shorts'), studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    _auth(studio_token)
    briefs = [v.strip() for v in episodes.splitlines() if v.strip()]
    if not 2 <= len(briefs) <= 12:
        raise HTTPException(status_code=422, detail='Seri için 2–12 bölüm yaz; her bölüm ayrı satırda olsun.')
    group = 'plan-' + uuid4().hex; entries = []
    try:
        for index, brief in enumerate(briefs):
            entries.append(plans.item(brief[:140], brief, format, series={'id': group, 'name': name,
                'number': index+1, 'total': len(briefs)}, depends_on=[entries[-1]['id']] if entries else []))
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail='Serinin adını ve bölüm metinlerini kontrol et.') from None
    return _save(request, studio_token, channel_id, revision, 'append', entries)


@router.post('/studio/plan/action')
def plan_action(request: Request, channel_id: str = Form(...), revision: str = Form(...),
                item_id: str = Form(...), action: str = Form(...),
                studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    if action not in {'up','down','remove'}:
        raise HTTPException(status_code=422, detail='İşlem geçersiz.')
    return _save(request, studio_token, channel_id, revision, action, {'id': item_id})


@router.post('/studio/plan/settings')
def plan_settings(request: Request, channel_id: str = Form(...), revision: str = Form(...),
                  enabled: str = Form(''), after_queue: str = Form('pause'),
                  studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    return _save(request, studio_token, channel_id, revision, 'settings',
                 {'enabled': enabled == 'yes', 'after_queue': after_queue})
