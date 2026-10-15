"""Private, unverified provider-key staging; no promotion or provider requests."""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
from uuid import UUID, uuid4

from cryptography.fernet import Fernet

from app.config import settings


CANDIDATE_KEY = 'youtube_studio:{provider_key_candidate}:abacus:v1'
_DOMAIN = b'youtube-studio/provider-key-candidate/abacus/v1\x00'
_MAX_RECORD_BYTES = 16384
_CODES = frozenset({'candidate_invalid', 'candidate_same_active', 'candidate_pending',
                    'candidate_unavailable', 'candidate_state_invalid'})


class ProviderKeyCandidateError(RuntimeError):
    """Fixed local code; never include a key, ciphertext, or backend exception."""
    def __init__(self, code='candidate_unavailable'):
        self.code = code if type(code) is str and code in _CODES else 'candidate_unavailable'
        super().__init__(self.code)


@dataclass(frozen=True)
class CandidateStatus:
    present: bool
    status: str
    api_access_verified: bool = False
    production_settings_changed: bool = False


@dataclass(frozen=True, repr=False)
class ProviderKeyCandidate:
    """Explicit operator-only decrypted value; never serialize into an HTTP response."""
    api_key: str
    candidate_id: str
    original_active_key_sha256: str
    record_sha256: str

    def __repr__(self):
        return '<ProviderKeyCandidate [redacted]; API access unverified>'

    @property
    def api_access_verified(self):
        return False

    @property
    def production_settings_changed(self):
        return False


def _require(condition, code='candidate_state_invalid'):
    if not condition:
        raise ProviderKeyCandidateError(code)


def _context():
    material = settings.app_encryption_key
    active = settings.abacus_api_key
    _require(type(material) is str and 1 <= len(material) <= 4096 and bool(material.strip())
             and type(active) is str and len(active) <= 4096, 'candidate_unavailable')
    return material, active


def _fernet(material):
    digest = hmac.new(material.encode('utf-8'), _DOMAIN + CANDIDATE_KEY.encode('ascii'), hashlib.sha256).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _key(value):
    _require(type(value) is str and 16 <= len(value) <= 4096 and value.isascii()
             and all(33 <= ord(c) <= 126 for c in value), 'candidate_invalid')
    compact = ''.join(c for c in value.lower() if c.isalnum())
    _require(not (value[0] == value[-1] and value[0] in ('\"', "'"))
             and not any(c in value for c in '*<>[]{}') and '...' not in value
             and 'xxxx' not in compact and not (compact and len(set(compact)) == 1)
             and not any(marker in compact for marker in (
                 'yourapikey', 'yourabacus', 'yourroutellm', 'insertapikey',
                 'pasteapikey', 'replacewith', 'replaceme', 'placeholder',
                 'changeme', 'redacted', 'masked', 'exampleapikey',
                 'actualapikey', 'apikeyhere', 'enterapikey', 'yourkey',
                 'yoursecret', 'insertkey', 'pastesecret', 'putkeyhere')), 'candidate_invalid')
    return value


def _snapshot(client):
    """Stable bounded GET + TTL; the PING ACK authorizes no writes or API use."""
    with client.pipeline() as pipe:
        pipe.watch(CANDIDATE_KEY)
        raw = pipe.get(CANDIDATE_KEY)
        ttl = pipe.ttl(CANDIDATE_KEY)
        _require(type(ttl) is int)
        if raw is None:
            _require(ttl == -2)
        else:
            _require(ttl == -1 and type(raw) in (str, bytes))
            if type(raw) is str:
                raw = raw.encode('ascii')
            _require(0 < len(raw) <= _MAX_RECORD_BYTES)
        pipe.multi()
        pipe.ping()
        ack = pipe.execute()
        _require(type(ack) is list and len(ack) == 1 and ack[0] is True,
                 'candidate_unavailable')
    return raw


