"""Fixed diagnostic request and genuine central admission, entirely offline."""
import hashlib
import json
import socket
from copy import copy, deepcopy
import base64
from dataclasses import FrozenInstanceError
from contextvars import copy_context
from threading import Thread
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from cryptography.fernet import InvalidToken
from redis.exceptions import ConnectionError

from app.services import abacus_router_adapter as adapter
from app.services import abacus_router_protocol_diagnostic as diagnostic
from app.services import retained_router_protocol_probe as probe
from app.services import abacus_router_audio_adapter as audio_adapter
from app.services import abacus_router_review_journal as story_journal
from app.services import abacus_router_audio_review_journal as audio_journal
from app.services import production_connection_continuity as continuity
from test_retained_router_protocol_probe import (
    frozen_three, scope, assert_old_unchanged, prepared, case, source,
)
from test_retained_review_credential_successor import Intercept


KEY = 'offline-protocol-diagnostic-key-491e26'
APP_KEY = 'offline-protocol-encryption-material-376f96'
PRIVATE = 'PRIVATE_PROVIDER_TEXT_HEADER_AND_EXCEPTION_88c4a0'
REAL_CLIENT = httpx.Client


@pytest.fixture
def config(monkeypatch):
    value = SimpleNamespace(abacus_api_key=KEY, app_encryption_key=APP_KEY)
    monkeypatch.setattr(diagnostic.spending, 'settings', value)
    return value


@pytest.fixture(autouse=True)
def forbid_live_transport(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Only explicitly installed offline HTTPX transport is allowed.')
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)


@pytest.fixture(autouse=True)
def forbid_semantic_observation_and_settlement(monkeypatch):
    blocked = Mock(side_effect=AssertionError('A diagnostic cannot observe or settle a review.'))
    monkeypatch.setattr(adapter, 'observe_router_response', blocked)
    monkeypatch.setattr(audio_adapter, 'observe_audio_router_response', blocked)
    monkeypatch.setattr(story_journal.RouterReviewJournal, 'settle', blocked)
    monkeypatch.setattr(audio_journal.RouterAudioReviewJournal, 'settle', blocked)
    yield
    blocked.assert_not_called()


class ResponseStream(httpx.SyncByteStream):
    def __init__(self, chunks, error=None):
        self.chunks, self.error = chunks, error
        self.closed = False

    def __iter__(self):
        yield from self.chunks
        if self.error is not None:
            raise self.error

    def close(self):
        self.closed = True


@pytest.fixture
def wire(monkeypatch):
    box = SimpleNamespace(status=200, chunks=[b'{"ok":true}'], error=None,
        transport_error=None, headers={'content-type': 'application/json'},
        calls=[], streams=[], transport_options=[], client_options=[], on_request=None)

    def handle(request):
        box.calls.append(request)
        if box.on_request:
            box.on_request(request)
        if box.transport_error:
            raise box.transport_error
        stream = ResponseStream(box.chunks, box.error)
        box.streams.append(stream)
        return httpx.Response(box.status, headers=box.headers, stream=stream, request=request)

    def transport(**kwargs):
        box.transport_options.append(kwargs)
        return httpx.MockTransport(handle)

    def client(**kwargs):
        box.client_options.append(kwargs)
        assert type(kwargs['transport']) is httpx.MockTransport
        return REAL_CLIENT(**kwargs)

    monkeypatch.setattr(httpx, 'HTTPTransport', transport)
    monkeypatch.setattr(httpx, 'Client', client)
    return box


def decrypted(record, material):
    encrypted = record['encrypted_response']
    token = encrypted['ciphertext'].encode('ascii')
    assert encrypted['sha256'] == hashlib.sha256(token).hexdigest()
    assert encrypted['bytes'] == len(token)
    packet = diagnostic._fernet(material).decrypt(token)
    assert packet.startswith(diagnostic._DOMAIN)
    offset = len(diagnostic._DOMAIN)
    length = int.from_bytes(packet[offset:offset + 4], 'big')
    context = json.loads(packet[offset + 4:offset + 4 + length])
    body = packet[offset + 4 + length:]
    assert context['captured_body_sha256'] == hashlib.sha256(body).hexdigest()
    assert context['summary'] == record['summary']
    assert context['permit_binding'] == record['permit_binding']
    return context, body


