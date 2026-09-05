"""Audited, one-shot advancement of an idle channel's next existing topic."""

from __future__ import annotations

import hashlib
import json
import math
import re
import time

import redis

from app.config import settings
from app.services.channel_production import (
    ACTIVE_KEY, CHANNEL_STATE_PREFIX, OAUTH_CHANNEL_INDEX,
    OAUTH_CHANNEL_PREFIX, OAUTH_CREDENTIAL_PREFIX, PROFILE_PREFIX,
    PRODUCTION_PREFIX, _decode_active_claims, _prefix_digest,
)


EXPEDITE_PREFIX = PRODUCTION_PREFIX + 'expedite:'
_ID = re.compile(r'^[A-Za-z0-9_-]{8,128}$')
_REVISION = re.compile(r'^[A-Za-z0-9_-]{1,128}$')
_TASK_ID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
_SHA256 = re.compile(r'^[0-9a-f]{64}$')


class ProductionScheduleControlError(RuntimeError):
    """Safe failure without revealing Redis records or credentials."""


def _redis():
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ProductionScheduleControlError(code)


def _object(raw: str | None) -> dict:
    value = json.loads(raw or '')
    _require(isinstance(value, dict), 'schedule_record_invalid')
    return value


def _number(value) -> float:
    _require(type(value) in (int, float) and math.isfinite(value) and value >= 0,
             'schedule_time_invalid')
    return float(value)


def _prior_result(raw: str, channel_id: str, revision: str) -> dict:
    audit = _object(raw)
    _require(
        set(audit) == {
            'version', 'channel_id', 'profile_revision', 'connection_id', 'cursor',
            'consumed_prefix', 'next_topic_sha256', 'previous_next_due', 'next_due', 'requested_at',
        }
        and type(audit.get('version')) is int and audit['version'] == 1
        and audit.get('channel_id') == channel_id and audit.get('profile_revision') == revision
        and isinstance(audit.get('connection_id'), str) and _ID.fullmatch(audit['connection_id'])
        and type(audit.get('cursor')) is int and audit['cursor'] > 0
        and all(isinstance(audit.get(key), str) and _SHA256.fullmatch(audit[key])
                for key in ('consumed_prefix', 'next_topic_sha256')),
        'schedule_audit_invalid',
    )
    old_due = _number(audit['previous_next_due'])
    due = _number(audit['next_due'])
    requested = _number(audit['requested_at'])
    _require(due == min(old_due, requested), 'schedule_audit_invalid')
    # This reports a past operation, not current eligibility. In particular,
    # retrying after the scheduler advances its cursor cannot expedite again.
    return {**audit, 'status': 'already_expedited'}


