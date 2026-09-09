"""Real funding/scene dispatch adapters with independent fake Redis and senders."""
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from unittest.mock import Mock

import fakeredis
import pytest

from app.services.production_spend import LEDGER_KEY, SpendBlocked, SpendLedger, SpendPolicy
from app.services.production_scene_budget import scene_fields
from app.services import production_spend_quotes as quotes
from app.services import production_spend_runtime as runtime
from spending_test_support import TEST_KEY, test_funding_policy as funding_policy_fixture


ROOT = '11111111-1111-4111-8111-111111111111'
CHANNEL = 'UC5v9AvNtD3PTLgo6m1jROOA'
CONNECTION = 'connection_AAAAA'
ABACUS_URL = 'https://routellm.abacus.ai/v1/messages'
VEO_MODEL = 'veo-3.1-lite-generate-preview'
VEO_URL = 'https://generativelanguage.googleapis.com/v1beta/models/' + VEO_MODEL + ':predictLongRunning'
HAIKU = 'claude-haiku-4-5-20251001'


def body(provider, number=1):
    prompt = f'private scenario {number:05}'
    if provider == 'runway':
        return {'model': 'gen4.5', 'prompt_text': prompt, 'ratio': '720:1280', 'duration': 5}
    if provider == 'gemini':
        return {'instances': [{'prompt': prompt}], 'parameters': {
            'aspectRatio': '9:16', 'resolution': '720p', 'durationSeconds': 6}}
    if provider == 'openai':
        return {'model': 'gpt-6-astra', 'input': prompt, 'store': False, 'max_output_tokens': 16}
    return {'model': HAIKU, 'messages': [{'role': 'user', 'content': prompt}],
            'system': 'Return one brief JSON object.', 'max_tokens': 16,
            'thinking': {'type': 'disabled'}, 'stream': False, 'service_tier': 'standard_only'}


def headers(provider, key=TEST_KEY):
    if provider == 'abacus':
        return {'x-api-key': key, 'content-type': 'application/json', 'anthropic-version': '2023-06-01'}
    return {'x-goog-api-key': key, 'content-type': 'application/json'}


def actual_quote(provider, number=1):
    request = body(provider, number)
    if provider == 'runway':
        return quotes.quote_runway_video(request)
    if provider == 'openai':
        return quotes.quote_openai_response(request)
    return quotes.quote_http_request(VEO_URL if provider == 'gemini' else ABACUS_URL,
                                     {'json': request, 'headers': headers(provider)})[2]


def account(policy, provider):
    return next(value for value in policy['accounts'] if value['provider'] == provider)


def covered(policy, provider, allowance):
    entry = account(policy, provider)
    entry['mode'] = 'covered_only'
    entry['funding'] = {'covered_list_allowance_micro': allowance,
                        'coverage_basis': 'verified_route_list_cost_usd', 'no_auto_overage': True}


def ledger_state(client, field='funding_state'):
    return json.loads(client.hget(LEDGER_KEY, field))


def request_receipts(client):
    return {field: json.loads(value) for field, value in client.hscan_iter(LEDGER_KEY, match='request:*')}


@pytest.fixture
def case(monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=True)
    clock = [datetime(2026, 9, 9, 12, tzinfo=timezone.utc)]
    # Gross list-cost caps deliberately exceed the owner's separate cash cap.
    ledger = SpendLedger(client, SpendPolicy(*([100_000_000] * 6)), clock=lambda: clock[0])
    ledger.initialize()
    monkeypatch.setattr(runtime, 'settings', SimpleNamespace(studio_spend_enforcement=True))
    monkeypatch.setattr(runtime, 'configured_ledger', lambda: ledger)
    monkeypatch.setattr(quotes, '_fresh', lambda: None)
    client.sadd(runtime._CHANNEL_INDEX, CHANNEL)
    client.set(runtime._CHANNEL_PREFIX + CHANNEL, json.dumps({'id': CHANNEL, 'connection_id': CONNECTION}))
    client.set(runtime._JOB_PREFIX + ROOT, json.dumps({
        'task_id': ROOT, 'parent_id': None, 'spec': {
            'production_channel_id': CHANNEL, 'production_connection_id': CONNECTION,
            'duration_minutes': 0.5}}))
    task_token = runtime._TASK_ID.set(ROOT)
    scene_token = runtime._SCENE.set(None)
    runtime.resolve_context(client, ROOT)
    runway = Mock(base_url='https://api.dev.runwayml.com', api_key=TEST_KEY)
    runway.with_options.return_value = runway
    runway.text_to_video.create.return_value = {'id': 'fake-runway-task'}
    openai = Mock(base_url='https://api.openai.com/v1', api_key=TEST_KEY)
    openai.with_options.return_value = openai
    openai.responses.create.return_value = {'id': 'fake-openai-response'}
    post = Mock(return_value={'id': 'fake-http-response'})
    prepared = runtime.prepare_video_scene_budget('a' * 64, [4.0], '9:16', scene_count=1)
    yield SimpleNamespace(client=client, ledger=ledger, clock=clock, runway=runway,
                          openai=openai, post=post, prepared=prepared)
    runtime._SCENE.reset(scene_token)
    runtime._TASK_ID.reset(task_token)


