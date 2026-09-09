"""Pure funding boundaries; no provider, credentials, Redis or account access."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from app.services.production_funding import (
    OWNER_MAX_NEW_CASH_MICRO, funding_summary, initial_funding_state,
    reserve_funding, validate_funding_policy,
)
from app.services.production_spend import SpendBlocked, SpendQuote


NOW = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)
START = '2026-09-09T10:00:00Z'
END = '2026-10-01T00:00:00Z'
ABACUS_ROUTE = 'https://routellm.abacus.ai/v1/messages'
OPENAI_ROUTE = 'https://api.openai.com/v1/responses'
HAIKU = 'claude-haiku-4-5-20251001'


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def credential(provider):
    return digest(provider + '\0offline-fixture-key')


def policy():
    return {
        'version': 1, 'currency': 'USD', 'month': '2026-09',
        'valid_from': START, 'valid_until': END,
        'cash_cap_micro': 10_000_000, 'opening_cash_micro': 2_000_000,
        'reconciliation_sha256': digest('operator reconciled prior and in-flight commitments'),
        'accounts': [
            {'provider': 'abacus', 'account_sha256': digest('abacus billing account'),
             'credential_sha256': credential('abacus'), 'evidence_sha256': digest('verified covered routes'),
             'valid_until': END, 'mode': 'covered_only',
             'routes': [{'route': ABACUS_ROUTE, 'model': HAIKU, 'price_revision': 'fixture-v1'},
                        {'route': ABACUS_ROUTE, 'model': 'claude-sonnet-5', 'price_revision': 'fixture-v1'}],
             'funding': {'covered_list_allowance_micro': 100_000,
                         'coverage_basis': 'verified_route_list_cost_usd', 'no_auto_overage': True}},
            {'provider': 'openai', 'account_sha256': digest('openai billing account'),
             'credential_sha256': credential('openai'), 'evidence_sha256': digest('verified cash taxes surcharge'),
             'valid_until': END, 'mode': 'cash_only',
             'routes': [{'route': OPENAI_ROUTE, 'model': 'gpt-6-astra', 'price_revision': 'fixture-v1'}],
             'funding': {'cash_factor_numerator': 11, 'cash_factor_denominator': 10,
                         'cash_bound_verified': True}},
        ],
    }


def reserve(p, state, *, provider='abacus', amount=10_000, model=None, route=None, **kwargs):
    return reserve_funding(
        p, state,
        quote=SpendQuote(provider, model or (HAIKU if provider == 'abacus' else 'gpt-6-astra'),
                         amount, 'fixture-v1'),
        route=route or (ABACUS_ROUTE if provider == 'abacus' else OPENAI_ROUTE),
        credential_sha256=kwargs.pop('credential_sha256', credential(provider)),
        now=kwargs.pop('now', NOW), **kwargs,
    )


def test_separate_covered_and_cash_counters_keep_prior_liability():
    p = policy()
    state = initial_funding_state(p, now=NOW)
    assert OWNER_MAX_NEW_CASH_MICRO == 10_000_000
    state, receipt = reserve(p, state)
    assert receipt['covered_micro'] == 10_000 and receipt['cash_micro'] == 0
    assert receipt['list_maximum_micro'] == 10_000
    assert state['cash_reserved_micro'] == 2_000_000
    state, receipt = reserve(p, state, provider='openai', amount=1_000_000)
    assert receipt['cash_micro'] == 1_100_000 and receipt['covered_micro'] == 0
    assert state['cash_reserved_micro'] == 3_100_000
    assert state['accounts'][digest('abacus billing account')]['covered_reserved_micro'] == 10_000
    assert state['accounts'][digest('openai billing account')]['cash_reserved_micro'] == 1_100_000


def test_covered_models_share_one_account_pool_and_never_fall_back_to_cash():
    p = policy()
    state = initial_funding_state(p, now=NOW)
    state, _ = reserve(p, state, amount=60_000)
    state, _ = reserve(p, state, model='claude-sonnet-5', amount=40_000)
    snapshot = deepcopy(state)
    with pytest.raises(SpendBlocked, match='spend_funding_covered_limit'):
        reserve(p, state, amount=1)
    assert state == snapshot and state['cash_reserved_micro'] == 2_000_000


def test_cash_cap_is_shared_across_providers_and_rounds_every_bound_up():
    p = policy()
    p['accounts'][0]['mode'] = 'cash_only'
    p['accounts'][0]['funding'] = {
        'cash_factor_numerator': 1, 'cash_factor_denominator': 1, 'cash_bound_verified': True}
    state = initial_funding_state(p, now=NOW)
    state, _ = reserve(p, state, provider='openai', amount=7_000_000)
    assert state['cash_reserved_micro'] == 9_700_000
    state, receipt = reserve(p, state, amount=300_000)
    assert state['cash_reserved_micro'] == 10_000_000 and receipt['cash_micro'] == 300_000
    for provider in ('abacus', 'openai'):
        with pytest.raises(SpendBlocked, match='spend_funding_cash_limit'):
            reserve(p, state, provider=provider, amount=1)
    fresh = initial_funding_state(p, now=NOW)
    _, receipt = reserve(p, fresh, provider='openai', amount=1)
    assert receipt['cash_micro'] == 2  # 1.1 microdollars must round upward.


def test_preexisting_debt_above_owner_cap_is_preserved_and_new_cash_stops():
    p = policy()
    p['opening_cash_micro'] = 26_000_000
    state = initial_funding_state(p, now=NOW)
    assert state['cash_reserved_micro'] == 26_000_000
    with pytest.raises(SpendBlocked, match='spend_funding_cash_limit'):
        reserve(p, state, provider='openai', amount=1)
    state, receipt = reserve(p, state)
    assert state['cash_reserved_micro'] == 26_000_000 and receipt['cash_micro'] == 0


def test_zero_cash_cap_allows_only_verified_covered_work():
    p = policy()
    p.update(cash_cap_micro=0, opening_cash_micro=0)
    state = initial_funding_state(p, now=NOW)
    state, receipt = reserve(p, state)
    assert receipt['covered_micro'] == 10_000 and state['cash_reserved_micro'] == 0
    with pytest.raises(SpendBlocked, match='spend_funding_cash_limit'):
        reserve(p, state, provider='openai', amount=1)


@pytest.mark.parametrize('provider', ['abacus', 'openai'])
def test_account_specific_tariff_must_match_the_actual_credential_account(provider):
    p = policy()
    state = initial_funding_state(p, now=NOW)
    account = next(row for row in p['accounts'] if row['provider'] == provider)
    updated, receipt = reserve(p, state, provider=provider, account_sha256=account['account_sha256'])
    assert receipt['account_sha256'] == account['account_sha256']
    assert updated != state
    before = deepcopy(state)
    for wrong in ('f' * 64, '', False, 1, ['f' * 64]):
        with pytest.raises(SpendBlocked, match='^spend_funding_account_mismatch$'):
            reserve(p, state, provider=provider, account_sha256=wrong)
        assert state == before


@pytest.mark.parametrize('field,value', [
    ('cash_cap_micro', 10_000_001), ('cash_cap_micro', True), ('cash_cap_micro', 10.0),
    ('cash_cap_micro', '10000000'), ('cash_cap_micro', -1), ('opening_cash_micro', -1),
    ('opening_cash_micro', False), ('opening_cash_micro', float('inf')),
    ('version', True), ('version', 2), ('currency', 'EUR'),
    ('reconciliation_sha256', None), ('reconciliation_sha256', 'no-evidence'),
    ('valid_from', '2026-09-09'), ('valid_from', '2026-09-31T00:00:00Z'),
    ('valid_from', '2026-09-09T10:00:00+00:00'), ('valid_until', '2026-10-02T00:00:00Z'),
    ('valid_until', START), ('month', '2026-9'),
])
def test_unproved_or_malformed_policy_is_rejected(field, value):
    p = policy()
    p[field] = value
    with pytest.raises(SpendBlocked):
        initial_funding_state(p, now=NOW)


@pytest.mark.parametrize('patch', [
    {'covered_list_allowance_micro': True}, {'covered_list_allowance_micro': -1},
    {'coverage_basis': 'subscription_price_divided_by_credits'}, {'no_auto_overage': False},
    {'no_auto_overage': 1}, {'cash_fallback': True}, {'remaining_credits': 15820},
])
def test_covered_mode_requires_explicit_route_cost_mapping_and_no_overage(patch):
    p = policy()
    p['accounts'][0]['funding'].update(patch)
    with pytest.raises(SpendBlocked, match='spend_funding_policy_invalid'):
        initial_funding_state(p, now=NOW)


@pytest.mark.parametrize('patch', [
    {'cash_bound_verified': False}, {'cash_bound_verified': 1},
    {'cash_factor_numerator': 0}, {'cash_factor_numerator': True},
    {'cash_factor_numerator': 9}, {'cash_factor_denominator': 0},
    {'cash_factor_denominator': 10.0}, {'cash_factor_numerator': float('nan')},
    {'cash_factor_denominator': '10'}, {'cash_factor_numerator': 1_000_000_001},
    {'covered_list_allowance_micro': 100000},
])
def test_cash_mode_never_assumes_unverified_or_discounted_cash_bound(patch):
    p = policy()
    p['accounts'][1]['funding'].update(patch)
    with pytest.raises(SpendBlocked, match='spend_funding_policy_invalid'):
        initial_funding_state(p, now=NOW)


@pytest.mark.parametrize('field', ['provider', 'account_sha256', 'credential_sha256'])
def test_duplicate_provider_account_or_credential_cannot_double_covered_pool(field):
    p = policy()
    p['accounts'][1][field] = p['accounts'][0][field]
    with pytest.raises(SpendBlocked, match='spend_funding_policy_invalid'):
        initial_funding_state(p, now=NOW)


@pytest.mark.parametrize('scope', ['policy', 'account', 'route', 'funding'])
def test_exact_schema_rejects_unreviewed_fields(scope):
    p = policy()
    target = {'policy': p, 'account': p['accounts'][0],
              'route': p['accounts'][0]['routes'][0], 'funding': p['accounts'][0]['funding']}[scope]
    target['unreviewed'] = 'secret-or-unsupported'
    with pytest.raises(SpendBlocked, match='spend_funding_policy_invalid') as caught:
        initial_funding_state(p, now=NOW)
    assert 'secret-or-unsupported' not in str(caught.value)


@pytest.mark.parametrize('scope,key', [
    ('policy', 'opening_cash_micro'), ('account', 'credential_sha256'),
    ('account', 'evidence_sha256'), ('route', 'price_revision'), ('funding', 'no_auto_overage'),
])
def test_missing_evidence_and_snapshot_fields_cannot_default_to_free(scope, key):
    p = policy()
    target = {'policy': p, 'account': p['accounts'][0],
              'route': p['accounts'][0]['routes'][0], 'funding': p['accounts'][0]['funding']}[scope]
    del target[key]
    with pytest.raises(SpendBlocked):
        initial_funding_state(p, now=NOW)


@pytest.mark.parametrize('route', [
    'http://routellm.abacus.ai/v1/messages', 'https://routellm.abacus.ai/v1/*',
    'https://routellm.abacus.ai/v1/messages?key=private',
    'https://routellm.abacus.ai/v1/messages#fragment',
    'https://private@routellm.abacus.ai/v1/messages',
    'https://routellm.abacus.ai:443/v1/messages',
    'https://ROUTELLM.ABACUS.AI/v1/messages', 'https://routellm.abacus.ai/v1//messages',
    'https://routellm.abacus.ai/v1/../messages', 'https://routellm.abacus.ai/v1/%2amessages',
    '/v1/messages', 'responses',
])
def test_route_evidence_must_be_exact_canonical_https(route):
    p = policy()
    p['accounts'][0]['routes'][0]['route'] = route
    with pytest.raises(SpendBlocked, match='spend_funding_policy_invalid'):
        initial_funding_state(p, now=NOW)


@pytest.mark.parametrize('route', [
    'https://api.openai.com/v1/responses', 'https://api.dev.runwayml.com/v1/text_to_video',
    'https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-pro-preview:generateContent',
    'https://api.elevenlabs.io/v1/text-to-speech/fixtureVoice123/with-timestamps',
])
def test_current_production_route_syntax_can_be_reviewed_explicitly(route):
    p = policy()
    p['accounts'][0]['routes'][0]['route'] = route
    assert validate_funding_policy(p, now=NOW) == p


@pytest.mark.parametrize('change', ['credential', 'provider', 'model', 'route', 'revision'])
def test_request_binding_matches_exact_evidence(change):
    p = policy()
    state = initial_funding_state(p, now=NOW)
    args = {'quote': SpendQuote('abacus', HAIKU, 10_000, 'fixture-v1'),
            'route': ABACUS_ROUTE, 'credential_sha256': credential('abacus'), 'now': NOW}
    if change == 'credential':
        args['credential_sha256'] = credential('rotated-key')
    elif change == 'provider':
        args['quote'] = SpendQuote('elevenlabs', HAIKU, 10_000, 'fixture-v1')
    elif change == 'model':
        args['quote'] = SpendQuote('abacus', 'route-llm', 10_000, 'fixture-v1')
    elif change == 'route':
        args['route'] = 'https://routellm.abacus.ai/v1/chat/completions'
    else:
        args['quote'] = SpendQuote('abacus', HAIKU, 10_000, 'fixture-v2')
    with pytest.raises(SpendBlocked):
        reserve_funding(p, state, **args)
    assert state['cash_reserved_micro'] == p['opening_cash_micro']


@pytest.mark.parametrize('amount', [0, -1, True, 1.2])
def test_bad_quote_cannot_become_a_covered_permit(amount):
    p = policy()
    with pytest.raises(SpendBlocked):
        reserve(p, initial_funding_state(p, now=NOW), amount=amount)


@pytest.mark.parametrize('now', [
    datetime(2026, 9, 9, 12), datetime(2026, 9, 9, 9, tzinfo=timezone.utc),
    datetime(2026, 10, 1, tzinfo=timezone.utc), datetime(2026, 8, 31, tzinfo=timezone.utc), None,
])
def test_clock_month_and_policy_expiry_fail_closed(now):
    p = policy()
    with pytest.raises(SpendBlocked):
        reserve(p, initial_funding_state(p, now=NOW), now=now)


def test_account_expiry_only_blocks_that_account_and_never_rolls_over():
    p = policy()
    p['accounts'][0]['valid_until'] = '2026-09-09T12:01:00Z'
    state = initial_funding_state(p, now=NOW)
    later = NOW + timedelta(minutes=1)
    with pytest.raises(SpendBlocked, match='spend_funding_account_expired'):
        reserve(p, state, now=later)
    updated, _ = reserve(p, state, provider='openai', now=later)
    assert updated['cash_reserved_micro'] > state['cash_reserved_micro']
    with pytest.raises(SpendBlocked, match='spend_funding_month_mismatch'):
        reserve(p, updated, now=datetime(2026, 10, 1, tzinfo=timezone.utc))


def test_clock_rollback_does_not_reset_previous_reservations():
    p = policy()
    state = initial_funding_state(p, now=NOW)
    state, _ = reserve(p, state, now=NOW + timedelta(minutes=2))
    with pytest.raises(SpendBlocked, match='spend_funding_clock_or_state'):
        reserve(p, state, now=NOW + timedelta(minutes=1))


def test_another_timezone_is_normalized_to_utc():
    p = policy()
    local = NOW.astimezone(timezone(timedelta(hours=3)))
    state = initial_funding_state(p, now=local)
    assert state['last_reserved_at'] == '2026-09-09T12:00:00Z'
    assert reserve(p, state, now=local)[0]['month'] == '2026-09'


@pytest.mark.parametrize('change', ['cap', 'opening', 'evidence', 'allowance', 'credential'])
def test_new_policy_or_balance_snapshot_cannot_reset_existing_state(change):
    p = policy()
    state, _ = reserve(p, initial_funding_state(p, now=NOW))
    modified = deepcopy(p)
    if change == 'cap':
        modified['cash_cap_micro'] -= 1
    elif change == 'opening':
        modified['opening_cash_micro'] = 0
    elif change == 'evidence':
        modified['reconciliation_sha256'] = digest('new observation')
    elif change == 'allowance':
        modified['accounts'][0]['funding']['covered_list_allowance_micro'] += 10_000
    else:
        modified['accounts'][0]['credential_sha256'] = credential('new-key')
    with pytest.raises(SpendBlocked, match='spend_funding_policy_mismatch'):
        reserve(modified, state)


@pytest.mark.parametrize('change', [
    'total', 'account_cash', 'negative', 'covered_in_cash', 'cash_in_covered',
    'covered_excess', 'missing_account', 'extra_account', 'unknown_field', 'wrong_month',
])
def test_corrupt_state_cannot_drop_prior_or_inflight_funding(change):
    p = policy()
    state, _ = reserve(p, initial_funding_state(p, now=NOW), provider='openai')
    abacus = state['accounts'][digest('abacus billing account')]
    openai = state['accounts'][digest('openai billing account')]
    if change == 'total':
        state['cash_reserved_micro'] = 0
    elif change == 'account_cash':
        openai['cash_reserved_micro'] = 0
    elif change == 'negative':
        abacus['covered_reserved_micro'] = -1
    elif change == 'covered_in_cash':
        openai['covered_reserved_micro'] = 1
    elif change == 'cash_in_covered':
        abacus['cash_reserved_micro'] = 1
        state['cash_reserved_micro'] += 1
    elif change == 'covered_excess':
        abacus['covered_reserved_micro'] = 100_001
    elif change == 'missing_account':
        del state['accounts'][digest('abacus billing account')]
    elif change == 'extra_account':
        state['accounts'][digest('extra account')] = deepcopy(abacus)
    elif change == 'unknown_field':
        abacus['allow_fallback'] = True
    else:
        state['month'] = '2026-08'
    with pytest.raises(SpendBlocked):
        reserve(p, state)


def test_input_objects_and_receipts_are_independent_and_secret_free():
    p = policy()
    state = initial_funding_state(p, now=NOW)
    original_policy, original_state = deepcopy(p), deepcopy(state)
    updated, receipt = reserve(p, state)
    assert p == original_policy and state == original_state
    assert updated != state
    serialized = json.dumps(receipt)
    assert 'offline-fixture-key' not in serialized
    assert 'abacus billing account' not in serialized
    assert credential('abacus') not in serialized
    assert receipt['account_sha256'] == digest('abacus billing account')
    assert receipt['evidence_sha256'] == p['accounts'][0]['evidence_sha256']
    validated = validate_funding_policy(p, now=NOW)
    validated['accounts'][0]['funding']['covered_list_allowance_micro'] = 0
    assert p == original_policy
    updated['accounts'][digest('abacus billing account')]['covered_reserved_micro'] = 0
    assert state == original_state


def test_exact_duplicate_model_route_cannot_add_another_pool_or_revision():
    p = policy()
    duplicate = deepcopy(p['accounts'][0]['routes'][0])
    duplicate['price_revision'] = 'fixture-v2'
    p['accounts'][0]['routes'].append(duplicate)
    with pytest.raises(SpendBlocked, match='spend_funding_policy_invalid'):
        initial_funding_state(p, now=NOW)


def test_no_matching_account_is_not_implicitly_created():
    p = policy()
    state = initial_funding_state(p, now=NOW)
    with pytest.raises(SpendBlocked, match='spend_funding_account_mismatch'):
        reserve(p, state, provider='unknown')
    assert len(state['accounts']) == 2


def test_helper_does_not_claim_to_handle_replay_or_persistence():
    p = policy()
    state = initial_funding_state(p, now=NOW)
    first, _ = reserve(p, state)
    second, _ = reserve(p, first)
    assert second['accounts'][digest('abacus billing account')]['covered_reserved_micro'] == 20_000
    # The shared ledger, not this arithmetic helper, must reject a replay key.
    assert state['accounts'][digest('abacus billing account')]['covered_reserved_micro'] == 0


def test_summary_keeps_negative_debt_and_never_exposes_account_bindings():
    p = policy()
    p['opening_cash_micro'] = 12_000_000
    state = initial_funding_state(p, now=NOW)
    state, _ = reserve(p, state)
    summary = funding_summary(p, state, now=NOW)
    assert summary == {
        'currency': 'USD', 'cash_cap_micro': 10_000_000,
        'cash_reserved_micro': 12_000_000, 'cash_remaining_micro': -2_000_000,
        'accounting': 'reserved_cash_upper_bound_not_invoice',
        'providers': [
            {'provider': 'abacus', 'mode': 'covered_only', 'covered_allowance_micro': 100_000,
             'covered_reserved_micro': 10_000, 'cash_reserved_micro': 0, 'valid_until': END},
            {'provider': 'openai', 'mode': 'cash_only', 'covered_allowance_micro': None,
             'covered_reserved_micro': 0, 'cash_reserved_micro': 0, 'valid_until': END},
        ],
    }
    encoded = json.dumps(summary)
    assert 'sha256' not in encoded and 'offline-fixture-key' not in encoded
    assert 'funding' not in encoded and 'evidence' not in encoded


def test_summary_rejects_corruption_expiry_and_missing_state_without_fake_quote():
    p = policy()
    state = initial_funding_state(p, now=NOW)
    for bad_state in (None, {}, {**state, 'cash_reserved_micro': 0}):
        with pytest.raises(SpendBlocked):
            funding_summary(p, bad_state, now=NOW)
    with pytest.raises(SpendBlocked):
        funding_summary(p, state, now=datetime(2026, 10, 1, tzinfo=timezone.utc))
    p['accounts'][0]['valid_until'] = '2026-09-09T11:59:00Z'
    state = initial_funding_state(p, now=NOW)
    with pytest.raises(SpendBlocked, match='spend_funding_account_expired'):
        funding_summary(p, state, now=NOW)
