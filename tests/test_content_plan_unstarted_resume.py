"""The thirteenth retained node failed before every execution/paid-work fence."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4, uuid5, NAMESPACE_URL

import fakeredis
import pytest

from app.services import content_plan as plan, studio_state as jobs


def chain(client, length):
    entry = str(uuid4()); previous = None; rows = []
    for _ in range(length):
        row = {'task_id': str(uuid4()), 'parent_id': previous, 'kind': 'render',
            'state': 'FAILURE', 'spec': {'content_plan_item_id': entry}}
        if rows:
            rows[-1]['retry_child_task_id'] = row['task_id']
        rows.append(row); previous = row['task_id']
    rows[-1].update(state='PENDING', stage='queued', progress=0)
    for row in rows: client.set(jobs.JOB_PREFIX+row['task_id'], plan._raw(row))
    dispatch = {'task_id': rows[0]['task_id'], 'item': {'id': entry}}
    client.set(plan.DISPATCH_PREFIX+entry, plan._raw(dispatch))
    return rows, dispatch


def test_thirteenth_verified_node_reaches_original_execution_fence(monkeypatch):
    from celery.exceptions import Ignore
    client = fakeredis.FakeRedis(decode_responses=True); rows, dispatch = chain(client, 13)
    monkeypatch.setattr(plan, '_client', lambda: client)
    assert plan._leaf(client, dispatch) == rows[-1]
    request = SimpleNamespace(id=rows[-1]['task_id'], retries=0)
    plan.observe_execution(request)
    with pytest.raises(Ignore): plan.observe_execution(request)
    assert client.exists(plan.EXECUTION_PREFIX+request.id+':0')


@pytest.mark.parametrize('damage', ['cycle', 'parent', 'foreign_item', 'too_deep'])
def test_lineage_stays_bounded_and_bound_to_original_queue(damage):
    client = fakeredis.FakeRedis(decode_responses=True)
    rows, dispatch = chain(client, 17 if damage == 'too_deep' else 13)
    if damage == 'cycle': rows[-1]['retry_child_task_id'] = rows[0]['task_id']
    if damage == 'parent': rows[-1]['parent_id'] = str(uuid4())
    if damage == 'foreign_item': rows[-1]['spec']['content_plan_item_id'] = str(uuid4())
    client.set(jobs.JOB_PREFIX+rows[-1]['task_id'], plan._raw(rows[-1]))
    with pytest.raises(plan.ContentPlanError, match='plan_lineage_invalid'): plan._leaf(client, dispatch)


@pytest.fixture
def pending(monkeypatch):
    from datetime import datetime, timezone, timedelta
    from app.services import content_plan_retained_completion as retained, content_plan_unstarted_resume as resume
    client=fakeredis.FakeRedis(decode_responses=True); parent=str(uuid4()); root=str(uuid4())
    task=str(uuid5(NAMESPACE_URL,'owner-plan-retained-completion-child:v1:'+parent))
    source={'task_id':task,'parent_id':parent,'kind':'render','state':'PENDING','stage':'queued','progress':0,
        'spec':{'topic':'Original source-backed story','duration_minutes':3,'language':'en','channel_id':'margin',
            'content_plan_item_id':str(uuid4())},'result':None,'error':None}
    terminal={'task_id':task,'status':'FAILURE','date_done':(datetime.now(timezone.utc)-timedelta(seconds=5)).isoformat(),
        'result':{'exc_type':'ContentPlanError','exc_module':'app.services.content_plan','exc_message':['plan_lineage_invalid']},
        'traceback':'production_spend_runtime.py, in wrapped\n content_plan.py, in observe_execution\n content_plan.py, in _leaf\n'}
    client.set(jobs.JOB_PREFIX+task,plan._raw(source));client.set('celery-task-meta-'+task,plan._raw(terminal))
    client.hset(jobs.RETRY_CHILD_CLAIM_PREFIX+task,mapping={'source_task_id':parent,'token':'original-long-retry-token'})
    proof={'accepted_output_of':parent,'mode':'sources'}
    old={'task_id':parent,'retry_child_task_id':task,'spec':source['spec']}
    claim={'task_id':retained.operation(parent),'source_task_id':parent}
    client.set(retained.DISPATCH+parent,plan._raw(claim));client.set(retained._root_key(root,proof),plan._raw(claim))
    client.set(retained.EXECUTION+parent,retained.operation(parent))
    monkeypatch.setattr(retained,'checked',Mock(return_value=(old,root,proof)))
    monkeypatch.setattr(retained,'_claim',lambda *args:claim)
    return SimpleNamespace(client=client,source=source,terminal=terminal,task=task,parent=parent,
        resume=resume,retained=retained,proof=proof)


def test_same_id_resumes_once_and_archives_terminal_without_resetting_originals(pending):
    p=pending;enqueue=Mock();before={k:p.client.dump(k)for k in p.client.scan_iter()}
    assert p.resume.schedule(p.source,enqueue,client=p.client)=='unstarted_resume_enqueued'
    assert p.resume.schedule(p.source,enqueue,client=p.client)=='unstarted_resume_reserved'
    assert enqueue.call_count==1 and enqueue.call_args.kwargs['task_id']==p.task
    assert enqueue.call_args.kwargs['args'][-1]==p.parent and enqueue.call_args.kwargs['retry']is False
    archive=plan._object(p.client.get(p.resume.PREFIX+p.task))
    assert archive['terminal']==plan._raw(p.terminal) and archive['source_job']==plan._raw(p.source)
    assert all(p.client.dump(k)==value for k,value in before.items())


def test_unknown_broker_reply_never_resends(pending):
    p=pending;enqueue=Mock(side_effect=TimeoutError('unknown delivery'))
    assert p.resume.schedule(p.source,enqueue,client=p.client)=='unstarted_resume_uncertain'
    assert p.resume.schedule(p.source,enqueue,client=p.client)=='unstarted_resume_reserved'
    assert enqueue.call_count==1


@pytest.mark.parametrize('witness',['execution0','execution2','retry','worker','media','upload','hold','cancel'])
def test_any_execution_or_owner_stop_blocks_resume(pending,witness):
    from app.services import production_worker_execution as worker
    from app.services.source_publication_hold import HOLD_PREFIX
    from app.services.youtube_publish_state import UPLOAD_PREFIX
    p=pending;keys={'execution0':plan.EXECUTION_PREFIX+p.task+':0','execution2':plan.EXECUTION_PREFIX+p.task+':2',
        'retry':jobs.RETRY_CHILD_EXECUTION_PREFIX+p.task,'worker':worker.CURRENT_PREFIX+p.task,
        'media':jobs.PAID_CREATE_BUDGET_PREFIX+p.task,'upload':UPLOAD_PREFIX+p.task,
        'hold':HOLD_PREFIX+p.task,'cancel':jobs.RENDER_CANCELLATION_PREFIX+p.task}
    p.client.set(keys[witness],'retained witness');enqueue=Mock()
    with pytest.raises(plan.ContentPlanError):p.resume.schedule(p.source,enqueue,client=p.client)
    enqueue.assert_not_called();assert not p.client.exists(p.resume.PREFIX+p.task)


@pytest.mark.parametrize('damage',['body_started','wrong_task','wrong_exception','future_terminal','not_accepted','job_changed','retry_token'])
def test_unverified_failure_does_not_gain_a_dispatch(pending,damage):
    from datetime import datetime,timezone,timedelta
    p=pending
    if damage=='body_started':p.terminal['traceback']+='in run_video_pipeline\n'
    if damage=='wrong_task':p.terminal['task_id']=str(uuid4())
    if damage=='wrong_exception':p.terminal['result']['exc_type']='TimeoutError'
    if damage=='future_terminal':p.terminal['date_done']=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
    if damage=='not_accepted':p.proof.pop('accepted_output_of')
    if damage=='job_changed':p.client.set(jobs.JOB_PREFIX+p.task,plan._raw({**p.source,'stage':'voice'}))
    if damage=='retry_token':p.client.hset(jobs.RETRY_CHILD_CLAIM_PREFIX+p.task,'source_task_id',str(uuid4()))
    p.client.set('celery-task-meta-'+p.task,plan._raw(p.terminal));enqueue=Mock()
    with pytest.raises(plan.ContentPlanError):p.resume.schedule(p.source,enqueue,client=p.client)
    enqueue.assert_not_called();assert not p.client.exists(p.resume.PREFIX+p.task)


def test_parallel_minute_ticks_cannot_send_twice(pending):
    p=pending;enqueue=Mock()
    with ThreadPoolExecutor(max_workers=8)as pool:
        list(pool.map(lambda _:p.resume.schedule(p.source,enqueue,client=p.client),range(8)))
    assert enqueue.call_count==1


def test_observation_revalidates_without_reserving_or_resetting(pending):
    p=pending;before={k:p.client.dump(k)for k in p.client.scan_iter()}
    assert p.resume._reserve(p.source,p.client,observe_only=True)['no_execution_observed']is True
    assert {k:p.client.dump(k)for k in p.client.scan_iter()}==before
