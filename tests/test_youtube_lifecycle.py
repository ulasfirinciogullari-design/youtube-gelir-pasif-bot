from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys
import types

import pytest


class FakeRefreshError(Exception):
    pass


class BaseCredentials:
    def __init__(self, **kwargs):
        self.token = kwargs.get('token')
        self.refresh_token = kwargs.get('refresh_token')
        self.token_uri = kwargs.get('token_uri')
        self.client_id = kwargs.get('client_id')
        self.client_secret = kwargs.get('client_secret')
        self.scopes = kwargs.get('scopes')

    def refresh(self, _request):
        self.token = 'short-lived-access-token'

    def has_scopes(self, scopes):
        return set(scopes).issubset(set(self.scopes or scopes))


class PlaceholderFlow:
    @classmethod
    def from_client_config(cls, *_args, **_kwargs):
        raise AssertionError('Flow must be replaced by the test')


class PlaceholderMediaFileUpload:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


def _install_google_stubs() -> None:
    modules = {
        'google': types.ModuleType('google'),
        'google.auth': types.ModuleType('google.auth'),
        'google.auth.exceptions': types.ModuleType('google.auth.exceptions'),
        'google.auth.transport': types.ModuleType('google.auth.transport'),
        'google.auth.transport.requests': types.ModuleType('google.auth.transport.requests'),
        'google.oauth2': types.ModuleType('google.oauth2'),
        'google.oauth2.credentials': types.ModuleType('google.oauth2.credentials'),
        'google_auth_oauthlib': types.ModuleType('google_auth_oauthlib'),
        'google_auth_oauthlib.flow': types.ModuleType('google_auth_oauthlib.flow'),
        'googleapiclient': types.ModuleType('googleapiclient'),
        'googleapiclient.discovery': types.ModuleType('googleapiclient.discovery'),
        'googleapiclient.http': types.ModuleType('googleapiclient.http'),
    }
    modules['google.auth.exceptions'].RefreshError = FakeRefreshError
    modules['google.auth.transport.requests'].Request = object
    modules['google.oauth2.credentials'].Credentials = BaseCredentials
    modules['google_auth_oauthlib.flow'].Flow = PlaceholderFlow
    modules['googleapiclient.discovery'].build = lambda *_a, **_k: None
    modules['googleapiclient.http'].MediaFileUpload = PlaceholderMediaFileUpload
    for name, module in modules.items():
        sys.modules.setdefault(name, module)


_install_google_stubs()

if 'app.config' not in sys.modules:
    config_module = types.ModuleType('app.config')
    config_module.settings = types.SimpleNamespace(
        redis_url='redis://test',
        app_encryption_key='',
        factory_api_token='',
        google_client_id='',
        google_client_secret='',
        google_redirect_uri='',
    )
    sys.modules['app.config'] = config_module

import app.services.youtube_auth as youtube_auth
import app.services.youtube as youtube_service
import app.services.youtube_publish_state as publish_state


class FakePipeline:
    def __init__(self, client):
        self.client = client
        self.operations = []

    def set(self, *args, **kwargs):
        self.operations.append(('set', args, kwargs))
        return self

    def delete(self, *args, **kwargs):
        self.operations.append(('delete', args, kwargs))
        return self

    def execute(self):
        for method, args, kwargs in self.operations:
            getattr(self.client, method)(*args, **kwargs)
        return [True] * len(self.operations)


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.expirations = {}

    def get(self, key):
        return self.values.get(key)

    def getdel(self, key):
        return self.values.pop(key, None)

    def set(self, key, value, ex=None, nx=False, **_kwargs):
        if nx and key in self.values:
            return False
        self.values[key] = value
        if ex:
            self.expirations[key] = int(ex)
        return True

    def delete(self, *keys):
        count = 0
        for key in keys:
            count += int(key in self.values)
            self.values.pop(key, None)
        return count

    def incr(self, key):
        value = int(self.values.get(key, 0)) + 1
        self.values[key] = str(value)
        return value

    def pipeline(self, transaction=True):
        del transaction
        return FakePipeline(self)

    def eval(self, script, numkeys, *values):
        keys = list(values[:numkeys])
        args = list(values[numkeys:])

        if script == youtube_auth._CLAIM_EPOCH_SCRIPT:
            expected = str(args[0])
            if str(self.values.get(keys[0], '0')) != expected:
                return 0
            return self.incr(keys[0])

        if script == youtube_auth._COMMIT_CONNECTION_SCRIPT:
            expected, encrypted, channel_json = map(str, args)
            if str(self.values.get(keys[0], '0')) != expected:
                return 0
            self.values[keys[1]] = encrypted
            self.values[keys[2]] = channel_json
            self.delete(keys[3], keys[4])
            self.incr(keys[0])
            return 1

        if script == youtube_auth._CLEAR_IF_CREDENTIAL_MATCHES_SCRIPT:
            if self.values.get(keys[0]) != args[0]:
                return 0
            self.delete(*keys)
            return 1

        if script == youtube_auth._CLEAR_IF_CHANNEL_MATCHES_SCRIPT:
            if self.values.get(keys[1]) != args[0]:
                return 0
            self.delete(*keys)
            return 1

        if script == youtube_auth._UPDATE_CHANNEL_IF_CURRENT_SCRIPT:
            if not self.values.get(keys[0]) or self.values.get(keys[1]) != args[0]:
                return 0
            self.values[keys[1]] = args[1]
            return 1

        if script == publish_state._CAS_RECORD:
            old_raw, new_raw, ttl = args
            if self.values.get(keys[0]) != old_raw:
                return 0
            self.values[keys[0]] = new_raw
            self.expirations[keys[0]] = int(ttl)
            return 1

        if script == publish_state._RELEASE_LOCK:
            if self.values.get(keys[0]) == args[0]:
                del self.values[keys[0]]
                return 1
            return 0

        raise AssertionError('Unexpected Redis script')