def prime_scene(case):
    with runtime.spending_scene(case.prepared, 0):
        pass


def issue(case, provider, number=1, *, key=None):
    request = body(provider, number)
    if provider == 'openai':
        if key is not None:
            case.openai.api_key = key
        return runtime.paid_response(case.openai, **request)
    if provider == 'abacus':
        return runtime.paid_post(case.post, ABACUS_URL, json=request,
                                 headers=headers(provider, TEST_KEY if key is None else key))
    with runtime.spending_scene(case.prepared, 0):
        if provider == 'runway':
            if key is not None:
                case.runway.api_key = key
            return runtime.paid_runway_create(case.runway, **request)
        return runtime.paid_post(case.post, VEO_URL, json=request,
                                 headers=headers(provider, TEST_KEY if key is None else key))


def sends(case):
    return (case.post.call_count + case.runway.text_to_video.create.call_count
            + case.openai.responses.create.call_count)


def test_four_real_adapters_share_the_ten_dollar_cash_cap(case):
    policy = funding_policy_fixture(case.ledger)
    providers = ('runway', 'gemini', 'openai', 'abacus')
    amounts = {provider: actual_quote(provider).maximum_micro for provider in providers}
    # An explicit reconciled opening makes the final real response hit $10
    # exactly; no list-cost or scene ceiling is close to deciding this test.
    policy['opening_cash_micro'] = 10_000_000 - sum(amounts.values())
    case.ledger.initialize_funding(policy)
    for provider in providers:
        issue(case, provider)
    summary = case.ledger.funding_snapshot()
    assert summary['cash_cap_micro'] == summary['cash_reserved_micro'] == 10_000_000
    assert summary['cash_remaining_micro'] == 0
    before = case.client.dump(LEDGER_KEY)
    for provider in providers:
        with pytest.raises(SpendBlocked, match='^spend_funding_cash_limit$'):
            issue(case, provider, 2)
    assert case.client.dump(LEDGER_KEY) == before
    assert sends(case) == 4
    assert case.ledger.snapshot()['period']['used_micro'] == sum(amounts.values())
    for receipt in request_receipts(case.client).values():
        funding = receipt['funding']
        assert funding['cash_micro'] == amounts[funding['provider']]
        assert funding['covered_micro'] == 0
    case.runway.with_options.assert_called_once_with(max_retries=0)
    case.openai.with_options.assert_called_once_with(max_retries=0)


def test_real_quote_cash_factor_rounds_up_before_atomic_reservation(case):
    policy = funding_policy_fixture(case.ledger)
    account(policy, 'openai')['funding'].update(cash_factor_numerator=11, cash_factor_denominator=10)
    quoted = actual_quote('openai').maximum_micro
    cash = (quoted * 11 + 9) // 10
    case.ledger.initialize_funding(policy)
    issue(case, 'openai')
    assert case.ledger.snapshot()['period']['used_micro'] == quoted
    assert case.ledger.funding_snapshot()['cash_reserved_micro'] == cash
    assert next(iter(request_receipts(case.client).values()))['funding']['cash_micro'] == cash


def test_covered_exhaustion_never_uses_available_cash_or_sends_another_post(case):
    policy = funding_policy_fixture(case.ledger)
    cost = actual_quote('abacus').maximum_micro
    covered(policy, 'abacus', cost)
    case.ledger.initialize_funding(policy)
    issue(case, 'abacus')
    before = case.client.dump(LEDGER_KEY)
    with pytest.raises(SpendBlocked, match='^spend_funding_covered_limit$'):
        issue(case, 'abacus', 2)
    assert case.client.dump(LEDGER_KEY) == before
    assert sends(case) == 1
    summary = case.ledger.funding_snapshot()
    assert summary['cash_remaining_micro'] == 10_000_000
    entry = next(row for row in summary['providers'] if row['provider'] == 'abacus')
    assert entry['covered_reserved_micro'] == cost and entry['cash_reserved_micro'] == 0


