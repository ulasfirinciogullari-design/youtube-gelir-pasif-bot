"""One bound completion for captured quota or lost public-citation failures.

Original speech and provider receipts survive. Quota recovery cannot send a
new video request; source recovery restores only a captured public reference.
Neither path approves narration, facts, visuals, rendering or publication.
"""
from copy import deepcopy
import hashlib
import json
import re
import secrets
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit
from uuid import uuid5, NAMESPACE_URL

from app.services import content_plan as plan, studio_state as jobs
from app.services import content_plan_voice_resume as voice, content_plan_research_resume as pre
from app.services import content_plan_interruption as interruption

PREFIX = plan.PREFIX + 'retained_completion:v1:'
DISPATCH, EXECUTION, ROOT = (PREFIX + k for k in ('dispatch:', 'execution:', 'root:'))
REVIEW_ROOT = PREFIX + 'visual_review_root:'
RECOVERY_ROOT = PREFIX + 'availability_recovery_root:'
RENDER_ROOT = PREFIX + 'render_completion_root:'
DEFERRED_ROOT = PREFIX + 'deferred_quota_root:'
WINDOW_ROOT = PREFIX + 'cut_window_root:'
ASSEMBLY_ROOT = PREFIX + 'assembly_memory_root:'
MOTION_ROOT = PREFIX + 'motion_repair_root:'
INTERRUPTION_ROOT = PREFIX + 'removed_worker_root:'
VISUAL_COMPLETION_ROOT = PREFIX + 'bounded_visual_completion_root:'
ACCEPTED_OUTPUT_ROOT = PREFIX + 'accepted_output_root:'
SOURCE_ERROR = 'Long documentary failed independent source or editorial review'
QUOTA_ERROR = 'commissioning_video_provider_rejected'
VISUAL_ERROR = 'Final visual quality gate rejected: '
LOCAL_ERROR = 'Long-form retained job stopped without automatic restart: WatchError'
RENDER_ERROR = 'Long-form retained job stopped without automatic restart: RuntimeError'
ASSEMBLY_ERROR = 'Long-form retained job stopped without automatic restart: CalledProcessError'


def _motion_failure(source):
    match = re.fullmatch(r'Final motion gate rejected ([0-9]+\.[0-9])s static interval \(limit 6\.0s\)',
                         str(source.get('error') or ''))
    return bool(source.get('failure_stage') == 'render' and match and 6 < float(match[1]) <= 240)


def _post_stock_failure(source):
    retained = source.get('retained_long_media') or {}
    return bool(source.get('failure_stage') == 'final_visual_qc_rescue'
        and str(source.get('error') or '').startswith(VISUAL_ERROR)
        and retained.get('stock_only') is True and retained.get('new_tts_requests') == 0)


def operation(source):
    return str(uuid5(NAMESPACE_URL, 'owner-plan-retained-completion:v1:' + source))


def registered(source):
    return bool(plan._client().exists(DISPATCH + plan._id(source)))


def eligible(source):
    spec = source.get('spec') or {}
    return bool(source.get('state') == 'FAILURE' and spec.get('content_plan_item_id')
        and spec.get('format') == 'landscape' and spec.get('duration_minutes') == 3
        and source.get('parent_id') and not source.get('retry_child_task_id')
        and ((source.get('failure_stage') == 'final_visual_qc_ai_repair' and source.get('error') == QUOTA_ERROR)
             or (source.get('failure_stage') == 'director_qc' and source.get('error') == SOURCE_ERROR
                 and plan._client().exists(voice.DISPATCH + source['parent_id']))
             or (source.get('failure_stage') == 'final_visual_qc_rescue'
                 and str(source.get('error') or '').startswith(VISUAL_ERROR)
                 and plan._client().exists(DISPATCH + source['parent_id']))
             or (source.get('failure_stage') == 'final_visual_qc_ai_repair' and source.get('error') == LOCAL_ERROR
                 and plan._client().exists(DISPATCH + source['parent_id']))
             or (source.get('failure_stage') == 'render' and source.get('error') in {RENDER_ERROR, ASSEMBLY_ERROR}
                 and plan._client().exists(DISPATCH + source['parent_id']))
             or (_motion_failure(source) and plan._client().exists(DISPATCH + source['parent_id']))
             or (interruption.eligible(source) and plan._client().exists(DISPATCH + source['parent_id']))))


