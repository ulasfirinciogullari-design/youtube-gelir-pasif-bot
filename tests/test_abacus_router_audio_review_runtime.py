"""Actual two-purpose Redis acknowledgements and HTTPX transport, offline."""
import ast
import base64
from contextvars import copy_context
from copy import deepcopy
from dataclasses import FrozenInstanceError
import inspect
import json
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from redis.exceptions import ConnectionError

from app.services import abacus_router_audio_adapter as adapter
from app.services import abacus_router_audio_review_journal as journal
from app.services import abacus_router_audio_review_runtime as runtime
from app.services import abacus_router_review_runtime as story_runtime
from app.services import production_connection_continuity as continuity
from app.services.production_spend import LEDGER_KEY, SpendBlocked
from app.services.retained_router_audio_qa import validate_retained_router_asr, validate_retained_router_prosody
from test_abacus_router_audio_review_journal import (
    case, source, commission, mp3, KEY, NOW, ASR, PROSODY, KEYS, state, canonical,
)
from test_production_connection_continuity import _dump
from test_production_credit_ledger import InterceptClient


CAPS = {name: 0 for name in ('monthly_micro', 'daily_micro', 'channel_monthly_micro',
                            'shorts_micro', 'long_micro', 'derived_micro')}
PROSODY_RESULT = {'pass': True, 'summary': 'Clear audible delivery.', 'scores': {
    'pronunciation': 80, 'naturalness': 80, 'pacing': 80, 'sentence_flow': 80,
    'emphasis': 80, 'roboticness': 20}, 'issues': []}


class Chunks(httpx.SyncByteStream):
    def __init__(self, chunks):
        self.chunks, self.closed, self.yielded = chunks, False, 0

    def __iter__(self):
        for chunk in self.chunks:
            self.yielded += 1
            yield chunk

    def close(self):
        self.closed = True


class ClientClockOnly:
    def __init__(self, client):
        self.client, self.clock = client, lambda: NOW

    def __getattr__(self, name):
        raise AssertionError('Only existing client and clock may be read')


def envelope(result):
    return {'id': 'offline-audio-runtime-response', 'object': 'chat.completion',
        'model': 'route-llm', 'choices': [{'index': 0, 'finish_reason': 'stop',
            'message': {'role': 'assistant', 'content': canonical(result)}}]}


@pytest.fixture
def sandbox(case, source, monkeypatch):
    commission(case, source)  # Explicit test-only fake-Redis setup.
    config = SimpleNamespace(studio_spend_enforcement=True,
        studio_abacus_router_retained_audio_review_enabled=True,
        studio_abacus_router_retained_review_enabled=False,
        studio_spend_policy_json=json.dumps(CAPS), abacus_api_key=KEY)
    foundation = ClientClockOnly(case.client)
    getter = Mock(return_value=foundation)
    monkeypatch.setattr(runtime.spending, 'settings', config)
    monkeypatch.setattr(runtime.spending, 'configured_ledger', getter)
    forbidden = Mock(side_effect=AssertionError('No commissioning, financing or fallback'))
    monkeypatch.setattr(runtime.RouterAudioReviewJournal, 'commission', forbidden)
    for name in ('resolve_context', 'reserve_request', 'paid_post'):
        monkeypatch.setattr(runtime.spending, name, forbidden)
    for name in ('post', 'get', 'request', 'stream', 'AsyncClient'):
        monkeypatch.setattr(httpx, name, forbidden)
    box = SimpleNamespace(case=case, source=source, config=config, foundation=foundation,
        getter=getter, forbidden=forbidden, calls=[], responses=[], streams=[])
    def handler(request):
        box.calls.append(request)
        purpose = json.loads(request.content)['response_format']['json_schema']['name']
        assert state(case)['slots'][purpose]['response'] is None
        result = source.asr_result if purpose == ASR.value else PROSODY_RESULT
        raw = canonical(envelope(result)).encode()
        stream = Chunks([raw[:20], raw[20:]])
        response = httpx.Response(200, request=request, stream=stream,
                                  headers={'content-type': 'application/json'})
        box.streams.append(stream); box.responses.append(response)
        return response
    box.handler = handler
    def transport(**options):
        assert options == {'retries': 0, 'trust_env': False}
        return httpx.MockTransport(lambda request: box.handler(request))
    box.transport = Mock(side_effect=transport)
    monkeypatch.setattr(httpx, 'HTTPTransport', box.transport)
    yield box
    forbidden.assert_not_called()


