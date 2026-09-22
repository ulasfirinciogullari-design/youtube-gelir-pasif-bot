"""One retained-voice continuation after an exactly resolved transcript mismatch.

The saved blind transcription must already match the complete original speech
and its word timings. This is admission to ordinary immutable-story, prosody,
visual and publication checks, never a publication or audio-quality approval.
"""
import hashlib
import json
import secrets
from uuid import uuid5, NAMESPACE_URL

from redis.exceptions import WatchError

from app.services import content_plan as plan, studio_state as jobs

PREFIX = plan.PREFIX + 'voice_resume:v1:'
DISPATCH, EXECUTION, ROOT = (PREFIX + name for name in ('dispatch:', 'execution:', 'root:'))


def operation(source):
    return str(uuid5(NAMESPACE_URL, 'owner-plan-voice-resume:v1:' + source))


def registered(source):
    return bool(plan._client().exists(DISPATCH + plan._id(source)))


def eligible(source):
    return bool(source.get('state') == 'FAILURE' and source.get('failure_stage') == 'audio_qc_retry'
        and (source.get('spec') or {}).get('content_plan_item_id')
        and source.get('audio_candidate_checkpoint') and source.get('paid_create_slots_used') == 0
        and not source.get('retry_child_task_id'))


def _scope(spec):
    if spec.get('format') == 'shorts' and spec.get('duration_minutes') == .5:
        return 'shorts', 6
    plan._require(spec.get('format') == 'landscape' and spec.get('duration_minutes') == 3)
    return 'long', 32


def _transcript_proof(client, source, root_id):
    from app.services import audio_checkpoint, audio_qc, storage, commissioning_scribe as scribe
    from app.services.voice_candidate_recovery import _POINTER_FIELDS, _METADATA_FIELDS, _unapproved, _voice_result
    pointer = source['audio_candidate_checkpoint']; task = source['task_id']
    plan._require(type(pointer) is dict and set(pointer) == _POINTER_FIELDS and _unapproved(pointer))
    prefix = 'audio_candidates/' + task + '/' + pointer['audio_sha256']
    plan._require(pointer['audio_key'] == prefix + '/candidate.mp3'
        and pointer['metadata_key'] == prefix + '/metadata-' + pointer['metadata_sha256'] + '.json')
    response = storage._client(single_attempt=True).get_object(Bucket=storage.settings.bucket, Key=pointer['metadata_key'])
    body = response['Body']; maximum = audio_checkpoint.MAX_AUDIO_CANDIDATE_METADATA_BYTES
    try:
        size = response.get('ContentLength')
        plan._require(type(size) is int and 0 < size <= maximum)
        raw = body.read(maximum + 1)
    finally:
        body.close()
    plan._require(len(raw) == size and hashlib.sha256(raw).hexdigest() == pointer['metadata_sha256'])
    metadata = json.loads(raw)
    plan._require(set(metadata) == _METADATA_FIELDS and _unapproved(metadata)
        and metadata['source_task_id'] == task and metadata['package_sha256'] == pointer['package_sha256']
        and metadata['audio'] == {'key': pointer['audio_key'], 'sha256': pointer['audio_sha256'], 'size': pointer['size']})
    package = audio_checkpoint._candidate_package(metadata['package'])
    plan._require(package == metadata['package'] and plan._sha(package) == pointer['package_sha256'])
    voice = _voice_result(metadata['voice'], len(package['scenes']))
    expected = ' '.join(voice['spoken_texts'])
    spec = source['spec']; kind, _ = _scope(spec)
    context = {'lineage_id': root_id, 'kind': kind,
        'channel_id': spec['production_channel_id'], 'connection_id': spec['production_connection_id']}
    keys = list(client.scan_iter(match=scribe.PREFIX + '*', count=128)); plan._require(len(keys) <= 2000)
    proofs = []
    for key in keys:
        row = plan._object(client.get(key))
        if row.get('context') != context or row.get('request', {}).get('audio', {}).get('sha256') != pointer['audio_sha256']:
            continue
        plan._require(client.pttl(key) == -1 and row.get('version') == 1
            and row.get('outcome') and row['request']['audio']['bytes'] == pointer['size']
            and key == scribe.PREFIX + scribe._sha(scribe._raw({'context': context, 'request': row['request']}).encode()))
        payload = audio_qc._response_payload(scribe._response(row['outcome']), 'ElevenLabs')
        review = audio_qc._require_word_timing_evidence(audio_qc.compare_transcript(expected, payload['text'],
            words=payload['words'], language_code=payload.get('language_code'),
            language_probability=payload.get('language_probability'), provider='elevenlabs',
            comparison_language=spec['language']), 'ElevenLabs')
        plan._require(review.get('pass') is True and review.get('score') == 100)
        proofs.append({'scribe_key': key, 'scribe_record_sha256': plan._sha(row),
            'audio_sha256': pointer['audio_sha256'], 'metadata_sha256': pointer['metadata_sha256'],
            'package_sha256': pointer['package_sha256']})
    plan._require(len(proofs) == 1, 'plan_saved_voice_transcript_unverified')
    return proofs[0]


