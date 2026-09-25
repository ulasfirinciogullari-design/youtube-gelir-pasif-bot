"""Retain a queued documentary after a captured terminal provider outage.

Only complete generated originals and an exact saved voice are admissible.
Every candidate receives ordinary QA again. The root provider journal keeps
its cumulative 32-create limit; a new child never resets its charged history.
"""
from copy import deepcopy
import hashlib
import json
import secrets
from uuid import uuid5, NAMESPACE_URL

from app.services import content_plan as plan, studio_state as jobs
from app.services import content_plan_voice_resume as voice, content_plan_research_resume as pre

PREFIX = plan.PREFIX + 'long_media_resume:v1:'
DISPATCH, EXECUTION, ROOT = (PREFIX + k for k in ('dispatch:', 'execution:', 'root:'))
ERROR = 'commissioning_video_generation_failed'
_TOKEN = object()


def operation(source):
    return str(uuid5(NAMESPACE_URL, 'owner-plan-long-media:v1:' + source))


def registered(source):
    from app.services import content_plan_fal_cut_resume as cut
    if cut.registered(source): return True
    from app.services import content_plan_retained_completion as completion
    from app.services import content_plan_fal_visual_resume as fal_visual
    return bool(plan._client().exists(DISPATCH + plan._id(source)) or completion.registered(source) or fal_visual.registered(source))


def eligible(source):
    return bool(source.get('state') == 'FAILURE' and source.get('failure_stage') == 'ai_scene_generation'
        and source.get('error') == ERROR and (source.get('spec') or {}).get('content_plan_item_id')
        and (source.get('spec') or {}).get('duration_minutes') == 3
        and source.get('generated_asset_candidates') and source.get('audio_candidate_checkpoint')
        and not source.get('retry_child_task_id'))


def _records(client, root, source, *, child=None):
    from app.services import commissioning_video as video
    spec = source['spec']; value = plan._object(client.get(video.PREFIX + root))
    context = {'lineage_id': root, 'kind': 'long', 'channel_id': spec['production_channel_id'],
               'connection_id': spec['production_connection_id']}
    plan._require(value.get('version') == 1 and value.get('context') == context)
    records, clips, outages = {}, set(), set()
    for identity, row in value['requests'].items():
        descriptor = row['request']; continuation = descriptor.get('continuation_task_id')
        if continuation is not None:
            plan._require(child is not None and continuation == child)
            continue
        plan._require(identity == video._sha(video._raw(descriptor).encode())
            and row.get('create') and row.get('result') and descriptor.get('model') == video.MODEL)
        created, result = video._payload(row['create']), video._payload(row['result'])
        plan._require(type(created.get('name')) is str and result.get('done') is True
            and result.get('name') == created['name'])
        index = descriptor['scene_index']; plan._require(type(index) is int and 0 <= index < 30)
        if result.get('error'):
            error = result['error']
            plan._require(type(error) is dict and type(error.get('code')) is int
                and error['code'] in {13, 14} and not result.get('response'))
            outages.add(index)
        else:
            video._result(result); plan._require(index not in clips); clips.add(index)
        records[identity] = plan._sha(row)
    journal = source['generated_asset_candidates']; entries = journal.get('entries') or []
    plan._require(len(records) == source['paid_create_slots_used'] and 1 <= len(records) < 32
        and len(outages) == 1 and not (outages & clips) and clips
        and journal.get('attempted_count') == journal.get('preserved_count') == len(entries) == len(clips)
        and journal.get('failed_count') == 0 and {r.get('scene_index') for r in entries} == clips
        and len(entries) + 1 == len(records))
    return records


