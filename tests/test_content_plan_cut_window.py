from unittest.mock import Mock

import pytest

from app.services import content_plan as plan, studio_state as jobs
from app.services import content_plan_retained_completion as completion, content_plan_recovery as recovery
from test_content_plan import case
from test_content_plan_render_completion import failed_render


def test_one_verified_cut_window_continuation_retains_voice_and_existing_video_records(case, monkeypatch):
    source, root = failed_render(case, monkeypatch)
    recovery.schedule(source, Mock())
    child = recovery.run(source['task_id'], completion.operation(source['task_id']))['task_id']
    assert jobs.acquire_retry_child_execution(child, source['task_id'])
    stopped = jobs.get_job(child)
    stopped.update(state='FAILURE', failure_stage='render', error=completion.RENDER_ERROR,
        paid_create_slots_used=4, preview_total_paid_create_cap=32, audio_candidate_checkpoint={'audio_sha256': 'b' * 64})
    case.client.set(jobs.JOB_PREFIX + child, plan._raw(stopped))
    case.client.hset(jobs.PAID_CREATE_BUDGET_PREFIX + child, mapping={'cap': '32', 'used': '4'})
    trace = ('File "tasks.py", line 1, in run_video_pipeline\n'
        'File "render.py", line 2, in render_video\n'
        'File "render.py", line 3, in normalize_clip\n'
        'RuntimeError: Generated clip is too short for a single-pass scene: 8.000s source for 6.867s segment\n')
    case.client.set('celery-task-meta-' + child, plan._raw({'task_id': child, 'status': 'FAILURE',
        'result': {'exc_type': 'FinalVisualQualityError', 'exc_message': [completion.RENDER_ERROR]}, 'traceback': trace}))
    queue = Mock()
    assert recovery.schedule(stopped, queue) == 'retained_completion_preparing'
    assert recovery.schedule(stopped, queue) == 'retained_completion_reserved'
    queue.assert_called_once()
    result = recovery.run(child, completion.operation(child))
    assert jobs.acquire_retry_child_execution(result['task_id'], child)
    _, _, proof = completion.verify_child(result['task_id'], child, stopped['spec'])
    assert proof['mode'] == 'quota' and proof['window_of'] == source['task_id']
    assert proof['transcript']['audio_sha256'] == 'b' * 64
    assert result['new_voice_requests'] == 0
    with pytest.raises(plan.ContentPlanError):
        completion._prior_completion(case.client, {**stopped, 'task_id': result['task_id'], 'parent_id': child})
