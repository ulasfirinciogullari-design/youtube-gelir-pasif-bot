"""Disposable authenticated histories; no production response or quality claim."""
from copy import copy, deepcopy
import json

import pytest
from redis.exceptions import ConnectionError

from app.services import retained_captured_story_visual_evidence as reader
from app.services import retained_review_captured_story_continuation as continuation
from app.services import retained_transport_story_evidence as story_reader
from app.services import abacus_router_review_journal as journal
from app.services import abacus_router_review_artifacts as artifacts
from app.services import abacus_router_review_runtime as runtime
from app.services import abacus_router_adapter as adapter
from app.services import retained_cut_evidence as cuts
from app.services import production_connection_continuity as continuity
from app.services import render
from app.services import retained_story_visual_evidence as legacy_reader
from test_retained_review_captured_story_continuation import (
    qualified, captured, source, case, planning_case, real_media, prepared,
    frozen_three, completed_probe, wire, forbid_live_transport, commission, selected,
    BUCKET, NEW_KEY, NOW,
)
from test_production_connection_continuity import _dump
from test_abacus_router_review_journal import request
from test_retained_review_credential_successor import Intercept
from test_preserved_captured_story_recovery import produced_captured_visual

_STORY, _VISUAL = journal.PURPOSES
ERROR = '^retained_captured_story_visual_'


def read(box, *, client=None, **changes):
    return reader.read_retained_captured_story_visual_evidence(client or box.client, box.s3, **{
        'bucket': BUCKET, 'captured_story_continuation': box.continuation_cap,
        'story_evidence': box.qualification, 'audit_pointer': box.audit_pointer, **changes})


def test_public_constructor_and_copied_or_unissued_objects_are_not_evidence():
    with pytest.raises(TypeError):
        reader.RetainedCapturedStoryVisualEvidence()
    forged = object.__new__(reader.RetainedCapturedStoryVisualEvidence)
    with pytest.raises(reader.RetainedCapturedStoryVisualEvidenceError):
        _ = forged.record
    with pytest.raises(TypeError):
        copy(forged)
    class Child(reader.RetainedCapturedStoryVisualEvidence):
        pass
    with pytest.raises(reader.RetainedCapturedStoryVisualEvidenceError):
        _ = object.__new__(Child).commitments


def test_empty_unknown_or_wrong_selection_rejects_before_any_storage_read(qualified, subtests):
    box = qualified
    box.continuation_cap = commission(box)
    box.audit_pointer = {}
    before, reads, puts, calls = _dump(box.client), len(box.s3.reads), len(box.s3.puts), len(box.wire.calls)
    for overrides in ({}, {'captured_story_continuation': None},
            {'captured_story_continuation': box.cap}, {'captured_story_continuation': {}},
            {'story_evidence': box.qualification.record},
            {'story_evidence': object.__new__(story_reader.RetainedTransportStoryEvidence)}):
        with subtests.test(overrides=tuple(overrides)):
            with pytest.raises(reader.RetainedCapturedStoryVisualEvidenceError, match=ERROR):
                read(box, **overrides)
            assert _dump(box.client) == before and len(box.s3.reads) == reads
    ledger, _ = selected(box, box.continuation_cap)
    ledger.reserve(_VISUAL, request(key=NEW_KEY))
    before = _dump(box.client)
    with pytest.raises(reader.RetainedCapturedStoryVisualEvidenceError, match=ERROR):
        read(box)
    assert _dump(box.client) == before and len(box.s3.reads) == reads
    assert len(box.s3.puts) == puts and len(box.wire.calls) == calls


def _replace_link(box, mutate, *, cut_pointer=None):
    anchor = json.loads(box.client.get(box.link_receipt['anchor_key']))
    record = json.loads(box.s3.objects[anchor['pointer']['key']][0])
    mutate(record)
    raw = cuts._raw(record)
    digest = reader._sha(raw)
    key = f'recovery/{continuity.LEAF_ID}/sampled_cut_link/v1/{digest}.json'
    box.s3.objects[key] = (raw, 'application/json')
    anchor['pointer'] = {'key': key, 'sha256': digest, 'size': len(raw), 'content_type': 'application/json'}
    if cut_pointer is not None:
        anchor['cut_manifest_sha256'] = cut_pointer['sha256']
    box.client.set(box.link_receipt['anchor_key'], cuts._raw(anchor).decode())


