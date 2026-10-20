"""Genuine disposable saved STORY/30-JPEG VISUAL, then actual mock audio ACKs.

Artifact fixtures below write only private FakeS3/FakeRedis, using exact actual
ASR artifacts. They are not a production sink or a fabricated issuer. Independent
negative cases restore only these disposable stores, never a live history.
"""
from contextvars import copy_context
from copy import deepcopy
from dataclasses import replace
import json
from threading import Thread
from types import SimpleNamespace

import pytest
from redis.exceptions import ConnectionError

from app.services import retained_captured_visual_scope as binding
from app.services import retained_captured_story_visual_evidence as visual_reader
from app.services import retained_audio_review_evidence as reader
from app.services import retained_review_captured_story_continuation as continuation
from app.services import abacus_router_audio_review_runtime as runtime
from app.services import abacus_router_audio_review_journal as journal
from app.services import abacus_router_audio_adapter as adapter
from app.services import abacus_router_transport_capture as capture
from app.services import production_connection_continuity as continuity
from app.services import abacus_router_review_artifacts as artifacts
from app.services.production_spend import SpendBlocked
from test_preserved_captured_story_recovery import (
    produced_captured_visual, qualified, captured, source, case, planning_case, real_media,
    prepared, frozen_three, completed_probe, wire, forbid_live_transport, NOW, BUCKET,
)
from test_abacus_router_audio_review_runtime import PROSODY_RESULT, envelope
from test_retained_review_captured_story_continuation import restore
from test_production_connection_continuity import _dump

ASR, PROSODY = adapter.AudioReviewPurpose


@pytest.fixture
def audio_case(produced_captured_visual, monkeypatch):
    box = produced_captured_visual
    box.visual_evidence = visual_reader.read_retained_captured_story_visual_evidence(
        box.client, box.s3, bucket=BUCKET, captured_story_continuation=box.visual_cap,
        story_evidence=box.qualification, audit_pointer=box.audit_pointer)
    box.config.studio_abacus_router_retained_audio_review_enabled = True
    box.config.studio_abacus_router_retained_review_enabled = False
    monkeypatch.setattr(runtime.spending, 'configured_ledger',
                        lambda: SimpleNamespace(client=box.client, clock=lambda: NOW))
    expected = box.source.expected
    words = expected.split()
    audio = box.source.policy['audio']
    step = (audio['decoded_samples'] / audio['decoded_sample_rate'] - .6) / len(words)
    box.asr_result = {'text': expected, 'language': 'tr', 'words': [
        {'word': word, 'start': round(.1 + index * step, 4),
         'end': round(.1 + index * step + step * .9, 4)} for index, word in enumerate(words)]}
    box.prosody_result = deepcopy(PROSODY_RESULT)
    def response(request):
        schema_name = json.loads(request.content)['response_format']['json_schema']['name']
        purpose = {name: purpose.value for purpose in (ASR, PROSODY)
                   for name in (purpose.value, purpose.value + '_enum_v1')}[schema_name]
        state = json.loads(box.client.get(continuation.selected_keys(box.visual_cap, 'audio')[0]))
        assert state['slots'][purpose]['response'] is None
        result = box.asr_result if purpose == ASR.value else box.prosody_result
        box.wire.chunks = [reader._raw(envelope(result))]
        box.wire.headers = {'content-type': 'application/json',
                            'content-length': str(len(box.wire.chunks[0]))}
    box.wire.on_request = response
    return box


def scope_for(box):
    return runtime.retained_audio_router_review_scope(continuity.LEAF_ID,
        captured_story_continuation=box.visual_cap, capture_transport=True)


def _asr(box):
    return runtime.run_blind_asr(box.source.audio_path.read_bytes())


def _prosody(box):
    return runtime.run_prosody(box.source.audio_path.read_bytes(), expected_narration=box.source.expected)