def _prior_completion(client, source):
    """One full visual recheck of a private completion, never a QA override."""
    parent = source['parent_id']; claim = plan._object(client.get(DISPATCH + parent))
    mode = (claim.get('evidence') or {}).get('mode')
    if claim['evidence'].get('visual_completion_of'):
        from app.services.accepted_video_outputs import failed_scenes
        failed_scenes(source)
        previous = plan._object(client.get(jobs.JOB_PREFIX + parent))
        plan._require(mode == 'sources' and not claim['evidence'].get('accepted_output_of')
            and claim.get('source_task_id') == parent and claim.get('task_id') == operation(parent)
            and client.get(EXECUTION + parent) == operation(parent)
            and client.get(_root_key(claim['root_task_id'], claim['evidence'])) == plan._raw(claim)
            and pre.fingerprint(previous) == claim['evidence']['source_sha256']
            and source['task_id'] == str(uuid5(NAMESPACE_URL, 'owner-plan-retained-completion-child:v1:' + parent)))
        return claim
    if interruption.eligible(source):
        witness = interruption.proof(client, source)
        previous = plan._object(client.get(jobs.JOB_PREFIX + parent))
        plan._require(mode == 'sources' and claim['evidence'].get('motion_of')
            and not claim['evidence'].get('interruption_of')
            and witness['root_task_id'] == claim['root_task_id']
            and claim.get('source_task_id') == parent and claim.get('task_id') == operation(parent)
            and client.get(EXECUTION + parent) == operation(parent)
            and client.get(_root_key(claim['root_task_id'], claim['evidence'])) == plan._raw(claim)
            and pre.fingerprint(previous) == claim['evidence']['source_sha256']
            and source['task_id'] == str(uuid5(NAMESPACE_URL, 'owner-plan-retained-completion-child:v1:' + parent)))
        return claim
    repair = bool(claim['evidence'].get('review_of'))
    assembly_failure = source.get('failure_stage') == 'render' and source.get('error') == ASSEMBLY_ERROR
    render_failure = source.get('failure_stage') == 'render' and source.get('error') in {RENDER_ERROR, ASSEMBLY_ERROR}
    deferred = bool(mode == 'sources' and claim['evidence'].get('recovery_of'))
    motion_failure = _motion_failure(source)
    post_stock = _post_stock_failure(source)
    if post_stock:
        plan._require(mode == 'sources' and (claim['evidence'].get('motion_of')
            or claim['evidence'].get('interruption_of')) and not claim['evidence'].get('visual_completion_of')
            and (source.get('audio_candidate_checkpoint') or {}).get('audio_sha256')
                == claim['evidence']['transcript']['audio_sha256'])
    plan._require(mode in {'quota', 'sources'} and not claim['evidence'].get('assembly_of')
        and not claim['evidence'].get('accepted_output_of')
        and not claim['evidence'].get('visual_completion_of')
        and (not claim['evidence'].get('motion_of') or post_stock)
        and (not claim['evidence'].get('window_of') or assembly_failure)
        and (not claim['evidence'].get('render_of') or render_failure)
        and (not claim['evidence'].get('deferred_of') or motion_failure)
        and (not claim['evidence'].get('recovery_of') or render_failure or deferred)
        and claim.get('source_task_id') == parent and claim.get('task_id') == operation(parent)
        and client.get(EXECUTION + parent) == operation(parent)
        and client.get(_root_key(claim['root_task_id'], claim['evidence'])) == plan._raw(claim)
        and source['task_id'] == str(uuid5(NAMESPACE_URL, 'owner-plan-retained-completion-child:v1:' + parent)))
    previous = plan._object(client.get(jobs.JOB_PREFIX + parent))
    plan._require(pre.fingerprint(previous) == claim['evidence']['source_sha256'])
    if motion_failure:
        classification = source.get('failure_classification') or {}
        plan._require(mode == 'sources' and claim['evidence'].get('deferred_of')
            and classification.get('category') == 'content_rejected'
            and classification.get('code') == 'render_quality_exhausted'
            and classification.get('stage') == 'render'
            and classification.get('error_sha256') == hashlib.sha256(source['error'].encode()).hexdigest())
        return claim
    if render_failure:
        plan._require(mode == 'quota' and claim['evidence'].get('recovery_of'))
        terminal = plan._object(client.get('celery-task-meta-' + source['task_id']))
        trace = terminal.get('traceback') or ''
        if assembly_failure:
            plan._require(claim['evidence'].get('window_of')
                and all(marker in trace for marker in ('in render_video\n', '_run(concat_command)\n',
                    'in _run\n', 'subprocess.CalledProcessError: Command ', 'concat=n=30:v=1:a=0',
                    "/silent.mp4']' died with <Signals.SIGKILL: 9>.\n"))
                and all(f"/{source['task_id']}_attempt_0/norm_{index:03d}.mp4" in trace for index in range(30))
                and 'ConnectionError' not in trace and 'TimeoutError' not in trace)
            return claim
        markers = (('RuntimeError: Generated clip is too short for a single-pass scene: 8.000s source for 6.867s segment\n',)
            if claim['evidence'].get('render_of') else ('in render_attempt\n',
            'RuntimeError: Normalized clip frame gate rejected segment: 150 frames for 151 frame target\n'))
        plan._require(all(marker in trace for marker in ('in render_video\n', 'in normalize_clip\n', *markers))
            and 'ConnectionError' not in trace and 'TimeoutError' not in trace)
        return claim
    if source['error'] == LOCAL_ERROR:
        plan._require(repair and mode == 'quota' and source.get('failure_stage') == 'final_visual_qc_ai_repair')
        terminal = plan._object(client.get('celery-task-meta-' + source['task_id']))
        trace = terminal.get('traceback') or ''
        plan._require('redis.exceptions.WatchError: Watched variable changed.' in trace
            and all(marker in trace for marker in ('in _reserve_paid_create_slot\n', 'in enabled_for_task\n',
                'in _execute_transaction\n', 'commissioning_video.py'))
            and 'ConnectionError' not in trace and 'TimeoutError' not in trace)
        return claim
    if deferred:
        plan._require(deferred_quota(client, source) is not None)
    plan._require(not repair or mode == 'sources')
    diagnostic = json.loads(source['error'][len(VISUAL_ERROR):])
    rejected = diagnostic.get('rejected')
    plan._require(diagnostic.get('stage') == 'after_rescue' and diagnostic.get('total') == 30
        and type(rejected) is dict and 1 <= len(rejected) <= 30
        and diagnostic.get('accepted') == 30 - len(rejected)
        and all(str(int(index)) == index and 0 <= int(index) < 30 and type(row.get('score')) is int
                and 0 <= row['score'] < 86 for index, row in rejected.items())
        and diagnostic.get('repair_checkpoint_available') is False)
    classification = source.get('failure_classification') or {}
    plan._require(classification.get('category') == 'content_rejected'
        and classification.get('code') == 'visual_quality_exhausted'
        and classification.get('error_sha256') == hashlib.sha256(source['error'].encode()).hexdigest())
    if repair:
        failures = (diagnostic.get('provider_generation_failures') or {}).get('failures')
        plan._require(type(failures) is list and failures
            and any(row.get('exception_class') == 'CommissionedVideoUnavailable' for row in failures)
            and all(row.get('exception_class') in {'CommissionedVideoUnavailable', 'WatchError'} for row in failures))
    return claim


