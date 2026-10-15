"""One same-root continuation after a returned malformed pre-speech critique."""
import hashlib
import json
import re
import secrets
from uuid import uuid5, NAMESPACE_URL

from app.services import content_plan as plan, content_plan_research_resume as pre, studio_state as jobs

PREFIX = plan.PREFIX + 'factual_resume:v1:'
DISPATCH, EXECUTION = PREFIX + 'dispatch:', PREFIX + 'execution:'
ERROR = 'included_factual_audit_invalid'


def operation(source): return str(uuid5(NAMESPACE_URL, 'owner-plan-factual-resume:v1:' + source))


def registered(source): return bool(plan._client().exists(DISPATCH + plan._id(source)))


def eligible(source):
    from app.services.channel_cadence import CHANNELS
    from app.services.documentary_word_contract import duration_failure
    spec = source.get('spec') or {}
    return bool(source.get('parent_id') is None and source.get('state') == 'FAILURE'
        and source.get('failure_stage') == 'director_qc'
        and (source.get('error') == ERROR or duration_failure(source))
        and spec.get('production_channel_id') in CHANNELS and spec.get('content_plan_item_id')
        and spec.get('format') == 'landscape' and spec.get('duration_minutes') == 3
        and not source.get('retry_child_task_id'))


def checked(client, task, *, claimed=False):
    from app.services import commissioning_reasoning as native, commissioning_video as video
    from app.services.content_plan_attention import isolate
    source = plan._object(client.get(jobs.JOB_PREFIX + plan._id(task)))
    plan._require(eligible({**source, 'retry_child_task_id': None})
        and source.get('task_id') == task and source.get('kind') == 'render'
        and all(source.get(key) is None for key in pre.MEDIA_FIELDS)
        and source.get('paid_create_slots_used') == 0 and source.get('preview_total_paid_create_cap') == 32
        and client.hgetall(jobs.PAID_CREATE_BUDGET_PREFIX + task) == {'cap': '32', 'used': '0'}
        and not client.exists(video.PREFIX + task))
    if not claimed:
        # Read-only admission also proves exact terminal, owner intent, absence
        # of holds/uploads/child claims, dependencies and captured outcomes.
        proof = isolate(source, client=client, observe_only=True)
        plan._require(proof and proof['status'] == 'eligible_for_attention')
    else:
        claim = plan._object(client.get(DISPATCH + task))
        plan._require(pre.fingerprint(source) == claim['source_sha256'])
        from app.services.source_publication_hold import HOLD_PREFIX
        from app.services.youtube_publish_state import UPLOAD_PREFIX
        plan._require(not source.get('publication_hold') and not source.get('owner_cancellation')
            and not client.exists(*(p + task for p in (HOLD_PREFIX, UPLOAD_PREFIX,
                jobs.RENDER_CANCELLATION_PREFIX, jobs.QUALITY_HOLD_JOB_FENCE_PREFIX))))
    keys = client.smembers(native.PREFIX + 'lineage:' + task)
    plan._require(1 <= len(keys) <= native.MAX_LONG_LINEAGE)
    proofs = {}
    editorial = []
    for identity in sorted(keys):
        request = plan._object(client.get(native.PREFIX + 'request:' + identity))
        response = plan._object(client.get(native.PREFIX + 'response:' + identity))
        plan._require(request.get('context', {}).get('lineage_id') == task
            and request.get('purpose') in {'research', 'editorial', 'story_review'}
            and request.get('request_sha256') == response.get('request_sha256') == identity
            and response.get('http_status') == 200 and response.get('encrypted_response'))
        proofs[identity] = plan._sha([request, response])
        from app.services.documentary_word_contract import duration_failure
        if duration_failure(source) and request['purpose'] == 'editorial':
            from app.services.production_included_router import _cipher
            raw = _cipher().decrypt(response['encrypted_response'].encode())
            plan._require(hashlib.sha256(raw).hexdigest() == response['response_sha256'])
            value = json.loads(raw); candidates = value.get('candidates') or []
            plan._require(len(candidates) == 1 and candidates[0].get('finishReason') == 'STOP')
            texts = [p['text'] for p in candidates[0]['content']['parts'] if p.get('text') and not p.get('thought')]
            plan._require(len(texts) == 1)
            editorial.append((request['reserved_at'], json.loads(texts[0])))
    from app.services.documentary_word_contract import duration_failure
    if duration_failure(source):
        from app.services.director import _word_count
        plan._require(editorial)
        scenes = max(editorial, key=lambda row: row[0])[1].get('scenes')
        plan._require(type(scenes) is list and len(scenes) == 30
            and all(type(s.get('narration')) is str for s in scenes))
        reported = int(re.match(r'Duration gate rejected script: (\d+)',source['error']).group(1))
        plan._require(_word_count(' '.join(s['narration'] for s in scenes)) == reported)
    pre._provider_free(client, task, known_reasoning=keys)
    return source, proofs


def schedule(source, enqueue, *, client=None):
    client = client or plan._client(); task = source['task_id']
    if client.exists(DISPATCH + task): return 'factual_resume_reserved'
    source, proofs = checked(client, task)
    claim = {'version': 1, 'source_task_id': task, 'task_id': operation(task),
        'source_sha256': pre.fingerprint(source), 'provider_records': proofs}
    if not client.set(DISPATCH + task, plan._raw(claim), nx=True): return 'factual_resume_reserved'
    try: enqueue(args=(task,), task_id=claim['task_id'], retry=False)
    except Exception: return 'factual_resume_uncertain'
    return 'factual_resume_preparing'


def verify_child(task, source_id, spec, *, client=None):
    from app.services.content_plan_local_resume import _claim
    client = client or plan._client(); source, proofs = checked(client, source_id, claimed=True)
    claim = plan._object(client.get(DISPATCH + source_id))
    child = plan._object(client.get(jobs.JOB_PREFIX + task))
    plan._require(claim['task_id'] == operation(source_id)
        and client.get(EXECUTION + source_id) == operation(source_id)
        and claim['provider_records'] == proofs and source.get('retry_child_task_id') == task
        and child.get('task_id') == task and child.get('parent_id') == source_id
        and child.get('kind') == 'render' and child.get('state') in {'PENDING', 'STARTED', 'PROGRESS'}
        and child.get('spec') == source['spec'] == spec
        and not child.get('owner_cancellation') and not child.get('publication_hold')
        and all(child.get(k) is None for k in pre.MEDIA_FIELDS))
    _claim(client, source_id, task)
    dispatch = plan._object(client.get(plan.DISPATCH_PREFIX + spec['content_plan_item_id']))
    profile = plan._object(client.get(plan.production.PROFILE_PREFIX + spec['production_channel_id']))
    plan._require(plan._leaf(client, dispatch)['task_id'] == task)
    plan.publication_series(child, profile, client=client)


def run(source_id, operation_id):
    from app.tasks import run_video_pipeline
    client = plan._client(); claim = plan._object(client.get(DISPATCH + source_id))
    plan._require(claim['task_id'] == operation_id == operation(source_id))
    if not client.set(EXECUTION + source_id, operation_id, nx=True): return {'status': 'already_started'}
    source, proofs = checked(client, source_id)
    plan._require(claim['source_sha256'] == pre.fingerprint(source) and claim['provider_records'] == proofs)
    child = str(uuid5(NAMESPACE_URL, 'owner-plan-factual-child:v1:' + source_id)); token = secrets.token_urlsafe(32)
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