def originals(box):
    return {key: value for key, value in _dump(box.case.client).items() if key not in KEYS}


def run_asr():
    return runtime.run_blind_asr(mp3())


def run_prosody(box, **changes):
    return runtime.run_prosody(mp3(), **{'expected_narration': box.source.expected, **changes})


def test_actual_ack_order_and_original_wire_artifacts_remain_diagnostic(sandbox):
    before = originals(sandbox)
    assert runtime.retained_audio_router_review_active() is False
    with runtime.retained_audio_router_review_scope(continuity.LEAF_ID) as scope:
        assert story_runtime.retained_router_review_active() is False
        first = run_asr()
        asr = validate_retained_router_asr(first.prepared, first.response, first.observed,
            expected_narration=sandbox.source.expected, original_audio=first.prepared.audio)
        second = run_prosody(sandbox)
        final = validate_retained_router_prosody(second.prepared, second.response, second.observed,
            asr_prepared=first.prepared, asr_response=first.response, asr_observed=first.observed,
            expected_narration=sandbox.source.expected, original_audio=first.prepared.audio)
        assert asr['component_pass'] is final['component_pass'] is True
        assert final['qa_approved'] is final['publish_eligible'] is final['journal_acknowledged'] is False
        for index, item in enumerate((first, second)):
            assert item.response is sandbox.responses[index]
            assert item.response.request is sandbox.calls[index]
            assert item.observed == adapter.observe_audio_router_response(item.prepared, item.response)
            assert item.settlement['diagnostic_only'] is True
            assert item.settlement['qa_approved'] is item.settlement['publish_eligible'] is False
            assert state(sandbox.case)['slots'][item.prepared.purpose.value]['response'] is not None
            assert KEY not in repr(item) and sandbox.source.expected not in repr(item)
            with pytest.raises(FrozenInstanceError): item.response = None
        assert second.reservation['asr_binding']['parsed_result_sha256'] == first.observed.evidence['parsed_result_sha256']
        detached = runtime.retained_audio_router_review_evidence()
        detached[ASR.value]['qa_approved'] = True
        assert scope.evidence[ASR.value]['qa_approved'] is False
        assert KEY not in repr(scope)
    assert runtime.retained_audio_router_review_active() is False
    assert runtime.retained_audio_router_review_evidence() == runtime.retained_audio_router_review_failure() == {}
    bodies = [json.loads(request.content) for request in sandbox.calls]
    assert sandbox.source.expected not in canonical(bodies[0])
    assert 'input_audio' in canonical(bodies[0])
    for request, body in zip(sandbox.calls, bodies):
        assert request.method == 'POST' and str(request.url) == adapter.ENDPOINT
        assert request.headers['authorization'] == 'Bearer ' + KEY
        assert request.headers['accept-encoding'] == 'identity' and 'cookie' not in request.headers
        assert not request.url.params
        assert body['model'] == 'route-llm' and body['modalities'] == ['text'] and body['stream'] is False
        assert base64.b64decode(body['messages'][1]['content'][0]['input_audio']['data']) == mp3()
    assert all(stream.closed for stream in sandbox.streams) and len(sandbox.calls) == 2
    assert originals(sandbox) == before


@pytest.mark.parametrize('source_id', [None, 1, True, continuity.ROOT_ID, continuity.MIDDLE_ID, 'other-leaf'])
def test_only_exact_source_leaf(sandbox, source_id):
    with pytest.raises(SpendBlocked, match='source_not_supported'):
        with runtime.retained_audio_router_review_scope(source_id): pass
    assert not sandbox.getter.called and not sandbox.transport.called


