"""Owner cost page: what each paid call and each video cost, live."""
from html import escape

from fastapi import APIRouter, Cookie
from fastapi.responses import JSONResponse

from app.studio import _require_auth as _auth, _shell, COOKIE_NAME
from app.services import cost_meter

router = APIRouter()

_OPERATIONS = {
    'responses': 'Metin / senaryo', 'text': 'Metin / denetim', 'messages': 'Metin / denetim',
    'video': 'AI video klibi', 'text_to_video': 'AI video klibi', 'speech': 'Seslendirme',
}
_STATUS = {
    'published': ('Yayında', 'cost-ok'), 'running': ('Üretiliyor', 'cost-wait'),
    'failed': ('Başarısız', 'cost-bad'), 'unpublished': ('Hazır, yayında değil', 'cost-wait'),
}


def _usd(value) -> str:
    if value is None:
        return '—'
    return '$' + format(float(value), ',.2f')


def _operation(entry: dict) -> str:
    return _OPERATIONS.get(str(entry.get('operation')), str(entry.get('operation') or ''))


def _amount(entry: dict) -> str:
    if entry.get('subscription'):
        return 'abonelik'
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


def render(data: dict):
    today, month = data['today'], data['month']
    unpriced = month.get('unpriced_calls', 0)
    body = '''<style>
.cost-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:14px;margin-bottom:24px}.cost-kpi{padding:16px;border-radius:12px;background:#f2f7f4}.cost-kpi small{display:block;color:#667d73;font-size:12px}.cost-kpi b{font-size:26px}.cost-bar{display:grid;grid-template-columns:110px 1fr 80px;gap:10px;align-items:center;padding:6px 0;font-size:14px}.cost-bar div{background:#e5ebed;border-radius:6px;height:10px;overflow:hidden}.cost-bar i{display:block;height:100%;background:#26785c}.cost-bar b{text-align:right}.cost-table{width:100%;border-collapse:collapse;font-size:13px}.cost-table td,.cost-table th{padding:8px 6px;border-bottom:1px solid #e5ebed;text-align:left;overflow-wrap:anywhere}.cost-table td.num,.cost-table th.num{text-align:right;white-space:nowrap}.cost-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:24px}.cost-tag{display:inline-block;padding:3px 8px;border-radius:6px;font-size:11px;white-space:nowrap}.cost-ok{background:#e3f2ea;color:#1e6b4f}.cost-wait{background:#eef1f4;color:#4d5b66}.cost-bad{background:#fbe9e7;color:#9a3b2c}.cost-scroll{overflow-x:auto}@media(max-width:900px){.cost-grid{grid-template-columns:1fr}.cost-bar{grid-template-columns:84px 1fr 70px}.cost-kpi b{font-size:22px}}
</style><div class="hero"><div><div class="eyebrow">MALİYET</div><h1>Hangi iş ne kadara mal oldu?</h1><p class="muted">Her ücretli yapay zeka çağrısı yapıldığı anda buraya düşer. Sayfa dakikada bir yenilenir.</p></div></div>'''
    body += ('<div class="cost-kpis">'
             + _kpi('Bugün (UTC)', _usd(today['total']), str(today.get('calls', 0)) + ' çağrı')
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
             '<section class="card"><h2>Son 14 gün</h2><table class="cost-table"><tr><th>Gün</th><th class="num">Çağrı</th><th class="num">Tutar</th></tr>'
             + ''.join('<tr><td>' + escape(day['day']) + '</td><td class="num">' + str(day.get('calls', 0))
                       + '</td><td class="num">' + _usd(day['total']) + '</td></tr>' for day in data['daily'])
             + '</table></section></div>')
    video_rows = []
    for row in data.get('videos', []):
        label, tone = _STATUS.get(row['status'], ('—', 'cost-wait'))
        video_rows.append(
            '<tr><td><a href="/studio/job/' + escape(row['root_id']) + '">' + escape(row['title']) + '</a><br><span class="tiny">'
            + escape(', '.join(cost_meter.PROVIDER_LABELS.get(p, p) for p in row['providers'])) + '</span></td>'
            '<td><span class="cost-tag ' + tone + '">' + label + '</span></td>'
            '<td class="num">' + ('{:,}'.format(row['views']).replace(',', '.') if row.get('views') is not None else '—')
            + '</td><td class="num"><b>' + _usd(row['total']) + '</b></td><td class="num">'
            + _usd(row.get('usd_per_1000_views')) + '</td></tr>')
    body += ('<section class="card"><h2>Video başına maliyet ve sonuç</h2><div class="cost-scroll"><table class="cost-table"><tr><th>Video</th>'
             '<th>Durum</th><th class="num">İzlenme</th><th class="num">Maliyet</th><th class="num">1.000 izlenme</th></tr>'
             + (''.join(video_rows) or '<tr><td colspan="5" class="muted">Henüz video kaydı yok.</td></tr>')
             + '</table></div><p class="tiny">Planlama, çekim, seslendirme ve düzeltme denemeleri aynı videonun altında toplanır. '
             'İzlenmeler Performans sayfasının son okumasından gelir.</p></section>')
    event_rows = ''.join(
        '<tr><td>' + escape(str(event.get('at', ''))[11:19]) + '</td><td>'
        + escape(cost_meter.PROVIDER_LABELS.get(event.get('provider'), str(event.get('provider'))))
        + '</td><td>' + escape(_operation(event)) + '<br><span class="tiny">' + escape(str(event.get('model') or ''))
        + (' · ' + escape(str(event['units'])) if event.get('units') else '') + '</span></td><td class="num">'
        + _amount(event) + '</td></tr>'
        for event in data['recent'][:60])
    body += ('<section class="card"><h2>Canlı akış</h2><div class="cost-scroll"><table class="cost-table"><tr><th>Saat (UTC)</th><th>Servis</th><th>İşlem</th>'
             '<th class="num">Tutar</th></tr>' + (event_rows or '<tr><td colspan="4" class="muted">Henüz ücretli çağrı yok.</td></tr>')
             + '</table></div><p class="tiny">Tutarlar liste fiyatından hesaplanan tahmindir, fatura değildir. Metin modellerinde '
             'servisin bildirdiği gerçek token sayısı kullanılır. Railway, ChatGPT, Abacus veya ses paketleri gibi sabit aylık '
             'abonelikler bu sayfaya girmez.</p></section>')
    return _shell(body, active='costs', title='Maliyet · Studio',
                  script='<script>setTimeout(function(){location.reload()},60000)</script>')


@router.get('/studio/costs')
def costs(studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    _auth(studio_token)
    return render(cost_meter.summary())


@router.get('/studio/api/costs')
def costs_api(studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    _auth(studio_token)
    return JSONResponse(cost_meter.summary())
