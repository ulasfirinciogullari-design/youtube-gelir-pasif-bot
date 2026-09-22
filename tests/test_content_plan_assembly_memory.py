from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import content_plan as plan, studio_state as jobs, production_spend_runtime as runtime
from app.services import content_plan_retained_completion as completion, content_plan_recovery as recovery
from app.services import commissioning_video as video
from test_content_plan import case
from test_content_plan_render_completion import failed_render


def stopped_assembly(case, monkeypatch):
    source, root = failed_render(case, monkeypatch)
    for error, assembly in [(completion.RENDER_ERROR, False), (completion.ASSEMBLY_ERROR, True)]:
        recovery.schedule(source, Mock())
        child = recovery.run(source['task_id'], completion.operation(source['task_id']))['task_id']
        assert jobs.acquire_retry_child_execution(child, source['task_id'])
        source = jobs.get_job(child)
        source.update(state='FAILURE', failure_stage='render', error=error,
            paid_create_slots_used=4, preview_total_paid_create_cap=32,
            audio_candidate_checkpoint={'audio_sha256': 'b' * 64})
        case.client.set(jobs.JOB_PREFIX + child, plan._raw(source))
        case.client.hset(jobs.PAID_CREATE_BUDGET_PREFIX + child, mapping={'cap': '32', 'used': '4'})
        trace = 'File "tasks.py", line 1, in run_video_pipeline\nFile "render.py", line 2, in render_video\n'
        if assembly:
            command = ['ffmpeg', '-y']
            for index in range(30):
                command.extend(['-i', f'/tmp/youtube_factory/{child}_attempt_0/norm_{index:03d}.mp4'])
            command.extend(['-filter_complex', 'concat=n=30:v=1:a=0',
                f'/tmp/youtube_factory/{child}_attempt_0/silent.mp4'])
            trace += ("    _run(concat_command)\nFile \"render.py\", line 3, in _run\n"
                f"subprocess.CalledProcessError: Command '{command!r}' died with <Signals.SIGKILL: 9>.\n")
        else:
            trace += ('File "render.py", line 3, in normalize_clip\n'
                'RuntimeError: Generated clip is too short for a single-pass scene: 8.000s source for 6.867s segment\n')
        case.client.set('celery-task-meta-' + child, plan._raw({'task_id': child, 'status': 'FAILURE',
            'result': {'exc_type': 'FinalVisualQualityError', 'exc_message': [error]}, 'traceback': trace}))
    return source, root


def test_one_captured_local_assembly_failure_retains_provider_history_and_stock_only_scope(case, monkeypatch):
    source, root = stopped_assembly(case, monkeypatch)
    old = {key: case.client.dump(key) for key in case.client.scan_iter()
           if key.startswith((completion.PREFIX, video.PREFIX))}
    queue = Mock()
    assert recovery.schedule(source, queue) == 'retained_completion_preparing'
    assert recovery.schedule(source, queue) == 'retained_completion_reserved'
    queue.assert_called_once()
    child = recovery.run(source['task_id'], completion.operation(source['task_id']))['task_id']
    assert jobs.acquire_retry_child_execution(child, source['task_id'])
    _, _, proof = completion.verify_child(child, source['task_id'], source['spec'])
    assert proof['assembly_of'] == source['parent_id'] and proof['mode'] == 'quota'
    assert proof['transcript']['audio_sha256'] == 'b' * 64
    scope = {'foundation': SimpleNamespace(client=case.client), 'context': {'lineage_id': root, 'kind': 'long'}}
    token = runtime._TASK_ID.set(child)
    try:
        assert completion.stock_only_scope(scope) is True
    finally:
        runtime._TASK_ID.reset(token)
    assert all(case.client.dump(key) == value for key, value in old.items())
    with pytest.raises(plan.ContentPlanError):
        completion._prior_completion(case.client, {**source, 'task_id': child, 'parent_id': source['task_id']})


@pytest.mark.parametrize('damage', ['signal', 'phase', 'input', 'transport', 'unknown_video', 'hold'])
def test_unproven_assembly_or_provider_failure_cannot_restart(case, monkeypatch, damage):
    source, root = stopped_assembly(case, monkeypatch)
    key = 'celery-task-meta-' + source['task_id']; terminal = plan._object(case.client.get(key))
    if damage == 'signal': terminal['traceback'] = terminal['traceback'].replace('SIGKILL: 9', 'SIGTERM: 15')
    if damage == 'phase': terminal['traceback'] = terminal['traceback'].replace('_run(concat_command)', '_run(upload_command)')
    if damage == 'input': terminal['traceback'] = terminal['traceback'].replace('norm_029.mp4', 'unrelated.mp4')
    if damage == 'transport': terminal['traceback'] += 'TimeoutError\n'
    if damage == 'unknown_video':
        journal = plan._object(case.client.get(video.PREFIX + root))
        next(iter(journal['requests'].values()))['create'] = None
        case.client.set(video.PREFIX + root, plan._raw(journal))
    if damage == 'hold': case.client.set(jobs.RENDER_CANCELLATION_PREFIX + root, 'stop')
    case.client.set(key, plan._raw(terminal))
    queue = Mock()
    with pytest.raises(Exception): recovery.schedule(source, queue)
    queue.assert_not_called()
