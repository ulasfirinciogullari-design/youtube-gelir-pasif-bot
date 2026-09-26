"""Replace a conclusively rejected pre-visual draft in its existing experiment slot.

This changes future editorial intent only. Previous jobs, negative verdicts,
paid requests, upload claims and ordinary daily counters are never changed.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
from uuid import uuid4

from app.services import content_plan as plan, shorts_experiment as batch, studio_state as jobs

ARCHIVE = batch.PREFIX + 'editorial_archive:'


def _eligible(source):
    if not (source.get('state') == 'FAILURE' and source.get('kind') == 'render'
            and source.get('parent_id') is None and source.get('paid_create_slots_used') == 0
            and not any(source.get(key) for key in ('result', 'retry_child_task_id', 'retry_claimed',
                'repair_claimed', 'owner_cancellation', 'publication_hold', 'generated_asset_candidates'))):
        return False
    error = str(source.get('error') or '')
    if source.get('failure_stage') == 'director_qc':
        return (error.startswith('Director could not produce fully stock-safe short-preview scenes: ')
                and any(reason in error for reason in ('must contain one simple sentence',
                    'contains an unfilmable abstraction')) and not source.get('audio_candidate_checkpoint'))
    if source.get('failure_stage') != 'audio_qc' or not error.startswith('Audio narration QA rejected before paid media: '):
        return False
    report = json.loads(error.split(': ', 1)[1])
    return (report.get('available') is True and report.get('score') == 100 and report.get('reason') is None
            and report.get('duration_qc', {}).get('reason') == 'short_form_script_too_thin'
            and report['duration_qc'].get('pass') is False
            and not report.get('mismatch_details', {}).get('missing_words')
            and not report.get('mismatch_details', {}).get('unexpected_words'))


def _settled(pipe, read, root):
    from app.services.content_plan_attention import _settled_providers
    from app.services import kie_voice_ledger as kie
    evidence = _settled_providers(pipe, read, root)
    raw = read(kie.JOURNAL_KEY)
    if raw:
        plan._require(read(kie.ANCHOR_KEY) == kie.sha(raw))
        for identity, request in json.loads(raw)['requests'].items():
            if request['scope'].get('lineage_id') != root:
                continue
            plan._require(request['create'] is not None and request['result'] is not None)
            created = kie.restore(request['create']); result = kie.restore(request['result'])
            plan._require(created.status_code == result.status_code == 200)
            a, b = created.json(), result.json()
            plan._require(a.get('code') == b.get('code') == 200
                and a['data']['taskId'] == b['data']['taskId']
                and b['data'].get('state') in {'success', 'fail'})
            evidence['kie:' + identity] = plan._sha(request)
    return evidence


def replace(item_id, new_item, *, expected_job_sha256=None, client=None, now=None, observe_only=False):
    """One CAS; at most two amendments per slot, never replay a failed task."""
    from app.services import channel_cadence as cadence, channel_production as production
    from app.services.youtube_publish_state import UPLOAD_PREFIX
    from app.services.source_publication_hold import HOLD_PREFIX
    client = client or plan._client(); plan._validate_item(new_item)
    plan._require(new_item['format'] == 'shorts' and new_item['series'] is None and not new_item['depends_on'])
    with client.pipeline() as pipe:
        def read(key):
            pipe.watch(key)
            return pipe.get(key)
        manifest = batch._manifest(pipe, now); plan._require(manifest is not None)
        groups = batch.resolved(pipe, manifest)
        group = next((g for g in groups if g['current']['item_id'] == item_id), None)
        plan._require(group is not None and len(group['history']) < 3)
        row = group['current']; channel = row['channel_id']; root = batch.root_id(channel, item_id)
        plan._require(new_item['id'] not in {r['item_id'] for g in groups for r in g['history']})
        key = plan.PLAN_PREFIX + channel; original_plan = read(key); document = plan._plan(original_plan, channel)
        plan._require(document['enabled'] and document['after_queue'] == 'auto_shorts')
        index = next((i for i, entry in enumerate(document['items']) if entry['id'] == item_id), None)
        plan._require(index is not None)
        item = document['items'][index]
        plan._require(plan._sha(item) == row['item_sha256'] and item['brief'] != new_item['brief']
            and item['series'] is None and not item['depends_on']
            and not any(item_id in entry['depends_on'] for entry in document['items']))
        profile = plan._object(read(production.PROFILE_PREFIX + channel))
        plan._require(profile['production_enabled'] and profile['auto_publish'] and profile['release_mode'] == 'public')
        read(plan.ACTIVE_KEY)
        active = plan._active(pipe)
        legacy = read(production.ACTIVE_KEY)
        plan._require(not any(row['channel_id'] == channel for row in
            (production._decode_active_claims(legacy) if legacy else [])))
        old_job = read(jobs.JOB_PREFIX + root); dispatch = read(plan.DISPATCH_PREFIX + item_id)
        plan._require(read(plan.COMPLETION_PREFIX + item_id) is None
            and read(cadence.PREFIX + 'publication:' + root) is None and read(UPLOAD_PREFIX + root) is None)
        pipe.watch(batch.produced_key(manifest['day'], channel))
        produced = pipe.hget(batch.produced_key(manifest['day'], channel), root)
        evidence = {}; terminal_raw = None
        if old_job is None:
            plan._require(expected_job_sha256 is None and dispatch is None and produced is None
                          and active.get(channel) != item_id)
        else:
            source = plan._object(old_job); plan._require(plan._sha(source) == expected_job_sha256 and _eligible(source)
                and source.get('task_id') == root and source.get('spec', {}).get('production_channel_id') == channel
                and source['spec'].get('content_plan_item_id') == item_id and source['spec'].get('format') == 'shorts')
            frozen = plan._object(dispatch)
            plan._require(frozen['item'] == item and frozen['task_id'] == root and frozen['channel_id'] == channel
                and frozen['profile_revision'] == profile['profile_revision']
                and plan.dispatch_spec_matches(frozen, source['spec']) and produced == 'shorts'
                and active.get(channel) == item_id)
            for prefix in (jobs.RETRY_DISPATCH_PREFIX, jobs.REPAIR_CHECKPOINT_PREFIX, jobs.RENDER_CANCELLATION_PREFIX,
                           jobs.EXTERNAL_EPISODE_LEAF_PREFIX):
                plan._require(read(prefix + root) is None)
            terminal_raw = read('celery-task-meta-' + root); terminal = plan._object(terminal_raw)
            expected = 'FinalAudioQualityError' if source['failure_stage'] == 'audio_qc' else 'ProductionContentError'
            plan._require(terminal.get('task_id') == root and terminal.get('status') == 'FAILURE'
                and (terminal.get('result') or {}).get('exc_type') == expected
                and terminal['result'].get('exc_message') == [source['error']])
            evidence = _settled(pipe, read, root)
            del active[channel]
        replacement = batch.replacement_key(manifest['day'], item_id)
        archive_key = ARCHIVE + item_id
        plan._require(read(replacement) is None and read(archive_key) is None)
        new_root = batch.root_id(channel, new_item['id'])
        for prefix, identity in ((jobs.JOB_PREFIX, new_root), (plan.DISPATCH_PREFIX, new_item['id']),
                                 (plan.COMPLETION_PREFIX, new_item['id'])):
            plan._require(read(prefix + identity) is None)
        # Read and preserve negative holds rather than treating this as approval.
        holds = {prefix + root: read(prefix + root) for prefix in (HOLD_PREFIX, jobs.QUALITY_HOLD_JOB_FENCE_PREFIX)}
        stamp = datetime.now(timezone.utc).isoformat()
        amendment = {'version': 1, 'channel_id': channel, 'original_item_id': group['base']['item_id'],
            'previous_item_sha256': row['item_sha256'], 'item': new_item,
            'previous_job_sha256': expected_job_sha256, 'created_at': stamp}
        edited = deepcopy(document); edited['items'][index] = new_item
        edited.update(revision=str(uuid4()), updated_at=stamp); plan._plan(plan._raw(edited), channel)
        archive = {'version': 1, 'original_plan': original_plan, 'original_job': old_job,
            'original_dispatch': dispatch, 'original_terminal': terminal_raw, 'negative_holds': holds,
            'provider_evidence': evidence, 'amendment': amendment, 'new_plan_revision': edited['revision'],
            'qa_approved': False, 'old_publish_eligible': False, 'replayed_requests': 0}
        if observe_only:
            return {'eligible': True, 'old_root': root, 'new_root': new_root, 'paid_visual_requests': 0}
        pipe.multi(); pipe.set(replacement, plan._raw(amendment), nx=True)
        pipe.set(archive_key, plan._raw(archive), nx=True); pipe.set(key, plan._raw(edited))
        pipe.set(plan.ACTIVE_KEY, plan._raw(active)); plan._require(pipe.execute() == [True] * 4)
        return {'status': 'editorial_replaced', 'old_root': root, 'new_root': new_root,
                'item_id': new_item['id'], 'old_job_preserved': True, 'financial_changes': False}
