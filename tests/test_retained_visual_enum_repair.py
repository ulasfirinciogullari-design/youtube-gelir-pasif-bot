"""A second genuine captured rejection has one fixed bounded successor."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from app.services import retained_visual_enum_repair as repair
from app.services import retained_visual_enum_rejection as rejection
from app.services import retained_review_captured_story_continuation as continuation
from app.services import retained_captured_story_scope as binding
from app.services import abacus_router_schema_compat as compat
from app.services import abacus_router_review_runtime as runtime
from app.services import abacus_router_review_journal as journal
from app.services.production_spend import SpendBlocked
from test_retained_visual_schema_repair import (
    eligible, rejected, qualified, captured, source, planning_case, prepared,
    frozen_three, completed_probe, wire, forbid_live_transport, real_media,
    commission as previous_commission, BUCKET, NOW, restore, Intercept, opened,
)
from test_production_connection_continuity import _dump
from test_retained_publication_plan import case
from test_abacus_router_adapter import SCHEMA, RESULT, image, prepare, envelope


def enum_schema():
    schema = deepcopy(SCHEMA)
    schema['properties']['accepted']['enum'] = [False]
    return schema


@pytest.fixture
def enum_eligible(eligible, monkeypatch):
    box = eligible
    monkeypatch.setattr(rejection, 'settings', box.config)
    cap = previous_commission(box)
    box.wire.status = 400
    box.wire.chunks = [json.dumps({'success': False, 'errorType': 'UserFeedbackError',
        'error': "Validation Error: ('enum',): Input should be a valid string"}).encode()]
    box.wire.headers = {'content-type': 'application/json'}
    with opened(box, cap) as scope:
        binding.bind_captured_story_predecessor(scope, story_evidence=box.qualification)
        with pytest.raises(SpendBlocked):
            runtime.generate_retained_router_review([{'type': 'text', 'text': 'Complete rubric'}, image()],
                purpose=rejection.PURPOSE, system_instruction='Complete rubric', json_schema=enum_schema())
    box.enum_parent_cap = cap
    box.enum_capture = scope.transport_captures[rejection.PURPOSE]
    box.enum_rejection = rejection.read_visual_enum_rejection(box.client, box.s3,
        bucket=BUCKET, captured_story_continuation=cap)
    record = box.enum_rejection.record
    assert record['historical_record_count'] == 45 and record['prior_occupied_count'] == 7
    box.enum_attestation = {**repair._ATTESTATION_FIXED,
        **{name: 'f' * 64 for name in repair._ATTESTATION_HASHES},
        'runtime_head_sha': box.continuation_attestation['runtime_head_sha'],
        'predecessor_snapshot_sha256': record['commitments']['history_sha256'],
        'captured_story_evidence_sha256': continuation._hash(box.qualification.record),
        'schema_rejection_evidence_sha256': continuation._hash(record),
        'current_credential_sha256': box.snapshot['credential_sha256'],
        'entitlement_evidence_sha256': box.continuation_policies['story']['entitlement_evidence_sha256']}
    return box


def commission(box, client=None, **changes):
    return repair.commission_visual_enum_repair(client or box.client, **{
        'story_evidence': box.qualification, 'rejection_evidence': box.enum_rejection,
        'story_policy': box.continuation_policies['story'], 'audio_policy': box.continuation_policies['audio'],
        'attestation': box.enum_attestation, 'clock': lambda: NOW, **changes})


def test_exact_enum_correction_preserves_all_history_and_cannot_repeat(enum_eligible, subtests):
    box = enum_eligible
    baseline, calls = _dump(box.client), len(box.wire.calls)
    for damage in ('raw', 'forged', 'source', 'capture', 'expiry', 'attestation', 'cash', 'partial'):
        with subtests.test(damage=damage):
            changes = {}
            if damage == 'raw': changes['rejection_evidence'] = box.enum_rejection.record
            elif damage == 'forged': changes['rejection_evidence'] = object.__new__(rejection.RetainedVisualEnumRejection)
            elif damage == 'source': box.client.set(rejection.continuity._AUTH_EPOCH, '99')
            elif damage == 'capture': box.client.delete(box.enum_capture['anchor_key'])
            elif damage == 'expiry': box.client.expire(rejection.previous.VISUAL_KEYS[0], 60)
            elif damage == 'attestation': changes['attestation'] = {**box.enum_attestation, 'schema_rejection_evidence_sha256': '0' * 64}
            elif damage == 'cash': changes['attestation'] = {**box.enum_attestation, 'included_cash_allowance_micro': 1}
            else: box.client.set(repair.ANCHOR_KEY, 'partial')
            before = _dump(box.client)
            with pytest.raises(SpendBlocked): commission(box, **changes)
            assert _dump(box.client) == before and len(box.wire.calls) == calls
            restore(box.client, baseline)
    cap = commission(box)
    after = _dump(box.client)
    assert set(after) - set(baseline) == set(repair.ALL_KEYS)
    assert all(box.client.dump(key) == value for key, value in baseline.items())
    assert cap.receipt['version'] == 3 and cap.receipt['prior_occupied_count'] == 7
    assert cap.receipt['prior_unknown_count'] == 6 and cap.receipt['max_total_attempts'] == 10
    assert cap.receipt['additional_attempt_limit'] == 3
    assert continuation.controller_keys(cap) == repair.ALL_KEYS
    assert continuation.historical_keys(cap) == repair.HISTORICAL_KEYS
    assert len(repair.HISTORICAL_KEYS) == 45
    assert repair.read_visual_enum_repair(box.client).receipt == cap.receipt
    with pytest.raises(SpendBlocked): commission(box)
    ledger = journal.RouterReviewJournal(box.client, clock=lambda: NOW, captured_story_continuation=cap)
    with pytest.raises(SpendBlocked): ledger.reserve(rejection.PURPOSE, prepare(api_key=box.config.abacus_api_key))
    assert _dump(box.client) == after
    payload = envelope();payload['choices'][0]['message']['content'] = json.dumps(RESULT)
    box.wire.status = 200;box.wire.chunks = [json.dumps(payload).encode()]
    with opened(box, cap) as scope:
        binding.bind_captured_story_predecessor(scope, story_evidence=box.qualification)
        assert runtime.generate_retained_router_review([{'type': 'text', 'text': 'Complete rubric'}, image()],
            purpose=rejection.PURPOSE, system_instruction='Complete rubric', json_schema=enum_schema()) == RESULT
        with pytest.raises(SpendBlocked):
            runtime.generate_retained_router_review([{'type': 'text', 'text': 'No repeat'}],
                purpose=rejection.PURPOSE, system_instruction='Complete rubric', json_schema=enum_schema())
    sent = json.loads(box.wire.calls[-1].content)
    assert sent['response_format']['json_schema']['name'] == compat.ENUM_SCHEMA_NAME
    assert compat.schema_for_body(sent) == enum_schema()
    assert all(box.client.dump(key) == value for key, value in baseline.items())
    assert len(box.wire.calls) == calls + 1


def test_lost_enum_commission_ack_preserves_one_controller(enum_eligible):
    box = enum_eligible
    def lost(commands, reply):
        if commands == ('SET', 'HSET', 'SET', 'SET', 'HSET', 'SET', 'SET', 'HSET', 'SET'):
            raise RuntimeError('PRIVATE lost acknowledgement')
        return reply
    with pytest.raises(SpendBlocked): commission(box, Intercept(box.client, after=lost))
    after = _dump(box.client)
    with pytest.raises(SpendBlocked): commission(box)
    assert _dump(box.client) == after and repair.read_visual_enum_repair(box.client).receipt['qa_approved'] is False


@pytest.fixture
def enum_corrected_visual(enum_eligible, monkeypatch, tmp_path):
    box = enum_eligible
    cap = commission(box)
    import test_preserved_captured_story_recovery as producer
    monkeypatch.setattr(producer, 'commission', lambda actual: cap if actual is box else None)
    box.wire.status = 200
    return producer.produced_captured_visual.__wrapped__(box, monkeypatch, tmp_path)


def test_real_thirty_images_audio_and_render_use_enum_controller(enum_corrected_visual, monkeypatch, tmp_path):
    box = enum_corrected_visual
    before = {key: box.client.dump(key) for key in repair.HISTORICAL_KEYS}
    import test_retained_captured_visual_scope as audio
    audio.audio_case.__wrapped__(box, monkeypatch)
    initial = len(box.wire.calls)
    audio.completed_captured_audio.__wrapped__(box)
    assert box.visual_evidence.record['component_pass'] is True and box.audio_evidence.component_pass is True
    assert box.visual_evidence.commitments['journal_keys'] == list(repair.VISUAL_KEYS)
    assert len(box.wire.calls) == initial + 2
    for request in box.wire.calls[-2:]:
        assert json.loads(request.content)['response_format']['json_schema']['name'].endswith('_enum_v1')
    assert all(box.client.dump(key) == value for key, value in before.items())
    import test_retained_render_consumer as rendering
    rendering.complete.__wrapped__(box, monkeypatch, tmp_path)
    result = rendering.consumer.render_retained_review(rendering.inputs(box), workdir=box.render_root)
    assert result.record['local_final_gates']['pass'] is True and result.record['qa_approved'] is False
    assert result.record['final_aac_independently_listened'] is False
    rendering.unchanged(box)
    import test_retained_final_artifacts as final
    box.final_render = result
    monkeypatch.setattr(box.s3, 'put_object', type(box.s3).put_object.__get__(box.s3))
    source_before = _dump(box.client)
    staged = final.safe_failure(lambda: final.stage(box))
    snapshot = staged.record['source_snapshot']
    assert set((*repair.HISTORICAL_KEYS, *repair.ALL_KEYS)) <= set(snapshot['permanent_record_keys'])
    for evidence in (box.rejection, box.enum_rejection):
        for name in ('intent_key', 'anchor_key'):
            assert evidence.record['commitments'][name] in snapshot['permanent_record_keys']
    assert _dump(box.client) == source_before and len(box.wire.calls) == initial + 2
    # Exercise the exact v3 snapshot through the real worker as well. Only the
    # upstream Google responses are synthetic; no live provider is contacted.
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
