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


def shortened_revision(case, monkeypatch):
    from test_content_plan import OTHER
    case.profile['default_language'] = 'en'
    case.client.set(plan.production.PROFILE_PREFIX + OTHER,
                    plan._raw({**case.profile, 'channel_id': OTHER}))
    root, _, _ = criticised(case, monkeypatch, fresh=True); root_id = root['task_id']
    for i, purpose in enumerate(('research', 'editorial_draft', 'editorial', 'story_review')):
        identity = hashlib.sha256(('completed-' + purpose).encode()).hexdigest()
        key = native.PREFIX + 'request:' + identity; request = json.loads(case.client.get(key))
        request['reserved_at'] = f'2026-09-22T12:19:0{i}+00:00'; case.client.set(key, plan._raw(request))
    recovery.schedule(root, Mock()); result = recovery.run(root_id, resume.operation(root_id))
    task = result['task_id']; assert jobs.acquire_retry_child_execution(task, root_id)
    source = jobs.get_job(task)
    source.update(state='FAILURE', failure_stage='director_qc', error=resume.CONTRACT_ERROR,
                  paid_create_slots_used=0, preview_total_paid_create_cap=32)
    case.client.set(jobs.JOB_PREFIX + task, plan._raw(source))
    case.client.hset(jobs.PAID_CREATE_BUDGET_PREFIX + task, mapping={'cap': '32', 'used': '0'})
    case.client.set('celery-task-meta-' + task, plan._raw({'task_id': task, 'status': 'FAILURE',
        'result': {'exc_type': 'ProductionContentError', 'exc_message': [resume.CONTRACT_ERROR]},
        'traceback': 'File "commissioning_longform.py", line 92, in review_story\n'
                     'File "longform_editorial_feedback.py", line 40, in revise\n'}))
    context = {'channel_id': source['spec']['production_channel_id'],
        'connection_id': source['spec']['production_connection_id'], 'lineage_id': root_id, 'kind': 'long'}
    latest = None
    for i, purpose in enumerate(('research', 'editorial', 'story_review', 'story_review', 'story_review', 'editorial')):
        identity = hashlib.sha256(f'feedback-{i}'.encode()).hexdigest(); latest = identity
        request = {'version': 1, 'request_sha256': identity, 'context': context,
            'provider': 'gemini', 'model': native.MODEL, 'purpose': purpose,
            'reserved_at': f'2026-09-22T12:52:0{i}+00:00'}
        value = {'factual_audit': {'sentences': [{'assessment': 'uncertain'}] * 10}}
        if purpose == 'editorial':
            value = {'scenes': [{'narration': ' '.join(['word'] * (11 if n < 23 else 10)),
                'ai_prompt': None, 'visual_queries': ['ordinary stock video']} for n in range(30)]}
        raw = plan._raw({'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': plan._raw(value)}]}}],
                        'usageMetadata': {'totalTokenCount': 100}}).encode()
        response = {'version': 1, 'request_sha256': identity, 'http_status': 200,
            'response_sha256': hashlib.sha256(raw).hexdigest(), 'encrypted_response': included._cipher().encrypt(raw).decode()}
        case.client.set(native.PREFIX + 'request:' + identity, plan._raw(request))
        case.client.set(native.PREFIX + 'response:' + identity, plan._raw(response))
        case.client.sadd(native.PREFIX + 'lineage:' + root_id, identity)
    return root, source, latest


@pytest.mark.parametrize('damage', [None, 'unknown', 'additional', 'original_claim', 'voice', 'cancel',
    'word_count_valid', 'scene_missing', 'paid_ai', 'body_hash', 'wrong_terminal'])