def _configure_auth(monkeypatch, client):
    monkeypatch.setattr(
        youtube_auth.settings,
        'google_client_id',
        'client-id',
        raising=False,
    )
    monkeypatch.setattr(
        youtube_auth.settings,
        'google_client_secret',
        'client-secret',
        raising=False,
    )
    monkeypatch.setattr(
        youtube_auth.settings,
        'google_redirect_uri',
        'https://studio.example.test/studio/youtube/callback',
        raising=False,
    )
    monkeypatch.setattr(
        youtube_auth.settings,
        'app_encryption_key',
        'dedicated-key',
        raising=False,
    )
    monkeypatch.setattr(youtube_auth, '_redis', lambda: client)


def test_oauth_state_is_encrypted_one_use_and_channel_is_verified(monkeypatch):
    client = FakeRedis()
    _configure_auth(monkeypatch, client)
    flow_instances = []

    class Credentials(BaseCredentials):
        def __init__(self):
            super().__init__(
                token='temporary-access',
                refresh_token='refresh-secret-value',
                scopes=youtube_auth.SCOPES,
            )

    class Flow:
        def __init__(self, state, code_verifier=None):
            self.state = state
            self.code_verifier = code_verifier or 'pkce-verifier-secret'
            self.redirect_uri = None
            self.credentials = Credentials()
            self.fetched_code = None
            flow_instances.append(self)

        @classmethod
        def from_client_config(cls, _config, **kwargs):
            return cls(kwargs['state'], kwargs.get('code_verifier'))

        def authorization_url(self, **kwargs):
            assert kwargs == {
                'access_type': 'offline',
                'include_granted_scopes': 'true',
                'prompt': 'consent',
            }
            return f'https://accounts.google.test/auth?state={self.state}', self.state

        def fetch_token(self, *, code):
            self.fetched_code = code

    channel_calls = []

    class ChannelRequest:
        def execute(self, *, num_retries):
            assert num_retries == 2
            return {
                'items': [{
                    'id': 'UC_verified',
                    'snippet': {'title': 'Verified channel'},
                    'statistics': {'videoCount': '4'},
                }]
            }

    class Channels:
        def list(self, **kwargs):
            channel_calls.append(kwargs)
            return ChannelRequest()

    class YouTube:
        def channels(self):
            return Channels()

    monkeypatch.setattr(youtube_auth, 'Flow', Flow)
    monkeypatch.setattr(youtube_auth, 'build', lambda *_a, **_k: YouTube())

    browser_binding = 'browser_binding_' + ('a' * 32)
    wrong_binding = 'browser_binding_' + ('b' * 32)
    authorization_url = youtube_auth.build_authorization_url(browser_binding)
    state = authorization_url.rsplit('=', 1)[1]
    state_key = youtube_auth._state_key(
        state,
        youtube_auth._binding_digest(browser_binding),
    )
    encrypted_state = client.values[state_key]
    assert 'pkce-verifier-secret' not in encrypted_state
    assert browser_binding not in encrypted_state
    assert client.expirations[state_key] == youtube_auth.STATE_TTL_SECONDS

    # An attacker with only the state cannot consume the legitimate browser's
    # one-use record because the binding hash is part of its Redis key.
    with pytest.raises(youtube_auth.OAuthStateError):
        youtube_auth.complete_authorization(
            'authorization-code',
            state,
            wrong_binding,
        )
    assert state_key in client.values

    channel = youtube_auth.complete_authorization(
        'authorization-code',
        state,
        browser_binding,
    )

    assert channel['id'] == 'UC_verified'
    assert channel['connection_id']
    assert channel_calls == [{
        'part': 'id,snippet,statistics',
        'mine': True,
        'maxResults': 1,
    }]
    assert flow_instances[-1].fetched_code == 'authorization-code'
    assert state_key not in client.values
    encrypted_credentials = client.values[youtube_auth.CREDENTIAL_KEY]
    assert 'refresh-secret-value' not in encrypted_credentials
    assert 'temporary-access' not in encrypted_credentials
    payload = youtube_auth._decrypt_json(encrypted_credentials)
    assert payload['refresh_token'] == 'refresh-secret-value'

    with pytest.raises(youtube_auth.OAuthStateError):
        youtube_auth.complete_authorization(
            'authorization-code',
            state,
            browser_binding,
        )


