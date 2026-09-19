"""Actual staged synthetic final → one durable child, without a queue or API."""
from copy import copy
import json

import pytest

from app.services import retained_production_admission as admission
from app.services import retained_final_artifacts as staging
from app.services import production_connection_continuity as continuity
from test_retained_final_artifacts import rendered, stage
from test_retained_render_consumer import (
    complete, completed_captured_audio, audio_case, produced_captured_visual, qualified,
    captured, source, case, planning_case, real_media, prepared, frozen_three,
    completed_probe, wire, forbid_live_transport,
)
from test_production_connection_continuity import _dump


def test_raw_or_unissued_preparation_cannot_read_or_claim():
    class NoIO:
        def __getattr__(self, name):
            raise AssertionError('No I/O before issuer admission')
    for value in (None, True, {}, object.__new__(staging.PreparedRetainedFinal)):
        with pytest.raises(admission.RetainedAdmissionError):
            admission.reserve_retained_child(NoIO(), value)
    with pytest.raises(TypeError):
        admission.RetainedChildDispatchPermit()


def test_actual_private_final_claims_only_one_child_without_new_budget(rendered, subtests):
    box = rendered
    prepared = stage(box)
    before = _dump(box.client)
    sends, objects = len(box.wire.calls), dict(box.s3.objects)
    try:
        permit = admission.reserve_retained_child(box.client, prepared)
    except admission.RetainedAdmissionError as error:
        current, locations = error, []
        for _ in range(8):
            if current is None:
                break
            trace = current.__traceback__
            while trace is not None:
                locations.append((trace.tb_frame.f_code.co_name, trace.tb_lineno))
                trace = trace.tb_next
            current = current.__context__
        pytest.fail(f'Actual retained admission failed at {locations}', pytrace=False)
    receipt = permit.receipt
    child = receipt['child_id']
    assert receipt['dispatch_acknowledged'] is receipt['publication_authorized'] is False
    assert receipt['resume_authorized'] is False
    with box.client.pipeline() as pipe:
        manifest, journal = admission._read_claim(pipe)
        admission._read_ack(pipe)
    job = json.loads(box.client.get(continuity._JOB + child))
    assert job['state'] == 'PENDING' and job['result'] is None
    assert job['spec']['production_connection_id'] == manifest['prepared_final']['source_snapshot']['source']['old_connection_id']
    assert job['spec']['current_delivery_connection_id'] == manifest['prepared_final']['source_snapshot']['source']['current_connection_id']
    assert job['spec']['workflow'] == 'retained_final'
    assert box.client.exists(continuity._PAID_CAP + child, continuity._EXECUTION + child) == 0
    assert box.client.hgetall(continuity._PAID_CAP + continuity.LEAF_ID) == {'cap': '6', 'used': '6'}
    after = _dump(box.client)
    assert {key: value for key, value in after.items() if key in before and
            key != continuity._JOB + continuity.LEAF_ID} == {
        key: value for key, value in before.items() if key != continuity._JOB + continuity.LEAF_ID}
    created = set(after) - set(before)
    assert created == {*admission.ROOT_KEYS, admission.CHILD_PREFIX + child,
        continuity._JOB + child, continuity._CHILD_CLAIM + child,
        continuity._DISPATCH + continuity.LEAF_ID}
    assert all(box.client.pttl(key) == -1 for key in created)
    # Ordinary preclaim source readers intentionally cease to apply after this
    # exact claim delta; the new read path verifies the committed projection.
    with pytest.raises(continuity.ConnectionContinuityError), box.client.pipeline() as pipe:
        continuity._derive(pipe, job['spec']['production_profile_revision'])
    with pytest.raises(admission.RetainedAdmissionError):
        admission.reserve_retained_child(box.client, prepared)
    with pytest.raises(TypeError):
        copy(permit)
    assert _dump(box.client) == after and len(box.wire.calls) == sends and box.s3.objects == objects
    assert journal['phase'] == 'claimed'
    # Changing only durability must invalidate immutable evidence even while
    # the captured bytes and the source hashes remain identical.
    immutable = manifest['prepared_final']['source_snapshot']['permanent_record_keys']
    assert set(staging.continuation.ALL_KEYS) <= set(immutable)
    for key in (staging.continuation.ALL_KEYS[0],
                next(iter(manifest['prepared_final']['source_snapshot']['transport_capture_objects']))):
        with subtests.test(expiring_evidence=key):
            assert box.client.expire(key, 3600)
            with pytest.raises(admission.RetainedAdmissionError), box.client.pipeline() as pipe:
                admission._read_claim(pipe)
            assert box.client.persist(key)
    with box.client.pipeline() as pipe:
        admission._read_claim(pipe)
        admission._read_ack(pipe)
    assert _dump(box.client) == after
    box.no_new_work.assert_not_called()
