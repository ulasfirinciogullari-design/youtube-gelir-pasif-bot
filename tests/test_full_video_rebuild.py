"""Real atomic Redis scripts; no provider imports, requests or live state."""
import ast
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
from uuid import UUID

import fakeredis
import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = '18300000-0000-4000-8000-000000000003'
CHILD = '18400000-0000-4000-8000-000000000004'
PARENT = '18200000-0000-4000-8000-000000000002'
CHANNEL = 'UC5v9AvNtD3PTLgo6m1jROOA'
TOKEN = 'one-shot-full-rebuild-token-1234'


def _load(name, namespace):
    class StripImports(ast.NodeTransformer):
        def visit_ImportFrom(self, node):
            return None if (node.module or '').startswith('app.') else node
    path = ROOT / 'app/services' / (name + '.py')
    tree = StripImports().visit(ast.parse(path.read_text(encoding='utf-8')))
    module = ModuleType(name)
    module.__dict__.update(namespace)
    exec(compile(tree, str(path), 'exec'), module.__dict__)
    return module


def _write(client, key, value):
    client.set(key, json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')))


def _edit(c, key, field, value):
    obj = json.loads(c.client.get(key))
    obj[field] = value
    _write(c.client, key, obj)


def _all(client):
    result = {}
    for key in client.scan_iter():
        kind = client.type(key)
        value = (client.hgetall(key) if kind == 'hash' else sorted(client.smembers(key)) if kind == 'set'
                 else client.zrange(key, 0, -1, withscores=True) if kind == 'zset' else client.get(key))
        result[key] = (kind, value)
    return result


@pytest.fixture
def case():
    client = fakeredis.FakeRedis(decode_responses=True)
    settings = SimpleNamespace(redis_url='redis://never-used')
    state = _load('studio_state', {'settings': settings})
    state._client = lambda: client
    ns = dict(vars(state), settings=settings)
    production = _load('channel_production', ns)
    ns.update({key: getattr(production, key) for key in (
        'ACTIVE_KEY', 'CHANNEL_STATE_PREFIX', 'PROFILE_PREFIX', 'OAUTH_CHANNEL_PREFIX',
        'OAUTH_CREDENTIAL_PREFIX', 'OAUTH_CHANNEL_INDEX', '_prefix_digest')})
    ns.update(AUTH_EPOCH_KEY='youtube_studio:oauth:authorization_epoch:v2',
              UPLOAD_PREFIX='youtube_studio:youtube_upload:v2:',
              EXECUTION_LOCK_PREFIX='youtube_studio:youtube_upload_lock:v2:')
    module = _load('full_video_rebuild', ns)
    module._redis = lambda: client
    state._claim_bound_full_rebuild = module._claim_bound_full_rebuild
    calls = []
    module.run_video_pipeline = SimpleNamespace(apply_async=lambda **kwargs: calls.append(kwargs))
    c = SimpleNamespace(client=client, state=state, module=module, calls=calls)
    _seed(c, SOURCE, CHANNEL)
    c.source = json.loads(client.get(module.JOB_PREFIX + SOURCE))
    c.spec = deepcopy(c.source['spec'])
    return c


def _seed(c, source_id, channel_id):
    m = c.module
    topic = 'The first verified barcode scan'
    revision, connection = 'same-profile-revision', 'connection-' + channel_id
    profile = {'channel_id': channel_id, 'route_label': 'route-' + channel_id,
               'profile_revision': revision, 'release_mode': 'public', 'auto_publish': True,
               'production_enabled': True, 'production_interval_hours': 6,
               'production_topics': ['Earlier topic', topic], 'default_language': 'tr'}
    spec = {'topic': topic, 'duration_minutes': .5, 'language': 'tr', 'channel_id': profile['route_label'],
            'mode': 'production', 'format': 'shorts', 'music': 'off', 'workflow': 'auto',
            'publish_after_render': True, 'production_scheduled': True,
            'production_channel_id': channel_id, 'production_connection_id': connection,
            'production_profile_revision': revision, 'production_topic_index': 1}
    job = {'task_id': source_id, 'kind': 'render', 'parent_id': None, 'spec': spec,
           'state': 'FAILURE', 'stage': 'failed', 'failure_stage': 'final_visual_qc',
           'error': 'Original quality rejection', 'result': None,
           'audio_candidate_checkpoint': {'retained': True}, 'generated_asset_candidates': {'retained': True}}
    _write(c.client, m.JOB_PREFIX + source_id, job)
    c.client.zadd(m.JOB_INDEX, {source_id: 1})
    _write(c.client, m.PROFILE_PREFIX + channel_id, profile)
    _write(c.client, m.OAUTH_CHANNEL_PREFIX + channel_id, {'id': channel_id, 'connection_id': connection})
    c.client.set(m.OAUTH_CREDENTIAL_PREFIX + channel_id, 'encrypted-credential-sentinel')
    c.client.sadd(m.OAUTH_CHANNEL_INDEX, channel_id)
    c.client.set(m.AUTH_EPOCH_KEY, '9')
    c.client.hset(m.CHANNEL_STATE_PREFIX + channel_id, mapping={
        'cursor': '2', 'consumed_prefix': m._prefix_digest(profile['production_topics']), 'next_due': '1',
        'paused_reason': 'previous_render_failed', 'last_task_id': source_id, 'last_result': 'FAILURE',
        'dispatch_status': 'finished', 'profile_revision': revision, 'connection_id': connection})
    c.client.hset(m.PAID_CREATE_BUDGET_PREFIX + source_id, mapping={'cap': '6', 'used': '4'})
    c.client.set(m.REPAIR_CHECKPOINT_PREFIX + source_id, 'existing-approved-checkpoint-untouched')


def _reserve(c, child_id=CHILD, source_id=SOURCE):
    return c.module.reserve_full_video_rebuild(source_id, child_id, TOKEN)


def _child(c):
    result = _reserve(c)
    assert c.state.acquire_retry_child_execution(CHILD, SOURCE) is True
    return result


def _get(c):
    return c.module.get_full_rebuild_policy(CHILD, SOURCE, c.spec)


def test_atomic_reservation_preserves_source_and_checkpoints_and_cap(case):
    c, m = case, case.module
    before = _all(c.client)
    result = _reserve(c)
    assert result['claimed'] is True and result['mode'] == 'full' and result['checkpoint'] is None
    source = json.loads(c.client.get(m.JOB_PREFIX + SOURCE))
    for key, value in c.source.items():
        assert source[key] == value
    assert source['retry_child_task_id'] == CHILD and source['retry_claimed'] is True
    child = json.loads(c.client.get(m.JOB_PREFIX + CHILD))
    assert child['spec'] == c.spec and child['parent_id'] == SOURCE and child['state'] == 'PENDING'
    assert child['result'] is None and 'full_rebuild' not in child
    assert c.client.zscore(m.JOB_INDEX, CHILD) is not None
    assert c.client.get(m.REPAIR_CHECKPOINT_PREFIX + SOURCE) == before[m.REPAIR_CHECKPOINT_PREFIX + SOURCE][1]
    assert c.client.hgetall(m.PAID_CREATE_BUDGET_PREFIX + SOURCE) == {'cap': '6', 'used': '4'}
    assert not c.client.exists(m.PAID_CREATE_BUDGET_PREFIX + CHILD)
    assert not c.client.exists(m.REPAIR_CHECKPOINT_CLAIM_PREFIX + SOURCE)
    assert c.client.hgetall(m.CHANNEL_STATE_PREFIX + CHANNEL) == before[m.CHANNEL_STATE_PREFIX + CHANNEL][1]
    assert c.client.ttl(m.SOURCE_PREFIX + SOURCE) == c.client.ttl(m.POLICY_PREFIX + CHILD) == -1
    assert 'encrypted-credential-sentinel' not in json.dumps(result)


def test_verified_getter_is_readonly_and_requires_execution_once(case):
    c, m = case, case.module
    _reserve(c)
    with pytest.raises(m.FullVideoRebuildError):
        _get(c)
    assert c.state.acquire_retry_child_execution(CHILD, SOURCE) is True
    before = _all(c.client)
    policy = _get(c)
    assert policy == {'version': 1, 'mode': 'full', 'source_task_id': SOURCE, 'child_task_id': CHILD,
                      'spec_sha256': m._digest(c.spec), 'original_paid_create_cap': 6,
                      'fresh_story': True, 'fresh_voice': True, 'fresh_media': True, 'requires_full_qa': True}
    assert _all(c.client) == before
    assert c.state.acquire_retry_child_execution(CHILD, SOURCE) is False
    assert not any('credential' in k or 'token' in k for k in policy)


def test_repeat_and_expired_retry_claim_never_grant_new_authority(case):
    _reserve(case)
    m = case.module
    case.client.delete(m.RETRY_DISPATCH_PREFIX + SOURCE, m.RETRY_CHILD_CLAIM_PREFIX + CHILD)
    before = _all(case.client)
    assert _reserve(case, str(UUID(int=99))) == {'claimed': False, 'child_task_id': CHILD}
    assert _all(case.client) == before


def test_concurrent_reservations_are_exactly_once(case):
    def reserve(i):
        try:
            return _reserve(case, str(UUID(int=100 + i)))
        except case.module.FullVideoRebuildError:
            return {'claimed': False}
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(reserve, range(8)))
    assert sum(r['claimed'] for r in results) == 1
    assert len(list(case.client.scan_iter(case.module.POLICY_PREFIX + '*'))) == 1
    assert len(case.client.zrange(case.module.JOB_INDEX, 0, -1)) == 2


