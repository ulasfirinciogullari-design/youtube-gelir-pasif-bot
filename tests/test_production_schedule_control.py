import ast
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import fakeredis
import pytest


ROOT = Path(__file__).resolve().parents[1]
CHANNEL = 'UC_schedule_test'
REVISION = 'new-public-six-hour-revision'
PREVIOUS_TASK = '11111111-1111-4111-8111-111111111111'


def _load(path, namespace, excluded):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    tree.body = [node for node in tree.body if not (
        isinstance(node, ast.ImportFrom) and node.module in excluded
    )]
    exec(compile(tree, str(path), 'exec'), namespace)
    return SimpleNamespace(**namespace)


@pytest.fixture
def case():
    namespace = {
        'settings': SimpleNamespace(redis_url='redis://not-used'),
        'JOB_PREFIX': 'youtube_studio:job:', 'JOB_INDEX': 'youtube_studio:jobs',
        'JOB_TTL_SECONDS': 90 * 24 * 3600,
    }
    production = _load(ROOT / 'app/services/channel_production.py', namespace,
                       {'app.config', 'app.services.studio_state'})
    client = fakeredis.FakeRedis(decode_responses=True)
    namespace['_redis'] = lambda: client
    control_namespace = {
        'settings': SimpleNamespace(redis_url='redis://not-used'),
        **{key: namespace[key] for key in (
            'ACTIVE_KEY', 'CHANNEL_STATE_PREFIX', 'OAUTH_CHANNEL_INDEX',
            'OAUTH_CHANNEL_PREFIX', 'OAUTH_CREDENTIAL_PREFIX', 'PROFILE_PREFIX',
            'PRODUCTION_PREFIX', '_decode_active_claims', '_prefix_digest',
        )},
    }
    control = _load(ROOT / 'app/services/production_schedule_control.py', control_namespace,
                    {'app.config', 'app.services.channel_production'})
    control_namespace['_redis'] = lambda: client
    profile = {
        'channel_id': CHANNEL, 'profile_revision': REVISION,
        'auto_publish': True, 'production_enabled': True, 'release_mode': 'public',
        'production_interval_hours': 6, 'default_language': 'tr', 'languages': ['tr'],
        'production_topics': ['Already consumed topic', 'Next topic with a source https://example.org/reference', 'Third topic'],
    }
    connection = {'id': CHANNEL, 'connection_id': 'current-oauth-generation'}
    state = {
        'cursor': '1', 'consumed_prefix': production._prefix_digest(profile['production_topics'][:1]),
        'next_due': '87400', 'dispatch_status': 'finished', 'last_task_id': PREVIOUS_TASK,
        'connection_id': connection['connection_id'], 'profile_revision': 'historical-revision',
        # A successful, explicitly recovered child need not rewrite its failed ancestor.
        'last_result': 'FAILURE',
    }
    profile_key = control.PROFILE_PREFIX + CHANNEL
    state_key = control.CHANNEL_STATE_PREFIX + CHANNEL
    connection_key = control.OAUTH_CHANNEL_PREFIX + CHANNEL
    credentials_key = control.OAUTH_CREDENTIAL_PREFIX + CHANNEL
    audit_key = control.EXPEDITE_PREFIX + CHANNEL + ':' + REVISION
    client.set(profile_key, json.dumps(profile))
    client.hset(state_key, mapping=state)
    client.set(connection_key, json.dumps(connection))
    client.set(credentials_key, 'opaque-secret-never-returned')
    client.sadd(control.OAUTH_CHANNEL_INDEX, CHANNEL)
    client.set('unrelated-paid-ledger', 'unchanged')
    client.set('unrelated-job', 'unchanged')
    return SimpleNamespace(**locals())


def run(case, now=1000):
    return case.control.expedite_next_production(CHANNEL, REVISION, now=now)


def test_expedite_changes_only_due_and_creates_one_safe_audit(case):
    before = {key: case.client.dump(key) for key in case.client.keys()}
    result = run(case)
    assert result['status'] == 'expedited'
    assert result['previous_next_due'] == 87400 and result['next_due'] == 1000
    assert result['cursor'] == 1
    assert case.client.hgetall(case.state_key) == {**case.state, 'next_due': '1000.0'}
    assert set(case.client.keys()) == set(before) | {case.audit_key}
    for key, raw in before.items():
        if key != case.state_key:
            assert case.client.dump(key) == raw
    assert 'opaque-secret' not in json.dumps(result)
    assert 'https://' not in json.dumps(result)
    assert case.client.ttl(case.audit_key) == -1


