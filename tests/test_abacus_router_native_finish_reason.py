"""Observed native stop metadata is optional, exact and never a QA grant."""
from copy import deepcopy
import hashlib
import json

import httpx
import pytest

from app.services import abacus_router_adapter as adapter
import test_abacus_router_adapter as fixture


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('Only the explicit in-memory HTTPX transport is permitted')
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', blocked)


def exchange(prepared, payload, *, status=200, raw=None, changed_wire=False):
    """Keep the actual request/response identities from one local transport."""
    requests = []
    def handler(request):
        requests.append(request)
        assert request.method == 'POST' and str(request.url) == prepared.endpoint
        return httpx.Response(status, content=raw if raw is not None else json.dumps(payload).encode(),
                              headers={'Content-Type': 'application/json'})
    kwargs = prepared.wire_kwargs()
    if changed_wire:
        kwargs['headers']['authorization'] = 'Bearer different-offline-key'
    with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False,
                      follow_redirects=False) as client:
        response = client.post(prepared.endpoint, **kwargs)
    assert len(requests) == 1 and response.request is requests[0]
    return response


def assert_metadata_only(before, after, response):
    assert before.result == after.result
    prior, current = before.evidence, after.evidence
    assert set(prior) == set(current)
    assert current['response_body_sha256'] == hashlib.sha256(response.content).hexdigest()
    assert prior['response_body_sha256'] != current['response_body_sha256']
    proof = current.pop('response_proof_sha256')
    assert proof == hashlib.sha256(adapter._canonical(current)).hexdigest()
    prior.pop('response_proof_sha256')
    prior.pop('response_body_sha256')
    current.pop('response_body_sha256')
    assert adapter._canonical(prior) == adapter._canonical(current)
    assert not {'native_finish_reason', 'qa_approved', 'publish_eligible'} & set(current)


def test_absent_native_field_preserves_frozen_5e_result_and_evidence_bytes():
    # Recorded from the actual committed5e observer, using only this public
    # synthetic fixture. No production response or private evidence is copied.
    prepared = fixture.prepare()
    observed = adapter.observe_router_response(prepared, fixture.response(prepared))
    assert hashlib.sha256(adapter._canonical(observed.result)).hexdigest() == (
        '9fbb95d9e4896a349ea20c863a33f968a02f237f3d9d769fbf08b4498357d1bb')
    assert hashlib.sha256(adapter._canonical(observed.evidence)).hexdigest() == (
        '8e073f4f286173c43d0d3e5163db22717c6a91fdec158b926189636fc2ff76b7')


@pytest.mark.parametrize('omit_identity', [False, True])
@pytest.mark.parametrize('native_reason', ['STOP', 'stop'])
def test_native_stop_preserves_exact_result_and_all_other_evidence(omit_identity, native_reason):
    prepared, payload = fixture.prepare(), fixture.envelope()
    if omit_identity:
        payload.pop('id'); payload.pop('object')
    before = adapter.observe_router_response(prepared, exchange(prepared, payload))
    payload['choices'][0]['native_finish_reason'] = native_reason
    response = exchange(prepared, payload)
    after = adapter.observe_router_response(prepared, response)
    assert_metadata_only(before, after, response)
    assert after.result == fixture.RESULT and after.result['accepted'] is False
    payload['choices'][0].pop('native_finish_reason')
    unchanged = adapter.observe_router_response(prepared, exchange(prepared, payload))
    assert unchanged == before


@pytest.mark.parametrize('value', [None, True, False, 0, 1, 1.0, [], {},
    'Stop', ' STOP', 'STOP ', 'STOP\n', 'END_TURN', 'length', 'tool_calls', fixture.KEY])
def test_present_native_reason_requires_an_exact_observed_string(value):
    prepared, payload = fixture.prepare(), fixture.envelope()
    payload['choices'][0]['native_finish_reason'] = value
    with pytest.raises(adapter.AbacusRouterError, match='^abacus_router_response_unverified$'):
        adapter.observe_router_response(prepared, exchange(prepared, payload))