@pytest.mark.parametrize('field', ['studio_spend_enforcement', 'studio_abacus_router_retained_audio_review_enabled'])
@pytest.mark.parametrize('value', [False, 1, 'true', None])
def test_both_flags_require_actual_true(sandbox, field, value):
    setattr(sandbox.config, field, value)
    sandbox.config.studio_abacus_router_retained_review_enabled = True
    with pytest.raises(SpendBlocked, match='runtime_not_enabled'):
        with runtime.retained_audio_router_review_scope(continuity.LEAF_ID): pass
    assert not sandbox.getter.called and not sandbox.transport.called


@pytest.mark.parametrize('name', CAPS)
@pytest.mark.parametrize('value', [1, -1, 0.0, False, '0'])
def test_all_six_caps_must_be_integer_zero(sandbox, name, value):
    sandbox.config.studio_spend_policy_json = json.dumps({**CAPS, name: value})
    with pytest.raises(SpendBlocked, match='zero_cash_required'):
        with runtime.retained_audio_router_review_scope(continuity.LEAF_ID): pass
    assert not sandbox.getter.called and not sandbox.transport.called


@pytest.mark.parametrize('raw', [None, '{}', '[]', '{', json.dumps({**CAPS, 'extra': 0}),
    '{"monthly_micro":0,' + json.dumps(CAPS)[1:], json.dumps({**CAPS, 'daily_micro': float('nan')})])
def test_malformed_or_duplicate_cash_policy_is_not_an_assumed_zero(sandbox, raw):
    sandbox.config.studio_spend_policy_json = raw
    with pytest.raises(SpendBlocked):
        with runtime.retained_audio_router_review_scope(continuity.LEAF_ID): pass
    assert not sandbox.getter.called


@pytest.mark.parametrize('key', [None, '', True, 'key with whitespace', 'key\nsecret', 'şifre'])
def test_invalid_server_key_cannot_open_scope(sandbox, key):
    sandbox.config.abacus_api_key = key
    with pytest.raises(SpendBlocked, match='credential_invalid'):
        with runtime.retained_audio_router_review_scope(continuity.LEAF_ID): pass
    assert not sandbox.getter.called


def test_scope_required_nested_closed_and_cross_thread(sandbox):
    with pytest.raises(SpendBlocked, match='scope_required'): run_asr()
    with runtime.retained_audio_router_review_scope(continuity.LEAF_ID):
        copied = copy_context()
        with pytest.raises(SpendBlocked, match='scope_nested'):
            with runtime.retained_audio_router_review_scope(continuity.LEAF_ID): pass
    with pytest.raises(SpendBlocked, match='scope_unusable'): copied.run(run_asr)
    assert copied.run(runtime.retained_audio_router_review_active) is True
    errors = []
    with runtime.retained_audio_router_review_scope(continuity.LEAF_ID):
        copied = copy_context()
        def outside_thread():
            try: copied.run(run_asr)
            except SpendBlocked as error: errors.append(str(error))
        worker = Thread(target=outside_thread)
        worker.start(); worker.join(timeout=5)
        assert not worker.is_alive() and errors == ['router_audio_scope_unusable']
        with pytest.raises(SpendBlocked, match='scope_unusable'): run_asr()
    assert not sandbox.transport.called


def test_private_sender_cannot_bypass_reservation(sandbox):
    with pytest.raises(SpendBlocked, match='scope_required'):
        runtime._send_once(sandbox.source.prepared)
    with runtime.retained_audio_router_review_scope(continuity.LEAF_ID):
        with pytest.raises(SpendBlocked, match='send_not_reserved'):
            runtime._send_once(sandbox.source.prepared)
    assert not sandbox.transport.called and not state(sandbox.case)['slots']


def test_cash_history_stays_absent_and_unknown(sandbox):
    sandbox.case.client.delete(LEDGER_KEY)
    before = originals(sandbox)
    with runtime.retained_audio_router_review_scope(continuity.LEAF_ID): run_asr()
    assert originals(sandbox) == before and not sandbox.case.client.exists(LEDGER_KEY)
    assert state(sandbox.case)['policy']['historical_extra_cash_micro'] is None


