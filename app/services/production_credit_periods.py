"""Renew observed native allowances while retaining every prior receipt.

Only the trusted account reader supplies an allowance observation. A period
change buys nothing, never replays a POST and never clears unknown usage. Old
policies, states and journals remain independently anchored and fully checked.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from app.services.production_credit_funding import (
    _timestamp, initial_credit_state, reserve_credit_intent, settle_credit_intent,
    validate_credit_policy,
)
from app.services.production_credit_ledger import (
    STATE_KEY, JOURNAL_KEY, MODE_FIELD, _require, _object, _json, _hash, _journal,
)
from app.services.production_spend import LEDGER_KEY, SpendBlocked

PREFIX = 'youtube_studio:{production_spend}:native_credit:v1:elevenlabs:'
HISTORY_KEY, HISTORY_ANCHOR = PREFIX + 'period_history', PREFIX + 'period_history_anchor'
HISTORY_FIELD = 'native_credit_period_history'
MAX_PERIODS = 120


def _date(value):
    return _timestamp(value, 'credit_period_evidence_invalid')


def _stamp(value):
    return value.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _empty():
    return {'version': 1, 'periods': []}


def _reconciliation(policy, state, journal, history):
    return _hash({'policy': policy, 'state': state, 'journal': journal, 'history': history})


def next_policy(policy, state, journal, history, observation, *, now, reallocation=None):
    """Pure candidate from a fresh account read; never an estimated new balance."""
    code = 'credit_period_evidence_invalid'
    _require(type(observation) is dict and set(observation) == {
        'version', 'source', 'account_sha256', 'credential_sha256', 'observed_at',
        'quota_credits', 'used_credits', 'provider_reset_at', 'status',
        'max_credit_limit_extension', 'can_extend_character_limit', 'response_sha256'}, code)
    _require(type(observation['version']) is int and observation['version'] == 1
        and observation['source'] == 'verified_GET_v1_user'
        and observation['account_sha256'] == policy['account_sha256']
        and observation['credential_sha256'] == policy['credential_sha256']
        and observation['status'] == 'active'
        and type(observation['max_credit_limit_extension']) is int
        and observation['max_credit_limit_extension'] == 0
        and observation['can_extend_character_limit'] is False, code)
    from app.services.production_credit_funding import _hash as valid_hash
    valid_hash(observation['response_sha256'], code)
    observed, reset = _date(observation['observed_at']), _date(observation['provider_reset_at'])
    earliest = policy['valid_from'] if reallocation is not None else policy['valid_until']
    _require(_date(earliest) <= observed <= now <= observed + timedelta(seconds=120)
        and reset > now, code)
    _require(state['reserved_credits'] == 0 and all(
        entry['settlement'] is not None for entry in state['intents'].values()),
        'credit_period_has_uncertain_usage')
    _require(state['spent_credits'] <= policy['allocation_credits'], 'credit_period_has_overrun')
    quota, used = observation['quota_credits'], observation['used_credits']
    _require(type(quota) is int and type(used) is int and 0 <= used < quota <= 1_000_000_000, code)
    previous_reset = _date(policy['balance']['provider_reset_at'])
    original = history['periods'][0]['policy'] if history['periods'] else policy
    withheld = policy['balance']['withheld_credits']
    if reallocation is not None:
        # Explicit operator action only. This is neither a provider reset nor
        # an automatic refill. The exact old period remains in the chain.
        _require(type(reallocation) is dict and set(reallocation) == {
            'version', 'source', 'authorization_sha256', 'withheld_credits', 'allocation_cap_credits'}
            and type(reallocation['version']) is int and reallocation['version'] == 1
            and reallocation['source'] == 'owner_authorized_existing_subscription_credits', code)
        valid_hash(reallocation['authorization_sha256'], code)
        withheld, ceiling = reallocation['withheld_credits'], reallocation['allocation_cap_credits']
        _require(type(withheld) is int and 0 <= withheld < quota
            and type(ceiling) is int and 0 < ceiling <= original['allocation_credits'], code)
        _require(now < _date(policy['valid_until']) and reset == previous_reset
            and quota == policy['balance']['quota_credits']
            and used >= policy['balance']['used_credits'] + state['spent_credits'],
            'credit_reallocation_account_unreconciled')
        _require(not any(row.get('reallocation') is not None
            and row['policy']['balance']['provider_reset_at'] == observation['provider_reset_at']
            for row in history['periods']), 'credit_reallocation_already_used')
    elif reset == previous_reset:
        # A calendar boundary is not a new provider allowance. Carry only the
        # unspent allocation, additionally bounded by the current real balance.
        end = _date(policy['valid_until'])
        _require(end.day == 1 and (end.hour, end.minute, end.second) == (0, 0, 0)
            and observed < previous_reset, 'credit_provider_period_not_renewed')
        ceiling = policy['allocation_credits'] - state['spent_credits']
    else:
        _require(reset > previous_reset and observed >= previous_reset,
                 'credit_provider_period_not_renewed')
        ceiling = original['allocation_credits']
        # A one-time use of existing reserve does not increase future periods.
        withheld = original['balance']['withheld_credits']
    allocation = min(ceiling, quota - used - withheld)
    _require(allocation > 0, 'credit_period_balance_unavailable')
    if reallocation is not None:
        _require(allocation > policy['allocation_credits'] - state['spent_credits'],
                 'credit_reallocation_no_additional_capacity')
    boundary = datetime(now.year + (now.month == 12), now.month % 12 + 1, 1, tzinfo=timezone.utc)
    if reallocation is not None: boundary = min(boundary, _date(policy['valid_until']))
    candidate = deepcopy(policy)
    candidate.update(month=now.strftime('%Y-%m'), valid_from=_stamp(now),
        valid_until=_stamp(min(reset, boundary)), allocation_credits=allocation,
        evidence_sha256=_hash(observation),
        reconciliation_sha256=_reconciliation(policy, state, journal, history),
        balance={'observed_at': _stamp(observed), 'provider_reset_at': _stamp(reset),
            'quota_credits': quota, 'used_credits': used, 'withheld_credits': withheld})
    # The owner's existing auto-top-up OFF evidence persists. This endpoint
    # does not report that separate setting, so do not relabel it as observed.
    candidate['cash_controls']['overage_observation_sha256'] = _hash(observation)
    return validate_credit_policy(candidate, now=now)


def read_history(ledger, pipe, policy, state, now):
    stored, anchor = pipe.get(HISTORY_KEY), pipe.get(HISTORY_ANCHOR)
    foundation_anchor = pipe.hget(LEDGER_KEY, HISTORY_FIELD)
    if stored is None and anchor is None and foundation_anchor is None:
        return _empty()
    _require(ledger.foundation is not None and stored is not None
        and pipe.pttl(HISTORY_KEY) == pipe.pttl(HISTORY_ANCHOR) == -1,
        'credit_period_history_missing')
    history = _object(stored)
    _require(_hash(history) == anchor == foundation_anchor
        and set(history) == {'version', 'periods'}
        and type(history['version']) is int and history['version'] == 1
        and type(history['periods']) is list and 1 <= len(history['periods']) <= MAX_PERIODS,
        'credit_period_history_invalid')
    prefix = _empty()
    for index, row in enumerate(history['periods']):
        fields = {'policy', 'state', 'journal', 'closed_at', 'observation', 'successor_policy_sha256'}
        _require(type(row) is dict and set(row) in (fields, fields | {'reallocation'})
            and ('reallocation' not in row or row['reallocation'] is not None),
            'credit_period_history_invalid')
        closed = _date(row['closed_at'])
        _require(closed <= now, 'credit_period_history_invalid')
        old_policy, old_state, _, journal = ledger._validate_records(
            {'policy': _json(row['policy']), 'state': _json(row['state'])},
            row['journal'], closed, historical=True)
        candidate = next_policy(old_policy, old_state, journal, prefix, row['observation'], now=closed,
                                reallocation=row.get('reallocation'))
        following = history['periods'][index + 1]['policy'] if index + 1 < len(history['periods']) else policy
        _require(candidate == following and row['successor_policy_sha256'] == _hash(following),
                 'credit_period_history_chain_changed')
        prefix['periods'].append(row)
    # Old root bindings and provider IDs remain unique across calendar periods.
    roots, intents, requests, provider_ids = {}, set(), set(), set()
    for item in [*(row['state'] for row in history['periods']), state]:
        for root, binding in item['roots'].items():
            _require(root not in roots or roots[root] == binding, 'credit_root_binding_mismatch')
            roots[root] = binding
        for intent_id, entry in item['intents'].items():
            identity = entry['reservation']['request_identity_sha256']
            _require(intent_id not in intents and identity not in requests,
                     'credit_period_request_replayed')
            intents.add(intent_id); requests.add(identity)
            if entry['settlement'] is not None:
                provider_id = entry['settlement']['provider_request_id_sha256']
                _require(provider_id not in provider_ids, 'credit_observation_conflict')
                provider_ids.add(provider_id)
    _require(len(intents) <= 1024, 'credit_foundation_history_limit')
    return history


def check_prior_identity(ledger, pipe, policy, state, now, operation, values):
    history = read_history(ledger, pipe, policy, state, now)
    for row in history['periods']:
        prior = row['state']
        if operation is reserve_credit_intent:
            intent = values['intent']
            _require(intent['intent_id'] not in prior['intents'], 'credit_intent_already_reserved')
            root = intent['root_lineage_id']
            binding = {'channel_id': intent['channel_id'], 'source_connection_id': intent['source_connection_id']}
            _require(root not in prior['roots'] or prior['roots'][root] == binding,
                     'credit_root_binding_mismatch')
        elif operation is settle_credit_intent:
            provider_id = values['observation'].get('provider_request_id_sha256')
            _require(all(entry['settlement']['provider_request_id_sha256'] != provider_id
                for entry in prior['intents'].values()), 'credit_observation_conflict')
    if operation is reserve_credit_intent:
        total = len(state['intents']) + sum(len(row['state']['intents']) for row in history['periods'])
        _require(total < 1024, 'credit_foundation_history_limit')


def recorded_intents(ledger, pipe, policy, state, now):
    """Complete validated history for media-free checks and narrator continuity."""
    history = read_history(ledger, pipe, policy, state, now)
    return [entry for prior in [*(row['state'] for row in history['periods']), state]
            for entry in prior['intents'].values()]


def renewal_snapshot(ledger):
    """Read expired accounting for reconciliation, never return a send permit."""
    with ledger.client.pipeline() as pipe:
        ledger._watch(pipe)
        policy, state, _, journal = ledger._read(pipe, ledger.clock(), historical=True)
        result = {'policy': policy, 'state': state, 'state_sha256': _hash(state),
                  'policy_sha256': _hash(policy), 'journal_sha256': _hash(journal)}
        ledger._ping(pipe)
    return result


def renew(ledger, observation, *, expected_policy_sha256, expected_state_sha256):
    return _advance(ledger, observation, expected_policy_sha256=expected_policy_sha256,
                    expected_state_sha256=expected_state_sha256)


def reallocate_existing_balance(ledger, observation, *, authorization_sha256,
        withheld_credits, allocation_cap_credits, expected_policy_sha256, expected_state_sha256):
    """Operator-only, once per provider period, with fresh existing-account proof.

    Never called by a worker tick or a provider request. The caller has explicit
    owner authority to use existing subscription credits and first reconciles
    outstanding work. No purchase, top-up, debt erasure or unknown-use release.
    """
    return _advance(ledger, observation, expected_policy_sha256=expected_policy_sha256,
        expected_state_sha256=expected_state_sha256, reallocation={
            'version': 1, 'source': 'owner_authorized_existing_subscription_credits',
            'authorization_sha256': authorization_sha256, 'withheld_credits': withheld_credits,
            'allocation_cap_credits': allocation_cap_credits})


def _advance(ledger, observation, *, expected_policy_sha256, expected_state_sha256, reallocation=None):
    """One acknowledged archive-and-advance; never retry an uncertain commit."""
    _require(ledger.foundation is not None, 'credit_foundation_required')
    observation = _object(_json(observation))
    if reallocation is not None: reallocation = _object(_json(reallocation))
    try:
        with ledger.client.pipeline() as pipe:
            ledger._watch(pipe)
            now = ledger.clock()
            policy, state, _, journal = ledger._read(pipe, now, historical=True)
            history = read_history(ledger, pipe, policy, state, now)
            if policy['evidence_sha256'] == _hash(observation) and now < _date(policy['valid_until']):
                _require(history['periods'] and history['periods'][-1].get('reallocation') == reallocation,
                         'credit_period_snapshot_changed')
                ledger._ping(pipe)
                return {'status': 'already_reallocated' if reallocation else 'already_renewed',
                        'valid_until': policy['valid_until']}
            _require(_hash(policy) == expected_policy_sha256 and _hash(state) == expected_state_sha256,
                     'credit_period_snapshot_changed')
            _require(len(history['periods']) < MAX_PERIODS, 'credit_period_history_limit')
            candidate = next_policy(policy, state, journal, history, observation, now=now,
                                    reallocation=reallocation)
            updated = initial_credit_state(candidate, now=now)
            genesis = {'version': 1, 'policy_sha256': _hash(candidate),
                       'initialized_at': updated['last_updated_at'], 'foundation_bound': True}
            next_journal = _journal(candidate, updated, genesis)
            history['periods'].append({'policy': policy, 'state': state, 'journal': journal,
                'closed_at': _stamp(now), 'observation': observation, 'successor_policy_sha256': _hash(candidate)})
            if reallocation is not None: history['periods'][-1]['reallocation'] = reallocation
            encoded, digest = _json(history), _hash(history)
            first = len(history['periods']) == 1
            pipe.multi()
            pipe.set(HISTORY_KEY, encoded)
            pipe.set(HISTORY_ANCHOR, digest)
            pipe.hset(LEDGER_KEY, HISTORY_FIELD, digest)
            pipe.hset(STATE_KEY, mapping={'policy': _json(candidate), 'state': _json(updated)})
            pipe.delete(JOURNAL_KEY)
            pipe.hset(JOURNAL_KEY, mapping=next_journal)
            pipe.hset(LEDGER_KEY, MODE_FIELD, _json(ledger._mode(candidate)))
            ack = pipe.execute()
            expected = [True, True, 1 if first else 0, 0, 1, len(next_journal), 0]
            _require(type(ack) is list and len(ack) == len(expected)
                and all(type(actual) is type(wanted) and actual == wanted for actual, wanted in zip(ack, expected)),
                'credit_period_commit_uncertain')
            return {'status': 'reallocated' if reallocation else 'renewed', 'valid_until': candidate['valid_until'],
                    'allocation_credits': candidate['allocation_credits'], 'archived_periods': len(history['periods'])}
    except SpendBlocked:
        raise
    except Exception:
        raise SpendBlocked('credit_period_commit_uncertain') from None
