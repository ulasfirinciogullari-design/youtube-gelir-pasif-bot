"""Public-delivery extension exercised with the real existing Lua/fixtures."""
import ast
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from test_production_recovery import (
    ROOT, CHANNEL, REVISION, CONNECTION_ID, VIDEO_ID, recovery as private_recovery,
    _seed, _change, _write, _digest, _snapshot,
)


def _public_seed(module, client, *, hops=3, repair=True):
    data = _seed(module, client, hops=hops)
    namespace = module._resume_after_retry.__globals__
    # Resolve the actual state-key constants rather than guessing their names.
    tree = ast.parse((ROOT / 'app/services/studio_state.py').read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {
                    'RETRY_CHILD_EXECUTION_PREFIX', 'REPAIR_CHECKPOINT_CLAIM_PREFIX',
                }:
                    value = ast.literal_eval(node.value)
                    namespace[target.id] = value
                    setattr(module, target.id, value)
    tree = ast.parse((ROOT / 'app/services/youtube_automation.py').read_text(encoding='utf-8'))
    gate = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                and node.name == 'contains_synthetic_media')
    namespace['Any'] = Any
    exec(compile(ast.Module(body=[gate], type_ignores=[]), '<real-disclosure-gate>', 'exec'), namespace)
    profile_key = module.PROFILE_PREFIX + CHANNEL
    profile = json.loads(client.get(profile_key))
    profile.update(release_mode='public', production_interval_hours=6, require_thumbnail=True)
    profile['production_topics'].insert(0, 'Earlier successfully published episode')
    _write(client, profile_key, profile)
    client.hset(data.state_key, mapping={
        'cursor': '2', 'consumed_prefix': _digest(profile['production_topics'][:2]), 'next_due': '110000',
    })
    for i, task_id in enumerate(data.ids):
        def update_job(job):
            job['spec']['production_topic_index'] = 1
            job['spec']['production_editorial'] = {'format': 'shorts', 'reason': 'single_source_fact'}
            if i == 0 or not repair:
                job['spec']['workflow'] = 'auto'
                job['spec'].pop('repair_source_task_id', None)
            if i < len(data.ids) - 1:
                job['repair_claimed'] = repair
        _change(client, module.JOB_PREFIX + task_id, update_job)
        if i:
            parent = data.ids[i - 1]
            token = client.hget(module.RETRY_DISPATCH_PREFIX + parent, 'token')
            client.set(module.RETRY_CHILD_EXECUTION_PREFIX + task_id, token)
            client.hset(module.RETRY_DISPATCH_PREFIX + parent, 'mode', 'repair' if repair else 'full')
            if repair:
                client.set(module.REPAIR_CHECKPOINT_CLAIM_PREFIX + parent, token)
    def public_source(job):
        job['result'].update(task_id=data.recovered_id, status='complete',
                             video_key=f'videos/{data.recovered_id}/final.mp4',
                             caption_key=f'videos/{data.recovered_id}/captions.tr.srt')
        job['result']['youtube_automation']['release_mode'] = 'public'
        job['result']['youtube'].update(
            privacy_status='public', release_status='public', contains_synthetic_media=True,
            caption_uploaded=True, caption_error_code=None, thumbnail_uploaded=True, thumbnail_error_code=None,
        )
    _change(client, module.JOB_PREFIX + data.recovered_id, public_source)
    def public_upload(record):
        record.update(status='uploading')
        record['publish_plan'].update(release_mode='public', contains_synthetic_media=True, require_thumbnail=True)
    _change(client, module.UPLOAD_PREFIX + data.recovered_id, public_upload)
    # Produce the ledger with actual production transitions, including the
    # privacy_status that mark_release_completed adds only at public completion.
    tree = ast.parse((ROOT / 'app/services/youtube_publish_state.py').read_text(encoding='utf-8'))
    tree.body = [node for node in tree.body if not (
        isinstance(node, ast.ImportFrom) and (node.module or '').startswith('app.')
    )]
    state_ns = {'settings': SimpleNamespace(redis_url='redis://not-used')}
    exec(compile(tree, '<real-publication-state>', 'exec'), state_ns)
    state_ns['_redis'] = lambda: client
    state_ns['mark_upload_completed'](data.recovered_id, data.publish_id, VIDEO_ID, release_mode='public')
    state_ns['mark_release_ready'](data.recovered_id, data.publish_id)
    state_ns['mark_release_started'](data.recovered_id, data.publish_id)
    ledger = state_ns['mark_release_completed'](data.recovered_id, data.publish_id, release_mode='public')
    assert ledger['privacy_status'] == ledger['release_status'] == 'public'
    # Execute the actual publisher's result/attribution dict expressions. No
    # publisher task, credentials, Google call, or fixture-invented proof runs.
    tree = ast.parse((ROOT / 'app/publish_tasks.py').read_text(encoding='utf-8'))
    publisher_fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == 'publish_video_pipeline')
    nodes = [node for node in ast.walk(publisher_fn) if isinstance(node, ast.Assign)
             and isinstance(node.value, ast.Dict)
             and any(isinstance(target, ast.Name) and target.id in {'result', 'youtube_attribution'}
                     for target in node.targets)
             and any(isinstance(key, ast.Constant) and key.value == 'caption_uploaded'
                     for key in node.value.keys)]
    assert len(nodes) == 2
    publisher_ns = {
        'task_id': data.publish_id, 'source_task_id': data.recovered_id, 'video_id': VIDEO_ID,
        'youtube_url': f'https://www.youtube.com/watch?v={VIDEO_ID}', 'final_privacy_status': 'public',
        'synthetic_disclosure': True, 'release_status': 'public', 'scheduled_publish_at': None,
        'release_error_code': None, 'uploaded_at': '2026-09-06T00:00:00+00:00',
        'caption_result': {'id': 'caption-fixture'}, 'caption_error_code': None,
        'thumbnail_result': {'ok': True}, 'thumbnail_error_code': None,
        'target_channel_id': CHANNEL, 'connection_id': CONNECTION_ID,
        'channel': {'id': CHANNEL, 'connection_id': CONNECTION_ID},
        'publish_plan': ledger['publish_plan'], 'title': 'Real publisher test', 'language': 'tr',
        'category_id': '27',
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), '<real-publisher-results>', 'exec'), publisher_ns)
    def public_publisher(job):
        job['spec']['release_mode'] = 'public'
        job['result'] = publisher_ns['result']
    _change(client, module.JOB_PREFIX + data.publish_id, public_publisher)
    _change(client, module.JOB_PREFIX + data.recovered_id,
            lambda job: job['result'].update(youtube=publisher_ns['youtube_attribution']))
    data.private_audit_key = data.audit_key
    data.audit_key = module.PUBLIC_RESUME_PREFIX + CHANNEL + ':' + data.original_id
    data.profile = profile
    return data


