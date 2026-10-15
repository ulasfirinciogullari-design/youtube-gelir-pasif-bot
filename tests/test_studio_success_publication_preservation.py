from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import fakeredis
import pytest


ROOT = Path(__file__).resolve().parents[1]
TASK = '44444444-4444-4444-8444-444444444444'
PUBLISH = '55555555-5555-4555-8555-555555555555'
OTHER = '66666666-6666-4666-8666-666666666666'


@pytest.fixture
def state():
    path = ROOT / 'app/services/studio_state.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    tree.body = [node for node in tree.body if not (
        isinstance(node, ast.ImportFrom) and (node.module or '').startswith('app.')
    )]
    ns = {'settings': SimpleNamespace(redis_url='redis://unused')}
    exec(compile(tree, str(path), 'exec'), ns)
    client = fakeredis.FakeRedis(decode_responses=True)
    ns['_client'] = lambda: client
    return SimpleNamespace(**ns), client


def seed(module, client, *, kind='render', state='SUCCESS', publication=True):
    result = {'task_id': TASK, 'video_key': 'opaque-final', 'quality_disposition': 'automated_qc_pass',
              'manual_qa_required': False, 'nested': {'empty': [], 'values': [1, 2]}}
    job = {'task_id': TASK, 'kind': kind, 'state': state, 'stage': 'complete',
           'progress': 100, 'message': 'Video hazır.', 'error': None,
           'created_ts': 100, 'created_at': 'original', 'updated_at': 'unchanged',
           'spec': {'mode': 'production', 'publish_after_render': True},
           'parent_id': OTHER, 'paid_create_slots_used': 0,
           'qa_workprint': {'diagnostic': True}, 'result': deepcopy(result)}
    if publication:
        job['result']['youtube_automation'] = {'status': 'queued', 'publish_task_id': PUBLISH}
        job['result']['youtube'] = {'video_id': 'ExactVideo01', 'privacy_status': 'private',
                                   'release_status': 'private'}
    if kind == 'publish':
        job['spec']['source_task_id'] = OTHER
        result['source_task_id'] = OTHER
        job['result']['source_task_id'] = OTHER
    client.set(module.JOB_PREFIX + TASK, json.dumps(job))
    client.hset(module.PAID_CREATE_BUDGET_PREFIX + TASK, mapping={'used': '0', 'cap': '4'})
    return job, result


def sync_function(module, result, *, celery_state='SUCCESS', info=None):
    path = ROOT / 'app/studio.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    tree.body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == '_sync_job']
    success = Mock(wraps=module.mark_success)
    ns = {'get_job': module.get_job, 'mark_success': success, 'mark_failure': Mock(),
          'update_job': module.update_job, 'celery': object(),
          '_job_progress': lambda record: int(record.get('progress') or 0),
          'AsyncResult': lambda *_a, **_k: SimpleNamespace(state=celery_state, result=result, info=info)}
    exec(compile(tree, str(path), 'exec'), ns)
    return ns['_sync_job'], success


def test_normal_success_poll_keeps_publication_and_does_not_refresh_activity(state):
    module, client = state
    job, cached = seed(module, client)
    before = client.get(module.JOB_PREFIX + TASK)
    sync, success = sync_function(module, cached)
    assert sync(TASK) == job
    success.assert_not_called()
    assert client.get(module.JOB_PREFIX + TASK) == before


@pytest.mark.parametrize('persisted,attempt,backend', [
    ('FAILURE', 0, 'SUCCESS'), ('FAILURE', 1, 'SUCCESS'),
    ('PROGRESS', 2, 'SUCCESS'), ('SUCCESS', 5, 'SUCCESS'),
    ('PROGRESS', 2, 'FAILURE'), ('SUCCESS', 5, 'FAILURE'),
])
def test_framecase_source_is_not_overwritten_by_original_stopped_delivery(state, persisted, attempt, backend):
    module, client = state
    job, _ = seed(module, client, state=persisted)
    job['spec']['framecase_animation'] = True
    job['framecase_resume_attempt'] = attempt
    if persisted != 'SUCCESS':
        job.update(stage='failed' if persisted == 'FAILURE' else 'voice', progress=20,
                   framecase_failure_code='credit_cross_mode_request_conflict')
        job.pop('result')
    client.set(module.JOB_PREFIX + TASK, json.dumps(job))
    before = client.get(module.JOB_PREFIX + TASK)
    sync, success = sync_function(module, {'status': 'stopped', 'task_id': TASK,
        'reason': 'credit_cross_mode_request_conflict'}, celery_state=backend)
    assert sync(TASK) == job
    success.assert_not_called()
    sync.__globals__['mark_failure'].assert_not_called()
    assert client.get(module.JOB_PREFIX + TASK) == before


