"""Offline original-lineage review admission with real Redis WATCH semantics."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from threading import Barrier

import httpx
import pytest
from redis.exceptions import ConnectionError, WatchError

from app.services import abacus_router_review_journal as journal
from app.services.abacus_router_adapter import ENDPOINT, MODEL, OPERATION, prepare_router_request
from app.services import production_connection_continuity as continuity
from app.services.production_spend import LEDGER_KEY
from app.services.production_spend_runtime import _request_fingerprint
from test_production_connection_continuity import case, REVISION, OLD, NEW, _dump
from test_production_credit_ledger import InterceptClient


NOW = datetime(2026, 9, 9, 16, 0, tzinfo=timezone.utc)
KEYS = (journal.STATE_KEY, journal.JOURNAL_KEY, journal.ANCHOR_KEY)


def request(text='Complete immutable story review', key='synthetic-not-live-key'):
    return prepare_router_request([{'type': 'text', 'text': text}], api_key=key,
        system_instruction='Return the complete requested JSON object.',
        json_schema={'type': 'object', 'properties': {'approved': {'type': 'boolean'}},
                     'required': ['approved'], 'additionalProperties': False}, max_tokens=128)


def response(prepared, **changes):
    payload = {'id': 'synthetic-review-1', 'object': 'chat.completion', 'model': MODEL,
        'choices': [{'index': 0, 'finish_reason': 'stop',
                     'message': {'role': 'assistant', 'content': '{"approved":false}'}}], **changes}
    wire = prepared.wire_kwargs()
    wire.pop('timeout')
    return httpx.Response(200, json=payload, request=httpx.Request('POST', ENDPOINT, **wire))


def store(case, now=NOW, client=None):
    return journal.RouterReviewJournal(client if client is not None else case.client, clock=lambda: now)


@pytest.fixture
def policy(case):
    receipt = continuity.prepare_connection_continuity(continuity.ROOT_ID, continuity.LEAF_ID,
        continuity.CHANNEL_ID, REVISION, client=case.client)
    return {'version': 1, 'kind': 'existing_subscription_retained_review',
        'endpoint': ENDPOINT, 'model': MODEL, 'original_task_id': continuity.ROOT_ID,
        'leaf_task_id': continuity.LEAF_ID, 'channel_id': continuity.CHANNEL_ID,
        'profile_revision': REVISION, 'old_connection_id': OLD, 'current_connection_id': NEW,
        'continuity_sha256': receipt['receipt_sha256'], 'credential_sha256': request().credential_sha256,
        'entitlement_evidence_sha256': 'e' * 64,
        'entitlement_source': 'owner_subscription_and_official_router_api_terms',
        'valid_from': '2026-09-09T15:00:00Z', 'valid_until': '2026-09-10T00:00:00Z',
        'historical_extra_cash_micro': None, 'new_cash_allowance_micro': 0}


def original_records(case):
    return {k: v for k, v in _dump(case.client).items() if k not in KEYS}


def test_two_distinct_reviews_never_change_finance_source_claim_or_scheduler(case, policy):
    before = original_records(case)
    ledger = store(case)
    commissioned = ledger.commission(policy)
    assert commissioned['qa_approved'] is commissioned['publish_eligible'] is False
    for purpose, text in zip(journal.PURPOSES, ('whole story', 'all retained frames')):
        prepared = request(text)
        receipt = ledger.reserve(purpose, prepared)
        assert receipt['root_request_fingerprint'] == _request_fingerprint(
            {'lineage_id': continuity.ROOT_ID}, 'abacus', OPERATION, prepared.payload)
        observed = ledger.settle(purpose, prepared, response(prepared))
        assert observed.result == {'approved': False}
        assert observed.underlying_model_verified is False and observed.usage is None
        with pytest.raises(journal.RouterReviewBlocked, match='already_reserved'):
            ledger.reserve(purpose, prepared)
    assert original_records(case) == before
    assert all(case.client.pttl(k) == -1 for k in KEYS)
    raw = case.client.get(journal.STATE_KEY)
    assert 'synthetic-not-live-key' not in raw and 'whole story' not in raw
    state = json.loads(raw)
    assert state['policy']['historical_extra_cash_micro'] is None
    assert state['policy']['new_cash_allowance_micro'] == 0


@pytest.mark.parametrize('existing_usd', [True, False])
def test_unknown_cash_is_not_initialized_or_overwritten(case, policy, existing_usd):
    if not existing_usd:
        case.client.delete(LEDGER_KEY)
    before = case.client.dump(LEDGER_KEY)
    ledger = store(case)
    ledger.commission(policy)
    ledger.reserve(journal.PURPOSES[0], request())
    assert case.client.dump(LEDGER_KEY) == before


def test_commission_freezes_policy_before_watch_retry(case, policy):
    original = deepcopy(policy)
    def mutate(call):
        if call == 1:
            policy['credential_sha256'] = 'd' * 64
            key = continuity._AUTH_EPOCH
            case.client.set(key, case.client.get(key))  # WATCH conflict, same source value.
    store(case, client=InterceptClient(case.client, before=mutate)).commission(policy)
    assert json.loads(case.client.get(journal.STATE_KEY))['policy'] == original
    store(case).reserve(journal.PURPOSES[0], request())


@pytest.mark.parametrize('field,value', [
    ('historical_extra_cash_micro', 0), ('new_cash_allowance_micro', 1),
    ('new_cash_allowance_micro', False), ('version', True),
    ('model', 'claude-haiku-4-5'), ('endpoint', ENDPOINT + '/'),
    ('valid_until', '2026-09-11T00:00:00Z'), ('valid_from', '2026-09-09T17:00:00Z'),
    ('old_connection_id', NEW), ('continuity_sha256', 'f' * 64),
])
def test_invalid_policy_has_no_writes(case, policy, field, value):
    before = _dump(case.client)
    with pytest.raises(journal.RouterReviewBlocked):
        store(case).commission({**policy, field: value})
    assert _dump(case.client) == before


def test_replay_across_purpose_and_different_key_cannot_get_new_permission(case, policy):
    ledger = store(case)
    ledger.commission(policy)
    ledger.reserve(journal.PURPOSES[0], request())
    before = _dump(case.client)
    for purpose, prepared in [(journal.PURPOSES[0], request('different body')),
                              (journal.PURPOSES[1], request()),
                              (journal.PURPOSES[1], request('different', key='other-key'))]:
        with pytest.raises(journal.RouterReviewBlocked):
            ledger.reserve(purpose, prepared)
    with pytest.raises(journal.RouterReviewBlocked):
        ledger.commission(policy)
    assert _dump(case.client) == before


@pytest.mark.parametrize('prefix', ['request:', 'native_request:'])
def test_existing_cross_mode_request_receipt_blocks(case, policy, prefix):
    ledger, prepared = store(case), request()
    ledger.commission(policy)
    fingerprint = _request_fingerprint({'lineage_id': continuity.ROOT_ID}, 'abacus', OPERATION, prepared.payload)
    field = hashlib.sha256(fingerprint.encode()).hexdigest()
    case.client.hset(LEDGER_KEY, prefix + field, 'existing-unreconciled-history')
    before = _dump(case.client)
    with pytest.raises(journal.RouterReviewBlocked, match='cross_mode'):
        ledger.reserve(journal.PURPOSES[0], prepared)
    assert _dump(case.client) == before


@pytest.mark.parametrize('key', KEYS)
@pytest.mark.parametrize('action', ['delete', 'expire'])
def test_any_missing_or_expiring_record_cannot_reopen_slot(case, policy, key, action):
    ledger = store(case)
    ledger.commission(policy)
    ledger.reserve(journal.PURPOSES[0], request())
    if action == 'delete':
        case.client.delete(key)
    else:
        case.client.expire(key, 1000)
    before = _dump(case.client)
    with pytest.raises(journal.RouterReviewBlocked):
        ledger.reserve(journal.PURPOSES[1], request('different'))
    with pytest.raises(journal.RouterReviewBlocked):
        ledger.commission(policy)
    assert _dump(case.client) == before


def test_joint_state_journal_rollback_detected_by_surviving_anchor(case, policy):
    ledger = store(case)
    ledger.commission(policy)
    initial_state = case.client.get(journal.STATE_KEY)
    initial_journal = case.client.hgetall(journal.JOURNAL_KEY)
    ledger.reserve(journal.PURPOSES[0], request())
    case.client.set(journal.STATE_KEY, initial_state)
    case.client.delete(journal.JOURNAL_KEY)
    case.client.hset(journal.JOURNAL_KEY, mapping=initial_journal)
    with pytest.raises(journal.RouterReviewBlocked, match='replay_evidence'):
        ledger.reserve(journal.PURPOSES[0], request())


def test_concurrent_same_slot_has_one_acknowledged_winner(case, policy):
    store(case).commission(policy)
    barrier = Barrier(2)
    def attempt():
        barrier.wait(timeout=5)
        try:
            return store(case).reserve(journal.PURPOSES[0], request())
        except journal.RouterReviewBlocked:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sum(value is not None for value in results) == 1


@pytest.mark.parametrize('operation', ['commission', 'reserve', 'settle'])
def test_lost_commit_reply_does_not_restore_capacity(case, policy, operation):
    ledger, prepared = store(case), request()
    if operation != 'commission':
        ledger.commission(policy)
    if operation == 'settle':
        ledger.reserve(journal.PURPOSES[0], prepared)
    def lose(*_):
        raise ConnectionError('private injected data must not escape')
    broken = store(case, client=InterceptClient(case.client, after=lose))
    with pytest.raises(journal.RouterReviewBlocked) as error:
        if operation == 'commission':
            broken.commission(policy)
        elif operation == 'reserve':
            broken.reserve(journal.PURPOSES[0], prepared)
        else:
            broken.settle(journal.PURPOSES[0], prepared, response(prepared))
    assert 'private' not in str(error.value)
    state = json.loads(case.client.get(journal.STATE_KEY))
    assert bool(state['slots']) is (operation != 'commission')
    if operation != 'commission':
        with pytest.raises(journal.RouterReviewBlocked, match='already_reserved'):
            ledger.reserve(journal.PURPOSES[0], prepared)
    if operation == 'settle':
        assert state['slots'][journal.PURPOSES[0]]['response'] is not None
        before = _dump(case.client)
        recovered = ledger.settle(journal.PURPOSES[0], prepared, response(prepared))
        assert recovered.result == {'approved': False} and _dump(case.client) == before
        with pytest.raises(journal.RouterReviewBlocked, match='settlement_conflict'):
            ledger.settle(journal.PURPOSES[0], prepared, response(prepared, id='different-provider-reply'))


def test_idempotent_settlement_readback_detects_changed_replay_evidence_at_exec(case, policy):
    ledger, prepared = store(case), request()
    ledger.commission(policy)
    ledger.reserve(journal.PURPOSES[0], prepared)
    ledger.settle(journal.PURPOSES[0], prepared, response(prepared))
    def change(call):
        if call == 1:
            case.client.set(journal.ANCHOR_KEY, 'f' * 64)
    with pytest.raises(journal.RouterReviewBlocked, match='replay_evidence'):
        store(case, client=InterceptClient(case.client, before=change)).settle(
            journal.PURPOSES[0], prepared, response(prepared))


@pytest.mark.parametrize('change', ['oauth', 'cancel', 'queued', 'source', 'profile'])
def test_owner_or_source_change_at_exec_is_rechecked_before_reserve(case, policy, change):
    store(case).commission(policy)
    def mutate(call):
        if call != 1:
            return
        if change == 'oauth':
            case.client.incr(continuity._AUTH_EPOCH)
        elif change == 'cancel':
            case.client.set('youtube_studio:render_cancellation:v1:' + continuity.LEAF_ID, 'cancelled')
        elif change == 'queued':
            case.client.lpush('celery', 'new-work')
        elif change == 'source':
            key = continuity._JOB + continuity.LEAF_ID
            row = json.loads(case.client.get(key))
            row['updated_at'] = '2026-09-09T16:01:00Z'
            case.client.set(key, json.dumps(row))
        else:
            case.client.set(continuity._PROFILE + continuity.CHANNEL_ID, '{}')
    with pytest.raises(journal.RouterReviewBlocked):
        store(case, client=InterceptClient(case.client, before=mutate)).reserve(journal.PURPOSES[0], request())
    assert json.loads(case.client.get(journal.STATE_KEY))['slots'] == {}


def test_expiry_or_owner_cancellation_blocks_new_work_but_preserves_actual_response(case, policy):
    ledger, prepared = store(case), request()
    ledger.commission(policy)
    ledger.reserve(journal.PURPOSES[0], prepared)
    case.client.set('youtube_studio:render_cancellation:v1:' + continuity.LEAF_ID, 'cancelled')
    later = store(case, NOW + timedelta(days=1))
    observed = later.settle(journal.PURPOSES[0], prepared, response(prepared))
    assert observed.result == {'approved': False}
    with pytest.raises(journal.RouterReviewBlocked):
        later.reserve(journal.PURPOSES[1], request('new'))


def test_invalid_or_mismatched_response_leaves_unknown_slot_reserved(case, policy):
    ledger, prepared = store(case), request()
    ledger.commission(policy)
    ledger.reserve(journal.PURPOSES[0], prepared)
    before = _dump(case.client)
    for invalid in (response(prepared, choices=[]), response(request('different'))):
        with pytest.raises(journal.RouterReviewBlocked):
            ledger.settle(journal.PURPOSES[0], prepared, invalid)
    assert _dump(case.client) == before
    with pytest.raises(journal.RouterReviewBlocked):
        ledger.reserve(journal.PURPOSES[0], prepared)


RECONFIRM_NOW = NOW + timedelta(days=1)


def next_policy(policy):
    start = datetime.strptime(policy['valid_until'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    return {**policy, 'valid_from': start.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'valid_until': (start + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'entitlement_evidence_sha256': hashlib.sha256(str(start).encode()).hexdigest()}


def state_hash(state):
    raw = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def read_state(case):
    return json.loads(case.client.get(journal.STATE_KEY))


def write_empty_state_with_matching_replay_evidence(case, state):
    """Corrupt stored structure without letting outer checksum mismatch mask it."""
    assert state['slots'] == {}
    case.client.set(journal.STATE_KEY, json.dumps(state))
    case.client.delete(journal.JOURNAL_KEY)
    case.client.hset(journal.JOURNAL_KEY, mapping={
        'policy_sha256': state_hash(state['policy']), 'state_sha256': state_hash(state)})
    case.client.set(journal.ANCHOR_KEY, state_hash(state))


@pytest.mark.parametrize('existing_cash_history', [True, False])
def test_expired_unused_reconfirmation_preserves_history_and_exact_two_review_purposes(
        case, policy, existing_cash_history):
    if not existing_cash_history:
        case.client.delete(LEDGER_KEY)
    before = original_records(case)
    store(case).commission(policy)
    original = read_state(case)
    renewed = next_policy(policy)
    ledger = store(case, RECONFIRM_NOW)
    before_reconfirmation = _dump(case.client)
    with pytest.raises(journal.RouterReviewBlocked):
        ledger.reserve(journal.PURPOSES[0], request())
    with pytest.raises(journal.RouterReviewBlocked):
        ledger.commission(renewed)
    assert _dump(case.client) == before_reconfirmation

    receipt = ledger.reconfirm_unused(renewed)
    assert receipt['qa_approved'] is receipt['publish_eligible'] is False
    state = read_state(case)
    assert state == {'policy': renewed, 'slots': {}, 'updated_at': '2026-09-10T16:00:00Z',
        'history': [{'previous_state': original, 'previous_state_sha256': state_hash(original),
                     'reconfirmed_at': '2026-09-10T16:00:00Z'}]}
    with pytest.raises(journal.RouterReviewBlocked):
        ledger.reserve('extra_review', request())
    for purpose, text in zip(journal.PURPOSES, ('whole original story', 'all original frames')):
        prepared = request(text)
        reserved = ledger.reserve(purpose, prepared)
        assert reserved['policy_sha256'] == state_hash(renewed)
        assert reserved['root_request_fingerprint'] == _request_fingerprint(
            {'lineage_id': continuity.ROOT_ID}, 'abacus', OPERATION, prepared.payload)
        assert ledger.settle(purpose, prepared, response(prepared)).result == {'approved': False}
        with pytest.raises(journal.RouterReviewBlocked):
            ledger.reserve(purpose, request('new content cannot reopen this purpose'))
    assert set(read_state(case)['slots']) == set(journal.PURPOSES)
    assert read_state(case)['history'] == state['history']
    assert original_records(case) == before
    assert all(case.client.pttl(key) == -1 for key in KEYS)


def test_second_expired_unused_reconfirmation_hashes_full_prior_history(case, policy):
    store(case).commission(policy)
    original = read_state(case)
    first_policy = next_policy(policy)
    store(case, RECONFIRM_NOW).reconfirm_unused(first_policy)
    first = read_state(case)
    second_policy = next_policy(first_policy)
    second_ledger = store(case, RECONFIRM_NOW + timedelta(days=1))
    second_ledger.reconfirm_unused(second_policy)
    second = read_state(case)
    assert second['history'][:1] == first['history']
    assert second['history'][0]['previous_state'] == original
    assert second['history'][1] == {
        'previous_state': {key: first[key] for key in ('policy', 'slots', 'updated_at')},
        'previous_state_sha256': state_hash(first), 'reconfirmed_at': '2026-09-11T16:00:00Z'}
    assert second['policy'] == second_policy and second['slots'] == {}
    second_ledger.reserve(journal.PURPOSES[0], request())
    assert read_state(case)['history'] == second['history']


@pytest.mark.parametrize('now', [NOW, datetime(2026, 9, 9, 23, 59, 59, tzinfo=timezone.utc)])
def test_unused_policy_cannot_be_reconfirmed_before_expiry(case, policy, now):
    store(case).commission(policy)
    before = _dump(case.client)
    with pytest.raises(journal.RouterReviewBlocked):
        store(case, now).reconfirm_unused(next_policy(policy))
    assert _dump(case.client) == before


def test_reconfirmation_accepts_exact_expiry_and_new_window_start(case, policy):
    store(case).commission(policy)
    boundary = datetime(2026, 9, 10, tzinfo=timezone.utc)
    ledger = store(case, boundary)
    ledger.reconfirm_unused(next_policy(policy))
    assert read_state(case)['history'][0]['reconfirmed_at'] == '2026-09-10T00:00:00Z'
    ledger.reserve(journal.PURPOSES[0], request())


@pytest.mark.parametrize('observed', [False, True])
def test_any_reserved_or_observed_review_permanently_prevents_unused_reconfirmation(case, policy, observed):
    ledger, prepared = store(case), request()
    ledger.commission(policy)
    ledger.reserve(journal.PURPOSES[0], prepared)
    if observed:
        ledger.settle(journal.PURPOSES[0], prepared, response(prepared))
    before = _dump(case.client)
    with pytest.raises(journal.RouterReviewBlocked):
        store(case, RECONFIRM_NOW).reconfirm_unused(next_policy(policy))
    assert _dump(case.client) == before


@pytest.mark.parametrize('field,value', [
    ('version', True), ('kind', 'new_subscription_review'),
    ('endpoint', ENDPOINT + '/'), ('model', 'claude-haiku-4-5'),
    ('original_task_id', continuity.LEAF_ID), ('leaf_task_id', continuity.ROOT_ID),
    ('channel_id', 'other_channel_0001'), ('profile_revision', 'other_profile_revision'),
    ('old_connection_id', 'other_old_connection'), ('current_connection_id', 'other_current_connection'),
    ('continuity_sha256', 'a' * 64), ('credential_sha256', 'b' * 64),
    ('entitlement_source', 'unverified_owner_assertion'),
    ('historical_extra_cash_micro', 0), ('new_cash_allowance_micro', 1),
])
def test_reconfirmation_cannot_change_original_policy_bindings(case, policy, field, value):
    store(case).commission(policy)
    before = _dump(case.client)
    with pytest.raises(journal.RouterReviewBlocked):
        store(case, RECONFIRM_NOW).reconfirm_unused({**next_policy(policy), field: value})
    assert _dump(case.client) == before


@pytest.mark.parametrize('changes', [
    {'valid_from': '2026-09-09T23:59:59Z', 'valid_until': '2026-09-10T23:00:00Z'},
    {'valid_until': '2026-09-11T00:00:01Z'},
    {'valid_from': '2026-09-10T17:00:00Z'},
    {'valid_until': '2026-09-10T16:00:00Z'},
    {'valid_from': '2026-09-10T00:00:00+00:00'},
    {'entitlement_evidence_sha256': 'not-evidence'},
])
def test_reconfirmation_requires_fresh_bounded_nonoverlapping_window_and_evidence(case, policy, changes):
    store(case).commission(policy)
    before = _dump(case.client)
    with pytest.raises(journal.RouterReviewBlocked):
        store(case, RECONFIRM_NOW).reconfirm_unused({**next_policy(policy), **changes})
    assert _dump(case.client) == before


def test_reconfirmation_freezes_policy_before_watch_retry(case, policy):
    store(case).commission(policy)
    renewed = next_policy(policy)
    expected = deepcopy(renewed)
    def mutate(call):
        if call == 1:
            renewed['entitlement_evidence_sha256'] = 'd' * 64
            case.client.set(continuity._AUTH_EPOCH, case.client.get(continuity._AUTH_EPOCH))
    client = InterceptClient(case.client, before=mutate)
    store(case, RECONFIRM_NOW, client=client).reconfirm_unused(renewed)
    assert client.calls == 2
    assert read_state(case)['policy'] == expected
    assert len(read_state(case)['history']) == 1


@pytest.mark.parametrize('change', ['oauth', 'source'])
def test_reconfirmation_rechecks_source_and_oauth_after_watch_conflict(case, policy, change):
    store(case).commission(policy)
    original = {key: case.client.dump(key) for key in KEYS}
    def mutate(call):
        if call != 1:
            return
        if change == 'oauth':
            case.client.incr(continuity._AUTH_EPOCH)
        else:
            key = continuity._JOB + continuity.LEAF_ID
            row = json.loads(case.client.get(key))
            row['updated_at'] = '2026-09-10T15:59:00Z'
            case.client.set(key, json.dumps(row))
    client = InterceptClient(case.client, before=mutate)
    with pytest.raises(journal.RouterReviewBlocked):
        store(case, RECONFIRM_NOW, client=client).reconfirm_unused(next_policy(policy))
    assert client.calls == 1
    assert {key: case.client.dump(key) for key in KEYS} == original


def test_concurrent_unused_reconfirmation_has_one_acknowledged_winner(case, policy):
    store(case).commission(policy)
    renewed = next_policy(policy)
    barrier = Barrier(2)
    def attempt():
        def synchronize(call):
            if call == 1:
                barrier.wait(timeout=5)
        client = InterceptClient(case.client, before=synchronize)
        try:
            return store(case, RECONFIRM_NOW, client=client).reconfirm_unused(renewed)
        except journal.RouterReviewBlocked:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sum(result is not None for result in results) == 1
    state = read_state(case)
    assert state['policy'] == renewed and len(state['history']) == 1 and state['slots'] == {}


@pytest.mark.parametrize('disconnect_error', [ConnectionError, WatchError])
def test_lost_reconfirmation_ack_cannot_duplicate_history_or_retry_the_active_window(
        case, policy, disconnect_error):
    store(case).commission(policy)
    renewed = next_policy(policy)
    def lose(*_):
        raise disconnect_error('private injected data must not escape')
    client = InterceptClient(case.client, after=lose)
    with pytest.raises(journal.RouterReviewBlocked) as error:
        store(case, RECONFIRM_NOW, client=client).reconfirm_unused(renewed)
    assert 'private' not in str(error.value)
    assert client.calls == 1 and len(read_state(case)['history']) == 1
    before = _dump(case.client)
    with pytest.raises(journal.RouterReviewBlocked):
        store(case, RECONFIRM_NOW).reconfirm_unused(renewed)
    assert _dump(case.client) == before
    store(case, RECONFIRM_NOW).reserve(journal.PURPOSES[0], request())


@pytest.mark.parametrize('key', KEYS)
@pytest.mark.parametrize('action', ['delete', 'expire'])
def test_unused_reconfirmation_never_repairs_partial_or_expiring_evidence(case, policy, key, action):
    store(case).commission(policy)
    if action == 'delete':
        case.client.delete(key)
    else:
        case.client.expire(key, 1000)
    before = _dump(case.client)
    with pytest.raises(journal.RouterReviewBlocked):
        store(case, RECONFIRM_NOW).reconfirm_unused(next_policy(policy))
    assert _dump(case.client) == before
    if action == 'delete':
        assert case.client.pttl(key) == -2
    else:
        assert case.client.pttl(key) > 0


def test_unused_reconfirmation_cannot_initialize_an_absent_journal(case, policy):
    before = _dump(case.client)
    with pytest.raises(journal.RouterReviewBlocked):
        store(case, RECONFIRM_NOW).reconfirm_unused(next_policy(policy))
    assert _dump(case.client) == before


@pytest.mark.parametrize('corruption', [
    'empty_history', 'oversized_history', 'history_object', 'extra_entry_field', 'extra_snapshot_field',
    'used_snapshot', 'wrong_previous_hash', 'invalid_date', 'expired_snapshot_stamp',
    'reconfirmation_before_expiry', 'intermediate_stamp_drift', 'current_stamp_drift',
    'changed_snapshot_binding', 'overlapping_window', 'core_only_previous_hash', 'reversed_chain',
])
def test_reconfirmation_history_rejects_malformed_structure_even_with_matching_replay_hashes(
        case, policy, corruption):
    store(case).commission(policy)
    first_policy = next_policy(policy)
    store(case, RECONFIRM_NOW).reconfirm_unused(first_policy)
    second_policy = next_policy(first_policy)
    now = RECONFIRM_NOW + timedelta(days=1)
    store(case, now).reconfirm_unused(second_policy)
    state = read_state(case)
    first, second = state['history']
    if corruption == 'empty_history':
        state['history'] = []
    elif corruption == 'oversized_history':
        state['history'] = [deepcopy(first) for _ in range(13)]
    elif corruption == 'history_object':
        state['history'] = {}
    elif corruption == 'extra_entry_field':
        first['untracked'] = True
    elif corruption == 'extra_snapshot_field':
        first['previous_state']['history'] = []
    elif corruption == 'used_snapshot':
        first['previous_state']['slots'] = {journal.PURPOSES[0]: {}}
    elif corruption == 'wrong_previous_hash':
        first['previous_state_sha256'] = '0' * 64
    elif corruption == 'invalid_date':
        first['reconfirmed_at'] = '2026-09-10T16:00:00+00:00'
    elif corruption == 'expired_snapshot_stamp':
        first['previous_state']['updated_at'] = policy['valid_until']
    elif corruption == 'reconfirmation_before_expiry':
        first['reconfirmed_at'] = '2026-09-09T23:59:59Z'
    elif corruption == 'intermediate_stamp_drift':
        second['previous_state']['updated_at'] = '2026-09-10T16:00:01Z'
    elif corruption == 'current_stamp_drift':
        state['updated_at'] = '2026-09-11T16:00:01Z'
    elif corruption == 'changed_snapshot_binding':
        first['previous_state']['policy']['credential_sha256'] = 'b' * 64
    elif corruption == 'overlapping_window':
        state['policy']['valid_from'] = '2026-09-10T23:59:59Z'
        state['policy']['valid_until'] = '2026-09-11T23:59:59Z'
    elif corruption == 'core_only_previous_hash':
        second['previous_state_sha256'] = state_hash(second['previous_state'])
    else:
        state['history'].reverse()
    write_empty_state_with_matching_replay_evidence(case, state)
    before = _dump(case.client)
    with pytest.raises(journal.RouterReviewBlocked):
        store(case, now).reserve(journal.PURPOSES[0], request())
    with pytest.raises(journal.RouterReviewBlocked):
        store(case, now + timedelta(days=1)).reconfirm_unused(next_policy(state['policy']))
    assert _dump(case.client) == before


def test_unused_reconfirmation_history_limit_preserves_all_twelve_prior_states(case, policy):
    store(case).commission(policy)
    current_policy = policy
    for index in range(12):
        previous = read_state(case)
        current_policy = next_policy(current_policy)
        now = RECONFIRM_NOW + timedelta(days=index)
        store(case, now).reconfirm_unused(current_policy)
        state = read_state(case)
        assert len(state['history']) == index + 1
        assert state['history'][-1]['previous_state_sha256'] == state_hash(previous)
        assert state['history'][:-1] == previous.get('history', [])
    before = _dump(case.client)
    with pytest.raises(journal.RouterReviewBlocked):
        store(case, RECONFIRM_NOW + timedelta(days=12)).reconfirm_unused(next_policy(current_policy))
    assert _dump(case.client) == before
    store(case, now).reserve(journal.PURPOSES[0], request())


@pytest.mark.parametrize('restored_keys', [
    (journal.STATE_KEY,), (journal.JOURNAL_KEY,), (journal.ANCHOR_KEY,),
    (journal.STATE_KEY, journal.JOURNAL_KEY),
    (journal.STATE_KEY, journal.ANCHOR_KEY),
    (journal.JOURNAL_KEY, journal.ANCHOR_KEY),
])
def test_reconfirmation_detects_rollback_when_any_current_replay_record_survives(case, policy, restored_keys):
    store(case).commission(policy)
    original = {key: case.client.dump(key) for key in KEYS}
    renewed = next_policy(policy)
    store(case, RECONFIRM_NOW).reconfirm_unused(renewed)
    for key in restored_keys:
        case.client.restore(key, 0, original[key], replace=True)
    before = _dump(case.client)
    with pytest.raises(journal.RouterReviewBlocked):
        store(case, RECONFIRM_NOW).reserve(journal.PURPOSES[0], request())
    with pytest.raises(journal.RouterReviewBlocked):
        store(case, RECONFIRM_NOW + timedelta(days=1)).reconfirm_unused(next_policy(renewed))
    assert _dump(case.client) == before


def test_reconfirmation_never_adopts_changed_previous_state_during_watch_retry(case, policy):
    store(case).commission(policy)
    changed = read_state(case)
    changed['updated_at'] = '2026-09-09T16:00:01Z'
    def mutate(call):
        if call == 1:
            write_empty_state_with_matching_replay_evidence(case, changed)
    client = InterceptClient(case.client, before=mutate)
    with pytest.raises(journal.RouterReviewBlocked):
        store(case, RECONFIRM_NOW, client=client).reconfirm_unused(next_policy(policy))
    assert client.calls == 1
    assert read_state(case) == changed


def test_history_does_not_allow_a_reservation_backdated_before_reconfirmation(case, policy):
    store(case).commission(policy)
    ledger = store(case, RECONFIRM_NOW)
    ledger.reconfirm_unused(next_policy(policy))
    prepared = request()
    ledger.reserve(journal.PURPOSES[0], prepared)
    state = read_state(case)
    state['slots'][journal.PURPOSES[0]]['reserved_at'] = '2026-09-10T15:59:59Z'
    case.client.set(journal.STATE_KEY, json.dumps(state))
    case.client.hset(journal.JOURNAL_KEY, mapping=journal._journal(state))
    case.client.set(journal.ANCHOR_KEY, state_hash(state))
    before = _dump(case.client)
    with pytest.raises(journal.RouterReviewBlocked):
        ledger.settle(journal.PURPOSES[0], prepared, response(prepared))
    with pytest.raises(journal.RouterReviewBlocked):
        ledger.reserve(journal.PURPOSES[1], request('other review'))
    assert _dump(case.client) == before
