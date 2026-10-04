"""A rejected Short releases its channel pause a bounded number of times a day."""
import hashlib
import json

import fakeredis
import pytest

from app.config import settings
from app.services import channel_production as production, rejection_auto_advance as advance

CHANNEL = 'UC' + 'a' * 22
NOW = 1_790_000_000.0


def _task(number):
    return f'{number:08d}-1111-4111-8111-111111111111'


@pytest.fixture
def client(monkeypatch):
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(production, '_redis', lambda: fake)
    monkeypatch.setattr(settings, 'studio_spend_enforcement', False)
    monkeypatch.setattr(settings, 'studio_auto_advance_after_rejection', True)
    monkeypatch.setattr(settings, 'studio_auto_advance_max_per_day', 2)
    monkeypatch.setattr(settings, 'studio_auto_advance_max_in_a_row', 5)
    return fake


def _failed(client, number, *, code='story_quality_exhausted', category='content_rejected',
            stage='director_qc', fmt='shorts', paused='previous_render_failed',
            error='Story rejected after the last correction'):
    task_id = _task(number)
    client.set(production.JOB_PREFIX + task_id, json.dumps({
        'task_id': task_id, 'kind': 'render', 'state': 'FAILURE', 'error': error, 'failure_stage': stage,
        'spec': {'production_scheduled': True, 'production_channel_id': CHANNEL, 'format': fmt},
        'failure_classification': {'version': 1, 'code': code, 'category': category, 'stage': stage,
                                   'error_sha256': hashlib.sha256(error.encode()).hexdigest()}}))
    client.hset(production.CHANNEL_STATE_PREFIX + CHANNEL, mapping={
        'paused_reason': paused, 'last_result': 'FAILURE', 'last_task_id': task_id,
        'next_due': str(NOW + 86400), 'cursor': str(number)})
    return task_id


def _profiles():
    return [{'channel_id': CHANNEL, 'production_enabled': True, 'auto_publish': True}]


def test_rejected_short_releases_the_pause_and_starts_soon(client):
    task_id = _failed(client, 1)
    assert advance.maintain(_profiles(), now=NOW) == {'status': 'checked', 'channels': {CHANNEL: 'advanced'}}
    state = client.hgetall(production.CHANNEL_STATE_PREFIX + CHANNEL)
    assert 'paused_reason' not in state and float(state['next_due']) == NOW + 600
    assert state['auto_advanced_task_id'] == task_id and state['cursor'] == '1'
    assert advance.maintain(_profiles(), now=NOW)['channels'][CHANNEL] == 'not_paused'


def test_daily_limit_then_the_owner_decides(client):
    for number in (1, 2):
        _failed(client, number)
        assert advance.advance(CHANNEL, now=NOW) == 'advanced'
    _failed(client, 3)
    assert advance.advance(CHANNEL, now=NOW) == 'daily_limit'
    assert client.hget(production.CHANNEL_STATE_PREFIX + CHANNEL, 'paused_reason') == 'previous_render_failed'
    assert advance.advance(CHANNEL, now=NOW + 86400) == 'advanced'  # the next Turkey day


@pytest.mark.parametrize('case', [
    {'code': 'spending_blocked', 'category': 'spending_blocked'},
    {'code': 'worker_process_lost', 'category': 'execution_interrupted'},
    {'code': 'audio_review_unverified', 'category': 'review_unverified', 'stage': 'audio_qc'},
    {'code': 'story_quality_exhausted', 'category': 'content_rejected', 'stage': 'render'},
])
def test_other_failures_still_wait_for_the_owner(client, case):
    _failed(client, 1, **case)
    assert advance.advance(CHANNEL, now=NOW) == 'needs_owner'
    assert client.hget(production.CHANNEL_STATE_PREFIX + CHANNEL, 'paused_reason') == 'previous_render_failed'


def test_script_spend_cap_waits_for_the_owner(client):
    # A Short that hit its script cap points at a looping planner, not one bad topic.
    _failed(client, 1, code='spending_blocked', category='spending_blocked', stage='research',
            error='cost_planning_task_cap_reached')
    assert advance.advance(CHANNEL, now=NOW) == 'needs_owner'


