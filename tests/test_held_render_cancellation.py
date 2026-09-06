"""Real Redis transactions, mocked worker inventory; no live workers/providers."""
import ast
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import held_render_cancellation_routes as routes
from app.services import held_render_cancellation as cancel
from test_source_publication_hold import case as hold_case, snapshot


@pytest.fixture
def case(hold_case, monkeypatch):
    c = hold_case
    c.parent, c.token = str(uuid4()), 'aB9_' * 10 + 'aB9'  # token_urlsafe(32) wire shape
    c.job['parent_id'] = c.parent
    c.client.set(c.key, json.dumps(c.job), keepttl=True)
    parent = {**deepcopy(c.job), 'task_id': c.parent, 'parent_id': None, 'state': 'FAILURE',
              'error': 'Original real QA failure', 'retry_child_task_id': c.task, 'retry_claimed': True}
    c.client.set(cancel.state.JOB_PREFIX + c.parent, json.dumps(parent))
    c.client.zadd(cancel.state.JOB_INDEX, {c.parent: 999})
    c.client.hset(cancel.state.RETRY_CHILD_CLAIM_PREFIX + c.task,
                  mapping={'source_task_id': c.parent, 'token': c.token})
    c.client.hset(cancel.state.RETRY_DISPATCH_PREFIX + c.parent,
                  mapping={'child_task_id': c.task, 'token': c.token, 'state': 'dispatched', 'mode': 'full'})
    c.client.set(cancel.state.RETRY_CHILD_EXECUTION_PREFIX + c.task, c.token)
    c.client.hset(cancel.CHANNEL_STATE_PREFIX + c.channel, mapping={'paused_reason': 'render_failed', 'topic_cursor': 2})
    c.run()
    c.request = {**c.request, 'retired_worker_deployment_id': str(uuid4()),
                 'expected_worker_names': ['celery@current-worker']}
    c.inspector = SimpleNamespace(**{name: Mock(return_value={'celery@current-worker': []})
                                    for name in ('active', 'reserved', 'scheduled')})
    c.control = SimpleNamespace(inspect=Mock(return_value=c.inspector))
    monkeypatch.setitem(sys.modules, 'app.celery_app', SimpleNamespace(celery=SimpleNamespace(control=c.control)))
    c.run = lambda: cancel.cancel_held_render(c.task, **c.request, now=3000)
    c.cancel_key = cancel.CANCELLATION_PREFIX + c.task
    return c


def test_real_cancel_preserves_every_other_record_and_all_original_qa_media_budget_fields(case):
    c = case
    before, original, ttl = snapshot(c.client), cancel.state.get_job(c.task), c.client.pttl(c.key)
    assert c.run() == {'status': 'cancelled', 'task_id': c.task, 'channel_id': c.channel, 'profile_revision': c.revision}
    final, receipt = cancel.state.get_job(c.task), json.loads(c.client.get(c.cancel_key))
    changed = {'state', 'stage', 'message', 'updated_at', 'owner_cancellation'}
    assert {k: v for k, v in final.items() if k not in changed} == {k: v for k, v in original.items() if k not in changed}
    assert final['state'] == 'CANCELLED' and final['progress'] == 64 and final['result'] is None
    assert all(c.client.dump(k) == v for k, v in before.items() if k != c.key)
    assert set(snapshot(c.client)) - set(before) == {c.cancel_key}
    assert c.client.ttl(c.cancel_key) == -1 and ttl - 2000 <= c.client.pttl(c.key) <= ttl
    assert receipt['original_job_sha256'] == cancel._digest(original)
    assert receipt['original_status']['state'] == 'PROGRESS'
    assert receipt['retired_deployment_evidence'] == 'owner_attested_removed_not_server_verified'
    assert receipt['worker_inventory']['scope'] == 'owner_attested_complete_current_worker_names'
    assert not {'qa_approved', 'success', 'publish_task_id', 'video_id'} & set(receipt)
    assert receipt['receipt_sha256'] == cancel._digest({k: v for k, v in receipt.items() if k != 'receipt_sha256'})
    c.control.inspect.assert_called_once_with(timeout=3.0)


