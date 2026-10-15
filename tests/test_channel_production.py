import ast
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import fakeredis
import pytest


ROOT = Path(__file__).resolve().parents[1]
CHANNEL = 'UC_channel_one'
CONNECTION = {'id': CHANNEL, 'connection_id': 'connection-generation-one'}


@pytest.fixture
def production():
    source = ROOT / 'app' / 'services' / 'channel_production.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    tree.body = [node for node in tree.body if not (
        isinstance(node, ast.ImportFrom)
        and node.module in {'app.config', 'app.services.studio_state'}
    )]
    namespace = {
        'settings': SimpleNamespace(redis_url='redis://not-used'),
        'JOB_PREFIX': 'youtube_studio:job:',
        'JOB_INDEX': 'youtube_studio:jobs',
        'JOB_TTL_SECONDS': 90 * 24 * 3600,
    }
    exec(compile(tree, str(source), 'exec'), namespace)
    client = fakeredis.FakeRedis(decode_responses=True)
    namespace['_redis'] = lambda: client
    return SimpleNamespace(**namespace), client


def _profile(**overrides):
    return {
        'channel_id': CHANNEL, 'profile_revision': 'profile-revision-1',
        'channel_identity': 'Kısa Türkçe bilim hikâyeleri',
        'route_label': 'bilim-tr', 'default_language': 'tr',
        'auto_publish': True, 'production_enabled': True,
        'production_topics': ['Silgi grafiti nasıl toplar?', 'Kurşun kalem neden yazar?'],
        'production_interval_hours': 24, **overrides,
    }


def _save(module, client, profile, connection=CONNECTION):
    client.set(module.PROFILE_PREFIX + profile['channel_id'], json.dumps(profile))
    client.set(module.OAUTH_CHANNEL_PREFIX + connection['id'], json.dumps(connection))
    client.set(module.OAUTH_CREDENTIAL_PREFIX + connection['id'], 'opaque-test-credential')
    client.sadd(module.OAUTH_CHANNEL_INDEX, connection['id'])


def _finish(module, client, task_id, *, state='SUCCESS', manual=False, delivered=True):
    key = module.JOB_PREFIX + task_id
    job = json.loads(client.get(key))
    job.update(state=state, result={
        'video_key': 'videos/final.mp4',
        'quality_disposition': 'manual_qa_preview' if manual else 'automated_qc_pass',
        'manual_qa_required': manual,
    })
    if state == 'SUCCESS' and not manual and delivered:
        child_id = '22222222-2222-4222-8222-' + task_id[-12:]
        job['result']['youtube_automation'] = {'status': 'queued', 'publish_task_id': child_id}
        client.set(module.JOB_PREFIX + child_id, json.dumps({
            'task_id': child_id, 'kind': 'publish', 'parent_id': task_id,
            'spec': {'source_task_id': task_id}, 'state': 'SUCCESS',
            'result': {'source_task_id': task_id, 'youtube_video_id': 'video-id-confirmed',
                       'release_status': 'private'},
        }))
    client.set(key, json.dumps(job))


def test_due_tick_reserves_registry_before_enqueue_and_uses_channel_brief(production):
    module, client = production
    profile = _profile()
    _save(module, client, profile)
    calls = []

    def enqueue(*, args, task_id):
        job = json.loads(client.get(module.JOB_PREFIX + task_id))
        assert job['state'] == 'PENDING'
        assert client.get(module.ACTIVE_KEY)
        calls.append((args, task_id))

    result = module.dispatch_due_productions([profile], [CONNECTION], enqueue, now=1000)
    assert result['status'] == 'queued'
    args, task_id = calls[0]
    assert profile['production_topics'][0] in args[0]
    assert profile['channel_identity'] in args[0]
    assert args[1:4] == (0.5, 'tr', 'bilim-tr')
    assert args[4]['format'] == 'shorts'
    assert args[4]['mode'] == 'production'
    assert args[4]['publish_after_render'] is True
    assert args[4]['production_scheduled'] is True
    assert args[4]['production_channel_id'] == CHANNEL
    assert args[4]['production_connection_id'] == CONNECTION['connection_id']
    assert args[4]['production_profile_revision'] == 'profile-revision-1'
    assert args[4]['visual_mix'] == 'real_first'
    state = module.get_production_state(CHANNEL)
    assert state['cursor'] == '1'
    assert float(state['next_due']) == 1000 + 24 * 3600
    assert state['dispatch_status'] == 'enqueued'
    assert module.dispatch_due_productions([profile], [CONNECTION], enqueue, now=2000)['status'] == 'active'
    assert len(calls) == 1


