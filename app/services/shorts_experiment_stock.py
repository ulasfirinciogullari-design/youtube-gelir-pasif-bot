"""Prepare the owner's finite experiment while public delivery waits.

Only independent, approved experiment items may be prepared ahead. Public
delivery retains its original order and is the only completion evidence.
"""
from app.services import content_plan as plan, shorts_experiment as batch, studio_state as jobs
from app.services.youtube_automation import automated_quality_approved


def member(client, channel, entry, *, now=None):
    found = batch._entry(client, channel, entry.get('id'), now)
    if found is None:
        return False
    plan._require(plan._sha(entry) == found[1]['item_sha256']
        and entry['format'] == 'shorts' and entry['series'] is None and not entry['depends_on'])
    return True


def prepared(client, channel, entry, *, now=None):
    if not member(client, channel, entry, now=now):
        return None
    raw = batch._read(client, plan.DISPATCH_PREFIX + entry['id'])
    if raw is None:
        return None
    dispatch = plan._object(raw)
    batch._read(client, jobs.JOB_PREFIX + dispatch['task_id'])
    source = plan._leaf(client, dispatch)
    # Watch the complete render as well as the immutable dispatch. No failed
    # or manually approved film can free production capacity through this path.
    batch._read(client, jobs.JOB_PREFIX + source['task_id'])
    if not automated_quality_approved(source):
        return None
    from app.services.source_publication_hold import HOLD_PREFIX
    for key in (HOLD_PREFIX + source['task_id'], *jobs.retained_delivery_fence_keys(source['task_id']),
                jobs.QUALITY_HOLD_JOB_FENCE_PREFIX + source['task_id'],
                jobs.RENDER_CANCELLATION_PREFIX + source['task_id']):
        plan._require(batch._read(client, key) is None, 'experiment_stock_held')
    spec = source['spec']
    plan._require(dispatch['item'] == entry and dispatch['channel_id'] == channel
        and dispatch['task_id'] == batch.root_id(channel, entry['id'])
        and spec.get('publish_after_render') is True and spec.get('production_channel_id') == channel
        and spec.get('production_connection_id') == dispatch['connection_id']
        and spec.get('production_profile_revision') == dispatch['profile_revision']
        and plan.dispatch_spec_matches(dispatch, spec))
    from app.services.channel_production import PROFILE_PREFIX, OAUTH_CHANNEL_PREFIX
    profile = plan._object(batch._read(client, PROFILE_PREFIX + channel))
    connection = plan._object(batch._read(client, OAUTH_CHANNEL_PREFIX + channel))
    plan._require(profile.get('profile_revision') == dispatch['profile_revision']
        and profile.get('auto_publish') is True and profile.get('production_enabled') is True
        and profile.get('release_mode') == 'public'
        and connection.get('connection_id') == dispatch['connection_id']
        and connection.get('requires_reconnect') is not True, 'experiment_stock_binding_changed')
    return source


def publication_wait(source, *, client=None):
    """Delay unqueued publication without spending metadata/API quota."""
    client = client or plan._client()
    spec = source.get('spec') or {}
    channel, item_id = spec.get('production_channel_id'), spec.get('content_plan_item_id')
    if channel not in batch.COUNTS or not item_id:
        return False
    document = plan.read(channel, client=client)
    entry = next((r for r in (document or {}).get('items', []) if r['id'] == item_id), None)
    if entry is None or not member(client, channel, entry):
        return False
    if not document['enabled']:
        return True
    for previous in document['items']:
        if previous['id'] == item_id:
            break
        if not client.exists(plan.COMPLETION_PREFIX + previous['id']):
            return True
    return False


def pending(client=None):
    client = client or plan._client()
    manifest = batch._manifest(client, None)
    return bool(manifest and any(not client.exists(plan.COMPLETION_PREFIX + g['current']['item_id'])
                                for g in batch.resolved(client, manifest)))


def maintain(channel, *, client=None):
    """Reconcile real public completions, then release only the queue's head."""
    client = client or plan._client()
    document = plan.read(channel, client=client)
    if not document or not document['enabled']:
        return
    for entry in document['items']:
        if client.exists(plan.COMPLETION_PREFIX + entry['id']):
            continue
        if not member(client, channel, entry):
            return
        source = prepared(client, channel, entry)
        if source is None:
            return
        raw = client.get(plan.DISPATCH_PREFIX + entry['id'])
        dispatch = plan._object(raw)
        proof = plan.publication_proof(client, dispatch)
        if proof:
            with client.pipeline() as pipe:
                pipe.watch(plan.PLAN_PREFIX + channel, plan.DISPATCH_PREFIX + entry['id'],
                    plan.COMPLETION_PREFIX + entry['id'], jobs.JOB_PREFIX + proof['source_task_id'],
                    jobs.JOB_PREFIX + proof['publisher_task_id'], plan.UPLOAD_PREFIX + proof['source_task_id'])
                plan._require(pipe.get(plan.PLAN_PREFIX + channel) == plan._raw(document)
                    and pipe.get(plan.DISPATCH_PREFIX + entry['id']) == raw)
                checked = plan.publication_proof(pipe, dispatch)
                plan._require(checked and checked['receipt_sha256'] == proof['receipt_sha256'])
                if not pipe.exists(plan.COMPLETION_PREFIX + entry['id']):
                    pipe.multi(); pipe.set(plan.COMPLETION_PREFIX + entry['id'], plan._raw(proof), nx=True)
                    plan._require(pipe.execute() == [True])
            continue
        if not client.exists(plan.UPLOAD_PREFIX + source['task_id']):
            from app.publish_tasks import queue_automatic_publish
            queue_automatic_publish(source['task_id'])
        return


def release_render_capacity(client, channel, entry_id):
    with client.pipeline() as pipe:
        pipe.watch(plan.ACTIVE_KEY, plan.PLAN_PREFIX + channel)
        document = plan.read(channel, client=pipe)
        if not document or not document['enabled']:
            return False
        entry = next((r for r in document['items'] if r['id'] == entry_id), None)
        if entry is None or prepared(pipe, channel, entry) is None:
            return False
        active = plan._active(pipe)
        plan._require(active.get(channel) == entry_id)
        del active[channel]
        pipe.multi(); pipe.set(plan.ACTIVE_KEY, plan._raw(active))
        plan._require(pipe.execute() == [True])
        return True