def expedite_next_production(
    channel_id: str,
    expected_profile_revision: str,
    *,
    now: float | None = None,
) -> dict:
    """Move only next_due earlier, once per explicitly supplied profile revision.

    This does not assert public-delivery proof, change cadence, enable a profile,
    clear a pause, reserve a topic, enqueue a job or touch any spending ledger.
    The caller must complete any required prior publication before invoking it.
    Ordinary scheduler reservation and all subsequent QA/publish checks remain.
    """
    _require(isinstance(channel_id, str) and _ID.fullmatch(channel_id), 'schedule_channel_invalid')
    _require(isinstance(expected_profile_revision, str) and _REVISION.fullmatch(expected_profile_revision),
             'schedule_revision_invalid')
    now = _number(time.time() if now is None else now)
    audit_key = EXPEDITE_PREFIX + channel_id + ':' + expected_profile_revision
    state_key = CHANNEL_STATE_PREFIX + channel_id
    profile_key = PROFILE_PREFIX + channel_id
    connection_key = OAUTH_CHANNEL_PREFIX + channel_id
    credentials_key = OAUTH_CREDENTIAL_PREFIX + channel_id
    try:
        client = _redis()
        with client.pipeline() as pipe:
            pipe.watch(audit_key, state_key, profile_key, ACTIVE_KEY, connection_key,
                       credentials_key, OAUTH_CHANNEL_INDEX)
            prior = pipe.get(audit_key)
            if prior is not None:
                return _prior_result(prior, channel_id, expected_profile_revision)
            profile = _object(pipe.get(profile_key))
            state = pipe.hgetall(state_key)
            connection = _object(pipe.get(connection_key))
            _require(
                profile.get('channel_id') == channel_id
                and profile.get('profile_revision') == expected_profile_revision
                and profile.get('production_enabled') is True
                and profile.get('auto_publish') is True
                and profile.get('release_mode') == 'public'
                and type(profile.get('production_interval_hours')) is int
                and 6 <= profile['production_interval_hours'] <= 168
                and profile.get('default_language') in {'tr', 'en', 'de', 'es', 'ar'}
                and isinstance(profile.get('languages'), list)
                and profile['default_language'] in profile['languages'],
                'schedule_profile_not_eligible',
            )
            _require(
                bool(state) and not state.get('paused_reason') and not state.get('active_task_id')
                and state.get('dispatch_status') == 'finished',
                'schedule_channel_not_idle',
            )
            active_raw = pipe.get(ACTIVE_KEY)
            claims = _decode_active_claims(active_raw) if active_raw is not None else []
            _require(all(claim['channel_id'] != channel_id for claim in claims), 'schedule_channel_not_idle')
            connection_id = connection.get('connection_id')
            _require(
                connection.get('id') == channel_id
                and isinstance(connection_id, str) and _ID.fullmatch(connection_id)
                and state.get('connection_id') == connection_id
                and pipe.type(credentials_key) == 'string' and pipe.strlen(credentials_key) > 0
                and pipe.sismember(OAUTH_CHANNEL_INDEX, channel_id),
                'schedule_connection_invalid',
            )
            topics = profile.get('production_topics')
            _require(
                isinstance(topics, list) and 1 <= len(topics) <= 60
                and all(isinstance(topic, str) and topic.strip() and len(topic) <= 240 for topic in topics),
                'schedule_topics_invalid',
            )
            topics = [topic.strip() for topic in topics]
            _require(len(set(topics)) == len(topics), 'schedule_topics_invalid')
            cursor = int(state.get('cursor', ''))
            _require(str(cursor) == state.get('cursor') and 1 <= cursor < len(topics),
                     'schedule_no_next_topic')
            prefix = _prefix_digest(topics[:cursor])
            _require(state.get('consumed_prefix') == prefix, 'schedule_consumed_topics_changed')
            _require(isinstance(state.get('last_task_id'), str) and _TASK_ID.fullmatch(state['last_task_id']),
                     'schedule_state_invalid')
            previous_due = _number(float(state.get('next_due', '')))
            due = min(previous_due, now)
            audit = {
                'version': 1, 'channel_id': channel_id, 'profile_revision': expected_profile_revision,
                'connection_id': connection_id, 'cursor': cursor, 'consumed_prefix': prefix,
                'next_topic_sha256': hashlib.sha256(topics[cursor].encode('utf-8')).hexdigest(),
                'previous_next_due': previous_due, 'next_due': due, 'requested_at': now,
            }
            pipe.multi()
            pipe.set(audit_key, json.dumps(audit, ensure_ascii=False, sort_keys=True), nx=True)
            if due < previous_due:
                pipe.hset(state_key, 'next_due', str(due))
            results = pipe.execute()
            _require(bool(results) and results[0] is True, 'schedule_state_changed')
            return {**audit, 'status': 'expedited' if due < previous_due else 'already_due'}
    except ProductionScheduleControlError:
        raise
    except redis.WatchError:
        raise ProductionScheduleControlError('schedule_state_changed') from None
    except Exception:
        # A lost commit reply is retried with the same revision: the audit
        # identifies the earlier operation without moving the next topic again.
        raise ProductionScheduleControlError('schedule_state_unavailable') from None
