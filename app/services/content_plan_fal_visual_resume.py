"""One read-only media continuation of a qualified Fal documentary.

Complete captured outputs and original narration are reused. Unknown creates
stay reserved; this path cannot submit any voice or video generation request.
Fresh story, speech, visual, motion and publication checks remain mandatory.
"""
from copy import deepcopy
import hashlib
import json
import secrets
from uuid import NAMESPACE_URL, uuid5

from app.services import content_plan as plan, studio_state as jobs
from app.services import content_plan_research_resume as pre, content_plan_fal_resume as fal
from app.services.included_stock_pool import _local_transaction

PREFIX = plan.PREFIX + 'fal_visual_resume:v1:'
DISPATCH, EXECUTION, ROOT = (PREFIX + name for name in ('dispatch:', 'execution:', 'root:'))
ERROR = 'Final visual quality gate rejected: '


def operation(source):
    return str(uuid5(NAMESPACE_URL, 'owner-plan-fal-visual-resume:v1:' + source))


def registered(source):
    return bool(plan._client().exists(DISPATCH + plan._id(source)))


def eligible(source):
    spec = source.get('spec') or {}
    return bool(source.get('state') == 'FAILURE' and source.get('failure_stage') == 'final_visual_qc_rescue'
        and str(source.get('error') or '').startswith(ERROR) and source.get('parent_id')
        and spec.get('content_plan_item_id') and spec.get('format') == 'landscape'
        and spec.get('duration_minutes') == 3 and not source.get('retry_child_task_id')
        and plan._client().exists(fal.DISPATCH + source['parent_id']))


def _diagnostic(source):
    value = json.loads(source['error'][len(ERROR):]); rejected = value.get('rejected')
    failures = (value.get('provider_generation_failures') or {}).get('failures')
    plan._require(value.get('stage') == 'after_rescue' and value.get('total') == 30
        and type(rejected) is dict and 1 <= len(rejected) <= 7
        and value.get('accepted') == 30 - len(rejected) and value.get('repair_checkpoint_available') is False
        and all(str(int(i)) == i and 0 <= int(i) < 30 and type(r.get('score')) is int
                and 0 <= r['score'] < 86 for i, r in rejected.items())
        and type(failures) is list and len(failures) == len(rejected)
        and {r.get('scene_index') for r in failures} == {int(i) for i in rejected}
        and all(r.get('stage') == 'final_repair' and r.get('exception_class') == 'WatchError' for r in failures))
    classification = source.get('failure_classification') or {}
    plan._require(classification == {'version': 1, 'stage': 'final_visual_qc_rescue',
        'category': 'content_rejected', 'code': 'visual_quality_exhausted',
        'error_sha256': hashlib.sha256(source['error'].encode()).hexdigest()})
    return sorted(int(i) for i in rejected)


@_local_transaction
def _voice(client, source, root, parent_proof):
    from app.services import fal_voice_production as production, fal_voice_trial as trial, kie_voice_ledger as kie
    from app.services import production_spend_runtime as runtime
    from app.services.content_plan_retained_completion import _metadata
    from app.services.voice_candidate_recovery import _voice_result, _unapproved
    spec = source['spec']; pointer = source['audio_candidate_checkpoint']
    with client.pipeline() as pipe:
        active = production.activation(pipe, spec['language'])
        rows = trial._read(pipe, trial._key(spec['language'])); qualified = trial.qualifying_record(rows)
        context = {'lineage_id': root, 'kind': 'long', 'channel_id': spec['production_channel_id'],
            'connection_id': spec['production_connection_id']}
        production._authorize(pipe, runtime.configured_ledger(read_timeout=3), context)
        plan._require(active is not None and active['channels'].get(spec['production_channel_id']) == spec['production_connection_id']
            and rows['grant']['source_task_id'] == source['parent_id']
            and rows['grant']['context'] == context and qualified['pass'] is True
            and parent_proof['new_tts_requests'] == 0
            and all(rows['grant'].get(k) == v for k, v in parent_proof['original_voice_evidence'].items())
            and kie.sha(kie.raw(qualified)) == parent_proof['qualification_sha256']
            and pointer['audio_sha256'] == qualified['audio_sha256'] == parent_proof['qualified_audio_sha256'])
        pipe.multi(); pipe.ping(); plan._require(pipe.execute() == [True])
    metadata = _metadata(source); voice = _voice_result(metadata['voice'], 30)
    plan._require(_unapproved(pointer) and _unapproved(metadata) and metadata['source_task_id'] == source['task_id']
        and metadata['audio'] == {'key': pointer['audio_key'], 'sha256': pointer['audio_sha256'], 'size': pointer['size']}
        and metadata['package_sha256'] == plan._sha(metadata['package']) == pointer['package_sha256']
        and voice['spoken_texts'] == qualified['spoken_scenes'] and len(voice['spoken_texts']) == 30
        and voice['voice_model'] == production.api.MODEL and voice['voice_language_code'] == spec['language']
        and voice['tempo_rate'] == 1.0)
    return {'audio_sha256': pointer['audio_sha256'], 'metadata_sha256': pointer['metadata_sha256'],
        'package_sha256': pointer['package_sha256'], 'qualification_sha256': parent_proof['qualification_sha256']}


