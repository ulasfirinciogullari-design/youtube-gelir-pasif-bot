"""Read genuine disposable completion records without renewing or sending."""
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from app.services import retained_review_completion_plan as plan
from app.services import retained_audio_review_evidence as reader
from app.services import abacus_router_audio_review_journal as journal
from app.services import abacus_router_audio_adapter as adapter
from app.services import production_connection_continuity as continuity
import test_retained_review_completion_plan as completion
from test_retained_review_completion_plan import (
    completed_probe, frozen_three, wire, prepared, case, source, no_transport,
    forbid_live_transport,
)
from test_retained_audio_review_evidence import (
    ReadOnlyS3, review, save_artifact, ENDPOINT, FLAGS, digest, mp3, prosody,
    ASR, PROSODY,
)
from test_production_connection_continuity import _dump


@pytest.fixture
def complete_plan_audio(completed_probe, monkeypatch):
    original = completed_probe
    cap = completion.commission(original)
    first, ledger = completion.selected(original, cap)
    completion.advance_story(original, first)
    monkeypatch.setattr(reader.storage, 'settings',
                        SimpleNamespace(endpoint=ENDPOINT, bucket='private-fixture'))
    source = SimpleNamespace(**vars(original.source))
    source.policy = deepcopy(original.completion_policies['audio'])
    source.prepared = adapter.prepare_blind_asr_request(
        mp3(), api_key=completion.history.NEW_KEY)
    asr, received = review(ledger, source.prepared, source.asr_result, source)
    first_record = {'version': 1, 'kind': 'asr', 'source': {
        'source_task_id': continuity.LEAF_ID, 'policy_sha256': digest(source.policy),
        'continuity_sha256': source.policy['continuity_sha256'],
        'expected_narration': source.expected,
        'expected_narration_sha256': source.policy['expected_narration_sha256'],
        'audio': source.prepared.audio}, 'reviews': {ASR.value: asr}, **FLAGS}
    result = {'pass': True, 'summary': 'Clear synthetic delivery.', 'scores': {
        'pronunciation': 80, 'naturalness': 80, 'pacing': 80, 'sentence_flow': 80,
        'emphasis': 80, 'roboticness': 20}, 'issues': []}
    second, _ = review(ledger, prosody(source, api_key=completion.history.NEW_KEY),
                       result, source, asr_response=received)
    final = {**deepcopy(first_record), 'kind': 'final', 'reviews': {
        **deepcopy(first_record['reviews']), PROSODY.value: second}}
    box = SimpleNamespace(case=SimpleNamespace(client=original.client),
        source=source, journal=ledger, s3=ReadOnlyS3(), cap=cap, original=original,
        records={'asr': first_record, 'final': final}, anchors={})
    pointer = json.loads(original.client.get(
        continuity._JOB + continuity.LEAF_ID))['audio_candidate_checkpoint']
    box.s3.objects[pointer['metadata_key']] = source.metadata_bytes
    box.s3.objects[pointer['audio_key']] = mp3()
    save_artifact(box, 'asr')
    save_artifact(box, 'final')
    return box


def read(box, **options):
    return reader.read_retained_audio_review_evidence(
        box.case.client, box.s3, bucket='private-fixture',
        **{'completion_plan': box.cap, **options})


def test_actual_completion_audio_is_read_only_and_cannot_approve_story_or_render(
        complete_plan_audio, monkeypatch):
    box = complete_plan_audio
    blocked = Mock(side_effect=AssertionError('No send, observation or renewal.'))
    for name in ('reserve', 'settle', 'commission', '_fresh', '_now', '_commit'):
        monkeypatch.setattr(journal.RouterAudioReviewJournal, name, blocked)
    for name in ('prepare_blind_asr_request', 'prepare_audio_prosody_request',
                 'observe_audio_router_response'):
        monkeypatch.setattr(adapter, name, blocked)
    for name in ('commission_retained_completion_plan', 'verify_scope_completion_plan'):
        monkeypatch.setattr(plan, name, blocked)
    for name in ('Client', 'AsyncClient', 'get', 'post', 'request'):
        monkeypatch.setattr(httpx, name, blocked)
    before = _dump(box.case.client)
    client = completion.Intercept(box.case.client)
    value = reader.read_retained_audio_review_evidence(
        client, box.s3, bucket='private-fixture', completion_plan=box.cap)
    assert value.component_pass and value.diagnostic_only
    assert not any((value.qa_approved, value.publish_eligible, value.full_qa_complete,
                    value.edit_duration_qa_complete))
    assert value.diagnostics['source']['policy_sha256'] == digest(box.source.policy)
    assert client.executions == [('PING',)]
    assert _dump(box.case.client) == before
    assert len(box.s3.gets) == 4 and all(body.closed for body in box.s3.bodies)
    completion.preserve(box.original)
    blocked.assert_not_called()


@pytest.mark.parametrize('selection', ['default', 'old_successor', 'both', 'mapping'])
def test_completed_audio_cannot_leak_to_another_selection(complete_plan_audio, selection):
    box = complete_plan_audio
    options = {'default': {'completion_plan': None},
        'old_successor': {'completion_plan': None, 'successor': box.original.cap},
        'both': {'successor': box.original.cap},
        'mapping': {'completion_plan': box.cap.receipt}}[selection]
    before = _dump(box.case.client)
    with pytest.raises(reader.RetainedAudioEvidenceError):
        read(box, **options)
    assert not box.s3.gets and _dump(box.case.client) == before


@pytest.mark.parametrize('key', [
    plan.HISTORICAL_KEYS[0], plan.HISTORICAL_KEYS[8], plan.HISTORICAL_KEYS[-1],
    plan.STORY_KEYS[-1], plan.ANCHOR_KEY,
])
def test_reader_requires_completed_probe_and_both_generations_of_history(
        complete_plan_audio, key):
    box = complete_plan_audio
    box.case.client.pexpire(key, 60_000)
    before = _dump(box.case.client)
    with pytest.raises(reader.RetainedAudioEvidenceError):
        read(box)
    assert not box.s3.gets and _dump(box.case.client) == before


@pytest.mark.parametrize('kind', ['asr', 'final'])
def test_old_artifact_locations_cannot_replace_completion_anchors(complete_plan_audio, kind):
    box = complete_plan_audio
    selected = reader._artifact_keys(box.journal)[kind]
    old_prefix = plan.predecessor.AUDIO_KEYS[0].rsplit(':', 1)[0]
    box.case.client.set(old_prefix + ':' + kind + '_artifact', box.case.client.get(selected))
    box.case.client.delete(selected)
    before = _dump(box.case.client)
    with pytest.raises(reader.RetainedAudioEvidenceError):
        read(box)
    assert not box.s3.gets and _dump(box.case.client) == before


def test_changed_oauth_cannot_reuse_the_completion_audio(complete_plan_audio):
    box = complete_plan_audio
    box.case.client.incr(continuity._AUTH_EPOCH)
    before = _dump(box.case.client)
    with pytest.raises(reader.RetainedAudioEvidenceError):
        read(box)
    assert not box.s3.gets and _dump(box.case.client) == before