def checked(client, task, *, claimed=False):
    from app.services.content_plan_local_resume import _claim
    from app.services.source_publication_hold import HOLD_PREFIX
    from app.services.youtube_publish_state import UPLOAD_PREFIX
    source = plan._object(client.get(jobs.JOB_PREFIX + plan._id(task))); spec = source.get('spec') or {}
    plan._require(eligible({**source, 'retry_child_task_id': None})
        and source.get('task_id') == task and voice._scope(spec) == ('long', 32)
        and spec.get('mode') == 'production' and not source.get('result')
        and not source.get('repair_checkpoint') and not source.get('qa_workprint'))
    if not claimed:
        plan._require(not any(source.get(k) for k in ('retry_child_task_id', 'retry_claimed', 'repair_claimed'))
            and not client.exists(jobs.RETRY_DISPATCH_PREFIX + task))
    dispatch = plan._object(client.get(plan.DISPATCH_PREFIX + plan._id(spec.get('content_plan_item_id'))))
    root_id = dispatch['task_id']; root = plan._object(client.get(jobs.JOB_PREFIX + root_id))
    plan._require(plan.dispatch_spec_matches(dispatch, root['spec']) and source['spec'] == root['spec']
        and plan._active(client).get(dispatch['channel_id']) == dispatch['item']['id'])
    current = source; seen = set()
    while True:
        ancestor = current['task_id']; used = source['paid_create_slots_used'] if ancestor == task else 0
        plan._require(ancestor not in seen and len(seen) < 12
            and current.get('kind') == 'render' and current.get('state') == 'FAILURE'
            and current.get('spec') == spec and not current.get('result')
            and type(current.get('paid_create_slots_used')) is int and current['paid_create_slots_used'] == used
            and current.get('preview_total_paid_create_cap') == 32
            and client.hgetall(jobs.PAID_CREATE_BUDGET_PREFIX + ancestor) == {'cap': '32', 'used': str(used)}
            and not current.get('publication_hold') and not current.get('owner_cancellation')
            and not client.exists(*(p + ancestor for p in (HOLD_PREFIX, UPLOAD_PREFIX,
                jobs.RENDER_CANCELLATION_PREFIX, jobs.EXTERNAL_EPISODE_LEAF_PREFIX,
                jobs.QUALITY_HOLD_JOB_FENCE_PREFIX, jobs.REPAIR_CHECKPOINT_PREFIX))))
        seen.add(ancestor)
        if ancestor == root_id:
            plan._require(current.get('parent_id') is None); break
        parent = plan._id(current.get('parent_id')); _claim(client, parent, ancestor)
        current = plan._object(client.get(jobs.JOB_PREFIX + parent))
        plan._require(current.get('retry_child_task_id') == ancestor)
    terminal = plan._object(client.get('celery-task-meta-' + task)); failure = terminal.get('result') or {}
    plan._require(terminal.get('task_id') == task and terminal.get('status') == 'FAILURE'
        and failure.get('exc_type') == 'SpendBlocked' and failure.get('exc_message') == [ERROR]
        and ', in _result\n' in (terminal.get('traceback') or ''))
    profile = plan._object(client.get(plan.production.PROFILE_PREFIX + dispatch['channel_id']))
    leaf = plan._leaf(client, dispatch)
    plan._require(leaf['task_id'] == (source.get('retry_child_task_id') if claimed else task))
    plan.publication_series(leaf, profile, client=client)
    channel = plan._object(client.get(plan.production.OAUTH_CHANNEL_PREFIX + dispatch['channel_id']))
    plan._require(profile.get('production_enabled') is True
        and channel.get('connection_id') == dispatch['connection_id'] and channel.get('requires_reconnect') is not True
        and client.exists(plan.production.OAUTH_CREDENTIAL_PREFIX + dispatch['channel_id']))
    proof = {'source_sha256': pre.fingerprint(source),
        'records': _records(client, root_id, source, child=source.get('retry_child_task_id') if claimed else None),
        'transcript': voice._transcript_proof(client, source, root_id)}
    return source, root_id, proof


def _claim_for(source, root, proof):
    return {'version': 1, 'task_id': operation(source), 'source_task_id': source,
            'root_task_id': root, 'evidence': proof}


def schedule(source, enqueue, *, client=None):
    client = client or plan._client(); task = source['task_id']
    if client.exists(DISPATCH + task): return 'long_media_reserved'
    source, root, proof = checked(client, task); claim = _claim_for(task, root, proof)
    from redis.exceptions import WatchError
    try:
        with client.pipeline() as pipe:
            pipe.watch(DISPATCH + task, ROOT + root)
            if pipe.exists(DISPATCH + task, ROOT + root): return 'long_media_reserved'
            pipe.multi(); pipe.set(DISPATCH + task, plan._raw(claim), nx=True); pipe.set(ROOT + root, plan._raw(claim), nx=True)
            plan._require(pipe.execute() == [True, True])
    except WatchError: return 'long_media_reserved'
    try: enqueue(args=(task,), task_id=claim['task_id'], retry=False)
    except Exception: return 'long_media_uncertain'
    return 'long_media_preparing'


