from copy import deepcopy
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

from celery.exceptions import Ignore
import pytest

from app.services import content_plan as plan, studio_state as jobs
from app.services import content_plan_retained_completion as completion, content_plan_recovery as recovery
from app.services import content_plan_interruption as interruption, production_worker_execution as execution
from app.services import commissioning_video as video, production_spend_runtime as runtime
from test_content_plan import case
from test_content_plan_motion_repair import failed_motion
from test_production_worker_execution import IDENTITY, NEW_INSTANCE


def running_motion(case, monkeypatch):
    parent, root, journal = failed_motion(case, monkeypatch)
    recovery.schedule(parent, Mock())
    task = recovery.run(parent['task_id'], completion.operation(parent['task_id']))['task_id']
    assert jobs.acquire_retry_child_execution(task, parent['task_id'])
    now = datetime.now(timezone.utc)
    job = jobs.get_job(task)
    job.update(state='PROGRESS', stage='visual_qc', progress=45, paid_create_slots_used=0,
        preview_total_paid_create_cap=32, audio_candidate_checkpoint={'audio_sha256': 'b' * 64},
        retained_long_media={'stock_only': True, 'new_tts_requests': 0},
        created_at=(now-timedelta(minutes=4)).isoformat(), updated_at=(now-timedelta(minutes=2)).isoformat())
    case.client.set(jobs.JOB_PREFIX+task, plan._raw(job))
    case.client.hset(jobs.PAID_CREATE_BUDGET_PREFIX+task, mapping={'cap':'32','used':'0'})
    current = {**IDENTITY, 'instance_id': NEW_INSTANCE, 'deployment_id': NEW_INSTANCE}
    for key,name in execution._ENV.items(): monkeypatch.setenv(name,current[key])
    monkeypatch.setattr(execution, '_client', lambda: case.client)
    observation={'source':'authenticated_railway_deployment_and_logs',
        **{key:IDENTITY[key] for key in ('project_id','environment_id','service_id','deployment_id','git_sha')},
        'status':'REMOVED','observed_at':now.isoformat(), 'deployment_response_sha256':'c'*64,
        'logs_sha256':'d'*64,'received_at':(now-timedelta(minutes=3)).isoformat(),
        'received_line':'[2026-09-23 21:47:42,761: INFO/MainProcess] Task app.tasks.run_video_pipeline['+task+'] received'}
    return job,root,journal,observation


def test_observed_removed_completion_gets_one_stock_only_repair_without_fabricated_celery_result(case,monkeypatch):
    source,root,journal,observation=running_motion(case,monkeypatch)
    before={k:case.client.dump(k) for k in case.client.scan_iter()}
    assert interruption.record_removed(source['task_id'],observation)['status']=='failure_recorded'
    failed=jobs.get_job(source['task_id'])
    assert failed['audio_candidate_checkpoint']==source['audio_candidate_checkpoint']
    assert not case.client.exists('celery-task-meta-'+source['task_id'])
    mutable={jobs.JOB_PREFIX+source['task_id'],interruption.PREFIX+source['task_id']}
    assert all(case.client.dump(k)==v for k,v in before.items() if k not in mutable)
    with pytest.raises(Ignore): execution.observe_start(SimpleNamespace(id=source['task_id'],retries=0))
    queue=Mock()
    assert recovery.schedule(failed,queue)=='retained_completion_preparing'
    assert recovery.schedule(failed,queue)=='retained_completion_reserved'
    queue.assert_called_once()
    child=recovery.run(source['task_id'],completion.operation(source['task_id']))['task_id']
    assert jobs.acquire_retry_child_execution(child,source['task_id'])
    _,_,proof=completion.verify_child(child,source['task_id'],source['spec'])
    assert proof['interruption_of']==source['parent_id'] and proof['interruption_sha256']
    scope={'foundation':SimpleNamespace(client=case.client),'context':journal['context']}
    token=runtime._TASK_ID.set(child)
    try: assert completion.stock_only_scope(scope) is True
    finally: runtime._TASK_ID.reset(token)
    assert plan._object(case.client.get(video.PREFIX+root))==journal
    with pytest.raises(Exception): interruption.record_removed(source['task_id'],observation)
    with pytest.raises(Exception):
        completion._prior_completion(case.client,{**failed,'task_id':child,'parent_id':source['task_id']})


@pytest.mark.parametrize('damage',['wrong_service','same_deployment','not_removed','old_observation',
    'wrong_task_log','after_progress','already_published','wrong_audio','paid_work','owner_stop','unknown_video'])
def test_removed_completion_requires_positive_evidence_and_preserves_uncertain_or_owner_stopped_work(case,monkeypatch,damage):
    source,root,journal,observation=running_motion(case,monkeypatch)
    task=source['task_id']
    if damage=='wrong_service': observation['service_id']=NEW_INSTANCE
    elif damage=='same_deployment': observation['deployment_id']=NEW_INSTANCE
    elif damage=='not_removed': observation['status']='CRASHED'
    elif damage=='old_observation': observation['observed_at']=(datetime.now(timezone.utc)-timedelta(minutes=10)).isoformat()
    elif damage=='wrong_task_log': observation['received_line']=observation['received_line'].replace(task,root)
    elif damage=='after_progress': observation['received_at']=observation['observed_at']
    elif damage=='already_published': source.update(state='SUCCESS',result={'video_key':'approved'})
    elif damage=='wrong_audio': source['audio_candidate_checkpoint']['audio_sha256']='f'*64
    elif damage=='paid_work': source['paid_create_slots_used']=1
    elif damage=='owner_stop': case.client.set(jobs.RENDER_CANCELLATION_PREFIX+root,'stop')
    else:
        next(r for r in journal['requests'].values() if r['create']['http_status']==200)['result']=None
        case.client.set(video.PREFIX+root,plan._raw(journal))
    case.client.set(jobs.JOB_PREFIX+task,plan._raw(source))
    before={k:case.client.dump(k) for k in case.client.scan_iter()}
    with pytest.raises(Exception): interruption.record_removed(task,observation)
    assert {k:case.client.dump(k) for k in case.client.scan_iter()}==before
