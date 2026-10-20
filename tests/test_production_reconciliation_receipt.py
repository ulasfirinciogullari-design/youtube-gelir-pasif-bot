"""Actual separate release proof + scheduler CAS; no provider or external store."""
from concurrent.futures import ThreadPoolExecutor
import json
from unittest.mock import Mock

import pytest

from test_production_recovery_receipt import recovery, case, real_case, EPOCH_KEY
from test_production_recovery import CHANNEL, _snapshot
from test_production_reconciliation import reconciliation, _tick_namespace


@pytest.fixture
def blocked(real_case):
    source, keys = real_case
    module, client, data, _, _ = source
    data.profile = json.loads(client.get(module.PROFILE_PREFIX + CHANNEL))
    result = reconciliation.__wrapped__((module, client, data))
    result.keys = keys
    result.blocked_proof = Mock(wraps=module.resume_after_blocked_public_retry)
    result.ns['resume_after_blocked_public_retry'] = result.blocked_proof
    return result


def run(c, now=100000):
    return c.ns['reconcile_public_retry_deliveries']([c.data.profile], now=now)


def test_separate_real_public_receipt_resumes_without_rewriting_blocked_history(blocked):
    c = blocked
    before = _snapshot(c.client)
    assert run(c)['resumed_count'] == 1
    c.proof.assert_not_called()
    c.blocked_proof.assert_called_once_with(CHANNEL, c.data.original_id, c.data.recovered_id,
        c.data.profile['profile_revision'], now=100000, continue_immediately=True)
    after = _snapshot(c.client)
    audit = json.loads(after.pop(c.data.audit_key))
    before[c.data.state_key].pop('paused_reason')
    before[c.data.state_key]['next_due'] = '100000'
    assert before == after
    assert audit['publication_proof'] == 'blocked_public_recovery' and audit['cursor'] == 1
    assert c.client.ttl(c.data.audit_key) == -1
    assert json.loads(c.client.get(c.keys['source']))['result']['youtube']['release_status'] == 'blocked'


def test_actual_tick_starts_only_next_frozen_episode_after_separate_receipt(blocked):
    c = blocked
    original = {key: c.client.get(key) for key in c.keys.values()}
    namespace, enqueue = _tick_namespace(c)
    result = namespace['production_tick']()
    assert result['status'] == 'queued' and result['public_retry_reconciliation']['resumed_count'] == 1
    child = json.loads(c.client.get(c.recovery.JOB_PREFIX + result['task_id']))
    assert child['spec']['production_topic_index'] == 1
    assert child['spec']['topic'].startswith(c.data.profile['production_topics'][1])
    assert c.client.hget(c.data.state_key, 'cursor') == '2'
    assert {key: c.client.get(key) for key in original} == original
    assert namespace['production_tick']()['status'] == 'active'
    assert enqueue.call_count == 1


@pytest.mark.parametrize('damage', ['missing', 'malformed', 'oversized', 'status', 'channel', 'source',
                                    'revision', 'proof', 'caption', 'assets', 'epoch', 'credential'])
def test_missing_changed_or_uncertain_receipt_never_clears_pause(blocked, damage):
    c = blocked
    if damage == 'missing': c.client.delete(c.data.receipt_key)
    elif damage == 'malformed': c.client.set(c.data.receipt_key, 'not-json')
    elif damage == 'oversized': c.client.set(c.data.receipt_key, 'x' * 2_000_001)
    elif damage == 'assets': c.client.delete(c.data.asset_key)
    elif damage == 'epoch': c.client.set(EPOCH_KEY, '8')
    elif damage == 'credential': c.client.set(c.recovery.OAUTH_CREDENTIAL_PREFIX + CHANNEL, 'changed-fixture')
    else:
        row = json.loads(c.client.get(c.data.receipt_key))
        if damage == 'status': row['status'] = 'uncertain'
        elif damage == 'channel': row['target_channel_id'] = 'UC_other_channel'
        elif damage == 'source': row['source_task_id'] = c.data.original_id
        elif damage == 'revision': row['profile_revision'] = 'different-revision'
        elif damage == 'proof': row['public_proof']['privacy_status'] = 'private'
        elif damage == 'caption': row['caption_proof']['status'] = 'syncing'
        c.client.set(c.data.receipt_key, json.dumps(row))
    before = _snapshot(c.client)
    assert run(c)['resumed_count'] == 0
    assert _snapshot(c.client) == before
    assert c.client.hget(c.data.state_key, 'paused_reason') == 'previous_render_failed'


