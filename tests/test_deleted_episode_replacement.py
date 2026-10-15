"""Real Redis reservation/proof boundaries; never a live provider or upload."""
from copy import deepcopy
import json
from uuid import uuid4

import pytest

from app.services import deleted_episode_replacement as replacement, youtube_automation
from test_external_episode_delivery import case as delivery_case, snapshot, NOW
from test_external_editorial_review import case as editorial_case, raw, change, CHANNEL, CONNECTION, REVISION


NOTE = 'The owner deleted the published second episode and requests a newly authored replacement, not acceptance of its old edit.'


@pytest.fixture
def case(delivery_case):
    c = delivery_case
    c.new_id = str(uuid4())
    new = deepcopy(c.source)
    new['task_id'] = c.new_id
    new['result']['external_descriptor_id'] = 'c' * 64
    new['result']['external_provenance']['video_sha256'] = 'd' * 64
    new['result']['video_key'] = 'external-masters/v1/' + 'd' * 64 + '/master.mp4'
    c.client.set(replacement.state.JOB_PREFIX + c.new_id, raw(new))
    c.client.set(replacement.editorial.ingest.RESERVATION_PREFIX + c.new_id,
                 raw({'status': 'complete', 'task_id': c.new_id, 'descriptor_id': 'c' * 64,
                      'job_sha256': replacement._digest(new)}))
    c.client.zadd(replacement.state.JOB_INDEX, {c.new_id: NOW})
    # Scheduler already dispatched episode 3; its genuine failures and paid
    # journal remain deferred. The actual publication counter is still 2.
    for task in (c.original, c.leaf):
        change(c.client, replacement.state.JOB_PREFIX + task,
               lambda x: x['spec'].update(production_topic_index=2,
                   topic=c.topics[2] + '\n\nChannel editorial direction: Business explanations'))
    c.client.hset(c.state_key, mapping={'cursor': '3',
        'consumed_prefix': replacement._prefix_digest(c.topics[:3])})
    c.cache_key = replacement.CACHE_PREFIX + CHANNEL + ':' + CONNECTION
    c.client.set(c.cache_key, raw({'version': 1, 'channel_id': CHANNEL, 'connection_id': CONNECTION,
        'last_error': None, 'last_attempt_at': NOW, 'videos': {c.video_id: {
            'availability': 'unavailable', 'error': 'video_unavailable', 'availability_checked_at': NOW}}}))
    c.assignment = replacement.editorial.SERIES_ASSIGNMENT_PREFIX + CHANNEL + ':business-one:' + c.new_id
    c.replacement_key = replacement.RECEIPT_PREFIX + c.new_id
    c.previous_index = replacement.PREVIOUS_INDEX_PREFIX + c.task
    return c


def reserve(c, **changes):
    values = dict(task_id=c.new_id, previous_source_task_id=c.task, deferred_failed_leaf_id=c.leaf,
                  expected_channel_id=CHANNEL, expected_profile_revision=REVISION, owner_reason=NOTE, now=NOW)
    return replacement.reserve_deleted_episode_replacement(**{**values, **changes})


def test_only_new_assignment_receipt_and_index_written_counter_cursor_qa_unchanged(case, monkeypatch):
    before = snapshot(case.client)
    result = reserve(case)
    assert result['status'] == 'reserved' and result['series']['number'] == 2
    after = snapshot(case.client)
    for key in (case.assignment, case.replacement_key, case.previous_index):
        after.pop(key)
    assert before == after
    receipt = json.loads(case.client.get(case.replacement_key))
    assert receipt['scheduler_cursor'] == 3
    assert receipt['frozen_replaced_topic_sha256'] == replacement.delivery.topic_sha256(case.topics[1])
    assert receipt['deferred_failure']['leaf_task_id'] == case.leaf
    assert receipt['deletion_attribution'] == 'owner_attested_deleted_not_inferred_from_api_absence'
    assert all(receipt[k] is False for k in ('previous_episode_accepted', 'qa_approved', 'publish_eligible',
                                            'scheduler_resumed', 'media_generation_authorized'))
    monkeypatch.setattr(youtube_automation, '_redis', lambda: case.client)
    # Exercise the existing real Lua publisher numbering, not a fake override.
    assert youtube_automation.reserve_series_number(CHANNEL, 'business-one', case.new_id, total=4) == 2
    assert case.client.get(replacement.editorial.SERIES_COUNTER_PREFIX + CHANNEL + ':business-one') == '2'
    assert json.loads(case.client.get(replacement.state.JOB_PREFIX + case.new_id))['result']['manual_qa_required'] is True


