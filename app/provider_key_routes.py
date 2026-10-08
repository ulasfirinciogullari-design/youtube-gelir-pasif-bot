"""Authenticated Studio password form for encrypted, unverified key staging."""
from __future__ import annotations

import re
from urllib.parse import parse_qsl

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
import redis
from redis.backoff import NoBackoff
from redis.retry import Retry
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.services.provider_key_candidate import (
    CandidateStatus, ProviderKeyCandidateError, candidate_status, stage_candidate,
)
from app.studio import _shell
from app.youtube_routes import COOKIE_NAME, _require_auth, _require_same_origin


router = APIRouter()
PATH = '/studio/providers/abacus'
MAX_FORM_BYTES = 16384
_HEADERS = {
    'Cache-Control': 'private, no-store',
    'Pragma': 'no-cache',
    'Referrer-Policy': 'same-origin',
    'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
    'X-Frame-Options': 'DENY',
    'X-Content-Type-Options': 'nosniff',
}
_SUCCESS = 'Yeni anahtar kaydedildi; erişim kontrolü bekliyor.'
_PENDING = 'Yeni anahtarınız erişim kontrolünü bekliyor.'
_UNAVAILABLE = 'Anahtarın kayıt durumu şu anda kontrol edilemiyor. Lütfen daha sonra bu sayfayı açın.'


def _redis():
    # Configure before first connection. URL query options cannot enable a retry
    # of a SET whose acknowledgement may already have been lost.
    pool = redis.ConnectionPool.from_url(settings.redis_url)
    pool.connection_kwargs.update(decode_responses=False, retry=Retry(NoBackoff(), 0),
                                  retry_on_timeout=False, retry_on_error=[],
                                  socket_connect_timeout=3, socket_timeout=3)
    return redis.Redis(connection_pool=pool)


def _operation(operation, *args):
    with _redis() as client:
        return operation(client, *args)


def _page(message='', *, form=False, status_code=200):
    # Every interpolated value is fixed local text; keys and backend messages
    # never enter the page, attributes, redirects, query strings, or scripts.
    body = '''<div class="hero"><div class="hero-copy"><h1>Abacus anahtarı</h1>
<p class="muted">Yeni RouteLLM API anahtarınızı güvenle kaydedin. Erişimi kontrol edilene kadar mevcut üretim ayarları değişmez.</p></div></div>'''
    if message:
        body += '<div class="notice" role="status">' + message + '</div>'
    if form:
        body += '''<section class="card"><form method="post" action="/studio/providers/abacus" autocomplete="off">
<label class="field" for="abacus-key">Yeni API anahtarı</label>
<input id="abacus-key" type="password" name="api_key" required minlength="16" maxlength="4096" autocomplete="off" autocapitalize="none" spellcheck="false">
<p class="muted">Anahtarınızı Abacus hesabınızdaki <a href="https://apps.abacus.ai/chatllm/admin/route-llm-apis" target="_blank" rel="noopener noreferrer">RouteLLM API sayfasından</a> alabilirsiniz.</p>
<button type="submit">Anahtarı güvenle kaydet</button></form></section>'''
    body += '<p><a class="btn secondary" href="/studio">Studio’ya dön</a></p>'
    response = _shell(body, active='providers', title='Abacus anahtarı')
    response.status_code = status_code
    response.headers.update(_HEADERS)
    return response


def _authorize(request, *, write=False):
    _require_auth(request.cookies.get(COOKIE_NAME))
    if write:
        _require_same_origin(request)
    if request.url.query:
        raise HTTPException(status_code=400, detail='provider_key_query_rejected')


def _authorization_failure(error):
    status = error.status_code if error.status_code in (400, 401, 403, 503) else 403
    message = ('Oturumun bu tarayıcıda açık değil. <a href="/studio/access">Studio’ya giriş yap</a>.'
               if status == 401 else 'Bu istek kabul edilmedi. Studio sayfasından devam edin.')
    return _page(message, status_code=status)


async def _form_key(request):
    if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/x-www-form-urlencoded':
        raise HTTPException(status_code=415, detail='provider_key_form_required')
    if request.headers.get('content-encoding', 'identity').lower() != 'identity':
        raise HTTPException(status_code=415, detail='provider_key_encoding_rejected')
    lengths = request.headers.getlist('content-length')
    if len(lengths) > 1:
        raise HTTPException(status_code=400, detail='provider_key_length_invalid')
    length = lengths[0] if lengths else None
    if length is not None and (not length.isascii() or not length.isdigit()
                               or len(length) > 6 or int(length) > MAX_FORM_BYTES):
        raise HTTPException(status_code=413, detail='provider_key_form_too_large')
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_FORM_BYTES:
            raise HTTPException(status_code=413, detail='provider_key_form_too_large')
        body.extend(chunk)
    if length is not None and len(body) != int(length):
        raise HTTPException(status_code=400, detail='provider_key_length_invalid')
    try:
        encoded = body.decode('ascii')
        if re.search(r'%(?![0-9a-fA-F]{2})', encoded):
            raise ValueError()
        fields = parse_qsl(encoded, keep_blank_values=True, strict_parsing=True,
                           encoding='utf-8', errors='strict', max_num_fields=1)
        if len(fields) != 1 or fields[0][0] != 'api_key':
            raise ValueError()
        return fields[0][1]
    except Exception:
        raise HTTPException(status_code=422, detail='provider_key_form_invalid') from None


@router.get(PATH, response_class=HTMLResponse)
async def abacus_key_page(request: Request):
    try:
        _authorize(request)
    except HTTPException as error:
        return _authorization_failure(error)
    try:
        state = await run_in_threadpool(_operation, candidate_status)
        return _page(_PENDING if state.present else '', form=not state.present)
    except Exception:
        return _page(_UNAVAILABLE, status_code=503)


@router.post(PATH, response_class=HTMLResponse)
async def abacus_key_stage(request: Request):
    try:
        _authorize(request, write=True)
    except HTTPException as error:
        return _authorization_failure(error)
    try:
        state = await run_in_threadpool(_operation, candidate_status)
        if state.present:
            return _page(_PENDING, status_code=409)
        key = await _form_key(request)
        result = await run_in_threadpool(_operation, stage_candidate, key)
        if type(result) is not CandidateStatus or result != CandidateStatus(True, 'pending'):
            raise ProviderKeyCandidateError('candidate_unavailable')
        return _page(_SUCCESS)
    except HTTPException as error:
        return _page('Anahtar girişi kabul edilmedi. RouteLLM API anahtarınızı kontrol edin.',
                     form=True, status_code=error.status_code)
    except ProviderKeyCandidateError as error:
        if error.code == 'candidate_pending':
            return _page(_PENDING, status_code=409)
        if error.code == 'candidate_same_active':
            return _page('Bu anahtar zaten kayıtlı. Abacus’tan yeni bir RouteLLM API anahtarı alın.',
                         form=True, status_code=422)
        if error.code == 'candidate_invalid':
            return _page('Yeni RouteLLM API anahtarınızı kontrol edin.', form=True, status_code=422)
        return _page(_UNAVAILABLE, status_code=503)
    except Exception:
        return _page(_UNAVAILABLE, status_code=503)