@pytest.fixture
def case(private_recovery):
    module, client = private_recovery
    return module, client, _public_seed(module, client)


def _run(case, *, now=100000):
    module, _, data = case
    return module.resume_after_public_retry(CHANNEL, data.original_id, data.recovered_id, REVISION, now=now)


def _paused(case):
    module, client, data = case
    assert client.hget(data.state_key, 'paused_reason') == 'previous_render_failed'
    assert client.hget(data.state_key, 'next_due') == '110000'
    assert not client.exists(data.audit_key)


def test_public_recovery_changes_only_pause_due_and_its_separate_audit(case):
    module, client, data = case
    before = _snapshot(client)
    result = _run(case)
    assert result['status'] == 'resumed'
    assert result['release_mode'] == result['release_status'] == 'public'
    assert result['contains_synthetic_media'] is result['caption_uploaded'] is True
    assert result['next_due'] == 121600 and result['cursor'] == 2
    assert not client.exists(data.private_audit_key)
    after = _snapshot(client)
    audit = json.loads(after.pop(data.audit_key))
    assert audit == {k: v for k, v in result.items() if k != 'status'}
    state = after.pop(data.state_key)
    expected = before.pop(data.state_key)
    expected.pop('paused_reason')
    expected['next_due'] = '121600'
    assert state == expected and after == before
    assert client.ttl(data.audit_key) == -1


def test_existing_private_function_never_accepts_public_proof(case):
    module, client, data = case
    with pytest.raises(module.ProductionRecoveryError):
        module.resume_after_private_retry(CHANNEL, data.original_id, data.recovered_id, REVISION, now=100000)
    _paused(case)
    assert not client.exists(data.private_audit_key)


