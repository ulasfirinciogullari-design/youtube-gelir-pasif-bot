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
            if node.module == 'app.services.channel_cadence':
                return node
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
    from app.services.production_failures import classify_failure
    client = fakeredis.FakeRedis(decode_responses=True)
    settings = SimpleNamespace(redis_url='redis://never-used')
    state = _load('studio_state', {'settings': settings, 'classify_failure': classify_failure})
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


GRANDCHILD = '18500000-0000-4000-8000-000000000005'


def _failed_planning_child(c):
    _child(c)
    job = json.loads(c.client.get(c.module.JOB_PREFIX + CHILD))
    job.update(state='FAILURE', stage='failed', failure_stage='director_qc', error='Story planning rejected')
    _write(c.client, c.module.JOB_PREFIX + CHILD, job)
    c.client.hset(c.module.PAID_CREATE_BUDGET_PREFIX + CHILD, mapping={'cap': '6', 'used': '0'})


def test_zero_media_full_rebuild_planning_failure_can_reserve_one_fresh_child(case):
    c, m = case, case.module
    _failed_planning_child(c)
    before = _all(c.client)
    result = _reserve(c, GRANDCHILD, CHILD)
    assert result['claimed'] is True and result['spec'] == c.spec
    assert result['full_rebuild']['source_task_id'] == CHILD
    assert result['full_rebuild']['original_paid_create_cap'] == 6
    assert c.state.acquire_retry_child_execution(GRANDCHILD, CHILD) is True
    assert m.get_full_rebuild_policy(GRANDCHILD, CHILD, c.spec) == result['full_rebuild']
    new_policy = json.loads(c.client.get(m.POLICY_PREFIX + GRANDCHILD))
    old_policy = json.loads(c.client.get(m.POLICY_PREFIX + CHILD))
    assert new_policy['inherited_policy_sha256'] == m._digest(old_policy)
    assert 'inherited_policy_sha256' not in result['full_rebuild']
    for key in (m.JOB_PREFIX + SOURCE, m.PAID_CREATE_BUDGET_PREFIX + SOURCE,
                m.PAID_CREATE_BUDGET_PREFIX + CHILD, m.RETRY_DISPATCH_PREFIX + SOURCE,
                m.RETRY_CHILD_CLAIM_PREFIX + CHILD, m.RETRY_CHILD_EXECUTION_PREFIX + CHILD,
                m.REPAIR_CHECKPOINT_PREFIX + SOURCE, m.SOURCE_PREFIX + SOURCE, m.POLICY_PREFIX + CHILD):
        assert _all(c.client)[key] == before[key]
    assert not c.client.exists(m.PAID_CREATE_BUDGET_PREFIX + GRANDCHILD)
    current = _all(c.client)
    assert _reserve(c, str(UUID(int=701)), CHILD) == {'claimed': False, 'child_task_id': GRANDCHILD}
    assert _all(c.client) == current


@pytest.mark.parametrize('field,value', [
    ('failure_stage', 'audio_qc'), ('failure_stage', 'final_visual_qc'), ('failure_stage', 'plan_retry'),
    ('state', 'PROGRESS'), ('audio_candidate_checkpoint', {}), ('audio_candidate_checkpoint_error', 'unavailable'),
    ('generated_asset_candidates', {}), ('voice_candidate_reuse', {}), ('voice_replacement', {}),
    ('repair_checkpoint', {}), ('qa_workprint', {}), ('result', {}), ('video_key', ''),
    ('youtube', {}), ('youtube_automation', {}),
])
def test_zero_media_exception_rejects_nonplanning_or_any_media_marker(case, field, value):
    _failed_planning_child(case)
    _edit(case, case.module.JOB_PREFIX + CHILD, field, value)
    before = _all(case.client)
    with pytest.raises(case.module.FullVideoRebuildError):
        _reserve(case, GRANDCHILD, CHILD)
    assert _all(case.client) == before


