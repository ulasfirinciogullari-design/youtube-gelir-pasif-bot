"""Dated owner approval for additional exact Shorts, without resetting a day.

Separate from the earlier ten-video experiment. Funding and YouTube quota
checks are unchanged. Pending uploads retain the ordinary durable root claim.
"""
from datetime import date, datetime, timezone
import json
import re
from uuid import UUID

from app.services import shorts_experiment as batch, channel_formats as formats

PREFIX = 'youtube_studio:extra_shorts:v1:'


def keys(channel, day):
    batch._require(channel in formats.CHANNELS and type(day) is str
        and date.fromisoformat(day).isoformat() == day)
    key = PREFIX + day + ':' + channel
    return key, key + ':anchor', key + ':produced'


def validate(value, channel, day):
    batch._require(type(value) is dict and set(value) == {
        'version', 'purpose', 'channel_id', 'day', 'connection_id', 'profile_revision',
        'items', 'owner_evidence_sha256', 'authorized_at'}
        and type(value['version']) is int and value['version'] == 1
        and value['purpose'] == 'owner_extra_shorts' and value['channel_id'] == channel
        and value['day'] == day and type(value['items']) is list and 1 <= len(value['items']) <= 10
        and type(value['owner_evidence_sha256']) is str
        and re.fullmatch('[0-9a-f]{64}', value['owner_evidence_sha256']))
    for field in ('connection_id', 'profile_revision'):
        batch._require(type(value[field]) is str and re.fullmatch('[A-Za-z0-9_-]{8,128}', value[field]))
    seen = set()
    for row in value['items']:
        batch._require(type(row) is dict and set(row) == {'item_id', 'item_sha256'}
            and type(row['item_id']) is str and str(UUID(row['item_id'])) == row['item_id']
            and row['item_id'] not in seen and type(row['item_sha256']) is str
            and re.fullmatch('[0-9a-f]{64}', row['item_sha256']))
        seen.add(row['item_id'])
    stamp = datetime.fromisoformat(value['authorized_at'])
    batch._require(stamp.tzinfo is not None and stamp <= datetime.now(timezone.utc))
    return value


def read(reader, channel, *, now=None):
    if channel not in formats.CHANNELS:
        return None
    from app.services.channel_cadence import _now
    from app.services.content_plan import _sha
    day = _now(now).date().isoformat(); key, anchor_key, _ = keys(channel, day)
    encoded, anchor = (batch._read(reader, k) for k in (key, anchor_key))
    if encoded is None and anchor is None:
        return None
    batch._require(type(encoded) is str and 0 < len(encoded) <= 8192
        and reader.pttl(key) == reader.pttl(anchor_key) == -1)
    value = validate(json.loads(encoded), channel, day)
    batch._require(anchor == _sha(value))
    return value


def _entry(reader, channel, item_id, now):
    value = read(reader, channel, now=now)
    if value is None:
        return None
    row = next((r for r in value['items'] if r['item_id'] == item_id), None)
    return (value, row) if row else None


def production_slot(pipe, channel, item, task, *, now=None):
    from app.services import content_plan as plan, youtube_auth as auth, youtube_automation as profiles
    found = _entry(pipe, channel, item.get('id') if item else None, now)
    if found is None:
        return None
    value, row = found
    batch._require(item['format'] == 'shorts' and plan._sha(item) == row['item_sha256']
        and task == batch.root_id(channel, item['id']))
    linked = json.loads(batch._read(pipe, auth.CHANNEL_PREFIX + channel) or '{}')
    profile = json.loads(batch._read(pipe, profiles.PROFILE_PREFIX + channel) or '{}')
    batch._require(linked.get('connection_id') == value['connection_id']
        and profile.get('profile_revision') == value['profile_revision'])
    key = keys(channel, value['day'])[2]; pipe.watch(key)
    used = pipe.hgetall(key); allowed = {batch.root_id(channel, r['item_id']) for r in value['items']}
    batch._require(set(used) <= allowed and all(v == 'shorts' for v in used.values())
        and task not in used and len(used) < len(allowed))
    return key


