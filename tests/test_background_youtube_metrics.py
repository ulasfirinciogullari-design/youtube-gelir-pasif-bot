"""Server observation uses mocked Google, existing ownership proofs and Redis."""
import ast
import json
from pathlib import Path
import sys
from types import ModuleType
from unittest.mock import Mock

import pytest

from app.services import studio_state
from test_youtube_metrics import (
    case, job, _refresh, _unlock, _google_count, ProviderError,
    CHANNEL, CONNECTION, SOURCE, VIDEO,
)


ROOT = Path(__file__).resolve().parents[1]


def _task(case, monkeypatch, *, listing=None, refresh=None):
    tree = ast.parse((ROOT / 'app/production_tasks.py').read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'observe_youtube_metrics')
    node.decorator_list = []
    registry = ModuleType('app.services.studio_state')
    registry.MAX_INDEXED_JOBS = 500
    registry.list_jobs = listing or Mock(return_value=[job()])
    monkeypatch.setitem(sys.modules, registry.__name__, registry)
    monkeypatch.setitem(sys.modules, 'app.services.youtube_metrics', case.module)
    if refresh is not None:
        monkeypatch.setattr(case.module, 'refresh_dashboard_metrics', refresh)
    namespace = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(ROOT / 'app/production_tasks.py'), 'exec'), namespace)
    return namespace['observe_youtube_metrics'], registry.list_jobs


def _background(case, jobs=None):
    return case.module.refresh_dashboard_metrics([job()] if jobs is None else jobs, background=True)


def test_observer_has_independent_bounded_schedule_without_changing_production_tick():
    tree = ast.parse((ROOT / 'app/celery_app.py').read_text(encoding='utf-8'))
    update = next(n.value for n in tree.body if isinstance(n, ast.Expr)
                  and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Attribute)
                  and n.value.func.attr == 'update')
    schedule = ast.literal_eval(next(k.value for k in update.keywords if k.arg == 'beat_schedule'))
    assert schedule['channel-production-every-minute'] == {
        'task': 'app.production_tasks.production_tick', 'schedule': 60.0, 'options': {'expires': 55}}
    observer = schedule['owned-youtube-metrics-every-five-minutes']
    assert observer == {'task': 'app.production_tasks.observe_youtube_metrics',
                        'schedule': 300.0, 'options': {'expires': 290}}
    tasks = ast.parse((ROOT / 'app/production_tasks.py').read_text(encoding='utf-8'))
    node = next(n for n in tasks.body if isinstance(n, ast.FunctionDef) and n.name == 'observe_youtube_metrics')
    options = {k.arg: ast.literal_eval(k.value) for k in node.decorator_list[0].keywords}
    assert options == {'name': observer['task'], 'acks_late': False, 'autoretry_for': (),
                       'max_retries': 0, 'soft_time_limit': 250, 'time_limit': 270}
    assert options['time_limit'] < observer['schedule']
    assert not node.args.args  # No caller-provided channel or paid-generation scope.
    tick = next(n for n in tasks.body if isinstance(n, ast.FunctionDef) and n.name == 'production_tick')
    assert 'observe_youtube_metrics' not in ast.unparse(tick)


def test_server_task_refreshes_exact_owned_videos_without_studio_or_spending(case, monkeypatch):
    c = case
    task, listing = _task(c, monkeypatch)
    for key, value in {'production:cursor': '5', 'production:hold': 'previous_render_failed',
                       'production_spend': 'unchanged', 'source-job': json.dumps(job())}.items():
        c.client.set(key, value)
    before = {key: c.client.dump(key) for key in c.client.keys('*')}
    result = task()
    listing.assert_called_once_with(limit=500)
    assert result == {'status': 'checked', 'channel_count': 1, 'video_count': 1}
    assert c.service.videos.return_value.list.call_args.kwargs['id'] == VIDEO
    assert _google_count(c) == 1
    assert all(c.client.dump(key) == value for key, value in before.items())
    assert all(key in before or key.startswith((c.module.CACHE_PREFIX, c.module.LOCK_PREFIX,
                                               c.module.OBSERVATION_PREFIX))
               for key in c.client.keys('*'))
    assert 'secret' not in json.dumps(result)


