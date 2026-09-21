"""Distinct scheduled attempts preserve all earlier receipts; no live API calls."""
from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from test_channel_production import production, CHANNEL, CONNECTION
from test_production_series_promotion import case, NOW, _answer, _snapshot
from test_production_series_scheduler import controller, fresh, _queued, _execute


@pytest.fixture(autouse=True)
def enable_multiple_attempts(fresh, monkeypatch):
    monkeypatch.setattr(fresh.controller['settings'], 'studio_series_multiple_attempts_enabled', True, raising=False)


def tick(c, sender, now):
    profile = json.loads(c.client.get(c.profile_key))
    return c.controller['maintain_production_series']([profile], [CONNECTION], sender, now=now)['channels'][CHANNEL]


def execute(c, binding, now):
    return c.controller['run_series_preparation'](binding, binding['task_id'], now=now)


@pytest.mark.parametrize('status', ['uncertain', 'failed'])
def test_new_same_day_attempt_preserves_failed_request_and_feeds_normal_production(fresh, status):
    c, sender = fresh, Mock()
    first = _queued(c, sender)
    c.prep['_generate'].side_effect = (RuntimeError('synthetic timeout') if status == 'uncertain'
                                       else c.prep['_InvalidOutput']('synthetic invalid output'))
    assert _execute(c, first)['status'] == status
    c.client.set('synthetic:old-provider-receipt', 'reserved-even-if-outcome-unknown')
    before = _snapshot(c)
    assert tick(c, sender, NOW + 1799) == status
    assert _snapshot(c) == before and sender.call_count == 1
    assert tick(c, sender, NOW + 1800) in {'finished_uncertain_archived', 'finished_failure_archived'}
    assert sender.call_count == 1
    assert all(c.client.dump(k) == v for k, v in before.items() if k != c.pending_key)
    assert tick(c, sender, NOW + 1801) == 'preparation_queued'
    second = sender.call_args.kwargs['args'][0]
    assert second['version'] == 2 and second['preparation_slot'] == 2
    assert second['task_id'] != first['task_id'] and second['day'] == first['day']
    c.prep['_generate'].side_effect = None
    c.prep['_generate'].return_value = _answer()
    assert execute(c, second, NOW + 1801)['status'] == 'ready'
    assert c.prep['_generate'].call_count == 2
    assert c.client.dump(c.daily_key) == before[c.daily_key]
    assert c.client.get('synthetic:old-provider-receipt') == 'reserved-even-if-outcome-unknown'
    old = _snapshot(c)
    assert execute(c, first, NOW + 1801)['status'] == 'execution_already_claimed'
    assert execute(c, second, NOW + 1801)['status'] == 'execution_already_claimed'
    assert _snapshot(c) == old and c.prep['_generate'].call_count == 2
    assert tick(c, sender, NOW + 1802) == 'promoted'
    profile = json.loads(c.client.get(c.profile_key))
    dispatched = c.scheduler.dispatch_due_productions([profile], [CONNECTION], Mock(), now=NOW + 1803)
    assert dispatched['status'] == 'queued'
    job = json.loads(c.client.get(c.ns['JOB_PREFIX'] + dispatched['task_id']))
    assert job['state'] == 'PENDING' and job['result'] is None
    assert job['spec']['production_profile_revision'] == profile['profile_revision']


def test_three_daily_attempts_never_replay_and_fourth_waits_for_next_day(fresh):
    c, sender = fresh, Mock()
    first = _queued(c, sender)
    c.prep['_generate'].side_effect = RuntimeError('synthetic provider failure')
    assert _execute(c, first)['status'] == 'uncertain'
    bindings = [first]
    for index in (2, 3):
        now = NOW + 1801 * (index - 1)
        assert tick(c, sender, now) == 'finished_uncertain_archived'
        assert tick(c, sender, now) == 'preparation_queued'
        binding = sender.call_args.kwargs['args'][0]
        assert binding['preparation_slot'] == index
        assert execute(c, binding, now)['status'] == 'uncertain'
        bindings.append(binding)
    assert len({b['task_id'] for b in bindings}) == 3
    before = _snapshot(c)
    for now in (NOW + 5404, NOW + 7000):
        assert tick(c, sender, now) == 'daily_preparation_limit'
    assert sender.call_count == c.prep['_generate'].call_count == 3 and _snapshot(c) == before
    assert tick(c, sender, NOW + 86400) == 'finished_uncertain_archived'
    assert tick(c, sender, NOW + 86401) == 'preparation_queued'
    tomorrow = sender.call_args.kwargs['args'][0]
    assert tomorrow['version'] == 1 and 'preparation_slot' not in tomorrow
    assert tomorrow['task_id'] not in {b['task_id'] for b in bindings}
    assert all(c.client.dump(k) == v for k, v in before.items() if k != c.pending_key)


@pytest.mark.parametrize('damage', ['broker_unknown', 'unfinished_worker', 'missing_claim', 'missing_daily',
    'expired_daily', 'wrong_slot', 'future_started', 'missing_started', 'changed_daily', 'unexplained_error'])
