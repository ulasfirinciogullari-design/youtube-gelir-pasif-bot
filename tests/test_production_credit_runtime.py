"""Actual native POST admission with synthetic Redis/account evidence only."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace

import fakeredis
import httpx
import pytest
from redis.exceptions import ConnectionError

from app.services import production_credit_funding as credit
from app.services import production_credit_ledger as durable
from app.services import production_credit_runtime as native
from app.services import production_spend_runtime as runtime
from app.services.elevenlabs_credit_adapter import inspect_credit_request
from app.services.production_spend import LEDGER_KEY, SpendBlocked, SpendLedger, SpendPolicy


NOW = datetime(2026, 9, 9, 14, tzinfo=timezone.utc)
ROOT = '11111111-1111-4111-8111-111111111111'
CHILD = '22222222-2222-4222-8222-222222222222'
CHANNEL = 'UC5v9AvNtD3PTLgo6m1jROOA'
CONNECTION = 'source-connection-0001'
KEY = 'private-synthetic-eleven-key'
TEXT = 'A complete original narration stays unchanged in this actual request.'


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def request(text=TEXT):
    return {
        'json': {'text': text, 'model_id': credit.MODEL, 'apply_text_normalization': 'on',
                 'voice_settings': {'stability': .40, 'similarity_boost': .80, 'style': 0.0,
                                    'use_speaker_boost': True, 'speed': 1.01}, 'seed': 42},
        'params': {'output_format': 'mp3_44100_128'}, 'timeout': 180,
        'headers': {'xi-api-key': KEY, 'Accept': 'application/json', 'Content-Type': 'application/json'},
    }


def put_job(client, task=ROOT, parent=None, *, duration=.5, connection=CONNECTION):
    client.set(runtime._JOB_PREFIX + task, json.dumps({
        'task_id': task, 'parent_id': parent, 'kind': 'render',
        'spec': {'production_channel_id': CHANNEL, 'production_connection_id': connection,
                 'duration_minutes': duration},
    }))


def state(case):
    return json.loads(case.client.hget(durable.STATE_KEY, 'state'))


def all_records(client):
    return {key: client.dump(key) for key in client.scan_iter()}


@pytest.fixture
def case(monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=True)
    current = [NOW]
    foundation = SpendLedger(client, SpendPolicy(10_000_000, 4_000_000, 8_000_000,
                                               1_000_000, 8_000_000, 500_000),
                             clock=lambda: current[0])
    foundation.initialize()
    policy = {
        'version': 1, 'provider': 'elevenlabs', 'month': '2026-09',
        'valid_from': '2026-09-09T13:00:00Z', 'valid_until': '2026-09-10T00:00:00Z',
        'account_sha256': sha('synthetic evidenced ElevenLabs account'),
        'credential_sha256': sha('elevenlabs\0' + KEY),
        'evidence_sha256': sha('synthetic balance/control observation'),
        'reconciliation_sha256': sha('synthetic prior usage reconciliation'),
        'route': credit.ROUTE, 'model': credit.MODEL, 'voice_id': credit.VOICE_ID,
        'allocation_credits': 1000,
        'balance': {'observed_at': '2026-09-09T12:59:00Z',
                    'provider_reset_at': '2026-09-25T00:00:00Z',
                    'quota_credits': 10000, 'used_credits': 3000, 'withheld_credits': 100},
        'cash_controls': {'max_credit_limit_extension': 0, 'can_extend_character_limit': False,
                          'overage_observation_sha256': sha('synthetic overage OFF'),
                          'auto_top_up_enabled': False, 'auto_top_up_source': 'owner_attested',
                          'auto_top_up_proof_sha256': sha('synthetic owner attestation')},
    }
    ledger = durable.CreditLedger(client, foundation=foundation, clock=foundation.clock)
    ledger.initialize(policy)
    client.set(runtime._CHANNEL_PREFIX + CHANNEL, json.dumps({'id': CHANNEL, 'connection_id': CONNECTION}))
    client.sadd(runtime._CHANNEL_INDEX, CHANNEL)
    put_job(client)
    monkeypatch.setattr(runtime, 'settings', SimpleNamespace(
        studio_spend_enforcement=True, studio_elevenlabs_native_credits=True,
    ))
    monkeypatch.setattr(runtime, 'configured_ledger', lambda: foundation)
    token = runtime._TASK_ID.set(ROOT)
    sends = []
    def respond(req):
        sends.append(req)
        # This is the true HTTPX boundary, after the real Redis reservation.
        snapshot = json.loads(client.hget(durable.STATE_KEY, 'state'))
        assert snapshot['reserved_credits'] == 1000 - snapshot['spent_credits']
        return httpx.Response(200, content=b'{"audio_base64":"parser-owns-audio-validation"}',
                              headers={'character-cost': '248', 'request-id': 'synthetic-request-' + str(len(sends))})
    http = httpx.Client(transport=httpx.MockTransport(respond))
    result = SimpleNamespace(client=client, foundation=foundation, ledger=ledger, policy=policy,
                             now=current, sends=sends, http=http)
    yield result
    http.close()
    runtime._TASK_ID.reset(token)


def test_actual_dispatcher_reserves_before_one_post_then_settles_native_meter(case, monkeypatch):
    from app.services import production_spend_quotes
    monkeypatch.setattr(production_spend_quotes, 'quote_http_request',
                        lambda *a, **k: pytest.fail('native credits must not obtain a USD quote'))
    original = request()
    response = runtime.paid_post(case.http.post, credit.ROUTE, **original)
    assert response.content == b'{"audio_base64":"parser-owns-audio-validation"}'
    assert len(case.sends) == 1
    sent = case.sends[0]
    assert json.loads(sent.content) == original['json']
    assert str(sent.url) == credit.ROUTE + '?output_format=mp3_44100_128'
    assert sent.headers['xi-api-key'] == KEY
    snapshot = state(case)
    assert (snapshot['spent_credits'], snapshot['reserved_credits']) == (248, 0)
    assert case.foundation.snapshot()['period']['used_micro'] == 0
    assert case.ledger.summary()['available_credits'] == 752
    entry = next(iter(snapshot['intents'].values()))
    context = {'channel_id': CHANNEL, 'connection_id': CONNECTION, 'lineage_id': ROOT, 'kind': 'shorts'}
    prepared = inspect_credit_request(credit.ROUTE, original)
    fingerprint = runtime._request_fingerprint(context, 'elevenlabs', prepared.operation, prepared.payload)
    assert entry['reservation']['intent']['request_sha256'] == fingerprint
    assert entry['reservation']['intent']['intent_id'] == sha('elevenlabs-native-credit-v1\0' + fingerprint)
    assert snapshot['roots'][ROOT] == {'channel_id': CHANNEL, 'source_connection_id': CONNECTION}
    assert case.client.hexists(LEDGER_KEY, 'native_request:' + sha(fingerprint))
    persisted = json.dumps({key: case.client.hgetall(key) for key in (durable.STATE_KEY, durable.JOURNAL_KEY, LEDGER_KEY)})
    assert KEY not in persisted and TEXT not in persisted and response.text not in persisted


def test_same_request_cannot_send_again_from_new_child_after_settlement(case):
    native.paid_credit_post(case.http.post, credit.ROUTE, request())
    before = deepcopy(state(case))
    put_job(case.client, CHILD, ROOT)
    runtime._TASK_ID.set(CHILD)
    with pytest.raises(SpendBlocked, match='credit_cross_mode_request_conflict'):
        native.paid_credit_post(case.http.post, credit.ROUTE, request())
    assert len(case.sends) == 1 and state(case) == before
    assert json.loads(case.client.hget(LEDGER_KEY, 'binding:' + CHILD))['lineage_id'] == ROOT


def test_distinct_legitimate_request_uses_only_unused_native_capacity(case):
    native.paid_credit_post(case.http.post, credit.ROUTE, request())
    native.paid_credit_post(case.http.post, credit.ROUTE, request(TEXT + ' A distinct revision.'))
    assert len(case.sends) == 2
    assert (state(case)['spent_credits'], state(case)['reserved_credits']) == (496, 0)
    receipts = [entry['reservation'] for entry in state(case)['intents'].values()]
    assert [r['reserved_credits'] for r in sorted(receipts, key=lambda r: r['sequence'])] == [1000, 752]


@pytest.mark.parametrize('enforced,enabled', [(False, True), (True, False), (False, False), (True, 'true')])
def test_direct_native_entry_requires_both_exact_optins_without_any_mutation(case, enforced, enabled):
    runtime.settings.studio_spend_enforcement = enforced
    runtime.settings.studio_elevenlabs_native_credits = enabled
    before = all_records(case.client)
    with pytest.raises(SpendBlocked, match='credit_runtime_not_enabled'):
        native.paid_credit_post(case.http.post, credit.ROUTE, request())
    assert not case.sends and all_records(case.client) == before


@pytest.mark.parametrize('missing', [LEDGER_KEY, durable.STATE_KEY, durable.JOURNAL_KEY])
def test_missing_foundation_or_native_history_is_never_initialized(case, missing):
    case.client.delete(missing)
    before = all_records(case.client)
    with pytest.raises(SpendBlocked):
        native.paid_credit_post(case.http.post, credit.ROUTE, request())
    assert not case.sends and all_records(case.client) == before


@pytest.mark.parametrize('damage', ['key', 'connection', 'membership', 'missing_parent', 'long', 'no_task'])
def test_actual_account_and_stored_context_cannot_be_supplied_or_rebound(case, damage):
    body = request()
    if damage == 'key':
        body['headers']['xi-api-key'] = 'another-private-synthetic-key'
    elif damage == 'connection':
        case.client.set(runtime._CHANNEL_PREFIX + CHANNEL, json.dumps({'id': CHANNEL, 'connection_id': 'new-connection-0001'}))
    elif damage == 'membership':
        case.client.srem(runtime._CHANNEL_INDEX, CHANNEL)
    elif damage == 'missing_parent':
        put_job(case.client, CHILD, ROOT)
        case.client.delete(runtime._JOB_PREFIX + ROOT)
        runtime._TASK_ID.set(CHILD)
    elif damage == 'long':
        put_job(case.client, duration=8)
    else:
        runtime._TASK_ID.set(None)
    before = deepcopy(state(case))
    with pytest.raises(SpendBlocked):
        native.paid_credit_post(case.http.post, credit.ROUTE, body)
    assert not case.sends and state(case) == before


@pytest.mark.parametrize('damage', ['route', 'model', 'output', 'extra_header', 'timeout', 'payload_authority'])
def test_unknown_request_shape_never_reserves(case, damage):
    body, url = request(), credit.ROUTE
    if damage == 'route':
        url += '?unpriced=1'
    elif damage == 'model':
        body['json']['model_id'] = 'eleven_turbo_v2_5'
    elif damage == 'output':
        body['params']['output_format'] = 'pcm_44100'
    elif damage == 'extra_header':
        body['headers']['Authorization'] = 'Bearer not-approved'
    elif damage == 'timeout':
        body['timeout'] = 9999
    else:
        body['production_context'] = {'lineage_id': ROOT}
    before = all_records(case.client)
    with pytest.raises(SpendBlocked):
        native.paid_credit_post(case.http.post, url, body)
    assert not case.sends and all_records(case.client) == before


@pytest.mark.parametrize('failure', ['timeout', '500', 'missing_meter', 'over_meter', 'bad_request', 'redirect'])
def test_uncertain_transport_or_meter_holds_all_and_blocks_both_replay_and_new_body(case, failure, monkeypatch):
    clock = [0.]
    monkeypatch.setattr(native, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(native, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    sends = []
    def respond(req):
        sends.append(req)
        if failure == 'timeout':
            raise httpx.ReadTimeout('RAW ' + KEY + ' ' + TEXT)
        headers = {'character-cost': '248', 'request-id': 'synthetic-uncertain'}
        if failure == 'missing_meter':
            headers.pop('character-cost')
        if failure == 'over_meter':
            # An internal-allocation overrun is known usage. A claimed amount
            # beyond this evidenced account quota remains out of scope.
            headers['character-cost'] = str(case.policy['balance']['quota_credits'] + 1)
        status = 500 if failure == '500' else 302 if failure == 'redirect' else 200
        return httpx.Response(status, content=b'private-response', headers=headers)
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        def sender(url, **kwargs):
            response = http.post(url, **kwargs)
            if failure == 'bad_request':
                # HTTPX attaches the actual request AFTER its MockTransport;
                # corrupt the returned response at the application boundary.
                response.request = httpx.Request('POST', 'https://example.invalid/another-route')
            return response
        with pytest.raises(SpendBlocked) as error:
            native.paid_credit_post(sender, credit.ROUTE, request())
        assert KEY not in str(error.value) and TEXT not in str(error.value) and 'RAW' not in str(error.value)
        assert (state(case)['spent_credits'], state(case)['reserved_credits']) == (0, 1000)
        for body in (request(), request(TEXT + ' Another candidate.')):
            with pytest.raises(SpendBlocked):
                native.paid_credit_post(sender, credit.ROUTE, body)
    assert len(sends) == 1


def test_caller_mutation_during_reservation_cannot_change_key_or_paid_payload(case, monkeypatch):
    body = request()
    original = deepcopy(body)
    reserve = durable.CreditLedger.reserve
    def mutate(self, **kwargs):
        receipt = reserve(self, **kwargs)
        body['json']['text'] = 'An unquoted injected request.'
        body['headers']['xi-api-key'] = 'a-mutated-private-key'
        body['params']['output_format'] = 'pcm_16000'
        return receipt
    monkeypatch.setattr(durable.CreditLedger, 'reserve', mutate)
    native.paid_credit_post(case.http.post, credit.ROUTE, body)
    assert json.loads(case.sends[0].content) == original['json']
    assert case.sends[0].headers['xi-api-key'] == KEY
    assert case.sends[0].url.params['output_format'] == 'mp3_44100_128'
    assert state(case)['spent_credits'] == 248


@pytest.mark.parametrize('after_reserve', [False, True])
def test_httpx_auth_override_is_checked_before_and_after_actual_reservation(case, monkeypatch, after_reserve):
    if after_reserve:
        reserve = durable.CreditLedger.reserve
        def drift(self, **kwargs):
            receipt = reserve(self, **kwargs)
            case.http.auth = ('unapproved-user', 'unapproved-pass')
            return receipt
        monkeypatch.setattr(durable.CreditLedger, 'reserve', drift)
    else:
        case.http.auth = ('unapproved-user', 'unapproved-pass')
    with pytest.raises(SpendBlocked, match='transport_unbound'):
        native.paid_credit_post(case.http.post, credit.ROUTE, request())
    assert not case.sends and state(case)['reserved_credits'] == (1000 if after_reserve else 0)
    case.http.auth = None
    if after_reserve:
        # The first invocation positively stopped before claiming/sending.
        # Its existing held receipt can now make exactly its first POST.
        native.paid_credit_post(case.http.post, credit.ROUTE, request())
        assert len(case.sends) == 1
        with pytest.raises(SpendBlocked):
            native.paid_credit_post(case.http.post, credit.ROUTE, request())
        assert len(case.sends) == 1


def test_expiry_during_post_records_real_meter_without_sending_again(case):
    def respond(req):
        case.sends.append(req)
        case.now[0] = NOW + timedelta(days=1)
        return httpx.Response(200, content=b'complete', headers={'character-cost': '248', 'request-id': 'late-meter'})
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        response = native.paid_credit_post(http.post, credit.ROUTE, request())
        assert response.content == b'complete'
        assert (state(case)['spent_credits'], state(case)['reserved_credits']) == (248, 0)
        with pytest.raises(SpendBlocked):
            native.paid_credit_post(http.post, credit.ROUTE, request())
    assert len(case.sends) == 1


@pytest.mark.parametrize('phase', ['reserve', 'settle'])
def test_lost_exec_reply_never_repeats_post_or_refunds_unverified_usage(case, monkeypatch, phase):
    factory = case.client.pipeline
    lost = []
    def pipeline(*args, **kwargs):
        pipe = factory(*args, **kwargs)
        execute = pipe.execute
        def perform(*a, **k):
            commands = [entry[0] for entry in pipe.command_stack]
            writes_native = any(command[0] == 'HSET' and command[1] == LEDGER_KEY
                                and str(command[2]).startswith('native_request:') for command in commands)
            writes_state = any(command[0] == 'HSET' and command[1] == durable.STATE_KEY for command in commands)
            should_lose = not lost and writes_state and (writes_native if phase == 'reserve' else not writes_native)
            result = execute(*a, **k)
            if should_lose:
                lost.append(True)
                raise ConnectionError('injected lost EXEC reply with ' + KEY)
            return result
        pipe.execute = perform
        return pipe
    monkeypatch.setattr(case.client, 'pipeline', pipeline)
    if phase == 'reserve':
        # The durable before-send journal can recover the committed hold.
        native.paid_credit_post(case.http.post, credit.ROUTE, request())
    else:
        with pytest.raises(SpendBlocked):
            native.paid_credit_post(case.http.post, credit.ROUTE, request())
    assert lost == [True]
    assert (state(case)['spent_credits'], state(case)['reserved_credits']) == (248, 0)
    with pytest.raises(SpendBlocked):
        native.paid_credit_post(case.http.post, credit.ROUTE, request())
    assert len(case.sends) == 1


def test_exact_meter_settles_even_if_downstream_audio_json_is_invalid(case):
    # Accounting does not pretend that the paid bytes passed narration QA.
    def respond(req):
        return httpx.Response(200, content=b'not-json-or-audio',
                              headers={'character-cost': '248', 'request-id': 'paid-invalid-audio'})
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        response = native.paid_credit_post(http.post, credit.ROUTE, request())
    assert response.content == b'not-json-or-audio' and state(case)['spent_credits'] == 248
    with pytest.raises(ValueError):
        response.json()


def _dispatch_inputs(case):
    context = runtime.resolve_context(case.client, ROOT)
    prepared = inspect_credit_request(credit.ROUTE, request())
    fingerprint = runtime._request_fingerprint(context, 'elevenlabs', prepared.operation, prepared.payload)
    intent = {'intent_id': sha('elevenlabs-native-credit-v1\0' + fingerprint),
              'root_lineage_id': ROOT, 'channel_id': CHANNEL, 'source_connection_id': CONNECTION,
              'request_sha256': fingerprint, 'route': credit.ROUTE, 'model': credit.MODEL,
              'voice_id': credit.VOICE_ID}
    actual = {'actual_account_sha256': case.policy['account_sha256'],
              'actual_credential_sha256': case.policy['credential_sha256']}
    return intent, context, actual


def test_worker_loss_before_send_claim_reuses_exact_reservation_once(case, monkeypatch):
    original = durable.CreditLedger.reserve
    def crash(self, **kwargs):
        original(self, **kwargs)
        raise SystemExit('simulate dead worker before a POST can be reached')
    with monkeypatch.context() as patch:
        patch.setattr(durable.CreditLedger, 'reserve', crash)
        with pytest.raises(SystemExit):
            native.paid_credit_post(case.http.post, credit.ROUTE, request())
    held = deepcopy(state(case))
    assert not case.sends and held['reserved_credits'] == 1000
    native.paid_credit_post(case.http.post, credit.ROUTE, request())
    assert len(case.sends) == 1 and state(case)['spent_credits'] == 248
    assert len(state(case)['intents']) == 1
    assert next(iter(state(case)['intents'].values()))['reservation'] == next(iter(held['intents'].values()))['reservation']
    with pytest.raises(SpendBlocked):
        native.paid_credit_post(case.http.post, credit.ROUTE, request())
    assert len(case.sends) == 1


@pytest.mark.parametrize('phase', ['reserve', 'claim'])
def test_post_commit_watch_error_can_continue_only_same_unsent_invocation(case, monkeypatch, phase):
    from redis.exceptions import WatchError
    from app.services import production_credit_dispatch as dispatch
    factory = case.client.pipeline
    lost = []
    def pipeline(*args, **kwargs):
        pipe = factory(*args, **kwargs); execute = pipe.execute
        def perform(*args, **kwargs):
            commands = [item[0] for item in pipe.command_stack]
            target = any(row[0] == 'HSET' and (
                row[1] == durable.STATE_KEY if phase == 'reserve' else
                str(row[1]).startswith(dispatch.PREFIX) and row[2] == 'claim') for row in commands)
            result = execute(*args, **kwargs)
            if target and not lost:
                lost.append(True)
                raise WatchError('ConnectionError after EXEC')
            return result
        pipe.execute = perform
        return pipe
    monkeypatch.setattr(case.client, 'pipeline', pipeline)
    native.paid_credit_post(case.http.post, credit.ROUTE, request())
    assert lost == [True] and len(case.sends) == 1 and len(state(case)['intents']) == 1
    with pytest.raises(SpendBlocked):
        native.paid_credit_post(case.http.post, credit.ROUTE, request())
    assert len(case.sends) == 1


def test_two_processes_continuing_one_prepared_receipt_cannot_both_claim(case):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app.services import production_credit_dispatch as dispatch
    intent, context, actual = _dispatch_inputs(case)
    dispatch.prepare(case.ledger, intent, context, actual)
    receipt = case.ledger.reserve(intent=intent, production_context=context, **actual)
    barrier = Barrier(2)
    def contend(_):
        barrier.wait()
        try:
            dispatch.claim(case.ledger, intent, context, actual, receipt)
            return 'one_send_permitted'
        except SpendBlocked as error:
            return str(error)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(contend, range(2)))
    assert sorted(results) == ['credit_dispatch_already_claimed', 'one_send_permitted']
    assert state(case)['reserved_credits'] == 1000


@pytest.mark.parametrize('damage', ['primary', 'anchor', 'both', 'claim_rollback',
                                  'both_claims_rollback', 'foundation_loss', 'expires'])
def test_missing_or_rolled_back_dispatch_journal_never_replays_a_claim(case, damage):
    from app.services import production_credit_dispatch as dispatch
    intent, context, actual = _dispatch_inputs(case)
    dispatch.prepare(case.ledger, intent, context, actual)
    receipt = case.ledger.reserve(intent=intent, production_context=context, **actual)
    dispatch.claim(case.ledger, intent, context, actual, receipt)
    key, anchor = dispatch._keys(intent)
    if damage == 'primary': case.client.delete(key)
    elif damage == 'anchor': case.client.delete(anchor)
    elif damage == 'both': case.client.delete(key, anchor)
    elif damage == 'claim_rollback': case.client.hdel(key, 'claim')
    elif damage == 'both_claims_rollback':
        case.client.hdel(key, 'claim'); case.client.hdel(anchor, 'claim')
    elif damage == 'foundation_loss':
        case.client.hdel(LEDGER_KEY, 'native_dispatch_claim:' + intent['intent_id'])
    else: case.client.expire(anchor, 10)
    before = state(case)
    with pytest.raises(SpendBlocked):
        native.paid_credit_post(case.http.post, credit.ROUTE, request())
    assert not case.sends and state(case) == before


def test_legacy_reservation_cannot_be_adopted_without_positive_operator_evidence(case):
    intent, context, actual = _dispatch_inputs(case)
    case.ledger.reserve(intent=intent, production_context=context, **actual)
    before = all_records(case.client)
    with pytest.raises(SpendBlocked, match='credit_dispatch_legacy_outcome_unverified'):
        native.paid_credit_post(case.http.post, credit.ROUTE, request())
    assert not case.sends and all_records(case.client) == before


def test_readonly_root_recovery_hint_requires_prepared_unclaimed_receipt(case, monkeypatch):
    from app.services import production_credit_dispatch as dispatch
    monkeypatch.setattr(runtime, 'configured_ledger', lambda **kwargs: case.foundation)
    intent, context, actual = _dispatch_inputs(case)
    assert dispatch.ready_for_root(ROOT) is False
    dispatch.prepare(case.ledger, intent, context, actual)
    assert dispatch.ready_for_root(ROOT) is False
    receipt = case.ledger.reserve(intent=intent, production_context=context, **actual)
    before = all_records(case.client)
    assert dispatch.ready_for_root(ROOT) is True
    assert dispatch.ready_for_root(CHILD) is False
    assert all_records(case.client) == before
    dispatch.claim(case.ledger, intent, context, actual, receipt)
    assert dispatch.ready_for_root(ROOT) is False


@pytest.mark.parametrize('correct_receipt', [False, True])
def test_explicit_legacy_presend_proof_is_receipt_bound_and_single_use(case, correct_receipt):
    from app.services import production_credit_dispatch as dispatch
    intent, context, actual = _dispatch_inputs(case)
    receipt = case.ledger.reserve(intent=intent, production_context=context, **actual)
    identity = dispatch._identity(case.ledger, case.policy, intent, context, actual)
    prepared = {**identity, 'origin': {'kind': 'verified_legacy_pre_send',
        'reservation_sha256': receipt['reservation_sha256'] if correct_receipt else '0' * 64,
        'evidence_sha256': sha('synthetic positive runtime proof of failure before sender')}}
    key, anchor = dispatch._keys(intent)
    # Test-only operator preparation, not a request/runtime auto-recovery API.
    case.client.hset(key, 'prepared', durable._json(prepared))
    case.client.hset(anchor, 'prepared', durable._hash(prepared))
    if correct_receipt:
        native.paid_credit_post(case.http.post, credit.ROUTE, request())
        assert len(case.sends) == 1 and state(case)['spent_credits'] == 248
        with pytest.raises(SpendBlocked):
            native.paid_credit_post(case.http.post, credit.ROUTE, request())
        assert len(case.sends) == 1
    else:
        with pytest.raises(SpendBlocked):
            native.paid_credit_post(case.http.post, credit.ROUTE, request())
        assert not case.sends and state(case)['reserved_credits'] == 1000


def test_real_meter_above_remaining_internal_share_is_recorded_and_next_post_stops(case):
    sends, holds = [], []
    amounts = (990, 248)

    def respond(req):
        sends.append(req)
        holds.append(state(case)['reserved_credits'])
        return httpx.Response(200, content=b'original-paid-audio-response', headers={
            'character-cost': str(amounts[len(sends) - 1]),
            'request-id': 'actual-native-overrun-' + str(len(sends)),
        })

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        native.paid_credit_post(http.post, credit.ROUTE, request())
        result = native.paid_credit_post(http.post, credit.ROUTE, request(TEXT + ' Second request.'))
        assert result.content == b'original-paid-audio-response'
        assert holds == [1000, 10]
        summary = case.ledger.summary()
        assert summary['spent_credits'] == 1238 and summary['overrun_credits'] == 238
        assert summary['available_credits'] == summary['reserved_credits'] == 0
        assert case.foundation.snapshot()['period']['used_micro'] == 0
        before = deepcopy(state(case))
        with pytest.raises(SpendBlocked, match='credit_allocation_exhausted'):
            native.paid_credit_post(http.post, credit.ROUTE, request(TEXT + ' Third request.'))
        with pytest.raises(SpendBlocked, match='credit_cross_mode_request_conflict'):
            native.paid_credit_post(http.post, credit.ROUTE, request(TEXT + ' Second request.'))
        assert len(sends) == 2 and state(case) == before


@pytest.mark.parametrize('voice_id', sorted(__import__('app.services.narrator_rotation', fromlist=['VOICE_IDS']).VOICE_IDS))
def test_actual_dispatcher_routes_every_commissioned_voice_to_existing_native_credits(case, monkeypatch, voice_id):
    from app.services import narrator_rotation as rotation, production_spend_quotes
    data = {'has_more': False, 'voices': [{'voice_id': voice, 'is_legacy': False,
        'high_quality_base_model_ids': [credit.MODEL, credit.TURKISH_SHORT_MODEL]} for voice in rotation.VOICE_IDS]}
    before = case.client.hget(durable.STATE_KEY, 'policy')
    rotation.commission(case.ledger, data, observed_at=NOW)
    monkeypatch.setattr(production_spend_quotes, 'quote_http_request',
                        lambda *a, **k: pytest.fail('Approved narrator escaped the native credit route'))
    url = rotation.route(voice_id)
    runtime.paid_post(case.http.post, url, **request())
    assert len(case.sends) == 1 and str(case.sends[0].url).startswith(url + '?')
    assert state(case)['spent_credits'] == 248 and state(case)['reserved_credits'] == 0
    assert case.client.hget(durable.STATE_KEY, 'policy') == before
    with pytest.raises(SpendBlocked): runtime.paid_post(case.http.post, url, **request())
    assert len(case.sends) == 1


def test_rotating_voice_without_account_grant_cannot_fall_back_to_cash(case, monkeypatch):
    from app.services import narrator_rotation as rotation, production_spend_quotes
    monkeypatch.setattr(production_spend_quotes, 'quote_http_request',
                        lambda *a, **k: pytest.fail('Uncommissioned voice tried cash fallback'))
    with pytest.raises(SpendBlocked, match='credit_actual_binding_mismatch'):
        runtime.paid_post(case.http.post, rotation.route(rotation.POOLS['en'][0][0]), **request())
    assert not case.sends and state(case)['intents'] == {}
