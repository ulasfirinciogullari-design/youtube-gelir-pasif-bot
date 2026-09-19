"""Real retained preparation with synthetic provider replies, never live calls.

This exercises the original source/voice loader, actual clip probes, six cuts,
thirty sampled JPEGs, selected VISUAL transport and private diagnostic writes.
Synthetic positive replies test the workflow; they establish no media quality.
"""
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import preserved_visual_recovery as recovery
from app.services import abacus_router_review_runtime as runtime
from app.services import abacus_router_review_journal as journal
from app.services import retained_captured_story_scope as binding
from app.services import retained_review_captured_story_continuation as continuation
from app.services import retained_review_completion_plan as completion
from app.services import production_connection_continuity as continuity
from app.services import audio_checkpoint, director, render, voice
from test_retained_review_captured_story_continuation import (
    qualified, captured, source, case, planning_case, real_media, prepared,
    frozen_three, completed_probe, wire, forbid_live_transport, commission, BUCKET, NEW_KEY, NOW,
)
from test_paid_render_recovery import _task_runtime
from test_abacus_router_review_runtime import payload
from test_visual_qc import _review
from test_production_connection_continuity import _dump
from test_retained_captured_story_scope import send_visual

STORY, VISUAL = journal.PURPOSES


def arguments(box):
    original, fingerprint = recovery._state(continuity.LEAF_ID, box.client)
    options = recovery._options(original)
    shooting = recovery._immutable_shooting_package(
        recovery._manifests(box.s3, original)[0]['package'], {}, options)
    candidate = {'package': deepcopy(box.source.metadata['package']),
                 'audio_sha256': box.source.policy['audio']['sha256']}
    return [box.qualification, original, fingerprint, candidate, shooting, options, {}]


def test_saved_story_core_is_rederived_without_director_approval_or_any_request(qualified, monkeypatch, subtests):
    box = qualified
    cap = commission(box)
    before = _dump(box.client)
    calls, puts = len(box.wire.calls), len(box.s3.puts)
    forbidden = Mock(side_effect=AssertionError('No fresh STORY or fabricated director approval'))
    monkeypatch.setattr(director, 'revalidate_immutable_short_story', forbidden)
    monkeypatch.setattr(director, 'short_story_package_is_approved', forbidden)
    with runtime.retained_router_review_scope(continuity.LEAF_ID,
            captured_story_continuation=cap, capture_transport=True) as scope:
        binding.bind_captured_story_predecessor(scope, story_evidence=box.qualification)
        args = arguments(box)
        result = recovery._captured_story_package(*args)
        assert result == box.contract['candidate']
        assert 'stock_scene_qc' not in result and 'short_story_qc' not in result
        assert scope.evidence == {} and scope._artifacts == {} and scope.attempted == set()
        for mode in ('unsealed', 'fingerprint', 'spec', 'journal', 'metadata', 'audio',
                     'options', 'narration', 'prompt', 'source_evidence'):
            with subtests.test(mode=mode):
                changed = [args[0], *deepcopy(args[1:])]
                if mode == 'unsealed': changed[0] = args[0].record
                elif mode == 'fingerprint': changed[2] = '0' * 64
                elif mode == 'spec': changed[1]['spec']['topic'] += ' changed'
                elif mode == 'journal': changed[1]['generated_asset_candidates']['entries'][0]['raw_sha256'] = '0' * 64
                elif mode == 'metadata': changed[1]['audio_candidate_checkpoint']['metadata_sha256'] = '0' * 64
                elif mode == 'audio': changed[3]['audio_sha256'] = '0' * 64
                elif mode == 'options': changed[5]['quality_threshold'] = 1
                elif mode == 'narration': changed[3]['package']['scenes'][0]['narration'] += ' changed'
                elif mode == 'prompt': changed[4]['scenes'][0]['ai_prompt'] += ' changed'
                elif mode == 'source_evidence': changed[4]['sources'][0]['evidence'] += ' changed'
                with pytest.raises((ValueError, RuntimeError)):
                    recovery._captured_story_package(*changed)
        with pytest.raises(recovery.PreservedVisualRecoveryError):
            recovery.prepare_preserved_visual_recovery(continuity.LEAF_ID, box.source.work,
                                                       preserve_exact_cuts=True)
    forbidden.assert_not_called()
    assert _dump(box.client) == before and len(box.wire.calls) == calls and len(box.s3.puts) == puts


def test_wrapper_retains_actual_capture_receipt_after_observer_failure(qualified, monkeypatch):
    box = qualified
    cap = commission(box)
    old = {key: box.client.dump(key) for key in continuation.HISTORICAL_KEYS}
    # Isolate wrapper lifetime here; the separate producer below tests all
    # actual cuts/sampling. This sends only through disposable MockHTTPX.
    monkeypatch.setattr(recovery, 'prepare_preserved_visual_recovery', lambda *a, **kw: send_visual())
    response = payload()
    encoded = json.dumps(response).encode()
    box.wire.chunks = [encoded]
    box.wire.headers = {'content-type': 'application/json', 'content-length': str(len(encoded)),
                        'transfer-encoding': 'chunked'}
    with pytest.raises(RuntimeError) as caught:
        recovery.prepare_captured_story_visual_recovery(continuity.LEAF_ID, box.source.work,
            captured_story_continuation=cap, story_evidence=box.qualification)
    receipt = caught.value.transport_captures[VISUAL]
    assert receipt['capture_acknowledged'] is True
    assert box.client.get(receipt['anchor_key']) is not None
    assert caught.value._transport_capture_fallbacks == {}
    assert json.loads(box.client.get(continuation.VISUAL_KEYS[0]))['slots'][VISUAL]['response'] is None
    assert {key: box.client.dump(key) for key in continuation.HISTORICAL_KEYS} == old
    assert len(box.wire.calls) == 3 and runtime.retained_router_review_active() is False


