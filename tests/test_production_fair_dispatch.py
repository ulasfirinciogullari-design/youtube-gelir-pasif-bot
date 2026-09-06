"""Durable fair priority with real reservation Lua and public delivery proof."""
from concurrent.futures import ThreadPoolExecutor
import json
from unittest.mock import Mock

import pytest

from test_channel_production import production, _parallel_channels, _save
from test_production_continuous_public import _public_finish


def _channels(case, count=10):
    module, client = case
    profiles, connections = _parallel_channels(case, count)
    for profile, connection in zip(profiles, connections):
        profile.update(release_mode='public', production_topics=[
            'First distinct documentary question', 'Second distinct documentary question',
            'Third distinct documentary question',
        ])
        _save(module, client, profile, connection)
    return module, client, profiles, connections


def test_waiting_third_channel_precedes_just_published_first_channel(production):
    module, client, profiles, connections = _channels(production, 3)
    first = module.dispatch_due_productions(profiles, connections, Mock(), now=1000)
    completed, running = first['queued']
    _public_finish(module, client, completed['task_id'])
    running_before = client.get(module.JOB_PREFIX + running['task_id'])
    next_tick = module.dispatch_due_productions(profiles, connections, Mock(), now=1001)
    assert next_tick['queued_count'] == 1
    assert next_tick['channel_id'] == profiles[2]['channel_id']
    assert module.get_production_state(profiles[0]['channel_id'])['cursor'] == '1'
    assert module.get_production_state(profiles[0]['channel_id'])['next_due'] == '1001'
    assert client.get(module.JOB_PREFIX + running['task_id']) == running_before
    assert len(module._decode_active_claims(client.get(module.ACTIVE_KEY))) == 2


def test_ten_channels_each_get_one_turn_before_any_gets_second_with_immediate_public_chaining(production):
    module, client, profiles, connections = _channels(production)
    expected = [profile['channel_id'] for profile in profiles]
    seen = []
    for wave in range(10):
        # Input order and clock progression are not the fairness mechanism.
        result = module.dispatch_due_productions(list(reversed(profiles)), connections, Mock(), now=1000)
        assert result['queued_count'] == 2
        seen.extend(item['channel_id'] for item in result['queued'])
        assert client.get(module.DISPATCH_CURSOR_KEY) == result['queued'][-1]['channel_id']
        for item in result['queued']:
            _public_finish(module, client, item['task_id'])
    assert seen == expected + expected
    assert all(module.get_production_state(channel)['cursor'] == '2' for channel in expected)


def test_concurrent_ticks_keep_fair_turns_without_overbooking_or_duplicate_topics(production):
    module, client, profiles, connections = _channels(production)
    seen = []
    enqueue = Mock()
    for wave in range(5):
        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(lambda _: module.dispatch_due_productions(
                profiles, connections, enqueue, now=1000 + wave,
            ), range(8)))
        queued = [item for result in results for item in result.get('queued', [])]
        assert len(queued) == 2
        assert len({item['channel_id'] for item in queued}) == 2
        assert len(module._decode_active_claims(client.get(module.ACTIVE_KEY))) == 2
        seen.extend(item['channel_id'] for item in queued)
        for item in queued:
            _public_finish(module, client, item['task_id'])
    assert set(seen) == {profile['channel_id'] for profile in profiles}
    assert len(seen) == len(set(seen)) == enqueue.call_count == 10
    assert len({call.kwargs['task_id'] for call in enqueue.call_args_list}) == 10


def test_reservation_advances_durable_cursor_before_enqueue_but_failed_eligibility_does_not(production):
    module, client, profiles, connections = _channels(production, 3)
    client.set(module.DISPATCH_CURSOR_KEY, profiles[0]['channel_id'])
    client.hset(module.CHANNEL_STATE_PREFIX + profiles[1]['channel_id'], 'paused_reason', 'previous_render_failed')
    seen = []
    def enqueue(*, task_id, args):
        job = json.loads(client.get(module.JOB_PREFIX + task_id))
        channel = job['spec']['production_channel_id']
        assert client.get(module.DISPATCH_CURSOR_KEY) == channel
        assert job['state'] == 'PENDING'
        seen.append(channel)
    result = module.dispatch_due_productions(profiles, connections, enqueue, now=1000)
    assert seen == [profiles[2]['channel_id'], profiles[0]['channel_id']]
    assert result['queued_count'] == 2
    assert module.get_production_state(profiles[1]['channel_id']) == {'paused_reason': 'previous_render_failed'}


