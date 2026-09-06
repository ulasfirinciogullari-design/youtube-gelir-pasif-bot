"""Real local Redis transactions; no provider, upload, retry or job dispatch."""
from copy import deepcopy
import json
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.services import external_episode_delivery as delivery
from test_external_editorial_review import case as editorial_case, approve, saved, raw, change, CHANNEL, CONNECTION, REVISION
from test_channel_production import production


NOW = 2000000000
EXPLANATION = ('The independently produced explanatory animation covers the frozen membership fee, warehouse model '
               'and shopper trade-off. Its rewritten narration does not claim that all profit comes from fees.')


@pytest.fixture
def case(editorial_case, monkeypatch):
    c = editorial_case; client = c.client
    c.original, c.leaf, c.publish_id = [str(uuid4()) for _ in range(3)]
    c.topics = ['First business episode', 'Why pay membership before shopping?', 'How shipping changes costs', 'Why stores group products']
    change(client, delivery.editorial.ingest.PROFILE_PREFIX + CHANNEL,
           lambda p: p.update(series_id='business-one', series_name='Business', series_total=4,
                require_thumbnail=True, production_topics=c.topics, production_enabled=True,
                production_interval_hours=6, default_language='en', route_label='margin-en', channel_identity='Business explanations'))
    approve(c)
    c.profile = json.loads(client.get(delivery.editorial.ingest.PROFILE_PREFIX + CHANNEL))
    c.spec = {'topic': c.topics[1] + '\n\nChannel editorial direction: Business explanations',
        'duration_minutes': .5, 'language': 'en', 'channel_id': 'margin-en', 'mode': 'production',
        'format': 'shorts', 'workflow': 'auto', 'production_channel_id': CHANNEL,
        'production_connection_id': CONNECTION, 'production_profile_revision': REVISION,
        'production_scheduled': True, 'publish_after_render': True, 'production_topic_index': 1}
    token = 'genuine-private-retry-token'
    for task, parent in ((c.original, None), (c.leaf, c.original)):
        job = {'task_id': task, 'kind': 'render', 'state': 'FAILURE', 'parent_id': parent,
               'spec': deepcopy(c.spec), 'result': None, 'error': 'Preserve original QA failure',
               'rejected_script': 'An older different script; exact words are not mandatory.'}
        if task == c.original:
            job.update(retry_child_task_id=c.leaf, retry_claimed=True, retry_dispatch_state='dispatched')
        client.set(delivery.studio_state.JOB_PREFIX + task, raw(job))
        client.hset(delivery.studio_state.PAID_CREATE_BUDGET_PREFIX + task, mapping={'cap': '6', 'used': '6'})
    client.hset(delivery.studio_state.RETRY_DISPATCH_PREFIX + c.original,
                mapping={'child_task_id': c.leaf, 'token': token, 'state': 'dispatched', 'mode': 'full'})
    client.hset(delivery.studio_state.RETRY_CHILD_CLAIM_PREFIX + c.leaf, mapping={'source_task_id': c.original, 'token': token})
    client.set(delivery.studio_state.RETRY_CHILD_EXECUTION_PREFIX + c.leaf, token)
    c.state_key = delivery.CHANNEL_STATE_PREFIX + CHANNEL
    client.hset(c.state_key, mapping={'cursor': '2', 'consumed_prefix': delivery._prefix_digest(c.topics[:2]),
        'paused_reason': 'previous_render_failed', 'last_result': 'FAILURE', 'last_task_id': c.original,
        'dispatch_status': 'finished', 'profile_revision': REVISION, 'connection_id': CONNECTION,
        'next_due': str(NOW + 9000)})
    source = saved(c)
    c.series = {'id': 'business-one', 'name': 'Business', 'number': 2, 'total': 4}
    c.plan = {'source_task_id': c.task, 'target_channel_id': CHANNEL, 'profile_revision': REVISION,
        'contains_synthetic_media': True, 'series': c.series, 'release_mode': 'public', 'publish_at': None,
        'require_thumbnail': True, 'thumbnail_key': None, 'title': 'Server-prepared rewritten episode (2/4)',
        'quality_snapshot': {k: source['result'][k] for k in
            ('quality_disposition', 'manual_qa_required', 'editorial_review_id', 'editorial_review_sha256')}}
    binding = {'target_channel_id': CHANNEL, 'connection_id': CONNECTION, 'profile_revision': REVISION}
    public = {**binding, 'privacy_status': 'public', 'release_status': 'public', 'caption_uploaded': True,
              'thumbnail_uploaded': True, 'contains_synthetic_media': True, 'series': c.series}
    c.video_id = 'Abc123def45'
    source['result'].update(youtube={**public, 'video_id': c.video_id},
        youtube_automation={'status': 'queued', 'publish_task_id': c.publish_id, 'target_channel_id': CHANNEL,
                            'profile_revision': REVISION, 'release_mode': 'public'})
    client.set(delivery.studio_state.JOB_PREFIX + c.task, raw(source))
    client.set(delivery.studio_state.JOB_PREFIX + c.publish_id, raw({'task_id': c.publish_id, 'kind': 'publish',
        'state': 'SUCCESS', 'parent_id': c.task, 'spec': {**binding, 'source_task_id': c.task,
            'privacy_status': 'private', 'release_mode': 'public'},
        'result': {**public, 'task_id': c.publish_id, 'source_task_id': c.task, 'status': 'complete',
                   'stage': 'complete', 'progress': 100, 'youtube_video_id': c.video_id}}))
    client.set(delivery.UPLOAD_PREFIX + c.task, raw({'version': 2, 'status': 'complete',
        'source_task_id': c.task, 'publish_task_id': c.publish_id, 'youtube_video_id': c.video_id,
        'target_channel_id': CHANNEL, 'connection_id': CONNECTION, 'privacy_status': 'public',
        'requested_release_mode': 'public', 'release_status': 'public', 'side_effect_possible': True,
        'release_side_effect_possible': True, 'release_completed_at': '2026-09-06T16:00:00Z', 'publish_plan': c.plan}))
    assignment, counter = delivery.editorial._series_keys(source, c.plan)
    client.set(assignment, '2'); client.set(counter, '2')
    client.zadd(delivery.studio_state.JOB_INDEX, {task: NOW - 1 for task in (c.original, c.leaf, c.task, c.publish_id)})
    monkeypatch.setattr(delivery.studio_state, '_client', lambda: client)
    c.receipt_key = delivery.RECEIPT_PREFIX + CHANNEL + ':' + c.original
    return c


