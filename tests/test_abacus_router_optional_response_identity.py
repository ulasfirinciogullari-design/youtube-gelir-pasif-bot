"""Optional identity and native usage retain actual, bounded router evidence."""
from dataclasses import replace
import hashlib
import json

import httpx
import pytest

from app.services import abacus_router_adapter as text_adapter
from app.services import abacus_router_audio_adapter as audio_adapter
from app.services import abacus_router_audio_review_journal as audio_journal
from app.services import retained_router_audio_qa as bridge
import test_abacus_router_adapter as text_fixture
import test_abacus_router_audio_adapter as audio_fixture
import test_abacus_router_audio_review_journal as journal_fixture
import test_retained_audio_review_evidence as persisted_fixture
from test_abacus_router_audio_review_journal import case, source, no_transport
from test_retained_audio_review_evidence import complete
from test_production_connection_continuity import _dump


# Counter shape/values match the private diagnostic inspection; no raw/effective
# input ordering, cache effect, price or reported total is inferred.
NATIVE_USAGE = {'input_tokens': 217, 'output_tokens': 9, 'raw_input_tokens': 217}


def sample(kind):
    if kind == 'text':
        return text_fixture.prepare(), text_fixture.envelope(), text_adapter.observe_router_response
    prepared = audio_fixture.prepare() if kind == 'asr' else audio_fixture.prosody()
    result = audio_fixture.ASR if kind == 'asr' else audio_fixture.PROSODY
    return prepared, audio_fixture.envelope(result), audio_adapter.observe_audio_router_response


def actual(prepared, payload, *, request=None, headers=None):
    kwargs = prepared.wire_kwargs()
    kwargs.pop('timeout')
    return httpx.Response(200, json=payload, headers=headers,
        request=request if request is not None else httpx.Request('POST', prepared.endpoint, **kwargs))


def without_identity(payload):
    return {key: value for key, value in payload.items() if key not in {'id', 'object'}}


def test_synthetic_fixture_of_inspected_protocol_shape_has_no_invented_metadata():
    schema = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}},
              'required': ['ok'], 'additionalProperties': False}
    prepared = text_adapter.prepare_router_request([{'type': 'text', 'text': 'Return {"ok":true}.'}],
        api_key=text_fixture.KEY, system_instruction='Return only the requested JSON object.',
        json_schema=schema, max_tokens=32)
    # Model/timestamp are synthetic. This tests the observed shape locally; it
    # neither reconstructs the private HTTP response nor settles a prior slot.
    payload = {'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': '{"ok":true}'},
                            'finish_reason': 'stop'}], 'created': 1_783_456_789,
               'model': 'synthetic-upstream-model', 'usage': dict(NATIVE_USAGE)}
    response = actual(prepared, payload)
    observed = text_adapter.observe_router_response(prepared, response)
    assert observed.result == {'ok': True} and observed.usage == NATIVE_USAGE
    assert observed.evidence['provider_request_id_sha256'] is None
    assert observed.returned_model == payload['model'] and observed.underlying_model_verified is False
    assert observed.evidence['response_body_sha256'] == hashlib.sha256(response.content).hexdigest()
    assert 'total_tokens' not in observed.usage


@pytest.mark.parametrize('kind', ['text', 'asr', 'prosody'])
@pytest.mark.parametrize('omitted', [('id',), ('object',), ('id', 'object')])
@pytest.mark.parametrize('native_usage', [False, True])
def test_only_absent_metadata_is_optional_and_body_id_is_never_synthesized(kind, omitted, native_usage):
    prepared, payload, observe = sample(kind)
    if native_usage:
        payload['usage'] = dict(NATIVE_USAGE)
    expected = json.loads(payload['choices'][0]['message']['content'])
    original_id = payload['id']
    for field in omitted:
        del payload[field]
    response = actual(prepared, payload, headers={
        'Content-Type': 'application/json', 'X-Request-ID': 'untrusted-header-is-not-body-id'})
    observed = observe(prepared, response)
    evidence = observed.evidence
    assert observed.result == expected and observed.underlying_model_verified is False
    assert observed.usage == payload['usage']
    if native_usage:
        assert 'total_tokens' not in observed.usage and 'prompt_tokens' not in observed.usage
    assert evidence['provider_request_id_sha256'] == (None if 'id' in omitted else
        hashlib.sha256(('abacus\0router-request\0' + original_id).encode()).hexdigest())
    assert evidence['request_sha256'] == prepared.request_sha256
    assert evidence['wire_body_sha256'] == hashlib.sha256(response.request.content).hexdigest()
    assert evidence['response_body_sha256'] == hashlib.sha256(response.content).hexdigest()
    proof = evidence.pop('response_proof_sha256')
    assert proof == hashlib.sha256(text_adapter._canonical(evidence)).hexdigest()
    assert 'untrusted-header-is-not-body-id' not in json.dumps(observed.evidence)
    assert not {'qa_approved', 'publish_eligible', 'actual_micro'} & observed.evidence.keys()
    assert 'id' not in payload if 'id' in omitted else payload['id'] == original_id


