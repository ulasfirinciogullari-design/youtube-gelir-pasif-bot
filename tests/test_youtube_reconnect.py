"""A reconnect can replace only the selected channel, with the normal OAuth fences."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from test_youtube_lifecycle import (
    BaseCredentials, FakeRedis, _configure_auth, _seed_v3_connection, youtube_auth as auth,
)
from test_youtube_dashboard_presentation import CHANNEL, OTHER_CHANNEL, dashboard


BINDING = 'reconnect_browser_' + 'a' * 32


@pytest.fixture
def reconnect(monkeypatch):
    client = FakeRedis()
    _configure_auth(monkeypatch, client)
    _seed_v3_connection(client, CHANNEL, 'margin-connection', title='Margin Verdict')
    _seed_v3_connection(client, OTHER_CHANNEL, 'capital-connection', title='Capital Corrupt')
    fetched = Mock()
    selected = {'id': CHANNEL, 'title': 'Margin Verdict'}

    class Flow:
        def __init__(self, state, code_verifier=None):
            self.state = state
            self.code_verifier = code_verifier or 'synthetic-pkce'
            self.credentials = BaseCredentials(refresh_token='synthetic-new-refresh', scopes=auth.SCOPES)

        @classmethod
        def from_client_config(cls, _config, **kwargs):
            return cls(kwargs['state'], kwargs.get('code_verifier'))

        def authorization_url(self, **_kwargs):
            return f'https://accounts.google.test/auth?state={self.state}', self.state

        def fetch_token(self, **kwargs):
            fetched(**kwargs)

    monkeypatch.setattr(auth, 'Flow', Flow)
    monkeypatch.setattr(auth, '_channel_from_credentials', lambda _credentials: deepcopy(selected))
    monkeypatch.setattr(auth, 'build', Mock(side_effect=AssertionError('No real YouTube client')))
    return SimpleNamespace(client=client, selected=selected, fetched=fetched)


def begin(reconnect):
    url = auth.build_authorization_url(BINDING, target_channel_id=CHANNEL)
    state = url.rsplit('=', 1)[1]
    key = auth._state_key(state, auth._binding_digest(BINDING))
    return state, key


def test_reconnect_target_is_encrypted_and_only_matching_channel_is_replaced(reconnect):
    before_other = {k: v for k, v in reconnect.client.values.items() if OTHER_CHANNEL in k}
    before_target = reconnect.client.get(auth._credential_key(CHANNEL))
    state, key = begin(reconnect)
    encrypted = reconnect.client.get(key)
    assert CHANNEL not in encrypted
    assert auth._decrypt_json(encrypted)['target_channel_id'] == CHANNEL
    assert reconnect.client.get(auth._credential_key(CHANNEL)) == before_target
    assert reconnect.fetched.call_count == 0

    result = auth.complete_authorization('synthetic-code', state, BINDING)
    assert result['id'] == CHANNEL
    assert reconnect.client.get(auth._credential_key(CHANNEL)) != before_target
    assert {k: v for k, v in reconnect.client.values.items() if OTHER_CHANNEL in k} == before_other
    assert reconnect.client.smembers(auth.CHANNEL_INDEX_KEY) == {CHANNEL, OTHER_CHANNEL}
    with pytest.raises(auth.OAuthStateError):
        auth.complete_authorization('synthetic-code', state, BINDING)
    reconnect.fetched.assert_called_once_with(code='synthetic-code')


@pytest.mark.parametrize('selected', [OTHER_CHANNEL, 'UCikkzCmN1ggb0Ag7eS9HDLA'])
def test_wrong_channel_never_adds_or_overwrites_connection_and_callback_cannot_replay(reconnect, selected):
    state, key = begin(reconnect)
    before = {k: v for k, v in reconnect.client.values.items()
              if k.startswith((auth.CREDENTIAL_PREFIX, auth.CHANNEL_PREFIX))}
    before_members = deepcopy(reconnect.client.sets)
    reconnect.selected['id'] = selected
    with pytest.raises(auth.YouTubeAuthError, match='youtube_oauth_channel_mismatch'):
        auth.complete_authorization('synthetic-code', state, BINDING)
    assert reconnect.client.get(key) is None
    assert {k: v for k, v in reconnect.client.values.items()
            if k.startswith((auth.CREDENTIAL_PREFIX, auth.CHANNEL_PREFIX))} == before
    assert reconnect.client.sets == before_members
    with pytest.raises(auth.OAuthStateError):
        auth.complete_authorization('synthetic-code', state, BINDING)
    reconnect.fetched.assert_called_once_with(code='synthetic-code')


@pytest.mark.parametrize('target', [None, '', 123, '../different-channel', 'not a channel'])
def test_malformed_stored_reconnect_target_is_consumed_without_token_exchange(reconnect, target):
    state, key = begin(reconnect)
    record = auth._decrypt_json(reconnect.client.get(key))
    record['target_channel_id'] = target
    reconnect.client.set(key, auth._encrypt_json(record))
    with pytest.raises(auth.OAuthStateError):
        auth.complete_authorization('synthetic-code', state, BINDING)
    assert reconnect.client.get(key) is None
    reconnect.fetched.assert_not_called()


def test_nonexistent_target_does_not_start_or_invalidate_any_flow(reconnect):
    before = deepcopy(reconnect.client.values)
    with pytest.raises(auth.YouTubeAuthError):
        auth.build_authorization_url(BINDING, target_channel_id='UC_missing_channel')
    assert reconnect.client.values == before
    reconnect.fetched.assert_not_called()


@pytest.mark.parametrize('interruption', ['new_flow', 'disconnect', 'wrong_browser'])
def test_reconnect_retains_epoch_and_browser_fences(reconnect, interruption):
    state, key = begin(reconnect)
    binding = BINDING
    if interruption == 'new_flow':
        auth.build_authorization_url('new_browser_' + 'b' * 40)
    elif interruption == 'disconnect':
        auth.disconnect(CHANNEL, expected_connection_id='margin-connection', revoke=False)
    else:
        binding = 'wrong_browser_' + 'c' * 40
    with pytest.raises(auth.OAuthStateError):
        auth.complete_authorization('synthetic-code', state, binding)
    reconnect.fetched.assert_not_called()
    if interruption == 'wrong_browser':
        assert reconnect.client.get(key) is not None


def test_reconnect_existing_channel_works_at_connection_limit(reconnect, monkeypatch):
    monkeypatch.setattr(auth, 'MAX_CONNECTIONS', 2)
    state, _ = begin(reconnect)
    assert auth.complete_authorization('synthetic-code', state, BINDING)['id'] == CHANNEL
    assert reconnect.client.smembers(auth.CHANNEL_INDEX_KEY) == {CHANNEL, OTHER_CHANNEL}


def test_permission_error_has_reconnect_action_and_dashboard_notice_without_writes(dashboard):
    dashboard.model['channels'][0].update(reason='permission', status='stale')
    response = dashboard.client.get('/studio/youtube')
    assert response.status_code == 200
    card = response.text.split(f'id="channel-{CHANNEL}"', 1)[1].split('</article>', 1)[0]
    assert 'Bağlantı yenilenmeli' in card and '● Bağlı' not in card
    assert f'action="/studio/youtube/reconnect/{CHANNEL}"' in card
    assert 'Google erişimi yenilenmeli' in response.text
    assert 'Kanallar sayfasından yeniden bağla' in response.text
    dashboard.forbidden.assert_not_called()


def test_unmeasured_connection_is_not_reported_as_healthy(dashboard):
    dashboard.model['channels'][0].update(status='unavailable', reason='not_refreshed')
    body = dashboard.client.get('/studio/youtube').text
    card = body.split(f'id="channel-{CHANNEL}"', 1)[1].split('</article>', 1)[0]
    assert 'Bağlantı kayıtlı' in card and '● Bağlı' not in card
    dashboard.forbidden.assert_not_called()


def test_reconnect_post_is_authenticated_same_origin_and_binds_target(dashboard):
    build = Mock(return_value='https://accounts.google.test/auth?state=synthetic')
    dashboard.ns['build_authorization_url'] = build
    url = f'/studio/youtube/reconnect/{CHANNEL}'
    assert dashboard.client.get(url).status_code == 405
    assert dashboard.client.post(url, headers={'Origin': 'https://foreign.test'}).status_code == 403
    build.assert_not_called()
    response = dashboard.client.post(url, headers={'Origin': 'https://studio.example'}, follow_redirects=False)
    assert response.status_code == 302
    assert build.call_args.kwargs == {'target_channel_id': CHANNEL}
    cookie = response.headers['set-cookie']
    assert 'HttpOnly' in cookie and 'Secure' in cookie and 'SameSite=lax' in cookie
    assert 'Path=/studio/youtube/callback' in cookie
    dashboard.client.cookies.clear()
    assert dashboard.client.post(url, headers={'Origin': 'https://studio.example'}).status_code == 401
    assert build.call_count == 1


def test_wrong_channel_callback_explains_recovery_without_leaking_provider_details(dashboard):
    dashboard.ns['complete_authorization'] = Mock(side_effect=RuntimeError('youtube_oauth_channel_mismatch'))
    response = dashboard.client.get('/studio/youtube/callback?state=synthetic&code=secret-code')
    assert response.status_code == 400
    assert 'Farklı bir kanal seçildi' in response.text and 'bu bağlantı kaydedilmedi' in response.text
    assert 'secret-code' not in response.text and 'youtube_oauth_channel_mismatch' not in response.text
    assert 'Max-Age=0' in response.headers['set-cookie']
    assert response.headers['referrer-policy'] == 'no-referrer'
