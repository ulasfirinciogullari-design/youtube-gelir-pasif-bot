import ast
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import fakeredis
import pytest


ROOT = Path(__file__).resolve().parents[1]
CHANNEL = 'UC_channel_one'
REVISION = 'profile-revision-one'
CONNECTION_ID = 'connection-generation-one'
VIDEO_ID = 'Private1234'


def _digest(topics):
    return hashlib.sha256(json.dumps(topics, ensure_ascii=False).encode()).hexdigest()


@pytest.fixture
def recovery():
    # Match the existing scheduler tests: isolate config/Celery imports while
    # executing the real production module and real Lua against fakeredis.
    source = ROOT / 'app' / 'services' / 'production_recovery.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    tree.body = [node for node in tree.body if not (
        isinstance(node, ast.ImportFrom) and (node.module or '').startswith('app.')
    )]
    namespace = {
        'settings': SimpleNamespace(redis_url='redis://not-used'),
        'PRODUCTION_PREFIX': 'youtube_studio:production:v1:',
        'ACTIVE_KEY': 'youtube_studio:production:v1:active',
        'CHANNEL_STATE_PREFIX': 'youtube_studio:production:v1:channel:',
        'PROFILE_PREFIX': 'youtube_studio:youtube_profile:v1:',
        'OAUTH_CHANNEL_PREFIX': 'youtube_studio:oauth:channel:v3:',
        'OAUTH_CREDENTIAL_PREFIX': 'youtube_studio:oauth:credential:v3:',
        'OAUTH_CHANNEL_INDEX': 'youtube_studio:oauth:channels:v3',
        'JOB_PREFIX': 'youtube_studio:job:',
        'RETRY_CHILD_CLAIM_PREFIX': 'youtube_studio:retry_child_claim:',
        'RETRY_DISPATCH_PREFIX': 'youtube_studio:retry_dispatch:',
        'RENDER_CANCELLATION_PREFIX': 'youtube_studio:render_cancellation:v1:',
        'HOLD_PREFIX': 'youtube_studio:source_publication_hold:v1:',
        'UPLOAD_PREFIX': 'youtube_studio:youtube_upload:v2:',
        '_prefix_digest': _digest,
    }
    exec(compile(tree, str(source), 'exec'), namespace)
    client = fakeredis.FakeRedis(decode_responses=True)
    namespace['_redis'] = lambda: client
    return SimpleNamespace(**namespace), client


def _write(client, key, record):
    client.set(key, json.dumps(record, ensure_ascii=False), ex=90 * 86400)


def _change(client, key, change):
    value = json.loads(client.get(key))
    change(value)
    _write(client, key, value)


