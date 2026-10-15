"""Setup ASR uses real decoding and Redis transactions with offline transport."""
from contextlib import nullcontext
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock

import fakeredis
import httpx
import pytest

from app.services import commissioning_audio as setup, audio_qc, youtube_auth
from app.services import production_spend_runtime as runtime, whisper_transcription as whisper
from app.services.production_spend import SpendBlocked, SpendLedger, SpendPolicy, LEDGER_KEY
from test_whisper_transcription import _wav, _payload, NOW, ROOT, CHILD, CHANNEL, KEY


@pytest.fixture
def box(monkeypatch, tmp_path):
    client = fakeredis.FakeRedis(decode_responses=True)
    foundation = SpendLedger(client, SpendPolicy(*([0] * 6)), clock=lambda: NOW)
    foundation.initialize_cash_disabled_unknown_history(evidence_sha256='a' * 64,
        additional_monthly_limit_micro=10_000_000)
    config = SimpleNamespace(studio_spend_enforcement=True, studio_abacus_included_production=True,
        app_encryption_key='offline-commissioning-encryption-key', openai_api_key=KEY)
    for module in (runtime, audio_qc, youtube_auth):
        monkeypatch.setattr(module, 'settings', config)
    monkeypatch.setattr(runtime, 'configured_ledger', lambda **_: foundation)
    monkeypatch.setattr(setup, '_now', lambda: NOW)
    client.sadd(runtime._CHANNEL_INDEX, CHANNEL)
    client.set(runtime._CHANNEL_PREFIX + CHANNEL, json.dumps({'id': CHANNEL, 'connection_id': 'connection_AAAAA'}))
    for task, parent in ((ROOT, None), (CHILD, ROOT)):
        client.set(runtime._JOB_PREFIX + task, json.dumps({'task_id': task, 'parent_id': parent,
            'spec': {'production_channel_id': CHANNEL, 'production_connection_id': 'connection_AAAAA',
                'duration_minutes': .5}}))
    policy = {'version': 1, 'purpose': 'owner_authorized_commissioning_whisper',
        'credential_sha256': hashlib.sha256(('openai\0' + KEY).encode()).hexdigest(),
        'channels': {CHANNEL: 'connection_AAAAA'}, 'valid_from': NOW.isoformat(),
        'valid_until': (NOW + timedelta(days=2)).isoformat(), 'max_requests': 100,
        'max_per_day': 24, 'max_per_lineage': 6, 'owner_authorization_sha256': 'b' * 64,
        'historical_cash_micro': None}
    path = tmp_path / 'voice.wav'; path.write_bytes(_wav())
    sender = Mock(return_value=nullcontext(httpx.Response(200, json=_payload())))
    monkeypatch.setattr(whisper.httpx, 'stream', sender)
    task_token = runtime._TASK_ID.set(ROOT)
    try:
        yield SimpleNamespace(client=client, foundation=foundation, config=config, policy=policy,
            path=path, sender=sender)
    finally:
        runtime._TASK_ID.reset(task_token)


def run(box):
    return setup.transcribe_if_commissioned(box.path, api_key=KEY, language='en')


def test_absent_commission_does_not_initialize_or_spend(box):
    before = box.client.hgetall(LEDGER_KEY)
    assert run(box) is None and not box.client.exists(setup.POLICY_KEY, setup.JOURNAL_KEY)
    assert box.client.hgetall(LEDGER_KEY) == before
    box.sender.assert_not_called()


def test_reserve_before_send_reuse_from_child_and_preserve_unknown_cash(box):
    setup.commission(box.client, box.policy)
    before = box.client.hgetall(LEDGER_KEY)
    def send(*args, **kwargs):
        rows = json.loads(box.client.get(setup.JOURNAL_KEY))['requests']
        assert len(rows) == 1 and next(iter(rows.values()))['outcome'] is None
        assert kwargs['data'] == {**whisper._FIELDS, 'language': 'en'}
        assert kwargs['files']['file'][1] == box.path.read_bytes()
        return nullcontext(httpx.Response(200, json=_payload()))
    box.sender.side_effect = send
    assert run(box).json()['text'] == 'Hello there.'
    assert run(box).json()['text'] == 'Hello there.'
    token = runtime._TASK_ID.set(CHILD)
    try: assert run(box).json()['text'] == 'Hello there.'
    finally: runtime._TASK_ID.reset(token)
    box.sender.assert_called_once()
    assert all(box.client.hget(LEDGER_KEY, key) == value for key, value in before.items())
    assert not any(key.startswith('request:') for key in box.client.hkeys(LEDGER_KEY))
    status = setup.status(box.client)
    assert status['requests'] == 1 and status['reserved_list_cost_micro_usd'] == 6000
    assert status['historical_cash_micro'] is None and status['unknown_requests'] == 0
    journal = box.client.get(setup.JOURNAL_KEY)
    assert 'Hello there' not in journal and KEY not in journal


