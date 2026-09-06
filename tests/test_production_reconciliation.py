"""Automatic discovery delegates to real public-proof Lua, never providers."""
import ast
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest

from test_production_recovery import (
    ROOT, CHANNEL, REVISION, _change, _snapshot, _write, recovery as private_recovery,
)
from test_production_recovery_public import case as public_case, _public_seed


@pytest.fixture
def reconciliation(public_case):
    recovery, client, data = public_case
    path = ROOT / 'app/services/production_reconciliation.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    tree.body = [node for node in tree.body if not (
        isinstance(node, ast.ImportFrom) and (node.module or '').startswith('app.')
    )]
    proof = Mock(wraps=recovery.resume_after_public_retry)
    namespace = {name: getattr(recovery, name) for name in (
        'ACTIVE_KEY', 'CHANNEL_STATE_PREFIX', 'JOB_PREFIX', 'MAX_RETRY_HOPS',
        'ProductionRecoveryError', '_ID', '_TASK_ID',
    )}
    auth = ast.parse((ROOT / 'app/services/youtube_auth.py').read_text(encoding='utf-8'))
    limit = next(node.value for node in auth.body if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == 'MAX_CONNECTIONS' for target in node.targets))
    namespace['MAX_CONNECTIONS'] = ast.literal_eval(limit)
    namespace.update(_redis=lambda: client, resume_after_public_retry=proof)
    exec(compile(tree, str(path), 'exec'), namespace)
    return SimpleNamespace(ns=namespace, recovery=recovery, client=client, data=data, proof=proof)


def _run(case, profiles=None, now=100000):
    return case.ns['reconcile_public_retry_deliveries'](
        [case.data.profile] if profiles is None else profiles, now=now,
    )


def test_real_public_retry_clears_only_original_pause_and_due_with_one_existing_audit(reconciliation):
    case = reconciliation
    before = _snapshot(case.client)
    result = _run(case)
    assert result == {'status': 'resumed', 'resumed_count': 1, 'channels': {CHANNEL: 'resumed'}}
    case.proof.assert_called_once_with(CHANNEL, case.data.original_id, case.data.recovered_id,
                                       REVISION, now=100000, continue_immediately=True)
    after = _snapshot(case.client)
    audit = json.loads(after.pop(case.data.audit_key))
    expected = before[case.data.state_key]
    expected.pop('paused_reason')
    expected['next_due'] = '100000'
    assert after == before
    assert audit['continue_immediately'] is True and audit['cursor'] == 2
    assert audit['recovered_task_id'] == case.data.recovered_id
    assert case.client.ttl(case.data.audit_key) == -1


def test_next_tick_and_duplicate_profiles_never_repeat_resume_or_shift_due(reconciliation):
    case = reconciliation
    assert _run(case, [case.data.profile, deepcopy(case.data.profile)])['resumed_count'] == 1
    before = _snapshot(case.client)
    assert _run(case, now=999999)['resumed_count'] == 0
    assert _snapshot(case.client) == before and case.proof.call_count == 1


@pytest.mark.parametrize('profiles', [None, {}, (), 'invalid', [None] * 11])
def test_malformed_or_over_limit_profiles_stop_before_redis_or_proof(reconciliation, profiles):
    case = reconciliation
    redis = Mock(side_effect=AssertionError('Redis must not be reached'))
    case.ns['_redis'] = redis
    assert case.ns['MAX_CONNECTIONS'] == 10
    result = case.ns['reconcile_public_retry_deliveries'](profiles)
    assert result == {'status': 'unavailable', 'resumed_count': 0, 'channels': {}}
    redis.assert_not_called()
    case.proof.assert_not_called()


def test_parallel_ticks_share_existing_atomic_recovery_idempotency(reconciliation):
    case = reconciliation
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: _run(case), range(12)))
    assert sum(result['resumed_count'] for result in results) == 1
    assert not case.client.hget(case.data.state_key, 'paused_reason')
    assert case.client.hget(case.data.state_key, 'cursor') == '2'
    assert len(case.client.keys(case.recovery.PUBLIC_RESUME_PREFIX + '*')) == 1


@pytest.mark.parametrize('field,value', [
    ('production_enabled', False), ('auto_publish', False), ('release_mode', 'private'),
    ('release_mode', 'scheduled'), ('profile_revision', ''), ('profile_revision', 'changed'),
    ('channel_id', '../../private'),
])
def test_only_current_enabled_public_profile_is_a_candidate(reconciliation, field, value):
    case = reconciliation
    profile = {**case.data.profile, field: value}
    before = _snapshot(case.client)
    assert _run(case, [profile])['resumed_count'] == 0
    assert _snapshot(case.client) == before
    case.proof.assert_not_called()


