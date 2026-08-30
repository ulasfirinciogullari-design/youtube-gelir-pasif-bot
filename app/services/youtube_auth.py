from __future__ import annotations

from datetime import datetime, timezone
import base64
import hashlib
import json
import secrets
from typing import Any

from cryptography.fernet import Fernet
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
import redis

from app.config import settings

SCOPES = [
    'https://www.googleapis.com/auth/youtube.upload',
    'https://www.googleapis.com/auth/youtube.readonly',
    'https://www.googleapis.com/auth/youtube.force-ssl',
]
CREDENTIAL_KEY = 'youtube_studio:oauth:credentials'
CHANNEL_KEY = 'youtube_studio:oauth:channel'
STATE_PREFIX = 'youtube_studio:oauth:state:'
STATE_TTL = 600


def _redis() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


def _secret_material() -> str:
    value = settings.app_encryption_key or settings.factory_api_token
    if not value:
        raise RuntimeError('APP_ENCRYPTION_KEY or FACTORY_API_TOKEN must be configured')
    return value


def _fernet() -> Fernet:
    digest = hashlib.sha256(_secret_material().encode('utf-8')).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def oauth_configured() -> bool:
    return bool(settings.google_client_id and settings.google_client_secret)


def _client_config() -> dict:
    if not oauth_configured():
        raise RuntimeError('GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are not configured')
    return {
        'web': {
            'client_id': settings.google_client_id,
            'client_secret': settings.google_client_secret,
            'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
            'token_uri': 'https://oauth2.googleapis.com/token',
        }
    }


def _serialize_credentials(credentials: Credentials) -> dict:
    expiry = credentials.expiry
    return {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri or 'https://oauth2.googleapis.com/token',
        'client_id': credentials.client_id or settings.google_client_id,
        'client_secret': credentials.client_secret or settings.google_client_secret,
        'scopes': list(credentials.scopes or SCOPES),
        'expiry': expiry.astimezone(timezone.utc).isoformat() if expiry else None,
    }


def _deserialize_credentials(payload: dict) -> Credentials:
    expiry = payload.get('expiry')
    expiry_dt = None
    if expiry:
        try:
            expiry_dt = datetime.fromisoformat(str(expiry).replace('Z', '+00:00'))
            if expiry_dt.tzinfo:
                expiry_dt = expiry_dt.astimezone(timezone.utc).replace(tzinfo=None)
        except Exception:
            expiry_dt = None
    return Credentials(
        token=payload.get('token'),
        refresh_token=payload.get('refresh_token'),
        token_uri=payload.get('token_uri') or 'https://oauth2.googleapis.com/token',
        client_id=payload.get('client_id') or settings.google_client_id,
        client_secret=payload.get('client_secret') or settings.google_client_secret,
        scopes=payload.get('scopes') or SCOPES,
        expiry=expiry_dt,
    )


def save_credentials(credentials: Credentials) -> None:
    encoded = json.dumps(_serialize_credentials(credentials), ensure_ascii=False).encode('utf-8')
    encrypted = _fernet().encrypt(encoded).decode('ascii')
    _redis().set(CREDENTIAL_KEY, encrypted)


def load_credentials(*, refresh: bool = True) -> Credentials | None:
    try:
        encrypted = _redis().get(CREDENTIAL_KEY)
        if not encrypted:
            return None
        payload = json.loads(_fernet().decrypt(encrypted.encode('ascii')).decode('utf-8'))
        credentials = _deserialize_credentials(payload)
        if refresh and credentials.expired and credentials.refresh_token:
            credentials.refresh(GoogleRequest())
            save_credentials(credentials)
        return credentials
    except Exception:
        return None


def build_authorization_url(redirect_uri: str) -> str:
    redirect_uri = settings.google_redirect_uri or redirect_uri
    state = secrets.token_urlsafe(32)
    flow = Flow.from_client_config(
        _client_config(),
        scopes=SCOPES,
        state=state,
        autogenerate_code_verifier=True,
    )
    flow.redirect_uri = redirect_uri
    authorization_url, _ = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent',
    )
    _redis().setex(
        STATE_PREFIX + state,
        STATE_TTL,
        json.dumps({
            'redirect_uri': redirect_uri,
            'code_verifier': flow.code_verifier,
        }, ensure_ascii=False),
    )
    return authorization_url


def complete_authorization(authorization_response: str, state: str) -> dict:
    client = _redis()
    raw_state = client.get(STATE_PREFIX + state)
    if not raw_state:
        raise RuntimeError('OAuth state expired or invalid')
    state_payload = json.loads(raw_state)
    redirect_uri = state_payload.get('redirect_uri') or settings.google_redirect_uri
    flow = Flow.from_client_config(
        _client_config(),
        scopes=SCOPES,
        state=state,
        code_verifier=state_payload.get('code_verifier'),
    )
    flow.redirect_uri = redirect_uri
    flow.fetch_token(authorization_response=authorization_response)
    credentials = flow.credentials
    save_credentials(credentials)
    client.delete(STATE_PREFIX + state)
    channel = refresh_channel_info(credentials)
    return channel


def refresh_channel_info(credentials: Credentials | None = None) -> dict:
    credentials = credentials or load_credentials()
    if not credentials:
        raise RuntimeError('YouTube is not connected')
    youtube = build('youtube', 'v3', credentials=credentials, cache_discovery=False)
    response = youtube.channels().list(part='snippet,statistics', mine=True).execute()
    items = response.get('items') or []
    if not items:
        raise RuntimeError('No YouTube channel was found for this Google account')
    item = items[0]
    snippet = item.get('snippet') or {}
    statistics = item.get('statistics') or {}
    channel = {
        'id': item.get('id'),
        'title': snippet.get('title'),
        'description': snippet.get('description'),
        'thumbnail_url': (((snippet.get('thumbnails') or {}).get('default') or {}).get('url')),
        'subscriber_count': statistics.get('subscriberCount'),
        'video_count': statistics.get('videoCount'),
        'view_count': statistics.get('viewCount'),
        'connected_at': datetime.now(timezone.utc).isoformat(),
    }
    _redis().set(CHANNEL_KEY, json.dumps(channel, ensure_ascii=False))
    return channel


def get_channel_info() -> dict | None:
    try:
        raw = _redis().get(CHANNEL_KEY)
        if raw:
            value = json.loads(raw)
            return value if isinstance(value, dict) else None
    except Exception:
        return None
    return None


def connection_status() -> dict:
    credentials = load_credentials(refresh=False)
    channel = get_channel_info()
    return {
        'configured': oauth_configured(),
        'connected': bool(credentials and credentials.refresh_token),
        'channel': channel,
        'redirect_uri': settings.google_redirect_uri or None,
    }


def disconnect() -> None:
    try:
        _redis().delete(CREDENTIAL_KEY, CHANNEL_KEY)
    except Exception:
        pass