def _seed(module, client, hops=3):
    ids = [str(UUID(int=i + 1)) for i in range(hops + 1)]
    publish_id = str(UUID(int=1000))
    profile = {
        'channel_id': CHANNEL, 'profile_revision': REVISION,
        'auto_publish': True, 'production_enabled': True, 'release_mode': 'private',
        'production_topics': ['Banknot kâğıdı neden farklı?', 'Euro köprüleri gerçek mi?'],
        'production_interval_hours': 24, 'default_language': 'tr',
        'route_label': 'capital-corrupt', 'channel_identity': 'Paranın arka yüzü',
    }
    _write(client, module.PROFILE_PREFIX + CHANNEL, profile)
    _write(client, module.OAUTH_CHANNEL_PREFIX + CHANNEL,
           {'id': CHANNEL, 'connection_id': CONNECTION_ID})
    client.set(module.OAUTH_CREDENTIAL_PREFIX + CHANNEL, 'opaque-fixture-credential')
    client.sadd(module.OAUTH_CHANNEL_INDEX, CHANNEL)
    state_key = module.CHANNEL_STATE_PREFIX + CHANNEL
    client.hset(state_key, mapping={
        'cursor': '1', 'consumed_prefix': _digest(profile['production_topics'][:1]),
        'next_due': '86401', 'last_task_id': ids[0], 'last_result': 'FAILURE',
        'dispatch_status': 'finished', 'paused_reason': 'previous_render_failed',
        'profile_revision': REVISION, 'connection_id': CONNECTION_ID,
    })
    spec = {
        'topic': profile['production_topics'][0] + '\n\nChannel editorial direction: Paranın arka yüzü',
        'duration_minutes': 0.5, 'language': 'tr', 'channel_id': 'capital-corrupt',
        'mode': 'production', 'format': 'shorts', 'workflow': 'auto',
        'content_style': 'documentary', 'pace': 'balanced', 'visual_mix': 'real_first',
        'music': 'off', 'subtitles': 'sidecar', 'quality_threshold': 86,
        'publish_after_render': True, 'production_scheduled': True,
        'production_channel_id': CHANNEL, 'production_connection_id': CONNECTION_ID,
        'production_profile_revision': REVISION, 'production_topic_index': 0,
    }
    binding = {'target_channel_id': CHANNEL, 'connection_id': CONNECTION_ID,
               'profile_revision': REVISION}
    private = {'privacy_status': 'private', 'release_status': 'private',
               'release_error_code': None, 'scheduled_publish_at': None}
    for index, task_id in enumerate(ids):
        final = index == len(ids) - 1
        task_spec = dict(spec)
        if index:
            task_spec.update(workflow='scene_repair', repair_source_task_id=ids[index - 1])
        record = {
            'task_id': task_id, 'kind': 'render', 'parent_id': ids[index - 1] if index else None,
            'spec': task_spec, 'state': 'SUCCESS' if final else 'FAILURE',
            'result': None, 'error': None if final else 'audio_duration',
        }
        if not final:
            record.update(retry_child_task_id=ids[index + 1], retry_claimed=True,
                          retry_dispatch_state='dispatched')
            token = 'test-claim-' + str(index)
            client.hset(module.RETRY_DISPATCH_PREFIX + task_id, mapping={
                'token': token, 'child_task_id': ids[index + 1], 'mode': 'repair',
                'state': 'dispatched', 'created_at': '2026-09-05T00:00:00Z',
            })
            client.hset(module.RETRY_CHILD_CLAIM_PREFIX + ids[index + 1], mapping={
                'token': token, 'source_task_id': task_id,
            })
        else:
            record['result'] = {
                'video_key': 'videos/final.mp4', 'quality_disposition': 'automated_qc_pass',
                'manual_qa_required': False,
                'youtube_automation': {'status': 'queued', 'publish_task_id': publish_id,
                                       **binding, 'release_mode': 'private'},
                'youtube': {'video_id': VIDEO_ID, **private, **binding},
            }
        _write(client, module.JOB_PREFIX + task_id, record)
        client.set('youtube_studio:paid_create_budget:' + task_id, 'opaque-existing-paid-ledger')
    _write(client, module.JOB_PREFIX + publish_id, {
        'task_id': publish_id, 'kind': 'publish', 'state': 'SUCCESS', 'parent_id': ids[-1],
        'spec': {'source_task_id': ids[-1], 'mode': 'autonomous_publish',
                 'privacy_status': 'private', 'release_mode': 'private', **binding},
        'result': {'status': 'complete', 'source_task_id': ids[-1],
                   'youtube_video_id': VIDEO_ID, **binding, **private},
    })
    _write(client, module.UPLOAD_PREFIX + ids[-1], {
        'version': 2, 'source_task_id': ids[-1], 'publish_task_id': publish_id,
        'status': 'complete', 'side_effect_possible': True, 'youtube_video_id': VIDEO_ID,
        'requested_release_mode': 'private', 'release_status': 'private',
        'release_side_effect_possible': False, 'requested_publish_at': None,
        'target_channel_id': CHANNEL, 'connection_id': CONNECTION_ID,
        'publish_plan': {'source_task_id': ids[-1], 'target_channel_id': CHANNEL,
                         'release_mode': 'private', 'profile_revision': REVISION, 'publish_at': None},
    })
    client.set('youtube_studio:youtube_series:v1:fixture', 'existing-series-ledger')
    return SimpleNamespace(
        ids=ids, original_id=ids[0], recovered_id=ids[-1], publish_id=publish_id,
        state_key=state_key, audit_key=module.RESUME_PREFIX + CHANNEL + ':' + ids[0],
        profile=profile,
    )


@pytest.fixture
def case(recovery):
    module, client = recovery
    return module, client, _seed(module, client)


def _resume(module, case, now=100_000):
    return module.resume_after_private_retry(CHANNEL, case.original_id, case.recovered_id,
                                            REVISION, now=now)


def _snapshot(client):
    result = {}
    for key in client.keys('*'):
        kind = client.type(key)
        if kind == 'string':
            result[key] = client.get(key)
        elif kind == 'hash':
            result[key] = client.hgetall(key)
        else:
            result[key] = client.smembers(key)
    return result


