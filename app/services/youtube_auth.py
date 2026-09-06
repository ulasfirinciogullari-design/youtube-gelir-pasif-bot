from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
import secrets
from typing import Any
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
import httpx
import redis

from app.config import settings


SCOPES = [
    'https://www.googleapis.com/auth/youtube.upload',
    'https://www.googleapis.com/auth/youtube.readonly',
    'https://www.googleapis.com/auth/youtube.force-ssl',
]

CREDENTIAL_KEY = 'youtube_studio:oauth:refresh_token:v2'
CHANNEL_KEY = 'youtube_studio:oauth:channel:v2'
LEGACY_CREDENTIAL_KEY = 'youtube_studio:oauth:credentials'
LEGACY_CHANNEL_KEY = 'youtube_studio:oauth:channel'
CHANNEL_INDEX_KEY = 'youtube_studio:oauth:channels:v3'
CREDENTIAL_PREFIX = 'youtube_studio:oauth:credential:v3:'
CHANNEL_PREFIX = 'youtube_studio:oauth:channel:v3:'
MAX_CONNECTIONS = 10
STATE_PREFIX = 'youtube_studio:oauth:state:v2:'
AUTH_EPOCH_KEY = 'youtube_studio:oauth:authorization_epoch:v2'
STATE_TTL_SECONDS = 10 * 60
TOKEN_URI = 'https://oauth2.googleapis.com/token'
REVOCATION_URI = 'https://oauth2.googleapis.com/revoke'
AUTH_URI = 'https://accounts.google.com/o/oauth2/v2/auth'

_STATE_PATTERN = re.compile(r'^[A-Za-z0-9_-]{40,128}$')
_BINDING_PATTERN = re.compile(r'^[A-Za-z0-9_-]{40,128}$')
_CHANNEL_ID_PATTERN = re.compile(r'^[A-Za-z0-9_-]{8,128}$')

_CLAIM_EPOCH_SCRIPT = '''
local current = redis.call('GET', KEYS[1]) or '0'
if current ~= ARGV[1] then
  return 0
end
return redis.call('INCR', KEYS[1])
'''

_COMMIT_CONNECTION_SCRIPT = '''
local current = redis.call('GET', KEYS[1]) or '0'
if current ~= ARGV[1] then
  return 0
end
if redis.call('SISMEMBER', KEYS[2], ARGV[2]) == 0 and redis.call('SCARD', KEYS[2]) >= tonumber(ARGV[5]) then
  return -1
end
redis.call('SET', KEYS[3], ARGV[3])
redis.call('SET', KEYS[4], ARGV[4])
redis.call('SADD', KEYS[2], ARGV[2])
redis.call('DEL', KEYS[5], KEYS[6], KEYS[7], KEYS[8])
redis.call('INCR', KEYS[1])
return 1
'''

_CLEAR_IF_CREDENTIAL_MATCHES_SCRIPT = '''
if redis.call('GET', KEYS[1]) == ARGV[1] then
  redis.call('DEL', KEYS[1], KEYS[2])
  redis.call('SREM', KEYS[3], ARGV[2])
  return 1
end
return 0
'''

_CLEAR_IF_CHANNEL_MATCHES_SCRIPT = '''
if redis.call('GET', KEYS[2]) == ARGV[1] then
  redis.call('DEL', KEYS[1], KEYS[2])
  redis.call('SREM', KEYS[3], ARGV[2])
  return 1
end
return 0
'''

_UPDATE_CHANNEL_IF_CURRENT_SCRIPT = '''
if not redis.call('GET', KEYS[1]) then
  return 0
end
if redis.call('GET', KEYS[2]) ~= ARGV[1] then
  return 0
end
redis.call('SET', KEYS[2], ARGV[2])
return 1
'''

_DISCONNECT_IF_CURRENT_SCRIPT = '''
if redis.call('GET', KEYS[1]) ~= ARGV[1] then
  return 0
end
if redis.call('GET', KEYS[2]) ~= ARGV[2] then
  return 0
end
redis.call('DEL', KEYS[1], KEYS[2])
redis.call('SREM', KEYS[3], ARGV[3])
redis.call('INCR', KEYS[4])
return 1
'''