def test_background_skips_recent_manual_refresh_but_manual_contract_is_preserved(case):
    c = case
    _refresh(c)
    _unlock(c)
    c.clock[0] += 61
    before = c.client.get(c.module.CACHE_PREFIX + CHANNEL + ':' + CONNECTION)
    assert _background(c)['channels'][0]['status'] == 'fresh'
    assert _google_count(c) == 1
    assert c.client.get(c.module.CACHE_PREFIX + CHANNEL + ':' + CONNECTION) == before
    _refresh(c)
    assert _google_count(c) == 2


def test_background_refreshes_at_freshness_boundary_and_uses_shared_lock(case):
    c = case
    _background(c)
    _unlock(c)
    c.clock[0] += c.module.REFRESH_SECONDS - 1
    _background(c)
    assert _google_count(c) == 1
    c.clock[0] += 1
    c.client.set(c.module.LOCK_PREFIX + CHANNEL + ':' + CONNECTION, 'owner-refresh', ex=60)
    _background(c)
    assert _google_count(c) == 1
    _unlock(c)
    _background(c)
    assert _google_count(c) == 2


def test_background_freshness_is_per_channel_so_fresh_sibling_is_not_requeried(case, monkeypatch):
    c = case
    _refresh(c)
    _unlock(c)
    c.clock[0] += 61
    second = {'id': 'UC_second_channel', 'connection_id': 'connection_second', 'title': 'Second channel'}
    c.client.sadd(c.auth.CHANNEL_INDEX_KEY, second['id'])
    c.client.set(c.auth.CHANNEL_PREFIX + second['id'], json.dumps(second))
    c.client.set(c.auth.CREDENTIAL_PREFIX + second['id'], 'second-encrypted-credential')
    read = Mock(return_value={'version': 1, 'channel_id': second['id'],
        'connection_id': second['connection_id'], 'channel': {'fetched_at': c.clock[0]},
        'videos': {}, 'last_error': None, 'last_attempt_at': c.clock[0]})
    monkeypatch.setattr(c.module, '_read_google', read)
    result = _background(c, [job(), job(source='22222222-2222-4222-8222-222222222222',
        video='bB123456789', channel=second['id'], connection=second['connection_id'])])
    read.assert_called_once()
    assert read.call_args.args[0]['channel_id'] == second['id']
    assert result['videos'][SOURCE]['view_count'] == 12


@pytest.mark.parametrize('inventory', [
    [{'task_id': SOURCE, 'kind': 'render', 'state': 'PENDING', 'spec': {}, 'result': None}],
    [job(channel='UC_unlinked_channel', connection='unlinked_connection')],
])
def test_nonempty_inventory_without_channel_upload_proof_preserves_absence(case, inventory):
    c = case
    c.service.videos.return_value.list.return_value.execute.return_value = {'items': []}
    _refresh(c)
    _unlock(c)
    c.clock[0] += 301
    before = {key: c.client.dump(key) for key in c.client.keys('*')}
    _background(c, inventory)
    assert _google_count(c) == 1
    assert {key: c.client.dump(key) for key in c.client.keys('*')} == before


def test_background_other_channel_proof_cannot_clear_prior_absence(case, monkeypatch):
    c = case
    c.service.videos.return_value.list.return_value.execute.return_value = {'items': []}
    _refresh(c)
    _unlock(c)
    c.clock[0] += 301
    original_key = c.module.CACHE_PREFIX + CHANNEL + ':' + CONNECTION
    before = c.client.dump(original_key)
    second = {'id': 'UC_second_channel', 'connection_id': 'connection_second', 'title': 'Second channel'}
    c.client.sadd(c.auth.CHANNEL_INDEX_KEY, second['id'])
    c.client.set(c.auth.CHANNEL_PREFIX + second['id'], json.dumps(second))
    c.client.set(c.auth.CREDENTIAL_PREFIX + second['id'], 'second-encrypted-credential')
    read = Mock(return_value={'version': 1, 'channel_id': second['id'],
        'connection_id': second['connection_id'], 'channel': {'fetched_at': c.clock[0]},
        'videos': {}, 'last_error': None, 'last_attempt_at': c.clock[0]})
    monkeypatch.setattr(c.module, '_read_google', read)
    _background(c, [job(source='22222222-2222-4222-8222-222222222222', video='bB123456789',
                       channel=second['id'], connection=second['connection_id'])])
    read.assert_called_once()
    assert read.call_args.args[0]['channel_id'] == second['id']
    assert c.client.dump(original_key) == before


