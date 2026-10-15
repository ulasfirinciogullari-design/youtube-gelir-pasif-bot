"""Cloud dispatch, restart fences and private children using fake transports."""
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import fakeredis
import pytest

from test_production_delivery import family, TASK, OPTIONS
from app.services import production_delivery_runtime as runtime
from app.services.production_delivery import file_sha256
from app.services.youtube_automation import automated_quality_approved


CHANNEL = 'UC5v9AvNtD3PTLgo6m1jROOA'
CONNECTION = 'connection_AAAAA'


@pytest.fixture
def case(family, monkeypatch):
    package, rendered, manifest, master = family
    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(runtime, '_client', lambda: client)
    monkeypatch.setattr(runtime, 'settings', SimpleNamespace(
        studio_longform_delivery_enabled=True, studio_spend_enforcement=True))
    profile = {'channel_id': CHANNEL, 'profile_revision': 'revision_1',
               'production_enabled': True, 'auto_publish': True}
    source = {
        'task_id': TASK, 'state': 'SUCCESS', 'kind': 'render', 'parent_id': None,
        'spec': {**deepcopy(OPTIONS), 'duration_minutes': 8, 'language': 'en',
                 'production_channel_id': CHANNEL, 'production_connection_id': CONNECTION,
                 'production_profile_revision': 'revision_1', 'production_scheduled': True},
        'result': {
            'video_key': manifest['master_key'], 'quality_disposition': 'automated_qc_pass',
            'manual_qa_required': False, 'delivery_status': 'awaiting_portrait_render_and_review',
            'delivery_manifest_key': f'videos/{TASK}/delivery/{manifest["manifest_sha256"]}.json',
            'delivery_manifest_sha256': manifest['manifest_sha256'],
            'delivery_master_sha256': manifest['master_sha256'],
            'publish_metadata': {'sources': [{'url': 'https://example.org/source'}]},
        },
    }
    client.set(runtime.JOB_PREFIX + TASK, json.dumps(source))
    client.set(runtime.PROFILE_PREFIX + CHANNEL, json.dumps(profile))
    client.set(runtime.OAUTH_CHANNEL_PREFIX + CHANNEL, json.dumps({'id': CHANNEL, 'connection_id': CONNECTION}))
    client.set(runtime.OAUTH_CREDENTIAL_PREFIX + CHANNEL, 'opaque-fixture-credential')
    client.sadd(runtime.OAUTH_CHANNEL_INDEX, CHANNEL)
    return SimpleNamespace(client=client, manifest=manifest, master=master, source=source, profile=profile,
                           key=runtime.FAMILY_PREFIX + TASK)


def test_disabled_mode_does_not_read_storage_or_dispatch(monkeypatch):
    monkeypatch.setattr(runtime, 'settings', SimpleNamespace())
    client = Mock(side_effect=AssertionError('Disabled feature touched Redis'))
    monkeypatch.setattr(runtime, '_client', client)
    assert runtime.queue_delivery_family(TASK)['status'] == 'disabled'
    assert runtime.maintain_delivery_families()['status'] == 'disabled'
    assert runtime.render_delivery_family(TASK, TASK)['status'] == 'disabled'
    client.assert_not_called()


def test_delivery_switch_without_spend_enforcement_does_not_dispatch(case, monkeypatch):
    monkeypatch.setattr(runtime, 'settings', SimpleNamespace(studio_longform_delivery_enabled=True))
    enqueue = Mock()
    assert runtime.queue_delivery_family(TASK, enqueue)['status'] == 'disabled'
    enqueue.assert_not_called()


def test_exact_family_queued_once_and_no_private_credential_in_record(case):
    enqueue = Mock()
    result = runtime.queue_delivery_family(TASK, enqueue)
    assert result['status'] == 'queued'
    enqueue.assert_called_once_with(args=[TASK], task_id=result['task_id'], retry=False)
    assert runtime.queue_delivery_family(TASK, enqueue)['status'] == 'already_claimed'
    assert enqueue.call_count == 1
    assert case.client.ttl(case.key) == -1
    assert 'opaque-fixture-credential' not in case.client.get(case.key)


