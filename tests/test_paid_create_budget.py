import ast
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re

import fakeredis
import pytest


ROOT = Path(__file__).resolve().parents[1]
TASK_ID = '11111111-1111-4111-8111-111111111111'
JOB_KEY = 'youtube_studio:job:' + TASK_ID


def _load_state(client):
    source = ROOT / 'app' / 'services' / 'studio_state.py'
    names = {
        'JOB_PREFIX', 'JOB_TTL_SECONDS', 'PAID_CREATE_BUDGET_PREFIX',
        '_TASK_ID_PATTERN', '_PAID_CREATE_BUDGET',
    }
    definitions = [
        node for node in ast.parse(source.read_text(encoding='utf-8')).body
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id in names
                    for target in node.targets)
        ) or (
            isinstance(node, ast.FunctionDef)
            and node.name in {'_job_key', 'paid_create_budget_state'}
        )
    ]
    namespace = {
        're': re,
        '_client': lambda: client,
        '_now_iso': lambda: '2026-09-04T00:00:00+00:00',
    }
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(source), 'exec'), namespace)
    return namespace['paid_create_budget_state']


def _load_task_budget(state):
    source = ROOT / 'app' / 'tasks.py'
    names = {
        '_persisted_paid_create_slots', '_reserve_paid_create_slot',
        '_validate_paid_create_allocation',
    }
    definitions = [
        node for node in ast.parse(source.read_text(encoding='utf-8')).body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    namespace = {
        'paid_create_budget_state': state,
        'FinalVisualQualityError': RuntimeError,
    }
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(source), 'exec'), namespace)
    return namespace


@pytest.fixture
def budget():
    client = fakeredis.FakeRedis(decode_responses=True)
    client.set(JOB_KEY, json.dumps({'task_id': TASK_ID, 'state': 'PROGRESS'}))
    return client, _load_state(client)


def test_concurrent_reservations_stop_at_two_and_resume_saved_count(budget):
    client, state = budget

    def attempt(_index):
        try:
            return state(TASK_ID, 2, reserve=True)['used']
        except RuntimeError as exc:
            assert 'exhausted' in str(exc)
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        reservations = list(pool.map(attempt, range(12)))
    assert sorted(value for value in reservations if value is not None) == [1, 2]
    assert state(TASK_ID, 2) == {'used': 2, 'cap': 2, 'remaining': 0}
    record = json.loads(client.get(JOB_KEY))
    assert record['paid_create_slots_used'] == 2
    assert record['paid_create_slots_remaining'] == 0


def test_stale_job_write_cannot_reset_authoritative_budget(budget):
    client, state = budget
    state(TASK_ID, 2, reserve=True)
    state(TASK_ID, 2, reserve=True)
    client.set(JOB_KEY, json.dumps({'task_id': TASK_ID, 'state': 'FAILURE'}))
    assert state(TASK_ID, 2)['used'] == 2
    with pytest.raises(RuntimeError, match='exhausted'):
        state(TASK_ID, 2, reserve=True)
    assert json.loads(client.get(JOB_KEY))['state'] == 'FAILURE'
    assert json.loads(client.get(JOB_KEY))['paid_create_slots_used'] == 2


def test_existing_paid_result_is_not_reset_when_ledger_is_first_created(budget):
    client, state = budget
    client.set(JOB_KEY, json.dumps({
        'task_id': TASK_ID, 'result': {'runway_attempts': 6},
    }))
    assert state(TASK_ID, 2) == {'used': 6, 'cap': 2, 'remaining': 0}
    with pytest.raises(RuntimeError, match='exhausted'):
        state(TASK_ID, 2, reserve=True)


def test_exhausted_budget_allows_recovery_only_but_no_new_repairs(budget):
    _client, state = budget
    state(TASK_ID, 2, reserve=True)
    state(TASK_ID, 2, reserve=True)
    boundary = _load_task_budget(state)
    used = boundary['_persisted_paid_create_slots'](TASK_ID, 2)
    selected = [{'scene_index': index} for index in range(6)]
    recovered = {'scenes': {index: ['existing.mp4'] for index in range(6)}}
    validate = boundary['_validate_paid_create_allocation']
    validate(selected, recovered, 2, paid_slots_used=used)
    del recovered['scenes'][4]
    with pytest.raises(RuntimeError, match='paid-create cap'):
        validate(selected, recovered, 2, paid_slots_used=used)


def test_lost_reservation_reply_fails_closed_and_keeps_consumed_slot(budget):
    client, state = budget

    def lost_reply(task_id, cap, *, reserve=False):
        result = state(task_id, cap, reserve=reserve)
        if reserve:
            raise ConnectionError('Redis reply was lost')
        return result

    reserve = _load_task_budget(lost_reply)['_reserve_paid_create_slot']
    with pytest.raises(RuntimeError, match='no new generation was submitted'):
        reserve(0, 2, task_id=TASK_ID)
    assert state(TASK_ID, 2)['used'] == 1
    assert json.loads(client.get(JOB_KEY))['paid_create_slots_used'] == 1


def test_missing_or_corrupt_budget_state_never_authorizes_a_create(budget):
    client, state = budget
    client.delete(JOB_KEY)
    with pytest.raises(RuntimeError, match='unavailable'):
        state(TASK_ID, 2, reserve=True)
    client.set(JOB_KEY, json.dumps({'task_id': TASK_ID}))
    client.hset('youtube_studio:paid_create_budget:' + TASK_ID, mapping={
        'used': 'not-an-integer', 'cap': '2',
    })
    with pytest.raises(RuntimeError, match='unavailable'):
        state(TASK_ID, 2, reserve=True)


def test_non_preview_retains_local_count_and_never_opens_budget_store():
    def unavailable(*_args, **_kwargs):
        raise AssertionError('Non-preview should not use capped ledger')

    reserve = _load_task_budget(unavailable)['_reserve_paid_create_slot']
    assert reserve(3, None, task_id=TASK_ID) == 4