def _video(client, source, root):
    from app.services import commissioning_video as video, accepted_video_outputs as outputs
    journal = plan._object(client.get(video.PREFIX + root)); spec = source['spec']
    entries = source['generated_asset_candidates']['entries']; packages = {p['package_sha256'] for p in entries}
    plan._require(client.pttl(video.PREFIX + root) == -1 and journal.get('version') == 1
        and journal.get('context') == {'lineage_id': root, 'kind': 'long',
            'channel_id': spec['production_channel_id'], 'connection_id': spec['production_connection_id']}
        and len(packages) == 1 and 1 <= len(journal['requests']) == source['paid_create_slots_used'] <= 32)
    records, accepted, unknown = {}, {}, []
    for identity, row in sorted(journal['requests'].items(), key=lambda pair: pair[1]['reserved_at']):
        descriptor = row['request']; index = descriptor.get('scene_index')
        plan._require(identity == video._sha(video._raw(descriptor).encode())
            and descriptor.get('package_sha256') in packages and descriptor.get('continuation_task_id') is None
            and type(index) is int and 0 <= index < 30 and descriptor.get('aspect_ratio') == '16:9'
            and source['created_at'] <= row['reserved_at'] <= source['updated_at'])
        if row.get('result') is None:
            # Immutable evidence, never permission to replace/replay this create.
            unknown.append(identity)
        else:
            plan._require(outputs.completed(row) is not None)
            accepted[str(index)] = identity
        records[identity] = plan._sha(row)
    plan._require(accepted and unknown)
    return {'video_records': records, 'accepted_outputs': accepted, 'unknown_requests': sorted(unknown)}


