from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.services import content_plan as plan, content_plan_recovery as recovery
from app.services import content_plan_research_resume as resume, studio_state as jobs
from app.services import included_research_sources as sources
from test_content_plan import case, CHANNEL, OTHER


def failed(case, monkeypatch, *, long=False):
    channel = OTHER if long else CHANNEL
    if long:
        entry = plan.item('Documentary', 'Two primary references', 'long')
        plan.change(OTHER, 'new', 'add', payload=entry)
    queue = Mock(); plan.maintain([{**case.profile, 'channel_id': channel}], queue)
    task = queue.call_args.kwargs['task_id']; source = json.loads(case.client.get(jobs.JOB_PREFIX + task))
    error = 'commissioning_longform_unverified' if long else 'included_research_primary_source_unavailable'
    source.update(state='FAILURE', failure_stage='research', error=error,
        paid_create_slots_used=0, preview_total_paid_create_cap=32 if long else 6)
    if long:
        key = plan.DISPATCH_PREFIX + source['spec']['content_plan_item_id']
        dispatch = json.loads(case.client.get(key))
        dispatch['spec_sha256'] = plan._sha({**source['spec'], 'duration_minutes': 3.0})
        case.client.set(key, plan._raw(dispatch))
    case.client.set(jobs.JOB_PREFIX + task, plan._raw(source))
    case.client.hset(jobs.PAID_CREATE_BUDGET_PREFIX + task, mapping={'cap': str(32 if long else 6), 'used': '0'})
    case.client.set('celery-task-meta-' + task, plan._raw({'task_id': task, 'status': 'FAILURE',
        'result': {'exc_type': 'SpendBlocked', 'exc_message': [error]},
        'traceback': 'File "/app/app/tasks.py", line 4949, in run_video_pipeline\n'
        + 'File "/app/app/services/research.py", line 278, in research_and_script\n'
        + ('File "/app/app/services/commissioning_longform.py", line 38, in authorize\n' if long else
           'File "/app/app/services/included_research_sources.py", line 205, in research_pages\n')}))
    monkeypatch.setattr(resume, '_provider_free', Mock())
    monkeypatch.setattr(sources, 'research_pages', Mock(return_value=[{'url': 'one'}, {'url': 'two'}]))
    return source


@pytest.mark.parametrize('long', [False, True])
def test_concurrent_schedule_sends_once_without_modifying_old_jobs_or_dispatches(case, monkeypatch, long):
    source = failed(case, monkeypatch, long=long)
    before = {k: case.client.dump(k) for k in case.client.scan_iter()}
    queue = Mock()
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: recovery.schedule(source, queue), range(4)))
    queue.assert_called_once()
    assert queue.call_args.kwargs == {'args': (source['task_id'],),
        'task_id': resume.operation(source['task_id']), 'retry': False}
    assert all(case.client.dump(k) == v for k, v in before.items())
    assert not case.client.exists(plan.COMPLETION_PREFIX + source['spec']['content_plan_item_id'])


@pytest.mark.parametrize('damage', ['voice', 'model', 'cash', 'terminal', 'cancel', 'spec', 'budget', 'hold', 'one_source'])
def test_unknown_or_paid_or_changed_roots_never_dispatch(case, monkeypatch, damage):
    from app.services import source_publication_hold
    source = failed(case, monkeypatch); task = source['task_id']
    if damage == 'voice': source['audio_candidate_checkpoint'] = {'voice': 'exists'}
    if damage in {'model', 'cash'}: resume._provider_free.side_effect = plan.ContentPlanError('provider_attempted')
    if damage == 'terminal': case.client.delete('celery-task-meta-' + task)
    if damage == 'cancel': case.client.set(jobs.RENDER_CANCELLATION_PREFIX + task, 'owner stopped')
    if damage == 'spec': source['spec']['topic'] = 'different'
    if damage == 'budget': case.client.hset(jobs.PAID_CREATE_BUDGET_PREFIX + task, 'used', '1')
    if damage == 'hold': case.client.set(source_publication_hold.HOLD_PREFIX + task, 'held')
    if damage == 'one_source': sources.research_pages.return_value = [{'url': 'one'}]
    case.client.set(jobs.JOB_PREFIX + task, plan._raw(source))
    before = {k: case.client.dump(k) for k in case.client.scan_iter()}; queue = Mock()
    with pytest.raises((plan.ContentPlanError, ValueError, TypeError)):
        recovery.schedule(source, queue)
    queue.assert_not_called()
    assert all(case.client.dump(k) == v for k, v in before.items())


def test_uncertain_queue_ack_preserved_and_not_sent_twice(case, monkeypatch):
    source = failed(case, monkeypatch); queue = Mock(side_effect=TimeoutError())
    assert recovery.schedule(source, queue) == 'research_resume_uncertain'
    assert recovery.schedule(source, queue) == 'research_resume_reserved'
    queue.assert_called_once()


