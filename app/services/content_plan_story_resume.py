"""One continuation to repair an observed pre-speech documentary critique.

The complete encrypted records must prove the original research and grammar
rejection, the compatible editorial result and its explicit negative critique.
No voice, media, other model attempt or unknown outcome qualifies. Original
responses are reused by identity and the ordinary factual-feedback loop runs.
"""
import hashlib
import json
import secrets
from collections import Counter
from uuid import uuid5, NAMESPACE_URL

from app.services import content_plan as plan, content_plan_research_resume as pre, studio_state as jobs

PREFIX = plan.PREFIX + 'story_resume:v1:'
DISPATCH, EXECUTION = PREFIX + 'dispatch:', PREFIX + 'execution:'
VALIDATION_DISPATCH, VALIDATION_EXECUTION = PREFIX + 'validation_dispatch:', PREFIX + 'validation_execution:'
ERROR = 'Long documentary failed independent source or editorial review'
CONTRACT_ERROR = 'Long documentary factual repair violated its production contract'


def operation(source):
    return str(uuid5(NAMESPACE_URL, 'owner-plan-story-resume:v1:' + source))


def validation_operation(source):
    return str(uuid5(NAMESPACE_URL, 'owner-plan-story-validation:v1:' + source))


def _validation_failed(client, source):
    """Only the completed read transaction conflict, before any child dispatch."""
    terminal = plan._object(client.get('celery-task-meta-' + operation(source)) or '{}')
    result = terminal.get('result') or {}; trace = terminal.get('traceback') or ''
    return bool(client.get(EXECUTION + source) == operation(source)
        and terminal.get('task_id') == operation(source) and terminal.get('status') == 'FAILURE'
        and result.get('exc_module') == 'redis.exceptions' and result.get('exc_type') == 'WatchError'
        and result.get('exc_message') == ['Watched variable changed.']
        and ', in _provider_free\n' in trace and ', in checked\n' in trace
        and ', in run\n' in trace and ', in _execute_transaction\n' in trace)


def registered(source):
    return bool(plan._client().exists(DISPATCH + plan._id(source)))


def eligible(source):
    return bool(source.get('state') == 'FAILURE'
        and source.get('failure_stage') == 'director_qc' and source.get('error') in {ERROR, CONTRACT_ERROR}
        and (source.get('spec') or {}).get('duration_minutes') == 3
        and (source.get('spec') or {}).get('content_plan_item_id') and not source.get('retry_child_task_id'))


def _responses(client, root, *, grammar_retry):
    from app.services import commissioning_reasoning as native, production_included_router as included
    keys = client.smembers(native.PREFIX + 'lineage:' + root)
    plan._require(len(keys) == 4)
    spec = plan._object(client.get(jobs.JOB_PREFIX + root))['spec']
    context = {'lineage_id': root, 'kind': 'long', 'channel_id': spec['production_channel_id'],
               'connection_id': spec['production_connection_id']}
    expected = Counter({('research', 200): 1, ('editorial', 200): 1 if grammar_retry else 2,
                        ('story_review', 200): 1})
    if grammar_retry: expected[('editorial', 400)] = 1
    roles = Counter(); proofs = {}; original = {}
    for identity in keys:
        request = plan._object(client.get(native.PREFIX + 'request:' + identity))
        response = plan._object(client.get(native.PREFIX + 'response:' + identity))
        role = (request.get('purpose'), response.get('http_status'))
        plan._require(request['request_sha256'] == response['request_sha256'] == identity
            and request['context'] == context and request.get('provider') == 'gemini'
            and request.get('model') == native.MODEL and request.get('version') == response.get('version') == 1
            and role in expected and roles[role] < expected[role])
        body = included._cipher().decrypt(response['encrypted_response'].encode())
        plan._require(hashlib.sha256(body).hexdigest() == response['response_sha256'])
        value = json.loads(body)
        if role[1] == 400:
            error = value.get('error') or {}
            plan._require(error.get('code') == 400 and error.get('status') == 'INVALID_ARGUMENT'
                and not value.get('candidates') and not value.get('usageMetadata'))
        else:
            candidates = value.get('candidates') or []
            plan._require(len(candidates) == 1 and candidates[0].get('finishReason') == 'STOP'
                and value.get('usageMetadata'))
            if role[0] == 'story_review':
                parts = candidates[0]['content']['parts']
                texts = [part['text'] for part in parts if part.get('text') and not part.get('thought')]
                plan._require(len(texts) == 1)
                critique = json.loads(texts[0]); rows = critique['factual_audit']['sentences']
                plan._require(len(rows) == 10 and any(row['assessment'] in {'unsupported', 'uncertain'} for row in rows))
        roles[role] += 1
        proofs[identity] = {'request_sha256': plan._sha(request), 'response_sha256': plan._sha(response)}
        if role in {('research', 200), ('editorial', 400)}:
            original[identity] = proofs[identity]
    plan._require(roles == expected)
    pre._provider_free(client, root, known_reasoning=keys)
    return proofs, original