def test_idempotent_replay_and_changed_request(case):
    reserve(case); before = snapshot(case.client)
    assert reserve(case, now=NOW + 999)['status'] == 'already_reserved'
    assert snapshot(case.client) == before
    with pytest.raises(replacement.DeletedEpisodeReplacementError): reserve(case, owner_reason=NOTE + ' Changed.')
    assert snapshot(case.client) == before


def test_lost_exec_reply_does_not_duplicate_or_increment(case, monkeypatch):
    original = case.client.pipeline
    def pipeline():
        pipe = original(); execute = pipe.execute
        def uncertain(*args, **kwargs):
            execute(*args, **kwargs)
            raise OSError('Lost reply, not an absent commit')
        pipe.execute = uncertain
        return pipe
    monkeypatch.setattr(case.client, 'pipeline', pipeline)
    with pytest.raises(replacement.DeletedEpisodeReplacementError): reserve(case)
    monkeypatch.setattr(case.client, 'pipeline', original)
    before = snapshot(case.client)
    assert reserve(case)['status'] == 'already_reserved' and snapshot(case.client) == before


@pytest.mark.parametrize('damage', ['stale', 'future', 'not_missing', 'auth_error', 'wrong_video', 'wrong_connection'])
def test_only_fresh_successful_owner_api_absence_is_evidence(case, damage):
    def mutate(cache):
        video = cache['videos'][case.video_id]
        if damage == 'stale': video['availability_checked_at'] = NOW - replacement.REFRESH_SECONDS - 1
        if damage == 'future': video['availability_checked_at'] = NOW + 1
        if damage == 'not_missing': video.update(availability='available', error=None)
        if damage == 'auth_error': cache['last_error'] = 'authorization_failed'
        if damage == 'wrong_video': cache['videos'] = {'OtherVid123': video}
        if damage == 'wrong_connection': cache['connection_id'] = 'different-connection'
    change(case.client, case.cache_key, mutate)
    before = snapshot(case.client)
    with pytest.raises(replacement.DeletedEpisodeReplacementError): reserve(case)
    assert snapshot(case.client) == before


@pytest.mark.parametrize('field,value', [('cursor', '4'), ('cursor', '03'), ('paused_reason', ''),
    ('last_result', 'SUCCESS'), pytest.param('active_task_id', str(uuid4()), id='active_task_id-different-task'), pytest.param('last_task_id', str(uuid4()), id='last_task_id-different-task'),
    ('connection_id', 'other-connection'), ('profile_revision', 'other-revision')])
def test_current_scheduler_snapshot_must_be_terminal_paused_and_exact(case, field, value):
    case.client.hset(case.state_key, field, value); before = snapshot(case.client)
    with pytest.raises(replacement.DeletedEpisodeReplacementError): reserve(case)
    assert snapshot(case.client) == before


@pytest.mark.parametrize('damage', ['new_review', 'new_upload', 'new_assignment', 'counter', 'old_assignment',
    'epoch', 'credential', 'membership', 'failed_leaf_success', 'claimed_leaf', 'old_caption', 'old_disclosure',
    'old_private', 'new_import_hash', 'old_receipt', 'old_ledger_uncertain'])