def test_fixed_request_has_only_tiny_closed_boolean_contract(config):
    prepared = diagnostic._prepare_probe()
    assert type(prepared) is adapter.PreparedRouterRequest
    assert prepared.endpoint == adapter.ENDPOINT
    assert prepared.payload == {
        'model': 'route-llm',
        'messages': [
            {'role': 'system', 'content': 'Return only the requested JSON object.'},
            {'role': 'user', 'content': [{'type': 'text', 'text': 'Return {"ok":true}.'}]},
        ],
        'response_format': {'type': 'json_schema', 'json_schema': {
            'name': 'youtube_review', 'strict': True,
            'schema': {'type': 'object', 'properties': {'ok': {'type': 'boolean'}},
                       'required': ['ok'], 'additionalProperties': False},
        }},
        'max_tokens': 32, 'stream': False, 'modalities': ['text'],
    }
    assert KEY not in repr(prepared)


def test_builder_returns_detached_request_and_binds_current_credential(config):
    first = diagnostic._prepare_probe()
    original = first.payload
    external = first.wire_kwargs()
    external['json']['max_tokens'] = 8192
    external['json']['messages'][1]['content'][0]['text'] = 'arbitrary instruction'
    external['headers']['authorization'] = 'Bearer changed-key'
    assert first.payload == original
    config.abacus_api_key = 'offline-protocol-second-key-584d82'
    second = diagnostic._prepare_probe()
    assert first.payload == second.payload == original
    assert first.request_sha256 == second.request_sha256
    assert first.credential_sha256 != second.credential_sha256


def test_root_summary_exposes_only_fixed_names_kinds_and_counts():
    private = 'PRIVATE_PROVIDER_VALUE_AND_FIELD_71fc0d'
    raw = json.dumps({'id': private, 'object': private, 'model': private,
                      'choices': [{'message': private}], private: private,
                      'error': {'message': private}}).encode()
    result = diagnostic._root_shape(raw)
    assert result == {
        'json_kind': 'object', 'key_count': 6, 'required_missing': [],
        'unknown_key_count': 1,
        'known_field_kinds': {'id': 'string', 'object': 'string', 'model': 'string',
                              'choices': 'array', 'error': 'object'},
    }
    assert private not in json.dumps(result)
    result['known_field_kinds']['id'] = private
    assert diagnostic._root_shape(raw)['known_field_kinds']['id'] == 'string'


def test_root_summary_distinguishes_missing_envelope_from_inner_result():
    assert diagnostic._root_shape(b'{"ok":true}') == {
        'json_kind': 'object', 'key_count': 1,
        'required_missing': ['id', 'object', 'model', 'choices'],
        'unknown_key_count': 1, 'known_field_kinds': {},
    }


def test_service_tier_summary_reports_only_fixed_field_kind():
    result = diagnostic._root_shape(json.dumps({'service_tier': PRIVATE}).encode())
    assert result['known_field_kinds'] == {'service_tier': 'string'}
    assert result['key_count'] == 1 and result['unknown_key_count'] == 0
    assert result['required_missing'] == ['id', 'object', 'model', 'choices']
    assert PRIVATE not in json.dumps(result)


@pytest.mark.parametrize(('raw', 'kind'), [
    (b'[]', 'array'), (b'"private provider text"', 'string'), (b'3', 'integer'),
    (b'3.5', 'number'), (b'true', 'boolean'), (b'null', 'null'),
])
def test_non_object_json_summary_has_no_semantic_approval(raw, kind):
    assert diagnostic._root_shape(raw) == {
        'json_kind': kind, 'key_count': None, 'required_missing': None,
        'unknown_key_count': None, 'known_field_kinds': {},
    }


@pytest.mark.parametrize('raw', [
    b'', b'not json PRIVATE', b'\xff', b'{"id":1,"id":2}', b'{"id":NaN}',
    b' ' * (diagnostic.MAX_RESPONSE_BYTES + 1),
])
def test_invalid_duplicate_or_oversized_json_has_fixed_summary(raw):
    result = diagnostic._root_shape(raw)
    assert result == {'json_kind': 'invalid', 'key_count': None,
                      'required_missing': None, 'unknown_key_count': None,
                      'known_field_kinds': {}}


def test_partial_body_never_looks_like_a_complete_json_result():
    assert diagnostic._root_shape(b'{"ok":true}', complete=False) == {
        'json_kind': 'unavailable', 'key_count': None, 'required_missing': None,
        'unknown_key_count': None, 'known_field_kinds': {},
    }