def test_ambiguous_broker_result_is_not_replayed(case):
    enqueue = Mock(side_effect=TimeoutError('possibly accepted'))
    assert runtime.queue_delivery_family(TASK, enqueue)['status'] == 'dispatch_uncertain'
    assert runtime.queue_delivery_family(TASK, enqueue)['status'] == 'already_claimed'
    assert enqueue.call_count == 1


@pytest.mark.parametrize('raises', [False, True])
def test_fast_worker_state_is_not_regressed_by_enqueue_reply(case, raises):
    def enqueue(**kwargs):
        record = json.loads(case.client.get(case.key))
        record.update(status='awaiting_automated_review', execution_claimed=True)
        case.client.set(case.key, json.dumps(record))
        if raises:
            raise TimeoutError('reply lost after delivery')
    runtime.queue_delivery_family(TASK, enqueue)
    assert json.loads(case.client.get(case.key))['status'] == 'awaiting_automated_review'


@pytest.mark.parametrize('change', ['disabled', 'reconnect', 'cancelled', 'failed', 'revision', 'changed_key', 'unreviewed'])
def test_invalid_or_stopped_source_cannot_be_queued(case, change):
    if change == 'disabled':
        case.profile['production_enabled'] = False
        case.client.set(runtime.PROFILE_PREFIX + CHANNEL, json.dumps(case.profile))
    elif change == 'reconnect':
        case.client.delete(runtime.OAUTH_CREDENTIAL_PREFIX + CHANNEL)
    elif change == 'cancelled':
        case.client.set(runtime.RENDER_CANCELLATION_PREFIX + TASK, 'owner-stop')
    else:
        if change == 'failed':
            case.source['state'] = 'FAILURE'
        elif change == 'revision':
            case.source['spec']['production_profile_revision'] = 'changed'
        elif change == 'changed_key':
            case.source['result']['delivery_manifest_key'] = 'videos/other/delivery.json'
        else:
            case.source['result']['manual_qa_required'] = True
        case.client.set(runtime.JOB_PREFIX + TASK, json.dumps(case.source))
    enqueue = Mock()
    assert runtime.queue_delivery_family(TASK, enqueue)['status'] == 'blocked'
    enqueue.assert_not_called()


def _fake_media(case, monkeypatch):
    case.assets = {}
    def download(key, path):
        if key == case.source['result']['delivery_manifest_key']:
            path.write_text(json.dumps(case.manifest), encoding='utf-8')
        elif key == case.manifest['master_key']:
            path.write_bytes(case.master.read_bytes())
        else:
            path.write_bytes(case.assets[key])
        return str(path)
    def render(master, manifest, work, **kwargs):
        work.mkdir(parents=True)
        for cut in manifest['shorts']:
            if cut['number'] not in kwargs.get('only_numbers', [1, 2, 3]):
                continue
            path = work / f'{cut["number"]}.mp4'
            path.write_bytes(f'fake cut {cut["number"]}'.encode())
            yield {
                **cut, 'path': str(path), 'sha256': file_sha256(path), 'duration': 32,
                'quality_disposition': 'derived_portrait_review_required', 'manual_qa_required': True,
                'publish_eligible': False, 'new_voice_generations': 0, 'new_video_generations': 0,
            }
    def upload(path, key, content_type):
        from pathlib import Path
        case.assets[key] = Path(path).read_bytes()
    monkeypatch.setattr(runtime, 'download_file', Mock(side_effect=download))
    monkeypatch.setattr(runtime, 'iter_candidates', Mock(side_effect=render))
    monkeypatch.setattr(runtime, 'upload_file', Mock(side_effect=upload))
    monkeypatch.setattr(runtime, 'presigned_download_url', Mock(return_value='https://example.org/private-fixture'))


