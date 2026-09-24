"""Real owner form and password verification, including cross-origin requests."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from app.services import studio_password as password
from test_provider_key_routes import web as base_web, auth, OWNER, COOKIE

SECRET = 'private-test-password-2026'


@pytest.fixture
def web(base_web, monkeypatch):
    # The production token is strong; the shared HTTP fixture uses a short label.
    monkeypatch.setattr(password, 'settings', base_web.config)
    monkeypatch.setattr(password, '_binding', lambda: 'a' * 64)
    monkeypatch.setitem(sys.modules, 'app.provider_key_routes', base_web.routes)
    monkeypatch.setitem(sys.modules, 'app.studio_access_routes', SimpleNamespace(_STYLE='body{margin:0}'))
    path = Path(__file__).resolve().parents[1] / 'app/studio_settings_routes.py'
    spec = importlib.util.spec_from_file_location('password_routes_isolated', path)
    routes = importlib.util.module_from_spec(spec); spec.loader.exec_module(routes)
    base_web.app.include_router(routes.router)
    return base_web


def test_owner_sets_password_then_new_browser_logs_in_without_a_grant(web, caplog):
    response = web.http.post('/studio/settings/access', headers=auth(), data={'password': SECRET})
    assert response.status_code == 200 and 'Şifren kaydedildi' in response.text
    assert SECRET.encode() not in web.client.get(password.KEY)
    assert password.configured(web.client) is True
    web.http.cookies.clear()
    response = web.http.post('/studio/login', headers={'origin': 'https://studio.example.test'},
        data={'password': SECRET}, follow_redirects=False)
    assert response.status_code == 303 and response.headers['location'] == '/studio'
    cookie = response.headers['set-cookie']
    assert 'HttpOnly' in cookie and 'Secure' in cookie and 'SameSite=lax' in cookie
    assert 'Max-Age=7776000' in cookie
    assert SECRET not in response.text + caplog.text


@pytest.mark.parametrize('path', ['/studio/login', '/studio/settings/access'])
def test_cross_origin_post_is_rejected_before_password_work(web, path):
    r = web.http.post(path, headers=auth(origin='https://evil.invalid'), data={'password': SECRET})
    assert r.status_code == 403 and web.client.dbsize() == 0


def test_password_setup_requires_existing_owner_session(web):
    r = web.http.post('/studio/settings/access', headers={'origin': 'https://studio.example.test'}, data={'password': SECRET})
    assert r.status_code == 401 and web.client.dbsize() == 0


def test_wrong_password_is_rate_limited_without_revealing_secret(web, caplog):
    password.save(web.client, SECRET)
    for _ in range(20):
        with pytest.raises(password.PasswordError, match='incorrect'):
            password.login(web.client, 'a-wrong-password')
    with pytest.raises(password.PasswordError, match='rate_limited'):
        password.login(web.client, SECRET)
    assert SECRET not in caplog.text
    assert 0 < web.client.ttl(password.LIMIT) <= 300


@pytest.mark.parametrize('secret', ['short', 'a' * 129, 'line\nbreak-long'])
def test_bad_password_never_changes_an_existing_password(web, secret):
    password.save(web.client, SECRET)
    before = web.client.get(password.KEY)
    r = web.http.post('/studio/settings/access', headers=auth(), data={'password': secret})
    assert r.status_code == 422 and web.client.get(password.KEY) == before
    assert secret not in r.text


def test_public_form_has_no_secret_and_unconfigured_login_does_not_bootstrap(web):
    r = web.http.get('/studio/login')
    assert r.status_code == 200 and 'autocomplete="current-password"' in r.text
    r = web.http.post('/studio/login', headers={'origin': 'https://studio.example.test'}, data={'password': SECRET})
    assert r.status_code == 401 and not web.client.exists(password.KEY)
    assert SECRET not in r.text


def test_authenticated_settings_has_direct_provider_links(web):
    r = web.http.get('/studio/settings', headers=auth())
    assert r.status_code == 200 and '/studio/providers/kie' in r.text
    assert '/voice-audition' in r.text
    assert '/studio/settings/access' in r.text and 'özel giriş bağlantısına gerek kalmadan' in r.text
