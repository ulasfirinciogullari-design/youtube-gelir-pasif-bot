"""One centrally admitted, fixed router protocol diagnostic.

The exact tiny request is frozen by the separate central permit. This module
does not commission, reserve ordinary reviews, observe semantic results or
grant QA/funding/publication. Response bytes are encrypted before they leave
the private capture; safe summaries contain only fixed field names and kinds.
The central permit persists the encrypted capture in its three permanent Redis
records. This utility neither writes S3 objects nor claims a private S3 ACK.
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

from app.services import abacus_router_adapter as adapter
from app.services import production_spend_runtime as spending


MAX_RESPONSE_BYTES = 256 * 1024
MAX_CAPTURE_BYTES = 512 * 1024
_CHUNK_BYTES = 4096
_SYSTEM = 'Return only the requested JSON object.'
_PROMPT = 'Return {"ok":true}.'
_SCHEMA = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}},
           'required': ['ok'], 'additionalProperties': False}
_SEAL = object()
_ISSUED = {}
_REQUIRED = ('id', 'object', 'model', 'choices')
_KNOWN = (*_REQUIRED, 'created', 'usage', 'system_fingerprint', 'service_tier', 'error')
_DOMAIN = b'abacus-router-protocol-diagnostic-v1\0'
_FLAGS = {'diagnostic_only': True, 'qa_approved': False, 'publish_eligible': False,
          'full_qa_complete': False, 'semantic_observation_verified': False,
          'completion_or_funding_verified': False, 'retry_authorized': False}


class RouterProtocolDiagnosticError(RuntimeError):
    """Fixed local rejection; no provider strings, request bodies or credentials."""


def _require(value, code='router_protocol_diagnostic_unverified'):
    if not value:
        raise RouterProtocolDiagnosticError(code)


def _raw(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _is_hash(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def _prepare_probe():
    """Pure fixed request builder; preparation grants no send permission."""
    return adapter.prepare_router_request([{'type': 'text', 'text': _PROMPT}],
        api_key=spending.settings.abacus_api_key, system_instruction=_SYSTEM,
        json_schema=json.loads(_raw(_SCHEMA)), max_tokens=32)


def _kind(value):
    return {dict: 'object', list: 'array', str: 'string', int: 'integer',
            float: 'number', bool: 'boolean', type(None): 'null'}.get(type(value), 'invalid')


def _root_shape(raw, *, complete=True):
    """Only fixed names/kinds/counts escape; unknown provider keys never do."""
    result = {'json_kind': 'unavailable', 'key_count': None, 'required_missing': None,
              'unknown_key_count': None, 'known_field_kinds': {}}
    if not complete:
        return result
    try:
        _require(type(raw) is bytes and len(raw) <= MAX_RESPONSE_BYTES)
        value = adapter._json_loads(raw.decode('utf-8'))
        result['json_kind'] = _kind(value)
        if type(value) is dict:
            result.update(key_count=len(value),
                required_missing=[name for name in _REQUIRED if name not in value],
                unknown_key_count=sum(name not in _KNOWN for name in value),
                known_field_kinds={name: _kind(value[name]) for name in _KNOWN if name in value})
    except Exception:
        result['json_kind'] = 'invalid'
    return result


def _material():
    value = getattr(spending.settings, 'app_encryption_key', None)
    _require(type(value) is str and 1 <= len(value) <= 4096 and bool(value.strip()),
             'router_protocol_encryption_unavailable')
    return value


def _fernet(material):
    digest = hmac.new(material.encode('utf-8'), _DOMAIN, hashlib.sha256).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _header_kind(response, name, allowed):
    values = response.headers.get_list(name)
    if not values:
        return 'absent'
    if len(values) != 1:
        return 'multiple'
    value = values[0].lower().replace(' ', '')
    return allowed.get(value, 'other')


@dataclass(frozen=True, repr=False, eq=False, init=False)
class ProtocolDiagnosticCapture:
    """Issuer-owned encrypted diagnostic; neither observation nor send permit."""
    _record_bytes: bytes
    _permit: object
    _owner_thread: int
    _seal: object
    _receipt_bytes: object

    def __new__(cls, *args, **kwargs):
        raise TypeError('router_protocol_capture_private')

    def __repr__(self):
        return '<ProtocolDiagnosticCapture encrypted diagnostic only>'

    @property
    def summary(self):
        _issued(self)
        return {**json.loads(self._record_bytes)['summary'], **_FLAGS,
                'capture_acknowledged': self._receipt_bytes is not None}

    @property
    def receipt(self):
        _issued(self)
        _require(type(self._receipt_bytes) is bytes, 'router_protocol_capture_ack_uncertain')
        return json.loads(self._receipt_bytes)


def _issued(capture):
    original = _ISSUED.get(id(capture))
    _require(type(capture) is ProtocolDiagnosticCapture
             and getattr(capture, '_seal', None) is _SEAL
             and original is not None and original[0]() is capture
             and type(capture._owner_thread) is int
             and capture._owner_thread == threading.get_ident()
             and type(capture._record_bytes) is bytes
             and 0 < len(capture._record_bytes) <= MAX_CAPTURE_BYTES
             and capture._record_bytes == original[1] and capture._permit is original[2]
             and capture._owner_thread == original[3]
             and (capture._receipt_bytes is None if original[4] is None else
                  type(capture._receipt_bytes) is bytes and capture._receipt_bytes == original[4]),
             'router_protocol_capture_unverified')


def _capture_record(capture, *, permit, request_sha256, credential_sha256):
    """Closed central admission's only export; no raw HTTPX reconstruction."""
    _issued(capture)
    permit.assert_active()
    _require(capture._permit is permit, 'router_protocol_capture_scope_changed')
    record = json.loads(capture._record_bytes)
    _require(record['request_sha256'] == request_sha256
             and record['credential_sha256'] == credential_sha256
             and _raw(record['permit_binding']) == _raw(permit.safe_binding),
             'router_protocol_capture_binding_changed')
    return _validate_capture_record(record, binding=permit.safe_binding)