def test_request_fence_is_present_before_each_real_server_inventory_call(case):
    c = case
    for method in ('active', 'reserved', 'scheduled'):
        def inspect():
            assert json.loads(c.client.get(c.cancel_key))['status'] == 'cancel_requested'
            assert cancel.state.get_job(c.task)['state'] == 'PROGRESS'
            assert cancel.state.render_cancellation_requested(c.task)
            return {'celery@current-worker': []}
        getattr(c.inspector, method).side_effect = inspect
    assert c.run()['status'] == 'cancelled'


@pytest.mark.parametrize('method', ['active', 'reserved', 'scheduled'])
@pytest.mark.parametrize('reply', [None, {}, {'unexpected@worker': []}, {'celery@current-worker': None},
                                  {'celery@current-worker': [{}]}, {'celery@current-worker': [] ,'extra@worker': []}])
def test_unknown_incomplete_or_malformed_inventory_never_marks_terminal(case, method, reply):
    c = case
    getattr(c.inspector, method).return_value = reply
    with pytest.raises(cancel.HeldRenderCancellationError, match='^held_render_cancellation_not_eligible$'):
        c.run()
    assert cancel.state.get_job(c.task)['state'] == 'PROGRESS'
    assert json.loads(c.client.get(c.cancel_key))['status'] == 'cancel_requested'


@pytest.mark.parametrize('method', ['active', 'reserved', 'scheduled'])
def test_known_live_or_queued_target_leaves_pending_fence_without_fake_stop(case, method):
    c = case
    row = {'request': {'id': c.task}} if method == 'scheduled' else {'id': c.task}
    getattr(c.inspector, method).return_value = {'celery@current-worker': [row]}
    with pytest.raises(cancel.HeldRenderCancellationError): c.run()
    assert cancel.state.get_job(c.task)['state'] == 'PROGRESS'
    getattr(c.inspector, method).return_value = {'celery@current-worker': []}
    assert c.run()['status'] == 'cancelled'


def test_inventory_exception_safe_no_raw_worker_data_and_exact_replay_can_finish(case):
    c = case
    c.inspector.active.side_effect = RuntimeError('redis://private-password')
    with pytest.raises(cancel.HeldRenderCancellationError) as error: c.run()
    assert 'private' not in str(error.value)
    c.inspector.active.side_effect = None
    assert c.run()['status'] == 'cancelled'
    frozen = snapshot(c.client)
    c.inspector.active.side_effect = AssertionError('historical replay must not inspect')
    assert c.run()['status'] == 'already_cancelled'
    assert snapshot(c.client) == frozen


def test_channel_analytics_refresh_does_not_deadlock_pending_request(case):
    c = case
    c.inspector.active.return_value = None
    with pytest.raises(cancel.HeldRenderCancellationError): c.run()
    channel = json.loads(c.client.get(cancel.CHANNEL_PREFIX + c.channel))
    c.client.set(cancel.CHANNEL_PREFIX + c.channel, json.dumps({**channel, 'view_count': 999, 'updated_at': 'new metric refresh'}))
    c.inspector.active.return_value = {'celery@current-worker': []}
    def inventory():
        c.client.set(cancel.CHANNEL_PREFIX + c.channel, json.dumps({**channel, 'view_count': 1001, 'updated_at': 'another refresh'}))
        return {'celery@current-worker': []}
    c.inspector.scheduled.side_effect = inventory
    assert c.run()['status'] == 'cancelled'


@pytest.mark.parametrize('field,value', [('title', 'Changed identity'), ('description', 'Changed biography'),
                                        ('connection_id', 'new-connection'), ('requires_reconnect', True)])
def test_pending_request_still_rejects_changed_channel_identity(case, field, value):
    c = case
    c.inspector.active.return_value = None
    with pytest.raises(cancel.HeldRenderCancellationError): c.run()
    channel = json.loads(c.client.get(cancel.CHANNEL_PREFIX + c.channel))
    c.client.set(cancel.CHANNEL_PREFIX + c.channel, json.dumps({**channel, field: value}))
    c.inspector.active.return_value = {'celery@current-worker': []}
    with pytest.raises(cancel.HeldRenderCancellationError): c.run()
    assert cancel.state.get_job(c.task)['state'] == 'PROGRESS'


@pytest.mark.parametrize('mutation', ['hold', 'upload', 'publisher_lock', 'revision', 'connection', 'active_slot',
                                    'channel_active', 'claim', 'execution', 'parent', 'dispatch', 'next_retry',
                                    'episode_resolved', 'unheld_spec', 'not_retry', 'other_channel_work', 'publisher'])
