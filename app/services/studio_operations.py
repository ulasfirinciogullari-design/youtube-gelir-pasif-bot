"""Observational owner status. Never used to authorize spending or dispatch."""
from datetime import datetime, timezone
import json
import math
import re
import time

import redis
from app.config import settings

TICK_KEY = 'youtube_studio:operations:v1:last_production_tick'
PENDING_PREFIX = 'youtube_studio:next_series:v1:pending:'
FRESH_SECONDS = 180


def _client():
    return redis.Redis.from_url(settings.redis_url, decode_responses=True,
                                socket_connect_timeout=1, socket_timeout=1,
                                retry_on_timeout=False)


def record_tick(state, result, *, client=None, now=None):
    """Best effort, one bounded status record after the real worker task ends."""
    try:
        instant = time.time() if now is None else now
        if type(instant) not in (int, float) or not math.isfinite(instant) or instant < 0:
            return
        status = 'checked' if state == 'SUCCESS' and type(result) is dict and result.get('status') in {
            'idle', 'active', 'queued'} else 'blocked'
        value = {'version': 1, 'observed_ts': instant, 'status': status}
        (client if client is not None else _client()).set(TICK_KEY, json.dumps(value), ex=86400)
    except Exception:
        pass  # A telemetry outage must never change the completed task result.


def read_tick(*, client=None, now=None):
    fallback = {'status': 'unavailable', 'observed_at': None}
    try:
        instant = time.time() if now is None else now
        raw = (client if client is not None else _client()).get(TICK_KEY)
        if type(raw) is not str or len(raw) > 512:
            return fallback
        value = json.loads(raw)
        stamp = value.get('observed_ts')
        if (value.get('version') != 1 or type(stamp) not in (int, float)
                or not math.isfinite(stamp) or not 0 <= stamp <= instant
                or value.get('status') not in {'checked', 'blocked'}):
            return fallback
        status = value['status'] if instant - stamp <= FRESH_SECONDS else 'stale'
        return {'status': status, 'observed_at': datetime.fromtimestamp(stamp, timezone.utc).isoformat()}
    except Exception:
        return fallback


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
        return {'status': value['status'], 'daily_wait': value['status'] == 'failed' and day == today}
    except Exception:
        return None
