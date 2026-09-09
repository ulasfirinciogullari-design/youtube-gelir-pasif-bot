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
import json
import threading

import httpx

from app.services import production_spend_runtime as spending
from app.services.abacus_router_adapter import (
    ENDPOINT, MAX_RESPONSE_BYTES, ObservedRouterResult, prepare_router_request,
)
from app.services.abacus_router_review_journal import PURPOSES, RouterReviewJournal
from app.services.production_connection_continuity import LEAF_ID
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
    failed: bool = False
    closed: bool = False

    def __repr__(self):
        return '<RetainedRouterReviewScope private>'

    @property
    def evidence(self):
        return json.loads(json.dumps(self.observations, allow_nan=False))


@contextmanager
def retained_router_review_scope(source_task_id):
    """Only the explicit retained-review entry may open this synchronous scope."""
    _require(_SCOPE.get() is None, 'router_review_scope_nested')
    _require(type(source_task_id) is str and source_task_id == LEAF_ID,
             'router_review_source_not_supported')
    _zero_cash_guard()
    try:
        foundation = spending.configured_ledger()
        # Access the existing configured client/clock only. Do not invoke any
        # financial method: old cash remains unknown and no USD permit is made.
        _require(all(type(getattr(foundation.policy, name, None)) is int
                     and getattr(foundation.policy, name) == 0 for name in _CAPS),
                 'router_review_zero_cash_required')
        scope = _ReviewScope(RouterReviewJournal(foundation.client, clock=foundation.clock),
                             threading.get_ident())
    except SpendBlocked:
        raise
    except Exception:
        raise SpendBlocked('router_review_runtime_unavailable') from None
    token = _SCOPE.set(scope)
    try:
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


def _send_once(prepared):
    """Private fixed HTTPX transport; no caller-provided sender or retry path."""
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
        scope.attempted.add(purpose)
        config = _zero_cash_guard()
        prepared = prepare_router_request(parts, api_key=getattr(config, 'abacus_api_key', None),
            system_instruction=system_instruction, json_schema=json_schema, max_tokens=max_tokens)
        scope.journal.reserve(purpose, prepared)  # Only an acknowledged first return permits a send.
        current = _zero_cash_guard()
        # Keep the reserved private key/body frozen while checking live config
        # did not switch account during the reservation transaction.
        _require(getattr(current, 'abacus_api_key', None)
                 == dict(prepared._header_pairs)['authorization'][len('Bearer '):],
                 'router_review_runtime_credential_changed')
        response = _send_once(prepared)
        observed = scope.journal.settle(purpose, prepared, response)
        _require(type(observed) is ObservedRouterResult, 'router_review_settlement_unverified')
        scope.observations[purpose] = observed.evidence
        return observed.result
    except SpendBlocked:
        scope.failed = True
        raise
    except Exception:
        scope.failed = True
        # Never expose the transport exception, provider body, key or prompts.
        raise SpendBlocked('router_review_runtime_outcome_unverified') from None