def test_invalid_grant_clears_stored_connection(monkeypatch):
    client = FakeRedis()
    _configure_auth(monkeypatch, client)
    client.values[youtube_auth.CREDENTIAL_KEY] = youtube_auth._encrypt_json({
        'version': 2,
        'refresh_token': 'revoked-refresh-token',
    })
    client.values[youtube_auth.CHANNEL_KEY] = json.dumps({'id': 'UC_old'})

    class RevokedCredentials(BaseCredentials):
        def refresh(self, _request):
            raise youtube_auth.RefreshError('invalid_grant: token revoked')

    monkeypatch.setattr(
        youtube_auth,
        '_credential_from_refresh_token',
        lambda token: RevokedCredentials(refresh_token=token),
    )

    with pytest.raises(youtube_auth.AuthorizationRevokedError):
        youtube_auth.load_credentials(refresh=True)
    assert youtube_auth.CREDENTIAL_KEY not in client.values
    assert youtube_auth.CHANNEL_KEY not in client.values


def test_disconnect_deletes_locally_and_revokes_without_url_secret(monkeypatch):
    client = FakeRedis()
    _configure_auth(monkeypatch, client)
    client.values[youtube_auth.CREDENTIAL_KEY] = youtube_auth._encrypt_json({
        'version': 2,
        'refresh_token': 'refresh-token-to-revoke',
    })
    client.values[youtube_auth.CHANNEL_KEY] = json.dumps({'id': 'UC_old'})
    request = {}

    class Response:
        status_code = 200

    def post(url, **kwargs):
        request.update({'url': url, **kwargs})
        return Response()

    monkeypatch.setattr(youtube_auth.httpx, 'post', post)
    assert youtube_auth.disconnect(revoke=True) is True
    assert request['url'] == youtube_auth.REVOCATION_URI
    assert 'refresh-token-to-revoke' not in request['url']
    assert request['data'] == {'token': 'refresh-token-to-revoke'}
    assert youtube_auth.CREDENTIAL_KEY not in client.values
    assert youtube_auth.CHANNEL_KEY not in client.values


