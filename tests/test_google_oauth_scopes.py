"""Exercise the real oauthlib/requests parser with offline Google responses."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from requests_oauthlib import OAuth2Session

from app.services.google_oauth_scopes import fetch_google_token

ANALYTICS = 'https://www.googleapis.com/auth/yt-analytics.readonly'
READ = 'https://www.googleapis.com/auth/youtube.readonly'
UPLOAD = 'https://www.googleapis.com/auth/youtube.upload'
FORCE = 'https://www.googleapis.com/auth/youtube.force-ssl'


def offline_flow(monkeypatch, requested, granted, **patch):
    monkeypatch.delenv('OAUTHLIB_RELAX_TOKEN_SCOPE', raising=False)
    session = OAuth2Session(client_id='offline-client', scope=requested)
    payload = {'access_token': 'offline-access', 'refresh_token': 'offline-refresh',
        'token_type': 'Bearer', 'expires_in': 3600, 'scope': ' '.join(granted), **patch}
    def send(request, **kwargs):
        assert request.url == 'https://oauth2.googleapis.com/token'
        assert 'code=offline-code' in request.body
        assert kwargs['timeout'] == 20
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(payload).encode()
        response.request = request
        return response
    sender = Mock(side_effect=send)
    monkeypatch.setattr(session, 'send', sender)
    def fetch(**kwargs):
        return session.fetch_token('https://oauth2.googleapis.com/token', **kwargs)
    return SimpleNamespace(oauth2session=session, fetch_token=fetch), sender


@pytest.mark.parametrize('required', [[ANALYTICS, READ], [UPLOAD, READ, FORCE]])
def test_incremental_google_grants_work_for_analytics_and_later_upload_reconnect(monkeypatch, required):
    granted = [ANALYTICS, READ, UPLOAD, FORCE]
    flow, sender = offline_flow(monkeypatch, required, granted)
    result = fetch_google_token(flow, code='offline-code', required_scopes=required)
    assert set(result['scope']) == set(granted)
    assert flow.oauth2session.access_token == 'offline-access'
    assert flow.oauth2session.token['refresh_token'] == 'offline-refresh'
    assert flow.oauth2session.scope == required
    sender.assert_called_once()


def test_exact_grant_keeps_normal_exchange(monkeypatch):
    required = [ANALYTICS, READ]
    flow, sender = offline_flow(monkeypatch, required, required)
    assert fetch_google_token(flow, code='offline-code', required_scopes=required)['access_token']
    sender.assert_called_once()


@pytest.mark.parametrize('granted', [[READ], [READ, UPLOAD, FORCE], []])
def test_missing_required_permission_is_not_accepted_or_retried(monkeypatch, granted):
    flow, sender = offline_flow(monkeypatch, [ANALYTICS, READ], granted)
    with pytest.raises(Warning):
        fetch_google_token(flow, code='offline-code', required_scopes=[ANALYTICS, READ])
    assert not flow.oauth2session.access_token
    sender.assert_called_once()


def test_error_body_and_unrelated_warning_are_never_accepted(monkeypatch):
    flow, sender = offline_flow(monkeypatch, [ANALYTICS, READ], [ANALYTICS, READ, UPLOAD], error='invalid_grant')
    with pytest.raises(Exception):
        fetch_google_token(flow, code='offline-code', required_scopes=[ANALYTICS, READ])
    assert not flow.oauth2session.access_token
    sender.assert_called_once()
    flow.fetch_token = Mock(side_effect=Warning('unrelated'))
    with pytest.raises(Warning, match='unrelated'):
        fetch_google_token(flow, code='offline-code', required_scopes=[ANALYTICS, READ])
    flow.fetch_token.assert_called_once()
