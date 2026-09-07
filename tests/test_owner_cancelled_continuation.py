"""Real fake-Redis CAS, actual cancellation receipts, mocked idle inventory."""
import ast
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import fakeredis
from fastapi import APIRouter, Cookie, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
import pytest

from app.services import owner_cancelled_continuation as flow
from app.services import held_render_cancellation as cancel, source_publication_hold as hold

NOW = 1788775500.0
CHANNEL = 'UCgvESYtYbn2w9R2ExBOF_cw'


def snapshot(client):
    return {key: client.dump(key) for key in client.scan_iter()}


@pytest.fixture
def case(monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(flow.jobs, '_client', lambda: client)
    root, leaf = str(uuid4()), str(uuid4())
    old, current, connection, token = 'old_profile_revision', 'new_profile_revision', 'same_connection', 'aB9_' * 10
    topics = ['IKEA fact', 'Costco membership', 'LEGO rescue plan', 'Nintendo playing cards']
    profile = {'channel_id': CHANNEL, 'profile_revision': old, 'production_enabled': True,
        'auto_publish': True, 'release_mode': 'public', 'production_topics': topics,
        'series_id': 'business-decisions-01', 'series_total': 4, 'default_language': 'en',
        'languages': ['en'], 'route_label': 'margin-verdict', 'channel_identity': 'Business evidence.'}
    channel = {'id': CHANNEL, 'connection_id': connection, 'verified_at': datetime.fromtimestamp(NOW - 5, timezone.utc).isoformat()}
    spec = {'topic': topics[1] + '\n\nChannel editorial direction: Business evidence.',
        'duration_minutes': .5, 'language': 'en', 'channel_id': 'margin-verdict', 'mode': 'production', 'format': 'shorts',
        'production_channel_id': CHANNEL, 'production_connection_id': connection,
        'production_profile_revision': old, 'production_topic_index': 1,
        'production_scheduled': True, 'publish_after_render': True, 'workflow': 'auto'}
    parent = {'task_id': root, 'parent_id': None, 'kind': 'render', 'state': 'FAILURE', 'stage': 'failed',
        'failure_stage': 'final_visual_qc', 'spec': deepcopy(spec), 'result': None,
        'retry_child_task_id': leaf, 'retry_claimed': True, 'retry_dispatch_state': 'dispatched'}
    child = {'task_id': leaf, 'parent_id': root, 'kind': 'render', 'state': 'PROGRESS', 'stage': 'ai_scene_generation',
        'progress': 64, 'message': 'Original progress', 'updated_at': '2026-09-06T17:25:00+00:00',
        'spec': deepcopy(spec), 'result': None, 'generated_asset_candidates': {'preserved_count': 5, 'failed_count': 0}}
    for job in (parent, child):
        client.set(flow.jobs.JOB_PREFIX + job['task_id'], json.dumps(job), ex=3600)
        client.zadd(flow.jobs.JOB_INDEX, {job['task_id']: 1000})
        client.hset(flow.jobs.PAID_CREATE_BUDGET_PREFIX + job['task_id'], mapping={'cap': 6, 'used': 6})
    client.hset(flow.jobs.RETRY_DISPATCH_PREFIX + root, mapping={
        'child_task_id': leaf, 'token': token, 'state': 'dispatched', 'mode': 'full'})
    client.hset(flow.jobs.RETRY_CHILD_CLAIM_PREFIX + leaf, mapping={'source_task_id': root, 'token': token})
    client.set(flow.jobs.RETRY_CHILD_EXECUTION_PREFIX + leaf, token)
    client.set(flow.PROFILE_PREFIX + CHANNEL, json.dumps(profile))
    client.set(flow.CHANNEL_PREFIX + CHANNEL, json.dumps(channel))
    client.set(flow.CREDENTIAL_PREFIX + CHANNEL, 'opaque credential never decoded')
    client.sadd(flow.CHANNEL_INDEX_KEY, CHANNEL)
    client.set(flow.AUTH_EPOCH_KEY, '7')
    state_key = flow.CHANNEL_STATE_PREFIX + CHANNEL
    client.hset(state_key, mapping={'cursor': 2, 'next_due': NOW - 100,
        'consumed_prefix': flow._prefix_digest(topics[:2]), 'last_task_id': root,
        'last_result': 'FAILURE', 'dispatch_status': 'finished', 'paused_reason': 'previous_render_failed',
        'profile_revision': old, 'connection_id': connection})
    client.set(flow.SERIES_COUNTER_PREFIX + CHANNEL + ':' + profile['series_id'], '2')
    client.set('youtube_studio:old_deleted_publication:keep', 'old deleted history and counters stay')
    hold.hold_source_publication(leaf, CHANNEL, old, 'Owner rejected this old edit; retain without uploading.', now=NOW - 100)
    inspector = SimpleNamespace(**{k: Mock(return_value={'celery@worker': []}) for k in ('active', 'reserved', 'scheduled')})
    control = SimpleNamespace(inspect=Mock(return_value=inspector))
    monkeypatch.setitem(sys.modules, 'app.celery_app', SimpleNamespace(celery=SimpleNamespace(control=control)))
    cancel.cancel_held_render(leaf, CHANNEL, old, 'Owner rejected this old edit; retain without uploading.',
        str(uuid4()), ['celery@worker'], now=NOW - 90)
    profile['profile_revision'] = current
    client.set(flow.PROFILE_PREFIX + CHANNEL, json.dumps(profile))
    request = {'expected_profile_revision': current, 'expected_state_revision': old,
        'expected_connection_id': connection, 'root_task_id': root, 'cancelled_leaf_id': leaf,
        'expected_next_topic_sha256': flow.delivery.topic_sha256(topics[2]),
        'expected_worker_names': ['celery@worker'],
        'owner_reason': 'The owner confirms the appeal was accepted and authorizes normal future public production. '
                        'Abandon this cancelled old edit without crediting its deletion as delivery.'}
    audit_key = flow.PREFIX + CHANNEL + ':' + root
    return SimpleNamespace(client=client, root=root, leaf=leaf, old=old, current=current, connection=connection,
        profile=profile, channel=channel, state_key=state_key, request=request, audit_key=audit_key,
        inspector=inspector, control=control,
        run=lambda: flow.continue_after_owner_cancellation(CHANNEL, now=NOW, **request))


def test_actual_continuation_changes_only_audit_and_three_scheduler_fields(case):
    c = case; before = snapshot(c.client); old_state = c.client.hgetall(c.state_key)
    result = c.run()
    assert result['status'] == 'continued' and result['next_topic_index'] == 2
    assert result['disposition'] == 'abandoned_not_delivered'
    assert all(result[k] is False for k in ('old_episode_delivered', 'old_qa_approved', 'old_media_reused', 'new_task_enqueued'))
    assert {k: v for k, v in snapshot(c.client).items() if k not in {c.state_key, c.audit_key}} == {
        k: v for k, v in before.items() if k != c.state_key}
    assert c.client.hgetall(c.state_key) == {**{k: v for k, v in old_state.items() if k != 'paused_reason'},
                                           'profile_revision': c.current, 'next_due': str(NOW)}
    record = json.loads(c.client.get(c.audit_key))
    assert record['proof']['chain'] == [c.root, c.leaf]
    assert record['proof']['series_counter'] == '2' and record['old_paid_requests_retried'] is False
    assert c.client.ttl(c.audit_key) == -1


def test_idempotent_replay_does_not_change_a_later_scheduler_state(case):
    c = case; c.run()
    c.client.hset(c.state_key, mapping={'cursor': 3, 'active_task_id': str(uuid4()), 'next_due': NOW + 9000})
    before = snapshot(c.client)
    c.inspector.active.side_effect = AssertionError('Replay must not call inventory')
    assert c.run()['status'] == 'already_continued'
    assert snapshot(c.client) == before


@pytest.mark.parametrize('mutation', ['revision', 'old_revision', 'connection', 'disabled', 'private', 'epoch',
    'stale_channel', 'topics_prefix', 'next_topic', 'root_pointer', 'active', 'uncertain', 'wrong_pause',
    'cursor', 'series_counter', 'missing_credential', 'missing_membership', 'foreign_active',
    'leaf_not_cancelled', 'leaf_provider_pending', 'root_provider_pending', 'leaf_upload', 'publisher_lock',
    'cancel_receipt', 'hold_receipt', 'claim', 'execution', 'dispatch', 'later_child'])
def test_ineligible_or_uncertain_state_writes_nothing(case, mutation):
    c = case
    pkey = flow.PROFILE_PREFIX + CHANNEL; skey = c.state_key
    def profile(**edits):
        c.client.set(pkey, json.dumps({**c.profile, **edits}))
    def job(task, **edits):
        key = flow.jobs.JOB_PREFIX + task
        c.client.set(key, json.dumps({**json.loads(c.client.get(key)), **edits}), keepttl=True)
    if mutation == 'revision': profile(profile_revision='different_revision')
    elif mutation == 'old_revision': c.client.hset(skey, 'profile_revision', 'changed_revision')
    elif mutation == 'connection': c.client.set(flow.CHANNEL_PREFIX + CHANNEL, json.dumps({**c.channel, 'connection_id': 'changed_connection'}))
    elif mutation == 'disabled': profile(production_enabled=False)
    elif mutation == 'private': profile(release_mode='private')
    elif mutation == 'epoch': profile(series_epoch=2)
    elif mutation == 'stale_channel': c.client.set(flow.CHANNEL_PREFIX + CHANNEL, json.dumps({**c.channel, 'verified_at': datetime.fromtimestamp(NOW - 901, timezone.utc).isoformat()}))
    elif mutation == 'topics_prefix': profile(production_topics=['Changed', *c.profile['production_topics'][1:]])
    elif mutation == 'next_topic': profile(production_topics=[*c.profile['production_topics'][:2], 'Different next', 'Last'])
    elif mutation == 'root_pointer': c.client.hset(skey, 'last_task_id', str(uuid4()))
    elif mutation == 'active': c.client.hset(skey, 'active_task_id', str(uuid4()))
    elif mutation == 'uncertain': c.client.hset(skey, 'dispatch_status', 'uncertain')
    elif mutation == 'wrong_pause': c.client.hset(skey, 'paused_reason', 'previous_publication_blocked')
    elif mutation == 'cursor': c.client.hset(skey, 'cursor', '3')
    elif mutation == 'series_counter': c.client.set(flow.SERIES_COUNTER_PREFIX + CHANNEL + ':' + c.profile['series_id'], '1')
    elif mutation == 'missing_credential': c.client.delete(flow.CREDENTIAL_PREFIX + CHANNEL)
    elif mutation == 'missing_membership': c.client.srem(flow.CHANNEL_INDEX_KEY, CHANNEL)
    elif mutation == 'foreign_active':
        task = str(uuid4()); c.client.set(flow.jobs.JOB_PREFIX + task, json.dumps({'task_id': task, 'state': 'PROGRESS', 'spec': {}})); c.client.zadd(flow.jobs.JOB_INDEX, {task: 1000})
    elif mutation == 'leaf_not_cancelled': job(c.leaf, state='PROGRESS')
    elif mutation == 'leaf_provider_pending': job(c.leaf, provider_in_flight=True)
    elif mutation == 'root_provider_pending': job(c.root, provider_request_state='uncertain')
    elif mutation == 'leaf_upload': c.client.set(flow.UPLOAD_PREFIX + c.leaf, json.dumps({'status': 'uploading'}))
    elif mutation == 'publisher_lock': c.client.set(flow.EXECUTION_LOCK_PREFIX + c.leaf, 'owned')
    elif mutation == 'cancel_receipt': c.client.set(cancel.CANCELLATION_PREFIX + c.leaf, '{}')
    elif mutation == 'hold_receipt': c.client.set(hold.HOLD_PREFIX + c.leaf, '{}')
    elif mutation == 'claim': c.client.hset(flow.jobs.RETRY_CHILD_CLAIM_PREFIX + c.leaf, 'token', 'different')
    elif mutation == 'execution': c.client.set(flow.jobs.RETRY_CHILD_EXECUTION_PREFIX + c.leaf, 'different')
    elif mutation == 'dispatch': c.client.hset(flow.jobs.RETRY_DISPATCH_PREFIX + c.root, 'state', 'uncertain')
    elif mutation == 'later_child': job(c.leaf, retry_child_task_id=str(uuid4()))
    before = snapshot(c.client)
    with pytest.raises(flow.OwnerContinuationError): c.run()
    assert snapshot(c.client) == before


@pytest.mark.parametrize('kind', ['active', 'reserved', 'scheduled'])
def test_fresh_server_inventory_busy_or_unknown_blocks(case, kind):
    c = case
    row = {'id': str(uuid4())}
    getattr(c.inspector, kind).return_value = {'celery@worker': [{'request': row} if kind == 'scheduled' else row]}
    before = snapshot(c.client)
    with pytest.raises(flow.OwnerContinuationError): c.run()
    assert snapshot(c.client) == before
    getattr(c.inspector, kind).return_value = None
    with pytest.raises(flow.OwnerContinuationError): c.run()
    assert snapshot(c.client) == before


def test_profile_change_during_inventory_wins_without_continuation(case):
    c = case
    def inventory():
        c.client.set(flow.PROFILE_PREFIX + CHANNEL, json.dumps({**c.profile, 'auto_publish': False}))
        return {'celery@worker': []}
    c.inspector.active.side_effect = inventory
    old_state = c.client.hgetall(c.state_key)
    with pytest.raises(flow.OwnerContinuationError): c.run()
    assert c.client.hgetall(c.state_key) == old_state and not c.client.exists(c.audit_key)


def test_watch_race_before_commit_cannot_clear_pause(case, monkeypatch):
    c = case; original_pipeline = c.client.pipeline; calls = 0
    def pipeline(*args, **kwargs):
        nonlocal calls
        pipe = original_pipeline(*args, **kwargs); calls += 1
        if calls == 2:
            execute = pipe.execute
            def raced():
                c.client.hset(c.state_key, 'active_task_id', str(uuid4()))
                return execute()
            pipe.execute = raced
        return pipe
    monkeypatch.setattr(c.client, 'pipeline', pipeline)
    with pytest.raises(flow.OwnerContinuationError): c.run()
    assert c.client.hget(c.state_key, 'paused_reason') == 'previous_render_failed'
    assert not c.client.exists(c.audit_key)


def test_lost_commit_response_replays_receipt_without_mutation(case, monkeypatch):
    c = case; original_pipeline = c.client.pipeline; calls = 0
    def pipeline(*args, **kwargs):
        nonlocal calls
        pipe = original_pipeline(*args, **kwargs); calls += 1
        if calls == 2:
            execute = pipe.execute
            def lost():
                execute(); raise RuntimeError('Lost reply')
            pipe.execute = lost
        return pipe
    monkeypatch.setattr(c.client, 'pipeline', pipeline)
    with pytest.raises(flow.OwnerContinuationError): c.run()
    before = snapshot(c.client)
    assert c.client.exists(c.audit_key) and c.run()['status'] == 'already_continued'
    assert snapshot(c.client) == before


def test_conflicting_replay_request_never_reauthorizes(case):
    c = case; c.run(); before = snapshot(c.client)
    c.request['owner_reason'] = 'A different owner command cannot replace the existing audit record.'
    with pytest.raises(flow.OwnerContinuationError): c.run()
    assert snapshot(c.client) == before


def test_real_normal_dispatch_reserves_next_topic_without_repausing_old_failure(case, monkeypatch):
    from app.services import channel_production as production
    c = case
    monkeypatch.setattr(production, '_redis', lambda: c.client)
    assert production.reserve_due_production(c.profile, c.channel, now=NOW)['status'] == 'paused'
    old_jobs = {task: c.client.dump(flow.jobs.JOB_PREFIX + task) for task in (c.root, c.leaf)}
    old_paid = {task: c.client.dump(flow.jobs.PAID_CREATE_BUDGET_PREFIX + task) for task in (c.root, c.leaf)}
    assert c.run()['status'] == 'continued'
    assert c.client.hget(c.state_key, 'last_result') == 'FAILURE'
    enqueue = Mock()
    result = production.dispatch_due_productions([c.profile], [c.channel], enqueue, now=NOW + 1)
    assert result['status'] == 'queued' and result['queued_count'] == 1
    task = result['task_id']
    assert task not in {c.root, c.leaf}
    enqueue.assert_called_once()
    assert enqueue.call_args.kwargs['task_id'] == task
    fresh = json.loads(c.client.get(flow.jobs.JOB_PREFIX + task))
    spec = fresh['spec']
    assert fresh['state'] == 'PENDING' and fresh['parent_id'] is None and fresh['result'] is None
    assert spec['production_topic_index'] == 2
    assert spec['topic'].startswith('LEGO rescue plan\n\n')
    assert spec['production_profile_revision'] == c.current
    assert spec['production_connection_id'] == c.connection
    assert spec['publish_after_render'] is True and spec['production_scheduled'] is True
    assert spec['quality_threshold'] == 86 and spec['workflow'] == 'auto'
    assert not any(key in fresh for key in ('audio_candidate_checkpoint', 'generated_asset_candidates', 'owner_cancellation'))
    assert not c.client.exists(flow.jobs.PAID_CREATE_BUDGET_PREFIX + task)
    assert c.client.hget(c.state_key, 'cursor') == '3'
    assert c.client.hget(c.state_key, 'last_task_id') == c.client.hget(c.state_key, 'active_task_id') == task
    assert c.client.hget(c.state_key, 'profile_revision') == c.current
    assert c.client.hget(c.state_key, 'paused_reason') is None
    assert production.reconcile_active_production(now=NOW + 2) == 'active'
    assert c.client.hget(c.state_key, 'paused_reason') is None
    # Old last_result FAILURE cannot repause the new PENDING active claim.
    assert c.client.hget(c.state_key, 'last_result') == 'FAILURE'
    assert all(c.client.dump(flow.jobs.JOB_PREFIX + t) == raw for t, raw in old_jobs.items())
    assert all(c.client.dump(flow.jobs.PAID_CREATE_BUDGET_PREFIX + t) == raw for t, raw in old_paid.items())
    assert c.client.get(flow.SERIES_COUNTER_PREFIX + CHANNEL + ':' + c.profile['series_id']) == '2'
    again = production.dispatch_due_productions([c.profile], [c.channel], Mock(), now=NOW + 3)
    assert again['status'] == 'active' and not again.get('queued')


def test_eleven_job_chain_preserves_every_historical_claim(case):
    c = case
    original_root = json.loads(c.client.get(flow.jobs.JOB_PREFIX + c.root))
    child_id = c.root
    # Prepend nine genuine claimed failures without changing the cancellation's
    # existing immediate parent or receipt. Total is ten failures + one cancel.
    for index in range(9):
        parent_id = str(uuid4()); token = ('claim_' + str(index) + '_') * 5
        child = json.loads(c.client.get(flow.jobs.JOB_PREFIX + child_id))
        child['parent_id'] = parent_id
        c.client.set(flow.jobs.JOB_PREFIX + child_id, json.dumps(child), keepttl=True)
        parent = {**deepcopy(original_root), 'task_id': parent_id, 'parent_id': None,
                  'retry_child_task_id': child_id}
        c.client.set(flow.jobs.JOB_PREFIX + parent_id, json.dumps(parent))
        c.client.zadd(flow.jobs.JOB_INDEX, {parent_id: 1000})
        c.client.hset(flow.jobs.RETRY_DISPATCH_PREFIX + parent_id, mapping={
            'child_task_id': child_id, 'token': token, 'state': 'dispatched', 'mode': 'full'})
        c.client.hset(flow.jobs.RETRY_CHILD_CLAIM_PREFIX + child_id, mapping={'source_task_id': parent_id, 'token': token})
        c.client.set(flow.jobs.RETRY_CHILD_EXECUTION_PREFIX + child_id, token)
        c.client.hset(flow.jobs.PAID_CREATE_BUDGET_PREFIX + parent_id, mapping={'cap': 6, 'used': index % 6})
        child_id = parent_id
    c.request['root_task_id'] = child_id
    c.client.hset(c.state_key, 'last_task_id', child_id)
    before = snapshot(c.client)
    assert c.run()['status'] == 'continued'
    new_audit = flow.PREFIX + CHANNEL + ':' + child_id
    record = json.loads(c.client.get(new_audit))
    assert len(record['proof']['chain']) == 11 and record['proof']['chain'][-1] == c.leaf
    assert all(c.client.dump(k) == v for k, v in before.items() if k != c.state_key)


def test_concurrent_second_owner_wins_once_and_first_cannot_duplicate_commit(case):
    c = case; active = False
    def race():
        nonlocal active
        if not active:
            active = True
            assert c.run()['status'] == 'continued'
        return {'celery@worker': []}
    c.inspector.active.side_effect = race
    with pytest.raises(flow.OwnerContinuationError): c.run()
    before = snapshot(c.client)
    assert c.run()['status'] == 'already_continued'
    assert snapshot(c.client) == before


@pytest.fixture
def route(case, monkeypatch):
    tree = ast.parse((Path(__file__).parents[1] / 'app/youtube_routes.py').read_text(encoding='utf-8'))
    functions = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'youtube_continue_after_owner_cancellation']
    def auth(token):
        if token != 'owner': raise HTTPException(401)
    def origin(request):
        if request.headers.get('origin') != 'https://studio.test': raise HTTPException(403)
    namespace = {'router': APIRouter(), 'Cookie': Cookie, 'COOKIE_NAME': 'youtube_studio_token',
        'Request': Request, 'HTTPException': HTTPException, '_require_auth': auth, '_require_same_origin': origin}
    exec(compile(ast.Module(body=functions, type_ignores=[]), '<owner-route>', 'exec'), namespace)
    service = Mock(return_value={'status': 'continued'})
    monkeypatch.setattr(flow, 'continue_after_owner_cancellation', service)
    app = FastAPI(); app.include_router(namespace['router'])
    return TestClient(app), service


@pytest.mark.parametrize('owner,origin,status', [(False, True, 401), (True, False, 403), (True, True, 200)])
def test_route_requires_owner_and_same_origin(case, route, owner, origin, status):
    client, service = route; headers = {}
    if owner: headers['Cookie'] = 'youtube_studio_token=owner'
    if origin: headers['Origin'] = 'https://studio.test'
    response = client.post('/studio/youtube/continue-after-owner-cancellation/' + CHANNEL, json=case.request, headers=headers)
    assert response.status_code == status
    assert service.call_count == int(status == 200)


@pytest.mark.parametrize('body,content_type,status', [('x' * 8193, 'application/json', 413),
    ('{}', 'text/plain', 415), ('{}', 'application/json', 422)])
def test_route_rejects_oversize_wrong_type_and_invalid_schema(case, route, body, content_type, status):
    client, service = route
    response = client.post('/studio/youtube/continue-after-owner-cancellation/' + CHANNEL, content=body,
        headers={'Cookie': 'youtube_studio_token=owner', 'Origin': 'https://studio.test', 'Content-Type': content_type})
    assert response.status_code == status; service.assert_not_called()
