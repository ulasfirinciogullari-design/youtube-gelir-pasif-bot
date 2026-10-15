"""Real offline HTTPX + Redis WATCH: capture is diagnostic, never admission."""
import base64
from copy import copy, deepcopy
from datetime import timedelta
from io import BytesIO
import json
from threading import Thread
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from redis.exceptions import ConnectionError

from app.services import abacus_router_transport_capture as capture
from app.services import abacus_router_review_runtime as text_runtime
from app.services import abacus_router_audio_review_runtime as audio_runtime
from app.services import abacus_router_review_journal as text_journal
from app.services import abacus_router_audio_review_journal as audio_journal
from app.services import production_connection_continuity as continuity
from app.services.production_spend import SpendBlocked
from test_abacus_router_review_runtime import (
    sandbox as text_sandbox, case, generate, STORY, VISUAL, Chunks, payload, NOW,
)
from test_abacus_router_audio_review_runtime import (
    sandbox as audio_sandbox, source, run_asr, run_prosody, ASR, PROSODY,
)
from test_production_connection_continuity import _dump
from test_production_credit_ledger import InterceptClient


BUCKET, ENDPOINT = 'private-transport-fixture', 'https://t3.storageapi.dev'
APP_KEY = 'SYNTHETIC-application-encryption-material'


class PrivateS3:
    def __init__(self):
        self.meta = SimpleNamespace(endpoint_url=ENDPOINT,
            config=SimpleNamespace(retries={'total_max_attempts': 1}))
        self.objects, self.puts, self.gets, self.acls = {}, [], [], []
        self.after_put = self.patch_get = None
        self.extra = []

    def put_object(self, **kw):
        self.puts.append(deepcopy(kw))
        assert kw['Bucket'] == BUCKET and kw['ContentType'] == 'application/octet-stream'
        assert kw['ACL'] == 'private' and kw['IfNoneMatch'] == '*'
        assert kw['ContentLength'] == len(kw['Body']) and kw['CacheControl'] == 'private, no-store'
        assert kw['Metadata'] == {'sha256': capture._sha(kw['Body'])}
        assert kw['Key'] not in self.objects
        self.objects[kw['Key']] = kw['Body']
        result = {'ResponseMetadata': {'HTTPStatusCode': 200}}
        if self.after_put:
            self.after_put(kw, result)
        return result

    def get_object(self, *, Bucket, Key):
        assert Bucket == BUCKET
        self.gets.append(Key)
        raw = self.objects[Key]
        result = {'ResponseMetadata': {'HTTPStatusCode': 200}, 'Body': BytesIO(raw),
                  'ContentLength': len(raw), 'ContentType': 'application/octet-stream'}
        if self.patch_get:
            self.patch_get(result)
        return result

    def get_object_acl(self, *, Bucket, Key):
        assert Bucket == BUCKET
        self.acls.append(Key)
        return {'Owner': {'ID': 'fixture-owner'}, 'ResponseMetadata': {'HTTPStatusCode': 200},
            'Grants': [{'Grantee': {'Type': 'CanonicalUser', 'ID': 'fixture-owner'},
                        'Permission': 'FULL_CONTROL'}, *deepcopy(self.extra)]}


def storage_fixture(box, monkeypatch):
    if not getattr(box.config, 'app_encryption_key', None):
        box.config.app_encryption_key = APP_KEY
    box.capture_material = box.config.app_encryption_key
    box.s3 = PrivateS3()
    monkeypatch.setattr(capture.storage, 'settings', SimpleNamespace(bucket=BUCKET, endpoint=ENDPOINT))
    factory = Mock(return_value=box.s3)
    monkeypatch.setattr(capture.storage, '_client', factory)
    box.storage_factory = factory
    return box


@pytest.fixture
def text_box(text_sandbox, monkeypatch):
    return storage_fixture(text_sandbox, monkeypatch)


@pytest.fixture
def audio_box(audio_sandbox, monkeypatch):
    return storage_fixture(audio_sandbox, monkeypatch)


