"""Correct one proven local cut mutation using the original recorded media.

The failed edit and all negative reviews remain intact. This is not a quality
retry permit: a hashed diagnostic must prove that initial ranking moved a
server-pinned generated cut. No new voice or video request is permitted.
"""
import json
import math
import secrets
from uuid import NAMESPACE_URL, uuid5

from app.services import content_plan as plan, studio_state as jobs
from app.services import content_plan_fal_visual_resume as original
from app.services import content_plan_research_resume as pre

PREFIX = plan.PREFIX + 'fal_cut_resume:v1:'
DISPATCH, EXECUTION, ROOT = (PREFIX + name for name in ('dispatch:', 'execution:', 'root:'))


def operation(source):
    return str(uuid5(NAMESPACE_URL, 'owner-plan-fal-cut-resume:v1:' + source))


def registered(source):
    return bool(plan._client().exists(DISPATCH + plan._id(source)))


def eligible(source):
    return bool(source.get('state') == 'FAILURE' and source.get('failure_stage') == 'final_visual_qc_rescue'
        and str(source.get('error') or '').startswith(original.ERROR)
        and source.get('parent_id') and not source.get('retry_child_task_id')
        and source.get('paid_create_slots_used') == 0 and source.get('qa_workprint')
        and plan._client().exists(original.DISPATCH + source['parent_id']))


def _workprint(source):
    from app.services import qa_workprint_access as access, storage
    from app.services.kie_voice_ledger import sha
    pointer = access.validated_pointer(source)
    plan._require(pointer is not None and pointer['version'] == 4)
    response = storage._client(single_attempt=True).get_object(
        Bucket=storage.settings.bucket, Key=pointer['metadata_key'])
    try:
        plan._require(0 < response['ContentLength'] == pointer['metadata_size'] <= 1024 * 1024)
        data = response['Body'].read(1024 * 1024 + 1)
    finally:
        response['Body'].close()
    plan._require(len(data) == response['ContentLength'] and sha(data) == pointer['metadata_sha256'])
    return json.loads(data)


def _cut_proof(source, ancestor):
    diagnostic = json.loads(source['error'][len(original.ERROR):])
    rejected = diagnostic.get('rejected')
    plan._require(diagnostic.get('stage') == 'after_rescue' and diagnostic.get('total') == 30
        and type(rejected) is dict and 1 <= len(rejected) <= 7
        and diagnostic.get('accepted') == 30 - len(rejected)
        and diagnostic.get('repair_checkpoint_available') is False
        and not diagnostic.get('provider_generation_failures')
        and all(str(int(i)) == i and 0 <= int(i) < 30 and type(row.get('score')) is int
                and 0 <= row['score'] < 86 for i, row in rejected.items()))
    from hashlib import sha256
    plan._require(source.get('failure_classification') == {
        'version': 1, 'stage': 'final_visual_qc_rescue', 'category': 'content_rejected',
        'code': 'visual_quality_exhausted', 'error_sha256': sha256(source['error'].encode()).hexdigest()})
    metadata = _workprint(source); pointer = source['qa_workprint']
    plan._require(metadata.get('task_id') == source['task_id'] and metadata.get('version') == 4
        and metadata.get('qa_approved') is False and metadata.get('publish_eligible') is False
        and metadata.get('reusable') is False and metadata.get('failure_stage') == 'final_visual_qc'
        and metadata.get('video_sha256') == pointer['sha256']
        and metadata.get('voice', {}).get('sha256') == ancestor['audio_candidate_checkpoint']['audio_sha256']
        and type(metadata.get('scenes')) is list and len(metadata['scenes']) == 30)
    preserved = {(p['scene_index'], p['raw_sha256'], p['raw_size'])
                 for p in ancestor['generated_asset_candidates']['entries']}
    moved = []
    for index, scene in enumerate(metadata['scenes']):
        plan._require(scene.get('scene_index') == index)
        selected = scene.get('selection') or {}; fraction = selected.get('start_fraction')
        if (str(index) in rejected and selected.get('generated') is True
                and selected.get('preserve_start_fraction') is True
                and selected.get('forbid_loop') is True and selected.get('source_type') == 'generated'
                and type(fraction) in (float, int) and math.isfinite(fraction) and 0 < fraction <= .95
                and (index, selected.get('sha256'), selected.get('size')) in preserved):
            moved.append({'scene_index': index, 'raw_sha256': selected['sha256'],
                          'observed_fraction': fraction, 'original_fraction': 0.})
    plan._require(moved)
    return {'workprint_metadata_sha256': pointer['metadata_sha256'], 'moved_cuts': moved}


