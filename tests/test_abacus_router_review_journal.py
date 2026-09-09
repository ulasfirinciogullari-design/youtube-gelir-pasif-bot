"""Offline original-lineage review admission with real Redis WATCH semantics."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from threading import Barrier

import httpx
import pytest
from redis.exceptions import ConnectionError

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