def checked(client, task, *, claimed=False, _cut_child=None):
    from app.services.content_plan_local_resume import _claim
    from app.services.source_publication_hold import HOLD_PREFIX
    from app.services.youtube_publish_state import UPLOAD_PREFIX
    source = plan._object(client.get(jobs.JOB_PREFIX + plan._id(task))); spec = source['spec']
    plan._require(eligible({**source, 'retry_child_task_id': None}) and spec.get('mode') == 'production')
    rejected = _diagnostic(source); parent = source['parent_id']
    # The original pre-media verifier deliberately prohibits any video journal.
    # Verify its immutable admission and current owner authority, never rerun
    # that earlier phase after media has already been produced.
    saved = plan._object(client.get(jobs.JOB_PREFIX + parent))
    admission = plan._object(client.get(fal.DISPATCH + parent))
    parent_proof = admission['provider_records']
    plan._require(source['task_id'] == str(uuid5(NAMESPACE_URL, 'owner-plan-qualified-fal-child:v1:' + parent))
        and saved.get('retry_child_task_id') == task and saved['spec'] == spec
        and admission['task_id'] == client.get(fal.EXECUTION + parent) == fal.operation(parent)
        and admission.get('source_task_id') == parent and admission['source_sha256'] == pre.fingerprint(saved)
        and all(saved.get(k) is None for k in pre.MEDIA_FIELDS))
    dispatch = plan._object(client.get(plan.DISPATCH_PREFIX + plan._id(spec['content_plan_item_id'])))
    root = dispatch['task_id']; current = source; seen = set()
    while True:
        name = current['task_id']; used = current.get('paid_create_slots_used')
        plan._require(name not in seen and len(seen) < 12 and current.get('state') == 'FAILURE'
            and current.get('kind') == 'render' and current.get('spec') == spec and not current.get('result')
            and type(used) is int and (1 <= used <= 32 if name == task else used == 0)
            and current.get('preview_total_paid_create_cap') == 32
            and client.hgetall(jobs.PAID_CREATE_BUDGET_PREFIX + name) == {'cap': '32', 'used': str(used)}
            and not current.get('publication_hold') and not current.get('owner_cancellation')
            and not client.exists(*(p + name for p in (HOLD_PREFIX, UPLOAD_PREFIX, jobs.RENDER_CANCELLATION_PREFIX,
                jobs.EXTERNAL_EPISODE_LEAF_PREFIX, jobs.QUALITY_HOLD_JOB_FENCE_PREFIX, jobs.REPAIR_CHECKPOINT_PREFIX))))
        seen.add(name)
        if name == root:
            plan._require(current.get('parent_id') is None and plan.dispatch_spec_matches(dispatch, current['spec'])); break
        previous = plan._id(current.get('parent_id')); _claim(client, previous, name)
        current = plan._object(client.get(jobs.JOB_PREFIX + previous))
        plan._require(current.get('retry_child_task_id') == name)
    if not claimed:
        plan._require(not any(source.get(k) for k in ('retry_child_task_id', 'retry_claimed', 'repair_claimed'))
            and not client.exists(jobs.RETRY_DISPATCH_PREFIX + task))
    terminal = plan._object(client.get('celery-task-meta-' + task))
    plan._require(terminal.get('task_id') == task and terminal.get('status') == 'FAILURE'
        and (terminal.get('result') or {}).get('exc_type') == 'FinalVisualQualityError'
        and terminal['result'].get('exc_message') == [source['error']]
        and ', in run_video_pipeline\n' in (terminal.get('traceback') or ''))
    profile = plan._object(client.get(plan.production.PROFILE_PREFIX + dispatch['channel_id']))
    channel = plan._object(client.get(plan.production.OAUTH_CHANNEL_PREFIX + dispatch['channel_id']))
    leaf = plan._leaf(client, dispatch)
    expected_leaf = source.get('retry_child_task_id') if claimed else task
    if _cut_child is not None:
        # Only the registered one-level correction of the initial-cut defect
        # may extend this original admission; the new module verifies its proof.
        from app.services import content_plan_fal_cut_resume as cut
        intermediate = plan._object(client.get(jobs.JOB_PREFIX + expected_leaf))
        child = plan._object(client.get(jobs.JOB_PREFIX + plan._id(_cut_child)))
        plan._require(claimed and client.exists(cut.DISPATCH + expected_leaf)
            and intermediate.get('parent_id') == task and intermediate.get('state') == 'FAILURE'
            and intermediate.get('spec') == child.get('spec') == spec
            and intermediate.get('retry_child_task_id') == _cut_child
            and child.get('parent_id') == expected_leaf)
        _claim(client, expected_leaf, _cut_child)
        expected_leaf = _cut_child
    plan._require(leaf['task_id'] == expected_leaf
        and plan._active(client).get(dispatch['channel_id']) == dispatch['item']['id']
        and profile.get('production_enabled') is True and profile.get('auto_publish') is True
        and channel.get('connection_id') == dispatch['connection_id'] and channel.get('requires_reconnect') is not True
        and client.exists(plan.production.OAUTH_CREDENTIAL_PREFIX + dispatch['channel_id']))
    plan.publication_series(leaf, profile, client=client)
    transcript = _voice(client, source, root, parent_proof)
    media = source['generated_asset_candidates']; entries = media['entries']
    plan._require(media.get('failed_count') == 0 and media.get('attempted_count') == media.get('preserved_count') == len(entries)
        and 1 <= len(entries) <= source['paid_create_slots_used']
        and all(p['audio_sha256'] == transcript['audio_sha256'] for p in entries))
    proof = {'source_sha256': pre.fingerprint(source), 'transcript': transcript, **_video(client, source, root),
        'rejected_scenes': rejected, 'new_tts_requests': 0, 'new_video_requests': 0}
    return source, root, proof


