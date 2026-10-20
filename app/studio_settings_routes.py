"""A compact owner settings hub and reusable password login."""
from urllib.parse import parse_qsl
import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from app.provider_key_routes import _HEADERS, _authorize, _operation
from app.services import studio_password
from app.studio import _shell
from app.studio_access_routes import _STYLE
from app.youtube_routes import COOKIE_NAME, _require_same_origin

router = APIRouter()


def _set_cookie(response, cookie):
    response.set_cookie(COOKIE_NAME, cookie, max_age=studio_password.MAX_AGE, path='/',
        httponly=True, secure=True, samesite='lax')


async def _password_form(request):
    if request.url.query:
        raise HTTPException(status_code=400)
    if request.headers.get('content-type', '').split(';')[0] != 'application/x-www-form-urlencoded' \
            or request.headers.get('content-encoding', 'identity') != 'identity':
        raise HTTPException(status_code=415)
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 4096:
            raise HTTPException(status_code=413)
        body.extend(chunk)
    try:
        text = body.decode('ascii')
        if re.search(r'%(?![a-fA-F0-9]{2})', text):
            raise ValueError()
        fields = parse_qsl(text, keep_blank_values=True, strict_parsing=True,
                           encoding='utf-8', errors='strict', max_num_fields=1)
        if len(fields) != 1 or fields[0][0] != 'password':
            raise ValueError()
        return fields[0][1]
    except ValueError:
        raise HTTPException(status_code=422) from None


def _login_page(message='', *, status=200):
    style = _STYLE + 'input{display:block;width:100%;box-sizing:border-box;padding:14px;border:1px solid #cdd6de;border-radius:10px;background:white;color:#192733;font:inherit}label{display:block;margin:20px 0 8px;font-size:14px}main{margin:5vh auto}p[role=status]{color:#946019}'
    return HTMLResponse('<!doctype html><html lang="tr"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><title>Studio’ya giriş</title>'
        '<style>' + style + '</style></head><body><main><p class="brand">YOUTUBE STUDIO</p>'
        '<h1>Tekrar hoş geldin</h1><p>Şifrenle dilediğin cihazdan giriş yap.</p>'
        + ('<p role="status">' + message + '</p>' if message else '')
        + '<form action="/studio/login" method="post"><label for="password">Studio şifren</label>'
        '<input id="password" name="password" type="password" required minlength="10" maxlength="128" autocomplete="current-password">'
        '<button type="submit">Giriş yap</button></form><p class="foot">Bu cihazda oturumun 90 gün açık kalır.'
        '<br>İlk kez şifre belirleyeceksen, açık Studio oturumunda Ayarlar → Giriş bölümünü kullan.</p>'
        '</main></body></html>', status_code=status, headers=_HEADERS)


@router.get('/studio/login', response_class=HTMLResponse)
async def login_page(request: Request):
    if request.url.query:
        return _login_page('Adres yerine normal Studio bağlantısını kullan.', status=400)
    return _login_page()


@router.post('/studio/login')
async def login(request: Request):
    try:
        _require_same_origin(request)
        password = await _password_form(request)
        cookie = await run_in_threadpool(_operation, studio_password.login, password)
        response = RedirectResponse('/studio', status_code=303, headers=_HEADERS)
        _set_cookie(response, cookie)
        return response
    except studio_password.PasswordError as error:
        if str(error) == 'password_rate_limited':
            return _login_page('Çok sayıda deneme oldu. Beş dakika sonra tekrar dene.', status=429)
        return _login_page('Şifreyi kontrol edip yeniden dene.', status=401)
    except HTTPException as error:
        return _login_page('Giriş formunu bu sayfadan yeniden aç.', status=error.status_code)
    except Exception:
        return _login_page('Giriş şu anda tamamlanamadı. Biraz sonra tekrar dene.', status=503)


def _settings(message='', *, status=200):
    body = '<div class="hero"><div class="hero-copy"><h1>Ayarlar</h1><p class="muted">Bağlantılar, sesler ve giriş.</p></div></div>'
    if message:
        body += '<div class="notice" role="status">' + message + '</div>'
    body += '''<div class="settings-grid"><section class="card"><h2>Bağlantılar</h2>
<div class="settings-links"><a href="/studio/providers/kie"><b>Kie.ai</b><span>Ses üretimi ve bakiye →</span></a>
<a href="/studio/providers/abacus"><b>Abacus</b><span>Yapay zekâ bağlantısı →</span></a>
<a href="/studio/youtube"><b>YouTube kanalları</b><span>Kanallar ve yayın bağlantıları →</span></a></div></section>
<section class="card" id="access"><h2>Her cihazdan giriş</h2><p class="muted">Bir şifre belirle. Bundan sonra özel giriş bağlantısına gerek kalmadan siteyi açabilirsin.</p>
<form action="/studio/settings/access" method="post"><label class="field" for="new-password">Studio şifren</label>
<input id="new-password" name="password" type="password" required minlength="10" maxlength="128" autocomplete="new-password" placeholder="En az 10 karakter">
<p class="tiny">Şifre yöneticine kaydedebilirsin. Oturumun bu cihazda 90 gün açık kalır.</p>
<button type="submit">Şifremi kaydet</button></form></section>
<section class="card"><h2>İçerik tercihleri</h2><div class="settings-links">
<a href="/studio/growth"><b>Trendler ve büyüme</b><span>İzleyiciye göre içerik ayarları →</span></a>
<a href="/voice-audition"><b>Anlatıcı sesleri</b><span>Sesleri dinle ve seç →</span></a>
<a href="/studio/create"><b>Elle video oluştur</b><span>Özel bir konuyu üretime gönder →</span></a></div></section></div>'''
    response = _shell(body, active='settings', title='Ayarlar · Studio')
    response.status_code = status
    response.headers.update(_HEADERS)
    return response


@router.get('/studio/settings', response_class=HTMLResponse)
async def settings_page(request: Request):
    try:
        _authorize(request)
    except HTTPException as error:
        if error.status_code == 401:
            return RedirectResponse('/studio/login', status_code=303, headers=_HEADERS)
        raise
    return _settings()


@router.post('/studio/settings/access', response_class=HTMLResponse)
async def save_password(request: Request):
    try:
        _authorize(request, write=True)
        password = await _password_form(request)
        await run_in_threadpool(_operation, studio_password.save, password)
        response = _settings('Şifren kaydedildi. Artık her cihazdan normal Studio adresi ve bu şifreyle giriş yapabilirsin.')
        _set_cookie(response, request.cookies[COOKIE_NAME])
        return response
    except HTTPException as error:
        return _settings('Şifre kaydedilemedi. Studio’ya giriş yapıp yeniden dene.', status=error.status_code)
    except studio_password.PasswordError as error:
        return _settings('Şifre 10–128 karakter olmalı. Yeniden dene.' if str(error) == 'password_length'
            else 'Şifre kaydı doğrulanamadı. Yeniden giriş yaparak kontrol et.', status=422 if str(error) == 'password_length' else 503)
    except Exception:
        return _settings('Şifre şu anda kaydedilemiyor. Biraz sonra tekrar dene.', status=503)
