"""Actual disposable VISUAL ACKs carry a distinct captured-STORY predecessor.

These small transport fixtures prove persistence/binding, not image semantics.
The full recovery producer separately performs real cuts and sampled JPEGs.
"""
from copy import deepcopy
import base64
import json

import pytest
from redis.exceptions import ConnectionError

from app.services import abacus_router_review_artifacts as artifacts
from app.services import abacus_router_review_runtime as runtime
from app.services import retained_captured_story_scope as binding
from app.services import retained_review_captured_story_continuation as continuation
from app.services import retained_sampled_input_linkage as sampled
from app.services import retained_transport_story_evidence as story_reader
from app.services import preserved_visual_recovery as recovery
from app.services import production_connection_continuity as continuity
from app.services.production_spend import SpendBlocked
from test_retained_review_captured_story_continuation import (
    qualified, captured, source, case, planning_case, real_media, prepared, frozen_three,
    completed_probe, wire, forbid_live_transport, commission, Intercept, BUCKET,
)
from test_retained_captured_story_scope import opened, send_visual
from test_abacus_router_review_runtime import payload
from test_production_connection_continuity import _dump
from test_preserved_captured_story_recovery import produced_captured_visual

STORY, VISUAL = runtime.PURPOSES


@pytest.fixture
def visual_box(qualified, monkeypatch, request):
    box = qualified
    box.continuation_cap = commission(box)
    mode = getattr(request, 'param', None)
    box.armed = False
    box.anchor_written = False
    def after(commands, ack):
        if box.armed and commands == ('SET',):
            box.anchor_written = True
            if mode == 'lost_anchor_ack':
                raise ConnectionError('PRIVATE lost backend response')
            if mode == 'malformed_anchor_ack':
                return [1]
        if box.armed and box.anchor_written and commands == ('PING',) and mode == 'lost_readback_ack':
            raise ConnectionError('PRIVATE lost readback')
        return ack
    box.instrumented = Intercept(box.client, after=after)
    foundation = runtime.spending.configured_ledger.return_value
    monkeypatch.setattr(foundation, 'client', box.instrumented)
    reply = payload()
    reply['choices'][0]['message']['content'] = '{"ok":true}'
    box.wire.headers = {'content-type': 'application/json'}
    box.wire.chunks = [artifacts._raw(reply)]
    with opened(box, box.continuation_cap) as scope:
        binding.bind_captured_story_predecessor(scope, story_evidence=box.qualification)
        assert send_visual() == {'ok': True}
        box.scope = scope
        box.artifact = runtime.retained_router_review_artifacts()[VISUAL]
        box.sink = artifacts.RetainedRouterReviewArtifactSink(box.s3, bucket=BUCKET)
        box.before_persist = _dump(box.client)
        box.put_count = len(box.s3.puts)
        box.armed = True
        yield box


def persist(box, **kwargs):
    return box.sink.persist(box.artifact, **{'captured_story_predecessor': box.qualification, **kwargs})


def preserve_old(box):
    assert {key: raw for key, raw in _dump(box.client).items() if key in box.frozen} == box.frozen
    assert len(box.wire.calls) == 3  # Historical probe, captured STORY, new tiny VISUAL.
    with box.client.pipeline() as pipe:
        _, states, _, _ = continuation._read_control(pipe)
        assert set(states['story']['slots']) == {VISUAL}
        assert states['audio']['slots'] == {}
        continuation._ping(pipe)


def test_actual_visual_capture_persists_v2_tag_without_any_new_story_observation(visual_box):
    box = visual_box
    tag = artifacts.captured_story_predecessor_tag(box.scope, box.qualification)
    assert tag == {'version': 1, 'kind': 'captured_transport_story',
        'continuation_manifest_sha256': box.continuation_cap.receipt['manifest_sha256'],
        'evidence': box.qualification.record}
    stored = persist(box)
    receipt = stored.receipt
    anchor = json.loads(box.client.get(receipt['anchor_key']))
    manifest = json.loads(box.s3.objects[receipt['manifest']['key']][0])
    for value in (receipt, anchor, manifest):
        assert value['version'] == 2 and value['story_predecessor'] == tag
        assert value['qa_approved'] is value['publish_eligible'] is value['full_qa_complete'] is False
    assert anchor['prior_story_anchor_sha256'] is manifest['prior_story_anchor_sha256'] is None
    assert receipt['anchor_key'] == artifacts._anchor_keys(continuation.VISUAL_KEYS)[VISUAL]
    assert box.client.exists(artifacts._anchor_keys(continuation.VISUAL_KEYS)[STORY]) == 0
    assert set(runtime.retained_router_review_artifacts()) == {VISUAL}
    assert len(box.s3.puts) - box.put_count == 5
    assert box.client.pttl(receipt['anchor_key']) == -1
    assert artifacts._hash(anchor) == receipt['anchor_sha256']
    with box.client.pipeline() as pipe:
        pipe.watch(receipt['anchor_key'])
        assert box.sink._read_anchor(pipe, VISUAL) == anchor
        artifacts._ack_read(pipe)
    preserve_old(box)


