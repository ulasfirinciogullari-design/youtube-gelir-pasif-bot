from copy import deepcopy
import json

import pytest

from test_commissioning_audio import box, run, _wav, CHANNEL, NOW
from test_production_included_router import commissioned, client, CONTEXT
from test_production_prepaid_audio import prepare
from prepaid_audio_test_support import audio_policy
from app.services import commissioning_audio as setup, production_continuation as continuation
from app.services import production_prepaid_audio as prepaid, production_included_router as included
from app.services.production_spend import SpendBlocked, LEDGER_KEY


def authorize(client, channel, now):
    continuation.initialize(client, {'version': 1, 'kind': 'continuous_commissioning',
        'allowed_channels': [channel], 'authorized_at': now.isoformat(), 'owner_evidence_sha256': 'f' * 64})


def test_active_owner_setup_lifts_old_trial_day_and_total_without_erasing_history(box):
    box.policy.update(max_requests=1, max_per_day=1)
    setup.commission(box.client, box.policy);run(box)
    original_policy = box.client.get(setup.POLICY_KEY)
    original = json.loads(box.client.get(setup.JOURNAL_KEY))['requests']
    authorize(box.client, CHANNEL, NOW)
    box.path.write_bytes(_wav(amplitude=1));run(box)
    rows = json.loads(box.client.get(setup.JOURNAL_KEY))['requests']
    assert len(rows) == 2 and all(rows[key] == value for key, value in original.items())
    assert box.client.get(setup.POLICY_KEY) == original_policy
    assert setup.status(box.client)['reserved_list_cost_micro_usd'] == 12000
    assert setup.status(box.client)['continuous_commissioning'] is True
    assert setup.status(box.client)['historical_cash_micro'] is None
    box.client.delete(continuation.ACTIVE_KEY)
    assert setup.status(box.client)['requests'] == 2
    box.path.write_bytes(_wav(amplitude=2))
    with pytest.raises(SpendBlocked, match='setup_limit'):run(box)
    assert box.sender.call_count == 2


def test_setup_permission_does_not_remove_episode_attempt_limit(box):
    box.policy.update(max_per_lineage=1)
    setup.commission(box.client, box.policy)
    authorize(box.client, CHANNEL, NOW);run(box)
    box.path.write_bytes(_wav(amplitude=1))
    with pytest.raises(SpendBlocked, match='episode_limit'):run(box)
    assert box.sender.call_count == 1


def test_removed_setup_authority_racing_reservation_cannot_start_a_send(box, monkeypatch):
    box.policy.update(max_requests=1, max_per_day=1)
    setup.commission(box.client, box.policy);run(box)
    authorize(box.client, CHANNEL, NOW)
    actual = continuation.authority
    removed = []
    def racing(reader, channel, **options):
        result = actual(reader, channel, **options)
        if options.get('active', True) and not removed:
            box.client.delete(continuation.ACTIVE_KEY);removed.append(True)
        return result
    monkeypatch.setattr(continuation, 'authority', racing)
    box.path.write_bytes(_wav(amplitude=1))
    with pytest.raises(SpendBlocked, match='setup_limit'):run(box)
    assert removed == [True] and box.sender.call_count == 1


def test_added_reservation_cannot_outlive_or_forge_its_authorization(box):
    box.policy.update(max_requests=1, max_per_day=1)
    setup.commission(box.client, box.policy);run(box)
    authorize(box.client, CHANNEL, NOW)
    box.path.write_bytes(_wav(amplitude=1));run(box)
    record = json.loads(box.client.get(setup.JOURNAL_KEY))
    row = next(r for r in record['requests'].values() if 'continuation_authority_sha256' in r)
    row['continuation_authority_sha256'] = '0' * 64
    box.client.set(setup.JOURNAL_KEY, setup._raw(record))
    with pytest.raises(SpendBlocked, match='continuation_unverified'):setup.status(box.client)


def test_prepaid_audio_uses_own_period_capacity_and_credits_without_inheriting_free_router(commissioned):
    route, policy, _ = commissioned
    ledger = prepaid.PrepaidAudioLedger(route.foundation)
    value = audio_policy(policy, ledger.clock(), max_requests_per_day=1, max_requests_total=3)
    ledger.initialize(value)
    first, _ = ledger.reserve(CONTEXT, 'blind_asr', prepare(440))
    old_policy = ledger.client.get(prepaid.STATE_KEY)
    old_row = json.loads(ledger.client.get(prepaid.JOURNAL_KEY))['requests'][first]
    with pytest.raises(SpendBlocked, match='daily_limit'):ledger.reserve(CONTEXT, 'blind_asr', prepare(441))
    authorize(ledger.client, CONTEXT['channel_id'], ledger.clock())
    ledger.reserve(CONTEXT, 'blind_asr', prepare(441))
    rows = json.loads(ledger.client.get(prepaid.JOURNAL_KEY))['requests']
    assert rows[first] == old_row and rows[first]['outcome'] is None
    assert ledger.client.get(prepaid.STATE_KEY) == old_policy
    assert json.loads(ledger.client.get(included.JOURNAL_KEY))['requests'] == {}
    assert ledger.foundation.snapshot()['cash_spending_enabled'] is False
    ledger.client.delete(continuation.ACTIVE_KEY)
    with ledger.client.pipeline() as p:
        state, read = ledger._read(p)
        assert len(read['requests']) == 2 and state['policy']['automatic_purchase_enabled'] is False
    with pytest.raises(SpendBlocked, match='daily_limit'):ledger.reserve(CONTEXT, 'blind_asr', prepare(442))