def stored_capture(box):
    return json.loads(box.client.get(probe.PROBE_KEYS[0]))['capture']


@pytest.mark.parametrize('status', [200, 401, 302, 503])
def test_real_permit_captures_all_statuses_once_without_semantic_admission(frozen_three, wire, status):
    box = frozen_three
    wire.status = status
    wire.headers.update({'location': 'https://must-not-follow.invalid/' + PRIVATE,
                         'x-provider-private': PRIVATE})
    with scope(box) as permit:
        result = diagnostic.run_protocol_diagnostic(permit)
        binding = permit.safe_binding
        assert type(result) is diagnostic.ProtocolDiagnosticCapture
        assert result.summary['capture_acknowledged'] is True
        assert result.summary['http_status'] == status
        assert result.summary['body_state'] == 'complete'
        assert result.summary['body_complete'] is True
        assert result.summary['root']['required_missing'] == ['id', 'object', 'model', 'choices']
        assert result.receipt['capture_record_acknowledged'] is True
        assert result.receipt['occupied_count'] == 4
        assert result.receipt['prior_unknown_count'] == 3
        for name, expected in diagnostic._FLAGS.items():
            assert result.summary[name] is expected
        with pytest.raises(diagnostic.RouterProtocolDiagnosticError):
            diagnostic.run_protocol_diagnostic(permit)
    record = stored_capture(box)
    context, body = decrypted(record, box.config.app_encryption_key)
    assert body == b'{"ok":true}'
    assert context['permit_binding'] == binding
    assert context['endpoint'] == adapter.ENDPOINT
    assert len(wire.calls) == 1 and all(stream.closed for stream in wire.streams)
    request = wire.calls[0]
    assert request.method == 'POST' and str(request.url) == adapter.ENDPOINT
    assert request.headers['authorization'] == 'Bearer ' + box.config.abacus_api_key
    assert request.headers['accept-encoding'] == 'identity' and 'cookie' not in request.headers
    assert context['wire_body_sha256'] == hashlib.sha256(request.content).hexdigest()
    assert base64.b64decode(context['wire_body_base64'], validate=True) == request.content
    assert base64.b64decode(context['prepared_body_base64'], validate=True) == diagnostic._raw(
        json.loads(request.content))
    assert wire.transport_options == [{'retries': 0, 'trust_env': False}]
    options = wire.client_options[0]
    assert options['trust_env'] is options['follow_redirects'] is False
    assert options['auth'] is options['cookies'] is None
    assert options['event_hooks'] == {'request': [], 'response': []}
    assert all(box.client.pttl(key) == -1 for key in probe.PROBE_KEYS)
    assert_old_unchanged(box)
    assert PRIVATE not in json.dumps(record)
    assert box.config.abacus_api_key not in repr(result) + json.dumps(result.summary)


@pytest.mark.parametrize('encoding', ['literal', 'json_escape', 'nested_escape'])
def test_credential_echo_is_retained_only_inside_bound_app_encryption(frozen_three, wire, encoding):
    box = frozen_three
    key = box.config.abacus_api_key
    escaped = ''.join('\\u%04x' % ord(char) for char in key)
    if encoding == 'literal':
        body = json.dumps({'error': key, PRIVATE: key}).encode()
    elif encoding == 'json_escape':
        body = ('{"error":"' + escaped + '"}').encode()
    else:
        body = json.dumps({'choices': [{'message': {'content': '{"echo":"' + escaped + '"}'}}]}).encode()
    wire.chunks = [body]
    wire.headers['x-private-key-echo'] = key
    with scope(box) as permit:
        result = diagnostic.run_protocol_diagnostic(permit)
    record = stored_capture(box)
    context, actual = decrypted(record, box.config.app_encryption_key)
    assert actual == body
    assert context['request_sha256'] == record['request_sha256']
    assert context['credential_sha256'] == record['credential_sha256']
    visible = json.dumps(record) + json.dumps(result.summary) + repr(result) + json.dumps(result.receipt)
    assert key not in visible and escaped not in visible and PRIVATE not in visible
    assert box.config.app_encryption_key not in visible
    assert 'authorization' not in json.dumps(context).lower()
    assert 'x-private-key-echo' not in json.dumps(context).lower()
    with pytest.raises(InvalidToken):
        diagnostic._fernet('different-application-encryption-key').decrypt(
            record['encrypted_response']['ciphertext'].encode())
    assert_old_unchanged(box)