def persist_fixture(box, scope, artifact, *, kind='asr'):
    """Exact test-only private record; caller-supplied receipt is never a gate."""
    state = json.loads(box.client.get(continuation.selected_keys(box.visual_cap, 'audio')[0]))
    policy = state['policy']
    first = scope.artifacts[ASR]
    record = {'version': 1, 'kind': kind, **reader._FLAGS,
        'source': {'source_task_id': continuity.LEAF_ID, 'policy_sha256': reader._hash(policy),
            'continuity_sha256': policy['continuity_sha256'], 'expected_narration': box.source.expected,
            'expected_narration_sha256': policy['expected_narration_sha256'], 'audio': policy['audio']},
        'reviews': {ASR.value: {'reservation': first.reservation, 'settlement': first.settlement,
            'diagnostic': reader._asr_diagnostic(first.observed.result, first.observed.evidence,
                                                box.source.expected)}}}
    prior = None
    if kind == 'final':
        from app.services.retained_router_audio_qa import validate_retained_router_prosody
        diagnostic = validate_retained_router_prosody(artifact.prepared, artifact.response, artifact.observed,
            asr_prepared=first.prepared, asr_response=first.response, asr_observed=first.observed,
            expected_narration=box.source.expected, original_audio=first.prepared.audio)
        record['reviews'][PROSODY.value] = {'reservation': artifact.reservation,
            'settlement': artifact.settlement, 'diagnostic': diagnostic}
        prior = reader._hash(json.loads(box.client.get(reader._artifact_keys(scope.journal)['asr'])))
    reader._validate_record(record, state)
    raw = reader._raw(record)
    digest = reader._sha(raw)
    pointer = {'key': f'recovery/{continuity.LEAF_ID}/included_router_audio_review/{kind}/{digest}.json',
        'sha256': digest, 'size': len(raw), 'content_type': 'application/json'}
    box.s3.put_object(Bucket=BUCKET, Key=pointer['key'], Body=raw, ContentLength=len(raw),
        ContentType='application/json', CacheControl='private, no-store', ACL='private',
        IfNoneMatch='*', Metadata={'sha256': digest})
    anchor = {'version': 1, 'kind': kind, 'source_task_id': continuity.LEAF_ID,
        'original_task_id': continuity.ROOT_ID, 'policy_sha256': reader._hash(policy),
        'continuity_sha256': policy['continuity_sha256'], 'journal_state_sha256': reader._hash(state),
        'pointer': pointer, 'asr_anchor_sha256': prior, **reader._FLAGS}
    key = reader._artifact_keys(scope.journal)[kind]
    assert box.client.set(key, reader._raw(anchor).decode(), nx=True) is True
    return key, anchor


@pytest.fixture
def completed_captured_audio(audio_case):
    """Actual issued VISUAL and both mock audio ACKs plus private final records."""
    box = audio_case
    with scope_for(box) as scope:
        binding.bind_captured_visual_predecessor(scope, visual_evidence=box.visual_evidence)
        box.first_audio = _asr(box)
        box.asr_anchor = persist_fixture(box, scope, box.first_audio)
        binding.bind_persisted_asr_predecessor(scope, box.s3, bucket=BUCKET)
        box.second_audio = _prosody(box)
        box.final_audio_anchor = persist_fixture(box, scope, box.second_audio, kind='final')
    box.audio_evidence = reader.read_retained_audio_review_evidence(box.client, box.s3, bucket=BUCKET,
        captured_story_continuation=box.visual_cap)
    return box