def test_timeout_keeps_reservation_and_never_sends_again(box):
    setup.commission(box.client, box.policy)
    box.sender.side_effect = httpx.ReadTimeout('private transport detail')
    with pytest.raises(whisper.WhisperTranscriptionError, match='outcome_unverified'): run(box)
    with pytest.raises(SpendBlocked, match='previous_outcome_unknown'): run(box)
    box.sender.assert_called_once()
    assert setup.status(box.client)['unknown_requests'] == 1


def continuous(box):
    from app.services import production_continuation as authority
    authority.initialize(box.client, {'version': 1, 'kind': 'continuous_commissioning',
        'allowed_channels': [CHANNEL], 'authorized_at': NOW.isoformat(),
        'owner_evidence_sha256': 'f' * 64})
    return authority


def test_expired_grant_reuses_paid_response_but_cannot_buy_without_continuation(box, monkeypatch):
    setup.commission(box.client, box.policy); run(box)
    original = box.client.get(setup.POLICY_KEY); history = box.client.get(setup.JOURNAL_KEY)
    monkeypatch.setattr(setup, '_now', lambda: NOW + timedelta(days=3))
    assert run(box).json()['text'] == 'Hello there.'
    assert setup.status(box.client)['legacy_grant_expired'] is True
    box.path.write_bytes(_wav(amplitude=1))
    with pytest.raises(SpendBlocked, match='expired'): run(box)
    assert box.client.get(setup.POLICY_KEY) == original and box.client.get(setup.JOURNAL_KEY) == history
    box.sender.assert_called_once()


def test_active_continuation_after_setup_expiry_keeps_grant_all_history_and_unknowns(box, monkeypatch):
    setup.commission(box.client, box.policy); run(box); authority = continuous(box)
    original = box.client.get(setup.POLICY_KEY)
    old = json.loads(box.client.get(setup.JOURNAL_KEY))['requests']
    monkeypatch.setattr(setup, '_now', lambda: NOW + timedelta(days=3))
    box.path.write_bytes(_wav(amplitude=1)); run(box)
    rows = json.loads(box.client.get(setup.JOURNAL_KEY))['requests']
    assert all(rows[k] == v for k,v in old.items()) and len(rows) == 2
    added = next(v for k,v in rows.items() if k not in old)
    assert added['continuation_authority_sha256'] == box.client.get(authority.ANCHOR_KEY)
    assert box.client.get(setup.POLICY_KEY) == original and box.sender.call_count == 2
    box.client.delete(authority.ACTIVE_KEY)
    assert run(box).json()['text'] == 'Hello there.'  # No new request after deactivation.
    box.path.write_bytes(_wav(amplitude=2))
    with pytest.raises(SpendBlocked, match='expired'): run(box)
    assert box.sender.call_count == 2


def test_unknown_asr_cannot_be_rebought_after_expiry_or_continuation_activation(box, monkeypatch):
    setup.commission(box.client, box.policy)
    box.sender.side_effect = httpx.ReadTimeout('unknown')
    with pytest.raises(whisper.WhisperTranscriptionError): run(box)
    history = box.client.get(setup.JOURNAL_KEY); continuous(box)
    monkeypatch.setattr(setup, '_now', lambda: NOW + timedelta(days=3))
    with pytest.raises(SpendBlocked, match='previous_outcome_unknown'): run(box)
    assert box.client.get(setup.JOURNAL_KEY) == history
    box.sender.assert_called_once()


def test_operating_budget_cannot_be_bypassed_by_continuation(box):
    setup.commission(box.client, box.policy); continuous(box)
    box.foundation.policy = SpendPolicy(*([1_000_000] * 6))
    history = box.client.get(setup.JOURNAL_KEY)
    with pytest.raises(SpendBlocked, match='zero_policy_required'): run(box)
    assert box.client.get(setup.JOURNAL_KEY) == history
    box.sender.assert_not_called()


def test_verified_probe_is_imported_without_a_second_charge_or_qa_approval(box):
    setup.commission(box.client, box.policy)
    raw, suffix = whisper._read_audio(box.path)
    descriptor = whisper._snapshot_audio(raw, suffix).descriptor({**whisper._FIELDS, 'language': 'en'})
    response = json.dumps(_payload()).encode()
    key = 'youtube_studio:commissioning:v1:whisper_probe:' + descriptor['audio']['sha256'] + ':en'
    probe = {'state': 'observed', 'source_task_id': ROOT, 'model': 'whisper-1',
        'endpoint': whisper.WHISPER_ROUTE, 'credential_sha256': box.policy['credential_sha256'],
        'request_sha256': setup._hash(json.dumps(descriptor, sort_keys=True)),
        'max_list_cost_micro_usd': 6000, 'response_sha256': hashlib.sha256(response).hexdigest(),
        'encrypted_response': youtube_auth._encrypt_json({'response': response.decode()})}
    box.client.set(key, json.dumps(probe)); original = box.client.get(key)
    assert run(box).json() == _payload()
    assert run(box).json() == _payload()
    box.sender.assert_not_called()
    assert box.client.get(key) == original and setup.status(box.client)['requests'] == 1
    row = next(iter(json.loads(box.client.get(setup.JOURNAL_KEY))['requests'].values()))
    assert row['outcome']['source_receipt'] == key


