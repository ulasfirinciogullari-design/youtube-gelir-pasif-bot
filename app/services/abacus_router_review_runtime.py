"""Explicit synchronous router reviews of the one retained Capital episode.

This default-inert entry never commissions a journal, initializes cash history,
resolves a new paid family, retries, falls back, generates media or grants QA.
A permanent journal reservation must be acknowledged before the single POST;
its observed result is returned only after the settlement acknowledgement.
Unknown outcomes retain the slot. A failed scope remains active until exit so
callers cannot silently switch to an ordinary paid provider.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import re
import threading

import httpx

from app.services import production_spend_runtime as spending
from app.services.abacus_router_adapter import (
    ENDPOINT, OPERATION, MAX_RESPONSE_BYTES, ObservedRouterResult,
    observe_router_response, prepare_router_request,
)
from app.services.abacus_router_review_journal import PURPOSES, RouterReviewJournal
from app.services.production_connection_continuity import LEAF_ID, ROOT_ID
from app.services.production_spend import SpendBlocked


_SCOPE = ContextVar('retained_abacus_router_review', default=None)
_CAPS = {'monthly_micro', 'daily_micro', 'channel_monthly_micro',
         'shorts_micro', 'long_micro', 'derived_micro'}
_CHUNK_BYTES = 64 * 1024


def _require(condition, code):
    if not condition:
        raise SpendBlocked(code)


def _zero_cash_guard():
    """Check current explicit configuration, without reading/creating history."""
    try:
        config = spending.settings
        _require(getattr(config, 'studio_spend_enforcement', False) is True
                 and getattr(config, 'studio_abacus_router_retained_review_enabled', False) is True,
                 'router_review_runtime_not_enabled')
        raw = getattr(config, 'studio_spend_policy_json', None)
        _require(type(raw) is str and len(raw.encode('utf-8')) <= 4096,
                 'router_review_zero_cash_required')
        def pairs(items):
            out = {}
            for key, value in items:
                _require(key not in out, 'router_review_zero_cash_required')
                out[key] = value
            return out
        policy = json.loads(raw, object_pairs_hook=pairs,
                            parse_constant=lambda _: _require(False, 'router_review_zero_cash_required'))
        _require(type(policy) is dict and set(policy) == _CAPS
                 and all(type(value) is int and value == 0 for value in policy.values()),
                 'router_review_zero_cash_required')
        return config
    except SpendBlocked:
        raise
    except Exception:
        raise SpendBlocked('router_review_zero_cash_required') from None


@dataclass(repr=False)
class _ReviewScope:
    journal: RouterReviewJournal
    owner_thread: int
    attempted: set = field(default_factory=set)
    observations: dict = field(default_factory=dict)
    _artifacts: dict = field(default_factory=dict)
    _transport_session: object = None
    _transport_captures: dict = field(default_factory=dict)
    failed: bool = False
    closed: bool = False

    def __repr__(self):
        return '<RetainedRouterReviewScope private>'

    @property
    def evidence(self):
        return json.loads(json.dumps(self.observations, allow_nan=False))

    @property
    def transport_captures(self):
        from app.services.abacus_router_transport_capture import _receipts
        return _receipts(self)

    @property
    def transport_capture_fallbacks(self):
        from app.services.abacus_router_transport_capture import _fallbacks
        return _fallbacks(self)


@contextmanager
def retained_router_review_scope(source_task_id, *, successor=None, completion_plan=None,
                                 captured_story_continuation=None,
                                 capture_transport=False):
    """Only the explicit retained-review entry may open this synchronous scope."""
    _require(_SCOPE.get() is None, 'router_review_scope_nested')
    _require(type(source_task_id) is str and source_task_id == LEAF_ID,
             'router_review_source_not_supported')
    _require(type(capture_transport) is bool
             and sum(value is not None for value in (successor, completion_plan, captured_story_continuation)) <= 1
             and (completion_plan is None and captured_story_continuation is None or capture_transport is True),
             'router_review_capture_option_invalid')
    _zero_cash_guard()
    try:
        foundation = spending.configured_ledger()
        if successor is not None:
            from app.services.retained_review_credential_successor import verify_scope_successor
            verify_scope_successor(foundation.client, successor)
        if captured_story_continuation is not None:
            from app.services.retained_review_captured_story_continuation import verify_scope_captured_story_continuation
            verify_scope_captured_story_continuation(foundation.client, captured_story_continuation)
        if completion_plan is not None:
            from app.services.retained_review_completion_plan import verify_scope_completion_plan
            verify_scope_completion_plan(foundation.client, completion_plan)
        # Access the existing configured client/clock only. Do not invoke any
        # financial method: old cash remains unknown and no USD permit is made.
        _require(all(type(getattr(foundation.policy, name, None)) is int
                     and getattr(foundation.policy, name) == 0 for name in _CAPS),
                 'router_review_zero_cash_required')
        scope = _ReviewScope(RouterReviewJournal(foundation.client, clock=foundation.clock, successor=successor,
                                               completion_plan=completion_plan,
                                               captured_story_continuation=captured_story_continuation),
                             threading.get_ident())
    except SpendBlocked:
        raise
    except Exception:
        raise SpendBlocked('router_review_runtime_unavailable') from None
    token = _SCOPE.set(scope)
    try:
        from app.services.abacus_router_transport_capture import _configure
        _configure(scope, 'story', capture_transport)
        yield scope
    finally:
        scope.closed = True
        _SCOPE.reset(token)


def retained_router_review_active():
    """True throughout scope, including failures; never imply a paid fallback."""
    return _SCOPE.get() is not None


def retained_router_review_approval_active():
    """Approval requires the still-open owning thread, unlike fallback locking."""
    scope = _SCOPE.get()
    if (scope is None or scope.closed or scope.failed
            or scope.owner_thread != threading.get_ident()):
        return False
    try:
        _zero_cash_guard()
        return True
    except SpendBlocked:
        return False


def retained_router_review_evidence():
    """Detached honest observations only; empty outside an explicit scope."""
    scope = _SCOPE.get()
    return scope.evidence if scope is not None else {}


def _artifact_scope():
    scope = _SCOPE.get()
    _require(scope is not None, 'router_review_scope_required')
    _require(not scope.closed and not scope.failed
             and scope.owner_thread == threading.get_ident(), 'router_review_scope_unusable')
    _zero_cash_guard()
    return scope


@dataclass(frozen=True, repr=False, init=False, slots=True)
class AcknowledgedRouterReview:
    """Private, scope-bound byte snapshots, never a QA or publication permit.

    No prepared/HTTPX object is retained: those objects contain credentials and
    mutable headers. Parsed properties always return detached JSON copies.
    """
    _purpose: str
    _snapshot: tuple

    def __new__(cls, *args, **kwargs):
        raise TypeError('router_review_artifact_private')

    def __repr__(self):
        return '<AcknowledgedRouterReview diagnostic redacted>'

    def _value(self, index):
        scope = _artifact_scope()
        _require(type(self) is AcknowledgedRouterReview
                 and scope._artifacts.get(self._purpose) is self._snapshot,
                 'router_review_artifact_unverified')
        return self._snapshot[index]

    @property
    def purpose(self):
        self._value(0)
        return self._purpose

    @property
    def prepared_body_bytes(self):
        return self._value(0)

    @property
    def request_body_bytes(self):
        return self._value(1)

    @property
    def response_body_bytes(self):
        return self._value(2)

    @property
    def result(self):
        return json.loads(self._value(3))

    @property
    def evidence(self):
        return json.loads(self._value(4))

    @property
    def reservation(self):
        return json.loads(self._value(5))

    @property
    def response_status_code(self):
        return self._value(6)

    @property
    def diagnostic_only(self):
        self._value(0)
        return True

    @property
    def qa_approved(self):
        self._value(0)
        return False

    @property
    def publish_eligible(self):
        self._value(0)
        return False


def retained_router_review_artifacts():
    """Return acknowledged captures only in the still-usable owning scope.

    This accessor performs no I/O. Each wrapper is bound to its original scope;
    keeping a wrapper or a copied context does not extend the access window.
    """
    scope = _artifact_scope()
    result = {}
    for purpose, snapshot in scope._artifacts.items():
        artifact = object.__new__(AcknowledgedRouterReview)
        object.__setattr__(artifact, '_purpose', purpose)
        object.__setattr__(artifact, '_snapshot', snapshot)
        result[purpose] = artifact
    return result


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      sort_keys=True, separators=(',', ':')).encode('utf-8')


def _acknowledged_reservation(value, purpose, prepared):
    fields = {'policy_sha256', 'purpose', 'request_sha256',
              'root_request_fingerprint', 'reserved_at'}
    _require(type(value) is dict and set(value) == fields | {'reservation_sha256'}
             and all(type(value[name]) is str for name in value)
             and value['purpose'] == purpose
             and value['request_sha256'] == prepared.request_sha256
             and value['root_request_fingerprint'] == spending._request_fingerprint(
                 {'lineage_id': ROOT_ID}, 'abacus', OPERATION, prepared.payload)
             and all(re.fullmatch(r'[0-9a-f]{64}', value[name]) for name in (
                 'policy_sha256', 'request_sha256', 'root_request_fingerprint', 'reservation_sha256'))
             and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', value['reserved_at']),
             'router_review_reservation_unverified')
    datetime.strptime(value['reserved_at'], '%Y-%m-%dT%H:%M:%SZ')
    _require(hashlib.sha256(_canonical({name: value[name] for name in fields})).hexdigest()
             == value['reservation_sha256'], 'router_review_reservation_unverified')
    return _canonical(value)


def _capture_review(prepared, response, observed, reservation, received):
    # Freeze the bytes received before settlement, then re-observe after its
    # ACK. Mutation of HTTPX caches during that transaction is not evidence.
    _require(received == (prepared._body_bytes, response.request.content,
                         response.content, response.status_code),
             'router_review_artifact_unverified')
    checked = observe_router_response(prepared, response)
    _require(type(observed) is ObservedRouterResult and observed == checked,
             'router_review_settlement_unverified')
    key = dict(prepared._header_pairs)['authorization'][len('Bearer '):]
    # Exact bodies cannot be silently redacted. Refuse an anomalous credential
    # echo, including JSON escapes, instead of retaining a secret in a capture.
    encoded_key = _canonical(key)[1:-1]
    # Assistant content is itself JSON encoded inside the outer JSON response;
    # inspect the decoded result too so a second layer of escapes cannot hide it.
    for raw in (*received[:3], checked._result_bytes, checked._evidence_bytes, reservation):
        _require(key.encode('ascii') not in raw and encoded_key not in _canonical(json.loads(raw)),
                 'router_review_artifact_credential_present')
    return (*received[:3], checked._result_bytes, checked._evidence_bytes,
            reservation, received[3])


def _send_once(prepared):
    """Private fixed HTTPX transport; no caller-provided sender or retry path."""
    scope = _SCOPE.get()
    from app.services.abacus_router_transport_capture import _enabled, _send, _accept_response
    if scope is not None and _enabled(scope):
        response, capture = _send(scope, prepared)
        _accept_response(response, capture)
        return response
    # A fresh pool avoids shared auth/cookie/header/hook state. Both the client
    # and transport ignore environment proxies. Identity encoding permits a
    # raw byte bound before decompression/allocation, including chunked bodies.
    transport = httpx.HTTPTransport(retries=0, trust_env=False)
    with httpx.Client(transport=transport, trust_env=False, follow_redirects=False,
                      auth=None, cookies=None, event_hooks={'request': [], 'response': []},
                      headers={'Accept-Encoding': 'identity'}) as client:
        with client.stream('POST', ENDPOINT, **prepared.wire_kwargs()) as response:
            _require(200 <= response.status_code < 300 and not response.history,
                     'router_review_response_rejected')
            encodings = response.headers.get_list('content-encoding')
            _require(not encodings or encodings == ['identity'], 'router_review_response_encoding_invalid')
            lengths = response.headers.get_list('content-length')
            _require(len(lengths) <= 1, 'router_review_response_size_invalid')
            if lengths:
                length = lengths[0]
                _require(length.isascii() and length.isdecimal()
                         and 1 <= len(length) <= 9 and int(length) <= MAX_RESPONSE_BYTES,
                         'router_review_response_size_invalid')
            chunks, size = [], 0
            for chunk in response.iter_raw(chunk_size=_CHUNK_BYTES):
                _require(type(chunk) is bytes and len(chunk) <= MAX_RESPONSE_BYTES - size,
                         'router_review_response_size_invalid')
                size += len(chunk)
                chunks.append(chunk)
            # HTTPX Response.read() populates this same byte cache, but performs
            # an unbounded join. Preserve the actual response/request and cache
            # only the bytes already bounded above; encoding was identity.
            response._content = b''.join(chunks)
            return response


def generate_retained_router_review(
    parts, *, purpose, system_instruction, json_schema, max_tokens=8192,
):
    """One acknowledged slot, one send, one acknowledged honest observation."""
    scope = _SCOPE.get()
    _require(scope is not None, 'router_review_scope_required')
    try:
        _require(not scope.closed and not scope.failed and scope.owner_thread == threading.get_ident(),
                 'router_review_scope_unusable')
        _require(type(purpose) is str and purpose in PURPOSES, 'router_review_purpose_invalid')
        _require(purpose not in scope.attempted, 'router_review_scope_already_attempted')
        if scope.journal._captured_story_continuation is not None:
            _require(purpose == PURPOSES[1], 'captured_story_continuation_story_forbidden')
            from app.services.retained_captured_story_scope import captured_story_predecessor
            captured_story_predecessor(scope)
        from app.services.abacus_router_transport_capture import _enabled, _begin
        capture_enabled = _enabled(scope)
        scope.attempted.add(purpose)
        config = _zero_cash_guard()
        builder = prepare_router_request
        if scope.journal._captured_story_continuation is not None:
            from app.services.retained_review_captured_story_continuation import uses_schema_compatibility, request_schema_name
            if uses_schema_compatibility(scope.journal._captured_story_continuation):
                from app.services.abacus_router_schema_compat import prepare_compatible_router_request
                from functools import partial
                builder = partial(prepare_compatible_router_request,
                    schema_name=request_schema_name(scope.journal._captured_story_continuation))
        prepared = builder(parts, api_key=getattr(config, 'abacus_api_key', None),
            system_instruction=system_instruction, json_schema=json_schema, max_tokens=max_tokens)
        reservation = _acknowledged_reservation(
            scope.journal.reserve(purpose, prepared), purpose, prepared)
        current = _zero_cash_guard()
        # Keep the reserved private key/body frozen while checking live config
        # did not switch account during the reservation transaction.
        _require(getattr(current, 'abacus_api_key', None)
                 == dict(prepared._header_pairs)['authorization'][len('Bearer '):],
                 'router_review_runtime_credential_changed')
        if capture_enabled:
            _begin(scope, prepared, purpose, reservation)
        response = _send_once(prepared)
        received = (prepared._body_bytes, response.request.content,
                    response.content, response.status_code)
        observed = scope.journal.settle(purpose, prepared, response)
        _require(type(observed) is ObservedRouterResult, 'router_review_settlement_unverified')
        capture = _capture_review(prepared, response, observed, reservation, received)
        scope._artifacts[purpose] = capture
        scope.observations[purpose] = observed.evidence
        return observed.result
    except SpendBlocked:
        scope.failed = True
        raise
    except Exception:
        scope.failed = True
        # Never expose the transport exception, provider body, key or prompts.
        raise SpendBlocked('router_review_runtime_outcome_unverified') from None
