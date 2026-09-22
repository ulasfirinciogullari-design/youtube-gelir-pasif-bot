import hashlib
import json
from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet

from app.services import content_plan as plan, studio_state as jobs
from app.services import content_plan_model_resume as model, content_plan_story_resume as resume
from app.services import content_plan_recovery as recovery, commissioning_reasoning as native
from app.services import production_included_router as included
from test_content_plan import case
from test_content_plan_model_resume import rejected


def criticised(case, monkeypatch, *, fresh=False):
    if fresh:
        from app import tasks
        from test_content_plan_research_resume import failed
        root = source = failed(case, monkeypatch, long=True); parent = None; task = source['task_id']
        monkeypatch.setattr(jobs, '_client', lambda: case.client)
        monkeypatch.setattr(tasks.run_video_pipeline, 'apply_async', Mock())
        cipher = Fernet(Fernet.generate_key()); monkeypatch.setattr(included, '_cipher', lambda: cipher)
    else:
        root, parent = rejected(case, monkeypatch)
        model.schedule(parent, Mock()); result = model.run(parent['task_id'], model.operation(parent['task_id']))
        task = result['task_id']; assert jobs.acquire_retry_child_execution(task, parent['task_id'])
        source = jobs.get_job(task)
    source.update(state='FAILURE', failure_stage='director_qc', error=resume.ERROR,
                  paid_create_slots_used=0, preview_total_paid_create_cap=32)
    case.client.set(jobs.JOB_PREFIX + task, plan._raw(source))
    case.client.hset(jobs.PAID_CREATE_BUDGET_PREFIX + task, mapping={'cap': '32', 'used': '0'})
    case.client.set('celery-task-meta-' + task, plan._raw({'task_id': task, 'status': 'FAILURE',
        'result': {'exc_type': 'ProductionContentError', 'exc_message': [resume.ERROR]},
        'traceback': 'File "/app/app/services/commissioning_longform.py", line 92, in review_story\n'}))
    context = {'channel_id': source['spec']['production_channel_id'],
        'connection_id': source['spec']['production_connection_id'], 'lineage_id': root['task_id'], 'kind': 'long'}
    for purpose in (('research', 'editorial_draft', 'editorial', 'story_review') if fresh else ('editorial', 'story_review')):
        identity = hashlib.sha256(('completed-' + purpose).encode()).hexdigest()
        request = {'version': 1, 'request_sha256': identity, 'context': context,
                   'provider': 'gemini', 'model': native.MODEL, 'purpose': purpose.replace('_draft', '')}
        critique = {'factual_audit': {'sentences': [{'assessment': 'uncertain'}] * 10}}
        body = {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': plan._raw(critique)}]}}],
                'usageMetadata': {'totalTokenCount': 100}}
        raw = plan._raw(body).encode()
        response = {'version': 1, 'request_sha256': identity, 'http_status': 200,
            'response_sha256': hashlib.sha256(raw).hexdigest(), 'encrypted_response': included._cipher().encrypt(raw).decode()}
        case.client.set(native.PREFIX + 'request:' + identity, plan._raw(request))
        case.client.set(native.PREFIX + 'response:' + identity, plan._raw(response))
        case.client.sadd(native.PREFIX + 'lineage:' + root['task_id'], identity)
    return root, parent, source


@pytest.mark.parametrize('damage', [None, 'unknown', 'additional_attempt', 'body_hash', 'voice', 'root_cancel', 'prior_claim', 'terminal'])
def test_only_completed_bound_pre_voice_critique_can_resume_once(case, monkeypatch, damage):
    root, parent, source = criticised(case, monkeypatch); task = source['task_id']
    key = native.PREFIX + 'response:' + hashlib.sha256(b'completed-story_review').hexdigest()
    if damage == 'unknown': case.client.delete(key)
    if damage == 'additional_attempt': case.client.sadd(native.PREFIX + 'lineage:' + root['task_id'], 'unknown-fifth')
    if damage == 'body_hash':
        value = json.loads(case.client.get(key)); value['response_sha256'] = 'a' * 64
        case.client.set(key, plan._raw(value))
    if damage == 'voice': source['audio_candidate_checkpoint'] = {'voice': 'exists'}
    if damage == 'root_cancel': case.client.set(jobs.RENDER_CANCELLATION_PREFIX + root['task_id'], 'owner stopped')
    if damage == 'prior_claim': case.client.delete(model.DISPATCH + parent['task_id'])
    if damage == 'terminal': case.client.delete('celery-task-meta-' + task)
    case.client.set(jobs.JOB_PREFIX + task, plan._raw(source))
    before = {k: case.client.dump(k) for k in case.client.scan_iter()}; queue = Mock(side_effect=TimeoutError())
    if damage:
        with pytest.raises((plan.ContentPlanError, TypeError, ValueError)):
            recovery.schedule(source, queue)
        queue.assert_not_called()
    else:
        assert recovery.schedule(source, queue) == 'story_resume_uncertain'
        assert recovery.schedule(source, queue) == 'story_resume_reserved'
        queue.assert_called_once()
    assert all(case.client.dump(k) == value for k, value in before.items())