def verify_child(task, source_id, spec, *, client=None):
    from app.services.content_plan_local_resume import _claim
    client = client or plan._client(); source, root, proof = checked(client, source_id, claimed=True)
    claim = _claim_for(source_id, root, proof); child = plan._object(client.get(jobs.JOB_PREFIX + task))
    plan._require(client.get(DISPATCH + source_id) == client.get(ROOT + root) == plan._raw(claim)
        and client.get(EXECUTION + source_id) == operation(source_id)
        and child.get('task_id') == task and child.get('parent_id') == source_id
        and source.get('retry_child_task_id') == task and child.get('kind') == 'render'
        and child.get('spec') == source['spec'] == spec and child.get('state') in {'PENDING', 'STARTED', 'PROGRESS'}
        and not child.get('result') and not child.get('publication_hold') and not child.get('owner_cancellation'))
    _claim(client, source_id, task)
    return source, root


def run(source_id, operation_id):
    from app.tasks import run_video_pipeline
    client = plan._client(); claim = plan._object(client.get(DISPATCH + source_id))
    plan._require(claim['task_id'] == operation_id == operation(source_id))
    if not client.set(EXECUTION + source_id, operation_id, nx=True): return {'status': 'already_started'}
    source, root, proof = checked(client, source_id)
    plan._require(claim == _claim_for(source_id, root, proof))
    child = str(uuid5(NAMESPACE_URL, 'owner-plan-long-media-child:v1:' + source_id)); token = secrets.token_urlsafe(32)
    plan._require(jobs.claim_retry_dispatch(source_id, child, token, allow_repair=False).get('claimed') is True)
    jobs.create_job(child, source['spec'], kind='render', parent_id=source_id)
    spec = source['spec']; options = {k: v for k, v in spec.items()
        if k not in {'topic', 'duration_minutes', 'language', 'channel_id'}}
    try:
        run_video_pipeline.apply_async(args=(spec['topic'], 3, spec['language'], spec['channel_id'],
            options, None, source_id), task_id=child, retry=False)
    except Exception:
        jobs.mark_retry_dispatch(source_id, token, 'uncertain'); return {'status': 'dispatch_uncertain', 'task_id': child}
    jobs.mark_retry_dispatch(source_id, token, 'dispatched')
    return {'status': 'enqueued', 'task_id': child, 'new_voice_requests': 0}


class RetainedClips(dict):
    def __init__(self, values, *, token=None):
        plan._require(token is _TOKEN); super().__init__(values); self.token = token


def prepare(task, source_id, spec, work):
    from app.services import content_plan_fal_cut_resume as cut
    if cut.registered(source_id): return cut.prepare(task, source_id, spec, work)
    from app.services import content_plan_fal_visual_resume as fal_visual
    if fal_visual.registered(source_id):
        return fal_visual.prepare(task, source_id, spec, work)
    from app.services import content_plan_retained_completion as completion
    if completion.registered(source_id):
        return completion.prepare(task, source_id, spec, work)
    source, _ = verify_child(task, source_id, spec)
    prepared = voice._prepare_long_candidate(task, source_id, spec, work)
    prepared.pop('content_plan_voice_source'); prepared['content_plan_media_source'] = source_id
    clips = _load_clips(source, prepared, work)
    prepared['retained_long_clips'] = RetainedClips(clips, token=_TOKEN)
    jobs.update_job(task, retained_long_media={'source_task_id': source_id, 'retained_clips': len(clips),
        'previous_create_count': source['paid_create_slots_used'], 'root_create_limit': 32,
        'new_tts_requests': 0, 'requires_full_qa': True})
    return prepared


