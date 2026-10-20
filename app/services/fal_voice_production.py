"""Qualified full documentary narration with original-root receipt reuse.

Each language is enabled explicitly after an actual full-length qualification.
All old provider pins, receipts and unknowns remain. New narration allows the
existing three attempts; accepted/unknown submits never purchase another copy.
"""
from datetime import datetime,timezone
import json

from app.config import settings
from app.services import fal_voice_adapter as api, fal_voice_trial as trial
from app.services import kie_voice_ledger as kie, production_spend_runtime as runtime
from app.services.included_stock_pool import _local_transaction

PREFIX='youtube_studio:commissioning:v1:fal_voice_production:'
ROOT_PREFIX=PREFIX+'root:'
PRICE_UNTIL=datetime(2026,10,25,tzinfo=timezone.utc)
PRICE_REVISION='fal-elevenlabs-turbo-0.05-per-1000-reviewed-20260925'


def _channel_connection(pipe, active, channel):
    if active is None:return None
    if channel in kie.CHANNELS:return active['channels'].get(channel)
    from app.services import framecase_fal_voice as framecase
    if channel != framecase.CHANNEL_ID:return None
    grant=framecase.read(pipe,active)
    return grant['connection_id'] if grant is not None else None


def _activation_key(language):
    api.require(language in api.VOICES)
    return PREFIX+'activation:'+language


def activation(pipe,language):
    key=_activation_key(language);pipe.watch(key,key+':anchor')
    encoded,anchor=pipe.get(key),pipe.get(key+':anchor')
    if encoded is None:
        api.require(anchor is None,'fal_voice_activation_incomplete');return None
    api.require(type(encoded)is str and kie.sha(encoded)==anchor and pipe.pttl(key)==pipe.pttl(key+':anchor')==-1)
    value=json.loads(encoded)
    api.require(value['version']==1 and value['purpose']=='qualified_full_documentary_voice'
        and value['model']==api.MODEL and value['language']==language and value['voice']==api.VOICES[language]
        and value['credential_sha256']==kie.sha('fal\0'+settings.fal_key)
        and value['price_revision']==PRICE_REVISION and set(value['channels'])<=kie.CHANNELS
        and len(value['channels'])==1)
    records=trial._read(pipe,trial._key(language))
    api.require(value['qualification']['qualification_sha256']==kie.sha(kie.raw(trial.qualifying_record(records)))
        and value['qualification']['grant_sha256']==kie.sha(kie.raw(records['grant']))
        and trial.qualifying_record(records)['pass']is True)
    return value


def commission(foundation,language,*,owner_evidence_sha256):
    """Operator only: verify original provider evidence before enabling."""
    from app.services import fal_voice_qualification as qualified
    import re
    api.require(re.fullmatch('[0-9a-f]{64}',owner_evidence_sha256))
    with foundation.client.pipeline()as pipe:
        records=trial._read(pipe,trial._key(language))
        pipe.multi();pipe.ping();api.require(pipe.execute()==[True])
    grant=records['grant'];body=trial.qualifying_record(records)['body']
    journal=trial.Journal(foundation,grant['source_task_id'],body,settings.fal_key)
    proof=qualified.verified(journal);context=grant['context']
    key=_activation_key(language)
    with foundation.client.pipeline()as pipe:
        pipe.watch(key,key+':anchor')
        api.require(not pipe.exists(key,key+':anchor'),'fal_voice_already_activated')
        _authorize(pipe,foundation,context)
        observed=trial._read(pipe,trial._key(language))
        api.require(observed==records)
        value={'version':1,'purpose':'qualified_full_documentary_voice','model':api.MODEL,
            'language':language,'voice':api.VOICES[language],'qualification':proof,
            'channels':{context['channel_id']:context['connection_id']},
            'credential_sha256':kie.sha('fal\0'+settings.fal_key),'price_revision':PRICE_REVISION,
            'owner_evidence_sha256':owner_evidence_sha256,'authorized_at':datetime.now(timezone.utc).isoformat()}
        encoded=kie.raw(value)
        pipe.multi();pipe.set(key,encoded,nx=True);pipe.set(key+':anchor',kie.sha(encoded),nx=True)
        api.require(pipe.execute()==[True,True])
    return value


def _authorize(pipe,foundation,context):
    from app.services import commissioning_longform,content_plan as plan,production_continuation as continuation
    from app.services import framecase_fal_voice as framecase
    api.require(context['kind']=='long'and context['channel_id']in kie.CHANNELS|{framecase.CHANNEL_ID})
    kie._foundation(pipe,foundation)
    commissioning_longform.authorize(pipe,context)
    if context['channel_id']==framecase.CHANNEL_ID:
        framecase.authorize_context(pipe,context)
        kie._connected_owner(pipe,context['channel_id'],context['connection_id'])
    else:kie._binding(pipe,context['channel_id'],context['connection_id'])
    keys=(plan.PLAN_PREFIX+context['channel_id'],plan.production.PROFILE_PREFIX+context['channel_id'])
    pipe.watch(*keys);document,profile=[json.loads(pipe.get(key))for key in keys]
    api.require(document['enabled']is True and profile['production_enabled']is True and profile['auto_publish']is True)
    return continuation.authority(pipe,context['channel_id'])