def _validate_capture_record(record, *, binding):
    """Closed durable encrypted shape; neither decryption nor provider proof."""
    _require(type(record) is dict and set(record) == {
        'version', 'kind', 'permit_binding', 'request_sha256', 'credential_sha256',
        'summary', 'encrypted_response', *_FLAGS}
        and type(record['version']) is int and record['version'] == 1
        and record['kind'] == 'abacus_router_protocol_diagnostic_capture'
        and all(record[name] is value for name, value in _FLAGS.items())
        and len(_raw(record)) <= MAX_CAPTURE_BYTES
        and type(binding) is dict and type(record['permit_binding']) is dict
        and _raw(record['permit_binding']) == _raw(binding)
        and all(_is_hash(record[name]) and record[name] == binding.get(name)
                for name in ('request_sha256', 'credential_sha256')),
        'router_protocol_capture_record_invalid')
    summary = record['summary']
    _require(type(summary) is dict and set(summary) == {'http_status', 'body_state',
        'body_complete', 'captured_bytes', 'wire_request_verified', 'content_type_kind',
        'content_encoding_kind', 'root'}
        and (summary['http_status'] is None or type(summary['http_status']) is int
             and 100 <= summary['http_status'] <= 599)
        and type(summary['body_state']) is str
        and summary['body_state'] in ('complete', 'body_limit', 'stream_error', 'transport_error')
        and summary['body_complete'] is (summary['body_state'] == 'complete')
        and type(summary['captured_bytes']) is int and 0 <= summary['captured_bytes'] <= MAX_RESPONSE_BYTES
        and type(summary['wire_request_verified']) is bool
        and summary['content_type_kind'] in ('unavailable', 'absent', 'multiple', 'json', 'text', 'html', 'other')
        and summary['content_encoding_kind'] in ('unavailable', 'absent', 'multiple', 'identity', 'other'),
        'router_protocol_capture_summary_invalid')
    if summary['body_state'] in ('complete', 'body_limit', 'stream_error'):
        _require(summary['http_status'] is not None and summary['wire_request_verified'] is True)
    if summary['body_state'] == 'body_limit':
        _require(summary['captured_bytes'] == MAX_RESPONSE_BYTES)
    if summary['body_state'] == 'transport_error':
        _require(summary['captured_bytes'] == 0)
    root = summary['root']
    kinds = ('object', 'array', 'string', 'integer', 'number', 'boolean', 'null', 'invalid')
    _require(type(root) is dict and set(root) == {'json_kind', 'key_count', 'required_missing',
        'unknown_key_count', 'known_field_kinds'} and root['json_kind'] in (*kinds, 'unavailable')
        and type(root['known_field_kinds']) is dict)
    if root['json_kind'] == 'object':
        fields = root['known_field_kinds']
        _require(set(fields) <= set(_KNOWN) and all(value in kinds for value in fields.values())
            and type(root['key_count']) is int and 0 <= root['key_count'] <= MAX_RESPONSE_BYTES
            and type(root['unknown_key_count']) is int and root['unknown_key_count'] >= 0
            and root['key_count'] == len(fields) + root['unknown_key_count']
            and root['required_missing'] == [name for name in _REQUIRED if name not in fields])
    else:
        _require(root['key_count'] is None and root['unknown_key_count'] is None
                 and root['required_missing'] is None and root['known_field_kinds'] == {})
    if not summary['body_complete'] or summary['content_encoding_kind'] not in ('identity', 'absent'):
        _require(root['json_kind'] == 'unavailable')
    encrypted = record['encrypted_response']
    _require(type(encrypted) is dict and set(encrypted) == {'algorithm', 'ciphertext', 'sha256', 'bytes'}
        and encrypted['algorithm'] == 'fernet_hmac_sha256_v1'
        and type(encrypted['ciphertext']) is str and encrypted['ciphertext'].isascii()
        and type(encrypted['bytes']) is int and 0 < encrypted['bytes'] <= MAX_CAPTURE_BYTES
        and len(encrypted['ciphertext']) == encrypted['bytes']
        and _is_hash(encrypted['sha256'])
        and _sha(encrypted['ciphertext'].encode('ascii')) == encrypted['sha256'])
    token = encrypted['ciphertext'].encode('ascii')
    decoded = base64.b64decode(token, altchars=b'-_', validate=True)
    _require(base64.urlsafe_b64encode(decoded) == token and len(decoded) >= 73
             and decoded[0] == 0x80 and (len(decoded) - 57) % 16 == 0)
    return record


