"""Disposable genuine saved-STORY history; no live commission or provider access."""
from copy import copy, deepcopy
from contextvars import copy_context
from datetime import timedelta
from threading import Thread

import pytest
from redis.exceptions import ConnectionError

from app.services import retained_review_captured_story_continuation as continuation
from app.services import retained_review_completion_plan as completion
from app.services import retained_transport_story_evidence as evidence
from app.services import abacus_router_adapter as adapter
from app.services import abacus_router_review_journal as story
from app.services import abacus_router_audio_review_journal as audio
from app.services import abacus_router_review_runtime as runtime
from app.services import abacus_router_audio_review_runtime as audio_runtime
from app.services import abacus_router_transport_capture as capture
from app.services.production_spend import SpendBlocked
from test_retained_transport_story_evidence import (
    captured, read, source, case, planning_case, real_media, prepared, frozen_three,
    completed_probe, wire, forbid_live_transport, BUCKET, NEW_KEY, NOW,
)
from test_retained_review_credential_successor import Intercept, records
from test_production_connection_continuity import _dump
from test_abacus_router_review_journal import request, response

_STORY, VISUAL = story.PURPOSES
_ORIGINALS = {name: getattr(story.RouterReviewJournal, name) for name in ('reserve', 'settle', '_fresh')}
_OBSERVER = adapter.observe_router_response
_GENERATE = runtime.generate_retained_router_review
_CAPTURE_SEND = capture._send


@pytest.fixture
def qualified(captured, monkeypatch):
    box = captured
    box.qualification = read(box)
    # Saved reader's fixture forbids live operations. Restore only for new,
    # disposable selected namespaces below; old captured STORY stays unknown.
    for name, method in _ORIGINALS.items():
        monkeypatch.setattr(story.RouterReviewJournal, name, method)
    monkeypatch.setattr(adapter, 'observe_router_response', _OBSERVER)
    monkeypatch.setattr(runtime, 'generate_retained_router_review', _GENERATE)
    monkeypatch.setattr(capture, '_send', _CAPTURE_SEND)
    monkeypatch.setattr(box.s3, 'put_object', type(box.s3).put_object.__get__(box.s3))
    box.continuation_policies = deepcopy(box.completion_policies)
    box.snapshot = continuation.snapshot_captured_story_predecessors(box.client, story_evidence=box.qualification)
    box.continuation_attestation = {**continuation._ATTESTATION_FIXED,
        **{name: 'f'*64 for name in continuation._ATTESTATION_HASHES},
        'runtime_head_sha': box.completion_attestation['runtime_head_sha'],
        'predecessor_snapshot_sha256': box.snapshot['snapshot_sha256'],
        'captured_story_evidence_sha256': continuation._hash(box.qualification.record),
        'current_credential_sha256': box.snapshot['credential_sha256'],
        'entitlement_evidence_sha256': box.continuation_policies['story']['entitlement_evidence_sha256']}
    box.frozen = _dump(box.client)
    return box


def commission(box, client=None, **changes):
    return continuation.commission_captured_story_continuation(client or box.client, **{
        'story_evidence': box.qualification, 'story_policy': box.continuation_policies['story'],
        'audio_policy': box.continuation_policies['audio'], 'attestation': box.continuation_attestation,
        'clock': lambda: NOW, **changes})


def selected(box, cap, *, client=None, clock=None):
    options = {'captured_story_continuation': cap, 'clock': clock or (lambda: NOW)}
    return story.RouterReviewJournal(client or box.client, **options), audio.RouterAudioReviewJournal(client or box.client, **options)


def preserved(box):
    assert {k:v for k,v in _dump(box.client).items() if k not in continuation.ALL_KEYS} == box.frozen


def test_default_selection_does_not_inspect_new_records_or_accept_raw_keys():
    class NoIO:
        def __getattr__(self, name):
            raise AssertionError('Selection must not perform I/O.')
    for module, cls in ((story, story.RouterReviewJournal), (audio, audio.RouterAudioReviewJournal)):
        ledger=cls(NoIO())
        assert ledger.keys==(module.STATE_KEY,module.JOURNAL_KEY,module.ANCHOR_KEY)
        for invalid in (True,False,{},list(continuation.VISUAL_KEYS)):
            with pytest.raises(SpendBlocked):cls(NoIO(),captured_story_continuation=invalid)
        with pytest.raises(SpendBlocked):
            cls(NoIO(),successor=True,captured_story_continuation=True)
        with pytest.raises(SpendBlocked):
            cls(NoIO(),completion_plan=True,captured_story_continuation=True)