@pytest.mark.parametrize('kind', ['text', 'asr', 'prosody'])
@pytest.mark.parametrize('raw_input', [0, 1_000_000_000])
def test_native_raw_input_is_preserved_without_an_unverified_arithmetic_relation(kind, raw_input):
    prepared, payload, observe = sample(kind)
    usage = {**NATIVE_USAGE, 'raw_input_tokens': raw_input}
    payload = {**without_identity(payload), 'usage': usage}
    observed = observe(prepared, actual(prepared, payload))
    assert observed.usage == usage and set(observed.usage) == set(NATIVE_USAGE)
    observed.usage['raw_input_tokens'] = 44
    assert observed.usage['raw_input_tokens'] == raw_input


@pytest.mark.parametrize('kind', ['text', 'asr'])
@pytest.mark.parametrize('field', ['input_tokens', 'output_tokens', 'raw_input_tokens'])
@pytest.mark.parametrize('value', [True, 1.0, -1, 1_000_000_001, None, '9'])
def test_native_counters_reject_coercion_negative_and_unbounded_values(kind, field, value):
    prepared, payload, observe = sample(kind)
    payload = {**without_identity(payload), 'usage': {**NATIVE_USAGE, field: value}}
    with pytest.raises((text_adapter.AbacusRouterError, audio_adapter.AbacusRouterAudioError),
                       match='usage_invalid'):
        observe(prepared, actual(prepared, payload))


@pytest.mark.parametrize('kind', ['text', 'asr'])
@pytest.mark.parametrize('damage', ['missing_input', 'missing_output', 'missing_raw',
    'extra', 'invented_total', 'mixed_complete', 'canonical_collision', 'output_over_limit'])
def test_native_and_legacy_usage_are_separate_closed_formats(kind, damage):
    prepared, payload, observe = sample(kind)
    usage = dict(NATIVE_USAGE)
    if damage.startswith('missing_'):
        del usage[{'missing_input': 'input_tokens', 'missing_output': 'output_tokens',
                   'missing_raw': 'raw_input_tokens'}[damage]]
    elif damage == 'extra': usage['unreviewed_counter'] = 0
    elif damage == 'invented_total': usage['total_tokens'] = 226
    elif damage == 'mixed_complete': usage.update(payload['usage'])
    elif damage == 'canonical_collision': usage = {**payload['usage'], 'input_tokens': 217}
    else: usage['output_tokens'] = prepared.payload['max_tokens'] + 1
    payload = {**without_identity(payload), 'usage': usage}
    with pytest.raises((text_adapter.AbacusRouterError, audio_adapter.AbacusRouterAudioError),
                       match='usage_invalid'):
        observe(prepared, actual(prepared, payload))


@pytest.mark.parametrize('kind', ['text', 'asr'])
@pytest.mark.parametrize('field,value', [
    ('id', None), ('id', ''), ('id', False), ('id', 0), ('id', []), ('id', {}),
    ('id', 'has whitespace'), ('id', 'x'*161),
    ('object', None), ('object', False), ('object', 0), ('object', []), ('object', {}),
    ('object', ''), ('object', 'chat.completion.chunk'),
])
def test_present_null_or_malformed_identity_is_not_treated_as_absent(kind, field, value):
    prepared, payload, observe = sample(kind)
    payload = {**without_identity(payload), field: value}
    with pytest.raises((text_adapter.AbacusRouterError, audio_adapter.AbacusRouterAudioError)):
        observe(prepared, actual(prepared, payload))


