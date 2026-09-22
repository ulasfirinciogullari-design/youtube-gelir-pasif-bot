from copy import deepcopy
from datetime import datetime, timezone, timedelta
import json
from types import SimpleNamespace
from unittest.mock import Mock

from celery.exceptions import Ignore
import pytest

from app.services import production_worker_execution as execution
from app.services import production_spend_runtime as runtime, production_quality_holds as holds
from app.services import studio_state as jobs, channel_production as production
from test_production_worker_loss import running
from test_production_quality_holds import ready, commission
from test_native_story_correction import native
from test_full_video_rebuild import case, SOURCE, _all, _write
from test_production_credit_ledger import NOW, policy

IDENTITY = {key: f'{index:08x}-1111-4111-8111-111111111111'
            for index, key in enumerate(execution._ENV, 1)}
IDENTITY['git_sha'] = 'a' * 40
NEW_INSTANCE = 'ffffffff-1111-4111-8111-111111111111'
REQUEST = SimpleNamespace(id=SOURCE, retries=0)


@pytest.fixture
def bound(running, monkeypatch):
    n = running
    for key, name in execution._ENV.items():
        monkeypatch.setenv(name, IDENTITY[key])
    monkeypatch.setattr(execution, '_client', lambda: n.client)
    execution.record_start(REQUEST)
    return n


def departed(n, monkeypatch):
    candidate = execution.candidates()['candidates'][0]
    monkeypatch.setenv(execution._ENV['instance_id'], NEW_INSTANCE)
    observation = {'source': 'authenticated_railway_deployment_query',
        **{k: IDENTITY[k] for k in ('project_id', 'environment_id', 'service_id', 'deployment_id', 'instance_id')},
        'instance_status': 'REMOVED', 'observed_at': datetime.now(timezone.utc).isoformat(),
        'response_sha256': 'b' * 64}
    return candidate, observation


def test_confirmed_departure_retains_attempt_then_normal_scheduler_continues(bound, monkeypatch):
    n = bound; candidate, proof = departed(n, monkeypatch); before = _all(n.client)
    assert execution.record_removed_replica(candidate, proof)['status'] == 'failure_recorded'
    job = json.loads(n.client.get(jobs.JOB_PREFIX + SOURCE))
    assert job['failure_stage'] == 'audio_qc'
    assert job['failure_classification']['category'] == 'execution_interrupted'
    assert job['audio_candidate_checkpoint'] == n.job['audio_candidate_checkpoint']
    assert job['result'] is None
    assert production.reconcile_active_production(now=NOW.timestamp()) == 'channel_paused'
    assert holds.hold_failed_episode(n.profile)['status'] == 'held_unpublished'
    assert not n.client.hget(n.state_key, 'paused_reason')
    mutable = {jobs.JOB_PREFIX + SOURCE, production.ACTIVE_KEY, n.state_key,
        holds.HOLD_PREFIX + SOURCE, n.day_key, holds.HISTORY_KEY, holds.HISTORY_ANCHOR,
        jobs.QUALITY_HOLD_JOB_FENCE_PREFIX + SOURCE, execution.FENCE_PREFIX + SOURCE}
    after = _all(n.client)
    assert {k:v for k,v in after.items() if k not in mutable} == {k:v for k,v in before.items() if k not in mutable}
    assert n.client.pttl(execution.FENCE_PREFIX + SOURCE) == -1
    assert not n.case.calls  # No transport, refund, publish or task enqueue.
    assert execution.record_removed_replica(candidate, proof)['status'] == 'retained_or_delivery'
    with pytest.raises(Ignore): execution.observe_start(REQUEST)
    assert _all(n.client) == after


@pytest.mark.parametrize('change', ['same_replica', 'different_project', 'different_service', 'different_environment',
    'wrong_deployment', 'wrong_instance', 'not_removed', 'old', 'future', 'naive', 'wrong_source', 'missing_digest'])
def test_elapsed_time_or_unbound_platform_observation_cannot_release_work(bound, monkeypatch, change):
    candidate, proof = departed(bound, monkeypatch)
    if change == 'same_replica': monkeypatch.setenv(execution._ENV['instance_id'], IDENTITY['instance_id'])
    elif change.startswith('different_'):
        monkeypatch.setenv(execution._ENV[change.removeprefix('different_') + '_id'], NEW_INSTANCE)
    elif change in {'wrong_deployment', 'wrong_instance'}: proof[change.removeprefix('wrong_') + '_id'] = NEW_INSTANCE
    elif change == 'not_removed': proof['instance_status'] = 'CRASHED'
    elif change in {'old', 'future', 'naive'}:
        proof['observed_at'] = (datetime.now(timezone.utc) + timedelta(seconds=300 if change == 'future' else -300)).isoformat()
        if change == 'naive': proof['observed_at'] = '2026-09-22T00:00:00'
    elif change == 'wrong_source': proof['source'] = 'heartbeat_timeout'
    else: proof.pop('response_sha256')
    before = _all(bound.client)
    assert execution.record_removed_replica(candidate, proof)['status'] == 'unverified'
    assert _all(bound.client) == before


