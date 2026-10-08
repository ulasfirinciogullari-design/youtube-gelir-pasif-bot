"""Durable observed Abacus usage never refunds or opens a new paid attempt."""
from copy import deepcopy
import json
from unittest.mock import Mock

import pytest
import redis

from app.services.production_spend import LEDGER_KEY, SpendBlocked
from app.services import production_spend_runtime as runtime
from test_production_spend_runtime import case, job, ROOT, CHILD


URL = 'https://routellm.abacus.ai/v1/messages'


def body():
    return {'model': 'claude-haiku-4-5-20251001', 'system': 'Return JSON.',
            'messages': [{'role': 'user', 'content': 'PRIVATE-NARRATION-DO-NOT-PERSIST'}],
            'max_tokens': 512, 'thinking': {'type': 'disabled'}, 'stream': False,
            'service_tier': 'standard_only'}


def usage():
    return {'request_id': 'msg_test_safe_receipt', 'model': body()['model'],
            'input_tokens': 100, 'output_tokens': 20,
            'cache_creation_input_tokens': 0, 'cache_read_input_tokens': 0,
            'actual_micro': 200}


def reserve(sender=None, payload=None):
    sender = sender or Mock(return_value='accepted')
    runtime.paid_post(sender, URL, json=payload or body(), headers={
        'x-api-key': 'private-test-key', 'Content-Type': 'application/json',
        'anthropic-version': '2023-06-01'}, timeout=10)
    return sender


def observations(client):
    return {key: json.loads(value) for key, value in client.hgetall(LEDGER_KEY).items()
            if key.startswith('usage:')}


def test_observed_usage_persists_without_refund_or_replay(case):
    client, ledger = case
    sender = reserve()
    before = client.hgetall(LEDGER_KEY)
    reserved = ledger.snapshot()['period']['used_micro']
    runtime.record_abacus_usage(body(), usage())
    records = observations(client)
    assert len(records) == 1
    record = next(iter(records.values()))
    assert record['actual_micro'] == 200 < reserved
    assert record['accounting'] == 'observed_list_cost_not_settlement'
    assert {key: value for key, value in client.hgetall(LEDGER_KEY).items()
            if not key.startswith('usage:')} == before
    serialized = json.dumps(client.hgetall(LEDGER_KEY))
    assert 'PRIVATE-NARRATION' not in serialized and 'FAKE-KEY' not in serialized
    runtime.record_abacus_usage(body(), deepcopy(usage()))
    assert observations(client) == records
    with pytest.raises(SpendBlocked, match='already_reserved'):
        reserve(sender)
    assert sender.call_count == 1
    assert ledger.snapshot()['period']['used_micro'] == reserved


def test_child_repair_observes_same_root_request_once(case):
    client, _ = case
    reserve()
    runtime.record_abacus_usage(body(), usage())
    records = observations(client)
    job(client, CHILD, ROOT)
    token = runtime._TASK_ID.set(CHILD)
    try:
        runtime.record_abacus_usage(body(), usage())
        with pytest.raises(SpendBlocked, match='already_reserved'):
            reserve()
    finally:
        runtime._TASK_ID.reset(token)
    assert observations(client) == records


@pytest.mark.parametrize('change', ['different_prompt', 'different_root', 'different_connection'])
def test_usage_cannot_attach_to_changed_request_or_ownership(case, change):
    client, _ = case
    reserve()
    payload = body()
    if change == 'different_prompt':
        payload['messages'][0]['content'] = 'A separate request.'
    elif change == 'different_root':
        job(client, CHILD)
    else:
        channel_key = next(key for key in client.scan_iter(runtime._CHANNEL_PREFIX + '*'))
        record = json.loads(client.get(channel_key))
        record['connection_id'] = 'another_connection'
        client.set(channel_key, json.dumps(record))
    token = runtime._TASK_ID.set(CHILD if change == 'different_root' else ROOT)
    try:
        with pytest.raises(SpendBlocked):
            runtime.record_abacus_usage(payload, usage())
    finally:
        runtime._TASK_ID.reset(token)
    assert observations(client) == {}


@pytest.mark.parametrize('patch', [
    {'input_tokens': True}, {'output_tokens': -1}, {'actual_micro': 1.2},
    {'request_id': 'unsafe\nheader'}, {'request_id': 'x' * 161},
    {'prompt': 'private'}, {'model': 'another-model'}, {'actual_micro': 10_000_000_001},
])
def test_invalid_usage_has_no_persistence_effect(case, patch):
    client, _ = case
    reserve()
    before = client.hgetall(LEDGER_KEY)
    with pytest.raises(SpendBlocked, match='usage_invalid'):
        runtime.record_abacus_usage(body(), {**usage(), **patch})
    assert client.hgetall(LEDGER_KEY) == before


def test_usage_requires_prior_reservation_and_cannot_exceed_it(case):
    client, _ = case
    with pytest.raises(SpendBlocked):
        runtime.record_abacus_usage(body(), usage())
    assert observations(client) == {}
    reserve()
    with pytest.raises(SpendBlocked, match='binding_invalid'):
        runtime.record_abacus_usage(body(), {**usage(), 'actual_micro': 999_999_999})
    assert observations(client) == {}


def test_conflicting_second_receipt_cannot_replace_first(case):
    client, ledger = case
    reserve()
    runtime.record_abacus_usage(body(), usage())
    before = client.hgetall(LEDGER_KEY)
    with pytest.raises(SpendBlocked, match='usage_conflict'):
        runtime.record_abacus_usage(body(), {**usage(), 'request_id': 'msg_another'})
    assert client.hgetall(LEDGER_KEY) == before


def test_committed_usage_with_lost_reply_is_recoverable_without_new_spend(case, monkeypatch):
    client, ledger = case
    sender = reserve()
    reserved = ledger.snapshot()['period']['used_micro']
    original = client.pipeline
    state = {'lose_reply': True}

    class LostReply:
        def __init__(self):
            self.pipe = original()
            self.usage_write = False

        def __enter__(self):
            self.pipe.__enter__()
            return self

        def __exit__(self, *args):
            return self.pipe.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.pipe, name)

        def hset(self, name, *args, **kwargs):
            self.usage_write = bool(args and str(args[0]).startswith('usage:'))
            return self.pipe.hset(name, *args, **kwargs)

        def execute(self):
            result = self.pipe.execute()
            if self.usage_write and state['lose_reply']:
                state['lose_reply'] = False
                raise redis.ConnectionError('FAKE-KEY private transport exception')
            return result

    monkeypatch.setattr(client, 'pipeline', LostReply)
    with pytest.raises(SpendBlocked, match='^spend_usage_unavailable$'):
        runtime.record_abacus_usage(body(), usage())
    assert len(observations(client)) == 1
    runtime.record_abacus_usage(body(), usage())
    with pytest.raises(SpendBlocked, match='already_reserved'):
        reserve(sender)
    assert sender.call_count == 1
    assert ledger.snapshot()['period']['used_micro'] == reserved


def test_disabled_enforcement_does_not_read_or_write_usage(case, monkeypatch):
    client, _ = case
    before = client.hgetall(LEDGER_KEY)
    monkeypatch.setattr(runtime.settings, 'studio_spend_enforcement', False)
    with pytest.raises(SpendBlocked, match='not_enabled'):
        runtime.record_abacus_usage(body(), usage())
    assert client.hgetall(LEDGER_KEY) == before
