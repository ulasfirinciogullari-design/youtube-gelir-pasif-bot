"""Operator-minted one-time Studio access; no HTTP mint, rotation, or generation."""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
import re
import secrets
import time

from cryptography.fernet import Fernet

from app.config import settings


GRANT_KEY_PREFIX = 'youtube_studio:{studio_access_grant}:v1:'
DEFAULT_TTL_SECONDS = 3600
MAX_TTL_SECONDS = 86400
_MAX_RECORD_BYTES = 4096
_DOMAIN = b'youtube-studio/one-time-access-grant/v1\x00'
_TOKEN = re.compile(r'[A-Za-z0-9_-]{43}')
_CODES = frozenset({'studio_access_invalid', 'studio_access_unavailable', 'studio_access_configuration'})


class StudioAccessError(RuntimeError):
    def __init__(self, code='studio_access_unavailable'):
        self.code = code if type(code) is str and code in _CODES else 'studio_access_unavailable'
        super().__init__(self.code)


@dataclass(frozen=True, repr=False)
class StudioAccessGrant:
    """Trusted operator output. The token must travel only in a URL fragment."""
    token: str
    expires_at_ms: int

    def __repr__(self):
        return '<StudioAccessGrant [redacted]>'


@dataclass(frozen=True, repr=False, init=False)
class StudioAccessReceipt:
    _context_binding: str
    _expires_at_ms: int

    def __repr__(self):
        return '<StudioAccessReceipt [redacted]>'


def _receipt(context, expires):
    receipt = object.__new__(StudioAccessReceipt)
    object.__setattr__(receipt, '_context_binding', _binding(context))
    object.__setattr__(receipt, '_expires_at_ms', expires)
    return receipt


def _require(condition, code='studio_access_invalid'):
    if not condition:
        raise StudioAccessError(code)


def _context():
    material, token = settings.app_encryption_key, settings.factory_api_token
    _require(type(material) is str and 1 <= len(material) <= 4096 and bool(material.strip())
             and type(token) is str and 32 <= len(token) <= 4096 and token.isascii()
             and all(33 <= ord(c) <= 126 for c in token), 'studio_access_configuration')
    return material, token


def _now_ms():
    return int(time.time() * 1000)


def _fernet(context):
    digest = hmac.new(context[0].encode(), _DOMAIN + b'encryption', hashlib.sha256).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _binding(context):
    return hmac.new(context[0].encode(), _DOMAIN + b'session\x00' + context[1].encode(), hashlib.sha256).hexdigest()


def _grant_key(token):
    _require(type(token) is str and _TOKEN.fullmatch(token) is not None)
    decoded = base64.urlsafe_b64decode(token + '=')
    _require(len(decoded) == 32 and base64.urlsafe_b64encode(decoded).rstrip(b'=').decode() == token)
    return GRANT_KEY_PREFIX + hashlib.sha256(token.encode()).hexdigest()


def _read(pipe, key):
    raw, ttl = pipe.get(key), pipe.ttl(key)
    _require(type(raw) in (bytes, str) and type(ttl) is int and 1 <= ttl <= MAX_TTL_SECONDS)
    if type(raw) is str:
        raw = raw.encode('ascii')
    _require(0 < len(raw) <= _MAX_RECORD_BYTES)
    return raw, ttl