def _assert_still_paused(module, client, case):
    assert not client.exists(case.audit_key)
    assert client.hget(case.state_key, 'paused_reason')


def test_four_job_private_recovery_preserves_every_ledger_and_failure(case):
    module, client, data = case
    before = _snapshot(client)
    result = _resume(module, data)
    assert result['status'] == 'resumed'
    assert result['next_due'] == 186_400
    assert result['cursor'] == 1
    assert result['youtube_video_id'] == VIDEO_ID
    expected = before[data.state_key].copy()
    del expected['paused_reason']
    expected['next_due'] = '186400'
    after = _snapshot(client)
    assert after.pop(data.audit_key) == json.dumps({k: v for k, v in result.items() if k != 'status'},
                                                   ensure_ascii=False)
    assert after.pop(data.state_key) == expected
    before.pop(data.state_key)
    assert after == before
    assert client.ttl(data.audit_key) == -1
    assert client.ttl(data.state_key) == -1


def test_repeat_is_idempotent_and_does_not_delay_again_or_touch_new_activity(case):
    module, client, data = case
    first = _resume(module, data)
    client.set(module.ACTIVE_KEY, 'later-legitimate-active-claim')
    client.hset(data.state_key, mapping={'cursor': '2', 'last_task_id': 'later-job'})
    before = _snapshot(client)
    second = _resume(module, data, now=200_000)
    assert second['status'] == 'already_resumed'
    assert second['next_due'] == first['next_due']
    assert second['resumed_at'] == first['resumed_at']
    assert _snapshot(client) == before


def test_concurrent_requests_commit_once(case):
    module, client, data = case
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: _resume(module, data), range(6)))
    assert [result['status'] for result in results].count('resumed') == 1
    assert {result['next_due'] for result in results} == {186_400}


def test_concurrent_winner_during_preread_is_returned_idempotently(case, monkeypatch):
    module, client, data = case
    actual_hgetall = client.hgetall
    race_once = True

    def finish_before_state_read(key):
        nonlocal race_once
        if key == data.state_key and race_once:
            race_once = False
            assert _resume(module, data)['status'] == 'resumed'
        return actual_hgetall(key)

    monkeypatch.setattr(client, 'hgetall', finish_before_state_read)
    assert _resume(module, data, now=200_000)['status'] == 'already_resumed'
    assert client.hget(data.state_key, 'next_due') == '186400'


def test_accepted_eval_with_lost_reply_can_be_repeated_safely(case, monkeypatch):
    module, client, data = case
    actual_eval = client.eval

    def lost_reply(*args, **kwargs):
        actual_eval(*args, **kwargs)
        raise ConnectionError('opaque transport detail must not appear')

    monkeypatch.setattr(client, 'eval', lost_reply)
    with pytest.raises(module.ProductionRecoveryError, match='^recovery_state_unavailable$'):
        _resume(module, data)
    monkeypatch.setattr(client, 'eval', actual_eval)
    assert _resume(module, data, now=300_000)['status'] == 'already_resumed'
    assert client.hget(data.state_key, 'next_due') == '186400'


def test_conflicting_recovery_cannot_replace_audit(case):
    module, client, data = case
    _resume(module, data)
    with pytest.raises(module.ProductionRecoveryError, match='recovery_audit_conflict'):
        module.resume_after_private_retry(CHANNEL, data.original_id, str(UUID(int=2000)),
                                          REVISION, now=300_000)
    assert client.hget(data.state_key, 'next_due') == '186400'


