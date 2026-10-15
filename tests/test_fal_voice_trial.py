"""A bounded trial cannot consume a second voice or erase prior accounting."""
from copy import deepcopy
import json

import httpx
import pytest

from app.services import fal_voice_trial as trial, fal_voice_adapter as api
from app.services import kie_voice_ledger as kie, production_continuation as continuation
from app.services.production_spend import LEDGER_KEY
from test_commissioning_audio import box
from test_whisper_transcription import CHANNEL, ROOT, NOW, KEY
from test_fal_voice_adapter import ID, URL


@pytest.fixture
def grant(box, monkeypatch):
    continuation.initialize(box.client, {'version':1,'kind':'continuous_commissioning',
        'allowed_channels':[CHANNEL],'authorized_at':NOW.isoformat(),'owner_evidence_sha256':'f'*64})
    with box.client.pipeline() as pipe:
        authority=continuation.authority(pipe,CHANNEL)
        pipe.multi();pipe.ping();pipe.execute()
    evidence={'context':{'kind':'long','lineage_id':ROOT,'channel_id':CHANNEL,'connection_id':'connection_AAAAA'},
        'source_task_id':ROOT,'source_spec_sha256':'a'*64,'source_error_sha256':'b'*64,
        'continuation_authority_sha256':authority,'prior_voice_records':{'original':'retained'},
        'original_kie_policy_sha256':'c'*64}
    box.client.set('trial_source','failed_three_takes')
    def proof(foundation,task,body,*,reader=None):
        assert reader is not None
        reader.watch('trial_source')
        api.require(reader.get('trial_source')=='failed_three_takes')
        return deepcopy(evidence)
    monkeypatch.setattr(trial,'proof',proof)
    box.body,_=api.request_body('A complete narration.',language='en')
    trial.stage(box.foundation,ROOT,box.body,KEY,owner_evidence_sha256='f'*64)
    box.journal=lambda:trial.Journal(box.foundation,ROOT,box.body,KEY)
    box.requests=[]
    box.fail=False
    def send(request):
        box.requests.append(request)
        if request.method=='POST':
            assert box.client.hexists(trial.PREFIX+'en','request')
            assert not box.client.hexists(trial.PREFIX+'en','create')
            if box.fail:raise httpx.ReadTimeout('lost response')
            return httpx.Response(202,json={'request_id':ID,'status_url':URL+'/status','response_url':URL})
        if request.url.path.endswith('/status'):
            return httpx.Response(200,json={'status':'COMPLETED'})
        return httpx.Response(200,json={'audio':{'url':'https://v3.fal.media/test.mp3'},
            'timestamps':[{'word':'A','start':0,'end':.2}]})
    original=httpx.Client
    monkeypatch.setattr(api.httpx,'Client',lambda **kw:original(**{**kw,'transport':httpx.MockTransport(send)}))
    return box


def test_one_request_captured_reused_without_reallocating_any_original_budget(grant):
    before=grant.client.hgetall(LEDGER_KEY)
    first=api.generate(grant.body,KEY,grant.journal())
    assert api.generate(grant.body,KEY,grant.journal())==first
    assert [r.method for r in grant.requests].count('POST')==1
    assert grant.client.hgetall(LEDGER_KEY)==before
    assert not grant.client.exists(kie.POLICY_KEY,kie.JOURNAL_KEY)
    assert KEY not in json.dumps(grant.client.hgetall(trial.PREFIX+'en'))


def test_unknown_submit_is_permanently_reserved_and_never_rebought(grant):
    grant.fail=True
    with pytest.raises(httpx.ReadTimeout):api.generate(grant.body,KEY,grant.journal())
    with pytest.raises(api.FalVoiceError,match='submit_unknown'):
        api.generate(grant.body,KEY,grant.journal())
    assert len(grant.requests)==1
    assert grant.client.hexists(trial.PREFIX+'en','request')
    assert not grant.client.hexists(trial.PREFIX+'en','create')


def test_inactive_owner_stops_new_submit_but_keeps_staged_grant(grant):
    original=grant.client.hgetall(trial.PREFIX+'en')
    grant.client.delete(continuation.ACTIVE_KEY)
    with pytest.raises(api.FalVoiceError):api.generate(grant.body,KEY,grant.journal())
    assert not grant.requests and grant.client.hgetall(trial.PREFIX+'en')==original


def test_stored_result_remains_readable_after_owner_stops_new_work(grant):
    first=api.generate(grant.body,KEY,grant.journal())
    grant.client.delete(continuation.ACTIVE_KEY)
    assert api.generate(grant.body,KEY,grant.journal())==first
    assert len(grant.requests)==3


def test_changed_text_or_credentials_cannot_take_the_same_slot(grant):
    changed={**grant.body,'text':'Another narration.'}
    with pytest.raises(api.FalVoiceError):
        api.generate(changed,KEY,trial.Journal(grant.foundation,ROOT,changed,KEY))
    with pytest.raises(api.FalVoiceError):
        api.generate(grant.body,'another',trial.Journal(grant.foundation,ROOT,grant.body,'another'))
    assert not grant.requests


def test_new_hold_or_source_change_refuses_before_paid_submit(grant):
    grant.client.set('trial_source','held')
    with pytest.raises(api.FalVoiceError):api.generate(grant.body,KEY,grant.journal())
    assert not grant.requests
    assert not grant.client.hexists(trial.PREFIX+'en','request')


def test_grant_or_receipt_cannot_be_overwritten_or_lost(grant):
    before=grant.client.hgetall(trial.PREFIX+'en')
    with pytest.raises(api.FalVoiceError,match='already_staged'):
        trial.stage(grant.foundation,ROOT,grant.body,KEY,owner_evidence_sha256='f'*64)
    assert grant.client.hgetall(trial.PREFIX+'en')==before
    grant.client.hdel(trial.PREFIX+'en:anchor','grant')
    with pytest.raises(api.FalVoiceError):api.generate(grant.body,KEY,grant.journal())
    assert not grant.requests
