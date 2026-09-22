"""One retained-media continuation of a queued Short that failed during rendering.

Preparation reconstructs candidates from verified private storage, reviews the
unchanged story, then hands them to the ordinary speech/visual/render/publisher
gates. It cannot synthesize a voice or purchase a replacement clip. Unknown
dispatches retain their claims. Historical jobs and provider receipts survive.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import secrets
import shutil
from uuid import uuid4, uuid5, NAMESPACE_URL

from app.services import content_plan as plan, studio_state as jobs

LEGACY_PREFIX = plan.PREFIX + 'render_recovery:'
PREFIX = LEGACY_PREFIX + 'v4:'
DISPATCH = PREFIX + 'dispatch:'
EXECUTION = PREFIX + 'execution:'
RECORD = PREFIX + 'record:'
STATUS = PREFIX + 'status:'


def _require(value):
    plan._require(value, 'plan_render_recovery_unverified')


def _source(client, task, *, claimed=False):
    from app.services.source_publication_hold import HOLD_PREFIX
    from app.services.youtube_publish_state import UPLOAD_PREFIX
    source = plan._object(client.get(jobs.JOB_PREFIX + plan._id(task)))
    spec = source.get('spec') or {}
    _require(source.get('task_id') == task and source.get('kind') == 'render'
        and source.get('state') == 'FAILURE' and (source.get('failure_stage') == 'render' or _stock_failure(source))
        and source.get('parent_id') is None and not source.get('result')
        and spec.get('mode') == 'production' and spec.get('format') == 'shorts'
        and spec.get('duration_minutes') == .5 and spec.get('music') == 'off'
        and spec.get('production_scheduled') is True and spec.get('publish_after_render') is True
        and not source.get('publication_hold') and not source.get('owner_cancellation'))
    _require(not client.exists(*(prefix + task for prefix in
        (HOLD_PREFIX, UPLOAD_PREFIX, jobs.RENDER_CANCELLATION_PREFIX,
         jobs.EXTERNAL_EPISODE_LEAF_PREFIX, jobs.QUALITY_HOLD_JOB_FENCE_PREFIX))))
    if not claimed:
        _require(not any(source.get(k) for k in ('retry_child_task_id', 'retry_claimed', 'repair_claimed'))
            and not client.exists(jobs.RETRY_DISPATCH_PREFIX + task,
                                  jobs.REPAIR_CHECKPOINT_PREFIX + task))
    entry = plan._id(spec.get('content_plan_item_id'))
    dispatch = plan._object(client.get(plan.DISPATCH_PREFIX + entry))
    _require(dispatch['task_id'] == task and dispatch['spec_sha256'] == plan._sha(spec)
        and plan._active(client).get(dispatch['channel_id']) == entry)
    profile = plan._object(client.get(plan.production.PROFILE_PREFIX + dispatch['channel_id']))
    # Before the child exists this also verifies all previous public deliveries.
    leaf = plan._leaf(client, dispatch)
    _require(not claimed or (leaf.get('parent_id') and leaf.get('spec') == spec))
    plan.publication_series(leaf, profile, client=client)
    _require(profile.get('production_enabled') is True)
    channel = plan._object(client.get(plan.production.OAUTH_CHANNEL_PREFIX + dispatch['channel_id']))
    _require(channel.get('connection_id') == dispatch['connection_id']
        and channel.get('requires_reconnect') is not True
        and client.exists(plan.production.OAUTH_CREDENTIAL_PREFIX + dispatch['channel_id']))
    return source


def _fingerprint(source):
    return plan._sha({k: source.get(k) for k in ('task_id', 'spec', 'state', 'failure_stage',
        'audio_candidate_checkpoint', 'generated_asset_candidates', 'included_stock_pools',
        'paid_create_slots_used', 'preview_total_paid_create_cap')})


def eligible(source):
    """Cheap scheduling hint only; preparation independently verifies authority."""
    journal = source.get('generated_asset_candidates') or {}
    if _stock_failure(source):
        return bool(source.get('parent_id') is None and not source.get('retry_child_task_id')
            and (source.get('spec') or {}).get('content_plan_item_id')
            and source.get('audio_candidate_checkpoint') and source.get('included_stock_pools')
            and type(source.get('paid_create_slots_used')) is int and 0 <= source['paid_create_slots_used'] <= 6)
    return bool(source.get('state') == 'FAILURE' and source.get('failure_stage') == 'render'
        and source.get('parent_id') is None and not source.get('retry_child_task_id')
        and (source.get('spec') or {}).get('content_plan_item_id')
        and source.get('audio_candidate_checkpoint') and source.get('included_stock_pools')
        and type(journal.get('preserved_count')) is int and 1 <= journal['preserved_count'] <= 6
        and journal.get('failed_count') == 0
        and journal.get('attempted_count') == journal['preserved_count']
        and source.get('paid_create_slots_used') == journal['preserved_count'])


def schedule(source, enqueue, *, client=None):
    from app.services import content_plan_retained_completion as completion
    if completion.eligible(source):
        return completion.schedule(source, enqueue, client=client)
    from app.services import content_plan_long_media_resume
    if content_plan_long_media_resume.eligible(source):
        return content_plan_long_media_resume.schedule(source, enqueue, client=client)
    from app.services import content_plan_story_resume
    if content_plan_story_resume.eligible(source):
        return content_plan_story_resume.schedule(source, enqueue, client=client)
    from app.services import content_plan_voice_resume
    if content_plan_voice_resume.eligible(source):
        return content_plan_voice_resume.schedule(source, enqueue, client=client)
    from app.services import content_plan_model_resume
    if content_plan_model_resume.eligible(source):
        return content_plan_model_resume.schedule(source, enqueue, client=client)
    from app.services import content_plan_research_resume
    if content_plan_research_resume.eligible(source):
        return content_plan_research_resume.schedule(source, enqueue, client=client)
    from app.services import content_plan_local_resume
    if content_plan_local_resume.eligible(source):
        return content_plan_local_resume.schedule(source, enqueue, client=client)
    if not eligible(source):
        return 'working_or_blocked'
    client = client or plan._client(); task = source['task_id']
    if not _prior_preparation_allows(client, task):
        return 'repair_preparing_or_stopped'
    operation = str(uuid5(NAMESPACE_URL, 'owner-plan-render-recovery:v4:' + task))
    claim = {'version': 1, 'source_task_id': task, 'task_id': operation,
             'source_sha256': _fingerprint(source)}
    # A lost ACK cannot authorize a second queue send.
    if not client.set(DISPATCH + task, plan._raw(claim), nx=True):
        return 'repair_preparing_or_stopped'
    try:
        enqueue(args=(task,), task_id=operation, retry=False)
    except Exception:
        return 'repair_dispatch_uncertain'
    return 'repair_preparing'


def _prior_preparation_allows(client, task):
    """Only the known local v1 package-normalization error admits a new attempt.

    Its worker failed before the story critic or any media/provider write.
    Preserve the old execution/dispatch/terminal evidence. Unknown, completed,
    or later failures cannot obtain a second provider request by upgrading.
    """
    prefix = LEGACY_PREFIX + 'v3:'
    previous = client.get(prefix + 'dispatch:' + task)
    if previous is not None:
        dispatch = plan._object(previous)
        execution = client.get(prefix + 'execution:' + task)
        status = plan._object(client.get(prefix + 'status:' + task))
        raw = client.get('celery-task-meta-' + str(execution))
        if (client.exists(prefix + 'record:' + task) or execution != dispatch.get('task_id')
                or status != {'state': 'stopped', 'error_type': 'AbacusRouterError'} or raw is None):
            return False
        terminal = plan._object(raw); result = terminal.get('result') or {}
        if not (terminal.get('status') == 'FAILURE' and type(result) is dict
                and result.get('exc_type') == 'AbacusRouterError'
                and result.get('exc_message') == ['abacus_router_request_invalid']
                and 'File "/app/app/services/abacus_router_adapter.py", line 137, in _inspect_body' in (terminal.get('traceback') or '')
                and 'File "/app/app/services/content_plan_stock_repair.py", line 91, in select' in (terminal.get('traceback') or '')
                and dispatch.get('source_sha256') == _fingerprint(_source(client, task))):
            return False
    newer = client.get(LEGACY_PREFIX + 'v2:dispatch:' + task)
    if newer is not None:
        prefix = LEGACY_PREFIX + 'v2:'
        dispatch = plan._object(newer)
        status = plan._object(client.get(prefix + 'status:' + task))
        execution = client.get(prefix + 'execution:' + task)
        raw = client.get('celery-task-meta-' + str(execution))
        if (client.exists(prefix + 'record:' + task) or execution != dispatch.get('task_id')
                or status != {'state': 'stopped', 'error_type': 'RuntimeError'} or raw is None):
            return False
        terminal = plan._object(raw); result = terminal.get('result') or {}
        if not (terminal.get('status') == 'FAILURE' and type(result) is dict
                and result.get('exc_type') == 'RuntimeError'
                and result.get('exc_message') == ['Fresh immutable story approval failed its final integrity check']
                and 'File "/app/app/services/director.py", line 3376, in revalidate_immutable_short_story' in (terminal.get('traceback') or '')
                and dispatch.get('source_sha256') == _fingerprint(_source(client, task))):
            return False
    old = client.get(LEGACY_PREFIX + 'dispatch:' + task)
    if old is None:
        return True
    if client.exists(LEGACY_PREFIX + 'record:' + task):
        return False
    dispatch = plan._object(old)
    status = plan._object(client.get(LEGACY_PREFIX + 'status:' + task))
    if status != {'state': 'stopped', 'error_type': 'ContentPlanError'}:
        return False
    execution = client.get(LEGACY_PREFIX + 'execution:' + task)
    if execution != dispatch.get('task_id'):
        return False
    raw = client.get('celery-task-meta-' + execution)
    if raw is None:
        return False
    terminal = plan._object(raw)
    result = terminal.get('result') or {}
    return bool(terminal.get('status') == 'FAILURE' and type(result) is dict
        and result.get('exc_type') == 'ContentPlanError'
        and 'File "/app/app/services/content_plan_recovery.py", line 140, in prepare' in (terminal.get('traceback') or '')
        and dispatch.get('source_sha256') == _fingerprint(_source(client, task)))


def _stock_failure(source):
    classification = source.get('failure_classification') or {}
    return (source.get('state') == 'FAILURE'
        and source.get('failure_stage') in {'final_visual_qc', 'final_visual_qc_rescue'}
        and classification.get('category') == 'content_rejected'
        and classification.get('code') == 'visual_quality_exhausted')


def _stored(client, key, digest, size, destination, maximum):
    from app.services.voice_candidate_recovery import _download_bounded
    actual = _download_bounded(client, key, destination, maximum, expected_size=size)
    _require(actual == (digest, size))
    return destination


def prepare(task, work):
    """Read exact saved candidates, then independently review unchanged wording."""
    from app import tasks
    from app.services import storage, director, generated_asset_checkpoint as assets
    from app.services.voice_candidate_recovery import load_voice_retry_candidate, require_unchanged_voice_narration
    from app.services.production_included_router import _cipher
    from app.services.production_spend_runtime import resolve_context
    client = plan._client(); source = _source(client, task)
    _require(eligible(source)); fingerprint = _fingerprint(source)
    context = resolve_context(client, task)
    stock_only = _stock_failure(source)
    journal = source.get('generated_asset_candidates') or {}; used = source['paid_create_slots_used']
    budget = client.hgetall(jobs.PAID_CREATE_BUDGET_PREFIX + task)
    _require(budget == {'cap': str(source['preview_total_paid_create_cap']), 'used': str(used)}
        and (stock_only or len(journal['entries']) == used == journal['preserved_count']))
    # Every charged generation has a captured result, not just a local filename.
    from app.services import commissioning_video
    native_raw = client.get(commissioning_video.PREFIX + task)
    native = plan._object(native_raw) if native_raw is not None else {'context': context, 'requests': {}}
    _require(native.get('context') == context and len(native.get('requests', {})) == used
        and all(row.get('create') and row.get('result') for row in native['requests'].values()))
    candidate = load_voice_retry_candidate(task, work.name.split('_attempt_')[0],
                                            source['audio_candidate_checkpoint'], work)
    original, voice = candidate['package'], candidate['voice_result']
    # The loader adds the joined narration for downstream callers. Durable
    # candidate manifests intentionally contain only the canonical package.
    canonical = assets._candidate_package(original)
    _require(plan._sha(canonical) == source['audio_candidate_checkpoint']['package_sha256'])
    _require(len(original['scenes']) == 6)
    object_store = storage._client(); generated = {}
    for ordinal, pointer in enumerate([] if stock_only else journal['entries']):
        _require(pointer.get('source_task_id') == task and pointer.get('status') == 'preserved_candidate'
            and pointer.get('qa_approved') is False and pointer.get('requires_full_qa') is True
            and pointer.get('audio_sha256') == candidate['audio_sha256'])
        manifest_path = _stored(object_store, pointer['manifest_key'], pointer['manifest_sha256'],
                                pointer['manifest_size'], work / f'generated-{ordinal}.json', assets.MAX_MANIFEST_BYTES)
        manifest = json.loads(manifest_path.read_text())
        index = manifest['scene_index']; _require(type(index) is int and 0 <= index < 6)
        _require(manifest['source_task_id'] == task and manifest['package'] == canonical
            and manifest['candidate_package_sha256'] == source['audio_candidate_checkpoint']['package_sha256']
            and manifest['audio']['sha256'] == candidate['audio_sha256']
            and manifest['raw']['key'] == pointer['raw_key']
            and manifest['raw']['sha256'] == pointer['raw_sha256']
            and manifest['raw']['size'] == pointer['raw_size']
            and manifest['raw']['synthetic_motion_only'] is False)
        raw = manifest['raw']; path = _stored(object_store, raw['key'], raw['sha256'], raw['size'],
                                              work / f'generated-{ordinal}.mp4', assets.MAX_RAW_BYTES)
        tasks._validate_recovered_generated_clip(path,
            minimum_duration=max(5., voice['scene_durations'][index] + .35),
            expected_size=raw['size'], expected_sha256=raw['sha256'])
        key = f'recovery/{task}/raw/scene-{index:02d}-initial.mp4'
        # Select the last preserved replacement for each scene, keep all receipts.
        generated[index] = (path, key, raw)
    pools = {}
    for pointer in source['included_stock_pools'].values():
        encoded = client.get(pointer['key'])
        _require(type(encoded) is str and hashlib.sha256(encoded.encode()).hexdigest() == pointer['sha256'])
        record = json.loads(_cipher().decrypt(encoded.encode()))
        scope = record['scope']
        _require(scope['context'] == context and scope['package'] == canonical
            and record['status'] == 'unapproved_stock_pool' and record['qa_approved'] is False)
        from app.services.included_stock_pool import _manifest
        _manifest(record, scope)
        pools[scope['phase']] = record
    chosen = next((pools[phase] for phase in ('budget_rescue', 'before_generation', 'initial') if phase in pools), None)
    _require(chosen is not None)
    stocks = {}; selection = None; credits = chosen['credits']
    if stock_only:
        from app.services.content_plan_stock_repair import select
        stocks, credits, selection = select(original, voice, pools, work, source['spec'])
    for index in ([] if stock_only else set(range(6)) - set(generated)):
        # The final stock pool must already have a single selected candidate.
        _require(len(chosen['pools'][index]) == 1)
        row = deepcopy(chosen['pools'][index][0]); stocks[str(index)] = row
        path = _stored(object_store, row['key'], row['sha256'], row['size'],
                       work / f'stock-{index}.mp4', 128 * 1024 * 1024)
        # Detect local crop/codec problems before spending another critic call.
        from app.services import render
        render.normalize_clip({**row['spec'], 'path': str(path)}, work / f'probe-{index}.mp4',
                              voice['scene_durations'][index], index, 'cut', '1080x1920')
    spec = source['spec']; options = {k: v for k, v in spec.items()
        if k not in {'topic', 'duration_minutes', 'language', 'channel_id'}}
    kwargs = ({'verified_spoken_word_budget': director.validate_spoken_word_budget(original['spoken_word_budget'])}
              if 'spoken_word_budget' in original else {})
    story = deepcopy(original)
    if kwargs:
        budget = kwargs['verified_spoken_word_budget']
        story['target_word_range'] = [budget['minimum_words'], budget['maximum_words']]
    reviewed = director.revalidate_immutable_short_story(story, spec['topic'], .5,
        spec['language'], options, immutable_candidate_narrations=[s['narration'] for s in original['scenes']],
        immutable_scene_fields=True, **kwargs)
    require_unchanged_voice_narration(original, reviewed)
    _require(reviewed['scenes'] == original['scenes']
        and director.short_story_package_is_approved(reviewed, spec['topic']))
    package_hash = tasks._recovery_package_sha256(reviewed)
    media = {'version': 7 if stock_only else 3, 'recovery_only': True, 'source_task_id': task,
             'package_sha256': package_hash, 'scenes': {}}
    for index, (path, key, raw) in generated.items():
        with path.open('rb') as body:
            assets._put_immutable(object_store, key, body, raw['sha256'], raw['size'], 'video/mp4')
        media['scenes'][str(index)] = [{'key': key, 'sha256': raw['sha256'], 'size': raw['size'],
            'provider': raw['provider'], 'provider_attempts': raw['provider_attempts'],
            'synthetic_motion_only': False, 'motion_recipe_version': None, 'source_media_type': 'video'}]
    audio_path = Path(voice['path']); audio_key = f'recovery/{task}/raw/voice.mp3'
    with audio_path.open('rb') as body:
        assets._put_immutable(object_store, audio_key, body, candidate['audio_sha256'], audio_path.stat().st_size, 'audio/mpeg')
    audio = {'version': 1, 'source_task_id': task, 'package_sha256': package_hash,
        'key': audio_key, 'sha256': candidate['audio_sha256'], 'size': audio_path.stat().st_size,
        **{key: voice[key] for key in ('scene_durations', 'spoken_texts', 'duration_before_fit',
        'duration_after_fit', 'tempo_rate', 'content_target_seconds', 'reserved_tail_seconds')}}
    tasks._validated_recovered_generated_media(media, 6, package_hash)
    tasks._validated_recovered_voice(audio, 6, package_hash)
    reviewed.update(_recovered_generated_media=media, _recovered_voice=audio)
    manifest = {'version': 1, 'kind': 'owner_plan_retained', 'source_task_id': task,
                'package_sha256': package_hash, 'stocks': stocks, 'credits': credits}
    if stock_only:
        manifest['stock_selection'] = selection
    record = {'version': 1, 'source_task_id': task, 'source_sha256': fingerprint,
              'approved_package': reviewed, 'manifest': manifest}
    with client.pipeline() as pipe:
        pipe.watch(jobs.JOB_PREFIX + task, RECORD + task)
        _require(_fingerprint(_source(pipe, task)) == fingerprint and not pipe.exists(RECORD + task))
        pipe.multi(); pipe.set(RECORD + task, plan._raw(record), nx=True)
        _require(pipe.execute() == [True])
    return record


def saved_record(client, task):
    records = [client.get(prefix + 'record:' + task)
               for prefix in (PREFIX, LEGACY_PREFIX + 'v3:')]
    records = [plan._object(raw) for raw in records if raw is not None]
    _require(len(records) == 1)
    return records[0]


def verify_child(task, source_task, runtime_spec, package, manifest):
    from app.services import content_plan_local_resume
    client = plan._client()
    child = plan._object(client.get(jobs.JOB_PREFIX + task))
    if child.get('parent_id') != manifest.get('source_task_id'):
        return content_plan_local_resume.verify_child(client, task, source_task, runtime_spec, package, manifest)
    client = plan._client(); source = _source(client, source_task, claimed=True)
    record = saved_record(client, source_task)
    claim = client.hgetall(jobs.RETRY_CHILD_CLAIM_PREFIX + task)
    _require(record['source_sha256'] == _fingerprint(source)
        and record['approved_package'] == package and record['manifest'] == manifest
        and source['retry_child_task_id'] == task and child.get('parent_id') == source_task
        and child['spec'] == runtime_spec == source['spec']
        and claim.get('source_task_id') == source_task and type(claim.get('token')) is str
        and len(claim['token']) >= 16
        and client.get(jobs.RETRY_CHILD_EXECUTION_PREFIX + task) == claim.get('token'))
    return source


def load_visuals(manifest, source, package, media, voice, task, work):
    from app import tasks
    from app.services import storage
    verify_child(task, source['task_id'], source['spec'], package, manifest)
    object_store = storage._client(); pools = [[] for _ in range(6)]; ids = set()
    for key, row in manifest['stocks'].items():
        index = int(key)
        path = _stored(object_store, row['key'], row['sha256'], row['size'],
                       work / f'plan-stock-{index}.mp4', 128 * 1024 * 1024)
        pools[index] = [{**row['spec'], 'path': str(path), 'curated_pinned': True,
                         'preserve_start_fraction': True}]
        ids.add(row['spec']['pexels_id'])
    for index, entries in media['scenes'].items():
        _require(len(entries) == 1 and not pools[index])
        row = entries[0]
        path = _stored(object_store, row['key'], row['sha256'], row['size'],
                       work / f'recovered_s{index:02d}_00.mp4', 100 * 1024 * 1024)
        spec = tasks._generated_visual_spec(path, provider=row['provider'], provider_attempts=row['provider_attempts'])
        spec.update(curated_pinned=True, preserve_start_fraction=True, generation_recovered=True,
                    recovered_from_task_id=source['task_id'])
        pools[index] = [spec]
    _require(all(len(row) == 1 for row in pools))
    tasks._require_unique_selected_stock(pools)
    return {'scene_visuals': pools, 'credits': manifest['credits'], 'seen_ids': ids}


def run(source_id, operation_id):
    from app.services import content_plan_retained_completion as completion
    if operation_id == completion.operation(source_id):
        return completion.run(source_id, operation_id)
    from app.services import content_plan_long_media_resume
    if operation_id == content_plan_long_media_resume.operation(source_id):
        return content_plan_long_media_resume.run(source_id, operation_id)
    from app.services import content_plan_story_resume
    if operation_id in {content_plan_story_resume.operation(source_id), content_plan_story_resume.validation_operation(source_id)}:
        return content_plan_story_resume.run(source_id, operation_id)
    from app.services import content_plan_voice_resume
    if operation_id == content_plan_voice_resume.operation(source_id):
        return content_plan_voice_resume.run(source_id, operation_id)
    from app.services import content_plan_model_resume
    if operation_id == content_plan_model_resume.operation(source_id):
        return content_plan_model_resume.run(source_id, operation_id)
    from app.services import content_plan_research_resume
    if content_plan_research_resume.is_operation(operation_id, source_id):
        return content_plan_research_resume.run(source_id, operation_id)
    from app.services import content_plan_local_resume
    if content_plan_local_resume.is_operation(operation_id, source_id):
        return content_plan_local_resume.run(source_id, operation_id)
    from app.services import production_spend_runtime as runtime
    from app.tasks import run_video_pipeline
    client = plan._client()
    claim = plan._object(client.get(DISPATCH + source_id))
    _require(claim['task_id'] == operation_id)
    if not client.set(EXECUTION + source_id, operation_id, nx=True):
        return {'status': 'already_started'}
    prep_id = str(uuid4()); work = Path('/tmp/youtube_factory') / (prep_id + '_attempt_0')
    work.mkdir(parents=True, exist_ok=False)
    token = runtime._TASK_ID.set(source_id)
    try:
        client.set(STATUS + source_id, plan._raw({'state': 'preparing'}))
        _require(_fingerprint(_source(client, source_id)) == claim['source_sha256'])
        record = prepare(source_id, work)
        source = _source(client, source_id)
        child = str(uuid5(NAMESPACE_URL, 'owner-plan-retained-child:' + source_id))
        dispatch_token = secrets.token_urlsafe(32)
        reserved = jobs.claim_retry_dispatch(source_id, child, dispatch_token, allow_repair=False)
        _require(reserved.get('claimed') is True)
        jobs.create_job(child, source['spec'], kind='render', parent_id=source_id)
        spec = source['spec']; options = {k: v for k, v in spec.items()
            if k not in {'topic', 'duration_minutes', 'language', 'channel_id'}}
        try:
            run_video_pipeline.apply_async(args=(spec['topic'], .5, spec['language'], spec['channel_id'],
                options, record['approved_package'], source_id, record['manifest']), task_id=child, retry=False)
        except Exception:
            jobs.mark_retry_dispatch(source_id, dispatch_token, 'uncertain')
            client.set(STATUS + source_id, plan._raw({'state': 'uncertain', 'task_id': child}))
            return {'status': 'dispatch_uncertain', 'task_id': child}
        jobs.mark_retry_dispatch(source_id, dispatch_token, 'dispatched')
        client.set(STATUS + source_id, plan._raw({'state': 'enqueued', 'task_id': child}))
        return {'status': 'enqueued', 'task_id': child, 'new_voice_requests': 0, 'new_video_requests': 0}
    except Exception as error:
        try:
            client.set(STATUS + source_id, plan._raw({'state': 'stopped', 'error_type': type(error).__name__}))
        except Exception:
            pass
        raise
    finally:
        runtime._TASK_ID.reset(token)
        shutil.rmtree(work, ignore_errors=True)