@pytest.mark.parametrize('provider', ['runway', 'gemini', 'openai', 'abacus'])
def test_missing_funding_is_unknown_and_never_a_zero_cost_assumption(case, provider):
    prime_scene(case)
    before = case.client.dump(LEDGER_KEY)
    with pytest.raises(SpendBlocked, match='^spend_funding_not_initialized$'):
        issue(case, provider)
    assert case.client.dump(LEDGER_KEY) == before
    assert sends(case) == 0
    assert runtime.budget_status() == {
        'enforced': True, 'status': 'blocked', 'reason_code': 'spend_funding_not_initialized'}


@pytest.mark.parametrize('provider', ['runway', 'gemini', 'openai', 'abacus'])
def test_actual_transport_key_rotation_cannot_reuse_old_account_evidence(case, provider):
    case.ledger.initialize_funding(funding_policy_fixture(case.ledger))
    prime_scene(case)
    before = case.client.dump(LEDGER_KEY)
    with pytest.raises(SpendBlocked, match='^spend_funding_account_mismatch$') as caught:
        issue(case, provider, key='rotated-private-test-key')
    assert 'private-test-key' not in str(caught.value)
    assert case.client.dump(LEDGER_KEY) == before
    assert sends(case) == 0


def test_expired_funding_policy_blocks_real_dispatch_and_status(case):
    policy = funding_policy_fixture(case.ledger)
    policy['valid_until'] = '2026-09-09T13:00:00Z'
    for entry in policy['accounts']:
        entry['valid_until'] = policy['valid_until']
    case.ledger.initialize_funding(policy)
    case.clock[0] += timedelta(hours=1)
    before = case.client.dump(LEDGER_KEY)
    with pytest.raises(SpendBlocked, match='^spend_funding_policy_expired$'):
        issue(case, 'openai')
    assert case.client.dump(LEDGER_KEY) == before
    assert sends(case) == 0
    assert runtime.budget_status()['reason_code'] == 'spend_funding_policy_expired'


def test_expired_account_evidence_is_not_a_fresh_global_active_status(case):
    policy = funding_policy_fixture(case.ledger)
    account(policy, 'abacus')['valid_until'] = '2026-09-09T13:00:00Z'
    case.ledger.initialize_funding(policy)
    case.clock[0] += timedelta(hours=1)
    with pytest.raises(SpendBlocked, match='^spend_funding_account_expired$'):
        issue(case, 'abacus')
    assert runtime.budget_status() == {
        'enforced': True, 'status': 'blocked', 'reason_code': 'spend_funding_account_expired'}
    assert sends(case) == 0


def test_month_rollover_never_assumes_a_new_cash_or_credit_allowance(case):
    case.ledger.initialize_funding(funding_policy_fixture(case.ledger))
    issue(case, 'openai')
    case.clock[0] = datetime(2026, 10, 1, tzinfo=timezone.utc)
    before = case.client.dump(LEDGER_KEY)
    with pytest.raises(SpendBlocked, match='^spend_funding_month_mismatch$'):
        issue(case, 'openai', 2)
    assert case.client.dump(LEDGER_KEY) == before
    assert runtime.budget_status()['reason_code'] == 'spend_funding_month_mismatch'
    assert sends(case) == 1


def lose_new_receipt_reply(case, monkeypatch):
    original = case.client.pipeline
    def pipeline(*args, **kwargs):
        pipe = original(*args, **kwargs)
        execute = pipe.execute
        def execute_then_lose(*args, **kwargs):
            before = set(request_receipts(case.client))
            result = execute(*args, **kwargs)
            if set(request_receipts(case.client)) != before:
                raise ConnectionError('private connection string must not escape')
            return result
        pipe.execute = execute_then_lose
        return pipe
    monkeypatch.setattr(case.client, 'pipeline', pipeline)
    return original


