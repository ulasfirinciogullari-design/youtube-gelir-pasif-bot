from copy import deepcopy
import ast
import json
from pathlib import Path
import re

import pytest

from app.services import pre_media_editorial_retry as retry
from app.services import production_credit_ledger as voice, production_included_router as included
from app.services.production_spend import LEDGER_KEY
from test_native_story_correction import native, case, policy, SOURCE, CHILD, TOKEN, _write, _all
from test_production_credit_ledger import intent, binding, InterceptClient


@pytest.fixture
def pending(native):
    n = native; j = retry.jobs
    source = {'task_id': SOURCE, 'spec': deepcopy(n.case.spec), 'kind': 'render',
        'state': 'FAILURE', 'failure_stage': 'research', 'parent_id': None}
    _write(n.client, j.JOB_PREFIX + SOURCE, source)
    n.client.delete(j.REPAIR_CHECKPOINT_PREFIX + SOURCE)
    assert n.case.state.claim_retry_dispatch(SOURCE, CHILD, TOKEN, allow_repair=False)['claimed']
    assert n.case.state.acquire_retry_child_execution(CHILD, SOURCE)
    child = {'task_id': CHILD, 'spec': deepcopy(n.case.spec), 'kind': 'render',
        'state': 'PROGRESS', 'parent_id': SOURCE}
    _write(n.client, j.JOB_PREFIX + CHILD, child)
    n.client.hset(j.PAID_CREATE_BUDGET_PREFIX + CHILD, mapping={'cap': '6', 'used': '0'})
    return n


def run(n):
    return retry.eligible(CHILD, SOURCE, n.case.spec)


def test_real_claim_before_media_preserves_every_ledger_and_unknown_request(pending):
    n = pending; before = _all(n.client)
    assert run(n) is True
    assert _all(n.client) == before
    assert len(json.loads(n.client.get(included.JOURNAL_KEY))['requests']) == 1
    assert n.foundation.snapshot()['cash_spending_enabled'] is False
    # Actual worker gate reaches the helper without inferring a full rebuild.
    tree = ast.parse((Path(__file__).parents[1] / 'app/tasks.py').read_text())
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
        and node.name == '_fresh_scheduled_short_shots')
    ns = {'_RECOVERED_MEDIA_SOURCE_PATTERN': re.compile(retry._TASK.pattern)}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), '<worker-fresh-planning>', 'exec'), ns)
    assert ns[fn.name](CHILD, n.case.spec, approved_package=None, retry_dispatch_source_id=SOURCE,
        curated_stock_manifest=None, voice_replacement_source_id=None, paid_slots_used=0) is True
    assert _all(n.client) == before


@pytest.mark.parametrize('damage', [
    'voice_pointer', 'checkpoint_error', 'generated_media', 'render_result', 'voice_reuse',
    'late_failure', 'wrong_child', 'wrong_parent', 'changed_topic', 'changed_oauth', 'cancelled',
    'private_checkpoint', 'used_slot', 'missing_budget', 'missing_execution', 'wrong_execution',
    'claim_token', 'repair_mode', 'child_completed', 'missing_root_binding', 'wrong_child_binding',
    'missing_credit_journal', 'expired_credit_journal', 'pending_native_voice',
])
def test_every_existing_or_uncertain_media_or_identity_state_keeps_legacy_path_without_writes(pending, damage):
    n = pending; j = retry.jobs
    source = json.loads(n.client.get(j.JOB_PREFIX + SOURCE))
    child = json.loads(n.client.get(j.JOB_PREFIX + CHILD))
    fields = {'voice_pointer': 'audio_candidate_checkpoint', 'checkpoint_error': 'audio_candidate_checkpoint_error',
              'generated_media': 'generated_asset_candidates', 'render_result': 'result', 'voice_reuse': 'voice_candidate_reuse'}
    if damage in fields:source[fields[damage]] = {}
    elif damage == 'late_failure':source['failure_stage'] = 'audio_qc'
    elif damage == 'wrong_child':source['retry_child_task_id'] = SOURCE
    elif damage == 'wrong_parent':child['parent_id'] = CHILD
    elif damage == 'changed_topic':source['spec']['topic'] = 'Another story'
    elif damage == 'changed_oauth':
        channel_key = retry.runtime._CHANNEL_PREFIX + n.context['channel_id']
        channel = json.loads(n.client.get(channel_key)); channel['connection_id'] = 'different'
        _write(n.client, channel_key, channel)
    elif damage == 'cancelled':n.client.set(j.RENDER_CANCELLATION_PREFIX + SOURCE, '1')
    elif damage == 'private_checkpoint':n.client.set(j.REPAIR_CHECKPOINT_PREFIX + SOURCE, 'private')
    elif damage == 'used_slot':n.client.hset(j.PAID_CREATE_BUDGET_PREFIX + SOURCE, 'used', '1')
    elif damage == 'missing_budget':n.client.delete(j.PAID_CREATE_BUDGET_PREFIX + SOURCE)
    elif damage == 'missing_execution':n.client.delete(j.RETRY_CHILD_EXECUTION_PREFIX + CHILD)
    elif damage == 'wrong_execution':n.client.set(j.RETRY_CHILD_EXECUTION_PREFIX + CHILD, 'different')
    elif damage == 'claim_token':n.client.hset(j.RETRY_CHILD_CLAIM_PREFIX + CHILD, 'token', 'different')
    elif damage == 'repair_mode':n.client.hset(j.RETRY_DISPATCH_PREFIX + SOURCE, 'mode', 'repair')
    elif damage == 'child_completed':child['state'] = 'SUCCESS'
    elif damage == 'missing_root_binding':n.client.hdel(LEDGER_KEY, 'binding:' + SOURCE)
    elif damage == 'wrong_child_binding':n.client.hset(LEDGER_KEY, 'binding:' + CHILD, '{}')
    elif damage == 'missing_credit_journal':n.client.delete(voice.JOURNAL_KEY)
    elif damage == 'expired_credit_journal':n.client.expire(voice.JOURNAL_KEY, 300)
    elif damage == 'pending_native_voice':
        p = json.loads(n.client.hget(voice.STATE_KEY, 'policy'))
        ledger = voice.CreditLedger(n.client, foundation=n.foundation, clock=n.foundation.clock)
        ledger.reserve(intent=intent(root_lineage_id=SOURCE, channel_id=n.context['channel_id'],
            source_connection_id=n.context['connection_id']), **binding(p), production_context=n.context)
    _write(n.client, j.JOB_PREFIX + SOURCE, source); _write(n.client, j.JOB_PREFIX + CHILD, child)
    before = _all(n.client)
    assert run(n) is False
    assert _all(n.client) == before


