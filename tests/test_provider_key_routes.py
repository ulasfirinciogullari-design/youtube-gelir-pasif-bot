"""Real HTTP with existing auth/origin functions and disposable encrypted Redis."""
import ast
import asyncio
import importlib.util
import http.client as http_client
import socket
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlencode, urlparse

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient
import fakeredis
import pytest
from redis.exceptions import ConnectionError
from starlette.requests import Request

from app.services import provider_key_candidate as candidate
from test_provider_key_candidate import ACTIVE, ENCRYPTION, KEY, SENTINEL


ROOT = Path(__file__).resolve().parents[1]
PATH = '/studio/providers/abacus'
COOKIE = 'youtube_studio_token'
OWNER = 'offline-owner-studio-session'
BASE = 'https://studio.example.test'


def extracted(path, names, namespace):
    nodes = [node for node in ast.parse(path.read_text()).body
             if isinstance(node, ast.FunctionDef) and node.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)


@pytest.fixture
def web(monkeypatch):
    config = SimpleNamespace(app_encryption_key=ENCRYPTION, abacus_api_key=ACTIVE,
                             factory_api_token=OWNER, google_redirect_uri=BASE + '/studio/youtube/callback',
                             redis_url='redis://unused.invalid/0')
    namespace = dict(settings=config, HTTPException=HTTPException, Request=Request, urlparse=urlparse)
    extracted(ROOT / 'app/youtube_routes.py',
              {'_valid_token', '_require_auth', '_canonical_origin', '_require_same_origin'}, namespace)
    routes_stub = SimpleNamespace(COOKIE_NAME=COOKIE, _require_auth=namespace['_require_auth'],
                                  _require_same_origin=namespace['_require_same_origin'])
    shell = lambda body, **_: HTMLResponse('<!doctype html><html lang="tr"><body>' + body + '</body></html>')
    monkeypatch.setitem(sys.modules, 'app.youtube_routes', routes_stub)
    monkeypatch.setitem(sys.modules, 'app.studio', SimpleNamespace(_shell=shell))
    spec = importlib.util.spec_from_file_location('candidate_routes_isolated', ROOT / 'app/provider_key_routes.py')
    routes = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(routes)
    monkeypatch.setattr(candidate, 'settings', config)
    monkeypatch.setattr(routes, 'settings', config)
    client = fakeredis.FakeRedis()
    real_redis_factory = routes._redis
    forbidden_network = Mock(side_effect=AssertionError('No outbound provider connection'))
    monkeypatch.setattr(socket, 'create_connection', forbidden_network)
    monkeypatch.setattr(http_client, 'HTTPSConnection', forbidden_network)
    redis_factory = Mock(return_value=client)
    monkeypatch.setattr(routes, '_redis', redis_factory)
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app, base_url=BASE) as http:
        yield SimpleNamespace(routes=routes, client=client, http=http, config=config,
                              redis_factory=redis_factory, real_redis_factory=real_redis_factory, app=app)
    forbidden_network.assert_not_called()


def auth(**extra):
    return {'cookie': COOKIE + '=' + OWNER, 'origin': BASE, **extra}


def safe(response, caplog):
    text = response.text + repr(dict(response.headers)) + caplog.text
    for secret in (KEY, ACTIVE, ENCRYPTION, SENTINEL, OWNER):
        assert secret not in text
    assert response.headers['cache-control'] == 'private, no-store'
    assert response.headers['referrer-policy'] == 'same-origin'
    assert response.headers['x-frame-options'] == 'DENY'
    assert response.headers['x-content-type-options'] == 'nosniff'
    csp = response.headers['content-security-policy']
    assert "frame-ancestors 'none'" in csp and "form-action 'self'" in csp
    assert "default-src 'none'" in csp
    assert 'Traceback' not in text and 'input_value' not in text


def test_get_displays_empty_password_form_with_safe_link_and_truthful_copy(web, caplog):
    response = web.http.get(PATH, headers=auth())
    assert response.status_code == 200
    assert 'type="password"' in response.text and 'name="api_key"' in response.text
    assert 'autocomplete="off"' in response.text and 'value=' not in response.text
    assert 'method="post"' in response.text and 'action="' + PATH + '"' in response.text
    assert 'href="https://apps.abacus.ai/chatllm/admin/route-llm-apis"' in response.text
    assert 'target="_blank" rel="noopener noreferrer"' in response.text
    assert 'Erişimi kontrol edilene kadar mevcut üretim ayarları değişmez.' in response.text
    assert web.client.dbsize() == 0
    safe(response, caplog)


