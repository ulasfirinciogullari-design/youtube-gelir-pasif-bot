"""Explicit synchronous, two-purpose review of original retained audio.

No automatic commissioning, financial initialization, source-file loading,
provider fallback, media generation or QA/publication grant. Each purpose needs
an acknowledged permanent reservation before one POST and an acknowledged
settlement before its actual request/response/observation can be returned.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import hashlib
import json
import re
import threading

import httpx

from app.services import audio_qc, production_spend_runtime as spending
from app.services import abacus_router_audio_adapter as adapter
from app.services.abacus_router_audio_review_journal import RouterAudioReviewJournal
from app.services.production_connection_continuity import LEAF_ID
from app.services.production_spend import SpendBlocked
from app.services.retained_router_audio_qa import validate_retained_router_asr


_SCOPE = ContextVar('retained_abacus_router_audio_review', default=None)
_CAPS = {'monthly_micro', 'daily_micro', 'channel_monthly_micro',
         'shorts_micro', 'long_micro', 'derived_micro'}
_FLAGS = {'diagnostic_only': True, 'qa_approved': False, 'publish_eligible': False}
_CHUNK_BYTES = 64 * 1024
_ERROR_BODY_BYTES = 16 * 1024
_ASR, _PROSODY = adapter.AudioReviewPurpose
_ERROR_INDICATORS = {
    'schema_invalid': {'invalid_json_schema', 'invalid_response_format', 'schema_validation_error'},
    'unsupported_parameter': {'unsupported_parameter', 'unsupported_value'},
    'authentication': {'invalid_api_key', 'authentication_error', 'incorrect_api_key', 'unauthorized'},
    'subscription_or_credits': {'insufficient_quota', 'insufficient_credits', 'credits_exhausted',
                                'subscription_required', 'subscription_expired'},
    'rate_limit': {'rate_limit_exceeded', 'rate_limit_error', 'too_many_requests'},
    'model_unavailable': {'model_not_found', 'model_unavailable', 'model_not_available', 'model_decommissioned'},
}
_ERROR_CODES = frozenset().union(*_ERROR_INDICATORS.values())
_ERROR_TYPES = _ERROR_CODES | {'invalid_request_error', 'permission_error', 'server_error',
                             'rate_limit_error', 'not_found_error'}
_ERROR_PARAMS = {'model', 'modalities', 'response_format', 'response_format.json_schema',
                 'max_tokens', 'messages', 'messages[1].content'}


def _require(condition, code):
    if not condition:
        raise SpendBlocked(code)


def _zero_cash_guard():
    try:
        config = spending.settings
        _require(getattr(config, 'studio_spend_enforcement', False) is True
                 and getattr(config, 'studio_abacus_router_retained_audio_review_enabled', False) is True,
                 'router_audio_runtime_not_enabled')
        raw = getattr(config, 'studio_spend_policy_json', None)
        _require(type(raw) is str and len(raw.encode('utf-8')) <= 4096,
                 'router_audio_zero_cash_required')
        def pairs(items):
            result = {}
            for key, value in items:
                _require(key not in result, 'router_audio_zero_cash_required')
                result[key] = value
            return result
        policy = json.loads(raw, object_pairs_hook=pairs,
                            parse_constant=lambda _: _require(False, 'router_audio_zero_cash_required'))
        _require(type(policy) is dict and set(policy) == _CAPS
                 and all(type(value) is int and value == 0 for value in policy.values()),
                 'router_audio_zero_cash_required')
        return config
    except SpendBlocked:
        raise
    except Exception:
        raise SpendBlocked('router_audio_zero_cash_required') from None


def _server_key(config):
    key = getattr(config, 'abacus_api_key', None)
    _require(type(key) is str and 1 <= len(key) <= 4096
             and key.isascii() and all(33 <= ord(char) <= 126 for char in key),
             'router_audio_runtime_credential_invalid')
    return key


@dataclass(frozen=True, repr=False)
class AcknowledgedAudioReview:
    """Retained actual HTTPX artifacts; a typed value is not a QA permit."""
    prepared: adapter.PreparedAudioRouterRequest
    response: httpx.Response
    observed: adapter.ObservedAudioRouterResult
    _reservation_bytes: bytes
    _settlement_bytes: bytes

    def __repr__(self):
        return '<AcknowledgedAudioReview abacus:route-llm diagnostic redacted>'

    @property
    def reservation(self):
        return json.loads(self._reservation_bytes)

    @property
    def settlement(self):
        return json.loads(self._settlement_bytes)


@dataclass(repr=False)
class _AudioScope:
    journal: RouterAudioReviewJournal
    owner_thread: int
    key: str
    attempted: set = field(default_factory=set)
    artifacts: dict = field(default_factory=dict)
    pending_request: object = None
    http_status: object = None
    http_error: dict = field(default_factory=dict)
    failure: dict = field(default_factory=dict)
    _transport_session: object = None
    _transport_captures: dict = field(default_factory=dict)
    failed: bool = False
    closed: bool = False

    def __repr__(self):
        return '<RetainedRouterAudioReviewScope private>'

    @property
    def evidence(self):
        return {purpose.value: item.settlement for purpose, item in self.artifacts.items()}

    @property
    def transport_captures(self):
        from app.services.abacus_router_transport_capture import _receipts
        return _receipts(self)

    @property
    def transport_capture_fallbacks(self):
        from app.services.abacus_router_transport_capture import _fallbacks
        return _fallbacks(self)


@contextmanager
def retained_audio_router_review_scope(source_task_id, *, successor=None, completion_plan=None,
                                      capture_transport=False):
    _require(_SCOPE.get() is None, 'router_audio_scope_nested')
    _require(type(source_task_id) is str and source_task_id == LEAF_ID,
             'router_audio_source_not_supported')
    _require(type(capture_transport) is bool and not (successor is not None and completion_plan is not None)
             and (completion_plan is None or capture_transport is True),
             'router_audio_capture_option_invalid')
    try:
        key = _server_key(_zero_cash_guard())
        foundation = spending.configured_ledger()
        if successor is not None:
            from app.services.retained_review_credential_successor import verify_scope_successor
            verify_scope_successor(foundation.client, successor)
        if completion_plan is not None:
            from app.services.retained_review_completion_plan import verify_scope_completion_plan
            verify_scope_completion_plan(foundation.client, completion_plan)
        # Only its existing client/clock are used. No financial method, policy
        # lookup or absent-history initialization belongs in an included review.
        scope = _AudioScope(RouterAudioReviewJournal(foundation.client, clock=foundation.clock, successor=successor,
                                                   completion_plan=completion_plan),
                            threading.get_ident(), key)
        _require(_server_key(_zero_cash_guard()) == key, 'router_audio_runtime_credential_changed')
    except SpendBlocked:
        raise
    except Exception:
        raise SpendBlocked('router_audio_runtime_unavailable') from None
    token = _SCOPE.set(scope)
    try:
        from app.services.abacus_router_transport_capture import _configure
        _configure(scope, 'audio', capture_transport)
        yield scope
    finally:
        scope.closed = True
        _SCOPE.reset(token)


def retained_audio_router_review_active():
    """Remain active even when failed/closed in a copied context; no fallback."""
    return _SCOPE.get() is not None


def retained_audio_router_review_evidence():
    scope = _SCOPE.get()
    return scope.evidence if scope is not None else {}


def retained_audio_router_review_failure():
    """Fixed reason, status and allowlisted indicators; no raw body/headers."""
    scope = _SCOPE.get()
    return json.loads(json.dumps(scope.failure)) if scope is not None else {}


def _usable(scope):
    _require(not scope.closed and not scope.failed and scope.owner_thread == threading.get_ident(),
             'router_audio_scope_unusable')
    _require(_server_key(_zero_cash_guard()) == scope.key, 'router_audio_runtime_credential_changed')


def _bounded_http_error(response, *, captured=False):
    """Retain only fixed provider field indicators, never body/message text.

    Recognized fields describe the provider's reported error, not a verified
    cause. Unknown JSON, encoding or fields remain explicitly unclassified.
    """
    unknown = {'classification': 'unknown', 'cause_verified': False}
    encodings = response.headers.get_list('content-encoding')
    if encodings and encodings != ['identity']:
        return unknown
    chunks, size = [], 0
    source = (response.content,) if captured else response.iter_raw(chunk_size=4096)
    for chunk in source:
        if type(chunk) is not bytes:
            return unknown
        if len(chunk) > _ERROR_BODY_BYTES - size:
            return {'body_too_large': True}
        size += len(chunk)
        chunks.append(chunk)
    try:
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError()
                result[key] = value
            return result
        def invalid_constant(_):
            raise ValueError()
        value = json.loads(b''.join(chunks).decode('utf-8'), object_pairs_hook=pairs,
                           parse_constant=invalid_constant)
        if type(value) is not dict:
            return unknown
        error = value.get('error', value)
        # Abacus also returns a plain error string. Classify only the exact
        # observed credential rejection; never retain arbitrary message text
        # or turn this diagnostic into permission to release/retry the slot.
        if type(error) is str and error.strip().casefold() == 'invalid api key':
            return {'classification': 'provider_reported_message', 'cause_verified': False,
                    'message_kind': 'invalid_api_key', 'indicators': {
                        name: name == 'authentication' for name in _ERROR_INDICATORS}}
        if type(error) is not dict:
            return unknown
        allowed = {'code': _ERROR_CODES, 'type': _ERROR_TYPES, 'param': _ERROR_PARAMS}
        fields = {name: error[name] for name, values in allowed.items()
                  if type(error.get(name)) is str and error[name] in values}
        if not fields:
            return unknown
        return {'classification': 'provider_reported_fields', 'cause_verified': False,
                'fields': fields, 'indicators': {
                    name: bool(values & {fields.get('code'), fields.get('type')})
                    for name, values in _ERROR_INDICATORS.items()}}
    except Exception:
        return unknown


def _send_once(prepared):
    """Private exact audio transport with one consumed reservation permission."""
    scope = _SCOPE.get()
    _require(scope is not None, 'router_audio_scope_required')
    _usable(scope)
    from app.services.abacus_router_transport_capture import _enabled, _send, _accept_response
    capture_enabled = _enabled(scope)
    _require(type(prepared) is adapter.PreparedAudioRouterRequest
             and scope.pending_request is prepared,
             'router_audio_send_not_reserved')
    scope.pending_request = None
    _require(adapter.inspect_audio_router_request(adapter.ENDPOINT, prepared.wire_kwargs(),
             purpose=prepared.purpose) == prepared, 'router_audio_request_unverified')
    if capture_enabled:
        response, capture = _send(scope, prepared)
        scope.http_status = response.status_code
        if not 200 <= response.status_code < 300:
            scope.http_error = _bounded_http_error(response, captured=True)
        _accept_response(response, capture)
        return response
    transport = httpx.HTTPTransport(retries=0, trust_env=False)
    with httpx.Client(transport=transport, trust_env=False, follow_redirects=False,
                      auth=None, cookies=None, event_hooks={'request': [], 'response': []},
                      headers={'Accept-Encoding': 'identity'}) as client:
        _usable(scope)
        with client.stream('POST', adapter.ENDPOINT, **prepared.wire_kwargs()) as response:
            scope.http_status = response.status_code
            if not 200 <= response.status_code < 300:
                scope.http_error = _bounded_http_error(response)
            _require(200 <= response.status_code < 300 and not response.history,
                     'router_audio_response_rejected')
            encodings = response.headers.get_list('content-encoding')
            _require(not encodings or encodings == ['identity'], 'router_audio_response_encoding_invalid')
            lengths = response.headers.get_list('content-length')
            _require(len(lengths) <= 1, 'router_audio_response_size_invalid')
            if lengths:
                length = lengths[0]
                _require(length.isascii() and length.isdecimal()
                         and 1 <= len(length) <= 9 and int(length) <= adapter.MAX_RESPONSE_BYTES,
                         'router_audio_response_size_invalid')
            chunks, size = [], 0
            for chunk in response.iter_raw(chunk_size=_CHUNK_BYTES):
                _require(type(chunk) is bytes and len(chunk) <= adapter.MAX_RESPONSE_BYTES - size,
                         'router_audio_response_size_invalid')
                size += len(chunk)
                chunks.append(chunk)
            # Preserve the actual response and request, caching only the bytes
            # already bounded before allocation; identity encoding was required.
            response._content = b''.join(chunks)
            return response


def _acknowledged_reservation(value, prepared):
    fields = {'policy_sha256', 'purpose', 'request_sha256', 'root_request_fingerprint',
              'reserved_at', 'asr_binding'}
    _require(type(value) is dict and set(value) == fields | {'reservation_sha256', *_FLAGS}
             and all(value[name] is flag for name, flag in _FLAGS.items())
             and value['purpose'] == prepared.purpose.value
             and value['request_sha256'] == prepared.request_sha256,
             'router_audio_reservation_unverified')
    raw = adapter._canonical({name: value[name] for name in fields})
    _require(hashlib.sha256(raw).hexdigest() == value['reservation_sha256'],
             'router_audio_reservation_unverified')
    return adapter._canonical(value)


def _execute(scope, prepared, **admission):
    from app.services.abacus_router_transport_capture import _enabled, _begin
    capture_enabled = _enabled(scope)
    reservation = _acknowledged_reservation(scope.journal.reserve(
        prepared.purpose, prepared, **admission), prepared)
    _usable(scope)
    if capture_enabled:
        _begin(scope, prepared, prepared.purpose.value, reservation)
    scope.pending_request = prepared
    response = _send_once(prepared)
    settled = scope.journal.settle(prepared.purpose, prepared, response)
    observed = adapter.observe_audio_router_response(prepared, response)
    _require(type(settled) is dict and set(settled) == {'result', 'evidence', *_FLAGS}
             and all(settled[name] is flag for name, flag in _FLAGS.items())
             and adapter._canonical(settled['result']) == adapter._canonical(observed.result)
             and adapter._canonical(settled['evidence']) == adapter._canonical(observed.evidence),
             'router_audio_settlement_unverified')
    artifact = AcknowledgedAudioReview(prepared, response, observed, reservation,
                                       adapter._canonical(settled))
    scope.artifacts[prepared.purpose] = artifact
    return artifact


def _run(purpose, audio_bytes, expected_narration=None):
    scope = _SCOPE.get()
    _require(scope is not None, 'router_audio_scope_required')
    try:
        # An earlier purpose's response is not an HTTP result for this attempt.
        # The first terminal failure snapshot is retained independently below.
        scope.http_status = None
        scope.http_error = {}
        _usable(scope)
        _require(purpose not in scope.attempted, 'router_audio_scope_already_attempted')
        scope.attempted.add(purpose)
        if purpose is _ASR:
            prepared = adapter.prepare_blind_asr_request(audio_bytes, api_key=scope.key)
            return _execute(scope, prepared)
        _require(_ASR in scope.artifacts, 'router_audio_asr_unacknowledged')
        asr = scope.artifacts[_ASR]
        # Never accept caller-supplied ASR results or a detached pass marker.
        # Re-observe the actual ACKed artifacts before a second reservation.
        report = validate_retained_router_asr(asr.prepared, asr.response, asr.observed,
            expected_narration=expected_narration, original_audio=asr.prepared.audio)
        _require(report['component_pass'] is True, 'router_audio_asr_not_exact')
        prepared = adapter.prepare_audio_prosody_request(audio_bytes, api_key=scope.key,
            expected_narration=expected_narration, system_instruction=audio_qc._PROSODY_SYSTEM_INSTRUCTION,
            json_schema=audio_qc._PROSODY_REVIEW_SCHEMA)
        _require(prepared.audio == asr.prepared.audio, 'router_audio_original_audio_changed')
        return _execute(scope, prepared, expected_narration=expected_narration,
                        asr_result=asr.observed.result)
    except SpendBlocked as error:
        scope.failed = True
        # Journal/adapter failures have fixed local codes. Do not preserve an
        # arbitrary exception string even if a dependency raises SpendBlocked.
        code = str(error)
        if re.fullmatch(r'(?:router_audio|abacus_router_audio|router_transport_capture)_[a-z_]{1,120}', code) is None:
            code = 'router_audio_runtime_outcome_unverified'
        if not scope.failure:
            scope.failure = {'purpose': purpose.value, 'reason': code,
                             'http_status': scope.http_status, **_FLAGS}
            if scope.http_error:
                scope.failure['provider_error'] = scope.http_error
        if code != str(error):
            raise SpendBlocked(code) from None
        raise
    except Exception:
        scope.failed = True
        if not scope.failure:
            scope.failure = {'purpose': purpose.value, 'reason': 'router_audio_runtime_outcome_unverified',
                             'http_status': scope.http_status, **_FLAGS}
            if scope.http_error:
                scope.failure['provider_error'] = scope.http_error
        raise SpendBlocked('router_audio_runtime_outcome_unverified') from None
    finally:
        scope.pending_request = None


def run_blind_asr(audio_bytes):
    """One blind original-audio request; no expected text or caller credential."""
    return _run(_ASR, audio_bytes)


def run_prosody(audio_bytes, *, expected_narration):
    """Second purpose for the same audio, after actual acknowledged exact ASR."""
    return _run(_PROSODY, audio_bytes, expected_narration)