def test_private_audit_cannot_satisfy_public_recovery(case):
    module, client, data = case
    client.set(data.private_audit_key, '{"status":"resumed"}')
    assert _run(case)['status'] == 'resumed'
    assert client.get(data.private_audit_key) == '{"status":"resumed"}'


def test_public_replay_is_idempotent_after_next_job_begins(case):
    module, client, data = case
    first = _run(case)
    client.set(module.ACTIVE_KEY, 'new-active-job')
    client.hset(data.state_key, mapping={'cursor': '3', 'next_due': '999999', 'active_task_id': 'later'})
    before = _snapshot(client)
    again = _run(case, now=200000)
    assert again == {**first, 'status': 'already_resumed'}
    assert _snapshot(client) == before


def test_concurrent_resumptions_commit_once(case):
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: _run(case), range(6)))
    assert sum(result['status'] == 'resumed' for result in results) == 1
    assert {result['next_due'] for result in results} == {121600}


def test_lost_atomic_reply_is_reconciled_without_delaying_again(case, monkeypatch):
    module, client, data = case
    execute = client.eval
    def lost(*args, **kwargs):
        execute(*args, **kwargs)
        raise ConnectionError('secret transport detail')
    monkeypatch.setattr(client, 'eval', lost)
    with pytest.raises(module.ProductionRecoveryError, match='^recovery_state_unavailable$'):
        _run(case)
    monkeypatch.setattr(client, 'eval', execute)
    assert _run(case, now=900000)['status'] == 'already_resumed'
    assert client.hget(data.state_key, 'next_due') == '121600'


@pytest.mark.parametrize('target,path,value', [
    ('profile', ['release_mode'], 'private'), ('profile', ['release_mode'], 'scheduled'),
    ('profile', ['profile_revision'], 'changed'), ('profile', ['auto_publish'], False),
    ('profile', ['production_enabled'], False), ('profile', ['production_interval_hours'], 5),
    ('source', ['state'], 'FAILURE'), ('source', ['kind'], 'publish'),
    ('source', ['result', 'manual_qa_required'], True),
    ('source', ['result', 'quality_disposition'], 'manual_approved'),
    ('source', ['result', 'task_id'], str(UUID(int=9999))),
    ('source', ['result', 'task_id'], None), ('source', ['result', 'status'], None),
    ('source', ['result', 'video_key'], 'videos/other/final.mp4'),
    ('source', ['result', 'caption_key'], 'videos/other/captions.tr.srt'),
    ('source', ['result', 'youtube_automation', 'release_mode'], 'private'),
    ('source', ['result', 'youtube_automation', 'target_channel_id'], 'UC_wrongchannel'),
    ('source', ['result', 'youtube', 'privacy_status'], 'private'),
    ('source', ['result', 'youtube', 'release_status'], 'uncertain'),
    ('source', ['result', 'youtube', 'contains_synthetic_media'], False),
    ('source', ['result', 'youtube', 'caption_uploaded'], False),
    ('source', ['result', 'youtube', 'caption_error_code'], 'unconfirmed'),
    ('source', ['result', 'youtube', 'thumbnail_uploaded'], False),
    ('source', ['result', 'youtube', 'thumbnail_error_code'], 'unconfirmed'),
    ('source', ['spec', 'production_editorial'], {'changed': True}),
    ('source', ['spec', 'unknown_future_render_option'], 'changed'),
    ('publisher', ['parent_id'], str(UUID(int=9999))), ('publisher', ['state'], 'FAILURE'),
    ('publisher', ['result', 'task_id'], str(UUID(int=9999))),
    ('publisher', ['result', 'task_id'], None),
    ('publisher', ['result', 'release_status'], 'blocked'),
    ('publisher', ['result', 'caption_uploaded'], False),
    ('publisher', ['result', 'contains_synthetic_media'], None),
    ('publisher', ['result', 'thumbnail_uploaded'], False),
    ('publisher', ['result', 'profile_revision'], 'changed'),
    ('publisher', ['spec', 'release_mode'], 'private'),
    ('ledger', ['status'], 'uploading'), ('ledger', ['version'], 1),
    ('ledger', ['youtube_video_id'], 'Another1234'),
    ('ledger', ['source_task_id'], str(UUID(int=9999))),
    ('ledger', ['requested_release_mode'], 'private'),
    ('ledger', ['release_status'], 'private'), ('ledger', ['release_status'], 'releasing'),
    ('ledger', ['release_status'], 'uncertain'), ('ledger', ['release_status'], 'blocked'),
    ('ledger', ['privacy_status'], 'private'), ('ledger', ['release_side_effect_possible'], False),
    ('ledger', ['release_completed_at'], None), ('ledger', ['release_error_code'], 'uncertain'),
    ('ledger', ['publish_plan', 'contains_synthetic_media'], False),
    ('ledger', ['publish_plan', 'contains_synthetic_media'], None),
    ('ledger', ['publish_plan', 'release_mode'], 'private'),
    ('ledger', ['publish_plan', 'profile_revision'], 'changed'),
])
def test_public_evidence_is_required_before_any_state_write(case, target, path, value):
    module, client, data = case
    key = {'profile': module.PROFILE_PREFIX + CHANNEL, 'source': module.JOB_PREFIX + data.recovered_id,
           'publisher': module.JOB_PREFIX + data.publish_id, 'ledger': module.UPLOAD_PREFIX + data.recovered_id}[target]
    def change(record):
        current = record
        for part in path[:-1]:
            current = current[part]
        current[path[-1]] = value
    _change(client, key, change)
    before = _snapshot(client)
    with pytest.raises(module.ProductionRecoveryError):
        _run(case)
    assert _snapshot(client) == before
    _paused(case)