def test_actual_client_disables_shared_auth_proxies_hooks_redirects_and_cookies(sandbox, monkeypatch):
    client = Mock(wraps=httpx.Client)
    monkeypatch.setattr(httpx, 'Client', client)
    monkeypatch.setenv('HTTPS_PROXY', 'https://private-proxy.invalid')
    with runtime.retained_audio_router_review_scope(continuity.LEAF_ID): run_asr()
    options = client.call_args.kwargs
    assert options['trust_env'] is options['follow_redirects'] is False
    assert options['auth'] is options['cookies'] is None
    assert options['event_hooks'] == {'request': [], 'response': []}
    assert options['headers'] == {'Accept-Encoding': 'identity'}
    assert client.call_count == 1 and len(sandbox.calls) == 1


def test_dependency_spend_exception_cannot_echo_private_details(sandbox):
    def private_error(request):
        sandbox.calls.append(request)
        raise SpendBlocked('PRIVATE ' + KEY)
    sandbox.handler = private_error
    with runtime.retained_audio_router_review_scope(continuity.LEAF_ID):
        with pytest.raises(SpendBlocked) as caught: run_asr()
        assert str(caught.value) == 'router_audio_runtime_outcome_unverified'
        assert runtime.retained_audio_router_review_failure()['reason'] == str(caught.value)
    assert len(sandbox.calls) == 1


@pytest.mark.parametrize('key', KEYS)
def test_no_missing_record_is_initialized_or_sent(sandbox, key):
    sandbox.case.client.delete(key)
    before = _dump(sandbox.case.client)
    with runtime.retained_audio_router_review_scope(continuity.LEAF_ID):
        with pytest.raises(SpendBlocked): run_asr()
    assert _dump(sandbox.case.client) == before and not sandbox.transport.called


@pytest.mark.parametrize('change', ['key', 'flag', 'enforcement', 'cash'])
@pytest.mark.parametrize('when', ['before_reserve', 'after_reserve'])
def test_scope_freezes_settings_key_and_rechecks_before_post(sandbox, change, when):
    def mutate():
        if change == 'key': sandbox.config.abacus_api_key = 'changed-account-key'
        elif change == 'flag': sandbox.config.studio_abacus_router_retained_audio_review_enabled = False
        elif change == 'enforcement': sandbox.config.studio_spend_enforcement = False
        else: sandbox.config.studio_spend_policy_json = json.dumps({**CAPS, 'long_micro': 1})
    if when == 'after_reserve':
        def after(number, ack):
            if number == 1: mutate()
            return ack
        sandbox.foundation.client = InterceptClient(sandbox.case.client, after=after)
    with runtime.retained_audio_router_review_scope(continuity.LEAF_ID):
        if when == 'before_reserve': mutate()
        with pytest.raises(SpendBlocked): run_asr()
        assert runtime.retained_audio_router_review_active()
    assert bool(state(sandbox.case)['slots']) is (when == 'after_reserve')
    assert not sandbox.transport.called


def test_source_continuity_change_before_reservation_prevents_post(sandbox):
    sandbox.case.client.set(continuity._AUTH_EPOCH, '19')
    with runtime.retained_audio_router_review_scope(continuity.LEAF_ID):
        with pytest.raises(SpendBlocked): run_asr()
    assert not sandbox.transport.called


@pytest.mark.parametrize('change', ['flag', 'key'])
def test_unsent_prosody_failure_does_not_reuse_successful_asr_http_status(sandbox, change):
    with runtime.retained_audio_router_review_scope(continuity.LEAF_ID):
        run_asr()
        if change == 'flag': sandbox.config.studio_abacus_router_retained_audio_review_enabled = False
        else: sandbox.config.abacus_api_key = 'changed-account-key'
        with pytest.raises(SpendBlocked): run_prosody(sandbox)
        failure = runtime.retained_audio_router_review_failure()
        assert failure['purpose'] == PROSODY.value and failure['http_status'] is None
    assert len(sandbox.calls) == 1 and set(state(sandbox.case)['slots']) == {ASR.value}