def test_failures_in_a_row_without_a_public_video_pause_again(client, monkeypatch):
    monkeypatch.setattr(settings, 'studio_auto_advance_max_in_a_row', 2)
    for number, day in ((1, 0), (2, 1)):
        _failed(client, number)
        assert advance.advance(CHANNEL, now=NOW + day * 86400) == 'advanced'
    _failed(client, 3)
    assert advance.advance(CHANNEL, now=NOW + 2 * 86400) == 'too_many_in_a_row'
    state_key = production.CHANNEL_STATE_PREFIX + CHANNEL
    assert client.hget(state_key, 'paused_reason') == 'previous_render_failed'
    # A video that went public after the run began resets it.
    client.hset(state_key, 'last_public_continued_at', str(NOW + 2 * 86400 - 60))
    assert advance.advance(CHANNEL, now=NOW + 2 * 86400) == 'advanced'
    state = client.hgetall(state_key)
    assert state['auto_advance_streak'] == '1' and float(state['auto_advance_streak_since']) == NOW + 2 * 86400


@pytest.mark.parametrize('field,value', [('auto_advance_streak', 'x'), ('auto_advance_streak_since', 'nan'),
                                         ('auto_advance_streak', '-1')])
def test_unreadable_run_count_waits_for_the_owner(client, field, value):
    _failed(client, 1)
    client.hset(production.CHANNEL_STATE_PREFIX + CHANNEL, field, value)
    assert advance.advance(CHANNEL, now=NOW) == 'too_many_in_a_row'


def test_daily_spend_cap_moves_on_the_next_turkey_day(client):
    _failed(client, 1, code='spending_blocked', category='spending_blocked', stage='voice',
            error='cost_daily_cap_reached')
    assert advance.advance(CHANNEL, now=NOW) == 'advanced'
    due = float(client.hget(production.CHANNEL_STATE_PREFIX + CHANNEL, 'next_due'))
    local = advance.datetime.fromtimestamp(due, advance._LOCAL_TZ)
    assert (local.hour, local.minute) == (0, 10) and NOW < due <= NOW + 86400 + 600


@pytest.mark.parametrize('case', [
    {'error': 'cost_daily_cap_reached!'},
    {'error': 'cost_daily_cap_reached', 'code': 'unclassified_failure', 'category': 'unclassified'},
])
def test_spend_cap_needs_the_exact_classified_code(client, case):
    _failed(client, 1, **{'code': 'spending_blocked', 'category': 'spending_blocked', 'stage': 'voice', **case})
    assert advance.advance(CHANNEL, now=NOW) == 'needs_owner'


def test_spend_cap_record_with_a_stale_digest_waits_for_the_owner(client):
    task_id = _failed(client, 1, code='spending_blocked', category='spending_blocked', stage='voice',
                      error='cost_daily_cap_reached')
    job = json.loads(client.get(production.JOB_PREFIX + task_id))
    job['error'] = 'cost_planning_task_cap_reached'
    client.set(production.JOB_PREFIX + task_id, json.dumps(job))
    assert advance.advance(CHANNEL, now=NOW) == 'needs_owner'


@pytest.mark.parametrize('case', [{'fmt': 'landscape'}, {'paused': 'owner_paused'}])
def test_long_films_and_owner_pauses_are_untouched(client, case):
    _failed(client, 1, **case)
    assert advance.advance(CHANNEL, now=NOW) in {'not_eligible', 'not_paused'}
    assert client.hget(production.CHANNEL_STATE_PREFIX + CHANNEL, 'paused_reason') == case.get(
        'paused', 'previous_render_failed')


def test_running_job_or_enforcement_or_setting_off_never_advances(client, monkeypatch):
    _failed(client, 1)
    client.hset(production.CHANNEL_STATE_PREFIX + CHANNEL, 'active_task_id', _task(9))
    assert advance.advance(CHANNEL, now=NOW) == 'not_eligible'
    client.hdel(production.CHANNEL_STATE_PREFIX + CHANNEL, 'active_task_id')
    monkeypatch.setattr(settings, 'studio_spend_enforcement', True)
    assert advance.maintain(_profiles(), now=NOW)['status'] == 'disabled'
    monkeypatch.setattr(settings, 'studio_spend_enforcement', False)
    monkeypatch.setattr(settings, 'studio_auto_advance_after_rejection', False)
    assert advance.maintain(_profiles(), now=NOW)['status'] == 'disabled'
    assert client.hget(production.CHANNEL_STATE_PREFIX + CHANNEL, 'paused_reason') == 'previous_render_failed'


def test_new_system_default_is_on_with_three_a_day():
    from app.config import Settings
    assert Settings.model_fields['studio_auto_advance_after_rejection'].default is True
    assert Settings.model_fields['studio_auto_advance_max_per_day'].default == 3
    assert Settings.model_fields['studio_auto_advance_max_in_a_row'].default == 3
