"""One explicit reuse of owned experiment footage for a rejected translation.

This operator purchases no speech or video. Donor footage is only a candidate:
the receiving story, its exact cuts, speech, render and publication retain all
ordinary reviews. Native failed preparation and paid receipts stay intact.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import secrets
import shutil
import subprocess
from uuid import uuid4, uuid5, NAMESPACE_URL

from app.services import content_plan as plan, content_plan_recovery as recovery
from app.services import shorts_experiment as batch, studio_state as jobs

PREFIX = plan.PREFIX + 'experiment_library:v1:'
CLAIM = PREFIX + 'claim:'
EXECUTION = PREFIX + 'execution:'
STATUS = PREFIX + 'status:'
REVIEW = PREFIX + 'review:'


def operation(source_id):
    return str(uuid5(NAMESPACE_URL, 'owner-experiment-library:v1:' + plan._id(source_id)))


def _require(value):
    plan._require(value, 'experiment_library_unverified')


def _stopped_preparation(client, source):
    task = source['task_id']
    prior = {name: client.get(prefix + task) for name, prefix in (
        ('dispatch', recovery.DISPATCH), ('execution', recovery.EXECUTION),
        ('status', recovery.STATUS), ('record', recovery.RECORD))}
    identity = str(uuid5(NAMESPACE_URL, 'owner-plan-render-recovery:v4:' + task))
    _require(plan._object(prior['dispatch']) == {'version': 1, 'source_task_id': task,
        'task_id': identity, 'source_sha256': recovery._fingerprint(source)}
        and prior['execution'] == identity and prior['record'] is None
        and plan._object(prior['status']) == {'state': 'stopped', 'error_type': 'RuntimeError'})
    prior['terminal'] = client.get('celery-task-meta-' + identity)
    terminal = plan._object(prior['terminal'])
    _require(terminal.get('task_id') == identity and terminal.get('status') == 'FAILURE'
        and (terminal.get('result') or {}).get('exc_type') == 'RuntimeError'
        and terminal['result'].get('exc_message') == [
            'Saved stock candidates did not pass independent exact-cut review'])
    return prior


def _scope(client, source_id, donor_id, expected_source_sha256, expected_donor_media_sha256):
    from app.services import shorts_experiment_stock as stock, shorts_experiment_editorial as editorial
    source = recovery._source(client, source_id)
    _require(plan._sha(source) == expected_source_sha256 and recovery._stock_failure(source)
        and recovery.eligible(source) and source.get('parent_id') is None)
    error = str(source.get('error') or '')
    _require(error.startswith('Final visual quality gate rejected: ')
        and source['failure_classification'].get('error_sha256') == hashlib.sha256(error.encode()).hexdigest())
    rejected = json.loads(error.split(': ', 1)[1])
    _require(rejected.get('stage') == 'after_rescue' and rejected.get('total') == 6
        and type(rejected.get('accepted')) is int and 4 <= rejected['accepted'] < 6
        and len(rejected.get('rejected', {})) == 6 - rejected['accepted']
        and rejected.get('repair_checkpoint_available') is False)
    prior = _stopped_preparation(client, source)
    channels = []
    for task in (source_id, donor_id):
        job = plan._object(client.get(jobs.JOB_PREFIX + task)); spec = job.get('spec') or {}
        channel = spec.get('production_channel_id'); entry_id = spec.get('content_plan_item_id')
        _require(channel in batch.COUNTS and spec.get('format') == 'shorts'
            and spec.get('duration_minutes') == .5 and job.get('parent_id') is None
            and task == batch.root_id(channel, entry_id))
        document = plan.read(channel, client=client)
        entry = next((row for row in (document or {}).get('items', []) if row['id'] == entry_id), None)
        _require(entry is not None and document['enabled'] and stock.member(client, channel, entry))
        channels.append(channel)
        if task == donor_id:
            _require(stock.prepared(client, channel, entry) == job)
            dispatch = plan._object(client.get(plan.DISPATCH_PREFIX + entry_id))
            proof = plan.publication_proof(client, dispatch)
            _require(proof is not None and client.exists(plan.COMPLETION_PREFIX + entry_id))
            donor = job
    _require(channels[0] != channels[1] and source_id != donor_id)
    _require(_media_fingerprint(donor) == expected_donor_media_sha256)
    for job in (source, donor):
        candidates = job.get('generated_asset_candidates') or {}
        _require(candidates.get('attempted_count') == candidates.get('preserved_count') == 6
            and candidates.get('failed_count') == 0 and len(candidates.get('entries', [])) == 6
            and job.get('paid_create_slots_used') == 6 and job.get('audio_candidate_checkpoint'))
    # No publication/repair/financial uncertainty is detached by this reuse.
    with client.pipeline() as pipe:
        def read(key):
            pipe.watch(key)
            return pipe.get(key)
        settled = {job['task_id']: editorial._settled(pipe, read, job['task_id']) for job in (source, donor)}
        pipe.multi(); pipe.ping(); _require(pipe.execute() == [True])
    return source, donor, {'native_preparation': prior, 'settled_provider_evidence': settled,
        'donor_publication': {k: v for k, v in proof.items() if k != 'completed_at'}}


def _media_fingerprint(source):
    return plan._sha({key: source.get(key) for key in ('task_id', 'spec', 'state',
        'audio_candidate_checkpoint', 'generated_asset_candidates', 'paid_create_slots_used')})


def _same_primary_sources(receiving, donor):
    def urls(package):
        rows = package.get('sources')
        _require(type(rows) is list and 1 <= len(rows) <= 5)
        found = {row.get('url') for row in rows if type(row) is dict}
        _require(len(found) == len(rows) and all(type(v) is str and v.startswith('https://') for v in found))
        return found
    _require(urls(receiving) == urls(donor))


def _review_passed(result, threshold):
    from app.services.content_plan_stock_repair import _passes
    _require(type(threshold) is int and 86 <= threshold <= 100)
    rows = result.get('reviews') or []
    _require(not result.get('missing_review_indices') and not result.get('unreviewable_scene_indices')
        and type(rows) is list and len(rows) == 6
        and all(type(row) is dict and type(row.get('scene_index')) is int for row in rows)
        and {row['scene_index'] for row in rows} == set(range(6))
        and all(type(row.get('best_candidate_index')) is int and row['best_candidate_index'] == 0
            and _passes(row, threshold) for row in rows))


def _prepare(source, donor, work):
    from app import tasks
    from app.services import storage, director, render, visual_qc, generated_asset_checkpoint as assets
    from app.services.voice_candidate_recovery import load_voice_retry_candidate, require_unchanged_voice_narration
    from app.services.production_included_router import _LAST_OBSERVED
    task = source['task_id']; spec = source['spec']; objects = storage._client()
    candidate = load_voice_retry_candidate(task, work.name.split('_attempt_')[0],
        source['audio_candidate_checkpoint'], work)
    original, voice = candidate['package'], candidate['voice_result']
    _require(plan._sha(assets._candidate_package(original)) == source['audio_candidate_checkpoint']['package_sha256']
        and len(original['scenes']) == len(voice['scene_durations']) == 6)
    footage = {}; provenance = {}
    for ordinal, pointer in enumerate(donor['generated_asset_candidates']['entries']):
        _require(pointer.get('source_task_id') == donor['task_id']
            and pointer.get('status') == 'preserved_candidate' and pointer.get('qa_approved') is False
            and pointer.get('requires_full_qa') is True)
        manifest_path = recovery._stored(objects, pointer['manifest_key'], pointer['manifest_sha256'],
            pointer['manifest_size'], work / f'donor-manifest-{ordinal}.json', assets.MAX_MANIFEST_BYTES)
        manifest = json.loads(manifest_path.read_text()); index = manifest['scene_index']; raw = manifest['raw']
        _require(type(index) is int and 0 <= index < 6 and index not in footage
            and manifest['source_task_id'] == donor['task_id'] and manifest['phase'] == 'initial_generation'
            and manifest['candidate_package_sha256'] == plan._sha(manifest['package'])
                == donor['audio_candidate_checkpoint']['package_sha256']
            and manifest['audio']['sha256'] == donor['audio_candidate_checkpoint']['audio_sha256']
            and raw['key'] == pointer['raw_key'] and raw['sha256'] == pointer['raw_sha256']
            and raw['size'] == pointer['raw_size'] and raw['synthetic_motion_only'] is False)
        _same_primary_sources(original, manifest['package'])
        path = recovery._stored(objects, raw['key'], raw['sha256'], raw['size'],
            work / f'donor-{index}.mp4', assets.MAX_RAW_BYTES)
        path, raw, edit = _fit_owned_footage(path, raw,
            max(5., voice['scene_durations'][index] + .35), work / f'library-paced-{index}.mp4')
        visual = tasks._generated_visual_spec(path, provider=raw['provider'], provider_attempts=raw['provider_attempts'])
        visual.update(curated_pinned=True, preserve_start_fraction=True, forbid_loop=True)
        footage[index] = (path, raw, visual)
        provenance[str(index)] = {'origin_task_id': donor['task_id'], 'origin_scene_index': index,
            'candidate_manifest': deepcopy(pointer), 'candidate_package_sha256': manifest['candidate_package_sha256'],
            'quality_inherited': False, 'edit': edit}
    _require(set(footage) == set(range(6)))
    pools = [[footage[index][2]] for index in range(6)]
    duration = render.media_duration(voice['path'])
    timeline = render._scene_timeline(original['scenes'], pools, voice['scene_durations'], duration,
        [row[0] for row in pools]); counts = render._timeline_frame_counts(timeline, duration)
    _require(len(timeline) == 6 and [row[3] for row in timeline] == list(range(6)))
    inputs = []
    for index in range(6):
        target = work / f'library-cut-{index}.mp4'
        render.normalize_clip(footage[index][2], target, counts[index] / render.FPS,
            index, timeline[index][2], '1080x1920')
        visual = tasks._generated_visual_spec(target, provider=footage[index][1]['provider'],
            provider_attempts=footage[index][1]['provider_attempts'])
        visual.update(start_fraction=0., preserve_start_fraction=True, forbid_loop=True)
        inputs.append([visual])
    reviewed = visual_qc.review_scene_visuals(original['scenes'], inputs, work / 'library-exact-cut-review',
        max_scenes=6, _missing_review_attempts=0, topic=spec['topic'], story_scenes=original['scenes'],
        content_style=spec['content_style'], evidence_sources=original['sources'])
    observation = deepcopy(_LAST_OBSERVED.get())
    _require(plan._client().set(REVIEW + task, plan._raw({'review': reviewed,
        'provider_evidence': observation, 'exact_cut_frames': counts, 'origins': provenance}), nx=True))
    _review_passed(reviewed, spec['quality_threshold'])
    options = {k: v for k, v in spec.items() if k not in {'topic', 'duration_minutes', 'language', 'channel_id'}}
    kwargs = ({'verified_spoken_word_budget': director.validate_spoken_word_budget(original['spoken_word_budget'])}
        if 'spoken_word_budget' in original else {})
    story = deepcopy(original)
    if kwargs:
        budget = kwargs['verified_spoken_word_budget']
        story['target_word_range'] = [budget['minimum_words'], budget['maximum_words']]
    package = director.revalidate_immutable_short_story(story, spec['topic'], .5, spec['language'], options,
        immutable_candidate_narrations=[s['narration'] for s in original['scenes']], immutable_scene_fields=True, **kwargs)
    require_unchanged_voice_narration(original, package)
    _require(package['scenes'] == original['scenes'] and director.short_story_package_is_approved(package, spec['topic']))
    package_hash = tasks._recovery_package_sha256(package)
    media = {'version': 3, 'recovery_only': True, 'source_task_id': task,
        'package_sha256': package_hash, 'scenes': {}}
    for index, (path, raw, _) in footage.items():
        key = f'recovery/{task}/raw/scene-{index:02d}-initial.mp4'
        with path.open('rb') as body:
            assets._put_immutable(objects, key, body, raw['sha256'], raw['size'], 'video/mp4')
        media['scenes'][str(index)] = [{'key': key, 'sha256': raw['sha256'], 'size': raw['size'],
            'provider': raw['provider'], 'provider_attempts': raw['provider_attempts'],
            'synthetic_motion_only': False, 'motion_recipe_version': None, 'source_media_type': 'video'}]
    path = Path(voice['path']); key = f'recovery/{task}/raw/voice.mp3'
    with path.open('rb') as body:
        assets._put_immutable(objects, key, body, candidate['audio_sha256'], path.stat().st_size, 'audio/mpeg')
    audio = {'version': 1, 'source_task_id': task, 'package_sha256': package_hash,
        'key': key, 'sha256': candidate['audio_sha256'], 'size': path.stat().st_size,
        **{k: voice[k] for k in ('scene_durations', 'spoken_texts', 'duration_before_fit',
            'duration_after_fit', 'tempo_rate', 'content_target_seconds', 'reserved_tail_seconds')}}
    tasks._validated_recovered_generated_media(media, 6, package_hash)
    tasks._validated_recovered_voice(audio, 6, package_hash)
    package.update(_recovered_generated_media=media, _recovered_voice=audio)
    manifest = {'version': 1, 'kind': 'owner_plan_retained', 'source_task_id': task,
        'package_sha256': package_hash, 'stocks': {}, 'credits': [],
        'library_origins': provenance, 'library_review': {'review': reviewed,
            'provider_evidence': observation, 'exact_cut_frames': counts,
            'quality_inherited': False, 'requires_worker_full_qa': True}}
    return {'version': 1, 'source_task_id': task, 'source_sha256': recovery._fingerprint(source),
        'approved_package': package, 'manifest': manifest}


def _fit_owned_footage(path, raw, minimum, target):
    """Fit real continuous frames at up to 1.25x duration; never loop or freeze.

    Different-language narration needs a different edit. Original bytes and
    provenance remain immutable; the derivative receives its own checksum and
    must pass the receiving story's full visual reviews.
    """
    from app import tasks
    from app.services import render
    tasks._validate_recovered_generated_clip(path, minimum_duration=5.,
        expected_size=raw['size'], expected_sha256=raw['sha256'])
    duration = render.media_duration(path)
    if duration + .04 >= minimum:
        return path, raw, {'kind': 'unchanged', 'source_sha256': raw['sha256']}
    scale = (minimum + .1) / duration
    _require(1. < scale <= 1.25 and not target.exists())
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(path), '-an',
        '-vf', f'setpts={scale:.12f}*PTS', '-c:v', 'libx264', '-preset', 'fast',
        '-crf', '18', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(target)],
        check=True, timeout=120, capture_output=True)
    derived = {**raw, 'sha256': tasks._file_sha256(target), 'size': target.stat().st_size}
    tasks._validate_recovered_generated_clip(target, minimum_duration=minimum,
        expected_size=derived['size'], expected_sha256=derived['sha256'])
    return target, derived, {'kind': 'continuous_slow_motion', 'duration_scale': scale,
        'source_sha256': raw['sha256'], 'derived_sha256': derived['sha256'],
        'source_duration': duration, 'derived_duration': render.media_duration(target),
        'looped': False, 'freeze_frame': False}


def continue_from_library(source_id, donor_id, *, expected_source_sha256, expected_donor_media_sha256,
                          client=None, observe_only=False):
    """Explicit operator; one durable attempt, no automatic retries on lost ACKs."""
    from app.services import production_spend_runtime as runtime
    from app.tasks import run_video_pipeline
    client = client or plan._client(); key = CLAIM + plan._id(source_id)
    source, donor, evidence = _scope(client, source_id, donor_id, expected_source_sha256,
        expected_donor_media_sha256)
    _require(not client.exists(key, EXECUTION + source_id, STATUS + source_id, REVIEW + source_id))
    claim = {'version': 1, 'task_id': operation(source_id), 'source_task_id': source_id,
        'donor_task_id': donor_id, 'source_sha256': expected_source_sha256,
        'donor_media_sha256': expected_donor_media_sha256, 'evidence': evidence,
        'new_voice_requests': 0, 'new_video_requests': 0, 'qa_approved': False}
    if observe_only:
        return {'eligible': True, 'source_task_id': source_id, 'donor_task_id': donor_id,
            'new_voice_requests': 0, 'new_video_requests': 0}
    _require(client.set(key, plan._raw(claim), nx=True))
    _require(client.set(EXECUTION + source_id, claim['task_id'], nx=True))
    work = Path('/tmp/youtube_factory') / (str(uuid4()) + '_attempt_0')
    work.mkdir(parents=True, exist_ok=False); token = runtime._TASK_ID.set(source_id)
    try:
        client.set(STATUS + source_id, plan._raw({'state': 'preparing'}))
        prepared = _prepare(source, donor, work)
        # Recheck the actual current sources, owners and terminal preparation
        # after review, before installing the ordinary worker's private record.
        current, current_donor, _ = _scope(client, source_id, donor_id, expected_source_sha256,
            expected_donor_media_sha256)
        with client.pipeline() as pipe:
            from app.services import source_publication_hold, shorts_experiment_stock as stock
            pipe.watch(jobs.JOB_PREFIX + source_id, jobs.JOB_PREFIX + donor_id, key,
                recovery.RECORD + source_id, recovery.STATUS + source_id,
                plan.ACTIVE_KEY, plan.PLAN_PREFIX + source['spec']['production_channel_id'],
                plan.PLAN_PREFIX + donor['spec']['production_channel_id'])
            _require(plan._sha(plan._object(pipe.get(jobs.JOB_PREFIX + source_id))) == expected_source_sha256
                and _media_fingerprint(plan._object(pipe.get(jobs.JOB_PREFIX + donor_id))) == expected_donor_media_sha256
                and pipe.get(key) == plan._raw(claim) and not pipe.exists(recovery.RECORD + source_id))
            # Watch owner and channel fences before reading them again. A
            # cancellation or credential/profile change during review cannot
            # turn this preparation into permission to publish.
            for job in (source, donor):
                task = job['task_id']; channel = job['spec']['production_channel_id']
                entry_id = job['spec']['content_plan_item_id']
                pipe.watch(plan.DISPATCH_PREFIX + entry_id, plan.COMPLETION_PREFIX + entry_id,
                    plan.production.PROFILE_PREFIX + channel, plan.production.OAUTH_CHANNEL_PREFIX + channel,
                    plan.production.OAUTH_CREDENTIAL_PREFIX + channel, plan.production.OAUTH_CHANNEL_INDEX,
                    source_publication_hold.HOLD_PREFIX + task, plan.UPLOAD_PREFIX + task,
                    jobs.RENDER_CANCELLATION_PREFIX + task, jobs.QUALITY_HOLD_JOB_FENCE_PREFIX + task,
                    jobs.RETRY_DISPATCH_PREFIX + task, jobs.REPAIR_CHECKPOINT_PREFIX + task,
                    jobs.EXTERNAL_EPISODE_LEAF_PREFIX + task)
            _require(recovery._source(pipe, source_id) == source)
            for job in (source, donor):
                channel = job['spec']['production_channel_id']
                document = plan.read(channel, client=pipe)
                _require(document['enabled'])
                entry = next(row for row in document['items'] if row['id'] == job['spec']['content_plan_item_id'])
                _require(stock.member(pipe, channel, entry))
                if job is donor:
                    _require(stock.prepared(pipe, channel, entry) == donor)
            # The original v4 failure is never changed into a success. Its
            # unused record slot receives a separately attributed preparation.
            _require(_stopped_preparation(pipe, current) == evidence['native_preparation'])
            prepared['library_continuation'] = deepcopy(claim)
            pipe.multi(); pipe.set(recovery.RECORD + source_id, plan._raw(prepared), nx=True)
            _require(pipe.execute() == [True])
        child = str(uuid5(NAMESPACE_URL, 'owner-experiment-library-child:v1:' + source_id))
        dispatch_token = secrets.token_urlsafe(32)
        _require(jobs.claim_retry_dispatch(source_id, child, dispatch_token, allow_repair=False).get('claimed') is True)
        jobs.create_job(child, source['spec'], kind='render', parent_id=source_id)
        spec = source['spec']; options = {k: v for k, v in spec.items()
            if k not in {'topic', 'duration_minutes', 'language', 'channel_id'}}
        try:
            run_video_pipeline.apply_async(args=(spec['topic'], .5, spec['language'], spec['channel_id'],
                options, prepared['approved_package'], source_id, prepared['manifest']), task_id=child, retry=False)
        except Exception:
            jobs.mark_retry_dispatch(source_id, dispatch_token, 'uncertain')
            client.set(STATUS + source_id, plan._raw({'state': 'uncertain', 'task_id': child}))
            return {'status': 'dispatch_uncertain', 'task_id': child}
        jobs.mark_retry_dispatch(source_id, dispatch_token, 'dispatched')
        client.set(STATUS + source_id, plan._raw({'state': 'enqueued', 'task_id': child}))
        return {'status': 'enqueued', 'task_id': child, 'new_voice_requests': 0, 'new_video_requests': 0}
    except Exception as error:
        client.set(STATUS + source_id, plan._raw({'state': 'stopped', 'error_type': type(error).__name__}))
        raise
    finally:
        runtime._TASK_ID.reset(token); shutil.rmtree(work, ignore_errors=True)