@pytest.mark.parametrize('provider', ['runway', 'abacus'])
def test_lost_exec_reply_debits_cash_scene_and_family_but_never_sends_or_replays(case, monkeypatch, provider):
    case.ledger.initialize_funding(funding_policy_fixture(case.ledger))
    prime_scene(case)
    original = lose_new_receipt_reply(case, monkeypatch)
    with pytest.raises(SpendBlocked, match='^spend_store_unavailable$'):
        issue(case, provider)
    monkeypatch.setattr(case.client, 'pipeline', original)
    cost = actual_quote(provider).maximum_micro
    assert sends(case) == 0
    assert case.ledger.funding_snapshot()['cash_reserved_micro'] == cost
    assert case.ledger.snapshot()['period']['used_micro'] == cost
    scene = ledger_state(case.client, scene_fields(ROOT)[1])
    assert scene['used_micro']['0'] == (cost if provider == 'runway' else 0)
    before = case.client.dump(LEDGER_KEY)
    with pytest.raises(SpendBlocked, match='^spend_request_already_reserved$'):
        issue(case, provider)
    assert sends(case) == 0 and case.client.dump(LEDGER_KEY) == before


def test_scene_denial_rolls_back_an_otherwise_available_cash_reservation(case):
    case.ledger.initialize_funding(funding_policy_fixture(case.ledger))
    issue(case, 'runway', 1)
    issue(case, 'runway', 2)
    before = case.client.dump(LEDGER_KEY)
    with pytest.raises(SpendBlocked, match='^spend_scene_total_limit$'):
        issue(case, 'runway', 3)
    assert case.client.dump(LEDGER_KEY) == before
    assert sends(case) == 2
    assert case.ledger.funding_snapshot()['cash_reserved_micro'] == 1_200_000
    assert case.ledger.snapshot()['period']['used_micro'] == 1_200_000


def test_cash_denial_keeps_scene_and_family_counters_untouched(case):
    policy = funding_policy_fixture(case.ledger)
    policy['cash_cap_micro'] = 300_000
    case.ledger.initialize_funding(policy)
    prime_scene(case)
    before = case.client.dump(LEDGER_KEY)
    with pytest.raises(SpendBlocked, match='^spend_funding_cash_limit$'):
        issue(case, 'runway')
    assert case.client.dump(LEDGER_KEY) == before
    assert sends(case) == 0
    assert ledger_state(case.client, scene_fields(ROOT)[1])['used_micro']['0'] == 0
    assert case.ledger.snapshot()['period']['used_micro'] == 0


def test_concurrent_real_requests_cannot_spend_the_same_remaining_cash(case):
    policy = funding_policy_fixture(case.ledger)
    cost = actual_quote('openai').maximum_micro
    policy['cash_cap_micro'] = 2 * cost
    case.ledger.initialize_funding(policy)
    def attempt(number):
        try:
            issue(case, 'openai', number)
            return True
        except SpendBlocked:
            return False
    with runtime.SpendingThreadPoolExecutor(max_workers=8) as executor:
        accepted = list(executor.map(attempt, range(20)))
    assert sum(accepted) == sends(case) == 2
    assert case.ledger.funding_snapshot()['cash_reserved_micro'] == 2 * cost
    assert case.ledger.snapshot()['period']['used_micro'] == 2 * cost
    assert len(request_receipts(case.client)) == 2


def test_funding_commissioning_is_idempotent_and_cannot_replace_existing_policy(case):
    policy = funding_policy_fixture(case.ledger)
    assert case.ledger.initialize_funding(policy) is True
    issue(case, 'openai')
    before = case.client.dump(LEDGER_KEY)
    assert case.ledger.initialize_funding(policy) is False
    assert case.client.dump(LEDGER_KEY) == before
    changes = [
        {'opening_cash_micro': 1}, {'cash_cap_micro': 9_000_000},
        {'reconciliation_sha256': 'd' * 64},
    ]
    for change in changes:
        with pytest.raises(SpendBlocked, match='^spend_funding_policy_mismatch$'):
            case.ledger.initialize_funding({**policy, **change})
        assert case.client.dump(LEDGER_KEY) == before


def test_current_cash_debt_is_preserved_and_covered_work_does_not_add_cash(case):
    policy = funding_policy_fixture(case.ledger)
    policy['opening_cash_micro'] = 12_000_000
    covered(policy, 'abacus', actual_quote('abacus').maximum_micro)
    case.ledger.initialize_funding(policy)
    with pytest.raises(SpendBlocked, match='^spend_funding_cash_limit$'):
        issue(case, 'openai')
    issue(case, 'abacus')
    status = runtime.budget_status()
    assert status['funding']['cash_reserved_micro'] == 12_000_000
    assert status['funding']['cash_remaining_micro'] == -2_000_000
    assert sends(case) == 1
    assert case.openai.responses.create.call_count == 0


