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


def test_concurrent_beats_across_two_channels_enqueue_only_one_global_render(production):
    module, client = production
    profiles = [_profile(), _profile(channel_id='UC_channel_two')]
    connections = [CONNECTION, {'id': 'UC_channel_two', 'connection_id': 'connection-generation-two'}]
    for profile, connection in zip(profiles, connections):
        _save(module, client, profile, connection)
    enqueue = Mock()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: module.dispatch_due_productions(
            profiles, connections, enqueue, now=1000,
        ), range(12)))
    assert sum(result['status'] == 'queued' for result in results) == 1
    assert enqueue.call_count == 1
    assert sum(int(module.get_production_state(profile['channel_id']).get('cursor', 0)) for profile in profiles) == 1


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
