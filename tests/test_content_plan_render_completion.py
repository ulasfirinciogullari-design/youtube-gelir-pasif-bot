from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import content_plan as plan, studio_state as jobs, production_spend_runtime as runtime
from app.services import content_plan_retained_completion as completion, content_plan_recovery as recovery
from app.services import commissioning_video as video
from test_content_plan import case
from test_content_plan_availability_recovery import stopped_recheck


def failed_render(case, monkeypatch):
    source, root = stopped_recheck(case, monkeypatch, 'quota')
    recovery.schedule(source, Mock())
    task = recovery.run(source['task_id'], completion.operation(source['task_id']))['task_id']
    assert jobs.acquire_retry_child_execution(task, source['task_id'])
    source = jobs.get_job(task)
    source.update(state='FAILURE', failure_stage='render', error=completion.RENDER_ERROR,
        paid_create_slots_used=4, preview_total_paid_create_cap=32,
        audio_candidate_checkpoint={'audio_sha256': 'b' * 64})
    case.client.set(jobs.JOB_PREFIX + task, plan._raw(source))
    case.client.hset(jobs.PAID_CREATE_BUDGET_PREFIX + task, mapping={'cap': '32', 'used': '4'})
    trace = ('File "tasks.py", line 1, in run_video_pipeline\n'
        'File "render.py", line 2, in render_video\n'
        'File "render.py", line 3, in normalize_clip\n'
        'File "render.py", line 4, in render_attempt\n'
        'RuntimeError: Normalized clip frame gate rejected segment: 150 frames for 151 frame target\n')
    case.client.set('celery-task-meta-' + task, plan._raw({'task_id': task, 'status': 'FAILURE',
        'result': {'exc_type': 'FinalVisualQualityError', 'exc_message': [completion.RENDER_ERROR]},
        'traceback': trace}))
    return source, root


def test_one_render_completion_uses_same_audio_clips_and_cannot_send_video_request(case, monkeypatch):
    source, root = failed_render(case, monkeypatch)
    old = {k: case.client.dump(k) for k in case.client.scan_iter()
           if k.startswith((completion.PREFIX, video.PREFIX))}
    queue = Mock()
    assert recovery.schedule(source, queue) == 'retained_completion_preparing'
    assert recovery.schedule(source, queue) == 'retained_completion_reserved'
    queue.assert_called_once()
    child = recovery.run(source['task_id'], completion.operation(source['task_id']))['task_id']
    assert jobs.acquire_retry_child_execution(child, source['task_id'])
    _, _, proof = completion.verify_child(child, source['task_id'], source['spec'])
    assert proof['render_of'] == source['parent_id'] and proof['mode'] == 'quota'
    scope = {'foundation': SimpleNamespace(client=case.client), 'context': {'lineage_id': root, 'kind': 'long'}}
    token = runtime._TASK_ID.set(child)
    try: assert completion.stock_only_scope(scope) is True
    finally: runtime._TASK_ID.reset(token)
    assert all(case.client.dump(k) == value for k, value in old.items())
    with pytest.raises(plan.ContentPlanError):
        completion._prior_completion(case.client, {**source, 'task_id': child, 'parent_id': source['task_id']})


@pytest.mark.parametrize('damage', ['different_error', 'transport', 'origin', 'unknown_video', 'hold'])
def test_no_generic_retry_from_unproven_render_error(case, monkeypatch, damage):
    source, root = failed_render(case, monkeypatch)
    key = 'celery-task-meta-' + source['task_id']; terminal = plan._object(case.client.get(key))
    if damage == 'different_error': terminal['traceback'] = terminal['traceback'].replace('150 frames', '100 frames')
    if damage == 'transport': terminal['traceback'] += 'TimeoutError\n'
    if damage == 'origin': terminal['traceback'] = terminal['traceback'].replace('in normalize_clip', 'in upload')
    if damage == 'unknown_video':
        journal = plan._object(case.client.get(video.PREFIX + root))
        next(iter(journal['requests'].values()))['create'] = None
        case.client.set(video.PREFIX + root, plan._raw(journal))
    if damage == 'hold': case.client.set(jobs.RENDER_CANCELLATION_PREFIX + root, 'stop')
    case.client.set(key, plan._raw(terminal))
    queue = Mock()
    with pytest.raises(Exception): recovery.schedule(source, queue)
    queue.assert_not_called()
