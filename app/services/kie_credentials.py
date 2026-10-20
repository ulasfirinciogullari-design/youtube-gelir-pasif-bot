"""Encrypted Kie key entry shared by web and workers; no automatic activation."""
from dataclasses import dataclass
from datetime import datetime, timezone
import base64
import hashlib
import hmac
import json
from uuid import UUID, uuid4

from cryptography.fernet import Fernet

from app.config import settings
from app.services.provider_key_candidate import ProviderKeyCandidateError, _key

KEY = 'youtube_studio:{provider_credentials}:kie:v1'
DOMAIN = b'youtube-studio/kie-credentials/v1\x00'


@dataclass(frozen=True, repr=False)
class Credential:
    api_key: str
    credential_id: str
    fingerprint: str
    record_sha256: str

    def __repr__(self):
        return '<KieCredential [redacted]>'


def _require(value, code='candidate_state_invalid'):
    if not value:
        raise ProviderKeyCandidateError(code)


def _fernet():
    material = settings.app_encryption_key
    _require(type(material) is str and 1 <= len(material) <= 4096 and material.strip(),
             'candidate_unavailable')
    digest = hmac.new(material.encode(), DOMAIN + KEY.encode(), hashlib.sha256).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _decode(raw):
    _require(type(raw) in (str, bytes))
    raw = raw.encode('ascii') if isinstance(raw, str) else raw
    _require(0 < len(raw) <= 16384)
    row = json.loads(_fernet().decrypt(raw))
    _require(type(row) is dict and set(row) == {
        'version', 'provider', 'purpose', 'redis_key', 'credential_id', 'created_at', 'api_key'})
    _require(type(row['version']) is int and row['version'] == 1 and row['provider'] == 'kie'
        and row['purpose'] == 'owner_entered_kie_api_key' and row['redis_key'] == KEY
        and str(UUID(row['credential_id'])) == row['credential_id'])
    instant = datetime.fromisoformat(row['created_at'])
    _require(instant.tzinfo is not None and instant <= datetime.now(timezone.utc))
    secret = _key(row['api_key'])
    return Credential(secret, row['credential_id'],
        hashlib.sha256(('kie\0' + secret).encode()).hexdigest(), hashlib.sha256(raw).hexdigest())


def read(client):
    """Only trusted server callers receive the decrypted key."""
    try:
        with client.pipeline() as pipe:
            pipe.watch(KEY)
            raw = pipe.get(KEY)
            _require(pipe.pttl(KEY) == (-2 if raw is None else -1))
            result = None if raw is None else _decode(raw)
            pipe.multi(); pipe.ping()
            _require(pipe.execute() == [True], 'candidate_unavailable')
        return result
    except ProviderKeyCandidateError:
        raise
    except Exception:
        raise ProviderKeyCandidateError('candidate_unavailable') from None


def present(client):
    return read(client) is not None


def save(client, api_key):
    """One immutable encrypted entry. Unknown writes are never retried or erased."""
    try:
        secret = _key(api_key)
        _require('yourkie' not in ''.join(c for c in secret.lower() if c.isalnum()),
                 'candidate_invalid')
        _require(read(client) is None, 'candidate_pending')
        row = {'version': 1, 'provider': 'kie', 'purpose': 'owner_entered_kie_api_key',
            'redis_key': KEY, 'credential_id': str(uuid4()),
            'created_at': datetime.now(timezone.utc).isoformat(), 'api_key': secret}
        token = _fernet().encrypt(json.dumps(row, sort_keys=True, separators=(',', ':')).encode())
        ack = client.set(KEY, token, nx=True)
        _require(ack is True, 'candidate_pending' if ack in (None, False) else 'candidate_unavailable')
        saved = read(client)
        _require(saved is not None and saved.record_sha256 == hashlib.sha256(token).hexdigest(),
                 'candidate_unavailable')
        return True
    except ProviderKeyCandidateError:
        raise
    except Exception:
        raise ProviderKeyCandidateError('candidate_unavailable') from None