def damage_payload(payload, damage):
    choice = payload['choices'][0]
    if damage == 'length': choice['finish_reason'] = 'length'
    elif damage == 'canonical_uppercase': choice['finish_reason'] = 'STOP'
    elif damage == 'missing_finish': choice.pop('finish_reason')
    elif damage == 'boolean_index': choice['index'] = False
    elif damage == 'missing_index': choice.pop('index')
    elif damage == 'second_choice': payload['choices'].append(deepcopy(choice))
    elif damage == 'logprobs': choice['logprobs'] = {}
    elif damage == 'unknown_choice': choice['unexpected_metadata'] = None
    elif damage == 'alias': choice['nativeFinishReason'] = choice.pop('native_finish_reason')
    elif damage == 'tool_calls': choice['message']['tool_calls'] = [{'id': 'fixture-tool'}]
    elif damage == 'refusal': choice['message']['refusal'] = 'Fixture refusal'
    elif damage == 'schema': choice['message']['content'] = '{}'
    elif damage not in {'wire', 'non2xx', 'duplicate_native'}: raise AssertionError(damage)


DAMAGES = ['length', 'canonical_uppercase', 'missing_finish', 'boolean_index', 'missing_index',
    'second_choice', 'logprobs', 'unknown_choice', 'alias', 'tool_calls', 'refusal', 'schema',
    'wire', 'non2xx', 'duplicate_native']


@pytest.mark.parametrize('damage', DAMAGES)
@pytest.mark.parametrize('native_reason', ['STOP', 'stop'])
def test_native_stop_does_not_waive_any_existing_envelope_or_request_guard(damage, native_reason):
    prepared, payload = fixture.prepare(), fixture.envelope()
    payload['choices'][0]['native_finish_reason'] = native_reason
    damage_payload(payload, damage)
    raw = (json.dumps(payload).replace(f'"native_finish_reason": "{native_reason}"',
        f'"native_finish_reason": "{native_reason}", "native_finish_reason": "{native_reason}"').encode()
        if damage == 'duplicate_native' else None)
    response = exchange(prepared, payload, status=403 if damage == 'non2xx' else 200,
                        raw=raw, changed_wire=damage == 'wire')
    with pytest.raises(adapter.AbacusRouterError):
        adapter.observe_router_response(prepared, response)


@pytest.mark.parametrize('native', [False, True])
@pytest.mark.parametrize('native_usage', [False, True])
def test_pure_payload_parser_has_no_transport_or_observation_authority(monkeypatch, native, native_usage):
    payload = fixture.envelope()
    if native:
        payload['choices'][0]['native_finish_reason'] = 'STOP'
    if native_usage:
        payload['usage'] = {'input_tokens': 9179, 'output_tokens': 1683, 'raw_input_tokens': 9179}
    raw = json.dumps(payload).encode()
    def blocked(*args, **kwargs):
        raise AssertionError('Pure parsing cannot observe transport or issue an observation')
    monkeypatch.setattr(adapter, 'ObservedRouterResult', blocked)
    monkeypatch.setattr(adapter, '_verify_request', blocked)
    monkeypatch.setattr(adapter, 'inspect_router_request', blocked)
    parsed = adapter._parse_response_payload(raw, schema=fixture.SCHEMA, max_tokens=8192)
    assert set(parsed) == {'payload', 'result', 'usage'}
    assert parsed['payload'] == payload and parsed['result'] == fixture.RESULT
    assert parsed['usage'] == payload['usage']
    parsed['result']['reason'] = 'Detached caller edit'
    assert adapter._parse_response_payload(raw, schema=fixture.SCHEMA, max_tokens=8192)['result'] == fixture.RESULT
    assert not {'evidence', 'qa_approved', 'response_proof_sha256'} & set(parsed)


@pytest.mark.parametrize('raw', [b'', b'\xff', b'{', b'[]', b'null'])
def test_pure_parser_rejects_malformed_bytes_with_fixed_errors(raw):
    with pytest.raises(adapter.AbacusRouterError, match='^abacus_router_response_unverified$'):
        adapter._parse_response_payload(raw, schema=fixture.SCHEMA, max_tokens=8192)


def test_pure_success_cannot_waive_live_response_headers():
    prepared, payload = fixture.prepare(), fixture.envelope()
    payload['choices'][0]['native_finish_reason'] = 'STOP'
    response = exchange(prepared, payload)
    assert adapter._parse_response_payload(response.content, schema=fixture.SCHEMA,
                                          max_tokens=8192)['result'] == fixture.RESULT
    response.headers['Content-Type'] = 'text/plain'
    with pytest.raises(adapter.AbacusRouterError, match='^abacus_router_response_unverified$'):
        adapter.observe_router_response(prepared, response)
