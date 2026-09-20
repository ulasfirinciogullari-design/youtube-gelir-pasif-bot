"""Bind a single next-series research request to its real scheduled task.

The scheduler stores the context in the same transaction as its dispatch.
No fake video job, funding initialization, new allowance or retry is created.
Draft research uses the existing shorts lineage ceiling and the shared daily,
channel and monthly cash/covered limits. It grants no media spending authority.
"""
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib

from app.services import production_next_series as planning
from app.services import production_spend_runtime as spending
from app.services.production_spend import LEDGER_KEY, SpendBlocked, _object, _period
from app.services.production_spend_quotes import quote_openai_series_search


CONTEXT_PREFIX = 'youtube_studio:next_series:v1:spend_context:'


def _require(condition, code='spend_series_context_invalid'):
    if not condition:
        raise SpendBlocked(code)


def _candidate(binding, profile, channel):
    planning._execution_keys(binding)
    _require(profile['channel_id'] == binding['channel_id']
             and profile['profile_revision'] == binding['profile_revision'])
    configuration = planning._configuration()
    if configuration[:2] == ('abacus_included', 'route-llm'):
        body = {'provider': configuration[0], 'model': configuration[1],
                'context': planning._context(profile, channel)}
        context = {'version': 2, 'purpose': 'series_preparation',
            'channel_id': binding['channel_id'], 'connection_id': channel['connection_id'],
            'lineage_id': binding['task_id'], 'kind': 'shorts', 'execution_binding': binding,
            'request_sha256': planning._digest(body), 'funding_mode': 'existing_subscription_included_router'}
        return context, body, None
    _require(configuration[:2] == ('openai', 'gpt-4.1-mini'), 'spend_request_not_priced')
    body = planning._openai_request(planning._context(profile, channel), configuration)
    quote = quote_openai_series_search(body)
    funding = spending._funding_admission('openai', 'responses', configuration[2])
    context = {'version': 1, 'purpose': 'series_preparation',
        'channel_id': binding['channel_id'], 'connection_id': channel['connection_id'],
        'lineage_id': binding['task_id'], 'kind': 'shorts', 'execution_binding': binding,
        'request_sha256': planning._digest(body), 'quote': asdict(quote), 'funding': funding}
    return context, body, quote


def _capacity(pipe, context, body, quote):
    """Pure checks under the caller's WATCH; no permit or summary is written."""
    from app.services.production_funding import reserve_funding

    ledger = spending.configured_ledger(read_timeout=2)
    now = ledger.clock()
    month, day = _period(now)
    _require(context['execution_binding']['day'] == day, 'spend_series_context_expired')
    if context.get('version') == 2:
        from app.services.production_included_router import IncludedRouterLedger, enabled
        _require(enabled() and quote is None and context['request_sha256'] == planning._digest(body))
        IncludedRouterLedger(ledger).check_capacity(pipe, context['channel_id'])
        return
    pipe.watch(LEDGER_KEY)
    period = ledger._read_state(pipe, month, day)
    _require(pipe.pttl(LEDGER_KEY) == -1, 'spend_store_expiring')
    channel, task = context['channel_id'], context['lineage_id']
    fingerprint = spending._request_fingerprint(context, 'openai', 'responses', body)
    for field in ('lineage:' + hashlib.sha256(task.encode()).hexdigest(),
                  'request:' + hashlib.sha256(fingerprint.encode()).hexdigest()):
        _require(not pipe.hexists(LEDGER_KEY, field), 'spend_request_already_reserved')
    checks = ((period['used_micro'], ledger.policy.monthly_micro, 'month'),
        (period['days'].get(day, 0), ledger.policy.daily_micro, 'day'),
        (period['channels'].get(channel, 0), ledger.policy.channel_monthly_micro, 'channel'),
        (0, ledger.policy.shorts_micro, 'lineage'))
    for used, limit, scope in checks:
        _require(used + quote.maximum_micro <= limit, 'spend_' + scope + '_limit')
    raw_policy, raw_state = (pipe.hget(LEDGER_KEY, name)
                             for name in ('funding_policy', 'funding_state'))
    _require(raw_policy is not None and raw_state is not None, 'spend_funding_not_initialized')
    # reserve_funding is a pure calculation; discard its returned state here.
    # The actual SDK adapter reserves the same bound atomically before POST.
    reserve_funding(_object(raw_policy), _object(raw_state), quote=quote,
                    now=now, **context['funding'])