def resolve(c, **kwargs):
    return delivery.resolve_external_episode(CHANNEL, c.original, c.leaf, c.task, REVISION,
        delivery.topic_sha256(c.topics[1]), EXPLANATION, **{'now': NOW, **kwargs})


def snapshot(client):
    result = {}
    for key in client.scan_iter():
        kind = client.type(key)
        if kind == 'string': value = client.get(key)
        elif kind == 'hash': value = client.hgetall(key)
        elif kind == 'set': value = client.smembers(key)
        else: value = client.zrange(key, 0, -1, withscores=True)
        result[key] = (kind, value)
    return result


def test_rewritten_public_episode_resolves_only_pause_and_due_not_old_failure(case):
    before = snapshot(case.client)
    result = resolve(case)
    assert result['status'] == 'resolved' and result['cursor'] == 2
    assert result['delivery_disposition'] == 'separately_delivered_editorial_replacement'
    assert result['retry_repaired'] is result['qa_approved'] is result['media_generation_authorized'] is False
    assert result['public_delivery']['youtube_video_id'] == case.video_id
    assert result['request']['editorial_explanation'] == EXPLANATION
    after = snapshot(case.client)
    for key in (case.receipt_key, delivery.LEAF_INDEX_PREFIX + case.leaf, delivery.EXTERNAL_INDEX_PREFIX + case.task):
        after.pop(key)
    before[case.state_key][1].pop('paused_reason'); before[case.state_key][1]['next_due'] = str(NOW)
    assert before == after
    assert saved(case)['parent_id'] is None and saved(case)['spec']['production_scheduled'] is False


