"""Re-observe an unchanged failed episode after legacy dashboard metadata writes.

The original negative disposition and daily counter never change. One separate
receipt seals the current terminal chain, retaining the old hash as history.
No retry, provider request, quality approval, schedule change or upload occurs.
"""
from datetime import datetime, timezone
import math

from app.services import production_quality_holds as holds, studio_state as jobs
from app.services import production_spend_runtime as runtime, channel_production as production

PREFIX = holds.PREFIX + 'revalidation:'
CANDIDATES = ('audio_candidate_checkpoint', 'included_stock_pools', 'generated_asset_candidates',
              'repair_checkpoint', 'qa_workprint')


def fence_record(root, hold_sha256, row):
    return {'version': 1, 'root_task_id': root, 'hold_sha256': hold_sha256,
            'task_id': row['task_id'], 'job_sha256': row['job_sha256']}


def _same_episode(record, leaf, evidence):
    old = record.get('lineage')
    holds._require(type(old) is list and 1 <= len(old) <= holds.MAX_HOPS
        and [row['task_id'] for row in old] == [row['task_id'] for row in evidence]
        and record['leaf_task_id'] == leaf['task_id'] and record['spec'] == leaf['spec']
        and record['reason'] == holds._reason(leaf)
        and record.get('retained_candidates') == {key: leaf[key] for key in CANDIDATES if leaf.get(key)},
        'quality_hold_episode_changed')


def verified_revalidation(snapshot, record, original_raw, leaf, evidence, now):
    """A reader still rejects changed jobs unless this exact later proof exists."""
    _same_episode(record, leaf, evidence)
    key = PREFIX + record['root_task_id']
    encoded = snapshot.read(key)
    value = holds._object(encoded)
    expected = {'version': 1, 'status': 'revalidated_unpublished',
        'root_task_id': record['root_task_id'], 'channel_id': record['channel_id'],
        'connection_id': record['connection_id'], 'profile_revision': record['profile_revision'],
        'original_hold_sha256': holds._sha(original_raw), 'lineage': evidence,
        'publish_eligible': False, 'retry_dispatched': False}
    holds._require(set(value) == {*expected, 'observed_at'}
        and all(type(value[k]) is type(v) and value[k] == v for k, v in expected.items())
        and snapshot.read(key, 'pttl') == -1, 'quality_hold_revalidation_changed')
    observed = datetime.fromisoformat(value['observed_at'])
    held = datetime.fromisoformat(record['held_at'])
    holds._require(observed.tzinfo is not None and observed.utcoffset().total_seconds() == 0
        and held <= observed <= datetime.fromtimestamp(now, timezone.utc))
    for row in evidence:
        key = jobs.QUALITY_HOLD_JOB_FENCE_PREFIX + row['task_id']
        holds._require(snapshot.read(key) == holds._raw(fence_record(record['root_task_id'],
            holds._sha(original_raw), row)) and snapshot.read(key, 'pttl') == -1
            and snapshot.read(jobs.JOB_PREFIX + row['task_id'], 'pttl') == -1,
            'quality_hold_fence_changed')
    return {'revalidation_sha256': holds._sha(encoded)}