def test_post_success_creates_only_encrypted_pending_and_never_displays_key(web, caplog):
    before = vars(web.config).copy()
    response = web.http.post(PATH, headers=auth(), data={'api_key': KEY})
    assert response.status_code == 200
    assert 'Yeni anahtar kaydedildi; erişim kontrolü bekliyor.' in response.text
    assert '<form' not in response.text and 'type="password"' not in response.text
    raw = web.client.get(candidate.CANDIDATE_KEY)
    assert KEY.encode() not in raw and web.client.ttl(candidate.CANDIDATE_KEY) == -1
    assert web.client.dbsize() == 1 and vars(web.config) == before
    assert candidate.read_candidate(web.client).api_key == KEY
    safe(response, caplog)
    get = web.http.get(PATH, headers=auth())
    assert '<form' not in get.text and 'erişim kontrolünü bekliyor' in get.text
    safe(get, caplog)


@pytest.mark.parametrize('headers,status', [
    ({}, 401), ({'cookie': COOKIE + '=wrong', 'origin': BASE}, 401),
    ({'cookie': COOKIE + '=' + OWNER}, 403),
    ({'cookie': COOKIE + '=' + OWNER, 'origin': 'https://evil.example.test'}, 403),
    ({'cookie': COOKIE + '=' + OWNER, 'referer': 'https://evil.example.test/'}, 403),
])
def test_post_authorization_and_csrf_fail_before_body_or_redis(web, caplog, headers, status):
    response = web.http.post(PATH, headers=headers, content=b'api_key=' + KEY.encode())
    assert response.status_code == status
    web.redis_factory.assert_not_called()
    assert web.client.dbsize() == 0
    safe(response, caplog)


def test_get_requires_current_cookie_on_every_view(web, caplog):
    assert web.http.get(PATH, headers=auth()).status_code == 200
    calls = web.redis_factory.call_count
    response = web.http.get(PATH)
    assert response.status_code == 401 and web.redis_factory.call_count == calls
    safe(response, caplog)


def test_same_origin_referer_is_accepted_without_origin(web, caplog):
    response = web.http.post(PATH, headers={'cookie': COOKIE + '=' + OWNER, 'referer': BASE + PATH},
                             data={'api_key': KEY})
    assert response.status_code == 200
    safe(response, caplog)


@pytest.mark.parametrize('key,message', [
    (ACTIVE, 'Bu anahtar zaten kayıtlı.'), ('masked-placeholder-key', 'kontrol edin'),
    ('\"' + KEY + '\"', 'kontrol edin'), ("'" + KEY + "'", 'kontrol edin'),
    (KEY + ' ', 'kontrol edin'), ('', 'kontrol edin'),
])
def test_invalid_candidate_returns_empty_form_without_echo(web, caplog, key, message):
    response = web.http.post(PATH, headers=auth(), data={'api_key': key})
    assert response.status_code == 422 and message in response.text
    assert '<form' in response.text and 'value=' not in response.text
    assert web.client.dbsize() == 0
    safe(response, caplog)


@pytest.mark.parametrize('body', [
    b'', b'api_key', b'other=unused', b'api_key=a&api_key=b', b'api_key=a&other=b',
    b'api_key=%ZZ', b'api_key=%ff', b'api_key=\xff', b'api_key=' + b'x' * 17000,
], ids=['empty', 'missing-equals', 'unknown-field', 'duplicate-field', 'extra-field',
         'bad-percent', 'bad-utf8', 'non-ascii', 'oversized'])
def test_bounded_single_field_form_rejects_malformed_duplicate_or_large_body(web, caplog, body):
    response = web.http.post(PATH, headers=auth(**{'content-type': 'application/x-www-form-urlencoded'}),
                             content=body)
    assert response.status_code in (413, 422)
    assert '<form' in response.text and 'value=' not in response.text
    assert web.client.dbsize() == 0
    safe(response, caplog)


@pytest.mark.parametrize('headers', [
    {'content-type': 'application/json'}, {'content-type': 'multipart/form-data; boundary=a'},
    {'content-type': 'application/x-www-form-urlencoded', 'content-encoding': 'gzip'},
    {'content-type': 'application/x-www-form-urlencoded', 'content-length': '-1'},
    {'content-type': 'application/x-www-form-urlencoded', 'content-length': '9999999'},
])
def test_unsupported_content_or_size_is_rejected_without_secret_echo(web, caplog, headers):
    response = web.http.post(PATH, headers=auth(**headers), content=('api_key=' + KEY).encode())
    assert response.status_code in (413, 415)
    assert '<form' in response.text and web.client.dbsize() == 0
    safe(response, caplog)


def test_pending_blocks_second_submit_without_reading_or_replacing_it(web, caplog):
    candidate.stage_candidate(web.client, KEY)
    raw = web.client.get(candidate.CANDIDATE_KEY)
    response = web.http.post(PATH, headers=auth(), data={'api_key': 'another-valid-offline-key-987654'})
    assert response.status_code == 409 and '<form' not in response.text
    assert web.client.get(candidate.CANDIDATE_KEY) == raw
    safe(response, caplog)


