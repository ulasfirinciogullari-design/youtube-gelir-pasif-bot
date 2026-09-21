"""Static fragment-based one-time access page; no mint or legacy-cookie login."""
from __future__ import annotations

import base64
import hashlib
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
import redis
from redis.backoff import NoBackoff
from redis.retry import Retry
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.services.studio_access_grant import StudioAccessError, consume, session_cookie_for
from app.youtube_routes import COOKIE_NAME, _require_same_origin


router = APIRouter()
PATH = '/studio/access'
DESTINATION = '/studio'
MAX_BODY_BYTES = 2048
MAX_TOKEN_CHARS = 256
_COOKIE_MAX_AGE = 30 * 24 * 60 * 60
_SCRIPT = r'''(() => {
  'use strict';
  let grant = window.location.hash.slice(1);
  window.history.replaceState(null, '', '/studio/access');
  const button = document.getElementById('open-studio');
  const message = document.getElementById('access-status');
  if (!/^[A-Za-z0-9_-]{43}$/.test(grant)) {
    grant = '';
    let checking = false;
    async function resume() {
      if (checking) return;
      checking = true;
      button.disabled = true;
      message.textContent = 'Bu cihazdaki oturum kontrol ediliyor…';
      try {
        const response = await fetch('/studio/api/session', {
          method: 'GET', credentials: 'same-origin', redirect: 'error', cache: 'no-store',
          referrerPolicy: 'no-referrer', headers: {'Accept': 'application/json'}
        });
        if (response.ok && (await response.json()).authenticated === true) {
          window.location.replace('/studio');
          return;
        }
        message.textContent = 'Bu cihazda açık oturum bulunamadı. Sana özel gönderilen giriş bağlantısını bu tarayıcıda aç.';
      } catch (_) {
        message.textContent = 'Oturum kontrol edilemedi. Bağlantını kontrol edip tekrar deneyebilirsin.';
      }
      button.textContent = 'Oturumu tekrar kontrol et';
      button.disabled = false;
      checking = false;
    }
    button.addEventListener('click', resume);
    resume();
    return;
  }
  let submitted = false;
  button.addEventListener('click', async () => {
    if (submitted) return;
    submitted = true;
    button.disabled = true;
    const pending = grant;
    grant = '';
    message.textContent = 'Studio açılıyor…';
    try {
      const response = await fetch('/studio/access', {
        method: 'POST', mode: 'cors', credentials: 'same-origin', redirect: 'follow', cache: 'no-store',
        referrerPolicy: 'no-referrer', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({grant: pending})
      });
      const destination = new URL('/studio', window.location.origin).href;
      if (response.ok && response.redirected && response.url === destination) {
        window.location.replace('/studio');
        return;
      }
    } catch (_) {}
    message.textContent = 'Giriş tamamlanamadı. Yeni bir giriş bağlantısı gerekiyor.';
  });
})();'''
_STYLE = '''*{box-sizing:border-box}html{color-scheme:dark;background:#0b0e15;color:#f3f5fa;font-family:system-ui,-apple-system,sans-serif}body{margin:0;min-height:100vh;padding:24px;background:radial-gradient(ellipse at 50% 0,#25203e,transparent 65%)}main{max-width:460px;margin:10vh auto;padding:34px;border:1px solid #353448;border-radius:24px;background:#131822;box-shadow:0 24px 90px #0004}.brand{color:#bbaaff;font-size:12px;letter-spacing:.12em;font-weight:750;margin:0 0 34px}.mark{display:grid;place-items:center;width:52px;height:52px;background:#2b2246;border:1px solid #5b497f;border-radius:15px;color:#c1afff;font-size:24px;margin-bottom:22px}h1{font-size:29px;letter-spacing:-.04em;line-height:1.2;margin:0 0 12px}p{line-height:1.7;color:#b9c2d2;font-size:14px}#access-status{min-height:48px}button,.back{display:block;width:100%;text-align:center;padding:14px 18px;background:#8c75f5;color:#fff;border:0;border-radius:12px;font:inherit;font-weight:650;cursor:pointer;text-decoration:none;margin:22px 0}button:hover,.back:hover{background:#a18bff}button:disabled{opacity:.55;cursor:default}button:focus-visible,a:focus-visible{outline:3px solid #d5caff;outline-offset:4px}.foot{padding-top:18px;margin-top:24px;border-top:1px solid #303342;color:#8f9bb0;font-size:12px}.foot b{color:#b8c3d5}noscript p{color:#f6d19b}@media(max-width:480px){body{padding:16px}main{margin:6vh auto;padding:26px 22px}h1{font-size:27px}}'''

_STYLE += """html{color-scheme:light;background:#f4f6f8;color:#192733}body{background:#f4f6f8}main{background:#fff;border-color:#e1e6eb;box-shadow:0 12px 45px #2534440a}.brand{color:#527e70}.mark{background:#e9f2ee;border-color:#d3e4d9;color:#276759}p{color:#627180}button,.back{background:#276759}button:hover,.back:hover{background:#205547}.foot{border-color:#e1e6eb;color:#73817c}.foot b{color:#405b50}noscript p{color:#946019}"""


