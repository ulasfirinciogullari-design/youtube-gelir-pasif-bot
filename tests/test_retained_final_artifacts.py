"""Private staging from genuine synthetic components and an actual local render."""
from copy import copy, deepcopy
import hashlib
import json
from threading import Thread

import pytest
from redis.exceptions import ConnectionError

from app.services import retained_final_artifacts as staging
from app.services import retained_render_consumer as consumer
from app.services import retained_cut_evidence as cuts
from app.services import production_connection_continuity as continuity
from test_retained_render_consumer import (
    complete, completed_captured_audio, audio_case, produced_captured_visual, qualified,
    captured, source, case, planning_case, real_media, prepared, frozen_three,
    completed_probe, wire, forbid_live_transport, inputs, BUCKET,
)
from test_retained_review_captured_story_continuation import restore
from test_production_connection_continuity import _dump


@pytest.fixture
def rendered(complete, monkeypatch):
    box = complete
    box.final_render = consumer.render_retained_review(inputs(box), workdir=box.render_root)
    monkeypatch.setattr(box.s3, 'put_object', type(box.s3).put_object.__get__(box.s3))
    box.put_count = len(box.s3.puts)
    return box


def stage(box):
    return staging.stage_retained_final(box.client, box.s3, bucket=BUCKET,
        captured_story_continuation=box.visual_cap, story_evidence=box.qualification,
        audit_pointer=box.audit_pointer, rendered=box.final_render, workdir=box.render_root)


def safe_failure(call):
    try:
        return call()
    except staging.RetainedFinalArtifactsError as error:
        locations = []
        current = error
        for _ in range(8):
            if current is None:
                break
            trace = current.__traceback__
            while trace is not None:
                locations.append((trace.tb_frame.f_code.co_name, trace.tb_lineno))
                trace = trace.tb_next
            current = current.__context__
        pytest.fail(f'Actual staging failed at {locations}', pytrace=False)


def test_unissued_render_is_rejected_before_io(tmp_path):
    class NoIO:
        def __getattr__(self, name):
            raise AssertionError('No I/O before issuer check')
    for value in (None, True, {}, object.__new__(consumer.RetainedLocalRender)):
        with pytest.raises(staging.RetainedFinalArtifactsError):
            staging.stage_retained_final(NoIO(), NoIO(), bucket=BUCKET,
                captured_story_continuation={}, story_evidence={}, audit_pointer={},
                rendered=value, workdir=tmp_path)
    with pytest.raises(TypeError):
        staging.PreparedRetainedFinal()


def test_actual_private_staging_preserves_source_and_binds_every_capture(rendered, monkeypatch, subtests):
    box = rendered
    value = safe_failure(lambda: stage(box))
    record = value.record
    assert type(value) is staging.PreparedRetainedFinal
    assert all(record[name] is expected for name, expected in staging._FLAGS.items())
    assert record['private_storage_verified'] is True
    assert set(record['artifacts']) == {'video', 'captions', 'thumbnail', 'metadata'}
    assert len(box.s3.puts) == box.put_count + 4
    assert record['artifacts']['video']['sha256'] == box.final_render.record['final']['sha256']
    metadata = json.loads(cuts._read_private(box.s3, BUCKET, record['artifacts']['metadata']))
    assert metadata['local_render'] == box.final_render.record
    assert metadata['thumbnail']['origin'] == 'retained_reviewed_final_frame'
    assert metadata['thumbnail']['source_sha256'] == record['artifacts']['video']['sha256']
    assert metadata['local_render']['final_aac_independently_listened'] is False
    assert all(value not in json.dumps(metadata) for value in
               (box.config.abacus_api_key, box.config.app_encryption_key))
    snapshot = record['source_snapshot']
    assert set(staging.continuation.ALL_KEYS) <= snapshot['records'].keys()
    assert set(staging.continuation.HISTORICAL_KEYS) <= snapshot['records'].keys()
    assert len(snapshot['transport_capture_objects']) == 6
    assert sum(value is not None for value in snapshot['transport_capture_objects'].values()) == 3
    assert snapshot['records'][continuity._PAID_CAP + continuity.LEAF_ID]['value_sha256'] == staging._hash(
        {'cap': '6', 'used': '6'})
    before = _dump(box.client)
    assert before == box.original_records
    state = staging._checked(value)
    # Revalidation binds stored records after staging, before a later admission.
    for key in snapshot['transport_capture_objects']:
        with subtests.test(capture=key):
            box.client.delete(key)
            with pytest.raises(Exception), box.client.pipeline() as pipe:
                staging._snapshot(pipe, state)
            restore(box.client, before)
    for key in (continuity._CREDENTIAL + continuity.CHANNEL_ID, continuity._PROFILE + continuity.CHANNEL_ID,
                continuity._JOB + continuity.LEAF_ID):
        with subtests.test(source=key):
            box.client.set(key, 'changed')
            with pytest.raises(Exception), box.client.pipeline() as pipe:
                staging._snapshot(pipe, state)
            restore(box.client, before)
    record['publish_eligible'] = True
    assert value.record['publish_eligible'] is False
    with pytest.raises(TypeError):
        copy(value)
    wrong_thread = []
    def other():
        with pytest.raises(staging.RetainedFinalArtifactsError):
            _ = value.record
        wrong_thread.append(True)
    thread = Thread(target=other); thread.start(); thread.join(timeout=5)
    assert not thread.is_alive() and wrong_thread == [True]
    with pytest.raises(staging.RetainedFinalArtifactsError):
        stage(box)
    assert len(box.s3.puts) == box.put_count + 4
    assert _dump(box.client) == before and len(box.wire.calls) == box.before_calls
    box.no_new_work.assert_not_called()


@pytest.mark.parametrize('fault', ['put_lost', 'source_race', 'read_ack_lost'])
def test_uncertain_staging_never_issues_capability_or_retries(rendered, monkeypatch, fault):
    box = rendered
    original = box.s3.put_object
    def put(**kwargs):
        result = original(**kwargs)
        if fault == 'put_lost':
            raise ConnectionError('PRIVATE unavailable acknowledgement')
        if fault == 'source_race':
            box.client.set(continuity._AUTH_EPOCH, '99')
        return result
    monkeypatch.setattr(box.s3, 'put_object', put)
    if fault == 'read_ack_lost':
        original_ack = staging.artifacts._ack_read
        def ack(pipe):
            if len(box.s3.puts) > box.put_count:
                raise ConnectionError('PRIVATE final acknowledgement')
            return original_ack(pipe)
        monkeypatch.setattr(staging.artifacts, '_ack_read', ack)
    with pytest.raises(staging.RetainedFinalArtifactsError) as caught:
        stage(box)
    assert 'PRIVATE' not in str(caught.value)
    assert staging._ATTEMPTED[box.final_render] == 'attempted'
    assert len(box.s3.puts) == box.put_count + (1 if fault == 'put_lost' else 4)
    saved = deepcopy(box.s3.objects)
    with pytest.raises(staging.RetainedFinalArtifactsError):
        stage(box)
    assert box.s3.objects == saved and len(box.wire.calls) == box.before_calls
    if fault != 'source_race':
        assert _dump(box.client) == box.original_records
    box.no_new_work.assert_not_called()