@pytest.mark.parametrize('fresh', [True, False])
def test_child_runs_full_ordinary_long_pipeline_with_frozen_original_records(case, monkeypatch, tmp_path, fresh):
    from app import tasks
    root, parent, source = criticised(case, monkeypatch, fresh=fresh); task = source['task_id']
    old = {k: case.client.get(k) for k in case.client.scan_iter(match=native.PREFIX + '*')
           if case.client.type(k) == 'string'}
    recovery.schedule(source, Mock()); outcome = recovery.run(task, resume.operation(task)); child = outcome['task_id']
    assert outcome['status'] == 'enqueued'
    assert recovery.run(task, resume.operation(task)) == {'status': 'already_started'}
    assert jobs.acquire_retry_child_execution(child, task)
    assert tasks._prepare_saved_voice_retry(child, task, source['spec'], tmp_path) is None
    assert tasks.run_video_pipeline.apply_async.call_args.kwargs['args'][1] == 3
    assert tasks.run_video_pipeline.apply_async.call_args.kwargs['args'][5:] == (None, task)
    assert all(case.client.get(k) == value for k, value in old.items())
    case.client.sadd(native.PREFIX + 'lineage:' + root['task_id'], 'unknown-fifth')
    with pytest.raises(plan.ContentPlanError): tasks._prepare_saved_voice_retry(child, task, source['spec'], tmp_path)


@pytest.mark.parametrize('terminal_kind', ['watch', 'timeout', 'unknown'])
def test_completed_read_conflict_can_continue_once_without_erasing_the_original_claim(case, monkeypatch, terminal_kind):
    from redis.exceptions import WatchError
    from app.services import content_plan_research_resume as pre
    root, _, source = criticised(case, monkeypatch, fresh=True); task = source['task_id']; queue = Mock()
    recovery.schedule(source, queue)
    pre._provider_free.side_effect = WatchError('Watched variable changed.')
    with pytest.raises(WatchError): recovery.run(task, resume.operation(task))
    pre._provider_free.side_effect = None
    terminal = {'task_id': resume.operation(task), 'status': 'FAILURE',
        'result': {'exc_module': 'redis.exceptions', 'exc_type': 'WatchError',
                   'exc_message': ['Watched variable changed.']},
        'traceback': 'File "x", line 1, in run\nFile "x", line 2, in checked\n'
                     'File "x", line 3, in _provider_free\nFile "x", line 4, in _execute_transaction\n'}
    if terminal_kind == 'timeout': terminal['result']['exc_type'] = 'ReadTimeout'
    if terminal_kind != 'unknown': case.client.set('celery-task-meta-' + resume.operation(task), plan._raw(terminal))
    before = {k: case.client.dump(k) for k in case.client.scan_iter()}
    if terminal_kind == 'watch':
        assert recovery.schedule(source, queue) == 'story_resume_preparing'
        assert recovery.schedule(source, queue) == 'story_resume_reserved'
        assert queue.call_count == 2 and queue.call_args.kwargs['task_id'] == resume.validation_operation(task)
        assert all(case.client.dump(k) == value for k, value in before.items())
        result = recovery.run(task, resume.validation_operation(task)); child = result['task_id']
        assert jobs.acquire_retry_child_execution(child, task)
        resume.verify_child(child, task, source['spec'])
        assert recovery.run(task, resume.validation_operation(task)) == {'status': 'already_started'}
        assert case.client.get(resume.EXECUTION + task) == resume.operation(task)
    else:
        assert recovery.schedule(source, queue) == 'story_resume_reserved'
        queue.assert_called_once()
        assert all(case.client.dump(k) == value for k, value in before.items())