def _hash(value):
    return base64.b64encode(hashlib.sha256(value.encode()).digest()).decode()


_HEADERS = {
    'Cache-Control': 'private, no-store', 'Pragma': 'no-cache',
    'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY',
    'X-Content-Type-Options': 'nosniff',
    'Content-Security-Policy': "default-src 'none'; script-src 'sha256-" + _hash(_SCRIPT)
        + "'; style-src 'sha256-" + _hash(_STYLE)
        + "'; connect-src 'self'; form-action 'none'; base-uri 'none'; frame-ancestors 'none'",
}



def _page(*, status_code=200, error=False):
    message = 'Giriş tamamlanamadı. Yeni bir giriş bağlantısı gerekiyor.' if error else 'Devam etmek için aşağıdaki düğmeye dokunun.'
    button = '<a class="back" href="/studio/access">Giriş ekranına dön</a>' if error else '<button id="open-studio" type="button">Studio’yu aç</button>'
    script = '' if error else '<script>' + _SCRIPT + '</script>'
    return HTMLResponse('<!doctype html><html lang="tr"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><title>Studio girişi</title>'
        '<style>' + _STYLE + '</style></head><body><main><p class="brand">YOUTUBE STUDIO</p>'
        '<div class="mark" aria-hidden="true">▶</div><h1>Studio’ya hoş geldin</h1><p>Kanalların, videoların ve otomasyonun burada.</p>'
        '<p id="access-status" role="status">' + message + '</p>' + button
        + '<noscript><p>Studio’ya giriş için tarayıcında JavaScript açık olmalı.</p></noscript>'
        + '<p class="foot"><b>Bir kez giriş yapman yeterli.</b><br>Oturumun bu tarayıcıda 30 gün saklanır. Giriş yaptıktan sonra normal site adresini kullanabilirsin.</p>'
        + '</main>' + script + '</body></html>', status_code=status_code, headers=_HEADERS)


def _redis():
    pool = redis.ConnectionPool.from_url(settings.redis_url)
    pool.connection_kwargs.update(decode_responses=False, retry=Retry(NoBackoff(), 0),
                                  retry_on_timeout=False, retry_on_error=[],
                                  socket_connect_timeout=3, socket_timeout=3)
    return redis.Redis(connection_pool=pool)


def _consume_once(token):
    with _redis() as client:
        return consume(client, token)


async def _read_grant(request):
    if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
        raise HTTPException(status_code=415)
    if request.headers.get('content-encoding', 'identity').lower() != 'identity':
        raise HTTPException(status_code=415)
    lengths = request.headers.getlist('content-length')
    if len(lengths) > 1:
        raise HTTPException(status_code=400)
    length = lengths[0] if lengths else None
    if length is not None and (not length.isascii() or not length.isdigit()
                               or len(length) > 5 or int(length) > MAX_BODY_BYTES):
        raise HTTPException(status_code=413)
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY_BYTES:
            raise HTTPException(status_code=413)
        body.extend(chunk)
    if length is not None and len(body) != int(length):
        raise HTTPException(status_code=400)
    try:
        def pairs(items):
            if len(items) != 1 or items[0][0] != 'grant':
                raise ValueError()
            return dict(items)
        def invalid(_):
            raise ValueError()
        value = json.loads(body, object_pairs_hook=pairs, parse_constant=invalid)
        if (type(value) is not dict or set(value) != {'grant'}
                or type(value['grant']) is not str or not 1 <= len(value['grant']) <= MAX_TOKEN_CHARS):
            raise ValueError()
        return value['grant']
    except Exception:
        raise HTTPException(status_code=422) from None


@router.get(PATH, response_class=HTMLResponse)
async def studio_access_page(request: Request):
    # Fragments never reach the server. GET neither reads nor mints/consumes a grant.
    if request.url.query:
        return _page(status_code=400, error=True)
    return _page()


@router.post(PATH)
async def studio_access_login(request: Request):
    try:
        # A previous Studio cookie grants no authority here. Origin is checked
        # before parsing any body; only the one-time grant can open a session.
        _require_same_origin(request)
        if request.url.query:
            raise HTTPException(status_code=400)
        token = await _read_grant(request)
        receipt = await run_in_threadpool(_consume_once, token)
        cookie = session_cookie_for(receipt)
        response = RedirectResponse(DESTINATION, status_code=303, headers=_HEADERS)
        response.set_cookie(COOKIE_NAME, cookie, max_age=_COOKIE_MAX_AGE, path='/',
                            httponly=True, secure=True, samesite='lax')
        return response
    except HTTPException as error:
        return _page(status_code=error.status_code, error=True)
    except StudioAccessError as error:
        status = 403 if error.code == 'studio_access_invalid' else 503
        return _page(status_code=status, error=True)
    except Exception:
        return _page(status_code=503, error=True)
