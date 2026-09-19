"""One owning scope may carry the original sealed saved-STORY interpretation.

This binding does not manufacture a STORY observation, director approval or
artifact. Every access verifies the current continuation/source/capture records;
only the actual reader-issued object is returned, with its diagnostic limits.
"""
import threading
import weakref

from app.services import retained_review_captured_story_continuation as continuation
from app.services.production_spend import SpendBlocked

_BINDINGS = {}


def _require(value, reason='captured_story_scope_unverified'):
    if not value:
        raise SpendBlocked(reason)


def _scope(scope):
    from app.services import abacus_router_review_runtime as runtime
    from app.services import abacus_router_review_journal as journal
    from app.services import abacus_router_transport_capture as capture
    _require(type(scope) is runtime._ReviewScope and runtime._SCOPE.get() is scope
             and scope.owner_thread == threading.get_ident() and not scope.closed and not scope.failed,
             'captured_story_scope_unusable')
    _require(runtime._artifact_scope() is scope and type(scope.journal) is journal.RouterReviewJournal)
    cap = scope.journal._captured_story_continuation
    _require(scope.journal._successor is None and scope.journal._completion_plan is None
             and continuation.selected_keys(cap, 'story') == scope.journal.keys == continuation.VISUAL_KEYS
             and capture._enabled(scope) is True, 'captured_story_scope_selection_changed')
    return cap


def _verify(scope, cap, evidence, *, initial):
    qualification = continuation._evidence(evidence)
    encoded = continuation._raw(qualification)
    context = continuation._configuration()
    with scope.journal.client.pipeline() as pipe:
        manifest, states, raw, actual = continuation._read_control(pipe, current=True)
        _require(raw == continuation._issued_bytes(cap)
                 and continuation._raw(manifest['story_qualification']) == encoded
                 and actual == context[:2] and manifest['attestation']['runtime_head_sha'] == context[2],
                 'captured_story_scope_evidence_changed')
        if initial:
            _require(all(state['slots'] == {} for state in states.values()),
                     'captured_story_scope_already_attempted')
        continuation._ping(pipe)
    _require(continuation._configuration() == context and _scope(scope) is cap,
             'captured_story_scope_context_changed')
    return encoded, raw


def bind_captured_story_predecessor(scope, *, story_evidence):
    """Bind once before any request, following watched source/capture ACK."""
    cap = _scope(scope)
    identity = id(scope)
    _require(identity not in _BINDINGS and not scope.attempted and not scope.observations
             and not scope._artifacts and not scope._transport_captures,
             'captured_story_scope_already_bound_or_attempted')
    ref = weakref.ref(scope, lambda _: _BINDINGS.pop(identity, None))
    # A failed/uncertain binding is terminal for this scope; a second local
    # binding attempt cannot turn a lost read ACK into success.
    _BINDINGS[identity] = (ref, None)
    try:
        encoded, manifest = _verify(scope, cap, story_evidence, initial=True)
        _BINDINGS[identity] = (ref, (scope.journal, scope.journal.client, cap,
                                   story_evidence, encoded, manifest))
    except SpendBlocked:
        scope.failed = True
        raise
    except Exception:
        scope.failed = True
        raise SpendBlocked('captured_story_scope_binding_unverified') from None


def captured_story_predecessor(scope):
    """Return the same genuine sealed object in its still-current owning scope."""
    cap = _scope(scope)
    entry = _BINDINGS.get(id(scope))
    _require(entry is not None and entry[0]() is scope and entry[1] is not None,
             'captured_story_scope_binding_required')
    journal, client, original_cap, evidence, encoded, manifest = entry[1]
    _require(scope.journal is journal and scope.journal.client is client and cap is original_cap,
             'captured_story_scope_selection_changed')
    try:
        current, current_manifest = _verify(scope, cap, evidence, initial=False)
        _require(current == encoded and current_manifest == manifest,
                 'captured_story_scope_evidence_changed')
        return evidence
    except SpendBlocked:
        scope.failed = True
        raise
    except Exception:
        scope.failed = True
        raise SpendBlocked('captured_story_scope_read_unverified') from None