def checked(client, task, *, claimed=False):
    from app.services.source_publication_hold import HOLD_PREFIX
    from app.services.youtube_publish_state import UPLOAD_PREFIX
    source = plan._object(client.get(jobs.JOB_PREFIX + plan._id(task))); spec = source.get('spec') or {}
    _, cap = _scope(spec)
    plan._require(source.get('task_id') == task and source.get('kind') == 'render'
        and source.get('state') == 'FAILURE' and source.get('failure_stage') == 'audio_qc_retry'
        and spec.get('mode') == 'production' and spec.get('language') in {'en', 'tr'}
        and type(source.get('paid_create_slots_used')) is int and source['paid_create_slots_used'] == 0
        and source.get('preview_total_paid_create_cap') == cap
        and all(source.get(k) is None for k in ('result', 'generated_asset_candidates', 'included_stock_pools',
            'repair_checkpoint', 'qa_workprint', 'voice_replacement', 'voice_candidate_reuse'))
        and not source.get('publication_hold') and not source.get('owner_cancellation')
        and client.hgetall(jobs.PAID_CREATE_BUDGET_PREFIX + task) == {'cap': str(cap), 'used': '0'})
    plan._require(not client.exists(*(p + task for p in (HOLD_PREFIX, UPLOAD_PREFIX,
        jobs.RENDER_CANCELLATION_PREFIX, jobs.EXTERNAL_EPISODE_LEAF_PREFIX,
        jobs.QUALITY_HOLD_JOB_FENCE_PREFIX, jobs.REPAIR_CHECKPOINT_PREFIX))))
    if not claimed:
        plan._require(not any(source.get(k) for k in ('retry_child_task_id', 'retry_claimed', 'repair_claimed'))
            and not client.exists(jobs.RETRY_DISPATCH_PREFIX + task))
    terminal = plan._object(client.get('celery-task-meta-' + task)); result = terminal.get('result') or {}
    plan._require(terminal.get('status') == 'FAILURE' and terminal.get('task_id') == task
        and result.get('exc_type') == 'FinalAudioQualityError' and result.get('exc_message') == [source.get('error')])
    dispatch = plan._object(client.get(plan.DISPATCH_PREFIX + plan._id(spec.get('content_plan_item_id'))))
    root_id = dispatch['task_id']; root = plan._object(client.get(jobs.JOB_PREFIX + root_id))
    plan._require(plan.dispatch_spec_matches(dispatch, root['spec']) and source['spec'] == root['spec']
        and plan._active(client).get(dispatch['channel_id']) == dispatch['item']['id'])
    # A retained child never escapes an owner stop or an earlier lineage fence.
    from app.services.content_plan_local_resume import _claim
    current = source; seen = set()
    while True:
        ancestor = current['task_id']
        plan._require(ancestor not in seen and len(seen) < 12
            and current.get('kind') == 'render' and current.get('state') == 'FAILURE'
            and current.get('spec') == spec and not current.get('result')
            and type(current.get('paid_create_slots_used')) is int and current['paid_create_slots_used'] == 0
            and current.get('preview_total_paid_create_cap') == cap
            and client.hgetall(jobs.PAID_CREATE_BUDGET_PREFIX + ancestor) == {'cap': str(cap), 'used': '0'}
            and not current.get('publication_hold') and not current.get('owner_cancellation')
            and not client.exists(*(p + ancestor for p in (HOLD_PREFIX, UPLOAD_PREFIX,
                jobs.RENDER_CANCELLATION_PREFIX, jobs.EXTERNAL_EPISODE_LEAF_PREFIX,
                jobs.QUALITY_HOLD_JOB_FENCE_PREFIX, jobs.REPAIR_CHECKPOINT_PREFIX))))
        seen.add(ancestor)
        if ancestor == root_id:
            plan._require(current.get('parent_id') is None)
            break
        parent = plan._id(current.get('parent_id')); _claim(client, parent, ancestor)
        current = plan._object(client.get(jobs.JOB_PREFIX + parent))
        plan._require(current.get('retry_child_task_id') == ancestor)
    profile = plan._object(client.get(plan.production.PROFILE_PREFIX + dispatch['channel_id']))
    leaf = plan._leaf(client, dispatch)
    plan._require(leaf['task_id'] == (source.get('retry_child_task_id') if claimed else task))
    plan.publication_series(leaf, profile, client=client)
    channel = plan._object(client.get(plan.production.OAUTH_CHANNEL_PREFIX + dispatch['channel_id']))
    plan._require(profile.get('production_enabled') is True
        and channel.get('connection_id') == dispatch['connection_id'] and channel.get('requires_reconnect') is not True
        and client.exists(plan.production.OAUTH_CREDENTIAL_PREFIX + dispatch['channel_id']))
    return source, root_id, _transcript_proof(client, source, root_id)


