"""Owner cost page: what each paid call and each video cost, live."""
from datetime import datetime, timezone
from html import escape

from fastapi import APIRouter, Cookie
from fastapi.responses import HTMLResponse, JSONResponse

from app.studio import _require_auth as _auth, _shell, COOKIE_NAME
from app.services import cost_meter

router = APIRouter()

_OPERATIONS = {
    'responses': 'Metin / senaryo', 'text': 'Metin / denetim', 'messages': 'Metin / denetim',
    'video': 'AI video klibi', 'text_to_video': 'AI video klibi', 'speech': 'Seslendirme',
    'image': 'AI görsel', 'transcription': 'Ses kontrolü', 'interaction': 'Gemini işlemi',
}
_STATUS = {
    'published': ('Yayında', 'cost-ok'), 'running': ('Üretiliyor', 'cost-wait'),
    'failed': ('Başarısız', 'cost-bad'), 'unpublished': ('Hazır, yayında değil', 'cost-wait'),
    'unknown': ('Studio kaydı yok', 'cost-wait'),
}


def _usd(value) -> str:
    if value is None:
        return '—'
    return '$' + format(float(value), ',.2f')


def _clock(value) -> str:
    try:
        return datetime.fromisoformat(str(value)).astimezone(cost_meter.LOCAL_TZ).strftime('%H:%M')
    except (TypeError, ValueError):
        return ''


def _operation(entry: dict) -> str:
    return _OPERATIONS.get(str(entry.get('operation')), str(entry.get('operation') or ''))


def _amount(entry: dict) -> str:
    return _usd(entry.get('usd')) if entry.get('priced') else 'fiyat yok'


def _provider_rows(providers: dict, total: float) -> str:
    rows = []
    for provider, usd in sorted(providers.items(), key=lambda item: -item[1]):
        share = (usd / total * 100) if total else 0
        rows.append('<div class="cost-bar"><span>' + escape(cost_meter.PROVIDER_LABELS.get(provider, provider))
                    + '</span><div><i style="width:' + format(min(share, 100), '.1f') + '%"></i></div><b>'
                    + _usd(usd) + '</b></div>')
    return ''.join(rows) or '<p class="muted">Henüz kayıt yok.</p>'


def _kpi(label: str, value: str, note: str) -> str:
    return ('<div class="cost-kpi"><small>' + escape(label) + '</small><b>' + value + '</b><small>'
            + escape(note) + '</small></div>')


def guards(*, now: datetime | None = None) -> dict:
    """Read-only state of the spend limits and admission holds; never raises."""
    now = now or datetime.now(timezone.utc)
    result = {}
    try:
        from app.config import settings
        cap = float(getattr(settings, 'cost_daily_cap_usd', 0) or 0)
        if cap > 0:
            result['daily_cap_usd'] = cap
        from app.services.admission_hold import REASONS, hold_reason
        reason = hold_reason(now=now)
        if reason:
            result['hold'] = {'reason_code': reason, 'message': REASONS[reason]}
        from app.services import channel_ids
        if (getattr(settings, 'studio_block_live_channels', False) is True
                and {channel_ids.CAPITAL_DEFAULT, channel_ids.MARGIN_DEFAULT} & set(channel_ids.MANAGED)):
            result['channel_not_set'] = True
        from app.services import fal_video_catalog
        if fal_video_catalog.primary_enabled(settings):
            result['video_prices_valid_until'] = fal_video_catalog.VALID_UNTIL.isoformat()
            result['video_prices_days_left'] = max(0, (fal_video_catalog.VALID_UNTIL - now).days)
    except Exception:
        pass
    return result


_VIDEO_PROVIDERS = {'fal': 'Fal', 'legacy': 'Runway', 'auto': 'Otomatik (Fal, gerekirse Runway)'}


def _limit(value) -> str:
    try:
        value = float(value or 0)
    except (TypeError, ValueError):
        return '—'
    return _usd(value) if value > 0 else 'kapalı'


def active_settings() -> list[dict]:
    """Read-only list of the settings that decide what a video costs; never raises."""
    rows = []

    def add(label, read):
        try:
            rows.append({'label': label, 'value': str(read())})
        except Exception:
            rows.append({'label': label, 'value': '—'})
    from app.config import settings
    add('Senaryo modeli', lambda: getattr(settings, 'studio_fresh_plan_openai_model', '') or '—')
    add('Görüntü kontrol modeli', lambda: getattr(settings, 'studio_visual_qc_openai_model', '') or '—')
    add('AI video servisi', lambda: _VIDEO_PROVIDERS.get(getattr(settings, 'studio_video_provider', ''), '—'))
    add('Günlük harcama sınırı', lambda: _limit(getattr(settings, 'cost_daily_cap_usd', 0)))
    add('Bir Short için senaryo sınırı', lambda: _limit(getattr(settings, 'cost_planning_task_cap_usd', 0)))

    def shorts_per_day():
        if str(getattr(settings, 'studio_shorts_policy_json', '') or '').strip():
            return 'özel plan'
        value = getattr(settings, 'studio_default_shorts_per_day', 0)
        return str(value) + ' Short' if type(value) is int and 1 <= value <= 5 else 'günlük karışık plan'
    add('Günlük video', shorts_per_day)

    def managed():
        from app.services import channel_ids
        return str(len(channel_ids.MANAGED)) + ' kanal'
    add('Video üretilen kanal', managed)
    add('Eski ödeme onay sistemi',
        lambda: 'açık' if getattr(settings, 'studio_spend_enforcement', False) is True else 'kapalı')
    return rows