def test_managed_daily_ceiling_is_enforced_inside_original_atomic_dispatch(production):
    from app.services import channel_cadence as cadence
    from uuid import uuid4
    module, client = production
    channel = 'UC5v9AvNtD3PTLgo6m1jROOA'
    connection = {**CONNECTION, 'id': channel}
    profile = _profile(channel_id=channel)
    _save(module, client, profile, connection)
    produced = cadence.keys(channel, now=1000)[0]
    client.hset(produced, mapping={**{str(uuid4()): 'shorts' for _ in range(5)}, str(uuid4()): 'long'})
    original = client.hgetall(produced)
    enqueue = Mock()
    result = module.dispatch_due_productions([profile], [connection], enqueue, now=1000)
    assert result['channels'][channel] == 'daily_limit_wait'
    enqueue.assert_not_called()
    assert client.hgetall(produced) == original
    assert client.hgetall(module.CHANNEL_STATE_PREFIX + channel) == {}
    assert not list(client.scan_iter(match=module.JOB_PREFIX + '*'))


def test_concurrent_beats_across_three_channels_enqueue_at_most_two_global_renders(production):
    module, client = production
    profiles = [_profile(), _profile(channel_id='UC_channel_two'), _profile(channel_id='UC_channel_three')]
    connections = [CONNECTION, {'id': 'UC_channel_two', 'connection_id': 'connection-generation-two'},
                   {'id': 'UC_channel_three', 'connection_id': 'connection-generation-three'}]
    for profile, connection in zip(profiles, connections):
        _save(module, client, profile, connection)
    enqueue = Mock()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: module.dispatch_due_productions(
            profiles, connections, enqueue, now=1000,
        ), range(12)))
    assert sum(result.get('queued_count', 0) for result in results) == 2
    assert enqueue.call_count == 2
    assert len({item.kwargs['task_id'] for item in enqueue.call_args_list}) == 2
    assert sum(int(module.get_production_state(profile['channel_id']).get('cursor', 0)) for profile in profiles) == 2


def test_success_advances_ordered_topics_only_after_interval_then_stops(production):
    module, client = production
    profile = _profile()
    _save(module, client, profile)
    enqueue = Mock()
    first = module.dispatch_due_productions([profile], [CONNECTION], enqueue, now=1000)
    _finish(module, client, first['task_id'])
    early = module.dispatch_due_productions([profile], [CONNECTION], enqueue, now=2000)
    assert early['channels'][CHANNEL] == 'not_due'
    second = module.dispatch_due_productions([profile], [CONNECTION], enqueue, now=1000 + 86400)
    assert second['status'] == 'queued'
    assert first['task_id'] != second['task_id']
    assert profile['production_topics'][1] in enqueue.call_args.kwargs['args'][0]
    _finish(module, client, second['task_id'])
    exhausted = module.dispatch_due_productions([profile], [CONNECTION], enqueue, now=1000 + 172800)
    assert exhausted['channels'][CHANNEL] == 'topics_exhausted'
    assert enqueue.call_count == 2