@pytest.mark.parametrize('field,value', [
    ('version', True), ('mode', 'repair'), ('child_task_id', GRANDCHILD), ('source_task_id', PARENT),
    ('lineage_root_task_id', CHILD), ('spec_sha256', 'f' * 64), ('original_paid_create_cap', 5),
    ('fresh_voice', 1), ('fresh_media', False), ('requires_full_qa', False),
    ('dispatch_token_sha256', 'e' * 64), ('source_paid_ledger_sha256', 'd' * 64),
    ('credential_sha256', 'c' * 64), ('schedule_sha256', 'b' * 64),
])
def test_zero_media_exception_requires_complete_original_private_authorization(case, field, value):
    _failed_planning_child(case)
    for key in (case.module.POLICY_PREFIX + CHILD, case.module.SOURCE_PREFIX + SOURCE):
        _edit(case, key, field, value)
    before = _all(case.client)
    with pytest.raises(case.module.FullVideoRebuildError):
        _reserve(case, GRANDCHILD, CHILD)
    assert _all(case.client) == before


@pytest.mark.parametrize('mutation', ['missing_policy', 'source_policy_mismatch', 'execution', 'cap',
                                      'parent_ledger', 'private_checkpoint', 'spec', 'profile', 'used_format'])
def test_zero_media_exception_rejects_missing_or_stale_binding(case, mutation):
    c, m = case, case.module
    _failed_planning_child(c)
    if mutation == 'missing_policy':
        c.client.delete(m.POLICY_PREFIX + CHILD)
    elif mutation == 'source_policy_mismatch':
        _edit(c, m.SOURCE_PREFIX + SOURCE, 'version', 2)
    elif mutation == 'execution':
        c.client.set(m.RETRY_CHILD_EXECUTION_PREFIX + CHILD, 'different-claim-token')
    elif mutation == 'cap':
        c.client.hset(m.PAID_CREATE_BUDGET_PREFIX + CHILD, 'cap', '5')
    elif mutation == 'parent_ledger':
        c.client.hset(m.PAID_CREATE_BUDGET_PREFIX + SOURCE, 'used', '5')
    elif mutation == 'private_checkpoint':
        c.client.set(m.REPAIR_CHECKPOINT_PREFIX + CHILD, 'existing-private-checkpoint')
    elif mutation == 'spec':
        _edit(c, m.JOB_PREFIX + CHILD, 'spec', {**c.spec, 'workflow': 'scene_repair'})
    elif mutation == 'profile':
        _edit(c, m.PROFILE_PREFIX + CHANNEL, 'profile_revision', 'changed')
    else:
        c.client.hset(m.PAID_CREATE_BUDGET_PREFIX + CHILD, 'used', '00')
    before = _all(c.client)
    with pytest.raises(m.FullVideoRebuildError):
        _reserve(c, GRANDCHILD, CHILD)
    assert _all(c.client) == before


@pytest.mark.parametrize('mutation', ['policy', 'parent_ledger', 'zero_ledger', 'private_checkpoint'])
def test_zero_media_ancestry_race_is_compared_before_atomic_claim(case, monkeypatch, mutation):
    c, m = case, case.module
    _failed_planning_child(c)
    original = c.client.eval
    def changed(script, *args):
        if script == m._RESERVE:
            if mutation == 'policy':
                _edit(c, m.POLICY_PREFIX + CHILD, 'mode', 'repair')
            elif mutation == 'parent_ledger':
                c.client.hset(m.PAID_CREATE_BUDGET_PREFIX + SOURCE, 'used', '5')
            elif mutation == 'zero_ledger':
                c.client.hset(m.PAID_CREATE_BUDGET_PREFIX + CHILD, 'used', '1')
            else:
                c.client.set(m.REPAIR_CHECKPOINT_PREFIX + CHILD, 'late-checkpoint')
        return original(script, *args)
    monkeypatch.setattr(c.client, 'eval', changed)
    with pytest.raises(m.FullVideoRebuildError):
        _reserve(c, GRANDCHILD, CHILD)
    assert not c.client.exists(m.RETRY_DISPATCH_PREFIX + CHILD)
    assert not c.client.exists(m.JOB_PREFIX + GRANDCHILD)


def test_zero_media_child_dispatch_keeps_verified_full_rebuild_worker_path(case):
    _failed_planning_child(case)
    result = case.module.dispatch_full_video_rebuild(CHILD)
    assert result['status'] == 'dispatched'
    assert case.calls[0]['args'][5:] == (None, CHILD)
    assert case.calls[0]['kwargs'] == {'full_rebuild_source_id': CHILD}
    assert case.module.dispatch_full_video_rebuild(CHILD)['status'] == 'already_claimed'
    assert len(case.calls) == 1