def _contract_responses(client, root):
    """The observed first feedback draft was complete but 323, not 345-375 words.

    Admit one continuation only when all ten model outcomes are captured and
    the final response demonstrably failed the length check before any speech.
    Nothing here accepts the draft or bypasses the independent factual review.
    """
    from app.services import commissioning_reasoning as native, production_included_router as included, director
    keys = client.smembers(native.PREFIX + 'lineage:' + root)
    plan._require(len(keys) in {10, 12})
    spec = plan._object(client.get(jobs.JOB_PREFIX + root))['spec']
    context = {'lineage_id': root, 'kind': 'long', 'channel_id': spec['production_channel_id'],
               'connection_id': spec['production_connection_id']}
    proofs, rows = {}, []
    for identity in keys:
        request = plan._object(client.get(native.PREFIX + 'request:' + identity))
        response = plan._object(client.get(native.PREFIX + 'response:' + identity))
        plan._require(request.get('request_sha256') == response.get('request_sha256') == identity
            and request.get('context') == context and request.get('provider') == 'gemini'
            and request.get('model') == native.MODEL and request.get('version') == response.get('version') == 1
            and response.get('http_status') == 200 and type(request.get('reserved_at')) is str)
        body = included._cipher().decrypt(response['encrypted_response'].encode())
        plan._require(hashlib.sha256(body).hexdigest() == response['response_sha256'])
        value = json.loads(body); candidates = value.get('candidates') or []
        plan._require(len(candidates) == 1 and candidates[0].get('finishReason') == 'STOP'
            and value.get('usageMetadata'))
        texts = [p['text'] for p in candidates[0]['content']['parts'] if p.get('text') and not p.get('thought')]
        plan._require(len(texts) == 1)
        rows.append((request['reserved_at'], request['purpose'], identity, json.loads(texts[0])))
        proofs[identity] = {'request_sha256': plan._sha(request), 'response_sha256': plan._sha(response)}
    rows.sort(); roles = [row[1] for row in rows]
    plan._require(roles == ['research', 'editorial', 'editorial', 'story_review',
        'research', 'editorial', 'story_review', 'story_review', 'story_review', 'editorial']
        + (['editorial', 'editorial'] if len(rows) == 12 else []))
    for row in (rows[3], *rows[6:9]):
        sentences = row[3].get('factual_audit', {}).get('sentences')
        plan._require(type(sentences) is list and len(sentences) == 10)
    plan._require(any(s.get('assessment') in {'unsupported', 'uncertain'}
        for row in rows[6:9] for s in row[3]['factual_audit']['sentences']))
    scenes = rows[-1][3].get('scenes'); plan._require(type(scenes) is list and len(scenes) == 30)
    plan._require(spec.get('language') == 'en' and all(type(s) is dict
        and type(s.get('narration')) is str and s['narration'].strip()
        and s.get('ai_prompt') is None and s.get('visual_queries') for s in scenes))
    words = director._word_count(' '.join(s['narration'] for s in scenes))
    plan._require(300 <= words < 345)
    original = {row[2]: proofs[row[2]] for row in rows[:4]}
    admission = plan._object(client.get(DISPATCH + root))
    root_job = plan._object(client.get(jobs.JOB_PREFIX + root))
    plan._require(admission == {'version': 1, 'task_id': operation(root), 'source_task_id': root,
        'source_sha256': pre.fingerprint(root_job), 'provider_records': original}
        and client.get(EXECUTION + root) == operation(root))
    if client.exists(VALIDATION_DISPATCH + root):
        plan._require(_validation_failed(client, root)
            and client.get(VALIDATION_DISPATCH + root) == plan._raw(admission)
            and client.get(VALIDATION_EXECUTION + root) == validation_operation(root))
    if len(rows) == 12:
        middle_id = plan._id(root_job.get('retry_child_task_id'))
        middle = plan._object(client.get(jobs.JOB_PREFIX + middle_id))
        plan._require(middle.get('error') == CONTRACT_ERROR and middle.get('parent_id') == root
            and client.get(DISPATCH + middle_id) == plan._raw({'version': 1,
                'task_id': operation(middle_id), 'source_task_id': middle_id,
                'source_sha256': pre.fingerprint(middle),
                'provider_records': {row[2]: proofs[row[2]] for row in rows[:10]}})
            and client.get(EXECUTION + middle_id) == operation(middle_id))
    pre._provider_free(client, root, known_reasoning=keys)
    return proofs, original