def test_genuine_saved_story_and_observed_visual_remain_distinct_and_read_only(
        produced_captured_visual, monkeypatch, subtests):
    box = produced_captured_visual
    before = _dump(box.client)
    puts, calls = len(box.s3.puts), len(box.wire.calls)
    def forbidden(*args, **kwargs):
        raise AssertionError('PRIVATE no observer, provider, mutation or rendering')
    for owner, name in ((adapter, 'observe_router_response'), (runtime, 'generate_retained_router_review'),
            (journal.RouterReviewJournal, 'reserve'), (journal.RouterReviewJournal, 'settle'),
            (journal.RouterReviewJournal, '_fresh'), (render, 'normalize_clip'), (box.s3, 'put_object')):
        monkeypatch.setattr(owner, name, forbidden)
    monkeypatch.setenv('RAILWAY_GIT_COMMIT_SHA', 'b' * 40)
    client = Intercept(box.client)
    value = read(box, client=client)
    assert type(value) is reader.RetainedCapturedStoryVisualEvidence
    assert value.record['component_pass'] is True
    assert all(value.record[name] is expected for name, expected in reader._FLAGS.items())
    assert value.record['visual_settlement_observed'] is True
    assert value.record['captured_story_read_acknowledged'] is True
    assert value.commitments['story_evidence_sha256'] == reader._hash(box.qualification.record)
    assert value.commitments['story_predecessor'] == box.visual_receipt['story_predecessor']
    assert value.commitments['visual_anchor_sha256'] == box.visual_receipt['anchor_sha256']
    assert value.commitments['sampled_link_anchor_sha256'] == box.link_receipt['anchor_sha256']
    assert value.commitments['cut_manifest'] == box.cut_receipt['manifest']
    assert client.executions == [('PING',), ('PING',), ('PING',)]
    assert NEW_KEY not in json.dumps(value.record) + repr(value)
    assert _dump(box.client) == before and len(box.s3.puts) == puts and len(box.wire.calls) == calls
    assert all(box.client.dump(k) == raw for k, raw in box.original_history.items())
    assert not box.client.exists(artifacts._anchor_keys(continuation.VISUAL_KEYS)[_STORY])
    detached = value.commitments; detached['journal_keys'].append('arbitrary namespace')
    assert value.commitments['journal_keys'] == list(continuation.VISUAL_KEYS)
    with pytest.raises(TypeError): copy(value)
    with pytest.raises(legacy_reader.RetainedStoryVisualEvidenceError):
        legacy_reader.read_retained_story_visual_evidence(box.client, box.s3, bucket=BUCKET,
            completion_plan=box.continuation_cap, audit_pointer=box.audit_pointer)

    snapshots = {key: box.client.dump(key) for key in box.client.scan_iter('*')}
    objects = deepcopy(box.s3.objects)
    for fault in ('link_missing', 'link_ttl', 'visual_missing', 'legacy_v1', 'tag_transplant',
                  'link_tag_transplant', 'sample_event', 'cut_timing', 'cut_blob', 'raw_blob',
                  'metadata_blob', 'saved_story_cipher', 'old_history', 'selected_rollback',
                  'source_claim', 'source_race', 'final_lost_ack', 'final_malformed_ack',
                  'unsealed_story', 'wrong_cap', 'public_blob'):
        with subtests.test(fault=fault):
            for key in list(box.client.scan_iter('*')): box.client.delete(key)
            for key, raw in snapshots.items(): box.client.restore(key, 0, raw)
            box.s3.objects = deepcopy(objects)
            box.s3.extra_grants = []
            options, client = {}, Intercept(box.client)
            if fault == 'link_missing': box.client.delete(box.link_receipt['anchor_key'])
            elif fault == 'link_ttl': box.client.pexpire(box.link_receipt['anchor_key'], 100000)
            elif fault == 'visual_missing': box.client.delete(box.visual_receipt['anchor_key'])
            elif fault in ('legacy_v1', 'tag_transplant'):
                key = box.visual_receipt['anchor_key']; anchor = json.loads(box.client.get(key))
                if fault == 'legacy_v1':
                    anchor['version'] = 1; anchor.pop('story_predecessor')
                else: anchor['story_predecessor']['continuation_manifest_sha256'] = '0' * 64
                box.client.set(key, cuts._raw(anchor).decode())
            elif fault == 'link_tag_transplant':
                _replace_link(box, lambda record: record['story_predecessor'].update(
                    continuation_manifest_sha256='0' * 64))
            elif fault == 'sample_event':
                _replace_link(box, lambda record: record['events'][0].update(moment_index=0))
            elif fault == 'cut_timing':
                pointer = box.cut_receipt['manifest']
                record = json.loads(box.s3.objects[pointer['key']][0])
                record['cuts'][0]['timeline_duration'] += .25
                record['cuts'][1]['timeline_duration'] -= .25
                raw = cuts._raw(record); digest = reader._sha(raw)
                key = f'recovery/{continuity.LEAF_ID}/retained_cuts/v1/manifests/{digest}.json'
                box.s3.objects[key] = (raw, 'application/json')
                changed = {'key': key, 'sha256': digest, 'size': len(raw), 'content_type': 'application/json'}
                _replace_link(box, lambda record: record.update(cut_manifest=changed), cut_pointer=changed)
            elif fault in ('cut_blob', 'raw_blob', 'metadata_blob', 'saved_story_cipher'):
                original = json.loads(box.client.get(continuity._JOB + continuity.LEAF_ID))
                if fault == 'cut_blob': key = value.commitments['cuts'][0]['key']
                elif fault == 'raw_blob': key = original['generated_asset_candidates']['entries'][0]['raw_key']
                elif fault == 'metadata_blob': key = original['audio_candidate_checkpoint']['metadata_key']
                else: key = box.qualification.commitments['encrypted_capture']['key']
                raw, mime = box.s3.objects[key]; box.s3.objects[key] = (raw + b'changed', mime)
            elif fault == 'old_history': box.client.set(continuation.HISTORICAL_KEYS[0], '{}')
            elif fault == 'selected_rollback':
                key = continuation.VISUAL_KEYS[0]; state = json.loads(box.client.get(key)); state['slots'] = {}
                box.client.set(key, cuts._raw(state).decode())
            elif fault in ('source_claim', 'source_race'):
                counter = [0]
                def claim(*unused):
                    counter[0] += 1
                    if fault == 'source_claim' or counter[0] == 3:
                        key = continuity._JOB + continuity.LEAF_ID
                        original = json.loads(box.client.get(key)); original['retry_claimed'] = True
                        box.client.set(key, cuts._raw(original).decode())
                if fault == 'source_claim': claim()
                else: client = Intercept(box.client, before=claim)
            elif fault in ('final_lost_ack', 'final_malformed_ack'):
                counter = [0]
                def fail_final(commands, result):
                    counter[0] += 1
                    if counter[0] == 3:
                        if fault == 'final_lost_ack': raise ConnectionError('PRIVATE_BACKEND_TEXT')
                        return [1]
                    return result
                client = Intercept(box.client, after=fail_final)
            elif fault == 'unsealed_story': options['story_evidence'] = box.qualification.record
            elif fault == 'wrong_cap': options['captured_story_continuation'] = box.cap
            elif fault == 'public_blob':
                box.s3.extra_grants = [{'Grantee': {'Type': 'Group',
                    'URI': 'http://acs.amazonaws.com/groups/global/AllUsers'}, 'Permission': 'READ'}]
            with pytest.raises(reader.RetainedCapturedStoryVisualEvidenceError, match=ERROR) as caught:
                read(box, client=client, **options)
            assert 'PRIVATE' not in str(caught.value) and NEW_KEY not in str(caught.value)
            assert all(commands == ('PING',) for commands in client.executions)
            assert len(box.s3.puts) == puts and len(box.wire.calls) == calls
