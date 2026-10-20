"""Real journal CAS + actual HTTPX streamed transport, entirely offline."""
import ast
from contextvars import copy_context
from copy import deepcopy
from datetime import datetime, timezone
from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from redis.exceptions import ConnectionError

from app.services import abacus_router_review_runtime as runtime
from app.services import abacus_router_review_journal as journal
from app.services import production_connection_continuity as continuity
from app.services.abacus_router_adapter import (
    ENDPOINT, MODEL, MAX_RESPONSE_BYTES, ObservedRouterResult,
)
from app.services.production_spend import LEDGER_KEY, SpendBlocked, SpendLedger, SpendPolicy
from test_production_connection_continuity import case, REVISION, OLD, NEW, _dump
from test_production_credit_ledger import InterceptClient


NOW = datetime(2026, 9, 9, 16, tzinfo=timezone.utc)
KEY = 'private-runtime-router-key'
CAPS = {'monthly_micro': 0, 'daily_micro': 0, 'channel_monthly_micro': 0,
        'shorts_micro': 0, 'long_micro': 0, 'derived_micro': 0}
SCHEMA = {'type': 'object', 'properties': {'accepted': {'type': 'boolean'}},
          'required': ['accepted'], 'additionalProperties': False}
STORY, VISUAL = journal.PURPOSES


def payload(model=MODEL):
    return {'id': 'fixture-runtime-request', 'object': 'chat.completion', 'model': model,
            'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {
                'role': 'assistant', 'content': '{"accepted": false}'}}]}


def generate(purpose=STORY, **changes):
    return runtime.generate_retained_router_review(
        [{'type': 'text', 'text': 'PRIVATE complete immutable rubric for ' + purpose}],
        **{'purpose': purpose, 'system_instruction': 'All original criteria; no waived gate.',
           'json_schema': deepcopy(SCHEMA), 'max_tokens': 1024, **changes})