@pytest.mark.parametrize('change', ['completed', 'progress_changed', 'claim_missing', 'binding_changed', 'binding_expired',
    'cancel', 'upload', 'release_lock', 'retained', 'publication_stage'])
def test_late_observation_never_overwrites_new_work_or_publication(bound, monkeypatch, change):
    n = bound; candidate, proof = departed(n, monkeypatch)
    if change in {'completed', 'progress_changed', 'publication_stage'}:
        job = json.loads(n.client.get(jobs.JOB_PREFIX + SOURCE))
        if change == 'completed': job.update(state='SUCCESS', result={'video_key': 'preserved'})
        elif change == 'publication_stage': job['stage'] = 'youtube_release'
        else: job['progress'] = 49
        _write(n.client, jobs.JOB_PREFIX + SOURCE, job)
        if change == 'publication_stage': candidate['job_sha256'] = execution._sha(n.client.get(jobs.JOB_PREFIX + SOURCE))
    elif change == 'claim_missing': n.client.delete(production.ACTIVE_KEY)
    elif change == 'binding_changed': n.client.set(execution.CURRENT_PREFIX + SOURCE, '{}')
    elif change == 'binding_expired': n.client.expire(execution.CURRENT_PREFIX + SOURCE, 300)
    else:
        prefix = {'cancel': jobs.RENDER_CANCELLATION_PREFIX, 'upload': execution.UPLOAD_PREFIX,
            'release_lock': execution.EXECUTION_LOCK_PREFIX, 'retained': jobs.QUALITY_HOLD_JOB_FENCE_PREFIX}[change]
        n.client.set(prefix + SOURCE, 'occupied')
    before = _all(n.client)
    assert execution.record_removed_replica(candidate, proof)['status'] in {'unverified', 'retained_or_delivery'}
    assert _all(n.client) == before


def test_atomic_completion_race_is_not_overwritten(bound, monkeypatch):
    n = bound; candidate, proof = departed(n, monkeypatch); factory = n.client.pipeline
    def racing(*args, **kwargs):
        pipe = factory(*args, **kwargs); execute = pipe.execute
        def finish(*args, **kwargs):
            job = json.loads(n.client.get(jobs.JOB_PREFIX + SOURCE))
            job.update(state='SUCCESS', result={'video_key': 'preserved'})
            _write(n.client, jobs.JOB_PREFIX + SOURCE, job)
            return execute(*args, **kwargs)
        pipe.execute = finish
        return pipe
    monkeypatch.setattr(n.client, 'pipeline', racing)
    assert execution.record_removed_replica(candidate, proof)['status'] == 'state_changed'
    assert json.loads(n.client.get(jobs.JOB_PREFIX + SOURCE))['state'] == 'SUCCESS'
    assert not n.client.exists(execution.FENCE_PREFIX + SOURCE)


def test_named_pipeline_is_bound_before_work_and_duplicate_is_ignored(bound):
    calls = []
    previous = runtime._TASK_ID.get()
    @runtime.spending_task
    def run_video_pipeline(self): calls.append(runtime._TASK_ID.get())
    with pytest.raises(Ignore): run_video_pipeline(SimpleNamespace(request=REQUEST))
    assert calls == [] and runtime._TASK_ID.get() == previous


def test_witness_outage_does_not_disable_existing_admission(monkeypatch):
    monkeypatch.setattr(execution, 'record_start', Mock(side_effect=RuntimeError('simulated storage outage')))
    assert execution.observe_start(REQUEST) is None


def test_next_real_celery_attempt_preserves_both_execution_records(bound):
    previous = bound.client.get(execution.CURRENT_PREFIX + SOURCE)
    execution.record_start(SimpleNamespace(id=SOURCE, retries=1))
    assert bound.client.get(execution.ATTEMPT_PREFIX + SOURCE + ':0') == previous
    current = bound.client.get(execution.CURRENT_PREFIX + SOURCE)
    assert json.loads(current)['attempt'] == 1
    assert bound.client.get(execution.ATTEMPT_PREFIX + SOURCE + ':1') == current


def test_missing_start_witness_is_not_reconstructed_from_age(bound):
    bound.client.delete(execution.CURRENT_PREFIX + SOURCE)
    before = _all(bound.client)
    assert execution.candidates()['candidates'] == []
    assert _all(bound.client) == before
