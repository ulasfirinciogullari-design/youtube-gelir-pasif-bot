"""One continuation after the observed long editorial grammar HTTP 400.

The complete encrypted provider responses must prove a successful research
result and an explicit rejected editorial request, with no other attempt or
media intent. The original research result is reused by its request identity;
only the new compatible editorial envelope may submit a distinct request.
"""
import hashlib
import json
import secrets
from uuid import uuid5, NAMESPACE_URL

from app.services import content_plan as plan, content_plan_research_resume as pre, studio_state as jobs

PREFIX = plan.PREFIX + 'model_resume:v1:'
DISPATCH, EXECUTION = PREFIX + 'dispatch:', PREFIX + 'execution:'
ERROR = 'commissioning_reasoning_response_unverified'


def operation(source):
    return str(uuid5(NAMESPACE_URL, 'owner-plan-model-resume:v1:' + source))


def registered(source):
    return bool(plan._client().exists(DISPATCH + plan._id(source)))


def eligible(source):
    return bool(source.get('parent_id') and source.get('state') == 'FAILURE'
        and source.get('failure_stage') == 'director_qc' and source.get('error') == ERROR
        and (source.get('spec') or {}).get('duration_minutes') == 3
        and (source.get('spec') or {}).get('content_plan_item_id') and not source.get('retry_child_task_id'))


def _responses(client, root):
    from app.services import commissioning_reasoning as native, production_included_router as included
    keys = client.smembers(native.PREFIX + 'lineage:' + root)
    plan._require(len(keys) == 2)
    spec = plan._object(client.get(jobs.JOB_PREFIX + root))['spec']
    context = {'lineage_id': root, 'kind': 'long', 'channel_id': spec['production_channel_id'],
               'connection_id': spec['production_connection_id']}
    roles = set(); proofs = {}
    for identity in keys:
        request = plan._object(client.get(native.PREFIX + 'request:' + identity))
        response = plan._object(client.get(native.PREFIX + 'response:' + identity))
        plan._require(request['request_sha256'] == response['request_sha256'] == identity
            and request['context'] == context and request.get('provider') == 'gemini'
            and request.get('model') == native.MODEL and request.get('version') == response.get('version') == 1
            and request['purpose'] in {'research', 'editorial'} and request['purpose'] not in roles)
        body = included._cipher().decrypt(response['encrypted_response'].encode())
        plan._require(hashlib.sha256(body).hexdigest() == response['response_sha256'])
        value = json.loads(body)
        if request['purpose'] == 'editorial':
            error = value.get('error') or {}
            plan._require(response['http_status'] == 400 and error.get('code') == 400
                and error.get('status') == 'INVALID_ARGUMENT' and not value.get('candidates')
                and not value.get('usageMetadata'))
        else:
            candidates = value.get('candidates') or []
            plan._require(response['http_status'] == 200 and len(candidates) == 1
                and candidates[0].get('finishReason') == 'STOP' and value.get('usageMetadata'))
        roles.add(request['purpose'])
        proofs[identity] = {'request_sha256': plan._sha(request), 'response_sha256': plan._sha(response)}
    pre._provider_free(client, root, known_reasoning=keys)
    return proofs