@pytest.mark.parametrize('failure', ['exact_limit', 'overflow', 'stream', 'transport'])
def test_bounded_or_failed_transport_is_honestly_encrypted_and_never_retried(frozen_three, wire, failure):
    maximum = diagnostic.MAX_RESPONSE_BYTES
    if failure == 'exact_limit':
        wire.chunks = [b'x' * maximum]
        expected, state = b'x' * maximum, 'complete'
    elif failure == 'overflow':
        wire.chunks = [b'x' * maximum, b'y']
        expected, state = b'x' * maximum, 'body_limit'
    elif failure == 'stream':
        wire.chunks, wire.error = [b'x' * 4096], RuntimeError(PRIVATE)
        expected, state = b'x' * 4096, 'stream_error'
    else:
        wire.transport_error = RuntimeError(PRIVATE)
        expected, state = b'', 'transport_error'
    with scope(frozen_three) as permit:
        result = diagnostic.run_protocol_diagnostic(permit)
        with pytest.raises(diagnostic.RouterProtocolDiagnosticError):
            diagnostic.run_protocol_diagnostic(permit)
    record = stored_capture(frozen_three)
    context, actual = decrypted(record, frozen_three.config.app_encryption_key)
    assert actual == expected
    assert result.summary['captured_bytes'] == len(expected) <= maximum
    assert result.summary['body_state'] == state
    assert result.summary['body_complete'] is (failure == 'exact_limit')
    if failure != 'exact_limit':
        assert result.summary['root']['json_kind'] == 'unavailable'
    if failure == 'transport':
        assert result.summary['http_status'] is None
        assert result.summary['wire_request_verified'] is False
        assert context['wire_body_sha256'] is None
        assert context['wire_body_base64'] is context['prepared_body_base64'] is None
    assert len(wire.calls) == 1 and all(stream.closed for stream in wire.streams)
    assert PRIVATE not in json.dumps(record) + json.dumps(result.summary)
    assert_old_unchanged(frozen_three)


def test_compressed_bytes_are_preserved_without_claiming_decoded_json(frozen_three, wire):
    import gzip
    original = b'{"ok":true}'
    wire.chunks = [gzip.compress(original)]
    wire.headers['content-encoding'] = 'gzip'
    with scope(frozen_three) as permit:
        result = diagnostic.run_protocol_diagnostic(permit)
    assert result.summary['content_encoding_kind'] == 'other'
    assert result.summary['root']['json_kind'] == 'unavailable'
    assert decrypted(stored_capture(frozen_three), frozen_three.config.app_encryption_key)[1] == wire.chunks[0]


def test_key_and_source_drift_after_actual_send_preserves_only_original_bound_diagnostic(frozen_three, wire):
    box = frozen_three
    original_key = box.config.abacus_api_key
    original_material = box.config.app_encryption_key
    original_credential_sha = hashlib.sha256(('abacus\0' + original_key).encode()).hexdigest()
    wire.chunks = [json.dumps({'error': original_key}).encode()]
    profile_key = continuity._PROFILE + continuity.CHANNEL_ID
    def drift_after_send(request):
        assert request.headers['authorization'] == 'Bearer ' + original_key
        box.config.abacus_api_key = 'offline-key-changed-after-send-937a2b'
        profile = json.loads(box.client.get(profile_key))
        profile['revision'] = 'changed-after-send'
        box.client.set(profile_key, json.dumps(profile))
    wire.on_request = drift_after_send
    with scope(box) as permit:
        original_binding = permit.safe_binding
        result = diagnostic.run_protocol_diagnostic(permit)
        with pytest.raises(diagnostic.RouterProtocolDiagnosticError):
            diagnostic.run_protocol_diagnostic(permit)
    record = stored_capture(box)
    context, body = decrypted(record, original_material)
    assert context['permit_binding'] == original_binding
    assert context['credential_sha256'] == original_credential_sha
    assert body == wire.chunks[0]
    assert base64.b64decode(context['wire_body_base64'], validate=True) == wire.calls[0].content
    assert result.summary['capture_acknowledged'] is True
    assert result.summary['qa_approved'] is result.summary['semantic_observation_verified'] is False
    assert result.summary['retry_authorized'] is False
    assert len(wire.calls) == 1
    assert_old_unchanged(box)


