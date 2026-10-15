from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from redis.exceptions import ConnectionError

from app.services import production_episode_replacement as replace
from app.services import channel_production as production, studio_state as jobs
from app.services import production_spend_runtime as runtime
from app.services.production_spend import SpendBlocked
from test_included_series_pipeline import native, box, policy, installed_sdk_modules, CHANNEL, NOW
from test_production_credit_ledger import InterceptClient
from test_production_series_spend import snapshot

ROOT = '11111111-1111-4111-8111-111111111111'
CHILD = '22222222-2222-4222-8222-222222222222'


@pytest.fixture
def failed(native, monkeypatch):
    p = native.profile
    topic = p['production_topics'][-1] + '\n\nChannel editorial direction: ' + p['channel_identity']
    spec = {'topic': topic, 'duration_minutes': .5, 'language': 'en', 'channel_id': CHANNEL,
        'mode': 'production', 'format': 'shorts', 'workflow': 'auto', 'music': 'off',
        'production_scheduled': True, 'publish_after_render': True,
        'production_channel_id': CHANNEL, 'production_connection_id': 'old-google-connection',
        'production_profile_revision': p['profile_revision'], 'production_topic_index': 1}
    native.client.set(jobs.JOB_PREFIX + ROOT, json.dumps({'task_id': ROOT, 'kind': 'render',
        'parent_id': None, 'spec': spec, 'state': 'FAILURE', 'result': None}))
    native.client.zadd(jobs.JOB_INDEX, {ROOT: NOW.timestamp() - 60})
    native.client.hset(production.CHANNEL_STATE_PREFIX + CHANNEL, mapping={
        'paused_reason': 'previous_render_failed', 'last_result': 'FAILURE', 'last_task_id': ROOT,
        'dispatch_status': 'finished', 'connection_id': 'old-google-connection'})
    monkeypatch.setattr(production, '_redis', lambda: native.client)
    native.before = snapshot(native.client)
    return native


def test_same_failed_episode_keeps_cursor_old_jobs_and_funding_history(failed):
    result = replace.reserve_failed_episode_replacement(ROOT, CHILD)
    assert result['claimed'] is True
    spec = result['spec']
    assert spec['production_connection_id'] == failed.channel['connection_id']
    assert spec['production_topic_index'] == 1 and spec['topic'] == json.loads(failed.client.get(jobs.JOB_PREFIX + ROOT))['spec']['topic']
    assert failed.client.dump(jobs.JOB_PREFIX + ROOT) == failed.before[jobs.JOB_PREFIX + ROOT]
    assert failed.client.hget(production.CHANNEL_STATE_PREFIX + CHANNEL, 'cursor') == '2'
    assert failed.client.hget(production.CHANNEL_STATE_PREFIX + CHANNEL, 'active_task_id') == CHILD
    assert not failed.client.hexists(production.CHANNEL_STATE_PREFIX + CHANNEL, 'paused_reason')
    record = json.loads(failed.client.get(replace.PREFIX + 'source:' + ROOT))
    assert record['qa_approved'] is record['publish_eligible'] is False
    assert record['before_schedule']['paused_reason'] == 'previous_render_failed'
    assert failed.ledger.snapshot()['historical_cash_micro'] is None
    resolved = runtime.resolve_context(failed.client, CHILD)
    assert resolved['lineage_id'] == CHILD and resolved['connection_id'] == failed.channel['connection_id']
    after = snapshot(failed.client)
    assert replace.reserve_failed_episode_replacement(ROOT, '33333333-3333-4333-8333-333333333333')['claimed'] is False
    assert snapshot(failed.client) == after and failed.calls == []


@pytest.mark.parametrize('damage', ['already_published', 'upload_unknown', 'running_child', 'topic_changed',
    'connection_unverified', 'active_other_production', 'cursor_changed'])