def test_cloud_worker_creates_three_same_channel_private_children_without_changing_parent(case, monkeypatch):
    _fake_media(case, monkeypatch)
    before = case.client.get(runtime.JOB_PREFIX + TASK)
    queued = runtime.queue_delivery_family(TASK, Mock())
    result = runtime.render_delivery_family(TASK, queued['task_id'])
    assert result['status'] == 'awaiting_automated_review'
    assert len(set(result['child_task_ids'])) == 3
    assert case.client.get(runtime.JOB_PREFIX + TASK) == before
    for child_id in result['child_task_ids']:
        child = json.loads(case.client.get(runtime.JOB_PREFIX + child_id))
        assert child['parent_id'] == TASK
        assert child['spec']['production_channel_id'] == CHANNEL
        assert child['spec']['production_connection_id'] == CONNECTION
        assert child['spec']['production_derived_from'] == TASK
        assert child['spec']['publish_after_render'] is False
        assert not automated_quality_approved(child)
    assert runtime.iter_candidates.call_count == 1
    assert runtime.upload_file.call_count == 3
    assert runtime.render_delivery_family(TASK, queued['task_id'])['status'] == 'already_executed'
    assert runtime.iter_candidates.call_count == 1


def test_wrong_worker_identity_does_not_mutate_real_claim(case):
    runtime.queue_delivery_family(TASK, Mock())
    before = case.client.get(case.key)
    assert runtime.render_delivery_family(TASK, '22222222-2222-4222-8222-222222222222')['status'] == 'blocked'
    assert case.client.get(case.key) == before


def test_stop_after_dispatch_prevents_any_media_download(case, monkeypatch):
    _fake_media(case, monkeypatch)
    queued = runtime.queue_delivery_family(TASK, Mock())
    case.client.set(runtime.RENDER_CANCELLATION_PREFIX + TASK, 'owner-stop')
    assert runtime.render_delivery_family(TASK, queued['task_id'])['status'] == 'blocked'
    runtime.download_file.assert_not_called()


def test_export_failure_preserves_long_and_completed_child_no_parent_rebuild(case, monkeypatch):
    _fake_media(case, monkeypatch)
    before = case.client.get(runtime.JOB_PREFIX + TASK)
    runtime.upload_file.side_effect = [None, TimeoutError('uncertain private upload')]
    queued = runtime.queue_delivery_family(TASK, Mock())
    assert runtime.render_delivery_family(TASK, queued['task_id'])['status'] == 'blocked'
    record = json.loads(case.client.get(case.key))
    assert record['status'] == 'render_or_export_failed'
    assert len(record['child_task_ids']) == 1
    assert case.client.get(runtime.JOB_PREFIX + TASK) == before
    assert runtime.render_delivery_family(TASK, queued['task_id'])['status'] == 'already_executed'


def test_completed_families_do_not_starve_later_unclaimed_long(case, monkeypatch):
    jobs = [{'task_id': str(n), 'result': {'delivery_manifest_key': 'fixture'}} for n in range(8)]
    monkeypatch.setattr(runtime, 'list_jobs', Mock(return_value=jobs))
    queue = Mock(side_effect=[{'status': 'already_claimed'}] * 7 + [{'status': 'queued'}])
    monkeypatch.setattr(runtime, 'queue_delivery_family', queue)
    assert runtime.maintain_delivery_families()['families'] == [{'status': 'queued'}]
    assert queue.call_count == 8


def _failed_after_first_export(case, monkeypatch):
    _fake_media(case, monkeypatch)
    upload = runtime.upload_file.side_effect
    count = 0
    def fail_second(path, key, content_type):
        nonlocal count
        count += 1
        if count == 2:
            raise TimeoutError('private export response lost')
        upload(path, key, content_type)
    runtime.upload_file.side_effect = fail_second
    queued = runtime.queue_delivery_family(TASK, Mock())
    assert runtime.render_delivery_family(TASK, queued['task_id'])['status'] == 'blocked'
    runtime.upload_file.side_effect = upload
    return queued


