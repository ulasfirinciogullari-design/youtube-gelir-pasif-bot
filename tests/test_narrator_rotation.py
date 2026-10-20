from copy import deepcopy
from datetime import timedelta
import json

import pytest

from app.services import narrator_rotation as rotation, production_credit_funding as credit
from app.services.production_credit_ledger import CreditLedger, STATE_KEY, JOURNAL_KEY
from app.services.production_spend import SpendBlocked, LEDGER_KEY
from test_production_cash_disabled import money
from test_production_credit_ledger import client, policy, NOW, binding, context, intent, observation
from test_elevenlabs_credit_adapter import kwargs, voice_body, response, observe


def catalog():
    return {'has_more': False, 'voices': [{'voice_id': voice, 'is_legacy': False,
        'high_quality_base_model_ids': [credit.MODEL, credit.TURKISH_SHORT_MODEL]} for voice in rotation.VOICE_IDS]}


def initialized(client, policy):
    foundation = money(client)
    ledger = CreditLedger(client, foundation=foundation, clock=lambda: NOW)
    ledger.initialize(policy)
    return ledger


def test_voice_extension_preserves_policy_spent_and_every_old_receipt(client, policy):
    ledger = initialized(client, policy)
    receipt = ledger.reserve(intent=intent(), production_context=context(client), **binding(policy))
    ledger.settle(observation=observation(policy, receipt), **binding(policy))
    old = (client.hgetall(STATE_KEY), client.hgetall(JOURNAL_KEY))
    assert rotation.commission(ledger, catalog(), observed_at=NOW)['status'] == 'active'
    assert (client.hgetall(STATE_KEY), client.hgetall(JOURNAL_KEY)) == old
    voice = rotation.POOLS['en'][0][0]
    request = intent(2, voice_id=voice, route=rotation.route(voice), voice_pool_sha256=rotation.POOL_SHA256)
    receipt = ledger.reserve(intent=request, production_context=context(client), **binding(policy))
    assert receipt['reserved_credits'] == 752  # consumed credits were not reset
    assert ledger.summary()['spent_credits'] == 248
    with pytest.raises(SpendBlocked):
        ledger.reserve(intent=request, production_context=context(client), **binding(policy))
    assert all(client.hget(JOURNAL_KEY, k) == v for k, v in old[1].items() if k not in {'revision', 'state_sha256'})


def test_pool_intent_without_account_grant_cannot_send(client, policy):
    ledger = initialized(client, policy)
    voice = rotation.POOLS['tr'][0][0]
    request = intent(voice_id=voice, route=rotation.route(voice), voice_pool_sha256=rotation.POOL_SHA256)
    with pytest.raises(SpendBlocked, match='grant_missing'):
        ledger.reserve(intent=request, production_context=context(client), **binding(policy))
    assert ledger.summary()['spent_credits'] == ledger.summary()['reserved_credits'] == 0


@pytest.mark.parametrize('damage', ['missing_voice', 'legacy', 'wrong_models', 'stale', 'pagination'])
def test_unverified_catalog_cannot_activate_rotation(client, policy, damage):
    ledger = initialized(client, policy); data = catalog(); stamp = NOW
    if damage == 'missing_voice': data['voices'].pop()
    if damage == 'legacy': data['voices'][0]['is_legacy'] = True
    if damage == 'wrong_models': data['voices'][0]['high_quality_base_model_ids'] = []
    if damage == 'pagination': data['has_more'] = True
    if damage == 'stale': stamp -= timedelta(seconds=121)
    with pytest.raises(SpendBlocked): rotation.commission(ledger, data, observed_at=stamp)
    assert not client.exists(rotation.GRANT_KEY)


def test_actual_wire_and_meter_are_bound_to_chosen_voice(kwargs):
    from app.services.elevenlabs_credit_adapter import inspect_credit_request
    voice = rotation.POOLS['en'][1][0]
    prepared = inspect_credit_request(rotation.route(voice), kwargs)
    assert prepared.voice_id == voice and prepared.route.endswith(voice + '/with-timestamps')
    assert observe(prepared, response(prepared, cost='23'))['actual_credit_cost'] == 23
    other = inspect_credit_request(credit.ROUTE, kwargs)
    with pytest.raises(SpendBlocked): observe(prepared, response(other))


def test_rotation_is_per_channel_and_root_retry_is_stable(client, policy, monkeypatch):
    from app.services import production_spend_runtime as runtime
    ledger = initialized(client, policy)
    rotation.commission(ledger, catalog(), observed_at=NOW)
    channel = 'UC5v9AvNtD3PTLgo6m1jROOA'
    root = {'channel_id': channel, 'connection_id': 'connection001', 'lineage_id': 'root00001', 'kind': 'shorts'}
    monkeypatch.setattr(runtime, 'enforcement_enabled', lambda: True)
    monkeypatch.setattr(runtime, 'configured_ledger', lambda **k: ledger.foundation)
    monkeypatch.setattr(runtime, 'resolve_context', lambda *a: dict(root))
    ledger.foundation.clock = lambda: NOW
    token = runtime._TASK_ID.set('test-task')
    try:
        first = rotation.assigned('tr'); assert rotation.assigned('tr') == first
        root['lineage_id'] = 'root00002'
        second = rotation.assigned('tr')
        assert first['voice_id'] != second['voice_id']
        root['lineage_id'] = 'root00001'
        assert rotation.assigned('tr') == first
        assert client.get(rotation.PREFIX + 'cursor:' + channel + ':tr') == '2'
        assert ledger.summary()['reserved_credits'] == ledger.summary()['spent_credits'] == 0
    finally:
        runtime._TASK_ID.reset(token)