def _load_clips(source, prepared, work, *, allow_repair=False):
    """Hash-check and probe existing private originals; never a QA approval."""
    from app import tasks
    from app.services import generated_asset_checkpoint as assets, storage
    from app.services.content_plan_recovery import _stored
    source_id = source['task_id']
    canonical = assets._candidate_package(prepared['package']); store = storage._client(); clips = {}
    for ordinal, pointer in enumerate(source['generated_asset_candidates']['entries']):
        plan._require(pointer.get('source_task_id') == source_id and pointer.get('status') == 'preserved_candidate'
            and pointer.get('qa_approved') is False and pointer.get('requires_full_qa') is True
            and pointer.get('audio_sha256') == prepared['source_audio_sha256'])
        path = _stored(store, pointer['manifest_key'], pointer['manifest_sha256'], pointer['manifest_size'],
                       work / f'retained-manifest-{ordinal}.json', assets.MAX_MANIFEST_BYTES)
        manifest = json.loads(path.read_text()); index = manifest['scene_index']; raw = manifest['raw']
        plan._require(index == pointer['scene_index'] and type(index) is int and 0 <= index < 30
            and (allow_repair or index not in clips)
            and manifest.get('phase') == pointer.get('phase')
            and manifest.get('phase') in ({'initial_generation', 'final_repair'} if allow_repair else {'initial_generation'})
            and manifest.get('qa_approved') is False and manifest.get('requires_full_qa') is True
            and manifest['source_task_id'] == source_id and manifest['package'] == canonical
            and manifest['candidate_package_sha256'] == source['audio_candidate_checkpoint']['package_sha256']
            and manifest['audio']['sha256'] == prepared['source_audio_sha256']
            and raw['key'] == pointer['raw_key'] and raw['sha256'] == pointer['raw_sha256']
            and raw['size'] == pointer['raw_size'] and raw['synthetic_motion_only'] is False
            and raw['provider'] in {'gemini_veo', 'fal_veo_lite', 'fal_seedance_15_pro', 'fal_seedance_1_fast'}
            and raw['provider_attempts'] == 1)
        path = _stored(store, raw['key'], raw['sha256'], raw['size'],
                       work / f'retained-s{index:02d}-{ordinal:02d}.mp4', assets.MAX_RAW_BYTES)
        tasks._validate_recovered_generated_clip(path,
            minimum_duration=max(5., prepared['voice_result']['scene_durations'][index] + .35),
            expected_size=raw['size'], expected_sha256=raw['sha256'])
        clips[index] = {'path': str(path), 'generated': True, 'source_type': 'generated',
            'generation_provider': raw['provider'], 'generation_provider_attempts': 1,
            'start_fraction': 0., 'preserve_start_fraction': True, 'forbid_loop': True,
            'source_media_type': 'video', 'synthetic_motion_only': False,
            'retained_source_task_id': source_id}
    return clips


def retained_cap(task, prepared):
    source = plan._id(prepared.get('content_plan_media_source')); child = jobs.get_job(task)
    from app.services import content_plan_fal_cut_resume as cut
    if cut.registered(source):
        cut.verify_child(task, source, child['spec'])
        return 32
    from app.services import content_plan_fal_visual_resume as fal_visual
    if fal_visual.registered(source):
        fal_visual.verify_child(task, source, child['spec'])
        return 32
    from app.services import content_plan_retained_completion as completion
    if completion.registered(source):
        completion.verify_child(task, source, child['spec'])
        return 32
    verify_child(task, source, child['spec'])
    return 32


def install(prepared, visuals):
    if not prepared or not prepared.get('content_plan_media_source'): return []
    clips = prepared.get('retained_long_clips')
    plan._require(type(clips) is RetainedClips and clips.token is _TOKEN and len(visuals) == 30)
    for index, clip in clips.items(): visuals[index] = deepcopy(clip) if type(clip) is list else [deepcopy(clip)]
    return sorted(clips)


def continuation_identity(scope):
    from app.services import production_spend_runtime as runtime
    task = runtime._TASK_ID.get()
    if scope['context'].get('kind') != 'long' or not task: return None
    client = scope['foundation'].client; child = plan._object(client.get(jobs.JOB_PREFIX + task))
    source = child.get('parent_id')
    if not source or not client.exists(DISPATCH + source): return None
    _, root = verify_child(task, source, child['spec'], client=client)
    plan._require(root == scope['context']['lineage_id'])
    return task
