from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from app.services import content_plan_factual_resume as resume, content_plan_attention as attention
from app.services import content_plan as plan, content_plan_research_resume as pre, studio_state as jobs
from app.services import commissioning_reasoning as native
from test_content_plan_attention import case, dump, CHANNEL


@pytest.fixture
def ready(case, monkeypatch):
    c, source, doc, item, previous = case; task = source['task_id']
    monkeypatch.setattr(jobs, '_client', lambda: c)
    source['preview_total_paid_create_cap'] = 32
    source['spec'].update(topic='An evidenced documentary.', language='tr', channel_id='capital-corrupt',
        production_connection_id='current-connection', production_profile_revision='current-profile')
    dispatch = json.loads(c.get(plan.DISPATCH_PREFIX + item['id']))
    dispatch.update(spec_sha256=plan._sha(source['spec']), connection_id='current-connection')
    c.set(plan.DISPATCH_PREFIX + item['id'], plan._raw(dispatch))
    c.set(jobs.JOB_PREFIX + task, plan._raw(source))
    c.hset(jobs.PAID_CREATE_BUDGET_PREFIX + task, mapping={'cap': '32', 'used': '0'})
    profile = json.loads(c.get(plan.production.PROFILE_PREFIX + CHANNEL)); profile['channel_id'] = CHANNEL
    c.set(plan.production.PROFILE_PREFIX + CHANNEL, plan._raw(profile))
    identity = 'a' * 64
    c.sadd(native.PREFIX + 'lineage:' + task, identity)
    c.set(native.PREFIX + 'request:' + identity, plan._raw({'context': {'lineage_id': task},
        'request_sha256': identity, 'purpose': 'story_review'}))
    c.set(native.PREFIX + 'response:' + identity, plan._raw({'request_sha256': identity,
        'http_status': 200, 'encrypted_response': 'offline-observed-response', 'response_sha256': 'b' * 64}))
    free = Mock(); monkeypatch.setattr(pre, '_provider_free', free)
    return c, source, free


def test_one_recovery_send_even_when_broker_ack_is_lost(ready):
    c, source, free = ready; enqueue = Mock(side_effect=TimeoutError('unknown broker reply'))
    assert resume.schedule(source, enqueue, client=c) == 'factual_resume_uncertain'
    assert resume.schedule(source, enqueue, client=c) == 'factual_resume_reserved'
    enqueue.assert_called_once(); free.assert_called_once()
    assert not c.exists(attention.PREFIX + source['spec']['content_plan_item_id'])


def test_same_root_child_preserves_daily_slot_provider_history_and_real_retry_fences(ready, monkeypatch):
    from app import tasks
    c, source, _ = ready; task = source['task_id']; enqueue = Mock(); send = Mock()
    monkeypatch.setattr(tasks.run_video_pipeline, 'apply_async', send)
    before_native = c.get(native.PREFIX + 'response:' + 'a' * 64)
    resume.schedule(source, enqueue, client=c)
    result = resume.run(task, resume.operation(task)); child = result['task_id']
    assert result['status'] == 'enqueued' and send.call_count == 1
    assert resume.run(task, resume.operation(task)) == {'status': 'already_started'}
    assert json.loads(c.get(jobs.JOB_PREFIX + task))['retry_child_task_id'] == child
    assert json.loads(c.get(jobs.JOB_PREFIX + child))['parent_id'] == task
    assert jobs.acquire_retry_child_execution(child, task) is True
    resume.verify_child(child, task, source['spec'], client=c)
    assert not resume.eligible(json.loads(c.get(jobs.JOB_PREFIX + child)))
    assert c.get(native.PREFIX + 'response:' + 'a' * 64) == before_native
    from app.services.channel_cadence import snapshot
    assert snapshot(CHANNEL, client=c)['counts']['produced']['long'] == 1


@pytest.mark.parametrize('damage', ['unknown_response', 'video_used', 'voice_exists', 'child', 'second_root', 'changed_provider'])
def test_only_captured_pre_speech_failure_can_continue(ready, damage):
    c, source, _ = ready; task = source['task_id']
    if damage == 'unknown_response': c.delete(native.PREFIX + 'response:' + 'a' * 64)
    if damage == 'video_used': source['paid_create_slots_used'] = 1
    if damage == 'voice_exists': source['audio_candidate_checkpoint'] = {'exists': True}
    if damage == 'child': source['retry_child_task_id'] = task
    if damage == 'second_root': source['parent_id'] = task
    if damage == 'changed_provider':
        response = json.loads(c.get(native.PREFIX + 'response:' + 'a' * 64)); response['http_status'] = 503
        c.set(native.PREFIX + 'response:' + 'a' * 64, plan._raw(response))
    c.set(jobs.JOB_PREFIX + task, plan._raw(source)); before = dump(c); enqueue = Mock()
    with pytest.raises((ValueError, TypeError)): resume.schedule(source, enqueue, client=c)
    enqueue.assert_not_called(); assert dump(c) == before


@pytest.mark.parametrize('damage', [None, 'count', 'digest', 'not_terminal', 'missing_response'])
def test_duration_resume_requires_the_observed_last_editorial_count(ready, monkeypatch, damage):
    import hashlib
    from cryptography.fernet import Fernet
    from app.services import production_included_router as router
    c, source, _ = ready; task = source['task_id']; cipher = Fernet(Fernet.generate_key())
    monkeypatch.setattr(router, '_cipher', lambda: cipher)
    source['error'] = 'Duration gate rejected script: 293 words for requested 3 min (target 300-330)'
    terminal = json.loads(c.get('celery-task-meta-' + task))
    terminal['result']['exc_message'] = [source['error']]
    terminal['result']['exc_type'] = 'ProductionContentError'
    c.set('celery-task-meta-' + task, plan._raw(terminal))
    c.set(jobs.JOB_PREFIX + task, plan._raw(source))
    scenes = [{'narration': ' '.join(['kanıt'] * (10 if i < 23 else 9))} for i in range(30)]
    if damage == 'count': scenes[-1]['narration'] += ' değişti'
    body = plan._raw({'candidates': [{'finishReason': 'MAX_TOKENS' if damage == 'not_terminal' else 'STOP',
        'content': {'parts': [{'text': json.dumps({'scenes': scenes})}]}}]}).encode()
    identity = 'd' * 64
    c.sadd(native.PREFIX + 'lineage:' + task, identity)
    c.set(native.PREFIX + 'request:' + identity, plan._raw({'context': {'lineage_id': task},
        'request_sha256': identity, 'purpose': 'editorial', 'reserved_at': '2026-09-25T01:00:00Z'}))
    if damage != 'missing_response':
        c.set(native.PREFIX + 'response:' + identity, plan._raw({'request_sha256': identity,
            'http_status': 200, 'encrypted_response': cipher.encrypt(body).decode(),
            'response_sha256': 'f' * 64 if damage == 'digest' else hashlib.sha256(body).hexdigest()}))
    before = dump(c)
    if damage:
        with pytest.raises((ValueError, TypeError)): resume.checked(c, task)
    else:
        observed, proof = resume.checked(c, task)
        assert observed['error'] == source['error'] and identity in proof
    assert dump(c) == before