def restore(client, snapshot):
    """Test-only rollback of a disposable FakeRedis between independent negatives."""
    client.flushdb()
    for key, value in snapshot.items():
        client.restore(key, 0, value)


def test_genuine_qualification_commissions_exact_nine_and_cannot_issue_story_or_audio(qualified, subtests):
    box = qualified
    client = Intercept(box.client)
    cap = commission(box, client)
    assert client.executions == [('SET','HSET','SET','SET','HSET','SET','SET','HSET','SET'), ('PING',)]
    assert set(_dump(box.client))-set(box.frozen) == set(continuation.ALL_KEYS)
    assert all(box.client.pttl(k) == -1 for k in continuation.ALL_KEYS)
    assert cap.receipt['prior_occupied_count'] == 5 and cap.receipt['prior_unknown_count'] == 4
    assert cap.receipt['additional_attempt_limit'] == 3 and cap.receipt['max_total_attempts'] == 8
    assert all(cap.receipt[k] is value for k,value in continuation._FLAGS.items())
    assert len(box.snapshot['records']) == 27
    receipt = cap.receipt; receipt['max_total_attempts'] = 999
    assert cap.receipt['max_total_attempts'] == 8
    assert NEW_KEY not in repr(cap) + str(cap.receipt)
    assert continuation.read_captured_story_continuation(box.client).receipt == cap.receipt
    for invalid in (None, True, {}, cap.receipt, box.cap, object.__new__(continuation.CapturedStoryContinuationAuthorization)):
        with subtests.test(invalid_type=type(invalid).__name__):
            with pytest.raises(SpendBlocked): continuation.selected_keys(invalid, 'story')
    with pytest.raises(TypeError): continuation.CapturedStoryContinuationAuthorization()
    with pytest.raises(TypeError): copy(cap)
    class Forged(continuation.CapturedStoryContinuationAuthorization):
        pass
    with pytest.raises(SpendBlocked):continuation.selected_keys(object.__new__(Forged),'story')
    text, audio_ledger = selected(box, cap)
    assert text.keys == continuation.VISUAL_KEYS and audio_ledger.keys == continuation.AUDIO_KEYS
    with pytest.raises(SpendBlocked):text.commission(box.continuation_policies['story'])
    with pytest.raises(SpendBlocked):audio_ledger.commission(box.continuation_policies['audio'],
                                                          source_metadata_bytes=box.source.metadata_bytes)
    before = _dump(box.client)
    with pytest.raises(SpendBlocked): text.reserve(_STORY, request(key=NEW_KEY))
    assert _dump(box.client) == before
    with pytest.raises(SpendBlocked, match='^captured_story_continuation_attempt_limit_or_order$'):
        with box.client.pipeline() as pipe: audio_ledger._admit(pipe, reserve=True)
    visual = request('synthetic complete retained visual', key=NEW_KEY)
    reserve = text.reserve(VISUAL, visual)
    assert reserve['purpose'] == VISUAL
    text.settle(VISUAL, visual, response(visual))  # Protocol-ACKed negative never opens audio.
    with pytest.raises(SpendBlocked, match='^router_audio_predecessor_admission_unverified$'):
        with box.client.pipeline() as pipe: audio_ledger._admit(pipe, reserve=True)
    assert audio_ledger._read(box.client)['slots'] == {}
    assert set(text._read(box.client)['slots']) == {VISUAL}
    assert len(box.wire.calls) == 2  # Actual fixture probe + failed captured STORY, no sends here.
    with pytest.raises(SpendBlocked): commission(box)
    preserved(box)


def test_closed_inputs_and_every_predecessor_capture_and_partial_new_record_fail_closed(qualified, subtests):
    box = qualified
    for value in (True, {}, box.qualification.record, object.__new__(evidence.RetainedTransportStoryEvidence)):
        with subtests.test(qualification=type(value).__name__):
            with pytest.raises(SpendBlocked): commission(box, story_evidence=value)
            assert _dump(box.client) == box.frozen
    for key in continuation.ALL_KEYS:
        with subtests.test(partial=key):
            box.client.set(key, 'partial')
            before = _dump(box.client)
            with pytest.raises(SpendBlocked): commission(box)
            assert _dump(box.client) == before
            restore(box.client, box.frozen)
    for key in (*continuation.HISTORICAL_KEYS, box.qualification.commitments['intent_key'],
                box.qualification.commitments['capture_anchor_key']):
        with subtests.test(missing=key):
            box.client.delete(key)
            before = _dump(box.client)
            with pytest.raises(SpendBlocked): commission(box)
            assert _dump(box.client) == before
            restore(box.client, box.frozen)
    for field,value in [('version',True),('captured_story_evidence_sha256','0'*64),
                        ('predecessor_snapshot_sha256','0'*64),('current_credential_sha256','0'*64),
                        ('included_cash_allowance_micro',1),('runtime_head_sha','0'*40),('extra',False)]:
        with subtests.test(attestation=field):
            attestation = {**box.continuation_attestation,field:value}
            with pytest.raises(SpendBlocked): commission(box, attestation=attestation)
            assert _dump(box.client) == box.frozen
    for kind,field,value in [('story','credential_sha256','0'*64),('audio','source_metadata_sha256','0'*64),
                             ('story','profile_revision','changed'),('audio','new_cash_allowance_micro',1)]:
        with subtests.test(policy=kind,field=field):
            policy = {**box.continuation_policies[kind],field:value}
            with pytest.raises(SpendBlocked): commission(box, **{kind+'_policy':policy})
            assert _dump(box.client) == box.frozen