def test_changed_snapshot_cannot_enable_planning(pending, monkeypatch):
    n = pending
    def race(number):
        n.client.hset(retry.jobs.PAID_CREATE_BUDGET_PREFIX + SOURCE, 'used', '1')
    intercepted = InterceptClient(n.client, before=race)
    # This client delegates ordinary attributes as a real wrapped connection.
    intercepted.connection_pool = n.client.connection_pool
    monkeypatch.setattr(n.foundation, 'client', intercepted)
    assert run(n) is False
    assert intercepted.calls == 1
    assert n.client.hget(retry.jobs.PAID_CREATE_BUDGET_PREFIX + SOURCE, 'used') == '1'


def test_archived_paid_voice_still_blocks_pre_media_replanning(pending):
    from app.services import production_credit_periods as periods
    from test_production_credit_ledger import observation
    n = pending
    ledger = voice.CreditLedger(n.client, foundation=n.foundation, clock=n.foundation.clock)
    p = periods.renewal_snapshot(ledger)['policy']
    receipt = ledger.reserve(intent=intent(root_lineage_id=SOURCE, channel_id=n.context['channel_id'],
        source_connection_id=n.context['connection_id']), **binding(p), production_context=n.context)
    ledger.settle(observation=observation(p, receipt), **binding(p))
    before = periods.renewal_snapshot(ledger)
    account = {'version': 1, 'source': 'verified_GET_v1_user', 'status': 'active',
        **{key: p[key] for key in ('account_sha256', 'credential_sha256')},
        'observed_at': periods._stamp(n.foundation.clock()), 'response_sha256': 'a' * 64,
        'provider_reset_at': p['balance']['provider_reset_at'], 'quota_credits': p['balance']['quota_credits'],
        'used_credits': p['balance']['used_credits'] + 248,
        'max_credit_limit_extension': 0, 'can_extend_character_limit': False}
    periods.reallocate_existing_balance(ledger, account, authorization_sha256='b' * 64,
        withheld_credits=100, allocation_cap_credits=p['allocation_credits'],
        expected_policy_sha256=before['policy_sha256'], expected_state_sha256=before['state_sha256'])
    assert ledger.summary()['intent_count'] == 0
    original = _all(n.client)
    assert run(n) is False
    assert _all(n.client) == original


def test_a_second_pre_media_child_keeps_the_original_root_and_checks_every_ancestor(pending):
    n = pending; j = retry.jobs
    grandchild = '33333333-3333-4333-8333-333333333333'
    child = json.loads(n.client.get(j.JOB_PREFIX + CHILD))
    child.update(state='FAILURE', failure_stage='director_qc')
    _write(n.client, j.JOB_PREFIX + CHILD, child)
    assert n.case.state.claim_retry_dispatch(CHILD, grandchild, TOKEN + '-next', allow_repair=False)['claimed']
    assert n.case.state.acquire_retry_child_execution(grandchild, CHILD)
    _write(n.client, j.JOB_PREFIX + grandchild, {'task_id': grandchild, 'parent_id': CHILD,
        'kind': 'render', 'state': 'PROGRESS', 'spec': deepcopy(n.case.spec)})
    n.client.hset(j.PAID_CREATE_BUDGET_PREFIX + grandchild, mapping={'cap': '6', 'used': '0'})
    before = _all(n.client)
    assert retry.eligible(grandchild, CHILD, n.case.spec) is True
    assert _all(n.client) == before
    root = json.loads(n.client.get(j.JOB_PREFIX + SOURCE)); root['audio_candidate_checkpoint'] = {}
    _write(n.client, j.JOB_PREFIX + SOURCE, root)
    before = _all(n.client)
    assert retry.eligible(grandchild, CHILD, n.case.spec) is False
    assert _all(n.client) == before