@pytest.mark.parametrize('publication_status', ['queued', 'reserved', 'uploading', 'complete'])
def test_render_success_waits_for_publication_dispatch_and_child_completion(production, publication_status):
    module, client = production
    profile = _profile()
    _save(module, client, profile)
    enqueue = Mock()
    first = module.dispatch_due_productions([profile], [CONNECTION], enqueue, now=1000)
    _finish(module, client, first['task_id'], delivered=False)
    assert module.dispatch_due_productions([profile], [CONNECTION], enqueue, now=90000)['status'] == 'active'
    _finish(module, client, first['task_id'])
    job = json.loads(client.get(module.JOB_PREFIX + first['task_id']))
    job['result']['youtube_automation']['status'] = publication_status
    client.set(module.JOB_PREFIX + first['task_id'], json.dumps(job))
    child_key = module.JOB_PREFIX + job['result']['youtube_automation']['publish_task_id']
    child = json.loads(client.get(child_key))
    child['state'] = 'STARTED'
    client.set(child_key, json.dumps(child))
    assert module.dispatch_due_productions([profile], [CONNECTION], enqueue, now=90000)['status'] == 'active'
    child['state'] = 'SUCCESS'
    client.set(child_key, json.dumps(child))
    assert module.dispatch_due_productions([profile], [CONNECTION], enqueue, now=90000)['status'] == 'queued'
    assert enqueue.call_count == 2


@pytest.mark.parametrize('status', ['queue_error', 'queue_blocked', 'no_unique_route', 'connection_changed', 'metadata_blocked'])
def test_publication_route_or_queue_failure_pauses_without_next_paid_render(production, status):
    module, client = production
    profile = _profile()
    _save(module, client, profile)
    enqueue = Mock()
    first = module.dispatch_due_productions([profile], [CONNECTION], enqueue, now=1000)
    _finish(module, client, first['task_id'], delivered=False)
    key = module.JOB_PREFIX + first['task_id']
    job = json.loads(client.get(key))
    job['result']['youtube_automation'] = {'status': status}
    client.set(key, json.dumps(job))
    result = module.dispatch_due_productions([profile], [CONNECTION], enqueue, now=90000)
    assert result['channels'][CHANNEL] == 'paused'
    assert module.get_production_state(CHANNEL)['paused_reason'] == 'previous_publication_blocked'
    assert enqueue.call_count == 1


@pytest.mark.parametrize('failure', ['FAILURE', 'blocked', 'uncertain'])
def test_failed_publish_child_or_release_pauses_channel(production, failure):
    module, client = production
    profile = _profile()
    _save(module, client, profile)
    enqueue = Mock()
    first = module.dispatch_due_productions([profile], [CONNECTION], enqueue, now=1000)
    _finish(module, client, first['task_id'])
    job = json.loads(client.get(module.JOB_PREFIX + first['task_id']))
    child_key = module.JOB_PREFIX + job['result']['youtube_automation']['publish_task_id']
    child = json.loads(client.get(child_key))
    if failure == 'FAILURE':
        child['state'] = 'FAILURE'
    else:
        child['result']['release_status'] = failure
    client.set(child_key, json.dumps(child))
    result = module.dispatch_due_productions([profile], [CONNECTION], enqueue, now=90000)
    assert result['channels'][CHANNEL] == 'paused'
    assert module.get_production_state(CHANNEL)['paused_reason'] == 'previous_publication_blocked'
    assert enqueue.call_count == 1


