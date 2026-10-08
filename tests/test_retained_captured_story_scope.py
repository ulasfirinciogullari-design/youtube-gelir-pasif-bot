"""Same real saved evidence, same live owning scope; no copied diagnostic permit."""
from contextvars import copy_context
from threading import Thread

import pytest
from redis.exceptions import ConnectionError

from app.services import retained_captured_story_scope as binding
from app.services import retained_review_captured_story_continuation as continuation
from app.services import retained_transport_story_evidence as reader
from app.services import abacus_router_review_runtime as runtime
from app.services.production_spend import SpendBlocked
from test_retained_review_captured_story_continuation import (
    qualified, captured, source, case, planning_case, real_media, prepared, frozen_three,
    completed_probe, wire, forbid_live_transport, commission, restore, Intercept,
)
from test_production_connection_continuity import _dump


def opened(box, cap):
    return runtime.retained_router_review_scope(runtime.LEAF_ID,
        captured_story_continuation=cap,capture_transport=True)


def send_visual():
    return runtime.generate_retained_router_review([{'type':'text','text':'synthetic visual'}],
        purpose=runtime.PURPOSES[1],system_instruction='Return JSON',max_tokens=32,
        json_schema={'type':'object','properties':{'ok':{'type':'boolean'}},
                     'required':['ok'],'additionalProperties':False})


def test_binding_preserves_same_issued_object_detached_values_and_scope_lifetime(qualified, subtests):
    box=qualified; cap=commission(box); before=_dump(box.client)
    with opened(box,cap) as scope:
        binding.bind_captured_story_predecessor(scope,story_evidence=box.qualification)
        assert binding.captured_story_predecessor(scope) is box.qualification
        public=binding.captured_story_predecessor(scope).record
        public['component_pass']=False; public['commitments']['request_sha256']='0'*64
        assert binding.captured_story_predecessor(scope).record['component_pass'] is True
        assert binding.captured_story_predecessor(scope).qa_approved is False
        with pytest.raises(SpendBlocked,match='already_bound'):
            binding.bind_captured_story_predecessor(scope,story_evidence=box.qualification)
        assert binding.captured_story_predecessor(scope) is box.qualification
        context=copy_context(); failures=[]
        def wrong_thread():
            try:context.run(binding.captured_story_predecessor,scope)
            except SpendBlocked:failures.append('blocked')
        thread=Thread(target=wrong_thread);thread.start();thread.join(timeout=5)
        assert not thread.is_alive() and failures==['blocked']
        assert binding.captured_story_predecessor(scope) is box.qualification
    with pytest.raises(SpendBlocked):binding.captured_story_predecessor(scope)
    with opened(box,cap) as other:
        with pytest.raises(SpendBlocked):binding.captured_story_predecessor(other)
        with pytest.raises(SpendBlocked):binding.captured_story_predecessor(scope)
    assert _dump(box.client)==before and len(box.wire.calls)==2 and len(box.s3.puts)==1


def test_no_boolean_copy_forged_or_missing_binding_can_send_visual(qualified, subtests):
    box=qualified; cap=commission(box); before=_dump(box.client)
    for value in (None,True,{},box.qualification.record,object.__new__(reader.RetainedTransportStoryEvidence)):
        with subtests.test(value=type(value).__name__):
            with opened(box,cap) as scope:
                with pytest.raises(SpendBlocked):binding.bind_captured_story_predecessor(scope,story_evidence=value)
                assert scope.failed is True
                with pytest.raises(SpendBlocked):binding.bind_captured_story_predecessor(scope,story_evidence=box.qualification)
                with pytest.raises(SpendBlocked):send_visual()
            assert _dump(box.client)==before
    with opened(box,cap) as scope:
        with pytest.raises(SpendBlocked,match='binding_required'):send_visual()
        assert scope.failed is True and scope.attempted==set()
    assert _dump(box.client)==before and len(box.wire.calls)==2 and len(box.s3.puts)==1


def test_binding_ack_source_and_post_bind_context_changes_fail_closed(qualified, monkeypatch, subtests):
    box=qualified; cap=commission(box); before=_dump(box.client)
    foundation=runtime.spending.configured_ledger.return_value
    original=foundation.client
    for mode in ('lost_ack','typed_ack','source_race','capture_race','credential_race'):
        with subtests.test(mode=mode),monkeypatch.context() as patch:
            active=[False]
            def before_exec(commands):
                if active[0] and commands==('PING',):
                    if mode=='source_race':
                        from app.services import preserved_visual_recovery as recovery
                        box.client.set(recovery._keys(runtime.LEAF_ID)[0],'changed')
                    elif mode=='capture_race':box.client.delete(box.qualification.commitments['capture_anchor_key'])
            def after_exec(commands,ack):
                if active[0] and commands==('PING',):
                    if mode=='lost_ack':raise ConnectionError('PRIVATE raw backend failure')
                    if mode=='typed_ack':return [1]
                    if mode=='credential_race':patch.setattr(box.config,'abacus_api_key','changed-private-key')
                return ack
            client=Intercept(box.client,before=before_exec,after=after_exec)
            patch.setattr(foundation,'client',client)
            with opened(box,cap) as scope:
                active[0]=True
                with pytest.raises(SpendBlocked) as caught:
                    binding.bind_captured_story_predecessor(scope,story_evidence=box.qualification)
                assert 'PRIVATE' not in str(caught.value) and scope.failed is True
                with pytest.raises(SpendBlocked):binding.captured_story_predecessor(scope)
                with pytest.raises(SpendBlocked):send_visual()
            assert all(commands==('PING',) for commands in client.executions)
        restore(box.client,before)
    assert foundation.client is original
    for mode in ('key','source','capture','journal','cap'):
        with subtests.test(after_binding=mode),monkeypatch.context() as patch:
            with opened(box,cap) as scope:
                binding.bind_captured_story_predecessor(scope,story_evidence=box.qualification)
                if mode=='key':patch.setattr(box.config,'abacus_api_key','different-private-key')
                elif mode=='source':
                    from app.services import preserved_visual_recovery as recovery
                    box.client.set(recovery._keys(runtime.LEAF_ID)[0],'changed')
                elif mode=='capture':box.client.delete(box.qualification.commitments['capture_anchor_key'])
                elif mode=='journal':scope.journal=type(scope.journal)(box.client,captured_story_continuation=cap)
                else:scope.journal._captured_story_continuation=continuation.read_captured_story_continuation(box.client)
                with pytest.raises(SpendBlocked):binding.captured_story_predecessor(scope)
        restore(box.client,before)
    assert len(box.wire.calls)==2 and len(box.s3.puts)==1