def test_new_connect_and_disconnect_invalidate_stale_oauth_callbacks(monkeypatch):
    client = FakeRedis()
    _configure_auth(monkeypatch, client)

    class Credentials(BaseCredentials):
        def __init__(self):
            super().__init__(
                refresh_token='new-refresh-token',
                scopes=youtube_auth.SCOPES,
            )

    class Flow:
        def __init__(self, state, code_verifier=None):
            self.state = state
            self.code_verifier = code_verifier or 'pkce-verifier'
            self.redirect_uri = None
            self.credentials = Credentials()

        @classmethod
        def from_client_config(cls, _config, **kwargs):
            return cls(kwargs['state'], kwargs.get('code_verifier'))

        def authorization_url(self, **_kwargs):
            return f'https://accounts.google.test/auth?state={self.state}', self.state

        def fetch_token(self, *, code):
            assert code == 'authorization-code'

    monkeypatch.setattr(youtube_auth, 'Flow', Flow)
    monkeypatch.setattr(
        youtube_auth,
        '_channel_from_credentials',
        lambda _credentials: {
            'id': 'UC_verified',
            'title': 'Verified channel',
            'connected_at': '2026-08-31T00:00:00+00:00',
            'verified_at': '2026-08-31T00:00:00+00:00',
        },
    )
    binding_a = 'browser_binding_' + ('a' * 32)
    binding_b = 'browser_binding_' + ('b' * 32)
    url_a = youtube_auth.build_authorization_url(binding_a)
    state_a = url_a.rsplit('=', 1)[1]
    url_b = youtube_auth.build_authorization_url(binding_b)
    state_b = url_b.rsplit('=', 1)[1]

    # A later connect supersedes every earlier pending authorization.
    with pytest.raises(youtube_auth.OAuthStateError, match='superseded'):
        youtube_auth.complete_authorization(
            'authorization-code',
            state_a,
            binding_a,
        )
    channel = youtube_auth.complete_authorization(
        'authorization-code',
        state_b,
        binding_b,
    )
    assert channel['id'] == 'UC_verified'

    binding_c = 'browser_binding_' + ('c' * 32)
    state_c = youtube_auth.build_authorization_url(binding_c).rsplit('=', 1)[1]
    youtube_auth.invalidate_pending_authorizations()
    with pytest.raises(youtube_auth.OAuthStateError, match='superseded'):
        youtube_auth.complete_authorization(
            'authorization-code',
            state_c,
            binding_c,
        )


def test_failed_new_callback_does_not_clear_existing_connection(monkeypatch):
    client = FakeRedis()
    _configure_auth(monkeypatch, client)
    encrypted = youtube_auth._encrypt_json({
        'version': 2,
        'refresh_token': 'existing-refresh-token',
    })
    channel_json = json.dumps({
        'id': 'UC_existing',
        'title': 'Existing channel',
        'connection_id': 'existing-connection-id',
    })
    client.values[youtube_auth.CREDENTIAL_KEY] = encrypted
    client.values[youtube_auth.CHANNEL_KEY] = channel_json

    class Unauthorized(Exception):
        resp = types.SimpleNamespace(status=401)

    class ChannelRequest:
        def execute(self, **_kwargs):
            raise Unauthorized('provider response intentionally hidden')

    class YouTube:
        def channels(self):
            return types.SimpleNamespace(list=lambda **_kwargs: ChannelRequest())

    monkeypatch.setattr(youtube_auth, 'build', lambda *_a, **_k: YouTube())
    with pytest.raises(youtube_auth.AuthorizationRevokedError):
        youtube_auth._channel_from_credentials(object())

    assert client.values[youtube_auth.CREDENTIAL_KEY] == encrypted
    assert client.values[youtube_auth.CHANNEL_KEY] == channel_json


def test_channel_refresh_is_bound_to_reserved_channel_and_connection(monkeypatch):
    client = FakeRedis()
    _configure_auth(monkeypatch, client)
    client.values[youtube_auth.CREDENTIAL_KEY] = 'encrypted-credential-present'
    client.values[youtube_auth.CHANNEL_KEY] = json.dumps({
        'id': 'UC_reserved',
        'title': 'Reserved channel',
        'connection_id': 'connection-id-reserved',
        'connected_at': '2026-08-31T00:00:00+00:00',
    })

    with pytest.raises(youtube_auth.YouTubeAuthError, match='target channel changed'):
        youtube_auth.refresh_channel_info(
            object(),
            expected_channel_id='UC_different',
            expected_connection_id='connection-id-reserved',
        )
    with pytest.raises(youtube_auth.YouTubeAuthError, match='connection changed'):
        youtube_auth.refresh_channel_info(
            object(),
            expected_channel_id='UC_reserved',
            expected_connection_id='connection-id-different',
        )

    monkeypatch.setattr(
        youtube_auth,
        '_channel_from_credentials',
        lambda _credentials: {
            'id': 'UC_reserved',
            'title': 'Reserved channel refreshed',
            'connected_at': 'new-value-must-not-replace-original',
            'verified_at': '2026-08-31T01:00:00+00:00',
        },
    )
    refreshed = youtube_auth.refresh_channel_info(
        object(),
        expected_channel_id='UC_reserved',
        expected_connection_id='connection-id-reserved',
    )
    assert refreshed['id'] == 'UC_reserved'
    assert refreshed['connection_id'] == 'connection-id-reserved'
    assert refreshed['connected_at'] == '2026-08-31T00:00:00+00:00'


