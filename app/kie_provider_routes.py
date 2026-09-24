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


def _page(message='', *, form=False, status_code=200):
    body = '''<div class="hero"><div class="hero-copy"><h1>Kie.ai ses bağlantısı</h1>
<p class="muted">Yüklediğin Kie.ai bakiyesini video seslendirmelerinde kullanmak için hesabını bağla.</p></div></div>'''
    if message:
        body += '<div class="notice" role="status">' + message + '</div>'
    if form:
        body += '''<section class="card"><form method="post" action="/studio/providers/kie" autocomplete="off">
<label class="field" for="kie-key">Kie.ai API anahtarı</label>
<input id="kie-key" type="password" name="api_key" required minlength="16" maxlength="4096" autocomplete="off" autocapitalize="none" spellcheck="false">
<p class="muted"><a href="https://kie.ai/api-key" target="_blank" rel="noopener noreferrer">Kie.ai anahtar sayfasını aç</a>, anahtarını kopyalayıp buraya yapıştır. Anahtarını sohbete göndermene gerek yok.</p>
<button type="submit">Bağlantıyı kaydet</button></form></section>'''
    body += '''<section class="card"><h2>Sonraki adım</h2><p>Bağlantı kaydedildikten sonra bakiye erişimi, Türkçe ve İngilizce sesler ve altyazı zamanlaması kontrol edilecek. Kaydetmek tek başına otomatik üretimi başlatmaz.</p></section>
<p><a class="btn secondary" href="/studio">Studio’ya dön</a></p>'''
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
        return _page('Anahtar kaydedildi. Bakiye ve ses üretimi kontrolü bekliyor.' if found else '',
                     form=not found)
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
