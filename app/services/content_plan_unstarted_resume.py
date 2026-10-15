"""Resume an accepted-output child conclusively rejected before execution.

The old twelve-node view rejected node thirteen before the execution fence,
worker witness or task body. Only that exact terminal, entirely unstarted
retained-media job can be sent once more. Original terminal evidence is archived
before dispatch; uncertainty and any evidence of execution keep the fence shut.
"""
from datetime import datetime, timezone
from uuid import uuid5, NAMESPACE_URL

from app.services import content_plan as plan, studio_state as jobs
from app.services.included_stock_pool import _local_transaction

PREFIX = plan.PREFIX + 'unstarted_lineage_resume:v1:'


@_local_transaction
def _reserve(source, client, *, observe_only=False):
    from app.services import content_plan_retained_completion as retained
    from app.services import production_worker_execution as worker
    from app.services.source_publication_hold import HOLD_PREFIX
    from app.services.youtube_publish_state import UPLOAD_PREFIX

    task = plan._id(source['task_id']); parent = plan._id(source['parent_id'])
    plan._require(task == str(uuid5(NAMESPACE_URL, 'owner-plan-retained-completion-child:v1:' + parent)))
    old, root, proof = retained.checked(client, parent, claimed=True)
    claim = retained._claim(parent, root, proof)
    plan._require(proof.get('accepted_output_of') and old.get('retry_child_task_id') == task
        and source['spec'] == old['spec']
        and client.get(retained.DISPATCH+parent) == client.get(retained._root_key(root, proof)) == plan._raw(claim)
        and client.get(retained.EXECUTION+parent) == retained.operation(parent))
    key = PREFIX+task; job_key = jobs.JOB_PREFIX+task; terminal_key = 'celery-task-meta-'+task
    blocked = [jobs.RETRY_CHILD_EXECUTION_PREFIX+task, worker.CURRENT_PREFIX+task,
        HOLD_PREFIX+task, UPLOAD_PREFIX+task, jobs.RENDER_CANCELLATION_PREFIX+task,
        jobs.EXTERNAL_EPISODE_LEAF_PREFIX+task, jobs.QUALITY_HOLD_JOB_FENCE_PREFIX+task,
        jobs.PAID_CREATE_BUDGET_PREFIX+task, jobs.REPAIR_CHECKPOINT_PREFIX+task]
    blocked += [plan.EXECUTION_PREFIX+task+':'+str(attempt) for attempt in range(3)]
    blocked += [worker.ATTEMPT_PREFIX+task+':'+str(attempt) for attempt in range(3)]
    retry_key = jobs.RETRY_CHILD_CLAIM_PREFIX+task
    with client.pipeline() as pipe:
        pipe.watch(key, job_key, terminal_key, retry_key, *blocked)
        if pipe.get(key): return None
        raw = pipe.get(job_key); plan._require(plan._object(raw) == source)
        terminal_raw = pipe.get(terminal_key); terminal = plan._object(terminal_raw)
        failure = terminal.get('result') or {}; trace = terminal.get('traceback') or ''
        plan._require(source.get('state') == 'PENDING' and source.get('kind') == 'render'
            and source.get('stage') == 'queued' and source.get('progress') == 0
            and all(source.get(k) is None for k in ('result', 'error', 'failure_stage', 'paid_create_slots_used',
                'audio_candidate_checkpoint', 'generated_asset_candidates', 'repair_checkpoint',
                'retry_child_task_id', 'publication_hold', 'owner_cancellation'))
            and terminal.get('task_id') == task and terminal.get('status') == 'FAILURE'
            and failure.get('exc_type') == 'ContentPlanError' and failure.get('exc_module') == 'app.services.content_plan'
            and failure.get('exc_message') == ['plan_lineage_invalid']
            and all(marker in trace for marker in ('production_spend_runtime.py', 'in wrapped\n',
                'in observe_execution\n', 'in _leaf\n'))
            and 'in run_video_pipeline\n' not in trace)
        completed = datetime.fromisoformat(terminal['date_done'])
        plan._require(completed.tzinfo is not None and completed <= datetime.now(timezone.utc)
            and not pipe.exists(*blocked))
        retry = pipe.hgetall(retry_key)
        plan._require(retry.get('source_task_id') == parent and type(retry.get('token')) is str and len(retry['token']) >= 16)
        archive = {'version': 1, 'task_id': task, 'source_task_id': parent, 'root_task_id': root,
            'source_job': raw, 'terminal': terminal_raw, 'proof_sha256': plan._sha(proof),
            'created_at': datetime.now(timezone.utc).isoformat(), 'no_execution_observed': True}
        if observe_only:
            pipe.multi(); pipe.ping(); plan._require(pipe.execute() == [True])
            return archive
        pipe.multi(); pipe.set(key, plan._raw(archive), nx=True)
        plan._require(pipe.execute() == [True])
    return archive


def schedule(source, enqueue, *, client):
    if source.get('state') != 'PENDING' or not source.get('parent_id'):
        return None
    raw = client.get('celery-task-meta-'+source['task_id'])
    if not raw: return None
    terminal = plan._object(raw)
    if terminal.get('status') != 'FAILURE' or (terminal.get('result') or {}).get('exc_message') != ['plan_lineage_invalid']:
        return None
    archive = _reserve(source, client)
    if archive is None: return 'unstarted_resume_reserved'
    spec = source['spec']; options = {k:v for k,v in spec.items() if k not in {'topic','duration_minutes','language','channel_id'}}
    status = 'unstarted_resume_enqueued'
    try:
        enqueue(args=(spec['topic'], spec['duration_minutes'], spec['language'], spec['channel_id'],
            options, None, source['parent_id']), task_id=source['task_id'], retry=False)
    except Exception:
        status = 'unstarted_resume_uncertain'
    # The first immutable claim protects against a lost publish/write reply.
    client.set(PREFIX+'delivery:'+source['task_id'], plan._raw({'status': status}), nx=True)
    return status