class Chunks(httpx.SyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False
        self.yielded = 0

    def __iter__(self):
        for chunk in self.chunks:
            self.yielded += 1
            yield chunk

    def close(self):
        self.closed = True


@pytest.fixture
def sandbox(case, monkeypatch):
    config = SimpleNamespace(studio_spend_enforcement=True,
        studio_abacus_router_retained_review_enabled=True, studio_spend_policy_json=json.dumps(CAPS),
        abacus_api_key=KEY)
    monkeypatch.setattr(runtime.spending, 'settings', config)
    foundation = SpendLedger(case.client, SpendPolicy(**CAPS), clock=lambda: NOW)
    ledger_getter = Mock(return_value=foundation)
    monkeypatch.setattr(runtime.spending, 'configured_ledger', ledger_getter)
    forbidden = Mock(side_effect=AssertionError('runtime must not initialize or submit ordinary paid work'))
    monkeypatch.setattr(foundation, 'initialize', forbidden)
    monkeypatch.setattr(foundation, 'reserve', forbidden)
    monkeypatch.setattr(runtime.spending, 'resolve_context', forbidden)
    monkeypatch.setattr(runtime.spending, 'reserve_request', forbidden)
    monkeypatch.setattr(runtime.spending, 'paid_post', forbidden)
    continuity_proof = continuity.prepare_connection_continuity(
        continuity.ROOT_ID, continuity.LEAF_ID, continuity.CHANNEL_ID, REVISION, client=case.client)
    policy = {'version': 1, 'kind': 'existing_subscription_retained_review',
        'endpoint': ENDPOINT, 'model': MODEL, 'original_task_id': continuity.ROOT_ID,
        'leaf_task_id': continuity.LEAF_ID, 'channel_id': continuity.CHANNEL_ID,
        'profile_revision': REVISION, 'old_connection_id': OLD, 'current_connection_id': NEW,
        'continuity_sha256': continuity_proof['receipt_sha256'],
        'credential_sha256': hashlib.sha256(('abacus\0' + KEY).encode()).hexdigest(),
        'entitlement_evidence_sha256': 'e' * 64,
        'entitlement_source': 'owner_subscription_and_official_router_api_terms',
        'valid_from': '2026-09-09T15:00:00Z', 'valid_until': '2026-09-10T00:00:00Z',
        'historical_extra_cash_micro': None, 'new_cash_allowance_micro': 0}
    # Test-only explicit commissioning against fake Redis, never runtime setup.
    journal.RouterReviewJournal(case.client, clock=lambda: NOW).commission(policy)
    commission = Mock(side_effect=AssertionError('runtime must not commission'))
    monkeypatch.setattr(runtime.RouterReviewJournal, 'commission', commission)
    box = SimpleNamespace(case=case, config=config, foundation=foundation, getter=ledger_getter,
        forbidden=forbidden, commission=commission, calls=[], streams=[], prepared=[], policy=policy)
    def handler(request):
        box.calls.append(request)
        state = json.loads(case.client.get(journal.STATE_KEY))
        assert any(slot['response'] is None for slot in state['slots'].values())
        body = json.dumps(payload()).encode()
        stream = Chunks([body[:12], body[12:]])
        box.streams.append(stream)
        return httpx.Response(200, request=request, headers={'content-type': 'application/json'}, stream=stream)
    box.handler = handler
    def transport(**options):
        assert options == {'retries': 0, 'trust_env': False}
        return httpx.MockTransport(lambda request: box.handler(request))
    box.transport = Mock(side_effect=transport)
    monkeypatch.setattr(httpx, 'HTTPTransport', box.transport)
    return box


def originals(box):
    return {key: value for key, value in _dump(box.case.client).items()
            if key not in (journal.STATE_KEY, journal.JOURNAL_KEY, journal.ANCHOR_KEY)}


def test_actual_two_slot_success_records_before_send_returns_only_after_settlement(sandbox):
    box, before = sandbox, originals(sandbox)
    assert runtime.retained_router_review_active() is False
    assert runtime.retained_router_review_evidence() == {}
    with runtime.retained_router_review_scope(continuity.LEAF_ID) as scope:
        assert runtime.retained_router_review_active() is True
        for purpose in journal.PURPOSES:
            assert generate(purpose) == {'accepted': False}
            state = json.loads(box.case.client.get(journal.STATE_KEY))
            assert state['slots'][purpose]['response'] is not None
            evidence = runtime.retained_router_review_evidence()[purpose]
            assert evidence['returned_model'] == MODEL and evidence['underlying_model_verified'] is False
            assert evidence['usage'] is None
        assert scope.evidence == runtime.retained_router_review_evidence()
        public_copy = runtime.retained_router_review_evidence()
        public_copy[STORY]['underlying_model_verified'] = True
        assert runtime.retained_router_review_evidence()[STORY]['underlying_model_verified'] is False
    assert runtime.retained_router_review_active() is False and runtime.retained_router_review_evidence() == {}
    assert len(scope.evidence) == 2 and KEY not in repr(scope)
    assert len(box.calls) == 2 and all(stream.closed for stream in box.streams)
    for request in box.calls:
        assert request.method == 'POST' and str(request.url) == ENDPOINT
        assert request.headers['authorization'] == 'Bearer ' + KEY
        assert request.headers['accept-encoding'] == 'identity'
        assert not request.url.params and 'cookie' not in request.headers
        assert request.extensions['timeout'] == {'connect': 90.0, 'read': 90.0, 'write': 90.0, 'pool': 90.0}
    assert originals(box) == before and not box.forbidden.called and not box.commission.called
    raw = box.case.client.get(journal.STATE_KEY)
    assert KEY not in raw and 'PRIVATE' not in raw


def test_cash_foundation_stays_absent_and_unknown_during_included_review(sandbox):
    sandbox.case.client.delete(LEDGER_KEY)
    before = originals(sandbox)
    with runtime.retained_router_review_scope(continuity.LEAF_ID):
        assert generate() == {'accepted': False}
    assert sandbox.case.client.exists(LEDGER_KEY) == 0
    assert originals(sandbox) == before and len(sandbox.calls) == 1
    assert not sandbox.forbidden.called and not sandbox.commission.called
    state = json.loads(sandbox.case.client.get(journal.STATE_KEY))
    assert state['policy']['historical_extra_cash_micro'] is None
    assert state['policy']['new_cash_allowance_micro'] == 0


@pytest.mark.parametrize('source', [None, continuity.ROOT_ID, continuity.MIDDLE_ID,
                                   'other-owner-task', 1, False])
def test_only_exact_leaf_can_open_scope(sandbox, source):
    before = _dump(sandbox.case.client)
    with pytest.raises(SpendBlocked, match='source_not_supported'):
        with runtime.retained_router_review_scope(source): pass
    assert not sandbox.getter.called and not sandbox.transport.called
    assert _dump(sandbox.case.client) == before and runtime.retained_router_review_active() is False


@pytest.mark.parametrize('field,value', [('studio_spend_enforcement', False),
    ('studio_spend_enforcement', 1), ('studio_abacus_router_retained_review_enabled', False),
    ('studio_abacus_router_retained_review_enabled', 1), ('studio_abacus_router_retained_review_enabled', None)])
def test_both_explicit_boolean_flags_required_before_any_access(sandbox, field, value):
    setattr(sandbox.config, field, value)
    with pytest.raises(SpendBlocked, match='not_enabled'):
        with runtime.retained_router_review_scope(continuity.LEAF_ID): generate()
    assert not sandbox.getter.called and not sandbox.transport.called


@pytest.mark.parametrize('name', sorted(CAPS))
@pytest.mark.parametrize('value', [1, -1, False, 0.0, '0', None])
def test_all_six_cash_caps_must_be_exact_integer_zero(sandbox, name, value):
    sandbox.config.studio_spend_policy_json = json.dumps({**CAPS, name: value})
    before = _dump(sandbox.case.client)
    with pytest.raises(SpendBlocked, match='zero_cash_required'):
        with runtime.retained_router_review_scope(continuity.LEAF_ID): generate()
    assert not sandbox.transport.called and not sandbox.getter.called
    assert _dump(sandbox.case.client) == before


@pytest.mark.parametrize('raw', [None, '{}', '[]', '{', json.dumps({**CAPS, 'extra': 0}),
    '{"monthly_micro":9,' + json.dumps(CAPS)[1:], json.dumps({**CAPS, 'monthly_micro': float('nan')})])
def test_invalid_or_duplicate_policy_is_not_a_zero_cash_assumption(sandbox, raw):
    sandbox.config.studio_spend_policy_json = raw
    with pytest.raises(SpendBlocked):
        with runtime.retained_router_review_scope(continuity.LEAF_ID): pass
    assert not sandbox.getter.called and not sandbox.transport.called


def test_actual_configured_policy_must_also_have_zero_caps(sandbox):
    sandbox.foundation.policy = SpendPolicy(10, 0, 0, 0, 0, 0)
    with pytest.raises(SpendBlocked, match='zero_cash_required'):
        with runtime.retained_router_review_scope(continuity.LEAF_ID): pass
    assert not sandbox.transport.called


def test_outside_scope_nested_scope_and_finally_reset(sandbox):
    with pytest.raises(SpendBlocked, match='scope_required'): generate()
    assert not sandbox.getter.called
    with pytest.raises(ValueError, match='external error'):
        with runtime.retained_router_review_scope(continuity.LEAF_ID):
            with pytest.raises(SpendBlocked, match='scope_nested'):
                with runtime.retained_router_review_scope(continuity.LEAF_ID): pass
            assert runtime.retained_router_review_active() is True
            raise ValueError('external error')
    assert runtime.retained_router_review_active() is False and not sandbox.transport.called


def test_closed_copied_context_and_other_thread_cannot_revive_synchronous_scope(sandbox):
    with runtime.retained_router_review_scope(continuity.LEAF_ID):
        context = copy_context()
    def run_closed():
        assert runtime.retained_router_review_active() is True  # Never signal a paid fallback.
        with pytest.raises(SpendBlocked, match='scope_unusable'): generate()
    context.run(run_closed)
    failures = []
    with runtime.retained_router_review_scope(continuity.LEAF_ID):
        current = copy_context()
        def cross_thread():
            try: current.run(generate)
            except SpendBlocked as error: failures.append(str(error))
        thread = Thread(target=cross_thread)
        thread.start(); thread.join(timeout=5)
        assert not thread.is_alive() and failures == ['router_review_scope_unusable']
    assert not sandbox.transport.called


@pytest.mark.parametrize('invalidated', ['closed_copy', 'other_thread', 'failed_scope', 'flag_off'])
def test_typed_story_proof_requires_a_still_usable_actual_scope(sandbox, invalidated):
    from app.services import director
    package = {'title': 'Exact immutable input'}
    topic = 'Exact reviewed topic'
    with runtime.retained_router_review_scope(continuity.LEAF_ID):
        generate()
        evidence = runtime.retained_router_review_evidence()[STORY]
        package['stock_scene_qc'] = {'included_router_critic': evidence}
        director._INCLUDED_STORY_APPROVAL.set(director._IncludedStoryApproval(
            *director._included_story_hashes(package, topic), evidence['response_proof_sha256']))
        assert director._included_story_approval_matches(package, topic)
        context = copy_context()
        if invalidated == 'other_thread':
            values = []
            thread = Thread(target=lambda: values.append(context.run(
                director._included_story_approval_matches, package, topic)))
            thread.start(); thread.join(timeout=5)
            assert not thread.is_alive() and values == [False]
        elif invalidated == 'failed_scope':
            with pytest.raises(SpendBlocked):
                generate()  # Permanent first slot is already occupied; scope becomes terminal.
            assert runtime.retained_router_review_active()
            assert not director._included_story_approval_matches(package, topic)
        elif invalidated == 'flag_off':
            sandbox.config.studio_abacus_router_retained_review_enabled = False
            assert runtime.retained_router_review_active()
            assert not director._included_story_approval_matches(package, topic)
    assert not director._included_story_approval_matches(package, topic)
    if invalidated == 'closed_copy':
        assert context.run(runtime.retained_router_review_active)
        assert not context.run(director._included_story_approval_matches, package, topic)
    assert len(sandbox.calls) == 1


@pytest.mark.parametrize('purpose', ['video', 'voice', 'research', None, True])
def test_only_two_semantic_purposes_and_failed_scope_remains_active(sandbox, purpose):
    with runtime.retained_router_review_scope(continuity.LEAF_ID):
        with pytest.raises(SpendBlocked):
            runtime.generate_retained_router_review([{'type': 'text', 'text': 'test'}], purpose=purpose,
                system_instruction='Full review', json_schema=SCHEMA)
        assert runtime.retained_router_review_active() is True
        with pytest.raises(SpendBlocked, match='scope_unusable'): generate(VISUAL)
    assert not sandbox.transport.called


def test_same_scope_and_new_scope_cannot_replay_a_purpose(sandbox):
    with runtime.retained_router_review_scope(continuity.LEAF_ID):
        generate()
        with pytest.raises(SpendBlocked, match='scope_already_attempted'): generate()
        assert runtime.retained_router_review_active() is True
        with pytest.raises(SpendBlocked, match='scope_unusable'): generate(VISUAL)
    with runtime.retained_router_review_scope(continuity.LEAF_ID):
        with pytest.raises(SpendBlocked, match='already_reserved'): generate()
    assert len(sandbox.calls) == 1


@pytest.mark.parametrize('key', [journal.STATE_KEY, journal.JOURNAL_KEY, journal.ANCHOR_KEY])
def test_missing_journal_never_commissions_or_sends(sandbox, key):
    sandbox.case.client.delete(key)
    before = _dump(sandbox.case.client)
    with runtime.retained_router_review_scope(continuity.LEAF_ID):
        with pytest.raises(SpendBlocked): generate()
        assert runtime.retained_router_review_active() is True
    assert _dump(sandbox.case.client) == before and not sandbox.transport.called
    assert not sandbox.commission.called and not sandbox.forbidden.called


@pytest.mark.parametrize('mutate', [
    lambda box: setattr(box.config, 'abacus_api_key', 'other-real-config-key'),
    lambda box: box.case.client.set(continuity._AUTH_EPOCH, '13'),
    lambda box: setattr(box.foundation, 'clock', lambda: datetime(2026, 9, 10, tzinfo=timezone.utc)),
])
def test_actual_key_source_epoch_and_expiry_are_bound_before_send(sandbox, mutate):
    mutate(sandbox)
    with runtime.retained_router_review_scope(continuity.LEAF_ID):
        with pytest.raises(SpendBlocked): generate()
    assert not sandbox.transport.called


@pytest.mark.parametrize('change', ['key', 'flag', 'enforcement', 'cash'])
def test_configuration_drift_after_reservation_keeps_slot_and_prevents_send(sandbox, change):
    def after(number, ack):
        if number != 1: return ack
        if change == 'key': sandbox.config.abacus_api_key = 'new-config-account-key'
        elif change == 'flag': sandbox.config.studio_abacus_router_retained_review_enabled = False
        elif change == 'enforcement': sandbox.config.studio_spend_enforcement = False
        else: sandbox.config.studio_spend_policy_json = json.dumps({**CAPS, 'monthly_micro': 10})
        return ack
    sandbox.foundation.client = InterceptClient(sandbox.case.client, after=after)
    with runtime.retained_router_review_scope(continuity.LEAF_ID):
        with pytest.raises(SpendBlocked): generate()
        assert runtime.retained_router_review_active() is True
    state = json.loads(sandbox.case.client.get(journal.STATE_KEY))
    assert state['slots'][STORY]['response'] is None and not sandbox.transport.called


@pytest.mark.parametrize('operation', ['reserve', 'settle'])
def test_lost_exec_ack_never_returns_result_or_replays_post(sandbox, operation):
    def after(number, ack):
        if number == (1 if operation == 'reserve' else 2):
            raise ConnectionError('PRIVATE injected secret ' + KEY)
        return ack
    sandbox.foundation.client = InterceptClient(sandbox.case.client, after=after)
    with runtime.retained_router_review_scope(continuity.LEAF_ID):
        with pytest.raises(SpendBlocked) as caught: generate()
        assert KEY not in str(caught.value) and 'PRIVATE' not in str(caught.value)
        assert runtime.retained_router_review_evidence() == {}
        with pytest.raises(SpendBlocked, match='scope_unusable'):
            runtime.retained_router_review_artifacts()
        assert scope_capture_absent()
        assert runtime.retained_router_review_active() is True
        with pytest.raises(SpendBlocked, match='scope_unusable'): generate(VISUAL)
    state = json.loads(sandbox.case.client.get(journal.STATE_KEY))
    assert (state['slots'][STORY]['response'] is not None) is (operation == 'settle')
    sandbox.foundation.client = sandbox.case.client
    with runtime.retained_router_review_scope(continuity.LEAF_ID):
        with pytest.raises(SpendBlocked, match='already_reserved'): generate()
    assert len(sandbox.calls) == (0 if operation == 'reserve' else 1)


@pytest.mark.parametrize('damage', ['timeout', 'response_json', 'finish_length', 'header_key',
    'request_body', 'redirect', '429', '500', 'compressed', 'huge_length', 'duplicate_length'])
def test_single_terminal_transport_or_protocol_failure_never_falls_back(sandbox, damage):
    def handle(request):
        sandbox.calls.append(request)
        if damage == 'timeout': raise httpx.ReadTimeout('PRIVATE ' + KEY, request=request)
        result, status, headers = payload(), 200, [('content-type', 'application/json')]
        if damage == 'finish_length': result['choices'][0]['finish_reason'] = 'length'
        elif damage == 'header_key': request.headers['authorization'] = 'Bearer changed-on-wire'
        elif damage == 'request_body':
            body = json.loads(request.content); body['messages'][0]['content'] = 'Changed rubric'
            request._content = json.dumps(body).encode()
            request.headers['content-length'] = str(len(request.content))
        elif damage == 'redirect': status = 302; headers += [('location', 'https://attacker.invalid/private')]
        elif damage == '429': status = 429
        elif damage == '500': status = 500
        elif damage == 'compressed': headers += [('content-encoding', 'gzip')]
        elif damage == 'huge_length': headers += [('content-length', str(MAX_RESPONSE_BYTES + 1))]
        elif damage == 'duplicate_length': headers += [('content-length', '2'), ('Content-Length', '2')]
        body = b'PRIVATE ' + KEY.encode() if damage == 'response_json' else json.dumps(result).encode()
        stream = Chunks([body]); sandbox.streams.append(stream)
        return httpx.Response(status, request=request, headers=headers, stream=stream)
    sandbox.handler = handle
    with runtime.retained_router_review_scope(continuity.LEAF_ID):
        with pytest.raises(SpendBlocked) as caught: generate()
        assert KEY not in repr(caught.value) and 'PRIVATE' not in str(caught.value)
        assert runtime.retained_router_review_active() is True
        assert runtime.retained_router_review_evidence() == {}
        with pytest.raises(SpendBlocked): generate()
    assert len(sandbox.calls) == 1 and all(s.closed for s in sandbox.streams)
    assert json.loads(sandbox.case.client.get(journal.STATE_KEY))['slots'][STORY]['response'] is None
    assert not sandbox.forbidden.called


def test_stream_limit_is_measured_before_retaining_more_and_stops_reading(sandbox):
    chunk = b'x' * (64 * 1024)
    def handle(request):
        sandbox.calls.append(request)
        stream = Chunks([chunk] * 40)
        sandbox.streams.append(stream)
        return httpx.Response(200, request=request, headers={'content-type': 'application/json'}, stream=stream)
    sandbox.handler = handle
    with runtime.retained_router_review_scope(continuity.LEAF_ID):
        with pytest.raises(SpendBlocked, match='response_size_invalid'): generate()
    assert len(sandbox.calls) == 1 and sandbox.streams[0].yielded == 33
    assert sandbox.streams[0].closed


def test_schema_valid_negative_is_not_retried_or_promoted_and_evidence_remains_available(sandbox):
    with runtime.retained_router_review_scope(continuity.LEAF_ID):
        result = generate()
        assert result == {'accepted': False}
        assert runtime.retained_router_review_evidence()[STORY]['underlying_model_verified'] is False
        with pytest.raises(SpendBlocked): generate()
        assert STORY in runtime.retained_router_review_evidence()
    assert len(sandbox.calls) == 1


def test_whole_input_snapshot_is_unchanged_if_caller_mutates_after_reserve(sandbox):
    parts = [{'type': 'text', 'text': 'Original whole private scene'}]
    schema = deepcopy(SCHEMA)
    def after(number, ack):
        if number == 1:
            parts[0]['text'] = 'Changed private scene'
            schema['properties']['accepted']['type'] = 'string'
        return ack
    sandbox.foundation.client = InterceptClient(sandbox.case.client, after=after)
    with runtime.retained_router_review_scope(continuity.LEAF_ID):
        assert runtime.generate_retained_router_review(parts, purpose=STORY, system_instruction='Original rubric',
            json_schema=schema) == {'accepted': False}
    sent = json.loads(sandbox.calls[0].content)
    assert sent['messages'][1]['content'][0]['text'] == 'Original whole private scene'
    assert sent['response_format']['json_schema']['schema'] == SCHEMA


def test_no_financial_initializer_context_resolution_sender_injection_or_retry_loop():
    tree = ast.parse(Path(runtime.__file__).read_text())
    forbidden = {'initialize', 'commission', 'resolve_context', 'paid_post', 'paid_response', 'reserve_request'}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ''
            assert name not in forbidden
        if isinstance(node, ast.FunctionDef) and node.name == 'generate_retained_router_review':
            assert not any(arg.arg in {'sender', 'client', 'journal', 'api_key', 'account'}
                           for arg in node.args.args + node.args.kwonlyargs)
            assert not any(isinstance(child, (ast.For, ast.While)) for child in ast.walk(node))


def scope_capture_absent():
    return runtime._SCOPE.get()._artifacts == {}


def test_full_capture_keeps_actual_bytes_receipt_and_negative_result_without_secrets(sandbox):
    before = originals(sandbox)
    with runtime.retained_router_review_scope(continuity.LEAF_ID):
        assert runtime.retained_router_review_artifacts() == {}
        for purpose in journal.PURPOSES:
            result = generate(purpose)
            artifact = runtime.retained_router_review_artifacts()[purpose]
            assert type(artifact) is runtime.AcknowledgedRouterReview
            assert artifact.purpose == purpose
            assert artifact.result == result == {'accepted': False}
            assert artifact.evidence == runtime.retained_router_review_evidence()[purpose]
            assert artifact.response_status_code == 200
            assert artifact.request_body_bytes == sandbox.calls[-1].content
            assert json.loads(artifact.prepared_body_bytes) == json.loads(artifact.request_body_bytes)
            assert artifact.response_body_bytes == b''.join(sandbox.streams[-1].chunks)
            evidence = artifact.evidence
            assert hashlib.sha256(artifact.request_body_bytes).hexdigest() == evidence['wire_body_sha256']
            assert hashlib.sha256(artifact.response_body_bytes).hexdigest() == evidence['response_body_sha256']
            assert hashlib.sha256(runtime._canonical(artifact.result)).hexdigest() == evidence['parsed_result_sha256']
            receipt = artifact.reservation
            stored = json.loads(sandbox.case.client.hget(journal.JOURNAL_KEY, 'reservation:' + purpose))
            assert receipt == {**stored, 'reservation_sha256': journal._hash(stored)}
            state = json.loads(sandbox.case.client.get(journal.STATE_KEY))
            assert state['slots'][purpose]['response']['reservation_sha256'] == receipt['reservation_sha256']
            assert artifact.diagnostic_only is True
            assert artifact.qa_approved is False and artifact.publish_eligible is False
            assert all(type(value) in (str, tuple) for value in (artifact._purpose, artifact._snapshot))
            assert all(type(value) in (bytes, int) for value in artifact._snapshot)
            assert not any(hasattr(artifact, name) for name in ('headers', 'request', 'response', 'prepared', '__dict__'))
            assert KEY not in repr(artifact) and 'PRIVATE' not in repr(artifact)
            assert KEY.encode() not in b''.join(x for x in artifact._snapshot if type(x) is bytes)
        assert len(runtime.retained_router_review_artifacts()) == 2
    assert originals(sandbox) == before and len(sandbox.calls) == 2
    assert not sandbox.forbidden.called and not sandbox.commission.called


def test_capture_is_absent_until_both_actual_transaction_acks(sandbox):
    snapshots = []
    def after(number, ack):
        snapshots.append((number, runtime.retained_router_review_artifacts()))
        return ack
    sandbox.foundation.client = InterceptClient(sandbox.case.client, after=after)
    with runtime.retained_router_review_scope(continuity.LEAF_ID):
        generate()
        assert snapshots == [(1, {}), (2, {})]
        assert set(runtime.retained_router_review_artifacts()) == {STORY}


def test_wire_whitespace_and_response_bytes_are_not_reencoded(sandbox):
    def handle(request):
        sandbox.calls.append(request)
        request._content = json.dumps(json.loads(request.content), ensure_ascii=False, indent=3).encode()
        request.headers['content-length'] = str(len(request.content))
        body = (' \n' + json.dumps(payload(), indent=2) + '\n ').encode()
        stream = Chunks([body]); sandbox.streams.append(stream)
        return httpx.Response(200, request=request, headers={
            'content-type': 'application/json', 'x-secret-private-header': KEY,
            'set-cookie': 'private=' + KEY}, stream=stream)
    sandbox.handler = handle
    with runtime.retained_router_review_scope(continuity.LEAF_ID):
        generate()
        artifact = runtime.retained_router_review_artifacts()[STORY]
        assert artifact.prepared_body_bytes != artifact.request_body_bytes
        assert artifact.request_body_bytes == sandbox.calls[0].content
        assert artifact.response_body_bytes == sandbox.streams[0].chunks[0]
        assert KEY.encode() not in b''.join(x for x in artifact._snapshot if type(x) is bytes)
        assert b'x-secret-private-header' not in repr(artifact._snapshot).encode()
        assert b'set-cookie' not in repr(artifact._snapshot).encode()


def test_capture_copies_and_mutable_transport_objects_cannot_change_saved_evidence(sandbox, monkeypatch):
    responses = []
    original_settle = runtime.RouterReviewJournal.settle
    def settle(self, purpose, prepared, response):
        responses.append(response)
        return original_settle(self, purpose, prepared, response)
    monkeypatch.setattr(runtime.RouterReviewJournal, 'settle', settle)
    with runtime.retained_router_review_scope(continuity.LEAF_ID):
        returned = generate()
        wrapper_map = runtime.retained_router_review_artifacts()
        artifact = wrapper_map[STORY]
        saved = artifact._snapshot
        returned['accepted'] = True
        artifact.result['accepted'] = True
        artifact.evidence['usage'] = {'total_tokens': 999}
        artifact.reservation['purpose'] = VISUAL
        wrapper_map.clear()
        sandbox.calls[0]._content = b'mutated wire body'
        responses[0]._content = b'mutated response body'
        responses[0].headers['private'] = KEY
        assert artifact.result == {'accepted': False}
        assert artifact.evidence['usage'] is None and artifact.reservation['purpose'] == STORY
        assert artifact._snapshot == saved
        assert runtime.retained_router_review_artifacts()[STORY]._snapshot is saved
        with pytest.raises(FrozenInstanceError):
            artifact._snapshot = ()
        with pytest.raises(TypeError, match='artifact_private'):
            runtime.AcknowledgedRouterReview()
        # Even an explicit bypass of frozen dataclass assignment cannot replace
        # the authoritative tuple held by the active scope.
        object.__setattr__(artifact, '_snapshot', (*saved[:2], b'forged', *saved[3:]))
        with pytest.raises(SpendBlocked, match='artifact_unverified'):
            _ = artifact.response_body_bytes
        assert runtime.retained_router_review_artifacts()[STORY].response_body_bytes == saved[2]


@pytest.mark.parametrize('invalidation', ['closed', 'failed', 'thread', 'new_scope', 'flag', 'enforcement', 'cash'])
def test_capture_and_retained_wrapper_require_original_usable_owner_scope(sandbox, invalidation):
    with pytest.raises(SpendBlocked, match='scope_required'):
        runtime.retained_router_review_artifacts()
    with runtime.retained_router_review_scope(continuity.LEAF_ID):
        generate()
        artifact = runtime.retained_router_review_artifacts()[STORY]
        context = copy_context()
        def denied():
            with pytest.raises(SpendBlocked): runtime.retained_router_review_artifacts()
            with pytest.raises(SpendBlocked): _ = artifact.result
        if invalidation == 'failed':
            with pytest.raises(SpendBlocked): generate()
            denied()
        elif invalidation == 'thread':
            failures = []
            def other_thread():
                try: context.run(denied)
                except BaseException as error: failures.append(error)
            thread = Thread(target=other_thread)
            thread.start(); thread.join(timeout=5)
            assert not thread.is_alive() and failures == []
            assert artifact.result == {'accepted': False}
        elif invalidation in {'flag', 'enforcement', 'cash'}:
            if invalidation == 'flag': sandbox.config.studio_abacus_router_retained_review_enabled = False
            elif invalidation == 'enforcement': sandbox.config.studio_spend_enforcement = False
            else: sandbox.config.studio_spend_policy_json = json.dumps({**CAPS, 'daily_micro': 1})
            denied()
    if invalidation == 'closed': context.run(denied)
    with pytest.raises(SpendBlocked): _ = artifact.response_body_bytes
    if invalidation == 'new_scope':
        with runtime.retained_router_review_scope(continuity.LEAF_ID):
            assert runtime.retained_router_review_artifacts() == {}
            with pytest.raises(SpendBlocked, match='artifact_unverified'): _ = artifact.result
    assert len(sandbox.calls) == 1


@pytest.mark.parametrize('damage', ['none', 'extra_header', 'missing', 'purpose', 'request', 'fingerprint',
                                  'hash', 'invalid_date', 'date_type', 'policy_type'])
def test_bad_reserve_receipt_is_terminal_before_any_send(sandbox, monkeypatch, damage):
    original = runtime.RouterReviewJournal.reserve
    def reserve(self, purpose, prepared):
        receipt = original(self, purpose, prepared)
        if damage == 'none': return None
        if damage == 'extra_header': receipt['authorization'] = 'Bearer ' + KEY
        elif damage == 'missing': receipt.pop('policy_sha256')
        elif damage == 'purpose': receipt['purpose'] = VISUAL
        elif damage == 'request': receipt['request_sha256'] = 'a' * 64
        elif damage == 'fingerprint': receipt['root_request_fingerprint'] = 'b' * 64
        elif damage == 'hash': receipt['reservation_sha256'] = 'c' * 64
        elif damage == 'invalid_date': receipt['reserved_at'] = '2026-99-99T16:00:00Z'
        elif damage == 'date_type': receipt['reserved_at'] = 1
        elif damage == 'policy_type': receipt['policy_sha256'] = True
        if damage in {'purpose', 'request', 'fingerprint', 'invalid_date'}:
            receipt['reservation_sha256'] = journal._hash({k: v for k, v in receipt.items() if k != 'reservation_sha256'})
        return receipt
    monkeypatch.setattr(runtime.RouterReviewJournal, 'reserve', reserve)
    with runtime.retained_router_review_scope(continuity.LEAF_ID):
        with pytest.raises(SpendBlocked) as caught: generate()
        assert KEY not in str(caught.value) and scope_capture_absent()
        with pytest.raises(SpendBlocked): runtime.retained_router_review_artifacts()
    assert not sandbox.calls and not sandbox.transport.called
    assert json.loads(sandbox.case.client.get(journal.STATE_KEY))['slots'][STORY]['response'] is None


@pytest.mark.parametrize('damage', ['result', 'evidence', 'wire_after', 'body_after', 'body_before', 'status'])
def test_settlement_ack_without_same_actual_received_bytes_cannot_create_capture(sandbox, monkeypatch, damage):
    original = runtime.RouterReviewJournal.settle
    def settle(self, purpose, prepared, response):
        if damage == 'body_before':
            changed = payload(); changed['choices'][0]['message']['content'] = '{"accepted":true}'
            response._content = json.dumps(changed).encode()
        observed = original(self, purpose, prepared, response)
        if damage == 'result': return ObservedRouterResult(b'{"accepted":true}', observed._evidence_bytes)
        if damage == 'evidence': return ObservedRouterResult(observed._result_bytes, b'{}')
        if damage == 'wire_after':
            response.request._content += b' '
            response.request.headers['content-length'] = str(len(response.request.content))
        if damage == 'body_after': response._content += b' '
        if damage == 'status': response.status_code = 201
        return observed
    monkeypatch.setattr(runtime.RouterReviewJournal, 'settle', settle)
    with runtime.retained_router_review_scope(continuity.LEAF_ID):
        with pytest.raises(SpendBlocked): generate()
        assert scope_capture_absent()
        with pytest.raises(SpendBlocked): runtime.retained_router_review_artifacts()
        with pytest.raises(SpendBlocked): generate(VISUAL)
    assert len(sandbox.calls) == 1
    assert json.loads(sandbox.case.client.get(journal.STATE_KEY))['slots'][STORY]['response'] is not None


@pytest.mark.parametrize('operation', ['reserve', 'settle'])
def test_second_purpose_lost_ack_hides_prior_capture_without_retry(sandbox, operation):
    def after(number, ack):
        if number == (3 if operation == 'reserve' else 4): raise ConnectionError('PRIVATE ' + KEY)
        return ack
    sandbox.foundation.client = InterceptClient(sandbox.case.client, after=after)
    with runtime.retained_router_review_scope(continuity.LEAF_ID):
        generate()
        artifact = runtime.retained_router_review_artifacts()[STORY]
        with pytest.raises(SpendBlocked): generate(VISUAL)
        with pytest.raises(SpendBlocked): runtime.retained_router_review_artifacts()
        with pytest.raises(SpendBlocked): _ = artifact.result
        with pytest.raises(SpendBlocked): generate(VISUAL)
        assert set(runtime.retained_router_review_evidence()) == {STORY}
    assert len(sandbox.calls) == (1 if operation == 'reserve' else 2)


@pytest.mark.parametrize('location', ['input', 'response', 'escaped_response', 'nested_escaped_response'])
def test_exact_capture_refuses_credential_echo_without_redacting_or_resending(sandbox, location):
    def handle(request):
        sandbox.calls.append(request)
        value = payload()
        value['choices'][0]['message']['content'] = json.dumps({'accepted': False, 'reason': KEY})
        if location == 'nested_escaped_response':
            value['choices'][0]['message']['content'] = value['choices'][0]['message']['content'].replace(
                KEY, ''.join('\\u%04x' % ord(char) for char in KEY))
        raw = json.dumps(value).encode()
        if location == 'escaped_response':
            raw = raw.replace(KEY.encode(), ''.join('\\u%04x' % ord(char) for char in KEY).encode())
        stream = Chunks([raw]); sandbox.streams.append(stream)
        return httpx.Response(200, request=request, headers={'content-type': 'application/json'}, stream=stream)
    if location != 'input': sandbox.handler = handle
    schema = deepcopy(SCHEMA)
    if location != 'input':
        schema['properties']['reason'] = {'type': 'string'}
        schema['required'].append('reason')
    with runtime.retained_router_review_scope(continuity.LEAF_ID):
        with pytest.raises(SpendBlocked, match='artifact_credential_present') as caught:
            generate(system_instruction=KEY if location == 'input' else 'Full rubric', json_schema=schema)
        assert KEY not in str(caught.value) and scope_capture_absent()
        with pytest.raises(SpendBlocked): runtime.retained_router_review_artifacts()
        with pytest.raises(SpendBlocked): generate()
    assert len(sandbox.calls) == 1