def test_repeat_is_historical_not_new_scheduling_permission(case):
    first = resolve(case)
    case.client.hset(case.state_key, mapping={'cursor': '3', 'next_due': '9999999999'})
    before = snapshot(case.client)
    assert resolve(case, now=NOW + 900)['status'] == 'already_resolved'
    assert snapshot(case.client) == before and first['next_due'] == NOW


def test_real_normal_scheduler_uses_own_next_slot_once_after_resolution(case, production):
    scheduler, _ = production
    scheduler.reserve_due_production.__globals__['_redis'] = lambda: case.client
    resolve(case)
    enqueue = Mock()
    connection = json.loads(case.client.get(delivery.editorial.ingest.CHANNEL_PREFIX + CHANNEL))
    output = scheduler.dispatch_due_productions([case.profile], [connection], enqueue, now=NOW + 1)
    assert output['status'] == 'queued' and enqueue.call_count == 1
    job = json.loads(case.client.get(delivery.studio_state.JOB_PREFIX + output['task_id']))
    assert job['spec']['production_topic_index'] == 2 and job['spec']['topic'].startswith(case.topics[2])
    assert case.client.hget(case.state_key, 'cursor') == '3'
    assert scheduler.dispatch_due_productions([case.profile], [connection], enqueue, now=NOW + 2)['status'] == 'active'
    assert enqueue.call_count == 1


def test_real_shared_retry_claim_rejects_resolved_leaf_before_consuming_checkpoint(case):
    resolve(case)
    checkpoint_key = delivery.studio_state.REPAIR_CHECKPOINT_PREFIX + case.leaf
    case.client.set(checkpoint_key, raw({'approved_package': {'must': 'stay'}}))
    before = snapshot(case.client)
    with pytest.raises(ValueError, match='separately delivered'):
        delivery.studio_state.claim_retry_dispatch(case.leaf, str(uuid4()), 'new-claim-token-long-enough', allow_repair=True)
    assert snapshot(case.client) == before


def test_claim_winning_race_prevents_resolution_without_resetting_new_retry(case, monkeypatch):
    original = case.client.pipeline
    claimed = []
    def pipeline():
        pipe = original(); execute = pipe.execute
        def race(*args, **kwargs):
            claimed.append(delivery.studio_state.claim_retry_dispatch(case.leaf, str(uuid4()),
                'race-token-is-long-enough', allow_repair=False))
            return execute(*args, **kwargs)
        pipe.execute = race; return pipe
    monkeypatch.setattr(case.client, 'pipeline', pipeline)
    with pytest.raises(delivery.ExternalEpisodeDeliveryError): resolve(case)
    assert claimed[0]['claimed'] and not case.client.exists(case.receipt_key)
    assert case.client.hget(case.state_key, 'paused_reason') == 'previous_render_failed'


def test_lost_exec_response_remains_idempotent(case, monkeypatch):
    original = case.client.pipeline
    def pipeline():
        pipe = original(); execute = pipe.execute
        def uncertain(*args, **kwargs):
            execute(*args, **kwargs); raise OSError('lost transaction response')
        pipe.execute = uncertain; return pipe
    monkeypatch.setattr(case.client, 'pipeline', pipeline)
    with pytest.raises(delivery.ExternalEpisodeDeliveryError): resolve(case)
    monkeypatch.setattr(case.client, 'pipeline', original)
    before = snapshot(case.client)
    assert resolve(case)['status'] == 'already_resolved' and snapshot(case.client) == before