@pytest.mark.parametrize('damage', ['missing', 'detached', 'other_sealed', 'mixed_prior', 'fake_story_anchor'])
def test_wrong_predecessor_cannot_authorize_artifact_writes(visual_box, damage):
    box = visual_box
    options = {}
    if damage == 'missing': options['captured_story_predecessor'] = None
    elif damage == 'detached': options['captured_story_predecessor'] = box.qualification.record
    elif damage == 'other_sealed':
        other = story_reader.read_retained_transport_story_evidence(box.client, box.s3,
            bucket=BUCKET, completion_plan=box.cap)
        assert other.record == box.qualification.record and other is not box.qualification
        options['captured_story_predecessor'] = other
    elif damage == 'mixed_prior': options['prior_story_anchor'] = object.__new__(artifacts.PersistedRouterReviewArtifact)
    elif damage == 'fake_story_anchor':
        box.client.set(artifacts._anchor_keys(continuation.VISUAL_KEYS)[STORY], '{}')
    before = _dump(box.client)
    with pytest.raises(artifacts.RouterReviewArtifactError): persist(box, **options)
    assert len(box.s3.puts) == box.put_count and _dump(box.client) == before
    assert box.scope.failed is True
    with pytest.raises(artifacts.RouterReviewArtifactError): persist(box)
    assert len(box.s3.puts) == box.put_count
    preserve_old(box)


@pytest.mark.parametrize('visual_box', ['lost_anchor_ack', 'malformed_anchor_ack', 'lost_readback_ack'], indirect=True)
def test_actual_anchor_ack_uncertainty_is_terminal_without_second_storage_attempt(visual_box):
    box = visual_box
    with pytest.raises(artifacts.RouterReviewArtifactError) as caught: persist(box)
    assert 'PRIVATE' not in str(caught.value)
    anchor_key = artifacts._anchor_keys(continuation.VISUAL_KEYS)[VISUAL]
    assert box.client.pttl(anchor_key) == -1 and len(box.s3.puts) - box.put_count == 5
    assert box.scope.failed is True
    count = len(box.s3.puts)
    with pytest.raises(artifacts.RouterReviewArtifactError): persist(box)
    assert len(box.s3.puts) == count
    preserve_old(box)


def test_capture_source_change_during_put_leaves_no_artifact_anchor_or_reusable_scope(visual_box):
    box = visual_box
    old_anchor = box.qualification.commitments['capture_anchor_key']
    def change(*args): box.client.delete(old_anchor)
    box.s3.after_put = change
    with pytest.raises(artifacts.RouterReviewArtifactError): persist(box)
    assert box.scope.failed is True and len(box.s3.puts) > box.put_count
    assert box.client.exists(artifacts._anchor_keys(continuation.VISUAL_KEYS)[VISUAL]) == 0
    count = len(box.s3.puts)
    with pytest.raises(artifacts.RouterReviewArtifactError): persist(box)
    assert len(box.s3.puts) == count


def test_same_namespace_v1_anchor_and_wrong_v2_tag_are_rejected(visual_box, subtests):
    box = visual_box
    stored = persist(box)
    key = stored.receipt['anchor_key']
    original = json.loads(box.client.get(key))
    for damage in ('version', 'missing_tag', 'tag_kind', 'tag_extra', 'tag_authority', 'prior_story'):
        with subtests.test(damage=damage):
            anchor = deepcopy(original)
            if damage == 'version': anchor['version'] = 1
            elif damage == 'missing_tag': anchor.pop('story_predecessor')
            elif damage == 'tag_kind': anchor['story_predecessor']['kind'] = 'observed_story'
            elif damage == 'tag_extra': anchor['story_predecessor']['qa_approved'] = True
            elif damage == 'tag_authority': anchor['story_predecessor']['evidence']['qa_approved'] = True
            elif damage == 'prior_story': anchor['prior_story_anchor_sha256'] = 'a' * 64
            box.client.set(key, artifacts._raw(anchor).decode())
            with box.client.pipeline() as pipe:
                pipe.watch(key)
                with pytest.raises((artifacts.RouterReviewArtifactError, SpendBlocked)):
                    box.sink._read_anchor(pipe, VISUAL)
    box.client.set(key, artifacts._raw(original).decode())
    preserve_old(box)


def test_input_production_phase_rejects_after_real_visual_ack(visual_box):
    # This direct phase check intentionally supplies no synthetic cut/sample
    # witness: the real occupied VISUAL slot must reject before accessing one.
    box = visual_box
    with pytest.raises(SpendBlocked): sampled._before_visual({'keys': continuation.VISUAL_KEYS}, box.scope)
    assert _dump(box.client) == box.before_persist and len(box.s3.puts) == box.put_count