@pytest.mark.parametrize('field,value', [
    ('kind', 'publish'), ('state', 'SUCCESS'), ('state', 'PROGRESS'), ('parent_id', SOURCE),
    ('retry_claimed', True), ('retry_child_task_id', CHILD), ('result', {'video_key': 'something'}),
    ('youtube', {'video_id': 'already-uploaded'}), ('youtube_automation', {'status': 'queued'}),
])
def test_source_rejections_do_not_mutate(case, field, value):
    _edit(case, case.module.JOB_PREFIX + SOURCE, field, value)
    before = _all(case.client)
    with pytest.raises(case.module.FullVideoRebuildError):
        _reserve(case)
    assert _all(case.client) == before


@pytest.mark.parametrize('field,value', [
    ('duration_minutes', True), ('duration_minutes', 5), ('mode', 'preview'), ('format', 'landscape'),
    ('workflow', 'arbitrary'), ('music', 'on'), ('production_scheduled', False), ('publish_after_render', False),
    ('production_channel_id', 'UCdifferent-channel'), ('production_connection_id', 'changed-connection'),
    ('production_profile_revision', 'changed-revision'), ('topic', 'different topic'), ('language', 'en'),
    ('production_topic_index', True), ('channel_id', 'wrong-route'),
])
def test_frozen_spec_rejections_do_not_mutate(case, field, value):
    spec = {**case.spec, field: value}
    _edit(case, case.module.JOB_PREFIX + SOURCE, 'spec', spec)
    before = _all(case.client)
    with pytest.raises(case.module.FullVideoRebuildError):
        _reserve(case)
    assert _all(case.client) == before