def publication_allowed(pipe, source, root, *, now=None):
    from app.services import content_plan as plan, studio_state as jobs
    spec = source.get('spec') or {}; channel = spec.get('production_channel_id')
    item_id = spec.get('content_plan_item_id')
    found = _entry(pipe, channel, item_id, now)
    if found is None:
        return False
    value, row = found
    batch._require(spec.get('format') == 'shorts' and root == batch.root_id(channel, item_id)
        and spec.get('production_connection_id') == value['connection_id']
        and spec.get('production_profile_revision') == value['profile_revision'])
    original = json.loads(batch._read(pipe, jobs.JOB_PREFIX + root) or '{}')
    dispatch = json.loads(batch._read(pipe, plan.DISPATCH_PREFIX + item_id) or '{}')
    batch._require(original.get('task_id') == root and original.get('spec') == spec
        and dispatch.get('task_id') == root and dispatch.get('channel_id') == channel
        and plan._sha(dispatch.get('item')) == row['item_sha256']
        and plan.dispatch_spec_matches(dispatch, spec))
    key = keys(channel, value['day'])[2]; pipe.watch(key)
    batch._require(pipe.hget(key, root) == 'shorts')
    return True


def snapshot(channel, published, pending, *, client, now=None):
    value = read(client, channel, now=now)
    if value is None:
        return None
    roots = {batch.root_id(channel, row['item_id']) for row in value['items']}
    produced = client.hgetall(keys(channel, value['day'])[2])
    batch._require(set(produced) <= roots and all(v == 'shorts' for v in produced.values()))
    return {'date': value['day'], 'limit': len(roots), 'produced': len(produced),
        'published': len(roots & set(published)), 'pending': len(roots & set(pending))}


def install(client, value, *, expected_plan_revision, now=None):
    """Private owner operation for existing, not-yet-started queue entries."""
    from app.services import content_plan as plan, youtube_auth as auth, youtube_automation as profiles
    from app.services.channel_cadence import _now
    channel, day = value['channel_id'], value['day']; validate(value, channel, day)
    batch._require(day == _now(now).date().isoformat())
    key, anchor_key, produced = keys(channel, day)
    with client.pipeline() as pipe:
        pipe.watch(key, anchor_key, produced, plan.PLAN_PREFIX + channel,
            auth.CHANNEL_PREFIX + channel, auth.CREDENTIAL_PREFIX + channel, auth.CHANNEL_INDEX_KEY,
            profiles.PROFILE_PREFIX + channel)
        batch._require(not pipe.exists(key, anchor_key, produced))
        linked = json.loads(pipe.get(auth.CHANNEL_PREFIX + channel) or '{}')
        profile = json.loads(pipe.get(profiles.PROFILE_PREFIX + channel) or '{}')
        document = plan._plan(pipe.get(plan.PLAN_PREFIX + channel), channel)
        batch._require(document['revision'] == expected_plan_revision and document['enabled'] is True
            and linked.get('id') == channel and linked.get('connection_id') == value['connection_id']
            and linked.get('requires_reconnect') is not True and pipe.get(auth.CREDENTIAL_PREFIX + channel)
            and pipe.sismember(auth.CHANNEL_INDEX_KEY, channel)
            and profile.get('profile_revision') == value['profile_revision']
            and profile.get('production_enabled') is True and profile.get('auto_publish') is True)
        entries = {row['id']:row for row in document['items']}
        existing = batch._manifest(pipe, now)
        historical = {row['item_id'] for group in batch.resolved(pipe, existing)
            for row in group['history']} if existing else set()
        for row in value['items']:
            entry = entries.get(row['item_id'])
            batch._require(entry is not None and entry['format'] == 'shorts' and entry['series'] is None
                and not entry['depends_on'] and plan._sha(entry) == row['item_sha256']
                and row['item_id'] not in historical)
            entry_keys = (plan.DISPATCH_PREFIX + row['item_id'], plan.COMPLETION_PREFIX + row['item_id'],
                          plan.jobs.JOB_PREFIX + batch.root_id(channel, row['item_id']))
            pipe.watch(*entry_keys); batch._require(not pipe.exists(*entry_keys))
        pipe.multi(); pipe.set(key, plan._raw(value), nx=True); pipe.set(anchor_key, plan._sha(value), nx=True)
        batch._require(pipe.execute() == [True, True])
    return {'status': 'active', 'day': day, 'channel_id': channel, 'extra_shorts': len(value['items'])}