@pytest.mark.parametrize('long', [False, True])
def test_actual_child_dispatch_and_entry_preserve_scope_and_do_not_load_nonexistent_voice(case, monkeypatch, tmp_path, long):
    from app import tasks
    source = failed(case, monkeypatch, long=long); task = source['task_id']
    monkeypatch.setattr(jobs, '_client', lambda: case.client)
    send = Mock(); monkeypatch.setattr(tasks.run_video_pipeline, 'apply_async', send)
    recovery.schedule(source, Mock())
    result = recovery.run(task, resume.operation(task))
    assert result['status'] == 'enqueued'; child = result['task_id']
    args = send.call_args.kwargs['args']
    assert args[1] == source['spec']['duration_minutes'] and args[5:] == (None, task)
    assert send.call_args.kwargs['retry'] is False
    assert recovery.run(task, resume.operation(task)) == {'status': 'already_started'}
    send.assert_called_once()
    assert jobs.acquire_retry_child_execution(child, task)
    assert resume.verify_child(child, task, source['spec'])['task_id'] == task
    assert tasks._prepare_saved_voice_retry(child, task, source['spec'], tmp_path) is None
    if not long:
        assert tasks._fresh_scheduled_short_shots(child, source['spec'], approved_package=None,
            retry_dispatch_source_id=task, curated_stock_manifest=None, voice_replacement_source_id=None,
            paid_slots_used=0) is True
    case.client.set(jobs.RENDER_CANCELLATION_PREFIX + task, 'owner stopped')
    with pytest.raises(plan.ContentPlanError): tasks._prepare_saved_voice_retry(child, task, source['spec'], tmp_path)


def test_only_equivalent_integral_duration_encoding_matches_old_dispatch():
    spec = {'topic': 'Original unchanged documentary', 'format': 'landscape', 'duration_minutes': 3.0}
    dispatch = {'item': {'format': 'long'}, 'spec_sha256': plan._sha(spec)}
    assert plan.dispatch_spec_matches(dispatch, {**spec, 'duration_minutes': 3})
    for changed in ({'topic': 'changed'}, {'duration_minutes': 3.1}, {'duration_minutes': True},
                    {'duration_minutes': '3'}, {'format': 'shorts'}):
        assert not plan.dispatch_spec_matches(dispatch, {**spec, **changed})


@pytest.mark.parametrize('provider', [None, 'voice', 'router', 'native', 'cash'])
def test_provider_free_check_uses_the_same_client_and_blocks_every_prior_intent(case, monkeypatch, provider):
    import fakeredis, hashlib
    from app.services import production_spend_runtime as runtime, production_credit_ledger as credit
    from app.services import production_included_router as included, commissioning_reasoning as native
    from app.services.production_spend import SpendLedger, SpendPolicy, LEDGER_KEY
    # Runtime creates a distinct client; the guarded transaction must use the
    # caller's client consistently when constructing its native credit ledger.
    foundation = SpendLedger(fakeredis.FakeRedis(decode_responses=True), SpendPolicy(0, 0, 0, 0, 0, 0))
    monkeypatch.setattr(runtime, 'configured_ledger', lambda **_: foundation)
    task = str(uuid4()); state = {'intents': {}}; router = {'requests': {}}
    original = credit.CreditLedger.__init__
    constructions = []
    def initialize(self, client, **kwargs):
        original(self, client, **kwargs)
        constructions.append(client)
    monkeypatch.setattr(credit.CreditLedger, '__init__', initialize)
    monkeypatch.setattr(credit.CreditLedger, '_watch', lambda *a: None)
    monkeypatch.setattr(credit.CreditLedger, '_read', lambda *a: ({}, state, {}, {}))
    monkeypatch.setattr(included.IncludedRouterLedger, '_read', lambda *a: ({}, router))
    if provider == 'voice': state['intents']['x'] = {'reservation': {'intent': {'root_lineage_id': task}}}
    if provider == 'router': router['requests']['x'] = {'context': {'lineage_id': task}}
    if provider == 'native': case.client.sadd(native.PREFIX + 'lineage:' + task, 'unknown-request')
    if provider == 'cash': case.client.hset(LEDGER_KEY, 'lineage:' + hashlib.sha256(task.encode()).hexdigest(), 'reserved')
    before = {k: case.client.dump(k) for k in case.client.scan_iter()}
    if provider:
        with pytest.raises(plan.ContentPlanError): resume._provider_free(case.client, task)
    else:
        resume._provider_free(case.client, task)
    assert constructions == [case.client]
    assert all(case.client.dump(k) == v for k, v in before.items())
