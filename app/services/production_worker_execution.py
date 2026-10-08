"""Bind a running production to its actual server replica before provider work.

An operator watchdog may close only an unpublished job whose exact replica
the platform reports REMOVED. Missing heartbeats and elapsed time are never
death evidence. No provider request, refund, old-task replay or upload occurs.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import logging

import redis
from billiard.exceptions import WorkerLostError

from app.services import studio_state as jobs, channel_production as production
from app.services.production_spend import SpendBlocked
from app.services.production_failures import classify_failure
from app.services.production_worker_loss import _client, UPLOAD_PREFIX, EXECUTION_LOCK_PREFIX

PREFIX = 'youtube_studio:worker_execution:v1:'
CURRENT_PREFIX, ATTEMPT_PREFIX, FENCE_PREFIX = (PREFIX + value for value in ('current:', 'attempt:', 'departed:'))
_ENV = {'project_id': 'RAILWAY_PROJECT_ID', 'environment_id': 'RAILWAY_ENVIRONMENT_ID',
        'service_id': 'RAILWAY_SERVICE_ID', 'deployment_id': 'RAILWAY_DEPLOYMENT_ID',
        'instance_id': 'RAILWAY_REPLICA_ID', 'git_sha': 'RAILWAY_GIT_COMMIT_SHA'}


class _RecordedExecution(Exception):
    pass


def observe_start(request):
    """A recovery witness must not create a new production availability gate.

    Existing task/provider admission still runs when witness storage is down.
    A positively recorded duplicate/departed execution is ignored without
    rewriting the active or completed job. No age-based replay is permitted.
    """
    from celery.exceptions import Ignore
    try:
        record_start(request)
    except _RecordedExecution:
        raise Ignore() from None
    except Exception:
        logging.getLogger(__name__).warning('Worker recovery witness unavailable; ordinary production admission remains active.')


def _require(value):
    if not value:
        raise SpendBlocked('worker_execution_unverified')


def _raw(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _identity():
    value = {key: os.environ.get(name) for key, name in _ENV.items()}
    _require(all(type(item) is str and re.fullmatch('[0-9a-f-]{36}' if key != 'git_sha' else '[0-9a-f]{40}', item)
                 for key, item in value.items()))
    return value


def record_start(request):
    if not os.environ.get('RAILWAY_DEPLOYMENT_ID'):
        return  # Development runs have no platform-removal recovery authority.
    task_id, attempt = request.id, request.retries
    _require(type(task_id) is str and jobs._TASK_ID_PATTERN.fullmatch(task_id)
        and type(attempt) is int and 0 <= attempt <= 2)
    client = _client();job_key = jobs.JOB_PREFIX + task_id
    current_key, attempt_key = CURRENT_PREFIX + task_id, ATTEMPT_PREFIX + task_id + ':' + str(attempt)
    with client.pipeline() as pipe:
        pipe.watch(job_key, current_key, attempt_key, FENCE_PREFIX + task_id)
        raw = pipe.get(job_key)
        _require(type(raw) is str and 0 < len(raw) <= 2_000_000)
        job = json.loads(raw)
        from app.services.content_plan_interruption import PREFIX as removed_completion
        pipe.watch(removed_completion + task_id)
        if pipe.exists(removed_completion + task_id):
            raise _RecordedExecution()
        if (job.get('kind') != 'render' or job.get('parent_id') is not None
                or (job.get('spec') or {}).get('production_scheduled') is not True):
            return
        if (jobs._watch_retained_delivery(pipe, task_id) or pipe.exists(FENCE_PREFIX + task_id)
                or job.get('state') in {'SUCCESS', 'FAILURE', 'CANCELLED'} or job.get('result')
                or pipe.exists(attempt_key)):
            raise _RecordedExecution()
        _require(job.get('state') in {'PENDING', 'STARTED', 'PROGRESS', 'RETRY'})
        previous = pipe.get(current_key)
        if attempt == 0:
            _require(previous is None)
        else:
            old_key = ATTEMPT_PREFIX + task_id + ':' + str(attempt - 1)
            pipe.watch(old_key)
            _require(type(previous) is str and pipe.get(old_key) == previous
                and json.loads(previous)['attempt'] == attempt - 1)
        record = {'version': 1, 'task_id': task_id, 'attempt': attempt,
                  'identity': _identity(), 'recorded_at': datetime.now(timezone.utc).isoformat()}
        encoded = _raw(record)
        pipe.multi();pipe.set(attempt_key, encoded, nx=True);pipe.set(current_key, encoded)
        _require(pipe.execute() == [True, True])


def candidates():
    """Read at most the two actual scheduled claims; never scan private jobs."""
    client = _client();active = client.get(production.ACTIVE_KEY)
    rows = []
    for claim in production._decode_active_claims(active) if active else []:
        task_id = claim['task_id'];job_raw = client.get(jobs.JOB_PREFIX + task_id)
        execution_raw = client.get(CURRENT_PREFIX + task_id)
        if not job_raw or not execution_raw:
            continue
        job, execution = json.loads(job_raw), json.loads(execution_raw)
        if (job.get('state') not in {'STARTED', 'PROGRESS'} or job.get('result')
                or job.get('stage') in {'plan_retry', 'upload', 'youtube_upload', 'youtube_release'}):
            continue
        rows.append({'task_id': task_id, 'job_sha256': _sha(job_raw),
                     'execution_sha256': _sha(execution_raw), 'execution': execution})
    return {'current_identity': _identity(), 'candidates': rows}


def record_removed_replica(candidate, observation):
    """Private operator entry; observation comes from authenticated Railway GET.

    The watchdog validates the platform response, retaining its exact digest.
    This is not exposed by any HTTP route. Atomic comparison plus the departed
    fence prevents a queued stale task from acquiring a new execution later.
    """
    try:
        _require(type(candidate) is dict and set(candidate) == {
            'task_id', 'job_sha256', 'execution_sha256', 'execution'})
        task_id, execution = candidate['task_id'], candidate['execution']
        _require(type(task_id) is str and jobs._TASK_ID_PATTERN.fullmatch(task_id)
            and type(execution) is dict and set(execution) == {'version', 'task_id', 'attempt', 'identity', 'recorded_at'}
            and execution['version'] == 1 and type(execution['version']) is int
            and execution['task_id'] == task_id and type(execution['attempt']) is int and 0 <= execution['attempt'] <= 2)
        current, owner = _identity(), execution['identity']
        _require(type(owner) is dict and set(owner) == set(current)
            and all(owner[key] == current[key] for key in ('project_id', 'environment_id', 'service_id'))
            and owner['instance_id'] != current['instance_id'])
        _require(type(observation) is dict and set(observation) == {
            'source', 'project_id', 'environment_id', 'service_id', 'deployment_id', 'instance_id',
            'instance_status', 'observed_at', 'response_sha256'})
        _require(observation['source'] == 'authenticated_railway_deployment_query'
            and observation['instance_status'] == 'REMOVED'
            and all(observation[key] == owner[key] for key in (
                'project_id', 'environment_id', 'service_id', 'deployment_id', 'instance_id'))
            and type(observation['response_sha256']) is str and re.fullmatch('[0-9a-f]{64}', observation['response_sha256']))
        observed = datetime.fromisoformat(observation['observed_at'])
        _require(observed.tzinfo is not None and 0 <= (datetime.now(timezone.utc) - observed).total_seconds() <= 120)
        client = _client();job_key = jobs.JOB_PREFIX + task_id;fence_key = FENCE_PREFIX + task_id
        current_key = CURRENT_PREFIX + task_id
        attempt_key = ATTEMPT_PREFIX + task_id + ':' + str(execution['attempt'])
        blockers = [prefix + task_id for prefix in (jobs.RENDER_CANCELLATION_PREFIX, UPLOAD_PREFIX,
            EXECUTION_LOCK_PREFIX, 'youtube_studio:source_publication_hold:v1:', 'youtube_studio:blocked_public_release:v1:')]
        with client.pipeline() as pipe:
            pipe.watch(job_key, current_key, attempt_key, fence_key, production.ACTIVE_KEY, *blockers)
            if jobs._watch_retained_delivery(pipe, task_id) or pipe.exists(fence_key, *blockers):
                return {'status': 'retained_or_delivery'}
            job_raw, current_raw = pipe.get(job_key), pipe.get(current_key)
            _require(type(job_raw) is str and _sha(job_raw) == candidate['job_sha256']
                and type(current_raw) is str and _sha(current_raw) == candidate['execution_sha256']
                and json.loads(current_raw) == execution and pipe.get(attempt_key) == current_raw
                and pipe.pttl(current_key) == pipe.pttl(attempt_key) == -1)
            job = json.loads(job_raw);spec = job.get('spec') or {}
            active = pipe.get(production.ACTIVE_KEY)
            _require(active and {'channel_id': spec.get('production_channel_id'), 'task_id': task_id}
                     in production._decode_active_claims(active))
            _require(job.get('task_id') == task_id and job.get('kind') == 'render'
                and job.get('parent_id') is None and spec.get('production_scheduled') is True
                and job.get('state') in {'STARTED', 'PROGRESS'}
                and not any(job.get(key) for key in ('result', 'youtube', 'youtube_automation', 'video_key', 'cancelled'))
                and job.get('stage') not in {'plan_retry', 'queued'})
            stage = job['stage'];error = WorkerLostError('Üretim sunucusu kapandı; tamamlanmayan deneme saklandı.')
            classification = classify_failure(error, stage)
            _require(classification['category'] == 'execution_interrupted')
            job.update(state='FAILURE', stage='failed', failure_stage=stage, progress=100,
                error=str(error), message=str(error), failure_classification=classification,
                updated_at=jobs._now_iso(), failure_observer='confirmed_removed_railway_replica')
            proof = {'version': 1, 'candidate': candidate, 'observation': observation,
                     'publish_eligible': False, 'retry_dispatched': False}
            pipe.multi();pipe.set(fence_key, _raw(proof), nx=True)
            pipe.setex(job_key, jobs.JOB_TTL_SECONDS, _raw(job))
            _require(pipe.execute() == [True, True])
        return {'status': 'failure_recorded', 'task_id': task_id}
    except redis.WatchError:
        return {'status': 'state_changed'}
    except Exception:
        return {'status': 'unverified'}
