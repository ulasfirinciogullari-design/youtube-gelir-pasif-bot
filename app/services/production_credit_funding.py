"""Pure native-credit accounting for the existing ElevenLabs voice only.

The explicit opt-in production_credit_runtime caller defaults OFF. These pure
functions create no provider request, USD quote, ledger, allowance refresh or
activation. A reservation holds ALL the remaining operator allocation because
the exact request meter is not assumed.
Provider-enforced cash controls are separate from any per-character estimate.
A verified meter may exceed the internal reservation: preserve that consumed
credit debt and stop later admission instead of hiding it as unknown usage.

All inputs containing account, key, source-root and provider observations must
come from the trusted runtime adapter. Hashes bind evidence; they are not provider
signatures. The caller must persist each returned state and the existing durable
request fence in one acknowledged compare-and-write transaction. Revision and
structural validation alone cannot authenticate a stale/partially lost state.
Never use initial_credit_state as a missing-state fallback. Retain permanent
request receipts outside an expiring job and never replay an uncertain POST.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re

from app.services.production_spend import SpendBlocked


PROVIDER = 'elevenlabs'
VOICE_ID = 'WtOce4YK0dDSxlVlSdBh'
MODEL = 'eleven_multilingual_v2'
TURKISH_SHORT_MODEL = 'eleven_flash_v2_5'
ROUTE = 'https://api.elevenlabs.io/v1/text-to-speech/' + VOICE_ID + '/with-timestamps'
_MAX_CREDITS = 1_000_000_000
_MAX_INTENTS = 1024
_MAX_STATE_BYTES = 4 * 1024 * 1024
_HASH = re.compile(r'[0-9a-f]{64}')
_ID = re.compile(r'[A-Za-z0-9_-]{8,128}')
_STAMP = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z')
_INTENT_FIELDS = {'intent_id', 'root_lineage_id', 'channel_id', 'source_connection_id',
                  'request_sha256', 'route', 'model', 'voice_id'}
_OBSERVATION_FIELDS = {
    'version', 'terminal', 'source', 'intent_id', 'reservation_sha256',
    'account_sha256', 'credential_sha256', 'root_lineage_id', 'channel_id',
    'source_connection_id', 'request_sha256', 'provider_request_id_sha256',
    'response_proof_sha256', 'actual_credit_cost', 'observed_at',
}


def _require(condition, code):
    if not condition:
        raise SpendBlocked(code)


def _exact(value, fields, code):
    _require(type(value) is dict and set(value) == set(fields), code)


def _integer(value, code, *, positive=False, maximum=_MAX_CREDITS):
    _require(type(value) is int and (1 if positive else 0) <= value <= maximum, code)
    return value


def _hash(value, code):
    _require(type(value) is str and _HASH.fullmatch(value) is not None, code)


def _timestamp(value, code):
    _require(type(value) is str and _STAMP.fullmatch(value) is not None, code)
    try:
        return datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except ValueError:
        raise SpendBlocked(code) from None


def _clock(now):
    _require(isinstance(now, datetime) and now.tzinfo is not None
             and now.utcoffset() is not None, 'credit_clock_invalid')
    return now.astimezone(timezone.utc)


def _stamp(now):
    return now.strftime('%Y-%m-%dT%H:%M:%SZ')


def _canonical(value, code):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                          allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise SpendBlocked(code) from None


def _digest(value, code='credit_state_invalid'):
    return hashlib.sha256(_canonical(value, code)).hexdigest()


def validate_credit_policy(policy, *, now):
    """Validate operator evidence without treating a credit count as USD money."""
    code = 'credit_policy_invalid'
    now = _clock(now)
    fields = {'version', 'provider', 'month', 'valid_from', 'valid_until',
                    'account_sha256', 'credential_sha256', 'evidence_sha256',
                    'reconciliation_sha256', 'route', 'model', 'voice_id',
                    'allocation_credits', 'balance', 'cash_controls'}
    extended = type(policy) is dict and type(policy.get('version')) is int and policy['version'] == 2
    _exact(policy, fields | ({'additional_models'} if extended else set()), code)
    _require(type(policy['version']) is int and policy['version'] in (1, 2)
             and policy['provider'] == PROVIDER and policy['route'] == ROUTE
             and policy['model'] == MODEL and policy['voice_id'] == VOICE_ID, code)
    if extended:
        _require(policy['additional_models'] == [TURKISH_SHORT_MODEL], code)
    for field in ('account_sha256', 'credential_sha256', 'evidence_sha256', 'reconciliation_sha256'):
        _hash(policy[field], code)
    _require(type(policy['month']) is str and policy['month'] == now.strftime('%Y-%m'),
             'credit_period_mismatch')
    start, end = (_timestamp(policy[field], code) for field in ('valid_from', 'valid_until'))
    try:
        boundary = datetime(now.year + (now.month == 12), now.month % 12 + 1, 1,
                            tzinfo=timezone.utc)
    except ValueError:
        raise SpendBlocked('credit_clock_invalid') from None
    _require(start.strftime('%Y-%m') == policy['month'] and start < end <= boundary, code)
    _require(start <= now < end, 'credit_policy_expired')
    balance = policy['balance']
    _exact(balance, {'observed_at', 'provider_reset_at', 'quota_credits', 'used_credits',
                     'withheld_credits'}, code)
    observed = _timestamp(balance['observed_at'], code)
    reset = _timestamp(balance['provider_reset_at'], code)
    _require(observed.strftime('%Y-%m') == policy['month'] and observed <= start < end <= reset, code)
    quota = _integer(balance['quota_credits'], code, positive=True)
    used = _integer(balance['used_credits'], code)
    withheld = _integer(balance['withheld_credits'], code)
    allocation = _integer(policy['allocation_credits'], code, positive=True)
    _require(used <= quota and withheld <= quota - used
             and allocation <= quota - used - withheld, 'credit_allocation_uncovered')
    controls = policy['cash_controls']
    _exact(controls, {'max_credit_limit_extension', 'can_extend_character_limit',
                      'overage_observation_sha256', 'auto_top_up_enabled',
                      'auto_top_up_source', 'auto_top_up_proof_sha256'}, code)
    _require(type(controls['max_credit_limit_extension']) is int
             and controls['max_credit_limit_extension'] == 0
             and controls['can_extend_character_limit'] is False
             and controls['auto_top_up_enabled'] is False
             and type(controls['auto_top_up_source']) is str
             and controls['auto_top_up_source'] in {'owner_attested', 'observed_account'},
             'credit_cash_controls_unverified')
    _hash(controls['overage_observation_sha256'], code)
    _hash(controls['auto_top_up_proof_sha256'], code)
    _require(len(_canonical(policy, code)) <= 16_384, code)
    return deepcopy(policy)


def initial_credit_state(policy, *, now):
    """Explicit initial candidate only; never a missing/lost-state recovery API.

    The durable initializer must independently refuse existing state or
    earlier native-credit receipts. This pure constructor cannot inspect storage.
    """
    policy = validate_credit_policy(policy, now=now)
    return {'version': 1, 'policy_sha256': _digest(policy), 'month': policy['month'],
            'revision': 0, 'spent_credits': 0, 'reserved_credits': 0,
            'last_updated_at': _stamp(_clock(now)), 'intents': {}, 'request_index': {}, 'roots': {}}


def _intent(intent, policy, code):
    _exact(intent, _INTENT_FIELDS, code)
    for field in ('intent_id', 'request_sha256'):
        _hash(intent[field], code)
    for field in ('root_lineage_id', 'channel_id', 'source_connection_id'):
        _require(type(intent[field]) is str and _ID.fullmatch(intent[field]) is not None, code)
    _require(intent['route'] == policy['route']
             and intent['model'] in [policy['model'], *policy.get('additional_models', [])]
             and intent['voice_id'] == policy['voice_id'], code)


def _request_identity(intent, policy):
    return _digest({'root_lineage_id': intent['root_lineage_id'],
                    'request_sha256': intent['request_sha256'], 'route': intent['route'],
                    'model': intent['model'], 'voice_id': intent['voice_id'],
                    'account_sha256': policy['account_sha256']})


def _root_binding(intent):
    return {field: intent[field] for field in ('channel_id', 'source_connection_id')}


def _actual_binding(policy, actual_account_sha256, actual_credential_sha256):
    for value in (actual_account_sha256, actual_credential_sha256):
        _hash(value, 'credit_actual_binding_invalid')
    _require(actual_account_sha256 == policy['account_sha256']
             and actual_credential_sha256 == policy['credential_sha256'], 'credit_actual_binding_mismatch')


def _reservation(policy, intent, amount, stamp, sequence):
    receipt = {'version': 1, 'policy_sha256': _digest(policy), 'account_sha256': policy['account_sha256'],
               'credential_sha256': policy['credential_sha256'], 'intent': deepcopy(intent),
               'request_identity_sha256': _request_identity(intent, policy),
               'reserved_credits': amount, 'reserved_at': stamp, 'sequence': sequence,
               'accounting': 'native_allocation_reservation_not_meter_upper_bound'}
    receipt['reservation_sha256'] = _digest(receipt)
    return receipt


def _observation(observation, receipt, policy, now, code):
    _exact(observation, _OBSERVATION_FIELDS, code)
    _require(type(observation['version']) is int and observation['version'] == 1
             and observation['terminal'] is True
             and observation['source'] == 'verified_provider_meter', code)
    for field in ('account_sha256', 'credential_sha256', 'reservation_sha256',
                  'provider_request_id_sha256', 'response_proof_sha256'):
        _hash(observation[field], code)
    _require(observation['account_sha256'] == policy['account_sha256']
             and observation['credential_sha256'] == policy['credential_sha256']
             and observation['reservation_sha256'] == receipt['reservation_sha256'], code)
    for field in ('intent_id', 'root_lineage_id', 'channel_id', 'source_connection_id', 'request_sha256'):
        _require(observation[field] == receipt['intent'][field], code)
    actual = _integer(observation['actual_credit_cost'], code, positive=True)
    # The internal allocation is not a request-price upper bound. Preserve a
    # verified overrun, bounded by the evidenced provider quota, as consumed debt.
    _require(actual <= policy['balance']['quota_credits'], code)
    observed = _timestamp(observation['observed_at'], code)
    _require(_timestamp(receipt['reserved_at'], code) <= observed <= now, code)


def _validate_state(policy, state, now):
    code = 'credit_state_invalid'
    _exact(state, {'version', 'policy_sha256', 'month', 'revision', 'spent_credits',
                   'reserved_credits', 'last_updated_at', 'intents', 'request_index', 'roots'}, code)
    _require(type(state['version']) is int and state['version'] == 1, code)
    _require(state['policy_sha256'] == _digest(policy), 'credit_policy_mismatch')
    _require(state['month'] == policy['month'], 'credit_period_mismatch')
    _integer(state['revision'], code, maximum=_MAX_INTENTS * 2)
    spent_bound = policy['allocation_credits'] + policy['balance']['quota_credits']
    _integer(state['spent_credits'], code, maximum=spent_bound)
    _integer(state['reserved_credits'], code, maximum=policy['allocation_credits'])
    updated = _timestamp(state['last_updated_at'], code)
    _require(_timestamp(policy['valid_from'], code) <= updated <= now, code)
    _require(type(state['intents']) is dict and len(state['intents']) <= _MAX_INTENTS, code)
    _require(type(state['request_index']) is dict and type(state['roots']) is dict, code)
    spent, held, active, settled = 0, 0, 0, 0
    requests, roots, provider_ids, ordered = {}, {}, set(), {}
    for intent_id, entry in state['intents'].items():
        _hash(intent_id, code)
        _exact(entry, {'reservation', 'settlement'}, code)
        receipt = entry['reservation']
        _exact(receipt, {'version', 'policy_sha256', 'account_sha256', 'credential_sha256',
                         'intent', 'request_identity_sha256', 'reserved_credits', 'reserved_at',
                         'accounting', 'reservation_sha256', 'sequence'}, code)
        _require(type(receipt['version']) is int and receipt['version'] == 1, code)
        _intent(receipt['intent'], policy, code)
        _require(receipt['intent']['intent_id'] == intent_id, code)
        amount = _integer(receipt['reserved_credits'], code, positive=True)
        _require(amount <= policy['allocation_credits'], code)
        reserved_at = _timestamp(receipt['reserved_at'], code)
        _require(_timestamp(policy['valid_from'], code) <= reserved_at <= updated, code)
        sequence = _integer(receipt['sequence'], code, positive=True, maximum=_MAX_INTENTS)
        _require(sequence not in ordered, code)
        ordered[sequence] = entry
        _require(receipt == _reservation(policy, receipt['intent'], amount, receipt['reserved_at'], sequence), code)
        identity = receipt['request_identity_sha256']
        _require(identity not in requests, code)
        requests[identity] = intent_id
        root = receipt['intent']['root_lineage_id']
        binding = _root_binding(receipt['intent'])
        _require(root not in roots or roots[root] == binding, code)
        roots[root] = binding
        observation = entry['settlement']
        if observation is None:
            held += amount
            active += 1
        else:
            _observation(observation, receipt, policy, updated, code)
            provider_id = observation['provider_request_id_sha256']
            _require(provider_id not in provider_ids, code)
            provider_ids.add(provider_id)
            spent += observation['actual_credit_cost']
            settled += 1
    _require(state['request_index'] == requests and state['roots'] == roots, code)
    _require(set(ordered) == set(range(1, len(ordered) + 1)), code)
    available = policy['allocation_credits']
    prior_terminal_at = _timestamp(policy['valid_from'], code)
    for sequence in range(1, len(ordered) + 1):
        entry = ordered[sequence]
        receipt = entry['reservation']
        _require(available > 0 and receipt['reserved_credits'] == available
                 and _timestamp(receipt['reserved_at'], code) >= prior_terminal_at, code)
        if entry['settlement'] is None:
            _require(sequence == len(ordered), code)
        else:
            available -= entry['settlement']['actual_credit_cost']
            prior_terminal_at = _timestamp(entry['settlement']['observed_at'], code)
    _require(active <= 1 and state['spent_credits'] == spent and state['reserved_credits'] == held
             and spent + held <= spent_bound
             and (active == 0 or spent + held == policy['allocation_credits'])
             and state['revision'] == len(state['intents']) + settled, code)
    _require(len(_canonical(state, code)) <= _MAX_STATE_BYTES, code)


def credit_funding_summary(policy, state, *, now):
    policy = validate_credit_policy(policy, now=now)
    _validate_state(policy, state, _clock(now))
    return {'provider': PROVIDER, 'mode': 'covered_native_credits_only', 'month': policy['month'],
            'allocation_credits': policy['allocation_credits'], 'spent_credits': state['spent_credits'],
            'reserved_credits': state['reserved_credits'],
            'available_credits': max(0, policy['allocation_credits'] - state['spent_credits'] - state['reserved_credits']),
            'overrun_credits': max(0, state['spent_credits'] - policy['allocation_credits']),
            'intent_count': len(state['intents']), 'revision': state['revision'],
            'valid_until': policy['valid_until'], 'accounting': 'native_credits_not_usd'}


def reserve_credit_intent(policy, state, *, intent, actual_account_sha256,
                          actual_credential_sha256, now):
    """Return (candidate state, receipt); no send permit until caller atomic ACK.

    The adapter must derive the ORIGINAL root/source connection and request hash
    from the actual immutable request, not a child/caller-supplied replacement.
    Changing the random intent ID cannot replay the same root/request identity.
    """
    now = _clock(now)
    policy = validate_credit_policy(policy, now=now)
    _validate_state(policy, state, now)
    _actual_binding(policy, actual_account_sha256, actual_credential_sha256)
    _intent(intent, policy, 'credit_intent_invalid')
    _require(intent['intent_id'] not in state['intents'], 'credit_intent_already_reserved')
    identity = _request_identity(intent, policy)
    _require(identity not in state['request_index'], 'credit_request_already_reserved')
    root = intent['root_lineage_id']
    _require(root not in state['roots'] or state['roots'][root] == _root_binding(intent),
             'credit_root_binding_mismatch')
    _require(state['reserved_credits'] == 0, 'credit_pool_has_uncertain_intent')
    available = policy['allocation_credits'] - state['spent_credits']
    _require(available > 0, 'credit_allocation_exhausted')
    _require(len(state['intents']) < _MAX_INTENTS, 'credit_intent_history_limit')
    receipt = _reservation(policy, intent, available, _stamp(now), len(state['intents']) + 1)
    updated = deepcopy(state)
    updated['intents'][intent['intent_id']] = {'reservation': deepcopy(receipt), 'settlement': None}
    updated['request_index'][identity] = intent['intent_id']
    updated['roots'][root] = _root_binding(intent)
    updated['reserved_credits'] = available
    updated['revision'] += 1
    updated['last_updated_at'] = _stamp(now)
    _validate_state(policy, updated, now)
    return updated, deepcopy(receipt)


def settle_credit_intent(policy, state, *, observation, actual_account_sha256,
                         actual_credential_sha256, now):
    """Settle exact terminal usage once and release ONLY unused held capacity.

    Positive consumed credits, including an allocation overrun, stay spent.
    An overrun leaves zero available capacity; it never increases an allowance.
    This is not a provider refund, quote correction or permission to repeat a
    POST. The trusted runtime adapter must bind the actual terminal provider
    meter to this reservation; these hashes
    alone cannot prove that it did. Missing/invalid/late observations change no
    state. An identical settlement is idempotent, including its proof identity.
    """
    now = _clock(now)
    policy = validate_credit_policy(policy, now=now)
    _validate_state(policy, state, now)
    _actual_binding(policy, actual_account_sha256, actual_credential_sha256)
    _exact(observation, _OBSERVATION_FIELDS, 'credit_observation_invalid')
    _hash(observation['intent_id'], 'credit_observation_invalid')
    _require(observation['intent_id'] in state['intents'], 'credit_intent_missing')
    entry = state['intents'][observation['intent_id']]
    receipt = entry['reservation']
    _observation(observation, receipt, policy, now, 'credit_observation_invalid')
    if entry['settlement'] is not None:
        _require(entry['settlement'] == observation, 'credit_observation_conflict')
        return deepcopy(state), deepcopy(entry['settlement'])
    for old in state['intents'].values():
        prior = old['settlement']
        _require(prior is None or prior['provider_request_id_sha256'] != observation['provider_request_id_sha256'],
                 'credit_observation_conflict')
    updated = deepcopy(state)
    updated['intents'][observation['intent_id']]['settlement'] = deepcopy(observation)
    updated['reserved_credits'] -= receipt['reserved_credits']
    updated['spent_credits'] += observation['actual_credit_cost']
    updated['revision'] += 1
    updated['last_updated_at'] = _stamp(now)
    _validate_state(policy, updated, now)
    return updated, deepcopy(observation)