@pytest.mark.parametrize('problem', ['missing', 'wrong_parent', 'wrong_source', 'missing_result'])
def test_missing_or_unbound_publish_child_keeps_global_claim(production, problem):
    module, client = production
    profile = _profile()
    _save(module, client, profile)
    enqueue = Mock()
    first = module.dispatch_due_productions([profile], [CONNECTION], enqueue, now=1000)
    _finish(module, client, first['task_id'])
    job = json.loads(client.get(module.JOB_PREFIX + first['task_id']))
    child_key = module.JOB_PREFIX + job['result']['youtube_automation']['publish_task_id']
    child = json.loads(client.get(child_key))
    if problem == 'missing':
        client.delete(child_key)
    else:
        if problem == 'wrong_parent':
            child['parent_id'] = 'some-other-source'
        elif problem == 'wrong_source':
            child['spec']['source_task_id'] = 'some-other-source'
        else:
            child['result'] = None
        client.set(child_key, json.dumps(child))
    assert module.dispatch_due_productions([profile], [CONNECTION], enqueue, now=90000)['status'] == 'state_unavailable'
    assert client.get(module.ACTIVE_KEY) is not None
    assert enqueue.call_count == 1


@pytest.mark.parametrize('state,manual,reason', [
    ('FAILURE', False, 'previous_render_failed'),
    ('SUCCESS', True, 'previous_render_needs_review'),
])
def test_failure_or_manual_qa_pauses_only_its_channel(production, state, manual, reason):
    module, client = production
    profile = _profile()
    _save(module, client, profile)
    enqueue = Mock()
    result = module.dispatch_due_productions([profile], [CONNECTION], enqueue, now=1000)
    _finish(module, client, result['task_id'], state=state, manual=manual)
    paused = module.dispatch_due_productions([profile], [CONNECTION], enqueue, now=90000)
    assert paused['channels'][CHANNEL] == 'paused'
    assert module.get_production_state(CHANNEL)['paused_reason'] == reason
    assert client.get(module.ACTIVE_KEY) is None
    assert enqueue.call_count == 1


def test_missing_connection_and_disabled_profile_never_reserve(production):
    module, client = production
    profile = _profile()
    _save(module, client, profile)
    enqueue = Mock()
    assert module.dispatch_due_productions([profile], [], enqueue, now=1000)['channels'][CHANNEL] == 'connection_missing'
    assert module.reserve_due_production(_profile(production_enabled=False), CONNECTION)['status'] == 'disabled'
    assert module.reserve_due_production(_profile(auto_publish=False), CONNECTION)['status'] == 'disabled'
    client.delete(module.OAUTH_CREDENTIAL_PREFIX + CHANNEL)
    assert module.dispatch_due_productions([profile], [CONNECTION], enqueue, now=1000)['channels'][CHANNEL] == 'connection_missing'
    enqueue.assert_not_called()
    assert not module.get_production_state(CHANNEL)


def test_oauth_reconnect_between_snapshot_and_reservation_fails_closed(production):
    module, client = production
    profile = _profile()
    _save(module, client, profile, {**CONNECTION, 'connection_id': 'new-connection-generation'})
    result = module.reserve_due_production(profile, CONNECTION, now=1000)
    assert result['status'] == 'connection_missing'
    assert not module.get_production_state(CHANNEL)


def test_lost_enqueue_reply_keeps_claim_and_never_resubmits(production):
    module, client = production
    profile = _profile()
    _save(module, client, profile)
    enqueue = Mock(side_effect=ConnectionError('reply lost'))
    result = module.dispatch_due_productions([profile], [CONNECTION], enqueue, now=1000)
    assert result['status'] == 'dispatch_uncertain'
    assert module.get_production_state(CHANNEL)['dispatch_status'] == 'uncertain'
    assert module.dispatch_due_productions([profile], [CONNECTION], enqueue, now=90000)['status'] == 'active'
    assert enqueue.call_count == 1


def test_lost_redis_reservation_reply_does_not_enqueue_or_advance_twice(production):
    module, client = production
    profile = _profile()
    _save(module, client, profile)
    original_eval = client.eval

    def lost_reply(script, *args):
        value = original_eval(script, *args)
        if script == module._RESERVE:
            raise ConnectionError('reservation reply lost')
        return value

    client.eval = lost_reply
    enqueue = Mock()
    result = module.dispatch_due_productions([profile], [CONNECTION], enqueue, now=1000)
    assert result['channels'][CHANNEL] == 'configuration_blocked'
    client.eval = original_eval
    assert module.dispatch_due_productions([profile], [CONNECTION], enqueue, now=90000)['status'] == 'active'
    assert module.get_production_state(CHANNEL)['cursor'] == '1'
    enqueue.assert_not_called()


