"""One continuation after the observed retained-loader filename defect.

Only the terminal local file error before any new paid create qualifies. The
original private package and media record are reused unchanged, every ordinary
quality gate runs again, and all earlier dispatch/receipt keys remain intact.
"""
import secrets
from uuid import uuid5, NAMESPACE_URL

from app.services import content_plan as plan, studio_state as jobs

PREFIX = plan.PREFIX + 'local_resume:v1:'
DISPATCH = PREFIX + 'dispatch:'
EXECUTION = PREFIX + 'execution:'
STATUS = PREFIX + 'status:'
ERROR = 'Recovered generated-media clip is unavailable'


def operation(source):
    return str(uuid5(NAMESPACE_URL, 'owner-plan-local-resume:v1:' + source))


def is_operation(task, source):
    return task == operation(source)


def eligible(source):
    return bool(source.get('parent_id') and source.get('state') == 'FAILURE'
        and source.get('failure_stage') == 'ai_scene_recovery' and source.get('error') == ERROR
        and source.get('paid_create_slots_used') == 0 and not source.get('retry_child_task_id'))


def _claim(client, parent, child):
    claim = client.hgetall(jobs.RETRY_CHILD_CLAIM_PREFIX + child)
    plan._require(claim.get('source_task_id') == parent and type(claim.get('token')) is str
        and len(claim['token']) >= 16
        and client.get(jobs.RETRY_CHILD_EXECUTION_PREFIX + child) == claim['token'])


def checked(client, task, *, claimed=False):
    from app.services import content_plan_recovery as recovery
    from app.services.source_publication_hold import HOLD_PREFIX
    from app.services.youtube_publish_state import UPLOAD_PREFIX
    source = plan._object(client.get(jobs.JOB_PREFIX + plan._id(task)))
    root_id = plan._id(source.get('parent_id'))
    root = recovery._source(client, root_id, claimed=True)
    plan._require(root.get('parent_id') is None and root.get('retry_child_task_id') == task
        and source.get('task_id') == task and source.get('kind') == 'render'
        and source.get('spec') == root['spec'] and source.get('state') == 'FAILURE'
        and source.get('failure_stage') == 'ai_scene_recovery' and source.get('error') == ERROR
        and type(source.get('paid_create_slots_used')) is int and source['paid_create_slots_used'] == 0
        and not source.get('result') and not source.get('publication_hold')
        and not source.get('owner_cancellation'))
    plan._require(client.hgetall(jobs.PAID_CREATE_BUDGET_PREFIX + task) == {'cap': '6', 'used': '0'}
        and source.get('preview_total_paid_create_cap') == 6)
    plan._require(not client.exists(*(prefix + task for prefix in (HOLD_PREFIX, UPLOAD_PREFIX,
        jobs.RENDER_CANCELLATION_PREFIX, jobs.EXTERNAL_EPISODE_LEAF_PREFIX, jobs.QUALITY_HOLD_JOB_FENCE_PREFIX))))
    if not claimed:
        plan._require(not any(source.get(k) for k in ('retry_child_task_id', 'retry_claimed', 'repair_claimed'))
            and not client.exists(jobs.RETRY_DISPATCH_PREFIX + task, jobs.REPAIR_CHECKPOINT_PREFIX + task))
    terminal = plan._object(client.get('celery-task-meta-' + task))
    result = terminal.get('result') or {}; trace = terminal.get('traceback') or ''
    plan._require(terminal.get('status') == 'FAILURE' and terminal.get('task_id') == task
        and type(result) is dict and result.get('exc_type') == 'FinalVisualQualityError'
        and result.get('exc_message') == [ERROR]
        and 'File "/app/app/tasks.py", line 6647, in run_video_pipeline' in trace
        and 'FileNotFoundError:' in trace
        and f'/tmp/youtube_factory/{task}_attempt_0/recovered_s00_00.mp4' in trace)
    _claim(client, root_id, task)
    record = recovery.saved_record(client, root_id)
    plan._require(record['source_sha256'] == recovery._fingerprint(root)
        and record['manifest'].get('kind') == 'owner_plan_retained'
        and record['source_task_id'] == record['manifest']['source_task_id'] == root_id)
    return source, root, record


def schedule(source, enqueue, *, client=None):
    client = client or plan._client(); task = source['task_id']
    source, root, record = checked(client, task)
    claim = {'version': 1, 'source_task_id': task, 'task_id': operation(task),
             'record_sha256': plan._sha(record)}
    if not client.set(DISPATCH + task, plan._raw(claim), nx=True):
        return 'repair_preparing_or_stopped'
    try:
        enqueue(args=(task,), task_id=claim['task_id'], retry=False)
    except Exception:
        return 'repair_dispatch_uncertain'
    return 'repair_preparing'


def verify_child(client, task, source_task, spec, package, manifest):
    child = plan._object(client.get(jobs.JOB_PREFIX + plan._id(task)))
    parent = plan._id(child.get('parent_id'))
    source, root, record = checked(client, parent, claimed=True)
    claim = plan._object(client.get(DISPATCH + parent))
    plan._require(source_task in {parent, root['task_id']}
        and claim == {'version': 1, 'source_task_id': parent, 'task_id': operation(parent),
                     'record_sha256': plan._sha(record)}
        and client.get(EXECUTION + parent) == operation(parent)
        and source.get('retry_child_task_id') == task and child.get('task_id') == task
        and child.get('kind') == 'render' and child.get('spec') == source['spec'] == spec
        and package == record['approved_package'] and manifest == record['manifest'])
    _claim(client, parent, task)
    return root


def run(source_id, operation_id):
    from app.tasks import run_video_pipeline
    client = plan._client(); claim = plan._object(client.get(DISPATCH + source_id))
    plan._require(claim['task_id'] == operation_id == operation(source_id))
    if not client.set(EXECUTION + source_id, operation_id, nx=True):
        return {'status': 'already_started'}
    try:
        source, root, record = checked(client, source_id)
        plan._require(claim['record_sha256'] == plan._sha(record))
        child = str(uuid5(NAMESPACE_URL, 'owner-plan-local-resume-child:v1:' + source_id))
        token = secrets.token_urlsafe(32)
        reserved = jobs.claim_retry_dispatch(source_id, child, token, allow_repair=False)
        plan._require(reserved.get('claimed') is True)
        jobs.create_job(child, source['spec'], kind='render', parent_id=source_id)
        spec = source['spec']; options = {k: v for k, v in spec.items()
            if k not in {'topic', 'duration_minutes', 'language', 'channel_id'}}
        try:
            run_video_pipeline.apply_async(args=(spec['topic'], .5, spec['language'], spec['channel_id'],
                options, record['approved_package'], source_id, record['manifest']), task_id=child, retry=False)
        except Exception:
            jobs.mark_retry_dispatch(source_id, token, 'uncertain')
            client.set(STATUS + source_id, plan._raw({'state': 'uncertain', 'task_id': child}))
            return {'status': 'dispatch_uncertain', 'task_id': child}
        jobs.mark_retry_dispatch(source_id, token, 'dispatched')
        client.set(STATUS + source_id, plan._raw({'state': 'enqueued', 'task_id': child}))
        return {'status': 'enqueued', 'task_id': child, 'new_voice_requests': 0, 'new_video_requests': 0}
    except Exception as error:
        client.set(STATUS + source_id, plan._raw({'state': 'stopped', 'error_type': type(error).__name__}))
        raise
