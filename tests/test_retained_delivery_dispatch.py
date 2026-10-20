"""Genuine corrected reviews/render/staging claim, one queue and one worker."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import fakeredis
import pytest

from app.services import retained_delivery_dispatch as delivery
from app.services import retained_production_admission as admission
from app.services import retained_visual_schema_repair as repair
from app.services import studio_state
from test_retained_visual_schema_repair import (
    corrected_visual, eligible, rejected, qualified, captured, source, case,
    planning_case, real_media, prepared, frozen_three, completed_probe, wire,
    forbid_live_transport,
)
from test_production_connection_continuity import _dump


@pytest.fixture
def admitted(corrected_visual, monkeypatch, tmp_path):
    box = corrected_visual
    import test_retained_captured_visual_scope as audio
    import test_retained_render_consumer as rendering
    import test_retained_final_artifacts as final
    audio.audio_case.__wrapped__(box, monkeypatch)
    audio.completed_captured_audio.__wrapped__(box)
    rendering.complete.__wrapped__(box, monkeypatch, tmp_path)
    final.rendered.__wrapped__(box, monkeypatch)
    prepared = final.stage(box)
    snapshot = prepared.record['source_snapshot']
    assert set((*repair.HISTORICAL_KEYS, *repair.ALL_KEYS)) <= set(snapshot['permanent_record_keys'])
    box.permit = admission.reserve_retained_child(box.client, prepared)
    box.child = box.permit.receipt['child_id']
    box.manifest_sha = box.permit.receipt['manifest_sha256']
    box.before_dispatch = _dump(box.client)
    box.sends = len(box.wire.calls)
    box.objects = dict(box.s3.objects)
    monkeypatch.setattr(studio_state, '_client', lambda: box.client)
    return box


def entry_guard():
    path = Path(__file__).resolve().parents[1] / 'app/tasks.py'
    node = next(n for n in ast.parse(path.read_text()).body
                if isinstance(n, ast.FunctionDef) and n.name == '_guard_retry_child_execution')
    class Ignore(Exception):
        pass
    acquire = Mock(side_effect=AssertionError('No generic execution mutation'))
    ns = {'Ignore': Ignore, 'acquire_retry_child_execution': acquire,
          'render_cancellation_requested': studio_state.render_cancellation_requested,
          'retained_delivery_blocked': studio_state.retained_delivery_blocked}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), ns)
    return ns['_guard_retry_child_execution'], Ignore, acquire


def generic_fences(box):
    before = _dump(box.client)
    c = admission.continuity
    guard, ignore, acquire = entry_guard()
    for task in (box.child, *c.LINEAGE):
        assert studio_state.retained_delivery_blocked(task)
        for retries in (0, 1, 2):
            for source in (None, c.LEAF_ID):
                with pytest.raises(ignore):
                    guard(SimpleNamespace(request=SimpleNamespace(retries=retries)), task, source)
        with pytest.raises(ValueError, match='dedicated delivery'):
            studio_state.claim_retry_dispatch(task, '99999999-9999-4999-8999-999999999999', 'x' * 32, allow_repair=False)
    dispatch = box.client.hgetall(c._DISPATCH + c.LEAF_ID)
    assert studio_state.mark_retry_dispatch(c.LEAF_ID, dispatch['token'], 'uncertain') is False
    assert studio_state.acquire_retry_child_execution(box.child, c.LEAF_ID) is False
    assert _dump(box.client) == before
    acquire.assert_not_called()


def unchanged(box):
    assert all(box.client.dump(key) == value for key, value in box.before_dispatch.items())
    assert len(box.wire.calls) == box.sends and box.s3.objects == box.objects
    box.no_new_work.assert_not_called()


def test_actual_corrected_final_single_queue_worker_and_generic_fences(admitted, subtests):
    box = admitted
    generic_fences(box)
    executions, calls = [], []
    def submit(**kwargs):
        calls.append(kwargs)
        assert kwargs == {'kwargs': {'manifest_sha256': box.manifest_sha}, 'task_id': box.child, 'retry': False}
        assert box.client.pttl(delivery.DISPATCH_KEY) == -1
        assert box.client.exists(delivery.DISPATCH_ACK_KEY, delivery.EXECUTION_KEY) == 0
        before = _dump(box.client)
        for child, digest in ((box.child, '0' * 64), ('99999999-9999-4999-8999-999999999999', box.manifest_sha)):
            with pytest.raises(delivery.RetainedDispatchError):
                delivery.acquire_retained_execution(box.client, child, digest)
            assert _dump(box.client) == before
        # A fast worker is allowed before the broker's acknowledgement returns.
        executions.append(delivery.acquire_retained_execution(box.client, box.child, box.manifest_sha))
        return SimpleNamespace(id=box.child)
    receipt = delivery.dispatch_retained_child(box.permit, submit)
    assert receipt['child_id'] == box.child and receipt['automatic_retry_permitted'] is False
    assert len(calls) == 1 and len(executions) == 1
    execution = executions[0]
    manifest = delivery.verify_execution(execution)
    assert manifest['authority']['publication_authorized'] is False
    for key in delivery._KEYS:
        assert box.client.pttl(key) == -1
    with pytest.raises(delivery.RetainedDispatchError): delivery.dispatch_retained_child(box.permit, submit)
    with pytest.raises(delivery.RetainedDispatchError):
        delivery.acquire_retained_execution(box.client, box.child, box.manifest_sha)
    for prefix in admission.continuity._ABSENT_PREFIXES:
        with subtests.test(child_hold=prefix):
            key = prefix + box.child
            box.client.set(key, '{}')
            with pytest.raises(delivery.RetainedDispatchError): delivery.verify_execution(execution)
            box.client.delete(key)
    generic_fences(box)
    assert len(calls) == 1
    unchanged(box)


def test_lost_broker_ack_keeps_intent_and_can_never_submit_again(admitted):
    box = admitted
    calls = []
    def submit(**kwargs):
        calls.append(kwargs)
        raise TimeoutError('PRIVATE broker outcome')
    with pytest.raises(delivery.RetainedDispatchError) as error:
        delivery.dispatch_retained_child(box.permit, submit)
    assert 'PRIVATE' not in str(error.value)
    assert box.client.pttl(delivery.DISPATCH_KEY) == -1 and not box.client.exists(delivery.DISPATCH_ACK_KEY)
    after = _dump(box.client)
    with pytest.raises(delivery.RetainedDispatchError): delivery.dispatch_retained_child(box.permit, submit)
    assert _dump(box.client) == after and len(calls) == 1
    # If the first message really reached the broker, it can run once. A lost
    # local acknowledgement does not fabricate a second message or execution.
    execution = delivery.acquire_retained_execution(box.client, box.child, box.manifest_sha)
    assert delivery.verify_execution(execution)['child_id'] == box.child
    with pytest.raises(delivery.RetainedDispatchError):
        delivery.acquire_retained_execution(box.client, box.child, box.manifest_sha)
    generic_fences(box)
    unchanged(box)


def test_no_unissued_permit_or_execution_can_access_a_backend():
    class NoIO:
        def __getattr__(self, name): raise AssertionError('No I/O')
    for value in (None, {}, object.__new__(admission.RetainedChildDispatchPermit)):
        with pytest.raises(delivery.RetainedDispatchError): delivery.dispatch_retained_child(value, NoIO())
    for value in (None, {}, object.__new__(delivery.RetainedDeliveryExecution)):
        with pytest.raises(delivery.RetainedDispatchError): delivery.verify_execution(value)


def test_partial_permanent_grant_and_child_indexes_fence_generic_mutations(monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(studio_state, '_client', lambda: client)
    c = admission.continuity
    child = '99999999-9999-4999-8999-999999999999'
    for key in (*admission.ROOT_KEYS, admission.CHILD_PREFIX + child):
        client.flushdb()
        client.set(key, 'partial')
        task = child if key.endswith(child) else c.LEAF_ID
        assert studio_state.retained_delivery_blocked(task)
        before = _dump(client)
        with pytest.raises(ValueError, match='dedicated delivery'):
            studio_state.claim_retry_dispatch(task, '88888888-8888-4888-8888-888888888888', 'x' * 32, allow_repair=False)
        assert studio_state.mark_retry_dispatch(task, 'x' * 32, 'dispatched') is False
        assert studio_state.acquire_retry_child_execution(child, task) is False
        assert _dump(client) == before