@pytest.mark.parametrize('missing', ['funding_policy', 'funding_state'])
def test_partial_funding_loss_cannot_be_reinitialized_or_dispatched(case, missing):
    policy = funding_policy_fixture(case.ledger)
    case.ledger.initialize_funding(policy)
    issue(case, 'openai')
    case.client.hdel(LEDGER_KEY, missing)
    before = case.client.dump(LEDGER_KEY)
    with pytest.raises(SpendBlocked):
        case.ledger.initialize_funding(policy)
    with pytest.raises(SpendBlocked, match='^spend_funding_not_initialized$'):
        issue(case, 'openai', 2)
    assert case.client.dump(LEDGER_KEY) == before
    assert sends(case) == 1


def test_both_funding_fields_lost_cannot_zero_existing_funded_receipts(case):
    policy = funding_policy_fixture(case.ledger)
    case.ledger.initialize_funding(policy)
    issue(case, 'openai')
    case.client.hdel(LEDGER_KEY, 'funding_policy', 'funding_state')
    before = case.client.dump(LEDGER_KEY)
    with pytest.raises(SpendBlocked, match='^spend_funding_initialization_late$'):
        case.ledger.initialize_funding(policy)
    assert case.client.dump(LEDGER_KEY) == before
    assert sends(case) == 1
    with pytest.raises(SpendBlocked, match='^spend_funding_not_initialized$'):
        issue(case, 'openai', 2)
    assert case.client.dump(LEDGER_KEY) == before


def test_first_explicit_funding_commissioning_preserves_reconciled_legacy_debt(case):
    quoted = actual_quote('openai')
    case.ledger.reserve(request_key='legacy_request_0001', channel_id=CHANNEL,
                        lineage_id=ROOT, kind='shorts', quote=quoted)
    policy = funding_policy_fixture(case.ledger)
    policy['opening_cash_micro'] = quoted.maximum_micro
    assert case.ledger.initialize_funding(policy) is True
    assert case.ledger.funding_snapshot()['cash_reserved_micro'] == quoted.maximum_micro
    assert case.ledger.snapshot()['period']['used_micro'] == quoted.maximum_micro
    assert sends(case) == 0


def test_funding_initializer_never_bootstraps_missing_general_ledger(case):
    policy = funding_policy_fixture(case.ledger)
    case.client.delete(LEDGER_KEY)
    with pytest.raises(SpendBlocked, match='^spend_not_initialized$'):
        case.ledger.initialize_funding(policy)
    assert not case.client.exists(LEDGER_KEY)


def test_funding_history_scan_is_bounded_and_unknown_tail_is_not_zero(case, monkeypatch):
    original, pages = case.client.pipeline, []
    def pipeline(*args, **kwargs):
        pipe = original(*args, **kwargs)
        def unending_scan(*args, **kwargs):
            pages.append(True)
            return 1, {}
        pipe.hscan = unending_scan
        return pipe
    monkeypatch.setattr(case.client, 'pipeline', pipeline)
    before = case.client.dump(LEDGER_KEY)
    with pytest.raises(SpendBlocked, match='^spend_funding_history_limit$'):
        case.ledger.initialize_funding(funding_policy_fixture(case.ledger))
    assert len(pages) == 128
    assert case.client.dump(LEDGER_KEY) == before
    assert sends(case) == 0


@pytest.mark.parametrize('raw,reason', [
    ('[]', 'spend_state_invalid'), ('{}', 'spend_state_invalid'),
    ('{"funding":null}', 'spend_funding_initialization_late'),
])
def test_unknown_or_already_funded_history_cannot_be_treated_as_unused(case, raw, reason):
    case.client.hset(LEDGER_KEY, 'request:' + 'f' * 64, raw)
    before = case.client.dump(LEDGER_KEY)
    with pytest.raises(SpendBlocked, match='^' + reason + '$'):
        case.ledger.initialize_funding(funding_policy_fixture(case.ledger))
    assert case.client.dump(LEDGER_KEY) == before
    assert sends(case) == 0


