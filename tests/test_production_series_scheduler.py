"""No live calls: real dispatch/execute/model fences around mocked Celery."""
import ast
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest

from test_channel_production import production, CHANNEL, CONNECTION, ROOT
from test_production_series_promotion import case, _load, _snapshot, _write, NOW, _answer


@pytest.fixture
def controller(case):
    ns = {**case.ns, **{key: case.prep[key] for key in (
        'PENDING_PREFIX', 'DAILY_PREFIX', '_FLAGS', '_context', '_digest', '_execution_guard',
        '_execution_keys', '_json', '_object', '_planning_channel_identity', '_require', 'prepare_next_series')},
        '_dispatch_profile_order': case.scheduler._dispatch_profile_order,
        'promote_ready_series': case.ns['promote_ready_series']}
    _load('app/services/production_scheduler.py', ns)
    ns['_client'] = lambda: case.client
    case.controller = ns
    return case


@pytest.fixture
def fresh(controller):
    c = controller
    # Remove only this local fixture's pre-existing completed planner attempt.
    for key in [c.pending_key, c.daily_key, *c.client.keys(c.prep['PREPARATION_DISPATCH_PREFIX'] + '*'),
                *c.client.keys(c.prep['PREPARATION_EXECUTION_PREFIX'] + '*')]:
        c.client.delete(key)
    c.prep['_generate'] = Mock(return_value=_answer())
    return c


def _maintain(c, enqueue, *, profiles=None, connections=None):
    return c.controller['maintain_production_series'](profiles or [c.profile], connections or [CONNECTION], enqueue, now=NOW)


def _queued(c, enqueue):
    result = _maintain(c, enqueue)
    assert result['channels'][CHANNEL] == 'preparation_queued'
    return enqueue.call_args.kwargs['args'][0]


def _execute(c, binding):
    return c.controller['run_series_preparation'](binding, binding['task_id'], now=NOW)


def test_tick_queues_separate_preparation_without_model_or_media_call(fresh):
    c, enqueue = fresh, Mock()
    before = _snapshot(c)
    binding = _queued(c, enqueue)
    assert enqueue.call_args.kwargs == {'args': [binding], 'task_id': binding['task_id'], 'retry': False}
    c.prep['_generate'].assert_not_called()
    assert c.client.get(c.pending_key) is c.client.get(c.daily_key) is None
    dispatch_key, execution_key = c.prep['_execution_keys'](binding)[:2]
    assert json.loads(c.client.get(dispatch_key))['status'] == 'reserved'
    assert json.loads(c.client.get(dispatch_key + ':delivery'))['status'] == 'dispatched'
    assert c.client.get(execution_key) is None
    assert all(c.client.dump(key) == value for key, value in before.items())
    assert _maintain(c, enqueue)['channels'][CHANNEL] == 'preparation_already_reserved'
    assert enqueue.call_count == 1


def test_worker_bound_execution_and_ready_promotion_feed_next_ordinary_tick(fresh):
    c, enqueue = fresh, Mock()
    binding = _queued(c, enqueue)
    assert _execute(c, binding)['status'] == 'ready'
    assert c.prep['_generate'].call_count == 1
    assert _execute(c, binding)['status'] == 'execution_already_claimed'
    assert c.prep['_generate'].call_count == 1
    result = _maintain(c, enqueue)
    assert result['channels'][CHANNEL] == 'promoted' and enqueue.call_count == 1
    profile = json.loads(c.client.get(c.profile_key))
    queued = c.scheduler.dispatch_due_productions([profile], [CONNECTION], Mock(), now=NOW)
    assert queued['status'] == 'queued'
    job = json.loads(c.client.get(c.ns['JOB_PREFIX'] + queued['task_id']))
    assert job['state'] == 'PENDING' and job['result'] is None
    assert job['spec']['production_topic_index'] == 0 and job['spec']['production_profile_revision'] == profile['profile_revision']


def test_many_concurrent_ticks_and_duplicate_workers_call_model_once(fresh):
    c, enqueue = fresh, Mock()
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: _maintain(c, enqueue), range(20)))
    assert enqueue.call_count == 1
    binding = enqueue.call_args.kwargs['args'][0]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: _execute(c, binding), range(12)))
    assert c.prep['_generate'].call_count == 1 and sum(r['status'] == 'ready' for r in results) == 1