def test_resumable_video_upload_is_forced_private(monkeypatch, tmp_path):
    video = tmp_path / 'final.mp4'
    video.write_bytes(b'video-bytes')
    media_calls = []
    insert_calls = []
    retry_calls = []

    class Media:
        def __init__(self, *args, **kwargs):
            media_calls.append((args, kwargs))

    class Status:
        def progress(self):
            return 0.5

    class Request:
        count = 0

        def next_chunk(self, *, num_retries):
            retry_calls.append(num_retries)
            self.count += 1
            if self.count == 1:
                return Status(), None
            return None, {'id': 'youtube-id', 'status': {'privacyStatus': 'private'}}

    class Videos:
        def insert(self, **kwargs):
            insert_calls.append(kwargs)
            return Request()

    class YouTube:
        def videos(self):
            return Videos()

    monkeypatch.setattr(youtube_service, 'MediaFileUpload', Media)
    monkeypatch.setattr(youtube_service, '_service', lambda _credentials: YouTube())
    progress = []
    result = youtube_service.upload_video_with_credentials(
        object(),
        str(video),
        'Title',
        'Description',
        privacy_status='private',
        progress_callback=progress.append,
    )

    assert result['id'] == 'youtube-id'
    assert insert_calls[0]['body']['status']['privacyStatus'] == 'private'
    assert insert_calls[0]['notifySubscribers'] is False
    assert media_calls[0][1]['resumable'] is True
    assert media_calls[0][1]['chunksize'] == youtube_service.UPLOAD_CHUNK_SIZE
    assert retry_calls == [3, 3]
    assert progress == [0.5, 1.0]

    with pytest.raises(ValueError, match='private'):
        youtube_service.upload_video_with_credentials(
            object(),
            str(video),
            'Title',
            'Description',
            privacy_status='public',
        )


def test_upload_reservation_blocks_concurrent_and_uncertain_duplicates(monkeypatch):
    client = FakeRedis()
    monkeypatch.setattr(publish_state, '_redis', lambda: client)
    source = 'source-task-1234'
    first_task = 'publish-task-1234'
    second_task = 'publish-task-5678'

    target = 'UC_channel_123'
    connection = 'connection-id-123'
    record, created = publish_state.reserve_upload(
        source,
        first_task,
        target_channel_id=target,
        connection_id=connection,
    )
    assert created is True
    assert record['status'] == 'reserved'

    # If the web process died before apply_async returned, the unconfirmed
    # reservation is replaceable and cannot permanently strand the source.
    recovered, created = publish_state.reserve_upload(
        source,
        second_task,
        target_channel_id=target,
        connection_id=connection,
    )
    assert created is True
    assert recovered['publish_task_id'] == second_task
    publish_state.mark_upload_enqueued(source, second_task)

    duplicate, created = publish_state.reserve_upload(
        source,
        first_task,
        target_channel_id=target,
        connection_id=connection,
    )
    assert created is False
    assert duplicate['publish_task_id'] == second_task

    publish_state.mark_upload_started(source, second_task)
    publish_state.mark_upload_uncertain(source, second_task, 'network_error')
    uncertain, created = publish_state.reserve_upload(
        source,
        first_task,
        target_channel_id=target,
        connection_id=connection,
    )
    assert created is False
    assert uncertain['status'] == 'uncertain'
    assert uncertain['side_effect_possible'] is True


def test_preflight_failure_can_be_safely_reserved_again(monkeypatch):
    client = FakeRedis()
    monkeypatch.setattr(publish_state, '_redis', lambda: client)
    source = 'source-task-9012'
    first_task = 'publish-task-9012'
    second_task = 'publish-task-3456'
    reservation = {
        'target_channel_id': 'UC_channel_9012',
        'connection_id': 'connection-id-9012',
    }
    publish_state.reserve_upload(source, first_task, **reservation)
    publish_state.mark_upload_preflight_failed(source, first_task, 'oauth_missing')

    record, created = publish_state.reserve_upload(
        source,
        second_task,
        **reservation,
    )
    assert created is True
    assert record['publish_task_id'] == second_task
    assert record['side_effect_possible'] is False


