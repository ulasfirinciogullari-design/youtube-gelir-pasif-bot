"""Durable native-credit admission under races, lost replies and partial loss."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from threading import Barrier

import fakeredis
import pytest
from redis.exceptions import ConnectionError, WatchError

from app.services.production_spend import LEDGER_KEY, SpendBlocked, SpendLedger, SpendPolicy, SpendQuote
from app.services import production_credit_funding as credit
from app.services import production_credit_ledger as ledger


NOW = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.fixture
def policy():
    return {
        'version': 1, 'provider': 'elevenlabs', 'month': '2026-09',
        'valid_from': '2026-09-09T13:00:00Z', 'valid_until': '2026-09-10T00:00:00Z',
        'account_sha256': sha('synthetic account'),
        'credential_sha256': sha('synthetic credential'),
        'evidence_sha256': sha('synthetic balance and cash control evidence'),
        'reconciliation_sha256': sha('synthetic reconciliation'),
        'route': credit.ROUTE, 'model': credit.MODEL, 'voice_id': credit.VOICE_ID,
        'allocation_credits': 1000,
        'balance': {'observed_at': '2026-09-09T12:59:00Z',
                    'provider_reset_at': '2026-09-25T00:00:00Z',
                    'quota_credits': 131000, 'used_credits': 71383, 'withheld_credits': 100},
        'cash_controls': {'max_credit_limit_extension': 0, 'can_extend_character_limit': False,
                          'overage_observation_sha256': sha('synthetic overage OFF'),
                          'auto_top_up_enabled': False, 'auto_top_up_source': 'owner_attested',
                          'auto_top_up_proof_sha256': sha('synthetic owner OFF attestation')},
    }


@pytest.fixture
def client():
    return fakeredis.FakeRedis(decode_responses=True)


def binding(policy):
    return {'actual_account_sha256': policy['account_sha256'],
            'actual_credential_sha256': policy['credential_sha256']}


def intent(number=1, **changes):
    return {'intent_id': sha('intent ' + str(number)),
            'root_lineage_id': 'original-root-0001', 'channel_id': 'channel-0001',
            'source_connection_id': 'source-connection-0001',
            'request_sha256': sha('immutable request ' + str(number)),
            'route': credit.ROUTE, 'model': credit.MODEL, 'voice_id': credit.VOICE_ID,
            **changes}


def observation(policy, receipt, **changes):
    return {'version': 1, 'terminal': True, 'source': 'verified_provider_meter',
            **{key: receipt['intent'][key] for key in (
                'intent_id', 'root_lineage_id', 'channel_id', 'source_connection_id', 'request_sha256')},
            'reservation_sha256': receipt['reservation_sha256'],
            'account_sha256': policy['account_sha256'], 'credential_sha256': policy['credential_sha256'],
            'provider_request_id_sha256': sha('provider request ' + receipt['intent']['intent_id']),
            'response_proof_sha256': sha('terminal response ' + receipt['intent']['intent_id']),
            'actual_credit_cost': 248, 'observed_at': '2026-09-09T14:00:00Z', **changes}


def store(client, now=NOW):
    return ledger.CreditLedger(client, clock=lambda: now)


def snapshot(client):
    return {key: client.hgetall(key) for key in (ledger.STATE_KEY, ledger.JOURNAL_KEY)}


class InterceptClient:
    """Observe the actual MULTI/EXEC boundary without replacing Redis semantics."""
    def __init__(self, client, *, before=None, after=None):
        self.client, self.before, self.after = client, before, after
        self.calls = 0

    def pipeline(self):
        pipe = self.client.pipeline()
        real_execute = pipe.execute

        def execute(*args, **kwargs):
            self.calls += 1
            if self.before:
                self.before(self.calls)
            result = real_execute(*args, **kwargs)
            if self.after:
                return self.after(self.calls, result)
            return result

        pipe.execute = execute
        return pipe


def lose_reply(call, result):
    raise ConnectionError('injected lost acknowledged-commit reply')


def test_uninitialized_reads_and_reservations_never_create_state(client, policy):
    for operation in (lambda: store(client).summary(),
                      lambda: store(client).reserve(intent=intent(), **binding(policy))):
        with pytest.raises(SpendBlocked, match='credit_not_initialized_or_partial'):
            operation()
        assert client.dbsize() == 0


def test_explicit_initialization_is_nonexpiring_and_never_resets_debt(client, policy):
    live = store(client)
    assert live.initialize(policy) is True
    assert client.ttl(ledger.STATE_KEY) == client.ttl(ledger.JOURNAL_KEY) == -1
    assert client.dbsize() == 2
    first = live.reserve(intent=intent(), **binding(policy))
    before = snapshot(client)
    assert live.initialize(policy) is False
    assert snapshot(client) == before
    assert live.summary()['reserved_credits'] == 1000
    assert live.summary()['available_credits'] == 0
    assert first['reserved_credits'] == 1000
    assert not client.exists('youtube_studio:{production_spend}:v1')


def test_settlement_releases_unused_capacity_but_durable_replay_never_reopens(client, policy):
    live = store(client)
    live.initialize(policy)
    receipt = live.reserve(intent=intent(), **binding(policy))
    seen = observation(policy, receipt)
    assert live.settle(observation=seen, **binding(policy)) == seen
    before = snapshot(client)
    assert live.settle(observation=seen, **binding(policy)) == seen
    assert snapshot(client) == before
    assert live.summary()['spent_credits'] == 248
    assert live.summary()['available_credits'] == 752
    for request in (intent(), intent(2, request_sha256=intent()['request_sha256'])):
        with pytest.raises(SpendBlocked, match='already_reserved'):
            live.reserve(intent=request, **binding(policy))
    assert snapshot(client) == before
    next_receipt = live.reserve(intent=intent(2), **binding(policy))
    assert next_receipt['reserved_credits'] == 752
    assert client.hget(ledger.JOURNAL_KEY, 'reservation:' + intent()['intent_id'])


@pytest.mark.parametrize('key', [ledger.STATE_KEY, ledger.JOURNAL_KEY])
def test_loss_of_either_key_never_implicitly_reinitializes(client, policy, key):
    live = store(client)
    live.initialize(policy)
    live.reserve(intent=intent(), **binding(policy))
    client.delete(key)
    before = snapshot(client)
    for operation in (live.summary, lambda: live.initialize(policy),
                      lambda: live.reserve(intent=intent(2), **binding(policy))):
        with pytest.raises(SpendBlocked):
            operation()
        assert snapshot(client) == before


@pytest.mark.parametrize('damage', ['policy', 'state', 'genesis', 'receipt', 'request', 'root',
                                  'revision', 'state_hash', 'extra'])
def test_partial_persistence_damage_blocks_without_repairing_capacity(client, policy, damage):
    live = store(client)
    live.initialize(policy)
    receipt = live.reserve(intent=intent(), **binding(policy))
    if damage in {'policy', 'state'}:
        client.hdel(ledger.STATE_KEY, damage)
    elif damage == 'extra':
        client.hset(ledger.JOURNAL_KEY, 'foreign', 'unrecognized')
    else:
        field = {'genesis': 'genesis', 'receipt': 'reservation:' + intent()['intent_id'],
                 'request': 'request:' + receipt['request_identity_sha256'],
                 'root': 'root:original-root-0001', 'revision': 'revision',
                 'state_hash': 'state_sha256'}[damage]
        client.hdel(ledger.JOURNAL_KEY, field)
    before = snapshot(client)
    with pytest.raises(SpendBlocked):
        live.reserve(intent=intent(2), **binding(policy))
    assert snapshot(client) == before


def test_balanced_state_rollback_is_detected_by_independent_journal(client, policy):
    live = store(client)
    live.initialize(policy)
    old = client.hget(ledger.STATE_KEY, 'state')
    live.reserve(intent=intent(), **binding(policy))
    client.hset(ledger.STATE_KEY, 'state', old)
    with pytest.raises(SpendBlocked, match='credit_journal_mismatch'):
        live.summary()


@pytest.mark.parametrize('key', [ledger.STATE_KEY, ledger.JOURNAL_KEY])
def test_expiring_replay_records_block_all_operations_without_auto_persist(client, policy, key):
    live = store(client)
    live.initialize(policy)
    receipt = live.reserve(intent=intent(), **binding(policy))
    client.expire(key, 60)
    before = snapshot(client)
    for action in (live.summary, lambda: live.initialize(policy),
                   lambda: live.reserve(intent=intent(2), **binding(policy)),
                   lambda: live.settle(observation=observation(policy, receipt), **binding(policy))):
        with pytest.raises(SpendBlocked, match='credit_store_expiring'):
            action()
        assert snapshot(client) == before
        assert 0 < client.pttl(key) <= 60000


def test_expiry_assigned_between_read_and_exec_invalidates_admission(client, policy):
    store(client).initialize(policy)

    def expire_during_commit(call):
        if call == 1:
            client.expire(ledger.JOURNAL_KEY, 60)

    wrapped = InterceptClient(client, before=expire_during_commit)
    with pytest.raises(SpendBlocked, match='credit_store_expiring'):
        store(wrapped).reserve(intent=intent(), **binding(policy))
    assert not client.hexists(ledger.JOURNAL_KEY, 'reservation:' + intent()['intent_id'])


def test_lost_initialization_reply_retains_state_and_reentry_never_resets(client, policy):
    broken = store(InterceptClient(client, after=lose_reply))
    with pytest.raises(SpendBlocked, match='credit_store_unavailable'):
        broken.initialize(policy)
    before = snapshot(client)
    assert store(client).initialize(policy) is False
    assert snapshot(client) == before


def test_lost_reservation_reply_keeps_all_capacity_and_blocks_any_resend(client, policy):
    store(client).initialize(policy)
    broken = store(InterceptClient(client, after=lose_reply))
    with pytest.raises(SpendBlocked, match='credit_store_unavailable'):
        broken.reserve(intent=intent(), **binding(policy))
    before = snapshot(client)
    assert store(client).summary()['reserved_credits'] == 1000
    for request in (intent(), intent(2)):
        with pytest.raises(SpendBlocked):
            store(client).reserve(intent=request, **binding(policy))
    assert snapshot(client) == before


def test_lost_settlement_reply_can_only_repeat_same_meter_without_extra_release(client, policy):
    live = store(client)
    live.initialize(policy)
    receipt = live.reserve(intent=intent(), **binding(policy))
    seen = observation(policy, receipt)
    with pytest.raises(SpendBlocked, match='credit_store_unavailable'):
        store(InterceptClient(client, after=lose_reply)).settle(observation=seen, **binding(policy))
    before = snapshot(client)
    assert live.settle(observation=seen, **binding(policy)) == seen
    assert snapshot(client) == before
    assert live.summary()['spent_credits'] == 248
    with pytest.raises(SpendBlocked, match='credit_observation_conflict'):
        live.settle(observation=observation(policy, receipt, actual_credit_cost=247), **binding(policy))
    assert snapshot(client) == before


def test_two_simultaneous_reservations_have_only_one_admitted_request(client, policy):
    store(client).initialize(policy)
    barrier = Barrier(2)

    def reserve_one(number):
        wrapped = InterceptClient(client, before=lambda call: barrier.wait(timeout=10) if call == 1 else None)
        try:
            return store(wrapped).reserve(intent=intent(number), **binding(policy))
        except SpendBlocked as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve_one, [1, 2]))
    assert sum(type(result) is dict for result in results) == 1
    assert sum(result == 'credit_pool_has_uncertain_intent' for result in results) == 1
    assert store(client).summary()['reserved_credits'] == 1000


def test_watch_race_reloads_the_changed_state_and_does_not_overwrite(client, policy):
    live = store(client)
    live.initialize(policy)

    def racing(call):
        if call == 1:
            live.reserve(intent=intent(2), **binding(policy))

    wrapped = InterceptClient(client, before=racing)
    with pytest.raises(SpendBlocked, match='credit_pool_has_uncertain_intent'):
        store(wrapped).reserve(intent=intent(), **binding(policy))
    assert store(client).summary()['reserved_credits'] == 1000
    assert not client.hexists(ledger.JOURNAL_KEY, 'reservation:' + intent()['intent_id'])


def test_watch_contention_is_bounded_and_never_writes(client, policy):
    store(client).initialize(policy)
    before = snapshot(client)

    def conflict(call):
        raise WatchError('injected concurrent mutation')

    wrapped = InterceptClient(client, before=conflict)
    with pytest.raises(SpendBlocked, match='credit_store_contention'):
        store(wrapped).reserve(intent=intent(), **binding(policy))
    assert wrapped.calls == 8 and snapshot(client) == before


@pytest.mark.parametrize('ack', [None, [0, True], [True, 3], [0, 0], [0, 3, 0]])
def test_ambiguous_commit_ack_never_returns_a_send_permit(client, policy, ack):
    store(client).initialize(policy)
    wrapped = InterceptClient(client, after=lambda call, result: ack)
    with pytest.raises(SpendBlocked, match='credit_commit_uncertain'):
        store(wrapped).reserve(intent=intent(), **binding(policy))
    assert store(client).summary()['reserved_credits'] == 1000


@pytest.mark.parametrize('ack', [[1], ['PONG'], None, [True, True]])
def test_read_confirmation_requires_exact_ping_ack(client, policy, ack):
    store(client).initialize(policy)
    before = snapshot(client)
    wrapped = InterceptClient(client, after=lambda call, result: ack)
    with pytest.raises(SpendBlocked, match='credit_commit_uncertain'):
        store(wrapped).summary()
    assert snapshot(client) == before


@pytest.mark.parametrize('mutation', ['credential', 'account', 'policy', 'expired', 'meter'])
def test_changed_authority_or_invalid_meter_cannot_change_persistent_capacity(client, policy, mutation):
    live = store(client)
    live.initialize(policy)
    receipt = live.reserve(intent=intent(), **binding(policy))
    before = snapshot(client)
    if mutation in {'credential', 'account'}:
        wrong = binding(policy) | {'actual_' + mutation + '_sha256': sha('other binding')}
        action = lambda: live.settle(observation=observation(policy, receipt), **wrong)
    elif mutation == 'policy':
        changed = deepcopy(policy)
        changed['allocation_credits'] = 2000
        action = lambda: live.initialize(changed)
    elif mutation == 'expired':
        action = lambda: store(client, NOW + timedelta(days=1)).summary()
    else:
        action = lambda: live.settle(observation=observation(policy, receipt, actual_credit_cost=0), **binding(policy))
    with pytest.raises(SpendBlocked):
        action()
    assert snapshot(client) == before


@pytest.mark.parametrize('raw', ['{"revision":0,"revision":1}', '{"revision":NaN}', '[]', 'null'])
def test_malformed_persisted_json_is_rejected_without_new_state(client, policy, raw):
    store(client).initialize(policy)
    client.hset(ledger.STATE_KEY, 'state', raw)
    before = snapshot(client)
    with pytest.raises(SpendBlocked, match='credit_store_invalid'):
        store(client).summary()
    assert snapshot(client) == before


def foundation(client, *, amount=0, initialize=True):
    money = SpendLedger(client, SpendPolicy(*(amount for _ in range(6))), clock=lambda: NOW)
    if initialize:
        money.initialize()
    return money


def production(client, money):
    return ledger.CreditLedger(client, clock=lambda: NOW, foundation=money)


def context(client):
    value = {'channel_id': 'channel-0001', 'lineage_id': 'original-root-0001',
             'connection_id': 'source-connection-0001', 'kind': 'shorts'}
    client.hset(LEDGER_KEY, 'binding:original-root-0001', json.dumps(value))
    return value


def old_cash_reserve(money, number=1):
    return money.reserve(request_key=intent(number)['request_sha256'], channel_id='channel-0001',
                         lineage_id='original-root-0001', kind='shorts',
                         quote=SpendQuote('elevenlabs', credit.MODEL, 100, 'fixture-v1'))


def test_production_native_credits_keep_zero_cash_policy_and_existing_root(client, policy):
    money = foundation(client)
    live = production(client, money)
    bound = context(client)
    old_money = money.snapshot()
    assert live.initialize(policy)
    before_bind = client.hget(LEDGER_KEY, 'binding:original-root-0001')
    account = live.binding_snapshot()
    assert account['account_sha256'] == policy['account_sha256']
    assert account['credential_sha256'] == policy['credential_sha256']
    receipt = live.reserve(intent=intent(), production_context=bound, **binding(policy))
    native_field = 'native_request:' + sha(intent()['request_sha256'])
    marker = json.loads(client.hget(LEDGER_KEY, native_field))
    assert marker['reservation_sha256'] == receipt['reservation_sha256']
    assert {key: marker[key] for key in bound} == bound
    assert 'quote' not in marker and 'cash_micro' not in marker
    live.settle(observation=observation(policy, receipt), **binding(policy))
    assert live.summary()['spent_credits'] == 248
    assert money.snapshot() == old_money
    assert client.hget(LEDGER_KEY, 'binding:original-root-0001') == before_bind
    assert not client.hexists(LEDGER_KEY, 'request:' + sha(intent()['request_sha256']))
    assert client.pttl(LEDGER_KEY) == -1


def test_native_initialization_cannot_initialize_foundation_or_recover_both_lost_keys(client, policy):
    money = foundation(client, initialize=False)
    live = production(client, money)
    with pytest.raises(SpendBlocked, match='spend_not_initialized'):
        live.initialize(policy)
    assert client.dbsize() == 0
    money.initialize()
    live.initialize(policy)
    client.delete(ledger.STATE_KEY, ledger.JOURNAL_KEY)
    before = client.hgetall(LEDGER_KEY)
    with pytest.raises(SpendBlocked, match='credit_not_initialized_or_partial'):
        live.initialize(policy)
    assert client.hgetall(LEDGER_KEY) == before
    assert not client.exists(ledger.STATE_KEY, ledger.JOURNAL_KEY)


def test_existing_cash_request_cannot_be_replayed_using_credits(client, policy):
    money = foundation(client, amount=1000)
    old_cash_reserve(money)
    live = production(client, money)
    live.initialize(policy)
    bound = context(client)
    before = snapshot(client), client.hgetall(LEDGER_KEY)
    with pytest.raises(SpendBlocked, match='credit_cross_mode_request_conflict'):
        live.reserve(intent=intent(), production_context=bound, **binding(policy))
    assert (snapshot(client), client.hgetall(LEDGER_KEY)) == before
    assert live.summary()['available_credits'] == 1000


@pytest.mark.parametrize('loss', ['none', 'mode', 'state', 'journal', 'both_native'])
def test_commissioned_native_mode_never_falls_back_to_cash_even_after_partial_loss(client, policy, loss):
    money = foundation(client, amount=1000)
    live = production(client, money)
    live.initialize(policy)
    if loss == 'mode':
        client.hdel(LEDGER_KEY, ledger.MODE_FIELD)
    elif loss == 'state':
        client.delete(ledger.STATE_KEY)
    elif loss == 'journal':
        client.delete(ledger.JOURNAL_KEY)
    elif loss == 'both_native':
        client.delete(ledger.STATE_KEY, ledger.JOURNAL_KEY)
    before = snapshot(client), client.hgetall(LEDGER_KEY)
    with pytest.raises(SpendBlocked, match='spend_native_credit_required'):
        old_cash_reserve(money)
    assert (snapshot(client), client.hgetall(LEDGER_KEY)) == before


@pytest.mark.parametrize('damage', ['mode', 'request', 'wrong_request', 'cash_duplicate', 'expired_foundation'])
def test_foundation_partial_loss_cannot_release_or_reopen_credit(client, policy, damage):
    money = foundation(client)
    live = production(client, money)
    live.initialize(policy)
    receipt = live.reserve(intent=intent(), production_context=context(client), **binding(policy))
    field = 'native_request:' + sha(intent()['request_sha256'])
    if damage == 'mode':
        client.hdel(LEDGER_KEY, ledger.MODE_FIELD)
    elif damage == 'request':
        client.hdel(LEDGER_KEY, field)
    elif damage == 'wrong_request':
        client.hset(LEDGER_KEY, field, '{}')
    elif damage == 'cash_duplicate':
        client.hset(LEDGER_KEY, 'request:' + sha(intent()['request_sha256']), '{}')
    else:
        client.expire(LEDGER_KEY, 60)
    before = snapshot(client), client.hgetall(LEDGER_KEY)
    for action in (live.summary, live.binding_snapshot,
                   lambda: live.settle(observation=observation(policy, receipt), **binding(policy))):
        with pytest.raises(SpendBlocked):
            action()
        assert (snapshot(client), client.hgetall(LEDGER_KEY)) == before


@pytest.mark.parametrize('changed', [None, {'kind': 'long'}, {'connection_id': 'new-connection-0002'},
                                   {'channel_id': 'channel-0002'}, {'lineage_id': 'child-task-00002'}])
def test_production_requires_exact_original_durable_binding(client, policy, changed):
    money = foundation(client)
    live = production(client, money)
    live.initialize(policy)
    bound = context(client)
    supplied = None if changed is None else bound | changed
    before = snapshot(client), client.hgetall(LEDGER_KEY)
    with pytest.raises(SpendBlocked, match='credit_production_context_invalid'):
        live.reserve(intent=intent(), production_context=supplied, **binding(policy))
    assert (snapshot(client), client.hgetall(LEDGER_KEY)) == before


def test_standalone_access_cannot_mutate_production_native_ledger(client, policy):
    money = foundation(client)
    live = production(client, money)
    live.initialize(policy)
    before = snapshot(client), client.hgetall(LEDGER_KEY)
    for action in (store(client).summary, lambda: store(client).initialize(policy),
                   lambda: store(client).reserve(intent=intent(), **binding(policy))):
        with pytest.raises(SpendBlocked, match='credit_foundation_required'):
            action()
    assert (snapshot(client), client.hgetall(LEDGER_KEY)) == before


def test_atomic_native_initialization_racing_cash_request_does_not_admit_both_modes(client, policy):
    money = foundation(client, amount=1000)
    live = production(client, money)
    original = client.pipeline
    entered = [False]

    def pipeline(*args, **kwargs):
        pipe = original(*args, **kwargs)
        execute = pipe.execute

        def racing_execute(*args, **kwargs):
            if not entered[0]:
                entered[0] = True
                # A separate client enters the exact same FakeServer while the
                # USD path has already read the absence of native mode.
                live.initialize(policy)
            return execute(*args, **kwargs)

        pipe.execute = racing_execute
        return pipe

    client.pipeline = pipeline
    with pytest.raises(SpendBlocked, match='spend_native_credit_required'):
        old_cash_reserve(money)
    client.pipeline = original
    assert money.snapshot()['period']['used_micro'] == 0
    assert live.summary()['available_credits'] == 1000


def test_lost_production_reserve_reply_preserves_all_three_linked_records(client, policy):
    money = foundation(client)
    # The same concrete client is required; wrap only its pipeline method.
    original = client.pipeline
    live = production(client, money)
    live.initialize(policy)
    bound = context(client)

    def pipeline(*args, **kwargs):
        pipe = original(*args, **kwargs)
        execute = pipe.execute

        def missing_reply(*args, **kwargs):
            execute(*args, **kwargs)
            raise ConnectionError('injected lost linked reservation reply')

        pipe.execute = missing_reply
        return pipe

    client.pipeline = pipeline
    with pytest.raises(SpendBlocked, match='credit_store_unavailable'):
        live.reserve(intent=intent(), production_context=bound, **binding(policy))
    client.pipeline = original
    assert live.summary()['reserved_credits'] == 1000
    assert client.hexists(LEDGER_KEY, 'native_request:' + sha(intent()['request_sha256']))
    with pytest.raises(SpendBlocked):
        live.reserve(intent=intent(), production_context=bound, **binding(policy))


def test_together_rolled_back_native_state_and_journal_cannot_hide_foundation_debt(client, policy):
    money = foundation(client)
    live = production(client, money)
    live.initialize(policy)
    bound = context(client)
    old = snapshot(client)
    live.reserve(intent=intent(), production_context=bound, **binding(policy))
    for key, fields in old.items():
        client.delete(key)
        client.hset(key, mapping=fields)
    before = snapshot(client), client.hgetall(LEDGER_KEY)
    with pytest.raises(SpendBlocked, match='credit_foundation_mismatch'):
        live.reserve(intent=intent(2), production_context=bound, **binding(policy))
    assert (snapshot(client), client.hgetall(LEDGER_KEY)) == before


def test_surviving_request_marker_alone_blocks_cash_replay_and_fresh_native_initialization(client, policy):
    money = foundation(client, amount=1000)
    live = production(client, money)
    live.initialize(policy)
    live.reserve(intent=intent(), production_context=context(client), **binding(policy))
    client.delete(ledger.STATE_KEY, ledger.JOURNAL_KEY)
    client.hdel(LEDGER_KEY, ledger.MODE_FIELD)
    before = client.hgetall(LEDGER_KEY)
    with pytest.raises(SpendBlocked, match='spend_native_credit_required'):
        old_cash_reserve(money)
    with pytest.raises(SpendBlocked, match='credit_not_initialized_or_partial'):
        live.initialize(policy)
    with pytest.raises(SpendBlocked, match='credit_foundation_required'):
        store(client).initialize(policy)
    assert client.hgetall(LEDGER_KEY) == before


def test_uninspected_foundation_scan_tail_cannot_mean_absent_native_history(client, monkeypatch):
    money = foundation(client, amount=1000)
    original = client.pipeline

    def pipeline(*args, **kwargs):
        pipe = original(*args, **kwargs)
        pipe.hscan = lambda *args, **kwargs: (1, {})
        return pipe

    monkeypatch.setattr(client, 'pipeline', pipeline)
    with pytest.raises(SpendBlocked, match='credit_foundation_history_limit'):
        old_cash_reserve(money)


def test_known_usage_above_internal_allocation_is_durable_debt_not_unverified_usage(client, policy):
    money = foundation(client)
    live = production(client, money)
    live.initialize(policy)
    bound = context(client)
    receipt = live.reserve(intent=intent(), production_context=bound, **binding(policy))
    live.settle(observation=observation(policy, receipt, actual_credit_cost=990), **binding(policy))
    second = live.reserve(intent=intent(2), production_context=bound, **binding(policy))
    assert second['reserved_credits'] == 10
    seen = observation(policy, second, actual_credit_cost=248)
    live.settle(observation=seen, **binding(policy))
    summary = live.summary()
    assert summary['spent_credits'] == 1238 and summary['overrun_credits'] == 238
    assert summary['available_credits'] == summary['reserved_credits'] == 0
    before = snapshot(client), client.hgetall(LEDGER_KEY)
    assert live.settle(observation=seen, **binding(policy)) == seen
    assert live.initialize(policy) is False
    with pytest.raises(SpendBlocked, match='credit_allocation_exhausted'):
        live.reserve(intent=intent(3), production_context=bound, **binding(policy))
    with pytest.raises(SpendBlocked, match='credit_cross_mode_request_conflict'):
        live.reserve(intent=intent(2), production_context=bound, **binding(policy))
    assert (snapshot(client), client.hgetall(LEDGER_KEY)) == before
    assert money.snapshot()['period']['used_micro'] == 0


def test_lost_overrun_settlement_reply_keeps_exact_consumption_and_never_restores_capacity(client, policy):
    live = store(client)
    live.initialize(policy)
    receipt = live.reserve(intent=intent(), **binding(policy))
    seen = observation(policy, receipt, actual_credit_cost=1238)
    with pytest.raises(SpendBlocked, match='credit_store_unavailable'):
        store(InterceptClient(client, after=lose_reply)).settle(observation=seen, **binding(policy))
    assert live.summary()['spent_credits'] == 1238
    before = snapshot(client)
    assert live.settle(observation=seen, **binding(policy)) == seen
    assert snapshot(client) == before
    with pytest.raises(SpendBlocked, match='credit_allocation_exhausted'):
        live.reserve(intent=intent(2), **binding(policy))