def test_prosody_before_acknowledged_asr_is_terminal(sandbox):
    with runtime.retained_audio_router_review_scope(continuity.LEAF_ID):
        with pytest.raises(SpendBlocked, match='asr_unacknowledged'): run_prosody(sandbox)
        with pytest.raises(SpendBlocked, match='scope_unusable'): run_asr()
    assert not sandbox.transport.called and not state(sandbox.case)['slots']


@pytest.mark.parametrize('mutation', ['expected', 'audio', 'response', 'observed_result'])
def test_prosody_requires_same_audio_original_contract_and_actual_asr(sandbox, mutation):
    with runtime.retained_audio_router_review_scope(continuity.LEAF_ID):
        first = run_asr()
        if mutation == 'response':
            first.response._content = canonical(envelope({**sandbox.source.asr_result, 'text': 'Başka metin.'})).encode()
        elif mutation == 'observed_result':
            object.__setattr__(first.observed, '_result_bytes', b'{}')
        with pytest.raises(SpendBlocked):
            if mutation == 'audio': runtime.run_prosody(mp3(600), expected_narration=sandbox.source.expected)
            else: run_prosody(sandbox, expected_narration='Başka metin.' if mutation == 'expected' else sandbox.source.expected)
    assert set(state(sandbox.case)['slots']) == {ASR.value} and len(sandbox.calls) == 1


def test_valid_blind_transcript_mismatch_is_recorded_but_cannot_open_prosody(sandbox):
    original = sandbox.handler
    def mismatched(request):
        response = original(request)
        result = {'text': 'Başka metin.', 'language': 'tr', 'words': [
            {'word': 'Başka', 'start': .1, 'end': .3}, {'word': 'metin.', 'start': .4, 'end': .6}]}
        response.stream = Chunks([canonical(envelope(result)).encode()])
        return response
    sandbox.handler = mismatched
    with runtime.retained_audio_router_review_scope(continuity.LEAF_ID):
        first = run_asr()
        assert first.observed.result['text'] == 'Başka metin.'
        with pytest.raises(SpendBlocked, match='asr_not_exact'): run_prosody(sandbox)
    assert len(sandbox.calls) == 1 and set(state(sandbox.case)['slots']) == {ASR.value}


@pytest.mark.parametrize('purpose', [ASR, PROSODY])
@pytest.mark.parametrize('operation', ['reserve', 'settle'])
def test_lost_reserve_or_settle_ack_cannot_return_resend_or_fallback(sandbox, purpose, operation):
    target = (1 if purpose is ASR else 3) + (operation == 'settle')
    def after(number, ack):
        if number == target: raise ConnectionError('PRIVATE ' + KEY)
        return ack
    sandbox.foundation.client = InterceptClient(sandbox.case.client, after=after)
    with runtime.retained_audio_router_review_scope(continuity.LEAF_ID):
        if purpose is PROSODY: run_asr()
        with pytest.raises(SpendBlocked) as caught:
            run_asr() if purpose is ASR else run_prosody(sandbox)
        assert KEY not in repr(caught.value) and 'PRIVATE' not in str(caught.value)
        assert purpose.value not in runtime.retained_audio_router_review_evidence()
        assert runtime.retained_audio_router_review_active()
        with pytest.raises(SpendBlocked, match='scope_unusable'): run_prosody(sandbox)
    slot = state(sandbox.case)['slots'][purpose.value]
    assert (slot['response'] is not None) is (operation == 'settle')
    sandbox.foundation.client = sandbox.case.client
    with runtime.retained_audio_router_review_scope(continuity.LEAF_ID):
        with pytest.raises(SpendBlocked):
            run_asr() if purpose is ASR else run_prosody(sandbox)
    assert len(sandbox.calls) == (purpose is PROSODY) + (operation == 'settle')