@pytest.mark.parametrize('prefix', ['UPLOAD_PREFIX', 'EXECUTION_LOCK_PREFIX', 'REPAIR_CHECKPOINT_CLAIM_PREFIX'])
def test_upload_intent_or_prior_repair_claim_prevents_rebuild(case, prefix):
    case.client.set(getattr(case.module, prefix) + SOURCE, 'existing-side-effect-or-claim')
    before = _all(case.client)
    with pytest.raises(case.module.FullVideoRebuildError):
        _reserve(case)
    assert _all(case.client) == before


@pytest.mark.parametrize('cap,used', [('6', '0'), ('6', '7'), ('0', '1'), ('7', '1'), ('6', 'bad')])
def test_paid_source_ledger_is_required_and_never_reset(case, cap, used):
    case.client.hset(case.module.PAID_CREATE_BUDGET_PREFIX + SOURCE, mapping={'cap': cap, 'used': used})
    before = _all(case.client)
    with pytest.raises(case.module.FullVideoRebuildError):
        _reserve(case)
    assert _all(case.client) == before


@pytest.mark.parametrize('field,value', [('auto_publish', False), ('release_mode', 'private'),
                                      ('profile_revision', 'changed'), ('production_enabled', False)])
def test_current_profile_rejected(case, field, value):
    _edit(case, case.module.PROFILE_PREFIX + CHANNEL, field, value)
    with pytest.raises(case.module.FullVideoRebuildError):
        _reserve(case)


