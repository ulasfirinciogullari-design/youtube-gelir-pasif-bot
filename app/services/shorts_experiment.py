"""A dated owner-approved batch grants only its ten exact editorial items.

The ordinary 4+1 policy and its historical counters are never changed. Every
admission still goes through the normal funding, rendering and publishing gates.
An item has one deterministic root; retries cannot acquire a second release.
"""
from datetime import date
import json
import re
from uuid import UUID, uuid5, NAMESPACE_URL

PREFIX = 'youtube_studio:shorts_experiment:v1:'
COUNTS = {'UC5v9AvNtD3PTLgo6m1jROOA': 8, 'UCgvESYtYbn2w9R2ExBOF_cw': 2}


def _require(value):
    if not value:
        raise ValueError('shorts_experiment_unverified')


def approval_key(day):
    _require(type(day) is str and date.fromisoformat(day).isoformat() == day)
    return PREFIX + 'approval:' + day


def produced_key(day, channel):
    _require(channel in COUNTS)
    return PREFIX + 'produced:' + day + ':' + channel


def root_id(channel, item_id):
    return str(uuid5(NAMESPACE_URL, 'youtube-owner-plan:' + channel + ':' + item_id))


def validate(value, day):
    _require(type(value) is dict and set(value) == {'version', 'id', 'day', 'items'}
        and type(value['version']) is int and value['version'] == 1
        and type(value['id']) is str and re.fullmatch(r'[a-z0-9_-]{1,64}', value['id'])
        and value['day'] == day and type(value['items']) is list and len(value['items']) == 10)
    approval_key(day)
    seen, counts = set(), dict.fromkeys(COUNTS, 0)
    for row in value['items']:
        _require(type(row) is dict and set(row) == {'item_id', 'channel_id', 'item_sha256'}
            and row['channel_id'] in COUNTS and type(row['item_id']) is str
            and str(UUID(row['item_id'])) == row['item_id'] and row['item_id'] not in seen
            and type(row['item_sha256']) is str and re.fullmatch('[0-9a-f]{64}', row['item_sha256']))
        seen.add(row['item_id']); counts[row['channel_id']] += 1
    _require(counts == COUNTS)
    return value


def _read(reader, key):
    from redis.client import Pipeline
    if isinstance(reader, Pipeline):
        reader.watch(key)
    return reader.get(key)


def _manifest(reader, now):
    from app.services.channel_cadence import _now
    day = _now(now).date().isoformat()
    raw = _read(reader, approval_key(day))
    if raw is None:
        return None
    _require(type(raw) is str and 0 < len(raw) <= 16_384)
    return validate(json.loads(raw), day)


def _entry(reader, channel, item_id, now):
    if channel not in COUNTS or not item_id:
        return None
    value = _manifest(reader, now)
    if value is None:
        return None
    found = [row for row in value['items'] if row['item_id'] == item_id]
    if not found:
        return None
    _require(len(found) == 1 and found[0]['channel_id'] == channel)
    return value, found[0]


def production_slot(pipe, channel, item, task, *, now=None):
    """Return a separate bounded receipt key; None uses ordinary daily policy."""
    from app.services import content_plan as plan
    found = _entry(pipe, channel, item.get('id') if item else None, now)
    if found is None:
        return None
    manifest, row = found
    _require(item['format'] == 'shorts' and plan._sha(item) == row['item_sha256']
        and task == root_id(channel, row['item_id']))
    key = produced_key(manifest['day'], channel)
    pipe.watch(key)
    used = pipe.hgetall(key)
    allowed = {root_id(channel, r['item_id']) for r in manifest['items'] if r['channel_id'] == channel}
    _require(set(used) <= allowed and all(v == 'shorts' for v in used.values())
        and task not in used and len(used) < COUNTS[channel])
    return key


def publication_allowed(pipe, source, root, *, now=None):
    """Authorize only an exact admitted plan root or its unchanged-spec child."""
    from app.services import content_plan as plan, studio_state as jobs
    spec = source.get('spec') or {}
    channel, item_id = spec.get('production_channel_id'), spec.get('content_plan_item_id')
    found = _entry(pipe, channel, item_id, now)
    if found is None:
        return False
    manifest, row = found
    _require(spec.get('format') == 'shorts' and root == root_id(channel, item_id))
    original = json.loads(_read(pipe, jobs.JOB_PREFIX + root) or '{}')
    dispatch = json.loads(_read(pipe, plan.DISPATCH_PREFIX + item_id) or '{}')
    _require(original.get('task_id') == root and original.get('spec') == spec
        and dispatch.get('task_id') == root and dispatch.get('channel_id') == channel
        and plan._sha(dispatch.get('item')) == row['item_sha256']
        and plan.dispatch_spec_matches(dispatch, spec))
    key = produced_key(manifest['day'], channel); pipe.watch(key)
    _require(pipe.hget(key, root) == 'shorts')
    return True


def snapshot(channel, published, pending, *, client, now=None):
    if channel not in COUNTS:
        return None
    value = _manifest(client, now)
    if value is None:
        return None
    roots = {root_id(channel, r['item_id']) for r in value['items'] if r['channel_id'] == channel}
    produced = client.hgetall(produced_key(value['day'], channel))
    _require(set(produced) <= roots and all(v == 'shorts' for v in produced.values()))
    return {'id': value['id'], 'date': value['day'], 'limit': COUNTS[channel],
        'produced': len(produced), 'published': len(roots & set(published)),
        'pending': len(roots & set(pending))}