def test_framecase_first_delivery_hard_failure_is_still_observed(state):
    module, client = state
    job, _ = seed(module, client, state='PROGRESS', publication=False)
    job['spec']['framecase_animation'] = True
    client.set(module.JOB_PREFIX + TASK, json.dumps(job))
    sync, _ = sync_function(module, 'actual worker failure', celery_state='FAILURE')
    sync(TASK)
    sync.__globals__['mark_failure'].assert_called_once_with(TASK, 'actual worker failure')


def test_success_sync_after_stale_progress_preserves_publisher_fields_and_frozen_job(state):
    module, client = state
    job, cached = seed(module, client, state='PROGRESS')
    before_cached = deepcopy(cached)
    sync, success = sync_function(module, cached)
    actual = sync(TASK)
    success.assert_called_once()
    assert actual['state'] == 'SUCCESS'
    assert actual['result'] == job['result']
    assert cached == before_cached
    for field in ('spec', 'parent_id', 'created_ts', 'created_at', 'paid_create_slots_used', 'qa_workprint'):
        assert actual[field] == job[field]
    assert client.hgetall(module.PAID_CREATE_BUDGET_PREFIX + TASK) == {'used': '0', 'cap': '4'}
    assert actual['result']['nested']['empty'] == []


@pytest.mark.parametrize('terminal', ['SUCCESS', 'AWAITING_APPROVAL'])
@pytest.mark.parametrize('cached_state', ['PROGRESS', 'STARTED', 'PENDING', 'RETRY'])
def test_stale_nonterminal_cache_cannot_downgrade_persisted_completion(state, terminal, cached_state):
    module, client = state
    job, cached = seed(module, client, state=terminal, publication=False)
    before = client.get(module.JOB_PREFIX + TASK)
    sync, success = sync_function(module, cached, celery_state=cached_state,
                                  info={'stage': 'rendering', 'progress': 99, 'message': 'prior progress'})
    assert sync(TASK) == job
    success.assert_not_called()
    assert client.get(module.JOB_PREFIX + TASK) == before
    # This is the real mark_success -> automatic route -> Celery SUCCESS window.
    if terminal == 'SUCCESS':
        assert module.get_job(TASK)['state'] == 'SUCCESS'
        assert module.get_job(TASK)['result']['quality_disposition'] == 'automated_qc_pass'


def test_real_celery_failure_is_not_hidden_by_terminal_success_guard(state):
    module, client = state
    _, cached = seed(module, client)
    sync, _ = sync_function(module, cached, celery_state='FAILURE')
    failure = Mock(return_value={'state': 'FAILURE'})
    sync.__globals__.update(mark_failure=failure, sync_repair_checkpoint_state=lambda _id: {})
    sync(TASK)
    failure.assert_called_once()


def test_success_and_publication_after_initial_progress_read_cannot_be_overwritten(state):
    module, client = state
    _, cached = seed(module, client, state='PROGRESS', publication=False)
    sync, _ = sync_function(module, cached, celery_state='PROGRESS',
                            info={'stage': 'rendering', 'progress': 99})
    original_get = module.get_job

    def initial_read_then_complete(task_id):
        snapshot = original_get(task_id)
        module.mark_success(task_id, cached)
        module.merge_youtube_result_field(task_id, 'youtube_automation',
                                          {'status': 'queued', 'publish_task_id': PUBLISH})
        return snapshot

    sync.__globals__['get_job'] = initial_read_then_complete
    progress_write = Mock(side_effect=AssertionError('Dashboard must not write stale progress'))
    sync.__globals__['update_job'] = progress_write
    sync(TASK)
    progress_write.assert_not_called()
    stored = module.get_job(TASK)
    assert stored['state'] == 'SUCCESS'
    assert stored['result']['youtube_automation']['publish_task_id'] == PUBLISH


@pytest.mark.parametrize('field,value', [
    ('youtube', {'video_id': 'ExactVideo01', 'privacy_status': 'private'}),
    ('youtube_automation', {'status': 'queued', 'publish_task_id': PUBLISH}),
])
def test_publisher_update_between_watch_and_write_is_not_lost(state, monkeypatch, field, value):
    module, client = state
    _, cached = seed(module, client, publication=False)
    original_pipeline = client.pipeline
    raced = False

    def pipeline(*args, **kwargs):
        pipe = original_pipeline(*args, **kwargs)
        original_execute = pipe.execute

        def execute(*args, **kwargs):
            nonlocal raced
            if not raced:
                raced = True
                module.merge_youtube_result_field(TASK, field, value)
            return original_execute(*args, **kwargs)

        pipe.execute = execute
        return pipe

    monkeypatch.setattr(client, 'pipeline', pipeline)
    actual = module.mark_success(TASK, cached)
    assert raced
    assert actual['result'][field] == value
    assert module.get_job(TASK)['result'][field] == value


