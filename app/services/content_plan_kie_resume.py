"""One same-root continuation of captured Kie speech rejected before visuals.

Every original request stays pinned. The worker first replays its saved take
and blind transcript, then the normal three-take limit may correct a proven
speech mismatch. Unknown requests and timing-only failures never buy a retry.
"""
import json
import secrets
from uuid import uuid5, NAMESPACE_URL

from app.services import content_plan as plan, studio_state as jobs
from app.services import content_plan_research_resume as pre

PREFIX = plan.PREFIX + 'kie_voice_resume:v1:'
DISPATCH, EXECUTION = PREFIX + 'dispatch:', PREFIX + 'execution:'
ERROR = 'Voice synthesis failed safely before paid media: {"generation_attempt":0,"error_type":"AudioQCError"}'


def operation(source): return str(uuid5(NAMESPACE_URL, 'owner-plan-kie-voice-resume:v1:' + source))


def registered(source): return bool(plan._client().exists(DISPATCH + plan._id(source)))


def eligible(source):
    from app.services.kie_voice_ledger import CHANNELS
    spec = source.get('spec') or {}
    return bool(source.get('parent_id') is None and source.get('state') == 'FAILURE'
        and source.get('failure_stage') == 'voice_and_visuals' and source.get('error') == ERROR
        and spec.get('production_channel_id') in CHANNELS and spec.get('content_plan_item_id')
        and spec.get('duration_minutes') == 3 and spec.get('format') == 'landscape'
        and not source.get('retry_child_task_id'))


def captured_voice(client, source):
    from app.services import kie_voice_ledger as kie, kie_voice_production as production
    from app.services import production_spend_runtime as runtime, commissioning_audio as asr
    from app.services import commissioning_reasoning as native, commissioning_video as video
    from app.services import whisper_transcription as whisper, youtube_auth, production_included_router as included
    task = source['task_id']; foundation = runtime.configured_ledger(read_timeout=3)
    context = runtime.resolve_context(client, task)
    with client.pipeline() as pipe:
        policy, journal, _ = kie._read(pipe)
        active = production.activation(pipe, policy)
        kie._foundation(pipe, foundation)
        kie._binding(pipe, context['channel_id'], context['connection_id'])
        choice = production._choice(pipe, active, context, source['spec']['language'])
        plan._require(choice is not None and context['lineage_id'] == task)
        rows = [(identity, row) for identity, row in journal['requests'].items() if row['scope'] == context]
        plan._require(len(rows) == 1)
        identity, row = rows[0]
        result = kie.restore(row['result']).json()['data']
        plan._require(row['descriptor']['attempt'] == 0 and result['state'] == 'success')
        key = kie.PREFIX + 'media:' + identity; pipe.watch(key)
        media = plan._object(pipe.get(key))
        plan._require(pipe.pttl(key) == -1 and media['request_identity'] == identity
            and media['result_receipt_sha256'] == row['result']['response_sha256'])
        _, audio = asr._read(pipe)
        matches = [r for r in audio['requests'].values() if r['context'] == context]
        plan._require(len(matches) == 1 and matches[0]['outcome'] is not None
            and matches[0]['request']['audio']['sha256'] == media['mp3_sha256'])
        captured = matches[0]['outcome']
        raw = youtube_auth._decrypt_json(captured['encrypted_response'])['response']
        plan._require(kie.sha(raw) == captured['response_sha256'])
        whisper._json_payload(raw.encode(), maximum_seconds=240)
        plan._require(not any(r['reservation']['intent']['root_lineage_id'] == task
            for r in production._native_intents(pipe, foundation)))
        _, included_requests = included.IncludedRouterLedger(foundation)._read(pipe)
        plan._require(not any(r['context']['lineage_id'] == task for r in included_requests['requests'].values()))
        native_key = native.PREFIX + 'lineage:' + task; pipe.watch(native_key, video.PREFIX + task)
        plan._require(pipe.get(video.PREFIX + task) is None)
        requests = pipe.smembers(native_key); plan._require(1 <= len(requests) <= native.MAX_LONG_LINEAGE)
        reasoning = {}
        for request_id in sorted(requests):
            keys = [native.PREFIX + field + ':' + request_id for field in ('request','response')]
            pipe.watch(*keys)
            request, response = [plan._object(pipe.get(k)) for k in keys]
            plan._require(request['context'] == context and request['purpose'] in {'research','editorial','story_review'}
                and request['request_sha256'] == response['request_sha256'] == request_id
                and response['http_status'] == 200 and all(pipe.pttl(k) == -1 for k in keys))
            body = included._cipher().decrypt(response['encrypted_response'].encode())
            plan._require(kie.sha(body) == response['response_sha256'])
            candidates = json.loads(body).get('candidates') or []
            plan._require(len(candidates) == 1 and candidates[0].get('finishReason') == 'STOP')
            reasoning[request_id] = plan._sha([request, response])
        pipe.multi(); pipe.ping(); plan._require(pipe.execute() == [True])
    return {'voice_request': identity, 'voice_receipt_sha256': plan._sha(row),
        'media_sha256': plan._sha(media), 'blind_asr_sha256': plan._sha(matches[0]),
        'reasoning': reasoning, 'original_take_limit': 3, 'new_allocation': 0}