def test_eligibility_failures_write_no_request_or_other_state(case, mutation):
    c = case
    if mutation == 'hold': c.client.delete(cancel.HOLD_PREFIX + c.task)
    elif mutation == 'upload': c.client.set(cancel.UPLOAD_PREFIX + c.task, '{}')
    elif mutation == 'publisher_lock': c.client.set(cancel.EXECUTION_LOCK_PREFIX + c.task, 'active')
    elif mutation == 'revision': c.client.set(cancel.PROFILE_PREFIX + c.channel, json.dumps({**c.profile, 'profile_revision': 'changed-revision'}))
    elif mutation == 'connection': c.client.set(cancel.CHANNEL_PREFIX + c.channel, json.dumps({'id': c.channel, 'connection_id': 'changed-connection'}))
    elif mutation == 'active_slot': c.client.set(cancel.ACTIVE_KEY, json.dumps({'channel_id': c.channel, 'task_id': c.task}))
    elif mutation == 'channel_active': c.client.hset(cancel.CHANNEL_STATE_PREFIX + c.channel, 'active_task_id', c.task)
    elif mutation == 'claim': c.client.hset(cancel.state.RETRY_CHILD_CLAIM_PREFIX + c.task, 'token', 'b' * 32)
    elif mutation == 'execution': c.client.delete(cancel.state.RETRY_CHILD_EXECUTION_PREFIX + c.task)
    elif mutation == 'parent': c.client.set(cancel.state.JOB_PREFIX + c.parent, '{}')
    elif mutation == 'dispatch': c.client.hset(cancel.state.RETRY_DISPATCH_PREFIX + c.parent, 'child_task_id', str(uuid4()))
    elif mutation == 'next_retry': c.client.hset(cancel.state.RETRY_DISPATCH_PREFIX + c.task, 'child_task_id', str(uuid4()))
    elif mutation == 'episode_resolved': c.client.set(cancel.state.EXTERNAL_EPISODE_LEAF_PREFIX + c.task, '{}')
    elif mutation in {'unheld_spec', 'not_retry'}:
        job = cancel.state.get_job(c.task)
        if mutation == 'not_retry': job['parent_id'] = None
        else: job['spec']['topic'] = 'Different topic'
        c.client.set(c.key, json.dumps(job), keepttl=True)
    else:
        task = str(uuid4())
        other = {'task_id': task, 'state': 'PROGRESS', 'kind': 'publish' if mutation == 'publisher' else 'render',
                 'parent_id': c.task if mutation == 'publisher' else None,
                 'spec': {'production_channel_id': c.channel}}
        c.client.set(cancel.state.JOB_PREFIX + task, json.dumps(other))
        c.client.zadd(cancel.state.JOB_INDEX, {task: 1001})
    before = snapshot(c.client)
    with pytest.raises(cancel.HeldRenderCancellationError): c.run()
    assert snapshot(c.client) == before
    c.control.inspect.assert_not_called()


@pytest.mark.parametrize('mutation', ['profile', 'new_claim', 'job_success'])
def test_changes_during_inventory_prevent_terminal_commit(case, mutation):
    c = case
    def inventory():
        if mutation == 'profile': c.client.set(cancel.PROFILE_PREFIX + c.channel, json.dumps({**c.profile, 'description': 'changed'}))
        elif mutation == 'new_claim': c.client.hset(cancel.state.RETRY_DISPATCH_PREFIX + c.task, 'child_task_id', str(uuid4()))
        else: cancel.state.mark_success(c.task, {'task_id': c.task, 'video_key': 'retained-output'})
        return {'celery@current-worker': []}
    c.inspector.scheduled.side_effect = inventory
    with pytest.raises(cancel.HeldRenderCancellationError): c.run()
    assert cancel.state.get_job(c.task)['state'] != 'CANCELLED'
    assert json.loads(c.client.get(c.cancel_key))['status'] == 'cancel_requested'