def _settings_card(data: dict) -> str:
    rows = data.get('settings') or []
    if not rows:
        return ''
    return ('<section class="card"><h2>Etkin ayarlar</h2>'
            + ''.join('<div class="cost-day"><span>' + escape(row['label']) + '</span><b>' + escape(row['value'])
                      + '</b></div>' for row in rows)
            + '<p class="tiny">Bu değerler Railway ortam değişkenlerinden okunur; değiştirmek için oradaki ayar '
            'güncellenip servis yeniden başlatılır.</p></section>')


def _guard_notices(data: dict) -> str:
    state = data.get('guards') or {}
    notices = []
    if state.get('channel_not_set'):
        notices.append('Yeni kanalın kimliği (STUDIO_CAPITAL_CHANNEL_ID) girilmemiş. Canlı sistemin kanallarına '
                       'video üretilmez; kimlik girilene kadar yeni video başlamaz.')
    if state.get('hold'):
        notices.append(escape(state['hold']['message']))
    days_left = state.get('video_prices_days_left')
    if type(days_left) is int and days_left <= 7:
        until = datetime.fromisoformat(state['video_prices_valid_until']).astimezone(cost_meter.LOCAL_TZ)
        notices.append('Video fiyat listesi ' + until.strftime('%d.%m.%Y') + ' tarihinde sona eriyor. '
                       'Fiyatlar yeniden kontrol edilip liste yenilenmezse o günden sonra yeni video başlatılmaz.')
    return ''.join('<p class="notice">' + notice + '</p>' for notice in notices)