def test_genuine_visual_asr_persistence_prosody_and_historical_reader_progress(audio_case, monkeypatch):
    box = audio_case
    old = {key: box.client.dump(key) for key in continuation.HISTORICAL_KEYS}
    original_visual = box.visual_evidence.record
    initial = len(box.wire.calls)
    with scope_for(box) as scope:
        binding.bind_captured_visual_predecessor(scope, visual_evidence=box.visual_evidence)
        first = _asr(box)
        assert first.settlement['qa_approved'] is False
        key, anchor = persist_fixture(box, scope, first)
        binding.bind_persisted_asr_predecessor(scope, box.s3, bucket=BUCKET)
        reads = len(box.s3.reads)
        # The pre-reserve gate has no S3 dependency; normal capture persistence
        # after actual POST still reads its own encrypted response blob.
        with box.client.pipeline() as pipe:
            binding.require_audio_predecessors(pipe, box.visual_cap, PROSODY.value)
            continuation._ping(pipe)
        assert len(box.s3.reads) == reads
        second = _prosody(box)
        assert second.settlement['qa_approved'] is False
        assert scope.artifacts[ASR] is first and scope.artifacts[PROSODY] is second
        assert box.client.get(key) == reader._raw(anchor).decode()
        persist_fixture(box, scope, second, kind='final')
    assert len(box.wire.calls) == initial + 2
    assert box.visual_evidence.record == original_visual
    assert {key: box.client.dump(key) for key in continuation.HISTORICAL_KEYS} == old
    assert continuation.read_captured_story_continuation(box.client).receipt == box.visual_cap.receipt
    audio_evidence = reader.read_retained_audio_review_evidence(box.client, box.s3, bucket=BUCKET,
        captured_story_continuation=box.visual_cap)
    assert audio_evidence.component_pass and not audio_evidence.qa_approved
    fresh = visual_reader.read_retained_captured_story_visual_evidence(box.client, box.s3,
        bucket=BUCKET, captured_story_continuation=box.visual_cap,
        story_evidence=box.qualification, audit_pointer=box.audit_pointer)
    assert fresh.commitments['journal_state_sha256'] == box.visual_evidence.commitments['journal_state_sha256']
    assert not fresh.qa_approved and not fresh.publish_eligible
    with pytest.raises(SpendBlocked):
        binding.bind_persisted_asr_predecessor(scope, box.s3, bucket=BUCKET)


def test_scope_and_visual_anchor_failures_never_reserve_or_send(audio_case, monkeypatch, subtests):
    box = audio_case
    baseline = _dump(box.client)
    for mode in ('missing', 'dict', 'unissued', 'duplicate', 'wrong_thread', 'closed',
                 'visual_deleted', 'link_expiring', 'source_changed', 'key_changed', 'read_ack_lost'):
        restore(box.client, baseline)
        calls, puts = len(box.wire.calls), len(box.s3.puts)
        with subtests.test(mode=mode), monkeypatch.context() as patch:
            with scope_for(box) as scope:
                if mode in ('dict', 'unissued'):
                    value = box.visual_evidence.record if mode == 'dict' else object.__new__(
                        visual_reader.RetainedCapturedStoryVisualEvidence)
                    with pytest.raises(SpendBlocked):
                        binding.bind_captured_visual_predecessor(scope, visual_evidence=value)
                    assert scope.failed
                elif mode == 'missing':
                    with pytest.raises(SpendBlocked): _asr(box)
                elif mode == 'read_ack_lost':
                    patch.setattr(continuation, '_ping', lambda pipe: (_ for _ in ()).throw(ConnectionError('PRIVATE')))
                    with pytest.raises(SpendBlocked):
                        binding.bind_captured_visual_predecessor(scope, visual_evidence=box.visual_evidence)
                    assert scope.failed
                elif mode == 'wrong_thread':
                    context, errors = copy_context(), []
                    def other():
                        try: context.run(binding.bind_captured_visual_predecessor,
                                         scope, visual_evidence=box.visual_evidence)
                        except SpendBlocked: errors.append(True)
                    thread = Thread(target=other); thread.start(); thread.join(timeout=5)
                    assert not thread.is_alive() and errors == [True]
                else:
                    binding.bind_captured_visual_predecessor(scope, visual_evidence=box.visual_evidence)
                    if mode == 'duplicate':
                        with pytest.raises(SpendBlocked):
                            binding.bind_captured_visual_predecessor(scope, visual_evidence=box.visual_evidence)
                    elif mode == 'closed':
                        scope.closed = True
                        with pytest.raises(SpendBlocked): _asr(box)
                    else:
                        if mode == 'visual_deleted': box.client.delete(box.visual_receipt['anchor_key'])
                        elif mode == 'link_expiring': box.client.expire(box.link_receipt['anchor_key'], 60)
                        elif mode == 'source_changed': box.client.set(continuity._JOB + continuity.LEAF_ID, '{}')
                        elif mode == 'key_changed': patch.setattr(box.config, 'abacus_api_key', 'different-offline-key')
                        with pytest.raises(SpendBlocked): _asr(box)
            assert len(box.wire.calls) == calls and len(box.s3.puts) == puts
            assert json.loads(box.client.get(continuation.AUDIO_KEYS[0]))['slots'] == {}


