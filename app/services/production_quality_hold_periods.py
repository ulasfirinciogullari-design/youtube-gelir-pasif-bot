"""Continue unpublished holds only inside verified renewed funding windows.

The original hold policy and every episode/day receipt stay unchanged. These
windows permit schedule maintenance, never content approval or publication.
"""
from datetime import timedelta

from app.services import production_quality_holds as holds
from app.services import production_credit_ledger as voice
from app.services import production_credit_periods as periods
from app.services.production_credit_funding import validate_credit_policy

WINDOWS_KEY = holds.PREFIX + 'funding_windows'
ANCHOR_KEY = holds.PREFIX + 'funding_windows_anchor'
WITNESS_KEY = holds.PREFIX + 'funding_windows_started'


def _native_policies(pipe):
    pipe.watch(voice.STATE_KEY, periods.HISTORY_KEY, periods.HISTORY_ANCHOR, holds.runtime.LEDGER_KEY)
    current = pipe.hgetall(voice.STATE_KEY)
    history_raw, anchor = pipe.get(periods.HISTORY_KEY), pipe.get(periods.HISTORY_ANCHOR)
    holds._require(type(history_raw) is str and pipe.pttl(periods.HISTORY_KEY) == -1
        and pipe.pttl(periods.HISTORY_ANCHOR) == pipe.pttl(voice.STATE_KEY) == -1)
    history = voice._object(history_raw)
    holds._require(voice._hash(history) == anchor
        and pipe.hgetall(holds.runtime.LEDGER_KEY).get(periods.HISTORY_FIELD) == anchor
        and type(history.get('periods')) is list and 1 <= len(history['periods']) <= periods.MAX_PERIODS)
    policies = [voice._object(current['policy']), *(row['policy'] for row in history['periods'])]
    for policy in policies:
        validate_credit_policy(policy, now=holds._date(policy['valid_from']))
    return {voice._hash(policy): policy for policy in policies}


def read_windows(pipe, policy, now):
    pipe.watch(WINDOWS_KEY, ANCHOR_KEY, WITNESS_KEY)
    raw, anchor, witness = (pipe.get(key) for key in (WINDOWS_KEY, ANCHOR_KEY, WITNESS_KEY))
    if raw is None and anchor is None and witness is None:
        return {'version': 1, 'policy_sha256': holds._sha(holds._raw(policy)), 'windows': []}
    holds._require(type(raw) is str and holds._sha(raw) == anchor
        and witness == holds._sha(holds._raw(policy))
        and all(pipe.pttl(key) == -1 for key in (WINDOWS_KEY, ANCHOR_KEY, WITNESS_KEY)),
        'quality_hold_window_history_missing')
    value = holds._object(raw)
    holds._require(set(value) == {'version', 'policy_sha256', 'windows'}
        and type(value['version']) is int and value['version'] == 1 and value['policy_sha256'] == witness
        and type(value['windows']) is list and 1 <= len(value['windows']) <= 120,
        'quality_hold_window_history_invalid')
    natives = _native_policies(pipe)
    previous_end = holds._date(policy['valid_until'])
    for row in value['windows']:
        holds._require(type(row) is dict and set(row) == {'valid_from', 'valid_until', 'native_policy_sha256'})
        start, end = holds._date(row['valid_from']), holds._date(row['valid_until'])
        authority = natives.get(row['native_policy_sha256'])
        holds._require(authority is not None and previous_end <= start <= now and start < end
            and end - start <= timedelta(days=30)
            and holds._date(authority['valid_from']) <= start < end <= holds._date(authority['valid_until']),
            'quality_hold_window_authority_invalid')
        previous_end = end
    return value


def validate_time(pipe, policy, when, *, now):
    holds._policy(policy, holds._date(policy['valid_from']))
    windows = read_windows(pipe, policy, now)
    if holds._date(policy['valid_from']) <= when < holds._date(policy['valid_until']):
        return policy
    holds._require(any(holds._date(row['valid_from']) <= when < holds._date(row['valid_until'])
        for row in windows['windows']), 'quality_hold_policy_expired')
    return policy


def maintain_quality_hold_period():
    """No providers, jobs, cursors, budgets or original hold receipts change."""
    runtime, included = holds.runtime, holds.included
    if not runtime.enforcement_enabled() or not included.enabled():
        return {'status': 'disabled'}
    try:
        foundation = runtime.configured_ledger(read_timeout=2)
        now = foundation.clock()
        with foundation.client.pipeline() as pipe:
            pipe.watch(holds.POLICY_KEY, holds.ANCHOR_KEY)
            raw, anchor = pipe.get(holds.POLICY_KEY), pipe.get(holds.ANCHOR_KEY)
            if raw is None and anchor is None:
                return {'status': 'not_commissioned'}
            holds._require(type(raw) is str and holds._sha(raw) == anchor
                and pipe.pttl(holds.POLICY_KEY) == pipe.pttl(holds.ANCHOR_KEY) == -1)
            policy = holds._object(raw)
            holds._policy(policy, holds._date(policy['valid_from']))
            windows = read_windows(pipe, policy, now)
            end = holds._date(windows['windows'][-1]['valid_until'] if windows['windows'] else policy['valid_until'])
            if now < end:
                pipe.multi(); pipe.ping(); holds._require(pipe.execute() == [True])
                return {'status': 'current'}
            pipe.watch(*holds._financial_keys())
            ledger = voice.CreditLedger(foundation.client, foundation=foundation, clock=foundation.clock)
            ledger._watch(pipe)
            native, state, _, _ = ledger._read(pipe, now)
            history = periods.read_history(ledger, pipe, native, state, now)
            holds._require(history['periods'] and len(windows['windows']) < 120)
            for channel in policy['allowed_channels']:
                holds._funding(foundation, channel)
            from app.services.production_prepaid_audio import PrepaidAudioLedger
            router, _ = included.IncludedRouterLedger(foundation)._read(pipe)
            audio, _ = PrepaidAudioLedger(foundation)._read(pipe)
            limit = min(now + timedelta(days=30), *(holds._date(item['valid_until'])
                for item in (native, router['policy'], audio['policy'])))
            holds._require(now < limit)
            windows['windows'].append({'valid_from': periods._stamp(now), 'valid_until': periods._stamp(limit),
                                       'native_policy_sha256': voice._hash(native)})
            encoded = holds._raw(windows)
            first = len(windows['windows']) == 1
            pipe.multi(); pipe.set(WINDOWS_KEY, encoded); pipe.set(ANCHOR_KEY, holds._sha(encoded))
            if first:
                pipe.set(WITNESS_KEY, windows['policy_sha256'], nx=True)
            ack = pipe.execute()
            holds._require(ack == [True] * (3 if first else 2), 'quality_hold_window_commit_uncertain')
            return {'status': 'renewed', 'valid_until': periods._stamp(limit)}
    except Exception:
        return {'status': 'unavailable'}