def test_atomic_commission_ack_uncertainty_and_watched_source_races(qualified, subtests):
    box = qualified
    for mode in ('lost', 'partial', 'typed', 'readback', 'source_race', 'control_race', 'capture_race'):
        with subtests.test(mode=mode):
            def before(commands):
                if len(commands) == 9 and mode == 'source_race':
                    from app.services import preserved_visual_recovery as recovery
                    box.client.set(recovery._keys(runtime.LEAF_ID)[0], 'changed')
                if len(commands) == 9 and mode == 'control_race':
                    box.client.set(continuation.completion.STATE_KEY, 'changed')
                if len(commands) == 9 and mode == 'capture_race':
                    box.client.delete(box.qualification.commitments['capture_anchor_key'])
            def after(commands, ack):
                if mode == 'readback' and commands == ('PING',): return [1]
                if len(commands) == 9:
                    if mode == 'lost': raise ConnectionError('PRIVATE transport text')
                    if mode == 'partial': return ack[:-1]
                    if mode == 'typed': return [1,*ack[1:]]
                return ack
            client = Intercept(box.client,before=before,after=after)
            with pytest.raises(SpendBlocked) as caught: commission(box,client)
            assert 'PRIVATE' not in str(caught.value)
            occupied = box.client.exists(*continuation.ALL_KEYS)
            assert occupied == (0 if mode.endswith('race') else 9)
            before_second = _dump(box.client)
            with pytest.raises(SpendBlocked): commission(box)
            assert _dump(box.client) == before_second
            restore(box.client,box.frozen)


def test_selected_heads_rollback_and_reserve_ack_loss_are_terminal(qualified, subtests):
    box=qualified; cap=commission(box); original=_dump(box.client)
    visual=request('selected visual',key=NEW_KEY)
    for mode in ('lost','typed','child_rollback','control_rollback'):
        with subtests.test(mode=mode):
            def after(commands,ack):
                if len(commands)==5:
                    if mode=='lost': raise ConnectionError('PRIVATE lost ACK')
                    if mode=='typed': return [1,*ack[1:]]
                return ack
            text,_=selected(box,cap,client=Intercept(box.client,after=after))
            if mode in ('lost','typed'):
                with pytest.raises(SpendBlocked):text.reserve(VISUAL,visual)
            else:
                text.reserve(VISUAL,visual)
                keys=continuation.VISUAL_KEYS if mode=='child_rollback' else (continuation.JOURNAL_KEY,continuation.ANCHOR_KEY)
                for key in keys:
                    box.client.delete(key);box.client.restore(key,0,original[key])
                with pytest.raises(SpendBlocked):continuation.read_captured_story_continuation(box.client)
            before=_dump(box.client)
            with pytest.raises(SpendBlocked):text.reserve(VISUAL,visual)
            assert _dump(box.client)==before
            restore(box.client,original)
    preserved(box)


