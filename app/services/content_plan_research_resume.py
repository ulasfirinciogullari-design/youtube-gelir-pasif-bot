"""Resume a queued root that demonstrably stopped before its first provider call.

One durable continuation, no erased error/dispatch/billing history, no replay on
lost queue acknowledgement. The exact source and all publication fences are
checked again by the child before fresh story, speech or media production.
"""
import hashlib
import secrets
from uuid import NAMESPACE_URL, uuid5

from app.services import content_plan as plan, studio_state as jobs

PREFIX = plan.PREFIX + 'research_resume:v1:'
DISPATCH, EXECUTION, STATUS = (PREFIX + name for name in ('dispatch:', 'execution:', 'status:'))
ERRORS = {'included_research_primary_source_unavailable', 'commissioning_longform_unverified'}
MEDIA_FIELDS = ('result', 'audio_candidate_checkpoint', 'generated_asset_candidates',
    'included_stock_pools', 'repair_checkpoint', 'qa_workprint', 'voice_candidate_reuse',
    'voice_replacement', 'youtube', 'youtube_automation')


def operation(source):
    return str(uuid5(NAMESPACE_URL, 'owner-plan-research-resume:v1:' + source))


def is_operation(task, source):
    return task == operation(source)


def registered(source, *, client=None):
    return bool((client or plan._client()).exists(DISPATCH + plan._id(source)))


def eligible(source):
    return bool(source.get('parent_id') is None and source.get('state') == 'FAILURE'
        and source.get('failure_stage') == 'research' and source.get('error') in ERRORS
        and source.get('paid_create_slots_used') == 0 and not source.get('retry_child_task_id')
        and (source.get('spec') or {}).get('content_plan_item_id'))


def _provider_free(client, task, *, known_reasoning=()):
    from app.services import production_spend_runtime as runtime, commissioning_reasoning as native
    from app.services import production_included_router as included
    from app.services.production_credit_ledger import CreditLedger
    from app.services.production_spend import SpendLedger
    foundation = runtime.configured_ledger(read_timeout=3)
    foundation = SpendLedger(client, foundation.policy, clock=foundation.clock)
    voice = CreditLedger(client, foundation=foundation, clock=foundation.clock)
    with client.pipeline() as pipe:
        voice._watch(pipe)
        _, state, _, _ = voice._read(pipe, foundation.clock())
        _, router = included.IncludedRouterLedger(foundation)._read(pipe)
        key = native.PREFIX + 'lineage:' + task
        pipe.watch(key, runtime.LEDGER_KEY)
        plan._require(pipe.smembers(key) == set(known_reasoning) and not pipe.hexists(runtime.LEDGER_KEY,
            'lineage:' + hashlib.sha256(task.encode()).hexdigest()))
        plan._require(not any(v['reservation']['intent']['root_lineage_id'] == task
            for v in state['intents'].values()))
        plan._require(not any(v['context']['lineage_id'] == task for v in router['requests'].values()))
        pipe.multi(); pipe.ping(); plan._require(pipe.execute() == [True])


def fingerprint(source):
    return plan._sha({k: source.get(k) for k in ('task_id', 'parent_id', 'kind', 'spec', 'state',
        'failure_stage', 'error', 'paid_create_slots_used', 'preview_total_paid_create_cap', *MEDIA_FIELDS)})


def checked(client, task, *, claimed=False):
    from app.services.source_publication_hold import HOLD_PREFIX
    from app.services.youtube_publish_state import UPLOAD_PREFIX
    source = plan._object(client.get(jobs.JOB_PREFIX + plan._id(task)))
    spec = source.get('spec') or {}; cap = 32 if spec.get('format') == 'landscape' else 6
    plan._require(source.get('task_id') == task and source.get('parent_id') is None
        and source.get('kind') == 'render' and source.get('state') == 'FAILURE'
        and source.get('failure_stage') == 'research' and source.get('error') in ERRORS
        and type(source.get('paid_create_slots_used')) is int and source['paid_create_slots_used'] == 0
        and source.get('preview_total_paid_create_cap') == cap
        and all(source.get(k) is None for k in MEDIA_FIELDS)
        and not source.get('publication_hold') and not source.get('owner_cancellation')
        and client.hgetall(jobs.PAID_CREATE_BUDGET_PREFIX + task) == {'cap': str(cap), 'used': '0'})
    plan._require(not client.exists(*(p + task for p in (HOLD_PREFIX, UPLOAD_PREFIX,
        jobs.RENDER_CANCELLATION_PREFIX, jobs.EXTERNAL_EPISODE_LEAF_PREFIX,
        jobs.QUALITY_HOLD_JOB_FENCE_PREFIX, jobs.REPAIR_CHECKPOINT_PREFIX))))
    if not claimed:
        plan._require(not any(source.get(k) for k in ('retry_child_task_id', 'retry_claimed', 'repair_claimed'))
            and not client.exists(jobs.RETRY_DISPATCH_PREFIX + task))
    terminal = plan._object(client.get('celery-task-meta-' + task))
    result = terminal.get('result') or {}; trace = terminal.get('traceback') or ''
    plan._require(terminal.get('status') == 'FAILURE' and terminal.get('task_id') == task
        and result.get('exc_type') == 'SpendBlocked' and result.get('exc_message') == [source['error']]
        and ', in research_and_script\n' in trace and ', in run_video_pipeline\n' in trace
        and ('in research_pages\n' in trace if source['error'].startswith('included_') else
             'in authorize\n' in trace and 'commissioning_longform.py' in trace and spec.get('duration_minutes') == 3))
    entry = plan._id(spec.get('content_plan_item_id'))
    dispatch = plan._object(client.get(plan.DISPATCH_PREFIX + entry))
    plan._require(dispatch['task_id'] == task and plan.dispatch_spec_matches(dispatch, spec)
        and plan._active(client).get(dispatch['channel_id']) == entry)
    profile = plan._object(client.get(plan.production.PROFILE_PREFIX + dispatch['channel_id']))
    leaf = plan._leaf(client, dispatch)
    plan.publication_series(leaf, profile, client=client)
    channel = plan._object(client.get(plan.production.OAUTH_CHANNEL_PREFIX + dispatch['channel_id']))
    plan._require(profile.get('production_enabled') is True
        and channel.get('connection_id') == dispatch['connection_id'] and channel.get('requires_reconnect') is not True
        and client.exists(plan.production.OAUTH_CREDENTIAL_PREFIX + dispatch['channel_id']))
    _provider_free(client, task)
    return source


