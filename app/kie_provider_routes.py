"""Owner-only Kie connection form. Secrets never enter HTML or URL parameters."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from app.provider_key_routes import _HEADERS, _authorize, _form_key, _operation
from app.services import kie_credentials
from app.services.provider_key_candidate import ProviderKeyCandidateError
from app.studio import _shell

router = APIRouter()
PATH = '/studio/providers/kie'


def _page(message='', *, form=False, status_code=200, funding=None):
    body = '''<div class="provider-panel"><div class="hero"><div class="hero-copy"><h1>Kie.ai ses bağlantısı</h1>
<p class="muted">Video seslendirmeleri için Kie.ai hesabın.</p></div></div>'''
    if message:
        body += '<div class="notice" role="status">' + message + '</div>'
    if isinstance(funding, dict) and type(funding.get('remaining_microcredits')) is int:
        available = f"{funding['remaining_microcredits'] / 1_000_000:,.2f}".replace(',', ' ').rstrip('0').rstrip('.')
        label = 'Ses ve zamanlama kontrolü bekleniyor'
        if funding.get('requests', 0) and funding.get('failed_requests') == funding['requests']:
            label = 'Ses denemesinde Kie.ai hata verdi. Üretim bağlantısı henüz etkin değil.'
        if funding.get('status') == 'active':
            label = 'Ses üretiminde etkin'
        body += '<section class="card"><span class="section-kicker">OTOMASYONA AYRILAN KIE BAKİYESİ</span>'
        body += '<h2>' + available + ' kredi</h2><p class="muted">' + label + '</p></section>'
        body += '<form method="post" action="/studio/providers/kie/balance"><button class="btn secondary" type="submit">Güncel hesap bakiyesini kontrol et</button></form>'
    if form:
        body += '''<section class="card"><form method="post" action="/studio/providers/kie" autocomplete="off">
<label class="field" for="kie-key">Kie.ai API anahtarı</label>
<input id="kie-key" type="password" name="api_key" required minlength="16" maxlength="4096" autocomplete="off" autocapitalize="none" spellcheck="false">
<p class="muted"><a href="https://kie.ai/api-key" target="_blank" rel="noopener noreferrer">Kie.ai anahtar sayfasını aç</a>, anahtarını kopyalayıp buraya yapıştır. Anahtarını sohbete göndermene gerek yok.</p>
<button type="submit">Bağlantıyı kaydet</button></form></section>'''
    body += '''<p class="provider-help">Bağlantı kaydedildikten sonra bakiye, Türkçe ve İngilizce sesler ve altyazı zamanlaması kontrol edilir. Kaydetmek tek başına otomatik üretimi başlatmaz.</p>
<p><a class="btn secondary" href="/studio/settings">← Ayarlar</a></p></div>'''
    response = _shell(body, active='providers', title='Kie.ai bağlantısı')
    response.status_code = status_code
    response.headers.update(_HEADERS)
    return response


def _auth_error(error):
    status = error.status_code if error.status_code in (400, 401, 403, 503) else 403
    message = ('<a href="/studio/access">Studio’ya giriş yap</a> ve bu sayfayı yeniden aç.'
        if status == 401 else 'Bu istek kabul edilmedi. Studio sayfasından devam et.')
    return _page(message, status_code=status)


@router.get(PATH, response_class=HTMLResponse)
async def kie_page(request: Request):
    try:
        _authorize(request)
    except HTTPException as error:
        return _auth_error(error)
    try:
        found = await run_in_threadpool(_operation, kie_credentials.present)
        funding = None
        if found:
            from app.services.kie_voice_ledger import status
            funding = await run_in_threadpool(_operation, status)
        message = ('Kie.ai bağlantısı kaydedildi.' if funding and funding.get('status') != 'not_allocated'
            else 'Anahtar kaydedildi. Bakiye ve ses üretimi kontrolü bekliyor.') if found else ''
        return _page(message,
                     form=not found, funding=funding)
    except Exception:
        return _page('Bağlantı durumu şu anda okunamıyor. Daha sonra yeniden aç.', status_code=503)


@router.post(PATH, response_class=HTMLResponse)
async def kie_save(request: Request):
    try:
        _authorize(request, write=True)
    except HTTPException as error:
        return _auth_error(error)
    try:
        if await run_in_threadpool(_operation, kie_credentials.present):
            return _page('Anahtar zaten kaydedildi; erişim kontrolü bekliyor.', status_code=409)
        key = await _form_key(request)
        await run_in_threadpool(_operation, kie_credentials.save, key)
        return _page('Kie.ai anahtarın güvenle kaydedildi. Bakiye ve ses üretimi kontrolüne hazır.')
    except HTTPException as error:
        return _page('Anahtar girişini kontrol et.', form=True, status_code=error.status_code)
    except ProviderKeyCandidateError as error:
        if error.code == 'candidate_invalid':
            return _page('Kie.ai API anahtarını kontrol et.', form=True, status_code=422)
        if error.code == 'candidate_pending':
            return _page('Anahtar zaten kaydedildi; erişim kontrolü bekliyor.', status_code=409)
        return _page('Kayıt sonucu doğrulanamadı. Durumu görmek için sayfayı yeniden aç.', status_code=503)
    except Exception:
        return _page('Kayıt sonucu doğrulanamadı. Durumu görmek için sayfayı yeniden aç.', status_code=503)


def _live_balance(client):
    from app.services.kie_voice_adapter import read_balance
    credential = kie_credentials.read(client)
    if credential is None:
        raise ValueError('key_missing')
    return read_balance(credential.api_key)['available_microcredits']


@router.post(PATH + '/balance', response_class=HTMLResponse)
async def kie_balance(request: Request):
    try:
        _authorize(request, write=True)
    except HTTPException as error:
        return _auth_error(error)
    try:
        remaining = await run_in_threadpool(_operation, _live_balance)
        number = f'{remaining / 1_000_000:,.2f}'.replace(',', ' ').rstrip('0').rstrip('.')
        return _page('Kie.ai hesabındaki güncel bakiye: <b>' + number + ' kredi</b>.')
    except Exception:
        return _page('Kie.ai bakiyesi şu anda okunamıyor. Mevcut kayıtlar korundu.', status_code=503)
