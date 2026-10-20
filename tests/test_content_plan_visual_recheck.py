"""One normal-QA continuation after a retained documentary's final rejection."""
from copy import deepcopy
import hashlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import content_plan as plan, studio_state as jobs
from app.services import content_plan_retained_completion as completion, content_plan_recovery as recovery
from app.services import content_plan_voice_resume as voice, commissioning_video as video
from app.services import production_spend_runtime as runtime
from test_content_plan import case
from test_content_plan_retained_completion import quota_failure
from test_content_plan_voice_resume import candidate_failure


def failed_review(case, monkeypatch, mode):
    if mode == 'quota':
        previous, root, _ = quota_failure(case, monkeypatch)
    else:
        saved, root = candidate_failure(case, monkeypatch, child=True, long=True)
        voice.schedule(saved, Mock()); first = voice.run(saved['task_id'], voice.operation(saved['task_id']))
        task = first['task_id']; assert jobs.acquire_retry_child_execution(task, saved['task_id'])
        previous = jobs.get_job(task)
        previous.update(state='FAILURE', failure_stage='director_qc', error=completion.SOURCE_ERROR,
                        paid_create_slots_used=0, preview_total_paid_create_cap=32)
        case.client.set(jobs.JOB_PREFIX + task, plan._raw(previous))
        case.client.hset(jobs.PAID_CREATE_BUDGET_PREFIX + task, mapping={'cap': '32', 'used': '0'})
        case.client.set('celery-task-meta-' + task, plan._raw({'task_id': task, 'status': 'FAILURE',
            'result': {'exc_type': 'ProductionContentError', 'exc_message': [completion.SOURCE_ERROR]},
            'traceback': 'File "longform.py", line 1, in review_story\n'}))
        monkeypatch.setattr(completion, '_restored_sources', Mock(return_value={'sources': ['restored-public-source']}))
    recovery.schedule(previous, Mock())
    task = recovery.run(previous['task_id'], completion.operation(previous['task_id']))['task_id']
    assert jobs.acquire_retry_child_execution(task, previous['task_id'])
    source = jobs.get_job(task)
    error = completion.VISUAL_ERROR + plan._raw({'stage': 'after_rescue', 'total': 30, 'accepted': 28,
        'rejected': {'9': {'score': 40}, '21': {'score': 40}}, 'repair_checkpoint_available': False})
    source.update(state='FAILURE', stage='failed', failure_stage='final_visual_qc_rescue', error=error,
        failure_classification={'category': 'content_rejected', 'code': 'visual_quality_exhausted',
                                'error_sha256': hashlib.sha256(error.encode()).hexdigest()},
        paid_create_slots_used=6, preview_total_paid_create_cap=32,
        audio_candidate_checkpoint={'audio_sha256': 'b' * 64})
    case.client.set(jobs.JOB_PREFIX + task, plan._raw(source))
    case.client.hset(jobs.PAID_CREATE_BUDGET_PREFIX + task, mapping={'cap': '32', 'used': '6'})
    case.client.set('celery-task-meta-' + task, plan._raw({'task_id': task, 'status': 'FAILURE',
        'result': {'exc_type': 'FinalVisualQualityError', 'exc_message': [error]},
        'traceback': 'File "tasks.py", line 1, in run_video_pipeline\n'}))
    return source, root


@pytest.mark.parametrize('mode', ['quota', 'sources'])
def test_old_claims_receipts_and_negative_verdicts_survive_one_full_recheck(case, monkeypatch, mode):
    source, root = failed_review(case, monkeypatch, mode); task = source['task_id']
    keys = [k for k in case.client.scan_iter() if k.startswith((completion.PREFIX, video.PREFIX))]
    old = {key: case.client.dump(key) for key in keys}; queue = Mock()
    assert recovery.schedule(source, queue) == 'retained_completion_preparing'
    assert recovery.schedule(source, queue) == 'retained_completion_reserved'
    queue.assert_called_once()
    child = recovery.run(task, completion.operation(task))['task_id']
    assert jobs.acquire_retry_child_execution(child, task)
    _, _, proof = completion.verify_child(child, task, source['spec'])
    assert proof['review_of'] == source['parent_id'] and proof['mode'] == mode
    assert proof['saved_voice_source'] == task and proof['restored_source'] is None
    assert jobs.get_job(task)['error'] == source['error']
    assert all(case.client.dump(k) == v for k, v in old.items())
    scope = {'context': {'lineage_id': root, 'kind': 'long'}, 'foundation': SimpleNamespace(client=case.client)}
    token = runtime._TASK_ID.set(child)
    try:
        assert completion.stock_only_scope(scope) is (mode == 'quota')
        if mode == 'sources': assert completion.continuation_identity(scope) == child
    finally: runtime._TASK_ID.reset(token)
    # A further failure cannot create a chain of fresh per-run allowances.
    later = deepcopy(source); later.update(task_id=child, parent_id=task)
    with pytest.raises(plan.ContentPlanError): completion._prior_completion(case.client, later)


@pytest.mark.parametrize('damage', ['changed_old_claim', 'unknown_video', 'changed_verdict', 'missing_terminal',
    'root_hold', 'changed_voice', 'different_child', 'changed_counter'])
def test_recheck_rejects_uncertain_or_changed_proof_without_dispatch(case, monkeypatch, damage):
    source, root = failed_review(case, monkeypatch, 'quota'); task = source['task_id']
    if damage == 'changed_old_claim': case.client.set(completion.ROOT + root, '{}')
    if damage == 'unknown_video':
        journal = plan._object(case.client.get(video.PREFIX + root))
        next(iter(journal['requests'].values()))['create'] = None
        case.client.set(video.PREFIX + root, plan._raw(journal))
    if damage == 'changed_verdict': source['failure_classification']['error_sha256'] = '0' * 64
    if damage == 'missing_terminal': case.client.delete('celery-task-meta-' + task)
    if damage == 'root_hold': case.client.set(jobs.RENDER_CANCELLATION_PREFIX + root, 'stopped')
    if damage == 'changed_voice':
        monkeypatch.setattr(voice, '_transcript_proof', lambda *args: {'audio_sha256': 'x' * 64})
    if damage == 'different_child': source['parent_id'] = root
    if damage == 'changed_counter': case.client.hset(jobs.PAID_CREATE_BUDGET_PREFIX + task, 'used', '0')
    case.client.set(jobs.JOB_PREFIX + task, plan._raw(source))
    before = {k: case.client.dump(k) for k in case.client.scan_iter()}; enqueue = Mock()
    with pytest.raises(Exception): completion.schedule(source, enqueue)
    enqueue.assert_not_called()
    assert all(case.client.dump(k) == v for k, v in before.items())