def test_retry_cannot_retire_or_enqueue_without_terminal_evidence(fresh, damage):
    c, sender = fresh, Mock()
    binding = _queued(c, sender)
    c.prep['_generate'].side_effect = RuntimeError('synthetic failure')
    assert _execute(c, binding)['status'] == 'uncertain'
    dispatch_key, claim_key = c.prep['_execution_keys'](binding)[:2]
    dispatch = json.loads(c.client.get(dispatch_key))
    if damage == 'broker_unknown': dispatch['status'] = 'uncertain'
    if damage == 'unfinished_worker': dispatch['status'] = 'reserved'
    if damage == 'missing_claim': c.client.delete(claim_key)
    if damage == 'missing_daily': c.client.delete(c.daily_key)
    if damage == 'expired_daily': c.client.expire(c.daily_key, 60)
    if damage == 'wrong_slot': dispatch.update(version=2, preparation_slot=2)
    if damage == 'future_started': dispatch['created_at'] = NOW + 86400
    if damage == 'missing_started': dispatch.pop('created_at')
    if damage == 'changed_daily': c.client.set(c.daily_key, '{}')
    if damage == 'unexplained_error':
        record = json.loads(c.client.get(c.pending_key)); record['error_code'] = 'other'
        c.client.set(c.pending_key, c.prep['_json'](record)); c.client.set(c.daily_key, c.prep['_json'](record))
    c.client.set(dispatch_key, c.prep['_json'](dispatch))
    before = _snapshot(c)
    assert tick(c, sender, NOW + 1801) == 'ineligible_or_changed'
    assert _snapshot(c) == before and sender.call_count == 1 and c.prep['_generate'].call_count == 1


@pytest.mark.parametrize('slot', [True, False, 0, 1, 4, -1, '2', None])
def test_only_numbered_second_and_third_bindings_are_valid(fresh, slot):
    c, sender = fresh, Mock()
    binding = _queued(c, sender)
    bad = {**deepcopy(binding), 'version': 2, 'preparation_slot': slot}
    before = _snapshot(c)
    assert execute(c, bad, NOW)['status'] == 'unavailable'
    assert _snapshot(c) == before and c.prep['_generate'].call_count == 0


def test_concurrent_new_attempt_reservation_and_workers_still_generate_once(fresh):
    from concurrent.futures import ThreadPoolExecutor
    c, sender = fresh, Mock()
    first = _queued(c, sender)
    c.prep['_generate'].side_effect = RuntimeError('synthetic unknown result')
    assert _execute(c, first)['status'] == 'uncertain'
    assert tick(c, sender, NOW + 1801) == 'finished_uncertain_archived'
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: tick(c, sender, NOW + 1802), range(20)))
    assert sender.call_count == 2
    second = sender.call_args.kwargs['args'][0]
    c.prep['_generate'].side_effect = None
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(lambda _: execute(c, second, NOW + 1802), range(12)))
    assert sum(row['status'] == 'ready' for row in outcomes) == 1
    assert c.prep['_generate'].call_count == 2


@pytest.mark.parametrize('kind', ['daily', 'dispatch'])
def test_a_hole_in_daily_attempt_history_does_not_allow_reusing_an_earlier_slot(fresh, kind):
    c, sender = fresh, Mock()
    prefix = c.prep['DAILY_PREFIX'] if kind == 'daily' else c.prep['PREPARATION_DISPATCH_PREFIX']
    day = c.controller['datetime'].fromtimestamp(NOW, c.controller['timezone'].utc).date().isoformat()
    c.client.set(prefix + CHANNEL + ':' + day + ':attempt:3', '{}')
    before = _snapshot(c)
    assert tick(c, sender, NOW) == 'ineligible_or_changed'
    assert _snapshot(c) == before and sender.call_count == 0


def test_rollout_gate_keeps_same_day_failure_held_until_all_workers_support_numbered_attempts(fresh, monkeypatch):
    c, sender = fresh, Mock()
    first = _queued(c, sender)
    c.prep['_generate'].side_effect = RuntimeError('synthetic timeout')
    assert _execute(c, first)['status'] == 'uncertain'
    monkeypatch.setattr(c.controller['settings'], 'studio_series_multiple_attempts_enabled', False)
    before = _snapshot(c)
    assert tick(c, sender, NOW + 1801) == 'uncertain'
    assert _snapshot(c) == before and sender.call_count == 1
    monkeypatch.setattr(c.controller['settings'], 'studio_series_multiple_attempts_enabled', True)
    assert tick(c, sender, NOW + 1801) == 'finished_uncertain_archived'
    assert tick(c, sender, NOW + 1802) == 'preparation_queued'
    second = sender.call_args.kwargs['args'][0]
    # Old instances of this release still consume v2 tasks during flag rollout.
    monkeypatch.setattr(c.controller['settings'], 'studio_series_multiple_attempts_enabled', False)
    c.prep['_generate'].side_effect = None
    assert execute(c, second, NOW + 1802)['status'] == 'ready'
