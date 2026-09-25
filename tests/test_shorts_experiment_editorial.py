from copy import deepcopy
import json
from uuid import uuid4

import fakeredis
import pytest

from app.config import settings
from app.services import content_plan as plan, shorts_experiment as batch, shorts_experiment_editorial as edit
from app.services import studio_state as jobs, channel_production as production, channel_cadence as cadence

C, M = tuple(batch.COUNTS)
NOW = 1790373600.0
DAY = '2026-09-26'


def fixture(monkeypatch, failed=True):
    client = fakeredis.FakeRedis(decode_responses=True)
    entries = {channel: [plan.item('Story', str(uuid4())) for _ in range(count)] for channel,count in batch.COUNTS.items()}
    manifest = {'version':1,'id':'ten','day':DAY,'items':[{'item_id':v['id'],'channel_id':c,'item_sha256':plan._sha(v)}for c,vs in entries.items()for v in vs]}
    client.set(batch.approval_key(DAY), plan._raw(manifest))
    item=entries[C][0];root=batch.root_id(C,item['id'])
    profile={'production_enabled':True,'auto_publish':True,'release_mode':'public','profile_revision':'revision'}
    client.set(production.PROFILE_PREFIX+C, plan._raw(profile))
    document={'version':1,'channel_id':C,'revision':str(uuid4()),'enabled':True,'after_queue':'auto_shorts','items':entries[C],'updated_at':item['created_at']}
    client.set(plan.PLAN_PREFIX+C,plan._raw(document))
    source=None
    if failed:
        error='Director could not produce fully stock-safe short-preview scenes: must contain one simple sentence'
        spec={'format':'shorts','content_plan_item_id':item['id'],'production_channel_id':C}
        source={'task_id':root,'kind':'render','state':'FAILURE','parent_id':None,'paid_create_slots_used':0,'failure_stage':'director_qc','error':error,'spec':spec}
        client.set(jobs.JOB_PREFIX+root,plan._raw(source))
        client.set(plan.DISPATCH_PREFIX+item['id'],plan._raw({'item':item,'task_id':root,'channel_id':C,'profile_revision':'revision','spec_sha256':plan._sha(spec)}))
        client.set(plan.ACTIVE_KEY,plan._raw({C:item['id'],M:'11111111-1111-4111-8111-111111111111'}))
        client.hset(batch.produced_key(DAY,C),root,'shorts')
        client.set('celery-task-meta-'+root,plan._raw({'task_id':root,'status':'FAILURE','result':{'exc_type':'ProductionContentError','exc_message':[error]}}))
    monkeypatch.setattr(edit,'_settled',lambda *a:{'verified':'receipts'})
    return client,item,root,source,manifest


def test_failed_draft_replacement_preserves_job_hold_and_old_production_count(monkeypatch):
    c,item,root,source,manifest=fixture(monkeypatch)
    c.set(jobs.QUALITY_HOLD_JOB_FENCE_PREFIX+root,'unchanged negative verdict')
    original=c.get(jobs.JOB_PREFIX+root);grant=c.get(batch.approval_key(DAY))
    new=plan.item('Corrected story','New simple sentence and unchanged cited facts')
    result=edit.replace(item['id'],new,expected_job_sha256=plan._sha(source),client=c,now=NOW)
    assert result['status']=='editorial_replaced'
    assert c.get(jobs.JOB_PREFIX+root)==original and c.get(batch.approval_key(DAY))==grant
    assert c.get(jobs.QUALITY_HOLD_JOB_FENCE_PREFIX+root)=='unchanged negative verdict'
    assert c.hget(batch.produced_key(DAY,C),root)=='shorts'
    assert not c.exists(plan.COMPLETION_PREFIX+item['id'],jobs.JOB_PREFIX+result['new_root'])
    assert plan._active(c)=={M:'11111111-1111-4111-8111-111111111111'}
    assert plan.read(C,client=c)['items'][0]==new
    assert not json.loads(c.get(edit.ARCHIVE+item['id']))['qa_approved']


@pytest.mark.parametrize('mutation',['running','paid_visual','publication','unknown_provider','changed_job','cancelled','retry'])
def test_uncertain_or_ineligible_prior_work_cannot_be_replaced(monkeypatch,mutation):
    c,item,root,source,manifest=fixture(monkeypatch);expected=plan._sha(source)
    if mutation=='running':source['state']='PROGRESS'
    if mutation=='paid_visual':source['paid_create_slots_used']=1
    if mutation=='cancelled':source['owner_cancellation']=True
    if mutation=='retry':source['retry_claimed']=True
    if mutation=='changed_job':source['error']+=' changed'
    c.set(jobs.JOB_PREFIX+root,plan._raw(source))
    if mutation=='publication':c.set(cadence.PREFIX+'publication:'+root,'existing claim')
    if mutation=='unknown_provider':
        def blocked(*a):raise ValueError('unresolved request')
        monkeypatch.setattr(edit,'_settled',blocked)
    before=c.get(plan.PLAN_PREFIX+C);active=c.get(plan.ACTIVE_KEY)
    with pytest.raises(ValueError):edit.replace(item['id'],plan.item('New','New text'),expected_job_sha256=expected,client=c,now=NOW)
    assert c.get(plan.PLAN_PREFIX+C)==before and c.get(plan.ACTIVE_KEY)==active
    assert not c.exists(batch.replacement_key(DAY,item['id']),edit.ARCHIVE+item['id'])


def test_unstarted_editorial_amendment_does_not_clear_another_active_item(monkeypatch):
    c,item,root,source,manifest=fixture(monkeypatch,False)
    c.set(plan.ACTIVE_KEY,plan._raw({C:'22222222-2222-4222-8222-222222222222'}))
    result=edit.replace(item['id'],plan.item('New','Revised future narration'),client=c,now=NOW)
    assert result['old_job_preserved'] and plan._active(c)=={C:'22222222-2222-4222-8222-222222222222'}
    assert not c.exists(batch.produced_key(DAY,C))


def test_observation_is_read_only_and_duplicate_operator_is_rejected(monkeypatch):
    c,item,root,source,manifest=fixture(monkeypatch)
    new=plan.item('Fixed','Reviewed new script');kwargs={'expected_job_sha256':plan._sha(source),'client':c,'now':NOW}
    before=sorted(c.keys('*'))
    assert edit.replace(item['id'],new,observe_only=True,**kwargs)['eligible']
    assert sorted(c.keys('*'))==before
    edit.replace(item['id'],new,**kwargs)
    with pytest.raises(ValueError):edit.replace(item['id'],new,**kwargs)
