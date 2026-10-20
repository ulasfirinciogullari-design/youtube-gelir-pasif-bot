"""Ordinary registry writers cannot overwrite or publish an admitted episode."""
import json

import fakeredis
import pytest

from app.services import studio_state as studio
from app.services import youtube_publish_state as uploads

CHILD = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
PUBLISHER = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'


@pytest.fixture
def client(monkeypatch):
    value = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(studio, '_client', lambda: value)
    monkeypatch.setattr(uploads, '_redis', lambda: value)
    return value


@pytest.mark.parametrize('task,marker', [(CHILD, studio.RETAINED_DELIVERY_CHILD_PREFIX + CHILD),
    *((task, key) for task in studio._RETAINED_LINEAGE for key in studio._RETAINED_ROOT_KEYS)])
def test_partial_or_full_retained_owner_blocks_all_ordinary_registry_writes(client, task, marker):
    original = {'task_id': task, 'kind': 'render', 'state': 'PENDING', 'result': None,
                'created_ts': 1.0, 'spec': {}}
    client.set(studio.JOB_PREFIX + task, json.dumps(original))
    client.set(marker, 'occupied')
    before = {k: client.dump(k) for k in client.scan_iter()}
    assert studio.save_job({**original, 'state': 'FAILURE'}) == original
    assert studio.mark_success(task, {'task_id': task, 'qa': 'not-retained-proof'}) == original
    assert studio.merge_youtube_result_field(task, 'youtube', {'youtube_video_id': 'Unbound01'}) is False
    with pytest.raises(uploads.UploadReservationError):
        uploads.reserve_upload(task, PUBLISHER, target_channel_id='channel123', connection_id='connection123')
    with pytest.raises(uploads.UploadAlreadyInProgress): uploads.acquire_execution_lock(task, PUBLISHER)
    assert {k: client.dump(k) for k in client.scan_iter()} == before
    assert all(client.pttl(k) == -1 for k in before)


def test_old_reserved_upload_and_existing_lock_cannot_be_adopted_after_retained_claim(client):
    record, created = uploads.reserve_upload(CHILD, PUBLISHER, target_channel_id='channel123', connection_id='connection123')
    assert created
    token = uploads.acquire_execution_lock(CHILD, PUBLISHER)
    client.set(studio.RETAINED_DELIVERY_CHILD_PREFIX + CHILD, 'occupied')
    before = {k: client.dump(k) for k in client.scan_iter()}
    same, replaced = uploads.reserve_upload(CHILD, PUBLISHER, target_channel_id='channel123', connection_id='connection123')
    assert replaced is False and same == record
    with pytest.raises(uploads.UploadReservationError): uploads.mark_upload_started(CHILD, PUBLISHER)
    uploads.release_execution_lock(CHILD, token)
    # Remaining TTL moves with time; compare values rather than encoded expiry.
    assert client.get(uploads._lock_key(CHILD)) == token
    assert uploads.get_upload_record(CHILD) == record
    assert set(client.scan_iter()) == set(before)


def test_dashboard_overflow_preserves_retained_source_bytes_and_durability(client, monkeypatch):
    monkeypatch.setattr(studio, 'MAX_INDEXED_JOBS', 1)
    old = {'task_id': CHILD, 'kind': 'render', 'state': 'PENDING', 'result': None, 'created_ts': 1.0}
    client.set(studio.JOB_PREFIX + CHILD, json.dumps(old))
    client.set(studio.RETAINED_DELIVERY_CHILD_PREFIX + CHILD, 'occupied')
    client.zadd(studio.JOB_INDEX, {CHILD: 1.0})
    encoded = client.get(studio.JOB_PREFIX + CHILD)
    studio.save_job({'task_id': PUBLISHER, 'kind': 'render', 'state': 'PENDING', 'created_ts': 2.0})
    assert client.get(studio.JOB_PREFIX + CHILD) == encoded
    assert client.pttl(studio.JOB_PREFIX + CHILD) == -1