def test_final_watch_conflict_keeps_request_and_real_paid_ledger_not_false_cancelled(case, monkeypatch):
    c = case
    original_pipeline, calls = c.client.pipeline, [0]
    def pipeline(*args, **kwargs):
        pipe = original_pipeline(*args, **kwargs)
        execute = pipe.execute
        def race(*args, **kwargs):
            calls[0] += 1
            if calls[0] == 2:
                c.client.hset(cancel.state.PAID_CREATE_BUDGET_PREFIX + c.task, 'used', 7)
            return execute(*args, **kwargs)
        pipe.execute = race
        return pipe
    monkeypatch.setattr(c.client, 'pipeline', pipeline)
    with pytest.raises(cancel.HeldRenderCancellationError): c.run()
    assert cancel.state.get_job(c.task)['state'] == 'PROGRESS'
    assert json.loads(c.client.get(c.cancel_key))['status'] == 'cancel_requested'
    assert c.client.hget(cancel.state.PAID_CREATE_BUDGET_PREFIX + c.task, 'used') == '7'


@pytest.mark.parametrize('after_commit', [1, 2])
def test_lost_exec_reply_exact_replay_is_idempotent(case, monkeypatch, after_commit):
    c = case
    original_pipeline, calls = c.client.pipeline, [0]
    def pipeline(*args, **kwargs):
        pipe = original_pipeline(*args, **kwargs)
        execute = pipe.execute
        def lost(*args, **kwargs):
            value = execute(*args, **kwargs); calls[0] += 1
            if calls[0] == after_commit: raise ConnectionError('lost EXEC reply')
            return value
        pipe.execute = lost
        return pipe
    monkeypatch.setattr(c.client, 'pipeline', pipeline)
    with pytest.raises(cancel.HeldRenderCancellationError): c.run()
    monkeypatch.setattr(c.client, 'pipeline', original_pipeline)
    assert c.run()['status'] == ('cancelled' if after_commit == 1 else 'already_cancelled')


def test_actual_state_writers_cannot_resurrect_cancelled_snapshot(case):
    c = case
    stale = deepcopy(cancel.state.get_job(c.task))
    c.run()
    before = snapshot(c.client)
    for writer in (lambda: cancel.state.save_job(stale),
                   lambda: cancel.state.update_job(c.task, state='PROGRESS'),
                   lambda: cancel.state.mark_failure(c.task, 'stale old failure'),
                   lambda: cancel.state.mark_success(c.task, {'task_id': c.task, 'video_key': 'stale old output'})):
        assert writer()['state'] == 'CANCELLED'
        assert snapshot(c.client) == before


def test_pending_fence_keeps_real_inflight_media_journal(case):
    c = case
    c.inspector.active.return_value = None
    with pytest.raises(cancel.HeldRenderCancellationError): c.run()
    cancel.state.update_job(c.task, generated_asset_candidates=[{'retained_real_output': 1}])
    assert cancel.state.get_job(c.task)['generated_asset_candidates'] == [{'retained_real_output': 1}]


@pytest.mark.parametrize('terminal', [False, True])
def test_save_watch_race_retries_real_journal_or_returns_cancelled_without_resurrection(case, monkeypatch, terminal):
    c = case
    original_pipeline, raced = c.client.pipeline, [False]
    def pipeline(*args, **kwargs):
        pipe = original_pipeline(*args, **kwargs)
        execute = pipe.execute
        def race(*args, **kwargs):
            if not raced[0]:
                raced[0] = True
                if terminal:
                    c.run()
                else:
                    c.inspector.active.return_value = None
                    with pytest.raises(cancel.HeldRenderCancellationError): c.run()
            return execute(*args, **kwargs)
        pipe.execute = race
        return pipe
    monkeypatch.setattr(c.client, 'pipeline', pipeline)
    result = cancel.state.update_job(c.task, generated_asset_candidates=[{'real': 'retained'}])
    assert result['state'] == ('CANCELLED' if terminal else 'PROGRESS')
    if not terminal:
        assert cancel.state.get_job(c.task)['generated_asset_candidates'] == [{'real': 'retained'}]


@pytest.mark.parametrize('retries', [0, 1, 2])
@pytest.mark.parametrize('source_id', [None, '11111111-1111-4111-8111-111111111111'])
def test_actual_entry_guard_stops_every_cancelled_delivery_before_execution_claim(case, retries, source_id):
    c = case
    c.inspector.active.return_value = None
    with pytest.raises(cancel.HeldRenderCancellationError): c.run()
    path = Path(__file__).resolve().parents[1] / 'app/tasks.py'
    node = next(n for n in ast.parse(path.read_text(encoding='utf-8')).body
                if isinstance(n, ast.FunctionDef) and n.name == '_guard_retry_child_execution')
    class Ignore(Exception): pass
    acquire = Mock(side_effect=AssertionError('No existing claim mutation or paid work'))
    ns = {'render_cancellation_requested': cancel.state.render_cancellation_requested,
          'acquire_retry_child_execution': acquire, 'Ignore': Ignore}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), ns)
    with pytest.raises(Ignore):
        ns['_guard_retry_child_execution'](SimpleNamespace(request=SimpleNamespace(retries=retries)), c.task, source_id)
    acquire.assert_not_called()


