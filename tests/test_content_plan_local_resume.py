from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.services import content_plan as plan, content_plan_recovery as recovery
from app.services import content_plan_local_resume as resume, studio_state as jobs
from test_content_plan_recovery import case, failed


def prepared_child(case):
    root = failed(case); child_id = str(uuid4())
    record = {'source_task_id': root['task_id'], 'source_sha256': recovery._fingerprint(root),
              'approved_package': {'scenes': ['unchanged']},
              'manifest': {'kind': 'owner_plan_retained', 'source_task_id': root['task_id']}}
    case.client.set(recovery.LEGACY_PREFIX + 'v3:record:' + root['task_id'], plan._raw(record))
    root.update(retry_child_task_id=child_id, retry_claimed=True)
    child = {'task_id': child_id, 'parent_id': root['task_id'], 'kind': 'render', 'spec': root['spec'],
        'state': 'FAILURE', 'failure_stage': 'ai_scene_recovery', 'error': resume.ERROR,
        'paid_create_slots_used': 0, 'preview_total_paid_create_cap': 6}
    for job in (root, child): case.client.set(jobs.JOB_PREFIX + job['task_id'], plan._raw(job))
    case.client.hset(jobs.RETRY_CHILD_CLAIM_PREFIX + child_id,
        mapping={'source_task_id': root['task_id'], 'token': 'first-single-use-claim'})
    case.client.set(jobs.RETRY_CHILD_EXECUTION_PREFIX + child_id, 'first-single-use-claim')
    case.client.hset(jobs.PAID_CREATE_BUDGET_PREFIX + child_id, mapping={'cap': '6', 'used': '0'})
    case.client.set('celery-task-meta-' + child_id, plan._raw({'task_id': child_id, 'status': 'FAILURE',
        'result': {'exc_type': 'FinalVisualQualityError', 'exc_message': [resume.ERROR]},
        'traceback': 'File "/app/app/tasks.py", line 6647, in run_video_pipeline\n'
        + f"FileNotFoundError: /tmp/youtube_factory/{child_id}_attempt_0/recovered_s00_00.mp4"}))
    return root, child, record


@pytest.mark.parametrize('damage', [None, 'unknown', 'quality', 'charge', 'cancel', 'source', 'claim'])
def test_only_known_local_file_failure_can_dispatch_once_and_preserves_all_history(case, damage):
    root, source, record = prepared_child(case); task = source['task_id']
    if damage == 'unknown': case.client.delete('celery-task-meta-' + task)
    if damage == 'quality': source['error'] = 'The visual critic rejected this scene'
    if damage == 'charge': case.client.hset(jobs.PAID_CREATE_BUDGET_PREFIX + task, 'used', '1')
    if damage == 'cancel': case.client.set(jobs.RENDER_CANCELLATION_PREFIX + task, 'owner stopped')
    if damage == 'source': source['spec'] = {**source['spec'], 'topic': 'changed'}
    if damage == 'claim': case.client.delete(jobs.RETRY_CHILD_EXECUTION_PREFIX + task)
    case.client.set(jobs.JOB_PREFIX + task, plan._raw(source))
    before = {key: case.client.dump(key) for key in case.client.scan_iter()}
    queue = Mock(side_effect=TimeoutError('lost ACK'))
    plan.maintain([case.profile], Mock(), repair_enqueue=queue)
    plan.maintain([case.profile], Mock(), repair_enqueue=queue)
    assert queue.call_count == (1 if damage is None else 0)
    assert all(case.client.dump(key) == value for key, value in before.items())
    assert not case.client.exists(plan.COMPLETION_PREFIX + case.a['id'])
    assert not case.client.exists(plan.DISPATCH_PREFIX + case.b['id'])


