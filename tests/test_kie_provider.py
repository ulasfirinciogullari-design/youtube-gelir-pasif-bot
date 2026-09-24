"""The actual owner form stores one secret without spending or exposing it."""
import importlib.util
from pathlib import Path
import sys

import pytest

from app.services import kie_credentials as credentials
from app.services.provider_key_candidate import ProviderKeyCandidateError
from test_provider_key_routes import web as base_web, auth, safe
from test_provider_key_candidate import KEY, ENCRYPTION

PATH = '/studio/providers/kie'


@pytest.fixture
def web(base_web, monkeypatch):
    monkeypatch.setattr(credentials, 'settings', base_web.config)
    monkeypatch.setitem(sys.modules, 'app.provider_key_routes', base_web.routes)
    source = Path(__file__).resolve().parents[1] / 'app/kie_provider_routes.py'
    spec = importlib.util.spec_from_file_location('kie_routes_isolated', source)
    routes = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(routes)
    base_web.app.include_router(routes.router)
    return base_web


def test_owner_can_save_once_and_all_responses_hide_the_secret(web, caplog):
    page = web.http.get(PATH, headers=auth())
    assert page.status_code == 200 and 'type="password"' in page.text
    assert 'otomatik üretimi başlatmaz' in page.text
    safe(page, caplog)
    response = web.http.post(PATH, headers=auth(), data={'api_key': KEY})
    assert response.status_code == 200 and 'güvenle kaydedildi' in response.text
    safe(response, caplog)
    raw = web.client.get(credentials.KEY)
    assert KEY.encode() not in raw and web.client.pttl(credentials.KEY) == -1
    assert web.client.dbsize() == 1
    item = credentials.read(web.client)
    assert item.api_key == KEY and KEY not in repr(item)
    response = web.http.post(PATH, headers=auth(), data={'api_key': 'another-legitimate-key-9843'})
    assert response.status_code == 409 and web.client.get(credentials.KEY) == raw
    safe(response, caplog)
    page = web.http.get(PATH, headers=auth())
    assert '<form' not in page.text and 'kontrolü bekliyor' in page.text
    safe(page, caplog)


@pytest.mark.parametrize('headers,status', [({}, 401),
    ({'cookie': 'youtube_studio_token=offline-owner-studio-session'}, 403),
    (auth(origin='https://evil.invalid'), 403)])
def test_auth_and_origin_are_checked_before_reading_secret_or_database(web, headers, status, caplog):
    response = web.http.post(PATH, headers=headers, data={'api_key': KEY})
    assert response.status_code == status and web.client.dbsize() == 0
    web.redis_factory.assert_not_called()
    safe(response, caplog)


@pytest.mark.parametrize('body', [b'api_key=%ZZ', b'api_key=a&api_key=b',
    b'api_key=' + b'x' * 17000, b'other=secret', b'api_key=your-kie-api-key'])
def test_malformed_or_placeholder_secret_is_never_saved(web, body, caplog):
    response = web.http.post(PATH, headers=auth(**{'content-type': 'application/x-www-form-urlencoded'}),
                            content=body)
    assert response.status_code in (413, 422) and web.client.dbsize() == 0
    safe(response, caplog)


def test_query_string_secret_is_rejected(web, caplog):
    response = web.http.get(PATH + '?api_key=unsafe', headers=auth())
    assert response.status_code == 400 and web.client.dbsize() == 0
    safe(response, caplog)


def test_tampered_or_expiring_credential_cannot_be_used(web):
    credentials.save(web.client, KEY)
    original = web.client.get(credentials.KEY)
    web.client.set(credentials.KEY, original[:-1] + b'x')
    with pytest.raises(ProviderKeyCandidateError):
        credentials.read(web.client)
    web.client.set(credentials.KEY, original, ex=3600)
    with pytest.raises(ProviderKeyCandidateError):
        credentials.read(web.client)


def test_abacus_and_kie_ciphertexts_are_not_interchangeable(web):
    from app.services import provider_key_candidate as abacus
    abacus.stage_candidate(web.client, KEY)
    web.client.set(credentials.KEY, web.client.get(abacus.CANDIDATE_KEY))
    with pytest.raises(ProviderKeyCandidateError):
        credentials.read(web.client)


def test_missing_encryption_material_never_saves_plaintext(web):
    web.config.app_encryption_key = ''
    with pytest.raises(ProviderKeyCandidateError):
        credentials.save(web.client, KEY)
    assert web.client.dbsize() == 0
