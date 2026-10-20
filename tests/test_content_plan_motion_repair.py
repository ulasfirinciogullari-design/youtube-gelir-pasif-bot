import hashlib
from datetime import datetime, timezone
from unittest.mock import Mock
from types import SimpleNamespace

import pytest

from app.services import content_plan as plan, studio_state as jobs, youtube_auth
from app.services import content_plan_retained_completion as completion, content_plan_recovery as recovery
from app.services import commissioning_video as video, production_spend_runtime as runtime
from test_content_plan import case
from test_content_plan_quota_deferred import captured_quota, set_clock


def failed_motion(case, monkeypatch):
    source, root, journal = captured_quota(case, monkeypatch)
    set_clock(monkeypatch, datetime(2026, 9, 23, 7, 6, tzinfo=timezone.utc))
    recovery.schedule(source, Mock())
    child = recovery.run(source['task_id'], completion.operation(source['task_id']))['task_id']
    assert jobs.acquire_retry_child_execution(child, source['task_id'])
    error = 'Final motion gate rejected 7.2s static interval (limit 6.0s)'
    job = jobs.get_job(child)
    job.update(state='FAILURE', error=error, failure_stage='render', paid_create_slots_used=1,
        preview_total_paid_create_cap=32, audio_candidate_checkpoint={'audio_sha256': 'b' * 64},
        failure_classification={'category': 'content_rejected', 'code': 'render_quality_exhausted',
            'stage': 'render', 'version': 1, 'error_sha256': hashlib.sha256(error.encode()).hexdigest()})
    case.client.set(jobs.JOB_PREFIX + child, plan._raw(job))
    case.client.hset(jobs.PAID_CREATE_BUDGET_PREFIX + child, mapping={'cap': '32', 'used': '1'})
    case.client.set('celery-task-meta-' + child, plan._raw({'task_id': child, 'status': 'FAILURE',
        'result': {'exc_type': 'ProductionContentError', 'exc_message': [error]},
        'traceback': 'File "tasks.py", line 1, in run_video_pipeline\n'}))
    descriptor = {'model': video.MODEL, 'continuation_task_id': child}
    def observed(payload):
        data = plan._raw(payload)
        return {'http_status': 200, 'encrypted_response': youtube_auth._encrypt_json({'response': data}),
                'response_sha256': hashlib.sha256(data.encode()).hexdigest()}
    row = {'request': descriptor, 'create': observed({'name': 'test-operation'}),
           'result': observed({'name': 'test-operation', 'done': True,
                              'response': {'generateVideoResponse': {'generatedSamples': [
                                  {'video': {'uri': video.BASE + '/files/verified-video:download'}}]}}})}
    journal['requests'][video._sha(video._raw(descriptor).encode())] = row
    case.client.set(video.PREFIX + root, plan._raw(journal))
    return job, root, journal


def test_known_motion_failure_gets_one_normal_qa_repair_and_keeps_paid_history(case, monkeypatch):
    source, root, journal = failed_motion(case, monkeypatch)
    before = {k: case.client.dump(k) for k in case.client.scan_iter()
              if k.startswith((completion.PREFIX, video.PREFIX))}
    queue = Mock()
    assert recovery.schedule(source, queue) == 'retained_completion_preparing'
    assert recovery.schedule(source, queue) == 'retained_completion_reserved'
    queue.assert_called_once()
    child = recovery.run(source['task_id'], completion.operation(source['task_id']))['task_id']
    assert jobs.acquire_retry_child_execution(child, source['task_id'])
    _, _, proof = completion.verify_child(child, source['task_id'], source['spec'])
    assert proof['motion_of'] == source['parent_id'] and proof['mode'] == 'sources'
    assert proof['transcript']['audio_sha256'] == 'b' * 64
    assert len(proof['video_records']) == 2
    assert plan._object(case.client.get(video.PREFIX + root)) == journal
    assert all(case.client.dump(k) == v for k, v in before.items())
    scope = {'foundation': SimpleNamespace(client=case.client), 'context': journal['context']}
    token = runtime._TASK_ID.set(child)
    try:
        assert completion.continuation_identity(scope) == child
        assert completion.stock_only_scope(scope) is True
    finally: runtime._TASK_ID.reset(token)
    with pytest.raises(plan.ContentPlanError):
        completion._prior_completion(case.client, {**source, 'task_id': child, 'parent_id': source['task_id']})


@pytest.mark.parametrize('damage', ['other_error', 'classification', 'unknown', 'incomplete', 'hold', 'changed_receipt'])
def test_motion_repair_does_not_restart_unproven_or_stopped_work(case, monkeypatch, damage):
    source, root, journal = failed_motion(case, monkeypatch)
    if damage == 'other_error': source['error'] = 'Final duration gate rejected render'
    if damage == 'classification': source['failure_classification']['error_sha256'] = 'f' * 64
    if damage == 'hold': case.client.set(jobs.RENDER_CANCELLATION_PREFIX + root, 'stop')
    new = next(row for row in journal['requests'].values() if row['create']['http_status'] == 200)
    if damage == 'unknown': new['create'] = None
    if damage == 'incomplete': new['result'] = None
    if damage == 'changed_receipt':
        next(row for row in journal['requests'].values() if row['create']['http_status'] == 429)['reserved_at'] = 'changed'
    case.client.set(jobs.JOB_PREFIX + source['task_id'], plan._raw(source))
    case.client.set(video.PREFIX + root, plan._raw(journal))
    queue = Mock()
    with pytest.raises(Exception): completion.schedule(source, queue)
    queue.assert_not_called()


def test_completed_provider_filter_is_preserved_and_cannot_authorize_new_generation(case, monkeypatch):
    source, root, journal = failed_motion(case, monkeypatch)
    row = next(row for row in journal['requests'].values() if row['create']['http_status'] == 200)
    data = plan._raw({'name': 'test-operation', 'done': True, 'response': {'generateVideoResponse': {
        'generatedSamples': [], 'raiMediaFilteredCount': 1, 'raiMediaFilteredReasons': ['Filtered.']}}})
    row['result'].update(encrypted_response=youtube_auth._encrypt_json({'response': data}),
                         response_sha256=hashlib.sha256(data.encode()).hexdigest())
    case.client.set(video.PREFIX + root, plan._raw(journal))
    before = case.client.get(video.PREFIX + root)
    recovery.schedule(source, Mock())
    child = recovery.run(source['task_id'], completion.operation(source['task_id']))['task_id']
    assert jobs.acquire_retry_child_execution(child, source['task_id'])
    scope = {'foundation': SimpleNamespace(client=case.client), 'context': journal['context'],
             'generation_seconds': 8, 'aspect_ratio': '16:9', 'package_sha256': 'a' * 64,
             'scene_index': 13, 'narration_millis': 7200}
    monkeypatch.setattr(video, 'enabled_for_task', lambda: True)
    token = runtime._TASK_ID.set(child)
    try:
        assert completion.stock_only_scope(scope) is True
        with pytest.raises(video.CommissionedVideoUnavailable, match='quota_stock_rescue'):
            _refuse_new_video(scope)
    finally: runtime._TASK_ID.reset(token)
    assert case.client.get(video.PREFIX + root) == before


def _refuse_new_video(scope):
    token = video._SCENE.set(scope)
    try:
        return video.generate_if_commissioned('unused', 8, '16:9')
    finally:
        video._SCENE.reset(token)
