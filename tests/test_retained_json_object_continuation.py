"""Explicit new admission across OAuth renewal; old unknown attempts stay closed."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from app.services import retained_json_object_continuation as repair
from app.services import retained_visual_enum_repair as previous
from app.services import retained_story_connection_rebind as rebound
from app.services import retained_review_captured_story_continuation as continuation
from app.services import retained_captured_story_scope as binding
from app.services import abacus_router_review_runtime as runtime
from app.services import abacus_router_review_journal as journal
from app.services.production_spend import SpendBlocked
from test_retained_visual_enum_repair import (
    enum_eligible, eligible, rejected, qualified, captured, source, planning_case,
    prepared, frozen_three, completed_probe, wire, forbid_live_transport, real_media,
    case, commission as previous_commission, opened, enum_schema, BUCKET, NOW, Intercept,
)
from test_abacus_router_adapter import image
from test_retained_story_connection_rebind import reconnect
from test_production_connection_continuity import _dump


@pytest.fixture
def json_eligible(enum_eligible):
    box = enum_eligible
    cap = previous_commission(box)
    box.wire.status = 400
    box.wire.chunks = [json.dumps({'success': False, 'errorType': 'UserFeedbackError',
        'error': 'Unable to process this request.'}).encode()]
    box.wire.headers = {'content-type': 'application/json'}
    with opened(box, cap) as scope:
        binding.bind_captured_story_predecessor(scope, story_evidence=box.qualification)
        with pytest.raises(SpendBlocked):
            runtime.generate_retained_router_review([{'type': 'text', 'text': 'Complete rubric'}, image()],
                purpose=journal.PURPOSES[1], system_instruction='Complete rubric', json_schema=enum_schema())
    box.old_last_capture = scope.transport_captures[journal.PURPOSES[1]]
    box.old_last_cap = cap
    reconnect(box)
    box.reconnected = rebound.read_reconnected_story_evidence(box.client, box.s3,
        bucket=BUCKET, qualification=box.qualification.record)
    box.qualification = box.reconnected.story_evidence
    box.json_policies = deepcopy(box.continuation_policies)
    bridge = box.reconnected.record
    for p in box.json_policies.values():
        p['current_connection_id'] = bridge['current_source']['current_connection_id']
        p['continuity_sha256'] = bridge['current_source_sha256']
    with box.client.pipeline() as pipe:
        snapshot, _ = repair._historical(pipe, box.qualification.record)
        continuation._ping(pipe)
    box.json_attestation = {**repair._ATTESTATION_FIXED,
        **{k: 'f' * 64 for k in repair._ATTESTATION_HASHES},
        'runtime_head_sha': box.continuation_attestation['runtime_head_sha'],
        'captured_story_evidence_sha256': continuation._hash(box.qualification.record),
        'source_bridge_sha256': continuation._hash(bridge),
        'predecessor_snapshot_sha256': snapshot['snapshot_sha256'],
        'current_credential_sha256': box.json_policies['story']['credential_sha256'],
        'entitlement_evidence_sha256': box.json_policies['story']['entitlement_evidence_sha256']}
    return box


def commission(box, client=None, **changes):
    return repair.commission_json_object_continuation(client or box.client, **{
        'reconnected_story': box.reconnected, 'story_policy': box.json_policies['story'],
        'audio_policy': box.json_policies['audio'], 'attestation': box.json_attestation,
        'clock': lambda: NOW, **changes})


def test_new_format_current_source_and_three_attempt_limit_keep_old_unknowns(json_eligible, subtests):
    box = json_eligible
    baseline, calls = _dump(box.client), len(box.wire.calls)
    for field in ('monthly_additional_cash_limit_micro', 'included_cash_allowance_micro',
                  'historical_extra_cash_micro', 'source_bridge_sha256'):
        with subtests.test(field=field):
            bad = {**box.json_attestation, field: '0' * 64 if field.endswith('sha256') else 1}
            with pytest.raises(SpendBlocked):
                commission(box, attestation=bad)
            assert _dump(box.client) == baseline and len(box.wire.calls) == calls
    cap = commission(box)
    assert cap.receipt['version'] == 4 and cap.receipt['max_total_attempts'] == 11
    assert cap.receipt['prior_occupied_count'] == 8 and cap.receipt['prior_unknown_count'] == 7
    assert cap.receipt['additional_attempt_limit'] == 3 and cap.receipt['qa_approved'] is False
    assert set(_dump(box.client)) - set(baseline) == set(repair.ALL_KEYS)
    assert repair.read_json_object_continuation(box.client).receipt == cap.receipt
    for old_key, old_value in baseline.items():
        assert box.client.dump(old_key) == old_value
    with pytest.raises(SpendBlocked):
        commission(box)
    with pytest.raises(SpendBlocked):
        previous.read_visual_enum_repair(box.client)
    with opened(box, cap) as scope:
        binding.bind_captured_story_predecessor(scope, story_evidence=box.qualification)
        with pytest.raises(SpendBlocked):
            runtime.generate_retained_router_review([{'type': 'text', 'text': 'Complete rubric'}, image()],
                purpose=journal.PURPOSES[1], system_instruction='Complete rubric', json_schema=enum_schema())
        assert scope.transport_captures[journal.PURPOSES[1]]['capture_acknowledged'] is True
        with pytest.raises(SpendBlocked):
            runtime.generate_retained_router_review([{'type': 'text', 'text': 'No repeat'}],
                purpose=journal.PURPOSES[1], system_instruction='Complete rubric', json_schema=enum_schema())
    assert len(box.wire.calls) == calls + 1
    assert json.loads(box.wire.calls[-1].content)['response_format'] == {'type': 'json_object'}
    assert all(box.client.dump(k) == v for k, v in baseline.items())


def test_lost_commission_ack_cannot_recommission(json_eligible):
    box = json_eligible
    def lost(commands, reply):
        if commands == ('SET', 'HSET', 'SET', 'SET', 'HSET', 'SET', 'SET', 'HSET', 'SET'):
            raise RuntimeError('Synthetic lost acknowledgement')
        return reply
    with pytest.raises(SpendBlocked):
        commission(box, Intercept(box.client, after=lost))
    after = _dump(box.client)
    with pytest.raises(SpendBlocked):
        commission(box)
    assert _dump(box.client) == after
    assert repair.read_json_object_continuation(box.client).receipt['qa_approved'] is False


@pytest.fixture
def json_visual(json_eligible, monkeypatch, tmp_path):
    box = json_eligible
    cap = commission(box)
    import test_preserved_captured_story_recovery as producer
    monkeypatch.setattr(producer, 'commission', lambda actual: cap if actual is box else None)
    box.wire.status = 200
    return producer.produced_captured_visual.__wrapped__(box, monkeypatch, tmp_path)


def test_reconnected_thirty_frames_audio_and_local_render(json_visual, monkeypatch, tmp_path):
    box = json_visual
    before = {k: box.client.dump(k) for k in repair.HISTORICAL_KEYS}
    import test_retained_captured_visual_scope as fixture
    fixture.audio_case.__wrapped__(box, monkeypatch)
    def response(request):
        body = json.loads(request.content)
        assert body['response_format'] == {'type': 'json_object'}
        blind = body['messages'][0]['content'] == fixture.adapter._ASR_SYSTEM
        result = box.asr_result if blind else box.prosody_result
        reply = fixture.envelope(result)
        reply['choices'][0]['native_finish_reason'] = 'stop'
        reply['usage'] = {'input_tokens': 46, 'output_tokens': 33, 'raw_input_tokens': 46, 'reasoning_tokens': 19}
        box.wire.chunks = [fixture.reader._raw(reply)]
        box.wire.headers = {'content-type': 'application/json',
                            'content-length': str(len(box.wire.chunks[0]))}
    box.wire.on_request = response
    initial = len(box.wire.calls)
    fixture.completed_captured_audio.__wrapped__(box)
    assert box.visual_evidence.record['component_pass'] is True
    assert box.audio_evidence.component_pass is True and len(box.wire.calls) == initial + 2
    assert box.visual_evidence.commitments['continuity_sha256'] == box.reconnected.record['current_source_sha256']
    assert box.visual_evidence.commitments['story_predecessor']['evidence'] == box.qualification.record
    import test_retained_render_consumer as rendering
    rendering.complete.__wrapped__(box, monkeypatch, tmp_path)
    result = rendering.consumer.render_retained_review(rendering.inputs(box), workdir=box.render_root)
    assert result.record['local_render_verified'] is True and result.record['local_final_gates']['pass'] is True
    assert result.record['qa_approved'] is False
    rendering.unchanged(box)
    assert all(box.client.dump(k) == v for k, v in before.items())
    import test_retained_final_artifacts as final
    box.final_render = result
    monkeypatch.setattr(box.s3, 'put_object', type(box.s3).put_object.__get__(box.s3))
    staged = final.safe_failure(lambda: final.stage(box))
    snapshot = staged.record['source_snapshot']
    assert set((*repair.HISTORICAL_KEYS, *repair.ALL_KEYS)) <= set(snapshot['permanent_record_keys'])
    control = continuation._checked(box.visual_cap)
    assert set(control['predecessors']['capture_records']) <= set(snapshot['permanent_record_keys'])
    from app.services import retained_production_admission as admission
    from app.services import retained_delivery_dispatch as delivery
    from app.services import retained_delivery_runtime as worker
    from app.services import studio_state
    from test_retained_delivery_completion import synthetic_youtube
    permit = admission.reserve_retained_child(box.client, staged)
    child, digest = permit.receipt['child_id'], permit.receipt['manifest_sha256']
    monkeypatch.setattr(studio_state, '_client', lambda: box.client)
    monkeypatch.setattr(worker.storage, '_client',
        lambda **kw: box.s3 if kw == {'single_attempt': True} else pytest.fail('Storage retries forbidden'))
    calls, completed = synthetic_youtube(box, monkeypatch), []
    def send(**kw):
        assert kw == {'kwargs': {'manifest_sha256': digest}, 'task_id': child, 'retry': False}
        completed.append(worker.run_retained_delivery(child, digest, work_root=tmp_path / 'delivery'))
        assert completed[-1]['status'] == 'complete', completed[-1]
        return SimpleNamespace(id=child)
    delivery.dispatch_retained_child(permit, send)
    published = _dump(box.client)
    assert worker.run_retained_delivery(child, digest, work_root=tmp_path / 'delivery') == completed[0]
    assert _dump(box.client) == published
    assert calls == ['upload', 'private_status', 'captions', 'release', 'public_status']
    assert all(box.client.dump(key) == value for key, value in before.items())
    assert len(box.wire.calls) == initial + 2
    box.no_new_work.assert_not_called()
