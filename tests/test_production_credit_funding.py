"""Offline credit admission: actual meter settlement, not guessed USD quotes."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from app.services.production_spend import SpendBlocked
from app.services import production_credit_funding as credit


NOW = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.fixture
def policy():
    return {
        'version': 1, 'provider': 'elevenlabs', 'month': '2026-09',
        'valid_from': '2026-09-09T13:00:00Z', 'valid_until': '2026-09-10T00:00:00Z',
        'account_sha256': digest('synthetic billing account'),
        'credential_sha256': digest('synthetic actual credential binding'),
        'evidence_sha256': digest('synthetic account and voice evidence'),
        'reconciliation_sha256': digest('synthetic reconciliation'),
        'route': credit.ROUTE, 'model': credit.MODEL, 'voice_id': credit.VOICE_ID,
        'allocation_credits': 1000,
        'balance': {'observed_at': '2026-09-09T12:59:00Z',
                    'provider_reset_at': '2026-09-25T00:00:00Z',
                    'quota_credits': 131000, 'used_credits': 71383, 'withheld_credits': 100},
        'cash_controls': {'max_credit_limit_extension': 0, 'can_extend_character_limit': False,
                          'overage_observation_sha256': digest('synthetic observed overage disabled'),
                          'auto_top_up_enabled': False, 'auto_top_up_source': 'owner_attested',
                          'auto_top_up_proof_sha256': digest('synthetic owner OFF attestation')},
    }


def intent(number=1, **changes):
    return {
        'intent_id': digest('intent ' + str(number)),
        'root_lineage_id': 'original-root-0001', 'channel_id': 'channel-0001',
        'source_connection_id': 'source-connection-0001',
        'request_sha256': digest('actual immutable request ' + str(number)),
        'route': credit.ROUTE, 'model': credit.MODEL, 'voice_id': credit.VOICE_ID,
        **changes,
    }


def binding(policy):
    return {'actual_account_sha256': policy['account_sha256'],
            'actual_credential_sha256': policy['credential_sha256']}


def reserve(policy, state, request=None, *, now=NOW, **kwargs):
    return credit.reserve_credit_intent(
        policy, state, intent=request or intent(), now=now, **(binding(policy) | kwargs))


def observation(policy, receipt, actual=248, **changes):
    return {
        'version': 1, 'terminal': True, 'source': 'verified_provider_meter',
        **{key: receipt['intent'][key] for key in (
            'intent_id', 'root_lineage_id', 'channel_id', 'source_connection_id', 'request_sha256')},
        'reservation_sha256': receipt['reservation_sha256'],
        'account_sha256': policy['account_sha256'], 'credential_sha256': policy['credential_sha256'],
        'provider_request_id_sha256': digest('provider request ' + receipt['intent']['intent_id']),
        'response_proof_sha256': digest('terminal response ' + receipt['intent']['intent_id']),
        'actual_credit_cost': actual, 'observed_at': '2026-09-09T14:00:00Z', **changes,
    }


def settle(policy, state, seen, *, now=NOW, **kwargs):
    return credit.settle_credit_intent(
        policy, state, observation=seen, now=now, **(binding(policy) | kwargs))


def test_terminal_meter_releases_only_unused_credits_and_permanent_replay_stays(policy):
    initial = credit.initial_credit_state(policy, now=NOW)
    held, receipt = reserve(policy, initial)
    assert held['reserved_credits'] == 1000
    assert held['spent_credits'] == 0
    assert receipt['accounting'] == 'native_allocation_reservation_not_meter_upper_bound'
    assert initial['intents'] == {} and initial['revision'] == 0
    settled, accepted = settle(policy, held, observation(policy, receipt))
    assert settled['reserved_credits'] == 0
    assert settled['spent_credits'] == 248
    assert settled['revision'] == 2
    assert held['reserved_credits'] == 1000  # candidate creation never mutates stored input
    again, repeated = settle(policy, settled, accepted, now=NOW + timedelta(seconds=1))
    assert again == settled and repeated == accepted
    second, receipt2 = reserve(policy, settled, intent(2), now=NOW + timedelta(seconds=1))
    assert second['reserved_credits'] == 752 and receipt2['sequence'] == 2
    final, _ = settle(policy, second, observation(policy, receipt2, actual=752,
                                                 observed_at='2026-09-09T14:00:01Z'),
                      now=NOW + timedelta(seconds=1))
    summary = credit.credit_funding_summary(policy, final, now=NOW + timedelta(seconds=1))
    assert summary['spent_credits'] == 1000 and summary['available_credits'] == 0
    assert summary['accounting'] == 'native_credits_not_usd'
    assert not any(key in summary for key in ('maximum_micro', 'cash_micro', 'currency'))
    with pytest.raises(SpendBlocked, match='credit_allocation_exhausted'):
        reserve(policy, final, intent(3), now=NOW + timedelta(seconds=2))
    with pytest.raises(SpendBlocked, match='credit_request_already_reserved'):
        reserve(policy, final, intent(9, request_sha256=intent()['request_sha256']),
                now=NOW + timedelta(seconds=2))


def test_uncertain_request_holds_entire_pool_across_jobs_and_replay_nonces(policy):
    state, receipt = reserve(policy, credit.initial_credit_state(policy, now=NOW))
    before = deepcopy(state)
    attempts = [(intent(), 'credit_intent_already_reserved'),
                (intent(8, request_sha256=intent()['request_sha256']), 'credit_request_already_reserved'),
                (intent(2), 'credit_pool_has_uncertain_intent'),
                (intent(3, root_lineage_id='different-root-0002', channel_id='channel-0002'),
                 'credit_pool_has_uncertain_intent')]
    for request, reason in attempts:
        with pytest.raises(SpendBlocked, match=reason):
            reserve(policy, state, request)
    assert state == before
    assert state['request_index'][receipt['request_identity_sha256']] == receipt['intent']['intent_id']


def test_verified_overrun_records_full_debt_and_blocks_every_later_intent(policy):
    state, first = reserve(policy, credit.initial_credit_state(policy, now=NOW))
    state, _ = settle(policy, state, observation(policy, first, actual=990))
    held, second = reserve(policy, state, intent(2))
    assert held['spent_credits'] == 990 and second['reserved_credits'] == 10
    seen = observation(policy, second, actual=248)
    final, returned = settle(policy, held, seen)
    assert returned == seen and final['spent_credits'] == 1238
    summary = credit.credit_funding_summary(policy, final, now=NOW)
    assert summary['reserved_credits'] == 0 and summary['available_credits'] == 0
    assert summary['overrun_credits'] == 238 and summary['allocation_credits'] == 1000
    assert held['spent_credits'] == 990 and held['reserved_credits'] == 10
    repeated, repeated_receipt = settle(policy, final, seen, now=NOW + timedelta(seconds=1))
    assert repeated == final and repeated_receipt == seen
    for next_request in (intent(3), intent(4, root_lineage_id='different-original-root')):
        with pytest.raises(SpendBlocked, match='credit_allocation_exhausted'):
            reserve(policy, final, next_request)
    with pytest.raises(SpendBlocked, match='credit_request_already_reserved'):
        reserve(policy, final, intent(8, request_sha256=intent(2)['request_sha256']))
    with pytest.raises(SpendBlocked, match='credit_observation_conflict'):
        settle(policy, final, seen | {'actual_credit_cost': 10})


def test_final_debt_can_exceed_one_billion_but_stays_within_finite_account_bound(policy):
    policy['allocation_credits'] = 1_000_000_000
    policy['balance'].update(quota_credits=1_000_000_000, used_credits=0, withheld_credits=0)
    state, first = reserve(policy, credit.initial_credit_state(policy, now=NOW))
    state, _ = settle(policy, state, observation(policy, first, actual=999_999_999))
    state, last = reserve(policy, state, intent(2))
    assert last['reserved_credits'] == 1
    final, _ = settle(policy, state, observation(policy, last, actual=1_000_000_000))
    summary = credit.credit_funding_summary(policy, final, now=NOW)
    assert summary['spent_credits'] == 1_999_999_999
    assert summary['overrun_credits'] == 999_999_999 and summary['available_credits'] == 0
    with pytest.raises(SpendBlocked, match='credit_allocation_exhausted'):
        reserve(policy, final, intent(3))


def test_account_quota_not_internal_hold_is_the_verified_meter_bound(policy):
    held, receipt = reserve(policy, credit.initial_credit_state(policy, now=NOW))
    actual = policy['balance']['quota_credits']
    final, _ = settle(policy, held, observation(policy, receipt, actual=actual))
    assert final['spent_credits'] == actual and final['reserved_credits'] == 0
    before = deepcopy(held)
    with pytest.raises(SpendBlocked, match='credit_observation_invalid'):
        settle(policy, held, observation(policy, receipt, actual=actual + 1))
    assert held == before


def test_reconstructed_history_cannot_have_an_intent_after_an_overrun(policy):
    state = credit.initial_credit_state(policy, now=NOW)
    for number, actual in ((1, 990), (2, 1), (3, 1)):
        state, receipt = reserve(policy, state, intent(number))
        state, _ = settle(policy, state, observation(policy, receipt, actual=actual))
    # Even coherent totals and valid receipt hashes cannot justify a third
    # request after the second request's retrospectively forged overrun.
    broken = deepcopy(state)
    broken['intents'][intent(2)['intent_id']]['settlement']['actual_credit_cost'] = 248
    broken['spent_credits'] = 1239
    assert broken['reserved_credits'] == 0
    with pytest.raises(SpendBlocked, match='credit_state_invalid'):
        credit.credit_funding_summary(policy, broken, now=NOW)


@pytest.mark.parametrize('field,value', [
    ('terminal', False), ('terminal', 1), ('actual_credit_cost', 0),
    ('actual_credit_cost', -1), ('actual_credit_cost', 131001), ('actual_credit_cost', True),
    ('actual_credit_cost', 0.55), ('actual_credit_cost', '248'),
    ('source', 'estimated_characters'), ('request_sha256', digest('other body')),
    ('root_lineage_id', 'recovery-child-0002'), ('channel_id', 'other-channel-0002'),
    ('source_connection_id', 'new-oauth-connection-0002'),
    ('account_sha256', digest('another account')), ('credential_sha256', digest('rotated key')),
    ('reservation_sha256', digest('another reservation')),
    ('provider_request_id_sha256', 'raw-request-id'), ('response_proof_sha256', ''),
    ('observed_at', '2026-09-09T13:59:59Z'), ('observed_at', '2026-09-09T14:00:01Z'),
])
def test_invalid_or_unbound_meter_never_releases_hold(policy, field, value):
    state, receipt = reserve(policy, credit.initial_credit_state(policy, now=NOW))
    before = deepcopy(state)
    with pytest.raises(SpendBlocked, match='credit_observation_invalid'):
        settle(policy, state, observation(policy, receipt, **{field: value}))
    assert state == before and state['reserved_credits'] == 1000


@pytest.mark.parametrize('field,value', [
    ('actual_credit_cost', 247), ('response_proof_sha256', digest('different proof')),
    ('provider_request_id_sha256', digest('different provider request')),
    ('observed_at', '2026-09-09T14:00:01Z'),
])
def test_conflicting_settlement_cannot_mint_capacity(policy, field, value):
    state, receipt = reserve(policy, credit.initial_credit_state(policy, now=NOW))
    seen = observation(policy, receipt)
    settled, _ = settle(policy, state, seen)
    before = deepcopy(settled)
    with pytest.raises(SpendBlocked, match='credit_observation_conflict'):
        settle(policy, settled, seen | {field: value}, now=NOW + timedelta(seconds=1))
    assert settled == before and settled['spent_credits'] == 248


def test_provider_request_cannot_settle_two_distinct_intents(policy):
    state, receipt1 = reserve(policy, credit.initial_credit_state(policy, now=NOW))
    first = observation(policy, receipt1)
    state, _ = settle(policy, state, first)
    state, receipt2 = reserve(policy, state, intent(2))
    with pytest.raises(SpendBlocked, match='credit_observation_conflict'):
        settle(policy, state, observation(policy, receipt2,
               provider_request_id_sha256=first['provider_request_id_sha256']))
    assert state['spent_credits'] == 248 and state['reserved_credits'] == 752


@pytest.mark.parametrize('field', ['channel_id', 'source_connection_id'])
def test_source_root_cannot_be_rebound_even_after_exact_settlement(policy, field):
    state, receipt = reserve(policy, credit.initial_credit_state(policy, now=NOW))
    state, _ = settle(policy, state, observation(policy, receipt))
    with pytest.raises(SpendBlocked, match='credit_root_binding_mismatch'):
        reserve(policy, state, intent(2, **{field: 'changed-source-identity'}))
    child_nonce = intent(9, request_sha256=intent()['request_sha256'])
    with pytest.raises(SpendBlocked, match='credit_request_already_reserved'):
        reserve(policy, state, child_nonce)
    # A truly different source root may share the same account's remaining pool.
    _, next_receipt = reserve(policy, state, intent(2, root_lineage_id='other-original-root',
                                                 **{field: 'changed-source-identity'}))
    assert next_receipt['reserved_credits'] == 752


@pytest.mark.parametrize('actual_field', ['actual_account_sha256', 'actual_credential_sha256'])
def test_actual_transport_binding_required_at_both_transitions(policy, actual_field):
    initial = credit.initial_credit_state(policy, now=NOW)
    with pytest.raises(SpendBlocked, match='credit_actual_binding_mismatch'):
        reserve(policy, initial, **{actual_field: digest('wrong actual identity')})
    held, receipt = reserve(policy, initial)
    with pytest.raises(SpendBlocked, match='credit_actual_binding_mismatch'):
        settle(policy, held, observation(policy, receipt),
               **{actual_field: digest('wrong actual identity')})
    assert held['reserved_credits'] == 1000


@pytest.mark.parametrize('missing', ['terminal', 'response_proof_sha256', 'reservation_sha256'])
def test_partial_observation_cannot_reconcile(policy, missing):
    held, receipt = reserve(policy, credit.initial_credit_state(policy, now=NOW))
    seen = observation(policy, receipt)
    del seen[missing]
    with pytest.raises(SpendBlocked, match='credit_observation_invalid'):
        settle(policy, held, seen)
    assert held['reserved_credits'] == 1000


@pytest.mark.parametrize('state', [None, {}, [], 'missing'])
def test_missing_state_is_not_an_empty_allowance(policy, state):
    with pytest.raises(SpendBlocked, match='credit_state_invalid'):
        reserve(policy, state)


@pytest.mark.parametrize('damage', ['intents', 'request_index', 'roots', 'spent', 'reserved',
                                   'receipt_bool', 'revision', 'settlement', 'root_binding'])
def test_partial_state_and_inconsistent_counters_fail_closed(policy, damage):
    held, receipt = reserve(policy, credit.initial_credit_state(policy, now=NOW))
    settled, _ = settle(policy, held, observation(policy, receipt))
    broken = deepcopy(settled)
    if damage in ('intents', 'request_index', 'roots'):
        broken[damage] = {}
    elif damage in ('spent', 'reserved'):
        broken[damage + '_credits'] += 1
    elif damage == 'receipt_bool':
        broken['intents'][intent()['intent_id']]['reservation']['version'] = True
    elif damage == 'revision':
        broken['revision'] = 0
    elif damage == 'settlement':
        broken['intents'][intent()['intent_id']]['settlement'] = None
    else:
        broken['roots']['original-root-0001']['source_connection_id'] = 'silently-reconnected'
    with pytest.raises(SpendBlocked, match='credit_state_invalid'):
        credit.credit_funding_summary(policy, broken, now=NOW)
    assert settled['spent_credits'] == 248


def test_coincidentally_equal_spent_counter_does_not_hide_missing_request_index(policy):
    state, receipt = reserve(policy, credit.initial_credit_state(policy, now=NOW))
    state, _ = settle(policy, state, observation(policy, receipt, actual=100))
    state, second = reserve(policy, state, intent(2))
    state, _ = settle(policy, state, observation(policy, second, actual=100))
    broken = deepcopy(state)
    del broken['intents'][receipt['intent']['intent_id']]
    broken['intents'][second['intent']['intent_id']]['settlement']['actual_credit_cost'] = 200
    # Recomputing spent totals still cannot restore the original durable fence.
    with pytest.raises(SpendBlocked, match='credit_state_invalid'):
        credit.credit_funding_summary(policy, broken, now=NOW)


@pytest.mark.parametrize('field,value', [
    ('max_credit_limit_extension', 1), ('max_credit_limit_extension', False),
    ('max_credit_limit_extension', 'unlimited'), ('can_extend_character_limit', True),
    ('can_extend_character_limit', 0), ('auto_top_up_enabled', True),
    ('auto_top_up_enabled', 0), ('auto_top_up_source', 'assumed_from_plan'),
])
def test_provider_cash_controls_cannot_be_assumed_from_credit_balance(policy, field, value):
    policy['cash_controls'][field] = value
    with pytest.raises(SpendBlocked, match='credit_cash_controls_unverified'):
        credit.initial_credit_state(policy, now=NOW)


def test_owner_attestation_is_supported_without_claiming_api_observation(policy):
    accepted = credit.validate_credit_policy(policy, now=NOW)
    assert accepted['cash_controls']['auto_top_up_source'] == 'owner_attested'
    accepted['cash_controls']['auto_top_up_source'] = 'observed_account'
    assert policy['cash_controls']['auto_top_up_source'] == 'owner_attested'
    assert credit.validate_credit_policy(accepted, now=NOW)['cash_controls']['auto_top_up_source'] == 'observed_account'


@pytest.mark.parametrize('path,value', [
    (('allocation_credits',), 0), (('allocation_credits',), True),
    (('allocation_credits',), 59518), (('allocation_credits',), 59617),
    (('balance', 'withheld_credits'), 59618), (('balance', 'used_credits'), 131001),
    (('balance', 'quota_credits'), 1_000_000_001), (('balance', 'used_credits'), 71383.0),
])
def test_allocation_is_native_positive_and_net_of_observed_use_and_withheld(policy, path, value):
    target = policy if len(path) == 1 else policy[path[0]]
    target[path[-1]] = value
    with pytest.raises(SpendBlocked):
        credit.initial_credit_state(policy, now=NOW)


@pytest.mark.parametrize('field,value', [
    ('route', credit.ROUTE + '?other=1'), ('route', credit.ROUTE.replace('https:', 'http:')),
    ('model', 'eleven_flash_v2_5'), ('voice_id', 'another-voice'),
    ('provider', 'other_provider'), ('evidence_sha256', ''),
])
def test_policy_supports_only_exact_reviewed_native_profile(policy, field, value):
    policy[field] = value
    with pytest.raises(SpendBlocked, match='credit_policy_invalid'):
        credit.initial_credit_state(policy, now=NOW)


def test_no_claim_of_usd_or_per_character_upper_bound_can_be_added(policy):
    for field, value in (('currency', 'USD'), ('maximum_micro', 0),
                         ('billing_unit_bound', {'verified': True, 'factor': 0.55})):
        with pytest.raises(SpendBlocked, match='credit_policy_invalid'):
            credit.validate_credit_policy(policy | {field: value}, now=NOW)


def test_clock_before_opening_never_releases_reserved_credits(policy):
    held, receipt = reserve(policy, credit.initial_credit_state(policy, now=NOW))
    with pytest.raises(SpendBlocked, match='credit_clock_invalid'):
        settle(policy, held, observation(policy, receipt), now=datetime(2026, 9, 9, 12, tzinfo=timezone.utc))
    assert held['reserved_credits'] == 1000


@pytest.mark.parametrize('when', [datetime(2026, 9, 10, tzinfo=timezone.utc),
                                 datetime(2026, 10, 1, tzinfo=timezone.utc)])
def test_verified_late_meter_preserves_usage_without_opening_new_admission(policy, when):
    held, receipt = reserve(policy, credit.initial_credit_state(policy, now=NOW))
    observed = observation(policy, receipt)
    updated, terminal = settle(policy, held, observed, now=when)
    assert terminal == observed and updated['spent_credits'] == observed['actual_credit_cost']
    assert updated['reserved_credits'] == 0 and held['reserved_credits'] == 1000
    with pytest.raises(SpendBlocked):
        reserve(policy, updated, intent(2), now=when)


def test_evidence_expiry_cannot_extend_past_provider_credit_reset(policy):
    policy['balance']['provider_reset_at'] = '2026-09-09T23:59:59Z'
    with pytest.raises(SpendBlocked, match='credit_policy_invalid'):
        credit.initial_credit_state(policy, now=NOW)


def test_changed_policy_cannot_replenish_or_settle_old_state(policy):
    held, receipt = reserve(policy, credit.initial_credit_state(policy, now=NOW))
    changed = deepcopy(policy)
    changed['allocation_credits'] += 1
    with pytest.raises(SpendBlocked, match='credit_policy_mismatch'):
        reserve(changed, held, intent(2))
    with pytest.raises(SpendBlocked, match='credit_policy_mismatch'):
        settle(changed, held, observation(policy, receipt))


def test_all_outputs_detached_and_roundtrip_json_keeps_replay_fences(policy):
    original = deepcopy(policy)
    request = intent()
    held, receipt = reserve(policy, credit.initial_credit_state(policy, now=NOW), request)
    receipt['intent']['channel_id'] = 'caller-mutated-channel'
    request['request_sha256'] = digest('caller-mutated-body')
    assert policy == original
    stored = json.loads(json.dumps(held, sort_keys=True))
    authentic_receipt = stored['intents'][intent()['intent_id']]['reservation']
    seen = observation(policy, authentic_receipt)
    settled, returned = settle(policy, stored, seen)
    returned['actual_credit_cost'] = 1
    seen['response_proof_sha256'] = digest('caller-mutated-proof')
    assert settled['spent_credits'] == 248
    assert stored['reserved_credits'] == 1000
    with pytest.raises(SpendBlocked, match='credit_request_already_reserved'):
        reserve(policy, settled, intent(5, request_sha256=intent()['request_sha256']))


def test_pure_revision_requires_future_atomic_compare_and_write(policy):
    initial = credit.initial_credit_state(policy, now=NOW)
    first, _ = reserve(policy, initial)
    competing, _ = reserve(policy, initial, intent(2))
    assert first['revision'] == competing['revision'] == 1
    assert initial['revision'] == 0
    # A pure candidate is not an execution permit. Only one can win the future CAS.
    assert first != competing


def test_bounded_history_never_drops_old_replay_receipts_to_allow_more(policy, monkeypatch):
    monkeypatch.setattr(credit, '_MAX_INTENTS', 2)
    state = credit.initial_credit_state(policy, now=NOW)
    for number in (1, 2):
        state, receipt = reserve(policy, state, intent(number))
        state, _ = settle(policy, state, observation(policy, receipt, actual=1))
    assert state['spent_credits'] == 2 and len(state['request_index']) == 2
    with pytest.raises(SpendBlocked, match='credit_intent_history_limit'):
        reserve(policy, state, intent(3))
    with pytest.raises(SpendBlocked, match='credit_request_already_reserved'):
        reserve(policy, state, intent(3, request_sha256=intent()['request_sha256']))


def test_rehashed_receipt_cannot_hide_an_underreserved_historical_intent(policy):
    state, receipt = reserve(policy, credit.initial_credit_state(policy, now=NOW))
    state, _ = settle(policy, state, observation(policy, receipt, actual=100))
    broken = deepcopy(state)
    entry = broken['intents'][intent()['intent_id']]
    forged = entry['reservation']
    forged['reserved_credits'] = 500  # full remaining allocation was1000
    canonical = json.dumps({k: v for k, v in forged.items() if k != 'reservation_sha256'},
                           ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    forged['reservation_sha256'] = hashlib.sha256(canonical).hexdigest()
    entry['settlement']['reservation_sha256'] = forged['reservation_sha256']
    # Paid totals and ordinary receipt hashes still match: sequence reconstruction
    # must enforce the original full-allocation admission rule independently.
    with pytest.raises(SpendBlocked, match='credit_state_invalid'):
        credit.credit_funding_summary(policy, broken, now=NOW)


def test_native_policy_validation_requires_aware_clock(policy):
    with pytest.raises(SpendBlocked, match='credit_clock_invalid'):
        credit.initial_credit_state(policy, now=NOW.replace(tzinfo=None))
