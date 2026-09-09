"""Pure staged accounting for covered allocation versus NEW API cash liability.

This immutable operator-evidence contract does not prove a provider account's
rights, balance, tax rate or no-overage guarantee. A covered allowance is a
verified USD-list-cost equivalent NET of all prior/in-flight commitments; it is
never inferred from subscription dollars or credit counts. Cash factors must
bound the actual bill including every applicable surcharge and tax.

No network, activation, refresh, top-up, refund, persistence or replay handling
exists here. The caller must atomically persist the returned state AND its
existing replay reservation before issuing one request. Never call the initial
state helper as a missing-state fallback. New evidence/months need explicit
operator reconciliation; changing policy cannot reset an existing state.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re
from urllib.parse import urlsplit

from app.services.production_spend import SpendBlocked, SpendQuote


OWNER_MAX_NEW_CASH_MICRO = 10_000_000
_MAX_AMOUNT = 10_000_000_000
_HASH = re.compile(r'[0-9a-f]{64}')
_NAME = re.compile(r'[A-Za-z0-9._/-]{1,100}')
_PROVIDER = re.compile(r'[a-z][a-z0-9_-]{0,31}')
_HOST = re.compile(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+')
_PATH = re.compile(r'/[A-Za-z0-9_-]+(?:[./:-][A-Za-z0-9_-]+)*')
_STAMP = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z')


def _require(condition, code='spend_funding_policy_invalid'):
    if not condition:
        raise SpendBlocked(code)


def _exact(value, fields, code='spend_funding_policy_invalid'):
    _require(type(value) is dict and set(value) == set(fields), code)


def _integer(value, maximum=_MAX_AMOUNT, *, positive=False, code='spend_funding_policy_invalid'):
    _require(type(value) is int and (1 if positive else 0) <= value <= maximum, code)
    return value


def _hash(value, code='spend_funding_policy_invalid'):
    _require(type(value) is str and _HASH.fullmatch(value) is not None, code)


def _clock(now):
    _require(isinstance(now, datetime) and now.tzinfo is not None
             and now.utcoffset() is not None, 'spend_funding_clock_invalid')
    return now.astimezone(timezone.utc)


def _timestamp(value, code='spend_funding_policy_invalid'):
    _require(type(value) is str and _STAMP.fullmatch(value) is not None, code)
    try:
        return datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except ValueError:
        raise SpendBlocked(code) from None


def _month(now):
    return f'{now.year:04d}-{now.month:02d}'


def _route(value, code='spend_funding_policy_invalid'):
    _require(type(value) is str and len(value) <= 512, code)
    try:
        parsed = urlsplit(value)
        _require(parsed.scheme == 'https' and parsed.hostname is not None
                 and parsed.netloc == parsed.hostname
                 and _HOST.fullmatch(parsed.hostname) is not None
                 and _PATH.fullmatch(parsed.path) is not None
                 and not parsed.query and not parsed.fragment
                 and value == f'https://{parsed.hostname}{parsed.path}', code)
    except ValueError:
        raise SpendBlocked(code) from None


def _digest(policy):
    encoded = json.dumps(policy, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def validate_funding_policy(policy: dict, *, now: datetime) -> dict:
    """Validate exact operator schema and return an independent JSON-safe copy.

    Account credential_sha256 is SHA256(provider + NUL + the actual API key),
    computed by the trusted runtime. account_sha256 identifies the independently
    verified billing account. Neither raw account details nor keys belong here.
    """
    now = _clock(now)
    _exact(policy, {'version', 'currency', 'month', 'valid_from', 'valid_until',
                    'cash_cap_micro', 'opening_cash_micro', 'reconciliation_sha256', 'accounts'})
    _require(type(policy['version']) is int and policy['version'] == 1
             and policy['currency'] == 'USD')
    _require(policy['month'] == _month(now), 'spend_funding_month_mismatch')
    start, end = _timestamp(policy['valid_from']), _timestamp(policy['valid_until'])
    try:
        boundary = datetime(now.year + (now.month == 12), now.month % 12 + 1, 1,
                            tzinfo=timezone.utc)
    except ValueError:
        raise SpendBlocked('spend_funding_clock_invalid') from None
    _require(_month(start) == policy['month'] and start < end <= boundary)
    _require(start <= now < end, 'spend_funding_policy_expired')
    _integer(policy['cash_cap_micro'], OWNER_MAX_NEW_CASH_MICRO)
    # Preserve existing debt even if it already exceeds the newly approved cap.
    _integer(policy['opening_cash_micro'])
    _hash(policy['reconciliation_sha256'])
    accounts = policy['accounts']
    _require(type(accounts) is list and 1 <= len(accounts) <= 32)
    providers, account_hashes, credential_hashes = set(), set(), set()
    for account in accounts:
        _exact(account, {'provider', 'account_sha256', 'credential_sha256', 'evidence_sha256',
                         'valid_until', 'mode', 'routes', 'funding'})
        provider = account['provider']
        _require(type(provider) is str and _PROVIDER.fullmatch(provider) is not None
                 and provider not in providers)
        for field, seen in (('account_sha256', account_hashes),
                            ('credential_sha256', credential_hashes)):
            _hash(account[field])
            _require(account[field] not in seen)
            seen.add(account[field])
        providers.add(provider)
        _hash(account['evidence_sha256'])
        _require(start < _timestamp(account['valid_until']) <= end)
        routes = account['routes']
        _require(type(routes) is list and 1 <= len(routes) <= 64)
        seen_routes = set()
        for route in routes:
            _exact(route, {'route', 'model', 'price_revision'})
            _route(route['route'])
            for name in ('model', 'price_revision'):
                _require(type(route[name]) is str and _NAME.fullmatch(route[name]) is not None)
            identity = (route['route'], route['model'])
            _require(identity not in seen_routes)
            seen_routes.add(identity)
        funding = account['funding']
        if account['mode'] == 'covered_only':
            _exact(funding, {'covered_list_allowance_micro', 'coverage_basis', 'no_auto_overage'})
            _integer(funding['covered_list_allowance_micro'])
            _require(funding['coverage_basis'] == 'verified_route_list_cost_usd'
                     and funding['no_auto_overage'] is True)
        elif account['mode'] == 'cash_only':
            _exact(funding, {'cash_factor_numerator', 'cash_factor_denominator', 'cash_bound_verified'})
            numerator = _integer(funding['cash_factor_numerator'], 1_000_000_000, positive=True)
            denominator = _integer(funding['cash_factor_denominator'], 1_000_000_000, positive=True)
            _require(numerator >= denominator and funding['cash_bound_verified'] is True)
        else:
            raise SpendBlocked('spend_funding_policy_invalid')
    return deepcopy(policy)


def initial_funding_state(policy: dict, *, now: datetime) -> dict:
    """Build a candidate for explicit one-time commissioning; never grants a POST."""
    policy = validate_funding_policy(policy, now=now)
    return {
        'version': 1, 'policy_sha256': _digest(policy), 'month': policy['month'],
        'cash_reserved_micro': policy['opening_cash_micro'],
        'last_reserved_at': _clock(now).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'accounts': {account['account_sha256']: {'covered_reserved_micro': 0, 'cash_reserved_micro': 0}
                     for account in policy['accounts']},
    }


def _validate_state(policy, state, now):
    code = 'spend_funding_state_invalid'
    _exact(state, {'version', 'policy_sha256', 'month', 'cash_reserved_micro',
                   'last_reserved_at', 'accounts'}, code)
    _require(type(state['version']) is int and state['version'] == 1, code)
    _require(state['policy_sha256'] == _digest(policy), 'spend_funding_policy_mismatch')
    _require(state['month'] == policy['month'], 'spend_funding_month_mismatch')
    _require(_timestamp(policy['valid_from']) <= _timestamp(state['last_reserved_at'], code)
             <= now, 'spend_funding_clock_or_state')
    _integer(state['cash_reserved_micro'], code=code)
    _exact(state['accounts'], [account['account_sha256'] for account in policy['accounts']], code)
    cash = policy['opening_cash_micro']
    for account in policy['accounts']:
        balance = state['accounts'][account['account_sha256']]
        _exact(balance, {'covered_reserved_micro', 'cash_reserved_micro'}, code)
        covered = _integer(balance['covered_reserved_micro'], code=code)
        cash_amount = _integer(balance['cash_reserved_micro'], code=code)
        if account['mode'] == 'covered_only':
            _require(cash_amount == 0 and covered <= account['funding']['covered_list_allowance_micro'], code)
        else:
            _require(covered == 0, code)
        cash += cash_amount
    _require(cash == state['cash_reserved_micro'], code)


def funding_summary(policy: dict, state: dict, *, now: datetime) -> dict:
    """Validate the whole snapshot for display without exposing bindings.

    Unlike single-account admission, this complete summary requires every
    account's evidence to remain fresh, preventing a stale global active flag.
    Remaining cash can be negative: prior debt must never be hidden/clamped.
    """
    now = _clock(now)
    policy = validate_funding_policy(policy, now=now)
    _validate_state(policy, state, now)
    providers = []
    for account in policy['accounts']:
        _require(now < _timestamp(account['valid_until']), 'spend_funding_account_expired')
        balance = state['accounts'][account['account_sha256']]
        providers.append({
            'provider': account['provider'], 'mode': account['mode'],
            'covered_allowance_micro': account['funding'].get('covered_list_allowance_micro'),
            'covered_reserved_micro': balance['covered_reserved_micro'],
            'cash_reserved_micro': balance['cash_reserved_micro'],
            'valid_until': account['valid_until'],
        })
    return {
        'currency': 'USD', 'cash_cap_micro': policy['cash_cap_micro'],
        'cash_reserved_micro': state['cash_reserved_micro'],
        'cash_remaining_micro': policy['cash_cap_micro'] - state['cash_reserved_micro'],
        'accounting': 'reserved_cash_upper_bound_not_invoice', 'providers': providers,
    }


def reserve_funding(
    policy: dict,
    state: dict,
    *,
    quote: SpendQuote,
    route: str,
    credential_sha256: str,
    now: datetime,
    account_sha256: str | None = None,
) -> tuple[dict, dict]:
    """Return increased counters and a safe receipt, without mutation or fallback.

    The returned receipt is NOT a transport permit on its own. Existing ledger
    replay checks and atomic commit must succeed before a provider is called.
    A cash_bound_verified factor attests to an account-specific upper bound
    including taxes/surcharges; absent evidence never assumes list price=cash.
    """
    now = _clock(now)
    policy = validate_funding_policy(policy, now=now)
    _validate_state(policy, state, now)
    _require(type(quote) is SpendQuote, 'spend_funding_quote_invalid')
    quote.validate()
    _route(route, 'spend_funding_route_invalid')
    _hash(credential_sha256, 'spend_funding_credential_invalid')
    matches = [account for account in policy['accounts']
               if account['provider'] == quote.provider and account['credential_sha256'] == credential_sha256]
    _require(len(matches) == 1, 'spend_funding_account_mismatch')
    account = matches[0]
    if account_sha256 is not None:
        _hash(account_sha256, 'spend_funding_account_mismatch')
        _require(account['account_sha256'] == account_sha256, 'spend_funding_account_mismatch')
    _require(now < _timestamp(account['valid_until']), 'spend_funding_account_expired')
    binding = {'route': route, 'model': quote.model, 'price_revision': quote.price_revision}
    _require(binding in account['routes'], 'spend_funding_route_not_covered')
    updated = deepcopy(state)
    balance = updated['accounts'][account['account_sha256']]
    covered_micro, cash_micro = 0, 0
    if account['mode'] == 'covered_only':
        covered_micro = quote.maximum_micro
        remaining = account['funding']['covered_list_allowance_micro'] - balance['covered_reserved_micro']
        _require(covered_micro <= remaining, 'spend_funding_covered_limit')
        balance['covered_reserved_micro'] += covered_micro
    else:
        funding = account['funding']
        numerator, denominator = funding['cash_factor_numerator'], funding['cash_factor_denominator']
        cash_micro = (quote.maximum_micro * numerator + denominator - 1) // denominator
        _require(cash_micro >= quote.maximum_micro, 'spend_funding_quote_invalid')
        _require(updated['cash_reserved_micro'] + cash_micro <= policy['cash_cap_micro'],
                 'spend_funding_cash_limit')
        balance['cash_reserved_micro'] += cash_micro
        updated['cash_reserved_micro'] += cash_micro
    updated['last_reserved_at'] = now.strftime('%Y-%m-%dT%H:%M:%SZ')
    receipt = {
        'state': 'reserved_before_request', 'policy_sha256': updated['policy_sha256'],
        'month': policy['month'], 'account_sha256': account['account_sha256'],
        'evidence_sha256': account['evidence_sha256'], 'provider': quote.provider,
        'model': quote.model, 'route': route, 'price_revision': quote.price_revision,
        'mode': account['mode'], 'list_maximum_micro': quote.maximum_micro,
        'covered_micro': covered_micro, 'cash_micro': cash_micro,
    }
    return updated, receipt