def test_known_cpu_failure_reuses_verified_child_and_renders_only_missing_cuts(case, monkeypatch):
    original = _failed_after_first_export(case, monkeypatch)
    first_record = json.loads(case.client.get(case.key))
    first_id = first_record['child_task_ids'][0]
    before = case.client.get(runtime.JOB_PREFIX + first_id)
    parent_before = case.client.get(runtime.JOB_PREFIX + TASK)
    assert len(first_record['children']) == 1
    enqueue = Mock()
    retry = runtime.queue_delivery_family(TASK, enqueue)
    assert retry['status'] == 'queued' and retry['task_id'] != original['task_id']
    assert runtime.queue_delivery_family(TASK, enqueue)['status'] == 'already_claimed'
    assert enqueue.call_count == 1
    runtime.download_file.reset_mock()
    runtime.iter_candidates.reset_mock()
    runtime.upload_file.reset_mock()
    result = runtime.render_delivery_family(TASK, retry['task_id'])
    assert result['status'] == 'awaiting_automated_review'
    assert result['child_task_ids'][0] == first_id
    assert case.client.get(runtime.JOB_PREFIX + first_id) == before
    assert case.client.get(runtime.JOB_PREFIX + TASK) == parent_before
    assert runtime.iter_candidates.call_args.kwargs['only_numbers'] == [2, 3]
    assert runtime.upload_file.call_count == 2
    assert [call.args[0] for call in runtime.download_file.call_args_list] == [
        case.source['result']['delivery_manifest_key'], json.loads(before)['result']['video_key'],
        case.manifest['master_key'],
    ]
    record = json.loads(case.client.get(case.key))
    assert record['attempt'] == 2 and len(record['children']) == 3
    for child_id in result['child_task_ids']:
        assert not automated_quality_approved(json.loads(case.client.get(runtime.JOB_PREFIX + child_id)))


def test_later_cpu_render_failure_keeps_earlier_export(case, monkeypatch):
    _fake_media(case, monkeypatch)
    render = runtime.iter_candidates.side_effect
    def fail_later(*args, **kwargs):
        yield next(render(*args, **kwargs))
        raise RuntimeError('second FFmpeg cut failed')
    runtime.iter_candidates.side_effect = fail_later
    queued = runtime.queue_delivery_family(TASK, Mock())
    assert runtime.render_delivery_family(TASK, queued['task_id'])['status'] == 'blocked'
    record = json.loads(case.client.get(case.key))
    assert record['status'] == 'render_or_export_failed'
    assert len(record['children']) == len(record['child_task_ids']) == 1
    assert case.client.exists(runtime.JOB_PREFIX + record['child_task_ids'][0])
    assert runtime.upload_file.call_count == 1


@pytest.mark.parametrize('status', ['reserved', 'queued', 'dispatch_uncertain', 'rendering'])
def test_unknown_dispatch_or_live_execution_is_never_recovered(case, status):
    runtime.queue_delivery_family(TASK, Mock())
    record = json.loads(case.client.get(case.key))
    record.update(status=status, execution_claimed=status == 'rendering')
    case.client.set(case.key, json.dumps(record))
    before = case.client.get(case.key)
    enqueue = Mock()
    assert runtime.queue_delivery_family(TASK, enqueue)['status'] == 'already_claimed'
    assert case.client.get(case.key) == before
    enqueue.assert_not_called()


def test_hard_interruption_leaves_execution_held_with_successful_child(case, monkeypatch):
    _fake_media(case, monkeypatch)
    render = runtime.iter_candidates.side_effect
    def interrupted(*args, **kwargs):
        yield next(render(*args, **kwargs))
        raise SystemExit('worker terminated')
    runtime.iter_candidates.side_effect = interrupted
    queued = runtime.queue_delivery_family(TASK, Mock())
    with pytest.raises(SystemExit):
        runtime.render_delivery_family(TASK, queued['task_id'])
    record = json.loads(case.client.get(case.key))
    assert record['status'] == 'rendering' and len(record['children']) == 1
    assert runtime.queue_delivery_family(TASK, Mock())['status'] == 'already_claimed'


def test_legacy_failed_claim_without_atomic_receipts_is_held(case):
    runtime.queue_delivery_family(TASK, Mock())
    record = json.loads(case.client.get(case.key))
    record.update(version=1, status='render_or_export_failed', execution_claimed=True)
    case.client.set(case.key, json.dumps(record))
    enqueue = Mock()
    assert runtime.queue_delivery_family(TASK, enqueue)['status'] == 'already_claimed'
    enqueue.assert_not_called()