def test_actual_retry_lua_cannot_claim_even_stale_failure_snapshot_after_cancellation(case):
    c = case
    c.run()
    stale = {**c.job, 'state': 'FAILURE'}
    c.client.set(c.key, json.dumps(stale), keepttl=True)  # simulate an undeployed stale writer
    with pytest.raises(ValueError, match='owner cancellation fence'):
        cancel.state.claim_retry_dispatch(c.task, str(uuid4()), 'b' * 32, allow_repair=False)
    assert not c.client.exists(cancel.state.RETRY_DISPATCH_PREFIX + c.task)


def test_cancelled_sync_and_ui_do_not_claim_failure_or_running(case):
    c = case
    c.run()
    path = Path(__file__).resolve().parents[1] / 'app/studio.py'
    names = {'_sync_job', '_job_ui_status', '_job_display_status', '_job_status_message'}
    nodes = [n for n in ast.parse(path.read_text(encoding='utf-8')).body if isinstance(n, ast.FunctionDef) and n.name in names]
    ns = {'get_job': cancel.state.get_job, 'AsyncResult': Mock(side_effect=AssertionError('stale Celery snapshot'))}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), ns)
    actual = ns['_sync_job'](c.task)
    assert ns['_job_ui_status'](actual) == ns['_job_display_status'](actual) == 'cancelled'
    assert 'Kalite onayı verilmedi' in ns['_job_status_message'](actual)


@pytest.fixture
def web(case, monkeypatch):
    monkeypatch.setattr(routes, '_require_auth', lambda token: None if token == 'owner' else (_ for _ in ()).throw(HTTPException(401, 'unauthorized')))
    app = FastAPI(); app.include_router(routes.router)
    return TestClient(app), case


def test_owner_route_returns_only_compact_status_ids(web):
    client, c = web
    response = client.post(f'/studio/api/job/{c.task}/cancel-held-render', json=c.request, headers={'X-Factory-Token': 'owner'})
    assert response.status_code == 200
    assert set(response.json()) == {'status', 'task_id', 'channel_id', 'profile_revision'}
    assert response.json()['status'] == 'cancelled'


def test_unauthorized_request_rejected_before_path_body_or_size(web):
    client, c = web
    response = client.post('/studio/api/job/not-a-uuid/cancel-held-render', content=b'x' * 9000,
                           headers={'Content-Type': 'application/json'})
    assert response.status_code == 401 and not c.client.exists(c.cancel_key)


@pytest.mark.parametrize('change', [lambda x: {**x, 'state': 'CANCELLED'}, lambda x: {**x, 'reason': 'short'},
                                   lambda x: {**x, 'expected_worker_names': []},
                                   lambda x: {**x, 'expected_worker_names': ['worker@a', 'worker@a']},
                                   lambda x: {**x, 'expected_worker_names': ['worker@z', 'worker@a']},
                                   lambda x: {**x, 'retired_worker_deployment_id': 'not-uuid'},
                                   lambda x: {**x, 'expected_profile_revision': True}])
def test_route_rejects_extra_fields_bad_identifiers_and_unbounded_attestations(web, change):
    client, c = web
    response = client.post(f'/studio/api/job/{c.task}/cancel-held-render', json=change(c.request), headers={'X-Factory-Token': 'owner'})
    assert response.status_code == 422 and not c.client.exists(c.cancel_key)


def test_route_oversize_and_unknown_inventory_do_not_return_terminal_status(web):
    client, c = web
    url = f'/studio/api/job/{c.task}/cancel-held-render'
    response = client.post(url, content=b'x' * 8193, headers={'X-Factory-Token': 'owner', 'Content-Type': 'application/json'})
    assert response.status_code == 413
    c.inspector.active.return_value = None
    response = client.post(url, json=c.request, headers={'X-Factory-Token': 'owner'})
    assert response.status_code == 409 and response.json() == {'detail': 'held_render_cancellation_not_eligible'}
    assert cancel.state.get_job(c.task)['state'] == 'PROGRESS'