@pytest.mark.parametrize('field,value', [('last_task_id', CHILD), ('paused_reason', 'something_else'),
                                      ('cursor', '1'), ('dispatch_status', 'uncertain'), ('active_task_id', CHILD)])
def test_schedule_must_be_exact_failed_original(case, field, value):
    case.client.hset(case.module.CHANNEL_STATE_PREFIX + CHANNEL, field, value)
    with pytest.raises(case.module.FullVideoRebuildError):
        _reserve(case)


def _add_parent(c, *, repair=False):
    m = c.module
    parent = deepcopy(c.source)
    parent.update(task_id=PARENT, retry_child_task_id=SOURCE, retry_claimed=True, retry_dispatch_state='dispatched')
    if repair:
        parent['repair_claimed'] = True
        c.client.set(m.REPAIR_CHECKPOINT_CLAIM_PREFIX + PARENT, TOKEN)
        spec = {**c.spec, 'workflow': 'scene_repair', 'repair_source_task_id': PARENT}
        _edit(c, m.JOB_PREFIX + SOURCE, 'spec', spec)
        c.spec = spec
    _write(c.client, m.JOB_PREFIX + PARENT, parent)
    _edit(c, m.JOB_PREFIX + SOURCE, 'parent_id', PARENT)
    c.client.hset(m.RETRY_DISPATCH_PREFIX + PARENT, mapping={
        'child_task_id': SOURCE, 'token': TOKEN, 'state': 'dispatched', 'mode': 'repair' if repair else 'full'})
    c.client.hset(m.RETRY_CHILD_CLAIM_PREFIX + SOURCE, mapping={'source_task_id': PARENT, 'token': TOKEN})
    c.client.set(m.RETRY_CHILD_EXECUTION_PREFIX + SOURCE, TOKEN)
    c.client.hset(m.CHANNEL_STATE_PREFIX + CHANNEL, 'last_task_id', PARENT)


@pytest.mark.parametrize('repair', [False, True])
def test_exact_claimed_failed_descendant_supported_and_preserved(case, repair):
    _add_parent(case, repair=repair)
    before = _all(case.client)
    _child(case)
    assert _get(case)['spec_sha256'] == case.module._digest(case.spec)
    for key in (case.module.JOB_PREFIX + PARENT, case.module.RETRY_DISPATCH_PREFIX + PARENT):
        assert _all(case.client)[key] == before[key]


def test_bad_lineage_and_ancestor_upload_fail_closed(case):
    _add_parent(case)
    case.client.set(case.module.UPLOAD_PREFIX + PARENT, 'upload-intent')
    with pytest.raises(case.module.FullVideoRebuildError):
        _reserve(case)


def test_two_independent_channels_can_reserve_while_first_is_running(case):
    c, m = case, case.module
    other_source, other_child = str(UUID(int=500)), str(UUID(int=501))
    _seed(c, other_source, 'UCother-actual-channel')
    _child(c)
    _edit(c, m.JOB_PREFIX + CHILD, 'state', 'PROGRESS')
    assert _reserve(c, other_child, other_source)['claimed'] is True
    assert c.state.acquire_retry_child_execution(other_child, other_source) is True
    other_spec = json.loads(c.client.get(m.JOB_PREFIX + other_child))['spec']
    assert m.get_full_rebuild_policy(other_child, other_source, other_spec)['child_task_id'] == other_child