def test_only_captured_shortened_pre_speech_feedback_admits_one_new_child(case, monkeypatch, tmp_path, damage):
    from app import tasks
    root, source, latest = shortened_revision(case, monkeypatch); task = source['task_id']
    key = native.PREFIX + 'response:' + latest
    if damage == 'unknown': case.client.delete(key)
    if damage == 'additional': case.client.sadd(native.PREFIX + 'lineage:' + root['task_id'], 'eleventh')
    if damage == 'original_claim': case.client.delete(resume.DISPATCH + root['task_id'])
    if damage == 'voice': source['audio_candidate_checkpoint'] = {'voice': 'exists'}
    if damage == 'cancel': case.client.set(jobs.RENDER_CANCELLATION_PREFIX + root['task_id'], 'owner stopped')
    if damage == 'wrong_terminal': case.client.delete('celery-task-meta-' + task)
    if damage in {'word_count_valid', 'scene_missing', 'paid_ai', 'body_hash'}:
        response = json.loads(case.client.get(key))
        body = json.loads(included._cipher().decrypt(response['encrypted_response'].encode()))
        part = body['candidates'][0]['content']['parts'][0]; value = json.loads(part['text'])
        if damage == 'word_count_valid':
            for scene in value['scenes']: scene['narration'] = ' '.join(['word'] * 12)
        if damage == 'scene_missing': value['scenes'].pop()
        if damage == 'paid_ai': value['scenes'][0]['ai_prompt'] = 'purchase a clip'
        part['text'] = plan._raw(value); raw = plan._raw(body).encode()
        response.update(response_sha256=('a' * 64 if damage == 'body_hash' else hashlib.sha256(raw).hexdigest()),
                        encrypted_response=included._cipher().encrypt(raw).decode())
        case.client.set(key, plan._raw(response))
    case.client.set(jobs.JOB_PREFIX + task, plan._raw(source))
    before = {k: case.client.dump(k) for k in case.client.scan_iter()}; queue = Mock()
    if damage:
        with pytest.raises((plan.ContentPlanError, TypeError, ValueError)): recovery.schedule(source, queue)
        queue.assert_not_called()
    else:
        assert recovery.schedule(source, queue) == 'story_resume_preparing'
        assert recovery.schedule(source, queue) == 'story_resume_reserved'
        queue.assert_called_once()
    assert all(case.client.dump(k) == value for k, value in before.items())
    if not damage:
        result = recovery.run(task, resume.operation(task)); child = result['task_id']
        assert jobs.acquire_retry_child_execution(child, task)
        assert tasks._prepare_saved_voice_retry(child, task, source['spec'], tmp_path) is None
        assert recovery.run(task, resume.operation(task)) == {'status': 'already_started'}
        assert tasks.run_video_pipeline.apply_async.call_args.kwargs['args'][1] == 3


def test_captured_second_length_failure_preserves_both_earlier_continuation_claims(case, monkeypatch, tmp_path):
    from app import tasks
    root, middle, latest = shortened_revision(case, monkeypatch); mid = middle['task_id']
    recovery.schedule(middle, Mock()); result = recovery.run(mid, resume.operation(mid)); task = result['task_id']
    assert jobs.acquire_retry_child_execution(task, mid)
    source = jobs.get_job(task)
    source.update(state='FAILURE', failure_stage='director_qc', error=resume.CONTRACT_ERROR,
                  paid_create_slots_used=0, preview_total_paid_create_cap=32)
    case.client.set(jobs.JOB_PREFIX + task, plan._raw(source))
    case.client.hset(jobs.PAID_CREATE_BUDGET_PREFIX + task, mapping={'cap': '32', 'used': '0'})
    terminal = json.loads(case.client.get('celery-task-meta-' + mid)); terminal['task_id'] = task
    case.client.set('celery-task-meta-' + task, plan._raw(terminal))
    for i in range(2):
        identity = hashlib.sha256(f'length-{i}'.encode()).hexdigest()
        request = json.loads(case.client.get(native.PREFIX + 'request:' + latest))
        request.update(request_sha256=identity, reserved_at=f'2026-09-22T12:53:0{i}+00:00')
        response = json.loads(case.client.get(native.PREFIX + 'response:' + latest)); response['request_sha256'] = identity
        case.client.set(native.PREFIX + 'request:' + identity, plan._raw(request))
        case.client.set(native.PREFIX + 'response:' + identity, plan._raw(response))
        case.client.sadd(native.PREFIX + 'lineage:' + root['task_id'], identity)
    before = {key: case.client.get(key) for key in (resume.DISPATCH + root['task_id'], resume.DISPATCH + mid)}
    assert recovery.schedule(source, Mock()) == 'story_resume_preparing'
    result = recovery.run(task, resume.operation(task)); child = result['task_id']
    assert jobs.acquire_retry_child_execution(child, task)
    assert tasks._prepare_saved_voice_retry(child, task, source['spec'], tmp_path) is None
    assert all(case.client.get(key) == value for key, value in before.items())