def test_changed_source_and_ambiguous_publication_or_running_work_block(failed, damage):
    client = failed.client
    if damage == 'already_published':
        job = json.loads(client.get(jobs.JOB_PREFIX + ROOT)); job['youtube_video_id'] = 'published-id'
        client.set(jobs.JOB_PREFIX + ROOT, json.dumps(job))
    elif damage == 'upload_unknown': client.set(replace.UPLOAD_PREFIX + ROOT, 'unknown upload')
    elif damage == 'running_child':
        job = json.loads(client.get(jobs.JOB_PREFIX + ROOT)); job['retry_child_task_id'] = CHILD
        client.set(jobs.JOB_PREFIX + ROOT, json.dumps(job))
        client.set(jobs.JOB_PREFIX + CHILD, json.dumps({**job, 'task_id': CHILD, 'parent_id': ROOT, 'state': 'STARTED'}))
    elif damage == 'topic_changed':
        profile = deepcopy(failed.profile); profile['production_topics'][-1] = 'Changed topic'
        client.set(production.PROFILE_PREFIX + CHANNEL, json.dumps(profile))
    elif damage == 'connection_unverified':
        channel = {**failed.channel, 'requires_reconnect': True}
        client.set(production.OAUTH_CHANNEL_PREFIX + CHANNEL, json.dumps(channel))
    elif damage == 'active_other_production': client.set(production.ACTIVE_KEY, 'occupied')
    elif damage == 'cursor_changed': client.hset(production.CHANNEL_STATE_PREFIX + CHANNEL, 'cursor', '1')
    before = snapshot(client)
    with pytest.raises(SpendBlocked): replace.reserve_failed_episode_replacement(ROOT, CHILD)
    assert snapshot(client) == before and failed.calls == []


def test_lost_reservation_ack_does_not_release_or_create_another_job(failed, monkeypatch):
    def lost(number, result):
        # Read-only capacity PINGs may finish; lose only the actual 7-write ACK.
        if len(result) == 7: raise ConnectionError('synthetic lost ACK')
        return result
    wrapped = InterceptClient(failed.client, after=lost)
    monkeypatch.setattr(failed.ledger, 'client', wrapped)
    with pytest.raises(SpendBlocked, match='outcome_unverified'):
        replace.reserve_failed_episode_replacement(ROOT, CHILD)
    monkeypatch.setattr(failed.ledger, 'client', failed.client)
    assert replace.reserve_failed_episode_replacement(ROOT, '33333333-3333-4333-8333-333333333333')['claimed'] is False


def test_missing_receipt_half_is_not_a_new_replacement(failed):
    replace.reserve_failed_episode_replacement(ROOT, CHILD)
    failed.client.delete(replace.PREFIX + 'anchor:' + ROOT)
    before = snapshot(failed.client)
    with pytest.raises(SpendBlocked, match='record_incomplete'):
        replace.reserve_failed_episode_replacement(ROOT, '33333333-3333-4333-8333-333333333333')
    assert snapshot(failed.client) == before


def test_ambiguous_broker_reply_never_dispatches_the_replacement_twice(failed, monkeypatch):
    import sys
    send = Mock(side_effect=ConnectionError('synthetic broker accepted but ACK lost'))
    monkeypatch.setitem(sys.modules, 'app.tasks', SimpleNamespace(run_video_pipeline=SimpleNamespace(apply_async=send)))
    first = replace.dispatch_failed_episode_replacement(ROOT)
    assert first['claimed'] is True and first['status'] == 'dispatch_uncertain'
    assert send.call_count == 1 and send.call_args.kwargs['retry'] is False
    second = replace.dispatch_failed_episode_replacement(ROOT)
    assert second['claimed'] is False and second['task_id'] == first['task_id']
    assert send.call_count == 1 and failed.calls == []


@pytest.mark.parametrize('manual', [False, True])
def test_replacement_still_waits_for_publication_or_pauses_for_review(failed, manual):
    replace.reserve_failed_episode_replacement(ROOT, CHILD)
    job = json.loads(failed.client.get(jobs.JOB_PREFIX + CHILD))
    job.update(state='SUCCESS', result={'video_key': 'synthetic/final.mp4',
        'quality_disposition': 'manual_qa_preview' if manual else 'automated_qc_pass',
        'manual_qa_required': manual})
    failed.client.set(jobs.JOB_PREFIX + CHILD, json.dumps(job))
    result = production.reconcile_active_production(now=NOW.timestamp())
    state = failed.client.hgetall(production.CHANNEL_STATE_PREFIX + CHANNEL)
    assert state['cursor'] == '2' and state.get('last_public_task_id') is None
    if manual:
        assert state['paused_reason'] == 'previous_render_needs_review'
    else:
        assert result == 'active' and state['active_task_id'] == CHILD
    assert failed.client.dump(jobs.JOB_PREFIX + ROOT) == failed.before[jobs.JOB_PREFIX + ROOT]
