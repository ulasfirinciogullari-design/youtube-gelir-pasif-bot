"""Real HTTP/Redis and JavaScript VM checks for explicit one-time Studio login."""
import ast
import asyncio
import base64
import hashlib
import http.client as http_client
import importlib.util
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import fakeredis
import pytest
from redis.exceptions import ConnectionError
from starlette.requests import Request

from app.services import studio_access_grant as access
from test_studio_access_grant import ENCRYPTION, FACTORY, OLD_COOKIE, TOKEN, SENTINEL, NOW, key


ROOT = Path(__file__).resolve().parents[1]
BASE = 'https://studio.example.test'
PATH = '/studio/access'
COOKIE = 'youtube_studio_token'


@pytest.fixture
def web(monkeypatch):
    config = SimpleNamespace(app_encryption_key=ENCRYPTION, factory_api_token=FACTORY,
                             redis_url='redis://unused.invalid/0',
                             google_redirect_uri=BASE + '/studio/youtube/callback')
    namespace = dict(settings=config, HTTPException=HTTPException, Request=Request, urlparse=urlparse)
    nodes = [node for node in ast.parse((ROOT / 'app/youtube_routes.py').read_text()).body
             if isinstance(node, ast.FunctionDef) and node.name in {'_canonical_origin', '_require_same_origin'}]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'existing_origin_guard', 'exec'), namespace)
    monkeypatch.setitem(sys.modules, 'app.youtube_routes', SimpleNamespace(
        COOKIE_NAME=COOKIE, _require_same_origin=namespace['_require_same_origin']))
    spec = importlib.util.spec_from_file_location('isolated_access_routes', ROOT / 'app/studio_access_routes.py')
    routes = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(routes)
    monkeypatch.setattr(routes, 'settings', config)
    monkeypatch.setattr(access, 'settings', config)
    clock = SimpleNamespace(now=NOW)
    monkeypatch.setattr(access, '_now_ms', lambda: clock.now)
    client = fakeredis.FakeRedis()
    real_redis = routes._redis
    redis_factory = Mock(return_value=client)
    monkeypatch.setattr(routes, '_redis', redis_factory)
    network = Mock(side_effect=AssertionError('No outbound provider connections'))
    monkeypatch.setattr(socket, 'create_connection', network)
    monkeypatch.setattr(http_client, 'HTTPSConnection', network)
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app, base_url=BASE) as http:
        yield SimpleNamespace(routes=routes, client=client, config=config, clock=clock,
                              redis_factory=redis_factory, real_redis=real_redis, app=app, http=http)
    network.assert_not_called()


def safe(response, caplog, grant=None, *, cookie=False):
    visible = response.text + caplog.text + repr({k: v for k, v in response.headers.items() if k.lower() != 'set-cookie'})
    for secret in (FACTORY, ENCRYPTION, TOKEN, OLD_COOKIE, SENTINEL, grant or TOKEN):
        assert secret not in visible
    assert response.headers['cache-control'] == 'private, no-store'
    assert response.headers['referrer-policy'] == 'no-referrer'
    assert response.headers['x-frame-options'] == 'DENY'
    assert response.headers['x-content-type-options'] == 'nosniff'
    csp = response.headers['content-security-policy']
    assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp
    assert "connect-src 'self'" in csp and "form-action 'none'" in csp
    assert 'unsafe-inline' not in csp and 'unsafe-eval' not in csp
    if not cookie:
        assert 'set-cookie' not in response.headers


def mint(web, **kwargs):
    return access.mint(web.client, **kwargs)


def test_get_is_generic_static_and_neither_reads_redis_nor_consumes_grant(web, caplog):
    grant = mint(web)
    original = web.client.get(key(grant.token))
    response = web.http.get(PATH + '#' + grant.token)
    assert response.status_code == 200
    assert response.content == web.routes._page().body
    assert 'id="open-studio"' in response.text and 'autofocus' not in response.text
    assert 'type="button"' in response.text and '<form' not in response.text
    web.redis_factory.assert_not_called()
    assert web.client.get(key(grant.token)) == original
    safe(response, caplog, grant.token)