def _hold_guard(pipe,task):
    from app.services import studio_state as jobs
    from app.services.source_publication_hold import HOLD_PREFIX
    from app.services.youtube_publish_state import UPLOAD_PREFIX
    seen=set()
    for _ in range(12):
        api.require(task not in seen);seen.add(task)
        key=jobs.JOB_PREFIX+task
        keys=[p+task for p in (HOLD_PREFIX,UPLOAD_PREFIX,jobs.RENDER_CANCELLATION_PREFIX,
            jobs.QUALITY_HOLD_JOB_FENCE_PREFIX,jobs.EXTERNAL_EPISODE_LEAF_PREFIX)]
        pipe.watch(key,*keys);row=json.loads(pipe.get(key))
        api.require(not pipe.exists(*keys) and not row.get('publication_hold') and not row.get('owner_cancellation'))
        task=row.get('parent_id')
        if task is None:return
    raise api.FalVoiceError('fal_voice_lineage_unverified')


def guard_other_provider(pipe,root):
    pipe.watch(ROOT_PREFIX+root)
    api.require(pipe.get(ROOT_PREFIX+root)is None,'fal_voice_root_provider_pinned')


@_local_transaction
def select(language):
    from app.services import kie_voice_production as old,narrator_rotation as rotation
    from app.services.framecase_cadence import CHANNEL_ID as framecase_channel
    if language not in api.VOICES or not runtime.enforcement_enabled()or not runtime._TASK_ID.get():return None
    f=runtime.configured_ledger(read_timeout=3);task=runtime._TASK_ID.get()
    context=runtime.resolve_context(f.client,task)
    if context['kind']!='long'or context['channel_id']not in kie.CHANNELS|{framecase_channel}:return None
    root=context['lineage_id'];key=ROOT_PREFIX+root
    with f.client.pipeline()as pipe:
        pipe.watch(key);encoded=pipe.get(key);active=activation(pipe,language)
        if _channel_connection(pipe,active,context['channel_id'])!=context['connection_id']:
            api.require(encoded is None,'fal_voice_activation_missing')
            pipe.multi();pipe.ping();api.require(pipe.execute()==[True]);return None
        _authorize(pipe,f,context);_hold_guard(pipe,task)
        if encoded is not None:
            choice=json.loads(encoded)
            api.require(pipe.pttl(key)==-1 and choice['context']==context and choice['language']==language
                and choice['activation_sha256']==kie.sha(kie.raw(active))
                and choice['voice_id']==active['voice'] and choice['model']==api.MODEL)
            pipe.multi();pipe.ping();api.require(pipe.execute()==[True]);return choice
        pipe.watch(old.ROOT_PREFIX+root,rotation.PREFIX+'root:'+root)
        if pipe.get(rotation.PREFIX+'root:'+root)is not None or any(r['reservation']['intent']['root_lineage_id']==root for r in old._native_intents(pipe,f)):
            pipe.multi();pipe.ping();api.require(pipe.execute()==[True]);return None
        prior_kie=pipe.get(old.ROOT_PREFIX+root);mode='new_long'
        if prior_kie is not None:
            records=trial._read(pipe,trial._key(language))
            if records['grant']['context']!=context:
                pipe.multi();pipe.ping();api.require(pipe.execute()==[True]);return None
            # Explicitly qualified same-root alternative, with all old takes
            # preserved. It reuses the already-paid full trial, never a POST.
            mode='qualified_trial'
        choice={'version':1,'context':context,'language':language,'voice_id':active['voice'],
            'model':api.MODEL,'activation_sha256':kie.sha(kie.raw(active)),'mode':mode}
        pipe.multi();pipe.set(key,kie.raw(choice),nx=True);api.require(pipe.execute()==[True])
    return choice


def capacity(pipe,foundation,channel_id,*,kind):
    from app.services.framecase_cadence import CHANNEL_ID as framecase_channel
    if kind!='long'or channel_id not in kie.CHANNELS|{framecase_channel}:return None
    for language in api.VOICES:
        active=activation(pipe,language)
        connection=_channel_connection(pipe,active,channel_id)
        if connection is None:continue
        kie._foundation(pipe,foundation);kie._connected_owner(pipe,channel_id,connection)
        api.require(datetime.now(timezone.utc)<PRICE_UNTIL,'fal_voice_price_review_due')
        return {'voice_provider':'fal','voice_model':api.MODEL,'voice_cost_basis':'commissioning_list_ceiling',
            'maximum_long_voice_list_cost_micro_usd':750000}
    return None