@pytest.mark.parametrize('field,value', [('cursor', '3'), ('cursor', '02'), ('cursor', '4'),
    ('paused_reason', 'different_pause'), ('last_result', 'SUCCESS'), ('active_task_id', str(uuid4())),
    ('last_task_id', str(uuid4())), ('consumed_prefix', 'wrong'), ('profile_revision', 'other-revision'),
    ('connection_id', 'other-connection'), ('dispatch_status', 'uncertain')])
def test_changed_pause_frozen_episode_or_nonterminal_state_rejects(case, field, value):
    case.client.hset(case.state_key, field, value); before = snapshot(case.client)
    with pytest.raises(delivery.ExternalEpisodeDeliveryError): resolve(case)
    assert snapshot(case.client) == before


@pytest.mark.parametrize('target,update', [
    ('source', lambda x: x['result']['youtube'].update(privacy_status='private')),
    ('source', lambda x: x['result']['youtube'].update(video_id='WrongVideo1')),
    ('source', lambda x: x['result']['youtube'].update(caption_uploaded=False)),
    ('source', lambda x: x['result']['youtube'].update(thumbnail_uploaded=False)),
    ('source', lambda x: x['result']['youtube'].update(contains_synthetic_media=False)),
    ('source', lambda x: x['result']['youtube'].update(series={'number': 3})),
    ('source', lambda x: x['spec'].update(language='tr')),
    ('source', lambda x: x['spec'].update(production_channel_id='UCotherchannel1234')),
    ('source', lambda x: x['result'].update(editorial_review_sha256='0' * 64)),
    ('publisher', lambda x: x.update(state='PROGRESS')),
    ('publisher', lambda x: x['result'].update(release_status='blocked')),
    ('publisher', lambda x: x['result'].update(thumbnail_uploaded=False)),
    ('publisher', lambda x: x['spec'].update(connection_id='other-connection')),
    ('ledger', lambda x: x.update(status='uncertain')),
    ('ledger', lambda x: x.update(release_side_effect_possible=False)),
    ('ledger', lambda x: x.update(release_error_code='unknown')),
    ('ledger', lambda x: x.update(requested_publish_at='2027-01-01T00:00:00Z')),
    ('ledger', lambda x: x['publish_plan']['series'].update(number=3)),
    ('root', lambda x: x['spec'].update(topic='A different frozen historical topic')),
    ('root', lambda x: x.update(retry_child_task_id=str(uuid4()))),
    ('leaf', lambda x: x.update(state='SUCCESS')),
    ('leaf', lambda x: x.update(parent_id=str(uuid4()))),
    ('leaf', lambda x: x.update(retry_claimed=True)),
    ('profile', lambda x: x.update(default_language='tr')),
    ('profile', lambda x: x.update(production_enabled=False)),
    ('profile', lambda x: x.update(profile_revision='other-revision')),
])
def test_forged_or_incomplete_delivery_never_changes_schedule(case, target, update):
    keys = {'source': delivery.studio_state.JOB_PREFIX + case.task, 'publisher': delivery.studio_state.JOB_PREFIX + case.publish_id,
        'ledger': delivery.UPLOAD_PREFIX + case.task, 'root': delivery.studio_state.JOB_PREFIX + case.original,
        'leaf': delivery.studio_state.JOB_PREFIX + case.leaf, 'profile': delivery.editorial.ingest.PROFILE_PREFIX + CHANNEL}
    change(case.client, keys[target], update); before = snapshot(case.client)
    with pytest.raises(delivery.ExternalEpisodeDeliveryError): resolve(case)
    assert snapshot(case.client) == before


@pytest.mark.parametrize('damage', ['epoch', 'credential', 'membership', 'claim', 'execution', 'leaf_upload', 'public_execution_lock',
                                    'publisher_lock', 'global_active', 'active_own_job', 'active_other_job', 'unknown_active_job', 'used_external'])