@pytest.mark.parametrize('field,value', [
    ('paused_reason', 'previous_publication_blocked'), ('paused_reason', ''),
    ('last_result', 'SUCCESS'), ('dispatch_status', 'uncertain'),
    ('active_task_id', 'still-running'), ('profile_revision', 'changed'),
])
def test_other_or_ambiguous_schedule_states_are_never_cleared(reconciliation, field, value):
    case = reconciliation
    case.client.hset(case.data.state_key, field, value)
    before = _snapshot(case.client)
    assert _run(case)['resumed_count'] == 0
    assert _snapshot(case.client) == before
    case.proof.assert_not_called()


@pytest.mark.parametrize('damage', [
    'missing', 'invalid_json', 'oversized', 'wrong_id', 'foreign_channel', 'foreign_profile',
    'not_render', 'not_scheduled', 'unclaimed', 'wrong_parent', 'cycle', 'pending_leaf',
    'failed_leaf', 'leaf_has_child', 'private', 'uncertain', 'not_original',
])
def test_bounded_discovery_never_guesses_through_missing_or_ambiguous_links(reconciliation, damage):
    case, data = reconciliation, reconciliation.data
    key = case.recovery.JOB_PREFIX + data.recovered_id
    if damage == 'missing': case.client.delete(key)
    elif damage == 'invalid_json': case.client.set(key, 'PRIVATE INVALID JSON')
    elif damage == 'oversized': case.client.set(key, 'x' * 2_000_001)
    elif damage == 'wrong_id': _change(case.client, key, lambda job: job.update(task_id=data.original_id))
    elif damage == 'foreign_channel': _change(case.client, key, lambda job: job['spec'].update(production_channel_id='Other_channel'))
    elif damage == 'foreign_profile': _change(case.client, key, lambda job: job['spec'].update(production_profile_revision='changed'))
    elif damage == 'not_render': _change(case.client, key, lambda job: job.update(kind='publish'))
    elif damage == 'not_scheduled': _change(case.client, key, lambda job: job['spec'].update(production_scheduled=False))
    elif damage == 'unclaimed': _change(case.client, case.recovery.JOB_PREFIX + data.original_id, lambda job: job.update(retry_claimed=False))
    elif damage == 'wrong_parent': _change(case.client, key, lambda job: job.update(parent_id=data.original_id))
    elif damage == 'cycle': _change(case.client, case.recovery.JOB_PREFIX + data.ids[1], lambda job: job.update(retry_child_task_id=data.original_id))
    elif damage == 'pending_leaf': _change(case.client, key, lambda job: job.update(state='PENDING'))
    elif damage == 'failed_leaf': _change(case.client, key, lambda job: job.update(state='FAILURE'))
    elif damage == 'leaf_has_child': _change(case.client, key, lambda job: job.update(retry_child_task_id=str(UUID(int=9000))))
    elif damage in {'private', 'uncertain'}: _change(case.client, key, lambda job: job['result']['youtube'].update(release_status=damage))
    elif damage == 'not_original': _change(case.client, case.recovery.JOB_PREFIX + data.original_id, lambda job: job.update(parent_id=str(UUID(int=9000))))
    before = _snapshot(case.client)
    assert _run(case)['resumed_count'] == 0
    assert _snapshot(case.client) == before
    case.proof.assert_not_called()


@pytest.mark.parametrize('hops,allowed', [(16, True), (17, False)])
def test_discovery_uses_existing_maximum_retry_hops(reconciliation, hops, allowed):
    case = reconciliation
    case.client.flushdb()  # Isolated fakeredis fixture, never an external Redis.
    case.data = _public_seed(case.recovery, case.client, hops=hops)
    assert case.ns['MAX_RETRY_HOPS'] == case.recovery.MAX_RETRY_HOPS == 16
    result = _run(case)
    assert bool(result['resumed_count']) is allowed
    assert case.proof.call_count == int(allowed)


