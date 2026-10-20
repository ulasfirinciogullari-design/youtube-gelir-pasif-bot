from copy import deepcopy
import json

import httpx
import pytest

from app.services import commissioning_reasoning as native
from app.services.production_spend import SpendBlocked
from test_commissioning_reasoning import setup, commissioned, client, payload, records, run, RESULT
from test_production_cash_disabled import dump


def failure(status=500):
    return status, json.dumps({'error': {'code': status, 'message': 'Internal error encountered.',
        'status': 'INTERNAL' if status == 500 else 'UNAVAILABLE'}}).encode()


@pytest.fixture(autouse=True)
def no_delay(monkeypatch):
    monkeypatch.setattr(native, '_sleep', lambda seconds: None)


def test_captured_server_error_uses_distinct_admission_then_replays_success(setup):
    before = dump(setup[0].client)
    setup[2].side_effect = [failure(), (200, json.dumps(payload()).encode())]
    assert run(setup) == RESULT
    rows = records(setup)
    assert len(rows) == 2 and setup[2].call_count == 2
    first = next(row for row in rows if 'transient_parent_request_sha256' not in row)
    second = next(row for row in rows if 'transient_parent_request_sha256' in row)
    assert second['transient_parent_request_sha256'] == first['request_sha256']
    assert second['max_list_cost_micro_usd'] == first['max_list_cost_micro_usd']
    captured = dump(setup[0].client)
    assert all(captured[key] == value for key, value in before.items())
    assert run(setup) == RESULT and setup[2].call_count == 2
    assert dump(setup[0].client) == captured


def test_repeated_explicit_failures_stop_at_three_and_never_reopen_on_replay(setup):
    setup[2].return_value = failure()
    for _ in range(2):
        with pytest.raises(SpendBlocked, match='response_unverified'):
            run(setup)
    assert setup[2].call_count == len(records(setup)) == 3


def test_unknown_retry_outcome_cannot_create_a_third_attempt(setup):
    setup[2].side_effect = [failure(), httpx.ReadTimeout('unknown')]
    with pytest.raises(SpendBlocked, match='outcome_unknown'):
        run(setup)
    with pytest.raises(SpendBlocked, match='previous_outcome_unknown'):
        run(setup)
    assert setup[2].call_count == len(records(setup)) == 2


def test_day_and_lineage_admission_still_limits_server_error_recovery(setup, monkeypatch):
    monkeypatch.setattr(native, 'MAX_LINEAGE', 1)
    setup[2].return_value = failure()
    with pytest.raises(SpendBlocked, match='capacity'):
        run(setup)
    assert setup[2].call_count == len(records(setup)) == 1


@pytest.mark.parametrize('damage', ['quota', 'output', 'usage', 'mismatch', 'malformed'])
def test_ambiguous_or_nontransient_response_is_preserved_without_retry(setup, damage):
    status, raw = failure()
    data = json.loads(raw)
    if damage == 'quota': status = 429; data['error']['code'] = 429
    if damage == 'output': data['candidates'] = payload()['candidates']
    if damage == 'usage': data['usageMetadata'] = payload()['usageMetadata']
    if damage == 'mismatch': data['error']['code'] = 400
    setup[2].return_value = (status, b'not-json' if damage == 'malformed' else json.dumps(data).encode())
    for _ in range(2):
        with pytest.raises(SpendBlocked, match='response_unverified'):
            run(setup)
    assert setup[2].call_count == len(records(setup)) == 1


def test_parent_response_change_blocks_retry_admission_before_transport(setup, monkeypatch):
    setup[2].return_value = failure()
    def change(seconds):
        key = next(setup[0].client.scan_iter(match=native.PREFIX + 'response:*'))
        row = json.loads(setup[0].client.get(key)); row['http_status'] = 200
        setup[0].client.set(key, native._raw(row))
    monkeypatch.setattr(native, '_sleep', change)
    with pytest.raises(SpendBlocked, match='transient_parent_unverified'):
        run(setup)
    assert setup[2].call_count == len(records(setup)) == 1