def prepare_dispatch_context(pipe, binding, profile, channel):
    """Return bytes to SET NX alongside the dispatch; budget failures write zero."""
    _require(spending.enforcement_enabled(), 'spend_enforcement_required')
    key = CONTEXT_PREFIX + binding['task_id']
    job_key = spending._JOB_PREFIX + binding['task_id']
    pipe.watch(key, job_key)
    _require(not pipe.exists(key, job_key), 'spend_series_context_already_exists')
    context, body, quote = _candidate(binding, profile, channel)
    _capacity(pipe, context, body, quote)
    return key, planning._json(context)


def read_context(pipe, task_id, *, require_pending=True):
    """Validate immutable request binding plus current planning/OAuth authority."""
    from app.services.production_scheduler import _current

    key, job_key = CONTEXT_PREFIX + task_id, spending._JOB_PREFIX + task_id
    pipe.watch(key, job_key, LEDGER_KEY)
    _require(not pipe.exists(job_key) and pipe.pttl(key) == -1)
    _require(pipe.pttl(LEDGER_KEY) == -1, 'spend_store_expiring')
    stored = _object(pipe.get(key))
    binding = stored.get('execution_binding')
    keys = planning._execution_keys(binding)
    _require(binding['task_id'] == task_id
        and binding['day'] == datetime.now(timezone.utc).date().isoformat(), 'spend_series_context_expired')
    channel_id = binding['channel_id']
    pipe.watch(*keys, planning.PROFILE_PREFIX + channel_id,
        planning.OAUTH_CHANNEL_PREFIX + channel_id, planning.CHANNEL_STATE_PREFIX + channel_id)
    _require(pipe.pttl(keys[0]) == -1 and pipe.pttl(keys[1]) == -1)
    profile, channel, state, remaining = _current(pipe, channel_id)
    _require(remaining <= 2)
    planning._execution_guard(pipe, binding, profile, channel, state)
    expected, _, _ = _candidate(binding, profile, channel)
    _require(stored == expected)
    if require_pending:
        pending_key = planning.PENDING_PREFIX + channel_id
        daily_key = planning.DAILY_PREFIX + channel_id + ':' + binding['day']
        pipe.watch(pending_key, daily_key)
        raw = pipe.get(pending_key)
        _require(raw is not None and pipe.get(daily_key) == raw
            and pipe.pttl(pending_key) == -1 and pipe.pttl(daily_key) == -1)
        pending = _object(raw)
        expected_provider, expected_model = (('abacus_included', 'route-llm')
            if stored.get('version') == 2 else ('openai', 'gpt-4.1-mini'))
        _require(pending.get('status') == 'reserved' and pending.get('provider') == expected_provider
            and pending.get('model') == expected_model
            and pending.get('channel_id') == channel_id and pending.get('day') == binding['day']
            and pending.get('profile_revision') == binding['profile_revision']
            and pending.get('context_sha256') == planning._digest(planning._context(profile, channel)))
    return expected


def check_worker_capacity(pipe, binding):
    _require(spending.enforcement_enabled(), 'spend_enforcement_required')
    _require(spending._TASK_ID.get() == binding['task_id'], 'spend_context_missing')
    context = read_context(pipe, binding['task_id'], require_pending=False)
    profile = planning._object(pipe.get(planning.PROFILE_PREFIX + binding['channel_id']))
    channel = planning._object(pipe.get(planning.OAUTH_CHANNEL_PREFIX + binding['channel_id']))
    expected, body, quote = _candidate(binding, profile, channel)
    _require(context == expected)
    _capacity(pipe, context, body, quote)


def validate_request(context, provider, operation, payload, quote, funding):
    _require(provider == 'openai' and operation == 'responses'
        and planning._digest(payload) == context['request_sha256']
        and asdict(quote) == context['quote'] and funding == context['funding'],
        'spend_series_request_changed')