@pytest.mark.parametrize('damage', [None, 'package', 'claim', 'dispatch', 'record'])
def test_second_child_reuses_exact_root_record_and_actual_claim(case, damage):
    root, source, record = prepared_child(case); task = source['task_id']; grandchild = str(uuid4())
    resume.schedule(source, Mock())
    case.client.set(resume.EXECUTION + task, resume.operation(task))
    source['retry_child_task_id'] = grandchild
    case.client.set(jobs.JOB_PREFIX + task, plan._raw(source))
    case.client.set(jobs.JOB_PREFIX + grandchild, plan._raw({'task_id': grandchild, 'parent_id': task,
        'kind': 'render', 'spec': source['spec'], 'state': 'PROGRESS'}))
    case.client.hset(jobs.RETRY_CHILD_CLAIM_PREFIX + grandchild,
        mapping={'source_task_id': task, 'token': 'second-single-use-claim'})
    case.client.set(jobs.RETRY_CHILD_EXECUTION_PREFIX + grandchild, 'second-single-use-claim')
    package, manifest = deepcopy(record['approved_package']), deepcopy(record['manifest'])
    if damage == 'package': package['scenes'] = ['changed']
    if damage == 'claim': case.client.delete(jobs.RETRY_CHILD_EXECUTION_PREFIX + grandchild)
    if damage == 'dispatch': case.client.delete(resume.EXECUTION + task)
    if damage == 'record':
        case.client.set(recovery.RECORD + root['task_id'], plan._raw(record))
    for parent_argument in (task, root['task_id']):
        if damage:
            with pytest.raises(plan.ContentPlanError):
                recovery.verify_child(grandchild, parent_argument, source['spec'], package, manifest)
        else:
            assert recovery.verify_child(grandchild, parent_argument, source['spec'], package, manifest) == root


def test_loader_and_actual_paid_reuse_loop_use_the_same_verified_file(monkeypatch, tmp_path):
    from app import tasks
    from app.services import storage
    from test_production_paid_reuse_selection import _runtime, _select, _run_primary_loop
    source = {'task_id': str(uuid4()), 'spec': {}}
    voice = {'scene_durations': [4.8]*6}
    ns = _runtime(); media = ns['recovered_generated_media']
    row = media['scenes'][3][0]
    content = b'\0\0\0\x18ftypmp42' + b'private retained media' * 100
    import hashlib
    row.update(size=len(content), sha256=hashlib.sha256(content).hexdigest())
    def stored(client, key, sha, size, target, maximum):
        target.write_bytes(content); return target
    monkeypatch.setattr(recovery, '_stored', stored)
    monkeypatch.setattr(recovery, 'verify_child', Mock(return_value=source))
    monkeypatch.setattr(storage, '_client', lambda: object())
    manifest = {'stocks': {str(i): {'key': str(i), 'sha256': row['sha256'], 'size': len(content),
        'spec': {'pexels_id': 100+i, 'source_type': 'stock'}} for i in (0,1,2,4,5)}, 'credits': []}
    pools = recovery.load_visuals(manifest, source, {}, media, voice, 'child', tmp_path)['scene_visuals']
    ns['scene_visuals'] = pools
    monkeypatch.setattr(tasks, 'media_duration', lambda _: 8.)
    monkeypatch.setattr(tasks, 'video_frame_count', lambda _: 240)
    _select(ns)
    assert _run_primary_loop(ns, tmp_path) == []  # No new downloads/generations.
    assert Path(ns['scene_visuals'][3][0]['path']).read_bytes() == content
    tasks._validate_recovered_generated_clip(ns['scene_visuals'][3][0]['path'], 5.35,
        expected_size=row['size'], expected_sha256=row['sha256'])


@pytest.mark.parametrize('damage', [None, 'unknown', 'different_error', 'ready'])
def test_only_pretransport_oversized_stock_review_admits_v4_preparation(case, damage):
    source = failed(case); task = source['task_id']; operation = str(uuid4())
    prefix = recovery.LEGACY_PREFIX + 'v3:'
    case.client.set(prefix + 'dispatch:' + task, plan._raw({'task_id': operation,
        'source_sha256': recovery._fingerprint(source)}))
    case.client.set(prefix + 'execution:' + task, operation)
    case.client.set(prefix + 'status:' + task, plan._raw({'state': 'stopped', 'error_type': 'AbacusRouterError'}))
    if damage != 'unknown':
        case.client.set('celery-task-meta-' + operation, plan._raw({'status': 'FAILURE',
            'result': {'exc_type': 'AbacusRouterError', 'exc_message': [
                'abacus_router_request_invalid' if damage != 'different_error' else 'provider_unavailable']},
            'traceback': 'File "/app/app/services/abacus_router_adapter.py", line 137, in _inspect_body\n'
            + 'File "/app/app/services/content_plan_stock_repair.py", line 91, in select'}))
    if damage == 'ready': case.client.set(prefix + 'record:' + task, 'private')
    before = {k: case.client.dump(k) for k in case.client.scan_iter()}
    queue = Mock(); recovery.schedule(source, queue); recovery.schedule(source, queue)
    assert queue.call_count == (1 if damage is None else 0)
    assert all(case.client.dump(k) == v for k, v in before.items())
