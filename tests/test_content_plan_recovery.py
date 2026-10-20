from concurrent.futures import ThreadPoolExecutor
import json
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.services import content_plan as plan, content_plan_recovery as recovery, studio_state as jobs
from test_content_plan import case, CHANNEL


def failed(case):
    queue = Mock(); plan.maintain([case.profile], queue)
    task = queue.call_args.kwargs['task_id']
    source = json.loads(case.client.get(jobs.JOB_PREFIX + task))
    source.update(state='FAILURE', stage='failed', failure_stage='render',
        audio_candidate_checkpoint={'status': 'unapproved_candidate'},
        included_stock_pools={'retained': {}}, paid_create_slots_used=2,
        generated_asset_candidates={'preserved_count': 2, 'attempted_count': 2, 'failed_count': 0})
    case.client.set(jobs.JOB_PREFIX + task, plan._raw(source))
    return source


def test_concurrent_maintenance_sends_one_recovery_without_advancing(case):
    source = failed(case); enqueue = Mock(); normal = Mock()
    before = case.client.get(jobs.JOB_PREFIX + source['task_id'])
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda _: plan.maintain([case.profile], normal, repair_enqueue=enqueue), range(6)))
    assert enqueue.call_count == 1 and enqueue.call_args.kwargs['retry'] is False
    assert case.client.get(jobs.JOB_PREFIX + source['task_id']) == before
    normal.assert_not_called()
    assert not case.client.exists(plan.COMPLETION_PREFIX + case.a['id'])
    assert not case.client.exists(plan.DISPATCH_PREFIX + case.b['id'])


def test_lost_recovery_queue_ack_never_replays(case):
    source = failed(case); enqueue = Mock(side_effect=TimeoutError())
    assert recovery.schedule(source, enqueue) == 'repair_dispatch_uncertain'
    assert recovery.schedule(source, enqueue) == 'repair_preparing_or_stopped'
    assert enqueue.call_count == 1


@pytest.mark.parametrize('outcome', ['known_local', 'unknown', 'critic_failed', 'already_recorded'])
def test_upgrade_only_resumes_observed_local_preparation_error_without_erasing_old_claims(case, outcome):
    source = failed(case); task = source['task_id']; operation = str(uuid4())
    legacy = recovery.LEGACY_PREFIX
    case.client.set(legacy + 'dispatch:' + task, plan._raw({'task_id': operation,
        'source_sha256': recovery._fingerprint(source)}))
    case.client.set(legacy + 'execution:' + task, operation)
    case.client.set(legacy + 'status:' + task, plan._raw({'state': 'stopped', 'error_type': 'ContentPlanError'}))
    if outcome != 'unknown':
        case.client.set('celery-task-meta-' + operation, plan._raw({'status': 'FAILURE',
            'result': {'exc_type': 'ContentPlanError'}, 'traceback':
            'File "/app/app/services/content_plan_recovery.py", line '
            + ('140' if outcome != 'critic_failed' else '184') + ', in prepare'}))
    if outcome == 'already_recorded': case.client.set(legacy + 'record:' + task, 'private record')
    prior = {k:case.client.dump(k) for k in case.client.scan_iter()}
    enqueue = Mock()
    recovery.schedule(source, enqueue)
    assert enqueue.call_count == (1 if outcome == 'known_local' else 0)
    assert all(case.client.dump(k) == v for k,v in prior.items())
    recovery.schedule(source, enqueue)
    assert enqueue.call_count == (1 if outcome == 'known_local' else 0)


@pytest.mark.parametrize('change', [
    {'failure_stage': 'final_visual_qc'}, {'paid_create_slots_used': 3},
    {'parent_id': 'prior-attempt'}, {'retry_child_task_id': str(uuid4())},
    {'audio_candidate_checkpoint': None}, {'included_stock_pools': {}},
    {'generated_asset_candidates': {'preserved_count': 2, 'attempted_count': 3, 'failed_count': 1}},
])
def test_quality_rejections_missing_artifacts_and_unknown_creates_do_not_retry(case, change):
    source = failed(case); source.update(change); queue = Mock()
    assert recovery.schedule(source, queue) == 'working_or_blocked'
    queue.assert_not_called()