def revalidate_held_completion(profile, *, dry_run=False):
    """One watched append for the same finished chain; never overwrite a receipt."""
    from app.services.production_series_promotion import _Snapshot
    from app.services.youtube_auth import AUTH_EPOCH_KEY

    foundation = runtime.configured_ledger(read_timeout=2)
    client, now = foundation.client, foundation.clock()
    channel_id = profile['channel_id']
    snapshot = _Snapshot(client)
    state_key = production.CHANNEL_STATE_PREFIX + channel_id
    state = snapshot.read(state_key, 'hash')
    root = state.get('quality_hold_task_id')
    if not root:
        return {'status': 'not_needed'}
    holds._require(state.get('last_task_id') == root and state.get('last_result') == 'FAILURE'
        and not state.get('paused_reason') and not state.get('active_task_id')
        and state.get('dispatch_status') == 'finished'
        and snapshot.read(production.ACTIVE_KEY) is None, 'quality_hold_active')
    holds._require(snapshot.object(production.PROFILE_PREFIX + channel_id) == profile
        and profile.get('production_enabled') is True and profile.get('auto_publish') is True
        and profile.get('release_mode') == 'public')
    channel = snapshot.object(production.OAUTH_CHANNEL_PREFIX + channel_id)
    snapshot.read(AUTH_EPOCH_KEY)
    holds._require(channel.get('id') == channel_id and channel.get('requires_reconnect') is not True
        and client.sismember(runtime._CHANNEL_INDEX, channel_id)
        and snapshot.read(production.OAUTH_CREDENTIAL_PREFIX + channel_id)
        and state.get('connection_id') == channel['connection_id']
        and state.get('profile_revision') == profile['profile_revision'])
    cursor, due = int(state.get('cursor', '-1')), float(state.get('next_due', 'nan'))
    holds._require(str(cursor) == state.get('cursor') and 1 <= cursor <= len(profile['production_topics'])
        and state.get('consumed_prefix') == production._prefix_digest(profile['production_topics'][:cursor])
        and math.isfinite(due) and due >= 0)
    record, original_raw, leaf, evidence = holds._held_evidence(
        snapshot, profile, channel, root, cursor, now.timestamp())
    _same_episode(record, leaf, evidence)
    if record['lineage'] == evidence:
        return {'status': 'not_needed'}
    key = PREFIX + root
    if snapshot.read(key) is not None:
        verified_revalidation(snapshot, record, original_raw, leaf, evidence, now.timestamp())
        return {'status': 'already_revalidated'}
    context = {'channel_id': channel_id, 'connection_id': channel['connection_id'],
               'lineage_id': root, 'kind': 'shorts'}
    ledger = snapshot.read(runtime.LEDGER_KEY, 'hash')
    holds._require(holds._object(ledger.get('binding:' + root)) == context)
    value = {'version': 1, 'status': 'revalidated_unpublished', 'root_task_id': root,
        'channel_id': channel_id, 'connection_id': channel['connection_id'],
        'profile_revision': profile['profile_revision'], 'original_hold_sha256': holds._sha(original_raw),
        'lineage': evidence, 'observed_at': now.isoformat(), 'publish_eligible': False, 'retry_dispatched': False}
    fences = {jobs.QUALITY_HOLD_JOB_FENCE_PREFIX + row['task_id']:
              holds._raw(fence_record(root, holds._sha(original_raw), row)) for row in evidence}
    holds._require(all(snapshot.read(name) is None for name in fences), 'quality_hold_fence_conflict')
    with client.pipeline() as pipe:
        pipe.watch(*snapshot.values, runtime._CHANNEL_INDEX, *holds._financial_keys())
        snapshot.compare(pipe)
        holds._require(pipe.sismember(runtime._CHANNEL_INDEX, channel_id))
        holds._funding(foundation, channel_id)
        if dry_run:
            pipe.multi(); pipe.ping(); holds._require(pipe.execute() == [True])
            return {'status': 'eligible_no_writes', 'root_task_id': root, 'lineage_count': len(evidence)}
        pipe.multi()
        pipe.set(key, holds._raw(value), nx=True)
        for name, encoded in fences.items():
            pipe.set(name, encoded, nx=True)
        for row in evidence:
            pipe.persist(jobs.JOB_PREFIX + row['task_id'])
        ack = pipe.execute()
        holds._require(len(ack) == 1 + 2 * len(evidence)
            and all(v is True for v in ack[:1 + len(evidence)])
            and all(type(v) is bool for v in ack[1 + len(evidence):]), 'quality_hold_revalidation_ack_unknown')
    return {'status': 'revalidated_unpublished', 'root_task_id': root}
