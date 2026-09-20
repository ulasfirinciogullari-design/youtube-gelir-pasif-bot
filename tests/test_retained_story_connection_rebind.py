"""Same saved encrypted STORY, genuine fresh source reads, no transport/writes."""
from copy import deepcopy
import json

import pytest

from app.services import retained_story_connection_rebind as rebound
from app.services import retained_transport_story_evidence as saved
from app.services import production_connection_continuity as continuity
from app.services import retained_review_completion_plan as completion
from test_retained_transport_story_evidence import (
    captured, source, case, planning_case, real_media, prepared, frozen_three,
    completed_probe, wire, forbid_live_transport, read, snapshot, restore, BUCKET,
)
from test_production_connection_continuity import _dump


def reconnect(box):
    key = continuity._CHANNEL + continuity.CHANNEL_ID
    row = json.loads(box.client.get(key))
    row['connection_id'] = 'current-owner-reconnected'
    box.client.set(key, json.dumps(row))
    box.client.set(continuity._CREDENTIAL + continuity.CHANNEL_ID, 'synthetic-new-cipher')
    box.client.set(continuity._AUTH_EPOCH, '99')


def test_same_content_reconnection_reuses_authentic_story_without_reviving_request(captured):
    box = captured
    historical = read(box)
    reconnect(box)
    before, calls = _dump(box.client), len(box.wire.calls)
    with pytest.raises(Exception):
        read(box)
    result = rebound.read_reconnected_story_evidence(box.client, box.s3,
        bucket=BUCKET, qualification=historical.record)
    assert result.story_evidence.record == historical.record
    record = result.record
    assert record['changed_fields'] == sorted(rebound._OAUTH_FIELDS)
    assert record['current_source']['current_connection_id'] == 'current-owner-reconnected'
    assert record['current_source_sha256'] != record['historical_source_sha256']
    assert record['historical_source_sha256'] == historical.commitments['continuity_sha256']
    assert not any(record[k] for k in ('qa_approved', 'publish_eligible', 'request_authorized',
                                      'cash_authorized', 'settlement_observed', 'fresh_google_identity_verified'))
    assert result.story_evidence.qa_approved is False
    assert _dump(box.client) == before and len(box.wire.calls) == calls
    assert json.loads(box.client.get(completion.STORY_KEYS[0]))['slots'][saved._STORY]['response'] is None
    box.forbidden.assert_not_called()
    with box.client.pipeline() as pipe:
        assert rebound.verify_current_bridge(pipe, record, historical.record) == record['current_source']
        completion._ping(pipe)
    box.client.set(continuity._AUTH_EPOCH, '100')
    with box.client.pipeline() as pipe, pytest.raises(saved.RetainedTransportStoryEvidenceError):
        rebound.verify_current_bridge(pipe, record, historical.record)


def test_qualification_cannot_substitute_semantics_media_or_capture(captured, subtests):
    box = captured
    original = read(box).record
    reconnect(box)
    before, calls = _dump(box.client), len(box.wire.calls)
    for field in ('parsed_result_sha256', 'usage_sha256', 'semantic_diagnostic_sha256',
                  'source_metadata_sha256', 'immutable_core_sha256', 'source_state_sha256',
                  'continuity_sha256', 'request_sha256', 'wire_sha256', 'capture_context_sha256'):
        with subtests.test(field=field):
            changed = deepcopy(original)
            changed['commitments'][field] = '0' * 64
            with pytest.raises(saved.RetainedTransportStoryEvidenceError):
                rebound.read_reconnected_story_evidence(box.client, box.s3,
                    bucket=BUCKET, qualification=changed)
            assert _dump(box.client) == before and len(box.wire.calls) == calls
    box.forbidden.assert_not_called()


def test_profile_job_or_audio_drift_is_not_a_connection_renewal(captured, subtests):
    box = captured
    historical = read(box).record
    # Source values originate in the authenticated transport, not a caller's
    # proposed list of fields to ignore.
    cipher = saved.cuts._read_private(box.s3, BUCKET, historical['commitments']['encrypted_capture'])
    context, _, _ = saved._authenticated_context(cipher, box.config.app_encryption_key)
    source = context['source']
    for field in set(source) - rebound._OAUTH_FIELDS:
        with subtests.test(field=field):
            changed = deepcopy(source)
            changed[field] = None if changed[field] is not None else 'changed'
            with pytest.raises(saved.RetainedTransportStoryEvidenceError):
                rebound.verify_unchanged_content(source, changed)
    with pytest.raises(TypeError):
        rebound.ReconnectedStoryEvidence()
    forged = object.__new__(rebound.ReconnectedStoryEvidence)
    with pytest.raises(saved.RetainedTransportStoryEvidenceError):
        _ = forged.record
    box.forbidden.assert_not_called()