def _decode(raw, ttl, key, context):
    def pairs(items):
        result = {}
        for name, value in items:
            _require(name not in result)
            result[name] = value
        return result
    def invalid(_):
        raise StudioAccessError('studio_access_invalid')
    plaintext = _fernet(context).decrypt(raw)
    _require(0 < len(plaintext) <= _MAX_RECORD_BYTES)
    value = json.loads(plaintext, object_pairs_hook=pairs, parse_constant=invalid)
    _require(type(value) is dict and set(value) == {
        'version', 'purpose', 'redis_key', 'grant_sha256', 'factory_token_sha256',
        'issued_at_ms', 'expires_at_ms', 'ttl_seconds'})
    _require(type(value['version']) is int and value['version'] == 1
             and value['purpose'] == 'studio_access' and value['redis_key'] == key
             and value['grant_sha256'] == key.removeprefix(GRANT_KEY_PREFIX)
             and value['factory_token_sha256'] == hashlib.sha256(context[1].encode()).hexdigest())
    issued, expires, limit = value['issued_at_ms'], value['expires_at_ms'], value['ttl_seconds']
    now = _now_ms()
    _require(type(issued) is int and type(expires) is int and type(limit) is int
             and 0 < issued <= now < expires and 1 <= limit <= MAX_TTL_SECONDS
             and expires - issued == limit * 1000 and ttl <= limit
             and ttl <= (expires - now + 999) // 1000)
    return value


def mint(client, *, ttl_seconds=DEFAULT_TTL_SECONDS) -> StudioAccessGrant:
    """One operator-only SET NX; no collision retry or ambiguous-ACK recovery."""
    try:
        context = _context()
        _require(type(ttl_seconds) is int and 1 <= ttl_seconds <= MAX_TTL_SECONDS)
        token = secrets.token_urlsafe(32)
        key = _grant_key(token)
        issued = _now_ms()
        expires = issued + ttl_seconds * 1000
        value = {
            'version': 1, 'purpose': 'studio_access', 'redis_key': key,
            'grant_sha256': key.removeprefix(GRANT_KEY_PREFIX),
            'factory_token_sha256': hashlib.sha256(context[1].encode()).hexdigest(),
            'issued_at_ms': issued, 'expires_at_ms': expires, 'ttl_seconds': ttl_seconds,
        }
        encrypted = _fernet(context).encrypt(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                                        allow_nan=False).encode())
        _require(len(encrypted) <= _MAX_RECORD_BYTES)
        _require(_context() == context, 'studio_access_configuration')
        ack = client.set(key, encrypted, nx=True, ex=ttl_seconds)
        _require(ack is True, 'studio_access_unavailable')
        with client.pipeline() as pipe:
            pipe.watch(key)
            actual, ttl = _read(pipe, key)
            _require(hmac.compare_digest(actual, encrypted), 'studio_access_unavailable')
            _decode(actual, ttl, key, context)
            _require(_context() == context, 'studio_access_configuration')
            pipe.multi()
            pipe.ping()
            ack = pipe.execute()
            _require(type(ack) is list and len(ack) == 1 and ack[0] is True,
                     'studio_access_unavailable')
        _require(_context() == context, 'studio_access_configuration')
        _require(_now_ms() < expires)
        return StudioAccessGrant(token, expires)
    except StudioAccessError:
        raise
    except Exception:
        raise StudioAccessError('studio_access_unavailable') from None


def consume(client, token) -> StudioAccessReceipt:
    """Validate one grant under WATCH, then acknowledge its atomic deletion once."""
    try:
        context = _context()
        key = _grant_key(token)
        with client.pipeline() as pipe:
            pipe.watch(key)
            raw, ttl = _read(pipe, key)
            value = _decode(raw, ttl, key, context)
            _require(_context() == context, 'studio_access_configuration')
            pipe.multi()
            pipe.delete(key)
            ack = pipe.execute()
            _require(type(ack) is list and len(ack) == 1 and type(ack[0]) is int and ack[0] == 1,
                     'studio_access_unavailable')
        _require(_context() == context, 'studio_access_configuration')
        _require(value['issued_at_ms'] <= _now_ms() < value['expires_at_ms'])
        return _receipt(context, value['expires_at_ms'])
    except StudioAccessError:
        raise
    except Exception:
        raise StudioAccessError('studio_access_unavailable') from None


def session_cookie_for(receipt) -> str:
    """Recheck current strong settings after consumption, immediately before Set-Cookie."""
    try:
        context = _context()
        _require(type(receipt) is StudioAccessReceipt
                 and type(receipt._context_binding) is str
                 and hmac.compare_digest(receipt._context_binding, _binding(context))
                 and type(receipt._expires_at_ms) is int and _now_ms() < receipt._expires_at_ms,
                 'studio_access_configuration')
        return context[1]
    except StudioAccessError:
        raise
    except Exception:
        raise StudioAccessError('studio_access_unavailable') from None