@pytest.mark.parametrize('kind', ['execution_missing', 'execution_wrong', 'claim_wrong', 'dispatch_child',
                                  'dispatch_mode', 'repair_missing', 'repair_wrong', 'repair_unclaimed'])
def test_each_executed_reciprocal_edge_is_bound(case, kind):
    module, client, data = case
    parent, child = data.ids[-2:]
    if kind.startswith('execution'):
        key = module.RETRY_CHILD_EXECUTION_PREFIX + child
        client.delete(key) if kind == 'execution_missing' else client.set(key, 'wrong')
    elif kind == 'claim_wrong':
        client.hset(module.RETRY_CHILD_CLAIM_PREFIX + child, 'token', 'wrong')
    elif kind.startswith('dispatch'):
        client.hset(module.RETRY_DISPATCH_PREFIX + parent,
                    'child_task_id' if kind == 'dispatch_child' else 'mode', 'wrong')
    elif kind == 'repair_unclaimed':
        _change(client, module.JOB_PREFIX + parent, lambda record: record.update(repair_claimed=False))
    else:
        key = module.REPAIR_CHECKPOINT_CLAIM_PREFIX + parent
        client.delete(key) if kind == 'repair_missing' else client.set(key, 'wrong')
    with pytest.raises(module.ProductionRecoveryError):
        _run(case)
    _paused(case)


@pytest.mark.parametrize('race', ['profile', 'credential_replace', 'credential_delete', 'channel', 'membership',
                                'source', 'publisher', 'ledger', 'ancestor', 'ancestor_upload',
                                'child_claim', 'execution', 'repair_claim', 'dispatch', 'cursor', 'pause', 'active'])