def test_runtime_selection_requires_capture_and_uses_actual_new_visual_transport(qualified, monkeypatch, subtests):
    box=qualified; cap=commission(box)
    for scope in (runtime.retained_router_review_scope,audio_runtime.retained_audio_router_review_scope):
        for options in ({'capture_transport':False},{'capture_transport':True,'completion_plan':box.cap},
                        {'capture_transport':True,'successor':True},
                        {'capture_transport':True,'captured_story_continuation':True}):
            with subtests.test(scope=scope.__name__,options=tuple(options)):
                with pytest.raises(SpendBlocked):
                    with scope(runtime.LEAF_ID,**{'captured_story_continuation':cap,**options}):pass
    before=_dump(box.client)
    with runtime.retained_router_review_scope(runtime.LEAF_ID,captured_story_continuation=cap,capture_transport=True):
        with pytest.raises(SpendBlocked):
            runtime.generate_retained_router_review([{'type':'text','text':'forbidden story'}],purpose=_STORY,
                system_instruction='Return JSON',json_schema={'type':'object','properties':{},'required':[],
                'additionalProperties':False},max_tokens=32)
    assert _dump(box.client)==before and len(box.wire.calls)==2
    box.wire.headers={'content-type':'application/json'}
    box.wire.chunks=[b'{"model":"synthetic","choices":[{"index":0,"finish_reason":"stop","message":{"role":"assistant","content":"{\\"approved\\":false}"}}]}']
    kwargs={'purpose':VISUAL,'system_instruction':'Return the complete requested JSON object.',
        'json_schema':{'type':'object','properties':{'approved':{'type':'boolean'}},'required':['approved'],
                       'additionalProperties':False},'max_tokens':128}
    with runtime.retained_router_review_scope(runtime.LEAF_ID,captured_story_continuation=cap,capture_transport=True) as scope:
        from app.services.retained_captured_story_scope import bind_captured_story_predecessor
        bind_captured_story_predecessor(scope,story_evidence=box.qualification)
        result=runtime.generate_retained_router_review([{'type':'text','text':'new visual'}],**kwargs)
        assert result=={'approved':False}
        receipt=scope.transport_captures[VISUAL]
        assert receipt['capture_acknowledged'] is True
        assert receipt['binding']['journal_keys']==list(continuation.VISUAL_KEYS)
        artifact=runtime.retained_router_review_artifacts()[VISUAL]
        assert artifact.purpose==VISUAL and artifact.qa_approved is False
    assert len(box.wire.calls)==3 and len(box.s3.puts)==2
    assert all(key not in _dump(box.client) or _dump(box.client)[key]==value for key,value in box.frozen.items())
    assert selected(box,cap)[1]._read(box.client)['slots']=={}


def test_current_key_source_cash_window_and_owner_context_cannot_extend_selection(qualified, monkeypatch, subtests):
    box=qualified; cap=commission(box); baseline=_dump(box.client)
    for damage in ('credential','encryption','head','cash','source','capture_expiry'):
        with subtests.test(damage=damage), monkeypatch.context() as patch:
            if damage=='credential': patch.setattr(box.config,'abacus_api_key','different-offline-key')
            elif damage=='encryption': patch.setattr(box.config,'app_encryption_key','different-encryption-material')
            elif damage=='head': patch.setenv('RAILWAY_GIT_COMMIT_SHA','0'*40)
            elif damage=='cash': patch.setattr(box.config,'studio_spend_enforcement',False)
            elif damage=='source':
                from app.services import preserved_visual_recovery as recovery
                box.client.set(recovery._keys(runtime.LEAF_ID)[0],'changed')
            else: box.client.expire(box.qualification.commitments['capture_anchor_key'],60)
            before=_dump(box.client)
            with pytest.raises(SpendBlocked):continuation.verify_scope_captured_story_continuation(box.client,cap)
            assert _dump(box.client)==before
        restore(box.client,baseline)
    late,_=selected(box,cap,clock=lambda:NOW+timedelta(days=2))
    with pytest.raises(SpendBlocked):late.reserve(VISUAL,request(key=NEW_KEY))
    assert continuation.read_captured_story_continuation(box.client).receipt==cap.receipt
    kwargs={'purpose':VISUAL,'system_instruction':'Return JSON',
        'json_schema':{'type':'object','properties':{'approved':{'type':'boolean'}},
                       'required':['approved'],'additionalProperties':False},'max_tokens':32}
    with runtime.retained_router_review_scope(runtime.LEAF_ID,captured_story_continuation=cap,capture_transport=True):
        context=copy_context(); errors=[]
        def wrong_thread():
            try:context.run(runtime.generate_retained_router_review,[{'type':'text','text':'wrong owner'}],**kwargs)
            except SpendBlocked:errors.append('blocked')
        thread=Thread(target=wrong_thread);thread.start();thread.join(timeout=5)
        assert not thread.is_alive() and errors==['blocked']
    with pytest.raises(SpendBlocked):runtime.generate_retained_router_review([{'type':'text','text':'closed'}],**kwargs)
    assert _dump(box.client)==baseline and len(box.wire.calls)==2
    box.config.studio_abacus_router_retained_audio_review_enabled=True
    with audio_runtime.retained_audio_router_review_scope(runtime.LEAF_ID,captured_story_continuation=cap,capture_transport=True):
        with pytest.raises(SpendBlocked,match='^router_audio_review_state_or_outcome_unverified$'):
            audio_runtime.run_blind_asr(box.source.audio_path.read_bytes())
    assert _dump(box.client)==baseline and len(box.wire.calls)==2
