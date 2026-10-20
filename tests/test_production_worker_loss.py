import ast
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from billiard.exceptions import WorkerLostError, TimeLimitExceeded
from celery.utils.dispatch import Signal
import pytest

from app.services import production_worker_loss as loss, production_quality_holds as holds
from app.services import studio_state as jobs, channel_production as production
from test_production_quality_holds import ready, commission
from test_native_story_correction import native
from test_full_video_rebuild import case, SOURCE, _all, _write
from test_production_credit_ledger import NOW, policy


class RenderSender:
    name = 'app.tasks.run_video_pipeline'
    acks_late = False


SENDER = RenderSender()


@pytest.fixture
def running(ready, monkeypatch):
    n = ready; commission(n)
    monkeypatch.setattr(loss, '_client', lambda: n.client)
    monkeypatch.setattr(production, '_redis', lambda: n.client)
    job = deepcopy(n.job)
    job.update(state='PROGRESS', stage='audio_qc', failure_stage=None, error=None)
    _write(n.client, jobs.JOB_PREFIX + SOURCE, job)
    n.client.hset(n.state_key, mapping={'active_task_id': SOURCE, 'dispatch_status': 'enqueued'})
    n.client.hdel(n.state_key, 'paused_reason')
    n.client.set(production.ACTIVE_KEY, json.dumps({'channel_id': n.profile['channel_id'], 'task_id': SOURCE}))
    return n


def _lost():
    return loss.record_worker_loss(SENDER, SOURCE, WorkerLostError('Worker exited prematurely: signal 9. Job: 12.'))


def test_confirmed_parent_notice_finishes_record_then_normal_reconciliation_and_hold_continue(running):
    n = running; before = _all(n.client)
    assert _lost()['status'] == 'failure_recorded'
    job = json.loads(n.client.get(jobs.JOB_PREFIX + SOURCE))
    assert job['state'] == 'FAILURE' and job['failure_stage'] == 'audio_qc'
    assert job['failure_classification']['category'] == 'execution_interrupted'
    assert job['failure_classification']['code'] == 'worker_process_lost'
    assert job['audio_candidate_checkpoint'] == n.job['audio_candidate_checkpoint']
    assert holds._reason(job) == 'worker_interrupted' and job['result'] is None
    assert 'signal 9' not in job['error']
    assert production.reconcile_active_production(now=NOW.timestamp()) == 'channel_paused'
    assert holds.hold_failed_episode(n.profile)['status'] == 'held_unpublished'
    after = _all(n.client)
    mutable = {jobs.JOB_PREFIX + SOURCE, production.ACTIVE_KEY, n.state_key,
               holds.HOLD_PREFIX + SOURCE, n.day_key, holds.HISTORY_KEY, holds.HISTORY_ANCHOR,
               jobs.QUALITY_HOLD_JOB_FENCE_PREFIX + SOURCE}
    assert {k:v for k,v in after.items() if k not in mutable} == {k:v for k,v in before.items() if k not in mutable}
    assert not n.client.exists(production.ACTIVE_KEY) and not n.case.calls
    assert not n.client.hget(n.state_key, 'paused_reason')
    record = json.loads(n.client.get(holds.HOLD_PREFIX + SOURCE))
    assert record['publish_eligible'] is False and record['retry_dispatched'] is False
    assert _lost()['status'] == 'retained_or_delivery' and _all(n.client) == after


@pytest.mark.parametrize('change', ['wrong_task', 'late_ack', 'missing_ack', 'timeout', 'ordinary_error', 'bad_id'])
def test_age_generic_failures_requeue_and_other_tasks_are_not_worker_loss_proof(running, change):
    sender, task, error = SENDER, SOURCE, WorkerLostError('lost')
    if change == 'wrong_task': sender = SimpleNamespace(name='app.publish_tasks.publish_video', acks_late=False)
    elif change == 'late_ack': sender = SimpleNamespace(name=SENDER.name, acks_late=True)
    elif change == 'missing_ack': sender = SimpleNamespace(name=SENDER.name)
    elif change == 'timeout': error = TimeLimitExceeded('No process-exit proof')
    elif change == 'ordinary_error': error = RuntimeError('WorkerLostError')
    elif change == 'bad_id': task = 'unverified'
    before = _all(running.client)
    assert loss.record_worker_loss(sender, task, error)['status'] == 'not_applicable'
    assert _all(running.client) == before


