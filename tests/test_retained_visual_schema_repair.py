"""Explicit corrected namespace, preserving a genuine prior failed transport."""
from copy import deepcopy
import json

import pytest

from app.services import retained_visual_schema_repair as repair
from app.services import retained_visual_schema_rejection as rejection
from app.services import retained_review_captured_story_continuation as continuation
from app.services import retained_captured_story_scope as binding
from app.services import abacus_router_schema_compat as compat
from app.services import abacus_router_review_runtime as runtime
from app.services import abacus_router_review_journal as journal
from app.services import abacus_router_transport_capture as capture
from app.services.production_spend import SpendBlocked
from test_retained_visual_schema_rejection import (
    rejected, qualified, captured, source, case, planning_case, prepared,
    frozen_three, completed_probe, wire, forbid_live_transport, BUCKET,
)
from test_retained_review_captured_story_continuation import (
    _ORIGINALS, _GENERATE, _CAPTURE_SEND, NOW, restore, Intercept,
)
from test_retained_captured_story_scope import opened
from test_production_connection_continuity import _dump
from test_abacus_router_adapter import SCHEMA, RESULT, image, prepare, envelope
from test_retained_render_consumer import real_media


@pytest.fixture
def eligible(rejected, monkeypatch):
    box = rejected
    box.rejection = rejection.read_visual_schema_rejection(box.client, box.s3,
        bucket=BUCKET, captured_story_continuation=box.visual_cap)
    box.old_visual_cap = box.visual_cap
    for name, method in _ORIGINALS.items():
        monkeypatch.setattr(journal.RouterReviewJournal, name, method)
    monkeypatch.setattr(runtime, 'generate_retained_router_review', _GENERATE)
    monkeypatch.setattr(capture, '_send', _CAPTURE_SEND)
    monkeypatch.setattr(box.s3, 'put_object', type(box.s3).put_object.__get__(box.s3))
    box.repair_attestation = {**repair._ATTESTATION_FIXED,
        **{name: 'f' * 64 for name in repair._ATTESTATION_HASHES},
        'runtime_head_sha': box.continuation_attestation['runtime_head_sha'],
        'predecessor_snapshot_sha256': box.rejection.record['commitments']['history_sha256'],
        'captured_story_evidence_sha256': continuation._hash(box.qualification.record),
        'schema_rejection_evidence_sha256': continuation._hash(box.rejection.record),
        'current_credential_sha256': box.snapshot['credential_sha256'],
        'entitlement_evidence_sha256': box.continuation_policies['story']['entitlement_evidence_sha256']}
    return box


def commission(box, client=None, **changes):
    return repair.commission_visual_schema_repair(client or box.client, **{
        'story_evidence': box.qualification, 'rejection_evidence': box.rejection,
        'story_policy': box.continuation_policies['story'], 'audio_policy': box.continuation_policies['audio'],
        'attestation': box.repair_attestation, 'clock': lambda: NOW, **changes})


def test_exact_correction_is_permanent_bounded_and_preserves_every_predecessor(eligible, monkeypatch, subtests):
    box = eligible
    baseline = _dump(box.client)
    calls = len(box.wire.calls)
    for damage in ('raw', 'forged', 'source', 'capture', 'expiry', 'owner_binding', 'limit', 'partial'):
        with subtests.test(damage=damage):
            changes = {}
            if damage == 'raw': changes['rejection_evidence'] = box.rejection.record
            elif damage == 'forged': changes['rejection_evidence'] = object.__new__(rejection.RetainedVisualSchemaRejection)
            elif damage == 'source': box.client.set(rejection.continuity._AUTH_EPOCH, '99')
            elif damage == 'capture': box.client.delete(box.visual_capture['anchor_key'])
            elif damage == 'expiry': box.client.expire(continuation.VISUAL_KEYS[0], 3600)
            elif damage == 'owner_binding':
                changes['attestation'] = {**box.repair_attestation, 'schema_rejection_evidence_sha256': '0' * 64}
            elif damage == 'limit':
                changes['attestation'] = {**box.repair_attestation, 'monthly_additional_cash_limit_micro': 20_000_000}
            elif damage == 'partial': box.client.set(repair.ANCHOR_KEY, 'partial')
            before = _dump(box.client)
            with pytest.raises(SpendBlocked): commission(box, **changes)
            assert _dump(box.client) == before and len(box.wire.calls) == calls
            restore(box.client, baseline)
    cap = commission(box)
    claimed = _dump(box.client)
    assert set(claimed) - set(baseline) == set(repair.ALL_KEYS)
    assert all(box.client.pttl(key) == -1 for key in repair.ALL_KEYS)
    assert {key: value for key, value in claimed.items() if key in baseline} == baseline
    assert cap.receipt['prior_occupied_count'] == 6 and cap.receipt['prior_unknown_count'] == 5
    assert cap.receipt['max_total_attempts'] == 9 and cap.receipt['additional_attempt_limit'] == 3
    assert repair.read_visual_schema_repair(box.client).receipt == cap.receipt
    assert continuation.read_captured_story_continuation(box.client).receipt == box.old_visual_cap.receipt
    assert continuation.historical_keys(cap) == repair.HISTORICAL_KEYS
    assert continuation.controller_keys(cap) == repair.ALL_KEYS
    ledger = journal.RouterReviewJournal(box.client, clock=lambda: NOW, captured_story_continuation=cap)
    with pytest.raises(SpendBlocked, match='schema_correction_required'):
        ledger.reserve(rejection.PURPOSE, prepare(api_key=box.config.abacus_api_key))
    assert _dump(box.client) == claimed
    with pytest.raises(SpendBlocked): commission(box)
    # Exactly one mock corrected send. Native wire omits only the incompatible
    # constraint; the returned object is still checked against its full schema.
    box.wire.status = 200
    payload = envelope()
    payload['choices'][0]['message']['content'] = json.dumps(RESULT)
    box.wire.chunks = [json.dumps(payload).encode()]
    box.wire.headers = {'content-type': 'application/json'}
    with opened(box, cap) as scope:
        binding.bind_captured_story_predecessor(scope, story_evidence=box.qualification)
        assert runtime.generate_retained_router_review([{'type': 'text', 'text': 'Same diagnostic scope'}, image()],
            purpose=rejection.PURPOSE, system_instruction='Complete rubric', json_schema=deepcopy(SCHEMA)) == RESULT
        with pytest.raises(SpendBlocked):
            runtime.generate_retained_router_review([{'type': 'text', 'text': 'Never resend'}],
                purpose=rejection.PURPOSE, system_instruction='Complete rubric', json_schema=deepcopy(SCHEMA))
    sent = json.loads(box.wire.calls[-1].content)
    assert sent['response_format']['json_schema']['name'] == compat.SCHEMA_NAME
    assert compat.schema_for_body(sent) == SCHEMA
    assert len(box.wire.calls) == calls + 1
    assert all(box.client.dump(key) == value for key, value in baseline.items())
    with box.client.pipeline() as pipe:
        _, states, _, _ = continuation._read_control(pipe, authorization=cap)
        assert states['story']['slots'][rejection.PURPOSE]['response'] is not None
        assert states['audio']['slots'] == {}
        continuation._ping(pipe)