def test_zero_media_concurrent_operator_retry_is_exactly_once(case):
    _failed_planning_child(case)
    def reserve(i):
        try:
            return _reserve(case, str(UUID(int=810 + i)), CHILD)
        except case.module.FullVideoRebuildError:
            return {'claimed': False}
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(reserve, range(6)))
    assert sum(result['claimed'] for result in results) == 1
    assert len(list(case.client.scan_iter(case.module.POLICY_PREFIX + '*'))) == 2


def _failed_english_audio_child(c):
    c.spec = {**c.spec, 'language': 'en'}
    _edit(c, c.module.JOB_PREFIX + SOURCE, 'spec', c.spec)
    _edit(c, c.module.PROFILE_PREFIX + CHANNEL, 'default_language', 'en')
    _failed_planning_child(c)
    prefix = f'audio_candidates/{CHILD}/' + 'a' * 64
    pointer = {'version': 1, 'status': 'unapproved_candidate', 'qa_approved': False,
               'requires_full_qa': True, 'audio_sha256': 'a' * 64, 'metadata_sha256': 'b' * 64,
               'package_sha256': 'c' * 64, 'audio_key': prefix + '/candidate.mp3',
               'metadata_key': prefix + '/metadata-' + 'b' * 64 + '.json', 'size': 607148}
    _edit(c, c.module.JOB_PREFIX + CHILD, 'failure_stage', 'audio_qc_retry')
    _edit(c, c.module.JOB_PREFIX + CHILD, 'audio_candidate_checkpoint', pointer)
    return pointer


def test_english_premedia_audio_failure_retains_candidate_and_requires_full_qa(case):
    c, m = case, case.module
    pointer = _failed_english_audio_child(c)
    before = _all(c.client)
    result = _reserve(c, GRANDCHILD, CHILD)
    assert result['claimed'] is True and result['spec'] == c.spec
    assert result['checkpoint'] is None
    assert all(result['full_rebuild'][k] is True for k in m._FLAGS)
    private = json.loads(c.client.get(m.POLICY_PREFIX + GRANDCHILD))
    assert private['retained_audio_checkpoint_sha256'] == m._digest(pointer)
    assert 'retained_audio_checkpoint_sha256' not in result['full_rebuild']
    assert json.loads(c.client.get(m.JOB_PREFIX + CHILD))['audio_candidate_checkpoint'] == pointer
    for key in (m.JOB_PREFIX + SOURCE, m.PAID_CREATE_BUDGET_PREFIX + SOURCE,
                m.PAID_CREATE_BUDGET_PREFIX + CHILD, m.POLICY_PREFIX + CHILD,
                m.SOURCE_PREFIX + SOURCE, m.RETRY_DISPATCH_PREFIX + SOURCE,
                m.RETRY_CHILD_CLAIM_PREFIX + CHILD, m.RETRY_CHILD_EXECUTION_PREFIX + CHILD,
                m.CHANNEL_STATE_PREFIX + CHANNEL):
        assert _all(c.client)[key] == before[key]
    assert c.state.acquire_retry_child_execution(GRANDCHILD, CHILD) is True
    assert m.get_full_rebuild_policy(GRANDCHILD, CHILD, c.spec) == result['full_rebuild']
    assert _reserve(c, str(UUID(int=903)), CHILD) == {'claimed': False, 'child_task_id': GRANDCHILD}


@pytest.mark.parametrize('field,value', [
    ('version', True), ('version', 2), ('qa_approved', True), ('requires_full_qa', False),
    ('status', 'approved'), ('audio_sha256', 'a' * 63), ('metadata_sha256', 'B' * 64),
    ('package_sha256', None), ('size', True), ('size', 1023), ('size', 14 * 1024 * 1024 + 1),
    ('audio_key', f'audio_candidates/{SOURCE}/' + 'a' * 64 + '/candidate.mp3'),
    ('audio_key', 'https://example.com/private.mp3'), ('metadata_key', '../metadata.json'),
    ('unexpected_field', 'not-allowed'),
])
def test_audio_continuation_rejects_invalid_or_cross_job_pointer_without_mutation(case, field, value):
    pointer = _failed_english_audio_child(case)
    _edit(case, case.module.JOB_PREFIX + CHILD, 'audio_candidate_checkpoint', {**pointer, field: value})
    before = _all(case.client)
    with pytest.raises(case.module.FullVideoRebuildError):
        _reserve(case, GRANDCHILD, CHILD)
    assert _all(case.client) == before


