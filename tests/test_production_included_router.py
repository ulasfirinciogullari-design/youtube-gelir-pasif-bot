from copy import deepcopy
from datetime import timedelta
import json

import pytest
from redis.exceptions import ConnectionError

from app.services import production_included_router as included
from app.services import production_spend_runtime as runtime
from app.services.abacus_router_adapter import observe_router_response
from app.services.abacus_router_schema_compat import prepare_json_object_router_request
from app.services.production_spend import LEDGER_KEY, SpendBlocked
from test_production_cash_disabled import money, dump
from test_production_credit_ledger import client, NOW, InterceptClient
from test_abacus_router_adapter import KEY, SCHEMA, RESULT, response

CHANNEL = 'UC' + 'a' * 22
CONTEXT = {'channel_id': CHANNEL, 'connection_id': 'actual-connection',
           'lineage_id': '11111111-1111-4111-8111-111111111111', 'kind': 'shorts'}


def request(text='First complete prompt'):
    return prepare_json_object_router_request([{'type': 'text', 'text': text}],
        api_key=KEY, system_instruction='Return the complete authored JSON.', json_schema=SCHEMA)


@pytest.fixture
def commissioned(client, monkeypatch):
    from app import config
    # Legacy tests replace app.config during collection. Use one shared
    # fixture configuration for transport, encryption and opt-in routing.
    monkeypatch.setattr(runtime, 'settings', config.settings)
    for name, value in {'studio_spend_enforcement': False, 'studio_abacus_included_production': False,
                        'abacus_api_key': ''}.items():
        monkeypatch.setattr(config.settings, name, value, raising=False)
    foundation = money(client)
    prepared = request()
    policy = {'version': 1, 'kind': 'existing_subscription_included_router',
        'endpoint': prepared.endpoint, 'model': prepared.model,
        'credential_sha256': prepared.credential_sha256, 'owner_evidence_sha256': 'b' * 64,
        'terms_evidence_sha256': 'c' * 64,
        'valid_from': included._stamp(NOW), 'valid_until': included._stamp(NOW + timedelta(days=1)),
        'automatic_purchase_enabled': False, 'new_cash_allowance_micro': 0, 'historical_cash_micro': None,
        'allowed_channels': [CHANNEL], 'max_requests_per_lineage': 3, 'max_requests_per_day': 4}
    ledger = included.IncludedRouterLedger(foundation)
    ledger.initialize(policy)
    client.hset(LEDGER_KEY, 'binding:' + CONTEXT['lineage_id'], included._raw(CONTEXT))
    client.set(runtime._CHANNEL_PREFIX + CHANNEL, json.dumps({'id': CHANNEL,
               'connection_id': CONTEXT['connection_id'], 'requires_reconnect': False}))
    client.sadd(runtime._CHANNEL_INDEX, CHANNEL)
    monkeypatch.setattr(runtime.settings, 'app_encryption_key', 'private-fixture-encryption-material-' * 2, raising=False)
    return ledger, policy, prepared


def test_completed_result_reused_without_new_reservation_and_cash_stays_unknown(commissioned):
    ledger, policy, prepared = commissioned
    identity, prior = ledger.reserve(CONTEXT, 'research', prepared)
    assert prior is None
    observed = observe_router_response(prepared, response(prepared))
    outcome = ledger.settle(identity, prepared, observed)
    assert included._result(prepared, outcome) == RESULT
    assert RESULT['reason'] not in ledger.client.get(included.JOURNAL_KEY)
    before = dump(ledger.client)
    same, cached = ledger.reserve(CONTEXT, 'research', prepared)
    assert same == identity and included._result(prepared, cached) == RESULT
    assert dump(ledger.client) == before
    assert ledger.foundation.snapshot()['historical_cash_micro'] is None
    assert ledger.foundation.snapshot()['cash_spending_enabled'] is False


def test_unobserved_reservation_cannot_resend_or_reopen(commissioned):
    ledger, policy, prepared = commissioned
    ledger.reserve(CONTEXT, 'research', prepared)
    before = dump(ledger.client)
    with pytest.raises(SpendBlocked, match='previous_outcome_unknown'):
        ledger.reserve(CONTEXT, 'research', prepared)
    with pytest.raises(SpendBlocked, match='already_commissioned'):
        ledger.initialize(policy)
    assert dump(ledger.client) == before


@pytest.mark.parametrize('key', [included.STATE_KEY, included.JOURNAL_KEY, included.ANCHOR_KEY])
def test_partial_ledger_loss_is_not_a_new_allowance(commissioned, key):
    ledger, policy, prepared = commissioned
    ledger.reserve(CONTEXT, 'research', prepared)
    ledger.client.delete(key)
    before = dump(ledger.client)
    with pytest.raises(SpendBlocked): ledger.reserve(CONTEXT, 'editorial', request('Changed prompt'))
    with pytest.raises(SpendBlocked): ledger.initialize(policy)
    assert dump(ledger.client) == before


def test_even_all_router_keys_lost_cannot_reinitialize(commissioned):
    ledger, policy, _ = commissioned
    ledger.client.delete(included.STATE_KEY, included.JOURNAL_KEY, included.ANCHOR_KEY)
    with pytest.raises(SpendBlocked, match='already_commissioned'): ledger.initialize(policy)