def _claim(source, root, proof):
    return {'version': 1, 'source_task_id': source, 'root_task_id': root,
        'task_id': operation(source), 'evidence': proof}


def schedule(source, enqueue, *, client=None):
    client = client or plan._client(); task = source['task_id']
    if client.exists(DISPATCH + task): return 'fal_visual_resume_reserved'
    source, root, proof = checked(client, task); claim = _claim(task, root, proof)
    from redis.exceptions import WatchError
    try:
        with client.pipeline() as pipe:
            pipe.watch(DISPATCH + task, ROOT + root)
            if pipe.exists(DISPATCH + task, ROOT + root): return 'fal_visual_resume_reserved'
            pipe.multi(); pipe.set(DISPATCH + task, plan._raw(claim), nx=True); pipe.set(ROOT + root, plan._raw(claim), nx=True)
            plan._require(pipe.execute() == [True, True])
    except WatchError: return 'fal_visual_resume_reserved'
    try: enqueue(args=(task,), task_id=operation(task), retry=False)
    except Exception: return 'fal_visual_resume_uncertain'
    return 'fal_visual_resume_preparing'


def verify_child(task, source_id, spec, *, client=None):
    from app.services.content_plan_local_resume import _claim as retry_claim
    client = client or plan._client(); source, root, proof = checked(client, source_id, claimed=True)
    child = plan._object(client.get(jobs.JOB_PREFIX + task)); claim = _claim(source_id, root, proof)
    plan._require(client.get(DISPATCH + source_id) == client.get(ROOT + root) == plan._raw(claim)
        and client.get(EXECUTION + source_id) == operation(source_id)
        and child.get('task_id') == source.get('retry_child_task_id') == task and child.get('parent_id') == source_id
        and child.get('spec') == source['spec'] == spec and child.get('kind') == 'render'
        and child.get('state') in {'PENDING', 'STARTED', 'PROGRESS'} and not child.get('result')
        and not child.get('publication_hold') and not child.get('owner_cancellation'))
    retry_claim(client, source_id, task)
    plan._require(not client.exists(jobs.RENDER_CANCELLATION_PREFIX + task, jobs.QUALITY_HOLD_JOB_FENCE_PREFIX + task))
    return source, root, proof


def run(source_id, operation_id):
    from app.tasks import run_video_pipeline
    client = plan._client(); claim = plan._object(client.get(DISPATCH + source_id))
    plan._require(claim['task_id'] == operation_id == operation(source_id))
    if not client.set(EXECUTION + source_id, operation_id, nx=True): return {'status': 'already_started'}
    source, root, proof = checked(client, source_id); plan._require(claim == _claim(source_id, root, proof))
    child = str(uuid5(NAMESPACE_URL, 'owner-plan-fal-visual-child:v1:' + source_id)); token = secrets.token_urlsafe(32)
    plan._require(jobs.claim_retry_dispatch(source_id, child, token, allow_repair=False).get('claimed') is True)
    jobs.create_job(child, source['spec'], kind='render', parent_id=source_id)
    spec = source['spec']; options = {k: v for k, v in spec.items() if k not in {'topic', 'duration_minutes', 'language', 'channel_id'}}
    try:
        run_video_pipeline.apply_async(args=(spec['topic'], 3, spec['language'], spec['channel_id'], options, None, source_id),
            task_id=child, retry=False)
    except Exception:
        jobs.mark_retry_dispatch(source_id, token, 'uncertain'); return {'status': 'dispatch_uncertain', 'task_id': child}
    jobs.mark_retry_dispatch(source_id, token, 'dispatched')
    return {'status': 'enqueued', 'task_id': child, 'new_voice_requests': 0, 'new_video_requests': 0}


def prepare(task, source_id, spec, work):
    from app.services import content_plan_voice_resume as voice, content_plan_long_media_resume as media
    from app.services import commissioning_video as video, accepted_video_outputs as outputs
    from app.services.runway import download_generated_scene
    from app import tasks
    source, root, proof = verify_child(task, source_id, spec)
    return _prepare_assets(task, source_id, spec, work, source, root, proof)


