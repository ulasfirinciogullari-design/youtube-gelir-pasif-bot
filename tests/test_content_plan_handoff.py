from copy import deepcopy
import json
import re
from uuid import uuid4

import pytest

from app.services import content_plan as plan, content_plan_handoff as handoff
from app.services import studio_state as jobs
from app.services.youtube_publish_state import UPLOAD_PREFIX
from test_production_series_promotion import case, _run, _snapshot, CHANNEL, CONNECTION, NOW
from test_channel_production import production


def install(case, monkeypatch):
    # Older scheduler fixtures predate the real YouTube channel-id validator.
    monkeypatch.setattr(plan, 'CHANNEL', re.compile('^' + CHANNEL + '$'))
    entry = plan.item('Completed documentary', 'Verified public documentary', 'long')
    task, publisher = str(uuid4()), str(uuid4())
    profile = json.loads(case.client.get(case.profile_key))
    source = deepcopy(json.loads(case.client.get(jobs.JOB_PREFIX + case.source_id)))
    source.update(task_id=task, parent_id=None)
    source['spec'].update(content_plan_item_id=entry['id'], format='landscape', duration_minutes=3)
    binding = {'target_channel_id': CHANNEL, 'connection_id': CONNECTION['connection_id'],
               'privacy_status': 'public', 'release_status': 'public'}
    source['result']['youtube'] = {**binding, 'video_id': 'planvideo01'}
    frozen = {'profile_revision': profile['profile_revision'], 'target_channel_id': CHANNEL, 'series': None}
    receipt = {'source_task_id': task, 'publish_task_id': publisher, 'status': 'complete',
               'publish_plan': frozen, 'youtube_video_id': 'planvideo01', **binding}
    pub = {'task_id': publisher, 'kind': 'publish', 'parent_id': task, 'state': 'SUCCESS',
           'result': {**binding, 'youtube_video_id': 'planvideo01'}}
    dispatch = {'task_id': task, 'item': entry, 'channel_id': CHANNEL,
                'profile_revision': profile['profile_revision'], 'connection_id': CONNECTION['connection_id']}
    document = {'version': 1, 'channel_id': CHANNEL, 'revision': str(uuid4()), 'enabled': True,
                'after_queue': 'auto_shorts', 'items': [entry], 'updated_at': entry['created_at']}
    for key, value in ((jobs.JOB_PREFIX + task, source), (jobs.JOB_PREFIX + publisher, pub),
                       (UPLOAD_PREFIX + task, receipt), (plan.DISPATCH_PREFIX + entry['id'], dispatch),
                       (plan.PLAN_PREFIX + CHANNEL, document), (plan.ACTIVE_KEY, {})):
        case.client.set(key, plan._raw(value))
    proof = plan.publication_proof(case.client, dispatch)
    assert proof
    case.client.set(plan.COMPLETION_PREFIX + entry['id'], plan._raw(proof))
    return document, receipt, source


def block_legacy(case):
    raw = json.loads(case.client.get(case.keys['ledger']))
    raw.update(release_status='blocked', release_side_effect_possible=False)
    case.client.set(case.keys['ledger'], plan._raw(raw))
    case.client.hset(case.state_key, mapping={'paused_reason': 'previous_publication_blocked'})


def test_completed_owner_queue_rotates_once_and_archives_legacy_private_hold(case, monkeypatch):
    document, _, _ = install(case, monkeypatch)
    block_legacy(case)
    old_job = case.client.get(jobs.JOB_PREFIX + case.source_id)
    old_upload = case.client.get(case.keys['ledger'])
    old_state = case.client.hgetall(case.state_key)
    result = _run(case)
    assert result['status'] == 'promoted'
    archive = json.loads(case.client.get(result['archive_key']))
    assert archive['state'] == old_state and archive['profile'] == case.profile
    assert 'public_proof' not in archive and 'unpublished_proof' not in archive
    proof = archive['owner_plan_handoff']
    assert proof['completion_kind'] == 'owner_plan_complete'
    assert proof['previous_job']['release_status'] == 'blocked'
    assert proof['previous_job_released'] is False
    assert proof['public_items'][0]['video_id'] == 'planvideo01'
    assert case.client.get(jobs.JOB_PREFIX + case.source_id) == old_job
    assert case.client.get(case.keys['ledger']) == old_upload
    assert not case.client.hget(case.state_key, 'paused_reason')
    assert _run(case)['status'] == 'already_promoted'
    profile = json.loads(case.client.get(case.profile_key))
    assert handoff.completed(case.client, profile, CONNECTION, case.client.hgetall(case.state_key)) is None