def _decode(raw, context):
    material, active = context
    def pairs(items):
        result = {}
        for name, value in items:
            _require(name not in result)
            result[name] = value
        return result
    def invalid(_):
        raise ProviderKeyCandidateError('candidate_state_invalid')
    plaintext = _fernet(material).decrypt(raw)
    _require(0 < len(plaintext) <= _MAX_RECORD_BYTES)
    value = json.loads(plaintext, object_pairs_hook=pairs, parse_constant=invalid)
    _require(type(value) is dict and set(value) == {
        'schema_version', 'provider', 'purpose', 'redis_key', 'candidate_id',
        'created_at', 'api_key', 'original_active_key_sha256', 'status',
        'api_access_verified', 'production_settings_changed'})
    _require(type(value['schema_version']) is int and value['schema_version'] == 1
             and value['provider'] == 'abacus' and value['purpose'] == 'provider_key_candidate'
             and value['redis_key'] == CANDIDATE_KEY and value['status'] == 'pending'
             and value['api_access_verified'] is False and value['production_settings_changed'] is False)
    _require(type(value['candidate_id']) is str and str(UUID(value['candidate_id'])) == value['candidate_id'])
    created = value['created_at']
    _require(type(created) is str and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00', created))
    _require(datetime.fromisoformat(created).tzinfo is not None)
    key = _key(value['api_key'])
    original_hash = hashlib.sha256(active.encode('utf-8')).hexdigest()
    _require(value['original_active_key_sha256'] == original_hash
             and not hmac.compare_digest(key.encode(), active.encode()))
    return ProviderKeyCandidate(key, value['candidate_id'], original_hash, hashlib.sha256(raw).hexdigest())


def read_candidate(client) -> ProviderKeyCandidate | None:
    """Operator-only validated decrypt. No network other than this supplied Redis client."""
    try:
        context = _context()
        raw = _snapshot(client)
        result = None if raw is None else _decode(raw, context)
        _require(_context() == context, 'candidate_unavailable')
        return result
    except ProviderKeyCandidateError:
        raise
    except Exception:
        raise ProviderKeyCandidateError('candidate_unavailable') from None


def candidate_status(client) -> CandidateStatus:
    """Safe local presence projection; neither existence nor decryption proves API access."""
    present = read_candidate(client) is not None
    return CandidateStatus(present, 'pending' if present else 'absent')


def stage_candidate(client, key: str) -> CandidateStatus:
    """One SET NX only; an ambiguous write remains occupied and is never retried here."""
    try:
        context = _context()
        material, active = context
        key = _key(key)
        _require(not hmac.compare_digest(key.encode(), active.encode()), 'candidate_same_active')
        _require(_snapshot(client) is None, 'candidate_pending')
        payload = {
            'schema_version': 1, 'provider': 'abacus', 'purpose': 'provider_key_candidate',
            'redis_key': CANDIDATE_KEY, 'candidate_id': str(uuid4()),
            'created_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'api_key': key, 'original_active_key_sha256': hashlib.sha256(active.encode()).hexdigest(),
            'status': 'pending', 'api_access_verified': False, 'production_settings_changed': False,
        }
        token = _fernet(material).encrypt(json.dumps(payload, sort_keys=True, separators=(',', ':'),
                                                    allow_nan=False).encode())
        _require(len(token) <= _MAX_RECORD_BYTES)
        _require(_context() == context, 'candidate_unavailable')
        ack = client.set(CANDIDATE_KEY, token, nx=True)
        if ack is None or ack is False:
            raise ProviderKeyCandidateError('candidate_pending')
        _require(ack is True, 'candidate_unavailable')
        observed = _snapshot(client)
        _require(observed is not None and hmac.compare_digest(observed, token), 'candidate_unavailable')
        _decode(observed, context)
        _require(_context() == context, 'candidate_unavailable')
        return CandidateStatus(True, 'pending')
    except ProviderKeyCandidateError:
        raise
    except Exception:
        raise ProviderKeyCandidateError('candidate_unavailable') from None