def _encrypted_failure_record(error):
    """Private operator fallback after storage failure; ciphertext only, no ACK.

    Export remains possible on the original thread after scope closure, so a
    failed Redis ACK cannot force the caller back to traceback locals. Keeping
    encrypted data never reopens the permit or authorizes a second POST.
    """
    _require(isinstance(error, BaseException), 'router_protocol_capture_unverified')
    captures, seen, pending = [], set(), [error]
    while pending and len(seen) < 16:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if type(current) is RouterProtocolDiagnosticError and hasattr(current, '_capture'):
            captures.append(current._capture)
        pending.extend(item for item in (current.__cause__, current.__context__)
                       if isinstance(item, BaseException) and id(item) not in seen)
    _require(not pending and len(captures) == 1, 'router_protocol_capture_unverified')
    capture = captures[0]
    _issued(capture)
    _require(capture._receipt_bytes is None, 'router_protocol_capture_unverified')
    return {**json.loads(capture._record_bytes), 'capture_acknowledged': False}


def _freeze_capture(permit, binding, material, body, summary, wire_sha, wire_body, prepared_body):
    _require(type(body) is bytes and len(body) <= MAX_RESPONSE_BYTES)
    # No authorization or response header is copied, even inside encryption.
    context = {'version': 1, 'permit_binding': binding,
        'endpoint': adapter.ENDPOINT, 'requested_model': adapter.MODEL,
        'request_sha256': binding['request_sha256'],
        'credential_sha256': binding['credential_sha256'],
        'wire_body_sha256': wire_sha, 'captured_body_sha256': _sha(body),
        'wire_body_base64': base64.b64encode(wire_body).decode('ascii') if wire_body is not None else None,
        'prepared_body_base64': base64.b64encode(prepared_body).decode('ascii') if prepared_body is not None else None,
        'summary': summary, **_FLAGS}
    encoded_context = _raw(context)
    plaintext = _DOMAIN + len(encoded_context).to_bytes(4, 'big') + encoded_context + body
    encrypted = _fernet(material).encrypt(plaintext)
    record = {'version': 1, 'kind': 'abacus_router_protocol_diagnostic_capture',
        'permit_binding': binding, 'request_sha256': binding['request_sha256'],
        'credential_sha256': binding['credential_sha256'], 'summary': summary,
        'encrypted_response': {'algorithm': 'fernet_hmac_sha256_v1',
            'ciphertext': encrypted.decode('ascii'), 'sha256': _sha(encrypted), 'bytes': len(encrypted)},
        **_FLAGS}
    raw = _raw(record)
    _require(len(raw) <= MAX_CAPTURE_BYTES, 'router_protocol_capture_too_large')
    capture = object.__new__(ProtocolDiagnosticCapture)
    for name, value in (('_record_bytes', raw), ('_permit', permit),
                        ('_owner_thread', threading.get_ident()), ('_seal', _SEAL),
                        ('_receipt_bytes', None)):
        object.__setattr__(capture, name, value)
    identity = id(capture)
    def discard(reference):
        original = _ISSUED.get(identity)
        if original is not None and original[0] is reference:
            _ISSUED.pop(identity, None)
    _ISSUED[identity] = (weakref.ref(capture, discard), raw, permit,
                         threading.get_ident(), None)
    return capture


