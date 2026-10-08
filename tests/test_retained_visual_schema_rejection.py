"""A genuine captured 400 remains unknown; a diagnostic never resends it."""
from copy import copy, deepcopy
import json
from unittest.mock import Mock

import pytest

from app.services import retained_visual_schema_rejection as reader
from app.services import retained_review_captured_story_continuation as continuation
from app.services import retained_captured_story_scope as binding
from app.services import abacus_router_review_runtime as runtime
from app.services import abacus_router_transport_capture as capture
from app.services import abacus_router_review_journal as journal
from app.services.production_spend import SpendBlocked
from test_retained_review_captured_story_continuation import (
    qualified, captured, source, case, planning_case, real_media, prepared, frozen_three,
    completed_probe, wire, forbid_live_transport, commission, restore, Intercept, BUCKET,
)
from test_retained_captured_story_scope import opened
from test_abacus_router_adapter import SCHEMA, image
from test_production_connection_continuity import _dump

REJECTION = {'success': False, 'errorType': 'UserFeedbackError',
             'error': "Validation error: ('uniqueItems',): Extra inputs are not permitted"}


@pytest.fixture
def rejected(qualified, monkeypatch):
    box = qualified
    box.visual_cap = commission(box)
    monkeypatch.setattr(reader, 'settings', box.config)
    box.wire.status = 400
    box.wire.chunks = [json.dumps(REJECTION).encode()]
    box.wire.headers = {'content-type': 'application/json', 'content-length': str(len(box.wire.chunks[0]))}
    with opened(box, box.visual_cap) as scope:
        binding.bind_captured_story_predecessor(scope, story_evidence=box.qualification)
        with pytest.raises(capture.RouterTransportCaptureError):
            runtime.generate_retained_router_review([{'type': 'text', 'text': 'Inspect this actual frame'}, image()],
                purpose=reader.PURPOSE, system_instruction='Complete visual review.', json_schema=deepcopy(SCHEMA))
        box.visual_capture = scope.transport_captures[reader.PURPOSE]
    box.rejected_snapshot = _dump(box.client)
    box.rejected_objects = deepcopy(box.s3.objects)
    box.rejected_calls = len(box.wire.calls)
    blocked = Mock(side_effect=AssertionError('No operation after the saved rejection'))
    for owner, name in ((capture, '_send'), (runtime, 'generate_retained_router_review'),
                        (journal.RouterReviewJournal, 'reserve'), (journal.RouterReviewJournal, 'settle'),
                        (journal.RouterReviewJournal, '_fresh'), (box.s3, 'put_object')):
        monkeypatch.setattr(owner, name, blocked)
    box.read_blocked = blocked
    return box


def read(box, client=None):
    return reader.read_visual_schema_rejection(client or box.client, box.s3,
        bucket=BUCKET, captured_story_continuation=box.visual_cap)


def test_real_capture_is_read_only_and_keeps_original_unknown(rejected, subtests):
    box = rejected
    value = read(box)
    record = value.record
    assert record['prior_occupied_count'] == 6 and record['prior_unknown_count'] == 5
    assert record['historical_record_count'] == len(record['commitments']['history_records']) == 36
    assert record['response_status_code'] == 400 and record['rejected_keyword'] == 'uniqueItems'
    assert record['read_acknowledged'] is True
    assert all(record[key] is expected for key, expected in reader._FLAGS.items())
    assert record['commitments']['image_count'] == record['commitments']['unique_items_rule_count'] == 1
    assert record['commitments']['response_sha256'] == box.visual_capture['summary']['response_sha256']
    with box.client.pipeline() as pipe:
        _, states, _, _ = continuation._read_control(pipe)
        assert states['story']['slots'][reader.PURPOSE]['response'] is None
        assert states['audio']['slots'] == {}
        continuation._ping(pipe)
    assert read(box).record == record
    record['qa_approved'] = True
    assert value.record['qa_approved'] is False
    with pytest.raises(TypeError): copy(value)
    with pytest.raises(reader.RetainedVisualSchemaRejectionError):
        _ = object.__new__(reader.RetainedVisualSchemaRejection).record
    anchor = box.visual_capture['anchor_key']
    for damage in ('absent_anchor', 'expiring_anchor', 'noncanonical_anchor', 'changed_source', 'cipher'):
        with subtests.test(damage=damage):
            if damage == 'absent_anchor': box.client.delete(anchor)
            elif damage == 'expiring_anchor': box.client.expire(anchor, 600)
            elif damage == 'noncanonical_anchor': box.client.set(anchor, json.dumps(json.loads(box.client.get(anchor))))
            elif damage == 'changed_source': box.client.set(reader.continuity._AUTH_EPOCH, '99')
            elif damage == 'cipher':
                key = box.visual_capture['encrypted_blob']['key']
                stored = box.s3.objects[key]
                box.s3.objects[key] = (b'changed', *stored[1:])
            with pytest.raises(reader.RetainedVisualSchemaRejectionError): read(box)
            restore(box.client, box.rejected_snapshot)
            box.s3.objects = deepcopy(box.rejected_objects)
    for mode in ('lost', 'wrong_type'):
        with subtests.test(ack=mode):
            def after(commands, ack):
                if commands == ('PING',):
                    if mode == 'lost': raise RuntimeError('PRIVATE acknowledgement')
                    return [1]
                return ack
            with pytest.raises(reader.RetainedVisualSchemaRejectionError) as error:
                read(box, Intercept(box.client, after=after))
            assert 'PRIVATE' not in str(error.value)
    assert _dump(box.client) == box.rejected_snapshot and box.s3.objects == box.rejected_objects
    assert len(box.wire.calls) == box.rejected_calls
    box.read_blocked.assert_not_called()


@pytest.mark.parametrize('change', ['other_keyword', 'other_error', 'success', 'error_type', 'extra', 'null', 'hidden_words'])
def test_unrelated_error_cannot_qualify_for_the_correction(change):
    value = deepcopy(REJECTION)
    if change == 'other_keyword': value['error'] = value['error'].replace('uniqueItems', 'maxItems')
    elif change == 'other_error': value['error'] = 'No credits available'
    elif change == 'success': value['success'] = True
    elif change == 'error_type': value['errorType'] = 'AuthenticationError'
    elif change == 'extra': value['completion'] = {}
    elif change == 'null': value['error'] = None
    elif change == 'hidden_words': value['error'] += ' but this request completed'
    with pytest.raises(reader.RetainedVisualSchemaRejectionError):
        reader._rejection(json.dumps(value).encode())