def checked(client, task, *, claimed=False):
    from app.services.content_plan_local_resume import _claim
    from app.services.source_publication_hold import HOLD_PREFIX
    from app.services.youtube_publish_state import UPLOAD_PREFIX
    source = plan._object(client.get(jobs.JOB_PREFIX + plan._id(task)))
    plan._require(eligible({**source, 'retry_child_task_id': None}) and source.get('kind') == 'render'
        and not source.get('result') and not source.get('publication_hold') and not source.get('owner_cancellation')
        and source.get('preview_total_paid_create_cap') == 32
        and client.hgetall(jobs.PAID_CREATE_BUDGET_PREFIX + task) == {'cap': '32', 'used': '0'}
        and not client.exists(*(p + task for p in (HOLD_PREFIX, UPLOAD_PREFIX,
            jobs.RENDER_CANCELLATION_PREFIX, jobs.QUALITY_HOLD_JOB_FENCE_PREFIX,
            jobs.EXTERNAL_EPISODE_LEAF_PREFIX, jobs.REPAIR_CHECKPOINT_PREFIX))))
    if not claimed:
        plan._require(not any(source.get(k) for k in ('retry_child_task_id', 'retry_claimed', 'repair_claimed'))
            and not client.exists(jobs.RETRY_DISPATCH_PREFIX + task))
    parent = source['parent_id']; child = source.get('retry_child_task_id') if claimed else None
    ancestor, root, proof = original.checked(client, parent, claimed=True, _cut_child=child)
    old_claim = original._claim(parent, root, proof)
    plan._require(source['task_id'] == str(uuid5(NAMESPACE_URL, 'owner-plan-fal-visual-child:v1:' + parent))
        and source['spec'] == ancestor['spec'] and ancestor.get('retry_child_task_id') == task
        and client.get(original.DISPATCH + parent) == client.get(original.ROOT + root) == plan._raw(old_claim)
        and client.get(original.EXECUTION + parent) == original.operation(parent)
        and source['audio_candidate_checkpoint']['audio_sha256'] == proof['transcript']['audio_sha256']
        and (source.get('retained_long_media') or {}).get('new_video_requests') == 0
        and (source.get('retained_long_media') or {}).get('new_tts_requests') == 0
        and (source.get('retained_long_media') or {}).get('source_task_id') == parent
        and (source.get('retained_long_media') or {}).get('stock_only') is True
        and not source.get('generated_asset_candidates'))
    _claim(client, parent, task)
    terminal = plan._object(client.get('celery-task-meta-' + task))
    plan._require(terminal.get('task_id') == task and terminal.get('status') == 'FAILURE'
        and (terminal.get('result') or {}).get('exc_type') == 'FinalVisualQualityError'
        and terminal['result'].get('exc_message') == [source['error']]
        and ', in run_video_pipeline\n' in (terminal.get('traceback') or ''))
    evidence = {'source_sha256': pre.fingerprint(source), 'original_source_task_id': parent,
                'original_admission_sha256': plan._sha(old_claim), **_cut_proof(source, ancestor)}
    return source, root, evidence, ancestor, proof


def _claim(source, root, proof):
    return {'version': 1, 'source_task_id': source, 'root_task_id': root,
            'task_id': operation(source), 'evidence': proof}


def schedule(source, enqueue, *, client=None):
    from redis.exceptions import WatchError
    client = client or plan._client(); task = source['task_id']
    if client.exists(DISPATCH + task): return 'fal_cut_resume_reserved'
    source, root, proof, _, _ = checked(client, task); claim = _claim(task, root, proof)
    try:
        with client.pipeline() as pipe:
            pipe.watch(DISPATCH + task, ROOT + root)
            if pipe.exists(DISPATCH + task, ROOT + root): return 'fal_cut_resume_reserved'
            pipe.multi(); pipe.set(DISPATCH + task, plan._raw(claim), nx=True); pipe.set(ROOT + root, plan._raw(claim), nx=True)
            plan._require(pipe.execute() == [True, True])
    except WatchError: return 'fal_cut_resume_reserved'
    try: enqueue(args=(task,), task_id=operation(task), retry=False)
    except Exception: return 'fal_cut_resume_uncertain'
    return 'fal_cut_resume_preparing'


def verify_child(task, source_id, spec, *, client=None):
    from app.services.content_plan_local_resume import _claim as retry_claim
    client = client or plan._client()
    source, root, proof, ancestor, previous = checked(client, source_id, claimed=True)
    child = plan._object(client.get(jobs.JOB_PREFIX + task)); claim = _claim(source_id, root, proof)
    plan._require(client.get(DISPATCH + source_id) == client.get(ROOT + root) == plan._raw(claim)
        and client.get(EXECUTION + source_id) == operation(source_id)
        and child.get('task_id') == source.get('retry_child_task_id') == task
        and child.get('parent_id') == source_id and child.get('spec') == source['spec'] == spec
        and child.get('kind') == 'render' and child.get('state') in {'PENDING', 'STARTED', 'PROGRESS'}
        and not child.get('result') and not child.get('publication_hold') and not child.get('owner_cancellation')
        and not client.exists(jobs.RENDER_CANCELLATION_PREFIX + task, jobs.QUALITY_HOLD_JOB_FENCE_PREFIX + task))
    retry_claim(client, source_id, task)
    return source, root, proof, ancestor, previous


def run(source_id, operation_id):
    from app.tasks import run_video_pipeline
    client = plan._client(); claim = plan._object(client.get(DISPATCH + source_id))
    plan._require(claim['task_id'] == operation_id == operation(source_id))
    if not client.set(EXECUTION + source_id, operation_id, nx=True): return {'status': 'already_started'}
    source, root, proof, _, _ = checked(client, source_id)
    plan._require(claim == _claim(source_id, root, proof))
    child = str(uuid5(NAMESPACE_URL, 'owner-plan-fal-cut-child:v1:' + source_id)); token = secrets.token_urlsafe(32)
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
    _, root, proof, ancestor, previous = verify_child(task, source_id, spec)
    result = original._prepare_assets(task, source_id, spec, work, ancestor, root, previous)
    jobs.update_job(task, retained_cut_correction={'version': 1, 'source_task_id': source_id,
        'original_source_task_id': ancestor['task_id'], 'moved_cuts': proof['moved_cuts'],
        'new_tts_requests': 0, 'new_video_requests': 0, 'requires_full_qa': True})
    return result