@pytest.mark.parametrize('prefix', [jobs.RENDER_CANCELLATION_PREFIX, jobs.QUALITY_HOLD_JOB_FENCE_PREFIX,
    jobs.RETAINED_DELIVERY_CHILD_PREFIX, loss.UPLOAD_PREFIX, loss.EXECUTION_LOCK_PREFIX,
    'youtube_studio:source_publication_hold:v1:', 'youtube_studio:blocked_public_release:v1:'])
def test_any_owner_retention_or_delivery_fence_blocks_even_a_real_worker_loss(running, prefix):
    n = running; n.client.set(prefix + SOURCE, 'occupied')
    before = _all(n.client)
    assert _lost()['status'] == 'retained_or_delivery' and _all(n.client) == before


@pytest.mark.parametrize('field,value', [('state', 'SUCCESS'), ('state', 'FAILURE'), ('state', 'CANCELLED'),
    ('kind', 'publish'), ('result', {'video_key': 'already-rendered'}), ('stage', 'upload'),
    ('stage', 'youtube_release'), ('stage', 'unknown'), ('stage', None), ('task_id', 'changed')])
def test_finished_delivering_or_unverified_job_remains_untouched(running, field, value):
    n = running
    job = json.loads(n.client.get(jobs.JOB_PREFIX + SOURCE));job[field] = value
    _write(n.client, jobs.JOB_PREFIX + SOURCE, job)
    before = _all(n.client)
    assert _lost()['status'] in {'not_running_unpublished_production', 'stage_unverified'}
    assert _all(n.client) == before


@pytest.mark.parametrize('race', ['completed_job', 'cancel', 'upload'])
def test_late_parent_notice_cannot_overwrite_a_concurrent_completion_or_owner_action(running, monkeypatch, race):
    n = running; factory = n.client.pipeline
    def pipeline(*args, **kwargs):
        pipe = factory(*args, **kwargs); execute = pipe.execute
        def racing_execute(*args, **kwargs):
            if race == 'completed_job':
                job = json.loads(n.client.get(jobs.JOB_PREFIX + SOURCE))
                job.update(state='SUCCESS', result={'video_key': 'preserved'})
                _write(n.client, jobs.JOB_PREFIX + SOURCE, job)
            else: n.client.set((jobs.RENDER_CANCELLATION_PREFIX if race == 'cancel' else loss.UPLOAD_PREFIX) + SOURCE, 'occupied')
            return execute(*args, **kwargs)
        pipe.execute = racing_execute
        return pipe
    monkeypatch.setattr(n.client, 'pipeline', pipeline)
    assert _lost()['status'] == 'state_changed'
    job = json.loads(n.client.get(jobs.JOB_PREFIX + SOURCE))
    assert job['state'] == ('SUCCESS' if race == 'completed_job' else 'PROGRESS')
    assert 'failure_classification' not in job


def test_actual_signal_declaration_records_loss_without_a_dashboard_request(running, monkeypatch):
    path = Path(__file__).resolve().parents[1] / 'app/production_tasks.py'
    node = next(n for n in ast.parse(path.read_text()).body
                if isinstance(n, ast.FunctionDef) and n.name == 'observe_render_worker_loss')
    signal = Signal(name='isolated-worker-parent-failure')
    ns = {'task_failure': signal}
    spy = Mock(wraps=loss.record_worker_loss)
    monkeypatch.setattr(loss, 'record_worker_loss', spy)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), ns)
    signal.send(sender=SENDER, task_id=SOURCE, exception=WorkerLostError('lost'))
    spy.assert_called_once()
    assert json.loads(running.client.get(jobs.JOB_PREFIX + SOURCE))['state'] == 'FAILURE'
