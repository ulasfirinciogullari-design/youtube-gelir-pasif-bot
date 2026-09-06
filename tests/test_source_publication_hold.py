"""Real fake-Redis CAS and old-dispatcher hold compatibility; no network."""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import fakeredis
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from app import publication_hold_routes as routes
from app.external_routes import settings
from app.services import source_publication_hold as hold, youtube_publish_state as uploads


@pytest.fixture
def case(monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(hold.studio_state, '_client', lambda: client)
    monkeypatch.setattr(uploads, '_redis', lambda: client)
    task, channel, revision, connection = str(uuid4()), 'UC_hold_source_only', 'revision-hold-test', 'connection-hold-test'
    spec = {'mode': 'production', 'topic': 'Costco membership', 'language': 'en',
            'production_channel_id': channel, 'production_profile_revision': revision,
            'production_connection_id': connection, 'publish_after_render': True}
    job = {'task_id': task, 'kind': 'render', 'state': 'PROGRESS', 'stage': 'ai_scene_generation',
           'progress': 64, 'updated_at': '2026-09-06T17:25:00+00:00', 'spec': spec,
           'result': None, 'generated_asset_candidates': [{'existing': 'retained'}]}
    key = hold.studio_state.JOB_PREFIX + task
    client.set(key, json.dumps(job), ex=3600)
    client.zadd(hold.studio_state.JOB_INDEX, {task: 1000})
    profile = {'channel_id': channel, 'profile_revision': revision, 'series_epoch': 3,
               'auto_publish': True, 'production_enabled': True, 'release_mode': 'public',
               'default_language': 'en', 'languages': ['en']}
    client.set(hold.PROFILE_PREFIX + channel, json.dumps(profile))
    client.set(hold.CHANNEL_PREFIX + channel, json.dumps({'id': channel, 'connection_id': connection}))
    client.hset(hold.studio_state.PAID_CREATE_BUDGET_PREFIX + task, mapping={'cap': 6, 'used': 6})
    client.set(hold.studio_state.RETRY_CHILD_EXECUTION_PREFIX + task, 'retained-execution-proof')
    request = {'expected_channel_id': channel, 'expected_profile_revision': revision,
               'reason': 'Owner rejected this topic; preserve paid output but do not publish.'}
    return SimpleNamespace(client=client, task=task, key=key, job=job, profile=profile, request=request,
        channel=channel, revision=revision, connection=connection,
        run=lambda: hold.hold_source_publication(task, **request, now=2000))


def snapshot(client):
    return {key: client.dump(key) for key in client.scan_iter()}


def test_hold_changes_only_own_spec_receipt_reference_and_new_nonpublication_fence(case):
    c = case
    before, ttl = snapshot(c.client), c.client.pttl(c.key)
    assert c.run()['status'] == 'held'
    job = json.loads(c.client.get(c.key))
    assert job['spec']['publish_after_render'] is False
    assert {k: v for k, v in job.items() if k not in {'spec', 'publication_hold'}} == {k: v for k, v in c.job.items() if k != 'spec'}
    assert {**job['spec'], 'publish_after_render': True} == c.job['spec']
    assert ttl - 2000 <= c.client.pttl(c.key) <= ttl
    assert all(c.client.dump(key) == value for key, value in before.items() if key != c.key)
    assert set(snapshot(c.client)) - set(before) == {hold.HOLD_PREFIX + c.task, uploads.UPLOAD_PREFIX + c.task}
    fence = uploads.get_upload_record(c.task)
    assert fence['status'] == 'held_by_owner' and fence['side_effect_possible'] is False
    assert fence['release_side_effect_possible'] is False
    assert not set(fence) & {'publish_task_id', 'youtube_video_id', 'video_id', 'publish_plan', 'qa_approved'}
    assert c.client.ttl(hold.HOLD_PREFIX + c.task) == c.client.ttl(uploads.UPLOAD_PREFIX + c.task) == -1


def test_real_old_reserve_upload_cannot_overwrite_hold_even_after_stale_spec_restoration(case):
    c = case
    c.run()
    fence = c.client.get(uploads.UPLOAD_PREFIX + c.task)
    c.client.set(c.key, json.dumps(c.job), keepttl=True)  # actual old progress/redelivery snapshot race
    record, created = uploads.reserve_upload(c.task, str(uuid4()), target_channel_id=c.channel,
                                            connection_id=c.connection)
    assert created is False and record['status'] == 'held_by_owner'
    assert c.client.get(uploads.UPLOAD_PREFIX + c.task) == fence
    assert c.run()['status'] == 'already_held'  # historical receipt does not overwrite the job again
    assert json.loads(c.client.get(c.key))['spec']['publish_after_render'] is True


def test_real_old_automatic_queue_cannot_create_publisher_after_stale_worker_completion(case, monkeypatch):
    from test_youtube_lifecycle import _import_publish_tasks_with_stubs
    c = case
    c.run()
    restored = {**c.job, 'state': 'SUCCESS', 'result': {
        'video_key': 'videos/retained/final.mp4', 'caption_key': 'videos/retained/captions.en.srt',
        'publish_metadata': {'title': 'Costco membership',
                             'description': 'Source-backed membership explanation.', 'tags': [], 'hashtags': []},
        'title': 'Costco membership', 'quality_disposition': 'automated_qc_pass', 'manual_qa_required': False}}
    c.client.set(c.key, json.dumps(restored), keepttl=True)
    module = _import_publish_tasks_with_stubs(monkeypatch)
    monkeypatch.setattr(module, 'get_job', lambda task: json.loads(c.client.get(hold.studio_state.JOB_PREFIX + task)))
    monkeypatch.setattr(module, 'connection_status', lambda: {'connections': [{'id': c.channel, 'connection_id': c.connection}]})
    monkeypatch.setattr(module, 'list_channel_profiles', lambda: [c.profile])
    create, enqueue = Mock(), Mock()
    monkeypatch.setattr(module, 'create_job', create)
    monkeypatch.setattr(module, 'publish_video_pipeline', SimpleNamespace(apply_async=enqueue))
    outcome = module.queue_automatic_publish(c.task)
    assert outcome == {'status': 'held_by_owner', 'publish_task_id': None}
    create.assert_not_called(); enqueue.assert_not_called()


def test_actual_mark_success_preserves_current_held_spec_and_private_reference(case):
    c = case
    c.run()
    before = json.loads(c.client.get(c.key))
    hold.studio_state.mark_success(c.task, {'task_id': c.task, 'video_key': 'videos/retained/final.mp4'})
    after = json.loads(c.client.get(c.key))
    assert after['state'] == 'SUCCESS' and after['spec'] == before['spec']
    assert after['publication_hold'] == before['publication_hold']


@pytest.mark.parametrize('status', ['reserved', 'queued', 'uploading', 'uncertain', 'complete', 'failed_preflight'])
def test_any_existing_upload_record_is_never_overwritten(case, status):
    case.client.set(uploads.UPLOAD_PREFIX + case.task, json.dumps({'status': status}))
    before = snapshot(case.client)
    with pytest.raises(hold.SourcePublicationHoldError): case.run()
    assert snapshot(case.client) == before


def test_existing_linked_publisher_without_registry_is_rejected(case):
    publisher = str(uuid4())
    case.client.set(hold.studio_state.JOB_PREFIX + publisher, json.dumps({
        'task_id': publisher, 'kind': 'publish', 'parent_id': case.task, 'state': 'PENDING'}))
    case.client.zadd(hold.studio_state.JOB_INDEX, {publisher: 1000})
    before = snapshot(case.client)
    with pytest.raises(hold.SourcePublicationHoldError): case.run()
    assert snapshot(case.client) == before


@pytest.mark.parametrize('damage', ['lock', 'wrong_revision', 'wrong_channel', 'wrong_connection',
                                   'already_disabled', 'existing_attribution', 'linked_publish_id', 'kind'])
def test_changed_authority_or_existing_publication_rejects_without_writes(case, damage):
    c = case
    job = deepcopy(c.job)
    if damage == 'lock': c.client.set(uploads.EXECUTION_LOCK_PREFIX + c.task, 'owned-lock')
    elif damage == 'wrong_revision': job['spec']['production_profile_revision'] = 'different'
    elif damage == 'wrong_channel': job['spec']['production_channel_id'] = 'UC_other_channel'
    elif damage == 'wrong_connection': job['spec']['production_connection_id'] = 'other-connection'
    elif damage == 'already_disabled': job['spec']['publish_after_render'] = False
    elif damage == 'existing_attribution': job['result'] = {'youtube': {'video_id': 'existing'}}
    elif damage == 'linked_publish_id': job['result'] = {'youtube_automation': {'publish_task_id': str(uuid4())}}
    elif damage == 'kind': job['kind'] = 'publish'
    c.client.set(c.key, json.dumps(job), keepttl=True)
    before = snapshot(c.client)
    with pytest.raises(hold.SourcePublicationHoldError): c.run()
    assert snapshot(c.client) == before


@pytest.mark.parametrize('race', ['publisher', 'lost_reply'])
def test_reservation_winning_cas_and_lost_hold_commit_are_safe(case, monkeypatch, race):
    c = case
    original = c.client.pipeline
    def pipeline(*args, **kwargs):
        pipe = original(*args, **kwargs)
        execute = pipe.execute
        def raced():
            if race == 'publisher':
                c.client.set(uploads.UPLOAD_PREFIX + c.task, json.dumps({'status': 'reserved', 'real': True}))
            result = execute()
            if race == 'lost_reply': raise TimeoutError('mocked lost EXEC reply')
            return result
        pipe.execute = raced
        return pipe
    monkeypatch.setattr(c.client, 'pipeline', pipeline)
    with pytest.raises(hold.SourcePublicationHoldError): c.run()
    monkeypatch.setattr(c.client, 'pipeline', original)
    if race == 'publisher':
        assert json.loads(c.client.get(c.key)) == c.job and not c.client.exists(hold.HOLD_PREFIX + c.task)
    else:
        assert c.run()['status'] == 'already_held'
        assert uploads.get_upload_record(c.task)['status'] == 'held_by_owner'


@pytest.fixture
def api(case, monkeypatch):
    monkeypatch.setattr(settings, 'factory_api_token', 'hold-test-owner', raising=False)
    app = FastAPI(); app.include_router(routes.router)
    return TestClient(app), case


def post(api, **kwargs):
    client, c = api
    kwargs.setdefault('headers', {'X-Factory-Token': 'hold-test-owner', 'Content-Type': 'application/json'})
    if 'content' not in kwargs: kwargs.setdefault('json', c.request)
    return client.post('/studio/api/job/' + c.task + '/hold-publication', **kwargs)


def test_owner_route_returns_only_compact_hold_identifiers(api):
    response = post(api)
    assert response.status_code == 200
    assert response.json() == {'status': 'held', 'task_id': api[1].task,
                              'channel_id': api[1].channel, 'profile_revision': api[1].revision}
    assert post(api).json()['status'] == 'already_held'
    assert 'reason' not in response.text and 'receipt' not in response.text


def test_auth_runs_before_path_headers_or_body(api):
    class NeverRead:
        @property
        def headers(self): raise AssertionError('Must authenticate first')
    with pytest.raises(HTTPException) as error:
        asyncio.run(routes.hold_publication('not-uuid', NeverRead(), None))
    assert error.value.status_code == 401 and not api[1].client.exists(hold.HOLD_PREFIX + api[1].task)


@pytest.mark.parametrize('extra', [{'publish_after_render': False}, {'now': 1}, {'qa_approved': True},
                                  {'reason': 'short'}, {'reason': 'x' * 801}, {'reason': 'secret=not-for-response'}])
def test_route_rejects_arbitrary_job_editing_and_unsafe_reason(api, extra):
    response = post(api, json={**api[1].request, **extra})
    assert response.status_code == 422 and 'not-for-response' not in response.text
    assert not api[1].client.exists(hold.HOLD_PREFIX + api[1].task)


def test_route_body_limit_and_safe_service_failure(api, monkeypatch):
    monkeypatch.setattr(routes, 'MAX_HOLD_REQUEST_BYTES', 20)
    assert post(api, content=b' ' * 21).status_code == 413
    monkeypatch.setattr(routes, 'MAX_HOLD_REQUEST_BYTES', 8192)
    api[1].client.set(uploads.EXECUTION_LOCK_PREFIX + api[1].task, 'lock')
    assert post(api).json() == {'detail': 'publication_hold_not_eligible'}