def run_protocol_diagnostic(permit):
    """Consume one central permit and capture one bounded response, any status.

    There is no arbitrary request/URL/key/sender argument, observer, retry or
    ordinary review settlement. Partial reads are encrypted as partial reads.
    An uncertain capture ACK raises a fixed error retaining ciphertext privately.
    """
    capture = None
    try:
        from app.services.retained_router_protocol_probe import ProtocolProbePermit
        _require(type(permit) is ProtocolProbePermit, 'router_protocol_permit_required')
        permit.assert_active()
        material = _material()  # Fail before consuming a permit if encryption is unavailable.
        prepared = permit.take_request()
        _require(type(prepared) is adapter.PreparedRouterRequest and prepared == _prepare_probe()
                 and adapter.inspect_router_request(adapter.ENDPOINT, prepared.wire_kwargs()) == prepared,
                 'router_protocol_request_changed')
        binding = permit.safe_binding
        _require(type(binding) is dict
                 and binding.get('request_sha256') == prepared.request_sha256
                 and binding.get('credential_sha256') == prepared.credential_sha256,
                 'router_protocol_permit_changed')
        chunks, size, status, wire_sha, wire_body, prepared_body = [], 0, None, None, None, None
        state, mime, encoding = 'transport_error', 'unavailable', 'unavailable'
        try:
            transport = httpx.HTTPTransport(retries=0, trust_env=False)
            with httpx.Client(transport=transport, trust_env=False, follow_redirects=False,
                    auth=None, cookies=None, event_hooks={'request': [], 'response': []},
                    headers={'Accept-Encoding': 'identity'}) as client:
                with client.stream('POST', adapter.ENDPOINT, **prepared.wire_kwargs()) as response:
                    _require(type(response) is httpx.Response and type(response.status_code) is int
                             and 100 <= response.status_code <= 599 and not response.history,
                             'router_protocol_response_invalid')
                    status = response.status_code
                    wire_sha = adapter._verify_request(prepared, response.request)
                    wire_body, prepared_body = response.request.content, prepared._body_bytes
                    mime = _header_kind(response, 'content-type', {
                        'application/json': 'json', 'application/json;charset=utf-8': 'json',
                        'text/plain': 'text', 'text/html': 'html'})
                    encoding = _header_kind(response, 'content-encoding', {'identity': 'identity'})
                    state = 'stream_error'
                    for chunk in response.iter_raw(chunk_size=_CHUNK_BYTES):
                        _require(type(chunk) is bytes, 'router_protocol_response_invalid')
                        remaining = MAX_RESPONSE_BYTES - size
                        chunks.append(chunk[:remaining])
                        size += min(len(chunk), remaining)
                        if len(chunk) > remaining:
                            state = 'body_limit'
                            break
                    else:
                        state = 'complete'
        except Exception:
            # Fixed state only: transport messages and response headers may echo secrets.
            if state == 'complete':
                state = 'stream_error'
        body = b''.join(chunks)
        complete = state == 'complete'
        summary = {'http_status': status, 'body_state': state, 'body_complete': complete,
            'captured_bytes': len(body), 'wire_request_verified': wire_sha is not None,
            'content_type_kind': mime, 'content_encoding_kind': encoding,
            'root': _root_shape(body, complete=complete and encoding in ('identity', 'absent'))}
        capture = _freeze_capture(permit, binding, material, body, summary, wire_sha,
                                  wire_body, prepared_body)
        receipt = permit.record_capture(capture)
        expected = {**binding, 'capture_sha256': _sha(capture._record_bytes),
            'capture_record_acknowledged': True, 'occupied_count': 4, 'prior_unknown_count': 3,
            'diagnostic_only': True, 'qa_approved': False, 'publish_eligible': False,
            'resume_authorized': False, 'retry_authorized': False}
        _require(type(receipt) is dict and _raw(receipt) == _raw(expected),
                 'router_protocol_capture_ack_uncertain')
        _issued(capture)
        acknowledged = _raw(receipt)
        original = _ISSUED[id(capture)]
        _ISSUED[id(capture)] = (*original[:4], acknowledged)
        object.__setattr__(capture, '_receipt_bytes', acknowledged)
        return capture
    except Exception:
        error = RouterProtocolDiagnosticError('router_protocol_diagnostic_unverified')
        if capture is not None:
            error._capture = capture
        raise error from None