def decode(box, receipt=None, ciphertext=None):
    if ciphertext is None:
        ciphertext = box.s3.objects[receipt['encrypted_blob']['key']]
    key = base64.urlsafe_b64encode(capture.hmac.new(box.capture_material.encode(), capture._DOMAIN,
                                                 capture.hashlib.sha256).digest())
    raw = capture.Fernet(key).decrypt(ciphertext)
    assert raw.startswith(capture._DOMAIN)
    length = int.from_bytes(raw[len(capture._DOMAIN):len(capture._DOMAIN)+4], 'big')
    offset = len(capture._DOMAIN) + 4
    context = json.loads(raw[offset:offset+length])
    offset += length
    parts = []
    for kind in ('prepared', 'wire', 'response'):
        size = context[kind + '_size']
        part = raw[offset:offset+size]
        assert len(part) == size and capture._sha(part) == context[kind + '_sha256']
        parts.append(part)
        offset += size
    assert offset == len(raw)
    return context, *parts


def own_keys(client):
    return [key for key in client.scan_iter() if ':transport_capture:v1:' in key]


def response(box, raw, status=200, headers=None, stream=None):
    def handle(request):
        box.calls.append(request)
        intents = [key for key in own_keys(box.case.client) if key.endswith(':intent')]
        assert len(intents) == 1 and box.case.client.pttl(intents[0]) == -1
        return httpx.Response(status, request=request,
            headers=headers or {'content-type': 'application/json'}, stream=stream or Chunks([raw]))
    box.handler = handle


@pytest.mark.parametrize('status,raw', [
    (200, b'{"choices": [], "created": 1, "model": "fixture", "usage": {}}'),
    (403, b'{"error":"Invalid API Key"}'), (302, b'PRIVATE redirect response'),
    (200, b'not JSON'), (500, b'PRIVATE upstream failure'),
])
def test_actual_failure_body_is_encrypted_durable_before_any_observer(text_box, monkeypatch, status, raw):
    box = text_box
    response(box, raw, status)
    original = text_journal.RouterReviewJournal.settle
    observed = []
    def settle(self, *args):
        anchors = [key for key in own_keys(box.case.client) if key.endswith(':anchor')]
        assert len(anchors) == 1 and len(box.s3.gets) == len(box.s3.acls) == 1
        observed.append(args[2])
        return original(self, *args)
    monkeypatch.setattr(text_journal.RouterReviewJournal, 'settle', settle)
    with text_runtime.retained_router_review_scope(continuity.LEAF_ID, capture_transport=True) as scope:
        with pytest.raises(SpendBlocked):
            generate()
        receipt = scope.transport_captures[STORY]
        assert scope.evidence == {} and scope._artifacts == {}
        with pytest.raises(SpendBlocked):
            generate(VISUAL)
    assert receipt == scope.transport_captures[STORY] and not scope.transport_capture_fallbacks
    assert receipt['capture_acknowledged'] is True
    context, prepared, wire, body = decode(box, receipt)
    assert body == raw and wire == box.calls[0].content
    assert capture._raw(json.loads(wire)) == prepared
    assert context['reservation']['request_sha256'] == context['binding']['request_sha256']
    assert context['prepared_sha256'] == capture._sha(prepared)
    assert context['policy']['credential_sha256'] == context['binding']['credential_sha256']
    assert capture._sha(capture._raw(context['source'])) == context['binding']['continuity_sha256']
    assert receipt['summary']['http_status'] == status and receipt['summary']['response_complete'] is True
    assert all(receipt[name] is value for name, value in capture._FLAGS.items())
    assert len(box.calls) == len(box.s3.puts) == 1 and len(observed) == (1 if status == 200 else 0)
    assert json.loads(box.case.client.get(text_journal.STATE_KEY))['slots'][STORY]['response'] is None


def test_normal_success_keeps_original_actual_response_and_both_settlement_acks(text_box, monkeypatch):
    box = text_box
    seen = []
    original = text_journal.RouterReviewJournal.settle
    def settle(self, purpose, prepared, actual):
        seen.append(actual)
        assert len(box.s3.puts) == len(box.s3.gets) == len(box.s3.acls)
        assert len([key for key in own_keys(box.case.client) if key.endswith(':anchor')]) == len(seen)
        return original(self, purpose, prepared, actual)
    monkeypatch.setattr(text_journal.RouterReviewJournal, 'settle', settle)
    with text_runtime.retained_router_review_scope(continuity.LEAF_ID, capture_transport=True) as scope:
        assert generate() == {'accepted': False}
        assert generate(VISUAL) == {'accepted': False}
        artifacts = text_runtime.retained_router_review_artifacts()
        assert set(scope.transport_captures) == set(artifacts) == {STORY, VISUAL}
        for purpose, actual in zip((STORY, VISUAL), seen):
            context, prepared, wire, body = decode(box, scope.transport_captures[purpose])
            assert body == actual.content == artifacts[purpose].response_body_bytes
            assert wire == actual.request.content == artifacts[purpose].request_body_bytes
    assert len(box.calls) == len(box.s3.puts) == 2


