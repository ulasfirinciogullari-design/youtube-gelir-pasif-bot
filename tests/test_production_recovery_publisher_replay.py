"""Real compact publisher results may resume only with complete existing proof."""
import ast
import json
from uuid import UUID

import pytest

from test_production_recovery import ROOT, CHANNEL, REVISION, _change, _snapshot
from test_production_recovery_public import case, private_recovery, _run, _paused
from test_production_reconciliation import reconciliation, _tick_namespace


def _compact(case):
    module, client, data = case
    tree = ast.parse((ROOT / 'app/publish_tasks.py').read_text(encoding='utf-8'))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == '_result_from_existing_record')
    namespace = {'UploadAlreadyInProgress': RuntimeError}
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<real-public-replay>', 'exec'), namespace)
    ledger = json.loads(client.get(module.UPLOAD_PREFIX + data.recovered_id))
    compact = namespace['_result_from_existing_record'](data.publish_id, data.recovered_id, ledger)
    _change(client, module.JOB_PREFIX + data.publish_id, lambda job: job.update(result=compact))
    return compact


def test_actual_completed_retry_redelivery_resumes_without_rewriting_any_proof(case):
    module, client, data = case
    compact = _compact(case)
    assert compact['idempotent_replay'] is True
    assert not {'profile_revision', 'caption_uploaded', 'contains_synthetic_media'} & compact.keys()
    before = _snapshot(client)
    result = _run(case)
    assert result['status'] == 'resumed' and result['cursor'] == 2
    after = _snapshot(client)
    after.pop(data.audit_key)
    before[data.state_key].pop('paused_reason')
    before[data.state_key]['next_due'] = str(result['next_due'])
    assert after == before
    # The durable audit remains idempotent even after unrelated new activity.
    client.set(module.ACTIVE_KEY, 'later-claim')
    unchanged = _snapshot(client)
    assert _run(case, now=999999)['status'] == 'already_resumed'
    assert _snapshot(client) == unchanged


@pytest.mark.parametrize('include_flag', [True, False])
def test_compact_replay_accepts_proven_stronger_publisher_disclosure(case, include_flag):
    module, client, data = case
    _change(client, module.UPLOAD_PREFIX + data.recovered_id,
            lambda record: record['publish_plan'].update(contains_synthetic_media=False))
    _compact(case)
    if include_flag:
        _change(client, module.JOB_PREFIX + data.publish_id,
                lambda job: job['result'].update(contains_synthetic_media=True))
    assert _run(case)['contains_synthetic_media'] is True


def test_actual_tick_continues_next_frozen_topic_once_after_compact_retry_delivery(case):
    module, client, data = case
    _compact(case)
    discovered = reconciliation.__wrapped__(case)
    tick, enqueue = _tick_namespace(discovered)
    original = client.get(module.JOB_PREFIX + data.original_id)
    result = tick['production_tick']()
    assert result['public_retry_reconciliation']['resumed_count'] == 1
    assert result['status'] == 'queued' and enqueue.call_count == 1
    assert client.hget(data.state_key, 'cursor') == '3'
    next_job = json.loads(client.get(module.JOB_PREFIX + result['task_id']))
    assert next_job['spec']['production_topic_index'] == 2
    assert client.get(module.JOB_PREFIX + data.original_id) == original
    assert tick['production_tick']()['status'] == 'active'
    assert enqueue.call_count == 1


@pytest.mark.parametrize('synthetic', [True, False])
def test_replay_accepts_present_fields_only_when_identical_to_complete_proof(case, synthetic):
    module, client, data = case
    if not synthetic:
        def stock(job):
            job['result'].update(runway_scenes_used=0, video_generation_provider_records=[],
                                 runway_success_scene_indices=[], final_runway_repair_scene_indices=[])
            job['result']['youtube']['contains_synthetic_media'] = False
        _change(client, module.JOB_PREFIX + data.recovered_id, stock)
        _change(client, module.UPLOAD_PREFIX + data.recovered_id,
                lambda ledger: ledger['publish_plan'].update(contains_synthetic_media=False))
    _compact(case)
    _change(client, module.JOB_PREFIX + data.publish_id,
            lambda job: job['result'].update(profile_revision=REVISION, caption_uploaded=True,
                                             thumbnail_uploaded=True, contains_synthetic_media=synthetic))
    assert _run(case)['contains_synthetic_media'] is synthetic


