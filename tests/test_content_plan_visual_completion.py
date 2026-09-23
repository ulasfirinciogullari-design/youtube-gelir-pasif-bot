import hashlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import content_plan as plan, studio_state as jobs
from app.services import content_plan_retained_completion as completion, content_plan_recovery as recovery
from app.services import content_plan_interruption as interruption, commissioning_video as video
from app.services import production_spend_runtime as runtime
from test_content_plan import case
from test_content_plan_interruption import running_motion


def failed_stock_completion(case, monkeypatch):
    source, root, journal, observation = running_motion(case, monkeypatch)
    interruption.record_removed(source['task_id'], observation)
    recovery.schedule(jobs.get_job(source['task_id']), Mock())
    task = recovery.run(source['task_id'], completion.operation(source['task_id']))['task_id']
    assert jobs.acquire_retry_child_execution(task, source['task_id'])
    diagnostic = {'stage': 'after_rescue', 'accepted': 29, 'total': 30,
        'rejected': {'17': {'score': 60, 'reason': 'Generic footage does not show the object.'}},
        'repair_checkpoint_available': False, 'provider_generation_failures': {
            'failures': [{'exception_class': 'CommissionedVideoUnavailable'}]}}
    error = completion.VISUAL_ERROR + plan._raw(diagnostic)
    job = jobs.get_job(task)
    job.update(state='FAILURE', stage='failed', failure_stage='final_visual_qc_rescue', error=error,
        paid_create_slots_used=0, preview_total_paid_create_cap=32,
        audio_candidate_checkpoint={'audio_sha256': 'b' * 64},
        retained_long_media={'stock_only': True, 'new_tts_requests': 0},
        failure_classification={'category': 'content_rejected', 'code': 'visual_quality_exhausted',
            'error_sha256': hashlib.sha256(error.encode()).hexdigest()})
    case.client.set(jobs.JOB_PREFIX + task, plan._raw(job))
    case.client.hset(jobs.PAID_CREATE_BUDGET_PREFIX + task, mapping={'cap': '32', 'used': '0'})
    case.client.set('celery-task-meta-' + task, plan._raw({'task_id': task, 'status': 'FAILURE',
        'result': {'exc_type': 'FinalVisualQualityError', 'exc_message': [error]},
        'traceback': 'File "tasks.py", line 1, in run_video_pipeline\n'}))
    return job, root, journal


def test_one_completion_only_allows_recorded_failed_scenes_and_preserves_root_cap(case, monkeypatch):
    source, root, journal = failed_stock_completion(case, monkeypatch)
    original = case.client.get(video.PREFIX + root)
    queue = Mock()
    assert recovery.schedule(source, queue) == 'retained_completion_preparing'
    assert recovery.schedule(source, queue) == 'retained_completion_reserved'
    queue.assert_called_once()
    child = recovery.run(source['task_id'], completion.operation(source['task_id']))['task_id']
    assert jobs.acquire_retry_child_execution(child, source['task_id'])
    _, _, proof = completion.verify_child(child, source['task_id'], source['spec'])
    assert proof['generation_scenes'] == [17] and proof['visual_completion_of']
    assert proof['transcript']['audio_sha256'] == 'b' * 64
    scope = {'foundation': SimpleNamespace(client=case.client), 'context': journal['context'], 'scene_index': 17}
    token = runtime._TASK_ID.set(child)
    try:
        assert completion.stock_only_scope(scope) is False
        assert completion.stock_only_scope({**scope, 'scene_index': 16}) is True
        assert completion.continuation_identity(scope) == child
        descriptor = {'model': 'fal-ai/bytedance/seedance/v1.5/pro/text-to-video',
            'scene_index': 17, 'continuation_task_id': child}
        journal['requests'][video._sha(video._raw(descriptor).encode())] = {
            'request': descriptor, 'create': None, 'result': None}
        case.client.set(video.PREFIX + root, plan._raw(journal))
        # Its own unknown HTTP result stays fenced by the native journal. It
        # never changes the immutable admission or creates a new root budget.
        assert completion.continuation_identity(scope) == child
    finally:
        runtime._TASK_ID.reset(token)
    del journal['requests'][video._sha(video._raw(descriptor).encode())]
    assert plan._raw(journal) == original
    with pytest.raises(Exception):
        completion._prior_completion(case.client, {**source, 'task_id': child, 'parent_id': source['task_id']})


@pytest.mark.parametrize('damage', ['unknown', 'incomplete', 'owner_stop', 'changed_audio', 'false_failure', 'unapproved_scope'])
def test_new_completion_refuses_uncertain_paid_work_owner_stops_and_changed_evidence(case, monkeypatch, damage):
    source, root, journal = failed_stock_completion(case, monkeypatch)
    row = next(r for r in journal['requests'].values() if r['create']['http_status'] == 200)
    if damage == 'unknown': row['create'] = None
    elif damage == 'incomplete': row['result'] = None
    elif damage == 'owner_stop': case.client.set(jobs.RENDER_CANCELLATION_PREFIX + root, 'stop')
    elif damage == 'changed_audio': source['audio_candidate_checkpoint']['audio_sha256'] = 'f' * 64
    elif damage == 'false_failure': source['failure_classification']['error_sha256'] = 'f' * 64
    else: source['retained_long_media']['stock_only'] = False
    case.client.set(video.PREFIX + root, plan._raw(journal))
    case.client.set(jobs.JOB_PREFIX + source['task_id'], plan._raw(source))
    before = {key: case.client.dump(key) for key in case.client.scan_iter()}
    queue = Mock()
    with pytest.raises(Exception): recovery.schedule(source, queue)
    queue.assert_not_called()
    assert before == {key: case.client.dump(key) for key in case.client.scan_iter()}