@pytest.mark.parametrize('stage', [2, 3, 4, 5, 6, 7])
@pytest.mark.parametrize('damage', ['lost', 'partial', 'race'])
def test_each_own_transaction_ack_is_terminal_without_resend_or_observation(text_box, stage, damage):
    box = text_box
    def before(number):
        if number == stage and damage == 'race':
            key = text_journal.STATE_KEY if stage in (2, 4) else next(k for k in own_keys(box.case.client) if k.endswith(':intent'))
            box.case.client.set(key, box.case.client.get(key))
    def after(number, result):
        if number == stage and damage == 'lost':
            raise ConnectionError('PRIVATE ' + box.config.abacus_api_key)
        return [] if number == stage and damage == 'partial' else result
    box.foundation.client = InterceptClient(box.case.client, before=before, after=after)
    with text_runtime.retained_router_review_scope(continuity.LEAF_ID, capture_transport=True) as scope:
        with pytest.raises(SpendBlocked) as caught:
            generate()
        assert box.config.abacus_api_key not in str(caught.value)
        assert scope.evidence == {} and scope._artifacts == {}
        with pytest.raises(SpendBlocked):
            generate(VISUAL)
    assert len(box.calls) == int(stage >= 5)
    assert len(box.s3.puts) == int(stage >= 6)
    assert json.loads(box.case.client.get(text_journal.STATE_KEY))['slots'][STORY]['response'] is None
    if stage >= 5:
        fallback = scope.transport_capture_fallbacks[STORY]
        assert fallback.receipt['capture_acknowledged'] is False
        context, prepared, wire, body = decode(box, ciphertext=fallback.encrypted_fallback)
        assert body == json.dumps(payload()).encode() and wire == box.calls[0].content


@pytest.mark.parametrize('damage', ['put_lost', 'put_status', 'readback', 'public', 'unknown_group', 'duplicate_owner'])
def test_storage_failure_retains_one_sealed_ciphertext_without_second_put(text_box, damage):
    box = text_box
    if damage == 'put_lost':
        def fail(*_): raise ConnectionError('PRIVATE ' + box.config.abacus_api_key)
        box.s3.after_put = fail
    elif damage == 'put_status':
        box.s3.after_put = lambda _, result: result.update(ResponseMetadata={'HTTPStatusCode': True})
    elif damage == 'readback':
        box.s3.patch_get = lambda result: result.update(ContentLength=True)
    else:
        grantee = {'Type': 'CanonicalUser', 'ID': 'fixture-owner'} if damage == 'duplicate_owner' else {
            'Type': 'Group', 'URI': 'http://acs.amazonaws.com/groups/global/AllUsers'
            if damage == 'public' else 'https://groups.invalid/private'}
        box.s3.extra = [{'Grantee': grantee, 'Permission': 'FULL_CONTROL'}]
    with text_runtime.retained_router_review_scope(continuity.LEAF_ID, capture_transport=True) as scope:
        with pytest.raises(SpendBlocked) as caught:
            generate()
        fallback = capture._fallback(caught.value)
        assert fallback is scope.transport_capture_fallbacks[STORY]
    assert decode(box, ciphertext=fallback.encrypted_fallback)[-1] == json.dumps(payload()).encode()
    assert len(box.calls) == len(box.s3.puts) == 1
    assert len(own_keys(box.case.client)) == 1
    assert box.config.abacus_api_key not in repr(fallback) + str(caught.value)
    with pytest.raises(TypeError): capture.RetainedRouterTransportCapture(b'forged', b'{}', 0)
    forged = object.__new__(capture.RetainedRouterTransportCapture)
    for name in ('_ciphertext', '_receipt', '_owner'):
        object.__setattr__(forged, name, getattr(fallback, name))
    with pytest.raises(SpendBlocked): _ = forged.encrypted_fallback
    result = []
    def thread():
        try: result.append(fallback.encrypted_fallback)
        except SpendBlocked: result.append('blocked')
    other = Thread(target=thread); other.start(); other.join()
    assert result == ['blocked']
    object.__setattr__(fallback, '_receipt', b'{}')
    with pytest.raises(SpendBlocked): _ = fallback.receipt


