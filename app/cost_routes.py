"""Owner cost page: what each paid call and each video cost, live."""
from html import escape

from fastapi import APIRouter, Cookie
from fastapi.responses import JSONResponse

from app.studio import _require_auth as _auth, _shell, COOKIE_NAME
from app.services import cost_meter

router = APIRouter()

_OPERATIONS = {
    'responses': 'Metin / senaryo', 'text': 'Metin / denetim', 'messages': 'Metin / denetim',
    'video': 'AI video klibi', 'text_to_video': 'AI video klibi',
}


def _usd(value) -> str:
    return '$' + format(float(value or 0), ',.2f')


def _operation(entry: dict) -> str:
    if entry.get('provider') == 'elevenlabs':
        return 'Seslendirme'
    return _OPERATIONS.get(str(entry.get('operation')), str(entry.get('operation') or ''))


def _job_title(task_id: str) -> str:
    try:
        from app.services.studio_state import get_job
        spec = (get_job(task_id) or {}).get('spec') or {}
        return str(spec.get('title') or spec.get('topic') or task_id[:8])
    except Exception:
        return task_id[:8]


def _provider_rows(providers: dict, total: float) -> str:
    rows = []
    for provider, usd in sorted(providers.items(), key=lambda item: -item[1]):
        share = (usd / total * 100) if total else 0
        rows.append('<div class="cost-bar"><span>' + escape(cost_meter.PROVIDER_LABELS.get(provider, provider))
                    + '</span><div><i style="width:' + format(min(share, 100), '.1f') + '%"></i></div><b>'
                    + _usd(usd) + '</b></div>')
    return ''.join(rows) or '<p class="muted">Henüz kayıt yok.</p>'


def render(data: dict):
    today, month = data['today'], data['month']
    unpriced = month.get('unpriced_calls', 0)
    body = '''<style>
.cost-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:16px;margin-bottom:24px}.cost-kpi{padding:18px;border-radius:12px;background:#f2f7f4}.cost-kpi small{display:block;color:#667d73;font-size:12px}.cost-kpi b{font-size:28px}.cost-bar{display:grid;grid-template-columns:120px 1fr 80px;gap:10px;align-items:center;padding:6px 0;font-size:14px}.cost-bar div{background:#e5ebed;border-radius:6px;height:10px;overflow:hidden}.cost-bar i{display:block;height:100%;background:#26785c}.cost-bar b{text-align:right}.cost-table{width:100%;border-collapse:collapse;font-size:13px}.cost-table td,.cost-table th{padding:8px 6px;border-bottom:1px solid #e5ebed;text-align:left;overflow-wrap:anywhere}.cost-table td.num,.cost-table th.num{text-align:right;white-space:nowrap}.cost-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:24px}@media(max-width:900px){.cost-grid{grid-template-columns:1fr}.cost-bar{grid-template-columns:90px 1fr 70px}}
</style><div class="hero"><div><div class="eyebrow">MALİYET</div><h1>Hangi iş ne kadara mal oldu?</h1><p class="muted">Her ücretli yapay zeka çağrısı yapıldığı anda buraya düşer. Sayfa 30 saniyede bir yenilenir.</p></div></div>'''
    body += ('<div class="cost-kpis">'
             '<div class="cost-kpi"><small>Bugün (UTC)</small><b>' + _usd(today['total']) + '</b><small>'
             + str(today.get('calls', 0)) + ' çağrı</small></div>'
             '<div class="cost-kpi"><small>Bu ay</small><b>' + _usd(month['total']) + '</b><small>'
             + str(month.get('calls', 0)) + ' çağrı</small></div>'
             '<div class="cost-kpi"><small>Ay sonu tahmini</small><b>' + _usd(data['projected_month'])
             + '</b><small>bugüne kadarki hızla</small></div></div>')
    if unpriced:
        body += ('<p class="notice">Bu ay ' + str(unpriced) + ' çağrının fiyatı bilinmiyor ve toplama $0 olarak girdi. '
                 'Gerçek tarifeyi COST_METER_PRICES_JSON ile ekleyebilirsin.</p>')
    body += ('<div class="cost-grid"><section class="card"><h2>Bu ay servislere göre</h2>'
             + _provider_rows(month['providers'], month['total']) + '</section>'
             '<section class="card"><h2>Son 14 gün</h2><table class="cost-table"><tr><th>Gün</th><th class="num">Çağrı</th><th class="num">Tutar</th></tr>'
             + ''.join('<tr><td>' + escape(day['day']) + '</td><td class="num">' + str(day.get('calls', 0))
                       + '</td><td class="num">' + _usd(day['total']) + '</td></tr>' for day in data['daily'])
             + '</table></section></div>')
    job_rows = ''.join(
        '<tr><td><a href="/studio/job/' + escape(job['task_id']) + '">' + escape(_job_title(job['task_id']))
        + '</a></td><td>' + escape(', '.join(cost_meter.PROVIDER_LABELS.get(p, p) for p in job['providers']))
        + '</td><td class="num">' + str(job.get('calls', 0)) + '</td><td class="num"><b>' + _usd(job['total'])
        + '</b></td></tr>' for job in data['jobs'])
    body += ('<section class="card"><h2>Video başına maliyet</h2><table class="cost-table"><tr><th>Video</th><th>Servisler</th>'
             '<th class="num">Çağrı</th><th class="num">Toplam</th></tr>'
             + (job_rows or '<tr><td colspan="4" class="muted">Henüz video kaydı yok.</td></tr>') + '</table></section>')
    event_rows = ''.join(
        '<tr><td>' + escape(str(event.get('at', ''))[11:19]) + '</td><td>'
        + escape(cost_meter.PROVIDER_LABELS.get(event.get('provider'), str(event.get('provider'))))
        + '</td><td>' + escape(_operation(event)) + '<br><span class="tiny">' + escape(str(event.get('model') or ''))
        + (' · ' + escape(str(event['units'])) if event.get('units') else '') + '</span></td><td class="num">'
        + (_usd(event.get('usd')) if event.get('priced') else 'fiyat yok') + '</td></tr>'
        for event in data['recent'][:60])
    body += ('<section class="card"><h2>Canlı akış</h2><table class="cost-table"><tr><th>Saat</th><th>Servis</th><th>İşlem</th>'
             '<th class="num">Tutar</th></tr>' + (event_rows or '<tr><td colspan="4" class="muted">Henüz ücretli çağrı yok.</td></tr>')
             + '</table><p class="tiny">Tutarlar liste fiyatından hesaplanan tahmindir, fatura değildir. Metin modellerinde '
             'servisin bildirdiği gerçek token sayısı kullanılır.</p></section>')
    return _shell(body, active='costs', title='Maliyet · Studio',
                  script='<script>setTimeout(function(){location.reload()},30000)</script>')


@router.get('/studio/costs')
def costs(studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    _auth(studio_token)
    return render(cost_meter.summary())


@router.get('/studio/api/costs')
def costs_api(studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    _auth(studio_token)
    return JSONResponse(cost_meter.summary())