@pytest.fixture
def produced_captured_visual(qualified, monkeypatch, tmp_path):
    box = qualified
    box.visual_cap = commission(box)
    box.continuation_cap = box.visual_cap
    box.original_history = {key: box.client.dump(key) for key in continuation.HISTORICAL_KEYS}
    monkeypatch.setattr(recovery.studio_state, '_client', lambda: box.client)
    monkeypatch.setattr(audio_checkpoint, '_AUDIO_TEMP_ROOT', tmp_path)
    # Use only the real extracted normalizer/clip validator. Replace its test
    # media placeholders with the actual FFprobe functions; no quality shim.
    tasks = _task_runtime()
    tasks._validate_recovered_generated_clip.__globals__.update(
        media_duration=render.media_duration, video_frame_count=render.video_frame_count)
    monkeypatch.setattr(recovery, '_runtime', lambda: (tasks, director, voice))
    forbidden = Mock(side_effect=AssertionError('No new STORY or ordinary reusable checkpoint'))
    monkeypatch.setattr(director, 'revalidate_immutable_short_story', forbidden)
    monkeypatch.setattr(director, 'short_story_package_is_approved', forbidden)
    for name in ('_recovery_package_sha256', '_validated_recovered_voice', '_validated_recovered_generated_media'):
        setattr(tasks, name, forbidden)
    original_put = type(box.s3).put_object.__get__(box.s3)
    def put_object(**kwargs):
        # The pre-existing audit writer streams its body to a private bucket;
        # support the same transport in the private in-memory S3 fixture.
        if hasattr(kwargs['Body'], 'read'):
            kwargs = {**kwargs, 'Body': kwargs['Body'].read()}
        kwargs.setdefault('ACL', 'private')
        return original_put(**kwargs)
    monkeypatch.setattr(box.s3, 'put_object', put_object)
    def reply(request):
        body = json.loads(request.content)
        assert any(part['type'] == 'image_url' for part in body['messages'][1]['content'])
        assert sum(part['type'] == 'image_url' for part in body['messages'][1]['content']) == 30
        response = payload()
        response['choices'][0]['native_finish_reason'] = 'STOP'
        response['choices'][0]['message']['content'] = json.dumps({'reviews': [
            _review(index, recurring_identity_continuity_applicable=True,
                    recurring_identity_continuity_matches=True) for index in range(6)]})
        box.wire.chunks = [recovery.json.dumps(response, separators=(',', ':')).encode()]
        box.wire.headers = {'content-type': 'application/json',
                            'content-length': str(len(box.wire.chunks[0]))}
    box.wire.on_request = reply
    work = tmp_path / 'youtube_factory' / '66666666-6666-4666-8666-666666666666_attempt_0'
    work.mkdir(parents=True)
    try:
        box.preparation = recovery.prepare_captured_story_visual_recovery(continuity.LEAF_ID, work,
            captured_story_continuation=box.visual_cap, story_evidence=box.qualification)
    except Exception as error:
        # Production exceptions intentionally redact details. Report only the
        # local exception types/line numbers when this disposable fixture fails.
        chain = []
        while error is not None:
            frames = []
            frame = error.__traceback__
            while frame is not None:
                frames.append((frame.tb_frame.f_code.co_name, frame.tb_lineno))
                frame = frame.tb_next
            chain.append((type(error).__name__, frames))
            error = error.__context__
        pytest.fail('Synthetic preparation failed: ' + repr(chain))
    box.audit_pointer = box.preparation['audit_pointer']
    box.audit = recovery._read_record(box.s3, box.audit_pointer, 'audit')
    box.visual_receipt = box.audit['included_router_artifact_anchors'][VISUAL]
    box.link_receipt = box.audit['retained_sampled_input_link']
    box.cut_receipt = box.audit['retained_cut_evidence']
    box.preparation_work = work
    box.before_read = _dump(box.client)
    forbidden.assert_not_called()
    return box


def test_actual_new_entry_samples_exact_retained_media_and_returns_only_diagnostic(produced_captured_visual):
    box = produced_captured_visual
    assert len(box.wire.calls) == 3  # Previous tiny probe + unknown STORY, then one VISUAL.
    assert box.preparation['kind'] == 'captured_story_visual_preparation'
    for name in ('qa_approved', 'publish_eligible', 'claim_authorized', 'render_authorized', 'resume_authorized', 'reusable'):
        assert box.preparation[name] is False
    assert box.preparation['new_paid_create_requests'] == box.preparation['new_tts_requests'] == 0
    assert box.audit['package'] == box.contract['candidate']
    assert set(box.audit['included_router_review']['observations']) == {VISUAL}
    assert set(box.audit['included_router_artifact_anchors']) == {VISUAL}
    assert box.visual_receipt['version'] == box.link_receipt['version'] == 2
    assert box.audit['captured_story_evidence'] == box.qualification.record
    assert {key: box.client.dump(key) for key in continuation.HISTORICAL_KEYS} == box.original_history
    old = json.loads(box.client.get(completion.STORY_KEYS[0]))
    assert old['slots'][STORY]['response'] is None
    assert not any('/preserved_visual/prepared-' in key for key in box.s3.objects)
    with pytest.raises(recovery.PreservedVisualRecoveryError):
        recovery.publish_preserved_visual_recovery(box.preparation)
    assert _dump(box.client) == box.before_read