@pytest.mark.parametrize('damage', ['source', 'key', 'expiry', 'bucket'])
def test_post_send_drift_preserves_received_bytes_without_new_authority(text_box, damage):
    box = text_box
    original = box.handler
    def handle(request):
        actual = original(request)
        if damage == 'source':
            box.case.client.set(continuity._JOB + continuity.LEAF_ID, '{}')
        elif damage == 'key':
            box.config.abacus_api_key = 'replacement-after-send'
        elif damage == 'expiry':
            scope.journal.clock = lambda: NOW + timedelta(days=2)
        else:
            capture.storage.settings.bucket = 'changed-bucket'
        return actual
    box.handler = handle
    with text_runtime.retained_router_review_scope(continuity.LEAF_ID, capture_transport=True) as scope:
        if damage == 'bucket':
            with pytest.raises(SpendBlocked): generate()
        else:
            generate()  # Existing journal may honestly ACK a late result; capture grants nothing.
        receipt = scope.transport_captures[STORY]
        with pytest.raises(SpendBlocked): generate(VISUAL)
    ciphertext = scope.transport_capture_fallbacks[STORY].encrypted_fallback if damage == 'bucket' else None
    assert decode(box, receipt, ciphertext)[-1] == json.dumps(payload()).encode()
    assert receipt['qa_approved'] is False and receipt['semantic_observation_verified'] is False
    assert len(box.calls) == 1


@pytest.mark.parametrize('mode', ['partial', 'oversize', 'connect', 'encoding', 'length'])
def test_bounded_partial_nonidentity_and_transport_failure_are_saved_but_rejected(text_box, mode):
    box = text_box
    prefix = b'PRIVATE PARTIAL\0'
    class Broken(httpx.SyncByteStream):
        def __iter__(self):
            yield prefix
            raise httpx.ReadError('PRIVATE ' + box.config.abacus_api_key)
    if mode == 'partial':
        response(box, b'', stream=Broken())
    elif mode == 'oversize':
        response(box, b'x' * (capture.story.MAX_RESPONSE_BYTES + 1))
    elif mode == 'connect':
        def fail(request):
            box.calls.append(request)
            raise httpx.ConnectError('PRIVATE ' + box.config.abacus_api_key)
        box.handler = fail
    else:
        response(box, b'{}', headers={'content-encoding': 'gzip'} if mode == 'encoding'
                 else {'content-length': str(capture.story.MAX_RESPONSE_BYTES + 1)})
    with text_runtime.retained_router_review_scope(continuity.LEAF_ID, capture_transport=True) as scope:
        with pytest.raises(SpendBlocked): generate()
        receipt = scope.transport_captures[STORY]
        assert scope.evidence == {}
    body = decode(box, receipt)[-1]
    assert body == (prefix if mode == 'partial' else b'x' * capture.story.MAX_RESPONSE_BYTES
                    if mode == 'oversize' else b'' if mode == 'connect' else b'{}')
    assert receipt['summary']['response_complete'] is (mode in ('encoding', 'length'))
    assert len(box.calls) == len(box.s3.puts) == 1


def test_echoed_direct_encoded_and_opaque_secrets_exist_only_in_ciphertext(text_box):
    box = text_box
    secrets = [box.config.abacus_api_key, APP_KEY]
    echoes = [value for secret in secrets for value in (
        secret, base64.b64encode(secret.encode()).decode(),
        ''.join('\\u%04x' % ord(char) for char in secret))]
    raw = capture._raw({name: echoes for name in echoes})
    response(box, raw)
    with text_runtime.retained_router_review_scope(continuity.LEAF_ID, capture_transport=True) as scope:
        with pytest.raises(SpendBlocked): generate()
    receipt = scope.transport_captures[STORY]
    public = capture._raw(receipt) + repr(_dump(box.case.client)).encode()
    assert all(secret.encode() not in public for secret in echoes)
    assert receipt['summary']['root_shape']['unknown_key_count'] == len(echoes)
    context, prepared, wire, body = decode(box, receipt)
    assert body == raw
    assert 'authorization' not in context and 'request_headers' not in context and 'response_headers' not in context


