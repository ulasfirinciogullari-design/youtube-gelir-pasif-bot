"""Real scheduler, planner, ledger and installed SDK over synthetic HTTP only."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import Mock

import fakeredis
import httpx
import pytest

from app.services import production_next_series as planning
from app.services import production_scheduler as scheduler
from app.services import production_series_spend as series
from app.services import production_spend_runtime as runtime
from app.services import production_spend_quotes as quotes
from app.services.production_spend import LEDGER_KEY, SpendBlocked, SpendLedger, SpendPolicy
from spending_test_support import TEST_KEY, installed_sdk_modules, test_funding_policy as funding_fixture
from test_production_next_series import _answer, URL
from test_retained_review_credential_successor import Intercept


NOW = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
CHANNEL = 'UC5v9AvNtD3PTLgo6m1jROOA'


def snapshot(client):
    return {key: client.dump(key) for key in client.scan_iter('*')}


@pytest.fixture
def box(monkeypatch, installed_sdk_modules):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)
    monkeypatch.setattr(quotes, 'datetime', Clock)
    monkeypatch.setattr(series, 'datetime', Clock)
    client = fakeredis.FakeRedis(decode_responses=True)
    ledger = SpendLedger(client, SpendPolicy(10_000_000, 1_000_000, 5_000_000,
                         200_000, 1_000_000, 100_000), clock=lambda: NOW)
    ledger.initialize()
    funding = funding_fixture(ledger)
    funding['accounts'] = funding['accounts'][:1]
    funding['accounts'][0]['routes'].append({'route': 'https://api.openai.com/v1/responses',
        'model': 'gpt-4.1-mini', 'price_revision': quotes.OPENAI_SERIES_PRICE_REVISION})
    settings = SimpleNamespace(redis_url='redis://synthetic', studio_spend_enforcement=True,
                              openai_api_key=TEST_KEY)
    for module in (planning, scheduler, runtime):
        monkeypatch.setattr(module, 'settings', settings)
    monkeypatch.setattr(runtime, 'configured_ledger', lambda **kw: ledger)
    monkeypatch.setattr(scheduler, '_client', lambda: client)
    monkeypatch.setattr(planning.redis.Redis, 'from_url', lambda *a, **kw: client)
    profile = {'channel_id': CHANNEL, 'profile_revision': 'profile-synthetic-one',
        'channel_identity': 'Evidence-led business history', 'default_language': 'en',
        'series_name': 'Old Decisions', 'series_id': 'series-old', 'series_total': 2,
        'production_topics': ['Why banks close on holidays', 'The first supermarket barcode'],
        'release_mode': 'public', 'production_enabled': True, 'auto_publish': True,
        'production_interval_hours': 24}
    channel = {'id': CHANNEL, 'connection_id': 'synthetic-connection-one',
               'title': 'Synthetic channel', 'description': 'Business history and decisions.'}
    client.set(planning.PROFILE_PREFIX + CHANNEL, json.dumps(profile))
    client.set(planning.OAUTH_CHANNEL_PREFIX + CHANNEL, json.dumps(channel))
    client.set(planning.OAUTH_CREDENTIAL_PREFIX + CHANNEL, 'encrypted-synthetic-credential')
    client.sadd(planning.OAUTH_CHANNEL_INDEX, CHANNEL)
    client.set(planning.AUTH_EPOCH_KEY, '13')
    client.hset(planning.CHANNEL_STATE_PREFIX + CHANNEL, mapping={
        'cursor': '2', 'next_due': '0', 'profile_revision': profile['profile_revision'],
        'connection_id': channel['connection_id'],
        'consumed_prefix': planning._prefix_digest(profile['production_topics'])})
    client.set('synthetic:old_source_and_unknown_request', 'preserve-existing-history')
    result = SimpleNamespace(client=client, ledger=ledger, funding=funding,
        settings=settings, profile=profile, channel=channel, calls=[], behavior='success')
    def send(request):
        assert request.method == 'POST' and str(request.url) == 'https://api.openai.com/v1/responses'
        body = json.loads(request.content)
        assert ledger.snapshot()['period']['used_micro'] == quotes.quote_openai_response(body).maximum_micro
        result.calls.append(body)
        if result.behavior == 'timeout':
            raise httpx.ReadTimeout('SYNTHETIC unknown provider reply', request=request)
        output = _answer()
        sources = [{'type': 'url', 'url': URL}]
        if result.behavior == 'unconsulted':
            sources = [{'type': 'url', 'url': 'https://example.org/unrelated'}]
        items = [{'type': 'web_search_call', 'id': 'ws_synthetic', 'status': 'completed',
                  'action': {'type': 'search', 'query': 'synthetic', 'sources': sources}}]
        if result.behavior == 'no_search': items = []
        items.append({'type': 'message', 'id': 'msg_synthetic', 'role': 'assistant',
            'status': 'completed', 'content': [{'type': 'output_text', 'text': json.dumps(output),
                                              'annotations': []}]})
        return httpx.Response(200, json={'id': 'resp_synthetic', 'object': 'response',
            'created_at': NOW.timestamp(), 'status': 'completed', 'model': 'gpt-4.1-mini-2025-04-14',
            'output': items})
    sdk = installed_sdk_modules['openai']
    original = sdk.OpenAI
    def make(**kwargs):
        return original(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(send)))
    monkeypatch.setattr(sdk, 'OpenAI', make)
    return result


def enqueue(box):
    sender = Mock()
    result = scheduler._reserve_preparation(CHANNEL, box.profile['profile_revision'],
        box.channel['connection_id'], sender, NOW.timestamp())
    assert result == {'status': 'preparation_queued'}, result
    return sender.call_args.kwargs['args'][0]


def execute(binding):
    # Exercise the actual Celery task's spending_task decorator, not a manually
    # installed ContextVar or a mock planner that bypasses the paid adapter.
    from app.production_tasks import prepare_series_batch
    prepare_series_batch.push_request(id=binding['task_id'])
    try:
        return prepare_series_batch.run(binding)
    finally:
        prepare_series_batch.pop_request()


def test_real_series_task_uses_one_bounded_paid_request_and_no_video_job(box, monkeypatch):
    monkeypatch.setattr(scheduler, '_now', lambda value: NOW.timestamp() if value is None else value)
    before = snapshot(box.client)
    sender = Mock()
    assert scheduler._reserve_preparation(CHANNEL, box.profile['profile_revision'],
        box.channel['connection_id'], sender, NOW.timestamp())['status'] == 'budget_blocked'
    assert snapshot(box.client) == before and not box.calls
    box.ledger.initialize_funding(box.funding)
    before = snapshot(box.client)
    binding = enqueue(box)
    context_key = series.CONTEXT_PREFIX + binding['task_id']
    assert box.client.pttl(context_key) == -1
    assert box.client.dump(LEDGER_KEY) == before[LEDGER_KEY]
    result = execute(binding)
    assert result['status'] == 'ready', result
    assert result['qa_approved'] is result['publish_eligible'] is result['media_budget_approved'] is False
    assert result['requires_full_research_and_critic'] is True
    assert len(box.calls) == 1 and box.calls[0]['model'] == 'gpt-4.1-mini'
    assert box.calls[0]['max_tool_calls'] == 2 and box.calls[0]['max_output_tokens'] == 3600
    assert 'reasoning' not in box.calls[0]
    quote = quotes.quote_openai_response(box.calls[0])
    assert 20000 < quote.maximum_micro < 100000
    usage = box.ledger.snapshot()['period']
    assert usage['used_micro'] == usage['channels'][CHANNEL] == usage['days']['2026-09-20'] == quote.maximum_micro
    assert box.ledger.funding_snapshot()['cash_reserved_micro'] == quote.maximum_micro
    assert not box.client.exists(runtime._JOB_PREFIX + binding['task_id'])
    stored = box.client.hget(LEDGER_KEY, 'binding:' + binding['task_id'])
    assert json.loads(stored)['purpose'] == 'series_preparation'
    assert TEST_KEY not in stored and 'documentary' not in stored
    assert runtime._TASK_ID.get() is None
    after = snapshot(box.client)
    assert execute(binding)['status'] == 'execution_already_claimed'
    assert snapshot(box.client) == after and len(box.calls) == 1
    assert box.client.get('synthetic:old_source_and_unknown_request') == 'preserve-existing-history'


@pytest.mark.parametrize('damage', ['route', 'credential', 'cash', 'covered', 'daily', 'lineage', 'ttl'])
def test_exact_route_preflight_never_consumes_a_dispatch_or_daily_attempt(box, damage):
    if damage == 'route': box.funding['accounts'][0]['routes'].pop()
    if damage == 'credential': box.settings.openai_api_key = 'different-synthetic-key'
    if damage == 'cash': box.funding['opening_cash_micro'] = 10_000_000
    if damage == 'covered':
        box.funding['accounts'][0].update(mode='covered_only', funding={
            'covered_list_allowance_micro': 1, 'coverage_basis': 'verified_route_list_cost_usd', 'no_auto_overage': True})
    if damage in {'daily', 'lineage'}:
        field = 'daily_micro' if damage == 'daily' else 'shorts_micro'
        box.ledger.policy = replace(box.ledger.policy, **{field: 1})
        from dataclasses import asdict
        box.client.hset(LEDGER_KEY, 'policy', json.dumps(asdict(box.ledger.policy)))
    box.ledger.initialize_funding(box.funding)
    if damage == 'ttl': box.client.expire(LEDGER_KEY, 600)
    before, sender = snapshot(box.client), Mock()
    result = scheduler._reserve_preparation(CHANNEL, box.profile['profile_revision'],
        box.channel['connection_id'], sender, NOW.timestamp())
    assert result['status'] == 'budget_blocked', result
    assert snapshot(box.client) == before and not box.calls
    sender.assert_not_called()


@pytest.mark.parametrize('behavior,status', [('timeout', 'uncertain'), ('no_search', 'failed'), ('unconsulted', 'failed')])
def test_unknown_or_unverified_output_never_creates_a_ready_batch_or_refund(box, monkeypatch, behavior, status):
    monkeypatch.setattr(scheduler, '_now', lambda value: NOW.timestamp() if value is None else value)
    box.ledger.initialize_funding(box.funding)
    binding = enqueue(box)
    box.behavior = behavior
    result = execute(binding)
    assert result['status'] == status, result
    assert len(box.calls) == 1 and box.ledger.snapshot()['period']['used_micro'] > 0
    before = snapshot(box.client)
    execute(binding)
    assert snapshot(box.client) == before and len(box.calls) == 1


def test_capacity_lost_after_enqueue_leaves_pending_and_daily_unused(box, monkeypatch):
    monkeypatch.setattr(scheduler, '_now', lambda value: NOW.timestamp() if value is None else value)
    box.ledger.initialize_funding(box.funding)
    binding = enqueue(box)
    state = json.loads(box.client.hget(LEDGER_KEY, 'funding_state'))
    state['cash_reserved_micro'] = 10_000_000
    box.client.hset(LEDGER_KEY, 'funding_state', json.dumps(state))
    result = execute(binding)
    assert result['status'] == 'budget_blocked', result
    assert not box.client.exists(planning.PENDING_PREFIX + CHANNEL,
                                 planning.DAILY_PREFIX + CHANNEL + ':2026-09-20')
    assert not box.calls


@pytest.mark.parametrize('damage', ['context_missing', 'context_expiring', 'context_changed',
    'job_collision', 'owner_pause', 'oauth_epoch', 'oauth_credential', 'api_credential', 'membership', 'ledger_expiring'])
def test_request_context_and_current_authority_are_checked_at_actual_adapter(box, monkeypatch, damage):
    monkeypatch.setattr(scheduler, '_now', lambda value: NOW.timestamp() if value is None else value)
    box.ledger.initialize_funding(box.funding)
    binding = enqueue(box)
    context_key = series.CONTEXT_PREFIX + binding['task_id']
    original = planning._generate
    def changed(context, configuration):
        if damage == 'context_missing': box.client.delete(context_key)
        if damage == 'context_expiring': box.client.expire(context_key, 600)
        if damage == 'ledger_expiring': box.client.expire(LEDGER_KEY, 600)
        if damage == 'context_changed':
            record = json.loads(box.client.get(context_key))
            record['quote']['maximum_micro'] = 1
            box.client.set(context_key, json.dumps(record))
        if damage == 'job_collision': box.client.set(runtime._JOB_PREFIX + binding['task_id'], '{}')
        if damage == 'owner_pause': box.client.hset(planning.CHANNEL_STATE_PREFIX + CHANNEL, 'paused_reason', 'owner_disabled')
        if damage == 'oauth_epoch': box.client.set(planning.AUTH_EPOCH_KEY, '14')
        if damage == 'oauth_credential': box.client.set(planning.OAUTH_CREDENTIAL_PREFIX + CHANNEL, 'changed')
        if damage == 'api_credential': box.settings.openai_api_key = 'changed-synthetic-key'
        if damage == 'membership': box.client.srem(planning.OAUTH_CHANNEL_INDEX, CHANNEL)
        return original(context, configuration)
    monkeypatch.setattr(planning, '_generate', changed)
    assert execute(binding)['status'] == 'uncertain'
    assert not box.calls and box.ledger.snapshot()['period']['used_micro'] == 0


@pytest.mark.parametrize('fault', ['lost_ack', 'authority_changed'])
def test_paid_reservation_keeps_hold_when_ack_or_later_authority_is_lost(box, monkeypatch, fault):
    monkeypatch.setattr(scheduler, '_now', lambda value: NOW.timestamp() if value is None else value)
    box.ledger.initialize_funding(box.funding)
    binding = enqueue(box)
    intercepted = False
    def after(commands, reply):
        nonlocal intercepted
        if (commands == ('HSET',) and not intercepted
                and any(box.client.hscan_iter(LEDGER_KEY, match='request:*'))):
            intercepted = True
            if fault == 'lost_ack': raise ConnectionError('SYNTHETIC lost paid reservation')
            box.client.set(planning.AUTH_EPOCH_KEY, '14')
        return reply
    box.ledger.client = Intercept(box.client, after=after)
    assert execute(binding)['status'] == 'uncertain'
    assert intercepted and not box.calls and box.ledger.snapshot()['period']['used_micro'] > 0
    before = snapshot(box.client)
    execute(binding)
    assert snapshot(box.client) == before and not box.calls


@pytest.mark.parametrize('field,value', [('max_tool_calls', 3), ('max_tool_calls', True),
    ('max_output_tokens', 3601), ('max_output_tokens', None), ('store', True),
    ('previous_response_id', 'resp_old'), ('reasoning', {'effort': 'low'}),
    ('tools', [{'type': 'web_search_preview'}]), ('tools', [{'type': 'web_search', 'return_token_budget': 'unlimited'}]),
    ('service_tier', 'priority'), ('include', []), ('input', [{'role': 'user', 'content': 'unbounded'}]),
    ('input', 'A' * 30001), ('model', 'gpt-5.6-luna')])
def test_search_quote_rejects_unreviewed_shapes(box, field, value):
    body = planning._openai_request(planning._context(box.profile, box.channel), planning._configuration())
    body[field] = value
    with pytest.raises(SpendBlocked): quotes.quote_openai_response(body)


@pytest.mark.parametrize('fault', ['race', 'lost_ack'])
def test_atomic_dispatch_and_context_cannot_authorize_after_uncertain_commit(box, monkeypatch, fault):
    box.ledger.initialize_funding(box.funding)
    def race(commands):
        if commands == ('SET', 'SET') and fault == 'race':
            box.client.hset(LEDGER_KEY, 'other_reserved_request', 'preserve')
    def lost(commands, reply):
        if commands == ('SET', 'SET') and fault == 'lost_ack':
            raise ConnectionError('SYNTHETIC lost acknowledgement')
        return reply
    monkeypatch.setattr(scheduler, '_client', lambda: Intercept(box.client, before=race, after=lost))
    sender = Mock()
    with pytest.raises(Exception):
        scheduler._reserve_preparation(CHANNEL, box.profile['profile_revision'],
            box.channel['connection_id'], sender, NOW.timestamp())
    sender.assert_not_called()
    assert not box.calls
    contexts = list(box.client.scan_iter(series.CONTEXT_PREFIX + '*'))
    dispatches = list(box.client.scan_iter(planning.PREPARATION_DISPATCH_PREFIX + '*'))
    assert len(contexts) == len(dispatches) == (1 if fault == 'lost_ack' else 0)
    if fault == 'lost_ack':
        result = scheduler._reserve_preparation(CHANNEL, box.profile['profile_revision'],
            box.channel['connection_id'], sender, NOW.timestamp())
        assert result['status'] == 'preparation_already_reserved'
        sender.assert_not_called()
