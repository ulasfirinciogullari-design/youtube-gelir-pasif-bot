"""Real disposable Redis semantics and cryptographic binding, never live credentials."""
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

from app.services import provider_key_candidate as candidate


KEY = 'offline-new-provider-key-7c482af9173b'
ACTIVE = 'offline-active-provider-key-a1340985'
ENCRYPTION = 'offline-dedicated-app-encryption-material-24ab'
SENTINEL = 'SECRET-EXCEPTION-NEVER-ECHO'


@pytest.fixture
def sandbox(monkeypatch):
    config = SimpleNamespace(app_encryption_key=ENCRYPTION, abacus_api_key=ACTIVE)
    monkeypatch.setattr(candidate, 'settings', config)
    forbidden = Mock(side_effect=AssertionError('No provider network'))
    for name in ('post', 'get', 'request', 'stream'):
        monkeypatch.setattr(httpx, name, forbidden)
    box = SimpleNamespace(config=config, client=fakeredis.FakeRedis(), forbidden=forbidden)
    yield box
    forbidden.assert_not_called()


def contents(client):
    return {key: client.get(key) for key in client.scan_iter()}


def token(client):
    return client.get(candidate.CANDIDATE_KEY)


def payload(raw):
    return json.loads(candidate._fernet(ENCRYPTION).decrypt(raw))


def rewrite(client, value):
    encoded = json.dumps(value, separators=(',', ':')).encode()
    client.set(candidate.CANDIDATE_KEY, candidate._fernet(ENCRYPTION).encrypt(encoded))


def safe_failure(operation):
    with pytest.raises(candidate.ProviderKeyCandidateError) as caught:
        operation()
    shown = str(caught.value) + repr(caught.value)
    for secret in (KEY, ACTIVE, ENCRYPTION, SENTINEL):
        assert secret not in shown
    assert caught.value.__suppress_context__ or type(caught.value) is candidate.ProviderKeyCandidateError
    return caught.value.code


def test_absent_status_and_operator_read_do_not_create_state(sandbox):
    assert candidate.candidate_status(sandbox.client) == candidate.CandidateStatus(False, 'absent')
    assert candidate.read_candidate(sandbox.client) is None
    assert sandbox.client.dbsize() == 0


@pytest.mark.parametrize('decode_responses', [False, True])
def test_encrypted_staging_has_exact_binding_permanent_ttl_and_redacted_operator_result(sandbox, decode_responses):
    client = fakeredis.FakeRedis(decode_responses=decode_responses)
    client.set('unrelated-existing-state', 'unchanged')
    before = vars(sandbox.config).copy()
    result = candidate.stage_candidate(client, KEY)
    assert result == candidate.CandidateStatus(True, 'pending')
    raw = token(client)
    raw_bytes = raw.encode() if isinstance(raw, str) else raw
    assert KEY.encode() not in raw_bytes and ACTIVE.encode() not in raw_bytes
    assert ENCRYPTION.encode() not in raw_bytes
    assert client.ttl(candidate.CANDIDATE_KEY) == -1
    assert client.get('unrelated-existing-state') in (b'unchanged', 'unchanged')
    assert client.dbsize() == 2 and vars(sandbox.config) == before
    value = payload(raw_bytes)
    assert value['provider'] == 'abacus' and value['redis_key'] == candidate.CANDIDATE_KEY
    assert value['purpose'] == 'provider_key_candidate'
    assert value['original_active_key_sha256'] == hashlib.sha256(ACTIVE.encode()).hexdigest()
    assert value['api_access_verified'] is False and value['production_settings_changed'] is False
    observed = candidate.read_candidate(client)
    assert observed.api_key == KEY and observed.candidate_id == value['candidate_id']
    assert observed.record_sha256 == hashlib.sha256(raw_bytes).hexdigest()
    assert observed.api_access_verified is False and observed.production_settings_changed is False
    assert KEY not in repr(observed) and ACTIVE not in repr(observed) and raw_bytes.decode() not in repr(observed)
    with pytest.raises(FrozenInstanceError):
        observed.api_key = 'another-key'
    assert candidate.candidate_status(client) == result
    assert token(client) == raw


@pytest.mark.parametrize('key', [ACTIVE, '', 'short', ' ', 'a b' * 10, '\n' + KEY,
    KEY + '\t', 'ş' * 24, 'x' * 4097, '*' * 24, 'YOUR_ABACUS_API_KEY',
    '<your_api_key_here>', 'sk-xxxxxxxxxxxxxxxx', 'sk-abc...0123456789',
    'placeholder-key-here', 'sk-your-key-here', 'your-secret-key-here', 'A' * 32,
    '"' + KEY + '"', "'" + KEY + "'"])