@pytest.mark.parametrize('field,value', [
    ('profile_revision', 'changed'), ('profile_revision', None),
    ('caption_uploaded', False), ('caption_uploaded', None), ('caption_uploaded', 1),
    ('thumbnail_uploaded', False), ('thumbnail_uploaded', None),
    ('contains_synthetic_media', False), ('contains_synthetic_media', None),
    ('contains_synthetic_media', 1), ('caption_error_code', 'failed'),
    ('thumbnail_error_code', 'failed'), ('release_error_code', 'uncertain'),
    ('caption_error_code', False), ('thumbnail_error_code', False),
    ('release_error_code', False), ('scheduled_publish_at', False),
    ('target_channel_id', 'UC_wrong_channel'), ('connection_id', 'wrong-connection'),
    ('task_id', str(UUID(int=9999))), ('source_task_id', str(UUID(int=9999))),
    ('youtube_video_id', 'OtherVideo0'), ('privacy_status', 'private'),
    ('release_status', 'uncertain'), ('scheduled_publish_at', '2026-10-01T00:00:00Z'),
    ('stage', 'pending'), ('progress', 99), ('progress', '100'),
    ('idempotent_replay', False), ('idempotent_replay', 1),
])
def test_compact_result_never_hides_explicit_contradictions(case, field, value):
    module, client, data = case
    _compact(case)
    _change(client, module.JOB_PREFIX + data.publish_id,
            lambda job: job['result'].update({field: value}))
    before = _snapshot(client)
    with pytest.raises(module.ProductionRecoveryError):
        _run(case)
    assert _snapshot(client) == before
    _paused(case)


@pytest.mark.parametrize('target,path,value', [
    ('source', ['result', 'manual_qa_required'], True),
    ('source', ['result', 'quality_disposition'], 'manual_approved'),
    ('source', ['result', 'youtube', 'profile_revision'], 'changed'),
    ('source', ['result', 'youtube', 'caption_uploaded'], False),
    ('source', ['result', 'youtube', 'caption_uploaded'], None),
    ('source', ['result', 'youtube', 'thumbnail_uploaded'], False),
    ('source', ['result', 'youtube', 'contains_synthetic_media'], False),
    ('source', ['result', 'youtube', 'target_channel_id'], 'UC_wrong_channel'),
    ('source', ['result', 'youtube', 'connection_id'], 'changed'),
    ('source', ['result', 'youtube', 'release_status'], 'uncertain'),
    ('ledger', ['status'], 'uploading'), ('ledger', ['release_status'], 'uncertain'),
    ('ledger', ['privacy_status'], 'private'), ('ledger', ['release_completed_at'], None),
    ('ledger', ['youtube_video_id'], 'OtherVideo0'),
    ('ledger', ['target_channel_id'], 'UC_wrong_channel'), ('ledger', ['connection_id'], 'changed'),
    ('ledger', ['publish_plan', 'profile_revision'], 'changed'),
    ('ledger', ['publish_plan', 'contains_synthetic_media'], None),
    ('profile', ['profile_revision'], 'changed'), ('profile', ['release_mode'], 'private'),
])
def test_compact_delivery_still_requires_full_source_ledger_and_current_profile(case, target, path, value):
    module, client, data = case
    _compact(case)
    key = {'source': module.JOB_PREFIX + data.recovered_id,
           'ledger': module.UPLOAD_PREFIX + data.recovered_id,
           'profile': module.PROFILE_PREFIX + CHANNEL}[target]
    def damage(record):
        current = record
        for part in path[:-1]:
            current = current[part]
        current[path[-1]] = value
    _change(client, key, damage)
    before = _snapshot(client)
    with pytest.raises(module.ProductionRecoveryError):
        _run(case)
    assert _snapshot(client) == before
    _paused(case)


@pytest.mark.parametrize('damage', ['source_missing', 'ledger_missing', 'claim_missing',
                                  'execution_wrong', 'active', 'private_resume'])
def test_compact_replay_never_substitutes_missing_or_incompatible_authority(case, damage):
    module, client, data = case
    _compact(case)
    if damage == 'source_missing': client.delete(module.JOB_PREFIX + data.recovered_id)
    elif damage == 'ledger_missing': client.delete(module.UPLOAD_PREFIX + data.recovered_id)
    elif damage == 'claim_missing': client.delete(module.RETRY_CHILD_CLAIM_PREFIX + data.recovered_id)
    elif damage == 'execution_wrong': client.set(module.RETRY_CHILD_EXECUTION_PREFIX + data.recovered_id, 'wrong')
    elif damage == 'active': client.set(module.ACTIVE_KEY, 'current-claim')
    before = _snapshot(client)
    with pytest.raises(module.ProductionRecoveryError):
        if damage == 'private_resume':
            module.resume_after_private_retry(CHANNEL, data.original_id, data.recovered_id, REVISION, now=100000)
        else:
            _run(case)
    assert _snapshot(client) == before
    _paused(case)


@pytest.mark.parametrize('target', ['source', 'ledger', 'publisher', 'credential'])
def test_compact_retry_keeps_actual_compare_and_swap_snapshot_guards(case, monkeypatch, target):
    module, client, data = case
    _compact(case)
    original_eval = client.eval
    def raced(*args):
        if target == 'credential':
            client.set(module.OAUTH_CREDENTIAL_PREFIX + CHANNEL, 'changed-opaque-value')
        else:
            key = {'source': module.JOB_PREFIX + data.recovered_id,
                   'publisher': module.JOB_PREFIX + data.publish_id,
                   'ledger': module.UPLOAD_PREFIX + data.recovered_id}[target]
            _change(client, key, lambda record: record.update(concurrently_changed=True))
        return original_eval(*args)
    monkeypatch.setattr(client, 'eval', raced)
    with pytest.raises(module.ProductionRecoveryError, match='recovery_state_changed'):
        _run(case)
    _paused(case)