def test_same_purpose_cannot_repeat_in_same_or_new_scope(sandbox):
    with runtime.retained_audio_router_review_scope(continuity.LEAF_ID):
        run_asr()
        with pytest.raises(SpendBlocked, match='already_attempted'): run_asr()
        with pytest.raises(SpendBlocked, match='scope_unusable'): run_prosody(sandbox)
    with runtime.retained_audio_router_review_scope(continuity.LEAF_ID):
        with pytest.raises(SpendBlocked, match='already_reserved'): run_asr()
    assert len(sandbox.calls) == 1


@pytest.mark.parametrize('damage', ['timeout', 'json', 'finish_length', 'header_key', 'request_body',
    '302', '400', '401', '403', '429', '500', 'compressed', 'huge_length', 'duplicate_length'])
def test_terminal_transport_protocol_failure_keeps_status_slot_and_no_retry(sandbox, damage):
    def handle(request):
        sandbox.calls.append(request)
        if damage == 'timeout': raise httpx.ReadTimeout('PRIVATE ' + KEY, request=request)
        status_code = int(damage) if damage.isdecimal() else 200
        payload = envelope(sandbox.source.asr_result)
        headers = [('content-type', 'application/json')]
        if damage == '302': headers += [('location', 'https://private.invalid/secret')]
        elif damage == 'finish_length': payload['choices'][0]['finish_reason'] = 'length'
        elif damage == 'header_key': request.headers['authorization'] = 'Bearer changed-wire-key'
        elif damage == 'request_body':
            changed = json.loads(request.content); changed['model'] = 'premium-model'
            request._content = canonical(changed).encode()
            request.headers['content-length'] = str(len(request.content))
        elif damage == 'compressed': headers += [('content-encoding', 'gzip')]
        elif damage == 'huge_length': headers += [('content-length', str(adapter.MAX_RESPONSE_BYTES + 1))]
        elif damage == 'duplicate_length': headers += [('content-length', '2'), ('Content-Length', '2')]
        raw = b'PRIVATE ' + KEY.encode() if damage == 'json' or status_code != 200 else canonical(payload).encode()
        stream = Chunks([raw]); sandbox.streams.append(stream)
        return httpx.Response(status_code, request=request, headers=headers, stream=stream)
    sandbox.handler = handle
    with runtime.retained_audio_router_review_scope(continuity.LEAF_ID) as scope:
        with pytest.raises(SpendBlocked) as caught: run_asr()
        detail = runtime.retained_audio_router_review_failure()
        assert detail['http_status'] == (None if damage == 'timeout' else int(damage) if damage.isdecimal() else 200)
        assert KEY not in repr(caught.value) and 'PRIVATE' not in str(caught.value)
        assert KEY not in canonical(detail) and 'PRIVATE' not in canonical(detail)
        assert detail['qa_approved'] is detail['publish_eligible'] is False
        detail['reason'] = 'caller changed'
        assert runtime.retained_audio_router_review_failure()['reason'] != 'caller changed'
        with pytest.raises(SpendBlocked): run_prosody(sandbox)
        assert scope.failure['http_status'] == (None if damage == 'timeout' else int(damage) if damage.isdecimal() else 200)
        assert runtime.retained_audio_router_review_evidence() == {}
    assert len(sandbox.calls) == 1 and all(stream.closed for stream in sandbox.streams)
    assert state(sandbox.case)['slots'][ASR.value]['response'] is None


def test_raw_stream_bound_stops_reading_and_closes_response(sandbox):
    def oversized(request):
        sandbox.calls.append(request)
        stream = Chunks([b'x' * (64 * 1024)] * 40); sandbox.streams.append(stream)
        return httpx.Response(200, request=request, headers={'content-type': 'application/json'}, stream=stream)
    sandbox.handler = oversized
    with runtime.retained_audio_router_review_scope(continuity.LEAF_ID):
        with pytest.raises(SpendBlocked, match='response_size_invalid'): run_asr()
    assert sandbox.streams[0].yielded == 33 and sandbox.streams[0].closed
    assert len(sandbox.calls) == 1


