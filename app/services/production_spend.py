"""Provider-neutral, fail-closed pre-request spending reservations.

This is a foundation, not an installed HTTP interceptor. A caller may issue
ONE bounded paid request only after reserve() returns successfully. Retrying
the same reservation never grants a second permit, including after a lost
Redis reply. Reservations count their full upper bound, NOT actual invoices.

No automatic bootstrap, expiry, refund or provider-network operation exists.
Use the same lineage for repairs and derived outputs across queue task IDs.
Integration must enforce the quoted output/token limits at the provider.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import hashlib
import json
import re

from redis.exceptions import WatchError


LEDGER_KEY = 'youtube_studio:{production_spend}:v1'
_ID = re.compile(r'^[A-Za-z0-9_-]{8,128}$')
_KINDS = {'shorts', 'long', 'derived'}
_MAX_MICRO = 10_000_000_000


class SpendBlocked(RuntimeError):
    """Fixed non-secret failure code; no paid request is authorized."""


def usd_micro(value: str | Decimal) -> int:
    """Round a positive dollar upper bound UP; floats/bools are not money."""
    if not isinstance(value, (str, Decimal)):
        raise SpendBlocked('spend_amount_invalid')
    try:
        number = Decimal(value)
        if not number.is_finite() or number <= 0 or number > 10_000:
            raise ValueError
        return int((number * 1_000_000).to_integral_value(rounding=ROUND_CEILING))
    except (ValueError, InvalidOperation):
        raise SpendBlocked('spend_amount_invalid') from None


def _integer(value, *, positive=False):
    if type(value) is not int or not (1 if positive else 0) <= value <= _MAX_MICRO:
        raise SpendBlocked('spend_state_invalid')
    return value


def _identifier(value):
    if type(value) is not str or not _ID.fullmatch(value):
        raise SpendBlocked('spend_identity_invalid')
    return value


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _object(raw):
    try:
        value = json.loads(raw)
        if type(value) is not dict:
            raise ValueError
        return value
    except (ValueError, TypeError):
        raise SpendBlocked('spend_state_invalid') from None


@dataclass(frozen=True)
class SpendPolicy:
    """API allowance only: fixed bills/taxes/reserve stay outside this cap."""

    monthly_micro: int
    daily_micro: int
    channel_monthly_micro: int
    shorts_micro: int
    long_micro: int
    derived_micro: int

    def validate(self):
        for value in asdict(self).values():
            _integer(value)  # An all-zero policy explicitly disables paid work.
        if self.daily_micro > self.monthly_micro or self.channel_monthly_micro > self.monthly_micro:
            raise SpendBlocked('spend_policy_invalid')
        if any(getattr(self, kind + '_micro') > self.monthly_micro for kind in _KINDS):
            raise SpendBlocked('spend_policy_invalid')
        return self


@dataclass(frozen=True)
class SpendQuote:
    provider: str
    model: str
    maximum_micro: int
    price_revision: str

    def validate(self):
        for value in (self.provider, self.model, self.price_revision):
            if type(value) is not str or not re.fullmatch(r'[A-Za-z0-9._/-]{1,100}', value):
                raise SpendBlocked('spend_quote_invalid')
        _integer(self.maximum_micro, positive=True)
        return self


@dataclass(frozen=True)
class OpeningReservation:
    """An audited pre-existing paid intent, including uncertain/in-flight work.

    request_key and lineage_id must match the production dispatch identities.
    Import the full reserved upper bound; this is not a refund/settlement API.
    """

    day: str
    request_key: str
    channel_id: str
    lineage_id: str
    kind: str
    quote: SpendQuote


def _period(now):
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise SpendBlocked('spend_clock_invalid')
    utc = now.astimezone(timezone.utc)
    return utc.strftime('%Y-%m'), utc.strftime('%Y-%m-%d')


def _fresh_period(month):
    return {'month': month, 'used_micro': 0, 'days': {}, 'channels': {}}


class SpendLedger:
    """One persistent Redis hash; WATCH makes all counters and receipt atomic.

    Redis must be durable/no-eviction. Missing policy/active state blocks;
    initialization is a separate operator action, never a request fallback.
    This key is intentionally not included in expiring Studio job records.
    """

    def __init__(self, client, policy: SpendPolicy, *, clock=None):
        self.client = client
        self.policy = policy.validate()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def initialize(self):
        """Explicit empty start for a period with no pre-existing API usage.

        For a partially used month, use initialize_reconciled instead. Neither
        method may run as a missing-ledger fallback in a production request.
        Existing matching state is only read/validated.
        """
        month, day = _period(self.clock())
        return self._initialize(month, day, {'period:' + month: _json(_fresh_period(month))})

    def initialize_reconciled(self, *, month, reservations, reconciliation_sha256):
        """Explicit one-time import, with no provider calls or spending permits.

        The operator must reconcile ALL current-month billed/in-flight intents
        before activation. Amounts may exceed policy limits: preserve the debt
        and block subsequent dispatch, rather than discard it to fit a budget.
        No existing ledger is overwritten, including after a lost reply.
        """
        current_month, day = _period(self.clock())
        if (month != current_month or type(month) is not str
                or type(reconciliation_sha256) is not str
                or not re.fullmatch(r'[0-9a-f]{64}', reconciliation_sha256)
                or type(reservations) is not list or len(reservations) > 10000):
            raise SpendBlocked('spend_opening_invalid')
        period, mapping, lineages = _fresh_period(month), {}, {}
        for entry in reservations:
            if type(entry) is not OpeningReservation or type(entry.quote) is not SpendQuote:
                raise SpendBlocked('spend_opening_invalid')
            try:
                parsed_day = datetime.strptime(entry.day, '%Y-%m-%d').date().isoformat()
            except (TypeError, ValueError):
                raise SpendBlocked('spend_opening_invalid') from None
            if parsed_day != entry.day or not entry.day.startswith(month + '-') or entry.day > day:
                raise SpendBlocked('spend_opening_invalid')
            for value in (entry.request_key, entry.channel_id, entry.lineage_id):
                _identifier(value)
            if type(entry.kind) is not str or entry.kind not in _KINDS:
                raise SpendBlocked('spend_kind_invalid')
            entry.quote.validate()
            amount = entry.quote.maximum_micro
            request_field = 'request:' + hashlib.sha256(entry.request_key.encode()).hexdigest()
            if request_field in mapping:
                raise SpendBlocked('spend_opening_duplicate_request')
            lineage = lineages.setdefault(entry.lineage_id, {
                'channel_id': entry.channel_id, 'kind': entry.kind, 'used_micro': 0})
            if lineage['channel_id'] != entry.channel_id or lineage['kind'] != entry.kind:
                raise SpendBlocked('spend_lineage_binding_invalid')
            lineage['used_micro'] += amount
            period['used_micro'] += amount
            _integer(period['used_micro'])
            period['days'][entry.day] = period['days'].get(entry.day, 0) + amount
            period['channels'][entry.channel_id] = period['channels'].get(entry.channel_id, 0) + amount
            mapping[request_field] = _json({
                'version': 1, 'month': month, 'day': entry.day,
                'channel_id': entry.channel_id, 'lineage_id': entry.lineage_id,
                'kind': entry.kind, 'quote': asdict(entry.quote),
                'state': 'opening_reserved_upper_bound',
            })
        mapping.update({'lineage:' + hashlib.sha256(key.encode()).hexdigest(): _json(value)
                        for key, value in lineages.items()})
        mapping['period:' + month] = _json(period)
        mapping['opening_reconciliation'] = _json({
            'version': 1, 'month': month, 'reserved_micro': period['used_micro'],
            'request_count': len(reservations), 'reconciliation_sha256': reconciliation_sha256,
            'opening_sha256': hashlib.sha256(_json(mapping).encode()).hexdigest(),
        })
        return self._initialize(month, day, mapping)

    def _initialize(self, month, day, opening):
        policy_raw = _json(asdict(self.policy))
        try:
            with self.client.pipeline() as pipe:
                pipe.watch(LEDGER_KEY)
                if pipe.exists(LEDGER_KEY):
                    self._read_state(pipe, month, day)
                    if ('opening_reconciliation' in opening and
                            pipe.hget(LEDGER_KEY, 'opening_reconciliation') != opening['opening_reconciliation']):
                        raise SpendBlocked('spend_opening_mismatch')
                    return False
                mapping = {**opening, 'policy': policy_raw, 'active_month': month, 'last_day': day}
                pipe.multi()
                pipe.hset(LEDGER_KEY, mapping=mapping)
                result = pipe.execute()
                if len(result) != 1 or type(result[0]) is not int or result[0] != len(mapping):
                    raise SpendBlocked('spend_initialization_uncertain')
                return True
        except SpendBlocked:
            raise
        except Exception:
            raise SpendBlocked('spend_store_unavailable') from None

    def _read_state(self, reader, month, day):
        policy = reader.hget(LEDGER_KEY, 'policy')
        if policy is None:
            raise SpendBlocked('spend_not_initialized')
        stored_policy = _object(policy)
        if (set(stored_policy) != set(asdict(self.policy))
                or any(type(value) is not int for value in stored_policy.values())
                or stored_policy != asdict(self.policy)):
            raise SpendBlocked('spend_policy_mismatch')
        active = reader.hget(LEDGER_KEY, 'active_month')
        last_day = reader.hget(LEDGER_KEY, 'last_day')
        if (type(active) is not str or not re.fullmatch(r'\d{4}-\d{2}', active)
                or type(last_day) is not str or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', last_day)
                or not last_day.startswith(active + '-') or month < active or day < last_day):
            raise SpendBlocked('spend_clock_or_state_invalid')
        current = _object(reader.hget(LEDGER_KEY, 'period:' + active))
        if set(current) != {'month', 'used_micro', 'days', 'channels'} or current['month'] != active:
            raise SpendBlocked('spend_state_invalid')
        _integer(current['used_micro'])
        for name in ('days', 'channels'):
            if type(current[name]) is not dict:
                raise SpendBlocked('spend_state_invalid')
            for value in current[name].values():
                _integer(value)
            if sum(current[name].values()) != current['used_micro']:
                raise SpendBlocked('spend_state_invalid')
        if month == active:
            return current
        # A future period is new; historical receipts/lineage totals never reset.
        if reader.hget(LEDGER_KEY, 'period:' + month) is not None:
            raise SpendBlocked('spend_state_invalid')
        return _fresh_period(month)

    def reserve(self, *, request_key, channel_id, lineage_id, kind, quote: SpendQuote):
        """Return one permit only after the durable transaction is acknowledged.

        Caller identities must come from verified server-side job bindings,
        not an arbitrary request body. The persisted request_key identifies
        ONE paid submission, including SDK retries and alternate providers.
        """
        for value in (request_key, channel_id, lineage_id):
            _identifier(value)
        if type(kind) is not str or kind not in _KINDS:
            raise SpendBlocked('spend_kind_invalid')
        quote.validate()
        amount = quote.maximum_micro
        operation_field = 'request:' + hashlib.sha256(request_key.encode()).hexdigest()
        lineage_field = 'lineage:' + hashlib.sha256(lineage_id.encode()).hexdigest()
        for _attempt in range(12):
            month, day = _period(self.clock())
            try:
                with self.client.pipeline() as pipe:
                    pipe.watch(LEDGER_KEY)
                    period = self._read_state(pipe, month, day)
                    if pipe.hexists(LEDGER_KEY, operation_field):
                        raise SpendBlocked('spend_request_already_reserved')
                    raw_lineage = pipe.hget(LEDGER_KEY, lineage_field)
                    lineage = (_object(raw_lineage) if raw_lineage is not None else
                               {'channel_id': channel_id, 'kind': kind, 'used_micro': 0})
                    if (set(lineage) != {'channel_id', 'kind', 'used_micro'}
                            or lineage['channel_id'] != channel_id or lineage['kind'] != kind):
                        raise SpendBlocked('spend_lineage_binding_invalid')
                    _integer(lineage['used_micro'])
                    checks = (
                        (period['used_micro'], self.policy.monthly_micro, 'month'),
                        (period['days'].get(day, 0), self.policy.daily_micro, 'day'),
                        (period['channels'].get(channel_id, 0), self.policy.channel_monthly_micro, 'channel'),
                        (lineage['used_micro'], getattr(self.policy, kind + '_micro'), 'lineage'),
                    )
                    for used, limit, scope in checks:
                        if used + amount > limit:
                            raise SpendBlocked('spend_' + scope + '_limit')
                    period['used_micro'] += amount
                    period['days'][day] = period['days'].get(day, 0) + amount
                    period['channels'][channel_id] = period['channels'].get(channel_id, 0) + amount
                    lineage['used_micro'] += amount
                    receipt = {
                        'version': 1, 'month': month, 'day': day,
                        'channel_id': channel_id, 'lineage_id': lineage_id, 'kind': kind,
                        'quote': asdict(quote), 'state': 'reserved_before_request',
                    }
                    pipe.multi()
                    pipe.hset(LEDGER_KEY, mapping={
                        'active_month': month, 'last_day': day,
                        'period:' + month: _json(period), lineage_field: _json(lineage),
                        operation_field: _json(receipt),
                    })
                    result = pipe.execute()
                    if len(result) != 1 or type(result[0]) is not int or result[0] < 1:
                        raise SpendBlocked('spend_reservation_uncertain')
                    return receipt
            except WatchError:
                continue  # No provider call has been authorized or attempted.
            except SpendBlocked:
                raise
            except Exception:
                raise SpendBlocked('spend_store_unavailable') from None
        raise SpendBlocked('spend_store_contention')

    def snapshot(self):
        month, day = _period(self.clock())
        try:
            with self.client.pipeline() as pipe:
                pipe.watch(LEDGER_KEY)
                period = self._read_state(pipe, month, day)
                pipe.multi()
                pipe.ping()
                pipe.execute()
                return {'policy': asdict(self.policy), 'period': period,
                        'remaining_micro': self.policy.monthly_micro - period['used_micro'],
                        'accounting': 'reserved_upper_bound_not_invoice'}
        except SpendBlocked:
            raise
        except Exception:
            raise SpendBlocked('spend_store_unavailable') from None