@pytest.mark.parametrize('kind', ['text', 'asr', 'prosody'])
@pytest.mark.parametrize('damage', ['missing_model', 'missing_choices', 'unknown_root',
    'two_choices', 'length', 'refusal', 'schema', 'usage_boolean', 'usage_sum', 'wire'])
def test_absent_identity_never_waives_completion_schema_usage_or_wire_checks(kind, damage):
    prepared, payload, observe = sample(kind)
    payload = without_identity(payload)
    request = None
    if damage == 'missing_model': del payload['model']
    elif damage == 'missing_choices': del payload['choices']
    elif damage == 'unknown_root': payload['unreviewed_execution'] = True
    elif damage == 'two_choices': payload['choices'] *= 2
    elif damage == 'length': payload['choices'][0]['finish_reason'] = 'length'
    elif damage == 'refusal': payload['choices'][0]['message']['refusal'] = 'refused'
    elif damage == 'schema': payload['choices'][0]['message']['content'] = '{}'
    elif damage == 'usage_boolean': payload['usage']['prompt_tokens'] = False
    elif damage == 'usage_sum': payload['usage']['total_tokens'] += 1
    else:
        request = actual(prepared, payload).request
        request.headers['authorization'] = 'Bearer changed-fixture-key'
    with pytest.raises((text_adapter.AbacusRouterError, audio_adapter.AbacusRouterAudioError)):
        observe(prepared, actual(prepared, payload, request=request))


def test_nullable_identity_settles_and_reads_back_without_new_send_permission(case, source):
    ledger = journal_fixture.commission(case, source)
    ledger.reserve(journal_fixture.ASR, source.prepared)
    response = journal_fixture.response(source.prepared, source.asr_result)
    response = actual(source.prepared, {**without_identity(response.json()), 'usage': dict(NATIVE_USAGE)})
    settled = ledger.settle(journal_fixture.ASR, source.prepared, response)
    assert settled['evidence']['provider_request_id_sha256'] is None
    assert settled['evidence']['usage'] == NATIVE_USAGE
    assert settled['qa_approved'] is settled['publish_eligible'] is False
    before = _dump(case.client)
    with case.client.pipeline() as pipe:
        pipe.watch(*ledger.keys)
        recorded = ledger._read(pipe)
    assert recorded['slots'][journal_fixture.ASR.value]['response']['evidence'] == settled['evidence']
    assert _dump(case.client) == before
    with pytest.raises(audio_journal.RouterAudioReviewBlocked):
        ledger.reserve(journal_fixture.ASR, source.prepared)
    assert _dump(case.client) == before


@pytest.mark.parametrize('invalid', ['', False, 0, [], {}, 'x'*64])
def test_nullable_durable_id_still_rejects_malformed_non_null_hash(case, source, invalid):
    ledger = journal_fixture.commission(case, source)
    journal_fixture.observe_asr(ledger, source)
    state = journal_fixture.state(case)
    recorded = state['slots'][journal_fixture.ASR.value]['response']
    evidence = recorded['evidence']
    evidence['provider_request_id_sha256'] = invalid
    evidence['response_proof_sha256'] = journal_fixture.digest({
        key: item for key, item in evidence.items() if key != 'response_proof_sha256'})
    case.client.set(audio_journal.STATE_KEY, journal_fixture.canonical(state))
    case.client.hset(audio_journal.JOURNAL_KEY, mapping={
        'state_sha256': journal_fixture.digest(state),
        'response:' + journal_fixture.ASR.value: journal_fixture.canonical(recorded)})
    case.client.set(audio_journal.ANCHOR_KEY, journal_fixture.digest(state))
    before = _dump(case.client)
    with pytest.raises(audio_journal.RouterAudioReviewBlocked):
        with case.client.pipeline() as pipe:
            pipe.watch(*ledger.keys)
            ledger._read(pipe)
    assert _dump(case.client) == before


