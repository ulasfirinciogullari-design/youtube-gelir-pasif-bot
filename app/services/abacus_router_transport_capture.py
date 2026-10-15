"""Opt-in, pre-observer encrypted transport diagnostics for retained reviews.

The existing runtime/journal alone admits a request. This module adds its own
permanent intent before that one send, then a private create-only encrypted S3
object and small Redis anchor before semantic observation. It never settles a
review, changes admission, or grants QA, funding, retry or publication authority.

An interrupted process can still lose bytes before storage acknowledgement.
Bounded partial/oversized streams are explicitly incomplete. Storage ambiguity
is terminal and retains an issuer-bound encrypted fallback; it is not a retry
permit. Owner/admin object ACL checks do not attest bucket/CDN policy. Trusted
backend replacement or coordinated rollback requires external reconciliation.
"""
import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
import re
import threading
import weakref

from cryptography.fernet import Fernet
import httpx

from app.services import abacus_router_adapter as story
from app.services import abacus_router_audio_adapter as audio
from app.services import production_connection_continuity as continuity
from app.services import production_spend_runtime as spending, storage
from app.services.production_spend import SpendBlocked
from app.services.retained_audio_review_evidence import _source_acl_shape


_DOMAIN = b'abacus-router-retained-transport-capture-v1\0'
_CONTEXT_LIMIT = 256 * 1024
_RECORD_LIMIT = 16 * 1024
_CHUNK = 64 * 1024
_CONTENT_TYPE = 'application/octet-stream'
_FLAGS = {'diagnostic_only': True, 'qa_approved': False, 'publish_eligible': False,
          'full_qa_complete': False, 'semantic_observation_verified': False,
          'completion_or_funding_verified': False, 'retry_authorized': False}
_KNOWN = ('id', 'object', 'model', 'choices', 'created', 'usage',
          'system_fingerprint', 'service_tier', 'error')
_TIGRIS_ENDPOINTS = {'https://t3.storageapi.dev', 'https://t3.storage.dev',
                     'https://fly.storage.tigris.dev'}
_TIGRIS_ADMINS = {'Grantee': {'Type': 'Group', 'URI': 'https://groups.tigris.dev/org/admins'},
                  'Permission': 'FULL_CONTROL'}
_SESSIONS = {}
_CAPTURES = {}
_SCOPES = {}


class RouterTransportCaptureError(SpendBlocked):
    """Fixed local diagnostic failure; exceptions never contain provider data."""


def _require(value, code='router_transport_capture_unverified'):
    if not value:
        raise RouterTransportCaptureError(code)