def test_completed_upload_is_terminal_against_late_worker_failure(monkeypatch):
    client = FakeRedis()
    monkeypatch.setattr(publish_state, '_redis', lambda: client)
    source = 'source-task-6060'
    task = 'publish-task-6060'
    publish_state.reserve_upload(
        source,
        task,
        target_channel_id='UC_channel_6060',
        connection_id='connection-id-6060',
    )
    publish_state.mark_upload_enqueued(source, task)
    publish_state.mark_upload_started(source, task)
    publish_state.mark_upload_completed(source, task, 'YT_COMPLETE_2')

    after_preflight = publish_state.mark_upload_preflight_failed(
        source,
        task,
        'late_redelivery',
    )
    after_uncertain = publish_state.mark_upload_uncertain(
        source,
        task,
        'late_redelivery',
    )

    assert after_preflight['status'] == 'complete'
    assert after_uncertain['status'] == 'complete'
    assert publish_state.get_upload_record(source)['youtube_video_id'] == 'YT_COMPLETE_2'


def _import_publish_tasks_with_stubs(monkeypatch):
    celery_module = types.ModuleType('app.celery_app')
    task_options = {}

    class Celery:
        def task(self, **options):
            task_options.update(options)
            return lambda function: function

    celery_module.celery = Celery()
    storage_module = types.ModuleType('app.services.storage')
    storage_module.download_file = lambda *_a, **_k: None
    state_module = types.ModuleType('app.services.studio_state')
    for name in (
        'create_job',
        'get_job',
        'list_jobs',
        'mark_failure',
        'mark_success',
        'set_stage',
        'update_job',
    ):
        setattr(state_module, name, lambda *_a, **_k: None)
    monkeypatch.setitem(sys.modules, 'app.celery_app', celery_module)
    monkeypatch.setitem(sys.modules, 'app.services.storage', storage_module)
    monkeypatch.setitem(sys.modules, 'app.services.studio_state', state_module)
    sys.modules.pop('app.publish_tasks', None)
    module = importlib.import_module('app.publish_tasks')
    module._test_task_options = task_options
    return module


def test_publish_pipeline_crosses_registry_boundary_before_private_insert(
    monkeypatch,
    tmp_path,
):
    module = _import_publish_tasks_with_stubs(monkeypatch)
    monkeypatch.setattr(module, 'Path', lambda _value: tmp_path / 'youtube_publish')
    events = []
    source_id = 'source-task-7777'
    publish_id = 'publish-task-7777'

    class Task:
        request = types.SimpleNamespace(id=publish_id)

        def update_state(self, **_kwargs):
            pass

    monkeypatch.setattr(
        module,
        'get_upload_record',
        lambda source: {
            'source_task_id': source,
            'publish_task_id': publish_id,
            'status': 'queued',
            'side_effect_possible': False,
            'target_channel_id': 'UC_verified',
            'connection_id': 'connection-id-7777',
        },
    )
    monkeypatch.setattr(module, 'acquire_execution_lock', lambda *_a: 'lock-token')
    monkeypatch.setattr(module, 'release_execution_lock', lambda *_a: events.append('release'))
    monkeypatch.setattr(
        module,
        'get_job',
        lambda _task: {
            'state': 'SUCCESS',
            'kind': 'render',
            'spec': {'language': 'tr'},
            'result': {'video_key': 'videos/source/final.mp4', 'title': 'Title'},
        },
    )
    monkeypatch.setattr(module, 'load_credentials', lambda **_k: object())
    def refresh_channel(_credentials, **kwargs):
        events.append((
            'channel-verified',
            kwargs['expected_channel_id'],
            kwargs['expected_connection_id'],
        ))
        return {
            'id': kwargs['expected_channel_id'],
            'connection_id': kwargs['expected_connection_id'],
            'title': 'Channel',
        }

    monkeypatch.setattr(module, 'refresh_channel_info', refresh_channel)

    def download(_key, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b'video')

    monkeypatch.setattr(module, 'download_file', download)
    monkeypatch.setattr(module, 'set_stage', lambda *_a, **_k: None)
    monkeypatch.setattr(
        module,
        'mark_upload_started',
        lambda *_a: events.append('registry-started'),
    )

    def upload(_credentials, _path, _title, _description, **kwargs):
        events.append(('insert', kwargs['privacy_status']))
        kwargs['progress_callback'](1.0)
        return {'id': 'YT_PRIVATE_1', 'status': {'privacyStatus': 'private'}}

    monkeypatch.setattr(module, 'upload_video_with_credentials', upload)
    monkeypatch.setattr(
        module,
        'mark_upload_completed',
        lambda *_a: events.append('registry-complete'),
    )
    monkeypatch.setattr(module, 'update_job', lambda *_a, **_k: None)
    monkeypatch.setattr(module, 'mark_success', lambda *_a, **_k: None)
    monkeypatch.setattr(module, 'mark_failure', lambda *_a, **_k: None)

    result = module.publish_video_pipeline(Task(), source_id, 'public')

    assert result['privacy_status'] == 'private'
    assert module._test_task_options == {
        'bind': True,
        'acks_late': True,
        'reject_on_worker_lost': True,
    }
    assert (
        'channel-verified',
        'UC_verified',
        'connection-id-7777',
    ) in events
    assert events.index('registry-started') < events.index(('insert', 'private'))
    assert events.index('registry-complete') > events.index(('insert', 'private'))
    assert events[-1] == 'release'