_MIGRATE_V2_SCRIPT = '''
if redis.call('EXISTS', KEYS[2]) == 1 or redis.call('EXISTS', KEYS[3]) == 1 then
  return 2
end
if redis.call('SISMEMBER', KEYS[1], ARGV[1]) == 0 and redis.call('SCARD', KEYS[1]) >= tonumber(ARGV[6]) then
  return -1
end
if redis.call('GET', KEYS[4]) ~= ARGV[2] or redis.call('GET', KEYS[5]) ~= ARGV[3] then
  return 0
end
redis.call('SET', KEYS[2], ARGV[4])
redis.call('SET', KEYS[3], ARGV[5])
redis.call('SADD', KEYS[1], ARGV[1])
redis.call('DEL', KEYS[4], KEYS[5], KEYS[6], KEYS[7])
return 1
'''

_CLEAR_STALE_V2_IF_V3_CURRENT_SCRIPT = '''
if redis.call('GET', KEYS[1]) ~= ARGV[1] or redis.call('GET', KEYS[2]) ~= ARGV[2] then
  return 0
end
if redis.call('SISMEMBER', KEYS[3], ARGV[5]) == 0 then
  return 0
end
if redis.call('GET', KEYS[4]) ~= ARGV[3] or redis.call('GET', KEYS[5]) ~= ARGV[4] then
  return 0
end
redis.call('DEL', KEYS[4], KEYS[5], KEYS[6], KEYS[7])
return 1
'''


class YouTubeAuthError(RuntimeError):
    """Safe, user-displayable authorization failure."""


class OAuthConfigurationError(YouTubeAuthError):
    pass


class OAuthStateError(YouTubeAuthError):
    pass


class OAuthStorageError(YouTubeAuthError):
    pass


class AuthorizationRevokedError(YouTubeAuthError):
    pass


def _redis() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


def _fernet() -> Fernet:
    # Never fall back to a session or API token. OAuth credentials have their
    # own encryption boundary and cannot be decrypted without the dedicated key.
    material = str(settings.app_encryption_key or '').strip()
    if not material:
        raise OAuthConfigurationError('OAuth credential encryption is not configured')
    digest = hashlib.sha256(material.encode('utf-8')).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _binding_digest(binding: str) -> str:
    binding = str(binding or '')
    if not _BINDING_PATTERN.fullmatch(binding):
        raise OAuthStateError('OAuth browser binding is missing or invalid')
    material = str(settings.app_encryption_key or '').strip().encode('utf-8')
    if not material:
        raise OAuthConfigurationError('OAuth credential encryption is not configured')
    return hmac.new(material, binding.encode('ascii'), hashlib.sha256).hexdigest()


def _state_key(state: str, binding_digest: str) -> str:
    return f'{STATE_PREFIX}{state}:{binding_digest}'


def _rotate_authorization_epoch(client: redis.Redis | None = None) -> int:
    client = client or _redis()
    try:
        return int(client.incr(AUTH_EPOCH_KEY))
    except Exception as exc:
        raise OAuthStorageError('OAuth authorization generation is unavailable') from exc


def invalidate_pending_authorizations() -> int:
    """Logically invalidate every pending state across every web instance."""
    return _rotate_authorization_epoch()


def _claim_authorization_epoch(expected_epoch: int) -> int:
    try:
        claimed = int(_redis().eval(
            _CLAIM_EPOCH_SCRIPT,
            1,
            AUTH_EPOCH_KEY,
            str(int(expected_epoch)),
        ))
    except Exception as exc:
        raise OAuthStorageError('OAuth authorization generation is unavailable') from exc
    if claimed <= 0:
        raise OAuthStateError('OAuth state was superseded by a newer authorization')
    return claimed


