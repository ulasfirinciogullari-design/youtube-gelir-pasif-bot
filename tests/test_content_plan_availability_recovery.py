"""Local uncommitted errors and elapsed captured quotas cannot replay sends."""
from datetime import datetime, timezone, timedelta
import hashlib
import json
from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet

from app.services import content_plan as plan, studio_state as jobs, youtube_auth
from app.services import content_plan_retained_completion as completion, content_plan_recovery as recovery
from app.services import commissioning_video as video
from test_content_plan import case
from test_content_plan_visual_recheck import failed_review


def stopped_recheck(case, monkeypatch, mode):
    source, root = failed_review(case, monkeypatch, mode); parent = source['task_id']
    recovery.schedule(source, Mock()); task = recovery.run(parent, completion.operation(parent))['task_id']
    assert jobs.acquire_retry_child_execution(task, parent)
    source = jobs.get_job(task)
    if mode == 'quota':
        error = completion.LOCAL_ERROR
        trace = ('File "tasks.py", line 1, in run_video_pipeline\n'
                 'File "tasks.py", line 2, in _reserve_paid_create_slot\n'
                 'File "commissioning_video.py", line 3, in enabled_for_task\n'
                 'File "redis.py", line 4, in _execute_transaction\n'
                 'redis.exceptions.WatchError: Watched variable changed.\n')
        stage = 'final_visual_qc_ai_repair'
    else:
        error = completion.VISUAL_ERROR + plan._raw({'stage': 'after_rescue', 'total': 30, 'accepted': 28,
            'rejected': {'9': {'score': 40}, '21': {'score': 40}}, 'repair_checkpoint_available': False,
            'provider_generation_failures': {'failures': [{'exception_class': 'CommissionedVideoUnavailable'}]}})
        trace = 'File "tasks.py", line 1, in run_video_pipeline\n'; stage = 'final_visual_qc_rescue'
    source.update(state='FAILURE', stage='failed', failure_stage=stage, error=error,
        failure_classification={'category': 'content_rejected', 'code': 'visual_quality_exhausted',
                                'error_sha256': hashlib.sha256(error.encode()).hexdigest()},
        paid_create_slots_used=2, preview_total_paid_create_cap=32, audio_candidate_checkpoint={'audio_sha256': 'b' * 64})
    case.client.set(jobs.JOB_PREFIX + task, plan._raw(source))
    case.client.hset(jobs.PAID_CREATE_BUDGET_PREFIX + task, mapping={'cap': '32', 'used': '2'})
    case.client.set('celery-task-meta-' + task, plan._raw({'task_id': task, 'status': 'FAILURE',
        'result': {'exc_type': 'FinalVisualQualityError', 'exc_message': [error]}, 'traceback': trace}))
    return source, root


@pytest.mark.parametrize('mode', ['quota', 'sources'])
def test_one_availability_recovery_preserves_prior_claims_and_still_requires_full_qa(case, monkeypatch, mode):
    source, root = stopped_recheck(case, monkeypatch, mode); task = source['task_id']
    ready = Mock(); monkeypatch.setattr(completion, '_quota_ready', ready)
    old_keys = [k for k in case.client.scan_iter() if k.startswith((video.PREFIX, completion.PREFIX))]
    before = {k: case.client.dump(k) for k in old_keys}; enqueue = Mock()
    assert recovery.schedule(source, enqueue) == 'retained_completion_preparing'
    assert recovery.schedule(source, enqueue) == 'retained_completion_reserved'; enqueue.assert_called_once()
    result = recovery.run(task, completion.operation(task)); child = result['task_id']
    assert result['new_voice_requests'] == 0 and jobs.acquire_retry_child_execution(child, task)
    _, _, proof = completion.verify_child(child, task, source['spec'])
    assert proof['recovery_of'] == source['parent_id'] and proof['mode'] == mode
    assert proof['saved_voice_source'] == task and proof['transcript']['audio_sha256'] == 'b' * 64
    assert all(case.client.dump(k) == v for k, v in before.items())
    if mode == 'sources': assert ready.call_count == 2  # admission and worker, never after child starts
    else: ready.assert_not_called()
    assert recovery.run(task, completion.operation(task)) == {'status': 'already_started'}
    later = {**source, 'task_id': child, 'parent_id': task}
    with pytest.raises(plan.ContentPlanError): completion._prior_completion(case.client, later)


@pytest.mark.parametrize('damage', ['transport', 'different_origin', 'old_receipt', 'unknown', 'cancel'])
def test_unverified_local_failure_is_never_an_automatic_send_permit(case, monkeypatch, damage):
    source, root = stopped_recheck(case, monkeypatch, 'quota'); task = source['task_id']
    terminal_key = 'celery-task-meta-' + task; terminal = plan._object(case.client.get(terminal_key))
    if damage == 'transport': terminal['traceback'] += 'ConnectionError: lost acknowledgement\n'
    if damage == 'different_origin': terminal['traceback'] = terminal['traceback'].replace('in enabled_for_task', 'in transmit')
    if damage in {'old_receipt', 'unknown'}:
        key = video.PREFIX + root; journal = plan._object(case.client.get(key)); row = next(iter(journal['requests'].values()))
        if damage == 'unknown': row['create'] = None
        else: row['create']['response_sha256'] = 'f' * 64
        case.client.set(key, plan._raw(journal))
    if damage == 'cancel': case.client.set(jobs.RENDER_CANCELLATION_PREFIX + root, 'stop')
    case.client.set(terminal_key, plan._raw(terminal)); queue = Mock()
    with pytest.raises(Exception): completion.schedule(source, queue)
    queue.assert_not_called()


@pytest.mark.parametrize('age,damage,allowed', [(1800, None, False), (5400, None, True),
    (5400, 'hash', False), (5400, 'unrelated_key', False), (5400, 'unknown', False)])
def test_recovery_waits_for_captured_matching_credential_quota_without_mutation(case, monkeypatch, age, damage, allowed):
    cipher = Fernet(Fernet.generate_key()); monkeypatch.setattr(youtube_auth, '_fernet', lambda: cipher)
    monkeypatch.setattr(video.settings, 'gemini_api_key', 'quota-fixture-key')
    now = datetime.now(timezone.utc); observed_at = now - timedelta(seconds=age)
    raw = plan._raw({'error': {'code': 429, 'status': 'RESOURCE_EXHAUSTED'}})
    observed = {'http_status': 429, 'encrypted_response': youtube_auth._encrypt_json({'response': raw}),
        'response_sha256': hashlib.sha256(raw.encode()).hexdigest(), 'observed_at': observed_at.isoformat()}
    descriptor = {'model': video.MODEL, 'credential_sha256': video._sha(b'gemini\0quota-fixture-key')}
    if damage == 'hash': observed['response_sha256'] = 'f' * 64
    if damage == 'unrelated_key': descriptor['credential_sha256'] = '0' * 64
    case.client.set(video.PREFIX + 'other-root', plan._raw({'requests': {
        'quota': {'request': descriptor, 'create': None if damage == 'unknown' else observed, 'result': None}}}))
    before = {k: case.client.dump(k) for k in case.client.scan_iter()}
    source = {'created_at': (observed_at + timedelta(minutes=10)).isoformat()}
    if allowed: completion._quota_ready(case.client, source)
    else:
        with pytest.raises(Exception): completion._quota_ready(case.client, source)
    assert all(case.client.dump(k) == v for k, v in before.items())