def schedule(source, enqueue, *, client=None):
    client = client or plan._client(); task = source['task_id']
    if client.exists(DISPATCH + task):
        return 'voice_resume_reserved'
    source, root, proof = checked(client, task)
    claim = {'version': 1, 'task_id': operation(task), 'source_task_id': task,
             'root_task_id': root, 'source_spec_sha256': plan._sha(source['spec']), 'transcript_proof': proof}
    try:
        with client.pipeline() as pipe:
            pipe.watch(DISPATCH + task, ROOT + root)
            if pipe.exists(DISPATCH + task, ROOT + root):
                return 'voice_resume_reserved'
            pipe.multi(); pipe.set(DISPATCH + task, plan._raw(claim), nx=True); pipe.set(ROOT + root, plan._raw(claim), nx=True)
            plan._require(pipe.execute() == [True, True])
    except WatchError:
        return 'voice_resume_reserved'
    try:
        enqueue(args=(task,), task_id=claim['task_id'], retry=False)
    except Exception:
        return 'voice_resume_uncertain'
    return 'voice_resume_preparing'


def verify_child(task, source_id, spec, *, client=None):
    from app.services.content_plan_local_resume import _claim
    client = client or plan._client(); source, root, proof = checked(client, source_id, claimed=True)
    claim = plan._object(client.get(DISPATCH + source_id))
    child = plan._object(client.get(jobs.JOB_PREFIX + task))
    plan._require(claim == {'version': 1, 'task_id': operation(source_id), 'source_task_id': source_id,
        'root_task_id': root, 'source_spec_sha256': plan._sha(spec), 'transcript_proof': proof}
        and client.get(ROOT + root) == plan._raw(claim) and client.get(EXECUTION + source_id) == operation(source_id)
        and child.get('task_id') == task and child.get('kind') == 'render'
        and child.get('state') in {'PENDING', 'STARTED', 'PROGRESS'}
        and child.get('spec') == source['spec'] == spec and child.get('parent_id') == source_id
        and source.get('retry_child_task_id') == task and not child.get('result')
        and not child.get('publication_hold') and not child.get('owner_cancellation'))
    _claim(client, source_id, task)


