from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4
import json

import fakeredis
import pytest

from app.services import framecase_editorial_revision as revision, framecase_pipeline as pipeline
from app.services import framecase_art as art, framecase_schedule as schedule, content_plan as plan
from app.services import studio_state as jobs, commissioning_video as video

OTHER = 'UC5v9AvNtD3PTLgo6m1jROOA'


@pytest.fixture
def setup(monkeypatch):
    client=fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(plan,'_client',lambda:client)
    monkeypatch.setattr(art,'approved_reference',lambda:b'owner approved reference')
    story=json.loads(pipeline.ASSET.read_text());rows=schedule.items(story)
    document={'version':1,'channel_id':revision.CHANNEL_ID,'revision':str(uuid4()),
        'enabled':False,'after_queue':'pause','updated_at':'2026-09-24T22:00:00+00:00','items':rows}
    source={'task_id':revision.SOURCE_ID,'state':'FAILURE','publication_hold':{'owner':True},
        'spec':{'production_channel_id':revision.CHANNEL_ID,'publish_after_render':False,'content_plan_item_id':rows[1]['id']}}
    voice={'asset':{'key':'original-voice.mp3','sha256':'a'*64},'spoken_texts':['one','two','three','four']}
    checkpoint={'package':{'scenes':[{'narration':text}for text in voice['spoken_texts']]},'voice':voice,
                'audio_qc':{'transcript':{'pass':True},'prosody':{'pass':True}}}
    native={'requests':{'original-denied-request':{'create':{'http_status':200},'result':{'http_status':422}}}}
    for key,value in [(plan.PLAN_PREFIX+revision.CHANNEL_ID,document),(jobs.JOB_PREFIX+revision.SOURCE_ID,source),
        (pipeline.PREFIX+'checkpoint:'+revision.SOURCE_ID,checkpoint),(video.PREFIX+revision.SOURCE_ID,native),
        (jobs.JOB_PREFIX+revision.PREVIEW_ID,{'state':'SUCCESS','result':{'video_sha256':revision.APPROVED_MASTER,'creative_qc':{'pass':True}}}),
        (plan.ACTIVE_KEY,{revision.CHANNEL_ID:rows[1]['id'],OTHER:rows[4]['id']}),
        (plan.COMPLETION_PREFIX+rows[0]['id'],{'original-public':'still-public'})]:
        client.set(key,plan._raw(value))
    return SimpleNamespace(client=client,document=document,source=source,checkpoint=checkpoint,native=native)


def test_revision_archives_intent_reuses_voice_and_preserves_every_old_receipt(setup):
    s=setup;c=s.client;old=s.document['items'][1]['id']
    preserved={key:c.get(key)for key in [jobs.JOB_PREFIX+revision.SOURCE_ID,pipeline.PREFIX+'checkpoint:'+revision.SOURCE_ID,
        video.PREFIX+revision.SOURCE_ID,plan.COMPLETION_PREFIX+s.document['items'][0]['id']]}
    result=revision.activate(s.document['revision'],owner_feedback=revision.OWNER_FEEDBACK,client=c)
    assert result['status']=='activated' and result['old_source_held'] and result['provider_requests']==0
    current=plan.read(revision.CHANNEL_ID,client=c)
    assert current['enabled'] and current['items'][1]['id']!=old
    assert current['items'][2]['depends_on']==[result['item_id']]
    assert plan._active(c)=={OTHER:s.document['items'][4]['id']}
    assert {key:c.get(key)for key in preserved}==preserved
    retained=revision.retained_source({'item':current['items'][1]})
    assert retained['voice']==s.checkpoint['voice'] and retained['source_task_id']==revision.SOURCE_ID
    assert not c.exists(plan.COMPLETION_PREFIX+old)
    before={key:c.get(key)for key in c.scan_iter()}
    assert revision.activate(s.document['revision'],owner_feedback=revision.OWNER_FEEDBACK,client=c)['status']=='already_activated'
    assert {key:c.get(key)for key in before}==before


@pytest.mark.parametrize('damage',['owner','pending_provider','changed_plan','live_source','unapproved_preview','later_started'])
def test_revision_never_consumes_unsafe_or_stale_editorial_state(setup,damage):
    s=setup;c=s.client;feedback=revision.OWNER_FEEDBACK;expected=s.document['revision']
    if damage=='owner':feedback='different instruction'
    if damage=='pending_provider':
        s.native['requests']['original-denied-request']['result']=None;c.set(video.PREFIX+revision.SOURCE_ID,plan._raw(s.native))
    if damage=='changed_plan':expected=str(uuid4())
    if damage=='live_source':s.source['state']='PROGRESS';c.set(jobs.JOB_PREFIX+revision.SOURCE_ID,plan._raw(s.source))
    if damage=='unapproved_preview':c.set(jobs.JOB_PREFIX+revision.PREVIEW_ID,plan._raw({'state':'SUCCESS','result':{}}))
    if damage=='later_started':c.set(plan.DISPATCH_PREFIX+s.document['items'][2]['id'],'preserved later dispatch')
    before={key:c.get(key)for key in c.scan_iter()}
    with pytest.raises(plan.ContentPlanError):revision.activate(expected,owner_feedback=feedback,client=c)
    assert {key:c.get(key)for key in c.scan_iter()}==before


def test_retained_narration_binding_rejects_changed_shots_or_paid_checkpoint(setup):
    s=setup;c=s.client
    revision.activate(s.document['revision'],owner_feedback=revision.OWNER_FEEDBACK,client=c)
    item=plan.read(revision.CHANNEL_ID,client=c)['items'][1]
    changed=deepcopy(item);changed['brief']='Changed direction'
    with pytest.raises(plan.ContentPlanError):revision.retained_source({'item':changed})
    s.checkpoint['voice']['asset']['sha256']='b'*64
    c.set(pipeline.PREFIX+'checkpoint:'+revision.SOURCE_ID,plan._raw(s.checkpoint))
    with pytest.raises(plan.ContentPlanError):revision.retained_source({'item':item})