def test_static_script_and_style_hashes_match_the_actual_inline_bytes(web):
    response = web.http.get(PATH)
    for name in ('_SCRIPT', '_STYLE'):
        text = getattr(web.routes, name)
        digest = base64.b64encode(hashlib.sha256(text.encode()).digest()).decode()
        assert "'sha256-" + digest + "'" in response.headers['content-security-policy']
        assert text in response.text
    assert "mode: 'cors'" in web.routes._SCRIPT
    assert "referrerPolicy: 'no-referrer'" in web.routes._SCRIPT


def test_valid_grant_sets_only_strong_secure_cookie_after_delete_ack_and_fixed_303(web, caplog):
    grant = mint(web)
    before = vars(web.config).copy()
    response = web.http.post(PATH, headers={'origin': BASE}, json={'grant': grant.token}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers['location'] == '/studio/providers/abacus'
    assert response.text == ''
    header = response.headers['set-cookie']
    assert COOKIE + '=' + FACTORY in header
    for flag in ('HttpOnly', 'Secure', 'SameSite=strict', 'Path=/', 'Max-Age=2592000'):
        assert flag in header
    assert web.client.exists(key(grant.token)) == 0 and vars(web.config) == before
    safe(response, caplog, grant.token, cookie=True)
    replay = web.http.post(PATH, headers={'origin': BASE}, json={'grant': grant.token})
    assert replay.status_code == 403
    safe(replay, caplog, grant.token)


@pytest.mark.parametrize('cookie', [OLD_COOKIE, FACTORY, ''])
def test_cookie_alone_cannot_login_or_mint(web, caplog, cookie):
    headers = {'origin': BASE, 'cookie': COOKIE + '=' + cookie}
    response = web.http.post(PATH, headers=headers, json={})
    assert response.status_code == 422
    assert web.client.dbsize() == 0
    safe(response, caplog)
    assert web.http.post(PATH + '/mint', headers=headers).status_code == 404
    assert web.http.get(PATH + '/mint', headers=headers).status_code == 404


@pytest.mark.parametrize('headers', [
    {}, {'origin': 'https://evil.example.test'}, {'origin': 'null'},
    {'referer': 'https://evil.example.test/'},
    {'cookie': COOKIE + '=' + OLD_COOKIE, 'origin': 'https://evil.example.test'},
])
def test_bad_origin_rejected_before_body_or_redis_even_with_old_cookie(web, caplog, headers):
    response = web.http.post(PATH, headers=headers, content=b'not-json')
    assert response.status_code == 403
    web.redis_factory.assert_not_called()
    safe(response, caplog)


def test_existing_same_origin_referrer_guard_remains_available(web, caplog):
    grant = mint(web)
    response = web.http.post(PATH, headers={'referer': BASE + PATH}, json={'grant': grant.token}, follow_redirects=False)
    assert response.status_code == 303
    safe(response, caplog, grant.token, cookie=True)


@pytest.mark.parametrize('body', [b'', b'null', b'[]', b'{}', b'not-json', b'{"grant":1}',
    b'{"grant":null}', b'{"grant":true}', b'{"grant":{}}', b'{"grant":""}',
    b'{"grant":"a","grant":"b"}', b'{"grant":"a","extra":1}',
    b'{"grant":NaN}', b'{"grant":"' + b'a' * 257 + b'"}', b'x' * 2049,
], ids=['empty', 'null', 'list', 'empty-object', 'bad-json', 'number', 'null-grant',
        'bool', 'object', 'empty-grant', 'duplicate', 'extra', 'nan', 'long-grant', 'large-body'])
def test_bounded_manual_json_rejects_without_framework_or_secret_echo(web, caplog, body):
    response = web.http.post(PATH, headers={'origin': BASE, 'content-type': 'application/json'}, content=body)
    assert response.status_code in (413, 422)
    web.redis_factory.assert_not_called()
    safe(response, caplog)


@pytest.mark.parametrize('headers', [
    {'content-type': 'application/x-www-form-urlencoded'}, {'content-type': 'multipart/form-data'},
    {'content-type': 'application/json', 'content-encoding': 'gzip'},
    {'content-type': 'application/json', 'content-length': '-1'},
    {'content-type': 'application/json', 'content-length': '999999'},
])
def test_bad_content_type_encoding_or_length_never_reaches_redis(web, caplog, headers):
    response = web.http.post(PATH, headers={'origin': BASE, **headers}, content=b'{}')
    assert response.status_code in (413, 415)
    web.redis_factory.assert_not_called()
    safe(response, caplog)


def test_query_is_never_an_access_input_channel(web, caplog):
    response = web.http.get(PATH + '?grant=invalid')
    assert response.status_code == 400 and '<script>' not in response.text
    safe(response, caplog)
    response = web.http.post(PATH + '?grant=invalid', headers={'origin': BASE}, json={'grant': TOKEN})
    assert response.status_code == 400
    web.redis_factory.assert_not_called()
    safe(response, caplog)


def test_expired_or_rotated_context_never_returns_cookie(web, caplog):
    grant = mint(web, ttl_seconds=86400)
    web.clock.now = grant.expires_at_ms
    response = web.http.post(PATH, headers={'origin': BASE}, json={'grant': grant.token})
    assert response.status_code == 403
    safe(response, caplog, grant.token)
    web.clock.now = NOW
    web.config.factory_api_token = FACTORY + '-rotated'
    response = web.http.post(PATH, headers={'origin': BASE}, json={'grant': grant.token})
    assert response.status_code == 403
    safe(response, caplog, grant.token)


def test_weak_current_factory_token_cannot_open_session(web, caplog):
    grant = mint(web)
    web.config.factory_api_token = OLD_COOKIE
    response = web.http.post(PATH, headers={'origin': BASE}, json={'grant': grant.token})
    assert response.status_code == 503 and web.client.exists(key(grant.token)) == 1
    safe(response, caplog, grant.token)


def test_lost_delete_ack_never_sets_cookie_and_replay_cannot_recover_it(web, monkeypatch, caplog):
    grant = mint(web)
    pipeline = web.client.pipeline
    executes = []
    def intercepted():
        pipe = pipeline()
        execute = pipe.execute
        def lost(*args, **kwargs):
            executes.append(1)
            execute(*args, **kwargs)
            raise ConnectionError(SENTINEL + FACTORY)
        pipe.execute = lost
        return pipe
    monkeypatch.setattr(web.client, 'pipeline', intercepted)
    response = web.http.post(PATH, headers={'origin': BASE}, json={'grant': grant.token})
    assert response.status_code == 503 and executes == [1]
    assert web.client.exists(key(grant.token)) == 0
    safe(response, caplog, grant.token)
    response = web.http.post(PATH, headers={'origin': BASE}, json={'grant': grant.token})
    assert response.status_code == 403 and executes == [1]
    safe(response, caplog, grant.token)


def test_context_change_between_consume_and_set_cookie_fails_closed(web, monkeypatch, caplog):
    grant = mint(web)
    real = web.routes._consume_once
    def consume_then_rotate(token):
        receipt = real(token)
        web.config.factory_api_token = FACTORY + '-rotated'
        return receipt
    monkeypatch.setattr(web.routes, '_consume_once', consume_then_rotate)
    response = web.http.post(PATH, headers={'origin': BASE}, json={'grant': grant.token})
    assert response.status_code == 503 and web.client.exists(key(grant.token)) == 0
    safe(response, caplog, grant.token)


def streamed_request(headers, chunks):
    consumed = []
    iterator = iter(chunks)
    async def receive():
        chunk = next(iterator)
        consumed.append(chunk)
        return {'type': 'http.request', 'body': chunk, 'more_body': True}
    scope = {'type': 'http', 'method': 'POST', 'scheme': 'https', 'path': PATH,
             'raw_path': PATH.encode(), 'query_string': b'', 'server': ('studio.example.test', 443),
             'client': ('test', 1), 'headers': [(k.lower().encode(), v.encode()) for k, v in headers.items()]}
    return Request(scope, receive), consumed


def test_origin_rejection_does_not_consume_even_one_body_chunk(web):
    request, consumed = streamed_request({'origin': 'https://evil.example.test', 'content-type': 'application/json'},
                                         [json.dumps({'grant': TOKEN}).encode()])
    response = asyncio.run(web.routes.studio_access_login(request))
    assert response.status_code == 403 and consumed == []
    web.redis_factory.assert_not_called()


def test_stream_limit_stops_before_reading_later_token_chunk(web):
    request, consumed = streamed_request({'origin': BASE, 'content-type': 'application/json'},
                                         [b'a' * 1024, b'b' * 1025, TOKEN.encode()])
    response = asyncio.run(web.routes.studio_access_login(request))
    assert response.status_code == 413 and len(consumed) == 2
    web.redis_factory.assert_not_called()


def test_redis_client_never_retries_even_with_url_overrides(web):
    web.config.redis_url = 'redis://unused.invalid/0?retry_on_timeout=True&socket_timeout=120'
    client = web.real_redis()
    try:
        options = client.connection_pool.connection_kwargs
        assert options['retry']._retries == 0 and options['retry_on_timeout'] is False
        assert options['retry_on_error'] == [] and options['socket_timeout'] == 3
    finally:
        client.close()


def test_no_mint_route_or_fastapi_body_validation_schema(web):
    paths = web.app.openapi()['paths']
    assert set(paths) == {PATH}
    assert set(paths[PATH]) == {'get', 'post'}
    assert 'requestBody' not in repr(paths) and 'mint' not in repr(paths)
    assert 'grant' not in repr(paths)


NODE = shutil.which('node')
@pytest.mark.skipif(NODE is None, reason='JavaScript runtime unavailable')
@pytest.mark.parametrize('outcome', ['success', 'error', 'network', 'wrong-url', 'no-redirect', 'no-fragment'])
def test_browser_script_clears_fragment_then_sends_only_once_on_click(web, outcome):
    harness = r'''
const fs = require('fs');
const vm = require('vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const events = [], calls = [];
let callback;
const button = {disabled:false, addEventListener:(name, fn)=>{events.push(name); callback=fn;}};
const message = {textContent:''};
const location = {hash:input.outcome==='no-fragment'?'':'#'+input.token, origin:input.base,
                  replace:path=>events.push('navigate:'+path)};
const context = {URL, window:{location, history:{replaceState:(_a,_b,path)=>{
    events.push('clear:'+path); location.hash='';}}},
  document:{getElementById:id=>id==='open-studio'?button:message},
  fetch:async(path, options)=>{
    events.push('fetch'); calls.push({path,options});
    if(input.outcome==='network') throw new Error('never displayed');
    return {ok:input.outcome!=='error',redirected:input.outcome!=='no-redirect',
      url:input.outcome==='wrong-url'?'https://evil.invalid/':input.base+'/studio/providers/abacus'};
  }};
vm.runInNewContext(input.script, context);
const beforeClick=calls.length;
(async()=>{
  if(callback){await callback(); await callback();}
  const first=calls[0];
  process.stdout.write(JSON.stringify({beforeClick, events, calls:calls.length, disabled:button.disabled,
    validBody:first?first.options.body===JSON.stringify({grant:input.token}):null,
    path:first?.path, mode:first?.options.mode, method:first?.options.method,
    credentials:first?.options.credentials, redirect:first?.options.redirect,
    fragmentCleared:location.hash==='', reflected:message.textContent.includes(input.token)}));
})().catch(()=>process.exit(1));
'''
    result = subprocess.run([NODE, '-e', harness], input=json.dumps({
        'script': web.routes._SCRIPT, 'token': TOKEN, 'base': BASE, 'outcome': outcome}),
        text=True, capture_output=True, timeout=10, check=True)
    value = json.loads(result.stdout)
    assert value['beforeClick'] == 0 and value['fragmentCleared'] is True
    assert value['events'][0] == 'clear:/studio/access' and value['reflected'] is False
    assert value['disabled'] is True
    if outcome == 'no-fragment':
        assert value['calls'] == 0
    else:
        assert value['calls'] == 1 and value['validBody'] is True
        assert value['path'] == PATH and value['method'] == 'POST' and value['mode'] == 'cors'
        assert value['credentials'] == 'same-origin' and value['redirect'] == 'follow'
    assert ('navigate:/studio/providers/abacus' in value['events']) == (outcome == 'success')
