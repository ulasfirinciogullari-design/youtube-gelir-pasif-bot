"""A native-credit foundation that never invents a USD opening balance.

The owner can authorize use of already paid subscription credits while prior
cash invoices remain unknown. This permanent foundation admits no cash-funded
request, does not reconcile history, and cannot be converted by auto-init.
"""
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import re

from app.services.production_spend import LEDGER_KEY, SpendBlocked, _identifier, _integer, _json, _object, _period

ANCHOR_KEY = 'youtube_studio:{production_spend}:cash_disabled:v1:foundation'
FIELD = 'cash_disabled_foundation'
_FIELDS = {'policy', FIELD}
_NATIVE_MODE = 'native_credit_policy:elevenlabs'
_INCLUDED_MODE = 'included_router_policy'
_PREPAID_AUDIO_MODE = 'prepaid_audio_policy'


def _require(value, code='cash_disabled_foundation_invalid'):
    if not value:
        raise SpendBlocked(code)


def present(reader):
    return bool(reader.exists(ANCHOR_KEY) or reader.hexists(LEDGER_KEY, FIELD))


def _zero(policy):
    values = asdict(policy)
    _require(all(type(value) is int and value == 0 for value in values.values()),
             'cash_disabled_zero_policy_required')
    return values


def read(pipe, foundation, *, now):
    """Watched read shared by native ledgers; it grants no send authority."""
    _period(now)
    expected_policy = _zero(foundation.policy)
    pipe.watch(LEDGER_KEY, ANCHOR_KEY)
    _require(all(type(ttl) is int and ttl == -1 for ttl in
                 (pipe.pttl(LEDGER_KEY), pipe.pttl(ANCHOR_KEY))), 'cash_disabled_foundation_missing')
    # One watched snapshot avoids a round trip for every historical binding.
    # Validation remains complete and EXEC still detects any intervening write.
    fields = pipe.hgetall(LEDGER_KEY)
    _require(type(fields) is dict and len(fields) <= 8200 and _FIELDS <= set(fields))
    stored = _object(fields.get(FIELD))
    _require(fields.get(FIELD) == _json(stored))
    _require(set(stored) == {'version', 'kind', 'initialized_at', 'evidence_sha256',
        'additional_monthly_limit_micro', 'historical_cash_micro', 'new_cash_allowance_micro'})
    _require(type(stored['version']) is int and stored['version'] == 1
        and stored['kind'] == 'cash_disabled_unknown_history'
        and stored['historical_cash_micro'] is None
        and type(stored['new_cash_allowance_micro']) is int and stored['new_cash_allowance_micro'] == 0
        and type(stored['evidence_sha256']) is str
        and re.fullmatch('[0-9a-f]{64}', stored['evidence_sha256']) is not None)
    _integer(stored['additional_monthly_limit_micro'])
    try:
        stamp = datetime.strptime(stored['initialized_at'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        raise SpendBlocked('cash_disabled_foundation_invalid') from None
    _require(stamp <= now)
    stored_policy = _object(fields.get('policy'))
    _require(stored_policy == expected_policy and all(type(v) is int for v in stored_policy.values()))
    _require(fields.get('policy') == _json(stored_policy))
    anchor = {'version': 1, 'foundation_sha256': hashlib.sha256(_json(stored).encode()).hexdigest(),
              'policy_sha256': hashlib.sha256(_json(expected_policy).encode()).hexdigest()}
    _require(pipe.get(ANCHOR_KEY) == _json(anchor), 'cash_disabled_foundation_mismatch')
    # USD periods, funding and receipts cannot coexist with unknown history.
    # Native receipt correspondence is independently checked by CreditLedger.
    for key, value in fields.items():
        if type(key) is str and key.startswith('binding:'):
            _identifier(key[len('binding:'):])
            binding = _object(value)
            _require(set(binding) == {'channel_id', 'lineage_id', 'connection_id', 'kind'}
                and binding['kind'] in {'shorts', 'long', 'derived'})
            for name in ('channel_id', 'lineage_id', 'connection_id'):
                _identifier(binding[name])
            continue
        _require(key in _FIELDS or key in (_NATIVE_MODE, _INCLUDED_MODE, _PREPAID_AUDIO_MODE, 'native_credit_period_history',
            'narrator_rotation_grant:v1')
            or type(key) is str and re.fullmatch(r'native_request:[0-9a-f]{64}', key) is not None,
            'cash_disabled_conflicting_accounting')
    return stored


def initialize(foundation, *, evidence_sha256, additional_monthly_limit_micro):
    """Explicit one-time commissioning; not called by a request or scheduler."""
    policy = _zero(foundation.policy)
    _require(type(evidence_sha256) is str and re.fullmatch('[0-9a-f]{64}', evidence_sha256) is not None)
    _integer(additional_monthly_limit_micro)
    now = foundation.clock()
    _period(now)
    record = {'version': 1, 'kind': 'cash_disabled_unknown_history',
        'initialized_at': now.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'evidence_sha256': evidence_sha256, 'additional_monthly_limit_micro': additional_monthly_limit_micro,
        'historical_cash_micro': None, 'new_cash_allowance_micro': 0}
    from app.services.production_credit_ledger import STATE_KEY, JOURNAL_KEY
    try:
        with foundation.client.pipeline() as pipe:
            pipe.watch(LEDGER_KEY, ANCHOR_KEY, STATE_KEY, JOURNAL_KEY)
            if pipe.exists(LEDGER_KEY, ANCHOR_KEY):
                prior = read(pipe, foundation, now=now)
                _require(prior['evidence_sha256'] == evidence_sha256
                    and prior['additional_monthly_limit_micro'] == additional_monthly_limit_micro,
                    'cash_disabled_opening_mismatch')
                pipe.multi(); pipe.ping()
                ack = pipe.execute()
                _require(type(ack) is list and len(ack) == 1 and ack[0] is True,
                         'cash_disabled_ack_uncertain')
                return False
            _require(pipe.exists(STATE_KEY, JOURNAL_KEY) == 0, 'cash_disabled_native_history_present')
            anchor = {'version': 1, 'foundation_sha256': hashlib.sha256(_json(record).encode()).hexdigest(),
                'policy_sha256': hashlib.sha256(_json(policy).encode()).hexdigest()}
            pipe.multi()
            pipe.hset(LEDGER_KEY, mapping={'policy': _json(policy), FIELD: _json(record)})
            pipe.set(ANCHOR_KEY, _json(anchor), nx=True)
            ack = pipe.execute()
            _require(type(ack) is list and len(ack) == 2 and type(ack[0]) is int
                     and ack[0] == 2 and ack[1] is True, 'cash_disabled_ack_uncertain')
            return True
    except SpendBlocked:
        raise
    except Exception:
        # No retry after an uncertain initialization or lost acknowledgement.
        raise SpendBlocked('cash_disabled_initialization_unverified') from None


def summary(record):
    return {'mode': record['kind'], 'historical_cash_micro': None,
        'additional_monthly_limit_micro': record['additional_monthly_limit_micro'],
        'new_cash_allowance_micro': 0, 'cash_spending_enabled': False,
        'accounting': 'native_subscriptions_separate_from_unknown_cash_history'}