def test_new_sampling_phase_rejects_different_story_core_before_any_visual(qualified):
    # Only a negative phase boundary: these detached source rows cannot issue
    # a collector and do not claim to be actual prepared cuts or sampled media.
    box = qualified
    cap = commission(box)
    state, fingerprint = recovery._state(continuity.LEAF_ID, box.client)
    pointers = sorted(state['generated_asset_candidates']['entries'], key=lambda row: row['scene_index'])
    package = deepcopy(box.contract['candidate'])
    package['scenes'][0]['ai_prompt'] += ' Different visual instruction.'
    data = {'keys': continuation.VISUAL_KEYS, 'package': sampled._raw(package), 'record': {
        'source': {'source_state_sha256': fingerprint,
            'source_spec_sha256': recovery._digest(state['spec']),
            'source_journal_sha256': recovery._digest(state['generated_asset_candidates']),
            'source_metadata_sha256': state['audio_candidate_checkpoint']['metadata_sha256'],
            'audio': {'sha256': state['audio_candidate_checkpoint']['audio_sha256'],
                      'size': state['audio_candidate_checkpoint']['size']}},
        'cuts': [{'raw': {'sha256': row['raw_sha256'], 'size': row['raw_size'],
                         'provider': row['provider']}} for row in pointers]}}
    before = _dump(box.client)
    calls, puts = len(box.wire.calls), len(box.s3.puts)
    with opened(box, cap) as scope:
        binding.bind_captured_story_predecessor(scope, story_evidence=box.qualification)
        with pytest.raises(sampled.SampledInputLinkageError): sampled._before_visual(data, scope)
        assert scope.attempted == set() and scope.evidence == {}
        assert 'story_predecessor' not in data
    assert _dump(box.client) == before and len(box.wire.calls) == calls and len(box.s3.puts) == puts


def test_actual_recovery_producer_links_v2_tag_all_thirty_images_and_original_unknown(produced_captured_visual):
    box = produced_captured_visual
    tag = {'version': 1, 'kind': 'captured_transport_story',
        'continuation_manifest_sha256': box.visual_cap.receipt['manifest_sha256'],
        'evidence': box.qualification.record}
    visual_anchor = json.loads(box.client.get(box.visual_receipt['anchor_key']))
    visual_manifest = json.loads(box.s3.objects[box.visual_receipt['manifest']['key']][0])
    link_anchor = json.loads(box.client.get(box.link_receipt['anchor_key']))
    link_record = json.loads(box.s3.objects[box.link_receipt['pointer']['key']][0])
    cut_record = json.loads(box.s3.objects[box.cut_receipt['manifest']['key']][0])
    for value in (box.visual_receipt, visual_anchor, visual_manifest,
                  box.link_receipt, link_anchor, link_record):
        assert value['version'] == 2 and value['story_predecessor'] == tag
        assert value['qa_approved'] is value['publish_eligible'] is value['full_qa_complete'] is False
    assert visual_anchor['prior_story_anchor_sha256'] is visual_manifest['prior_story_anchor_sha256'] is None
    assert link_record['visual_artifact_anchor_sha256'] == artifacts._hash(visual_anchor)
    assert link_record['visual_artifact_manifest'] == box.visual_receipt['manifest']
    assert link_record['cut_manifest'] == box.cut_receipt['manifest']
    assert link_record['reservation'] == visual_manifest['reservation']
    assert link_record['journal_keys'] == list(continuation.VISUAL_KEYS)
    prepared_bytes = box.s3.objects[visual_manifest['bodies']['prepared']['key']][0]
    wire_bytes = box.s3.objects[visual_manifest['bodies']['wire']['key']][0]
    assert artifacts._raw(json.loads(wire_bytes), artifacts._LIMITS['wire']) == prepared_bytes
    parts = json.loads(wire_bytes)['messages'][1]['content']
    images = [(parts[index-1]['text'], base64.b64decode(part['image_url']['url'].split(',')[1], validate=True))
              for index, part in enumerate(parts) if part['type'] == 'image_url']
    assert len(images) == len(link_record['events']) == 30
    for number, (event, (label, image)) in enumerate(zip(link_record['events'], images)):
        scene, order = divmod(number, 5)
        moment, fraction = sampled._ORDER[order]
        assert (event['scene_index'], event['candidate_index'], event['moment_index'], event['fraction']) == (scene, 0, moment, fraction)
        assert event['image'] == {'sha256': sampled._sha(image), 'size': len(image)}
        assert event['label_sha256'] == sampled._sha(label.encode())
        assert event['cut_sha256'] == cut_record['cuts'][scene]['cut']['sha256']
        assert event['recipe']['seconds'] == event['recipe']['source_duration'] * fraction
    assert box.client.exists(artifacts._anchor_keys(continuation.VISUAL_KEYS)[STORY]) == 0
    assert box.client.pttl(box.visual_receipt['anchor_key']) == box.client.pttl(box.link_receipt['anchor_key']) == -1
    assert {key: box.client.dump(key) for key in continuation.HISTORICAL_KEYS} == box.original_history
    assert _dump(box.client) == box.before_read and len(box.wire.calls) == 3
