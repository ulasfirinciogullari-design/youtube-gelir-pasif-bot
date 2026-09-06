"""No provider calls: real atomic scheduler proof and finite-queue behavior."""
from concurrent.futures import ThreadPoolExecutor
import ast
import json
from unittest.mock import Mock

import pytest

from test_channel_production import production, _profile, _save, _finish, _parallel_channels, CHANNEL, CONNECTION, ROOT


VIDEO = 'Public00000'


def _public_finish(module, client, task_id):
    _finish(module, client, task_id)
    key = module.JOB_PREFIX + task_id
    job = json.loads(client.get(key))
    spec = job['spec']
    child_id = job['result']['youtube_automation']['publish_task_id']
    binding = {'target_channel_id': spec['production_channel_id'],
               'connection_id': spec['production_connection_id'], 'profile_revision': spec['production_profile_revision']}
    delivered = {'privacy_status': 'public', 'release_status': 'public', 'release_error_code': None,
                 'scheduled_publish_at': None, 'caption_uploaded': True, 'caption_error_code': None,
                 'thumbnail_uploaded': True, 'thumbnail_error_code': None, 'contains_synthetic_media': True, **binding}
    job['result'].update(task_id=task_id, status='complete', video_key=f'videos/{task_id}/final.mp4',
                         caption_key=f'videos/{task_id}/captions.{spec["language"]}.srt',
                         youtube={'video_id': VIDEO, **delivered},
                         youtube_automation={'status': 'queued', 'publish_task_id': child_id,
                                             'release_mode': 'public', **binding})
    client.set(key, json.dumps(job))
    publisher_key = module.JOB_PREFIX + child_id
    child = json.loads(client.get(publisher_key))
    child['spec'].update(source_task_id=task_id, privacy_status='private', release_mode='public', **binding)
    child['result'] = {'task_id': child_id, 'status': 'complete', 'source_task_id': task_id,
                       'youtube_video_id': VIDEO, **delivered}
    client.set(publisher_key, json.dumps(child))
    ledger = {'version': 2, 'source_task_id': task_id, 'publish_task_id': child_id, 'status': 'complete',
              'youtube_video_id': VIDEO, 'requested_release_mode': 'public', 'requested_publish_at': None,
              'side_effect_possible': True, 'release_side_effect_possible': True,
              'privacy_status': 'public', 'release_status': 'public', 'release_error_code': None,
              'release_completed_at': '2026-09-06T12:00:00+00:00', **binding,
              'publish_plan': {'source_task_id': task_id, 'target_channel_id': binding['target_channel_id'],
                               'profile_revision': binding['profile_revision'], 'release_mode': 'public',
                               'publish_at': None, 'contains_synthetic_media': True, 'require_thumbnail': True}}
    ledger_key = module.PUBLICATION_UPLOAD_PREFIX + task_id
    client.set(ledger_key, json.dumps(ledger))
    return {'source': key, 'publisher': publisher_key, 'ledger': ledger_key,
            'profile': module.PROFILE_PREFIX + binding['target_channel_id'],
            'channel': module.OAUTH_CHANNEL_PREFIX + binding['target_channel_id']}


@pytest.fixture
def case(production):
    module, client = production
    profile = _profile(release_mode='public', require_thumbnail=True)
    _save(module, client, profile)
    first = module.dispatch_due_productions([profile], [CONNECTION], Mock(), now=1000)
    keys = _public_finish(module, client, first['task_id'])
    return module, client, profile, first, keys


def test_confirmed_public_delivery_removes_only_cooldown_and_keeps_history(case):
    module, client, _, first, keys = case
    before = {name: client.get(key) for name, key in keys.items()}
    assert module.reconcile_active_production(now=2000) == 'completed'
    state = module.get_production_state(CHANNEL)
    assert state['cursor'] == '1' and state['next_due'] == '2000'
    assert state['last_public_task_id'] == first['task_id'] and state['last_public_continued_at'] == '2000'
    assert state['dispatch_status'] == 'finished' and not state.get('active_task_id')
    assert {name: client.get(key) for name, key in keys.items()} == before


def test_same_dispatch_tick_starts_next_frozen_topic_without_analytics_or_approval(case):
    module, client, profile, first, _ = case
    enqueue = Mock()
    second = module.dispatch_due_productions([profile], [CONNECTION], enqueue, now=2000)
    assert second['status'] == 'queued' and second['task_id'] != first['task_id']
    assert enqueue.call_count == 1
    next_job = json.loads(client.get(module.JOB_PREFIX + second['task_id']))
    assert next_job['spec']['production_topic_index'] == 1
    assert next_job['spec']['topic'].startswith(profile['production_topics'][1])
    assert module.get_production_state(CHANNEL)['cursor'] == '2'
    assert module.dispatch_due_productions([profile], [CONNECTION], enqueue, now=2001)['status'] == 'active'
    assert enqueue.call_count == 1


