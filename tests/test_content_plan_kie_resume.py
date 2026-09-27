import json
from unittest.mock import Mock
import pytest

from app.services import content_plan_kie_resume as resume, content_plan as plan, studio_state as jobs
from test_content_plan_factual_resume import ready, case
from test_content_plan_attention import dump, CHANNEL


@pytest.fixture
def captured(ready,monkeypatch):
    c,source,_=ready;task=source['task_id']
    source.update(failure_stage='voice_and_visuals',error=resume.ERROR)
    c.set(jobs.JOB_PREFIX+task,plan._raw(source))
    terminal=json.loads(c.get('celery-task-meta-'+task))
    terminal.update(result={'exc_type':'FinalAudioQualityError','exc_message':[resume.ERROR]},
        traceback='File /app/app/tasks.py, in _synthesize_voice_candidate\n')
    c.set('celery-task-meta-'+task,plan._raw(terminal))
    proof={'voice_request':'a'*64,'original_take_limit':3,'new_allocation':0}
    observed=Mock(return_value=proof);monkeypatch.setattr(resume,'captured_voice',observed)
    return c,source,observed


def test_accepted_take_stays_in_same_lineage_and_daily_slot(captured,monkeypatch):
    from app import tasks
    from app.services.channel_cadence import snapshot
    c,source,observed=captured;task=source['task_id'];send=Mock()
    monkeypatch.setattr(tasks.run_video_pipeline,'apply_async',send)
    assert resume.schedule(source,Mock(),client=c)=='kie_voice_resume_preparing'
    result=resume.run(task,resume.operation(task));child=result['task_id']
    assert send.call_count==1 and result['status']=='enqueued'
    assert jobs.acquire_retry_child_execution(child,task)
    resume.verify_child(child,task,source['spec'],client=c)
    assert json.loads(c.get(jobs.JOB_PREFIX+child))['parent_id']==task
    assert snapshot(CHANNEL,client=c)['counts']['produced']['long']==1
    assert resume.run(task,resume.operation(task))=={'status':'already_started'}
    assert not resume.eligible(json.loads(c.get(jobs.JOB_PREFIX+child)))


def test_lost_dispatch_ack_never_sends_twice(captured):
    c,source,_=captured;send=Mock(side_effect=TimeoutError())
    assert resume.schedule(source,send,client=c)=='kie_voice_resume_uncertain'
    assert resume.schedule(source,send,client=c)=='kie_voice_resume_reserved'
    send.assert_called_once()


def test_short_continuation_keeps_thirty_second_spec_and_failed_parent(captured, monkeypatch):
    from app import tasks
    from app.services import content_plan_kie_short_resume as short, channel_cadence as cadence
    c, source, _ = captured; task = source['task_id']; send = Mock()
    source.update(error=short.ERROR, preview_total_paid_create_cap=6)
    source['spec'].update(format='shorts', duration_minutes=0.5)
    c.set(jobs.JOB_PREFIX + task, plan._raw(source))
    c.hset(jobs.PAID_CREATE_BUDGET_PREFIX + task, mapping={'cap': '6', 'used': '0'})
    c.hset(cadence.keys(CHANNEL)[0], task, 'shorts')
    key = plan.DISPATCH_PREFIX + source['spec']['content_plan_item_id']
    dispatch = json.loads(c.get(key)); dispatch['spec_sha256'] = plan._sha(source['spec'])
    c.set(key, plan._raw(dispatch))
    terminal = json.loads(c.get('celery-task-meta-' + task))
    terminal['result']['exc_message'] = [short.ERROR]
    c.set('celery-task-meta-' + task, plan._raw(terminal))
    monkeypatch.setattr(tasks.run_video_pipeline, 'apply_async', send)
    assert resume.schedule(source, Mock(), client=c) == 'kie_voice_resume_preparing'
    child = resume.run(task, resume.operation(task))['task_id']
    assert send.call_args.kwargs['args'][1] == 0.5
    assert jobs.acquire_retry_child_execution(child, task)
    resume.verify_child(child, task, source['spec'], client=c)
    parent = json.loads(c.get(jobs.JOB_PREFIX + task))
    assert parent['state'] == 'FAILURE' and parent['error'] == short.ERROR
    assert cadence.snapshot(CHANNEL, client=c)['counts']['produced']['shorts'] == 1
    assert c.hgetall(jobs.PAID_CREATE_BUDGET_PREFIX + task) == {'cap': '6', 'used': '0'}


@pytest.mark.parametrize('damage',['unknown_voice','changed_error','owner_hold','disabled_plan','media','unsettled_video','child','profile'])
def test_unverified_or_changed_job_cannot_continue(captured,damage):
    c,source,observed=captured;task=source['task_id']
    if damage=='unknown_voice':observed.side_effect=ValueError('unknown')
    if damage=='changed_error':source['error']='timeout'
    if damage=='owner_hold':source['publication_hold']={'owner':True}
    if damage=='media':source['audio_candidate_checkpoint']={'saved':True}
    if damage=='unsettled_video':source['paid_create_slots_used']=1
    if damage=='child':source['retry_child_task_id']=task
    if damage=='disabled_plan':
        doc=json.loads(c.get(plan.PLAN_PREFIX+CHANNEL));doc['enabled']=False;c.set(plan.PLAN_PREFIX+CHANNEL,plan._raw(doc))
    if damage=='profile':
        profile=json.loads(c.get(plan.production.PROFILE_PREFIX+CHANNEL));profile['profile_revision']='changed';c.set(plan.production.PROFILE_PREFIX+CHANNEL,plan._raw(profile))
    c.set(jobs.JOB_PREFIX+task,plan._raw(source));before=dump(c);send=Mock()
    with pytest.raises((ValueError,TypeError)):resume.schedule(source,send,client=c)
    send.assert_not_called();assert dump(c)==before
