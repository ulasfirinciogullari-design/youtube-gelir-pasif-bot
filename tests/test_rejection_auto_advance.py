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
    return fake


def _failed(client, number, *, code='story_quality_exhausted', category='content_rejected',
            stage='director_qc', fmt='shorts', paused='previous_render_failed'):
    task_id, error = _task(number), 'Story rejected after the last correction'
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