@pytest.mark.parametrize('state', ['PENDING', 'STARTED', 'PROGRESS', 'RETRY'])
def test_active_same_channel_blocks_before_claim(case, state):
    job = {**case.source, 'task_id': CHILD, 'state': state}
    _write(case.client, case.module.JOB_PREFIX + CHILD, job)
    case.client.zadd(case.module.JOB_INDEX, {CHILD: 2})
    before = _all(case.client)
    with pytest.raises(case.module.FullVideoRebuildError):
        _reserve(case, str(UUID(int=900)))
    assert _all(case.client) == before


@pytest.mark.parametrize('mutate', ['credential', 'profile', 'schedule', 'paid', 'index', 'execution', 'source_policy'])
def test_snapshot_race_does_not_grant_authority(case, monkeypatch, mutate):
    c, m = case, case.module
    original = c.client.eval
    def changed(script, *args):
        if script == m._RESERVE:
            if mutate == 'credential':
                c.client.set(m.OAUTH_CREDENTIAL_PREFIX + CHANNEL, 'changed-encrypted-value')
            elif mutate == 'profile':
                _edit(c, m.PROFILE_PREFIX + CHANNEL, 'profile_revision', 'changed')
            elif mutate == 'schedule':
                c.client.hset(m.CHANNEL_STATE_PREFIX + CHANNEL, 'next_due', '2')
            elif mutate == 'paid':
                c.client.hset(m.PAID_CREATE_BUDGET_PREFIX + SOURCE, 'used', '5')
            elif mutate == 'index':
                c.client.zadd(m.JOB_INDEX, {str(UUID(int=999)): 7})
            elif mutate == 'execution':
                c.client.set(m.RETRY_CHILD_EXECUTION_PREFIX + CHILD, 'already-executed')
            else:
                c.client.set(m.SOURCE_PREFIX + SOURCE, '{}')
        return original(script, *args)
    monkeypatch.setattr(c.client, 'eval', changed)
    with pytest.raises(m.FullVideoRebuildError):
        _reserve(c)
    assert not c.client.exists(m.RETRY_DISPATCH_PREFIX + SOURCE)
    assert not c.client.exists(m.JOB_PREFIX + CHILD)


@pytest.mark.parametrize('mutate', ['spec', 'flags', 'execution', 'profile', 'credential', 'schedule', 'source_cap',
                                  'child_paid', 'child_audio', 'child_upload', 'source_policy', 'child_state'])
def test_getter_rejects_forged_or_stale_proof_without_mutation(case, mutate):
    c, m = case, case.module
    _child(c)
    if mutate == 'spec':
        c.spec['topic'] = 'Changed after authorization'
    elif mutate == 'flags':
        _edit(c, m.POLICY_PREFIX + CHILD, 'fresh_voice', 1)
    elif mutate == 'execution':
        c.client.set(m.RETRY_CHILD_EXECUTION_PREFIX + CHILD, 'incorrect-token')
    elif mutate == 'profile':
        _edit(c, m.PROFILE_PREFIX + CHANNEL, 'profile_revision', 'changed')
    elif mutate == 'credential':
        c.client.set(m.OAUTH_CREDENTIAL_PREFIX + CHANNEL, 'changed-encrypted-value')
    elif mutate == 'schedule':
        c.client.hset(m.CHANNEL_STATE_PREFIX + CHANNEL, 'next_due', '2')
    elif mutate == 'source_cap':
        c.client.hset(m.PAID_CREATE_BUDGET_PREFIX + SOURCE, 'cap', '5')
    elif mutate == 'child_paid':
        c.client.hset(m.PAID_CREATE_BUDGET_PREFIX + CHILD, mapping={'cap': '6', 'used': '1'})
    elif mutate == 'child_audio':
        _edit(c, m.JOB_PREFIX + CHILD, 'audio_candidate_checkpoint', {'already-created': True})
    elif mutate == 'child_upload':
        c.client.set(m.UPLOAD_PREFIX + CHILD, 'existing-upload')
    elif mutate == 'source_policy':
        c.client.delete(m.SOURCE_PREFIX + SOURCE)
    else:
        _edit(c, m.JOB_PREFIX + CHILD, 'state', 'FAILURE')
    before = _all(c.client)
    with pytest.raises(m.FullVideoRebuildError):
        _get(c)
    assert _all(c.client) == before


