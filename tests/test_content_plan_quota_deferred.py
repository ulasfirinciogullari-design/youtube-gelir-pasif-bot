from datetime import datetime, timezone, timedelta
import hashlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet

from app.services import content_plan as plan, studio_state as jobs, youtube_auth
from app.services import content_plan_retained_completion as completion, content_plan_recovery as recovery
from app.services import commissioning_video as video, production_spend_runtime as runtime
from test_content_plan import case
from test_content_plan_availability_recovery import stopped_recheck


def captured_quota(case, monkeypatch):
    source, root = stopped_recheck(case, monkeypatch, 'sources')
    monkeypatch.setattr(completion, '_quota_ready', Mock())
    recovery.schedule(source, Mock())
    child = recovery.run(source['task_id'], completion.operation(source['task_id']))['task_id']
    assert jobs.acquire_retry_child_execution(child, source['task_id'])
    job = jobs.get_job(child)
    instant = datetime(2026, 9, 22, 21, 40, tzinfo=timezone.utc)
    job.update(state='FAILURE', error=source['error'], failure_stage=source['failure_stage'],
        failure_classification=source['failure_classification'], paid_create_slots_used=1,
        preview_total_paid_create_cap=32, audio_candidate_checkpoint={'audio_sha256': 'b' * 64},
        created_at=(instant - timedelta(minutes=1)).isoformat(), updated_at=(instant + timedelta(minutes=2)).isoformat())
    case.client.set(jobs.JOB_PREFIX + child, plan._raw(job))
    case.client.hset(jobs.PAID_CREATE_BUDGET_PREFIX + child, mapping={'cap': '32', 'used': '1'})
    case.client.set('celery-task-meta-' + child, plan._raw({'task_id': child, 'status': 'FAILURE',
        'result': {'exc_type': 'FinalVisualQualityError', 'exc_message': [job['error']]},
        'traceback': 'File "tasks.py", line 1, in run_video_pipeline\n'}))
    cipher = Fernet(Fernet.generate_key()); monkeypatch.setattr(youtube_auth, '_fernet', lambda: cipher)
    monkeypatch.setattr(video.settings, 'gemini_api_key', 'deferred-quota-test')
    descriptor = {'model': video.MODEL, 'credential_sha256': video._sha(b'gemini\0deferred-quota-test'),
                  'continuation_task_id': child}
    raw = plan._raw({'error': {'code': 429, 'status': 'RESOURCE_EXHAUSTED'}})
    row = {'request': descriptor, 'result': None, 'create': {'http_status': 429,
        'encrypted_response': youtube_auth._encrypt_json({'response': raw}),
        'response_sha256': hashlib.sha256(raw.encode()).hexdigest(), 'observed_at': instant.isoformat()}}
    journal = {'version': 1, 'context': {'lineage_id': root, 'kind': 'long',
        'channel_id': job['spec']['production_channel_id'], 'connection_id': job['spec']['production_connection_id']},
        'requests': {video._sha(video._raw(descriptor).encode()): row}}
    case.client.set(video.PREFIX + root, plan._raw(journal))
    return job, root, journal


def set_clock(monkeypatch, instant):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return instant.astimezone(tz or timezone.utc)
    monkeypatch.setattr(completion, 'datetime', Clock)


def test_waiting_is_read_only_then_one_normal_qa_continuation_after_reset(case, monkeypatch):
    source, root, journal = captured_quota(case, monkeypatch)
    set_clock(monkeypatch, datetime(2026, 9, 22, 22, tzinfo=timezone.utc))
    before = {k:case.client.dump(k) for k in case.client.scan_iter()}
    pending = completion.deferred_quota(case.client, source)
    assert pending['retry_at'] == '2026-09-23T07:05:00+00:00'
    queue = Mock()
    assert recovery.schedule(source, queue) == 'plan_video_quota_waiting'
    queue.assert_not_called()
    assert all(case.client.dump(k) == v for k, v in before.items())
    set_clock(monkeypatch, datetime(2026, 9, 23, 7, 6, tzinfo=timezone.utc))
    assert recovery.schedule(source, queue) == 'retained_completion_preparing'
    assert recovery.schedule(source, queue) == 'retained_completion_reserved'
    queue.assert_called_once()
    child = recovery.run(source['task_id'], completion.operation(source['task_id']))['task_id']
    assert jobs.acquire_retry_child_execution(child, source['task_id'])
    _, _, proof = completion.verify_child(child, source['task_id'], source['spec'])
    assert proof['deferred_until'] == pending['retry_at'] and proof['mode'] == 'sources'
    assert plan._object(case.client.get(video.PREFIX + root)) == journal
    token = runtime._TASK_ID.set(child)
    scope = {'foundation': SimpleNamespace(client=case.client), 'context': journal['context']}
    try:
        assert completion.stock_only_scope(scope) is False
        assert completion.continuation_identity(scope) == child
    finally: runtime._TASK_ID.reset(token)
    with pytest.raises(plan.ContentPlanError):
        completion._prior_completion(case.client, {**source, 'task_id': child, 'parent_id': source['task_id']})


@pytest.mark.parametrize('damage', ['unknown', 'accepted', 'wrong_key', 'bad_hash', 'other_task'])
def test_rejected_create_must_be_fully_known_and_bound_before_wait_or_enqueue(case, monkeypatch, damage):
    source, root, journal = captured_quota(case, monkeypatch)
    row = next(iter(journal['requests'].values()))
    if damage == 'unknown': row['create'] = None
    if damage == 'accepted': row['create']['http_status'] = 200
    if damage == 'wrong_key': monkeypatch.setattr(video.settings, 'gemini_api_key', 'changed')
    if damage == 'bad_hash': row['create']['response_sha256'] = 'f' * 64
    if damage == 'other_task': row['request']['continuation_task_id'] = source['parent_id']
    case.client.set(video.PREFIX + root, plan._raw(journal))
    queue = Mock()
    with pytest.raises(Exception): recovery.schedule(source, queue)
    queue.assert_not_called()


def test_web_can_display_captured_wait_but_cannot_admit_with_another_credential(case, monkeypatch):
    source, root, journal = captured_quota(case, monkeypatch)
    before = {k: case.client.dump(k) for k in case.client.scan_iter()}
    monkeypatch.setattr(video.settings, 'gemini_api_key', 'web-service-key')
    waiting = completion.deferred_quota(case.client, source, require_current_credential=False)
    assert waiting['retry_at'] == '2026-09-23T07:05:00+00:00'
    queue = Mock()
    with pytest.raises(plan.ContentPlanError): recovery.schedule(source, queue)
    queue.assert_not_called()
    assert all(case.client.dump(k) == v for k, v in before.items())
