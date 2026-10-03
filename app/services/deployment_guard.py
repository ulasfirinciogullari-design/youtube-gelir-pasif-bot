"""Refuse to run this system on a Redis that another deployment already uses.

Redis is the Celery broker as well as the job store. A copy of the bot started
with the live system's REDIS_URL would take live tasks and write live records.
The first start claims an empty Redis for STUDIO_DEPLOYMENT_ID; every later
start must find the same id. A Redis that already holds jobs or linked
channels and has no claim is only adopted when STUDIO_ADOPT_EXISTING_REDIS is
set. An unreachable Redis is left to the normal connection errors.
"""
import logging

import redis

from app.config import settings

MARKER_KEY = 'youtube_studio:deployment:v1'
_EXISTING_DATA_KEYS = ('youtube_studio:jobs', 'youtube_studio:oauth:channels:v3',
                       'youtube_studio:production:v1:active')
logger = logging.getLogger(__name__)


class SharedRedisError(RuntimeError):
    pass


def claim(*, client=None) -> str:
    deployment = str(getattr(settings, 'studio_deployment_id', '') or '').strip()
    if not deployment:
        return 'disabled'
    try:
        client = client or redis.Redis.from_url(settings.redis_url, decode_responses=True,
                                                socket_timeout=5, socket_connect_timeout=5)
        current = client.get(MARKER_KEY)
        if current is None:
            if (getattr(settings, 'studio_adopt_existing_redis', False) is not True
                    and client.exists(*_EXISTING_DATA_KEYS)):
                raise SharedRedisError(
                    'REDIS_URL already holds another deployment\'s jobs or channels. '
                    'Give this system its own Redis, or set STUDIO_ADOPT_EXISTING_REDIS=true on purpose.')
            if client.set(MARKER_KEY, deployment, nx=True):
                return 'claimed'
            current = client.get(MARKER_KEY)
    except SharedRedisError:
        raise
    except redis.RedisError:
        logger.warning('deployment guard could not read Redis; the normal connection errors apply')
        return 'unavailable'
    if current != deployment:
        raise SharedRedisError(
            f'REDIS_URL belongs to deployment {current!r}, not {deployment!r}. '
            'Give this system its own Redis.')
    return 'same'
