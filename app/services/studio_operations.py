"""Observational owner status. Never used to authorize spending or dispatch."""
from datetime import datetime, timezone, timedelta
import json
import math
import re
import time

import redis
from app.config import settings

TICK_KEY = 'youtube_studio:operations:v1:last_production_tick'
PENDING_PREFIX = 'youtube_studio:next_series:v1:pending:'
FRESH_SECONDS = 180
MAX_TICK_BYTES = 8192


def _quality_wait(value, instant):
    """Accept only the current UTC day's exact, bounded wait observation."""
    if (type(value) is not dict or set(value) != {
            'status', 'root_task_id', 'profile_revision', 'retry_after'}
            or value.get('status') != 'daily_hold_limit'
            or type(value.get('root_task_id')) is not str
            or not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', value['root_task_id'])
            or type(value.get('profile_revision')) is not str
            or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value['profile_revision'])):
        return None
    tomorrow = (datetime.fromtimestamp(instant, timezone.utc) + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0).isoformat()
    if value.get('retry_after') != tomorrow:
        return None
    return dict(value)


def _tick_value(client, instant):
    if type(instant) not in (int, float) or not math.isfinite(instant) or instant < 0:
        return None
    raw = client.get(TICK_KEY)
    if type(raw) is not str or len(raw.encode()) > MAX_TICK_BYTES:
        return None
    value = json.loads(raw)
    if type(value) is not dict:
        return None
    stamp = value.get('observed_ts')
    if (type(value.get('version')) is not int or value['version'] != 1
            or type(stamp) not in (int, float) or not math.isfinite(stamp)
            or not 0 <= stamp <= instant or value.get('status') not in {'checked', 'blocked'}):
        return None
    return value


def _client():
    return redis.Redis.from_url(settings.redis_url, decode_responses=True,
                                socket_connect_timeout=1, socket_timeout=1,
                                retry_on_timeout=False)


def held_task_ids(records, *, client=None):
    """One bounded observation for presentation; never a retry permission."""
    try:
        from app.services.studio_state import QUALITY_HOLD_PREFIX, QUALITY_HOLD_JOB_FENCE_PREFIX
        if type(records) is not list or len(records) > 1000:
            return set()
        task_ids = sorted({row['task_id'] for row in records if type(row) is dict
            and row.get('kind') == 'render' and row.get('state') == 'FAILURE'
            and type(row.get('task_id')) is str
            and re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', row['task_id'])})
        if not task_ids:
            return set()
        keys = [prefix + task for task in task_ids
                for prefix in (QUALITY_HOLD_PREFIX, QUALITY_HOLD_JOB_FENCE_PREFIX)]
        values = (client if client is not None else _client()).mget(keys)
        if type(values) is not list or len(values) != len(keys):
            return set()
        return {task for index, task in enumerate(task_ids)
                if any(value is not None for value in values[index * 2:index * 2 + 2])}
    except Exception:
        return set()  # Backend retry/voice guards remain authoritative on an outage.


def record_tick(state, result, *, client=None, now=None):
    """Best effort, one bounded status record after the real worker task ends."""
    try:
        instant = time.time() if now is None else now
        if type(instant) not in (int, float) or not math.isfinite(instant) or instant < 0:
            return
        status = 'checked' if state == 'SUCCESS' and type(result) is dict and result.get('status') in {
            'idle', 'active', 'queued'} else 'blocked'
        value = {'version': 1, 'observed_ts': instant, 'status': status}
        holds = result.get('quality_holds') if type(result) is dict else None
        if status == 'checked' and type(holds) is dict and holds.get('status') == 'checked':
            channels = holds.get('channels')
            if type(channels) is dict and len(channels) <= 8:
                waits = {}
                for channel, row in channels.items():
                    if type(channel) is str and re.fullmatch(r'UC[A-Za-z0-9_-]{22}', channel):
                        wait = _quality_wait(row, instant)
                        if wait:
                            waits[channel] = wait
                if waits:
                    value['quality_waits'] = waits
        (client if client is not None else _client()).set(TICK_KEY, json.dumps(value), ex=86400)
    except Exception:
        pass  # A telemetry outage must never change the completed task result.


def read_tick(*, client=None, now=None):
    fallback = {'status': 'unavailable', 'observed_at': None}
    try:
        instant = time.time() if now is None else now
        value = _tick_value(client if client is not None else _client(), instant)
        if value is None:
            return fallback
        stamp = value['observed_ts']
        status = value['status'] if instant - stamp <= FRESH_SECONDS else 'stale'
        return {'status': status, 'observed_at': datetime.fromtimestamp(stamp, timezone.utc).isoformat()}
    except Exception:
        return fallback


def read_quality_wait(channel_id, profile_revision, root_task_id, *, client=None, now=None):
    """A fresh worker result bound to the same paused job, never dispatch authority."""
    try:
        if type(channel_id) is not str or not re.fullmatch(r'UC[A-Za-z0-9_-]{22}', channel_id):
            return None
        instant = time.time() if now is None else now
        value = _tick_value(client if client is not None else _client(), instant)
        if (value is None or value['status'] != 'checked'
                or instant - value['observed_ts'] > FRESH_SECONDS):
            return None
        waits = value.get('quality_waits')
        if type(waits) is not dict or len(waits) > 8:
            return None
        wait = _quality_wait(waits.get(channel_id), instant)
        if (wait is None or wait['profile_revision'] != profile_revision
                or wait['root_task_id'] != root_task_id):
            return None
        return {'status': wait['status'], 'retry_after': wait['retry_after']}
    except Exception:
        return None


def read_series_preparation(channel_id, profile_revision, *, client=None, now=None):
    """Read only the current profile's planner state, without exposing prompts."""
    try:
        if (type(channel_id) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{8,128}', channel_id)
                or type(profile_revision) is not str or not profile_revision):
            return None
        raw = (client if client is not None else _client()).get(PENDING_PREFIX + channel_id)
        if type(raw) is not str or len(raw.encode()) > 32768:
            return None
        value = json.loads(raw)
        if (value.get('channel_id') != channel_id or value.get('profile_revision') != profile_revision
                or value.get('status') not in {'reserved', 'uncertain', 'ready', 'failed'}):
            return None
        day = datetime.strptime(value['day'], '%Y-%m-%d').date()
        today = datetime.fromtimestamp(time.time() if now is None else now, timezone.utc).date()
        if day > today:
            return None
        slot = value.get('preparation_slot', 1)
        if type(slot) is not int or slot not in (1, 2, 3):
            return None
        return {'status': value['status'], 'attempt_number': slot,
                'daily_wait': value['status'] in {'failed', 'uncertain'} and day == today and slot == 3}
    except Exception:
        return None