def _prepare_assets(task, source_id, spec, work, source, root, proof):
    # Callers must verify their distinct immutable admission before this helper.
    from app.services import content_plan_voice_resume as voice, content_plan_long_media_resume as media
    from app.services import commissioning_video as video, accepted_video_outputs as outputs
    from app.services.runway import download_generated_scene
    from app import tasks
    prepared = voice._prepare_long_candidate(task, source['task_id'], spec, work)
    prepared.pop('content_plan_voice_source'); prepared['content_plan_media_source'] = source_id
    clips = media._load_clips(source, prepared, work, allow_repair=True)
    journal = plan._object(plan._client().get(video.PREFIX + root))
    for index, identity in proof['accepted_outputs'].items():
        index = int(index)
        if index in clips: continue
        row = journal['requests'][identity]; plan._require(plan._sha(row) == proof['video_records'][identity])
        result = outputs.completed(row); plan._require(result is not None)
        target = work / f'accepted_fal_s{index:02d}.mp4'; download_generated_scene(result, target)
        tasks._validate_recovered_generated_clip(target, minimum_duration=max(5., prepared['voice_result']['scene_durations'][index] + .35))
        clips[index] = {**tasks._generated_visual_spec(target, provider=result['provider'], provider_attempts=1),
            'retained_source_task_id': source_id, 'retained_native_request_sha256': identity}
    prepared['retained_long_clips'] = media.RetainedClips(deepcopy(clips), token=media._TOKEN)
    jobs.update_job(task, retained_long_media={'source_task_id': source_id, 'retained_clips': len(clips),
        'stock_only': True, 'new_tts_requests': 0, 'new_video_requests': 0, 'requires_full_qa': True,
        'previous_create_count': source['paid_create_slots_used'], 'root_create_limit': 32})
    return prepared


def readonly_for_task():
    from app.services import production_spend_runtime as runtime
    task = runtime._TASK_ID.get()
    if not task: return False
    client = plan._client(); child = plan._object(client.get(jobs.JOB_PREFIX + task)); source = child.get('parent_id')
    from app.services import content_plan_fal_cut_resume as cut
    if source and cut.registered(source):
        cut.verify_child(task, source, child['spec'], client=client)
        return True
    if not source or not client.exists(DISPATCH + source): return False
    verify_child(task, source, child['spec'], client=client)
    return True


def attention_provider_proof(source, root, *, client):
    """Verify a failed retained-only child before detaching its editorial slot."""
    task = plan._id(source['task_id']); parent = source.get('parent_id')
    if not parent or not client.exists(DISPATCH + parent):
        return None
    client.watch(DISPATCH + parent, EXECUTION + parent, ROOT + root,
                 jobs.PAID_CREATE_BUDGET_PREFIX + task)
    ancestor, found_root, proof = checked(client, parent, claimed=True)
    claim = _claim(parent, root, proof); retained = source.get('retained_long_media') or {}
    plan._require(found_root == root and source.get('state') == 'FAILURE'
        and source.get('failure_stage') == 'final_visual_qc_rescue'
        and task == str(uuid5(NAMESPACE_URL, 'owner-plan-fal-visual-child:v1:' + parent))
        and ancestor.get('retry_child_task_id') == task and source['spec'] == ancestor['spec']
        and client.get(DISPATCH + parent) == client.get(ROOT + root) == plan._raw(claim)
        and client.get(EXECUTION + parent) == operation(parent)
        and source.get('paid_create_slots_used') == 0
        and client.hgetall(jobs.PAID_CREATE_BUDGET_PREFIX + task) == {'cap': '32', 'used': '0'}
        and not source.get('generated_asset_candidates') and not source.get('retained_cut_correction')
        and source.get('audio_candidate_checkpoint', {}).get('audio_sha256') == proof['transcript']['audio_sha256']
        and retained.get('source_task_id') == parent and retained.get('stock_only') is True
        and retained.get('new_tts_requests') == retained.get('new_video_requests') == 0
        and proof['unknown_requests'] and proof['accepted_outputs'])
    return {'admission_sha256': plan._sha(claim), 'video_records': proof['video_records'],
            'unknown_requests': proof['unknown_requests']}