@pytest.mark.parametrize(('code', 'indicator'), [
    ('invalid_json_schema', 'schema_invalid'), ('unsupported_parameter', 'unsupported_parameter'),
    ('invalid_api_key', 'authentication'), ('insufficient_credits', 'subscription_or_credits'),
    ('rate_limit_exceeded', 'rate_limit'), ('rate_limit_error', 'rate_limit'),
    ('model_not_found', 'model_unavailable'),
])
def test_error_json_preserves_only_fixed_fields_and_unverified_reason_indicators(sandbox, code, indicator):
    secret = 'PRIVATE remote echo ' + KEY
    def denied(request):
        sandbox.calls.append(request)
        raw = canonical({'error': {'code': code, 'type': 'invalid_request_error',
            'param': 'response_format', 'message': secret, 'api_key': secret}, 'private': secret}).encode()
        stream = Chunks([raw]); sandbox.streams.append(stream)
        return httpx.Response(400, request=request, stream=stream,
            headers={'content-type': 'application/json', 'x-request-id': secret})
    sandbox.handler = denied
    with runtime.retained_audio_router_review_scope(continuity.LEAF_ID):
        with pytest.raises(SpendBlocked, match='response_rejected'): run_asr()
        detail = runtime.retained_audio_router_review_failure()
        error = detail['provider_error']
        assert detail['http_status'] == 400 and error['cause_verified'] is False
        assert error['classification'] == 'provider_reported_fields'
        assert error['fields'] == {'code': code, 'type': 'invalid_request_error', 'param': 'response_format'}
        assert error['indicators'][indicator] is True and sum(error['indicators'].values()) == 1
        assert secret not in canonical(detail) and 'message' not in canonical(detail)
        error['fields']['code'] = secret
        assert secret not in canonical(runtime.retained_audio_router_review_failure())
        with pytest.raises(SpendBlocked, match='scope_unusable'): run_asr()
    assert len(sandbox.calls) == 1 and sandbox.streams[0].closed
    assert state(sandbox.case)['slots'][ASR.value]['response'] is None


@pytest.mark.parametrize('raw', [
    canonical({'error': {'message': 'authentication failed PRIVATE ' + KEY}}).encode(),
    canonical({'error': {'code': KEY, 'type': KEY, 'param': KEY}}).encode(),
    b'{"error":{"code":"invalid_api_key","code":"model_not_found"}}',
    b'not JSON', b'[]', b'{"error":"private message"}',
])
def test_unknown_or_duplicate_provider_errors_never_invent_a_cause(sandbox, raw):
    def denied(request):
        sandbox.calls.append(request)
        stream = Chunks([raw]); sandbox.streams.append(stream)
        return httpx.Response(403, request=request, stream=stream)
    sandbox.handler = denied
    with runtime.retained_audio_router_review_scope(continuity.LEAF_ID):
        with pytest.raises(SpendBlocked): run_asr()
        detail = runtime.retained_audio_router_review_failure()
        assert detail['http_status'] == 403
        assert detail['provider_error'] == {'classification': 'unknown', 'cause_verified': False}
        assert KEY not in canonical(detail)
    assert len(sandbox.calls) == 1


@pytest.mark.parametrize('message', ['Invalid API Key', 'invalid api key', ' Invalid API Key\n'])
def test_abacus_text_credential_rejection_is_classified_without_releasing_slot(sandbox, message):
    before = originals(sandbox)
    secret = 'PRIVATE diagnostic echo ' + KEY
    def denied(request):
        sandbox.calls.append(request)
        raw = canonical({'error': message, 'private': secret}).encode()
        stream = Chunks([raw]); sandbox.streams.append(stream)
        return httpx.Response(403, request=request, stream=stream,
                              headers={'content-type': 'application/json', 'x-private': secret})
    sandbox.handler = denied
    with runtime.retained_audio_router_review_scope(continuity.LEAF_ID):
        with pytest.raises(SpendBlocked, match='response_rejected'): run_asr()
        detail = runtime.retained_audio_router_review_failure()
        error = detail['provider_error']
        assert detail['http_status'] == 403
        assert error['classification'] == 'provider_reported_message'
        assert error['message_kind'] == 'invalid_api_key' and error['cause_verified'] is False
        assert error['indicators']['authentication'] is True
        assert sum(error['indicators'].values()) == 1
        assert secret not in canonical(detail) and message.strip() not in canonical(detail)
        error['message_kind'] = secret
        assert secret not in canonical(runtime.retained_audio_router_review_failure())
        with pytest.raises(SpendBlocked, match='scope_unusable'): run_asr()
        with pytest.raises(SpendBlocked, match='scope_unusable'): run_prosody(sandbox)
    assert len(sandbox.calls) == 1 and sandbox.streams[0].closed
    slots = state(sandbox.case)['slots']
    assert set(slots) == {ASR.value} and slots[ASR.value]['response'] is None
    assert originals(sandbox) == before