def test_asr_artifact_persistence_and_pre_send_races_are_terminal(audio_case, monkeypatch, subtests):
    box = audio_case
    baseline, objects = _dump(box.client), deepcopy(box.s3.objects)
    for mode in ('no_anchor', 'unbound_anchor', 'copied_artifact', 'body_mutated', 'anchor_expiry',
                 'blob_changed', 'private_acl', 'asr_semantic_negative', 'anchor_after_bind',
                 'link_after_reserve', 'asr_after_reserve', 'lost_binding_ack', 'source_during_blob'):
        restore(box.client, baseline); box.s3.objects = deepcopy(objects)
        calls = len(box.wire.calls)
        with subtests.test(mode=mode), monkeypatch.context() as patch:
            with scope_for(box) as scope:
                binding.bind_captured_visual_predecessor(scope, visual_evidence=box.visual_evidence)
                if mode == 'asr_semantic_negative':
                    patch.setattr(box, 'asr_result', {'text': 'Yanlış.', 'language': 'tr',
                        'words': [{'word': 'Yanlış.', 'start': .1, 'end': .3}]})
                first = _asr(box)
                if mode == 'asr_semantic_negative':
                    with pytest.raises(SpendBlocked): _prosody(box)
                elif mode == 'no_anchor':
                    with pytest.raises(SpendBlocked): _prosody(box)
                else:
                    key, anchor = persist_fixture(box, scope, first)
                    if mode == 'unbound_anchor':
                        with pytest.raises(SpendBlocked): _prosody(box)
                    else:
                        if mode == 'copied_artifact': scope.artifacts[ASR] = replace(first)
                        elif mode == 'body_mutated': first.response._content += b' '
                        elif mode == 'anchor_expiry': box.client.expire(key, 60)
                        elif mode == 'blob_changed': box.s3.objects[anchor['pointer']['key']] = (b'{}', 'application/json')
                        elif mode == 'private_acl':
                            patch.setattr(box.s3, 'get_object_acl', lambda **kw: {'Grants': []})
                        elif mode == 'lost_binding_ack':
                            patch.setattr(continuation, '_ping', lambda pipe: (_ for _ in ()).throw(ConnectionError('PRIVATE')))
                        elif mode == 'source_during_blob':
                            actual = reader._private_blob
                            def changed(*args):
                                result = actual(*args)
                                box.client.set(continuity._JOB + continuity.LEAF_ID, '{}')
                                return result
                            patch.setattr(reader, '_private_blob', changed)
                        if mode in ('anchor_after_bind', 'link_after_reserve', 'asr_after_reserve'):
                            binding.bind_persisted_asr_predecessor(scope, box.s3, bucket=BUCKET)
                            if mode == 'anchor_after_bind': box.client.delete(key)
                            else:
                                actual = capture._begin
                                def after(*args):
                                    result = actual(*args)
                                    box.client.delete(box.link_receipt['anchor_key'] if mode == 'link_after_reserve' else key)
                                    return result
                                patch.setattr(capture, '_begin', after)
                            with pytest.raises(SpendBlocked): _prosody(box)
                        else:
                            with pytest.raises(SpendBlocked):
                                binding.bind_persisted_asr_predecessor(scope, box.s3, bucket=BUCKET)
                        assert scope.failed
            assert len(box.wire.calls) == calls + 1
            state = json.loads(box.client.get(continuation.AUDIO_KEYS[0]))
            assert state['slots'][ASR.value]['response'] is not None
            if PROSODY.value in state['slots']:
                assert state['slots'][PROSODY.value]['response'] is None
            assert all('PRIVATE' not in str(value) for value in scope.failure.values())
