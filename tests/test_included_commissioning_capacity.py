"""Owner-authorized setup continues without resetting the old daily ledger."""
from copy import deepcopy
import json
import uuid

import pytest

from app.services import production_included_router as included, production_continuation as continuation
from app.services.production_spend import SpendBlocked, LEDGER_KEY
from test_production_included_router import commissioned, client, CONTEXT, CHANNEL, request, NOW


def activate(ledger):
    continuation.initialize(ledger.client, {'version': 1, 'kind': 'continuous_commissioning',
        'allowed_channels': [CHANNEL], 'authorized_at': NOW.isoformat(), 'owner_evidence_sha256': 'c' * 64})


def reserve(ledger, index):
    context = {**CONTEXT, 'lineage_id': str(uuid.UUID(int=index + 1))}
    ledger.client.hset(LEDGER_KEY, 'binding:' + context['lineage_id'], included._raw(context))
    return ledger.reserve(context, 'research', request('Actual attempt ' + str(index)))


def test_cross_day_trial_limit_preserves_all_prior_unknowns_and_policy(commissioned):
    ledger, policy, _ = commissioned
    for index in range(4): reserve(ledger, index)
    before_policy = ledger.client.get(ledger.state_key)
    old = deepcopy(json.loads(ledger.client.get(ledger.journal_key))['requests'])
    with pytest.raises(SpendBlocked, match='daily_limit'): reserve(ledger, 4)
    activate(ledger)
    for index in range(4, 7): reserve(ledger, index)
    journal = json.loads(ledger.client.get(ledger.journal_key))['requests']
    assert len(journal) == 7 and all(journal[k] == v for k, v in old.items())
    assert all(row['outcome'] is None for row in journal.values())
    assert all(row.get('continuation_authority_sha256') for k, row in journal.items() if k not in old)
    assert ledger.client.get(ledger.state_key) == before_policy
    with ledger.client.pipeline() as pipe:
        capacity = ledger.check_capacity(pipe, CHANNEL, minimum_requests=8)
        pipe.multi(); pipe.ping(); assert pipe.execute() == [True]
    assert capacity['day_requests_remaining'] == 7440 - 7
    ledger.client.delete(continuation.ACTIVE_KEY)
    # Past authorized receipts remain valid after deactivation; new requests
    # once again obey the original cap, and no history is erased to fit it.
    with ledger.client.pipeline() as pipe:
        _, saved = ledger._read(pipe)
        pipe.multi(); pipe.ping(); assert pipe.execute() == [True]
    assert saved['requests'] == journal
    with pytest.raises(SpendBlocked, match='daily_limit'): reserve(ledger, 7)


def test_unresolved_request_and_episode_retry_limits_still_apply(commissioned):
    ledger, _, prepared = commissioned
    activate(ledger)
    ledger.reserve(CONTEXT, 'research', prepared)
    with pytest.raises(SpendBlocked, match='previous_outcome_unknown'):
        ledger.reserve(CONTEXT, 'research', prepared)
    for i in range(2): ledger.reserve(CONTEXT, 'research', request('Distinct ' + str(i)))
    with pytest.raises(SpendBlocked, match='episode_limit'):
        ledger.reserve(CONTEXT, 'research', request('Still bounded'))
