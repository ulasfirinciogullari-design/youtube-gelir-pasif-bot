"""One continuation reusing a qualified complete alternate voice, without TTS."""
import json
import secrets
from uuid import uuid5,NAMESPACE_URL

from app.services import content_plan as plan,studio_state as jobs,content_plan_research_resume as pre
from app.services import fal_voice_trial as trial,fal_voice_production as production,kie_voice_ledger as kie

PREFIX=plan.PREFIX+'qualified_fal_resume:v1:'
DISPATCH,EXECUTION=PREFIX+'dispatch:',PREFIX+'execution:'


def operation(source):return str(uuid5(NAMESPACE_URL,'owner-plan-qualified-fal-resume:v1:'+source))


def registered(source):return bool(plan._client().exists(DISPATCH+plan._id(source)))


def eligible(source):
    spec=source.get('spec')or{}
    if not(source.get('state')=='FAILURE'and source.get('failure_stage')=='voice_and_visuals'
        and source.get('error','').startswith('Voice synthesis quality rejected before paid media: ')
        and spec.get('production_channel_id')in kie.CHANNELS and spec.get('content_plan_item_id')
        and spec.get('duration_minutes')==3 and spec.get('format')=='landscape'
        and not source.get('retry_child_task_id')and spec.get('language')in {'tr','en'}):return False
    client=plan._client()
    with client.pipeline()as pipe:
        active=production.activation(pipe,spec['language'])
        if active is None:
            pipe.multi();pipe.ping();plan._require(pipe.execute()==[True]);return False
        rows=trial._read(pipe,trial._key(spec['language']))
        result=rows['grant']['source_task_id']==source['task_id']and trial.qualifying_record(rows)['pass']is True
        pipe.multi();pipe.ping();plan._require(pipe.execute()==[True]);return result


def checked(client,task,*,claimed=False):
    from app.services import production_spend_runtime as runtime
    source=plan._object(client.get(jobs.JOB_PREFIX+plan._id(task)));spec=source['spec']
    with client.pipeline()as pipe:
        active=production.activation(pipe,spec['language']);plan._require(active is not None)
        rows=trial._read(pipe,trial._key(spec['language']))
        plan._require(rows['grant']['source_task_id']==task and trial.qualifying_record(rows)['pass']is True
            and active['channels'].get(spec['production_channel_id'])==spec['production_connection_id'])
        pipe.multi();pipe.ping();plan._require(pipe.execute()==[True])
    foundation=runtime.configured_ledger(read_timeout=3)
    evidence=trial.proof(foundation,task,trial.qualifying_record(rows)['body'],claimed=claimed)
    plan._require(all(rows['grant'][k]==v for k,v in evidence.items())
        and all(source.get(k)is None for k in pre.MEDIA_FIELDS)
        and source.get('preview_total_paid_create_cap')==32
        and client.hgetall(jobs.PAID_CREATE_BUDGET_PREFIX+task)=={'cap':'32','used':'0'})
    if not claimed:
        plan._require(not any(source.get(k)for k in ('retry_child_task_id','retry_claimed','repair_claimed'))
            and not client.exists(jobs.RETRY_DISPATCH_PREFIX+task))
    return source,{'original_voice_evidence':evidence,'qualified_audio_sha256':trial.qualifying_record(rows)['audio_sha256'],
        'qualification_sha256':kie.sha(kie.raw(trial.qualifying_record(rows))),'new_tts_requests':0}


def schedule(source,enqueue,*,client=None):
    client=client or plan._client();task=source['task_id']
    if client.exists(DISPATCH+task):return 'qualified_voice_resume_reserved'
    source,proof=checked(client,task)
    claim={'version':1,'source_task_id':task,'task_id':operation(task),
        'source_sha256':pre.fingerprint(source),'provider_records':proof}
    if not client.set(DISPATCH+task,plan._raw(claim),nx=True):return 'qualified_voice_resume_reserved'
    try:enqueue(args=(task,),task_id=operation(task),retry=False)
    except Exception:return 'qualified_voice_resume_uncertain'
    return 'qualified_voice_resume_preparing'


def verify_child(task,source_id,spec,*,client=None):
    from app.services.content_plan_local_resume import _claim
    client=client or plan._client();source,proof=checked(client,source_id,claimed=True)
    claim=plan._object(client.get(DISPATCH+source_id));child=plan._object(client.get(jobs.JOB_PREFIX+task))
    plan._require(claim['task_id']==operation(source_id)and claim['provider_records']==proof
        and claim['source_sha256']==pre.fingerprint(source)and client.get(EXECUTION+source_id)==operation(source_id)
        and source.get('retry_child_task_id')==task and child.get('parent_id')==source_id
        and child.get('kind')=='render'and child.get('state')in {'PENDING','STARTED','PROGRESS'}
        and child['spec']==spec==source['spec']and all(child.get(k)is None for k in pre.MEDIA_FIELDS)
        and not child.get('owner_cancellation')and not child.get('publication_hold'))
    _claim(client,source_id,task)


def run(source_id,operation_id):
    from app.tasks import run_video_pipeline
    client=plan._client();claim=plan._object(client.get(DISPATCH+source_id))
    plan._require(claim['task_id']==operation_id==operation(source_id))
    if not client.set(EXECUTION+source_id,operation_id,nx=True):return {'status':'already_started'}
    source,proof=checked(client,source_id)
    plan._require(claim['provider_records']==proof and claim['source_sha256']==pre.fingerprint(source))
    child=str(uuid5(NAMESPACE_URL,'owner-plan-qualified-fal-child:v1:'+source_id));token=secrets.token_urlsafe(32)
    plan._require(jobs.claim_retry_dispatch(source_id,child,token,allow_repair=False).get('claimed')is True)
    jobs.create_job(child,source['spec'],kind='render',parent_id=source_id)
    spec=source['spec'];options={k:v for k,v in spec.items()if k not in {'topic','duration_minutes','language','channel_id'}}
    try:
        run_video_pipeline.apply_async(args=(spec['topic'],3,spec['language'],spec['channel_id'],options,None,source_id),
            task_id=child,retry=False)
    except Exception:
        jobs.mark_retry_dispatch(source_id,token,'uncertain');return {'status':'dispatch_uncertain','task_id':child}
    jobs.mark_retry_dispatch(source_id,token,'dispatched');return {'status':'enqueued','task_id':child}
