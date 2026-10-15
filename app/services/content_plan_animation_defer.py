"""Move an owner's failed, pre-media fiction brief into a separate stock inbox.

Explicit operator action only. This does not classify briefs, generate media,
approve a failed job, claim publication, or change any historical dispatch.
"""
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

from app.services import content_plan as plan, channel_production as production, studio_state as jobs
from app.services.youtube_publish_state import UPLOAD_PREFIX

PREFIX = plan.PREFIX + 'deferred_animation:'


def defer(channel_id, revision, item_id, source_id, expected_item_sha256, *, client=None):
    client = client or plan._client()
    plan._id(item_id); plan._id(source_id)
    key = PREFIX + item_id
    with client.pipeline() as pipe:
        def read(key):
            pipe.watch(key)
            return pipe.get(key)
        previous = read(key)
        if previous:
            record = plan._object(previous)
            plan._require(record.get('channel_id') == channel_id and record.get('source_task_id') == source_id
                and record.get('item_sha256') == expected_item_sha256, 'animation_defer_binding_changed')
            return {'status': 'already_deferred', 'item_id': item_id, 'stock_key': key}
        raw_plan = read(plan.PLAN_PREFIX + channel_id)
        document = plan._plan(raw_plan, channel_id)
        plan._require(document['revision'] == revision, 'plan_changed')
        matches = [row for row in document['items'] if row['id'] == item_id]
        plan._require(len(matches) == 1, 'plan_item_missing')
        item = matches[0]
        plan._require(plan._sha(item) == expected_item_sha256 and item['series'] is None
            and not item['depends_on'] and not any(item_id in row['depends_on'] for row in document['items']),
            'animation_defer_dependencies')
        raw_dispatch = read(plan.DISPATCH_PREFIX + item_id)
        dispatch = plan._object(raw_dispatch)
        raw_source = read(jobs.JOB_PREFIX + source_id)
        source = plan._object(raw_source)
        plan._require(dispatch['task_id'] == source_id and dispatch['channel_id'] == channel_id
            and dispatch['item'] == item and source.get('task_id') == source_id and not source.get('parent_id')
            and source.get('kind') == 'render' and source.get('state') == 'FAILURE'
            and source.get('failure_stage') == 'research'
            and source.get('error') == 'included_research_primary_source_required'
            and plan.dispatch_spec_matches(dispatch, source.get('spec') or {})
            and (source.get('spec') or {}).get('content_plan_item_id') == item_id
            and (source.get('spec') or {}).get('production_channel_id') == channel_id
            and source.get('paid_create_slots_used') == 0
            and not any(source.get(k) for k in ('result', 'retry_child_task_id', 'audio_candidate_checkpoint',
                'generated_asset_candidates', 'owner_cancellation', 'publication_hold')),
            'animation_defer_media_or_retry_exists')
        for absent in (UPLOAD_PREFIX + source_id, plan.COMPLETION_PREFIX + item_id,
            jobs.RETRY_CHILD_CLAIM_PREFIX + source_id, jobs.RENDER_CANCELLATION_PREFIX + source_id,
            jobs.EXTERNAL_EPISODE_LEAF_PREFIX + source_id, jobs.QUALITY_HOLD_JOB_FENCE_PREFIX + source_id):
            plan._require(read(absent) is None, 'animation_defer_unsettled')
        active = plan._object(read(plan.ACTIVE_KEY) or '{}')
        plan._require(active.get(channel_id) == item_id, 'animation_defer_active_changed')
        legacy = read(production.ACTIVE_KEY)
        plan._require(not any(row['channel_id'] == channel_id for row in
            (production._decode_active_claims(legacy) if legacy else [])), 'animation_defer_channel_running')
        del active[channel_id]
        edited = deepcopy(document)
        edited['items'] = [row for row in edited['items'] if row['id'] != item_id]
        edited.update(revision=str(uuid4()), updated_at=datetime.now(timezone.utc).isoformat())
        plan._plan(plan._raw(edited), channel_id)
        record = {'version': 1, 'status': 'waiting_separate_animation_channel', 'item': item,
            'item_sha256': expected_item_sha256, 'channel_id': channel_id, 'source_task_id': source_id,
            'reason': 'owner_requested_separate_animation_channel_and_stock',
            'publish_eligible': False, 'qa_approved': False, 'media_generated': False,
            'original_plan': raw_plan, 'original_dispatch': raw_dispatch, 'original_source': raw_source,
            'created_at': edited['updated_at'], 'new_plan_revision': edited['revision']}
        pipe.multi(); pipe.set(key, plan._raw(record), nx=True)
        pipe.set(plan.PLAN_PREFIX + channel_id, plan._raw(edited)); pipe.set(plan.ACTIVE_KEY, plan._raw(active))
        plan._require(pipe.execute() == [True, True, True], 'animation_defer_commit_uncertain')
    return {'status': 'deferred_to_animation_stock', 'item_id': item_id, 'stock_key': key,
        'historical_job_and_dispatch_preserved': True, 'media_requests': 0, 'publication_requests': 0}