@pytest.mark.parametrize('damage', [
    'qa', 'caption', 'thumbnail', 'disclosure', 'upload_uncertain', 'publisher_pending',
    'missing_claim', 'wrong_execution', 'changed_profile', 'missing_credentials', 'ancestor_upload',
])
def test_public_looking_candidate_never_bypasses_existing_real_delivery_guards(reconciliation, damage):
    case, data, module = reconciliation, reconciliation.data, reconciliation.recovery
    source_key = module.JOB_PREFIX + data.recovered_id
    if damage == 'qa': _change(case.client, source_key, lambda job: job['result'].update(manual_qa_required=True))
    elif damage in {'caption', 'thumbnail'}: _change(case.client, source_key, lambda job: job['result']['youtube'].update({damage + '_uploaded': False}))
    elif damage == 'disclosure': _change(case.client, source_key, lambda job: job['result']['youtube'].update(contains_synthetic_media=False))
    elif damage == 'upload_uncertain': _change(case.client, module.UPLOAD_PREFIX + data.recovered_id, lambda record: record.update(release_status='uncertain'))
    elif damage == 'publisher_pending': _change(case.client, module.JOB_PREFIX + data.publish_id, lambda job: job.update(state='PENDING'))
    elif damage == 'missing_claim': case.client.delete(module.RETRY_CHILD_CLAIM_PREFIX + data.recovered_id)
    elif damage == 'wrong_execution': case.client.set(module.RETRY_CHILD_EXECUTION_PREFIX + data.recovered_id, 'wrong-claim')
    elif damage == 'changed_profile': _change(case.client, module.PROFILE_PREFIX + CHANNEL, lambda profile: profile.update(profile_revision='changed'))
    elif damage == 'missing_credentials': case.client.delete(module.OAUTH_CREDENTIAL_PREFIX + CHANNEL)
    elif damage == 'ancestor_upload': _write(case.client, module.UPLOAD_PREFIX + data.original_id, {'status': 'uploading', 'side_effect_possible': True})
    before = _snapshot(case.client)
    result = _run(case)
    assert result['channels'][CHANNEL] == 'public_retry_not_verified'
    assert _snapshot(case.client) == before
    case.proof.assert_called_once()


def test_active_sibling_claim_is_not_expired_or_stolen(reconciliation):
    case = reconciliation
    case.client.set(case.recovery.ACTIVE_KEY, 'opaque-live-sibling-claim')
    before = _snapshot(case.client)
    assert _run(case) == {'status': 'active', 'resumed_count': 0, 'channels': {}}
    assert _snapshot(case.client) == before
    case.proof.assert_not_called()


def test_race_after_discovery_is_still_checked_by_existing_atomic_helper(reconciliation, monkeypatch):
    case = reconciliation
    original_eval = case.client.eval
    def race(*args):
        if args[0] == case.recovery._RESUME:
            _change(case.client, case.recovery.JOB_PREFIX + case.data.recovered_id,
                    lambda job: job['result']['youtube'].update(caption_uploaded=False))
        return original_eval(*args)
    monkeypatch.setattr(case.client, 'eval', race)
    assert _run(case)['resumed_count'] == 0
    assert case.client.hget(case.data.state_key, 'paused_reason') == 'previous_render_failed'
    assert not case.client.exists(case.data.audit_key)


def test_lost_commit_reply_never_causes_second_write_or_due_shift(reconciliation, monkeypatch):
    case = reconciliation
    original_eval = case.client.eval
    def lost(*args):
        result = original_eval(*args)
        if args[0] == case.recovery._RESUME:
            raise ConnectionError('PRIVATE provider and claim details')
        return result
    monkeypatch.setattr(case.client, 'eval', lost)
    first = _run(case)
    assert first['resumed_count'] == 0 and 'PRIVATE' not in json.dumps(first)
    assert not case.client.hget(case.data.state_key, 'paused_reason')
    before = _snapshot(case.client)
    assert _run(case, now=900000)['resumed_count'] == 0
    assert _snapshot(case.client) == before and case.proof.call_count == 1


def test_bad_channel_cannot_starve_another_valid_public_receipt(reconciliation):
    case = reconciliation
    other_id = 'UC_other_channel'
    bad = {**case.data.profile, 'channel_id': other_id}
    bad_task = str(UUID(int=9999))
    state = case.client.hgetall(case.data.state_key)
    case.client.hset(case.recovery.CHANNEL_STATE_PREFIX + other_id, mapping={**state, 'last_task_id': bad_task})
    case.client.set(case.recovery.JOB_PREFIX + bad_task, 'PRIVATE broken record')
    result = _run(case, [bad, case.data.profile])
    assert result['resumed_count'] == 1
    assert result['channels'][other_id] == 'reconciliation_unavailable'
    assert result['channels'][CHANNEL] == 'resumed'
    assert 'PRIVATE' not in json.dumps(result)