def test_full_public_snapshot_set_is_rechecked_by_real_lua(case, monkeypatch, race):
    module, client, data = case
    execute = client.eval
    def changed(*args, **kwargs):
        if race == 'profile':
            _change(client, module.PROFILE_PREFIX + CHANNEL, lambda record: record.update(profile_revision='changed'))
        elif race == 'credential_replace':
            client.set(module.OAUTH_CREDENTIAL_PREFIX + CHANNEL, 'changed-opaque-credential')
        elif race == 'credential_delete':
            client.delete(module.OAUTH_CREDENTIAL_PREFIX + CHANNEL)
        elif race == 'channel':
            _change(client, module.OAUTH_CHANNEL_PREFIX + CHANNEL, lambda record: record.update(connection_id='changed'))
        elif race == 'membership':
            client.srem(module.OAUTH_CHANNEL_INDEX, CHANNEL)
        elif race in {'source', 'publisher', 'ancestor'}:
            task_id = {'source': data.recovered_id, 'publisher': data.publish_id, 'ancestor': data.original_id}[race]
            _change(client, module.JOB_PREFIX + task_id, lambda record: record.update(concurrent=True))
        elif race == 'ledger':
            _change(client, module.UPLOAD_PREFIX + data.recovered_id, lambda record: record.update(release_status='uncertain'))
        elif race == 'ancestor_upload':
            _write(client, module.UPLOAD_PREFIX + data.original_id, {'status': 'uploading'})
        elif race == 'child_claim':
            client.hset(module.RETRY_CHILD_CLAIM_PREFIX + data.recovered_id, 'token', 'changed')
        elif race == 'execution':
            client.set(module.RETRY_CHILD_EXECUTION_PREFIX + data.recovered_id, 'changed')
        elif race == 'repair_claim':
            client.set(module.REPAIR_CHECKPOINT_CLAIM_PREFIX + data.original_id, 'changed')
        elif race == 'dispatch':
            client.hset(module.RETRY_DISPATCH_PREFIX + data.original_id, 'state', 'changed')
        elif race == 'cursor':
            client.hset(data.state_key, 'cursor', '3')
        elif race == 'pause':
            client.hset(data.state_key, 'paused_reason', 'another_pause')
        elif race == 'active':
            client.set(module.ACTIVE_KEY, 'do-not-reset')
        return execute(*args, **kwargs)
    monkeypatch.setattr(client, 'eval', changed)
    with pytest.raises(module.ProductionRecoveryError, match='recovery_(state_changed|connection_missing)'):
        _run(case)
    assert not client.exists(data.audit_key)
    assert client.hget(data.state_key, 'next_due') == '110000'


def test_full_retry_without_repair_claim_is_supported(private_recovery):
    module, client = private_recovery
    data = _public_seed(module, client, hops=1, repair=False)
    assert _run((module, client, data))['status'] == 'resumed'


@pytest.mark.parametrize('hops,allowed', [(16, True), (17, False)])
def test_public_lineage_is_bounded(private_recovery, hops, allowed):
    module, client = private_recovery
    data = _public_seed(module, client, hops=hops)
    if allowed:
        assert _run((module, client, data))['status'] == 'resumed'
    else:
        with pytest.raises(module.ProductionRecoveryError, match='too_deep'):
            _run((module, client, data))


def test_pure_stock_disclosure_false_requires_complete_real_stock_provenance(case):
    module, client, data = case
    def stock(job):
        job['result'].update(runway_scenes_used=0, video_generation_provider_records=[],
                             runway_success_scene_indices=[], final_runway_repair_scene_indices=[])
        job['result']['youtube']['contains_synthetic_media'] = False
    _change(client, module.JOB_PREFIX + data.recovered_id, stock)
    _change(client, module.JOB_PREFIX + data.publish_id,
            lambda job: job['result'].update(contains_synthetic_media=False))
    _change(client, module.UPLOAD_PREFIX + data.recovered_id,
            lambda record: record['publish_plan'].update(contains_synthetic_media=False))
    assert _run(case)['contains_synthetic_media'] is False


def test_current_thumbnail_requirement_also_applies(case):
    module, client, data = case
    _change(client, module.UPLOAD_PREFIX + data.recovered_id,
            lambda record: record['publish_plan'].update(require_thumbnail=False))
    _change(client, module.JOB_PREFIX + data.recovered_id,
            lambda job: job['result']['youtube'].update(thumbnail_uploaded=False))
    with pytest.raises(module.ProductionRecoveryError, match='assets_unverified'):
        _run(case)


def test_public_audit_with_wrong_mode_cannot_be_reused(case):
    module, client, data = case
    _run(case)
    _change(client, data.audit_key, lambda record: record.update(release_mode='private'))
    with pytest.raises(module.ProductionRecoveryError, match='audit_invalid'):
        _run(case)


def test_imported_state_contract_names_exist_in_real_module():
    source = ast.parse((ROOT / 'app/services/production_recovery.py').read_text(encoding='utf-8'))
    state = ast.parse((ROOT / 'app/services/studio_state.py').read_text(encoding='utf-8'))
    defined = {target.id for node in state.body if isinstance(node, ast.Assign)
               for target in node.targets if isinstance(target, ast.Name)}
    imports = [name.name for node in source.body if isinstance(node, ast.ImportFrom)
               and node.module == 'app.services.studio_state' for name in node.names]
    assert set(imports) <= defined
