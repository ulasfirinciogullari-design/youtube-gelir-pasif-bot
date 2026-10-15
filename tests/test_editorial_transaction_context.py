"""Real Redis CAS during the director's outer exception handler, no paid HTTP."""
import json
import sys
from unittest.mock import Mock

import pytest
from redis.exceptions import WatchError

from app.services import production_included_router as included
from app.services import included_stock_pool as pool
from app.services.production_spend import SpendBlocked
from test_production_included_router import client, commissioned, CONTEXT, RESULT, response, observe_router_response


@pytest.mark.parametrize('phase', ['reserve', 'settle', 'failure_record'])
def test_real_uncommitted_conflict_inside_editorial_handler_retries_only_local_commit(commissioned, monkeypatch, phase):
    ledger, _, prepared = commissioned
    identity = None
    if phase != 'reserve': identity, _ = ledger.reserve(CONTEXT, 'editorial', prepared)
    original = included.IncludedRouterLedger._ack
    calls = []
    def interfere(pipe, expected):
        calls.append(1)
        if len(calls) == 1:
            ledger.client.set(included.ANCHOR_KEY, ledger.client.get(included.ANCHOR_KEY))
        return original(pipe, expected)
    monkeypatch.setattr(included.IncludedRouterLedger, '_ack', staticmethod(interfere))
    try:
        raise ValueError('The story needs an editorial repair.')
    except ValueError as original_editorial_error:
        if phase == 'reserve':
            identity, outcome = ledger.reserve(CONTEXT, 'editorial', prepared)
            assert outcome is None
        elif phase == 'settle':
            observed = observe_router_response(prepared, response(prepared))
            assert included._result(prepared, ledger.settle(identity, prepared, observed)) == RESULT
        else:
            ledger.record_failure(identity, prepared, response(prepared), original_editorial_error)
        assert sys.exc_info()[1] is original_editorial_error
    assert len(calls) == 2
    requests = json.loads(ledger.client.get(included.JOURNAL_KEY))['requests']
    assert len(requests) == 1
    if phase == 'failure_record':
        assert requests[identity]['outcome'] is None
        record = json.loads(ledger.client.get(included.PREFIX + 'failure:' + identity))
        assert record['retry_allowed'] is False and record['http_status'] == 200


def test_shared_local_transaction_handles_inherited_context_without_replaying_provider():
    calls = []
    @pool._local_transaction
    def commit():
        calls.append(1)
        if len(calls) == 1: raise WatchError('Watched variable changed.')
        return 'recorded'
    try: raise ValueError('prior editorial rejection')
    except ValueError: assert commit() == 'recorded'
    assert len(calls) == 2


@pytest.mark.parametrize('damage', ['cause', 'new_context', 'transport_text', 'subclass'])
def test_new_transport_chain_still_stops_after_one_local_attempt(damage):
    calls = []
    class AlternateWatch(WatchError): pass
    @pool._local_transaction
    def commit():
        calls.append(1)
        if damage == 'cause': raise WatchError('Watched variable changed.') from ConnectionError('lost ACK')
        if damage == 'new_context':
            try: raise ConnectionError('lost ACK')
            except ConnectionError: raise WatchError('Watched variable changed.')
        if damage == 'subclass': raise AlternateWatch('Watched variable changed.')
        raise WatchError('ConnectionError while watching keys')
    try: raise ValueError('outer editorial rejection')
    except ValueError:
        with pytest.raises(WatchError): commit()
    assert len(calls) == 1


@pytest.mark.parametrize('stage', ['research', 'director_qc', 'final_visual_qc_ai_repair', 'upload'])
def test_unverified_recording_can_hold_unpublished_work_without_approving_or_retrying(stage):
    from app.services.production_failures import classify_failure, classified_hold_reason
    for code in ('included_router_reservation_uncertain', 'included_router_settlement_uncertain'):
        error = SpendBlocked(code);classification = classify_failure(error, stage)
        record = {'error': code, 'failure_stage': stage, 'failure_classification': classification}
        assert classified_hold_reason(record) == (None if stage == 'upload' else 'review_unverified')
        # Read existing terminal records under the same exact-code contract;
        # the old classification and its content hash must not be rewritten.
        classification.update(code='spending_blocked', category='spending_blocked')
        assert classified_hold_reason(record) == (None if stage == 'upload' else 'review_unverified')