def _root_key(root, proof):
    return (ACCEPTED_OUTPUT_ROOT if proof.get('accepted_output_of') else VISUAL_COMPLETION_ROOT if proof.get('visual_completion_of') else INTERRUPTION_ROOT if proof.get('interruption_of') else MOTION_ROOT if proof.get('motion_of') else ASSEMBLY_ROOT if proof.get('assembly_of') else WINDOW_ROOT if proof.get('window_of') else DEFERRED_ROOT if proof.get('deferred_of') else RENDER_ROOT if proof.get('render_of') else RECOVERY_ROOT if proof.get('recovery_of')
            else REVIEW_ROOT if proof.get('review_of') else ROOT) + root


def deferred_quota(client, source, *, require_current_credential=True):
    """One captured rejected create can wait for the next Pacific daily reset.

    The provider does not identify which quota was exhausted. Waiting through
    the daily boundary is a conservative retry time, not a promise of capacity.
    No accepted or ambiguous operation can authorize this new request.
    Displaying a historical wait does not require the web service to hold the
    worker's generation credential. Every scheduling/execution call keeps the
    default credential check; this flag never changes a dispatch proof.
    """
    if not (source.get('state') == 'FAILURE' and source.get('failure_stage') == 'final_visual_qc_rescue'
            and str(source.get('error') or '').startswith(VISUAL_ERROR) and source.get('parent_id')):
        return None
    raw = client.get(DISPATCH + source['parent_id'])
    if raw is None: return None
    prior = plan._object(raw); evidence = prior.get('evidence') or {}
    if evidence.get('mode') != 'sources' or not evidence.get('recovery_of') or evidence.get('deferred_of'):
        return None
    from app.services import commissioning_video as video
    from zoneinfo import ZoneInfo
    root = prior['root_task_id']; raw = client.get(video.PREFIX + root)
    journal = plan._object(raw); spec = source['spec']
    plan._require(journal.get('version') == 1 and journal.get('context') == {
        'lineage_id': root, 'kind': 'long', 'channel_id': spec['production_channel_id'],
        'connection_id': spec['production_connection_id']} and len(journal.get('requests', {})) >= 1)
    matches = []
    for identity, row in journal['requests'].items():
        descriptor = row['request']
        if descriptor.get('continuation_task_id') != source['task_id']:
            plan._require(descriptor.get('continuation_task_id') == source.get('retry_child_task_id')
                and source.get('retry_child_task_id') is not None)
            continue  # The started child's durable journal retains its ordinary no-replay handling.
        plan._require(identity == video._sha(video._raw(descriptor).encode())
            and descriptor.get('model') == video.MODEL
            and (not require_current_credential or descriptor.get('credential_sha256')
                 == video._sha(('gemini\0' + video.settings.gemini_api_key).encode()))
            and row.get('result') is None and row.get('create') and video.quota_rejected(row['create']))
        instant = datetime.fromisoformat(row['create']['observed_at'])
        plan._require(datetime.fromisoformat(source['created_at']) <= instant
            <= datetime.fromisoformat(source['updated_at']))
        matches.append((identity, row, instant))
    plan._require(len(matches) == 1 and not evidence.get('video_records'))
    identity, row, instant = matches[0]
    pacific = instant.astimezone(ZoneInfo('America/Los_Angeles'))
    reset = (pacific + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
    deadline = max(reset.astimezone(timezone.utc), instant + timedelta(hours=1))
    return {'retry_at': deadline.isoformat(), 'video_records': {identity: plan._sha(row)}}


def _quota_ready(client, source):
    """Wait for the actual quota window before admitting an unused repair."""
    from app.services import commissioning_video as video
    created = datetime.fromisoformat(source['created_at']); now = datetime.now(timezone.utc)
    credential = video._sha(('gemini\0' + video.settings.gemini_api_key).encode())
    deadlines = []; keys = list(client.scan_iter(match=video.PREFIX + '*', count=128))
    plan._require(len(keys) <= 2000)
    for key in keys:
        journal = plan._object(client.get(key))
        for row in journal.get('requests', {}).values():
            descriptor, observed = row.get('request') or {}, row.get('create')
            if (descriptor.get('model') != video.MODEL or descriptor.get('credential_sha256') != credential
                    or row.get('result') is not None or not observed or not video.quota_rejected(observed)):
                continue
            instant = datetime.fromisoformat(observed['observed_at'])
            plan._require(instant.tzinfo is not None)
            if instant <= created <= instant + timedelta(hours=1):
                deadlines.append(instant + timedelta(hours=1))
            plan._require(now > instant + timedelta(hours=1), 'plan_video_quota_waiting')
    plan._require(deadlines and now > max(deadlines), 'plan_video_quota_waiting')


def _metadata(source):
    from app.services import storage, audio_checkpoint
    pointer = source['audio_candidate_checkpoint']
    response = storage._client(single_attempt=True).get_object(Bucket=storage.settings.bucket, Key=pointer['metadata_key'])
    body = response['Body']
    try:
        raw = body.read(audio_checkpoint.MAX_AUDIO_CANDIDATE_METADATA_BYTES + 1)
    finally:
        body.close()
    plan._require(0 < len(raw) <= audio_checkpoint.MAX_AUDIO_CANDIDATE_METADATA_BYTES
        and hashlib.sha256(raw).hexdigest() == pointer['metadata_sha256'])
    return json.loads(raw)


def _restored_sources(client, root, saved_source):
    from app.services import audio_checkpoint as checkpoint, commissioning_reasoning as native
    from app.services import production_included_router as included
    old = _metadata(saved_source)['package']['sources']; matches = []
    keys = client.smembers(native.PREFIX + 'lineage:' + root); plan._require(1 <= len(keys) <= 160)
    for identity in keys:
        request = plan._object(client.get(native.PREFIX + 'request:' + identity))
        if request.get('purpose') != 'research':
            continue
        response = plan._object(client.get(native.PREFIX + 'response:' + identity))
        plan._require(request.get('request_sha256') == response.get('request_sha256') == identity
            and request.get('context', {}).get('lineage_id') == root and request.get('model') == native.MODEL
            and response.get('http_status') == 200 and request['reserved_at'] < saved_source['created_at'])
        raw = included._cipher().decrypt(response['encrypted_response'].encode())
        plan._require(hashlib.sha256(raw).hexdigest() == response['response_sha256'])
        candidates = json.loads(raw).get('candidates') or []
        plan._require(len(candidates) == 1 and candidates[0].get('finishReason') == 'STOP')
        texts = [p['text'] for p in candidates[0]['content']['parts'] if p.get('text') and not p.get('thought')]
        plan._require(len(texts) == 1)
        sources = json.loads(texts[0]).get('sources')
        if type(sources) is not list or checkpoint._public_sources(sources) != sources:
            continue
        retained = [row for row in sources if not urlsplit(row['url']).query]
        omitted = [row for row in sources if checkpoint._public_entry_query(urlsplit(row['url']))]
        if retained == old and len(omitted) == 1 and len(sources) == len(old) + 1:
            matches.append({'sources': sources, 'request_id': identity,
                'request_sha256': plan._sha(request), 'response_sha256': plan._sha(response)})
    plan._require(len(matches) == 1, 'plan_original_source_unverified')
    return matches[0]


def _video_records(client, root, ancestors, mode, *, child=None, accepted_output=False):
    from app.services import commissioning_video as video
    raw = client.get(video.PREFIX + root)
    motion_failure = _motion_failure(ancestors[0])
    interrupted = interruption.eligible(ancestors[0])
    post_stock = _post_stock_failure(ancestors[0])
    if mode == 'sources' and not (motion_failure or interrupted or post_stock or accepted_output):
        if raw is not None:
            journal = plan._object(raw)
            deferred = deferred_quota(client, ancestors[0])
            if deferred is not None:
                return deferred['video_records']
            plan._require(child is not None and journal.get('version') == 1
                and journal.get('context', {}).get('lineage_id') == root
                and all(row['request'].get('continuation_task_id') == child
                        for row in journal['requests'].values()))
        return {}
    journal = plan._object(raw); spec = ancestors[0]['spec']
    plan._require(journal.get('version') == 1 and journal.get('context') == {
        'lineage_id': root, 'kind': 'long', 'channel_id': spec['production_channel_id'],
        'connection_id': spec['production_connection_id']} and client.pttl(video.PREFIX + root) == -1)
    records = {}; quotas = 0; names = {r['task_id'] for r in ancestors}
    for identity, row in journal['requests'].items():
        descriptor = row['request']
        if accepted_output and descriptor.get('continuation_task_id') == ancestors[0]['task_id']:
            from app.services.accepted_video_outputs import completed
            plan._require(identity == video._sha(video._raw(descriptor).encode()))
            completed(row)
            records[identity] = plan._sha(row)
            continue
        if (motion_failure or post_stock) and child is not None and descriptor.get('continuation_task_id') == child:
            plan._require(identity == video._sha(video._raw(descriptor).encode()))
            if post_stock:
                rejected = json.loads(ancestors[0]['error'][len(VISUAL_ERROR):])['rejected']
                plan._require(str(descriptor.get('scene_index')) in rejected)
            continue  # New ordinary repairs retain their own durable request history.
        plan._require(identity == video._sha(video._raw(descriptor).encode())
            and descriptor.get('model') == video.MODEL and row.get('create')
            and descriptor.get('continuation_task_id') in names | {None})
        if video.quota_rejected(row['create']):
            plan._require(row.get('result') is None); quotas += 1
        else:
            plan._require(row.get('result'))
            created, result = video._payload(row['create']), video._payload(row['result'])
            plan._require(created.get('name') and created['name'] == result.get('name') and result.get('done') is True)
            try:
                video._result(result)
            except video.CommissionedVideoUnavailable as exc:
                plan._require((result.get('error') or {}).get('code') in {13, 14}
                    or ((motion_failure or interrupted or post_stock or accepted_output) and str(exc) == 'commissioned_video_completed_filtered'))
        records[identity] = plan._sha(row)
    plan._require(quotas == 1 and 1 <= len(records) <= 32
        and len(records) <= sum(r['paid_create_slots_used'] for r in ancestors))
    return records


def checked(client, task, *, claimed=False):
    from app.services.content_plan_local_resume import _claim
    from app.services.source_publication_hold import HOLD_PREFIX
    from app.services.youtube_publish_state import UPLOAD_PREFIX
    source = plan._object(client.get(jobs.JOB_PREFIX + plan._id(task))); spec = source.get('spec') or {}
    plan._require(eligible({**source, 'retry_child_task_id': None}) and source['task_id'] == task
        and voice._scope(spec) == ('long', 32) and spec.get('mode') == 'production')
    motion_failure = _motion_failure(source)
    interrupted = interruption.eligible(source)
    prior = _prior_completion(client, source) if interrupted or motion_failure or source['error'].startswith(VISUAL_ERROR) or source['error'] in {LOCAL_ERROR, RENDER_ERROR, ASSEMBLY_ERROR} else None
    mode = prior['evidence']['mode'] if prior else ('quota' if source['error'] == QUOTA_ERROR else 'sources')
    dispatch = plan._object(client.get(plan.DISPATCH_PREFIX + plan._id(spec['content_plan_item_id'])))
    root = dispatch['task_id']; ancestors = []; current = source
    while True:
        name = current['task_id']; used = current.get('paid_create_slots_used')
        plan._require(name not in {r['task_id'] for r in ancestors} and len(ancestors) < 12
            and current.get('kind') == 'render' and current.get('state') == 'FAILURE'
            and current.get('spec') == spec and not current.get('result')
            and type(used) is int and 0 <= used <= 32 and (prior or mode != 'sources' or used == 0)
            and current.get('preview_total_paid_create_cap') == 32
            and client.hgetall(jobs.PAID_CREATE_BUDGET_PREFIX + name) == {'cap': '32', 'used': str(used)}
            and not current.get('publication_hold') and not current.get('owner_cancellation')
            and not client.exists(*(p + name for p in (HOLD_PREFIX, UPLOAD_PREFIX, jobs.RENDER_CANCELLATION_PREFIX,
                jobs.EXTERNAL_EPISODE_LEAF_PREFIX, jobs.QUALITY_HOLD_JOB_FENCE_PREFIX, jobs.REPAIR_CHECKPOINT_PREFIX))))
        ancestors.append(current)
        if name == root:
            plan._require(current.get('parent_id') is None); break
        parent = plan._id(current.get('parent_id')); _claim(client, parent, name)
        current = plan._object(client.get(jobs.JOB_PREFIX + parent))
        plan._require(current.get('retry_child_task_id') == name)
    plan._require(plan.dispatch_spec_matches(dispatch, current['spec'])
        and plan._active(client).get(dispatch['channel_id']) == dispatch['item']['id'])
    if not claimed:
        plan._require(not any(source.get(k) for k in ('retry_child_task_id', 'retry_claimed', 'repair_claimed'))
            and not client.exists(jobs.RETRY_DISPATCH_PREFIX + task))
    if interrupted:
        interruption.proof(client, source)
    else:
        terminal = plan._object(client.get('celery-task-meta-' + task)); failure = terminal.get('result') or {}
        plan._require(terminal.get('task_id') == task and terminal.get('status') == 'FAILURE'
            and failure.get('exc_type') == ('ProductionContentError' if motion_failure else 'FinalVisualQualityError' if prior else
                                          'SpendBlocked' if mode == 'quota' else 'ProductionContentError')
            and failure.get('exc_message') == [source['error']]
            and (', in run_video_pipeline\n' if prior else ', in _payload\n' if mode == 'quota'
                 else ', in review_story\n') in (terminal.get('traceback') or ''))
    saved = source if prior or mode == 'quota' else ancestors[1]
    if mode == 'sources' and not prior:
        admission = plan._object(client.get(voice.DISPATCH + saved['task_id']))
        plan._require(admission.get('root_task_id') == root and admission.get('source_task_id') == saved['task_id']
            and client.get(voice.EXECUTION + saved['task_id']) == admission.get('task_id') == voice.operation(saved['task_id'])
            and source['task_id'] == str(uuid5(NAMESPACE_URL, 'owner-plan-voice-child:v1:' + saved['task_id']))
            and not source.get('audio_candidate_checkpoint') and not source.get('generated_asset_candidates'))
    transcript = voice._transcript_proof(client, saved, root)
    if prior:
        plan._require(prior['root_task_id'] == root
            and prior['evidence']['transcript']['audio_sha256'] == transcript['audio_sha256'])
    media = []
    for ancestor in reversed(ancestors):
        journal = ancestor.get('generated_asset_candidates')
        if journal:
            entries = journal.get('entries') or []
            plan._require((mode == 'quota' or prior) and journal.get('failed_count') == 0
                and journal.get('attempted_count') == journal.get('preserved_count') == len(entries)
                and 1 <= len(entries) <= ancestor['paid_create_slots_used']
                and ancestor['audio_candidate_checkpoint']['audio_sha256'] == transcript['audio_sha256'])
            media.append(ancestor['task_id'])
    plan._require(mode != 'quota' or media)
    profile = plan._object(client.get(plan.production.PROFILE_PREFIX + dispatch['channel_id']))
    leaf = plan._leaf(client, dispatch)
    plan._require(leaf['task_id'] == (source.get('retry_child_task_id') if claimed else task))
    plan.publication_series(leaf, profile, client=client)
    channel = plan._object(client.get(plan.production.OAUTH_CHANNEL_PREFIX + dispatch['channel_id']))
    plan._require(profile.get('production_enabled') is True and channel.get('connection_id') == dispatch['connection_id']
        and channel.get('requires_reconnect') is not True
        and client.exists(plan.production.OAUTH_CREDENTIAL_PREFIX + dispatch['channel_id']))
    proof = {'mode': mode, 'source_sha256': pre.fingerprint(source), 'saved_voice_source': saved['task_id'],
        'transcript': transcript, 'media_sources': media,
        'media_fingerprints': {r['task_id']: pre.fingerprint(r) for r in ancestors if r['task_id'] in media},
        'video_records': _video_records(client, root, ancestors, mode,
            child=source.get('retry_child_task_id') if claimed else None,
            accepted_output=bool(prior and prior['evidence'].get('visual_completion_of'))),
        'restored_source': _restored_sources(client, root, saved) if mode == 'sources' and not prior else None}
    if prior:
        deferred = deferred_quota(client, source)
        if prior['evidence'].get('visual_completion_of'):
            from app.services import accepted_video_outputs, commissioning_video
            plan._require(all(proof['video_records'].get(key) == digest
                for key, digest in prior['evidence']['video_records'].items()))
            selected = accepted_video_outputs.selected(
                plan._object(client.get(commissioning_video.PREFIX + root)), source, prior)
            proof.update(accepted_output_of=source['parent_id'],
                accepted_outputs={str(i): identity for i, identity in selected.items()})
        elif motion_failure:
            plan._require(proof['video_records'] and all(proof['video_records'].get(key) == digest
                for key, digest in prior['evidence']['video_records'].items()))
        else:
            plan._require(proof['video_records'] == (deferred['video_records'] if deferred else prior['evidence']['video_records']))
        proof.update(review_of=source['parent_id'], previous_completion_sha256=plan._sha(prior))
        if proof.get('accepted_output_of'):
            pass  # Read existing paid outputs; no further video or speech allowance.
        elif _post_stock_failure(source):
            rejected = sorted(int(i) for i in json.loads(source['error'][len(VISUAL_ERROR):])['rejected'])
            from app.services import commissioning_video as native_video
            journal = plan._object(client.get(native_video.PREFIX + root))
            # A completed provider filter is never a reason to try that scene
            # through another route. Successful existing scenes are also kept.
            filtered = []
            for row in journal['requests'].values():
                if not row.get('result') or row['request'].get('model') != native_video.MODEL:
                    continue
                response = native_video._payload(row['result']).get('response', {}).get('generateVideoResponse', {})
                if response.get('raiMediaFilteredCount', 0):
                    filtered.append(row['request'].get('scene_index'))
            plan._require(not set(rejected) & set(filtered) and len(proof['video_records']) < 32,
                          'plan_visual_completion_capacity_or_filter')
            proof.update(visual_completion_of=source['parent_id'], generation_scenes=rejected)
        elif interrupted:
            proof['interruption_of'] = source['parent_id']
            proof['interruption_sha256'] = plan._sha(interruption.proof(client, source))
        elif motion_failure:
            proof['motion_of'] = source['parent_id']
        elif deferred:
            if not claimed:
                plan._require(datetime.now(timezone.utc) >= datetime.fromisoformat(deferred['retry_at']),
                              'plan_video_quota_waiting')
            proof.update(deferred_of=source['parent_id'], deferred_until=deferred['retry_at'])
        elif prior['evidence'].get('review_of'):
            proof['recovery_of'] = source['parent_id']
            if mode == 'sources' and not claimed:
                _quota_ready(client, source)
        if source['error'] in {RENDER_ERROR, ASSEMBLY_ERROR}:
            proof['render_of'] = source['parent_id']
            if prior['evidence'].get('render_of'):
                proof['window_of'] = source['parent_id']
            if source['error'] == ASSEMBLY_ERROR:
                proof['assembly_of'] = source['parent_id']
    return source, root, proof


def _claim(source, root, proof):
    return {'version': 1, 'task_id': operation(source), 'source_task_id': source, 'root_task_id': root, 'evidence': proof}


def schedule(source, enqueue, *, client=None):
    client = client or plan._client(); task = source['task_id']
    if client.exists(DISPATCH + task): return 'retained_completion_reserved'
    deferred = deferred_quota(client, source)
    if deferred and datetime.now(timezone.utc) < datetime.fromisoformat(deferred['retry_at']):
        return 'plan_video_quota_waiting'
    source, root, proof = checked(client, task); claim = _claim(task, root, proof); root_key = _root_key(root, proof)
    from redis.exceptions import WatchError
    try:
        with client.pipeline() as pipe:
            pipe.watch(DISPATCH + task, root_key)
            if pipe.exists(DISPATCH + task, root_key): return 'retained_completion_reserved'
            pipe.multi(); pipe.set(DISPATCH + task, plan._raw(claim), nx=True); pipe.set(root_key, plan._raw(claim), nx=True)
            plan._require(pipe.execute() == [True, True])
    except WatchError: return 'retained_completion_reserved'
    try: enqueue(args=(task,), task_id=claim['task_id'], retry=False)
    except Exception: return 'retained_completion_uncertain'
    return 'retained_completion_preparing'


def verify_child(task, source_id, spec, *, client=None):
    from app.services.content_plan_local_resume import _claim as verify_retry
    client = client or plan._client(); source, root, proof = checked(client, source_id, claimed=True)
    claim = _claim(source_id, root, proof); child = plan._object(client.get(jobs.JOB_PREFIX + task))
    plan._require(client.get(DISPATCH + source_id) == client.get(_root_key(root, proof)) == plan._raw(claim)
        and client.get(EXECUTION + source_id) == operation(source_id)
        and child.get('task_id') == task and child.get('parent_id') == source_id and source.get('retry_child_task_id') == task
        and child.get('kind') == 'render' and child.get('spec') == spec == source['spec']
        and child.get('state') in {'PENDING', 'STARTED', 'PROGRESS'} and not child.get('result')
        and not child.get('publication_hold') and not child.get('owner_cancellation'))
    verify_retry(client, source_id, task)
    return source, root, proof


def run(source_id, operation_id):
    from app.tasks import run_video_pipeline
    client = plan._client(); claim = plan._object(client.get(DISPATCH + source_id))
    plan._require(operation_id == claim['task_id'] == operation(source_id))
    if not client.set(EXECUTION + source_id, operation_id, nx=True): return {'status': 'already_started'}
    source, root, proof = checked(client, source_id); plan._require(claim == _claim(source_id, root, proof))
    child = str(uuid5(NAMESPACE_URL, 'owner-plan-retained-completion-child:v1:' + source_id)); token = secrets.token_urlsafe(32)
    plan._require(jobs.claim_retry_dispatch(source_id, child, token, allow_repair=False).get('claimed') is True)
    jobs.create_job(child, source['spec'], kind='render', parent_id=source_id)
    spec = source['spec']; options = {k: v for k, v in spec.items() if k not in {'topic', 'duration_minutes', 'language', 'channel_id'}}
    try:
        run_video_pipeline.apply_async(args=(spec['topic'], 3, spec['language'], spec['channel_id'], options, None, source_id), task_id=child, retry=False)
    except Exception:
        jobs.mark_retry_dispatch(source_id, token, 'uncertain'); return {'status': 'dispatch_uncertain', 'task_id': child}
    jobs.mark_retry_dispatch(source_id, token, 'dispatched')
    return {'status': 'enqueued', 'task_id': child, 'new_voice_requests': 0}


def prepare(task, source_id, spec, work):
    from app.services import content_plan_long_media_resume as media
    source, root, proof = verify_child(task, source_id, spec)
    restored = proof['restored_source']
    prepared = voice._prepare_long_candidate(task, proof['saved_voice_source'], spec, work,
        restored_sources=restored['sources'] if restored else None)
    prepared.pop('content_plan_voice_source'); prepared['content_plan_media_source'] = source_id
    clips = {}
    for position, ancestor in enumerate(proof['media_sources']):
        directory = work / ('retained-source-' + str(position)); directory.mkdir()
        found = media._load_clips(jobs.get_job(ancestor), prepared, directory, allow_repair=True)
        for index, clip in found.items(): clips[index] = [deepcopy(clip), *clips.get(index, [])][:3]
    if proof.get('accepted_output_of'):
        from app.services.accepted_video_outputs import load
        for index, clip in load(plan._client(), root, source, proof, prepared, work).items():
            clips[index] = [clip, *clips.get(index, [])][:3]
    prepared['retained_long_clips'] = media.RetainedClips(clips, token=media._TOKEN)
    jobs.update_job(task, retained_long_media={'source_task_id': source_id, 'retained_clips': len(clips),
        'stock_only': proof['mode'] == 'quota' or bool(proof.get('motion_of') or proof.get('interruption_of') or proof.get('accepted_output_of')), 'new_tts_requests': 0, 'requires_full_qa': True,
        'restored_public_sources': len(restored['sources']) if restored else 0})
    return prepared


def stock_only_scope(scope):
    from app.services.content_plan_fal_visual_resume import readonly_for_task
    if scope['context'].get('kind') == 'long' and readonly_for_task():
        return True
    from app.services import production_spend_runtime as runtime
    task = runtime._TASK_ID.get()
    if not task or scope['context'].get('kind') != 'long': return False
    client = scope['foundation'].client; child = plan._object(client.get(jobs.JOB_PREFIX + task)); source = child.get('parent_id')
    if not source or not client.exists(DISPATCH + source): return False
    _, root, proof = verify_child(task, source, child['spec'], client=client)
    plan._require(root == scope['context']['lineage_id'])
    if proof.get('visual_completion_of'):
        # Only the recorded rejected scene indices may use the unchanged
        # original root's remaining 32-request journal. Never a fresh cap.
        return scope.get('scene_index') not in proof['generation_scenes']
    return proof['mode'] == 'quota' or bool(proof.get('motion_of') or proof.get('interruption_of') or proof.get('accepted_output_of'))


def continuation_identity(scope):
    from app.services import production_spend_runtime as runtime
    task = runtime._TASK_ID.get()
    if not task or scope['context'].get('kind') != 'long': return None
    client = scope['foundation'].client; child = plan._object(client.get(jobs.JOB_PREFIX + task)); source = child.get('parent_id')
    if not source or not client.exists(DISPATCH + source): return None
    _, root, proof = verify_child(task, source, child['spec'], client=client)
    plan._require(root == scope['context']['lineage_id'] and proof['mode'] == 'sources')
    return task
