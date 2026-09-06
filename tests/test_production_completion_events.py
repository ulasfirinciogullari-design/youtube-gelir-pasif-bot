"""Public events are hints only; real fake-Redis dispatch owns concurrency."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.services import production_events, youtube_publish_state
from test_editorial_publish_boundary import boundary
from test_channel_production import production
from test_production_fair_dispatch import _channels
from test_production_continuous_public import _public_finish


@pytest.fixture
def event_case(boundary, monkeypatch):
    b = boundary
    b.locked, b.kicks, b.publisher = True, [], None
    b.record.update(version=2, source_task_id=b.source_id, requested_release_mode='public',
                    requested_publish_at=None)
    def mark_success(task, result):
        b.events.append('mark-success')
        b.publisher = {'task_id': task, 'kind': 'publish', 'parent_id': b.source_id,
                       'spec': {'source_task_id': b.source_id}, 'state': 'SUCCESS', 'result': deepcopy(result)}
    def released(_source, _task, mode, **_kwargs):
        b.events.append('release-committed')
        b.record.update(release_status=mode, privacy_status='public' if mode == 'public' else 'private',
                        release_completed_at='2026-09-06T12:00:00+00:00',
                        side_effect_possible=True, release_side_effect_possible=True)
    def unlock(*_args):
        b.events.append('unlock')
        b.locked = False
    def kick():
        assert b.locked is False and b.publisher['state'] == 'SUCCESS'
        assert not (b.paths['external/master.mp4']).exists()  # after cleanup too
        b.events.append('tick')
        b.kicks.append(True)
        return True
    monkeypatch.setattr(b.module, 'get_job', lambda task: deepcopy(b.source if task == b.source_id else b.publisher))
    monkeypatch.setattr(b.module, 'mark_success', mark_success)
    monkeypatch.setattr(b.module, 'mark_release_completed', released)
    monkeypatch.setattr(b.module, 'release_execution_lock', unlock)
    monkeypatch.setattr(youtube_publish_state, '_redis', lambda: SimpleNamespace(exists=lambda key: int(b.locked)))
    monkeypatch.setattr(production_events, 'request_production_tick', kick)
    return b


def test_actual_public_completion_kicks_only_after_persisted_success_unlock_and_cleanup(event_case):
    b = event_case
    result = b.run()
    assert result['release_status'] == 'public' and len(b.kicks) == 1 and not b.failures
    assert b.events.index('release-committed') < b.events.index('mark-success') < b.events.index('unlock') < b.events.index('tick')
    assert len(b.inserts) == len(b.releases) == 1


def test_genuine_compact_public_replay_kicks_without_a_second_insert(event_case):
    b = event_case
    b.run()
    result = b.run()
    assert result['idempotent_replay'] is True and len(b.kicks) == 2
    assert len(b.inserts) == len(b.releases) == 1 and not b.failures


def test_broker_hint_failure_cannot_downgrade_actual_public_success(event_case, monkeypatch):
    b = event_case
    monkeypatch.setattr(production_events, 'request_production_tick', Mock(side_effect=RuntimeError('mocked broker failure')))
    assert b.run()['release_status'] == 'public'
    assert b.publisher['state'] == 'SUCCESS' and not b.failures


def test_failed_unlock_never_sends_an_early_hint(event_case, monkeypatch):
    b = event_case
    monkeypatch.setattr(b.module, 'release_execution_lock', lambda *_a: None)  # production helper swallows Redis outage
    assert b.run()['release_status'] == 'public'
    assert b.publisher['state'] == 'SUCCESS' and b.locked and not b.kicks


def test_registry_success_write_not_observed_does_not_send_hint(event_case, monkeypatch):
    b = event_case
    monkeypatch.setattr(b.module, 'mark_success', lambda *_a: None)
    assert b.run()['release_status'] == 'public' and not b.kicks


def test_private_pipeline_never_kicks(event_case):
    b = event_case
    b.record['publish_plan']['release_mode'] = 'private'
    assert b.run()['release_status'] == 'private' and not b.kicks and not b.releases


def test_blocked_caption_pipeline_never_kicks(event_case):
    b = event_case
    def fail(): raise RuntimeError('mocked caption failure')
    b.after_caption = fail
    assert b.run()['release_status'] == 'blocked' and not b.kicks and not b.releases


def test_uncertain_release_never_kicks(event_case, monkeypatch):
    b = event_case
    monkeypatch.setattr(b.module, 'set_video_release_with_credentials', Mock(side_effect=TimeoutError('mocked release timeout')))
    with pytest.raises(RuntimeError, match='uncertain'):
        b.run()
    assert not b.kicks and b.failures


@pytest.mark.parametrize('record,field,value', [
    ('ledger', 'version', 1), ('ledger', 'status', 'uncertain'),
    ('ledger', 'release_status', 'scheduled'), ('ledger', 'release_completed_at', None),
    ('ledger', 'release_side_effect_possible', False), ('ledger', 'publish_task_id', 'different'),
    ('ledger', 'source_task_id', 'different'), ('ledger', 'requested_publish_at', 'tomorrow'),
    ('ledger', 'youtube_video_id', 'other-video'), ('ledger', 'connection_id', 'other-connection'),
    ('attribution', 'caption_uploaded', False), ('attribution', 'contains_synthetic_media', False),
    ('attribution', 'profile_revision', 'changed'), ('attribution', 'release_error_code', 'blocked'),
    ('result', 'release_status', 'scheduled'), ('result', 'privacy_status', 'private'),
    ('result', 'caption_uploaded', False), ('result', 'contains_synthetic_media', False),
    ('plan', 'release_mode', 'private'), ('plan', 'profile_revision', 'changed'),
])
def test_contradictory_public_proof_cannot_kick(event_case, record, field, value):
    b = event_case
    result = b.run()
    b.kicks.clear()
    target = {'ledger': b.record, 'attribution': b.source['result']['youtube'],
              'result': result, 'plan': b.record['publish_plan']}[record]
    target[field] = value
    b.publisher['result'] = deepcopy(result)
    b.module._wake_after_public_success(b.source_id, b.task_id, result)
    assert not b.kicks


@pytest.mark.parametrize('field,value', [('caption_uploaded', False), ('profile_revision', 'wrong'),
                                       ('contains_synthetic_media', False), ('release_error_code', 'uncertain')])
def test_forged_compact_replay_fields_are_not_ignored(event_case, field, value):
    b = event_case
    b.run()
    result = b.run()
    b.kicks.clear()
    result[field] = value
    b.publisher['result'] = deepcopy(result)
    b.module._wake_after_public_success(b.source_id, b.task_id, result)
    assert not b.kicks


@pytest.mark.parametrize('broker_fails', [False, True])
def test_helper_only_queues_existing_tick_with_short_expiry_no_retry(monkeypatch, broker_fails):
    module = ModuleType('app.production_tasks')
    queued = Mock(side_effect=RuntimeError('mocked broker failure') if broker_fails else None)
    module.production_tick = SimpleNamespace(apply_async=queued)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    assert production_events.request_production_tick() is (not broker_fails)
    queued.assert_called_once_with(expires=55, retry=False)


@pytest.mark.parametrize('status,expected', [('resolved', ['commit', 'tick']), ('already_resolved', ['commit'])])
def test_resolution_kicks_only_new_committed_delivery(monkeypatch, status, expected):
    from app import episode_delivery_routes as routes
    external, original, leaf, publisher = [str(uuid4()) for _ in range(4)]
    values = {'channel_id': 'UC_channel_test', 'original_task_id': original, 'failed_leaf_id': leaf}
    events = []
    def resolve(**_kwargs):
        events.append('commit')
        return {'status': status, 'public_delivery': {'external_task_id': external,
            'publish_task_id': publisher, 'youtube_video_id': 'Public00000'}}
    monkeypatch.setattr(routes, 'resolve_external_episode', resolve)
    monkeypatch.setattr(routes, 'request_production_tick', lambda: events.append('tick'))
    assert routes._resolve_and_present(external, values)['status'] == status
    assert events == expected


def test_resolution_failure_does_not_send_hint(monkeypatch):
    from app import episode_delivery_routes as routes
    kick = Mock()
    monkeypatch.setattr(routes, 'request_production_tick', kick)
    monkeypatch.setattr(routes, 'resolve_external_episode', Mock(side_effect=routes.ExternalEpisodeDeliveryError('conflict')))
    with pytest.raises(routes.ExternalEpisodeDeliveryError):
        routes._resolve_and_present(str(uuid4()), {})
    kick.assert_not_called()


def test_resolution_success_survives_broker_failure(monkeypatch):
    from app import episode_delivery_routes as routes
    external, publisher = str(uuid4()), str(uuid4())
    receipt = {'status': 'resolved', 'public_delivery': {'external_task_id': external,
               'publish_task_id': publisher, 'youtube_video_id': 'Public00000'}}
    monkeypatch.setattr(routes, 'resolve_external_episode', lambda **_kw: receipt)
    task_module = ModuleType('app.production_tasks')
    task_module.production_tick = SimpleNamespace(apply_async=Mock(side_effect=RuntimeError('broker unavailable')))
    monkeypatch.setitem(sys.modules, task_module.__name__, task_module)
    values = {'channel_id': 'UC_channel_test', 'original_task_id': str(uuid4()), 'failed_leaf_id': str(uuid4())}
    assert routes._resolve_and_present(external, values)['status'] == 'resolved'
    task_module.production_tick.apply_async.assert_called_once_with(expires=55, retry=False)


def test_duplicate_public_hints_and_beat_still_use_atomic_two_slot_fair_dispatch(production, monkeypatch):
    module, client, profiles, connections = _channels(production, 3)
    first = module.dispatch_due_productions(profiles, connections, Mock(), now=1000)
    completed, still_running = first['queued']
    _public_finish(module, client, completed['task_id'])
    running_before = client.get(module.JOB_PREFIX + still_running['task_id'])
    enqueue, hints = Mock(), []
    task_module = ModuleType('app.production_tasks')
    task_module.production_tick = SimpleNamespace(apply_async=lambda **kwargs: hints.append(kwargs))
    monkeypatch.setitem(sys.modules, task_module.__name__, task_module)
    assert production_events.request_production_tick() and production_events.request_production_tick()
    with ThreadPoolExecutor(max_workers=3) as pool:
        outcomes = list(pool.map(lambda _: module.dispatch_due_productions(
            profiles, connections, enqueue, now=1001), range(len(hints) + 1)))  # two hints + beat
    assert sum(row.get('queued_count', 0) for row in outcomes) == enqueue.call_count == 1
    assert json.loads(client.get(module.JOB_PREFIX + enqueue.call_args.kwargs['task_id']))['spec']['production_channel_id'] == profiles[2]['channel_id']
    claims = module._decode_active_claims(client.get(module.ACTIVE_KEY))
    assert len(claims) == len({row['channel_id'] for row in claims}) == 2
    assert client.get(module.JOB_PREFIX + still_running['task_id']) == running_before