def schedule(source, enqueue, *, client=None):
    from app.services import included_research_sources as sources, production_spend_runtime as runtime
    client = client or plan._client(); task = source['task_id']
    if client.exists(DISPATCH + task):
        return 'research_resume_reserved'
    source = checked(client, task)
    # Check restored source access before consuming the one resume dispatch.
    pages = sources.research_pages(source['spec']['topic'])
    plan._require(len({p['url'] for p in pages}) >= 2, 'plan_research_source_wait')
    runtime.preflight_scheduled_production(source['spec']['production_channel_id'],
        kind='long' if source['spec']['duration_minutes'] == 3 else 'shorts')
    claim = {'version': 1, 'task_id': operation(task), 'source_task_id': task,
             'source_sha256': fingerprint(source)}
    if not client.set(DISPATCH + task, plan._raw(claim), nx=True):
        return 'research_resume_reserved'
    try:
        enqueue(args=(task,), task_id=claim['task_id'], retry=False)
    except Exception:
        return 'research_resume_uncertain'
    return 'research_resume_preparing'


def verify_child(task, source_id, spec, *, client=None):
    client = client or plan._client()
    source = checked(client, source_id, claimed=True)
    claim = plan._object(client.get(DISPATCH + source_id))
    child = plan._object(client.get(jobs.JOB_PREFIX + task))
    token = client.hgetall(jobs.RETRY_CHILD_CLAIM_PREFIX + task)
    plan._require(claim == {'version': 1, 'task_id': operation(source_id), 'source_task_id': source_id,
        'source_sha256': fingerprint(source)} and client.get(EXECUTION + source_id) == operation(source_id)
        and source.get('retry_child_task_id') == task and child.get('parent_id') == source_id
        and child.get('task_id') == task and child.get('kind') == 'render'
        and child.get('spec') == source['spec'] == spec and child.get('state') in {'PENDING', 'PROGRESS', 'STARTED'}
        and all(child.get(k) is None for k in MEDIA_FIELDS)
        and token.get('source_task_id') == source_id and type(token.get('token')) is str
        and len(token['token']) >= 16 and client.get(jobs.RETRY_CHILD_EXECUTION_PREFIX + task) == token['token'])
    return source


def run(source_id, operation_id):
    from app.tasks import run_video_pipeline
    client = plan._client(); claim = plan._object(client.get(DISPATCH + source_id))
    plan._require(claim['task_id'] == operation_id == operation(source_id))
    if not client.set(EXECUTION + source_id, operation_id, nx=True):
        return {'status': 'already_started'}
    source = checked(client, source_id)
    plan._require(fingerprint(source) == claim['source_sha256'])
    child = str(uuid5(NAMESPACE_URL, 'owner-plan-research-child:v1:' + source_id))
    token = secrets.token_urlsafe(32)
    plan._require(jobs.claim_retry_dispatch(source_id, child, token, allow_repair=False).get('claimed') is True)
    jobs.create_job(child, source['spec'], kind='render', parent_id=source_id)
    spec = source['spec']; options = {k: v for k, v in spec.items()
        if k not in {'topic', 'duration_minutes', 'language', 'channel_id'}}
    try:
        run_video_pipeline.apply_async(args=(spec['topic'], spec['duration_minutes'], spec['language'],
            spec['channel_id'], options, None, source_id), task_id=child, retry=False)
    except Exception:
        jobs.mark_retry_dispatch(source_id, token, 'uncertain')
        client.set(STATUS + source_id, plan._raw({'state': 'uncertain', 'task_id': child}))
        return {'status': 'dispatch_uncertain', 'task_id': child}
    jobs.mark_retry_dispatch(source_id, token, 'dispatched')
    client.set(STATUS + source_id, plan._raw({'state': 'enqueued', 'task_id': child}))
    return {'status': 'enqueued', 'task_id': child}