@pytest.mark.parametrize('damage', ['unpublished', 'disabled', 'pause', 'changed_publication',
    'uncertain_legacy', 'active_plan', 'owner_hold', 'wrong_pause'])
def test_no_handoff_or_mutation_on_incomplete_or_changed_evidence(case, monkeypatch, damage):
    document, receipt, source = install(case, monkeypatch)
    block_legacy(case)
    if damage == 'unpublished': case.client.delete(plan.COMPLETION_PREFIX + document['items'][0]['id'])
    if damage == 'disabled': document['enabled'] = False
    if damage == 'pause': document['after_queue'] = 'pause'
    if damage == 'changed_publication':
        receipt['release_status'] = 'private'
        case.client.set(UPLOAD_PREFIX + source['task_id'], plan._raw(receipt))
    if damage == 'uncertain_legacy':
        old = json.loads(case.client.get(case.keys['ledger'])); old['release_side_effect_possible'] = True
        case.client.set(case.keys['ledger'], plan._raw(old))
    if damage == 'active_plan': case.client.set(plan.ACTIVE_KEY, plan._raw({CHANNEL: document['items'][0]['id']}))
    if damage == 'owner_hold':
        old = json.loads(case.client.get(jobs.JOB_PREFIX + case.source_id)); old['owner_cancellation'] = {'reason': 'stop'}
        case.client.set(jobs.JOB_PREFIX + case.source_id, plan._raw(old))
    if damage == 'wrong_pause': case.client.hset(case.state_key, 'paused_reason', 'consumed_topics_changed')
    case.client.set(plan.PLAN_PREFIX + CHANNEL, plan._raw(document))
    before = _snapshot(case)
    with pytest.raises(Exception): _run(case)
    assert _snapshot(case) == before


def test_prepare_handoff_reads_are_watched_and_do_not_clear_old_hold(case, monkeypatch):
    install(case, monkeypatch); block_legacy(case)
    before = _snapshot(case)
    with case.client.pipeline() as pipe:
        assert handoff.allows_preparation(pipe, case.profile, CONNECTION, case.client.hgetall(case.state_key))
        pipe.multi(); pipe.ping(); assert pipe.execute() == [True]
    assert _snapshot(case) == before


@pytest.mark.parametrize('current_item', [False, True])
def test_older_public_history_does_not_block_new_plan_but_cannot_authorize_rotation_alone(case, monkeypatch, current_item):
    previous, receipt, source = install(case, monkeypatch)
    entry = previous['items'][0]
    dispatch = plan._object(case.client.get(plan.DISPATCH_PREFIX + entry['id']))
    old_revision = str(uuid4())
    dispatch['profile_revision'] = old_revision
    receipt['publish_plan']['profile_revision'] = old_revision
    source['spec']['production_profile_revision'] = old_revision
    receipt['youtube_video_id'] = 'oldervid001'
    source['result']['youtube']['video_id'] = 'oldervid001'
    publisher = plan._object(case.client.get(jobs.JOB_PREFIX + receipt['publish_task_id']))
    publisher['result']['youtube_video_id'] = 'oldervid001'
    for key, value in ((plan.DISPATCH_PREFIX + entry['id'], dispatch),
            (UPLOAD_PREFIX + source['task_id'], receipt), (jobs.JOB_PREFIX + source['task_id'], source),
            (jobs.JOB_PREFIX + publisher['task_id'], publisher)):
        case.client.set(key, plan._raw(value))
    public = plan.publication_proof(case.client, dispatch)
    assert public
    case.client.set(plan.COMPLETION_PREFIX + entry['id'], plan._raw(public))
    if current_item:
        current, _, _ = install(case, monkeypatch)
        previous['items'].extend(current['items'])
    case.client.set(plan.PLAN_PREFIX + CHANNEL, plan._raw(previous))
    block_legacy(case)
    before = _snapshot(case)
    proof = handoff.completed(case.client, case.profile, CONNECTION, case.client.hgetall(case.state_key))
    assert _snapshot(case) == before
    if current_item:
        assert proof is not None
        assert [row['video_id'] for row in proof['public_items']] == ['oldervid001', 'planvideo01']
        assert _run(case)['status'] == 'promoted'
    else:
        assert proof is None
