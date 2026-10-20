from copy import deepcopy
import json

import pytest

from app.services import production_included_router as included, production_spend_runtime as runtime
from app.services.abacus_router_adapter import observe_router_response
from app.services.abacus_router_schema_compat import prepare_json_object_router_request
from app.services.production_spend import LEDGER_KEY, SpendBlocked
from test_production_included_router import commissioned, CONTEXT
from test_production_credit_ledger import client
from test_production_cash_disabled import dump
from test_abacus_router_adapter import KEY, response, envelope


def request(text='Same sources and prior topics'):
    schema = {'type': 'object', 'properties': {'can_prepare': {'type': 'boolean'},
        'language': {'type': 'string', 'enum': ['en']}, 'series_title': {'type': 'string'},
        'briefs': {'type': 'array', 'items': {'type': 'string'}}},
        'required': ['can_prepare', 'language', 'series_title', 'briefs'], 'additionalProperties': False}
    return prepare_json_object_router_request([{'type': 'text', 'text': text}],
        api_key=KEY, system_instruction='Prepare a new source-backed series.', json_schema=schema)


def recorded(ledger, prepared, *, purpose='next_series', positive=False):
    identity, _ = ledger.reserve(CONTEXT, purpose, prepared)
    payload = envelope();payload['choices'][0]['message']['content'] = json.dumps({
        'can_prepare': positive, 'language': 'en', 'series_title': 'New' if positive else '',
        'briefs': ['A source-backed idea'] if positive else []})
    ledger.settle(identity, prepared, observe_router_response(prepared, response(prepared, payload=payload)))
    return identity


def successor(ledger):
    context = {**CONTEXT, 'lineage_id': '22222222-2222-4222-8222-222222222222'}
    ledger.client.hset(LEDGER_KEY, 'binding:' + context['lineage_id'], included._raw(context))
    return context


def test_exact_observed_negative_reused_without_new_charge_or_journal_entry(commissioned):
    ledger, _, _ = commissioned;prepared = request()
    old = recorded(ledger, prepared);context = successor(ledger);before = dump(ledger.client)
    identity, outcome = ledger.reserve(context, 'next_series', prepared)
    assert identity == old and included._result(prepared, outcome)['can_prepare'] is False
    assert dump(ledger.client) == before


@pytest.mark.parametrize('change', ['positive', 'different_prompt', 'different_purpose', 'different_connection'])
def test_nonidentical_or_positive_result_never_supplies_a_cached_plan(commissioned, change):
    ledger, _, _ = commissioned;prepared = request()
    recorded(ledger, prepared, purpose='research' if change == 'different_purpose' else 'next_series',
             positive=change == 'positive')
    context = successor(ledger)
    if change == 'different_connection':
        context['connection_id'] = 'new-connection'
        ledger.client.hset(LEDGER_KEY, 'binding:' + context['lineage_id'], included._raw(context))
        ledger.client.set(runtime._CHANNEL_PREFIX + context['channel_id'], json.dumps({
            'id': context['channel_id'], 'connection_id': context['connection_id']}))
    _, outcome = ledger.reserve(context, 'next_series', request('Changed source text') if change == 'different_prompt' else prepared)
    assert outcome is None and len(json.loads(ledger.client.get(included.JOURNAL_KEY))['requests']) == 2


def test_unknown_current_identity_is_not_overridden_by_another_observed_refusal(commissioned):
    ledger, _, _ = commissioned;prepared = request();context = successor(ledger)
    ledger.reserve(context, 'next_series', prepared)
    recorded(ledger, prepared)
    before = dump(ledger.client)
    with pytest.raises(SpendBlocked, match='previous_outcome_unknown'):
        ledger.reserve(context, 'next_series', prepared)
    assert dump(ledger.client) == before


def test_corrupt_observed_refusal_stops_before_a_new_provider_reservation(commissioned):
    ledger, _, _ = commissioned;prepared = request();identity = recorded(ledger, prepared)
    context = successor(ledger)
    state = json.loads(ledger.client.get(included.STATE_KEY));journal = json.loads(ledger.client.get(included.JOURNAL_KEY))
    journal['requests'][identity]['outcome']['evidence']['parsed_result_sha256'] = '0' * 64
    ledger.client.set(included.JOURNAL_KEY, included._raw(journal))
    ledger.client.set(included.ANCHOR_KEY, included._sha({'state': state, 'journal': journal}))
    before = dump(ledger.client)
    with pytest.raises(SpendBlocked): ledger.reserve(context, 'next_series', prepared)
    assert dump(ledger.client) == before


def test_actual_runtime_does_not_send_identical_negative_planning_request_twice(commissioned, monkeypatch):
    from app.services import production_included_transport as transport
    from app.services.abacus_router_schema_compat import schema_for_body
    ledger, _, _ = commissioned;current = deepcopy(CONTEXT);calls = []
    monkeypatch.setattr(runtime.settings, 'studio_spend_enforcement', True)
    monkeypatch.setattr(runtime.settings, 'studio_abacus_included_production', True)
    monkeypatch.setattr(runtime.settings, 'abacus_api_key', KEY)
    monkeypatch.setattr(runtime, 'configured_ledger', lambda: ledger.foundation)
    monkeypatch.setattr(runtime, 'resolve_context', lambda *_: deepcopy(current))
    result = {'can_prepare': False, 'language': 'en', 'series_title': '', 'briefs': []}
    def send(prepared):
        calls.append(prepared)
        payload = envelope();payload['choices'][0]['message']['content'] = json.dumps(result)
        return response(prepared, payload=payload)
    monkeypatch.setattr(transport, 'send_once', send)
    schema = schema_for_body(request().payload)
    assert included.generate_text_json('Unchanged evidence.', schema, purpose='next_series') == result
    current.update(successor(ledger));before = dump(ledger.client)
    assert included.generate_text_json('Unchanged evidence.', schema, purpose='next_series') == result
    assert len(calls) == 1 and dump(ledger.client) == before