def test_source_public_flags_cannot_mint_authorization(case):
    _edit(case, case.module.JOB_PREFIX + SOURCE, 'full_rebuild', {'fresh_voice': True})
    with pytest.raises(case.module.FullVideoRebuildError):
        _get(case)
    with pytest.raises(case.module.FullVideoRebuildError):
        case.state.claim_retry_dispatch(SOURCE, CHILD, TOKEN, allow_repair=False,
                                        full_rebuild_binding={'fresh_voice': True})


def test_dispatch_uses_exact_spec_once_and_preserves_lineage(case):
    c, m = case, case.module
    result = m.dispatch_full_video_rebuild(SOURCE)
    assert result['status'] == 'dispatched'
    call = c.calls[0]
    assert call['args'][:4] == (c.spec['topic'], .5, 'tr', c.spec['channel_id'])
    assert call['args'][5:] == (None, SOURCE)
    assert call['kwargs'] == {'full_rebuild_source_id': SOURCE}
    assert call['task_id'] == result['child_task_id']
    assert m.dispatch_full_video_rebuild(SOURCE)['status'] == 'already_claimed'
    assert len(c.calls) == 1


def test_uncertain_broker_response_never_redispatches(case):
    c, m = case, case.module
    def lost(**kwargs):
        c.calls.append(kwargs)
        raise TimeoutError('simulated broker accepted message but reply was lost')
    m.run_video_pipeline.apply_async = lost
    result = m.dispatch_full_video_rebuild(SOURCE)
    assert result['status'] == 'dispatch_uncertain'
    assert c.client.hget(m.RETRY_DISPATCH_PREFIX + SOURCE, 'state') == 'uncertain'
    assert m.dispatch_full_video_rebuild(SOURCE)['status'] == 'already_claimed'
    assert len(c.calls) == 1


def test_lost_atomic_reply_never_enqueue_or_create_second_child(case, monkeypatch):
    c, m = case, case.module
    original = c.client.eval
    def lost(script, *args):
        result = original(script, *args)
        if script == m._RESERVE:
            raise TimeoutError('lost atomic reply')
        return result
    monkeypatch.setattr(c.client, 'eval', lost)
    with pytest.raises(m.FullVideoRebuildError, match='full_rebuild_unavailable'):
        m.dispatch_full_video_rebuild(SOURCE)
    assert m.dispatch_full_video_rebuild(SOURCE)['status'] == 'already_claimed'
    assert c.calls == []
    assert len(list(c.client.scan_iter(m.POLICY_PREFIX + '*'))) == 1


def test_repeat_source_read_cannot_replace_original_eligibility_snapshot(case, monkeypatch):
    c, m = case, case.module
    original = m._idle_channel
    def changed(client, channel_id, snapshots):
        _edit(c, m.JOB_PREFIX + SOURCE, 'spec', {**c.spec, 'topic': 'changed during preflight'})
        original(client, channel_id, snapshots)
    monkeypatch.setattr(m, '_idle_channel', changed)
    with pytest.raises(m.FullVideoRebuildError, match='full_rebuild_state_changed'):
        _reserve(c)
    assert not c.client.exists(m.RETRY_DISPATCH_PREFIX + SOURCE)


def test_source_paid_usage_cannot_change_after_reservation(case):
    _child(case)
    case.client.hset(case.module.PAID_CREATE_BUDGET_PREFIX + SOURCE, 'used', '5')
    with pytest.raises(case.module.FullVideoRebuildError):
        _get(case)


def test_same_channel_job_started_after_reservation_blocks_worker_before_cost(case):
    c, m = case, case.module
    _child(c)
    manual_id = str(UUID(int=888))
    _write(c.client, m.JOB_PREFIX + manual_id, {**c.source, 'task_id': manual_id, 'state': 'STARTED'})
    c.client.zadd(m.JOB_INDEX, {manual_id: 2})
    before = _all(c.client)
    with pytest.raises(m.FullVideoRebuildError, match='full_rebuild_channel_busy'):
        _get(c)
    assert _all(c.client) == before