@pytest.mark.parametrize('fence', ['owner', 'hold', 'upload', 'changed_spec', 'changed_profile', 'reconnect'])
def test_preparation_rechecks_live_owner_and_publication_binding(case, fence):
    from app.services import source_publication_hold, youtube_publish_state
    source = failed(case); task = source['task_id']
    assert recovery._source(case.client, task) == source
    if fence == 'owner':
        case.client.set(jobs.RENDER_CANCELLATION_PREFIX + task, 'owner stopped')
    elif fence == 'hold':
        case.client.set(source_publication_hold.HOLD_PREFIX + task, 'hold')
    elif fence == 'upload':
        case.client.set(youtube_publish_state.UPLOAD_PREFIX + task, 'existing upload')
    elif fence == 'changed_spec':
        source['spec']['topic'] = 'different'; case.client.set(jobs.JOB_PREFIX + task, plan._raw(source))
    elif fence == 'changed_profile':
        profile = {**case.profile, 'profile_revision': 'changed'}
        case.client.set(plan.production.PROFILE_PREFIX + CHANNEL, plan._raw(profile))
    else:
        key = plan.production.OAUTH_CHANNEL_PREFIX + CHANNEL
        value = json.loads(case.client.get(key)); value['requires_reconnect'] = True
        case.client.set(key, plan._raw(value))
    with pytest.raises(plan.ContentPlanError):
        recovery._source(case.client, task)


def test_duplicate_prepare_delivery_cannot_enter_story_or_media(case, monkeypatch):
    source = failed(case); queue = Mock(); recovery.schedule(source, queue)
    operation = queue.call_args.kwargs['task_id']
    case.client.set(recovery.EXECUTION + source['task_id'], operation)
    prepare = Mock(side_effect=AssertionError('must not prepare again'))
    monkeypatch.setattr(recovery, 'prepare', prepare)
    assert recovery.run(source['task_id'], operation) == {'status': 'already_started'}
    prepare.assert_not_called()


@pytest.mark.parametrize('damage', [None, 'package', 'manifest', 'claim', 'source', 'wrong_child'])
def test_retained_worker_requires_exact_private_record_and_executed_claim(case, damage):
    source = failed(case); task = source['task_id']; child = str(uuid4())
    record = {'source_sha256': recovery._fingerprint(source),
              'approved_package': {'scenes': ['immutable']},
              'manifest': {'kind': 'owner_plan_retained', 'source_task_id': task}}
    case.client.set(recovery.RECORD + task, plan._raw(record))
    source.update(retry_child_task_id=child, retry_claimed=True)
    case.client.set(jobs.JOB_PREFIX + task, plan._raw(source))
    case.client.set(jobs.JOB_PREFIX + child, plan._raw({'task_id': child, 'kind': 'render',
        'parent_id': task, 'state': 'PROGRESS', 'spec': source['spec']}))
    case.client.hset(jobs.RETRY_CHILD_CLAIM_PREFIX + child,
                    mapping={'source_task_id': task, 'token': 'single-use-claim-token'})
    case.client.set(jobs.RETRY_CHILD_EXECUTION_PREFIX + child, 'single-use-claim-token')
    package, manifest = record['approved_package'], record['manifest']
    if damage == 'package': package = {'scenes': ['changed']}
    if damage == 'manifest': manifest = {'kind': 'unverified'}
    if damage == 'claim': case.client.delete(jobs.RETRY_CHILD_EXECUTION_PREFIX + child)
    if damage == 'source':
        source['paid_create_slots_used'] = 3
        case.client.set(jobs.JOB_PREFIX + task, plan._raw(source))
    if damage == 'wrong_child': child = str(uuid4())
    if damage:
        with pytest.raises((plan.ContentPlanError, ValueError)):
            recovery.verify_child(child, task, source['spec'], package, manifest)
    else:
        assert recovery.verify_child(child, task, source['spec'], package, manifest) == source