@pytest.mark.parametrize('mode', ['missing_key', 'retrying', 'endpoint'])
def test_known_invalid_capture_configuration_rejects_before_reservation(text_box, mode):
    box = text_box
    before = _dump(box.case.client)
    if mode == 'missing_key': box.config.app_encryption_key = ''
    elif mode == 'retrying': box.s3.meta.config.retries = {'total_max_attempts': 2}
    else: box.s3.meta.endpoint_url = 'https://another.invalid'
    with pytest.raises(SpendBlocked):
        with text_runtime.retained_router_review_scope(continuity.LEAF_ID, capture_transport=True):
            generate()
    assert _dump(box.case.client) == before and not box.calls and not box.s3.puts


def test_exact_tigris_owner_admin_private_acl_is_accepted(text_box):
    text_box.s3.extra = [deepcopy(capture._TIGRIS_ADMINS)]
    with text_runtime.retained_router_review_scope(continuity.LEAF_ID, capture_transport=True) as scope:
        generate()
        assert scope.transport_captures[STORY]['capture_acknowledged'] is True


def test_default_never_opens_storage_or_adds_capture_keys(text_box):
    with text_runtime.retained_router_review_scope(continuity.LEAF_ID) as scope:
        generate()
        assert scope.transport_captures == scope.transport_capture_fallbacks == {}
    text_box.storage_factory.assert_not_called()
    assert own_keys(text_box.case.client) == []


@pytest.mark.parametrize('damage', ['source', 'key', 'expiry'])
def test_final_watched_source_and_key_check_follows_client_construction(text_box, damage, monkeypatch):
    box = text_box
    original = box.transport.side_effect
    def transport(**kwargs):
        result = original(**kwargs)
        if damage == 'source': box.case.client.set(continuity._AUTH_EPOCH, '13')
        elif damage == 'key': box.config.abacus_api_key = 'changed-before-send'
        else: scope.journal.clock = lambda: NOW + timedelta(days=2)
        return result
    monkeypatch.setattr(httpx, 'HTTPTransport', transport)
    with text_runtime.retained_router_review_scope(continuity.LEAF_ID, capture_transport=True) as scope:
        with pytest.raises(SpendBlocked): generate()
        assert scope.transport_captures == {}
    assert len(own_keys(box.case.client)) == 1  # intent stays durable, original slot stays occupied
    assert not box.calls and not box.s3.puts


def test_private_capture_send_needs_actual_scope_issued_intent_and_is_one_use(text_box):
    box = text_box
    prepared = capture.story.prepare_router_request([{'type': 'text', 'text': 'No admission'}],
        api_key=box.config.abacus_api_key, system_instruction='No admission',
        json_schema={'type': 'object', 'properties': {}}, max_tokens=32)
    with text_runtime.retained_router_review_scope(continuity.LEAF_ID, capture_transport=True) as scope:
        with pytest.raises(SpendBlocked): capture._send(scope, prepared)
        assert not own_keys(box.case.client) and not box.calls
        generate()
        assert len(box.calls) == 1
        with pytest.raises(SpendBlocked): capture._send(scope, prepared)
    with pytest.raises(SpendBlocked): capture._send(scope, prepared)
    assert len(box.calls) == len(box.s3.puts) == 1


@pytest.mark.parametrize('value', [None, 0, 1, 'true'])
@pytest.mark.parametrize('runtime', [text_runtime, audio_runtime])
def test_optin_is_exact_boolean_and_plan_cannot_disable_it(text_box, value, runtime):
    method = runtime.retained_router_review_scope if runtime is text_runtime else runtime.retained_audio_router_review_scope
    with pytest.raises(SpendBlocked):
        with method(continuity.LEAF_ID, capture_transport=value): pass
    with pytest.raises(SpendBlocked):
        with method(continuity.LEAF_ID, completion_plan=object()): pass
    with pytest.raises(SpendBlocked):
        with method(continuity.LEAF_ID, completion_plan=object(), successor=object(), capture_transport=True): pass
    text_box.getter.assert_not_called()