def test_genuine_capture_cannot_be_copied_forged_or_mutated_into_acknowledged_evidence(frozen_three, wire):
    with scope(frozen_three) as permit:
        result = diagnostic.run_protocol_diagnostic(permit)
        with pytest.raises(TypeError):
            diagnostic.ProtocolDiagnosticCapture()
        with pytest.raises((TypeError, diagnostic.RouterProtocolDiagnosticError)):
            copy(result).summary
        forged = object.__new__(diagnostic.ProtocolDiagnosticCapture)
        for name in ('_record_bytes', '_permit', '_owner_thread', '_seal', '_receipt_bytes'):
            object.__setattr__(forged, name, getattr(result, name))
        with pytest.raises(diagnostic.RouterProtocolDiagnosticError):
            _ = forged.summary
        with pytest.raises(FrozenInstanceError):
            result._receipt_bytes = b'{}'
        for field, altered in (('_record_bytes', b'{}'), ('_receipt_bytes', b'{}'), ('_permit', object())):
            original = getattr(result, field)
            object.__setattr__(result, field, altered)
            try:
                with pytest.raises(diagnostic.RouterProtocolDiagnosticError):
                    _ = result.summary
                with pytest.raises(diagnostic.RouterProtocolDiagnosticError):
                    diagnostic._capture_record(result, permit=permit,
                        request_sha256=permit.safe_binding['request_sha256'],
                        credential_sha256=permit.safe_binding['credential_sha256'])
            finally:
                object.__setattr__(result, field, original)
        assert result.summary['capture_acknowledged'] is True
        external = result.summary
        external['qa_approved'] = True
        assert result.summary['qa_approved'] is False
    assert len(wire.calls) == 1


def test_invalid_or_wrong_thread_permits_never_reach_transport(frozen_three, wire):
    for invalid in (None, {}, object.__new__(probe.ProtocolProbePermit)):
        with pytest.raises(diagnostic.RouterProtocolDiagnosticError):
            diagnostic.run_protocol_diagnostic(invalid)
    with scope(frozen_three) as permit:
        context, errors = copy_context(), []
        def wrong_thread():
            try:
                context.run(diagnostic.run_protocol_diagnostic, permit)
            except diagnostic.RouterProtocolDiagnosticError:
                errors.append(True)
        worker = Thread(target=wrong_thread)
        worker.start(); worker.join(timeout=5)
        assert not worker.is_alive() and errors == [True]
    with pytest.raises(diagnostic.RouterProtocolDiagnosticError):
        diagnostic.run_protocol_diagnostic(permit)
    assert wire.calls == []
    assert stored_capture(frozen_three) is None
    assert_old_unchanged(frozen_three)


@pytest.mark.parametrize('fault', ['lost_write_ack', 'malformed_write_ack', 'lost_read_ack'])
def test_capture_ack_failure_keeps_only_encrypted_fallback_and_never_resends(frozen_three, wire, fault):
    box = frozen_three
    enabled, wrote = [False], [False]
    def after(commands, result):
        if enabled[0] and commands == ('SET', 'HSET', 'SET'):
            wrote[0] = True
            if fault == 'lost_write_ack':
                raise ConnectionError(PRIVATE)
            if fault == 'malformed_write_ack':
                return [1, 0, True]
        elif enabled[0] and wrote[0] and commands == ('PING',) and fault == 'lost_read_ack':
            raise ConnectionError(PRIVATE)
        return result
    client = Intercept(box.client, after=after)
    with scope(box, client=client) as permit:
        enabled[0] = True
        with pytest.raises(diagnostic.RouterProtocolDiagnosticError) as caught:
            diagnostic.run_protocol_diagnostic(permit)
        assert str(caught.value) == 'router_protocol_diagnostic_unverified'
        with pytest.raises(diagnostic.RouterProtocolDiagnosticError):
            diagnostic.run_protocol_diagnostic(permit)
    fallback = diagnostic._encrypted_failure_record(caught.value)
    assert fallback['capture_acknowledged'] is False
    assert fallback['qa_approved'] is fallback['publish_eligible'] is False
    assert decrypted(fallback, box.config.app_encryption_key)[1] == b'{"ok":true}'
    assert caught.value._capture.summary['capture_acknowledged'] is False
    with pytest.raises(diagnostic.RouterProtocolDiagnosticError):
        _ = caught.value._capture.receipt
    assert PRIVATE not in repr(caught.value) + json.dumps(fallback)
    assert len(wire.calls) == 1
    assert sum(commands == ('SET', 'HSET', 'SET') for commands in client.executions) == 2
    assert_old_unchanged(box)