def checked(client, task, *, claimed=False):
    from app.services.content_plan_local_resume import _claim
    from app.services.source_publication_hold import HOLD_PREFIX
    from app.services.youtube_publish_state import UPLOAD_PREFIX
    source = plan._object(client.get(jobs.JOB_PREFIX + plan._id(task)))
    spec = source.get('spec') or {}; root_id = plan._id(source.get('parent_id'))
    root = plan._object(client.get(jobs.JOB_PREFIX + root_id))
    plan._require(source.get('task_id') == task and source.get('kind') == 'render'
        and source.get('state') == 'FAILURE' and source.get('failure_stage') == 'director_qc'
        and source.get('error') == ERROR and spec.get('duration_minutes') == 3
        and spec.get('mode') == 'production' and spec.get('format') == 'landscape'
        and root.get('parent_id') is None and root.get('retry_child_task_id') == task
        and root.get('spec') == spec and root.get('state') == 'FAILURE')
    for job in (root, source):
        job_id = job['task_id']
        plan._require(all(job.get(k) is None for k in pre.MEDIA_FIELDS)
            and not job.get('publication_hold') and not job.get('owner_cancellation')
            and type(job.get('paid_create_slots_used')) is int and job['paid_create_slots_used'] == 0
            and job.get('preview_total_paid_create_cap') == 32
            and client.hgetall(jobs.PAID_CREATE_BUDGET_PREFIX + job_id) == {'cap': '32', 'used': '0'}
            and not client.exists(*(p + job_id for p in (HOLD_PREFIX, UPLOAD_PREFIX,
                jobs.RENDER_CANCELLATION_PREFIX, jobs.EXTERNAL_EPISODE_LEAF_PREFIX,
                jobs.QUALITY_HOLD_JOB_FENCE_PREFIX, jobs.REPAIR_CHECKPOINT_PREFIX))))
    if not claimed:
        plan._require(not any(source.get(k) for k in ('retry_child_task_id', 'retry_claimed', 'repair_claimed'))
            and not client.exists(jobs.RETRY_DISPATCH_PREFIX + task))
    _claim(client, root_id, task)
    previous = plan._object(client.get(pre.DISPATCH + root_id))
    plan._require(previous == {'version': 1, 'task_id': pre.operation(root_id),
        'source_task_id': root_id, 'source_sha256': pre.fingerprint(root)}
        and client.get(pre.EXECUTION + root_id) == pre.operation(root_id))
    terminal = plan._object(client.get('celery-task-meta-' + task)); result = terminal.get('result') or {}
    plan._require(terminal.get('task_id') == task and terminal.get('status') == 'FAILURE'
        and result.get('exc_type') == 'SpendBlocked' and result.get('exc_message') == [ERROR]
        and ', in _run_director\n' in (terminal.get('traceback') or ''))
    dispatch = plan._object(client.get(plan.DISPATCH_PREFIX + plan._id(spec.get('content_plan_item_id'))))
    plan._require(dispatch['task_id'] == root_id and plan.dispatch_spec_matches(dispatch, spec)
        and plan._active(client).get(dispatch['channel_id']) == dispatch['item']['id'])
    profile = plan._object(client.get(plan.production.PROFILE_PREFIX + dispatch['channel_id']))
    plan.publication_series(plan._leaf(client, dispatch), profile, client=client)
    channel = plan._object(client.get(plan.production.OAUTH_CHANNEL_PREFIX + dispatch['channel_id']))
    plan._require(profile.get('production_enabled') is True
        and channel.get('connection_id') == dispatch['connection_id'] and channel.get('requires_reconnect') is not True
        and client.exists(plan.production.OAUTH_CREDENTIAL_PREFIX + dispatch['channel_id']))
    return source, _responses(client, root_id)


def schedule(source, enqueue, *, client=None):
    client = client or plan._client(); task = source['task_id']
    if client.exists(DISPATCH + task):
        return 'model_resume_reserved'
    source, responses = checked(client, task)
    claim = {'version': 1, 'task_id': operation(task), 'source_task_id': task,
        'source_sha256': pre.fingerprint(source), 'provider_records': responses}
    if not client.set(DISPATCH + task, plan._raw(claim), nx=True):
        return 'model_resume_reserved'
    try:
        enqueue(args=(task,), task_id=claim['task_id'], retry=False)
    except Exception:
        return 'model_resume_uncertain'
    return 'model_resume_preparing'


def verify_child(task, source_id, spec, *, client=None):
    from app.services.content_plan_local_resume import _claim
    client = client or plan._client(); source, responses = checked(client, source_id, claimed=True)
    claim = plan._object(client.get(DISPATCH + source_id))
    child = plan._object(client.get(jobs.JOB_PREFIX + task))
    plan._require(claim == {'version': 1, 'task_id': operation(source_id), 'source_task_id': source_id,
        'source_sha256': pre.fingerprint(source), 'provider_records': responses}
        and client.get(EXECUTION + source_id) == operation(source_id)
        and child.get('task_id') == task and child.get('kind') == 'render' and child.get('parent_id') == source_id
        and child.get('spec') == source['spec'] == spec and source.get('retry_child_task_id') == task
        and child.get('state') in {'PENDING', 'STARTED', 'PROGRESS'}
        and all(child.get(k) is None for k in pre.MEDIA_FIELDS))
    _claim(client, source_id, task)
    return source


def run(source_id, operation_id):
    from app.tasks import run_video_pipeline
    client = plan._client(); claim = plan._object(client.get(DISPATCH + source_id))
    plan._require(claim['task_id'] == operation_id == operation(source_id))
    if not client.set(EXECUTION + source_id, operation_id, nx=True):
        return {'status': 'already_started'}
    source, responses = checked(client, source_id)
    plan._require(pre.fingerprint(source) == claim['source_sha256'] and responses == claim['provider_records'])
    child = str(uuid5(NAMESPACE_URL, 'owner-plan-model-child:v1:' + source_id)); token = secrets.token_urlsafe(32)
    plan._require(jobs.claim_retry_dispatch(source_id, child, token, allow_repair=False).get('claimed') is True)
    jobs.create_job(child, source['spec'], kind='render', parent_id=source_id)
    spec = source['spec']; options = {k: v for k, v in spec.items()
        if k not in {'topic', 'duration_minutes', 'language', 'channel_id'}}
    try:
        run_video_pipeline.apply_async(args=(spec['topic'], 3, spec['language'], spec['channel_id'],
            options, None, source_id), task_id=child, retry=False)
    except Exception:
        jobs.mark_retry_dispatch(source_id, token, 'uncertain')
        return {'status': 'dispatch_uncertain', 'task_id': child}
    jobs.mark_retry_dispatch(source_id, token, 'dispatched')
    return {'status': 'enqueued', 'task_id': child}