def test_lost_reserve_ack_never_sends_or_refunds(commissioned):
    ledger, _, prepared = commissioned
    def lose(number, result): raise ConnectionError('Lost ACK')
    ledger.client = InterceptClient(ledger.client, after=lose)
    with pytest.raises(SpendBlocked, match='reservation_uncertain'):
        ledger.reserve(CONTEXT, 'research', prepared)
    ledger.client = ledger.foundation.client
    with pytest.raises(SpendBlocked, match='previous_outcome_unknown'):
        ledger.reserve(CONTEXT, 'research', prepared)


def test_lost_settlement_ack_can_reuse_only_the_committed_output(commissioned):
    ledger, _, prepared = commissioned
    identity, _ = ledger.reserve(CONTEXT, 'research', prepared)
    def lose(number, result): raise ConnectionError('Lost ACK')
    ledger.client = InterceptClient(ledger.client, after=lose)
    with pytest.raises(SpendBlocked, match='settlement_uncertain'):
        ledger.settle(identity, prepared, observe_router_response(prepared, response(prepared)))
    ledger.client = ledger.foundation.client
    _, outcome = ledger.reserve(CONTEXT, 'research', prepared)
    assert included._result(prepared, outcome) == RESULT


def test_lineage_and_daily_request_limits_include_unknowns(commissioned):
    ledger, _, _ = commissioned
    for index in range(3): ledger.reserve(CONTEXT, 'research', request(str(index)))
    with pytest.raises(SpendBlocked, match='episode_limit'):
        ledger.reserve(CONTEXT, 'research', request('Fourth'))
    second = {**CONTEXT, 'lineage_id': '22222222-2222-4222-8222-222222222222'}
    ledger.client.hset(LEDGER_KEY, 'binding:' + second['lineage_id'], included._raw(second))
    ledger.reserve(second, 'research', request('First second episode'))
    with pytest.raises(SpendBlocked, match='daily_limit'):
        ledger.reserve(second, 'research', request('Second second episode'))


def test_expired_or_changed_account_and_cash_permission_block(commissioned):
    ledger, policy, prepared = commissioned
    bad = {**CONTEXT, 'connection_id': 'different-connection'}
    with pytest.raises(SpendBlocked): ledger.reserve(bad, 'research', prepared)
    ledger.foundation.clock = ledger.clock = lambda: NOW + timedelta(days=2)
    before = dump(ledger.client)
    with pytest.raises(SpendBlocked, match='entitlement_expired'):
        ledger.reserve(CONTEXT, 'research', prepared)
    assert dump(ledger.client) == before
    for field, value in [('new_cash_allowance_micro', 1), ('historical_cash_micro', 0),
                         ('automatic_purchase_enabled', True), ('model', 'premium-fixed-model')]:
        with pytest.raises(SpendBlocked): included.validate_policy({**policy, field: value}, NOW)


def test_reconnected_channel_cannot_reuse_an_old_context(commissioned):
    ledger, _, prepared = commissioned
    ledger.client.set(runtime._CHANNEL_PREFIX + CHANNEL, json.dumps({'id': CHANNEL,
        'connection_id': 'new-google-connection', 'requires_reconnect': False}))
    before = dump(ledger.client)
    with pytest.raises(SpendBlocked, match='channel_changed'):
        ledger.reserve(CONTEXT, 'research', prepared)
    assert dump(ledger.client) == before


def test_runtime_one_send_then_cached_result_with_existing_real_context(commissioned, monkeypatch):
    ledger, _, _ = commissioned
    from app.services import production_included_transport as transport
    monkeypatch.setattr(runtime.settings, 'studio_spend_enforcement', True)
    monkeypatch.setattr(runtime.settings, 'studio_abacus_included_production', True)
    monkeypatch.setattr(runtime.settings, 'abacus_api_key', KEY)
    monkeypatch.setattr(runtime, 'configured_ledger', lambda: ledger.foundation)
    monkeypatch.setattr(runtime, 'resolve_context', lambda client, task: deepcopy(CONTEXT))
    calls = []
    def send(prepared):
        calls.append(prepared)
        return response(prepared)
    monkeypatch.setattr(transport, 'send_once', send)
    kwargs = {'purpose': 'research', 'system_instruction': 'Return the complete authored JSON.', 'json_schema': SCHEMA}
    for _ in range(2):
        assert included.generate_included_json([{'type': 'text', 'text': 'First complete prompt'}], **kwargs) == RESULT
    assert len(calls) == 1


def test_runtime_timeout_is_terminal_for_same_request(commissioned, monkeypatch):
    ledger, _, _ = commissioned
    from app.services import production_included_transport as transport
    monkeypatch.setattr(runtime.settings, 'studio_spend_enforcement', True)
    monkeypatch.setattr(runtime.settings, 'studio_abacus_included_production', True)
    monkeypatch.setattr(runtime.settings, 'abacus_api_key', KEY)
    monkeypatch.setattr(runtime, 'configured_ledger', lambda: ledger.foundation)
    monkeypatch.setattr(runtime, 'resolve_context', lambda client, task: deepcopy(CONTEXT))
    calls = []
    def send(prepared):
        calls.append(prepared)
        raise RuntimeError('Unknown provider outcome')
    monkeypatch.setattr(transport, 'send_once', send)
    kwargs = {'purpose': 'research', 'system_instruction': 'Full schema', 'json_schema': SCHEMA}
    with pytest.raises(SpendBlocked, match='response_unverified'):
        included.generate_included_json([{'type': 'text', 'text': 'Prompt'}], **kwargs)
    with pytest.raises(SpendBlocked, match='previous_outcome_unknown'):
        included.generate_included_json([{'type': 'text', 'text': 'Prompt'}], **kwargs)
    assert len(calls) == 1
