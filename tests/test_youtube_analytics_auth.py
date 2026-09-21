"""Incremental owner grant preserves current upload authorization and identity."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import fakeredis
import json
import pytest
from app.services import youtube_analytics_auth as access
from test_youtube_lifecycle import BaseCredentials, _configure_auth, youtube_auth as auth

CHANNEL = 'UC_analytics_channel'
OTHER = 'UC_other_channel'
BINDING = 'analytics_browser_' + 'x' * 40


@pytest.fixture
def flow(monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=True)
    _configure_auth(monkeypatch, client)
    monkeypatch.setattr(access.metrics, '_redis', lambda: client)
    monkeypatch.setattr(access.metrics, '_auth', lambda: auth)
    _seed_v3_connection(client, CHANNEL, 'connection-original', title='Owner channel')
    _seed_v3_connection(client, OTHER, 'connection-other')
    selected = {'id': CHANNEL, 'title': 'Owner channel'}
    options = {'granted': access.SCOPES}
    created = []
    class Flow:
        @classmethod
        def from_client_config(cls, _config, **kwargs):
            obj = cls(); obj.state = kwargs['state']; obj.code_verifier = kwargs.get('code_verifier') or 'synthetic-pkce'
            obj.scopes = kwargs['scopes']
            obj.credentials = BaseCredentials(refresh_token='analytics-refresh-secret', scopes=obj.scopes)
            obj.credentials.granted_scopes = options['granted']
            created.append(obj); return obj
        def authorization_url(self, **kwargs):
            assert kwargs['prompt'] == 'consent select_account'
            return 'https://accounts.google.test/?state=' + self.state, self.state
        def fetch_token(self, **kwargs): pass
    monkeypatch.setattr(auth, 'Flow', Flow)
    monkeypatch.setattr(auth, '_channel_from_credentials', lambda c: deepcopy(selected))
    return SimpleNamespace(client=client, selected=selected, options=options, created=created)


def _seed_v3_connection(client, channel_id, connection_id, title="Channel"):
    client.set(auth.CREDENTIAL_PREFIX + channel_id, auth._encrypt_json({
        "version": 3, "channel_id": channel_id, "connection_id": connection_id,
        "refresh_token": "synthetic-base-token", "scopes": auth.SCOPES}))
    client.set(auth.CHANNEL_PREFIX + channel_id, json.dumps({
        "id": channel_id, "connection_id": connection_id, "title": title,
        "connected_at": "2026-09-01T00:00:00Z", "verified_at": "2026-09-01T00:00:00Z"}))
    client.sadd(auth.CHANNEL_INDEX_KEY, channel_id)


def begin(flow):
    url = auth.build_authorization_url(BINDING, target_channel_id=CHANNEL, analytics=True)
    state = url.rsplit('=', 1)[1]
    return state, auth._state_key(state, auth._binding_digest(BINDING))


def core(flow):
    return {key: flow.client.dump(key) for key in flow.client.scan_iter('*')
        if key.startswith((auth.CHANNEL_PREFIX, auth.CREDENTIAL_PREFIX, 'production:')) or key == auth.CHANNEL_INDEX_KEY}


def test_optional_grant_preserves_both_production_connections_and_existing_grant_is_encrypted(flow):
    before = core(flow)
    state, key = begin(flow)
    record = auth._decrypt_json(flow.client.get(key))
    assert record['version'] == 4 and record['purpose'] == 'analytics'
    assert flow.created[0].scopes == access.SCOPES
    assert 'youtube.upload' not in str(flow.created[0].scopes)
    result = auth.complete_authorization('synthetic-code', state, BINDING)
    assert result['analytics_connected'] is True and result['id'] == CHANNEL
    assert core(flow) == before
    encrypted = flow.client.get(access.PREFIX + CHANNEL)
    assert 'analytics-refresh-secret' not in encrypted
    payload = auth._decrypt_json(encrypted)
    assert payload['connection_id'] == 'connection-original' and payload['scopes'] == access.SCOPES
    with pytest.raises(auth.OAuthStateError): auth.complete_authorization('synthetic-code', state, BINDING)


@pytest.mark.parametrize('change', ['wrong_channel', 'credential', 'connection', 'epoch', 'scope', 'membership'])
def test_rejected_grant_never_replaces_upload_tokens_or_analytics(flow, change):
    state, key = begin(flow)
    if change == 'wrong_channel': flow.selected['id'] = OTHER
    if change == 'credential': flow.client.set(auth.CREDENTIAL_PREFIX + CHANNEL, 'changed-cipher')
    if change == 'connection': _seed_v3_connection(flow.client, CHANNEL, 'replacement-connection')
    if change == 'epoch': flow.client.incr(auth.AUTH_EPOCH_KEY)
    if change == 'scope': flow.options['granted'] = [access.SCOPES[1]]
    if change == 'membership': flow.client.srem(auth.CHANNEL_INDEX_KEY, CHANNEL)
    before = core(flow)
    with pytest.raises(auth.YouTubeAuthError): auth.complete_authorization('synthetic-code', state, BINDING)
    assert core(flow) == before and flow.client.get(access.PREFIX + CHANNEL) is None
    assert flow.client.get(key) is None


@pytest.mark.parametrize('change', ['version', 'purpose', 'binding', 'sha', 'legacy'])
def test_tampered_analytics_state_cannot_become_an_upload_reconnect(flow, change):
    state, key = begin(flow); value = auth._decrypt_json(flow.client.get(key))
    if change == 'version': value['version'] = 3
    if change == 'purpose': value['purpose'] = 'upload'
    if change == 'binding': value['analytics_binding'] = {}
    if change == 'sha': value['analytics_binding']['credential_sha256'] = 'x'
    if change == 'legacy': value.pop('analytics_binding')
    flow.client.set(key, auth._encrypt_json(value)); before = core(flow)
    with pytest.raises(auth.OAuthStateError): auth.complete_authorization('synthetic-code', state, BINDING)
    assert core(flow) == before and len(flow.created) == 1


def test_analytics_requires_a_target_before_starting_oauth(flow):
    before = core(flow)
    with pytest.raises(auth.YouTubeAuthError): auth.build_authorization_url(BINDING, analytics=True)
    assert core(flow) == before and flow.created == []


def test_read_credentials_binds_grant_to_same_current_cipher_and_connection(flow, monkeypatch):
    state, _ = begin(flow); auth.complete_authorization('synthetic-code', state, BINDING)
    context = access._context(CHANNEL); encrypted = flow.client.get(access.PREFIX + CHANNEL)
    credentials = SimpleNamespace(token='temporary-test-token', refresh=Mock(), granted_scopes=access.SCOPES)
    constructor = Mock(return_value=credentials)
    monkeypatch.setattr(auth, 'Credentials', constructor)
    monkeypatch.setattr(auth, 'GoogleRequest', Mock(return_value=Mock()))
    assert access.read_credentials(context, encrypted) is credentials
    assert constructor.call_args.kwargs['scopes'] == access.SCOPES
    with pytest.raises(access.metrics.YouTubeMetricsError):
        access.read_credentials({**context, 'cipher': 'changed-main-credential'}, encrypted)
    assert constructor.call_count == 1

from test_youtube_dashboard_presentation import dashboard


def test_analytics_consent_route_requires_owner_same_origin_and_post(dashboard):
    start = Mock(return_value='https://accounts.google.test/auth?state=synthetic')
    dashboard.ns['build_authorization_url'] = start
    url = '/studio/youtube/analytics/connect/' + CHANNEL
    assert dashboard.client.get(url).status_code == 405
    assert dashboard.client.post(url, headers={'Origin': 'https://foreign.test'}).status_code == 403
    start.assert_not_called()
    response = dashboard.client.post(url, headers={'Origin': 'https://studio.example'}, follow_redirects=False)
    assert response.status_code == 302
    assert start.call_args.kwargs == {'target_channel_id': CHANNEL, 'analytics': True}
    dashboard.client.cookies.clear()
    assert dashboard.client.post(url, headers={'Origin': 'https://studio.example'}).status_code == 401
    assert dashboard.client.get('/studio/analytics').status_code == 401
    assert start.call_count == 1


def test_analytics_callback_links_to_performance_and_clears_browser_cookie(dashboard):
    dashboard.ns['complete_authorization'] = Mock(return_value={'id': CHANNEL, 'analytics_connected': True})
    response = dashboard.client.get('/studio/youtube/callback?state=synthetic&code=secret-code')
    assert response.status_code == 200 and 'İzleyici analizi bağlandı' in response.text
    assert '/studio/analytics' in response.text and 'secret-code' not in response.text
    assert response.headers['Referrer-Policy'] == 'no-referrer'
    assert 'Max-Age=0' in response.headers['set-cookie']