def checked(client, task, *, claimed=False):
    from app.services import content_plan_model_resume as model
    from app.services.content_plan_local_resume import _claim
    from app.services.source_publication_hold import HOLD_PREFIX
    from app.services.youtube_publish_state import UPLOAD_PREFIX
    source = plan._object(client.get(jobs.JOB_PREFIX + plan._id(task)))
    spec = source.get('spec') or {}; contract_retry = source.get('error') == CONTRACT_ERROR
    grammar_retry = source.get('parent_id') is not None and not contract_retry
    if contract_retry:
        current = source; children = []
        while current.get('parent_id') is not None:
            plan._require(len(children) < 2 and current.get('error') == CONTRACT_ERROR)
            parent_id = plan._id(current['parent_id']); current_id = current['task_id']
            plan._require(current_id == str(uuid5(NAMESPACE_URL, 'owner-plan-story-child:v1:' + parent_id)))
            _claim(client, parent_id, current_id); children.append(current)
            current = plan._object(client.get(jobs.JOB_PREFIX + parent_id))
            plan._require(current.get('retry_child_task_id') == current_id)
        root = current; root_id = root['task_id']
        plan._require(root.get('error') == ERROR and root.get('failure_stage') == 'director_qc')
        lineage = (root, *reversed(children))
    elif grammar_retry:
        parent_id = plan._id(source.get('parent_id'))
        parent = plan._object(client.get(jobs.JOB_PREFIX + parent_id)); root_id = plan._id(parent.get('parent_id'))
        root = plan._object(client.get(jobs.JOB_PREFIX + root_id))
        plan._require(root.get('retry_child_task_id') == parent_id
            and parent.get('retry_child_task_id') == task and parent.get('error') == model.ERROR)
        lineage = (root, parent, source)
    else:
        root_id = task; root = source; lineage = (source,)
    plan._require(source.get('task_id') == task and source.get('failure_stage') == 'director_qc'
        and source.get('error') == (CONTRACT_ERROR if contract_retry else ERROR) and spec.get('duration_minutes') == 3
        and spec.get('mode') == 'production' and spec.get('format') == 'landscape'
        and root.get('parent_id') is None)
    for job in lineage:
        job_id = job['task_id']
        plan._require(job.get('kind') == 'render' and job.get('state') == 'FAILURE' and job.get('spec') == spec
            and all(job.get(k) is None for k in pre.MEDIA_FIELDS)
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
    proofs, original = (_contract_responses(client, root_id) if contract_retry else
                        _responses(client, root_id, grammar_retry=grammar_retry))
    if grammar_retry:
        _claim(client, root_id, parent_id); _claim(client, parent_id, task)
        previous = plan._object(client.get(pre.DISPATCH + root_id))
        plan._require(previous == {'version': 1, 'task_id': pre.operation(root_id),
            'source_task_id': root_id, 'source_sha256': pre.fingerprint(root)}
            and client.get(pre.EXECUTION + root_id) == pre.operation(root_id))
        admission = plan._object(client.get(model.DISPATCH + parent_id))
        plan._require(admission == {'version': 1, 'task_id': model.operation(parent_id),
            'source_task_id': parent_id, 'source_sha256': pre.fingerprint(parent), 'provider_records': original}
            and client.get(model.EXECUTION + parent_id) == model.operation(parent_id))
    terminal = plan._object(client.get('celery-task-meta-' + task)); result = terminal.get('result') or {}
    plan._require(terminal.get('task_id') == task and terminal.get('status') == 'FAILURE'
        and result.get('exc_type') == 'ProductionContentError' and result.get('exc_message') == [source['error']]
        and ', in review_story\n' in (terminal.get('traceback') or '')
        and (not contract_retry or ', in revise\n' in (terminal.get('traceback') or '')))
    dispatch = plan._object(client.get(plan.DISPATCH_PREFIX + plan._id(spec.get('content_plan_item_id'))))
    plan._require(dispatch['task_id'] == root_id and plan.dispatch_spec_matches(dispatch, spec)
        and plan._active(client).get(dispatch['channel_id']) == dispatch['item']['id'])
    profile = plan._object(client.get(plan.production.PROFILE_PREFIX + dispatch['channel_id']))
    leaf = plan._leaf(client, dispatch)
    plan._require(leaf['task_id'] == (source.get('retry_child_task_id') if claimed else task))
    plan.publication_series(leaf, profile, client=client)
    channel = plan._object(client.get(plan.production.OAUTH_CHANNEL_PREFIX + dispatch['channel_id']))
    plan._require(profile.get('production_enabled') is True
        and channel.get('connection_id') == dispatch['connection_id'] and channel.get('requires_reconnect') is not True
        and client.exists(plan.production.OAUTH_CREDENTIAL_PREFIX + dispatch['channel_id']))
    return source, proofs


def schedule(source, enqueue, *, client=None):
    client = client or plan._client(); task = source['task_id']
    validation = bool(client.exists(DISPATCH + task))
    if validation and not _validation_failed(client, task):
        return 'story_resume_reserved'
    source, responses = checked(client, task)
    claim = {'version': 1, 'task_id': operation(task), 'source_task_id': task,
        'source_sha256': pre.fingerprint(source), 'provider_records': responses}
    if validation:
        plan._require(client.get(DISPATCH + task) == plan._raw(claim))
    if not client.set((VALIDATION_DISPATCH if validation else DISPATCH) + task, plan._raw(claim), nx=True):
        return 'story_resume_reserved'
    try:
        enqueue(args=(task,), task_id=validation_operation(task) if validation else claim['task_id'], retry=False)
    except Exception:
        return 'story_resume_uncertain'
    return 'story_resume_preparing'


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
    if client.exists(VALIDATION_DISPATCH + source_id):
        plan._require(_validation_failed(client, source_id)
            and client.get(VALIDATION_DISPATCH + source_id) == plan._raw(claim)
            and client.get(VALIDATION_EXECUTION + source_id) == validation_operation(source_id))
    return source


def run(source_id, operation_id):
    from app.tasks import run_video_pipeline
    client = plan._client(); claim = plan._object(client.get(DISPATCH + source_id))
    validation = operation_id == validation_operation(source_id)
    plan._require(claim['task_id'] == operation(source_id)
        and operation_id == (validation_operation(source_id) if validation else operation(source_id)))
    if validation:
        plan._require(_validation_failed(client, source_id)
            and client.get(VALIDATION_DISPATCH + source_id) == plan._raw(claim))
    if not client.set((VALIDATION_EXECUTION if validation else EXECUTION) + source_id, operation_id, nx=True):
        return {'status': 'already_started'}
    source, responses = checked(client, source_id)
    plan._require(pre.fingerprint(source) == claim['source_sha256'] and responses == claim['provider_records'])
    child = str(uuid5(NAMESPACE_URL, 'owner-plan-story-child:v1:' + source_id)); token = secrets.token_urlsafe(32)
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
