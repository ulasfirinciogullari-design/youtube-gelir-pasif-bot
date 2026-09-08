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
    def download(key, path):
        if key == case.source['result']['delivery_manifest_key']:
            path.write_text(json.dumps(case.manifest), encoding='utf-8')
        else:
            assert key == case.manifest['master_key']
            path.write_bytes(case.master.read_bytes())
        return str(path)
    def render(master, manifest, work, **kwargs):
        work.mkdir(parents=True)
        results = []
        for cut in manifest['shorts']:
            path = work / f'{cut["number"]}.mp4'
            path.write_bytes(f'fake cut {cut["number"]}'.encode())
            results.append({
                **cut, 'path': str(path), 'sha256': file_sha256(path), 'duration': 32,
                'quality_disposition': 'derived_portrait_review_required', 'manual_qa_required': True,
                'publish_eligible': False, 'new_voice_generations': 0, 'new_video_generations': 0,
            })
        return results
    monkeypatch.setattr(runtime, 'download_file', Mock(side_effect=download))
    monkeypatch.setattr(runtime, 'render_candidates', Mock(side_effect=render))
    monkeypatch.setattr(runtime, 'upload_file', Mock())
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
    assert runtime.render_candidates.call_count == 1
    assert runtime.upload_file.call_count == 3
    assert runtime.render_delivery_family(TASK, queued['task_id'])['status'] == 'already_executed'
    assert runtime.render_candidates.call_count == 1


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
