"""Capture selects actual new completion journals without altering old18."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import abacus_router_review_runtime as runtime
from app.services import retained_review_completion_plan as plan
from app.services import production_connection_continuity as continuity
from app.services.production_spend import SpendBlocked, SpendPolicy
from test_abacus_router_transport_capture import storage_fixture, decode
from test_abacus_router_review_runtime import generate, STORY, payload
from test_retained_review_completion_plan import completed_probe, commission
from test_retained_router_protocol_probe import frozen_three
from test_retained_review_credential_successor import prepared, records
from test_abacus_router_protocol_diagnostic import wire, forbid_live_transport
from test_abacus_router_audio_review_journal import case, source, NOW
from test_production_connection_continuity import _dump


def setup(box, monkeypatch):
    cap = commission(box)
    storage_fixture(box, monkeypatch)
    box.config.studio_abacus_router_retained_review_enabled = True
    foundation = SimpleNamespace(client=box.client, clock=lambda: NOW,
                                 policy=SpendPolicy(**{name: 0 for name in runtime._CAPS}))
    monkeypatch.setattr(runtime.spending, 'configured_ledger', Mock(return_value=foundation))
    box.wire.chunks = [runtime._canonical(payload())]
    return cap


def test_actual_completion_scope_captures_in_selected_namespace_and_keeps_old18(completed_probe, monkeypatch):
    box = completed_probe
    cap = setup(box, monkeypatch)
    old = records(box.client, plan.HISTORICAL_KEYS)
    with runtime.retained_router_review_scope(continuity.LEAF_ID,
            completion_plan=cap, capture_transport=True) as scope:
        assert generate() == {'accepted': False}
        receipt = scope.transport_captures[STORY]
        assert receipt['binding']['journal_keys'] == list(plan.STORY_KEYS)
        assert receipt['anchor_key'].startswith(plan.STORY_KEYS[0].rsplit(':', 1)[0] + ':transport_capture:v1:')
        context, prepared_bytes, wire_bytes, body = decode(box, receipt)
        assert body == box.wire.chunks[0] and wire_bytes == box.wire.calls[-1].content
        assert context['binding']['request_sha256'] == context['reservation']['request_sha256']
        assert context['prepared_sha256'] == runtime.hashlib.sha256(prepared_bytes).hexdigest()
    assert records(box.client, plan.HISTORICAL_KEYS) == old
    assert len(box.wire.calls) == 2 and len(box.s3.puts) == 1


@pytest.mark.parametrize('damage', ['old_probe', 'wrong_cap', 'unknown'])
def test_completion_history_or_unknown_cannot_be_bypassed_by_capture(completed_probe, monkeypatch, damage):
    box = completed_probe
    cap = setup(box, monkeypatch)
    if damage == 'old_probe':
        from app.services.retained_router_protocol_probe import PROBE_KEYS
        box.client.set(PROBE_KEYS[0], '{}')
    elif damage == 'wrong_cap':
        cap = object()
    else:
        from app.services.abacus_router_review_journal import RouterReviewJournal
        RouterReviewJournal(box.client, clock=lambda: NOW, completion_plan=cap).reserve(STORY, box.new_request)
    before = _dump(box.client)
    with pytest.raises(SpendBlocked):
        with runtime.retained_router_review_scope(continuity.LEAF_ID,
                completion_plan=cap, capture_transport=True):
            generate()
    assert _dump(box.client) == before and len(box.wire.calls) == 1 and not box.s3.puts


@pytest.mark.parametrize('replacement', [None, object()])
@pytest.mark.parametrize('stage', ['before_reserve', 'after_reserve'])
def test_mutable_scope_cannot_downgrade_mandatory_completion_capture(
        completed_probe, monkeypatch, replacement, stage):
    box = completed_probe
    cap = setup(box, monkeypatch)
    with runtime.retained_router_review_scope(continuity.LEAF_ID,
            completion_plan=cap, capture_transport=True) as scope:
        before = _dump(box.client)
        if stage == 'before_reserve':
            scope._transport_session = replacement
        else:
            original = scope.journal.reserve
            def reserve(*args):
                result = original(*args)
                scope._transport_session = replacement
                return result
            monkeypatch.setattr(scope.journal, 'reserve', reserve)
        with pytest.raises(SpendBlocked): generate()
        assert scope.evidence == {} and scope.transport_captures == {}
        if stage == 'before_reserve': assert _dump(box.client) == before
    assert len(box.wire.calls) == 1 and not box.s3.puts
    assert records(box.client, plan.HISTORICAL_KEYS) == box.historical_records