def _validate_redirect_uri(value: str) -> str:
    value = str(value or '').strip()
    parsed = urlparse(value)
    local_hosts = {'localhost', '127.0.0.1', '::1'}
    valid_scheme = parsed.scheme == 'https' or (
        parsed.scheme == 'http' and parsed.hostname in local_hosts
    )
    if (
        not value
        or not valid_scheme
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise OAuthConfigurationError('Google OAuth redirect URI is invalid')
    return value


def oauth_configured() -> bool:
    if not (
        settings.google_client_id
        and settings.google_client_secret
        and settings.google_redirect_uri
        and settings.app_encryption_key
    ):
        return False
    try:
        _validate_redirect_uri(settings.google_redirect_uri)
    except OAuthConfigurationError:
        return False
    return True


def _client_config() -> dict[str, Any]:
    if not oauth_configured():
        raise OAuthConfigurationError('Google OAuth is not fully configured')
    return {
        'web': {
            'client_id': settings.google_client_id,
            'client_secret': settings.google_client_secret,
            'auth_uri': AUTH_URI,
            'token_uri': TOKEN_URI,
            'redirect_uris': [_validate_redirect_uri(settings.google_redirect_uri)],
        }
    }


def _encrypt_json(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(',', ':'),
    ).encode('utf-8')
    return _fernet().encrypt(encoded).decode('ascii')


def _decrypt_json(encoded: str) -> dict[str, Any]:
    try:
        raw = _fernet().decrypt(str(encoded).encode('ascii')).decode('utf-8')
        payload = json.loads(raw)
    except (InvalidToken, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise OAuthStorageError('Stored OAuth credentials could not be read') from exc
    if not isinstance(payload, dict):
        raise OAuthStorageError('Stored OAuth credentials could not be read')
    return payload


def _credential_from_refresh_token(refresh_token: str) -> Credentials:
    return Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=TOKEN_URI,
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        scopes=SCOPES,
    )


def _safe_channel_id(value: str) -> str:
    channel_id = str(value or '').strip()
    if not _CHANNEL_ID_PATTERN.fullmatch(channel_id):
        raise YouTubeAuthError('YouTube channel identifier is invalid')
    return channel_id


def _credential_key(channel_id: str) -> str:
    return CREDENTIAL_PREFIX + _safe_channel_id(channel_id)


def _channel_key(channel_id: str) -> str:
    return CHANNEL_PREFIX + _safe_channel_id(channel_id)


def _decode_channel(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        channel = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise OAuthStorageError('Stored YouTube channel data could not be read') from exc
    if not isinstance(channel, dict):
        raise OAuthStorageError('Stored YouTube channel data could not be read')
    channel_id = _safe_channel_id(str(channel.get('id') or ''))
    connection_id = str(channel.get('connection_id') or '').strip()
    if not _CHANNEL_ID_PATTERN.fullmatch(connection_id):
        raise OAuthStorageError('Stored YouTube channel data could not be read')
    channel['id'] = channel_id
    channel['connection_id'] = connection_id
    return channel


def _migrate_v2_connection(client: redis.Redis | None = None) -> None:
    """Atomically adopt the singleton v2 record without overwriting v3."""
    client = client or _redis()
    try:
        old_encrypted = client.get(CREDENTIAL_KEY)
        old_channel_raw = client.get(CHANNEL_KEY)
    except Exception as exc:
        raise OAuthStorageError('OAuth credential storage is unavailable') from exc
    if not old_encrypted or not old_channel_raw:
        return
    old_channel = _decode_channel(str(old_channel_raw))
    if not old_channel:
        return
    old_payload = _decrypt_json(str(old_encrypted))
    if old_payload.get('version') != 2:
        raise OAuthStorageError('Stored OAuth credentials use an unsupported format')
    refresh_token = old_payload.get('refresh_token')
    if not isinstance(refresh_token, str) or not refresh_token or len(refresh_token) > 4096:
        raise OAuthStorageError('Stored OAuth credentials could not be read')
    channel_id = old_channel['id']
    connection_id = old_channel['connection_id']
    try:
        v3_encrypted = client.get(_credential_key(channel_id))
        v3_channel_raw = client.get(_channel_key(channel_id))
    except Exception as exc:
        raise OAuthStorageError('OAuth credential storage is unavailable') from exc
    if v3_encrypted or v3_channel_raw:
        # Never reactivate a stale v2 generation after a successful v3
        # reconnect. Delete it only after both v3 halves are complete, bound to
        # the same channel/generation, and still match the values just read.
        if not v3_encrypted or not v3_channel_raw:
            return
        v3_payload = _decrypt_json(str(v3_encrypted))
        v3_channel = _decode_channel(str(v3_channel_raw))
        v3_refresh_token = v3_payload.get('refresh_token')
        if (
            v3_payload.get('version') != 3
            or str(v3_payload.get('channel_id') or '') != channel_id
            or not isinstance(v3_refresh_token, str)
            or not v3_refresh_token
            or len(v3_refresh_token) > 4096
            or not v3_channel
            or v3_channel.get('id') != channel_id
            or str(v3_payload.get('connection_id') or '')
            != str(v3_channel.get('connection_id') or '')
        ):
            return
        try:
            client.eval(
                _CLEAR_STALE_V2_IF_V3_CURRENT_SCRIPT,
                7,
                _credential_key(channel_id),
                _channel_key(channel_id),
                CHANNEL_INDEX_KEY,
                CREDENTIAL_KEY,
                CHANNEL_KEY,
                LEGACY_CREDENTIAL_KEY,
                LEGACY_CHANNEL_KEY,
                str(v3_encrypted),
                str(v3_channel_raw),
                str(old_encrypted),
                str(old_channel_raw),
                channel_id,
            )
        except Exception as exc:
            raise OAuthStorageError('OAuth credential storage is unavailable') from exc
        return
    encrypted = _encrypt_json({
        'version': 3,
        'channel_id': channel_id,
        'connection_id': connection_id,
        'refresh_token': refresh_token,
        'scopes': old_payload.get('scopes') or SCOPES,
    })
    channel_raw = json.dumps(old_channel, ensure_ascii=False, separators=(',', ':'))
    try:
        result = int(client.eval(
            _MIGRATE_V2_SCRIPT,
            7,
            CHANNEL_INDEX_KEY,
            _credential_key(channel_id),
            _channel_key(channel_id),
            CREDENTIAL_KEY,
            CHANNEL_KEY,
            LEGACY_CREDENTIAL_KEY,
            LEGACY_CHANNEL_KEY,
            channel_id,
            str(old_encrypted),
            str(old_channel_raw),
            encrypted,
            channel_raw,
            MAX_CONNECTIONS,
        ))
    except Exception as exc:
        raise OAuthStorageError('OAuth credential storage is unavailable') from exc
    if result < 0:
        raise OAuthStorageError('YouTube connection limit has been reached')


def _read_credential_cipher_and_payload(
    channel_id: str,
    *,
    client: redis.Redis | None = None,
) -> tuple[str | None, dict[str, Any] | None]:
    channel_id = _safe_channel_id(channel_id)
    client = client or _redis()
    _migrate_v2_connection(client)
    try:
        encrypted = client.get(_credential_key(channel_id))
    except Exception as exc:
        raise OAuthStorageError('OAuth credential storage is unavailable') from exc
    if not encrypted:
        return None, None
    payload = _decrypt_json(str(encrypted))
    if payload.get('version') != 3:
        raise OAuthStorageError('Stored OAuth credentials use an unsupported format')
    if str(payload.get('channel_id') or '') != channel_id:
        raise OAuthStorageError('Stored OAuth credentials do not match the target channel')
    connection_id = str(payload.get('connection_id') or '').strip()
    refresh_token = payload.get('refresh_token')
    if (
        not _CHANNEL_ID_PATTERN.fullmatch(connection_id)
        or not isinstance(refresh_token, str)
        or not refresh_token
        or len(refresh_token) > 4096
    ):
        raise OAuthStorageError('Stored OAuth credentials could not be read')
    return str(encrypted), payload


def _clear_connection_if_credential_matches(
    channel_id: str,
    encrypted: str,
) -> None:
    channel_id = _safe_channel_id(channel_id)
    try:
        _redis().eval(
            _CLEAR_IF_CREDENTIAL_MATCHES_SCRIPT,
            3,
            _credential_key(channel_id),
            _channel_key(channel_id),
            CHANNEL_INDEX_KEY,
            encrypted,
            channel_id,
        )
    except Exception as exc:
        raise OAuthStorageError('OAuth credential storage is unavailable') from exc


def _clear_connection_if_channel_matches(
    channel_id: str,
    channel_json: str,
) -> None:
    channel_id = _safe_channel_id(channel_id)
    try:
        _redis().eval(
            _CLEAR_IF_CHANNEL_MATCHES_SCRIPT,
            3,
            _credential_key(channel_id),
            _channel_key(channel_id),
            CHANNEL_INDEX_KEY,
            channel_json,
            channel_id,
        )
    except Exception as exc:
        raise OAuthStorageError('OAuth credential storage is unavailable') from exc


def _refresh_error_is_revoked(exc: RefreshError) -> bool:
    value = ' '.join(str(item) for item in getattr(exc, 'args', ()) or (exc,)).lower()
    return any(
        marker in value
        for marker in (
            'invalid_grant',
            'invalid_rapt',
            'token has been expired or revoked',
            'invalid refresh token',
        )
    )


def load_credentials(
    channel_id: str,
    *,
    expected_connection_id: str | None = None,
    refresh: bool = True,
) -> Credentials | None:
    channel_id = _safe_channel_id(channel_id)
    encrypted, payload = _read_credential_cipher_and_payload(channel_id)
    if not encrypted or not payload:
        return None
    connection_id = str(payload['connection_id'])
    _channel_raw, channel = _read_channel_record(channel_id)
    if not channel or str(channel.get('connection_id') or '') != connection_id:
        raise OAuthStorageError('Stored YouTube connection records do not match')
    if expected_connection_id and connection_id != str(expected_connection_id):
        raise YouTubeAuthError('YouTube connection changed before upload')
    refresh_token = str(payload['refresh_token'])
    credentials = _credential_from_refresh_token(refresh_token)
    if not refresh:
        return credentials
    try:
        credentials.refresh(GoogleRequest())
    except RefreshError as exc:
        if _refresh_error_is_revoked(exc):
            _clear_connection_if_credential_matches(channel_id, encrypted)
            raise AuthorizationRevokedError(
                'Google authorization expired or was revoked; reconnect is required'
            ) from exc
        raise YouTubeAuthError('Google authorization could not be refreshed') from exc
    except Exception as exc:
        raise YouTubeAuthError('Google authorization could not be refreshed') from exc
    if not credentials.token:
        raise YouTubeAuthError('Google authorization could not be refreshed')
    return credentials


def _channel_from_credentials(credentials: Credentials) -> dict[str, Any]:
    try:
        youtube = build(
            'youtube',
            'v3',
            credentials=credentials,
            cache_discovery=False,
        )
        response = youtube.channels().list(
            part='id,snippet,statistics',
            mine=True,
            maxResults=1,
        ).execute(num_retries=2)
    except Exception as exc:
        status = getattr(getattr(exc, 'resp', None), 'status', None)
        if status == 401:
            raise AuthorizationRevokedError(
                'Google authorization expired or was revoked; reconnect is required'
            ) from exc
        raise YouTubeAuthError('YouTube channel verification failed') from exc

    items = response.get('items') if isinstance(response, dict) else None
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        raise YouTubeAuthError('No YouTube channel was found for this Google account')
    item = items[0]
    channel_id = str(item.get('id') or '').strip()
    snippet = item.get('snippet') if isinstance(item.get('snippet'), dict) else {}
    title = str(snippet.get('title') or '').strip()
    if not channel_id or not title:
        raise YouTubeAuthError('YouTube channel verification returned incomplete data')
    statistics = item.get('statistics') if isinstance(item.get('statistics'), dict) else {}
    thumbnails = snippet.get('thumbnails') if isinstance(snippet.get('thumbnails'), dict) else {}
    default_thumbnail = thumbnails.get('default') if isinstance(thumbnails.get('default'), dict) else {}
    now = datetime.now(timezone.utc).isoformat()
    return {
        'id': channel_id,
        'title': title,
        'description': str(snippet.get('description') or ''),
        'thumbnail_url': default_thumbnail.get('url'),
        'subscriber_count': statistics.get('subscriberCount'),
        'video_count': statistics.get('videoCount'),
        'view_count': statistics.get('viewCount'),
        'connected_at': now,
        'verified_at': now,
    }


def _persist_connection(
    credentials: Credentials,
    channel: dict[str, Any],
    *,
    expected_epoch: int,
) -> None:
    refresh_token = str(credentials.refresh_token or '').strip()
    if not refresh_token:
        raise YouTubeAuthError(
            'Google did not return offline authorization; reconnect and grant access again'
        )
    channel_id = _safe_channel_id(str(channel.get('id') or ''))
    connection_id = str(channel.get('connection_id') or '').strip()
    if not _CHANNEL_ID_PATTERN.fullmatch(connection_id):
        raise YouTubeAuthError('YouTube connection generation is invalid')
    encrypted = _encrypt_json({
        'version': 3,
        'channel_id': channel_id,
        'connection_id': connection_id,
        'refresh_token': refresh_token,
        'scopes': SCOPES,
    })
    channel_json = json.dumps(channel, ensure_ascii=False, separators=(',', ':'))
    client = _redis()
    # A callback that was already in flight during a v2 -> v3 deployment must
    # not delete the previously connected singleton while adding a new channel.
    _migrate_v2_connection(client)
    try:
        committed = client.eval(
            _COMMIT_CONNECTION_SCRIPT,
            8,
            AUTH_EPOCH_KEY,
            CHANNEL_INDEX_KEY,
            _credential_key(channel_id),
            _channel_key(channel_id),
            CREDENTIAL_KEY,
            CHANNEL_KEY,
            LEGACY_CREDENTIAL_KEY,
            LEGACY_CHANNEL_KEY,
            str(int(expected_epoch)),
            channel_id,
            encrypted,
            channel_json,
            MAX_CONNECTIONS,
        )
    except Exception as exc:
        raise OAuthStorageError('OAuth credential storage is unavailable') from exc
    if int(committed) < 0:
        raise YouTubeAuthError(
            f'YouTube connection limit is {MAX_CONNECTIONS}; disconnect a channel first'
        )
    if not committed:
        raise OAuthStateError(
            'OAuth authorization was superseded before it could be saved'
        )


def build_authorization_url(session_binding: str) -> str:
    redirect_uri = _validate_redirect_uri(settings.google_redirect_uri)
    binding_digest = _binding_digest(session_binding)
    # Starting a new browser flow invalidates every older pending state.
    epoch = _rotate_authorization_epoch()
    state = secrets.token_urlsafe(32)
    flow = Flow.from_client_config(
        _client_config(),
        scopes=SCOPES,
        state=state,
        autogenerate_code_verifier=True,
    )
    flow.redirect_uri = redirect_uri
    authorization_url, returned_state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent',
    )
    if returned_state != state or not flow.code_verifier:
        raise YouTubeAuthError('Google authorization could not be initialized')
    state_payload = _encrypt_json({
        'version': 2,
        'redirect_uri': redirect_uri,
        'code_verifier': flow.code_verifier,
        'binding_digest': binding_digest,
        'authorization_epoch': epoch,
        'created_at': datetime.now(timezone.utc).isoformat(),
    })
    try:
        stored = _redis().set(
            _state_key(state, binding_digest),
            state_payload,
            ex=STATE_TTL_SECONDS,
            nx=True,
        )
    except Exception as exc:
        raise OAuthStorageError('OAuth state storage is unavailable') from exc
    if not stored:
        raise OAuthStorageError('OAuth state could not be created')
    return authorization_url


def _consume_state(state: str, session_binding: str) -> dict[str, Any]:
    state = str(state or '')
    if not _STATE_PATTERN.fullmatch(state):
        raise OAuthStateError('OAuth state expired, invalid, or already used')
    binding_digest = _binding_digest(session_binding)
    try:
        # GETDEL is atomic: concurrent callback replays cannot both exchange the
        # same authorization code, even when multiple web instances are running.
        encrypted = _redis().getdel(_state_key(state, binding_digest))
    except Exception as exc:
        raise OAuthStorageError('OAuth state storage is unavailable') from exc
    if not encrypted:
        raise OAuthStateError('OAuth state expired, invalid, or already used')
    payload = _decrypt_json(encrypted)
    if payload.get('version') != 2:
        raise OAuthStateError('OAuth state expired, invalid, or already used')
    if payload.get('redirect_uri') != _validate_redirect_uri(settings.google_redirect_uri):
        raise OAuthStateError('OAuth state expired, invalid, or already used')
    stored_binding = str(payload.get('binding_digest') or '')
    if not hmac.compare_digest(stored_binding, binding_digest):
        raise OAuthStateError('OAuth browser binding is missing or invalid')
    try:
        epoch = int(payload.get('authorization_epoch'))
    except (TypeError, ValueError) as exc:
        raise OAuthStateError('OAuth state expired, invalid, or already used') from exc
    if epoch <= 0:
        raise OAuthStateError('OAuth state expired, invalid, or already used')
    code_verifier = payload.get('code_verifier')
    if not isinstance(code_verifier, str) or not code_verifier:
        raise OAuthStateError('OAuth state expired, invalid, or already used')
    return payload


def discard_authorization_state(state: str, session_binding: str) -> None:
    _consume_state(state, session_binding)


def complete_authorization(
    code: str,
    state: str,
    session_binding: str,
) -> dict[str, Any]:
    code = str(code or '').strip()
    if not code or len(code) > 4096:
        # A valid state is still consumed so a malformed callback cannot be
        # retried later with a different code.
        _consume_state(state, session_binding)
        raise YouTubeAuthError('Google authorization response was incomplete')
    state_payload = _consume_state(state, session_binding)
    claimed_epoch = _claim_authorization_epoch(
        int(state_payload['authorization_epoch'])
    )
    flow = Flow.from_client_config(
        _client_config(),
        scopes=SCOPES,
        state=state,
        code_verifier=state_payload['code_verifier'],
    )
    flow.redirect_uri = state_payload['redirect_uri']
    try:
        flow.fetch_token(code=code)
    except Exception as exc:
        raise YouTubeAuthError('Google authorization exchange failed') from exc
    credentials = flow.credentials
    if not credentials or not credentials.refresh_token:
        raise YouTubeAuthError(
            'Google did not return offline authorization; reconnect and grant access again'
        )
    if hasattr(credentials, 'has_scopes') and not credentials.has_scopes(SCOPES):
        raise YouTubeAuthError('Required YouTube permissions were not granted')

    # Verify the exact authenticated channel before any token is persisted.
    channel = _channel_from_credentials(credentials)
    channel['connection_id'] = secrets.token_urlsafe(24)
    _persist_connection(
        credentials,
        channel,
        expected_epoch=claimed_epoch,
    )
    return channel


def _read_channel_record(
    channel_id: str,
    *,
    client: redis.Redis | None = None,
) -> tuple[str | None, dict[str, Any] | None]:
    channel_id = _safe_channel_id(channel_id)
    client = client or _redis()
    _migrate_v2_connection(client)
    try:
        raw = client.get(_channel_key(channel_id))
    except Exception as exc:
        raise OAuthStorageError('OAuth credential storage is unavailable') from exc
    channel = _decode_channel(str(raw)) if raw else None
    if channel and channel['id'] != channel_id:
        raise OAuthStorageError('Stored YouTube channel data does not match its key')
    return (str(raw), channel) if raw else (None, None)


def list_connections() -> list[dict[str, Any]]:
    client = _redis()
    _migrate_v2_connection(client)
    try:
        channel_ids = sorted(str(value) for value in client.smembers(CHANNEL_INDEX_KEY))
    except Exception as exc:
        raise OAuthStorageError('OAuth credential storage is unavailable') from exc
    connections = []
    for channel_id in channel_ids:
        try:
            _encrypted, payload = _read_credential_cipher_and_payload(
                channel_id,
                client=client,
            )
            _raw, channel = _read_channel_record(channel_id, client=client)
        except YouTubeAuthError:
            continue
        if (
            payload
            and channel
            and payload.get('connection_id') == channel.get('connection_id')
        ):
            connections.append(channel)
    return sorted(
        connections,
        key=lambda item: (
            str(item.get('connected_at') or ''),
            str(item.get('id') or ''),
        ),
    )


def get_channel_info(channel_id: str) -> dict[str, Any] | None:
    _raw, channel = _read_channel_record(channel_id)
    if not channel:
        return None
    _encrypted, payload = _read_credential_cipher_and_payload(channel_id)
    if not payload or payload.get('connection_id') != channel.get('connection_id'):
        raise OAuthStorageError('Stored YouTube connection records do not match')
    return channel


def refresh_channel_info(
    channel_id: str,
    credentials: Credentials | None = None,
    *,
    expected_connection_id: str | None = None,
) -> dict[str, Any]:
    channel_id = _safe_channel_id(channel_id)
    old_raw, stored_channel = _read_channel_record(channel_id)
    if not old_raw or not stored_channel:
        raise YouTubeAuthError('YouTube connection must be established again')
    stored_connection_id = str(stored_channel.get('connection_id') or '')
    if expected_connection_id and stored_connection_id != str(expected_connection_id):
        raise YouTubeAuthError('YouTube connection changed before upload')
    credentials = credentials or load_credentials(
        channel_id,
        expected_connection_id=stored_connection_id,
        refresh=True,
    )
    if not credentials:
        raise YouTubeAuthError('YouTube is not connected')
    try:
        channel = _channel_from_credentials(credentials)
    except AuthorizationRevokedError:
        _clear_connection_if_channel_matches(channel_id, old_raw)
        raise
    if str(channel.get('id') or '') != channel_id:
        raise YouTubeAuthError('YouTube target channel changed before upload')
    channel['connection_id'] = stored_connection_id
    channel['connected_at'] = stored_channel.get('connected_at') or channel['connected_at']
    new_raw = json.dumps(channel, ensure_ascii=False, separators=(',', ':'))
    try:
        updated = _redis().eval(
            _UPDATE_CHANNEL_IF_CURRENT_SCRIPT,
            2,
            _credential_key(channel_id),
            _channel_key(channel_id),
            old_raw,
            new_raw,
        )
    except Exception as exc:
        raise OAuthStorageError('OAuth credential storage is unavailable') from exc
    if not updated:
        raise YouTubeAuthError('YouTube connection changed before upload')
    return channel


def connection_status(
    *,
    channel_id: str | None = None,
    verify: bool = False,
) -> dict[str, Any]:
    configured = oauth_configured()
    if not configured:
        return {
            'configured': False,
            'connected': False,
            'requires_reconnect': False,
            'channel': None,
            'connections': [],
            'connection_count': 0,
            'connection_limit': MAX_CONNECTIONS,
            'redirect_uri': settings.google_redirect_uri or None,
        }
    try:
        connections = list_connections()
        selected = None
        if channel_id:
            selected = next(
                (item for item in connections if item.get('id') == channel_id),
                None,
            )
            if verify and selected:
                selected = refresh_channel_info(
                    channel_id,
                    expected_connection_id=str(selected.get('connection_id') or ''),
                )
                connections = [
                    selected if item.get('id') == channel_id else item
                    for item in connections
                ]
        elif len(connections) == 1:
            selected = connections[0]
            if verify:
                selected = refresh_channel_info(
                    str(selected['id']),
                    expected_connection_id=str(selected.get('connection_id') or ''),
                )
                connections = [selected]
    except AuthorizationRevokedError:
        connections = list_connections()
        selected = None
    except YouTubeAuthError:
        connections = []
        selected = None
        return {
            'configured': True,
            'connected': False,
            'requires_reconnect': True,
            'channel': None,
            'connections': [],
            'connection_count': 0,
            'connection_limit': MAX_CONNECTIONS,
            'redirect_uri': settings.google_redirect_uri,
        }
    return {
        'configured': True,
        'connected': bool(connections),
        'requires_reconnect': False,
        'channel': selected,
        'connections': connections,
        'connection_count': len(connections),
        'connection_limit': MAX_CONNECTIONS,
        'redirect_uri': settings.google_redirect_uri,
    }


def disconnect(
    channel_id: str,
    *,
    expected_connection_id: str | None = None,
    revoke: bool = True,
) -> bool:
    channel_id = _safe_channel_id(channel_id)
    client = _redis()
    _migrate_v2_connection(client)
    encrypted, payload = _read_credential_cipher_and_payload(channel_id, client=client)
    channel_raw, channel = _read_channel_record(channel_id, client=client)
    if not encrypted or not payload or not channel_raw or not channel:
        return False
    connection_id = str(channel.get('connection_id') or '')
    if payload.get('connection_id') != connection_id:
        raise OAuthStorageError('Stored YouTube connection records do not match')
    if expected_connection_id and connection_id != str(expected_connection_id):
        raise YouTubeAuthError('YouTube connection changed before disconnect')
    refresh_token = str(payload.get('refresh_token') or '')
    try:
        deleted = int(client.eval(
            _DISCONNECT_IF_CURRENT_SCRIPT,
            4,
            _credential_key(channel_id),
            _channel_key(channel_id),
            CHANNEL_INDEX_KEY,
            AUTH_EPOCH_KEY,
            encrypted,
            channel_raw,
            channel_id,
        ))
    except Exception as exc:
        raise OAuthStorageError('OAuth credential storage is unavailable') from exc
    if not deleted:
        raise YouTubeAuthError('YouTube connection changed before disconnect')
    if not revoke or not refresh_token:
        return False
    try:
        response = httpx.post(
            REVOCATION_URI,
            data={'token': refresh_token},
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            timeout=10.0,
        )
        return response.status_code == 200
    except Exception:
        return False