@pytest.mark.parametrize('damage', ['boolean_raw', 'extra_total', 'mixed'])
def test_durable_native_usage_rejects_self_consistent_invalid_counters(case, source, damage):
    ledger = journal_fixture.commission(case, source)
    journal_fixture.observe_asr(ledger, source)
    state = journal_fixture.state(case)
    recorded = state['slots'][journal_fixture.ASR.value]['response']
    evidence = recorded['evidence']
    usage = dict(NATIVE_USAGE)
    if damage == 'boolean_raw': usage['raw_input_tokens'] = True
    elif damage == 'extra_total': usage['total_tokens'] = 226
    else: usage.update(prompt_tokens=217, completion_tokens=9, total_tokens=226)
    evidence['usage'] = usage
    evidence['response_proof_sha256'] = journal_fixture.digest({
        key: item for key, item in evidence.items() if key != 'response_proof_sha256'})
    case.client.set(audio_journal.STATE_KEY, journal_fixture.canonical(state))
    case.client.hset(audio_journal.JOURNAL_KEY, mapping={
        'state_sha256': journal_fixture.digest(state),
        'response:' + journal_fixture.ASR.value: journal_fixture.canonical(recorded)})
    case.client.set(audio_journal.ANCHOR_KEY, journal_fixture.digest(state))
    before = _dump(case.client)
    with pytest.raises(audio_adapter.AbacusRouterAudioError, match='^abacus_router_audio_usage_invalid$'):
        with case.client.pipeline() as pipe:
            pipe.watch(*ledger.keys)
            ledger._read(pipe)
    assert _dump(case.client) == before


def test_forged_missing_id_cannot_replace_an_actual_body_id_in_pure_bridge():
    prepared, payload, observe = sample('asr')
    response = actual(prepared, payload)
    observed = observe(prepared, response)
    evidence = observed.evidence
    evidence['provider_request_id_sha256'] = None
    evidence['response_proof_sha256'] = hashlib.sha256(text_adapter._canonical({
        key: item for key, item in evidence.items() if key != 'response_proof_sha256'})).hexdigest()
    forged = replace(observed, _evidence_bytes=text_adapter._canonical(evidence))
    with pytest.raises(bridge.RetainedRouterAudioQAError):
        bridge.validate_retained_router_asr(prepared, response, forged,
            expected_narration=audio_fixture.EXPECTED, original_audio=prepared.audio)


def test_forged_native_counter_cannot_replace_the_actual_body_in_pure_bridge():
    prepared, payload, observe = sample('asr')
    response = actual(prepared, {**without_identity(payload), 'usage': dict(NATIVE_USAGE)})
    observed = observe(prepared, response)
    evidence = observed.evidence
    evidence['usage']['raw_input_tokens'] += 1
    evidence['response_proof_sha256'] = hashlib.sha256(text_adapter._canonical({
        key: item for key, item in evidence.items() if key != 'response_proof_sha256'})).hexdigest()
    forged = replace(observed, _evidence_bytes=text_adapter._canonical(evidence))
    with pytest.raises(bridge.RetainedRouterAudioQAError):
        bridge.validate_retained_router_asr(prepared, response, forged,
            expected_narration=audio_fixture.EXPECTED, original_audio=prepared.audio)


@pytest.fixture
def nullable_complete(request, monkeypatch):
    response = persisted_fixture.response
    def missing(prepared, result, **patch):
        received = response(prepared, result, **patch)
        return actual(prepared, {**without_identity(received.json()), 'usage': dict(NATIVE_USAGE)})
    monkeypatch.setattr(persisted_fixture, 'response', missing)
    return request.getfixturevalue('complete')


def test_persisted_audio_with_absent_ids_stays_read_only_and_diagnostic(nullable_complete):
    box = nullable_complete
    before = _dump(box.case.client)
    verified = persisted_fixture.read(box)
    assert verified.diagnostic_only and verified.component_pass
    assert not any((verified.qa_approved, verified.publish_eligible,
                    verified.full_qa_complete, verified.edit_duration_qa_complete))
    for review in verified.diagnostics['reviews'].values():
        assert review['settlement']['evidence']['provider_request_id_sha256'] is None
        assert review['settlement']['evidence']['usage'] == NATIVE_USAGE
    assert _dump(box.case.client) == before