@pytest.mark.parametrize('kind', ['cursor', 'global', 'missing_job', 'partial_state'])
def test_corrupt_or_missing_state_never_starts_another_render(production, kind):
    module, client = production
    profile = _profile()
    _save(module, client, profile)
    enqueue = Mock()
    if kind == 'cursor':
        client.hset(module.CHANNEL_STATE_PREFIX + CHANNEL, 'cursor', 'broken')
    elif kind == 'partial_state':
        client.hset(module.CHANNEL_STATE_PREFIX + CHANNEL, 'cursor', '1')
    elif kind == 'global':
        client.set(module.ACTIVE_KEY, 'broken-json')
    else:
        task = module.reserve_due_production(profile, CONNECTION, now=1000)
        client.delete(module.JOB_PREFIX + task['task_id'])
    if kind == 'missing_job':
        assert module.dispatch_due_productions([profile], [CONNECTION], enqueue, now=1000)['status'] == 'state_unavailable'
    elif kind == 'global':
        with pytest.raises(module.ChannelProductionError):
            module.dispatch_due_productions([profile], [CONNECTION], enqueue, now=1000)
    else:
        result = module.dispatch_due_productions([profile], [CONNECTION], enqueue, now=1000)
        assert result['channels'][CHANNEL] == 'configuration_blocked'
    enqueue.assert_not_called()


def test_bad_channel_configuration_does_not_starve_a_healthy_channel(production):
    module, client = production
    profiles = [_profile(default_language='unsupported'), _profile(channel_id='UC_channel_two')]
    connections = [CONNECTION, {'id': 'UC_channel_two', 'connection_id': 'connection-generation-two'}]
    for profile, connection in zip(profiles, connections):
        _save(module, client, profile, connection)
    enqueue = Mock()
    result = module.dispatch_due_productions(profiles, connections, enqueue, now=1000)
    assert result['status'] == 'queued'
    assert result['channel_id'] == 'UC_channel_two'
    enqueue.assert_called_once()


def test_append_topics_continues_cursor_but_rewriting_consumed_topics_pauses(production):
    module, client = production
    profile = _profile(production_topics=['Birinci konu'])
    _save(module, client, profile)
    enqueue = Mock()
    first = module.dispatch_due_productions([profile], [CONNECTION], enqueue, now=1000)
    _finish(module, client, first['task_id'])
    profile = {**profile, 'production_topics': ['Birinci konu', 'İkinci konu'], 'profile_revision': 'revision-2'}
    _save(module, client, profile)
    second = module.dispatch_due_productions([profile], [CONNECTION], enqueue, now=87400)
    assert second['status'] == 'queued'
    _finish(module, client, second['task_id'])
    profile = {**profile, 'production_topics': ['Değişen konu', 'İkinci konu', 'Üçüncü konu'], 'profile_revision': 'revision-3'}
    _save(module, client, profile)
    result = module.dispatch_due_productions([profile], [CONNECTION], enqueue, now=173800)
    assert result['channels'][CHANNEL] == 'paused'
    assert module.get_production_state(CHANNEL)['paused_reason'] == 'consumed_topics_changed'
    assert enqueue.call_count == 2


def _parallel_channels(production, count=3):
    module, client = production
    profiles, connections = [], []
    for index in range(count):
        channel_id = f'UC_parallel_{index}'
        profile = _profile(channel_id=channel_id)
        connection = {'id': channel_id, 'connection_id': f'connection-generation-{index}'}
        _save(module, client, profile, connection)
        profiles.append(profile)
        connections.append(connection)
    return profiles, connections