def test_auth_lineage_activity_and_unique_delivery_fences(case, damage):
    c = case; client = c.client
    if damage == 'epoch': client.set(delivery.editorial.ingest.AUTH_EPOCH_KEY, '16')
    elif damage == 'credential': client.set(delivery.editorial.ingest.CREDENTIAL_PREFIX + CHANNEL, 'changed-cipher')
    elif damage == 'membership': client.srem(delivery.editorial.ingest.CHANNEL_INDEX_KEY, CHANNEL)
    elif damage == 'claim': client.hset(delivery.studio_state.RETRY_CHILD_CLAIM_PREFIX + c.leaf, 'token', 'wrong')
    elif damage == 'execution': client.set(delivery.studio_state.RETRY_CHILD_EXECUTION_PREFIX + c.leaf, 'wrong')
    elif damage == 'leaf_upload': client.set(delivery.UPLOAD_PREFIX + c.leaf, raw({'status': 'uncertain'}))
    elif damage == 'publisher_lock': client.set(delivery.EXECUTION_LOCK_PREFIX + c.leaf, 'active')
    elif damage == 'public_execution_lock': client.set(delivery.EXECUTION_LOCK_PREFIX + c.task, 'active')
    elif damage == 'global_active': client.set(delivery.ACTIVE_KEY, raw({'channel_id': CHANNEL, 'task_id': c.leaf}))
    elif damage == 'used_external': client.set(delivery.EXTERNAL_INDEX_PREFIX + c.task, 'prior-resolution')
    else:
        task = str(uuid4()); spec = ({'production_channel_id': CHANNEL} if damage == 'active_own_job' else
                                   {'production_channel_id': 'UCotherchannel123'} if damage == 'active_other_job' else {})
        client.set(delivery.studio_state.JOB_PREFIX + task, raw({'task_id': task, 'state': 'PROGRESS', 'spec': spec}))
        client.zadd(delivery.studio_state.JOB_INDEX, {task: NOW})
    before = snapshot(client)
    with pytest.raises(delivery.ExternalEpisodeDeliveryError): resolve(c)
    assert snapshot(client) == before


def test_compact_publisher_redelivery_keeps_full_source_assets_authoritative(case):
    def compact(job):
        job['result']['idempotent_replay'] = True
        for k in ('profile_revision', 'caption_uploaded', 'thumbnail_uploaded', 'contains_synthetic_media', 'series'):
            job['result'].pop(k)
    change(case.client, delivery.studio_state.JOB_PREFIX + case.publish_id, compact)
    assert resolve(case)['status'] == 'resolved'


def test_operator_decision_requires_exact_frozen_topic_and_nonempty_explanation(case):
    for checksum, note in [('0' * 64, EXPLANATION), (delivery.topic_sha256(case.topics[1]), ''),
                           (delivery.topic_sha256(case.topics[1]), 'api_key=sk-secret-should-never-appear-in-error')]:
        with pytest.raises(delivery.ExternalEpisodeDeliveryError) as error:
            delivery.resolve_external_episode(CHANNEL, case.original, case.leaf, case.task, REVISION, checksum, note, now=NOW)
        assert 'secret' not in str(error.value) and not case.client.exists(case.receipt_key)


def test_current_cover_requires_upload_proof_not_a_fictitious_storage_thumbnail(case):
    assert case.plan['require_thumbnail'] is True and case.plan['thumbnail_key'] is None
    assert resolve(case)['status'] == 'resolved'


def test_common_retry_fence_is_included_in_full_rebuild_and_voice_scripts():
    from app.services import full_video_rebuild, voice_replacement
    prefix = delivery.LEAF_INDEX_PREFIX
    for script in (delivery.studio_state._CLAIM_RETRY_DISPATCH, full_video_rebuild._RESERVE, voice_replacement._RESERVE):
        assert prefix in script and script.index(prefix) < script.index("job['retry_claimed'] = true")