@pytest.mark.parametrize('race', [
    'profile', 'connection', 'credential', 'membership', 'source', 'publisher', 'upload',
    'parent', 'claim', 'dispatch', 'ancestor_upload', 'cursor', 'pause', 'active',
])
def test_changes_between_validation_and_atomic_commit_fail_closed(case, monkeypatch, race):
    module, client, data = case
    actual_eval = client.eval

    def raced(*args, **kwargs):
        if race == 'profile':
            _change(client, module.PROFILE_PREFIX + CHANNEL, lambda r: r.update(profile_revision='new'))
        elif race == 'connection':
            _change(client, module.OAUTH_CHANNEL_PREFIX + CHANNEL, lambda r: r.update(connection_id='new'))
        elif race == 'credential':
            client.delete(module.OAUTH_CREDENTIAL_PREFIX + CHANNEL)
        elif race == 'membership':
            client.srem(module.OAUTH_CHANNEL_INDEX, CHANNEL)
        elif race == 'source':
            _change(client, module.JOB_PREFIX + data.recovered_id,
                    lambda r: r['result'].update(manual_qa_required=True))
        elif race == 'publisher':
            _change(client, module.JOB_PREFIX + data.publish_id, lambda r: r.update(state='FAILURE'))
        elif race == 'upload':
            _change(client, module.UPLOAD_PREFIX + data.recovered_id,
                    lambda r: r.update(release_status='public'))
        elif race == 'parent':
            _change(client, module.JOB_PREFIX + data.original_id,
                    lambda r: r.update(retry_child_task_id=str(UUID(int=9000))))
        elif race == 'claim':
            client.hset(module.RETRY_CHILD_CLAIM_PREFIX + data.recovered_id, 'token', 'changed')
        elif race == 'dispatch':
            client.hset(module.RETRY_DISPATCH_PREFIX + data.original_id, 'state', 'uncertain')
        elif race == 'ancestor_upload':
            _write(client, module.UPLOAD_PREFIX + data.original_id, {'status': 'uploading'})
        elif race == 'cursor':
            client.hset(data.state_key, 'cursor', '2')
        elif race == 'pause':
            client.hset(data.state_key, 'paused_reason', 'consumed_topics_changed')
        elif race == 'active':
            client.set(module.ACTIVE_KEY, 'another-active-job')
        return actual_eval(*args, **kwargs)

    monkeypatch.setattr(client, 'eval', raced)
    with pytest.raises(module.ProductionRecoveryError, match='recovery_(state_changed|connection_missing)'):
        _resume(module, data)
    _assert_still_paused(module, client, data)
    assert client.hget(data.state_key, 'next_due') == '86401'
    if race == 'active':
        assert client.get(module.ACTIVE_KEY) == 'another-active-job'


@pytest.mark.parametrize('field,value', [
    ('production_enabled', False), ('auto_publish', False), ('release_mode', 'public'),
    ('release_mode', 'scheduled'), ('profile_revision', 'new-revision'),
    ('production_interval_hours', 5), ('production_interval_hours', True),
    ('production_interval_hours', 169), ('production_topics', ['Different topic']),
])
def test_ineligible_or_changed_profile_stays_paused(case, field, value):
    module, client, data = case
    _change(client, module.PROFILE_PREFIX + CHANNEL, lambda r: r.update({field: value}))
    with pytest.raises(module.ProductionRecoveryError):
        _resume(module, data)
    _assert_still_paused(module, client, data)


@pytest.mark.parametrize('field,value', [
    ('paused_reason', 'previous_publication_blocked'), ('paused_reason', 'previous_render_needs_review'),
    ('paused_reason', 'consumed_topics_changed'), ('dispatch_status', 'uncertain'),
    ('cursor', '01'), ('cursor', '0'), ('cursor', '3'), ('cursor', 'invalid'),
    ('consumed_prefix', 'changed'), ('last_result', 'SUCCESS'), ('active_task_id', 'another-task'),
    ('next_due', 'NaN'), ('connection_id', 'another-generation'),
])
def test_unrelated_or_corrupt_scheduler_state_stays_paused(case, field, value):
    module, client, data = case
    client.hset(data.state_key, field, value)
    with pytest.raises(module.ProductionRecoveryError):
        _resume(module, data)
    _assert_still_paused(module, client, data)


