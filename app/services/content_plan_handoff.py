"""Use the owner's completed queue as the next-series handoff, not old uploads.

The original profile/state and every private or failed job remain in the
promotion archive. This proof never approves, releases or retries those jobs.
"""
from app.services import content_plan as plan, channel_production as production
from app.services import studio_state as jobs
from app.services.youtube_publish_state import UPLOAD_PREFIX
from redis.client import Pipeline


class _Reader:
    def __init__(self, client):
        self.client = client

    def get(self, key):
        if isinstance(self.client, Pipeline):
            self.client.watch(key)
        return self.client.get(key)


def completed(client, profile, channel, state):
    """Return a current, fully public plan proof or None; no state mutations."""
    reader = _Reader(client)
    channel_id = profile['channel_id']
    raw = reader.get(plan.PLAN_PREFIX + channel_id)
    if raw is None:
        return None
    document = plan._plan(raw, channel_id)
    if not document['enabled'] or document['after_queue'] != 'auto_shorts' or not document['items']:
        return None
    rows = []; has_current_item = False
    for entry in document['items']:
        value = reader.get(plan.COMPLETION_PREFIX + entry['id'])
        if value is None:
            return None
        receipt = plan._object(value)
        dispatch = plan._object(reader.get(plan.DISPATCH_PREFIX + entry['id']))
        if dispatch['profile_revision'] == profile['profile_revision']:
            has_current_item = True
        plan._require(dispatch['channel_id'] == channel_id
            and dispatch['connection_id'] == channel['connection_id'] and dispatch['item'] == entry)
        current = plan.publication_proof(reader, dispatch)
        plan._require(current is not None and {k:v for k,v in current.items() if k != 'completed_at'}
            == {k:v for k,v in receipt.items() if k != 'completed_at'}, 'plan_publication_changed')
        rows.append({'item_id': entry['id'], 'completion_sha256': plan._sha(receipt),
                     'video_id': receipt['video_id']})
    # Keep verified history when the owner extends a completed plan. At least
    # one newly completed item must belong to this profile: old history alone
    # cannot authorize a second rotation after the profile changes.
    if not has_current_item:
        return None
    active = plan._object(reader.get(plan.ACTIVE_KEY) or '{}')
    topics = profile.get('production_topics') or []
    plan._require(channel_id not in active and topics
        and state.get('cursor') == str(len(topics)) and not state.get('active_task_id')
        and state.get('dispatch_status') == 'finished'
        and state.get('paused_reason') in (None, '', 'previous_publication_blocked')
        and state.get('profile_revision') == profile['profile_revision']
        and state.get('connection_id') == channel['connection_id']
        and state.get('consumed_prefix') == production._prefix_digest(topics), 'plan_handoff_not_idle')
    task = plan._id(state.get('last_task_id'))
    source = plan._object(reader.get(jobs.JOB_PREFIX + task))
    plan._require(source.get('state') in {'SUCCESS', 'FAILURE'} and source.get('kind') == 'render'
        and not source.get('retry_child_task_id') and not source.get('owner_cancellation')
        and not source.get('publication_hold'), 'plan_previous_job_unsettled')
    old = {'source_task_id': task, 'state': source['state'], 'source_sha256': plan._sha(source)}
    upload = reader.get(UPLOAD_PREFIX + task)
    if upload is not None:
        record = plan._object(upload)
        publisher = plan._object(reader.get(jobs.JOB_PREFIX + plan._id(record.get('publish_task_id'))))
        plan._require(record.get('source_task_id') == task and record.get('status') == 'complete'
            and record.get('target_channel_id') == channel_id
            and record.get('connection_id') == channel['connection_id']
            and record.get('release_status') in {'public', 'blocked'}
            and publisher.get('state') == 'SUCCESS' and publisher.get('parent_id') == task
            and (record['release_status'] == 'public' or record.get('release_side_effect_possible') is False),
            'plan_previous_publication_uncertain')
        old.update(upload_sha256=plan._sha(record), release_status=record['release_status'],
                   publisher_sha256=plan._sha(publisher))
    else:
        plan._require(source['state'] == 'FAILURE' and not state.get('paused_reason'),
                      'plan_previous_publication_missing')
    return {'version': 1, 'completion_kind': 'owner_plan_complete', 'channel_id': channel_id,
            'plan_revision': document['revision'], 'plan_sha256': plan._sha(document),
            'public_items': rows, 'previous_job': old, 'previous_job_released': False}


def snapshot_completed(snapshot, profile, channel, state):
    from app.services.production_quality_holds import _SnapshotReader
    return completed(_SnapshotReader(snapshot), profile, channel, state)


def allows_preparation(client, profile, channel, state):
    if not state.get('paused_reason'):
        return True
    return completed(client, profile, channel, state) is not None
