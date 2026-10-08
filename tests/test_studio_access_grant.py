"""Operator grants tested only with disposable Redis and synthetic secret material."""
import base64
from dataclasses import FrozenInstanceError
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock

from cryptography.fernet import Fernet
import fakeredis
import httpx
import pytest
from redis.exceptions import ConnectionError

from app.services import studio_access_grant as access


FACTORY = 'offline-new-strong-factory-token-d81056ceff4d92'
ENCRYPTION = 'offline-app-encryption-key-d3021aa45bc34'
OLD_COOKIE = 'weak-before'
TOKEN = base64.urlsafe_b64encode(bytes(range(32))).rstrip(b'=').decode()
OTHER = base64.urlsafe_b64encode(bytes(range(1, 33))).rstrip(b'=').decode()
SENTINEL = 'DO-NOT-EXPOSE-BACKEND-SECRET'
NOW = 1_789_247_000_000


@pytest.fixture
def box(monkeypatch):
    config = SimpleNamespace(app_encryption_key=ENCRYPTION, factory_api_token=FACTORY)
    monkeypatch.setattr(access, 'settings', config)
    clock = SimpleNamespace(now=NOW)
    monkeypatch.setattr(access, '_now_ms', lambda: clock.now)
    forbidden = Mock(side_effect=AssertionError('No provider network'))
    for name in ('post', 'get', 'request', 'stream'):
        monkeypatch.setattr(httpx, name, forbidden)
    result = SimpleNamespace(config=config, clock=clock, client=fakeredis.FakeRedis())
    yield result
    forbidden.assert_not_called()


def safe_failure(operation):
    with pytest.raises(access.StudioAccessError) as caught:
        operation()
    shown = repr(caught.value) + str(caught.value)
    for secret in (FACTORY, ENCRYPTION, TOKEN, OTHER, SENTINEL, OLD_COOKIE):
        assert secret not in shown
    return caught.value.code


def key(token):
    return access.GRANT_KEY_PREFIX + hashlib.sha256(token.encode()).hexdigest()


def payload(box, token):
    return json.loads(access._fernet((ENCRYPTION, FACTORY)).decrypt(box.client.get(key(token))))


def rewrite(box, token, value):
    cipher = access._fernet((ENCRYPTION, FACTORY)).encrypt(json.dumps(value).encode())
    box.client.set(key(token), cipher, ex=value.get('ttl_seconds', 3600))


def test_operator_mint_uses_32_random_bytes_and_only_hash_in_redis_key(box, monkeypatch):
    entropy = Mock(return_value=TOKEN)
    monkeypatch.setattr(access.secrets, 'token_urlsafe', entropy)
    grant = access.mint(box.client)
    entropy.assert_called_once_with(32)
    assert grant.token == TOKEN and grant.expires_at_ms == NOW + 3600 * 1000
    assert TOKEN not in repr(grant) and FACTORY not in repr(grant)
    with pytest.raises(FrozenInstanceError):
        grant.token = OTHER
    raw = box.client.get(key(TOKEN))
    assert raw and box.client.ttl(key(TOKEN)) == 3600
    assert TOKEN.encode() not in raw and FACTORY.encode() not in raw and ENCRYPTION.encode() not in raw
    assert list(box.client.scan_iter()) == [key(TOKEN).encode()]
    data = payload(box, TOKEN)
    assert data['factory_token_sha256'] == hashlib.sha256(FACTORY.encode()).hexdigest()
    assert data['grant_sha256'] == hashlib.sha256(TOKEN.encode()).hexdigest()
    assert data['redis_key'] == key(TOKEN) and data['purpose'] == 'studio_access'


@pytest.mark.parametrize('ttl', [1, 900, 3600, 86400])
def test_allowed_expiry_is_bound_and_one_time_consume_returns_current_cookie(box, ttl):
    grant = access.mint(box.client, ttl_seconds=ttl)
    assert grant.expires_at_ms == NOW + ttl * 1000
    assert 1 <= box.client.ttl(key(grant.token)) <= ttl
    before = vars(box.config).copy()
    receipt = access.consume(box.client, grant.token)
    assert access.session_cookie_for(receipt) == FACTORY
    assert FACTORY not in repr(receipt) and grant.token not in repr(receipt)
    assert box.client.dbsize() == 0 and vars(box.config) == before
    safe_failure(lambda: access.consume(box.client, grant.token))


@pytest.mark.parametrize('ttl', [0, -1, 86401, True, 1.0, '900', None])
def test_invalid_ttl_never_mints(box, ttl):
    safe_failure(lambda: access.mint(box.client, ttl_seconds=ttl))
    assert box.client.dbsize() == 0