def test_lost_commission_ack_never_issues_a_second_controller(eligible):
    box = eligible
    before = _dump(box.client)
    def after(commands, ack):
        if commands == ('SET', 'HSET', 'SET', 'SET', 'HSET', 'SET', 'SET', 'HSET', 'SET'):
            raise RuntimeError('PRIVATE lost response')
        return ack
    with pytest.raises(SpendBlocked) as error: commission(box, Intercept(box.client, after=after))
    assert 'PRIVATE' not in str(error.value)
    assert box.client.exists(*repair.ALL_KEYS) == 9
    assert all(box.client.dump(key) == value for key, value in before.items())
    with pytest.raises(SpendBlocked): commission(box)
    cap = repair.read_visual_schema_repair(box.client)
    assert cap.receipt['qa_approved'] is False and cap.receipt['retry_authorized'] is False


@pytest.fixture
def corrected_visual(eligible, monkeypatch, tmp_path):
    box = eligible
    cap = commission(box)
    import test_preserved_captured_story_recovery as producer
    monkeypatch.setattr(producer, 'commission', lambda actual: cap if actual is box else None)
    box.wire.status = 200
    return producer.produced_captured_visual.__wrapped__(box, monkeypatch, tmp_path)


def test_real_thirty_images_audio_and_render_use_corrected_controller(corrected_visual, monkeypatch, tmp_path):
    box = corrected_visual
    import test_retained_captured_visual_scope as audio
    before = {key: box.client.dump(key) for key in repair.HISTORICAL_KEYS}
    initial = len(box.wire.calls)
    audio.audio_case.__wrapped__(box, monkeypatch)
    audio.completed_captured_audio.__wrapped__(box)
    assert box.visual_evidence.record['component_pass'] is True
    assert box.audio_evidence.component_pass is True
    assert box.audio_evidence.qa_approved is False
    assert box.visual_evidence.commitments['journal_keys'] == list(repair.VISUAL_KEYS)
    assert len(box.wire.calls) == initial + 2
    assert all(box.client.dump(key) == value for key, value in before.items())
    with box.client.pipeline() as pipe:
        _, states, _, _ = continuation._read_control(pipe, authorization=box.visual_cap)
        assert sum(len(state['slots']) for state in states.values()) == 3
        assert all(slot['response'] is not None for state in states.values() for slot in state['slots'].values())
        continuation._ping(pipe)
    assert repair.read_visual_schema_repair(box.client).receipt == box.visual_cap.receipt
    # Exercise the real corrected-controller render boundary. The synthetic
    # provider verdicts are not production QA; FFmpeg and local probes are real.
    import test_retained_render_consumer as rendering
    rendering.complete.__wrapped__(box, monkeypatch, tmp_path)
    anchor = box.rejection.record['commitments']['anchor_key']
    original = box.client.dump(anchor)
    box.client.delete(anchor)
    with pytest.raises(rendering.consumer.RetainedRenderError):
        rendering.inputs(box)
    box.client.restore(anchor, 0, original)
    result = rendering.consumer.render_retained_review(rendering.inputs(box), workdir=box.render_root)
    assert result.record['local_final_gates']['pass'] is True
    assert result.record['render_metrics']['frame_count'] == 900
    assert result.record['qa_approved'] is False
    assert result.record['final_aac_independently_listened'] is False
    rendering.unchanged(box)
