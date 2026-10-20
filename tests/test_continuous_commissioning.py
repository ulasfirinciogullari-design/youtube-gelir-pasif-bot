"""Real state transitions preserve old work while commissioning keeps moving."""
from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from app.services import production_continuation as continuation
from app.services import production_quality_holds as holds, channel_production as production
from app.services import studio_state as jobs, studio_operations as operations
from test_series_quality_continuation import series, ready, native, case, policy, NOW, promote
from test_full_video_rebuild import SOURCE, _all


def enable(client, channel):
    value = {'version': 1, 'kind': 'continuous_commissioning', 'allowed_channels': [channel],
        'authorized_at': NOW.isoformat(), 'owner_evidence_sha256': 'f' * 64}
    assert continuation.initialize(client, value) is True
    return value


def test_exact_authority_is_idempotent_and_deactivation_does_not_erase_history(series):
    n = series; channel = n.profile['channel_id']; value = enable(n.client, channel)
    before = _all(n.client)
    assert continuation.initialize(n.client, value) is False
    assert _all(n.client) == before
    with pytest.raises(ValueError):
        continuation.initialize(n.client, {**value, 'owner_evidence_sha256': 'a' * 64})
    n.client.delete(continuation.ACTIVE_KEY)
    with n.client.pipeline() as p:
        assert continuation.authority(p, channel) is None
        assert continuation.authority(p, channel, active=False) == continuation._sha(continuation._raw(value))


@pytest.mark.parametrize('damage', ['anchor', 'pointer', 'ttl'])
def test_corrupt_authority_cannot_release_a_failed_episode(series, damage):
    n = series; enable(n.client, n.profile['channel_id'])
    if damage == 'anchor': n.client.set(continuation.ANCHOR_KEY, 'changed')
    if damage == 'pointer': n.client.set(continuation.ACTIVE_KEY, 'changed')
    if damage == 'ttl': n.client.expire(continuation.AUTHORIZATION_KEY, 60)
    before = _all(n.client)
    with pytest.raises(ValueError): holds.hold_failed_episode(n.profile)
    assert _all(n.client) == before


def test_fourth_hold_preserves_original_days_and_advances_new_series_even_after_deactivation(series):
    n = series
    roots = ['10000000-0000-4000-8000-00000000000' + str(i) for i in range(3)]
    day = holds._raw(roots)
    n.client.set(n.day_key, day)
    history = holds._raw({'version': 1, 'days': {n.day_key: holds._sha(day)}})
    n.client.set(holds.HISTORY_KEY, history); n.client.set(holds.HISTORY_ANCHOR, holds._sha(history))
    # Prior original receipts are immutable; this fixture's earlier roots have
    # no current-profile topics and are not fabricated publication evidence.
    for root in roots:
        n.client.set(holds.HOLD_PREFIX + root, holds._raw({'channel_id': n.profile['channel_id'],
            'profile_revision': 'a-previous-profile', 'root_task_id': root}))
    enable(n.client, n.profile['channel_id'])
    due = NOW.timestamp() + 6 * 3600
    n.client.hset(n.state_key, 'next_due', str(due))
    before = _all(n.client)
    assert holds.hold_failed_episode(n.profile)['status'] == 'held_unpublished'
    record = json.loads(n.client.get(holds.HOLD_PREFIX + SOURCE))
    assert record['previous_next_due'] == due and record['next_due'] == NOW.timestamp()
    assert json.loads(n.client.get(n.day_key)) == roots + [SOURCE]
    assert record['publish_eligible'] is False and record['retry_dispatched'] is False
    assert n.client.hget(n.state_key, 'next_due') == str(NOW.timestamp())
    changed = {n.state_key, n.day_key, holds.HISTORY_KEY, holds.HISTORY_ANCHOR}
    assert all(_all(n.client)[k] == v for k, v in before.items() if k not in changed)
    n.client.delete(continuation.ACTIVE_KEY)
    assert promote(n)['status'] == 'promoted'
    assert n.client.hget(n.state_key, 'last_public_task_id') is None
    assert not n.case.calls


def test_already_held_episode_promotes_without_waiting_six_hours_when_commissioned(series):
    n = series
    due = str(NOW.timestamp() + 6 * 3600)
    n.client.hset(n.state_key, 'next_due', due)
    holds.hold_failed_episode(n.profile)
    old_hold = n.client.get(holds.HOLD_PREFIX + SOURCE)
    enable(n.client, n.profile['channel_id'])
    result = promote(n)
    assert n.client.hget(n.state_key, 'next_due') == str(NOW.timestamp() + 1)
    assert n.client.get(holds.HOLD_PREFIX + SOURCE) == old_hold
    assert json.loads(n.client.get(production.PROFILE_PREFIX + n.profile['channel_id']))['production_interval_hours'] == n.profile['production_interval_hours']
    archive = json.loads(n.client.get(result['archive_key']))
    assert archive['state']['next_due'] == due and archive['continuation_authority_sha256']
    assert archive['unpublished_proof']['publish_eligible'] is False


def test_extended_day_requires_actual_authorized_hold_receipt(series):
    n = series; enable(n.client, n.profile['channel_id'])
    roots = ['10000000-0000-4000-8000-00000000000' + str(i) for i in range(4)]
    n.client.set(holds.HOLD_PREFIX + roots[-1], holds._raw({'root_task_id': roots[-1],
        'channel_id': n.profile['channel_id']}))
    with n.client.pipeline() as p, pytest.raises(ValueError):
        continuation.validate_extended_hold_day(p, roots, n.profile['channel_id'], 3, holds.HOLD_PREFIX)


def test_deactivation_racing_hold_cannot_advance_the_schedule(series, monkeypatch):
    from redis.exceptions import WatchError
    n = series; enable(n.client, n.profile['channel_id'])
    read = continuation.authority
    def racing(reader, channel_id, **kwargs):
        result = read(reader, channel_id, **kwargs)
        n.client.delete(continuation.ACTIVE_KEY)
        return result
    monkeypatch.setattr(continuation, 'authority', racing)
    before = _all(n.client)
    with pytest.raises(WatchError): holds.hold_failed_episode(n.profile)
    assert _all(n.client) == {k: v for k, v in before.items() if k != continuation.ACTIVE_KEY}


def test_fourth_attempt_is_visible_without_a_false_daily_wait(series):
    n = series; channel = n.profile['channel_id']; enable(n.client, channel)
    value = {'channel_id': channel, 'profile_revision': n.profile['profile_revision'],
        'day': NOW.date().isoformat(), 'status': 'uncertain', 'preparation_slot': 4}
    n.client.set(operations.PENDING_PREFIX + channel, json.dumps(value))
    before = _all(n.client)
    assert operations.read_series_preparation(channel, n.profile['profile_revision'],
        client=n.client, now=NOW.timestamp()) == {'status': 'uncertain', 'daily_wait': False, 'attempt_number': 4}
    assert _all(n.client) == before