def test_single_legacy_claim_is_retained_when_second_channel_is_reserved(production):
    module, client = production
    profiles, connections = _parallel_channels(production)
    first = module.reserve_due_production(profiles[0], connections[0], now=1000)
    legacy = {'task_id': first['task_id'], 'channel_id': profiles[0]['channel_id']}
    assert json.loads(client.get(module.ACTIVE_KEY)) == legacy
    # Simulate the exact pre-upgrade payload: no rewrite/reset/lease expiry.
    client.set(module.ACTIVE_KEY, json.dumps(legacy))
    prior_job = client.get(module.JOB_PREFIX + first['task_id'])
    prior_state = module.get_production_state(profiles[0]['channel_id'])
    second = module.reserve_due_production(profiles[1], connections[1], now=1000)
    claims = module._decode_active_claims(client.get(module.ACTIVE_KEY))
    assert len(claims) == 2 and legacy in claims
    assert second['status'] == 'reserved'
    assert client.get(module.JOB_PREFIX + first['task_id']) == prior_job
    assert module.get_production_state(profiles[0]['channel_id']) == prior_state
    assert client.ttl(module.ACTIVE_KEY) == -1
    # The old scheduler and production_recovery both see the occupied fence.
    assert client.exists(module.ACTIVE_KEY) == 1


def test_one_tick_enqueues_two_channels_then_refuses_third_without_cursor_change(production):
    module, client = production
    profiles, connections = _parallel_channels(production)
    enqueue = Mock()
    result = module.dispatch_due_productions(profiles, connections, enqueue, now=1000)
    assert result['status'] == 'queued' and result['queued_count'] == 2
    assert enqueue.call_count == 2
    assert {item['channel_id'] for item in result['queued']} == {
        profiles[0]['channel_id'], profiles[1]['channel_id'],
    }
    before = client.get(module.ACTIVE_KEY)
    assert module.reserve_due_production(profiles[2], connections[2], now=1000)['status'] == 'active'
    assert module.get_production_state(profiles[2]['channel_id']) == {}
    assert client.get(module.ACTIVE_KEY) == before


def test_two_held_owner_assignments_allow_one_other_legacy_channel(production):
    module,client=production;profiles,connections=_parallel_channels(production)
    owner_key='youtube_studio:content_plan:v1:active'
    owners={'UCgvESYtYbn2w9R2ExBOF_cw':'11111111-1111-4111-8111-111111111111',
        'UCs93z6wf134H5_BL9pkQX4Q':'22222222-2222-4222-8222-222222222222'}
    client.set(owner_key,json.dumps(owners));enqueue=Mock()
    with ThreadPoolExecutor(max_workers=6)as pool:
        list(pool.map(lambda _:module.dispatch_due_productions(profiles,connections,enqueue,now=1000),range(6)))
    assert enqueue.call_count==1
    assert len(module._decode_active_claims(client.get(module.ACTIVE_KEY)))==1
    assert json.loads(client.get(owner_key))==owners
    assert sum(int(module.get_production_state(p['channel_id']).get('cursor',0))for p in profiles)==1


def test_one_owner_assignment_allows_two_legacy_channels_without_increasing_render_workers(production):
    from app.worker_runtime import commands
    module,client=production;profiles,connections=_parallel_channels(production)
    owner_key='youtube_studio:content_plan:v1:active'
    owners={'UCs93z6wf134H5_BL9pkQX4Q':'22222222-2222-4222-8222-222222222222'}
    client.set(owner_key,json.dumps(owners));enqueue=Mock()
    result=module.dispatch_due_productions(profiles,connections,enqueue,now=1000)
    assert result['queued_count']==enqueue.call_count==2
    assert len(module._decode_active_claims(client.get(module.ACTIVE_KEY)))==2
    assert json.loads(client.get(owner_key))==owners
    assert '--concurrency=2' in commands()[0]