@pytest.mark.parametrize('status,reason,cooldown', [(403, 'quotaExceeded', 3600), (500, 'backendError', 300)])
def test_background_failed_read_preserves_counts_and_bounds_next_attempt(case, status, reason, cooldown):
    c = case
    _refresh(c)
    _unlock(c)
    c.clock[0] += 300
    c.service.videos.return_value.list.return_value.execute.side_effect = ProviderError(status, reason)
    result = _background(c)
    assert result['videos'][SOURCE]['view_count'] == 12
    assert result['videos'][SOURCE]['status'] == 'stale'
    _unlock(c)
    c.clock[0] += cooldown - 1
    _background(c)
    assert _google_count(c) == 2
    c.clock[0] += 1
    _background(c)
    assert _google_count(c) == 3
    assert 'secret' not in json.dumps(result)


def test_background_skips_reconnect_required_channel_without_touching_credentials(case):
    c = case
    key = c.auth.CHANNEL_PREFIX + CHANNEL
    channel = json.loads(c.client.get(key))
    channel['requires_reconnect'] = True
    c.client.set(key, json.dumps(channel))
    before = {key: c.client.dump(key) for key in c.client.keys('*')}
    result = _background(c)
    c.auth._decrypt_json.assert_not_called()
    c.module._service.assert_not_called()
    assert result['channels'][0]['reason'] == 'permission'
    assert {key: c.client.dump(key) for key in c.client.keys('*')} == before


def test_background_retains_channel_and_video_query_bounds(case):
    c = case
    jobs = [job(source=f'{index:08d}-1111-4111-8111-111111111111', video=f'{index:011d}')
            for index in range(60)]
    c.service.videos.return_value.list.return_value.execute.return_value = {'items': []}
    _background(c, jobs)
    assert len(c.service.videos.return_value.list.call_args.kwargs['id'].split(',')) == 50
    c.module._service.reset_mock()
    for index in range(10):
        c.client.sadd(c.auth.CHANNEL_INDEX_KEY, f'UC_other_{index:08d}')
    assert _background(c, jobs).get('error') == 'cache_unavailable'
    c.module._service.assert_not_called()


@pytest.mark.parametrize('failure', ['listing', 'refresh', 'cache'])
def test_observation_failure_is_sanitized_and_does_not_run_production(case, monkeypatch, failure):
    c = case
    listing = Mock(side_effect=RuntimeError('secret-cache-detail')) if failure == 'listing' else None
    refresh = (Mock(return_value={'error': 'cache_unavailable'}) if failure == 'cache'
               else Mock(side_effect=RuntimeError('secret-provider-detail')) if failure == 'refresh' else None)
    task, _ = _task(c, monkeypatch, listing=listing, refresh=refresh)
    before = {key: c.client.dump(key) for key in c.client.keys('*')}
    assert task() == {'status': 'unavailable', 'channel_count': 0, 'video_count': 0}
    assert {key: c.client.dump(key) for key in c.client.keys('*')} == before
    c.module._service.assert_not_called()


@pytest.mark.parametrize('swallowed_store_error', [False, True])
def test_empty_or_failed_real_inventory_read_preserves_previous_observations(case, monkeypatch, swallowed_store_error):
    c = case
    c.service.videos.return_value.list.return_value.execute.return_value = {'items': []}
    _refresh(c)  # Preserve owner-observed absence, not just old numeric counts.
    if swallowed_store_error:
        monkeypatch.setattr(studio_state, '_client', Mock(side_effect=ConnectionError('private-store-detail')))
    else:
        monkeypatch.setattr(studio_state, '_client', lambda: c.client)
    listing = Mock(wraps=studio_state.list_jobs)
    refresh = Mock(wraps=c.module.refresh_dashboard_metrics)
    task, _ = _task(c, monkeypatch, listing=listing, refresh=refresh)
    before = {key: c.client.dump(key) for key in c.client.keys('*')}
    assert task() == {'status': 'unavailable', 'channel_count': 0, 'video_count': 0}
    listing.assert_called_once_with(limit=500)
    refresh.assert_not_called()
    assert {key: c.client.dump(key) for key in c.client.keys('*')} == before