def test_publish_pipeline_marks_uncertain_and_never_retries_insert(
    monkeypatch,
    tmp_path,
):
    module = _import_publish_tasks_with_stubs(monkeypatch)
    monkeypatch.setattr(module, 'Path', lambda _value: tmp_path / 'youtube_publish')
    events = []
    source_id = 'source-task-8888'
    publish_id = 'publish-task-8888'

    class Task:
        request = types.SimpleNamespace(id=publish_id)

        def update_state(self, **_kwargs):
            pass

    monkeypatch.setattr(
        module,
        'get_upload_record',
        lambda *_a: {
            'publish_task_id': publish_id,
            'status': 'queued',
            'side_effect_possible': False,
            'target_channel_id': 'UC_verified',
            'connection_id': 'connection-id-8888',
        },
    )
    monkeypatch.setattr(module, 'acquire_execution_lock', lambda *_a: 'lock-token')
    monkeypatch.setattr(module, 'release_execution_lock', lambda *_a: None)
    monkeypatch.setattr(
        module,
        'get_job',
        lambda _task: {
            'state': 'SUCCESS',
            'spec': {},
            'result': {'video_key': 'videos/source/final.mp4'},
        },
    )
    monkeypatch.setattr(module, 'load_credentials', lambda **_k: object())
    monkeypatch.setattr(
        module,
        'refresh_channel_info',
        lambda _c, **_kwargs: {'id': 'UC_verified'},
    )

    def download(_key, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b'video')

    monkeypatch.setattr(module, 'download_file', download)
    monkeypatch.setattr(module, 'set_stage', lambda *_a, **_k: None)
    monkeypatch.setattr(
        module,
        'mark_upload_started',
        lambda *_a: events.append('started'),
    )
    monkeypatch.setattr(
        module,
        'upload_video_with_credentials',
        lambda *_a, **_k: (_ for _ in ()).throw(ConnectionError('secret-safe test')),
    )
    monkeypatch.setattr(
        module,
        'mark_upload_uncertain',
        lambda *_a: events.append('uncertain'),
    )
    monkeypatch.setattr(
        module,
        'mark_upload_preflight_failed',
        lambda *_a: events.append('preflight'),
    )
    monkeypatch.setattr(module, 'mark_failure', lambda *_a, **_k: None)

    with pytest.raises(RuntimeError, match='uncertain'):
        module.publish_video_pipeline(Task(), source_id)
    assert events == ['started', 'uncertain']


def test_completed_registry_reconciles_source_without_second_insert(
    monkeypatch,
    tmp_path,
):
    module = _import_publish_tasks_with_stubs(monkeypatch)
    monkeypatch.setattr(module, 'Path', lambda _value: tmp_path / 'youtube_publish')
    source_id = 'source-task-9999'
    recovery_task_id = 'publish-task-recovery'
    updates = []
    successes = []

    class Task:
        request = types.SimpleNamespace(id=recovery_task_id)

        def update_state(self, **_kwargs):
            pass

    monkeypatch.setattr(
        module,
        'get_upload_record',
        lambda _source: {
            'source_task_id': source_id,
            'publish_task_id': 'publish-task-original',
            'status': 'complete',
            'side_effect_possible': True,
            'youtube_video_id': 'YT_COMPLETE_1',
            'completed_at': '2026-08-31T02:00:00+00:00',
            'target_channel_id': 'UC_original',
            'connection_id': 'connection-id-original',
        },
    )
    monkeypatch.setattr(
        module,
        'get_job',
        lambda _source: {
            'state': 'SUCCESS',
            'result': {'video_key': 'videos/source/final.mp4'},
        },
    )
    monkeypatch.setattr(
        module,
        'update_job',
        lambda task_id, **kwargs: updates.append((task_id, kwargs)),
    )
    monkeypatch.setattr(
        module,
        'mark_success',
        lambda task_id, result: successes.append((task_id, result)),
    )
    monkeypatch.setattr(
        module,
        'acquire_execution_lock',
        lambda *_a: (_ for _ in ()).throw(AssertionError('must not acquire')),
    )
    monkeypatch.setattr(
        module,
        'upload_video_with_credentials',
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError('must not insert')),
    )

    result = module.publish_video_pipeline(Task(), source_id)

    assert result['idempotent_replay'] is True
    assert result['youtube_video_id'] == 'YT_COMPLETE_1'
    assert updates[0][0] == source_id
    assert updates[0][1]['result']['youtube']['video_id'] == 'YT_COMPLETE_1'
    assert successes[0][0] == recovery_task_id