def test_same_masked_or_malformed_input_never_creates_candidate(sandbox, key):
    safe_failure(lambda: candidate.stage_candidate(sandbox.client, key))
    assert sandbox.client.dbsize() == 0


def test_single_pending_cannot_overwrite_even_with_identical_or_different_candidate(sandbox):
    candidate.stage_candidate(sandbox.client, KEY)
    first = token(sandbox.client)
    for key in (KEY, 'offline-another-provider-key-873426a'):
        assert safe_failure(lambda: candidate.stage_candidate(sandbox.client, key)) == 'candidate_pending'
        assert token(sandbox.client) == first


@pytest.mark.parametrize('material', ['', '   ', None, 1, 'x' * 4097])
def test_encryption_never_falls_back_to_active_key_or_other_token(sandbox, material):
    sandbox.config.app_encryption_key = material
    sandbox.config.factory_api_token = 'offline-session-token'
    safe_failure(lambda: candidate.stage_candidate(sandbox.client, KEY))
    assert sandbox.client.dbsize() == 0


@pytest.mark.parametrize('ttl', [1, 3600])
def test_expiring_pending_is_rejected_without_persisting_or_overwriting(sandbox, ttl):
    candidate.stage_candidate(sandbox.client, KEY)
    raw = token(sandbox.client)
    sandbox.client.expire(candidate.CANDIDATE_KEY, ttl)
    safe_failure(lambda: candidate.read_candidate(sandbox.client))
    safe_failure(lambda: candidate.stage_candidate(sandbox.client, 'offline-another-provider-key-873426a'))
    assert token(sandbox.client) == raw and sandbox.client.ttl(candidate.CANDIDATE_KEY) >= 0


@pytest.mark.parametrize('field,value', [
    ('schema_version', True), ('schema_version', 2), ('provider', 'openai'),
    ('purpose', 'youtube_oauth'), ('redis_key', 'different-namespace'),
    ('candidate_id', 'not-a-uuid'), ('created_at', '2026-09-12'),
    ('created_at', '2026-99-99T00:00:00+00:00'), ('status', 'verified'),
    ('api_access_verified', True), ('api_access_verified', 0),
    ('production_settings_changed', True), ('original_active_key_sha256', '0' * 64),
    ('api_key', ACTIVE), ('api_key', 'masked-placeholder-key'),
])
def test_authenticated_payload_still_requires_exact_candidate_schema_and_source_binding(sandbox, field, value):
    candidate.stage_candidate(sandbox.client, KEY)
    data = payload(token(sandbox.client))
    data[field] = value
    rewrite(sandbox.client, data)
    before = token(sandbox.client)
    safe_failure(lambda: candidate.read_candidate(sandbox.client))
    assert token(sandbox.client) == before


@pytest.mark.parametrize('change', ['extra', 'missing', 'duplicate', 'nan'])
def test_plaintext_schema_is_strict_even_with_valid_encryption(sandbox, change):
    candidate.stage_candidate(sandbox.client, KEY)
    data = payload(token(sandbox.client))
    if change == 'extra':
        data['extra'] = 'not-authorized'
    if change == 'missing':
        data.pop('purpose')
    raw = json.dumps(data).encode()
    if change == 'duplicate':
        raw = raw[:-1] + b',"provider":"abacus"}'
    if change == 'nan':
        raw = raw[:-1] + b',"extra":NaN}'
    sandbox.client.set(candidate.CANDIDATE_KEY, candidate._fernet(ENCRYPTION).encrypt(raw))
    safe_failure(lambda: candidate.read_candidate(sandbox.client))


@pytest.mark.parametrize('change', ['ciphertext', 'encryption_key', 'active_key', 'oauth_domain'])
def test_ciphertext_and_context_substitution_rejected(sandbox, change):
    candidate.stage_candidate(sandbox.client, KEY)
    raw = token(sandbox.client)
    if change == 'ciphertext':
        sandbox.client.set(candidate.CANDIDATE_KEY, raw[:50] + b'!' + raw[51:])
    elif change == 'encryption_key':
        sandbox.config.app_encryption_key = 'different-encryption-key'
    elif change == 'active_key':
        sandbox.config.abacus_api_key = 'different-active-key'
    else:
        ordinary = Fernet(base64.urlsafe_b64encode(hashlib.sha256(ENCRYPTION.encode()).digest()))
        sandbox.client.set(candidate.CANDIDATE_KEY, ordinary.encrypt(json.dumps(payload(raw)).encode()))
    safe_failure(lambda: candidate.read_candidate(sandbox.client))