def test_recovery_is_limited_to_one_retry_even_with_no_completed_exports(case, monkeypatch):
    _fake_media(case, monkeypatch)
    runtime.iter_candidates.side_effect = RuntimeError('CPU render failed')
    enqueue = Mock()
    for attempt in (1, 2):
        queued = runtime.queue_delivery_family(TASK, enqueue)
        assert queued['status'] == 'queued'
        assert runtime.render_delivery_family(TASK, queued['task_id'])['status'] == 'blocked'
        assert json.loads(case.client.get(case.key))['attempt'] == attempt
    assert runtime.queue_delivery_family(TASK, enqueue)['status'] == 'already_claimed'
    assert enqueue.call_count == 2
    runtime.upload_file.assert_not_called()


def test_uncertain_recovery_dispatch_cannot_be_replayed(case, monkeypatch):
    _failed_after_first_export(case, monkeypatch)
    enqueue = Mock(side_effect=TimeoutError('broker reply lost'))
    assert runtime.queue_delivery_family(TASK, enqueue)['status'] == 'dispatch_uncertain'
    assert runtime.queue_delivery_family(TASK, enqueue)['status'] == 'already_claimed'
    assert enqueue.call_count == 1


@pytest.mark.parametrize('change', ['changed', 'missing', 'cancelled'])
@pytest.mark.parametrize('after_dispatch', [False, True])
def test_recovery_rechecks_child_snapshot_and_cancellation(case, monkeypatch, change, after_dispatch):
    _failed_after_first_export(case, monkeypatch)
    if after_dispatch:
        retry = runtime.queue_delivery_family(TASK, Mock())
    record = json.loads(case.client.get(case.key))
    child_id = record['child_task_ids'][0]
    if change == 'changed':
        child = json.loads(case.client.get(runtime.JOB_PREFIX + child_id))
        child['result']['publish_eligible'] = True
        case.client.set(runtime.JOB_PREFIX + child_id, json.dumps(child))
    elif change == 'missing':
        case.client.delete(runtime.JOB_PREFIX + child_id)
    else:
        case.client.set(runtime.RENDER_CANCELLATION_PREFIX + child_id, 'owner-stop')
    runtime.download_file.reset_mock()
    runtime.iter_candidates.reset_mock()
    runtime.upload_file.reset_mock()
    enqueue = Mock()
    if after_dispatch:
        assert runtime.render_delivery_family(TASK, retry['task_id'])['status'] == 'blocked'
    else:
        assert runtime.queue_delivery_family(TASK, enqueue)['status'] == 'blocked'
    enqueue.assert_not_called()
    runtime.download_file.assert_not_called()
    runtime.iter_candidates.assert_not_called()
    runtime.upload_file.assert_not_called()


@pytest.mark.parametrize('change', ['tampered', 'missing'])
def test_recovery_checks_retained_asset_bytes_before_any_new_cpu_render(case, monkeypatch, change):
    _failed_after_first_export(case, monkeypatch)
    retry = runtime.queue_delivery_family(TASK, Mock())
    asset_key = next(iter(case.assets))
    if change == 'tampered':
        case.assets[asset_key] = b'replaced bytes'
    else:
        del case.assets[asset_key]
    runtime.iter_candidates.reset_mock()
    runtime.upload_file.reset_mock()
    assert runtime.render_delivery_family(TASK, retry['task_id'])['status'] == 'blocked'
    runtime.iter_candidates.assert_not_called()
    runtime.upload_file.assert_not_called()


def test_old_worker_cannot_mutate_new_recovery_claim(case, monkeypatch):
    original = _failed_after_first_export(case, monkeypatch)
    retry = runtime.queue_delivery_family(TASK, Mock())
    before = case.client.get(case.key)
    assert runtime.render_delivery_family(TASK, original['task_id'])['status'] == 'blocked'
    assert not runtime._set_status(case.client, case.key, json.loads(before)['binding'],
                                   'render_or_export_failed', execution_task_id=original['task_id'])
    assert case.client.get(case.key) == before
    assert json.loads(before)['task_id'] == retry['task_id']


