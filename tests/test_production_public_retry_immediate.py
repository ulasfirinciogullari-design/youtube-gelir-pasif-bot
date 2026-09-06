"""A proven public retry can resume now without rewriting any earlier proof."""
from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from test_production_recovery import CHANNEL, REVISION, _snapshot, _change, recovery as private_recovery
from test_production_recovery_public import case, _run as delayed_run


def _run(case, *, now=100000, immediate=True):
    module, _client, data = case
    return module.resume_after_public_retry(
        CHANNEL, data.original_id, data.recovered_id, REVISION,
        now=now, continue_immediately=immediate,
    )


def test_immediate_resume_changes_only_pause_due_and_one_audit(case):
    module, client, data = case
    before = _snapshot(client)
    result = _run(case)
    assert result['status'] == 'resumed'
    assert result['continue_immediately'] is True
    assert result['next_due'] == result['resumed_at'] == 100000
    assert result['cursor'] == 2
    after = _snapshot(client)
    audit = json.loads(after.pop(data.audit_key))
    assert audit == {key: value for key, value in result.items() if key != 'status'}
    state = after.pop(data.state_key)
    expected_state = before.pop(data.state_key)
    expected_state.pop('paused_reason')
    expected_state['next_due'] = '100000'
    assert state == expected_state and after == before
    assert client.ttl(data.audit_key) == -1


def test_default_keeps_the_existing_six_hour_interval(case):
    result = delayed_run(case)
    assert result['continue_immediately'] is False
    assert result['next_due'] == 121600


def test_immediate_replay_after_next_job_cannot_shift_time_or_cursor(case):
    module, client, data = case
    first = _run(case)
    client.set(module.ACTIVE_KEY, 'a-later-active-job')
    client.hset(data.state_key, mapping={'cursor': '3', 'active_task_id': 'later', 'next_due': '999999'})
    before = _snapshot(client)
    assert _run(case, now=900000) == {**first, 'status': 'already_resumed'}
    assert _snapshot(client) == before


@pytest.mark.parametrize('initial', [False, True])
def test_prior_timing_choice_cannot_be_rewritten(case, initial):
    _module, client, _data = case
    _run(case, immediate=initial)
    before = _snapshot(client)
    with pytest.raises(case[0].ProductionRecoveryError, match='recovery_audit_invalid'):
        _run(case, now=900000, immediate=not initial)
    assert _snapshot(client) == before


def test_legacy_public_audit_without_option_remains_default_only(case):
    module, client, data = case
    first = delayed_run(case)
    audit = json.loads(client.get(data.audit_key))
    audit.pop('continue_immediately')
    client.set(data.audit_key, json.dumps(audit))
    result = delayed_run(case, now=900000)
    assert result['status'] == 'already_resumed' and result['next_due'] == first['next_due']
    before = _snapshot(client)
    with pytest.raises(module.ProductionRecoveryError, match='recovery_audit_invalid'):
        _run(case)
    assert _snapshot(client) == before


@pytest.mark.parametrize('value', [None, 0, 1, 'true', 'false', {}, []])
def test_immediate_option_requires_a_real_boolean_before_any_write(case, value):
    module, client, _data = case
    before = _snapshot(client)
    with pytest.raises(module.ProductionRecoveryError, match='recovery_continuation_invalid'):
        _run(case, immediate=value)
    assert _snapshot(client) == before


@pytest.mark.parametrize('damage', ['caption', 'thumbnail', 'disclosure', 'private', 'qa', 'profile', 'active'])
def test_immediate_mode_keeps_every_publication_and_schedule_guard(case, damage):
    module, client, data = case
    if damage == 'active':
        client.set(module.ACTIVE_KEY, 'another-active-job')
    elif damage == 'profile':
        _change(client, module.PROFILE_PREFIX + CHANNEL, lambda record: record.update(profile_revision='changed'))
    elif damage == 'qa':
        _change(client, module.JOB_PREFIX + data.recovered_id,
                lambda record: record['result'].update(manual_qa_required=True))
    else:
        field, value = {'caption': ('caption_uploaded', False), 'thumbnail': ('thumbnail_uploaded', False),
                        'disclosure': ('contains_synthetic_media', False), 'private': ('privacy_status', 'private')}[damage]
        _change(client, module.JOB_PREFIX + data.publish_id,
                lambda record: record['result'].update({field: value}))
    before = _snapshot(client)
    with pytest.raises(module.ProductionRecoveryError):
        _run(case)
    assert _snapshot(client) == before


def test_concurrent_immediate_resume_commits_only_once(case):
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: _run(case), range(6)))
    assert sum(result['status'] == 'resumed' for result in results) == 1
    assert {result['next_due'] for result in results} == {100000}


def test_lost_atomic_reply_replays_the_original_immediate_choice(case, monkeypatch):
    module, client, data = case
    original = client.eval
    def lost(*args, **kwargs):
        original(*args, **kwargs)
        raise ConnectionError('private transport detail')
    monkeypatch.setattr(client, 'eval', lost)
    with pytest.raises(module.ProductionRecoveryError, match='recovery_state_unavailable'):
        _run(case)
    monkeypatch.setattr(client, 'eval', original)
    result = _run(case, now=900000)
    assert result['status'] == 'already_resumed'
    assert result['continue_immediately'] is True and result['next_due'] == 100000
    assert client.hget(data.state_key, 'next_due') == '100000'
