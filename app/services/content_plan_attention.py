"""Keep a conclusively rejected standalone documentary out of the active queue.

This is an editorial disposition, never a completion or retry authorization.
Jobs, provider receipts, daily reservations and publication holds stay intact.
Numbered series and unfinished dependencies cannot be advanced this way.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from uuid import uuid4

from app.services import content_plan as plan, channel_cadence as cadence, studio_state as jobs

PREFIX = plan.PREFIX + 'attention:v1:'
INDEX = PREFIX + 'channel:'
PREVOICE_ERRORS = {'included_factual_audit_invalid',
    'Long documentary failed independent source or editorial review'}
VOICE_DURATION_ERROR = 'Voice script duration rejected before paid media: '


def _archive(raw):
    plan._require(type(raw) is str and 0 < len(raw.encode()) <= 32 * 1024 * 1024)
    value = json.loads(raw); plan._require(type(value) is dict)
    return value


def _settled_providers(pipe, read, root, *, retained_fal=None):
    """Keep financial uncertainty reserved; only a verified terminal cut may detach."""
    from app.services import commissioning_reasoning as reasoning, commissioning_video as video
    from app.services import production_credit_ledger as credit, production_included_router as included
    key = reasoning.PREFIX + 'lineage:' + root; pipe.watch(key)
    identities = pipe.smembers(key); plan._require(len(identities) <= reasoning.MAX_LONG_LINEAGE)
    evidence = {}
    for identity in sorted(identities):
        request_raw = read(reasoning.PREFIX + 'request:' + identity)
        response_raw = read(reasoning.PREFIX + 'response:' + identity)
        request, response = plan._object(request_raw), plan._object(response_raw)
        plan._require(request.get('context', {}).get('lineage_id') == root
            and request.get('request_sha256') == response.get('request_sha256') == identity
            and response.get('http_status') in {200, 400, 422, 429}
            and response.get('encrypted_response') and response.get('response_sha256'))
        evidence[identity] = plan._sha([request_raw, response_raw])
    native_raw = read(video.PREFIX + root)
    if native_raw:
        journal = plan._object(native_raw)
        plan._require(journal.get('context', {}).get('lineage_id') == root
            and len(journal.get('requests', {})) <= 32)
        if retained_fal is not None:
            plan._require({key: plan._sha(row) for key, row in journal['requests'].items()}
                          == retained_fal['video_records'])
        unresolved = []
        for identity, row in journal['requests'].items():
            if (retained_fal is not None and identity in retained_fal['unknown_requests']):
                plan._require(row.get('create') is None and row.get('result') is None)
                unresolved.append(identity)
                continue
            created = row.get('create') or {}; status = created.get('http_status')
            if status in {400, 422, 429}:
                plan._require(created.get('encrypted_response') and created.get('response_sha256'))
                continue
            result = row.get('result') or {}
            plan._require(status == 200 and result.get('http_status') in {200, 400, 422, 429}
                and result.get('encrypted_response') and result.get('response_sha256'))
            if result['http_status'] == 200:
                value = video._payload(result)
                plan._require(value.get('done') is True or isinstance(value.get('video'), dict))
        evidence['native_video'] = plan._sha(native_raw)
        if retained_fal is not None:
            plan._require(sorted(unresolved) == retained_fal['unknown_requests'])
            evidence['reserved_fal_unknowns'] = {
                'request_sha256s': sorted(unresolved), 'cut_claim_sha256': retained_fal['cut_claim_sha256'],
                'financial_status': 'unresolved_reserved', 'retry_authorized': False,
                'refund_authorized': False, 'daily_capacity_released': False}
    elif retained_fal is not None:
        raise plan.ContentPlanError('plan_invalid')
    pipe.watch(credit.STATE_KEY)
    credit_state = pipe.hgetall(credit.STATE_KEY)
    if credit_state:
        state = credit._object(credit_state['state'])
        for row in state['intents'].values():
            if row['reservation']['intent']['root_lineage_id'] == root:
                plan._require(row.get('settlement') is not None)
    router_raw = read(included.JOURNAL_KEY)
    if router_raw:
        for row in _archive(router_raw)['requests'].values():
            if row['context']['lineage_id'] == root:
                plan._require(row.get('outcome') is not None)
    return evidence


def eligible(source):
    from app.services.documentary_word_contract import duration_failure
    spec = source.get('spec') or {}
    if not (source.get('kind') == 'render' and source.get('state') == 'FAILURE'
            and spec.get('production_channel_id') in cadence.CHANNELS
            and spec.get('content_plan_item_id') and spec.get('format') == 'landscape'
            and spec.get('duration_minutes') == 3 and not source.get('retry_child_task_id')):
        return False
    if source.get('failure_stage') == 'director_qc' and (source.get('error') in PREVOICE_ERRORS or duration_failure(source)):
        return (source.get('paid_create_slots_used') == 0
            and not source.get('audio_candidate_checkpoint') and not source.get('generated_asset_candidates'))
    retained = source.get('retained_long_media') or {}
    failure = source.get('failure_classification') or {}
    if (source.get('failure_stage') == 'voice_and_visuals'
            and failure.get('category') == 'content_rejected' and failure.get('code') == 'audio_quality_exhausted'
            and str(source.get('error') or '').startswith(VOICE_DURATION_ERROR)):
        return (source.get('paid_create_slots_used') == 0
            and not source.get('audio_candidate_checkpoint') and not source.get('generated_asset_candidates'))
    return (source.get('failure_stage') == 'final_visual_qc_rescue'
        and failure.get('category') == 'content_rejected' and failure.get('code') == 'visual_quality_exhausted'
        and str(source.get('error') or '').startswith('Final visual quality gate rejected: ')
        and retained.get('stock_only') is True and retained.get('new_tts_requests') == 0)


def isolate(source, *, client=None, observe_only=False):
    """Archive only exact terminal work; one CAS removes its editorial assignment."""
    if not eligible(source): return None
    from app.services.source_publication_hold import HOLD_PREFIX
    from app.services.youtube_publish_state import UPLOAD_PREFIX
    from app.services.qa_workprint_access import validated_pointer
    client = client or plan._client()
    channel = source['spec']['production_channel_id']; task = plan._id(source['task_id'])
    item_id = plan._id(source['spec']['content_plan_item_id']); archive_key = PREFIX + item_id
    with client.pipeline() as pipe:
        def read(key):
            pipe.watch(key)
            return pipe.get(key)
        prior = read(archive_key)
        if prior:
            record = _archive(prior)
            plan._require(record['source_task_id'] == task and record['channel_id'] == channel)
            return 'attention_archived'
        original_plan = read(plan.PLAN_PREFIX + channel); document = plan._plan(original_plan, channel)
        plan._require(document['enabled'] and document['after_queue'] == 'auto_shorts')
        matches = [row for row in document['items'] if row['id'] == item_id]
        plan._require(len(matches) == 1); item = matches[0]
        # A preceding, already public series is a legitimate dependency. No
        # later entry may rely on this failed film, and no numbered episode skips.
        if item['series'] is not None or any(item_id in row['depends_on'] for row in document['items']):
            return None
        for dependency in item['depends_on']:
            if read(plan.COMPLETION_PREFIX + dependency) is None: return None
        for row in document['items']:
            if row['id'] == item_id: break
            if read(plan.COMPLETION_PREFIX + row['id']) is None: return None
        raw_dispatch = read(plan.DISPATCH_PREFIX + item_id); dispatch = plan._object(raw_dispatch)
        plan._require(dispatch['item'] == item and dispatch['channel_id'] == channel)
        profile = plan._object(read(plan.production.PROFILE_PREFIX + channel))
        plan._require(profile.get('production_enabled') is True and profile.get('auto_publish') is True
            and profile.get('release_mode') == 'public'
            and profile.get('profile_revision') == dispatch.get('profile_revision'))
        active = plan._object(read(plan.ACTIVE_KEY) or '{}')
        plan._require(active.get(channel) == item_id)
        legacy = read(plan.production.ACTIVE_KEY)
        plan._require(not any(row['channel_id'] == channel for row in
            (plan.production._decode_active_claims(legacy) if legacy else [])))
        # WATCH every lineage member and every way another execution could
        # acquire it. A new retry/cancel/hold/upload races this disposition out.
        original_jobs = {}; current = dispatch['task_id']; previous = None
        for _ in range(16):
            plan._id(current); plan._require(current not in original_jobs)
            raw = read(jobs.JOB_PREFIX + current); job = plan._object(raw)
            plan._require(job.get('task_id') == current and job.get('parent_id') == previous
                and job.get('kind') == 'render' and job.get('state') == 'FAILURE'
                and job.get('spec') == source['spec'] and not job.get('result')
                and not job.get('owner_cancellation') and not job.get('publication_hold'))
            for prefix in (HOLD_PREFIX, UPLOAD_PREFIX, jobs.RENDER_CANCELLATION_PREFIX,
                jobs.EXTERNAL_EPISODE_LEAF_PREFIX, jobs.QUALITY_HOLD_JOB_FENCE_PREFIX):
                plan._require(read(prefix + current) is None)
            original_jobs[current] = raw
            if current == task:
                plan._require(job == source and not job.get('retry_child_task_id')
                    and not job.get('retry_claimed') and not job.get('repair_claimed')
                    and read(jobs.RETRY_DISPATCH_PREFIX + current) is None
                    and read(jobs.REPAIR_CHECKPOINT_PREFIX + current) is None)
                break
            previous, current = current, job.get('retry_child_task_id')
        else:
            raise plan.ContentPlanError('plan_lineage_invalid')
        plan._require(plan.dispatch_spec_matches(dispatch, source['spec'])
            and read(plan.COMPLETION_PREFIX + item_id) is None)
        terminal_raw = read('celery-task-meta-' + task); terminal = plan._object(terminal_raw)
        result = terminal.get('result') or {}; error = source['error']
        expected = ('FinalAudioQualityError' if source['failure_stage'] == 'voice_and_visuals'
            else 'FinalVisualQualityError' if source['failure_stage'] == 'final_visual_qc_rescue'
            else 'ValueError' if error == 'included_factual_audit_invalid' else 'ProductionContentError')
        plan._require(terminal.get('task_id') == task and terminal.get('status') == 'FAILURE'
            and result.get('exc_type') == expected and result.get('exc_message') == [error]
            and ', in run_video_pipeline\n' in (terminal.get('traceback') or ''))
        stamp = datetime.fromisoformat(terminal['date_done'])
        plan._require(stamp.tzinfo is not None and stamp <= datetime.now(timezone.utc))
        if source['failure_stage'] == 'final_visual_qc_rescue':
            plan._require(validated_pointer(source) is not None
                and source['failure_classification'].get('error_sha256') == hashlib.sha256(error.encode()).hexdigest())
        if source['failure_stage'] == 'voice_and_visuals':
            plan._require(source['failure_classification'].get('error_sha256') == hashlib.sha256(error.encode()).hexdigest())
        from app.services.content_plan_fal_cut_resume import attention_provider_proof
        retained_fal = attention_provider_proof(source, dispatch['task_id'], client=pipe)
        provider_evidence = _settled_providers(pipe, read, dispatch['task_id'], retained_fal=retained_fal)
        if observe_only:
            return {'status': 'eligible_for_attention', 'source_task_id': task,
                'lineage_records': len(original_jobs), 'captured_provider_groups': len(provider_evidence),
                'provider_requests': 0, 'state_writes': 0}
        edited = deepcopy(document); edited['items'] = [row for row in edited['items'] if row['id'] != item_id]
        edited.update(revision=str(uuid4()), updated_at=datetime.now(timezone.utc).isoformat())
        plan._plan(plan._raw(edited), channel); del active[channel]
        record = {'version': 1, 'status': 'attention', 'reason': 'standalone_documentary_rejected',
            'channel_id': channel, 'item': item, 'source_task_id': task, 'root_task_id': dispatch['task_id'],
            'qa_approved': False, 'publish_eligible': False, 'created_at': edited['updated_at'],
            'original_plan': original_plan, 'original_dispatch': raw_dispatch,
            'original_jobs': original_jobs, 'original_terminal': terminal_raw,
            'provider_evidence': provider_evidence,
            'new_plan_revision': edited['revision']}
        saved = plan._raw(record); _archive(saved)
        pipe.multi(); pipe.set(archive_key, saved, nx=True)
        pipe.set(plan.PLAN_PREFIX + channel, plan._raw(edited)); pipe.set(plan.ACTIVE_KEY, plan._raw(active))
        pipe.zadd(INDEX + channel, {item_id: datetime.now(timezone.utc).timestamp()})
        response = pipe.execute(); plan._require(response[:3] == [True, True, True] and len(response) == 4)
    return 'attention_archived'


def recent(channel, *, client):
    if channel not in cadence.CHANNELS: return []
    rows = []
    for item_id in client.zrevrange(INDEX + channel, 0, 9):
        record = _archive(client.get(PREFIX + plan._id(item_id)))
        plan._require(record['channel_id'] == channel and record['item']['id'] == item_id)
        rows.append({'id': item_id, 'title': record['item']['title'],
            'task_id': plan._id(record['source_task_id']), 'created_at': record['created_at']})
    return rows
