"""New system: after a rejected Short, go on to the next topic instead of waiting.

Today any failed scheduled video pauses its channel until the owner resumes
it. Here a Short that a quality gate rejected (story, voice, stock footage or
render) releases the pause so the next topic can start, at most a few times a
Turkey day. A Short stopped by the daily spend cap moves on the next Turkey
day. After a few failures in a row with no public video between them the
channel pauses again, so a lasting defect cannot spend every day unnoticed.
Every other failure, such as the per-Short script cap (a looping planner),
another spend block, a lost worker or an unverified review, still pauses.
Nothing is retried, published or approved here, and the failed job keeps
every record.
"""
from datetime import datetime, timedelta, timezone
import json
import time

import redis

from app.config import settings
from app.services import channel_production as production

PREFIX = 'youtube_studio:auto_advance:v1:'
_CONTENT_REJECTIONS = frozenset({'story_rejected', 'audio_rejected', 'stock_rejected', 'render_rejected'})
_LOCAL_TZ = timezone(timedelta(hours=3))
_DELAY_SECONDS = 600
# Exact SpendBlocked code from cost_meter; the job error must match it whole.
_DAILY_CAP_ERROR = 'cost_daily_cap_reached'


def enabled() -> bool:
    from app.services.production_spend_runtime import enforcement_enabled
    # With enforcement on, the funded quality-hold path owns this decision.
    return (getattr(settings, 'studio_auto_advance_after_rejection', False) is True
            and not enforcement_enabled())


def _limit() -> int:
    value = getattr(settings, 'studio_auto_advance_max_per_day', 0)
    return value if type(value) is int and 0 <= value <= 10 else 0


def _in_a_row_limit() -> int:
    value = getattr(settings, 'studio_auto_advance_max_in_a_row', 0)
    return value if type(value) is int and 0 <= value <= 10 else 0


def _streak(state: dict) -> int:
    """Advances since the last public video; unreadable state counts as too many."""
    try:
        count = int(state.get('auto_advance_streak') or 0)
        since = float(state.get('auto_advance_streak_since') or 0)
        public = float(state.get('last_public_continued_at') or 0)
    except (TypeError, ValueError):
        return 10 ** 6
    if count < 0 or since != since or public != public:
        return 10 ** 6
    return 0 if public > since else count


def _advance_reason(job: dict) -> str | None:
    from app.services.production_failures import _digest, classified_hold_reason

    reason = classified_hold_reason(job)
    if reason in _CONTENT_REJECTIONS:
        return reason
    error, evidence = job.get('error'), job.get('failure_classification')
    if (reason is None and error == _DAILY_CAP_ERROR and isinstance(evidence, dict)
            and evidence.get('version') == 1
            and evidence.get('code') == evidence.get('category') == 'spending_blocked'
            and evidence.get('stage') == job.get('failure_stage')
            and evidence.get('error_sha256') == _digest(error)):
        return error
    return None


def _next_due(reason: str, now: float) -> float:
    if reason != _DAILY_CAP_ERROR:
        return now + _DELAY_SECONDS
    today = datetime.fromtimestamp(now, _LOCAL_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    return (today + timedelta(days=1)).timestamp() + _DELAY_SECONDS


def advance(channel_id: str, *, client=None, now: float | None = None) -> str:
    """One watched transition for one channel; returns what happened."""

    now = time.time() if now is None else now
    client = client or production._redis()
    day = datetime.fromtimestamp(now, _LOCAL_TZ).strftime('%Y-%m-%d')
    state_key = production.CHANNEL_STATE_PREFIX + channel_id
    day_key = PREFIX + channel_id + ':' + day
    with client.pipeline() as pipe:
        pipe.watch(state_key, production.ACTIVE_KEY, day_key)
        state = pipe.hgetall(state_key)
        if state.get('paused_reason') != 'previous_render_failed':
            return 'not_paused'
        task_id = state.get('last_task_id') or ''
        if (state.get('last_result') != 'FAILURE' or state.get('active_task_id')
                or not production._TASK_ID.fullmatch(task_id)):
            return 'not_eligible'
        pipe.watch(production.JOB_PREFIX + task_id)
        try:
            job = json.loads(pipe.get(production.JOB_PREFIX + task_id) or '')
        except ValueError:
            return 'not_eligible'
        spec = job.get('spec') if isinstance(job, dict) else None
        if (not isinstance(spec, dict) or job.get('task_id') != task_id or job.get('state') != 'FAILURE'
                or spec.get('production_scheduled') is not True
                or spec.get('production_channel_id') != channel_id
                or spec.get('format') != 'shorts'):
            return 'not_eligible'
        reason = _advance_reason(job)
        if reason is None:
            return 'needs_owner'
        if pipe.sismember(day_key, task_id):
            return 'already_advanced'
        if pipe.scard(day_key) >= _limit():
            return 'daily_limit'
        streak = _streak(state)
        if streak >= _in_a_row_limit():
            return 'too_many_in_a_row'
        pipe.multi()
        pipe.hdel(state_key, 'paused_reason')
        pipe.hset(state_key, mapping={'next_due': str(_next_due(reason, now)),
                                      'auto_advanced_task_id': task_id,
                                      'auto_advance_streak': str(streak + 1),
                                      'auto_advance_streak_since': (
                                          state.get('auto_advance_streak_since') or str(now)
                                          if streak else str(now))})
        pipe.sadd(day_key, task_id)
        pipe.expire(day_key, 3 * 86400)
        pipe.execute()
    return 'advanced'


def maintain(profiles, *, now: float | None = None) -> dict:
    if not enabled() or _limit() == 0:
        return {'status': 'disabled', 'channels': {}}
    results = {}
    for profile in profiles if isinstance(profiles, list) else []:
        channel_id = profile.get('channel_id') if isinstance(profile, dict) else None
        if (not isinstance(channel_id, str) or channel_id in results
                or profile.get('production_enabled') is not True or profile.get('auto_publish') is not True):
            continue
        try:
            results[channel_id] = advance(production._channel_id(channel_id), now=now)
        except (redis.WatchError, production.ChannelProductionError):
            results[channel_id] = 'changed'
        except Exception:
            results[channel_id] = 'unavailable'
    return {'status': 'checked', 'channels': results}
