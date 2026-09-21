"""Bounded post-dispatch series maintenance and at-most-once preparation.

No model runs in the minute tick. Preparation uses the existing consumed
default Celery queue; dedicated worker isolation is a separate deployment
decision. Unknown queue/execution outcomes remain durable no-replay fences.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
import re
from uuid import NAMESPACE_URL, uuid4, uuid5

import redis

from app.config import settings
from app.services.channel_production import (
    CHANNEL_STATE_PREFIX, OAUTH_CHANNEL_PREFIX, PROFILE_PREFIX, _dispatch_profile_order, _prefix_digest,
)
from app.services.production_next_series import (
    PENDING_PREFIX, DAILY_PREFIX, PREPARATION_DISPATCH_PREFIX, _FLAGS, _context, _digest, _execution_guard, _execution_keys,
    _json, _object, _planning_channel_identity, _require, prepare_next_series,
)
from app.services.production_series_promotion import promote_ready_series, retire_stale_ready_batch


MAX_LINKED_CHANNELS = 10


def _client():
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


def _now(value):
    value = datetime.now(timezone.utc).timestamp() if value is None else value
    _require(type(value) in (int, float) and math.isfinite(value) and value >= 0)
    return value


def _current(client, channel_id):
    profile = _object(client.get(PROFILE_PREFIX + channel_id))
    channel = _object(client.get(OAUTH_CHANNEL_PREFIX + channel_id))
    state = client.hgetall(CHANNEL_STATE_PREFIX + channel_id)
    context = _context(profile, channel)
    topics = context['existing_topics']
    # Preparation is paid work: reject a series that the eventual promotion
    # cannot consume before either enqueue or worker execution is reserved.
    _require(type(profile.get('series_total')) is int and profile['series_total'] == len(topics)
             and isinstance(profile.get('series_id'), str)
             and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', profile['series_id'])
             and isinstance(profile.get('series_name'), str)
             and type(profile.get('production_interval_hours')) is int
             and 6 <= profile['production_interval_hours'] <= 168)
    cursor = int(state.get('cursor', '-1'))
    _require(profile.get('release_mode') == 'public' and not state.get('paused_reason')
             and str(cursor) == state.get('cursor') and 0 <= cursor <= len(topics)
             and state.get('consumed_prefix') == _prefix_digest(topics[:cursor])
             and state.get('profile_revision') == profile['profile_revision']
             and state.get('connection_id') == channel['connection_id'])
    return profile, channel, state, len(topics) - cursor


def _pending_status(client, channel_id, day):
    raw = client.get(PENDING_PREFIX + channel_id)
    if raw is not None:
        value = _object(raw)
        _require(type(value.get('version')) is int and value['version'] == 1
                 and value.get('channel_id') == channel_id
                 and all(type(value.get(k)) is type(v) and value[k] == v for k, v in _FLAGS.items())
                 and value.get('status') in {'ready', 'reserved', 'uncertain', 'failed'})
        if value['status'] != 'failed':
            return value['status'], value
        _require(isinstance(value.get('day'), str))
        if value['day'] >= day:
            return 'failed', value
    if client.get(DAILY_PREFIX + channel_id + ':' + day) is not None:
        return 'daily_fenced', None
    return 'available', None


def _mark_dispatch(client, key, original, status, *, outcome=None, delivery_only=False):
    """Only annotate this reservation; never replay or replace a newer state."""
    try:
        # A broker acknowledgement can race an already-started worker. Keep
        # producer delivery telemetry outside the proof key watched by worker
        # execution and paid reservation. It never authorizes another enqueue.
        destination = key + ':delivery' if delivery_only else key
        with client.pipeline() as pipe:
            pipe.watch(key, destination)
            if pipe.get(key) != original:
                return
            record = _object(original)
            record['status'] = status
            if outcome is not None:
                record['outcome'] = outcome
            pipe.multi()
            pipe.set(destination, _json(record))
            pipe.execute()
    except Exception:
        pass  # original durable reservation still forbids another enqueue


def retire_finished_uncertain_batch(client, channel_id, expected_revision, *, now):
    """Let a later day's ordinary planning proceed after a finished failure.

    Preserve the original daily/execution/provider records permanently. Only
    the active pending pointer is retired, after an atomic full archive. An
    unknown broker or worker outcome, or a same-day attempt, is never retired.
    """
    day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
    pending_key = PENDING_PREFIX + channel_id
    with client.pipeline() as pipe:
        pipe.watch(pending_key, PROFILE_PREFIX + channel_id, OAUTH_CHANNEL_PREFIX + channel_id,
                   CHANNEL_STATE_PREFIX + channel_id)
        profile, _, _, remaining = _current(pipe, channel_id)
        _require(profile['profile_revision'] == expected_revision and remaining <= 2)
        raw = pipe.get(pending_key)
        pending = _object(raw)
        _require(pending.get('status') == 'uncertain' and pending.get('error_code') == 'model_outcome_uncertain'
                 and pending.get('channel_id') == channel_id and type(pending.get('version')) is int
                 and pending['version'] == 1
                 and all(type(pending.get(k)) is type(v) and pending[k] == v for k, v in _FLAGS.items())
                 and isinstance(pending.get('day'), str)
                 and re.fullmatch(r'\d{4}-\d{2}-\d{2}', pending['day'])
                 and isinstance(pending.get('attempt_id'), str)
                 and re.fullmatch('[0-9a-f]{32}', pending['attempt_id']))
        if pending['day'] >= day:
            return 'uncertain'
        daily_key = DAILY_PREFIX + channel_id + ':' + pending['day']
        # Resolve only the known terminal dispatch, never assume expiry means
        # a running/unknown worker finished or that a model call was free.
        dispatch_key = PREPARATION_DISPATCH_PREFIX + channel_id + ':' + pending['day']
        pipe.watch(daily_key, dispatch_key)
        dispatch_raw = pipe.get(dispatch_key)
        dispatch = _object(dispatch_raw)
        binding = {k: dispatch.get(k) for k in ('version', 'channel_id', 'profile_revision', 'day', 'task_id', 'token')}
        resolved = _execution_keys(binding)
        execution_key = resolved[1]
        archive_key = 'youtube_studio:next_series:v1:superseded:' + channel_id + ':' + pending['attempt_id']
        anchor_key = archive_key + ':anchor'
        pipe.watch(execution_key, archive_key, anchor_key)
        _require(resolved[0] == dispatch_key and dispatch.get('status') == 'finished'
                 and dispatch.get('outcome') == 'uncertain' and binding['channel_id'] == channel_id
                 and binding['day'] == pending['day'] and binding['profile_revision'] == pending.get('profile_revision')
                 and dispatch.get('connection_id') == pending.get('connection_id')
                 and pipe.get(execution_key) == binding['token'] and pipe.get(daily_key) == raw
                 and all(pipe.pttl(key) == -1 for key in (pending_key, daily_key, dispatch_key, execution_key))
                 and not pipe.exists(archive_key, anchor_key))
        archive = {'version': 1, 'reason': 'finished_uncertain_previous_day', 'retired_at': now,
            'pending_batch': pending, 'original_pending_raw': raw, 'original_dispatch_raw': dispatch_raw,
            'original_execution_claim': binding['token'], 'provider_outcome_still_unknown': True,
            'daily_and_provider_records_preserved': True, **_FLAGS}
        pipe.multi()
        pipe.set(archive_key, _json(archive), nx=True)
        pipe.set(anchor_key, _digest(archive), nx=True)
        pipe.delete(pending_key)
        reply = pipe.execute()
        _require(type(reply) is list and len(reply) == 3 and reply[0] is True
                 and reply[1] is True and type(reply[2]) is int and reply[2] == 1)
    return 'finished_uncertain_archived'


def _reserve_preparation(channel_id, expected_revision, expected_connection, enqueue, now):
    client = _client()
    day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
    binding = {'version': 1, 'channel_id': channel_id, 'profile_revision': expected_revision,
               'day': day, 'task_id': str(uuid5(NAMESPACE_URL, f'youtube-series-preparation:{channel_id}:{day}')),
               'token': uuid4().hex}
    keys = _execution_keys(binding)
    dispatch_key, execution_key, credential_key, index_key, epoch_key = keys
    with client.pipeline() as pipe:
        pipe.watch(*keys, PROFILE_PREFIX + channel_id, OAUTH_CHANNEL_PREFIX + channel_id,
                   CHANNEL_STATE_PREFIX + channel_id, PENDING_PREFIX + channel_id, DAILY_PREFIX + channel_id + ':' + day)
        if pipe.get(dispatch_key) is not None:
            return {'status': 'preparation_already_reserved'}
        profile, channel, _, remaining = _current(pipe, channel_id)
        _require(profile['profile_revision'] == expected_revision and channel['connection_id'] == expected_connection
                 and remaining <= 2 and pipe.get(execution_key) is None)
        pending_status, _ = _pending_status(pipe, channel_id, day)
        if pending_status != 'available':
            return {'status': pending_status}
        credential, epoch = pipe.get(credential_key), pipe.get(epoch_key)
        _require(isinstance(credential, str) and credential and pipe.sismember(index_key, channel_id)
                 and (epoch is None or isinstance(epoch, str) and re.fullmatch(r'[0-9]+', epoch)))
        spending_context = None
        if getattr(settings, 'studio_spend_enforcement', False) is True:
            from app.services.production_spend_runtime import preflight_scheduled_production, SpendBlocked
            from app.services.production_series_spend import prepare_dispatch_context

            try:
                preflight_scheduled_production(channel_id, kind='shorts')
                spending_context = prepare_dispatch_context(pipe, binding, profile, channel)
            except SpendBlocked as error:
                return {'status': 'budget_blocked', 'reason_code': str(error)}
        record = {**binding, 'status': 'reserved', 'connection_id': expected_connection,
                  'profile_sha256': _digest(profile), 'channel_sha256': _digest(_planning_channel_identity(channel)),
                  'credential_sha256': _digest(credential), 'authorization_epoch_sha256': _digest(epoch),
                  'created_at': now}
        raw = _json(record)
        pipe.multi()
        pipe.set(dispatch_key, raw, nx=True)
        if spending_context is not None:
            pipe.set(*spending_context, nx=True)
        reply = pipe.execute()
        _require(len(reply) == (2 if spending_context is not None else 1)
                 and all(value is True for value in reply))
    # Celery broker retries are disabled; a lost acceptance response is not
    # permission to send the same preparation task again on a subsequent tick.
    try:
        enqueue(args=[binding], task_id=binding['task_id'], retry=False)
    except Exception:
        _mark_dispatch(client, dispatch_key, raw, 'uncertain', delivery_only=True)
        return {'status': 'preparation_dispatch_uncertain'}
    _mark_dispatch(client, dispatch_key, raw, 'dispatched', delivery_only=True)
    return {'status': 'preparation_queued'}


def maintain_production_series(profiles, connections, enqueue_preparation, *, now=None):
    """Best-effort maintenance AFTER normal render dispatch, capped at 10.

    Promotion only changes an exhausted queue; the next ordinary minute tick
    performs rendering with the unchanged global two-slot and fairness gates.
    A per-channel error never suppresses normal dispatch or another channel.
    """
    try:
        now = _now(now)
        linked = {c['id']: c for c in connections if isinstance(c, dict) and isinstance(c.get('id'), str)}
        _require(len(linked) <= MAX_LINKED_CHANNELS)
        selected = {p['channel_id']: p for p in profiles if isinstance(p, dict)
                    and isinstance(p.get('channel_id'), str) and p['channel_id'] in linked}
        ordered = _dispatch_profile_order(list(selected.values()))
    except Exception:
        return {'status': 'unavailable', 'channels': {}}
    results = {}
    for hint in ordered:
        channel_id = hint['channel_id']
        try:
            client = _client()
            profile, channel, _, remaining = _current(client, channel_id)
            _require(profile['profile_revision'] == hint.get('profile_revision')
                     and channel['connection_id'] == linked[channel_id].get('connection_id'))
            if remaining > 2:
                results[channel_id] = 'not_due'
                continue
            status, pending = _pending_status(client, channel_id, datetime.fromtimestamp(now, timezone.utc).date().isoformat())
            if status == 'ready':
                retired = retire_stale_ready_batch(channel_id, profile['profile_revision'], pending['attempt_id'], now=now)
                if retired['status'] == 'stale_ready_archived':
                    results[channel_id] = retired['status']
                    continue  # A later ordinary tick can reserve a new day's preparation.
                if remaining == 0:
                    outcome = promote_ready_series(channel_id, profile['profile_revision'], pending['attempt_id'], now=now)
                    results[channel_id] = outcome['status']
                else:
                    results[channel_id] = 'ready_waiting_for_series_end'
            elif status == 'uncertain':
                results[channel_id] = retire_finished_uncertain_batch(client, channel_id,
                    profile['profile_revision'], now=now)
            elif status != 'available':
                results[channel_id] = status
            else:
                results[channel_id] = _reserve_preparation(channel_id, profile['profile_revision'],
                                                           channel['connection_id'], enqueue_preparation, now)['status']
        except Exception:
            results[channel_id] = 'ineligible_or_changed'
    return {'status': 'checked', 'channels': results}


def run_series_preparation(binding, actual_task_id, *, now=None):
    """A bound worker acquires its one execution before the planner can spend."""
    client = _client()
    raw = None
    dispatch_key = None
    try:
        now = _now(now)
        keys = _execution_keys(binding)
        dispatch_key, execution_key = keys[:2]
        _require(actual_task_id == binding['task_id']
                 and binding['day'] == datetime.fromtimestamp(now, timezone.utc).date().isoformat())
        channel_id = binding['channel_id']
        with client.pipeline() as pipe:
            pipe.watch(*keys, PROFILE_PREFIX + channel_id, OAUTH_CHANNEL_PREFIX + channel_id,
                       CHANNEL_STATE_PREFIX + channel_id, PENDING_PREFIX + channel_id,
                       DAILY_PREFIX + channel_id + ':' + binding['day'])
            if pipe.get(execution_key) is not None:
                return {'status': 'execution_already_claimed', **_FLAGS}
            profile, channel, state, remaining = _current(pipe, channel_id)
            _require(remaining <= 2)
            _execution_guard(pipe, binding, profile, channel, state, require_execution=False)
            raw = pipe.get(dispatch_key)
            pipe.multi()
            pipe.set(execution_key, binding['token'], nx=True)
            _require(pipe.execute()[0] is True)
        result = prepare_next_series(profile, channel, now=now, execution_binding=binding)
        _mark_dispatch(client, dispatch_key, raw, 'finished', outcome=result.get('status', 'unavailable'))
        return result
    except Exception:
        if raw is not None and dispatch_key is not None:
            _mark_dispatch(client, dispatch_key, raw, 'uncertain', outcome='execution_or_result_uncertain')
        return {'status': 'unavailable', **_FLAGS}
