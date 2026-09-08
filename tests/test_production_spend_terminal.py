"""Budget stops must finish worker records without a dashboard visit or replay.

Exercise the real exception handlers and Celery retry declarations with a
synthetic throwing body, real job persistence and the server-side reconciler.
No planning, provider, rendering or publication code is invoked.
"""
import ast
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import Mock

from celery import Celery
import pytest

from app.services import studio_state
from app.services.production_spend import LEDGER_KEY, SpendBlocked
from test_channel_production import production, _parallel_channels


SOURCE = Path(__file__).resolve().parents[1] / 'app/tasks.py'
TREE = ast.parse(SOURCE.read_text(encoding='utf-8'))
TASK_ID = '11111111-1111-4111-8111-111111111111'
ERRORS = {
    name: type(name, (RuntimeError,), {}) for name in (
        'FinalVisualQualityError', 'FinalAudioQualityError',
        'GeminiOmniContinuityReferenceError', 'ImmutableNarrationSceneBudgetError',
        'UnsupportedLanguageError',
    )
}
ERRORS['SpendBlocked'] = SpendBlocked


def _worker(pipeline, task_id, error, *, attempts=0, options=None, **overrides):
    source = next(node for node in TREE.body
                  if isinstance(node, ast.FunctionDef) and node.name == pipeline)
    outer = next(node for node in source.body if isinstance(node, ast.Try))
    function = ast.parse('def failure_task(self):\n    try:\n        fail()\n    except Exception:\n        pass\n').body[0]
    function.body[0].handlers = deepcopy(outer.handlers)
    fail = Mock(side_effect=error)
    mark = Mock(wraps=studio_state.mark_failure)
    stage = Mock(wraps=studio_state.set_stage)
    namespace = dict(
        ERRORS, task_id=task_id, fail=fail, mark_failure=mark, set_stage=stage,
        total_paid_create_cap=6, runway_attempts=attempts,
        _persisted_paid_create_slots=Mock(return_value=attempts),
        options=options or {'mode': 'production', 'format': 'shorts'},
        retry_dispatch_source_id=None, duration_minutes=0.5, full_rebuild_source_id=None,
    )
    namespace.update(overrides)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])),
                 str(SOURCE), 'exec'), namespace)
    declaration = source.decorator_list[0]
    retry_options = {
        item.arg: eval(compile(ast.Expression(item.value), str(SOURCE), 'eval'), namespace)
        for item in declaration.keywords
    }
    app = Celery('budget-stop-test', broker='memory://', backend='cache+memory://')
    task = app.task(**retry_options)(namespace['failure_task'])
    return app, task, fail, mark, stage


@pytest.fixture
def registry(production, monkeypatch):
    module, client = production
    monkeypatch.setattr(studio_state, '_client', lambda: client)
    client.set(studio_state.JOB_PREFIX + TASK_ID, json.dumps({
        'task_id': TASK_ID, 'kind': 'render', 'state': 'PROGRESS', 'stage': 'director_qc',
        'audio_candidate_checkpoint': {'audio_sha256': 'a' * 64},
    }))
    return module, client


@pytest.mark.parametrize('pipeline', ['plan_video_pipeline', 'run_video_pipeline'])
@pytest.mark.parametrize('code', [
    'spend_day_limit', 'spend_month_limit', 'spend_not_initialized',
    'spend_request_already_reserved', 'spend_store_unavailable', 'spend_request_not_priced',
])
def test_budget_stop_persists_failure_once_without_celery_retry(registry, pipeline, code):
    _, client = registry
    error = SpendBlocked(code)
    app, task, fail, mark, stage = _worker(pipeline, TASK_ID, error)
    try:
        result = task.apply(task_id=TASK_ID, throw=False)
        assert result.state == 'FAILURE'
        assert str(result.result) == code
        fail.assert_called_once()
        mark.assert_called_once_with(TASK_ID, error)
        stage.assert_not_called()
        job = studio_state.get_job(TASK_ID)
        assert (job['state'], job['stage'], job['failure_stage']) == ('FAILURE', 'failed', 'director_qc')
        assert job['error'] == code and job['progress'] == 100
        assert job['audio_candidate_checkpoint'] == {'audio_sha256': 'a' * 64}
    finally:
        app.close()


