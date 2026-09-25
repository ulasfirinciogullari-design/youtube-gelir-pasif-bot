import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from app.services import fal_voice_production as production,fal_voice_trial as trial,fal_voice_adapter as api
from app.services import kie_voice_production as old,kie_voice_ledger as ledger,narrator_rotation
from test_kie_voice_production import active
from test_kie_gemini_voice import kie,box
from test_kie_voice import KEY


@pytest.fixture
def enabled(active,monkeypatch):
    active.context['kind']='long'
    monkeypatch.setattr(production,'settings',SimpleNamespace(fal_key=KEY))
    monkeypatch.setattr(production,'_authorize',lambda pipe,foundation,context:'a'*64)
    monkeypatch.setattr(production,'_hold_guard',lambda pipe,task:None)
    body,ceiling=api.request_body('An entire narration.',language='en')
    grant={'version':1,'purpose':'full_length_voice_validation','model':api.MODEL,'route':api.ROUTE,
        'maximum_requests':1,'max_list_cost_micro_usd':ceiling,'context':active.context,
        'source_task_id':active.context['lineage_id'],'request_sha256':ledger.sha(ledger.raw(body))}
    q={'pass':True,'body':body};key=trial._key('en')
    for field,value in [('grant',grant),('qualification',q)]:
        value=ledger.raw(value);active.client.hset(key,field,value);active.client.hset(key+':anchor',field,ledger.sha(value))
    activation={'version':1,'purpose':'qualified_full_documentary_voice','language':'en','model':api.MODEL,
        'voice':'George','credential_sha256':ledger.sha('fal\0'+KEY),'price_revision':production.PRICE_REVISION,
        'channels':{active.context['channel_id']:active.context['connection_id']},
        'qualification':{'qualification_sha256':ledger.sha(ledger.raw(q)),'grant_sha256':ledger.sha(ledger.raw(grant))}}
    encoded=ledger.raw(activation);key=production._activation_key('en')
    active.client.set(key,encoded);active.client.set(key+':anchor',ledger.sha(encoded))
    active.fal_body=body
    return active


def send(enabled,*,attempt=0,sender=None,body=None):
    body=body or enabled.fal_body
    journal=production.Journal(enabled.foundation,enabled.context['lineage_id'],body,KEY,attempt=attempt)
    return journal.submit(sender or Mock(return_value=httpx.Response(202,json={'accepted':True})),api.ROUTE,
        json=body,headers={'Authorization':'Key '+KEY})


def test_new_long_is_pinned_once_without_changing_original_funding_or_kie(enabled):
    before=enabled.client.get(ledger.JOURNAL_KEY)
    first=production.select('en');assert first==production.select('en')and first['mode']=='new_long'
    assert not enabled.client.exists(old.ROOT_PREFIX+enabled.context['lineage_id'])
    assert enabled.client.get(ledger.JOURNAL_KEY)==before
    with pytest.raises(api.FalVoiceError,match='provider_pinned'):old.select('en')
    with enabled.client.pipeline()as pipe,pytest.raises(api.FalVoiceError,match='provider_pinned'):
        old.native_guard(pipe,enabled.context['lineage_id'])


@pytest.mark.parametrize('prior',['native_narrator','native_unknown'])
def test_old_native_root_never_moves_providers(enabled,prior):
    if prior=='native_narrator':enabled.client.set(narrator_rotation.PREFIX+'root:'+enabled.context['lineage_id'],'preserved')
    else:enabled.native.append({'reservation':{'intent':{'root_lineage_id':enabled.context['lineage_id']}},'settlement':None})
    assert production.select('en')is None
    assert not enabled.client.exists(production.ROOT_PREFIX+enabled.context['lineage_id'])


def test_existing_kie_root_can_only_reuse_its_qualified_trial(enabled):
    original=old.select('en');saved=enabled.client.get(old.ROOT_PREFIX+enabled.context['lineage_id'])
    choice=production.select('en');assert choice['mode']=='qualified_trial'
    assert enabled.client.get(old.ROOT_PREFIX+enabled.context['lineage_id'])==saved
    with pytest.raises(api.FalVoiceError):send(enabled)
    assert original['voice_id']=='Kore'


