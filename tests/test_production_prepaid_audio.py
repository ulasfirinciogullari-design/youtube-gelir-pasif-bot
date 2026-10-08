from copy import deepcopy
from datetime import timedelta
import json

import pytest

from app.services import production_prepaid_audio as prepaid, production_included_router as included
from app.services import abacus_router_audio_adapter as adapter, production_included_transport as transport
from app.services import production_spend_runtime as runtime
from app.services.production_spend import LEDGER_KEY, SpendBlocked
from test_production_included_router import commissioned, client, CONTEXT, request
from test_abacus_router_adapter import KEY
from test_abacus_router_audio_adapter import mp3, response, envelope, ASR
from test_production_credit_ledger import InterceptClient
from test_production_cash_disabled import dump
from prepaid_audio_test_support import audio_policy


@pytest.fixture
def audio_ledger(commissioned):
    route, policy, _ = commissioned
    ledger = prepaid.PrepaidAudioLedger(route.foundation)
    value = audio_policy(policy, ledger.clock(), max_requests_total=3,
                         valid_until=included._stamp(ledger.clock()+timedelta(days=4)))
    ledger.initialize(value)
    return ledger, route, value


def prepare(frequency=440):
    return adapter.prepare_prepaid_blind_asr_request(mp3(frequency=frequency), api_key=KEY, language='tr')


def test_fixed_audio_has_separate_authority_no_cash_and_no_fictional_balance(audio_ledger):
    ledger, route, policy = audio_ledger
    before = {k:ledger.client.get(k)for k in (included.STATE_KEY,included.JOURNAL_KEY,included.ANCHOR_KEY)}
    prepared = prepare()
    assert type(prepared) is adapter.PreparedPrepaidAudioRequest
    assert prepared.model == 'gemini-2.5-flash'
    with pytest.raises(SpendBlocked, match='binding_invalid'):route.reserve(CONTEXT,'blind_asr',prepared)
    with pytest.raises(SpendBlocked):ledger.reserve(CONTEXT,'research',request())
    identity, prior = ledger.reserve(CONTEXT,'blind_asr',prepared)
    assert prior is None
    observed=adapter.observe_audio_router_response(prepared,response(prepared,payload=envelope(model=prepared.model)))
    receipt=ledger.settle(identity,prepared,observed)
    assert included._result(prepared,receipt)==ASR
    assert receipt['evidence']['requested_model']=='gemini-2.5-flash'
    assert policy['current_available_credits']is None and policy['reported_credit_balance']==15768
    assert {k:ledger.client.get(k)for k in before}==before
    assert ledger.foundation.snapshot()['historical_cash_micro']is None
    assert ledger.foundation.snapshot()['cash_spending_enabled']is False
    with pytest.raises(SpendBlocked):ledger.initialize(policy)
    with pytest.raises(SpendBlocked):route.initialize(policy)


@pytest.mark.parametrize('key',[prepaid.STATE_KEY,prepaid.JOURNAL_KEY,prepaid.ANCHOR_KEY])
def test_partial_loss_cannot_restore_audio_allowance_or_resend(audio_ledger,key):
    ledger,_,policy=audio_ledger
    ledger.reserve(CONTEXT,'blind_asr',prepare())
    ledger.client.delete(key);before=dump(ledger.client)
    with pytest.raises(SpendBlocked):ledger.reserve(CONTEXT,'blind_asr',prepare())
    with pytest.raises(SpendBlocked):ledger.initialize(policy)
    assert dump(ledger.client)==before


def test_lost_reserve_ack_cannot_send_again_or_release_occupied_request(audio_ledger):
    from redis.exceptions import ConnectionError
    ledger,_,_=audio_ledger
    def lost(number,result):raise ConnectionError('Lost reply')
    ledger.client=InterceptClient(ledger.client,after=lost)
    with pytest.raises(SpendBlocked):ledger.reserve(CONTEXT,'blind_asr',prepare())
    ledger.client=ledger.foundation.client
    with pytest.raises(SpendBlocked,match='previous_outcome_unknown'):ledger.reserve(CONTEXT,'blind_asr',prepare())


def test_total_request_ceiling_includes_unknowns_after_day_change(audio_ledger):
    ledger,_,_=audio_ledger
    for frequency in (440,441,442):ledger.reserve(CONTEXT,'blind_asr',prepare(frequency))
    now=ledger.clock();ledger.clock=lambda:now+timedelta(days=1)
    other={**CONTEXT,'lineage_id':'22222222-2222-4222-8222-222222222222'}
    ledger.client.hset(LEDGER_KEY,'binding:'+other['lineage_id'],included._raw(other))
    with pytest.raises(SpendBlocked,match='period_limit'):ledger.reserve(other,'blind_asr',prepare(443))


@pytest.mark.parametrize('patch',[
 {'automatic_purchase_enabled':True},{'new_cash_allowance_micro':1},
 {'historical_cash_micro':0},{'model':'route-llm'},{'current_available_credits':15768},
 {'balance_source':'live_provider_observation'},{'funding_basis':'unlimited'},
 {'reported_credit_balance':True},{'max_requests_total':1001},
 {'billing_controls_evidence_sha256':'unknown'}])
def test_policy_rejects_topups_cash_assumptions_unknown_models_and_misleading_balances(audio_ledger,patch):
    ledger,_,policy=audio_ledger
    with pytest.raises(SpendBlocked):prepaid.validate_policy({**policy,**patch},ledger.clock())


def test_old_router_reservations_survive_explicit_audio_commission(commissioned):
    route,policy,_=commissioned
    old=adapter.prepare_prompt_json_blind_asr_request(mp3(),api_key=KEY,language='tr')
    identity,_=route.reserve(CONTEXT,'blind_asr',old)
    original=route.client.get(included.JOURNAL_KEY)
    ledger=prepaid.PrepaidAudioLedger(route.foundation);ledger.initialize(audio_policy(policy,ledger.clock()))
    ledger.reserve(CONTEXT,'blind_asr',prepare())
    assert route.client.get(included.JOURNAL_KEY)==original
    assert json.loads(original)['requests'][identity]['outcome']is None


def test_disabled_or_uncommissioned_audio_never_falls_back_to_route_llm(commissioned,monkeypatch):
    route,_,_=commissioned
    for name,value in {'studio_spend_enforcement':True,'studio_abacus_included_production':True,
        'studio_abacus_prepaid_audio':False,'abacus_api_key':KEY}.items():
        monkeypatch.setattr(runtime.settings,name,value,raising=False)
    monkeypatch.setattr(runtime,'configured_ledger',lambda **kw:route.foundation)
    monkeypatch.setattr(runtime,'resolve_context',lambda *a:deepcopy(CONTEXT))
    monkeypatch.setattr(transport,'send_once',lambda *a:pytest.fail('No send permitted'))
    with pytest.raises(SpendBlocked,match='not_enabled'):included.generate_included_audio(mp3(),purpose='blind_asr',language='tr')
    monkeypatch.setattr(runtime.settings,'studio_abacus_prepaid_audio',True)
    with pytest.raises(SpendBlocked,match='not_commissioned'):included.generate_included_audio(mp3(),purpose='blind_asr',language='tr')