def _raw(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _bytes(raw):
    _require(type(raw) in (str, bytes))
    return raw.encode('utf-8') if type(raw) is str else raw


def _ack(value, expected):
    _require(type(value) is list and len(value) == len(expected)
             and all(type(a) is type(b) and a == b for a, b in zip(value, expected)),
             'router_transport_capture_ack_uncertain')


def _read_ack(pipe):
    pipe.multi()
    pipe.ping()
    _ack(pipe.execute(), [True])


def _runtime_scope(scope, kind):
    # Closed callers cannot construct their own client, namespace or permission.
    from app.services import abacus_router_review_runtime as text_runtime
    from app.services import abacus_router_audio_review_runtime as audio_runtime
    if kind == 'story':
        _require(type(scope) is text_runtime._ReviewScope
                 and text_runtime._SCOPE.get() is scope)
        text_runtime._artifact_scope()
    else:
        _require(kind == 'audio' and type(scope) is audio_runtime._AudioScope
                 and audio_runtime._SCOPE.get() is scope)
        audio_runtime._usable(scope)
    _require(scope.owner_thread == threading.get_ident() and not scope.closed and not scope.failed)


class _Session:
    def __new__(cls, *args, **kwargs):
        raise TypeError('router_transport_session_private')

    def __repr__(self):
        return '<RouterTransportCaptureSession private>'


def _configure(scope, kind, enabled):
    """Freeze the original opt-in independently of caller-visible scope fields."""
    _require(type(enabled) is bool and id(scope) not in _SCOPES)
    _runtime_scope(scope, kind)
    session = _open(scope, kind) if enabled else None
    scope._transport_session = session
    identity = id(scope)
    _SCOPES[identity] = (weakref.ref(scope, lambda _: _SCOPES.pop(identity, None)),
                         kind, session, scope.journal, scope.journal.keys)


def _enabled(scope):
    original = _SCOPES.get(id(scope))
    _require(original is not None and original[0]() is scope
             and scope._transport_session is original[2]
             and scope.journal is original[3] and scope.journal.keys == original[4],
             'router_transport_capture_scope_changed')
    _runtime_scope(scope, original[1])
    return original[2] is not None


def _session(scope):
    _require(_enabled(scope), 'router_transport_capture_scope_required')
    session = getattr(scope, '_transport_session', None)
    entry = _SESSIONS.get(id(session))
    _require(type(session) is _Session and entry is not None and entry[0]() is session
             and entry[1]['scope']() is scope)
    data = entry[1]
    _runtime_scope(scope, data['kind'])
    return data


def _storage_guard(data):
    meta = getattr(data['s3'], 'meta', None)
    retries = getattr(getattr(meta, 'config', None), 'retries', None)
    _require(type(retries) is dict and (
        type(retries.get('total_max_attempts')) is int and retries['total_max_attempts'] == 1
        if 'total_max_attempts' in (retries or {}) else
        type(retries.get('max_attempts')) is int and retries['max_attempts'] == 0),
        'router_transport_capture_storage_retry_invalid')
    _require(type(data['bucket']) is str
             and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{2,62}', data['bucket'])
             and data['bucket'] == storage.settings.bucket
             and type(data['endpoint']) is str and data['endpoint'].startswith('https://')
             and data['endpoint'] == storage.settings.endpoint == getattr(meta, 'endpoint_url', None),
             'router_transport_capture_storage_changed')
    _require(all(callable(getattr(data['s3'], name, None))
                 for name in ('put_object', 'get_object', 'get_object_acl')),
             'router_transport_capture_storage_unavailable')


def _open(scope, kind):
    """Configuration-only preflight before a runtime can reserve any slot."""
    _runtime_scope(scope, kind)
    try:
        material = getattr(spending.settings, 'app_encryption_key', None)
        _require(type(material) is str and 1 <= len(material) <= 4096 and bool(material.strip()),
                 'router_transport_capture_encryption_unavailable')
        key = base64.urlsafe_b64encode(hmac.new(material.encode('utf-8'), _DOMAIN, hashlib.sha256).digest())
        data = {'scope': weakref.ref(scope), 'kind': kind, 'fernet': Fernet(key),
                's3': storage._client(single_attempt=True), 'bucket': storage.settings.bucket,
                'endpoint': storage.settings.endpoint, 'attempts': {}, 'pending': None,
                'client': scope.journal.client}
        _storage_guard(data)
        session = object.__new__(_Session)
        identity = id(session)
        _SESSIONS[identity] = (weakref.ref(session, lambda _: _SESSIONS.pop(identity, None)), data)
        return session
    except RouterTransportCaptureError:
        raise
    except Exception:
        raise RouterTransportCaptureError('router_transport_capture_preflight_failed') from None


def _journal_state(scope, data, prepared, purpose, reservation):
    from app.services import abacus_router_review_journal as text_journal
    from app.services import abacus_router_audio_review_journal as audio_journal
    module = text_journal if data['kind'] == 'story' else audio_journal
    expected_type = module.RouterReviewJournal if data['kind'] == 'story' else module.RouterAudioReviewJournal
    _require(type(scope.journal) is expected_type and scope.journal.client is data['client'])
    keys = scope.journal.keys
    _require(type(keys) is tuple and len(keys) == 3 and keys[0].endswith(':state'))
    pipe = data['client'].pipeline()
    try:
        pipe.watch(*keys)
        scope.journal._admit(pipe, reserve=False)
        # A newly installed diagnostic fence must also prevent a pending send.
        if (getattr(scope.journal, '_completion_plan', None) is None
                and getattr(scope.journal, '_captured_story_continuation', None) is None):
            from app.services.retained_router_protocol_probe import PROBE_KEYS
            pipe.watch(*PROBE_KEYS)
            _require(pipe.exists(*PROBE_KEYS) == 0, 'router_transport_capture_admission_changed')
        state = scope.journal._read(pipe)
        scope.journal._fresh(pipe, state['policy'], scope.journal._now())
        if data['kind'] == 'story':
            scope.journal._request(prepared, state['policy'])
        else:
            audio_journal._prepare(prepared.purpose, prepared)
            audio_journal._request(prepared, state['policy'])
        slot = state['slots'].get(purpose)
        _require(type(slot) is dict and slot['response'] is None)
        receipt = module._receipt(state['policy'], purpose, slot)
        expected = {**receipt, 'reservation_sha256': module._hash(receipt)}
        if data['kind'] == 'audio':
            expected.update(audio_journal._FLAGS)
        _require(_raw(expected) == reservation and prepared.request_sha256 == slot['request_sha256'])
        if data['kind'] == 'audio' and scope.journal._captured_story_continuation is not None:
            from app.services.retained_captured_visual_scope import require_audio_predecessors
            require_audio_predecessors(pipe, scope.journal._captured_story_continuation, purpose,
                                       reserved=True)
        source = continuity._derive(pipe, state['policy']['profile_revision'])
        _require(_sha(_raw(source)) == state['policy']['continuity_sha256'])
        credential = getattr(spending.settings, 'abacus_api_key', None)
        _require(type(credential) is str and _sha(('abacus\0' + credential).encode())
                 == prepared.credential_sha256, 'router_transport_capture_credential_changed')
        return pipe, keys, state, source
    except BaseException:
        pipe.reset()
        raise


def _durable(pipe, key, raw):
    _require(type(pipe.pttl(key)) is int and pipe.pttl(key) == -1
             and _bytes(pipe.get(key)) == raw, 'router_transport_capture_record_changed')


def _begin(scope, prepared, purpose, reservation):
    """Only the actual current runtime can bind its just-ACKed reservation."""
    data = _session(scope)
    _require(type(reservation) is bytes and len(reservation) <= _RECORD_LIMIT
             and data['pending'] is None and purpose not in data['attempts'])
    data['attempts'][purpose] = None  # terminal even if any following ACK is lost
    pipe, keys, state, source = _journal_state(scope, data, prepared, purpose, reservation)
    try:
        receipt = json.loads(reservation)
        binding = {'version': 1, 'kind': data['kind'], 'purpose': purpose,
            'original_task_id': continuity.ROOT_ID, 'source_task_id': continuity.LEAF_ID,
            'journal_keys': list(keys), 'reservation_sha256': receipt['reservation_sha256'],
            'request_sha256': prepared.request_sha256, 'credential_sha256': prepared.credential_sha256,
            'policy_sha256': receipt['policy_sha256'], 'continuity_sha256': _sha(_raw(source)),
            'reserved_at': receipt['reserved_at'], 'journal_state_sha256': _sha(_raw(state)), **_FLAGS}
        prefix = keys[0].rsplit(':', 1)[0] + ':transport_capture:v1:' + purpose + ':' + receipt['reservation_sha256']
        intent_key, anchor_key = prefix + ':intent', prefix + ':anchor'
        intent = _raw({'binding': binding, 'storage_endpoint_sha256': _sha(data['endpoint'].encode()),
                       'storage_bucket_sha256': _sha(data['bucket'].encode()), 'status': 'intent', **_FLAGS})
        _require(len(intent) <= _RECORD_LIMIT)
        context = {'binding': binding, 'policy': state['policy'], 'source': source,
                   'reservation': receipt}
        _require(len(_raw(context)) <= _CONTEXT_LIMIT)
        pipe.watch(intent_key, anchor_key)
        _require(pipe.exists(intent_key, anchor_key) == 0, 'router_transport_capture_already_attempted')
        pipe.multi()
        pipe.set(intent_key, intent, nx=True)
        _ack(pipe.execute(), [True])
        pipe.watch(intent_key, anchor_key)
        _durable(pipe, intent_key, intent)
        _require(pipe.exists(anchor_key) == 0)
        _read_ack(pipe)
        attempt = {'prepared': prepared, 'prepared_bytes': prepared._body_bytes, 'purpose': purpose,
                   'reservation': reservation, 'binding': binding, 'context': context,
                   'intent_key': intent_key, 'anchor_key': anchor_key, 'intent': intent}
        data['pending'] = attempt
        data['attempts'][purpose] = attempt
    except RouterTransportCaptureError:
        raise
    except Exception:
        raise RouterTransportCaptureError('router_transport_capture_intent_uncertain') from None
    finally:
        pipe.reset()


def _kind(value):
    return {dict: 'object', list: 'array', str: 'string', int: 'integer', float: 'number',
            bool: 'boolean', type(None): 'null'}.get(type(value), 'invalid')


def _shape(raw, complete):
    result = {'json_kind': 'unavailable', 'key_count': None, 'required_missing': None,
              'unknown_key_count': None, 'known_field_kinds': {}}
    if complete:
        try:
            value = story._json_loads(raw.decode('utf-8'))
            result['json_kind'] = _kind(value)
            if type(value) is dict:
                result.update(key_count=len(value), required_missing=[name for name in
                    ('model', 'choices') if name not in value],
                    unknown_key_count=sum(name not in _KNOWN for name in value),
                    known_field_kinds={name: _kind(value[name]) for name in _KNOWN if name in value})
        except Exception:
            result['json_kind'] = 'invalid'
    return result


def _headers(response):
    def feature(name, allowed):
        values = response.headers.get_list(name)
        return 'absent' if not values else values[0] if len(values) == 1 and values[0] in allowed else 'other'
    lengths = response.headers.get_list('content-length')
    length = (int(lengths[0]) if len(lengths) == 1 and lengths[0].isascii()
              and lengths[0].isdecimal() and 1 <= len(lengths[0]) <= 9 else None)
    return {'content_type': feature('content-type', {'application/json', 'application/json; charset=utf-8'}),
            'content_encoding': feature('content-encoding', {'identity'}),
            'content_length_valid': not lengths or length is not None,
            'content_length': length, 'redirect_history': bool(response.history)}


@dataclass(frozen=True, init=False, repr=False, slots=True, weakref_slot=True, eq=False)
class RetainedRouterTransportCapture:
    """Issuer-bound encrypted diagnostics. No semantic/settlement authority."""
    _ciphertext: bytes
    _receipt: bytes
    _owner: int

    def __new__(cls, *args, **kwargs):
        raise TypeError('router_transport_capture_private')

    def __repr__(self):
        return '<RetainedRouterTransportCapture encrypted diagnostic only>'

    @property
    def receipt(self):
        return json.loads(_issued(self)[2])

    @property
    def encrypted_fallback(self):
        """Exact encrypted bytes, only when persistence was not acknowledged.

        Available to the issuing thread after scope close; never a send permit.
        A successful capture exposes its private storage pointer instead.
        """
        original = _issued(self)
        _require(json.loads(original[2])['capture_acknowledged'] is False,
                 'router_transport_capture_fallback_not_needed')
        return original[1]


def _issued(capture):
    entry = _CAPTURES.get(id(capture))
    _require(type(capture) is RetainedRouterTransportCapture and entry is not None
             and entry[0]() is capture and type(capture._ciphertext) is bytes
             and capture._ciphertext == entry[1] and type(capture._receipt) is bytes
             and capture._receipt == entry[2]
             and type(capture._owner) is int and capture._owner == entry[3] == threading.get_ident())
    return entry


def _issue(ciphertext, receipt):
    capture = object.__new__(RetainedRouterTransportCapture)
    raw = _raw(receipt)
    object.__setattr__(capture, '_ciphertext', ciphertext)
    object.__setattr__(capture, '_receipt', raw)
    object.__setattr__(capture, '_owner', threading.get_ident())
    identity = id(capture)
    _CAPTURES[identity] = (weakref.ref(capture, lambda _: _CAPTURES.pop(identity, None)),
                          ciphertext, raw, threading.get_ident())
    return capture


def _private_acl(data, pointer):
    acl = data['s3'].get_object_acl(Bucket=data['bucket'], Key=pointer['key'])
    _source_acl_shape(acl, code='router_transport_capture_acl_invalid')
    owner, grants = acl['Owner'], acl['Grants']
    owners = [grant for grant in grants if grant['Permission'] == 'FULL_CONTROL'
              and grant['Grantee'].get('Type') == 'CanonicalUser'
              and grant['Grantee'].get('ID') == owner['ID']]
    _require(len(owners) == 1 and len(grants) in (1, 2), 'router_transport_capture_not_private')
    if len(grants) == 2:
        _require(data['endpoint'] in _TIGRIS_ENDPOINTS
                 and sum(grant == _TIGRIS_ADMINS for grant in grants) == 1,
                 'router_transport_capture_not_private')


def _store(data, attempt, ciphertext, summary):
    # Frozen storage identity and own intent are sufficient to retain a late
    # response. Current source/key/window can no longer discard received bytes.
    _storage_guard(data)
    namespace = _sha(_raw(attempt['binding']['journal_keys']))
    digest = _sha(ciphertext)
    pointer = {'key': f'recovery/{continuity.LEAF_ID}/router_transport/v1/{namespace}/'
                     f'{attempt["purpose"]}/{attempt["binding"]["reservation_sha256"]}/{digest}.fernet',
               'sha256': digest, 'size': len(ciphertext), 'content_type': _CONTENT_TYPE}
    anchor = {'binding': attempt['binding'], 'intent_sha256': _sha(attempt['intent']),
              'encrypted_blob': pointer, 'summary': summary, **_FLAGS}
    raw = _raw(anchor)
    _require(len(raw) <= _RECORD_LIMIT)
    # Verify own intent immediately before the only PUT; never retry on failure.
    with data['client'].pipeline() as pipe:
        pipe.watch(attempt['intent_key'], attempt['anchor_key'])
        _durable(pipe, attempt['intent_key'], attempt['intent'])
        _require(pipe.exists(attempt['anchor_key']) == 0)
        _read_ack(pipe)
    result = data['s3'].put_object(Bucket=data['bucket'], Key=pointer['key'], Body=ciphertext,
        ContentLength=len(ciphertext), ContentType=_CONTENT_TYPE, CacheControl='private, no-store',
        Metadata={'sha256': digest}, ACL='private', IfNoneMatch='*')
    status = result.get('ResponseMetadata', {}).get('HTTPStatusCode')
    _require(type(status) is int and status == 200, 'router_transport_capture_put_uncertain')
    result = data['s3'].get_object(Bucket=data['bucket'], Key=pointer['key'])
    body = result.get('Body')
    try:
        status = result.get('ResponseMetadata', {}).get('HTTPStatusCode')
        _require(type(status) is int and status == 200
                 and type(result.get('ContentLength')) is int and result['ContentLength'] == len(ciphertext)
                 and result.get('ContentType') == _CONTENT_TYPE)
        size, digestor = 0, hashlib.sha256()
        while True:
            chunk = body.read(min(_CHUNK, len(ciphertext) - size + 1))
            _require(type(chunk) is bytes and len(chunk) <= len(ciphertext) - size)
            if not chunk:
                break
            size += len(chunk)
            digestor.update(chunk)
        _require(size == len(ciphertext) and digestor.hexdigest() == digest)
    finally:
        if body is not None:
            body.close()
    _private_acl(data, pointer)
    _storage_guard(data)
    with data['client'].pipeline() as pipe:
        pipe.watch(attempt['intent_key'], attempt['anchor_key'])
        _durable(pipe, attempt['intent_key'], attempt['intent'])
        _require(pipe.exists(attempt['anchor_key']) == 0)
        pipe.multi()
        pipe.set(attempt['anchor_key'], raw, nx=True)
        _ack(pipe.execute(), [True])
        pipe.watch(attempt['intent_key'], attempt['anchor_key'])
        _durable(pipe, attempt['intent_key'], attempt['intent'])
        _durable(pipe, attempt['anchor_key'], raw)
        _read_ack(pipe)
    return {'anchor_key': attempt['anchor_key'], 'anchor_sha256': _sha(raw),
            'encrypted_blob': pointer}


def _freeze(scope, data, attempt, wire, status, headers, body, complete, outcome):
    prepared = attempt['prepared_bytes']
    limit = story.MAX_REQUEST_BYTES if data['kind'] == 'story' else audio.MAX_REQUEST_BYTES
    _require(type(prepared) is bytes and 0 < len(prepared) <= limit
             and type(wire) is bytes and 0 < len(wire) <= limit
             and type(body) is bytes and len(body) <= story.MAX_RESPONSE_BYTES)
    summary = {'http_status': status, 'response_bytes': len(body), 'response_sha256': _sha(body),
               'response_complete': complete, 'response_binding_verified': headers is not None,
               'transport_outcome': outcome, 'headers': headers,
               'root_shape': _shape(body, complete and headers is not None
                                    and headers['content_encoding'] in ('absent', 'identity'))}
    context = {**attempt['context'], 'summary': summary,
        'prepared_size': len(prepared), 'prepared_sha256': _sha(prepared),
        'wire_size': len(wire), 'wire_sha256': _sha(wire),
        'response_size': len(body), 'response_sha256': _sha(body), **_FLAGS}
    raw = _raw(context)
    _require(len(raw) <= _CONTEXT_LIMIT)
    packet = _DOMAIN + len(raw).to_bytes(4, 'big') + raw + prepared + wire + body
    maximum = len(_DOMAIN) + 4 + _CONTEXT_LIMIT + 2 * limit + story.MAX_RESPONSE_BYTES
    _require(len(packet) <= maximum)
    ciphertext = data['fernet'].encrypt(packet)
    # Fernet: version/time/IV + padded CBC bytes + HMAC, then URL-safe base64.
    _require(len(ciphertext) <= 4 * ((57 + 16 * (maximum // 16 + 1) + 2) // 3))
    receipt = {'binding': attempt['binding'], 'summary': summary,
               'ciphertext_sha256': _sha(ciphertext), 'capture_acknowledged': False, **_FLAGS}
    capture = _issue(ciphertext, receipt)
    scope._transport_captures[attempt['purpose']] = capture
    try:
        persisted = _store(data, attempt, ciphertext, summary)
    except Exception:
        error = RouterTransportCaptureError('router_transport_capture_storage_uncertain')
        error._transport_capture = capture
        raise error from None
    capture = _issue(ciphertext, {**receipt, **persisted, 'capture_acknowledged': True})
    scope._transport_captures[attempt['purpose']] = capture
    return capture


def _send(scope, prepared):
    """Private single-use transport; only a scope-owned intent reaches HTTPX."""
    data = _session(scope)
    attempt = data['pending']
    _require(type(attempt) is dict and attempt['prepared'] is prepared
             and prepared._body_bytes == attempt['prepared_bytes'])
    data['pending'] = None  # consume before any further I/O, including failed WATCH
    _storage_guard(data)
    transport = httpx.HTTPTransport(retries=0, trust_env=False)
    with httpx.Client(transport=transport, trust_env=False, follow_redirects=False,
                      auth=None, cookies=None, event_hooks={'request': [], 'response': []},
                      headers={'Accept-Encoding': 'identity'}) as client:
        _runtime_scope(scope, data['kind'])
        request = client.build_request('POST', story.ENDPOINT, **prepared.wire_kwargs())
        verify = story._verify_request if data['kind'] == 'story' else audio._verify_wire
        verify(prepared, request)
        wire = request.content
        # Last read transaction follows client/request construction, so a slow
        # factory cannot silently extend the source/key/expiry check to a send.
        pipe, keys, state, source = _journal_state(scope, data, prepared, attempt['purpose'], attempt['reservation'])
        try:
            _require(list(keys) == attempt['binding']['journal_keys']
                     and _sha(_raw(state)) == attempt['binding']['journal_state_sha256'])
            pipe.watch(attempt['intent_key'], attempt['anchor_key'])
            _durable(pipe, attempt['intent_key'], attempt['intent'])
            _require(pipe.exists(attempt['anchor_key']) == 0)
            _read_ack(pipe)
        finally:
            pipe.reset()
        _runtime_scope(scope, data['kind'])
        current_key = getattr(spending.settings, 'abacus_api_key', None)
        _require(type(current_key) is str and _sha(('abacus\0' + current_key).encode())
                 == prepared.credential_sha256, 'router_transport_capture_credential_changed')
        response, chunks, size, complete, outcome = None, [], 0, False, 'transport_failed'
        headers, status, candidate = None, None, None
        try:
            candidate = client.send(request, stream=True, follow_redirects=False)
            _require(type(candidate) is httpx.Response and candidate.request is request
                     and type(candidate.status_code) is int and 100 <= candidate.status_code <= 599)
            verify(prepared, candidate.request)
            _require(candidate.request.content == wire and prepared._body_bytes == attempt['prepared_bytes'])
            response = candidate
            headers, status = _headers(response), response.status_code
            outcome = 'stream_failed'
            for chunk in response.iter_raw():
                _require(type(chunk) is bytes)
                remaining = story.MAX_RESPONSE_BYTES - size
                chunks.append(chunk[:remaining])
                size += min(len(chunk), remaining)
                if len(chunk) > remaining:
                    outcome = 'response_too_large'
                    break
            else:
                complete, outcome = True, 'response_received'
        except Exception:
            # Never retain exception messages, tracebacks, auth or other headers.
            pass
        finally:
            # Freeze before close; even a close failure cannot discard the body.
            body = b''.join(chunks)
            try:
                capture = _freeze(scope, data, attempt, wire, status, headers, body, complete, outcome)
            finally:
                if type(candidate) is httpx.Response:
                    try:
                        candidate.close()
                    except Exception:
                        pass
        if response is None:
            raise RouterTransportCaptureError('router_transport_capture_transport_failed') from None
        response._content = body
        return response, capture


def _accept_response(response, capture):
    """Transport checks only, after persistence; original observer still follows."""
    receipt = capture.receipt
    summary = receipt['summary']
    _require(receipt['capture_acknowledged'] is True
             and type(response) is httpx.Response
             and summary['response_complete'] is True
             and summary['response_binding_verified'] is True
             and summary['transport_outcome'] == 'response_received'
             and response.status_code == summary['http_status']
             and len(response.content) == summary['response_bytes']
             and _sha(response.content) == summary['response_sha256']
             and _headers(response) == summary['headers'],
             'router_transport_capture_response_incomplete')
    _require(200 <= response.status_code < 300 and not summary['headers']['redirect_history'],
             'router_transport_capture_response_rejected')
    headers = summary['headers']
    _require(headers['content_encoding'] in ('absent', 'identity'),
             'router_transport_capture_response_encoding_invalid')
    _require(headers['content_length_valid'] is True
             and (headers['content_length'] is None or headers['content_length'] <= story.MAX_RESPONSE_BYTES),
             'router_transport_capture_response_size_invalid')


def _receipts(scope):
    """Read-only diagnostic receipts survive closure on their original thread."""
    _require(scope.owner_thread == threading.get_ident())
    return {purpose: capture.receipt for purpose, capture in scope._transport_captures.items()}


def _fallback(error):
    """Find only issued ciphertext through bounded exception wrapping.

    Existing recovery wrappers intentionally hide provider exception messages.
    Their cause/context links retain the original typed fallback after scope
    close; no traceback frame, local, HTTPX object or exception text is read.
    """
    pending, seen = [error], set()
    for _ in range(16):
        if not pending:
            break
        current = pending.pop()
        _require(isinstance(current, BaseException))
        if id(current) in seen:
            continue
        seen.add(id(current))
        capture = getattr(current, '_transport_capture', None)
        if capture is not None:
            _issued(capture)
            capture.encrypted_fallback
            return capture
        for parent in (current.__context__, current.__cause__):
            if parent is not None and id(parent) not in seen:
                pending.append(parent)
    raise RouterTransportCaptureError('router_transport_capture_fallback_unavailable')


def _fallbacks(scope):
    _receipts(scope)  # strict owner/issuer verification, including after close
    return {purpose: capture for purpose, capture in scope._transport_captures.items()
            if capture.receipt['capture_acknowledged'] is False}