def test_repeated_request_after_cursor_advance_does_not_expedite_another_topic(case):
    first = run(case)
    case.client.hset(case.state_key, mapping={'cursor': '2', 'next_due': '999999', 'active_task_id': PREVIOUS_TASK})
    before = case.client.dump(case.state_key)
    repeated = run(case, now=2000)
    assert repeated == {**first, 'status': 'already_expedited'}
    assert case.client.dump(case.state_key) == before


def test_already_due_is_never_delayed(case):
    case.client.hset(case.state_key, 'next_due', '900')
    before = case.client.dump(case.state_key)
    result = run(case)
    assert result['status'] == 'already_due' and result['next_due'] == 900
    assert case.client.dump(case.state_key) == before
    assert run(case, now=2000)['status'] == 'already_expedited'


@pytest.mark.parametrize('field,value', [
    ('channel_id', 'UC_other_channel'), ('profile_revision', 'changed-revision'),
    ('production_enabled', False), ('production_enabled', 1),
    ('auto_publish', False), ('auto_publish', 'true'),
    ('release_mode', 'private'), ('release_mode', 'scheduled'),
    ('production_interval_hours', 5), ('production_interval_hours', 169),
    ('production_interval_hours', True), ('default_language', 'unknown'),
    ('languages', []), ('languages', 'tr'),
    ('production_topics', []), ('production_topics', ['Already consumed topic']),
    ('production_topics', ['Changed consumed topic', 'Next topic']),
    ('production_topics', ['Already consumed topic', '']),
    ('production_topics', ['Already consumed topic', 'x' * 241]),
    ('production_topics', ['Already consumed topic', 'Next topic', 'Next topic']),
])
def test_invalid_or_changed_profile_cannot_expedite(case, field, value):
    profile = {**case.profile, field: value}
    case.client.set(case.profile_key, json.dumps(profile))
    before = case.client.dump(case.state_key)
    with pytest.raises(case.control.ProductionScheduleControlError):
        run(case)
    assert case.client.dump(case.state_key) == before
    assert case.client.get(case.audit_key) is None


@pytest.mark.parametrize('field,value', [
    ('paused_reason', 'previous_render_failed'), ('active_task_id', PREVIOUS_TASK),
    ('dispatch_status', 'uncertain'), ('dispatch_status', 'reserved'),
    ('cursor', '0'), ('cursor', '-1'), ('cursor', '01'), ('cursor', '1.0'), ('cursor', '3'),
    ('consumed_prefix', 'wrong'), ('last_task_id', 'invalid'),
    ('next_due', 'nan'), ('next_due', 'inf'), ('next_due', '-1'), ('next_due', ''),
    ('connection_id', 'stale-oauth-generation'),
])
def test_non_idle_or_invalid_schedule_remains_untouched(case, field, value):
    case.client.hset(case.state_key, field, value)
    before = case.client.dump(case.state_key)
    with pytest.raises(case.control.ProductionScheduleControlError):
        run(case)
    assert case.client.dump(case.state_key) == before
    assert case.client.get(case.audit_key) is None


@pytest.mark.parametrize('damage', ['missing_credentials', 'empty_credentials', 'missing_index', 'reconnect', 'wrong_channel'])
def test_disconnected_or_changed_oauth_cannot_expedite(case, damage):
    if damage == 'missing_credentials':
        case.client.delete(case.credentials_key)
    elif damage == 'empty_credentials':
        case.client.set(case.credentials_key, '')
    elif damage == 'missing_index':
        case.client.srem(case.control.OAUTH_CHANNEL_INDEX, CHANNEL)
    else:
        connection = {**case.connection,
                      **({'connection_id': 'another-generation'} if damage == 'reconnect' else {'id': 'UC_another_channel'})}
        case.client.set(case.connection_key, json.dumps(connection))
    with pytest.raises(case.control.ProductionScheduleControlError):
        run(case)
    assert case.client.hgetall(case.state_key) == case.state
    assert case.client.get(case.audit_key) is None


@pytest.mark.parametrize('active', [
    {'channel_id': CHANNEL, 'task_id': PREVIOUS_TASK},
    {'version': 2, 'claims': [{'channel_id': CHANNEL, 'task_id': PREVIOUS_TASK},
                            {'channel_id': 'UC_other_channel', 'task_id': '22222222-2222-4222-8222-222222222222'}]},
    {'version': 2, 'claims': []}, {'broken': True},
])
def test_own_or_corrupt_global_claim_prevents_advancement(case, active):
    raw = json.dumps(active)
    case.client.set(case.control.ACTIVE_KEY, raw)
    with pytest.raises(case.control.ProductionScheduleControlError):
        run(case)
    assert case.client.get(case.control.ACTIVE_KEY) == raw
    assert case.client.hgetall(case.state_key) == case.state