@pytest.mark.parametrize('context', [
    {'attempts': 2},
    {'options': {'production_delivery': {'version': 1}}},
    {'full_rebuild_source_id': '22222222-2222-4222-8222-222222222222'},
    {'retry_dispatch_source_id': '22222222-2222-4222-8222-222222222222',
     'options': {'mode': 'production', 'format': 'landscape'}, 'duration_minutes': 3},
])
def test_budget_reason_survives_paid_and_retained_job_failure_paths(registry, context):
    error = SpendBlocked('spend_request_already_reserved')
    app, task, fail, mark, stage = _worker('run_video_pipeline', TASK_ID, error, **context)
    try:
        result = task.apply(task_id=TASK_ID, throw=False)
        assert result.state == 'FAILURE' and str(result.result) == 'spend_request_already_reserved'
        fail.assert_called_once()
        mark.assert_called_once_with(TASK_ID, error)
        stage.assert_not_called()
    finally:
        app.close()


@pytest.mark.parametrize('pipeline', ['plan_video_pipeline', 'run_video_pipeline'])
def test_other_declared_non_retryable_errors_are_terminal_too(registry, pipeline):
    source = next(node for node in TREE.body
                  if isinstance(node, ast.FunctionDef) and node.name == pipeline)
    excluded = next(item.value for item in source.decorator_list[0].keywords
                    if item.arg == 'dont_autoretry_for')
    for name in (item.id for item in excluded.elts):
        app, task, fail, mark, stage = _worker(pipeline, TASK_ID, ERRORS[name]('stopped'))
        try:
            assert task.apply(task_id=TASK_ID, throw=False).state == 'FAILURE'
            fail.assert_called_once()
            mark.assert_called_once()
            stage.assert_not_called()
        finally:
            app.close()


@pytest.mark.parametrize('pipeline,retries', [('plan_video_pipeline', 1), ('run_video_pipeline', 2)])
def test_ordinary_pre_media_retry_contract_is_preserved(registry, pipeline, retries):
    app, task, fail, mark, stage = _worker(pipeline, TASK_ID, RuntimeError('temporary storyboard error'))
    try:
        assert task.apply(task_id=TASK_ID, throw=False).state == 'FAILURE'
        assert fail.call_count == retries + 1
        assert stage.call_count == retries
        assert all(call.args[2] == 'plan_retry' for call in stage.call_args_list)
        mark.assert_called_once()
    finally:
        app.close()


def test_two_budget_stops_release_capacity_without_skipping_or_replaying_episodes(registry):
    module, client = registry
    profiles, connections = _parallel_channels((module, client))
    queued = module.dispatch_due_productions(profiles[:2], connections[:2], Mock(), now=1000)['queued']
    # Durable spending history is deliberately opaque to these handlers.
    client.hset(LEDGER_KEY, mapping={'reserved': '600000', 'request:uncertain': 'retained'})
    before_ledger = client.hgetall(LEDGER_KEY)
    before_states = {item['channel_id']: module.get_production_state(item['channel_id']) for item in queued}
    for item in queued:
        app, task, fail, mark, stage = _worker('run_video_pipeline', item['task_id'], SpendBlocked('spend_day_limit'))
        try:
            assert task.apply(task_id=item['task_id'], throw=False).state == 'FAILURE'
        finally:
            app.close()
    # Only the recurring server reconciler runs here; no Studio page sync.
    assert module.reconcile_active_production(now=1001) == 'channel_paused'
    assert client.get(module.ACTIVE_KEY) is None
    assert client.hgetall(LEDGER_KEY) == before_ledger
    for profile, connection in zip(profiles[:2], connections[:2]):
        state = module.get_production_state(profile['channel_id'])
        previous = before_states[profile['channel_id']]
        assert state['paused_reason'] == 'previous_render_failed'
        assert 'active_task_id' not in state
        assert all(state[key] == previous[key] for key in ('cursor', 'consumed_prefix', 'next_due', 'last_task_id'))
        assert module.reserve_due_production(profile, connection, now=1000 + 32 * 86400)['status'] == 'paused'
    assert module.reserve_due_production(profiles[2], connections[2], now=1002)['status'] == 'reserved'