def test_audio_asr_and_prosody_capture_full_original_bound_wires(audio_box):
    box = audio_box
    with audio_runtime.retained_audio_router_review_scope(continuity.LEAF_ID, capture_transport=True) as scope:
        asr = run_asr()
        prosody = run_prosody(box)
        for purpose, actual in ((ASR, asr), (PROSODY, prosody)):
            context, prepared, wire, body = decode(box, scope.transport_captures[purpose.value])
            assert prepared == actual.prepared._body_bytes and wire == actual.response.request.content
            assert body == actual.response.content and context['policy']['audio'] == actual.prepared.audio
            assert context['reservation'] == actual.reservation
    assert len(box.calls) == len(box.s3.puts) == 2


def test_audio_non2xx_error_classification_follows_durable_encrypted_body(audio_box):
    box = audio_box
    raw = b'{"error":"Invalid API Key"}'
    response(box, raw, 403)
    with audio_runtime.retained_audio_router_review_scope(continuity.LEAF_ID, capture_transport=True) as scope:
        with pytest.raises(SpendBlocked): run_asr()
        assert scope.failure['http_status'] == 403
        assert scope.failure['provider_error']['message_kind'] == 'invalid_api_key'
        assert scope.evidence == {}
        with pytest.raises(SpendBlocked): run_prosody(box)
    assert decode(box, scope.transport_captures[ASR.value])[-1] == raw
    assert len(box.calls) == len(box.s3.puts) == 1


def test_audio_storage_failure_preserves_same_thread_typed_fallback_after_close(audio_box):
    box = audio_box
    def fail(*_): raise ConnectionError('PRIVATE ' + box.config.abacus_api_key)
    box.s3.after_put = fail
    with audio_runtime.retained_audio_router_review_scope(continuity.LEAF_ID, capture_transport=True) as scope:
        with pytest.raises(SpendBlocked) as caught: run_asr()
    fallback = scope.transport_capture_fallbacks[ASR.value]
    assert capture._fallback(caught.value) is fallback
    assert decode(box, ciphertext=fallback.encrypted_fallback)[-1] == b''.join(box.streams[0].chunks)
    assert len(box.calls) == len(box.s3.puts) == 1


@pytest.mark.parametrize('stage', ['before_reserve', 'after_reserve'])
def test_audio_optin_cannot_be_removed_from_returned_scope(audio_box, monkeypatch, stage):
    box = audio_box
    with audio_runtime.retained_audio_router_review_scope(continuity.LEAF_ID, capture_transport=True) as scope:
        before = _dump(box.case.client)
        if stage == 'before_reserve':
            scope._transport_session = None
        else:
            original = scope.journal.reserve
            def reserve(*args, **kwargs):
                result = original(*args, **kwargs)
                scope._transport_session = None
                return result
            monkeypatch.setattr(scope.journal, 'reserve', reserve)
        with pytest.raises(SpendBlocked): run_asr()
        assert scope.transport_captures == {} and scope.evidence == {}
        if stage == 'before_reserve': assert _dump(box.case.client) == before
    assert not box.calls and not box.s3.puts


def test_wrapped_recovery_exception_keeps_only_issued_fallback_after_scope_close(text_box):
    box = text_box
    def fail(*_): raise ConnectionError('PRIVATE ' + box.config.abacus_api_key)
    box.s3.after_put = fail
    def recovery_wrapper():
        with text_runtime.retained_router_review_scope(continuity.LEAF_ID, capture_transport=True):
            try:
                generate()
            except SpendBlocked:
                raise RuntimeError('fixed_outer_recovery_failure') from None
    with pytest.raises(RuntimeError) as caught: recovery_wrapper()
    fallback = capture._fallback(caught.value)
    assert decode(box, ciphertext=fallback.encrypted_fallback)[-1] == json.dumps(payload()).encode()
    assert box.config.abacus_api_key not in str(caught.value) + repr(fallback)
    wrapped = caught.value
    for _ in range(17):
        parent = RuntimeError('fixed')
        parent.__cause__ = wrapped
        wrapped = parent
    with pytest.raises(SpendBlocked, match='fallback_unavailable'): capture._fallback(wrapped)
    cycle = RuntimeError('fixed')
    cycle.__context__ = cycle
    with pytest.raises(SpendBlocked, match='fallback_unavailable'): capture._fallback(cycle)
    forged = RuntimeError('fixed')
    forged._transport_capture = object()
    with pytest.raises(SpendBlocked): capture._fallback(forged)
    assert len(box.calls) == len(box.s3.puts) == 1