def test_finite_queue_is_never_cycled_or_expanded(case):
    module, client, profile, _, _ = case
    enqueue = Mock()
    second = module.dispatch_due_productions([profile], [CONNECTION], enqueue, now=2000)
    _public_finish(module, client, second['task_id'])
    assert module.dispatch_due_productions([profile], [CONNECTION], enqueue, now=2100) == {
        'status': 'idle', 'channels': {CHANNEL: 'topics_exhausted'}}
    assert module.get_production_state(CHANNEL)['cursor'] == '2'
    assert enqueue.call_count == 1 and client.get(module.ACTIVE_KEY) is None


@pytest.mark.parametrize('release', ['private', 'scheduled', 'uncertain', 'blocked'])
def test_nonpublic_delivery_never_removes_delay(case, release):
    module, client, profile, _, keys = case
    publisher = json.loads(client.get(keys['publisher']))
    publisher['result']['release_status'] = release
    publisher['result']['privacy_status'] = 'private'
    client.set(keys['publisher'], json.dumps(publisher))
    enqueue = Mock()
    module.dispatch_due_productions([profile], [CONNECTION], enqueue, now=2000)
    state = module.get_production_state(CHANNEL)
    assert state['next_due'] == '87400' and 'last_public_continued_at' not in state
    assert state['cursor'] == '1'
    if release in {'blocked', 'uncertain'}: assert state['paused_reason'] == 'previous_publication_blocked'
    enqueue.assert_not_called()


@pytest.mark.parametrize('name,path,value', [
    ('source', ['result', 'video_key'], 'videos/other/final.mp4'),
    ('source', ['result', 'caption_key'], 'videos/other/captions.tr.srt'),
    ('source', ['result', 'youtube', 'video_id'], 'Other000000'),
    ('source', ['result', 'youtube', 'privacy_status'], 'private'),
    ('source', ['result', 'youtube', 'caption_uploaded'], False),
    ('source', ['result', 'youtube', 'contains_synthetic_media'], False),
    ('source', ['result', 'youtube_automation', 'profile_revision'], 'changed'),
    ('source', ['spec', 'production_scheduled'], False),
    ('publisher', ['result', 'youtube_video_id'], 'Other000000'),
    ('publisher', ['result', 'privacy_status'], 'private'),
    ('publisher', ['result', 'release_error_code'], 'unknown'),
    ('publisher', ['result', 'scheduled_publish_at'], '2099-01-01T00:00:00Z'),
    ('publisher', ['result', 'thumbnail_uploaded'], False),
    ('publisher', ['result', 'caption_error_code'], 'HttpError_403'),
    ('publisher', ['spec', 'connection_id'], 'new-connection'),
    ('ledger', ['status'], 'uploading'), ('ledger', ['release_status'], 'uncertain'),
    ('ledger', ['release_side_effect_possible'], False), ('ledger', ['release_completed_at'], None),
    ('ledger', ['requested_release_mode'], 'private'), ('ledger', ['source_task_id'], 'other'),
    ('ledger', ['publish_task_id'], 'other'), ('ledger', ['youtube_video_id'], 'Other000000'),
    ('ledger', ['publish_plan', 'contains_synthetic_media'], None),
    ('ledger', ['publish_plan', 'profile_revision'], 'other'),
    ('profile', ['profile_revision'], 'changed'), ('profile', ['release_mode'], 'private'),
    ('profile', ['auto_publish'], False), ('channel', ['connection_id'], 'changed'),
    ('channel', ['requires_reconnect'], True),
])
def test_only_bound_completed_public_proof_can_continue(case, name, path, value):
    module, client, _, _, keys = case
    document = json.loads(client.get(keys[name]))
    row = document
    for field in path[:-1]: row = row[field]
    row[path[-1]] = value
    client.set(keys[name], json.dumps(document))
    assert module.reconcile_active_production(now=2000) == 'channel_paused'
    state = module.get_production_state(CHANNEL)
    assert state['paused_reason'] == 'previous_publication_blocked'
    assert state['next_due'] == '87400' and state['cursor'] == '1'


@pytest.mark.parametrize('missing', ['ledger', 'credential', 'membership'])
def test_missing_delivery_or_connection_proof_cannot_continue(case, missing):
    module, client, _, _, keys = case
    if missing == 'membership': client.srem(module.OAUTH_CHANNEL_INDEX, CHANNEL)
    else: client.delete(keys['ledger'] if missing == 'ledger' else module.OAUTH_CREDENTIAL_PREFIX + CHANNEL)
    assert module.reconcile_active_production(now=2000) == 'channel_paused'
    assert module.get_production_state(CHANNEL)['next_due'] == '87400'


def test_public_continuation_preserves_other_active_channel_and_capacity_two(production):
    module, client = production
    profiles, connections = _parallel_channels(production, 3)
    for profile, connection in zip(profiles, connections):
        profile['release_mode'] = 'public'
        _save(module, client, profile, connection)
    first = module.dispatch_due_productions(profiles, connections, Mock(), now=1000)
    completed, running = first['queued']
    _public_finish(module, client, completed['task_id'])
    before = client.get(module.JOB_PREFIX + running['task_id'])
    enqueue = Mock()
    next_tick = module.dispatch_due_productions(profiles, connections, enqueue, now=2000)
    assert next_tick['queued_count'] == 1 and enqueue.call_count == 1
    claims = module._decode_active_claims(client.get(module.ACTIVE_KEY))
    assert len(claims) == 2 and running in claims
    assert client.get(module.JOB_PREFIX + running['task_id']) == before
    assert next_tick['channel_id'] == profiles[2]['channel_id']
    assert module.get_production_state(profiles[2]['channel_id'])['cursor'] == '1'