class Journal(trial.Journal):
    def __init__(self,foundation,task,body,secret,*,attempt):
        super().__init__(foundation,task,body,secret)
        api.require(type(attempt)is int and 0<=attempt<3)
        self.attempt=attempt;self.context=runtime.resolve_context(foundation.client,task)
        self.key=PREFIX+'take:'+self.context['lineage_id']+':'+str(attempt)

    def _read(self,pipe):
        return trial._read(pipe,self.key,purpose='qualified_documentary_voice_attempt')

    def submit(self,sender,url,**kwargs):
        api.require(url==api.ROUTE and kwargs['json']==self.body
            and kwargs['headers']['Authorization']=='Key '+self.secret)
        with self.foundation.client.pipeline()as pipe:
            pipe.watch(self.key,self.key+':anchor')
            if pipe.exists(self.key,self.key+':anchor'):
                rows=self._read(pipe);grant=rows['grant']
                api.require(grant['context']==self.context and grant['attempt']==self.attempt
                    and grant['request_sha256']==kie.sha(kie.raw(self.body))
                    and grant['credential_sha256']==kie.sha('fal\0'+self.secret)
                    and 'request'in rows and 'create'in rows,'fal_voice_previous_submit_unknown')
                self.prior=rows;pipe.multi();pipe.ping();api.require(pipe.execute()==[True])
                return kie.restore(rows['create'])
            api.require(datetime.now(timezone.utc)<PRICE_UNTIL,'fal_voice_price_review_due')
            authority=_authorize(pipe,self.foundation,self.context);_hold_guard(pipe,self.task)
            active=activation(pipe,self.body['language_code']);api.require(active is not None)
            root_key=ROOT_PREFIX+self.context['lineage_id'];pipe.watch(root_key)
            choice=json.loads(pipe.get(root_key))
            api.require(choice['context']==self.context and choice['mode']=='new_long'
                and choice['activation_sha256']==kie.sha(kie.raw(active))
                and _channel_connection(pipe,active,self.context['channel_id'])==self.context['connection_id'])
            # An attempt's fixed slot cannot be changed by a child/new script.
            for attempt in range(self.attempt):
                previous=PREFIX+'take:'+self.context['lineage_id']+':'+str(attempt)
                prior=trial._read(pipe,previous,purpose='qualified_documentary_voice_attempt')
                api.require('result'in prior,'fal_voice_previous_submit_unknown')
            grant={'version':1,'purpose':'qualified_documentary_voice_attempt','context':self.context,
                'model':api.MODEL,'route':api.ROUTE,'attempt':self.attempt,'maximum_requests':1,
                'request_sha256':kie.sha(kie.raw(self.body)),'credential_sha256':kie.sha('fal\0'+self.secret),
                'max_list_cost_micro_usd':api.describe(self.body),'continuation_authority_sha256':authority,
                'activation_sha256':kie.sha(kie.raw(active)),'price_revision':PRICE_REVISION}
            request={'grant_sha256':kie.sha(kie.raw(grant)),'reserved_at':datetime.now(timezone.utc).isoformat()}
            pipe.multi()
            for field,value in (('grant',grant),('request',request)):
                encoded=kie.raw(value);pipe.hset(self.key,field,encoded);pipe.hset(self.key+':anchor',field,kie.sha(encoded))
            api.require(pipe.execute()==[1,1,1,1])
        response=sender(url,**kwargs);self.observe('create',response)
        from app.services import cost_meter
        cost_meter.observe_http(url,kwargs,response);return response


def synthesize(text,choice,*,attempt):
    from app.services import fal_voice_media
    f=runtime.configured_ledger(read_timeout=3);task=runtime._TASK_ID.get()
    api.require(runtime.resolve_context(f.client,task)==choice['context'])
    body,_=api.request_body(text,language=choice['language'])
    if choice['mode']=='qualified_trial':
        with f.client.pipeline()as pipe:
            rows=trial._read(pipe,trial._key(choice['language']))
            api.require(rows['grant']['context']==choice['context']and trial.qualifying_record(rows)['body']==body
                and trial.qualifying_record(rows)['pass']is True)
            pipe.multi();pipe.ping();api.require(pipe.execute()==[True])
        journal=trial.Journal(f,rows['grant']['source_task_id'],body,settings.fal_key)
        result=api.result(kie.restore(rows['result']))
    else:
        api.require(choice['mode']=='new_long')
        journal=Journal(f,task,body,settings.fal_key,attempt=attempt)
        result=api.generate(body,settings.fal_key,journal)
    audio,_=fal_voice_media.prepared(journal,result)
    return audio,result['timestamps']