def _tick_namespace(case):
    path = ROOT / 'app/services/channel_production.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    tree.body = [node for node in tree.body if not (
        isinstance(node, ast.ImportFrom) and node.module in {'app.config', 'app.services.studio_state'}
    )]
    scheduler = {'settings': SimpleNamespace(redis_url='redis://not-used'),
                 'JOB_PREFIX': case.recovery.JOB_PREFIX, 'JOB_INDEX': 'youtube_studio:jobs',
                 'JOB_TTL_SECONDS': 90 * 86400}
    exec(compile(tree, str(path), 'exec'), scheduler)
    scheduler['_redis'] = lambda: case.client
    path = ROOT / 'app/production_tasks.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    tick = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'production_tick')
    tick.decorator_list = []
    tick.body = [node for node in tick.body if not isinstance(node, ast.ImportFrom)]
    enqueue = Mock()
    namespace = {
        'run_video_pipeline': SimpleNamespace(apply_async=enqueue),
        'prepare_series_batch': SimpleNamespace(apply_async=Mock()),
        'maintain_production_series': Mock(return_value={'status': 'checked', 'channels': {}}),
        'connection_status': Mock(return_value={'connections': [json.loads(case.client.get(case.recovery.OAUTH_CHANNEL_PREFIX + CHANNEL))]}),
        'list_channel_profiles': Mock(return_value=[case.data.profile]),
        'reconcile_public_retry_deliveries': case.ns['reconcile_public_retry_deliveries'],
        **{name: scheduler[name] for name in ('reconcile_active_production', 'dispatch_due_productions', 'ChannelProductionError')},
    }
    exec(compile(ast.Module(body=[tick], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace, enqueue


def test_actual_cloud_tick_reconciles_then_starts_only_next_frozen_topic(reconciliation):
    case = reconciliation
    # The last scheduled original failed before beat consumed its terminal
    # state. Its successful public retry already exists, but cannot remove it.
    case.client.hdel(case.data.state_key, 'paused_reason')
    case.client.hset(case.data.state_key, mapping={'active_task_id': case.data.original_id,
                                                 'dispatch_status': 'enqueued', 'last_result': 'PENDING'})
    case.client.set(case.recovery.ACTIVE_KEY, json.dumps({'channel_id': CHANNEL, 'task_id': case.data.original_id}))
    original = case.client.get(case.recovery.JOB_PREFIX + case.data.original_id)
    namespace, enqueue = _tick_namespace(case)
    result = namespace['production_tick']()
    assert result['status'] == 'queued' and result['public_retry_reconciliation']['resumed_count'] == 1
    assert enqueue.call_count == 1
    next_job = json.loads(case.client.get(case.recovery.JOB_PREFIX + result['task_id']))
    assert next_job['spec']['production_topic_index'] == 2
    assert next_job['spec']['topic'].startswith(case.data.profile['production_topics'][2])
    assert case.client.get(case.recovery.JOB_PREFIX + case.data.original_id) == original
    assert case.client.hget(case.data.state_key, 'cursor') == '3'
    assert namespace['production_tick']()['status'] == 'active' and enqueue.call_count == 1


def test_tick_reconciles_only_current_linked_profiles_not_stale_disconnected_history(reconciliation):
    case = reconciliation
    namespace, enqueue = _tick_namespace(case)
    stale = [{**case.data.profile, 'channel_id': f'UC_stale_{index}'} for index in range(12)]
    namespace['list_channel_profiles'].return_value = [*stale, case.data.profile]
    result = namespace['production_tick']()
    assert result['status'] == 'queued' and result['public_retry_reconciliation']['resumed_count'] == 1
    assert enqueue.call_count == case.proof.call_count == 1


def test_discovery_module_has_no_independent_state_or_provider_mutators():
    tree = ast.parse((ROOT / 'app/services/production_reconciliation.py').read_text(encoding='utf-8'))
    methods = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    assert not methods & {'set', 'hset', 'delete', 'hdel', 'eval', 'pipeline', 'apply_async', 'delay', 'create'}
    calls = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert 'resume_after_public_retry' in calls
    assert not calls & {'claim_retry_dispatch', 'save_channel_profile', 'prepare_next_series', 'run_video_pipeline'}