@pytest.mark.parametrize('damage', [
    'missing_parent', 'cycle', 'wrong_child', 'missing_claim', 'token_mismatch',
    'ancestor_pending', 'final_pending', 'final_failed', 'final_has_child', 'topic',
    'language', 'duration', 'profile', 'channel', 'scheduled_false', 'manual', 'qa_missing',
    'video_missing', 'original_has_parent',
])
def test_invalid_or_incomplete_lineage_stays_paused(case, damage):
    module, client, data = case
    final_key = module.JOB_PREFIX + data.recovered_id
    original_key = module.JOB_PREFIX + data.original_id
    if damage == 'missing_parent':
        client.delete(module.JOB_PREFIX + data.ids[1])
    elif damage == 'cycle':
        _change(client, final_key, lambda r: r.update(parent_id=data.recovered_id))
    elif damage == 'wrong_child':
        _change(client, original_key, lambda r: r.update(retry_child_task_id=data.recovered_id))
    elif damage == 'missing_claim':
        client.delete(module.RETRY_CHILD_CLAIM_PREFIX + data.recovered_id)
    elif damage == 'token_mismatch':
        client.hset(module.RETRY_CHILD_CLAIM_PREFIX + data.recovered_id, 'token', 'wrong')
    elif damage == 'ancestor_pending':
        _change(client, original_key, lambda r: r.update(state='PENDING'))
    elif damage in {'final_pending', 'final_failed'}:
        _change(client, final_key, lambda r: r.update(state='PENDING' if damage == 'final_pending' else 'FAILURE'))
    elif damage == 'final_has_child':
        _change(client, final_key, lambda r: r.update(retry_child_task_id=str(UUID(int=999))))
    elif damage == 'original_has_parent':
        _change(client, original_key, lambda r: r.update(parent_id=str(UUID(int=999))))
    elif damage in {'manual', 'qa_missing', 'video_missing'}:
        key, value = {'manual': ('manual_qa_required', True), 'qa_missing': ('quality_disposition', None),
                      'video_missing': ('video_key', None)}[damage]
        _change(client, final_key, lambda r: r['result'].update({key: value}))
    else:
        key, value = {
            'topic': ('topic', 'Unrelated topic'), 'language': ('language', 'en'),
            'duration': ('duration_minutes', 1), 'profile': ('production_profile_revision', 'new'),
            'channel': ('production_channel_id', 'UC_other_channel'),
            'scheduled_false': ('production_scheduled', False),
        }[damage]
        _change(client, final_key, lambda r: r['spec'].update({key: value}))
    with pytest.raises(module.ProductionRecoveryError):
        _resume(module, data)
    _assert_still_paused(module, client, data)


@pytest.mark.parametrize('damage', [
    'queue_error', 'publisher_missing', 'publisher_pending', 'publisher_failed', 'wrong_parent',
    'wrong_source', 'wrong_video', 'wrong_channel', 'wrong_connection', 'wrong_revision',
    'public', 'scheduled', 'uncertain', 'attribution_missing', 'upload_missing', 'upload_corrupt',
    'upload_wrong_source', 'upload_wrong_child', 'upload_wrong_video', 'upload_uncertain',
    'upload_public', 'release_side_effect', 'plan_public', 'plan_source', 'plan_revision',
])
def test_no_resume_without_matching_completed_private_publication(case, damage):
    module, client, data = case
    final_key = module.JOB_PREFIX + data.recovered_id
    publish_key = module.JOB_PREFIX + data.publish_id
    upload_key = module.UPLOAD_PREFIX + data.recovered_id
    if damage == 'queue_error':
        _change(client, final_key, lambda r: r['result']['youtube_automation'].update(status='queue_error'))
    elif damage == 'publisher_missing':
        client.delete(publish_key)
    elif damage in {'publisher_pending', 'publisher_failed'}:
        _change(client, publish_key, lambda r: r.update(state='PENDING' if damage == 'publisher_pending' else 'FAILURE'))
    elif damage == 'wrong_parent':
        _change(client, publish_key, lambda r: r.update(parent_id=data.original_id))
    elif damage in {'wrong_source', 'wrong_video', 'wrong_channel', 'wrong_connection', 'wrong_revision',
                    'public', 'scheduled', 'uncertain'}:
        key, value = {
            'wrong_source': ('source_task_id', data.original_id), 'wrong_video': ('youtube_video_id', 'OtherVideo0'),
            'wrong_channel': ('target_channel_id', 'UC_other_channel'),
            'wrong_connection': ('connection_id', 'different-generation'),
            'wrong_revision': ('profile_revision', 'different-revision'),
            'public': ('privacy_status', 'public'), 'scheduled': ('release_status', 'scheduled'),
            'uncertain': ('release_status', 'uncertain'),
        }[damage]
        _change(client, publish_key, lambda r: r['result'].update({key: value}))
    elif damage == 'attribution_missing':
        _change(client, final_key, lambda r: r['result'].pop('youtube'))
    elif damage == 'upload_missing':
        client.delete(upload_key)
    elif damage == 'upload_corrupt':
        client.set(upload_key, '{broken')
    elif damage.startswith('plan_'):
        key, value = {'plan_public': ('release_mode', 'public'), 'plan_source': ('source_task_id', data.original_id),
                      'plan_revision': ('profile_revision', 'new-revision')}[damage]
        _change(client, upload_key, lambda r: r['publish_plan'].update({key: value}))
    else:
        key, value = {
            'upload_wrong_source': ('source_task_id', data.original_id),
            'upload_wrong_child': ('publish_task_id', data.original_id),
            'upload_wrong_video': ('youtube_video_id', 'OtherVideo0'),
            'upload_uncertain': ('status', 'uncertain'), 'upload_public': ('requested_release_mode', 'public'),
            'release_side_effect': ('release_side_effect_possible', True),
        }[damage]
        _change(client, upload_key, lambda r: r.update({key: value}))
    with pytest.raises(module.ProductionRecoveryError):
        _resume(module, data)
    _assert_still_paused(module, client, data)