@pytest.mark.parametrize('kind,value', [
    ('missing', None), ('string', ''), ('string', 'not/a/channel'),
    ('string', '{"claims":[]}'), ('string', 'x' * 129), ('hash', {'broken': 'state'}),
])
def test_bad_priority_metadata_only_bootstraps_order_never_changes_existing_claim(production, kind, value):
    module, client, profiles, connections = _channels(production, 3)
    first = module.reserve_due_production(profiles[0], connections[0], now=1000)
    before = client.get(module.JOB_PREFIX + first['task_id'])
    client.delete(module.DISPATCH_CURSOR_KEY)
    if kind == 'string': client.set(module.DISPATCH_CURSOR_KEY, value)
    elif kind == 'hash': client.hset(module.DISPATCH_CURSOR_KEY, mapping=value)
    result = module.dispatch_due_productions(profiles, connections, Mock(), now=1001)
    assert result['queued_count'] == 1 and result['channel_id'] == profiles[1]['channel_id']
    assert client.get(module.JOB_PREFIX + first['task_id']) == before
    assert len(module._decode_active_claims(client.get(module.ACTIVE_KEY))) == 2


def test_removed_channel_cursor_resumes_at_next_channel_without_requiring_old_membership(production):
    module, client, profiles, connections = _channels(production, 3)
    client.set(module.DISPATCH_CURSOR_KEY, 'UC_parallel_0_removed')
    result = module.dispatch_due_productions(profiles, connections, Mock(), now=1000)
    assert [item['channel_id'] for item in result['queued']] == [profiles[1]['channel_id'], profiles[2]['channel_id']]


def test_cursor_does_not_make_private_delivery_or_future_due_eligible(production):
    from test_channel_production import _finish
    module, client, profiles, connections = _channels(production, 1)
    enqueue = Mock()
    first = module.dispatch_due_productions(profiles, connections, enqueue, now=1000)
    _finish(module, client, first['task_id'])
    prior_cursor = client.get(module.DISPATCH_CURSOR_KEY)
    second = module.dispatch_due_productions(profiles, connections, enqueue, now=1001)
    assert second['channels'][profiles[0]['channel_id']] == 'not_due'
    assert enqueue.call_count == 1 and client.get(module.DISPATCH_CURSOR_KEY) == prior_cursor


@pytest.mark.parametrize('lost', ['reservation', 'enqueue'])
def test_lost_reply_keeps_cursor_and_one_shot_claim_without_resend(production, monkeypatch, lost):
    module, client, profiles, connections = _channels(production, 1)
    original = client.eval
    if lost == 'reservation':
        def evaluate(script, *args):
            result = original(script, *args)
            if script == module._RESERVE:
                raise TimeoutError('mocked lost reservation reply')
            return result
        monkeypatch.setattr(client, 'eval', evaluate)
    enqueue = Mock(side_effect=TimeoutError('mocked lost enqueue reply') if lost == 'enqueue' else None)
    module.dispatch_due_productions(profiles, connections, enqueue, now=1000)
    assert client.get(module.DISPATCH_CURSOR_KEY) == profiles[0]['channel_id']
    active = client.get(module.ACTIVE_KEY)
    monkeypatch.setattr(client, 'eval', original)
    assert module.dispatch_due_productions(profiles, connections, enqueue, now=1001)['status'] == 'active'
    assert client.get(module.ACTIVE_KEY) == active
    assert enqueue.call_count == int(lost == 'enqueue')


def test_unavailable_priority_read_never_allocates_a_new_claim(production, monkeypatch):
    module, client, profiles, connections = _channels(production, 1)
    original = client.type
    def unavailable(key):
        if key == module.DISPATCH_CURSOR_KEY:
            raise ConnectionError('mocked Redis outage')
        return original(key)
    monkeypatch.setattr(client, 'type', unavailable)
    enqueue = Mock()
    with pytest.raises(module.ChannelProductionError, match='production_state_unavailable'):
        module.dispatch_due_productions(profiles, connections, enqueue, now=1000)
    enqueue.assert_not_called()
    assert client.get(module.ACTIVE_KEY) is None