@pytest.mark.parametrize('damage', ['credential', 'connection', 'expired', 'enforcement', 'long_audio', 'partial_grant'])
def test_unadmitted_requests_never_reach_provider(box, monkeypatch, damage):
    policy = deepcopy(box.policy)
    if damage == 'credential': policy['credential_sha256'] = 'c' * 64
    setup.commission(box.client, policy)
    if damage == 'connection': box.client.set(runtime._CHANNEL_PREFIX + CHANNEL,
        json.dumps({'id': CHANNEL, 'connection_id': 'different_connection'}))
    if damage == 'expired': monkeypatch.setattr(setup, '_now', lambda: NOW + timedelta(days=3))
    if damage == 'enforcement': box.config.studio_spend_enforcement = False
    if damage == 'long_audio': box.path.write_bytes(_wav(41 * 48000))
    if damage == 'partial_grant': box.client.delete(setup.POLICY_KEY)
    with pytest.raises(SpendBlocked): run(box)
    box.sender.assert_not_called()


@pytest.mark.parametrize('scope', ['max_requests', 'max_per_day', 'max_per_lineage'])
def test_each_setup_limit_counts_even_unknown_outcomes(box, scope):
    box.policy[scope] = 1
    setup.commission(box.client, box.policy)
    run(box)
    box.path.write_bytes(_wav(amplitude=1))
    with pytest.raises(SpendBlocked, match='_limit'): run(box)
    box.sender.assert_called_once()


def test_recommission_never_erases_requests(box):
    setup.commission(box.client, box.policy); run(box)
    before = box.client.get(setup.JOURNAL_KEY)
    with pytest.raises(SpendBlocked, match='already_configured'): setup.commission(box.client, box.policy)
    assert box.client.get(setup.JOURNAL_KEY) == before


def test_lost_reservation_ack_never_reaches_provider_or_releases_charge(box, monkeypatch):
    from redis.client import Pipeline
    from redis.exceptions import ConnectionError
    setup.commission(box.client, box.policy)
    original = Pipeline.execute
    lost = []
    def execute(pipe, *args, **kwargs):
        target = any(cmd[0][:2] == ('SET', setup.JOURNAL_KEY) for cmd in pipe.command_stack)
        result = original(pipe, *args, **kwargs)
        if target and not lost:
            lost.append(True)
            raise ConnectionError('acknowledgment lost after durable reservation')
        return result
    monkeypatch.setattr(Pipeline, 'execute', execute)
    with pytest.raises(ConnectionError): run(box)
    with pytest.raises(SpendBlocked, match='previous_outcome_unknown'): run(box)
    box.sender.assert_not_called()
    assert setup.status(box.client)['unknown_requests'] == 1


def test_overlapping_workers_cannot_submit_the_same_audio_twice(box):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    setup.commission(box.client, box.policy)
    entered, finish = Event(), Event()
    def send(*args, **kwargs):
        entered.set()
        assert finish.wait(5)
        return nullcontext(httpx.Response(200, json=_payload()))
    box.sender.side_effect = send
    def worker():
        token = runtime._TASK_ID.set(ROOT)
        try: return run(box)
        finally: runtime._TASK_ID.reset(token)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(worker)
        assert entered.wait(5)
        second = pool.submit(worker)
        try:
            with pytest.raises(SpendBlocked, match='previous_outcome_unknown'): second.result(timeout=5)
        finally: finish.set()
        assert first.result(timeout=5).status_code == 200
    box.sender.assert_called_once()


def test_real_qc_path_uses_whisper_and_keeps_mismatches_rejected(box, monkeypatch):
    from app.services import production_included_router as router
    forbidden = Mock(side_effect=AssertionError('No fallback or second provider'))
    monkeypatch.setattr(router, 'generate_included_audio', forbidden)
    setup.commission(box.client, box.policy)
    result = audio_qc.verify_audio_narration(box.path, 'Hello there.', language='en')
    assert result['pass'] is True and result['provider'] == 'openai'
    rejected = audio_qc.verify_audio_narration(box.path, 'Hello world.', language='en')
    assert rejected['pass'] is False
    box.sender.assert_called_once(); forbidden.assert_not_called()