def test_lost_write_ack_never_claims_success_or_offers_second_submit(web, monkeypatch, caplog):
    original = web.client.set
    calls = []
    def lost(*args, **kwargs):
        calls.append((args[0], kwargs))
        original(*args, **kwargs)
        raise ConnectionError(KEY + SENTINEL)
    monkeypatch.setattr(web.client, 'set', lost)
    response = web.http.post(PATH, headers=auth(), data={'api_key': KEY})
    assert response.status_code == 503 and '<form' not in response.text
    assert 'Yeni anahtar kaydedildi;' not in response.text
    safe(response, caplog)
    raw = web.client.get(candidate.CANDIDATE_KEY)
    assert raw and len(calls) == 1
    second = web.http.post(PATH, headers=auth(), data={'api_key': KEY})
    assert second.status_code == 409 and len(calls) == 1
    assert web.client.get(candidate.CANDIDATE_KEY) == raw
    safe(second, caplog)


def test_unknown_backend_exception_has_fixed_response_and_no_empty_retry_form(web, monkeypatch, caplog):
    monkeypatch.setattr(web.routes, 'candidate_status', Mock(side_effect=RuntimeError(KEY + SENTINEL)))
    response = web.http.get(PATH, headers=auth())
    assert response.status_code == 503 and '<form' not in response.text
    safe(response, caplog)


def test_non_receipt_stage_return_cannot_claim_success(web, monkeypatch, caplog):
    monkeypatch.setattr(web.routes, 'stage_candidate', lambda *_: None)
    response = web.http.post(PATH, headers=auth(), data={'api_key': KEY})
    assert response.status_code == 503 and 'Yeni anahtar kaydedildi;' not in response.text
    safe(response, caplog)


def request_stream(headers, chunks, *, method='POST', query=b''):
    consumed = []
    iterator = iter(chunks)
    async def receive():
        chunk = next(iterator)
        consumed.append(chunk)
        return {'type': 'http.request', 'body': chunk, 'more_body': True}
    scope = {'type': 'http', 'method': method, 'scheme': 'https', 'path': PATH,
             'raw_path': PATH.encode(), 'query_string': query, 'server': ('studio.example.test', 443),
             'client': ('test', 1), 'headers': [(k.lower().encode(), v.encode()) for k, v in headers.items()]}
    return Request(scope, receive), consumed


@pytest.mark.parametrize('gate', ['auth', 'origin', 'pending', 'length'])
def test_rejected_preconditions_do_not_consume_a_body_chunk(web, gate):
    headers = auth(**{'content-type': 'application/x-www-form-urlencoded'})
    if gate == 'auth':
        headers.pop('cookie')
    elif gate == 'origin':
        headers['origin'] = 'https://evil.example.test'
    elif gate == 'pending':
        candidate.stage_candidate(web.client, KEY)
    else:
        headers['content-length'] = '9999999'
    request, consumed = request_stream(headers, [b'api_key=' + KEY.encode()])
    response = asyncio.run(web.routes.abacus_key_stage(request))
    assert response.status_code in (401, 403, 409, 413)
    assert consumed == []


def test_chunked_body_limit_stops_before_consuming_remaining_secret_chunks(web):
    request, consumed = request_stream(auth(**{'content-type': 'application/x-www-form-urlencoded'}),
                                       [b'x' * 10000, b'y' * 10000, KEY.encode()])
    response = asyncio.run(web.routes.abacus_key_stage(request))
    assert response.status_code == 413 and len(consumed) == 2
    assert web.client.dbsize() == 0


def test_queries_are_not_a_key_input_channel(web, caplog):
    response = web.http.post(PATH + '?unexpected=1', headers=auth(), data={'api_key': KEY})
    assert response.status_code == 400
    web.redis_factory.assert_not_called()
    safe(response, caplog)


def test_form_has_no_fastapi_body_fields_or_secret_response_schema(web):
    for route in web.app.routes:
        if getattr(route, 'path', None) == PATH:
            assert route.dependant.body_params == []
    schema = web.app.openapi()
    encoded = repr(schema['paths'][PATH])
    assert 'requestBody' not in encoded and 'api_key' not in encoded


def test_redis_factory_disables_retries_even_when_url_requests_them(web):
    web.config.redis_url = 'redis://unused.invalid/0?retry_on_timeout=True&socket_timeout=120&decode_responses=True'
    client = web.real_redis_factory()
    try:
        options = client.connection_pool.connection_kwargs
        assert options['retry']._retries == 0
        assert options['retry_on_timeout'] is False and options['retry_on_error'] == []
        assert options['socket_timeout'] == options['socket_connect_timeout'] == 3
        assert options['decode_responses'] is False
    finally:
        client.close()