def test_unknown_broker_acceptance_is_not_resent_on_later_tick(fresh):
    c, enqueue = fresh, Mock(side_effect=ConnectionError('simulated lost acceptance'))
    assert _maintain(c, enqueue)['channels'][CHANNEL] == 'preparation_dispatch_uncertain'
    assert _maintain(c, enqueue)['channels'][CHANNEL] == 'preparation_already_reserved'
    assert enqueue.call_count == 1
    binding = enqueue.call_args.kwargs['args'][0]
    dispatch_key = c.prep['_execution_keys'](binding)[0]
    assert json.loads(c.client.get(dispatch_key))['status'] == 'reserved'
    assert json.loads(c.client.get(dispatch_key + ':delivery'))['status'] == 'uncertain'
    # The original accepted message may still execute, exactly once.
    assert _execute(c, binding)['status'] == 'ready'
    assert c.prep['_generate'].call_count == 1


@pytest.mark.parametrize('field,value', [('series_total', 0), ('series_total', 1),
    ('series_total', 3), ('series_total', True), ('series_total', '2'), ('series_id', ''),
    ('series_id', None), ('series_id', 'bad id'), ('series_id', 'x' * 81), ('series_name', None),
    ('production_interval_hours', 5), ('production_interval_hours', 169),
    ('production_interval_hours', True)])
def test_invalid_finite_series_never_reserves_dispatch_or_spends(fresh, field, value):
    c, enqueue = fresh, Mock()
    c.profile[field] = value
    _write(c.client, c.profile_key, c.profile)
    before = _snapshot(c)
    assert _maintain(c, enqueue)['channels'][CHANNEL] == 'ineligible_or_changed'
    assert _snapshot(c) == before
    enqueue.assert_not_called()
    c.prep['_generate'].assert_not_called()


@pytest.mark.parametrize('field,value', [('series_total', 0), ('series_id', ''),
                                      ('series_name', None), ('production_interval_hours', 5)])
def test_finite_series_is_rechecked_by_worker_not_only_during_enqueue(fresh, field, value):
    c, enqueue = fresh, Mock()
    binding = _queued(c, enqueue)
    c.profile[field] = value
    _write(c.client, c.profile_key, c.profile)
    # Rebind only the fixture proof's profile hash to reach the new finite
    # series guard instead of merely failing the existing stale-hash check.
    dispatch_key, execution_key = c.prep['_execution_keys'](binding)[:2]
    record = json.loads(c.client.get(dispatch_key))
    record['profile_sha256'] = c.prep['_digest'](c.profile)
    _write(c.client, dispatch_key, record)
    before = _snapshot(c)
    assert _execute(c, binding)['status'] == 'unavailable'
    assert _snapshot(c) == before and c.client.get(execution_key) is None
    c.prep['_generate'].assert_not_called()


@pytest.mark.parametrize('phase', ['execution_claim', 'paid_reservation', 'pre_request_check'])
@pytest.mark.parametrize('delivery_status', ['dispatched', 'uncertain'])
def test_producer_delivery_annotation_cannot_abort_worker_proof_transaction(fresh, monkeypatch, phase, delivery_status):
    c, enqueue = fresh, Mock()
    binding = _queued(c, enqueue)
    dispatch_key, execution_key = c.prep['_execution_keys'](binding)[:2]
    raw = c.client.get(dispatch_key)
    original = c.client.pipeline
    target = {'execution_claim': 1, 'paid_reservation': 2, 'pre_request_check': 3}[phase]
    executions = []
    def pipeline(*args, **kwargs):
        pipe = original(*args, **kwargs)
        execute = pipe.execute
        def racing(*args, **kwargs):
            executions.append(True)
            if len(executions) == target:
                c.controller['_mark_dispatch'](c.client, dispatch_key, raw, delivery_status, delivery_only=True)
            return execute(*args, **kwargs)
        pipe.execute = racing
        return pipe
    monkeypatch.setattr(c.client, 'pipeline', pipeline)
    assert _execute(c, binding)['status'] == 'ready'
    assert c.prep['_generate'].call_count == 1
    assert c.client.get(execution_key) == binding['token']
    assert json.loads(c.client.get(dispatch_key))['status'] == 'finished'
    assert json.loads(c.client.get(dispatch_key + ':delivery'))['status'] == delivery_status
    assert _execute(c, binding)['status'] == 'execution_already_claimed'
    assert c.prep['_generate'].call_count == 1 and enqueue.call_count == 1


