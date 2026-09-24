"""Bounded continuations reuse the exact episode's paid receipts and assets."""
import json
import os
import time
from types import SimpleNamespace
from uuid import uuid5, NAMESPACE_URL

from app.services import content_plan as plan, studio_state as jobs
from app.services.framecase_cadence import CHANNEL_ID

PREFIX = 'youtube_studio:framecase_recovery:v1:'
# Ordinary transient retries remain six. A verified changed build may resume
# up to six additional corrections on the SAME root and original paid caps.
# The counters, paid intents and old continuation records are never reset.
MAX_TRANSIENT_CONTINUATIONS = 6
MAX_CONTINUATIONS = 12
FIX_REQUIRED = frozenset({'framecase_audio_timing_rejected', 'framecase_audio_transcript_rejected',
    'framecase_audio_prosody_rejected', 'framecase_scene_duration_invalid',
    'framecase_final_render_rejected', 'framecase_final_timing_rejected',
    'framecase_authored_scene_quality_rejected', 'framecase_FalVideoPolicyError',
    'framecase_review_window_invalid', 'framecase_visual_quality_exhausted',
    'framecase_art_direction_revision_required', 'framecase_creative_quality_rejected'})


def schedule(source):
    spec = source.get('spec') or {}
    if spec.get('production_channel_id') != CHANNEL_ID or spec.get('framecase_animation') is not True:
        return 'not_framecase'
    if (source.get('publication_hold') or source.get('owner_cancellation')
            or spec.get('publish_after_render') is False):
        return 'held_by_owner'
    client = plan._client()
    document = plan.read(CHANNEL_ID, client=client)
    if document is not None and document['enabled'] is False:
        return 'plan_paused'
    if source.get('state') != 'FAILURE':
        return 'working_or_waiting'
    code = source.get('framecase_failure_code', '')
    if (code in FIX_REQUIRED and source.get('framecase_failed_build')
            == os.environ.get('RAILWAY_GIT_COMMIT_SHA', 'local')):
        return 'waiting_for_pipeline_correction'
    if (not code or any(marker in code for marker in (
            'outcome_unverified', 'outcome_unknown',
            'binding_changed', 'generic_recovery_forbidden', 'pipeline_unverified'))):
        return 'held_for_verification'
    task = source['task_id']
    attempt = int(source.get('framecase_resume_attempt') or 0) + 1
    if attempt > MAX_CONTINUATIONS:
        return 'continuation_limit_reached'
    fixed_build = (code in FIX_REQUIRED and source.get('framecase_failed_build')
        and source.get('framecase_failed_build') != os.environ.get('RAILWAY_GIT_COMMIT_SHA', 'local'))
    if attempt > MAX_TRANSIENT_CONTINUATIONS and not fixed_build:
        return 'continuation_limit_reached'
    if not fixed_build and time.time() < float(source.get('framecase_retry_at') or 0):
        return 'retry_wait'
    operation = str(uuid5(NAMESPACE_URL, f'framecase-resume:{task}:{attempt}'))
    key = PREFIX + operation
    with client.pipeline() as pipe:
        pipe.watch(key, jobs.JOB_PREFIX + task, plan.PLAN_PREFIX + CHANNEL_ID)
        current = plan._object(pipe.get(jobs.JOB_PREFIX + task))
        document = plan.read(CHANNEL_ID, client=pipe)
        if document is not None and document['enabled'] is False:
            return 'plan_paused'
        if current != source or pipe.exists(key):
            return 'continuation_preparing_or_uncertain'
        record = {'version': 1, 'source_task_id': task, 'operation': operation,
            'attempt': attempt, 'spec_sha256': plan._sha(spec)}
        if attempt > MAX_TRANSIENT_CONTINUATIONS:
            record['correction'] = {'failure_code': code,
                'failed_build': source['framecase_failed_build'],
                'corrected_build': os.environ.get('RAILWAY_GIT_COMMIT_SHA', 'local')}
        pipe.multi(); pipe.set(key, plan._raw(record), nx=True)
        plan._require(pipe.execute() == [True], 'framecase_recovery_uncertain')
    from app.production_tasks import continue_framecase_episode
    try:
        continue_framecase_episode.apply_async(args=(task, attempt), task_id=operation, retry=False)
    except Exception:
        return 'continuation_dispatch_uncertain'
    return 'continuation_queued'


def run(celery_task, source_id, attempt):
    from app.services import framecase_pipeline as pipeline, production_spend_runtime as spending
    client = plan._client(); operation = str(celery_task.request.id)
    record = json.loads(client.get(PREFIX + operation) or '{}')
    plan._require(record.get('operation') == operation and record.get('source_task_id') == source_id
        and type(attempt) is int and 1 <= attempt <= MAX_CONTINUATIONS and record.get('attempt') == attempt,
        'framecase_recovery_unverified')
    source = jobs.get_job(source_id); spec = source.get('spec') or {}
    if (source.get('publication_hold') or source.get('owner_cancellation')
            or spec.get('publish_after_render') is False):
        return {'status': 'held_by_owner'}
    document = plan.read(CHANNEL_ID, client=client)
    if document is not None and document['enabled'] is False:
        return {'status': 'plan_paused'}
    plan._require(source.get('state') == 'FAILURE' and record['spec_sha256'] == plan._sha(spec)
        and spec.get('production_channel_id') == CHANNEL_ID
        and int(source.get('framecase_resume_attempt') or 0) == attempt - 1,
        'framecase_recovery_unverified')
    if attempt > MAX_TRANSIENT_CONTINUATIONS:
        correction = record.get('correction') or {}
        plan._require(correction.get('failure_code') in FIX_REQUIRED
            and correction.get('failure_code') == source.get('framecase_failure_code')
            and correction.get('failed_build') == source.get('framecase_failed_build')
            and correction.get('corrected_build') == os.environ.get('RAILWAY_GIT_COMMIT_SHA', 'local')
            and correction.get('failed_build') != correction.get('corrected_build'),
            'framecase_recovery_unverified')
    if not client.set(PREFIX + 'execution:' + operation, 'started', nx=True):
        return {'status': 'already_executed'}
    options = {k: v for k, v in spec.items() if k not in ('topic', 'duration_minutes', 'language', 'channel_id')}
    pipeline.authorize(source_id, spec['topic'], spec['duration_minutes'], spec['language'], spec['channel_id'], options)
    jobs.update_job(source_id, framecase_resume_attempt=attempt)
    # The continuation performs this SAME frozen render, with the same funding
    # lineage. It cannot create a new free provider or daily-publication budget.
    context = spending._TASK_ID.set(source_id)
    adapter = SimpleNamespace(request=SimpleNamespace(id=source_id),
        update_state=lambda **kwargs: celery_task.update_state(**kwargs))
    try:
        return pipeline.run(adapter, spec['topic'], spec['duration_minutes'], spec['language'], spec['channel_id'], options)
    finally:
        spending._TASK_ID.reset(context)
