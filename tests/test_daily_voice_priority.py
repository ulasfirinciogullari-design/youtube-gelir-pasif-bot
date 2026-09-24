"""Real queue/credit transactions: a waiting daily film cannot starve affordable Shorts."""
from concurrent.futures import ThreadPoolExecutor
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services import content_plan as plan, channel_cadence as cadence, channel_production as production
from app.services import daily_voice_priority as priority, production_spend_runtime as runtime
from app.services.production_credit_ledger import CreditLedger, JOURNAL_KEY
from test_content_plan import case, CHANNEL
from test_production_credit_ledger import policy, NOW, intent, observation, binding, context
from test_production_cash_disabled import money


def dump(c): return {key: c.dump(key) for key in c.scan_iter()}


@pytest.fixture
def waiting(case, policy, monkeypatch):
    c = case.client
    doc = plan.change(CHANNEL, case.document['revision'], 'settings',
        payload={'enabled': True, 'after_queue': 'auto_shorts'})
    for entry in doc['items']: c.set(plan.COMPLETION_PREFIX + entry['id'], 'original-public-proof')
    profile = {**case.profile, 'production_topics': ['A verified manufacturing question'],
        'production_interval_hours': 6}
    c.set(production.PROFILE_PREFIX + CHANNEL, plan._raw(profile))
    result = cadence.install_daily_long(profile, profile['production_topics'], client=c, now=NOW.timestamp())
    policy['allocation_credits'] = 6000
    foundation = money(c); foundation.clock = lambda: NOW
    ledger = CreditLedger(c, foundation=foundation, clock=foundation.clock); ledger.initialize(policy)
    ctx = context(c)
    receipt = ledger.reserve(intent=intent(), production_context=ctx, **binding(policy))
    ledger.settle(observation=observation(policy, receipt, actual_credit_cost=2000), **binding(policy))
    monkeypatch.setattr(runtime, 'configured_ledger', lambda **kw: foundation)
    monkeypatch.setattr(runtime, 'enforcement_enabled', lambda: True)
    monkeypatch.setattr(production.settings, 'studio_spend_enforcement', True)
    monkeypatch.setattr(production, '_redis', lambda: c)
    connection = json.loads(c.get(production.OAUTH_CHANNEL_PREFIX + CHANNEL))
    return SimpleNamespace(c=c, profile=profile, connection=connection, item=result['item_id'],
        ledger=ledger, policy=policy, context=ctx, funding=case.funding)


def dispatch(w): return production.reserve_due_production(w.profile, w.connection, now=NOW.timestamp())


def test_waiting_long_stays_exactly_queued_while_short_gets_ordinary_daily_slot(waiting):
    w = waiting; before = dump(w.c)
    assert plan.owns_channel(CHANNEL, client=w.c)
    assert priority.eligible(CHANNEL, client=w.c) and dump(w.c) == before
    result = dispatch(w); assert result['status'] == 'reserved'
    assert result['args'][1] == .5 and result['args'][4]['format'] == 'shorts'
    assert result['args'][4]['production_editorial']['reason_code'] == 'owner_daily_voice_priority'
    assert not w.c.exists(plan.DISPATCH_PREFIX + w.item, plan.COMPLETION_PREFIX + w.item)
    assert w.c.dump(plan.PLAN_PREFIX + CHANNEL) == before[plan.PLAN_PREFIX + CHANNEL]
    for key, value in before.items():
        if 'production_spend' in key: assert w.c.dump(key) == value
    assert cadence.snapshot(CHANNEL, client=w.c, now=NOW.timestamp())['counts']['produced'] == {'long': 0, 'shorts': 1}
    # The same channel cannot start the long concurrently with its Short.
    assert plan._reserve(CHANNEL, now=NOW.timestamp())['status'] == 'capacity_wait'


@pytest.mark.parametrize('damage', ['paused', 'pause_after', 'owner_edit', 'another_item', 'series',
    'missing_receipt', 'dispatched', 'started_job', 'active', 'unknown_voice', 'missing_journal', 'low_balance', 'other_channel'])
