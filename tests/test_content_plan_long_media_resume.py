from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet

from app.services import content_plan as plan, studio_state as jobs, youtube_auth
from app.services import content_plan_long_media_resume as resume, content_plan_recovery as recovery
from app.services import commissioning_video as video, production_spend_runtime as runtime
from test_content_plan import case
from test_content_plan_voice_resume import candidate_failure


def captured_failure(case, monkeypatch):
    source, root = candidate_failure(case, monkeypatch, child=True, long=True); task = source['task_id']
    cipher = Fernet(Fernet.generate_key()); monkeypatch.setattr(youtube_auth, '_fernet', lambda: cipher)
    source.update(failure_stage='ai_scene_generation', error=resume.ERROR, paid_create_slots_used=2,
        generated_asset_candidates={'attempted_count': 1, 'preserved_count': 1, 'failed_count': 0,
                                    'entries': [{'scene_index': 0}]})
    case.client.set(jobs.JOB_PREFIX + task, plan._raw(source))
    case.client.hset(jobs.PAID_CREATE_BUDGET_PREFIX + task, mapping={'cap': '32', 'used': '2'})
    case.client.set('celery-task-meta-' + task, plan._raw({'task_id': task, 'status': 'FAILURE',
        'result': {'exc_type': 'SpendBlocked', 'exc_message': [resume.ERROR]},
        'traceback': 'File "commissioning_video.py", line 1, in _result\n'}))
    def observed(payload):
        raw = plan._raw(payload)
        return {'http_status': 200, 'response_sha256': hashlib.sha256(raw.encode()).hexdigest(),
                'encrypted_response': youtube_auth._encrypt_json({'response': raw})}
    context = {'lineage_id': root, 'kind': 'long', 'channel_id': source['spec']['production_channel_id'],
               'connection_id': source['spec']['production_connection_id']}
    records = {}
    for index in range(2):
        descriptor = {'model': video.MODEL, 'scene_index': index}
        name = f'models/{video.MODEL}/operations/captured_{index}'
        payload = {'name': name, 'done': True}
        if index:
            payload['error'] = {'code': 14, 'message': 'Temporary high demand'}
        else:
            payload['response'] = {'generateVideoResponse': {'generatedSamples': [
                {'video': {'uri': 'https://generativelanguage.googleapis.com/v1beta/files/movie:download?alt=media'}}]}}
        identity = video._sha(video._raw(descriptor).encode())
        records[identity] = {'request': descriptor, 'create': observed({'name': name}), 'result': observed(payload)}
    value = {'version': 1, 'context': context, 'requests': records}
    case.client.set(video.PREFIX + root, plan._raw(value))
    return source, root, value, observed


@pytest.mark.parametrize('damage', [None, 'unknown', 'refusal', 'unfinished', 'missing_clip', 'counter',
    'cancelled_root', 'changed_spec', 'missing_terminal', 'changed_hash'])
def test_only_fully_captured_outage_with_complete_retained_originals_can_resume(case, monkeypatch, damage):
    source, root, value, observed = captured_failure(case, monkeypatch); task = source['task_id']
    outage = next(row for row in value['requests'].values() if row['request']['scene_index'] == 1)
    if damage == 'unknown': outage['result'] = None
    if damage in {'refusal', 'unfinished'}:
        payload = video._payload(outage['result'])
        if damage == 'refusal': payload['error']['code'] = 403
        else: payload['done'] = False
        outage['result'] = observed(payload)
    if damage == 'changed_hash': outage['result']['response_sha256'] = 'a' * 64
    if damage == 'missing_clip': source['generated_asset_candidates']['entries'] = []
    if damage == 'counter': case.client.hset(jobs.PAID_CREATE_BUDGET_PREFIX + task, 'used', '1')
    if damage == 'cancelled_root': case.client.set(jobs.RENDER_CANCELLATION_PREFIX + root, 'stop')
    if damage == 'changed_spec': source['spec'] = {**source['spec'], 'topic': 'Unrelated new story'}
    if damage == 'missing_terminal': case.client.delete('celery-task-meta-' + task)
    case.client.set(video.PREFIX + root, plan._raw(value)); case.client.set(jobs.JOB_PREFIX + task, plan._raw(source))
    before = {k: case.client.dump(k) for k in case.client.scan_iter()}; queue = Mock(side_effect=TimeoutError())
    if damage:
        with pytest.raises((plan.ContentPlanError, ValueError, RuntimeError)):
            recovery.schedule(source, queue)
        queue.assert_not_called()
    else:
        assert recovery.schedule(source, queue) == 'long_media_uncertain'
        assert recovery.schedule(source, queue) == 'long_media_reserved'
        queue.assert_called_once()
    assert all(case.client.dump(k) == v for k, v in before.items())


def test_private_child_keeps_original_root_receipts_and_cannot_repeat_dispatch(case, monkeypatch):
    from app import tasks
    source, root, value, _ = captured_failure(case, monkeypatch); task = source['task_id']
    old = case.client.get(video.PREFIX + root)
    recovery.schedule(source, Mock()); result = recovery.run(task, resume.operation(task)); child = result['task_id']
    assert result['new_voice_requests'] == 0 and recovery.run(task, resume.operation(task)) == {'status': 'already_started'}
    assert jobs.acquire_retry_child_execution(child, task)
    resume.verify_child(child, task, source['spec'])
    assert resume.retained_cap(child, {'content_plan_media_source': task}) == 32
    assert tasks.run_video_pipeline.apply_async.call_args.kwargs['args'][5:] == (None, task)
    scope = {'context': value['context'], 'foundation': SimpleNamespace(client=case.client)}
    token = runtime._TASK_ID.set(child)
    try:
        assert resume.continuation_identity(scope) == child
        value['requests']['new-child-unknown'] = {'request': {'continuation_task_id': child}, 'create': None, 'result': None}
        case.client.set(video.PREFIX + root, plan._raw(value))
        # An ambiguous new request keeps its identity; the provider adapter's
        # existing reserve operation still forbids a repeated POST for it.
        assert resume.continuation_identity(scope) == child
        value['requests']['new-child-unknown']['request']['continuation_task_id'] = 'another-child'
        case.client.set(video.PREFIX + root, plan._raw(value))
        with pytest.raises(plan.ContentPlanError): resume.continuation_identity(scope)
    finally: runtime._TASK_ID.reset(token)
    assert all(value['requests'][key] == row for key, row in json.loads(old)['requests'].items())


def test_only_in_process_verified_candidates_can_enter_full_visual_review():
    clip = {'path': '/private/retained.mp4', 'generated': True, 'forbid_loop': True}
    raw = {'content_plan_media_source': 'source', 'retained_long_clips': {2: clip}}
    visuals = [[{'path': f'/stock/{i}.mp4'}] for i in range(30)]; before = deepcopy(visuals)
    with pytest.raises(plan.ContentPlanError): resume.install(raw, visuals)
    assert visuals == before
    raw['retained_long_clips'] = resume.RetainedClips({2: clip}, token=resume._TOKEN)
    assert resume.install(raw, visuals) == [2]
    assert visuals[2] == [clip] and all(visuals[i] == before[i] for i in range(30) if i != 2)
    assert visuals[2][0] is not clip and 'qa_approved' not in visuals[2][0]