class Intercept:
    def __init__(self, client, *, on_set=None, on_execute=None):
        self.client, self.on_set, self.on_execute = client, on_set, on_execute
        self.set_calls = self.exec_calls = 0
    def set(self, *args, **kwargs):
        self.set_calls += 1
        assert args[0] == candidate.CANDIDATE_KEY and kwargs == {'nx': True}
        if self.on_set:
            return self.on_set(self.client, *args, **kwargs)
        return self.client.set(*args, **kwargs)
    def pipeline(self):
        pipe = self.client.pipeline()
        execute = pipe.execute
        def observed(*args, **kwargs):
            self.exec_calls += 1
            if self.on_execute:
                return self.on_execute(self.exec_calls, execute, *args, **kwargs)
            return execute(*args, **kwargs)
        pipe.execute = observed
        return pipe


def test_lost_set_ack_is_terminal_and_never_retries_or_deletes_pending(sandbox):
    def lost(client, *args, **kwargs):
        client.set(*args, **kwargs)
        raise ConnectionError(KEY + SENTINEL)
    client = Intercept(sandbox.client, on_set=lost)
    safe_failure(lambda: candidate.stage_candidate(client, KEY))
    first = token(sandbox.client)
    assert first and sandbox.client.ttl(candidate.CANDIDATE_KEY) == -1 and client.set_calls == 1
    safe_failure(lambda: candidate.stage_candidate(client, KEY))
    assert client.set_calls == 1 and token(sandbox.client) == first
    assert candidate.candidate_status(sandbox.client).present is True


@pytest.mark.parametrize('ack', [None, False, 1, 'OK', [True]])
def test_non_exact_set_ack_cannot_report_success_even_after_write(sandbox, ack):
    def wrong_ack(client, *args, **kwargs):
        client.set(*args, **kwargs)
        return ack
    client = Intercept(sandbox.client, on_set=wrong_ack)
    safe_failure(lambda: candidate.stage_candidate(client, KEY))
    assert token(sandbox.client) and client.set_calls == 1


@pytest.mark.parametrize('fault', ['raise', 'bad_ack', 'ttl', 'missing', 'replace'])
def test_post_set_readback_failure_keeps_occupied_or_observed_external_state(sandbox, fault):
    def after(count, execute, *args, **kwargs):
        result = execute(*args, **kwargs)
        if count == 2:
            if fault == 'raise':
                raise ConnectionError(KEY + SENTINEL)
            if fault == 'bad_ack':
                return [1]
        return result
    def set_then_fault(client, *args, **kwargs):
        ack = client.set(*args, **kwargs)
        if fault == 'ttl':
            client.expire(candidate.CANDIDATE_KEY, 30)
        elif fault == 'missing':
            client.delete(candidate.CANDIDATE_KEY)
        elif fault == 'replace':
            client.set(candidate.CANDIDATE_KEY, b'external-replacement')
        return ack
    client = Intercept(sandbox.client, on_set=set_then_fault, on_execute=after)
    safe_failure(lambda: candidate.stage_candidate(client, KEY))
    assert client.set_calls == 1
    if fault != 'missing':
        assert token(sandbox.client)


def test_atomic_nx_racing_winner_is_never_replaced(sandbox):
    winner = b'an-existing-racing-candidate'
    def racing(client, *args, **kwargs):
        client.set(candidate.CANDIDATE_KEY, winner)
        return client.set(*args, **kwargs)
    client = Intercept(sandbox.client, on_set=racing)
    assert safe_failure(lambda: candidate.stage_candidate(client, KEY)) == 'candidate_pending'
    assert token(sandbox.client) == winner and client.set_calls == 1


def test_watch_change_during_absent_read_never_admits_set(sandbox):
    def race(count, execute, *args, **kwargs):
        sandbox.client.set(candidate.CANDIDATE_KEY, b'competing-value')
        return execute(*args, **kwargs)
    client = Intercept(sandbox.client, on_execute=race)
    safe_failure(lambda: candidate.stage_candidate(client, KEY))
    assert client.set_calls == 0 and token(sandbox.client) == b'competing-value'


@pytest.mark.parametrize('change_at', [1, 2])
def test_settings_change_before_or_after_set_cannot_report_success(sandbox, change_at):
    def change(count, execute, *args, **kwargs):
        result = execute(*args, **kwargs)
        if count == change_at:
            sandbox.config.abacus_api_key = 'concurrent-active-change'
        return result
    client = Intercept(sandbox.client, on_execute=change)
    safe_failure(lambda: candidate.stage_candidate(client, KEY))
    assert client.set_calls == change_at - 1


def test_oversized_or_untyped_redis_record_fails_closed(sandbox):
    sandbox.client.set(candidate.CANDIDATE_KEY, b'a' * 16385)
    safe_failure(lambda: candidate.read_candidate(sandbox.client))
    assert sandbox.client.strlen(candidate.CANDIDATE_KEY) == 16385