def test_owner_order_uncertainty_and_insufficient_credits_never_allow_bypass(waiting, damage):
    w = waiting; doc = plan.read(CHANNEL, client=w.c); entry = doc['items'][-1]
    if damage == 'paused': doc['enabled'] = False
    if damage == 'pause_after': doc['after_queue'] = 'pause'
    if damage == 'owner_edit': entry['brief'] += ' Owner-specific change'
    if damage == 'another_item': doc['items'].append(plan.item('Next chapter', 'Specific sequence'))
    if damage == 'series': entry['series'] = {'id': 'ordered', 'name': 'Ordered', 'number': 1, 'total': 2}
    if damage == 'missing_receipt':
        w.c.delete(cadence.PREFIX + 'daily_plan:' + CHANNEL + ':2026-09-09')
    if damage == 'dispatched': w.c.set(plan.DISPATCH_PREFIX + w.item, 'uncertain broker acknowledgement')
    if damage == 'started_job':
        from uuid import uuid5, NAMESPACE_URL
        task = str(uuid5(NAMESPACE_URL, 'youtube-owner-plan:' + CHANNEL + ':' + w.item))
        w.c.set(plan.jobs.JOB_PREFIX + task, 'prior work')
    if damage == 'active': w.c.set(plan.ACTIVE_KEY, plan._raw({CHANNEL: w.item}))
    if damage in {'unknown_voice', 'low_balance'}:
        receipt = w.ledger.reserve(intent=intent(2), production_context=w.context, **binding(w.policy))
        if damage == 'low_balance':
            w.ledger.settle(observation=observation(w.policy, receipt, actual_credit_cost=3200), **binding(w.policy))
    if damage == 'missing_journal': w.c.delete(JOURNAL_KEY)
    w.c.set(plan.PLAN_PREFIX + CHANNEL, plan._raw(doc)); before = dump(w.c)
    assert not priority.eligible('UCs93z6wf134H5_BL9pkQX4Q' if damage == 'other_channel' else CHANNEL, client=w.c)
    assert dump(w.c) == before
    if damage != 'other_channel': assert dispatch(w)['status'] == 'owner_content_plan'


def test_exact_daily_limits_still_apply_when_long_is_waiting(waiting):
    w = waiting; w.c.hset(cadence.keys(CHANNEL, now=NOW.timestamp())[0],
        mapping={str(uuid4()): 'shorts' for _ in range(5)})
    before = dump(w.c)
    assert dispatch(w)['status'] == 'daily_limit_wait'
    assert dump(w.c) == before


def test_concurrent_ticks_reserve_only_one_short_and_preserve_the_long(waiting):
    def run(_):
        try: return dispatch(waiting)['status']
        except production.ChannelProductionError: return 'changed'
    with ThreadPoolExecutor(max_workers=8) as pool: result = list(pool.map(run, range(16)))
    assert result.count('reserved') == 1
    assert not waiting.c.exists(plan.DISPATCH_PREFIX + waiting.item)
    assert waiting.ledger.summary()['spent_credits'] == 2000


def test_owner_pause_during_final_atomic_recheck_prevents_short_creation(waiting, monkeypatch):
    w = waiting; original = w.c.pipeline
    def pipeline(*a, **kw):
        pipe = original(*a, **kw); execute = pipe.execute
        def race(*a, **kw):
            if any(row[0][0] == 'EVAL' for row in pipe.command_stack):
                doc = plan.read(CHANNEL, client=w.c); doc['enabled'] = False
                w.c.set(plan.PLAN_PREFIX + CHANNEL, plan._raw(doc))
            return execute(*a, **kw)
        pipe.execute = race; return pipe
    monkeypatch.setattr(w.c, 'pipeline', pipeline)
    with pytest.raises(production.ChannelProductionError): dispatch(w)
    assert not w.c.exists(production.ACTIVE_KEY)
    assert cadence.snapshot(CHANNEL, client=w.c, now=NOW.timestamp())['counts']['produced'] == {'shorts': 0, 'long': 0}