@pytest.mark.parametrize('field,value', [
    ('audio_candidate_checkpoint', None), ('failure_stage', 'audio_qc'),
    ('failure_stage', 'director_qc'), ('failure_stage', 'final_visual_qc'),
    ('audio_candidate_checkpoint_error', 'unavailable'), ('generated_asset_candidates', {}),
    ('voice_candidate_reuse', {}), ('voice_replacement', {}), ('repair_checkpoint', {}),
    ('qa_workprint', {}), ('result', {}), ('youtube', {}), ('youtube_automation', {}),
])
def test_audio_continuation_rejects_wrong_stage_and_other_artifacts(case, field, value):
    _failed_english_audio_child(case)
    _edit(case, case.module.JOB_PREFIX + CHILD, field, value)
    before = _all(case.client)
    with pytest.raises(case.module.FullVideoRebuildError):
        _reserve(case, GRANDCHILD, CHILD)
    assert _all(case.client) == before


def test_audio_continuation_is_not_a_general_turkish_voice_replacement(case):
    pointer = _failed_english_audio_child(case)
    # Directly exercise the narrow gate: forged runtime language cannot expand it.
    job = json.loads(case.client.get(case.module.JOB_PREFIX + CHILD))
    assert job['audio_candidate_checkpoint'] == pointer
    with pytest.raises(case.module.FullVideoRebuildError):
        case.module._premedia_checkpoint_binding(job, {**case.spec, 'language': 'tr'})


@pytest.mark.parametrize('mutation', ['missing_grant', 'ledger', 'repair', 'upload'])
def test_audio_checkpoint_alone_never_grants_a_new_paid_pipeline(case, mutation):
    c, m = case, case.module
    _failed_english_audio_child(c)
    if mutation == 'missing_grant':
        c.client.delete(m.POLICY_PREFIX + CHILD)
    elif mutation == 'ledger':
        c.client.hset(m.PAID_CREATE_BUDGET_PREFIX + SOURCE, 'used', '5')
    elif mutation == 'repair':
        c.client.set(m.REPAIR_CHECKPOINT_PREFIX + CHILD, 'retained-private-repair')
    else:
        c.client.set(m.UPLOAD_PREFIX + CHILD, 'existing-upload-intent')
    before = _all(c.client)
    with pytest.raises(m.FullVideoRebuildError):
        _reserve(c, GRANDCHILD, CHILD)
    assert _all(c.client) == before


@pytest.mark.parametrize('when', ['reservation', 'execution'])
def test_retained_audio_pointer_is_compared_atomically_and_rechecked_before_cost(case, monkeypatch, when):
    c, m = case, case.module
    pointer = _failed_english_audio_child(c)
    if when == 'execution':
        _reserve(c, GRANDCHILD, CHILD)
        assert c.state.acquire_retry_child_execution(GRANDCHILD, CHILD) is True
        _edit(c, m.JOB_PREFIX + CHILD, 'audio_candidate_checkpoint', {**pointer, 'package_sha256': 'd' * 64})
        with pytest.raises(m.FullVideoRebuildError):
            m.get_full_rebuild_policy(GRANDCHILD, CHILD, c.spec)
    else:
        original = c.client.eval
        def changed(script, *args):
            if script == m._RESERVE:
                _edit(c, m.JOB_PREFIX + CHILD, 'audio_candidate_checkpoint', {**pointer, 'package_sha256': 'd' * 64})
            return original(script, *args)
        monkeypatch.setattr(c.client, 'eval', changed)
        with pytest.raises(m.FullVideoRebuildError):
            _reserve(c, GRANDCHILD, CHILD)
        assert not c.client.exists(m.RETRY_DISPATCH_PREFIX + CHILD)
        assert not c.client.exists(m.JOB_PREFIX + GRANDCHILD)


def test_audio_continuation_dispatch_uses_fresh_verified_worker_once(case):
    _failed_english_audio_child(case)
    result = case.module.dispatch_full_video_rebuild(CHILD)
    assert result['status'] == 'dispatched'
    assert case.calls[0]['kwargs'] == {'full_rebuild_source_id': CHILD}
    assert case.calls[0]['args'][5:] == (None, CHILD)
    assert case.module.dispatch_full_video_rebuild(CHILD)['status'] == 'already_claimed'
    assert len(case.calls) == 1
