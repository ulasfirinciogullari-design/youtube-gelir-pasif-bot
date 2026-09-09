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

    def initialize_funding(self, policy):
        """Commission reconciled net coverage and cash once, without a POST.

        Equal re-entry validates existing use; it never resets counters. No
        request dispatcher or API endpoint invokes this operator-only method.
        """
        from app.services.production_funding import (
            funding_summary, initial_funding_state, validate_funding_policy,
        )
        policy = validate_funding_policy(policy, now=self.clock())
        initial = initial_funding_state(policy, now=self.clock())
        for _ in range(12):
            month, day = _period(self.clock())
            try:
                with self.client.pipeline() as pipe:
                    pipe.watch(LEDGER_KEY)
                    self._read_state(pipe, month, day)
                    validate_funding_policy(policy, now=self.clock())
                    raw_policy = pipe.hget(LEDGER_KEY, 'funding_policy')
                    raw_state = pipe.hget(LEDGER_KEY, 'funding_state')
                    if raw_policy is not None:
                        prior = _object(raw_policy)
                        if prior != policy:
                            raise SpendBlocked('spend_funding_policy_mismatch')
                        funding_summary(prior, _object(raw_state), now=self.clock())
                        pipe.multi()
                        pipe.ping()
                        result = pipe.execute()
                        if len(result) != 1 or result[0] is not True:
                            raise SpendBlocked('spend_funding_initialization_uncertain')
                        return False
                    if raw_state is not None:
                        raise SpendBlocked('spend_funding_state_invalid')
                    # Losing both summary fields must not reopen already used
                    # cash/coverage while durable funded request receipts remain.
                    cursor, scanned = 0, 0
                    for _page in range(128):
                        cursor, entries = pipe.hscan(
                            LEDGER_KEY, cursor=cursor, match='request:*', count=256)
                        scanned += len(entries)
                        if scanned > 10_000:
                            raise SpendBlocked('spend_funding_history_limit')
                        for raw in entries.values():
                            receipt = _object(raw)
                            if 'funding' in receipt:
                                raise SpendBlocked('spend_funding_initialization_late')
                            # Only a recognizable legacy reservation can be
                            # reconciled by the operator's opening balance.
                            # An arbitrary JSON object is unknown history.
                            try:
                                required = {'version', 'month', 'day', 'channel_id',
                                            'lineage_id', 'kind', 'quote', 'state'}
                                if (set(receipt) - (required | {'scene'})
                                        or not required.issubset(receipt)
                                        or type(receipt['version']) is not int
                                        or receipt['version'] != 1
                                        or receipt['kind'] not in _KINDS
                                        or receipt['state'] not in {
                                            'reserved_before_request', 'opening_reserved_upper_bound'}):
                                    raise ValueError
                                _identifier(receipt['channel_id'])
                                _identifier(receipt['lineage_id'])
                                parsed_day = datetime.strptime(receipt['day'], '%Y-%m-%d')
                                if (parsed_day.strftime('%Y-%m-%d') != receipt['day']
                                        or parsed_day.strftime('%Y-%m') != receipt['month']
                                        or receipt['day'] > day
                                        or type(receipt['quote']) is not dict):
                                    raise ValueError
                                SpendQuote(**receipt['quote']).validate()
                            except (ValueError, TypeError, SpendBlocked):
                                raise SpendBlocked('spend_state_invalid') from None
                        if cursor == 0:
                            break
                    else:
                        raise SpendBlocked('spend_funding_history_limit')
                    funding_summary(policy, initial, now=self.clock())
                    pipe.multi()
                    pipe.hset(LEDGER_KEY, mapping={
                        'funding_policy': _json(policy), 'funding_state': _json(initial),
                    })
                    result = pipe.execute()
                    if len(result) != 1 or type(result[0]) is not int or result[0] != 2:
                        raise SpendBlocked('spend_funding_initialization_uncertain')
                    return True
            except WatchError:
                continue
            except SpendBlocked:
                raise
            except Exception:
                raise SpendBlocked('spend_store_unavailable') from None
        raise SpendBlocked('spend_store_contention')

    def funding_snapshot(self):
        from app.services.production_funding import funding_summary
        for _ in range(12):
            try:
                with self.client.pipeline() as pipe:
                    pipe.watch(LEDGER_KEY)
                    month, day = _period(self.clock())
                    self._read_state(pipe, month, day)
                    raw_policy = pipe.hget(LEDGER_KEY, 'funding_policy')
                    raw_state = pipe.hget(LEDGER_KEY, 'funding_state')
                    if raw_policy is None or raw_state is None:
                        raise SpendBlocked('spend_funding_not_initialized')
                    summary = funding_summary(_object(raw_policy), _object(raw_state), now=self.clock())
                    pipe.multi()
                    pipe.ping()
                    result = pipe.execute()
                    if len(result) != 1 or result[0] is not True:
                        raise SpendBlocked('spend_funding_snapshot_uncertain')
                    return summary
            except WatchError:
                continue
            except SpendBlocked:
                raise
            except Exception:
                raise SpendBlocked('spend_store_unavailable') from None
        raise SpendBlocked('spend_store_contention')

    def initialize_scene_plan(self, *, channel_id, lineage_id, kind, connection_id,
                              package_sha256, scenes):
        """Freeze verified server allocations before this family's first video.

        Text/audio planning may already have consumed family funds. Historical
        video intents, including opening imports, cannot receive a fresh scene
        allowance. Equal re-entry only validates; it never resets scene usage.
        This is an explicit planning action, never a reserve() fallback.
        """
        from app.services.production_scene_budget import (
            initial_scene_usage, make_scene_plan, scene_fields,
            validate_scene_state, video_quote,
        )
        plan = make_scene_plan(channel_id=channel_id, lineage_id=lineage_id,
                               kind=kind, connection_id=connection_id,
                               package_sha256=package_sha256, scenes=scenes)
        plan_field, usage_field = scene_fields(lineage_id)
        lineage_field = 'lineage:' + hashlib.sha256(lineage_id.encode()).hexdigest()
        for _attempt in range(12):
            month, day = _period(self.clock())
            try:
                with self.client.pipeline() as pipe:
                    pipe.watch(LEDGER_KEY)
                    self._read_state(pipe, month, day)
                    raw_lineage = pipe.hget(LEDGER_KEY, lineage_field)
                    lineage = (_object(raw_lineage) if raw_lineage is not None else
                               {'channel_id': channel_id, 'kind': kind, 'used_micro': 0})
                    if (set(lineage) != {'channel_id', 'kind', 'used_micro'}
                            or lineage['channel_id'] != channel_id or lineage['kind'] != kind):
                        raise SpendBlocked('spend_lineage_binding_invalid')
                    _integer(lineage['used_micro'])
                    raw_plan = pipe.hget(LEDGER_KEY, plan_field)
                    raw_usage = pipe.hget(LEDGER_KEY, usage_field)
                    if raw_plan is not None:
                        prior, usage = _object(raw_plan), _object(raw_usage)
                        validate_scene_state(prior, usage, channel_id=channel_id,
                                             lineage_id=lineage_id, kind=kind)
                        if prior != plan:
                            raise SpendBlocked('spend_scene_plan_mismatch')
                        if sum(usage['used_micro'].values()) > lineage['used_micro']:
                            raise SpendBlocked('spend_scene_state_invalid')
                        # Even read-only equality must observe one committed
                        # version; a concurrent mutation retries validation.
                        pipe.multi()
                        pipe.ping()
                        result = pipe.execute()
                        if len(result) != 1 or result[0] is not True:
                            raise SpendBlocked('spend_scene_initialization_uncertain')
                        return False
                    if raw_usage is not None:
                        raise SpendBlocked('spend_scene_state_invalid')
                    # The old ledger has no per-family video index. Bound its
                    # one-time historical scan; never assume an unscanned tail
                    # is empty or load the whole durable ledger with HGETALL.
                    cursor, scanned = 0, 0
                    for _page in range(128):
                        cursor, entries = pipe.hscan(
                            LEDGER_KEY, cursor=cursor, match='request:*', count=256)
                        scanned += len(entries)
                        if scanned > 10_000:
                            raise SpendBlocked('spend_scene_history_limit')
                        for raw_receipt in entries.values():
                            receipt = _object(raw_receipt)
                            _identifier(receipt.get('lineage_id'))
                            if receipt['lineage_id'] != lineage_id:
                                continue
                            raw_quote = receipt.get('quote')
                            if (type(raw_quote) is not dict or set(raw_quote) != {
                                    'provider', 'model', 'maximum_micro', 'price_revision'}):
                                raise SpendBlocked('spend_state_invalid')
                            if video_quote(SpendQuote(**raw_quote)):
                                raise SpendBlocked('spend_scene_plan_late')
                        if cursor == 0:
                            break
                    else:
                        raise SpendBlocked('spend_scene_history_limit')
                    pipe.multi()
                    pipe.hset(LEDGER_KEY, mapping={
                        plan_field: _json(plan), usage_field: _json(initial_scene_usage(plan)),
                    })
                    result = pipe.execute()
                    if len(result) != 1 or type(result[0]) is not int or result[0] != 2:
                        raise SpendBlocked('spend_scene_initialization_uncertain')
                    return True
            except WatchError:
                continue
            except SpendBlocked:
                raise
            except Exception:
                raise SpendBlocked('spend_store_unavailable') from None
        raise SpendBlocked('spend_store_contention')

    def reserve(self, *, request_key, channel_id, lineage_id, kind, quote: SpendQuote,
                scene=None, funding=None):
        """Return one permit only after the durable transaction is acknowledged.

        Caller identities must come from verified server-side job bindings,
        not an arbitrary request body. The persisted request_key identifies
        ONE paid submission, including SDK retries and alternate providers.
        An optional scene admission binds the actual video shape to a frozen
        plan. Once a family has a plan, video cannot omit its scene admission.
        """
        for value in (request_key, channel_id, lineage_id):
            _identifier(value)
        if type(kind) is not str or kind not in _KINDS:
            raise SpendBlocked('spend_kind_invalid')
        quote.validate()
        amount = quote.maximum_micro
        operation_field = 'request:' + hashlib.sha256(request_key.encode()).hexdigest()
        lineage_field = 'lineage:' + hashlib.sha256(lineage_id.encode()).hexdigest()
        from app.services.production_scene_budget import admit_scene, scene_fields, video_quote
        plan_field, usage_field = scene_fields(lineage_id)
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
                    funding_mapping, funding_receipt = {}, None
                    if funding is not None:
                        from app.services.production_funding import reserve_funding
                        if (type(funding) is not dict or set(funding) not in (
                                {'route', 'credential_sha256'},
                                {'route', 'credential_sha256', 'account_sha256'})
                                or ('account_sha256' in funding and type(funding['account_sha256']) is not str)):
                            raise SpendBlocked('spend_funding_context_invalid')
                        raw_policy = pipe.hget(LEDGER_KEY, 'funding_policy')
                        raw_state = pipe.hget(LEDGER_KEY, 'funding_state')
                        if raw_policy is None or raw_state is None:
                            raise SpendBlocked('spend_funding_not_initialized')
                        funding_state, funding_receipt = reserve_funding(
                            _object(raw_policy), _object(raw_state), quote=quote,
                            route=funding['route'], credential_sha256=funding['credential_sha256'],
                            now=self.clock(),
                            account_sha256=funding.get('account_sha256'),
                        )
                        funding_mapping['funding_state'] = _json(funding_state)
                    elif (pipe.hexists(LEDGER_KEY, 'funding_policy')
                          or pipe.hexists(LEDGER_KEY, 'funding_state')):
                        raise SpendBlocked('spend_funding_context_missing')
                    scene_mapping, scene_receipt = {}, None
                    if scene is not None:
                        raw_plan = pipe.hget(LEDGER_KEY, plan_field)
                        raw_usage = pipe.hget(LEDGER_KEY, usage_field)
                        if raw_plan is None or raw_usage is None:
                            raise SpendBlocked('spend_scene_plan_missing')
                        usage, scene_receipt = admit_scene(
                            _object(raw_plan), _object(raw_usage), scene, quote,
                            channel_id=channel_id, lineage_id=lineage_id, kind=kind,
                            lineage_used_micro=lineage['used_micro'],
                        )
                        scene_mapping[usage_field] = _json(usage)
                    elif video_quote(quote) and (
                            pipe.hexists(LEDGER_KEY, plan_field)
                            or pipe.hexists(LEDGER_KEY, usage_field)):
                        raise SpendBlocked('spend_scene_context_missing')
                    period['used_micro'] += amount
                    period['days'][day] = period['days'].get(day, 0) + amount
                    period['channels'][channel_id] = period['channels'].get(channel_id, 0) + amount
                    lineage['used_micro'] += amount
                    receipt = {
                        'version': 1, 'month': month, 'day': day,
                        'channel_id': channel_id, 'lineage_id': lineage_id, 'kind': kind,
                        'quote': asdict(quote), 'state': 'reserved_before_request',
                    }
                    if scene_receipt is not None:
                        receipt['scene'] = scene_receipt
                    if funding_receipt is not None:
                        receipt['funding'] = funding_receipt
                    mapping = {
                        'active_month': month, 'last_day': day,
                        'period:' + month: _json(period), lineage_field: _json(lineage),
                        operation_field: _json(receipt), **scene_mapping, **funding_mapping,
                    }
                    pipe.multi()
                    pipe.hset(LEDGER_KEY, mapping=mapping)
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