@pytest.mark.parametrize('message', [
    'Invalid API Key ' + KEY, 'Invalid API Key: ' + KEY,
    'Not an invalid API key', 'Invalid API Key. Please use a different account.',
])
def test_credential_keywords_do_not_classify_or_store_arbitrary_messages(sandbox, message):
    def denied(request):
        sandbox.calls.append(request)
        stream = Chunks([canonical({'error': message}).encode()]); sandbox.streams.append(stream)
        return httpx.Response(403, request=request, stream=stream)
    sandbox.handler = denied
    with runtime.retained_audio_router_review_scope(continuity.LEAF_ID):
        with pytest.raises(SpendBlocked): run_asr()
        detail = runtime.retained_audio_router_review_failure()
        assert detail['provider_error'] == {'classification': 'unknown', 'cause_verified': False}
        assert KEY not in canonical(detail) and message not in canonical(detail)
    assert len(sandbox.calls) == 1


def test_error_body_limit_discards_all_fields_and_stops_after_first_overflow(sandbox):
    def denied(request):
        sandbox.calls.append(request)
        stream = Chunks([b'x' * 4096] * 6); sandbox.streams.append(stream)
        return httpx.Response(400, request=request, stream=stream)
    sandbox.handler = denied
    with runtime.retained_audio_router_review_scope(continuity.LEAF_ID):
        with pytest.raises(SpendBlocked, match='response_rejected'): run_asr()
        detail = runtime.retained_audio_router_review_failure()
        assert detail['http_status'] == 400 and detail['provider_error'] == {'body_too_large': True}
        with pytest.raises(SpendBlocked): run_prosody(sandbox)
    assert len(sandbox.calls) == 1 and sandbox.streams[0].yielded == 5 and sandbox.streams[0].closed
    assert state(sandbox.case)['slots'][ASR.value]['response'] is None


@pytest.mark.parametrize('operation', ['reserve', 'settle'])
def test_missing_ack_value_is_not_a_send_or_result_permission(sandbox, monkeypatch, operation):
    original = getattr(runtime.RouterAudioReviewJournal, operation)
    def missing(self, *args, **kwargs):
        original(self, *args, **kwargs)
        return None
    monkeypatch.setattr(runtime.RouterAudioReviewJournal, operation, missing)
    with runtime.retained_audio_router_review_scope(continuity.LEAF_ID):
        with pytest.raises(SpendBlocked, match='unverified'): run_asr()
        assert runtime.retained_audio_router_review_evidence() == {}
    assert len(sandbox.calls) == (operation == 'settle')


def test_no_initialization_sender_injection_qa_approval_or_default_flag():
    assert list(inspect.signature(runtime.run_blind_asr).parameters) == ['audio_bytes']
    assert list(inspect.signature(runtime.run_prosody).parameters) == ['audio_bytes', 'expected_narration']
    tree = ast.parse(Path(runtime.__file__).read_text())
    forbidden = {'commission', 'initialize', 'resolve_context', 'reserve_request', 'paid_post',
                 'transcribe_whisper_bounded', 'generate_gemini_audio_json'}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ''
            assert name not in forbidden
    config_tree = ast.parse((Path(runtime.__file__).parents[1] / 'config.py').read_text())
    flag = next(node for node in ast.walk(config_tree) if isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == 'studio_abacus_router_retained_audio_review_enabled')
    assert ast.literal_eval(flag.value) is False