def test_mutating_youtube_routes_require_exact_same_origin(monkeypatch):
    module = _import_publish_tasks_with_stubs(monkeypatch)
    module.publish_video_pipeline = types.SimpleNamespace(apply_async=lambda **_k: None)
    sys.modules.pop('app.youtube_routes', None)
    routes = importlib.import_module('app.youtube_routes')
    monkeypatch.setattr(
        routes.settings,
        'google_redirect_uri',
        'https://studio.example.test/studio/youtube/callback',
        raising=False,
    )

    routes._require_same_origin(types.SimpleNamespace(
        headers={'origin': 'https://studio.example.test'},
    ))
    routes._require_same_origin(types.SimpleNamespace(
        headers={'referer': 'https://studio.example.test/studio/youtube'},
    ))
    with pytest.raises(routes.HTTPException) as wrong_origin:
        routes._require_same_origin(types.SimpleNamespace(
            headers={'origin': 'https://evil.studio.example.test'},
        ))
    assert wrong_origin.value.status_code == 403
    with pytest.raises(routes.HTTPException) as missing_proof:
        routes._require_same_origin(types.SimpleNamespace(headers={}))
    assert missing_proof.value.status_code == 403

    monkeypatch.setattr(
        routes.settings,
        'google_redirect_uri',
        '',
        raising=False,
    )
    routes._require_same_origin(types.SimpleNamespace(
        base_url='https://studio-fallback.example.test/',
        headers={'origin': 'https://studio-fallback.example.test'},
    ))


def test_studio_router_mounts_secure_youtube_lifecycle(monkeypatch):
    celery_module = types.ModuleType('app.celery_app')

    class Celery:
        def task(self, **_options):
            return lambda function: function

    celery_module.celery = Celery()
    tasks_module = types.ModuleType('app.tasks')
    tasks_module.plan_video_pipeline = types.SimpleNamespace(delay=lambda *_a, **_k: None)
    tasks_module.run_video_pipeline = types.SimpleNamespace(delay=lambda *_a, **_k: None)
    storage_module = types.ModuleType('app.services.storage')
    storage_module.download_file = lambda *_a, **_k: None
    state_module = types.ModuleType('app.services.studio_state')
    for name in (
        'create_job',
        'get_job',
        'list_jobs',
        'mark_failure',
        'mark_success',
        'set_stage',
        'update_job',
    ):
        setattr(state_module, name, lambda *_a, **_k: None)
    voice_module = types.ModuleType('app.services.voice')
    voice_module.get_selected_voice = lambda: {'name': 'Test voice'}
    monkeypatch.setitem(sys.modules, 'app.celery_app', celery_module)
    monkeypatch.setitem(sys.modules, 'app.tasks', tasks_module)
    monkeypatch.setitem(sys.modules, 'app.services.storage', storage_module)
    monkeypatch.setitem(sys.modules, 'app.services.studio_state', state_module)
    monkeypatch.setitem(sys.modules, 'app.services.voice', voice_module)
    sys.modules.pop('app.publish_tasks', None)
    sys.modules.pop('app.youtube_routes', None)
    sys.modules.pop('app.studio', None)

    studio = importlib.import_module('app.studio')
    methods_by_path = {
        route.path: set(route.methods or set())
        for route in studio.router.routes
    }

    assert methods_by_path['/studio/youtube/connect'] == {'POST'}
    assert methods_by_path['/studio/youtube/callback'] == {'GET'}
    assert methods_by_path['/studio/youtube/status'] == {'GET'}
    assert methods_by_path['/studio/youtube/disconnect'] == {'POST'}
    assert methods_by_path['/studio/youtube/publish/{source_task_id}'] == {'POST'}
    assert methods_by_path['/studio/logout'] == {'POST'}
    assert all('public' not in path for path in methods_by_path)
