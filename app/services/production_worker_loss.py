"""Persist a confirmed child-process loss; no age-based unlocking or replay."""
import json
import re

import redis
from billiard.exceptions import WorkerLostError

from app.config import settings
from app.services import studio_state as jobs
from app.services.production_failures import classify_failure
from app.services.youtube_publish_state import UPLOAD_PREFIX, EXECUTION_LOCK_PREFIX


def _client():
    return redis.Redis.from_url(settings.redis_url, decode_responses=True,
        socket_connect_timeout=2, socket_timeout=2, retry_on_timeout=False)


def record_worker_loss(sender, task_id, exception):
    """Celery's surviving parent calls this only after its child actually exits.

    A whole-host failure without such evidence remains unresolved. Manual
    cancellation, late acknowledgements/requeue, publication and terminal jobs
    cannot use this path. The normal minute tick owns subsequent scheduling.
    """
    if (getattr(sender, 'name', None) != 'app.tasks.run_video_pipeline'
            or getattr(sender, 'acks_late', None) is not False
            or type(exception) is not WorkerLostError
            or type(task_id) is not str or not jobs._TASK_ID_PATTERN.fullmatch(task_id)):
        return {'status': 'not_applicable'}
    try:
        client = _client()
        key = jobs.JOB_PREFIX + task_id
        blockers = [prefix + task_id for prefix in (
            jobs.RENDER_CANCELLATION_PREFIX, UPLOAD_PREFIX, EXECUTION_LOCK_PREFIX,
            'youtube_studio:source_publication_hold:v1:', 'youtube_studio:blocked_public_release:v1:',
        )]
        with client.pipeline() as pipe:
            pipe.watch(key, *blockers)
            if jobs._watch_retained_delivery(pipe, task_id) or pipe.exists(*blockers):
                return {'status': 'retained_or_delivery'}
            raw = pipe.get(key)
            if type(raw) is not str or not 0 < len(raw) <= 2_000_000:
                return {'status': 'record_unavailable'}
            job = json.loads(raw)
            if (type(job) is not dict or job.get('task_id') != task_id or job.get('kind') != 'render'
                    or job.get('state') not in {'PENDING', 'STARTED', 'PROGRESS', 'RETRY'}
                    or type(job.get('spec')) is not dict or job['spec'].get('production_scheduled') is not True
                    or any(job.get(k) for k in ('result', 'youtube', 'youtube_automation', 'video_key', 'cancelled'))):
                return {'status': 'not_running_unpublished_production'}
            stage = job.get('stage')
            if type(stage) is not str or not re.fullmatch('[a-z0-9_]{1,64}', stage):
                return {'status': 'stage_unverified'}
            error = WorkerLostError('Üretim işlemi beklenmedik biçimde kapandı; tamamlanmayan deneme saklandı.')
            classification = classify_failure(error, stage)
            if classification['category'] != 'execution_interrupted':
                return {'status': 'stage_unverified'}
            job.update(state='FAILURE', stage='failed', failure_stage=stage, progress=100,
                       message='Üretim işlemi beklenmedik biçimde kapandı.', error=str(error),
                       failure_classification=classification, updated_at=jobs._now_iso(),
                       failure_observer='celery_worker_parent')
            pipe.multi()
            pipe.setex(key, jobs.JOB_TTL_SECONDS, json.dumps(job, ensure_ascii=False))
            if pipe.execute() != [True]:
                return {'status': 'recording_uncertain'}
        return {'status': 'failure_recorded'}
    except redis.WatchError:
        return {'status': 'state_changed'}
    except Exception:
        return {'status': 'recording_unavailable'}
