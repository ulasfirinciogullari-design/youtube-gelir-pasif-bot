"""Persisted owner cadence; changing a policy never rewrites delivery history."""
from functools import lru_cache
import json
import re

from app.config import settings

CHANNELS = frozenset({'UC5v9AvNtD3PTLgo6m1jROOA', 'UCgvESYtYbn2w9R2ExBOF_cw'})


@lru_cache(maxsize=8)
def _parse(raw):
    if not raw.strip(): return '', {}
    value = json.loads(raw)
    if (type(value) is not dict or set(value) != {'version', 'id', 'daily_limits'}
        or type(value['version']) is not int or value['version'] != 1
        or type(value['id']) is not str or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', value['id'])
        or type(value['daily_limits']) is not dict or not value['daily_limits']
        or not set(value['daily_limits']) <= CHANNELS
        or any(type(limit) is not int or not 1 <= limit <= 5 for limit in value['daily_limits'].values())):
        raise ValueError('channel_shorts_policy_invalid')
    return value['id'], value['daily_limits']


def _policy():
    return _parse(getattr(settings, 'studio_shorts_policy_json', ''))


def shorts_only(channel_id):
    return channel_id in _policy()[1]


def policy_id(channel_id):
    identity, limits = _policy()
    return identity if channel_id in limits else ''


def daily_limits(channel_id, fallback):
    limits = _policy()[1]
    return {'long': 0, 'shorts': limits[channel_id]} if channel_id in limits else dict(fallback)


def allows(channel_id, format_kind):
    return not shorts_only(channel_id) or format_kind == 'shorts'