def test_same_channel_waits_for_publisher_even_when_next_interval_due(production):
    module, client = production
    profiles, connections = _parallel_channels(production, 2)
    first = module.reserve_due_production(profiles[0], connections[0], now=1000)
    _finish(module, client, first['task_id'], delivered=False)
    enqueue = Mock()
    result = module.dispatch_due_productions(profiles, connections, enqueue, now=90000)
    assert result['queued_count'] == 1
    assert result['channel_id'] == profiles[1]['channel_id']
    assert module.get_production_state(profiles[0]['channel_id'])['cursor'] == '1'
    assert len(module._decode_active_claims(client.get(module.ACTIVE_KEY))) == 2


def test_reconcile_completed_sibling_releases_only_its_slot(production):
    module, client = production
    profiles, connections = _parallel_channels(production)
    first = module.dispatch_due_productions(profiles[:2], connections[:2], Mock(), now=1000)
    completed, pending = first['queued']
    pending_job = client.get(module.JOB_PREFIX + pending['task_id'])
    pending_state = module.get_production_state(pending['channel_id'])
    _finish(module, client, completed['task_id'])
    assert module.reconcile_active_production() == 'active'
    assert json.loads(client.get(module.ACTIVE_KEY)) == pending
    assert client.get(module.JOB_PREFIX + pending['task_id']) == pending_job
    assert module.get_production_state(pending['channel_id']) == pending_state
    third = module.reserve_due_production(profiles[2], connections[2], now=2000)
    assert third['status'] == 'reserved'
    assert len(module._decode_active_claims(client.get(module.ACTIVE_KEY))) == 2


def test_failed_channel_pauses_without_holding_successful_siblings_capacity(production):
    module, client = production
    profiles, connections = _parallel_channels(production)
    first = module.dispatch_due_productions(profiles[:2], connections[:2], Mock(), now=1000)
    failed, pending = first['queued']
    _finish(module, client, failed['task_id'], state='FAILURE')
    assert module.reconcile_active_production() == 'active'
    state = module.get_production_state(failed['channel_id'])
    assert state['paused_reason'] == 'previous_render_failed' and state['cursor'] == '1'
    assert json.loads(client.get(module.ACTIVE_KEY)) == pending
    assert module.reserve_due_production(profiles[0], connections[0], now=90000)['status'] == 'paused'
    assert module.reserve_due_production(profiles[2], connections[2], now=2000)['status'] == 'reserved'


def test_two_completed_publications_clear_fence_without_resetting_topic_cursors(production):
    module, client = production
    profiles, connections = _parallel_channels(production, 2)
    first = module.dispatch_due_productions(profiles, connections, Mock(), now=1000)
    for item in first['queued']:
        _finish(module, client, item['task_id'])
    assert module.reconcile_active_production() == 'completed'
    assert client.get(module.ACTIVE_KEY) is None
    for profile in profiles:
        state = module.get_production_state(profile['channel_id'])
        assert state['cursor'] == '1' and float(state['next_due']) == 87400
        assert state['last_result'] == 'SUCCESS' and 'active_task_id' not in state


def test_ambiguous_second_enqueue_keeps_both_claims_and_never_resends(production):
    module, client = production
    profiles, connections = _parallel_channels(production)
    enqueue = Mock(side_effect=[None, TimeoutError('lost acceptance reply')])
    first = module.dispatch_due_productions(profiles, connections, enqueue, now=1000)
    assert first['status'] == 'dispatch_uncertain' and first['queued_count'] == 1
    assert module.get_production_state(first['channel_id'])['dispatch_status'] == 'uncertain'
    before = client.get(module.ACTIVE_KEY)
    assert len(module._decode_active_claims(before)) == 2
    second = module.dispatch_due_productions(profiles, connections, enqueue, now=90000)
    assert second['status'] == 'active' and enqueue.call_count == 2
    assert client.get(module.ACTIVE_KEY) == before
    assert module.get_production_state(profiles[2]['channel_id']) == {}