def render(data: dict):
    today, month = data['today'], data['month']
    unpriced = month.get('unpriced_calls', 0)
    cap = (data.get('guards') or {}).get('daily_cap_usd')
    body = """<style>
.cost-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:24px}.cost-kpi{padding:14px 16px;border-radius:12px;background:#f2f7f4}.cost-kpi small{display:block;color:#667d73;font-size:12px}.cost-kpi b{font-size:24px}.cost-bar{display:grid;grid-template-columns:110px 1fr 76px;gap:10px;align-items:center;padding:6px 0;font-size:14px}.cost-bar div{background:#e5ebed;border-radius:6px;height:10px;overflow:hidden}.cost-bar i{display:block;height:100%;background:#26785c}.cost-bar b{text-align:right}.cost-day{display:flex;justify-content:space-between;gap:12px;padding:7px 0;border-bottom:1px solid #e5ebed;font-size:14px}.cost-day span{color:#667d73}.cost-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:24px}.cost-item{padding:12px 0;border-bottom:1px solid #e5ebed}.cost-item:last-child,.cost-day:last-child{border:0}.cost-item-top{display:flex;justify-content:space-between;gap:12px;align-items:baseline}.cost-item-top a{font-weight:700;overflow-wrap:break-word;min-width:0}.cost-item-top b{white-space:nowrap}.cost-item-meta{margin-top:6px;font-size:12px;color:#667d73;display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center}.cost-tag{display:inline-block;padding:3px 8px;border-radius:6px;font-size:11px;white-space:nowrap}.cost-ok{background:#e3f2ea;color:#1e6b4f}.cost-wait{background:#eef1f4;color:#4d5b66}.cost-bad{background:#fbe9e7;color:#9a3b2c}.cost-event{display:grid;grid-template-columns:44px minmax(0,1fr) auto;gap:4px 10px;padding:9px 0;border-bottom:1px solid #e5ebed;font-size:13px}.cost-event:last-child{border:0}.cost-event time{color:#667d73}.cost-event small{grid-column:2/4;color:#667d73;font-size:11px;overflow-wrap:anywhere}@media(max-width:900px){.cost-grid{grid-template-columns:1fr}.cost-bar{grid-template-columns:84px 1fr 66px}.cost-kpi b{font-size:21px}}
</style><div class="hero"><div><div class="eyebrow">MALİYET</div><h1>Hangi iş ne kadara mal oldu?</h1><p class="muted">Her ücretli yapay zeka çağrısı yapıldığı anda buraya düşer. Sayfa dakikada bir yenilenir.</p></div></div>"""
    body += _guard_notices(data)
    body += ('<div class="cost-kpis">'
             + _kpi('Bugün', _usd(today['total']), str(today.get('calls', 0)) + ' çağrı'
                    + (' · günlük sınır ' + _usd(cap) if cap else ''))
             + _kpi('Bu ay', _usd(month['total']), str(month.get('calls', 0)) + ' çağrı')
             + _kpi('Ay sonu tahmini', _usd(data['projected_month']), 'bugüne kadarki hızla')
             + _kpi('Yayınlanan video başına', _usd(data.get('per_published_video')), 'ortalama maliyet')
             + _kpi('1.000 izlenme başına', _usd(data.get('usd_per_1000_views')), 'yayındaki videolarda')
             + _kpi('Boşa giden (30 gün)', _usd(data.get('wasted_total')),
                    str(data.get('wasted_count', 0)) + ' başarısız iş')
             + '</div>')
    if unpriced:
        body += ('<p class="notice">Bu ay ' + str(unpriced) + ' çağrının fiyatı bilinmiyor ve toplama $0 olarak girdi. '
                 'Gerçek tarife COST_METER_PRICES_JSON ile eklenebilir.</p>')
    body += ('<div class="cost-grid"><section class="card"><h2>Bu ay servislere göre</h2>'
             + _provider_rows(month['providers'], month['total']) + '</section>'
             '<section class="card"><h2>Son 7 gün</h2>'
             + ''.join('<div class="cost-day"><span>' + escape(day['day']) + ' · ' + str(day.get('calls', 0))
                       + ' çağrı</span><b>' + _usd(day['total']) + '</b></div>' for day in data['daily'][:7])
             + '</section></div>')
    video_rows = []
    for row in data.get('videos', []):
        label, tone = _STATUS.get(row['status'], ('—', 'cost-wait'))
        views = row.get('views')
        meta = ['<span class="cost-tag ' + tone + '">' + label + '</span>']
        if views is not None:
            meta.append('{:,}'.format(views).replace(',', '.') + ' izlenme')
        if row.get('usd_per_1000_views') is not None:
            meta.append(_usd(row['usd_per_1000_views']) + ' / 1.000 izlenme')
        meta.append(escape(', '.join(cost_meter.PROVIDER_LABELS.get(p, p) for p in row['providers'])))
        video_rows.append(
            '<article class="cost-item"><div class="cost-item-top"><a href="/studio/job/' + escape(row['root_id']) + '">'
            + escape(row['title']) + '</a><b>' + _usd(row['total']) + '</b></div><div class="cost-item-meta">'
            + '<span>' + '</span><span>'.join(meta) + '</span></div></article>')
    body += ('<section class="card"><h2>Video başına maliyet ve sonuç</h2>'
             + (''.join(video_rows) or '<p class="muted">Henüz video kaydı yok.</p>')
             + '<p class="tiny">Planlama, çekim, seslendirme ve düzeltme denemeleri aynı videonun altında toplanır. '
             'İzlenmeler Performans sayfasının son okumasından gelir.</p></section>')
    event_rows = ''.join(
        '<div class="cost-event"><time>' + escape(_clock(event.get('at'))) + '</time><span>'
        + escape(cost_meter.PROVIDER_LABELS.get(event.get('provider'), str(event.get('provider'))))
        + ' · ' + escape(_operation(event)) + '</span><b>' + _amount(event) + '</b><small>'
        + escape(str(event.get('model') or '')) + (' · ' + escape(str(event['units'])) if event.get('units') else '')
        + '</small></div>'
        for event in data['recent'][:60])
    body += ('<section class="card"><h2>Canlı akış</h2>'
             + (event_rows or '<p class="muted">Henüz ücretli çağrı yok.</p>')
             + '<p class="tiny">Saatler Türkiye saatidir. Tutarlar liste fiyatından hesaplanan tahmindir, fatura değildir. Metin modellerinde '
             'servisin bildirdiği gerçek token sayısı kullanılır. Railway, ChatGPT, Abacus veya ses paketleri gibi sabit aylık '
             'abonelikler bu sayfaya girmez.</p></section>')
    body += _settings_card(data)
    return _shell(body, active='costs', title='Maliyet · Studio',
                  script='<script>setTimeout(function(){location.reload()},60000)</script>')


_UNAVAILABLE = 'Maliyet kayıtları şu an okunamıyor.'


def unavailable():
    response = _shell('<div class="hero"><div><div class="eyebrow">MALİYET</div><h1>Hangi iş ne kadara mal oldu?</h1>'
                      '</div></div><p class="notice">' + escape(_UNAVAILABLE) + ' Sayfa bir dakika içinde yeniden dener.</p>',
                      active='costs', title='Maliyet · Studio',
                      script='<script>setTimeout(function(){location.reload()},60000)</script>')
    response.status_code = 503
    return response


# HTMLResponse lets the Studio auth handler send a signed-out browser to the login page.
@router.get('/studio/costs', response_class=HTMLResponse)
def costs(studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    _auth(studio_token)
    try:
        data = cost_meter.summary()
    except Exception:
        return unavailable()
    return render({**data, 'guards': guards(), 'settings': active_settings()})


@router.get('/studio/api/costs')
def costs_api(studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    _auth(studio_token)
    try:
        data = cost_meter.summary()
    except Exception:
        return JSONResponse({'detail': _UNAVAILABLE}, status_code=503)
    return JSONResponse({**data, 'guards': guards(), 'settings': active_settings()})