@pytest.mark.parametrize('phase', ['dispatch', 'execution'])
def test_lost_committed_reservation_reply_never_replays_or_calls_model(fresh, monkeypatch, phase):
    c, enqueue = fresh, Mock()
    binding = _queued(c, enqueue) if phase == 'execution' else None
    original = c.client.pipeline
    def pipeline(*args, **kwargs):
        pipe = original(*args, **kwargs)
        execute = pipe.execute
        def lost(*args, **kwargs):
            execute(*args, **kwargs)
            raise ConnectionError('simulated committed reply loss')
        pipe.execute = lost
        return pipe
    monkeypatch.setattr(c.client, 'pipeline', pipeline)
    if phase == 'dispatch':
        _maintain(c, enqueue)
        _maintain(c, enqueue)
        enqueue.assert_not_called()
    else:
        assert _execute(c, binding)['status'] == 'unavailable'
        assert _execute(c, binding)['status'] == 'execution_already_claimed'
    c.prep['_generate'].assert_not_called()


@pytest.mark.parametrize('mutation', ['paused', 'private', 'disabled', 'membership', 'credential', 'epoch',
                                     'wrong_task', 'wrong_token', 'not_near_end'])
def test_worker_rechecks_authority_before_any_model_reservation(fresh, mutation):
    c, enqueue = fresh, Mock()
    binding = _queued(c, enqueue)
    if mutation == 'paused': c.client.hset(c.state_key, 'paused_reason', 'previous_render_failed')
    elif mutation in {'private', 'disabled'}:
        profile = deepcopy(c.profile)
        profile['release_mode' if mutation == 'private' else 'production_enabled'] = 'private' if mutation == 'private' else False
        _write(c.client, c.profile_key, profile)
    elif mutation == 'membership': c.client.srem(c.ns['OAUTH_CHANNEL_INDEX'], CHANNEL)
    elif mutation == 'credential': c.client.set(c.ns['OAUTH_CREDENTIAL_PREFIX'] + CHANNEL, 'rotated-cipher')
    elif mutation == 'epoch': c.client.incr(c.ns['AUTH_EPOCH_KEY'])
    elif mutation == 'wrong_token': binding = {**binding, 'token': 'f' * 32}
    elif mutation == 'not_near_end': c.client.hset(c.state_key, 'cursor', '99')
    actual_task = str(UUID(int=100)) if mutation == 'wrong_task' else binding['task_id']
    assert c.controller['run_series_preparation'](binding, actual_task, now=NOW)['status'] == 'unavailable'
    assert c.client.get(c.daily_key) is None
    c.prep['_generate'].assert_not_called()


def test_pause_after_worker_execution_claim_blocks_paid_planner_reservation(fresh):
    c, enqueue = fresh, Mock()
    binding = _queued(c, enqueue)
    real = c.controller['prepare_next_series']
    def paused(*args, **kwargs):
        c.client.hset(c.state_key, 'paused_reason', 'previous_render_failed')
        return real(*args, **kwargs)
    c.controller['prepare_next_series'] = paused
    assert _execute(c, binding)['status'] == 'unavailable'
    assert c.client.get(c.daily_key) is None
    c.prep['_generate'].assert_not_called()


def test_paused_channel_does_not_block_other_linked_channel_maintenance(fresh):
    c, enqueue = fresh, Mock()
    bad = {**c.profile, 'channel_id': 'UC_missing_channel'}
    result = _maintain(c, enqueue, profiles=[bad, c.profile],
                       connections=[{'id': bad['channel_id'], 'connection_id': 'missing-connection'}, CONNECTION])
    assert result['channels'][bad['channel_id']] == 'ineligible_or_changed'
    assert result['channels'][CHANNEL] == 'preparation_queued' and enqueue.call_count == 1


def test_no_blind_preparation_for_paused_or_more_than_ten_linked_channels(fresh):
    c, enqueue = fresh, Mock()
    c.client.hset(c.state_key, 'paused_reason', 'previous_render_failed')
    assert _maintain(c, enqueue)['channels'][CHANNEL] == 'ineligible_or_changed'
    connections = [{'id': f'UC_channel_{i}', 'connection_id': f'connection-{i}'} for i in range(11)]
    assert _maintain(c, enqueue, connections=connections)['status'] == 'unavailable'
    enqueue.assert_not_called()


