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
DESTINATION = '/studio/providers/abacus'
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
    button.disabled = true;
    message.textContent = 'Giriş bağlantısı eksik veya geçersiz.';
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
      const destination = new URL('/studio/providers/abacus', window.location.origin).href;
      if (response.ok && response.redirected && response.url === destination) {
        window.location.replace('/studio/providers/abacus');
        return;
      }
    } catch (_) {}
    message.textContent = 'Giriş tamamlanamadı. Yeni bir giriş bağlantısı gerekiyor.';
  });
})();'''
_STYLE = '''html{color-scheme:dark;background:#10131b;color:#f3f5fa;font-family:system-ui,sans-serif}body{margin:0;padding:24px}main{max-width:480px;margin:12vh auto;padding:28px;border:1px solid #343d50;border-radius:16px;background:#192130}h1{font-size:28px}p{line-height:1.6;color:#c6cedc}button{padding:13px 20px;background:#7e69ef;color:white;border:0;border-radius:10px;font:inherit;cursor:pointer}button:disabled{opacity:.55;cursor:default}button:focus-visible{outline:3px solid #bfb4ff;outline-offset:3px}'''


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
    button = '' if error else '<button id="open-studio" type="button">Studio’yu aç</button>'
    script = '' if error else '<script>' + _SCRIPT + '</script>'
    return HTMLResponse('<!doctype html><html lang="tr"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><title>Studio girişi</title>'
        '<style>' + _STYLE + '</style></head><body><main><h1>Studio’ya güvenli giriş</h1>'
        '<p id="access-status" role="status">' + message + '</p>' + button
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
                            httponly=True, secure=True, samesite='strict')
        return response
    except HTTPException as error:
        return _page(status_code=error.status_code, error=True)
    except StudioAccessError as error:
        status = 403 if error.code == 'studio_access_invalid' else 503
        return _page(status_code=status, error=True)
    except Exception:
        return _page(status_code=503, error=True)