def test_no_forged_source_auth_publication_or_numbering_grant(case, damage):
    c, client = case, case.client
    if damage == 'new_review': client.set(replacement.editorial.EDITORIAL_RECEIPT_PREFIX + c.new_id, '{}')
    if damage == 'new_upload': client.set(replacement.UPLOAD_PREFIX + c.new_id, '{}')
    if damage == 'new_assignment': client.set(c.assignment, '2')
    if damage == 'counter': client.set(replacement.editorial.SERIES_COUNTER_PREFIX + CHANNEL + ':business-one', '3')
    if damage == 'old_assignment': client.set(replacement.editorial.SERIES_ASSIGNMENT_PREFIX + CHANNEL + ':business-one:' + c.task, '1')
    if damage == 'epoch': client.set(replacement.editorial.ingest.AUTH_EPOCH_KEY, '16')
    if damage == 'credential': client.set(replacement.editorial.ingest.CREDENTIAL_PREFIX + CHANNEL, 'changed')
    if damage == 'membership': client.srem(replacement.editorial.ingest.CHANNEL_INDEX_KEY, CHANNEL)
    if damage == 'failed_leaf_success': change(client, replacement.state.JOB_PREFIX + c.leaf, lambda j: j.update(state='SUCCESS'))
    if damage == 'claimed_leaf': client.hset(replacement.state.RETRY_DISPATCH_PREFIX + c.leaf, mapping={'child_task_id': str(uuid4())})
    if damage == 'old_caption': change(client, replacement.state.JOB_PREFIX + c.task, lambda j: j['result']['youtube'].update(caption_uploaded=False))
    if damage == 'old_disclosure': change(client, replacement.state.JOB_PREFIX + c.task, lambda j: j['result']['youtube'].update(contains_synthetic_media=False))
    if damage == 'old_private': change(client, replacement.state.JOB_PREFIX + c.task, lambda j: j['result']['youtube'].update(privacy_status='private'))
    if damage == 'new_import_hash': change(client, replacement.editorial.ingest.RESERVATION_PREFIX + c.new_id, lambda j: j.update(job_sha256='0' * 64))
    if damage == 'old_receipt': change(client, replacement.editorial.EDITORIAL_RECEIPT_PREFIX + c.task, lambda j: j.update(receipt_sha256='0' * 64))
    if damage == 'old_ledger_uncertain': change(client, replacement.UPLOAD_PREFIX + c.task, lambda j: j.update(status='uncertain'))
    before = snapshot(client)
    with pytest.raises(replacement.DeletedEpisodeReplacementError): reserve(c)
    assert snapshot(client) == before


@pytest.mark.parametrize('same_channel', [False, True])
def test_other_channel_can_work_but_same_channel_cannot(case, same_channel):
    task = str(uuid4()); channel = CHANNEL if same_channel else 'UCdifferent123456'
    case.client.set(replacement.ACTIVE_KEY, raw({'channel_id': channel, 'task_id': task}))
    case.client.set(replacement.state.JOB_PREFIX + task, raw({'task_id': task, 'kind': 'render',
        'state': 'PROGRESS', 'spec': {'production_channel_id': channel}}))
    case.client.zadd(replacement.state.JOB_INDEX, {task: NOW})
    if same_channel:
        before = snapshot(case.client)
        with pytest.raises(replacement.DeletedEpisodeReplacementError): reserve(case)
        assert snapshot(case.client) == before
    else:
        assert reserve(case)['series']['number'] == 2


@pytest.mark.parametrize('race', ['counter', 'retry', 'profile'])
def test_concurrent_number_retry_or_auth_change_wins_without_partial_reservation(case, monkeypatch, race):
    original = case.client.pipeline
    def pipeline():
        pipe = original(); execute = pipe.execute
        def changed(*args, **kwargs):
            if race == 'counter': case.client.set(replacement.editorial.SERIES_COUNTER_PREFIX + CHANNEL + ':business-one', '3')
            if race == 'retry': case.client.hset(replacement.state.RETRY_DISPATCH_PREFIX + case.leaf, mapping={'child_task_id': str(uuid4())})
            if race == 'profile': change(case.client, replacement.editorial.ingest.PROFILE_PREFIX + CHANNEL, lambda p: p.update(profile_revision='new-revision'))
            return execute(*args, **kwargs)
        pipe.execute = changed
        return pipe
    monkeypatch.setattr(case.client, 'pipeline', pipeline)
    with pytest.raises(replacement.DeletedEpisodeReplacementError): reserve(case)
    assert not case.client.exists(case.assignment, case.replacement_key, case.previous_index)