def test_ten_linked_channels_enqueue_at_most_once_each_without_touching_render_slots(fresh):
    c, enqueue = fresh, Mock()
    profiles, connections = [], []
    state = c.client.hgetall(c.state_key)
    for index in range(10):
        channel_id = f'UC_test_channel_{index:02d}'
        connection = {'id': channel_id, 'connection_id': f'connection-generation-{index:02d}'}
        profile = {**c.profile, 'channel_id': channel_id}
        profiles.append(profile)
        connections.append(connection)
        _write(c.client, c.ns['PROFILE_PREFIX'] + channel_id, profile)
        _write(c.client, c.ns['OAUTH_CHANNEL_PREFIX'] + channel_id, connection)
        c.client.set(c.ns['OAUTH_CREDENTIAL_PREFIX'] + channel_id, 'opaque-channel-credential')
        c.client.sadd(c.ns['OAUTH_CHANNEL_INDEX'], channel_id)
        c.client.hset(c.ns['CHANNEL_STATE_PREFIX'] + channel_id,
                      mapping={**state, 'connection_id': connection['connection_id']})
    claims = json.dumps({'version': 2, 'claims': [
        {'channel_id': profiles[index]['channel_id'], 'task_id': str(UUID(int=index + 999))} for index in range(2)]})
    c.client.set(c.ns['ACTIVE_KEY'], claims)
    result = _maintain(c, enqueue, profiles=profiles, connections=connections)
    assert len(result['channels']) == enqueue.call_count == 10
    assert set(result['channels'].values()) == {'preparation_queued'}
    _maintain(c, enqueue, profiles=profiles, connections=connections)
    assert enqueue.call_count == 10 and c.client.get(c.ns['ACTIVE_KEY']) == claims
    c.prep['_generate'].assert_not_called()


def test_telemetry_refresh_between_enqueue_and_worker_keeps_exact_editorial_binding(fresh):
    c, enqueue = fresh, Mock()
    binding = _queued(c, enqueue)
    _write(c.client, c.keys['channel'], {**CONNECTION, 'verified_at': '2026-09-06T12:00:00Z',
                                        'video_count': 99, 'view_count': 1000})
    assert _execute(c, binding)['status'] == 'ready' and c.prep['_generate'].call_count == 1


def _tick(c, maintenance):
    path = ROOT / 'app/production_tasks.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    tick = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'production_tick')
    tick.decorator_list = []
    tick.body = [n for n in tick.body if not isinstance(n, ast.ImportFrom)]
    order = []
    def dispatch(*args):
        order.append('render_dispatch')
        return {'status': 'queued', 'queued_count': 2}
    ns = {'connection_status': Mock(return_value={'connections': [CONNECTION]}),
          'list_channel_profiles': Mock(return_value=[c.profile]),
          'reconcile_active_production': lambda: order.append('reconcile_active'),
          'reconcile_public_retry_deliveries': lambda p: order.append('reconcile_retry') or {'resumed_count': 0},
          'dispatch_due_productions': dispatch, 'ChannelProductionError': c.scheduler.ChannelProductionError,
          'run_video_pipeline': SimpleNamespace(apply_async=Mock()),
          'prepare_series_batch': SimpleNamespace(apply_async=Mock()),
          'maintain_production_series': lambda *a: order.append('series_maintenance') or maintenance(*a)}
    exec(compile(ast.Module(body=[tick], type_ignores=[]), str(path), 'exec'), ns)
    return ns, order


def test_actual_tick_preserves_normal_order_and_success_when_maintenance_fails(controller):
    ns, order = _tick(controller, Mock(side_effect=RuntimeError('maintenance unavailable')))
    result = ns['production_tick']()
    assert order == ['reconcile_active', 'reconcile_retry', 'render_dispatch', 'series_maintenance']
    assert result['status'] == 'queued' and result['queued_count'] == 2
    assert result['series_maintenance']['status'] == 'unavailable'


def test_actual_normal_dispatch_failure_is_not_masked_by_maintenance(controller):
    maintenance = Mock()
    ns, order = _tick(controller, maintenance)
    ns['dispatch_due_productions'] = Mock(side_effect=controller.scheduler.ChannelProductionError('broken'))
    assert ns['production_tick']() == {'status': 'blocked', 'reason': 'production_state_unavailable'}
    maintenance.assert_not_called()


def test_preparation_task_registered_no_autoretry_and_no_unconsumed_queue():
    tree = ast.parse((ROOT / 'app/production_tasks.py').read_text(encoding='utf-8'))
    task = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'prepare_series_batch')
    options = {kw.arg: ast.literal_eval(kw.value) for kw in task.decorator_list[0].keywords}
    assert options == {'name': 'app.production_tasks.prepare_series_batch', 'bind': True, 'acks_late': False,
                       'autoretry_for': (), 'max_retries': 0, 'soft_time_limit': 110, 'time_limit': 120}
    assert 'queue' not in options
    celery = (ROOT / 'app/celery_app.py').read_text(encoding='utf-8')
    assert "'app.production_tasks'" in celery
