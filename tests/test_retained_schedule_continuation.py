"""Real retained media/delivery evidence and ledger; synthetic provider transports."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import retained_schedule_continuation as continuation
from app.services import channel_production
from app.services import production_reconciliation as reconciliation
from app.services.production_spend import LEDGER_KEY, SpendLedger
from test_retained_delivery_completion import (
    executing, admitted, corrected_visual, eligible, rejected, qualified, captured,
    source, case, planning_case, real_media, prepared, frozen_three, completed_probe,
    wire, forbid_live_transport, prepare, synthetic_youtube, completion, transport,
    publication, admission, _dump, restore, Intercept,
)
from test_production_dispatch_budget import setup as funded


def test_unrelated_channel_and_original_never_read_a_store(monkeypatch):
    factory = Mock(side_effect=AssertionError('Unrelated channel must not read funding'))
    monkeypatch.setattr(continuation.spending, 'configured_ledger', factory)
    for profile, original in (({}, continuation.continuity.ROOT_ID),
            ({'channel_id': continuation.continuity.CHANNEL_ID}, 'unrelated-task')):
        assert continuation.resume_retained_schedule(profile, original) == {'status': 'not_applicable'}
    factory.assert_not_called()


def test_real_public_retained_delivery_resumes_only_with_current_funding(executing, monkeypatch, subtests):
    box = executing
    c = continuation.continuity
    profile = json.loads(box.client.get(c._PROFILE + c.CHANNEL_ID))
    value = prepare(box)
    calls = synthetic_youtube(box, monkeypatch)
    money = funded(client=box.client, initialize=False)
    factory = Mock(return_value=money.ledger)
    settings = SimpleNamespace(studio_spend_enforcement=True, studio_longform_delivery_enabled=False)
    original_settings = continuation.spending.settings
    original_factory = continuation.spending.configured_ledger
    monkeypatch.setattr(continuation.spending, 'settings', settings)
    monkeypatch.setattr(continuation.spending, 'configured_ledger', factory)
    now = money.clock[0].timestamp()

    def resume():
        return continuation.resume_retained_schedule(profile, c.ROOT_ID, now=now)

    before = _dump(box.client)
    assert resume() == {'status': 'waiting_for_retained_publication'}
    assert _dump(box.client) == before and calls == []
    monkeypatch.setattr(continuation.spending, 'settings', original_settings)
    monkeypatch.setattr(continuation.spending, 'configured_ledger', original_factory)
    transport.publish_retained_final(value)
    completion.complete_retained_publication(value)
    monkeypatch.setattr(continuation.spending, 'settings', settings)
    monkeypatch.setattr(continuation.spending, 'configured_ledger', factory)
    before = _dump(box.client)
    assert resume() == {'status': 'budget_blocked', 'reason_code': 'spend_not_initialized'}
    assert _dump(box.client) == before
    monkeypatch.setattr(reconciliation, '_redis', lambda: box.client)
    assert reconciliation.reconcile_public_retry_deliveries([profile], now=now) == {
        'status': 'idle', 'resumed_count': 0, 'channels': {c.CHANNEL_ID: 'budget_blocked'}}
    assert _dump(box.client) == before
    # The retained fixture has an unrelated historical ledger sentinel. An
    # explicit synthetic commissioning adds policy/period fields without
    # deleting that history; the application never initializes a missing one.
    commissioned = funded(funding=False)
    box.client.hset(LEDGER_KEY, mapping=commissioned.client.hgetall(LEDGER_KEY))
    assert money.ledger.initialize() is False
    assert resume() == {'status': 'budget_blocked', 'reason_code': 'spend_funding_not_initialized'}
    money.ledger.initialize_funding(money.funding)
    before = _dump(box.client)
    state_key = c._STATE + c.CHANNEL_ID
    plan = json.loads(box.client.get(publication.PLAN_KEY))
    assert box.client.hget(state_key, 'connection_id') != plan['connection_id']
    damage = [
        ('completion_missing', lambda: box.client.delete(completion.COMPLETION_KEY)),
        ('global_active', lambda: box.client.set(c._ACTIVE, 'existing-work')),
        ('profile_changed', lambda: box.client.set(c._PROFILE + c.CHANNEL_ID, '{}')),
        ('oauth_epoch_changed', lambda: box.client.set(c._AUTH_EPOCH, '999')),
        ('membership_removed', lambda: box.client.srem(c._CHANNEL_INDEX, c.CHANNEL_ID)),
        ('credential_missing', lambda: box.client.delete(c._CREDENTIAL + c.CHANNEL_ID)),
        ('counter_advanced', lambda: box.client.set(plan['series_keys'][0], '6')),
        ('counter_expiring', lambda: box.client.expire(plan['series_keys'][0], 600)),
        ('cursor_advanced', lambda: box.client.hset(state_key, 'cursor', '6')),
        ('owner_pause', lambda: box.client.hset(state_key, 'paused_reason', 'owner_disabled')),
        ('lineage_changed', lambda: box.client.set(c._JOB + c.ROOT_ID, '{}')),
        ('old_paid_cap_changed', lambda: box.client.hset(c._PAID_CAP + c.LEAF_ID, 'used', '0')),
        ('funding_expiring', lambda: box.client.expire(LEDGER_KEY, 600)),
    ]
    for task in (*c.LINEAGE, box.child):
        for prefix in c._ABSENT_PREFIXES[:2]:
            key = prefix + task
            damage.append((key, lambda key=key: box.client.set(key, 'owner-hold')))
    for name, change in damage:
        with subtests.test(blocked=name):
            change()
            changed = _dump(box.client)
            assert resume()['status'] not in ('resumed', 'already_resumed')
            assert _dump(box.client) == changed
            restore(box.client, before)
    with subtests.test(enforcement_disabled=True):
        settings.studio_spend_enforcement = False
        assert resume() == {'status': 'budget_blocked', 'reason_code': 'spend_enforcement_required'}
        assert _dump(box.client) == before
        settings.studio_spend_enforcement = True

    def race(commands):
        if commands == ('SET', 'HDEL', 'HSET'):
            box.client.hset(LEDGER_KEY, 'concurrent_reservation', 'preserved')
    racing = SpendLedger(Intercept(box.client, before=race), money.ledger.policy, clock=money.ledger.clock)
    with subtests.test(funding_changed_before_commit=True):
        factory.return_value = racing
        assert resume()['status'] == 'retained_continuation_unverified'
        assert box.client.hget(state_key, 'paused_reason') == 'previous_render_failed'
        assert not box.client.exists(continuation.CONTINUATION_KEY)
        assert box.client.hget(LEDGER_KEY, 'concurrent_reservation') == 'preserved'
        restore(box.client, before)
    def lost(commands, reply):
        if commands == ('SET', 'HDEL', 'HSET'):
            raise RuntimeError('PRIVATE lost schedule acknowledgement')
        return reply
    factory.return_value = SpendLedger(Intercept(box.client, after=lost), money.ledger.policy, clock=money.ledger.clock)
    assert resume() == {'status': 'retained_continuation_unverified'}
    factory.return_value = money.ledger
    resumed = _dump(box.client)
    result = resume()
    assert result['status'] == 'already_resumed' and result['cursor'] == 5
    assert result['video_id'] == 'Synthetic01' and result['next_due'] == now
    assert result['production_dispatched'] is False
    assert _dump(box.client) == resumed
    assert set(resumed) - set(before) == {continuation.CONTINUATION_KEY}
    assert all(resumed[key] == raw for key, raw in before.items() if key != state_key)
    assert box.client.hget(state_key, 'connection_id') == plan['connection_id']
    assert box.client.hget(state_key, 'last_result') == 'FAILURE'
    assert box.client.hget(state_key, 'last_task_id') == c.ROOT_ID
    assert box.client.pttl(continuation.CONTINUATION_KEY) == -1

    # The ordinary scheduler consumes topic six once using the newly verified
    # connection. It never resubmits the failed episode or spends in this test.
    monkeypatch.setattr(channel_production, '_redis', lambda: box.client)
    monkeypatch.setattr(channel_production, 'settings', settings)
    current = json.loads(box.client.get(c._CHANNEL + c.CHANNEL_ID))
    dispatch = channel_production.reserve_due_production(profile, current, now=now)
    assert dispatch['status'] == 'reserved'
    job = json.loads(box.client.get(c._JOB + dispatch['task_id']))
    assert job['spec']['production_topic_index'] == 5
    assert job['spec']['production_connection_id'] == plan['connection_id']
    assert box.client.hget(state_key, 'cursor') == '6'
    advanced = _dump(box.client)
    assert resume() == result
    assert _dump(box.client) == advanced
    assert box.client.dump(LEDGER_KEY) == before[LEDGER_KEY]
    assert calls == ['upload', 'private_status', 'captions', 'release', 'public_status']
    assert len(box.wire.calls) == box.sends and box.s3.objects == box.objects
    assert all(call.kwargs == {'read_timeout': 2} for call in factory.call_args_list)
    box.no_new_work.assert_not_called()
