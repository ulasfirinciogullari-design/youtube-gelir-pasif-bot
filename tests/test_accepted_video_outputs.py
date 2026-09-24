"""A captured paid result can be reused; it cannot open another create slot."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.services import accepted_video_outputs as outputs, content_plan as plan, studio_state as jobs
from app.services import content_plan_retained_completion as completion, content_plan_recovery as recovery
from app.services import commissioning_video as video, production_spend_runtime as runtime, youtube_auth
from test_content_plan import case
from test_content_plan_visual_completion import failed_stock_completion


def observed(payload, status=200):
    raw = plan._raw(payload)
    return {'http_status': status, 'response_sha256': video._sha(raw.encode()),
        'encrypted_response': youtube_auth._encrypt_json({'response': raw})}


def failed_save(case, monkeypatch):
    parent, root, journal = failed_stock_completion(case, monkeypatch)
    recovery.schedule(parent, Mock())
    task = recovery.run(parent['task_id'], completion.operation(parent['task_id']))['task_id']
    assert jobs.acquire_retry_child_execution(task, parent['task_id'])
    job = jobs.get_job(task)
    diagnostic = {'stage': 'after_rescue', 'accepted': 29, 'total': 30,
        'rejected': {'17': {'score': 70, 'reason': 'Existing alternative is insufficient.'}},
        'repair_checkpoint_available': False, 'provider_generation_failures': {
            'failures': [{'exception_class': 'WatchError', 'scene_index': 17, 'stage': 'final_repair'}]}}
    error = completion.VISUAL_ERROR + plan._raw(diagnostic)
    job.update(state='FAILURE', stage='failed', failure_stage='final_visual_qc_rescue', error=error,
        paid_create_slots_used=1, preview_total_paid_create_cap=32,
        audio_candidate_checkpoint={'audio_sha256': 'b' * 64},
        generated_asset_candidates={'failed_count': 0, 'attempted_count': 1, 'preserved_count': 1,
            'entries': [{'package_sha256': 'a' * 64}]},
        retained_long_media={'stock_only': False, 'new_tts_requests': 0},
        failure_classification={'category': 'content_rejected', 'code': 'visual_quality_exhausted',
            'error_sha256': video._sha(error.encode())})
    case.client.set(jobs.JOB_PREFIX + task, plan._raw(job))
    case.client.hset(jobs.PAID_CREATE_BUDGET_PREFIX + task, mapping={'cap': '32', 'used': '1'})
    case.client.set('celery-task-meta-' + task, plan._raw({'task_id': task, 'status': 'FAILURE',
        'result': {'exc_type': 'FinalVisualQualityError', 'exc_message': [error]},
        'traceback': 'File "tasks.py", line 1, in run_video_pipeline\n'}))
    descriptor = {'model': 'fal-ai/veo3.1/lite', 'scene_index': 17,
        'continuation_task_id': task, 'package_sha256': 'a' * 64}
    identity = video._sha(video._raw(descriptor).encode()); request = str(uuid4())
    journal['requests'][identity] = {'request': descriptor,
        'create': observed({'request_id': request, 'response_url': 'https://queue.fal.run/fal-ai/veo3.1/requests/' + request}),
        'result': observed({'video': {'url': 'https://v3.fal.media/files/accepted.mp4', 'content_type': 'video/mp4'}})}
    case.client.set(video.PREFIX + root, plan._raw(journal))
    return job, root, journal, identity


def test_saved_completed_result_admits_once_with_zero_fresh_video_or_voice(case, monkeypatch):
    source, root, journal, identity = failed_save(case, monkeypatch)
    before = case.client.get(video.PREFIX + root)
    queue = Mock()
    assert recovery.schedule(source, queue) == 'retained_completion_preparing'
    assert recovery.schedule(source, queue) == 'retained_completion_reserved'
    queue.assert_called_once()
    child = recovery.run(source['task_id'], completion.operation(source['task_id']))['task_id']
    assert jobs.acquire_retry_child_execution(child, source['task_id'])
    _, _, proof = completion.verify_child(child, source['task_id'], source['spec'])
    assert proof['accepted_outputs'] == {'17': identity} and proof['accepted_output_of']
    assert proof['transcript']['audio_sha256'] == 'b' * 64
    scope = {'foundation': SimpleNamespace(client=case.client), 'context': journal['context'],
        'scene_index': 17}
    token = runtime._TASK_ID.set(child)
    try:
        for index in range(30):
            assert completion.stock_only_scope({**scope, 'scene_index': index}) is True
    finally: runtime._TASK_ID.reset(token)
    assert case.client.get(video.PREFIX + root) == before
    with pytest.raises(Exception):
        completion._prior_completion(case.client, {**source, 'task_id': child, 'parent_id': source['task_id']})


@pytest.mark.parametrize('damage', ['missing_result', 'filter', 'wrong_story', 'wrong_scene',
    'bad_url', 'changed_failure', 'owner_stop', 'changed_receipt', 'wrong_voice'])
def test_unsafe_or_incomplete_result_cannot_start_new_work(case, monkeypatch, damage):
    source, root, journal, identity = failed_save(case, monkeypatch)
    row = journal['requests'][identity]
    if damage == 'missing_result': row['result'] = None
    if damage == 'filter': row['result'] = observed({'detail': [{'type': 'content_policy_violation'}]}, 422)
    if damage == 'wrong_story': source['generated_asset_candidates']['entries'][0]['package_sha256'] = 'f' * 64
    if damage == 'wrong_scene': row['request']['scene_index'] = 19
    if damage == 'bad_url': row['result'] = observed({'video': {'url': 'https://untrusted.invalid/video.mp4'}})
    if damage == 'changed_failure': source['failure_classification']['error_sha256'] = 'f' * 64
    if damage == 'owner_stop': case.client.set(jobs.RENDER_CANCELLATION_PREFIX + root, 'stopped')
    if damage == 'changed_receipt': next(r for r in journal['requests'].values() if r['create']['http_status'] == 429)['tampered'] = True
    if damage == 'wrong_voice': source['audio_candidate_checkpoint']['audio_sha256'] = 'f' * 64
    case.client.set(jobs.JOB_PREFIX + source['task_id'], plan._raw(source))
    case.client.set(video.PREFIX + root, plan._raw(journal))
    before = {key: case.client.dump(key) for key in case.client.scan_iter()}
    enqueue = Mock()
    with pytest.raises(Exception): recovery.schedule(source, enqueue)
    enqueue.assert_not_called()
    assert {key: case.client.dump(key) for key in case.client.scan_iter()} == before
