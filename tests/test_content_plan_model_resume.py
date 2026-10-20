from copy import deepcopy
import hashlib
import json
from unittest.mock import Mock

from cryptography.fernet import Fernet
import pytest

from app.services import content_plan as plan, content_plan_research_resume as pre
from app.services import content_plan_model_resume as resume, content_plan_recovery as recovery, studio_state as jobs
from app.services import commissioning_reasoning as native, production_included_router as included
from test_content_plan import case
from test_content_plan_research_resume import failed


def rejected(case, monkeypatch):
    from app import tasks
    root = failed(case, monkeypatch, long=True); monkeypatch.setattr(jobs, '_client', lambda: case.client)
    monkeypatch.setattr(tasks.run_video_pipeline, 'apply_async', Mock())
    pre.schedule(root, Mock()); result = pre.run(root['task_id'], pre.operation(root['task_id']))
    task = result['task_id']; assert jobs.acquire_retry_child_execution(task, root['task_id'])
    source = jobs.get_job(task)
    source.update(state='FAILURE', failure_stage='director_qc', error=resume.ERROR,
                  paid_create_slots_used=0, preview_total_paid_create_cap=32)
    case.client.set(jobs.JOB_PREFIX + task, plan._raw(source))
    case.client.hset(jobs.PAID_CREATE_BUDGET_PREFIX + task, mapping={'cap': '32', 'used': '0'})
    case.client.set('celery-task-meta-' + task, plan._raw({'task_id': task, 'status': 'FAILURE',
        'result': {'exc_type': 'SpendBlocked', 'exc_message': [resume.ERROR]},
        'traceback': 'File "/app/app/services/director.py", line 2205, in _run_director\n'}))
    cipher = Fernet(Fernet.generate_key()); monkeypatch.setattr(included, '_cipher', lambda: cipher)
    context = {'channel_id': source['spec']['production_channel_id'],
        'connection_id': source['spec']['production_connection_id'], 'lineage_id': root['task_id'], 'kind': 'long'}
    for purpose, code in [('research', 200), ('editorial', 400)]:
        identity = hashlib.sha256(purpose.encode()).hexdigest()
        request = {'version': 1, 'request_sha256': identity, 'context': context,
                   'provider': 'gemini', 'model': native.MODEL, 'purpose': purpose}
        body = ({'candidates': [{'finishReason': 'STOP'}], 'usageMetadata': {'totalTokenCount': 100}}
            if code == 200 else {'error': {'code': 400, 'status': 'INVALID_ARGUMENT'}})
        raw = plan._raw(body).encode()
        response = {'version': 1, 'request_sha256': identity, 'http_status': code,
            'response_sha256': hashlib.sha256(raw).hexdigest(), 'encrypted_response': cipher.encrypt(raw).decode()}
        case.client.set(native.PREFIX + 'request:' + identity, plan._raw(request))
        case.client.set(native.PREFIX + 'response:' + identity, plan._raw(response))
        case.client.sadd(native.PREFIX + 'lineage:' + root['task_id'], identity)
    return root, source


@pytest.mark.parametrize('damage', [None, 'unknown', 'changed_body', 'http500', 'another_request', 'voice', 'cancel', 'claim', 'scope'])
def test_only_bound_explicit_http400_can_dispatch_once_without_erasing_history(case, monkeypatch, damage):
    root, source = rejected(case, monkeypatch); task = source['task_id']
    key = native.PREFIX + 'response:' + hashlib.sha256(b'editorial').hexdigest()
    if damage == 'unknown': case.client.delete(key)
    if damage in {'changed_body', 'http500'}:
        response = json.loads(case.client.get(key))
        response['response_sha256' if damage == 'changed_body' else 'http_status'] = 'a' * 64 if damage == 'changed_body' else 500
        case.client.set(key, plan._raw(response))
    if damage == 'another_request': case.client.sadd(native.PREFIX + 'lineage:' + root['task_id'], 'unknown-third')
    if damage == 'voice': source['audio_candidate_checkpoint'] = {'already': 'generated'}
    if damage == 'cancel': case.client.set(jobs.RENDER_CANCELLATION_PREFIX + root['task_id'], 'stop')
    if damage == 'claim': case.client.delete(jobs.RETRY_CHILD_EXECUTION_PREFIX + task)
    if damage == 'scope': source['spec'] = {**source['spec'], 'duration_minutes': 4}
    case.client.set(jobs.JOB_PREFIX + task, plan._raw(source))
    before = {k: case.client.dump(k) for k in case.client.scan_iter()}; queue = Mock(side_effect=TimeoutError())
    if damage:
        with pytest.raises((plan.ContentPlanError, TypeError, ValueError)):
            resume.schedule(source, queue)
        queue.assert_not_called()
    else:
        assert resume.schedule(source, queue) == 'model_resume_uncertain'
        assert resume.schedule(source, queue) == 'model_resume_reserved'
        queue.assert_called_once()
    assert all(case.client.dump(k) == v for k, v in before.items())


def test_actual_grandchild_keeps_long_spec_and_ordinary_full_qa_entry(case, monkeypatch, tmp_path):
    from app import tasks
    root, source = rejected(case, monkeypatch); task = source['task_id']
    old_requests = {k: case.client.get(k) for k in case.client.scan_iter(match=native.PREFIX + '*')
                    if case.client.type(k) == 'string'}
    queue = Mock(); recovery.schedule(source, queue)
    outcome = recovery.run(task, resume.operation(task)); child = outcome['task_id']
    assert outcome['status'] == 'enqueued'
    assert recovery.run(task, resume.operation(task)) == {'status': 'already_started'}
    assert jobs.acquire_retry_child_execution(child, task)
    assert tasks._prepare_saved_voice_retry(child, task, source['spec'], tmp_path) is None
    assert tasks.run_video_pipeline.apply_async.call_args.kwargs['args'][1] == 3
    assert tasks.run_video_pipeline.apply_async.call_args.kwargs['args'][5:] == (None, task)
    assert all(case.client.get(k) == v for k, v in old_requests.items())
    # An ambiguous request made after admission invalidates the child entry.
    case.client.sadd(native.PREFIX + 'lineage:' + root['task_id'], 'unknown-third')
    with pytest.raises(plan.ContentPlanError): tasks._prepare_saved_voice_retry(child, task, source['spec'], tmp_path)