def test_all_three_committed_assets_recover_without_master_download_or_render(case, monkeypatch):
    _fake_media(case, monkeypatch)
    queued = runtime.queue_delivery_family(TASK, Mock())
    done = runtime.render_delivery_family(TASK, queued['task_id'])
    record = json.loads(case.client.get(case.key))
    # Simulate a failure recording completion after all child commits succeeded.
    record['status'] = 'render_or_export_failed'
    case.client.set(case.key, json.dumps(record))
    retry = runtime.queue_delivery_family(TASK, Mock())
    runtime.download_file.reset_mock()
    runtime.iter_candidates.reset_mock()
    runtime.upload_file.reset_mock()
    assert runtime.render_delivery_family(TASK, retry['task_id']) == done
    assert len(runtime.download_file.call_args_list) == 4
    assert case.manifest['master_key'] not in [call.args[0] for call in runtime.download_file.call_args_list]
    runtime.iter_candidates.assert_not_called()
    runtime.upload_file.assert_not_called()


@pytest.mark.parametrize('raw', ['{}', '[]', 'null', 'false', '0', '', '{"status":"render_or_export_failed"}'])
def test_corrupt_existing_claim_cannot_be_overwritten_and_dispatched(case, raw):
    case.client.set(case.key, raw)
    enqueue = Mock()
    assert runtime.queue_delivery_family(TASK, enqueue)['status'] in {'blocked', 'already_claimed'}
    assert case.client.get(case.key) == raw
    enqueue.assert_not_called()


@pytest.mark.parametrize('change', ['cancelled', 'disabled', 'disconnected', 'revision', 'asset'])
def test_recovery_rechecks_parent_and_channel_before_dispatch(case, monkeypatch, change):
    _failed_after_first_export(case, monkeypatch)
    if change == 'cancelled':
        case.client.set(runtime.RENDER_CANCELLATION_PREFIX + TASK, 'owner-stop')
    elif change == 'disabled':
        case.profile['production_enabled'] = False
        case.client.set(runtime.PROFILE_PREFIX + CHANNEL, json.dumps(case.profile))
    elif change == 'disconnected':
        case.client.delete(runtime.OAUTH_CREDENTIAL_PREFIX + CHANNEL)
    elif change == 'revision':
        case.profile['profile_revision'] = 'next_revision'
        case.client.set(runtime.PROFILE_PREFIX + CHANNEL, json.dumps(case.profile))
    else:
        case.source['result']['delivery_master_sha256'] = 'a' * 64
        case.client.set(runtime.JOB_PREFIX + TASK, json.dumps(case.source))
    before = case.client.get(case.key)
    enqueue = Mock()
    assert runtime.queue_delivery_family(TASK, enqueue)['status'] == 'blocked'
    assert case.client.get(case.key) == before
    enqueue.assert_not_called()


@pytest.mark.parametrize('cancel', ['parent', 'retained_child', 'new_child'])
def test_cancellation_during_recovery_export_prevents_child_commit(case, monkeypatch, cancel):
    _failed_after_first_export(case, monkeypatch)
    retry = runtime.queue_delivery_family(TASK, Mock())
    record = json.loads(case.client.get(case.key))
    new_id = runtime._child_id(record['binding'], 2)
    cancel_id = {'parent': TASK, 'retained_child': record['child_task_ids'][0], 'new_child': new_id}[cancel]
    upload = runtime.upload_file.side_effect
    def stop_during_upload(*args):
        upload(*args)
        case.client.set(runtime.RENDER_CANCELLATION_PREFIX + cancel_id, 'owner-stop')
    runtime.upload_file.side_effect = stop_during_upload
    assert runtime.render_delivery_family(TASK, retry['task_id'])['status'] == 'blocked'
    after = json.loads(case.client.get(case.key))
    assert after['children'] == record['children']
    assert not case.client.exists(runtime.JOB_PREFIX + new_id)


def test_unstarted_legacy_dispatch_adopts_atomic_receipts(case, monkeypatch):
    _fake_media(case, monkeypatch)
    queued = runtime.queue_delivery_family(TASK, Mock())
    record = json.loads(case.client.get(case.key))
    record['version'] = 1
    for field in ('attempt', 'children', 'child_task_ids'):
        record.pop(field)
    case.client.set(case.key, json.dumps(record))
    assert runtime.render_delivery_family(TASK, queued['task_id'])['status'] == 'awaiting_automated_review'
    record = json.loads(case.client.get(case.key))
    assert record['version'] == 2 and record['attempt'] == 1 and len(record['children']) == 3