@pytest.mark.parametrize('ancestor', [True, False])
@pytest.mark.parametrize('kind', ['RENDER_CANCELLATION_PREFIX', 'HOLD_PREFIX'])
def test_owner_fence_on_any_retry_node_blocks_automatic_continuation(blocked, ancestor, kind):
    c = blocked
    task_id = c.data.original_id if ancestor else c.data.recovered_id
    c.client.set(getattr(c.recovery, kind) + task_id, '{}')
    before = _snapshot(c.client)
    assert run(c)['channels'][CHANNEL] == 'public_retry_not_verified'
    assert _snapshot(c.client) == before


@pytest.mark.parametrize('target', ['cancel', 'hold', 'receipt', 'profile', 'epoch'])
def test_new_owner_fence_or_proof_change_before_commit_is_atomic(blocked, monkeypatch, target):
    c = blocked
    execute = c.client.eval
    def race(script, *args):
        if script == c.recovery._RESUME:
            if target == 'cancel': c.client.set(c.recovery.RENDER_CANCELLATION_PREFIX + c.data.original_id, '{}')
            elif target == 'hold': c.client.set(c.recovery.HOLD_PREFIX + c.data.recovered_id, '{}')
            elif target == 'epoch': c.client.set(EPOCH_KEY, '8')
            else:
                key = c.data.receipt_key if target == 'receipt' else c.keys['profile']
                row = json.loads(c.client.get(key))
                if target == 'receipt': row['status'] = 'uncertain'
                else: row['production_enabled'] = False
                c.client.set(key, json.dumps(row))
        return execute(script, *args)
    monkeypatch.setattr(c.client, 'eval', race)
    assert run(c)['resumed_count'] == 0
    assert not c.client.exists(c.data.audit_key)
    assert c.client.hget(c.data.state_key, 'paused_reason') == 'previous_render_failed'
    assert c.client.hget(c.data.state_key, 'cursor') == '1'


@pytest.mark.parametrize('gate', ['active', 'owner_pause', 'disabled'])
def test_existing_active_and_owner_gates_remain(blocked, gate):
    c = blocked
    if gate == 'active': c.client.set(c.recovery.ACTIVE_KEY, 'live-other-channel')
    elif gate == 'owner_pause': c.client.hset(c.data.state_key, 'paused_reason', 'owner_hold')
    else: c.data.profile['production_enabled'] = False
    before = _snapshot(c.client)
    assert run(c)['resumed_count'] == 0
    assert _snapshot(c.client) == before
    c.blocked_proof.assert_not_called()


def test_concurrent_discovery_has_one_durable_resume_and_no_due_shift(blocked):
    c = blocked
    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(lambda _: run(c), range(8)))
    assert sum(row['resumed_count'] for row in results) == 1
    before = _snapshot(c.client)
    assert run(c, now=200000)['resumed_count'] == 0
    assert _snapshot(c.client) == before


def test_lost_resume_reply_still_allows_exactly_one_next_dispatch(blocked, monkeypatch):
    c = blocked
    execute = c.client.eval
    def lose(script, *args):
        result = execute(script, *args)
        if script == c.recovery._RESUME: raise ConnectionError('fixture-lost-reply')
        return result
    monkeypatch.setattr(c.client, 'eval', lose)
    namespace, enqueue = _tick_namespace(c)
    assert namespace['production_tick']()['status'] == 'queued'
    audit = c.client.get(c.data.audit_key)
    assert audit is not None
    assert namespace['production_tick']()['status'] == 'active'
    assert c.client.get(c.data.audit_key) == audit and enqueue.call_count == 1
