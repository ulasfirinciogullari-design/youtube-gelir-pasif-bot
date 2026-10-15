"""Reconciled opening usage consumes caps and preserves paid replay fences."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest

from app.services.production_spend import LEDGER_KEY, OpeningReservation, SpendBlocked, SpendQuote
from test_production_spend import case, reserve


def entry(number=1, **changes):
    value = OpeningReservation(
        day='2026-09-08', request_key=f'request_{number:08}', channel_id='channel_AAAAA',
        lineage_id=f'lineage_{number:08}', kind='shorts',
        quote=SpendQuote('fixture', 'fixture-model', 1_000_000, 'fixture-v1'))
    return replace(value, **changes)


def initialize(ledger, entries=None, **changes):
    args = {'month': '2026-09', 'reservations': [entry()] if entries is None else entries,
            'reconciliation_sha256': 'a' * 64}
    return ledger.initialize_reconciled(**{**args, **changes})


def test_imported_usage_debits_existing_month_day_and_channel(case):
    client, ledger, _ = case
    assert initialize(ledger, [entry(1, day='2026-09-07'), entry(2)]) is True
    snapshot = ledger.snapshot()
    assert snapshot['remaining_micro'] == 8_000_000
    assert snapshot['period'] == {
        'month': '2026-09', 'used_micro': 2_000_000,
        'days': {'2026-09-07': 1_000_000, '2026-09-08': 1_000_000},
        'channels': {'channel_AAAAA': 2_000_000}}
    audit = json.loads(client.hget(LEDGER_KEY, 'opening_reconciliation'))
    assert audit['request_count'] == 2 and audit['reserved_micro'] == 2_000_000
    assert audit['reconciliation_sha256'] == 'a' * 64
    assert client.ttl(LEDGER_KEY) == -1
    for number in range(3, 6):
        reserve(ledger, number)
    with pytest.raises(SpendBlocked, match='day_limit'):
        reserve(ledger, 6)


def test_imported_paid_intent_cannot_be_replayed_or_obtain_new_lineage_money(case):
    _, ledger, _ = case
    initialize(ledger)
    with pytest.raises(SpendBlocked, match='already_reserved'):
        reserve(ledger, 1)
    reserve(ledger, 2, lineage_id='lineage_00000001')
    with pytest.raises(SpendBlocked, match='lineage_limit'):
        reserve(ledger, 3, lineage_id='lineage_00000001')
    with pytest.raises(SpendBlocked, match='lineage_binding'):
        reserve(ledger, 4, lineage_id='lineage_00000001', kind='long')


def test_import_is_idempotent_after_new_spend_and_never_resets_an_existing_ledger(case):
    client, ledger, clock = case
    initialize(ledger)
    reserve(ledger, 2)
    before = client.dump(LEDGER_KEY)
    clock[0] += timedelta(days=1)
    assert initialize(ledger) is False
    assert ledger.initialize() is False
    assert client.dump(LEDGER_KEY) == before
    with pytest.raises(SpendBlocked, match='opening_mismatch'):
        initialize(ledger, [])
    with pytest.raises(SpendBlocked, match='opening_mismatch'):
        initialize(ledger, reconciliation_sha256='b' * 64)
    assert client.dump(LEDGER_KEY) == before


def test_cannot_import_into_legacy_initialized_ledger(case):
    client, ledger, _ = case
    ledger.initialize()
    before = client.dump(LEDGER_KEY)
    with pytest.raises(SpendBlocked, match='opening_mismatch'):
        initialize(ledger)
    assert client.dump(LEDGER_KEY) == before


def test_over_limit_prior_usage_is_preserved_and_blocks_further_spending(case):
    _, ledger, _ = case
    initialize(ledger, [entry(quote=SpendQuote('fixture', 'fixture-model', 11_000_000, 'fixture-v1'))])
    assert ledger.snapshot()['remaining_micro'] == -1_000_000
    with pytest.raises(SpendBlocked, match='month_limit'):
        reserve(ledger, 2)


@pytest.mark.parametrize('changes', [
    {'day': '2026-08-31'}, {'day': '2026-09-09'}, {'day': '2026-09-31'},
    {'day': '2026-9-8'}, {'day': None}, {'kind': 'planning'}, {'kind': True},
    {'request_key': 'bad'}, {'channel_id': ''}, {'lineage_id': None},
    {'quote': {'maximum_micro': 100}},
    {'quote': SpendQuote('fixture', 'fixture-model', True, 'fixture-v1')},
    {'quote': SpendQuote('fixture', 'fixture-model', -1, 'fixture-v1')},
])
def test_invalid_opening_row_cannot_partially_initialize(case, changes):
    client, ledger, _ = case
    with pytest.raises(SpendBlocked):
        initialize(ledger, [entry(), entry(2, **changes)])
    assert not client.exists(LEDGER_KEY)


@pytest.mark.parametrize('changes', [
    {'month': '2026-08'}, {'month': '2026-10'}, {'month': None},
    {'reconciliation_sha256': ''}, {'reconciliation_sha256': 'secret-not-a-digest'},
    {'reservations': None}, {'reservations': [entry()] * 10001},
])
def test_invalid_reconciliation_metadata_never_writes(case, changes):
    client, ledger, _ = case
    with pytest.raises(SpendBlocked):
        initialize(ledger, **changes)
    assert not client.exists(LEDGER_KEY)


def test_duplicate_intents_and_inconsistent_lineage_fail_before_writes(case):
    client, ledger, _ = case
    for entries in ([entry(), entry()],
                    [entry(), entry(2, lineage_id='lineage_00000001', kind='long')],
                    [entry(), entry(2, lineage_id='lineage_00000001', channel_id='channel_BBBBB')]):
        with pytest.raises(SpendBlocked):
            initialize(ledger, entries)
        assert not client.exists(LEDGER_KEY)


def test_imported_lineages_and_requests_survive_month_rollover(case):
    client, ledger, clock = case
    initialize(ledger, [entry(), entry(2, lineage_id='lineage_00000001')])
    clock[0] = datetime(2026, 10, 1, tzinfo=timezone.utc)
    with pytest.raises(SpendBlocked, match='already_reserved'):
        reserve(ledger, 1)
    with pytest.raises(SpendBlocked, match='lineage_limit'):
        reserve(ledger, 3, lineage_id='lineage_00000001')
    reserve(ledger, 3)
    assert ledger.snapshot()['remaining_micro'] == 9_000_000
    assert json.loads(client.hget(LEDGER_KEY, 'period:2026-09'))['used_micro'] == 2_000_000


def test_lost_import_reply_preserves_usage_and_replay_fence(case, monkeypatch):
    client, ledger, _ = case
    original = client.pipeline
    def pipeline(*args, **kwargs):
        pipe = original(*args, **kwargs)
        execute = pipe.execute
        def lose_reply(*args, **kwargs):
            execute(*args, **kwargs)
            raise ConnectionError('simulated lost reply')
        pipe.execute = lose_reply
        return pipe
    monkeypatch.setattr(client, 'pipeline', pipeline)
    with pytest.raises(SpendBlocked, match='store_unavailable'):
        initialize(ledger)
    monkeypatch.setattr(client, 'pipeline', original)
    assert initialize(ledger) is False
    with pytest.raises(SpendBlocked, match='already_reserved'):
        reserve(ledger)
    assert ledger.snapshot()['period']['used_micro'] == 1_000_000


def test_concurrent_initializers_cannot_replace_or_add_opening_usage(case):
    _, ledger, _ = case
    def attempt(number):
        try:
            return initialize(ledger, [entry(number)])
        except SpendBlocked:
            return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(attempt, range(8)))
    assert sum(results) == 1
    assert ledger.snapshot()['period']['used_micro'] == 1_000_000