def run(source_id, operation_id):
    from app.tasks import run_video_pipeline
    client = plan._client(); claim = plan._object(client.get(DISPATCH + source_id))
    plan._require(claim['task_id'] == operation_id == operation(source_id))
    if not client.set(EXECUTION + source_id, operation_id, nx=True):
        return {'status': 'already_started'}
    source, root, proof = checked(client, source_id)
    plan._require(proof == claim['transcript_proof'] and client.get(ROOT + root) == plan._raw(claim)
        and plan._sha(source['spec']) == claim['source_spec_sha256'])
    child = str(uuid5(NAMESPACE_URL, 'owner-plan-voice-child:v1:' + source_id)); token = secrets.token_urlsafe(32)
    plan._require(jobs.claim_retry_dispatch(source_id, child, token, allow_repair=False).get('claimed') is True)
    jobs.create_job(child, source['spec'], kind='render', parent_id=source_id)
    spec = source['spec']; options = {k: v for k, v in spec.items()
        if k not in {'topic', 'duration_minutes', 'language', 'channel_id'}}
    try:
        run_video_pipeline.apply_async(args=(spec['topic'], spec['duration_minutes'], spec['language'], spec['channel_id'],
            options, None, source_id), task_id=child, retry=False)
    except Exception:
        jobs.mark_retry_dispatch(source_id, token, 'uncertain')
        return {'status': 'dispatch_uncertain', 'task_id': child}
    jobs.mark_retry_dispatch(source_id, token, 'dispatched')
    return {'status': 'enqueued', 'task_id': child, 'new_voice_requests': 0}


def prepare_long(task, source_id, spec, work):
    """Recheck the original three-minute speech without editing or fitting it."""
    from app.services import voice_candidate_recovery as retained, longform_voice_retry as legacy
    from app.services import commissioning_longform as longform, director, voice
    verify_child(task, source_id, spec)
    source = jobs.get_job(source_id)
    candidate = retained.load_voice_retry_candidate(source_id, task, source['audio_candidate_checkpoint'], work)
    raw_package, saved_voice = candidate['package'], candidate['voice_result']
    plan._require(_scope(spec) == ('long', 32) and len(raw_package['scenes']) == 30
        and saved_voice['spoken_texts'] == [voice.normalize_turkish_tts(row['narration'],
            ensure_terminal=i == 29) for i, row in enumerate(raw_package['scenes'])])
    prepared = {'voice_result': saved_voice, 'source_audio_sha256': candidate['audio_sha256'],
        'voice_contract_sha256': legacy._digest(legacy._voice_contract(saved_voice)),
        'preserve_audio_bytes': True, 'content_plan_voice_source': source_id}
    legacy.preserved_voice(prepared)
    package = director._clean_package(raw_package, raw_package)
    package['studio_options'] = {k: v for k, v in spec.items()
        if k not in {'topic', 'duration_minutes', 'language', 'channel_id'}}
    count = director._word_count(package['narration']); target = 315 if spec['language'] == 'tr' else 360
    plan._require(target - 15 <= count <= target + 15 and not any(row.get('ai_prompt') for row in package['scenes']))
    package.update(narration_word_count=count, target_word_range=[target-15, target+15], target_scene_count=30, ai_scene_count=0)
    reviewed = longform.review_story(package, spec['topic'], spec['language'], allow_revisions=False)
    retained.require_unchanged_voice_narration(raw_package, reviewed)
    prepared['package'] = reviewed
    jobs.update_job(task, voice_candidate_reuse={'source_task_id': source_id, 'new_tts_requests': 0,
        'requires_full_qa': True, 'preserve_audio_bytes': True, 'source_audio_sha256': prepared['source_audio_sha256'],
        'voice_contract_sha256': prepared['voice_contract_sha256']})
    return prepared


def retained_long_cap(task, candidate):
    source_id = plan._id(candidate.get('content_plan_voice_source'))
    child = jobs.get_job(task); plan._require(type(child) is dict and child.get('parent_id') == source_id)
    # The full admission is checked again at this boundary; a field in a
    # caller-supplied dictionary can never expand the ordinary legacy cap.
    verify_child(task, source_id, child['spec'])
    plan._require(_scope(child['spec']) == ('long', 32))
    return 32