def test_concurrent_public_ticks_reserve_next_topic_once(case):
    module, _, profile, _, _ = case
    enqueue = Mock()
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: module.dispatch_due_productions([profile], [CONNECTION], enqueue, now=2000), range(4)))
    assert enqueue.call_count == 1
    assert module.get_production_state(CHANNEL)['cursor'] == '2'


@pytest.mark.parametrize('field', ['publisher', 'ledger', 'profile', 'channel'])
def test_public_proof_race_is_checked_inside_lua(case, monkeypatch, field):
    module, client, _, _, keys = case
    original = client.eval
    def race(script, *args):
        if script == module._RECONCILE:
            value = json.loads(client.get(keys[field]))
            if field == 'publisher': value['result']['privacy_status'] = 'private'
            elif field == 'ledger': value['release_status'] = 'uncertain'
            elif field == 'profile': value['auto_publish'] = False
            else: value['requires_reconnect'] = True
            client.set(keys[field], json.dumps(value))
        return original(script, *args)
    monkeypatch.setattr(client, 'eval', race)
    assert module.reconcile_active_production(now=2000) == 'channel_paused'
    assert module.get_production_state(CHANNEL)['next_due'] == '87400'


@pytest.mark.parametrize('field,value', [('spec', None), ('spec', 3), ('spec', True),
                                        ('language', None), ('language', True), ('language', {}),
                                        ('attribution', None), ('plan', None)])
def test_corrupt_public_shape_pauses_safely_without_stopping_other_channel(case, field, value):
    module, client, _, _, keys = case
    target = 'ledger' if field == 'plan' else 'source'
    document = json.loads(client.get(keys[target]))
    if field == 'spec': document['spec'] = value
    elif field == 'language': document['spec']['language'] = value
    elif field == 'attribution': document['result']['youtube'] = value
    else: document['publish_plan'] = value
    client.set(keys[target], json.dumps(document))
    assert module.reconcile_active_production(now=2000) == 'channel_paused'
    assert module.get_production_state(CHANNEL)['paused_reason'] == 'previous_publication_blocked'
    assert module.get_production_state(CHANNEL)['next_due'] == '87400'


def _completed_replay(case):
    _, client, _, _, keys = case
    tree = ast.parse((ROOT / 'app/publish_tasks.py').read_text(encoding='utf-8'))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == '_result_from_existing_record')
    ns = {'UploadAlreadyInProgress': RuntimeError}
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<real-completed-publisher-replay>', 'exec'), ns)
    publisher = json.loads(client.get(keys['publisher']))
    source = json.loads(client.get(keys['source']))
    ledger = json.loads(client.get(keys['ledger']))
    publisher['result'] = ns['_result_from_existing_record'](publisher['task_id'], source['task_id'], ledger)
    client.set(keys['publisher'], json.dumps(publisher))
    return publisher


def test_actual_completed_publisher_redelivery_uses_existing_full_source_and_ledger(case):
    module, client, _, _, keys = case
    replay = _completed_replay(case)
    assert replay['result']['idempotent_replay'] is True
    assert 'caption_uploaded' not in replay['result'] and 'profile_revision' not in replay['result']
    before = {name: client.get(key) for name, key in keys.items()}
    assert module.reconcile_active_production(now=2000) == 'completed'
    assert module.get_production_state(CHANNEL)['next_due'] == '2000'
    assert {name: client.get(key) for name, key in keys.items()} == before


@pytest.mark.parametrize('field,value', [('profile_revision', 'changed'), ('profile_revision', None),
                                        ('caption_uploaded', False), ('thumbnail_uploaded', False),
                                        ('contains_synthetic_media', False), ('contains_synthetic_media', None),
                                        ('stage', 'pending'), ('progress', 99), ('idempotent_replay', False)])
def test_completed_replay_cannot_hide_explicit_contradictions(case, field, value):
    module, client, _, _, keys = case
    replay = _completed_replay(case)
    replay['result'][field] = value
    client.set(keys['publisher'], json.dumps(replay))
    assert module.reconcile_active_production(now=2000) == 'channel_paused'
    assert module.get_production_state(CHANNEL)['next_due'] == '87400'


@pytest.mark.parametrize('target', ['source', 'ledger'])
def test_completed_replay_still_requires_full_other_public_proofs(case, target):
    module, client, _, _, keys = case
    _completed_replay(case)
    data = json.loads(client.get(keys[target]))
    if target == 'source': data['result']['youtube']['caption_uploaded'] = False
    else: data['release_status'] = 'uncertain'
    client.set(keys[target], json.dumps(data))
    assert module.reconcile_active_production(now=2000) == 'channel_paused'