def test_another_channels_active_claim_is_preserved(case):
    raw = json.dumps({'channel_id': 'UC_other_channel', 'task_id': PREVIOUS_TASK})
    case.client.set(case.control.ACTIVE_KEY, raw)
    assert run(case)['status'] == 'expedited'
    assert case.client.get(case.control.ACTIVE_KEY) == raw


@pytest.mark.parametrize('key_name', ['state_key', 'profile_key', 'connection_key', 'credentials_key', 'active_key', 'index_key', 'audit_key'])
def test_watch_races_commit_no_due_change(case, monkeypatch, key_name):
    key = (case.control.ACTIVE_KEY if key_name == 'active_key' else
           case.control.OAUTH_CHANNEL_INDEX if key_name == 'index_key' else getattr(case, key_name))
    original_pipeline = case.client.pipeline

    def pipeline(*args, **kwargs):
        pipe = original_pipeline(*args, **kwargs)
        execute = pipe.execute

        def race(*args, **kwargs):
            if key_name == 'state_key':
                case.client.hset(key, 'concurrent', 'change')
            elif key_name == 'index_key':
                case.client.srem(key, CHANNEL)
            else:
                case.client.set(key, 'concurrent change')
            return execute(*args, **kwargs)

        pipe.execute = race
        return pipe

    monkeypatch.setattr(case.client, 'pipeline', pipeline)
    with pytest.raises(case.control.ProductionScheduleControlError, match='schedule_state_changed'):
        run(case)
    assert case.client.hget(case.state_key, 'next_due') == '87400'
    if key_name != 'audit_key':
        assert case.client.get(case.audit_key) is None


def test_lost_commit_reply_is_resolved_by_same_revision_audit(case, monkeypatch):
    original_pipeline = case.client.pipeline

    def pipeline(*args, **kwargs):
        pipe = original_pipeline(*args, **kwargs)
        execute = pipe.execute

        def lost_reply(*args, **kwargs):
            execute(*args, **kwargs)
            raise ConnectionError('upstream SECRET endpoint')

        pipe.execute = lost_reply
        return pipe

    monkeypatch.setattr(case.client, 'pipeline', pipeline)
    with pytest.raises(case.control.ProductionScheduleControlError, match='schedule_state_unavailable') as error:
        run(case)
    assert 'SECRET' not in str(error.value)
    assert case.client.hget(case.state_key, 'next_due') == '1000.0'
    assert run(case, now=2000)['status'] == 'already_expedited'
    assert case.client.hget(case.state_key, 'next_due') == '1000.0'


def test_concurrent_requests_have_one_winner_and_never_advance_cursor(case):
    def invoke(_index):
        try:
            return run(case)['status']
        except case.control.ProductionScheduleControlError as error:
            assert str(error) == 'schedule_state_changed'
            return 'state_changed'

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(invoke, range(16)))
    assert statuses.count('expedited') == 1
    assert case.client.hget(case.state_key, 'cursor') == '1'
    assert run(case, now=2000)['status'] == 'already_expedited'


def test_normal_scheduler_reserves_only_the_existing_next_topic_after_expedite(case):
    assert case.production.reserve_due_production(case.profile, case.connection, now=1000)['status'] == 'not_due'
    run(case)
    reserved = case.production.reserve_due_production(case.profile, case.connection, now=1000)
    assert reserved['status'] == 'reserved'
    assert reserved['args'][0] == case.profile['production_topics'][1]
    assert reserved['args'][4]['production_topic_index'] == 1
    assert case.client.hget(case.state_key, 'cursor') == '2'
    assert float(case.client.hget(case.state_key, 'next_due')) == 1000 + 6 * 3600
    frozen = case.client.dump(case.state_key)
    assert run(case, now=2000)['status'] == 'already_expedited'
    assert case.client.dump(case.state_key) == frozen


@pytest.mark.parametrize('now', [True, '1000', -1, float('nan'), float('inf')])
def test_invalid_time_cannot_write(case, now):
    with pytest.raises(case.control.ProductionScheduleControlError):
        run(case, now=now)
    assert case.client.get(case.audit_key) is None