@pytest.mark.parametrize('damage', ['hash', 'boolean_ack', 'extra_field'])
def test_actual_central_write_with_malformed_receipt_never_becomes_acknowledged_capture(
        frozen_three, wire, monkeypatch, damage):
    original = probe.ProtocolProbePermit.record_capture
    def damaged(self, capture):
        receipt = original(self, capture)
        if damage == 'hash': receipt['capture_sha256'] = '0' * 64
        elif damage == 'boolean_ack': receipt['capture_record_acknowledged'] = 1
        else: receipt['provider_message'] = PRIVATE
        return receipt
    monkeypatch.setattr(probe.ProtocolProbePermit, 'record_capture', damaged)
    # Let the original central context wrap the fixed transport exception;
    # fallback must follow its typed attachment without inspecting any frames.
    with pytest.raises(probe.ProtocolProbeBlocked) as caught:
        with scope(frozen_three) as permit:
            diagnostic.run_protocol_diagnostic(permit)
    fallback = diagnostic._encrypted_failure_record(caught.value)
    assert fallback['capture_acknowledged'] is False
    assert decrypted(fallback, frozen_three.config.app_encryption_key)[1] == b'{"ok":true}'
    assert PRIVATE not in repr(caught.value) + json.dumps(fallback)
    with pytest.raises(diagnostic.RouterProtocolDiagnosticError):
        diagnostic.run_protocol_diagnostic(permit)
    assert len(wire.calls) == 1
    assert stored_capture(frozen_three) is not None
    assert_old_unchanged(frozen_three)


def test_missing_application_encryption_fails_before_consuming_the_permit(frozen_three, wire, monkeypatch):
    box = frozen_three
    with scope(box) as permit:
        take = Mock(wraps=probe.ProtocolProbePermit.take_request)
        monkeypatch.setattr(probe.ProtocolProbePermit, 'take_request', take)
        box.config.app_encryption_key = ''
        with pytest.raises(diagnostic.RouterProtocolDiagnosticError) as caught:
            diagnostic.run_protocol_diagnostic(permit)
        take.assert_not_called()
        with pytest.raises(diagnostic.RouterProtocolDiagnosticError):
            diagnostic._encrypted_failure_record(caught.value)
    assert wire.calls == [] and stored_capture(box) is None
    assert_old_unchanged(box)


@pytest.mark.parametrize('damage', ['ciphertext_version', 'provider_field', 'partial_claim', 'qa_claim'])
def test_hash_consistent_malformed_durable_capture_still_fails_closed(frozen_three, wire, damage):
    box = frozen_three
    with scope(box) as permit:
        diagnostic.run_protocol_diagnostic(permit)
    state = json.loads(box.client.get(probe.PROBE_KEYS[0]))
    changed = deepcopy(state)
    capture = changed['capture']
    if damage == 'ciphertext_version':
        encrypted = capture['encrypted_response']
        token = bytearray(base64.urlsafe_b64decode(encrypted['ciphertext']))
        token[0] = 0
        encoded = base64.urlsafe_b64encode(token).decode()
        encrypted.update(ciphertext=encoded, bytes=len(encoded),
                         sha256=hashlib.sha256(encoded.encode()).hexdigest())
    elif damage == 'provider_field':
        capture['summary']['root']['known_field_kinds'][PRIVATE] = 'string'
        capture['summary']['root']['key_count'] += 1
    elif damage == 'partial_claim':
        capture['summary']['body_state'] = 'stream_error'
        capture['summary']['body_complete'] = True
    else:
        capture['qa_approved'] = True
    # Recompute every outer commitment: semantic structural validation must
    # reject corruption that a checksum-only reader would accept.
    box.client.set(probe.PROBE_KEYS[0], probe._raw(changed).decode())
    box.client.hset(probe.PROBE_KEYS[1], mapping=probe._journal(changed))
    box.client.set(probe.PROBE_KEYS[2], probe._hash(changed))
    with box.client.pipeline() as pipe:
        with pytest.raises((probe.ProtocolProbeBlocked, diagnostic.RouterProtocolDiagnosticError)):
            probe._read(pipe, state['reservation'])
    assert len(wire.calls) == 1
    assert_old_unchanged(box)