@pytest.mark.parametrize('damage', ['over_capacity', 'duplicate_channel', 'duplicate_task', 'empty', 'unknown_version',
                                    'foreign_field', 'oversized'])
def test_corrupt_multi_claim_state_never_allocates_or_removes_claims(production, damage):
    module, client = production
    profiles, connections = _parallel_channels(production)
    first = module.dispatch_due_productions(profiles[:2], connections[:2], Mock(), now=1000)
    active = json.loads(client.get(module.ACTIVE_KEY))
    if damage == 'over_capacity':
        active['claims'].append({'channel_id': profiles[2]['channel_id'], 'task_id': 'f' * 36})
    elif damage == 'duplicate_channel':
        active['claims'][1]['channel_id'] = active['claims'][0]['channel_id']
    elif damage == 'duplicate_task':
        active['claims'][1]['task_id'] = active['claims'][0]['task_id']
    elif damage == 'empty':
        active['claims'] = []
    elif damage == 'unknown_version':
        active['version'] = 3
    elif damage == 'foreign_field':
        active['claims'][0]['unused'] = 'unexpected'
    else:
        active['unused'] = 'x' * 4096
    raw = json.dumps(active)
    client.set(module.ACTIVE_KEY, raw)
    with pytest.raises(module.ChannelProductionError):
        module.reconcile_active_production()
    with pytest.raises(module.ChannelProductionError):
        module.reserve_due_production(profiles[2], connections[2], now=1000)
    assert client.get(module.ACTIVE_KEY) == raw
    assert module.get_production_state(profiles[2]['channel_id']) == {}
    assert all(client.get(module.JOB_PREFIX + item['task_id']) for item in first['queued'])


def test_concurrent_reconcile_and_new_reservation_cannot_erase_replacement_claim(production, monkeypatch):
    module, client = production
    profiles, connections = _parallel_channels(production)
    first = module.dispatch_due_productions(profiles[:2], connections[:2], Mock(), now=1000)
    completed, pending = first['queued']
    _finish(module, client, completed['task_id'])
    original_eval = client.eval
    raced = False
    replacement = {}

    def eval_with_race(script, *args):
        nonlocal raced
        if script == module._RECONCILE and not raced:
            raced = True
            assert original_eval(script, *args) == 'completed'
            replacement.update(module.reserve_due_production(profiles[2], connections[2], now=2000))
        return original_eval(script, *args)

    monkeypatch.setattr(client, 'eval', eval_with_race)
    assert module.reconcile_active_production() == 'active_changed'
    claims = module._decode_active_claims(client.get(module.ACTIVE_KEY))
    assert {claim['task_id'] for claim in claims} == {pending['task_id'], replacement['task_id']}
    assert len(claims) == 2


@pytest.mark.parametrize('field,value', [('task_id', 'different-job'), ('task_id', None),
                                       ('kind', 'publish'), ('kind', None)])
def test_mismatched_failed_job_cannot_release_an_active_render_claim(production, field, value):
    module, client = production
    profiles, connections = _parallel_channels(production)
    first = module.dispatch_due_productions(profiles[:2], connections[:2], Mock(), now=1000)
    current = first['queued'][0]
    key = module.JOB_PREFIX + current['task_id']
    job = json.loads(client.get(key))
    job.update(state='FAILURE', **{field: value})
    client.set(key, json.dumps(job))
    fence = client.get(module.ACTIVE_KEY)
    channel_state = module.get_production_state(current['channel_id'])
    enqueue = Mock()
    assert module.dispatch_due_productions(profiles, connections, enqueue, now=90000)['status'] == 'state_unavailable'
    enqueue.assert_not_called()
    assert client.get(module.ACTIVE_KEY) == fence
    assert module.get_production_state(current['channel_id']) == channel_state
    assert module.get_production_state(profiles[2]['channel_id']) == {}