def test_lost_submit_never_repeats_or_advances_to_a_new_take(enabled):
    production.select('en')
    sender=Mock(side_effect=httpx.ReadTimeout('unknown outcome'))
    with pytest.raises(httpx.ReadTimeout):send(enabled,sender=sender)
    with pytest.raises(api.FalVoiceError,match='submit_unknown'):send(enabled,sender=sender)
    with pytest.raises(api.FalVoiceError,match='submit_unknown'):send(enabled,attempt=1,sender=sender)
    assert sender.call_count==1


def test_captured_create_replayed_without_rebuying_and_script_change_refused(enabled):
    production.select('en');sender=Mock(return_value=httpx.Response(202,json={'accepted':True}))
    assert send(enabled,sender=sender).json()=={'accepted':True}
    assert send(enabled,sender=sender).json()=={'accepted':True}
    with pytest.raises(api.FalVoiceError):send(enabled,sender=sender,body={**enabled.fal_body,'text':'Changed text.'})
    with pytest.raises(api.FalVoiceError):send(enabled,sender=sender,attempt=3)
    assert sender.call_count==1


def test_removed_activation_or_modified_qualification_stops_a_pinned_root(enabled):
    production.select('en')
    key=production._activation_key('en');enabled.client.delete(key)
    with pytest.raises(api.FalVoiceError):production.select('en')


def test_owner_pause_is_checked_again_immediately_before_submit(enabled,monkeypatch):
    production.select('en');sender=Mock()
    monkeypatch.setattr(production,'_authorize',Mock(side_effect=api.FalVoiceError('owner_paused')))
    with pytest.raises(api.FalVoiceError,match='owner_paused'):send(enabled,sender=sender)
    sender.assert_not_called()
    assert not enabled.client.exists(production.PREFIX+'take:'+enabled.context['lineage_id']+':0')


def test_short_and_unrelated_channel_never_enter_long_route(enabled):
    enabled.context['kind']='shorts';assert production.select('en')is None
    enabled.context['kind']='long';enabled.context['channel_id']='UCs93z6wf134H5_BL9pkQX4Q'
    assert production.select('en')is None


def test_pipeline_reuses_prepared_whole_audio_bytes_without_new_normalization(tmp_path,monkeypatch):
    from pathlib import Path
    from app.services import voice,commissioning_longform
    actual=Path
    monkeypatch.setattr(voice,'Path',lambda value:tmp_path if str(value)=='/tmp'else actual(value))
    choice={'voice_id':'George','model':api.MODEL}
    monkeypatch.setattr(production,'select',lambda language:choice)
    forbidden=Mock(side_effect=AssertionError('A pinned Fal performance must stay exact'))
    monkeypatch.setattr(old,'select',forbidden)
    monkeypatch.setattr(voice.subprocess,'run',forbidden)
    monkeypatch.setattr(voice,'_media_duration',lambda path:168.)
    monkeypatch.setattr(commissioning_longform,'active',lambda:True)
    monkeypatch.setattr(commissioning_longform,'natural_documentary_timing',lambda:True)
    text='The company made one clear choice that changed its entire future.'
    spoken=[text]*30;narration=' '.join(spoken);step=167/len(narration)
    chunks=[{'characters':list(narration),
        'character_start_times_seconds':[i*step for i in range(len(narration))],
        'character_end_times_seconds':[(i+1)*step for i in range(len(narration))]}]
    generate=Mock(return_value=(b'exact-reviewed-prepared-audio',chunks))
    monkeypatch.setattr(production,'synthesize',generate)
    result=voice.synthesize_scene_sequence([{'narration':s}for s in spoken],'same-fal-voice',180,language='en')
    assert actual(result['path']).read_bytes()==b'exact-reviewed-prepared-audio'
    assert sum(result['scene_durations'])==pytest.approx(168)
    assert result['voice_model']==api.MODEL and not result.get('audio_qc')
    assert result['tempo_rate']==1 and generate.call_count==1 and forbidden.call_count==0
