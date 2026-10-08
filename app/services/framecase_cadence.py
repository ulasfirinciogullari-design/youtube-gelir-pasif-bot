"""Owner's per-channel daily production and publication ceiling.

Dates use Europe/Istanbul. Unfinished publication reservations count on every
day until delivery is known, including across midnight and worker restarts.
This prevents yesterday's in-flight upload from bypassing today's ceiling.
"""
from datetime import datetime, timezone
import json
from uuid import UUID
from zoneinfo import ZoneInfo

from app.services import studio_state as jobs

PREFIX = 'youtube_studio:framecase_cadence:v1:'
CHANNEL_ID = 'UCs93z6wf134H5_BL9pkQX4Q'
CHANNELS = frozenset({CHANNEL_ID})
LIMITS = {'long': 1, 'shorts': 10}
ZONE = ZoneInfo('Europe/Istanbul')
WAITING_KEY = PREFIX + 'waiting'


def _client():
    from app.services.channel_production import _redis
    return _redis()


def _require(value):
    if not value: raise ValueError('framecase_cadence_unverified')


def _now(now=None):
    value = datetime.now(timezone.utc) if now is None else datetime.fromtimestamp(now, timezone.utc)
    return value.astimezone(ZONE)


def kind(spec):
    value = spec.get('format')
    _require(value in {'shorts', 'landscape'})
    return 'shorts' if value == 'shorts' else 'long'


def keys(channel_id, *, now=None):
    day = _now(now).date().isoformat()
    base = PREFIX + channel_id + ':'
    return (base + 'produced:' + day, base + 'published:' + day, base + 'pending')


def _values(client, key):
    value = client.hgetall(key)
    _require(len(value) <= 10_000 and all(v in LIMITS for v in value.values()))
    return value


def production_slot(pipe, channel_id, format_kind, task_id, *, now=None):
    """Read inside the caller's WATCH; the caller writes with its job commit."""
    if channel_id not in CHANNELS: return None
    _require(format_kind in LIMITS and str(UUID(task_id)) == task_id)
    selected = keys(channel_id, now=now); pipe.watch(*selected)
    used = {}
    for key in selected: used.update(_values(pipe, key))
    if sum(v == format_kind for v in used.values()) >= LIMITS[format_kind]: return False
    return selected[0]


def _root(client, source):
    current = source; seen = set()
    channel = source.get('spec', {}).get('production_channel_id')
    for _ in range(16):
        task = current.get('task_id'); _require(str(UUID(task)) == task and task not in seen)
        seen.add(task)
        parent = current.get('parent_id')
        if not parent: return task
        previous = json.loads(client.get(jobs.JOB_PREFIX + parent) or '{}')
        _require(previous.get('retry_child_task_id') == task
            and previous.get('spec', {}).get('production_channel_id') == channel
            and kind(previous['spec']) == kind(source['spec']))
        current = previous
    raise ValueError('framecase_cadence_lineage_unverified')


def publication_slot(source, *, client=None, now=None):
    """Reserve once before queuing the first upload; False means ordinary wait."""
    channel = source.get('spec', {}).get('production_channel_id')
    if channel not in CHANNELS: return True
    client = client or _client(); format_kind = kind(source['spec'])
    root = _root(client, source); claim_key = PREFIX + 'publication:' + root
    _, day_key, pending_key = keys(channel, now=now)
    with client.pipeline() as pipe:
        pipe.watch(claim_key, day_key, pending_key)
        raw = pipe.get(claim_key)
        if raw:
            claim = json.loads(raw)
            _require(claim.get('source_task_id') == source['task_id'] and claim.get('channel_id') == channel
                and claim.get('kind') == format_kind and claim.get('root_task_id') == root)
            return True
        used = _values(pipe, day_key); used.update(_values(pipe, pending_key))
        if sum(v == format_kind for v in used.values()) >= LIMITS[format_kind]:
            # Waiting is not a financial or publication authorization.
            pipe.multi(); pipe.sadd(WAITING_KEY, source['task_id']); pipe.execute()
            return False
        claim = {'version': 1, 'source_task_id': source['task_id'], 'root_task_id': root,
            'channel_id': channel, 'kind': format_kind, 'status': 'reserved',
            'reserved_at': _now(now).isoformat()}
        pipe.multi(); pipe.set(claim_key, json.dumps(claim, sort_keys=True), nx=True)
        pipe.hset(pending_key, root, format_kind); pipe.srem(WAITING_KEY, source['task_id'])
        response = pipe.execute(); _require(response[0] is True)
    return True


def publication_completed(source, *, client=None, now=None):
    """Called only after a successful public release, never for a render alone."""
    channel = source.get('spec', {}).get('production_channel_id')
    if channel not in CHANNELS: return
    client = client or _client(); root = _root(client, source)
    claim_key = PREFIX + 'publication:' + root
    _, day_key, pending_key = keys(channel, now=now)
    with client.pipeline() as pipe:
        pipe.watch(claim_key, pending_key, day_key)
        claim = json.loads(pipe.get(claim_key) or '{}')
        _require(claim.get('source_task_id') == source['task_id'] and claim.get('channel_id') == channel
            and claim.get('root_task_id') == root and claim.get('kind') == kind(source['spec']))
        if claim.get('status') == 'public': return
        _require(claim.get('status') == 'reserved' and pipe.hget(pending_key, root) == claim['kind'])
        claim.update(status='public', completed_at=_now(now).isoformat(), day_key=day_key)
        pipe.multi(); pipe.hset(day_key, root, claim['kind']); pipe.hdel(pending_key, root)
        pipe.set(claim_key, json.dumps(claim, sort_keys=True)); pipe.execute()


def maintain(*, client=None):
    """A minute beat retries only unqueued, quality-approved publication waits."""
    client = client or _client()
    from app.publish_tasks import queue_automatic_publish
    from app.services.youtube_publish_state import UPLOAD_PREFIX
    outcomes = {}
    for task in sorted(client.smembers(WAITING_KEY))[:12]:
        try:
            if client.get(UPLOAD_PREFIX + task):
                client.srem(WAITING_KEY, task)
                continue
            outcomes[task] = queue_automatic_publish(task).get('status')
        except Exception:
            outcomes[task] = 'waiting'
    return {'status': 'checked', 'sources': outcomes}


def snapshot(channel_id, *, client=None, now=None):
    if channel_id not in CHANNELS: return None
    client = client or _client(); produced, published, pending = keys(channel_id, now=now)
    values = {name: _values(client, key) for name, key in
        (('produced', produced), ('published', published), ('pending', pending))}
    return {'timezone': 'Europe/Istanbul', 'date': _now(now).date().isoformat(), 'limits': LIMITS,
        'counts': {name: {k: sum(v == k for v in rows.values()) for k in LIMITS}
                   for name, rows in values.items()}}
