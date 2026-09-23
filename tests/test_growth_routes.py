from fastapi import FastAPI
from fastapi.testclient import TestClient
import fakeredis
import pytest

from app import growth_routes as routes
from app.config import settings
from app.services import audience_strategy as strategy

CHANNEL = 'UC5v9AvNtD3PTLgo6m1jROOA'


@pytest.fixture
def ui(monkeypatch):
    store = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(settings, 'factory_api_token', 'test-growth-owner')
    monkeypatch.setattr(settings, 'google_redirect_uri', 'https://studio.example/studio/youtube/callback')
    monkeypatch.setattr(routes, '_channels', lambda: [({'channel_id': CHANNEL}, 'Capital Corrupt')])
    monkeypatch.setattr(strategy, '_client', lambda: store)
    monkeypatch.setattr(routes, '_voices_active', lambda: False)
    app = FastAPI(); app.include_router(routes.router)
    client = TestClient(app, base_url='https://studio.example')
    client.cookies.set(routes.COOKIE_NAME, 'test-growth-owner')
    return client, store


def test_preferences_need_owner_authentication_and_same_origin(ui):
    client, store = ui
    assert client.get('/studio/growth').status_code == 200
    payload = {'channel_id': CHANNEL, 'revision': 0, 'trend_enabled': 'yes', 'languages': ['en', 'es']}
    assert client.post('/studio/growth/settings', data=payload,
        headers={'Origin': 'https://foreign.example'}).status_code == 403
    assert store.dbsize() == 0
    response = client.post('/studio/growth/settings', data=payload,
        headers={'Origin': 'https://studio.example'}, follow_redirects=False)
    assert response.status_code == 303
    assert strategy.read_settings(CHANNEL, client=store)['languages'] == ['en', 'es']
    assert client.post('/studio/growth/settings', data=payload,
        headers={'Origin': 'https://studio.example'}).status_code == 409
    client.cookies.clear()
    assert client.get('/studio/growth').status_code == 401
    assert client.get('/studio/growth/captions/abcdefghijk/en').status_code == 401


def test_page_is_read_only_and_distinguishes_subtitles_from_native_dubs(ui):
    client, store = ui
    response = client.get('/studio/growth')
    assert response.headers['cache-control'] == 'private, no-store'
    assert store.dbsize() == 0
    assert 'Türkçe → İngilizce' in response.text and 'YouTube izlenmesi değildir' in response.text
    assert 'test-growth-owner' not in response.text and 'API' not in response.text
    assert client.get('/studio/growth/captions/abcdefghijk/en').status_code == 404


def test_invalid_channel_and_storage_outage_are_actionable(ui, monkeypatch):
    client, store = ui
    response = client.post('/studio/growth/settings', data={'channel_id': 'other', 'revision': 0},
        headers={'Origin': 'https://studio.example'})
    assert response.status_code == 422 and store.dbsize() == 0
    def unavailable():
        raise ConnectionError()
    monkeypatch.setattr(strategy, '_client', unavailable)
    assert client.get('/studio/growth').status_code == 503


def test_external_trend_text_is_escaped(ui, monkeypatch):
    client, _ = ui
    monkeypatch.setattr(routes.trends, 'relevant', lambda *a, **k: [{'term': '<script>bad()</script>',
        'regions': ['US'], 'fresh': True, 'traffic_label': '10K+'}])
    response = client.get('/studio/growth')
    assert '&lt;script&gt;bad()&lt;/script&gt;' in response.text
    assert '<script>bad()</script>' not in response.text
