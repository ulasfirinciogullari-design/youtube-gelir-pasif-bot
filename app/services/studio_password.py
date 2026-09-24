"""Owner-selected reusable password; existing Studio authorization stays intact."""
import base64
import hashlib
import hmac
import json
import secrets

from app.config import settings

KEY = 'youtube_studio:owner_password:v1'
LIMIT = 'youtube_studio:owner_password:v1:login_window'
MAX_AGE = 90 * 24 * 60 * 60


class PasswordError(RuntimeError):
    pass


def _require(value, code='password_unavailable'):
    if not value:
        raise PasswordError(code)


def _binding():
    value = settings.factory_api_token
    _require(type(value) is str and len(value) >= 24)
    return hashlib.sha256(value.encode()).hexdigest()


def _derive(password, salt):
    _require(type(password) is str and 10 <= len(password) <= 128
        and not any(ord(c) < 32 for c in password), 'password_length')
    return hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)


def _read(reader):
    raw = reader.get(KEY)
    if raw is None:
        return None
    _require(reader.pttl(KEY) == -1 and len(raw) <= 1024)
    row = json.loads(raw)
    _require(type(row) is dict and set(row) == {'version', 'salt', 'digest', 'binding'}
        and type(row['version']) is int and row['version'] == 1 and row['binding'] == _binding())
    salt = base64.b64decode(row['salt'], validate=True)
    digest = base64.b64decode(row['digest'], validate=True)
    _require(len(salt) == 16 and len(digest) == 32)
    return salt, digest


def configured(client):
    with client.pipeline() as pipe:
        pipe.watch(KEY)
        result = _read(pipe) is not None
        pipe.multi(); pipe.ping(); _require(pipe.execute() == [True])
    return result


def save(client, password):
    """Called only by the authenticated, same-origin settings POST."""
    salt = secrets.token_bytes(16)
    digest = _derive(password, salt)
    row = {'version': 1, 'salt': base64.b64encode(salt).decode(),
        'digest': base64.b64encode(digest).decode(), 'binding': _binding()}
    encoded = json.dumps(row, sort_keys=True, separators=(',', ':'))
    with client.pipeline() as pipe:
        pipe.watch(KEY); _read(pipe)
        pipe.multi(); pipe.set(KEY, encoded)
        _require(pipe.execute() == [True])
    _require(client.get(KEY) in (encoded, encoded.encode()))
    return True


def login(client, password):
    # One owner account, a global bounded window: alternate IPs cannot bypass
    # the password guessing limit. Origin checks run before this function.
    with client.pipeline() as pipe:
        pipe.watch(LIMIT, KEY)
        current = pipe.get(LIMIT)
        count = 0 if current is None else int(current)
        _require(count < 20, 'password_rate_limited')
        if current is not None:
            _require(0 < pipe.pttl(LIMIT) <= 300000)
        row = _read(pipe)
        pipe.multi(); pipe.incr(LIMIT)
        if current is None:
            pipe.expire(LIMIT, 300)
        _require(pipe.execute() == ([count + 1, True] if current is None else [count + 1]))
    salt, expected = row if row is not None else (b'absent-owner-key', b'\0' * 32)
    try:
        actual = _derive(password, salt)
    except PasswordError:
        raise PasswordError('password_incorrect') from None
    _require(row is not None and hmac.compare_digest(actual, expected), 'password_incorrect')
    # Configuration/record changes while hashing invalidate the attempt.
    _require(_read(client) == row, 'password_incorrect')
    return settings.factory_api_token