@pytest.mark.parametrize('token', ['', OLD_COOKIE, 'x' * 31, None, 'x' * 4097, 'a' * 32 + ' '])
def test_old_weak_or_malformed_factory_setting_cannot_mint_or_consume(box, token):
    box.config.factory_api_token = token
    assert safe_failure(lambda: access.mint(box.client)) == 'studio_access_configuration'
    safe_failure(lambda: access.consume(box.client, TOKEN))
    assert box.client.dbsize() == 0


@pytest.mark.parametrize('material', ['', ' ', None, 1, 'a' * 4097])
def test_missing_encryption_never_uses_factory_token_as_fallback(box, material):
    box.config.app_encryption_key = material
    safe_failure(lambda: access.mint(box.client))
    assert box.client.dbsize() == 0


@pytest.mark.parametrize('token', ['', OLD_COOKIE, TOKEN + '=', TOKEN[:-1], TOKEN + 'a',
                                  '\n' + TOKEN, TOKEN[:-1] + '!', TOKEN[:-1] + '9', None, 123])
def test_only_canonical_32byte_grant_is_accepted(box, token):
    safe_failure(lambda: access.consume(box.client, token))
    assert box.client.dbsize() == 0


@pytest.mark.parametrize('field,value', [
    ('version', True), ('version', 2), ('purpose', 'provider_key_candidate'),
    ('redis_key', 'other-key'), ('grant_sha256', '0' * 64),
    ('factory_token_sha256', '0' * 64), ('issued_at_ms', NOW + 1),
    ('expires_at_ms', NOW), ('ttl_seconds', 86401), ('ttl_seconds', True),
])
def test_authenticated_record_must_match_full_context_and_expiry(box, field, value):
    grant = access.mint(box.client)
    data = payload(box, grant.token)
    data[field] = value
    cipher = access._fernet((ENCRYPTION, FACTORY)).encrypt(json.dumps(data).encode())
    box.client.set(key(grant.token), cipher, ex=3600)
    safe_failure(lambda: access.consume(box.client, grant.token))
    assert box.client.get(key(grant.token)) == cipher


@pytest.mark.parametrize('change', ['extra', 'missing', 'duplicate', 'nan', 'corrupt', 'different-domain', 'different-grant'])
def test_ciphertext_schema_domain_and_grant_substitution_are_rejected(box, change):
    grant = access.mint(box.client)
    data = payload(box, grant.token)
    if change == 'extra':
        data['extra'] = True
    elif change == 'missing':
        data.pop('purpose')
    raw = json.dumps(data).encode()
    if change == 'duplicate':
        raw = raw[:-1] + b',"purpose":"studio_access"}'
    elif change == 'nan':
        raw = raw[:-1] + b',"extra":NaN}'
    fernet = access._fernet((ENCRYPTION, FACTORY))
    if change == 'different-domain':
        fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(ENCRYPTION.encode()).digest()))
    cipher = fernet.encrypt(raw)
    if change == 'corrupt':
        cipher = cipher[:40] + b'!' + cipher[41:]
    target = OTHER if change == 'different-grant' else grant.token
    box.client.set(key(target), cipher, ex=3600)
    safe_failure(lambda: access.consume(box.client, target))
    assert box.client.get(key(target)) == cipher


@pytest.mark.parametrize('ttl', [1, 900, 86400])
def test_absolute_expiry_rejects_extended_redis_lifetime(box, ttl):
    grant = access.mint(box.client, ttl_seconds=ttl)
    box.clock.now = grant.expires_at_ms
    box.client.expire(key(grant.token), ttl)
    safe_failure(lambda: access.consume(box.client, grant.token))
    assert box.client.exists(key(grant.token)) == 1


def test_24h_grant_survives_late_arrival_but_replay_still_fails(box):
    grant = access.mint(box.client, ttl_seconds=86400)
    box.clock.now += 23 * 60 * 60 * 1000
    box.client.expire(key(grant.token), 3600)
    receipt = access.consume(box.client, grant.token)
    assert access.session_cookie_for(receipt) == FACTORY
    safe_failure(lambda: access.consume(box.client, grant.token))


@pytest.mark.parametrize('change', ['factory', 'encryption', 'persistent', 'expired', 'oversized'])
def test_rotation_missing_ttl_and_invalid_record_never_delete_or_authorize(box, change):
    grant = access.mint(box.client)
    if change == 'factory':
        box.config.factory_api_token = FACTORY + '-new'
    elif change == 'encryption':
        box.config.app_encryption_key = ENCRYPTION + '-new'
    elif change == 'persistent':
        box.client.persist(key(grant.token))
    elif change == 'expired':
        box.client.delete(key(grant.token))
    else:
        box.client.set(key(grant.token), b'a' * 4097, ex=3600)
    before = box.client.get(key(grant.token))
    safe_failure(lambda: access.consume(box.client, grant.token))
    assert box.client.get(key(grant.token)) == before