def test_lost_funding_initialization_reply_is_only_observed_on_reentry(case, monkeypatch):
    policy = funding_policy_fixture(case.ledger)
    original = case.client.pipeline
    def pipeline(*args, **kwargs):
        pipe = original(*args, **kwargs)
        execute = pipe.execute
        def execute_then_lose(*args, **kwargs):
            execute(*args, **kwargs)
            raise ConnectionError('private initialization details')
        pipe.execute = execute_then_lose
        return pipe
    monkeypatch.setattr(case.client, 'pipeline', pipeline)
    with pytest.raises(SpendBlocked, match='^spend_store_unavailable$'):
        case.ledger.initialize_funding(policy)
    monkeypatch.setattr(case.client, 'pipeline', original)
    before = case.client.dump(LEDGER_KEY)
    assert case.ledger.initialize_funding(policy) is False
    assert case.client.dump(LEDGER_KEY) == before
    assert case.ledger.funding_snapshot()['cash_reserved_micro'] == 0
    assert sends(case) == 0


def override_read_ack(case, monkeypatch, acknowledgement):
    original = case.client.pipeline
    def pipeline(*args, **kwargs):
        pipe = original(*args, **kwargs)
        execute = pipe.execute
        def execute_with_bad_ack(*args, **kwargs):
            result = execute(*args, **kwargs)
            if len(result) == 1 and result[0] is True:
                return acknowledgement
            return result
        pipe.execute = execute_with_bad_ack
        return pipe
    monkeypatch.setattr(case.client, 'pipeline', pipeline)


@pytest.mark.parametrize('acknowledgement', [[False], [1], []], ids=['false', 'integer', 'empty'])
def test_equal_funding_initialization_requires_acknowledged_read(case, monkeypatch, acknowledgement):
    policy = funding_policy_fixture(case.ledger)
    case.ledger.initialize_funding(policy)
    issue(case, 'openai')
    before = case.client.dump(LEDGER_KEY)
    override_read_ack(case, monkeypatch, acknowledgement)
    with pytest.raises(SpendBlocked):
        case.ledger.initialize_funding(policy)
    assert case.client.dump(LEDGER_KEY) == before
    assert sends(case) == 1


@pytest.mark.parametrize('acknowledgement', [[False], [1], []], ids=['false', 'integer', 'empty'])
def test_funding_snapshot_requires_ack_and_status_cannot_claim_active(case, monkeypatch, acknowledgement):
    case.ledger.initialize_funding(funding_policy_fixture(case.ledger))
    issue(case, 'openai')
    before = case.client.dump(LEDGER_KEY)
    override_read_ack(case, monkeypatch, acknowledgement)
    with pytest.raises(SpendBlocked):
        case.ledger.funding_snapshot()
    assert runtime.budget_status()['status'] == 'blocked'
    assert case.client.dump(LEDGER_KEY) == before
    assert sends(case) == 1


def test_budget_status_and_receipts_do_not_expose_secret_or_account_bindings(case):
    policy = funding_policy_fixture(case.ledger)
    covered(policy, 'abacus', 100_000)
    case.ledger.initialize_funding(policy)
    issue(case, 'abacus')
    issue(case, 'openai')
    before = case.client.dump(LEDGER_KEY)
    status = runtime.budget_status()
    assert status['status'] == 'active' and status['enforced'] is True
    assert status['funding']['accounting'] == 'reserved_cash_upper_bound_not_invoice'
    encoded = json.dumps(status)
    for private in (TEST_KEY, 'private scenario', 'sha256', 'evidence', 'routes'):
        assert private not in encoded
    encoded_receipts = json.dumps(request_receipts(case.client))
    assert TEST_KEY not in encoded_receipts and 'private scenario' not in encoded_receipts
    for entry in policy['accounts']:
        assert entry['credential_sha256'] not in encoded_receipts
    assert case.client.dump(LEDGER_KEY) == before
    assert sends(case) == 2


def test_corrupt_funding_counters_block_status_and_dispatch_without_repair(case):
    case.ledger.initialize_funding(funding_policy_fixture(case.ledger))
    issue(case, 'openai')
    state = ledger_state(case.client)
    state['cash_reserved_micro'] = 0
    case.client.hset(LEDGER_KEY, 'funding_state', json.dumps(state))
    before = case.client.dump(LEDGER_KEY)
    with pytest.raises(SpendBlocked, match='^spend_funding_state_invalid$'):
        issue(case, 'openai', 2)
    assert runtime.budget_status()['reason_code'] == 'spend_funding_state_invalid'
    assert case.client.dump(LEDGER_KEY) == before
    assert sends(case) == 1