def checked(client, task, *, claimed=False):
    from app.services.source_publication_hold import HOLD_PREFIX
    from app.services.youtube_publish_state import UPLOAD_PREFIX
    source = plan._object(client.get(jobs.JOB_PREFIX + plan._id(task)))
    plan._require(eligible({**source, 'retry_child_task_id': None}) and source['task_id'] == task
        and source.get('kind') == 'render' and source['spec'].get('mode') == 'production'
        and all(source.get(k) is None for k in pre.MEDIA_FIELDS)
        and source.get('paid_create_slots_used') == 0 and source.get('preview_total_paid_create_cap') == 32
        and client.hgetall(jobs.PAID_CREATE_BUDGET_PREFIX + task) == {'cap':'32','used':'0'}
        and not source.get('publication_hold') and not source.get('owner_cancellation')
        and not client.exists(*(p + task for p in (HOLD_PREFIX, UPLOAD_PREFIX, jobs.RENDER_CANCELLATION_PREFIX,
            jobs.QUALITY_HOLD_JOB_FENCE_PREFIX, jobs.EXTERNAL_EPISODE_LEAF_PREFIX, jobs.REPAIR_CHECKPOINT_PREFIX))))
    if not claimed:
        plan._require(not any(source.get(k) for k in ('retry_child_task_id','retry_claimed','repair_claimed'))
            and not client.exists(jobs.RETRY_DISPATCH_PREFIX + task))
    terminal = plan._object(client.get('celery-task-meta-' + task)); failure = terminal.get('result') or {}
    plan._require(terminal.get('status') == 'FAILURE' and terminal.get('task_id') == task
        and failure.get('exc_type') == 'FinalAudioQualityError' and failure.get('exc_message') == [ERROR]
        and ', in _synthesize_voice_candidate\n' in terminal.get('traceback',''))
    spec = source['spec']; dispatch = plan._object(client.get(plan.DISPATCH_PREFIX + spec['content_plan_item_id']))
    profile = plan._object(client.get(plan.production.PROFILE_PREFIX + spec['production_channel_id']))
    document = plan.read(spec['production_channel_id'], client=client)
    plan._require(dispatch['task_id'] == task and plan.dispatch_spec_matches(dispatch, spec)
        and plan._active(client).get(spec['production_channel_id']) == spec['content_plan_item_id']
        and document['enabled'] is True and profile.get('production_enabled') is True
        and profile.get('auto_publish') is True and profile.get('profile_revision') == dispatch['profile_revision'])
    leaf = plan._leaf(client, dispatch)
    plan._require(leaf['task_id'] == (source.get('retry_child_task_id') if claimed else task))
    plan.publication_series(leaf, profile, client=client)
    return source, captured_voice(client, source)


def schedule(source, enqueue, *, client=None):
    client = client or plan._client(); task = source['task_id']
    if client.exists(DISPATCH + task): return 'kie_voice_resume_reserved'
    source, proof = checked(client, task)
    claim = {'version':1,'source_task_id':task,'task_id':operation(task),
        'source_sha256':pre.fingerprint(source),'provider_records':proof}
    if not client.set(DISPATCH + task, plan._raw(claim), nx=True): return 'kie_voice_resume_reserved'
    try: enqueue(args=(task,),task_id=operation(task),retry=False)
    except Exception: return 'kie_voice_resume_uncertain'
    return 'kie_voice_resume_preparing'


def verify_child(task, source_id, spec, *, client=None):
    from app.services.content_plan_local_resume import _claim
    client = client or plan._client(); source, proof = checked(client, source_id, claimed=True)
    claim = plan._object(client.get(DISPATCH + source_id)); child = plan._object(client.get(jobs.JOB_PREFIX + task))
    plan._require(claim['task_id'] == operation(source_id) and claim['provider_records'] == proof
        and claim['source_sha256'] == pre.fingerprint(source)
        and client.get(EXECUTION + source_id) == operation(source_id)
        and source.get('retry_child_task_id') == task and child.get('parent_id') == source_id
        and child.get('kind') == 'render' and child.get('state') in {'PENDING','STARTED','PROGRESS'}
        and child['spec'] == spec == source['spec'] and all(child.get(k) is None for k in pre.MEDIA_FIELDS)
        and not child.get('owner_cancellation') and not child.get('publication_hold'))
    _claim(client, source_id, task)


def run(source_id, operation_id):
    from app.tasks import run_video_pipeline
    client = plan._client(); claim = plan._object(client.get(DISPATCH + source_id))
    plan._require(claim['task_id'] == operation_id == operation(source_id))
    if not client.set(EXECUTION + source_id, operation_id, nx=True): return {'status':'already_started'}
    source, proof = checked(client, source_id)
    plan._require(claim['provider_records'] == proof and claim['source_sha256'] == pre.fingerprint(source))
    child = str(uuid5(NAMESPACE_URL,'owner-plan-kie-voice-child:v1:' + source_id)); token = secrets.token_urlsafe(32)
    plan._require(jobs.claim_retry_dispatch(source_id,child,token,allow_repair=False).get('claimed') is True)
    jobs.create_job(child,source['spec'],kind='render',parent_id=source_id)
    spec=source['spec']; options={k:v for k,v in spec.items()if k not in {'topic','duration_minutes','language','channel_id'}}
    try:
        run_video_pipeline.apply_async(args=(spec['topic'],3,spec['language'],spec['channel_id'],options,None,source_id),
            task_id=child,retry=False)
    except Exception:
        jobs.mark_retry_dispatch(source_id,token,'uncertain')
        return {'status':'dispatch_uncertain','task_id':child}
    jobs.mark_retry_dispatch(source_id,token,'dispatched')
    return {'status':'enqueued','task_id':child}