@pytest.mark.parametrize('status', ['reserved', 'uploading', 'uncertain', 'complete'])
def test_any_ancestor_with_possible_upload_blocks_recovery(case, status):
    module, client, data = case
    _write(client, module.UPLOAD_PREFIX + data.ids[1], {
        'source_task_id': data.ids[1], 'status': status, 'side_effect_possible': status != 'reserved',
    })
    with pytest.raises(module.ProductionRecoveryError, match='recovery_ancestor_upload_ambiguous'):
        _resume(module, data)
    _assert_still_paused(module, client, data)


def test_proven_preflight_failure_on_ancestor_does_not_block_or_get_deleted(case):
    module, client, data = case
    key = module.UPLOAD_PREFIX + data.original_id
    _write(client, key, {'source_task_id': data.original_id, 'status': 'failed_preflight',
                         'side_effect_possible': False})
    prior = client.get(key)
    assert _resume(module, data)['status'] == 'resumed'
    assert client.get(key) == prior


@pytest.mark.parametrize('hops,accepted', [(1, True), (16, True), (17, False)])
def test_retry_lineage_bound(recovery, hops, accepted):
    module, client = recovery
    data = _seed(module, client, hops=hops)
    if accepted:
        assert _resume(module, data)['status'] == 'resumed'
    else:
        with pytest.raises(module.ProductionRecoveryError, match='recovery_lineage_too_deep'):
            _resume(module, data)
        _assert_still_paused(module, client, data)


def test_does_not_advance_an_existing_later_due_date(case):
    module, client, data = case
    client.hset(data.state_key, 'next_due', '500000')
    assert _resume(module, data)['next_due'] == 500_000


def test_real_scheduler_reserves_only_next_topic_after_resumed_due_time(case):
    module, client, data = case
    source = ROOT / 'app' / 'services' / 'channel_production.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    tree.body = [node for node in tree.body if not (
        isinstance(node, ast.ImportFrom) and (node.module or '').startswith('app.')
    )]
    namespace = {
        'settings': SimpleNamespace(redis_url='redis://not-used'),
        'JOB_PREFIX': module.JOB_PREFIX, 'JOB_INDEX': 'youtube_studio:jobs',
        'JOB_TTL_SECONDS': 90 * 86400,
    }
    exec(compile(tree, str(source), 'exec'), namespace)
    namespace['_redis'] = lambda: client
    connection = {'id': CHANNEL, 'connection_id': CONNECTION_ID}
    reserve = namespace['reserve_due_production']
    assert reserve(data.profile, connection, now=200_000)['status'] == 'paused'
    result = _resume(module, data, now=200_000.1234567)
    due = result['next_due']
    assert float(client.hget(data.state_key, 'next_due')) == due
    assert reserve(data.profile, connection, now=due - 0.001)['status'] == 'not_due'
    assert client.hget(data.state_key, 'cursor') == '1'
    reserved = reserve(data.profile, connection, now=due)
    assert reserved['status'] == 'reserved'
    assert reserved['task_id'] not in data.ids
    job = json.loads(client.get(module.JOB_PREFIX + reserved['task_id']))
    assert job['spec']['production_topic_index'] == 1
    assert job['spec']['topic'].startswith(data.profile['production_topics'][1])
    assert job['spec']['production_profile_revision'] == REVISION
    assert job['spec']['publish_after_render'] is True
    assert client.hget(data.state_key, 'cursor') == '2'
    assert client.get(data.audit_key)


@pytest.mark.parametrize('bad_time', [True, -1, float('inf'), float('nan'), 'now'])
def test_invalid_clock_never_writes(case, bad_time):
    module, client, data = case
    with pytest.raises(module.ProductionRecoveryError, match='recovery_time_invalid'):
        _resume(module, data, now=bad_time)
    _assert_still_paused(module, client, data)