def test_concurrent_completions_and_narrow_publish_writes_preserve_all_publication_fields(state):
    module, client = state
    _, cached = seed(module, client, publication=False)

    def update(index):
        module.mark_success(TASK, cached)
        module.merge_youtube_result_field(TASK, 'youtube', {f'proof_{index}': index})
        module.merge_youtube_result_field(TASK, 'youtube_automation', {f'proof_{index}': index})

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(update, range(24)))
    module.mark_success(TASK, cached)
    actual = module.get_job(TASK)['result']
    expected = {f'proof_{index}': index for index in range(24)}
    assert actual['youtube'] == expected
    assert actual['youtube_automation'] == expected


@pytest.mark.parametrize('kind,field,value', [
    ('render', 'task_id', OTHER),
    ('render', 'source_task_id', OTHER),
    ('publish', 'source_task_id', TASK),
    ('publish', 'task_id', OTHER),
])
def test_foreign_cached_result_cannot_overwrite_job_or_publication(state, kind, field, value):
    module, client = state
    _, cached = seed(module, client, kind=kind)
    cached[field] = value
    before = client.get(module.JOB_PREFIX + TASK)
    sync, success = sync_function(module, cached)
    sync(TASK)
    success.assert_not_called()
    with pytest.raises(ValueError, match='does not match task'):
        module.mark_success(TASK, cached)
    assert client.get(module.JOB_PREFIX + TASK) == before


def test_valid_publish_completion_retains_source_binding(state):
    module, client = state
    _, cached = seed(module, client, kind='publish', state='PROGRESS', publication=False)
    cached.update(status='complete', youtube_video_id='ExactVideo01', privacy_status='private')
    sync, _ = sync_function(module, cached)
    actual = sync(TASK)
    assert actual['result'] == cached
    assert actual['parent_id'] == OTHER


@pytest.mark.parametrize('exists', [True, False])
def test_render_cache_cannot_invent_publisher_owned_fields(state, exists):
    module, client = state
    _, cached = seed(module, client, publication=False)
    if not exists:
        client.delete(module.JOB_PREFIX + TASK)
    cached.update(youtube={'video_id': 'InjectedVideo'},
                  youtube_automation={'status': 'complete', 'publish_task_id': OTHER})
    actual = module.mark_success(TASK, cached)
    assert 'youtube' not in actual['result']
    assert 'youtube_automation' not in actual['result']


def test_publisher_cannot_complete_against_mismatched_persisted_parent(state):
    module, client = state
    job, cached = seed(module, client, kind='publish')
    job['parent_id'] = PUBLISH
    client.set(module.JOB_PREFIX + TASK, json.dumps(job))
    before = client.get(module.JOB_PREFIX + TASK)
    with pytest.raises(ValueError, match='source does not match task'):
        module.mark_success(TASK, cached)
    assert client.get(module.JOB_PREFIX + TASK) == before


def test_repeated_cas_conflict_has_no_unguarded_fallback(state, monkeypatch):
    module, client = state
    _, cached = seed(module, client)
    before = client.get(module.JOB_PREFIX + TASK)
    original_pipeline = client.pipeline
    attempts = []

    def pipeline(*args, **kwargs):
        pipe = original_pipeline(*args, **kwargs)

        def execute(*_a, **_k):
            attempts.append(True)
            raise module.redis.WatchError('test conflict')

        pipe.execute = execute
        return pipe

    monkeypatch.setattr(client, 'pipeline', pipeline)
    actual = module.mark_success(TASK, cached)
    assert len(attempts) == 5
    assert client.get(module.JOB_PREFIX + TASK) == before
    assert actual == json.loads(before)


def test_registry_outage_fallback_cannot_claim_publisher_fields(state, monkeypatch):
    module, _client = state
    incoming = {'task_id': TASK, 'video_key': 'opaque-final',
                'youtube': {'video_id': 'InjectedVideo'},
                'youtube_automation': {'status': 'complete', 'publish_task_id': OTHER}}
    failing_client = Mock(side_effect=ConnectionError('unavailable'))
    monkeypatch.setitem(module.mark_success.__globals__, '_client', failing_client)
    result = module.mark_success(TASK, incoming)
    assert result['kind'] == 'render'
    assert 'youtube' not in result['result']
    assert 'youtube_automation' not in result['result']
    assert result['result']['video_key'] == 'opaque-final'