class Intercept:
    def __init__(self, client, *, set_hook=None, execute_hook=None):
        self.client, self.set_hook, self.execute_hook = client, set_hook, execute_hook
        self.sets = self.executes = 0
    def set(self, *args, **kwargs):
        self.sets += 1
        if self.set_hook:
            return self.set_hook(self.client, *args, **kwargs)
        return self.client.set(*args, **kwargs)
    def pipeline(self):
        pipe = self.client.pipeline()
        execute = pipe.execute
        def wrapped(*args, **kwargs):
            self.executes += 1
            if self.execute_hook:
                return self.execute_hook(execute, *args, **kwargs)
            return execute(*args, **kwargs)
        pipe.execute = wrapped
        return pipe


def test_mint_collision_and_lost_ack_never_retry_or_delete(box, monkeypatch):
    monkeypatch.setattr(access.secrets, 'token_urlsafe', lambda _: TOKEN)
    def lost(client, *args, **kwargs):
        client.set(*args, **kwargs)
        raise ConnectionError(TOKEN + SENTINEL)
    client = Intercept(box.client, set_hook=lost)
    safe_failure(lambda: access.mint(client))
    assert client.sets == 1
    original = box.client.get(key(TOKEN))
    safe_failure(lambda: access.mint(box.client))
    assert box.client.get(key(TOKEN)) == original


@pytest.mark.parametrize('ack', [None, False, 1, 'OK'])
def test_mint_requires_exact_set_ack(box, ack):
    def wrong(client, *args, **kwargs):
        client.set(*args, **kwargs)
        return ack
    client = Intercept(box.client, set_hook=wrong)
    safe_failure(lambda: access.mint(client))
    assert client.sets == 1 and box.client.dbsize() == 1


@pytest.mark.parametrize('ack', [None, [], [True], [0], [1, 1]])
def test_consume_requires_exact_delete_ack_without_recreating_grant(box, ack):
    grant = access.mint(box.client)
    def wrong(execute, *args, **kwargs):
        execute(*args, **kwargs)
        return ack
    client = Intercept(box.client, execute_hook=wrong)
    safe_failure(lambda: access.consume(client, grant.token))
    assert client.executes == 1 and box.client.dbsize() == 0
    safe_failure(lambda: access.consume(client, grant.token))
    assert client.executes == 1


def test_lost_delete_ack_is_terminal_without_cookie_or_resend(box):
    grant = access.mint(box.client)
    def lost(execute, *args, **kwargs):
        execute(*args, **kwargs)
        raise ConnectionError(TOKEN + FACTORY + SENTINEL)
    client = Intercept(box.client, execute_hook=lost)
    safe_failure(lambda: access.consume(client, grant.token))
    assert client.executes == 1 and box.client.dbsize() == 0
    safe_failure(lambda: access.consume(client, grant.token))
    assert client.executes == 1


def test_racing_consumer_wins_once_and_outer_watch_cannot_issue_cookie(box):
    grant = access.mint(box.client)
    receipts = []
    def race(execute, *args, **kwargs):
        receipts.append(access.consume(box.client, grant.token))
        return execute(*args, **kwargs)
    client = Intercept(box.client, execute_hook=race)
    safe_failure(lambda: access.consume(client, grant.token))
    assert len(receipts) == 1 and access.session_cookie_for(receipts[0]) == FACTORY
    assert box.client.dbsize() == 0


@pytest.mark.parametrize('change', ['factory', 'encryption', 'expiry'])
def test_context_or_expiry_change_at_delete_ack_never_authorizes(box, change):
    grant = access.mint(box.client)
    def alter(execute, *args, **kwargs):
        result = execute(*args, **kwargs)
        if change == 'factory':
            box.config.factory_api_token = FACTORY + '-rotated'
        elif change == 'encryption':
            box.config.app_encryption_key = ENCRYPTION + '-rotated'
        else:
            box.clock.now = grant.expires_at_ms
        return result
    safe_failure(lambda: access.consume(Intercept(box.client, execute_hook=alter), grant.token))
    assert box.client.dbsize() == 0


@pytest.mark.parametrize('change', ['factory', 'encryption', 'expiry'])
def test_receipt_rechecks_settings_and_expiry_before_cookie(box, change):
    grant = access.mint(box.client)
    receipt = access.consume(box.client, grant.token)
    if change == 'factory':
        box.config.factory_api_token = FACTORY + '-rotated'
    elif change == 'encryption':
        box.config.app_encryption_key = ENCRYPTION + '-rotated'
    else:
        box.clock.now = grant.expires_at_ms
    safe_failure(lambda: access.session_cookie_for(receipt))
