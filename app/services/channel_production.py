"""Durable, bounded scheduling for curated per-channel production topics."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import re
import time
from uuid import NAMESPACE_URL, uuid5

import redis

from app.config import settings
from app.services.studio_state import JOB_INDEX, JOB_PREFIX, JOB_TTL_SECONDS


PRODUCTION_PREFIX = 'youtube_studio:production:v1:'
ACTIVE_KEY = PRODUCTION_PREFIX + 'active'
DISPATCH_CURSOR_KEY = PRODUCTION_PREFIX + 'last_dispatched_channel'
MAX_ACTIVE_PRODUCTIONS = 2
CHANNEL_STATE_PREFIX = PRODUCTION_PREFIX + 'channel:'
PROFILE_PREFIX = 'youtube_studio:youtube_profile:v1:'
OAUTH_CHANNEL_PREFIX = 'youtube_studio:oauth:channel:v3:'
OAUTH_CREDENTIAL_PREFIX = 'youtube_studio:oauth:credential:v3:'
OAUTH_CHANNEL_INDEX = 'youtube_studio:oauth:channels:v3'
PUBLICATION_UPLOAD_PREFIX = 'youtube_studio:youtube_upload:v2:'
_ID = re.compile(r'^[A-Za-z0-9_-]{8,128}$')
_TASK_ID = re.compile(r'^[0-9a-f-]{36}$')
_LANGUAGES = {'tr', 'en', 'de', 'es', 'ar'}


class ChannelProductionError(RuntimeError):
    pass


def _redis():
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


def _channel_id(value: object) -> str:
    value = str(value or '').strip()
    if not _ID.fullmatch(value):
        raise ChannelProductionError('production_channel_invalid')
    return value


def _prefix_digest(topics: list[str]) -> str:
    return hashlib.sha256(json.dumps(topics, ensure_ascii=False).encode()).hexdigest()


# No TTL on the active claim: an expired lease cannot prove that an accepted
# render stopped. Lost enqueue replies stay reserved and are never resent.
# Check all mutable eligibility and cursor state again in the atomic write.
_ACTIVE_CLAIMS_LUA = r'''
local function active_claims(raw)
  if not raw then return {} end
  if #raw > 4096 then return nil end
  local ok, active = pcall(cjson.decode, raw)
  if not ok or type(active) ~= 'table' then return nil end
  local claims
  if active['version'] == 2 then
    for key, _ in pairs(active) do
      if key ~= 'version' and key ~= 'claims' then return nil end
    end
    claims = active['claims']
    if type(claims) ~= 'table' or #claims < 1 or #claims > 2 then return nil end
    for key, _ in pairs(claims) do
      if type(key) ~= 'number' or key % 1 ~= 0 or key < 1 or key > #claims then return nil end
    end
  else
    claims = {active}
  end
  local channels, tasks = {}, {}
  for _, claim in ipairs(claims) do
    if type(claim) ~= 'table' then return nil end
    for key, _ in pairs(claim) do
      if key ~= 'channel_id' and key ~= 'task_id' then return nil end
    end
    local channel, task = claim['channel_id'], claim['task_id']
    if type(channel) ~= 'string' or #channel < 8 or #channel > 128
       or not string.match(channel, '^[A-Za-z0-9_%-]+$')
       or type(task) ~= 'string' or #task ~= 36
       or not string.match(task, '^[0-9a-f%-]+$')
       or channels[channel] or tasks[task] then return nil end
    channels[channel], tasks[task] = true, true
  end
  return claims
end
local function encode_active(claims)
  -- Single occupancy remains readable by the old worker during a rollout.
  if #claims == 1 then return cjson.encode(claims[1]) end
  return cjson.encode({version=2, claims=claims})
end
'''


from app.services.channel_cadence import PRODUCTION_LUA

_RESERVE = _ACTIVE_CLAIMS_LUA + r'''
local profile_raw = redis.call('GET', KEYS[1])
if profile_raw ~= ARGV[1] then return 'profile_changed' end
local ok, profile = pcall(cjson.decode, profile_raw or '')
if not ok or type(profile) ~= 'table' then return 'invalid_state' end
if profile['production_enabled'] ~= true or profile['auto_publish'] ~= true then
  return 'disabled'
end
local channel_raw = redis.call('GET', KEYS[5])
local valid, channel = pcall(cjson.decode, channel_raw or '')
if not valid or type(channel) ~= 'table'
   or channel['id'] ~= ARGV[7] or channel['connection_id'] ~= ARGV[8]
   or redis.call('EXISTS', KEYS[6]) ~= 1
   or redis.call('SISMEMBER', KEYS[7], ARGV[7]) ~= 1 then
  return 'connection_missing'
end
local cursor_raw = redis.call('HGET', KEYS[2], 'cursor') or '0'
local cursor = tonumber(cursor_raw)
local due = tonumber(redis.call('HGET', KEYS[2], 'next_due') or '0')
if not cursor or cursor < 0 or cursor % 1 ~= 0 or not due or due < 0 then
  return 'invalid_state'
end
if cursor > 0 and (not redis.call('HGET', KEYS[2], 'consumed_prefix')
   or not redis.call('HGET', KEYS[2], 'next_due')
   or not redis.call('HGET', KEYS[2], 'last_task_id')) then
  return 'invalid_state'
end
if (redis.call('HGET', KEYS[2], 'paused_reason') or '') ~= '' then return 'paused' end
if cursor ~= tonumber(ARGV[2]) then return 'cursor_changed' end
if (redis.call('HGET', KEYS[2], 'consumed_prefix') or ARGV[3]) ~= ARGV[3] then
  redis.call('HSET', KEYS[2], 'paused_reason', 'consumed_topics_changed')
  return 'paused'
end
if due > tonumber(ARGV[5]) then return 'not_due' end
local claims = active_claims(redis.call('GET', KEYS[3]))
if not claims then return 'invalid_state' end
local owner_raw = redis.call('GET', KEYS[10])
local owner_count = 0
if owner_raw then
  local valid_owner, owner = pcall(cjson.decode, owner_raw)
  if not valid_owner or type(owner) ~= 'table' then return 'invalid_state' end
  for channel_id, item in pairs(owner) do
    if type(channel_id) ~= 'string' or type(item) ~= 'string' then return 'invalid_state' end
    if channel_id == ARGV[7] then return 'active' end
    owner_count = owner_count + 1
  end
end
-- Keep the two legacy claims bound. One held owner-plan channel must not
-- occupy both legacy channels' queue capacity. Render concurrency stays two.
if #claims >= 2 or #claims + owner_count >= 3 then return 'active' end
for _, claim in ipairs(claims) do
  if claim['channel_id'] == ARGV[7] or claim['task_id'] == ARGV[9] then return 'active' end
end
if (redis.call('HGET', KEYS[2], 'active_task_id') or '') ~= '' then return 'active' end
if redis.call('EXISTS', KEYS[4]) == 1 then return 'invalid_state' end
''' + PRODUCTION_LUA + r'''
redis.call('HSET', KEYS[2],
  'cursor', cursor + 1, 'consumed_prefix', ARGV[4],
  'next_due', ARGV[6], 'active_task_id', ARGV[9],
  'last_task_id', ARGV[9], 'dispatch_status', 'reserved',
  'profile_revision', ARGV[12], 'connection_id', ARGV[8])
table.insert(claims, {channel_id=ARGV[7], task_id=ARGV[9]})
redis.call('SET', KEYS[3], encode_active(claims))
redis.call('SETEX', KEYS[4], tonumber(ARGV[13]), ARGV[11])
redis.call('ZADD', KEYS[8], tonumber(ARGV[5]), ARGV[9])
redis.call('EXPIRE', KEYS[8], tonumber(ARGV[13]))
-- Scheduling priority only: advance with the existing atomic reservation,
-- including when its enqueue reply is lost. Never clear or replace claims.
redis.call('SET', KEYS[9], ARGV[7])
return 'reserved'
'''


_RECONCILE = _ACTIVE_CLAIMS_LUA + r'''
local recheck_public = ARGV[7] == 'public_hold_recheck'
local current_active = redis.call('GET', KEYS[1])
if (current_active or '') ~= ARGV[1] then return 'active_changed' end
local claims = active_claims(current_active)
if not claims then return 'state_unavailable' end
local matched
for index, claim in ipairs(claims) do
  if recheck_public and (claim['channel_id'] == ARGV[4] or claim['task_id'] == ARGV[2]) then return 'active' end
  if claim['task_id'] == ARGV[2] and claim['channel_id'] == ARGV[4] then matched = index end
end
if recheck_public then
  if redis.call('HGET', KEYS[3], 'paused_reason') ~= 'previous_publication_blocked'
     or redis.call('HGET', KEYS[3], 'last_task_id') ~= ARGV[2]
     or redis.call('HGET', KEYS[3], 'last_result') ~= 'SUCCESS'
     or redis.call('HGET', KEYS[3], 'dispatch_status') ~= 'finished'
     or (redis.call('HGET', KEYS[3], 'active_task_id') or '') ~= '' then return 'state_unavailable' end
elseif not matched then return 'active_changed' end
local raw_job = redis.call('GET', KEYS[2])
local ok, job = pcall(cjson.decode, raw_job or '')
if not ok or type(job) ~= 'table' then return 'state_unavailable' end
if job['task_id'] ~= ARGV[2] or job['kind'] ~= 'render' then
  return 'state_unavailable'
end
if not recheck_public and redis.call('HGET', KEYS[3], 'active_task_id') ~= ARGV[2] then
  return 'state_unavailable'
end
local state = job['state']
if state ~= 'SUCCESS' and state ~= 'FAILURE' then return 'active' end
local reason = ''
local continue_public = false
if state == 'FAILURE' then
  reason = 'previous_render_failed'
else
  local result = job['result']
  if job['kind'] ~= 'render' or type(result) ~= 'table'
     or type(result['video_key']) ~= 'string' or result['video_key'] == ''
     or result['quality_disposition'] ~= 'automated_qc_pass'
     or result['manual_qa_required'] ~= false then
    reason = 'previous_render_needs_review'
  else
    local automation = result['youtube_automation']
    -- mark_success precedes automatic upload dispatch. Wait through that
    -- normal window instead of treating a completed render as delivered.
    if automation == nil or automation == cjson.null then return 'active' end
    if type(automation) ~= 'table' then return 'state_unavailable' end
    local publication_status = automation['status']
    if publication_status == nil or publication_status == '' then return 'active' end
    if publication_status ~= 'queued' and publication_status ~= 'reserved'
       and publication_status ~= 'uploading' and publication_status ~= 'complete' then
      reason = 'previous_publication_blocked'
    else
      local child_id = automation['publish_task_id']
      if type(child_id) ~= 'string' or #child_id ~= 36
         or not string.match(child_id, '^[0-9a-f%-]+$') then
        return 'state_unavailable'
      end
      local child_raw = redis.call('GET', ARGV[3] .. child_id)
      local child_ok, child = pcall(cjson.decode, child_raw or '')
      if not child_ok or type(child) ~= 'table'
         or child['kind'] ~= 'publish' or child['parent_id'] ~= ARGV[2]
         or type(child['spec']) ~= 'table'
         or child['spec']['source_task_id'] ~= ARGV[2] then
        return 'state_unavailable'
      end
      if child['state'] == 'FAILURE' then
        reason = 'previous_publication_blocked'
      elseif child['state'] ~= 'SUCCESS' then
        return 'active'
      else
        local delivered = child['result']
        if type(delivered) ~= 'table' or delivered['source_task_id'] ~= ARGV[2]
           or type(delivered['youtube_video_id']) ~= 'string'
           or delivered['youtube_video_id'] == '' then
          return 'state_unavailable'
        end
        local release = delivered['release_status']
        if release == 'blocked' or release == 'uncertain' then
          reason = 'previous_publication_blocked'
        elseif release ~= 'private' and release ~= 'public' and release ~= 'scheduled' then
          return 'state_unavailable'
        elseif release == 'public' then
          -- A publisher SUCCESS alone is not proof of public delivery. Inspect
          -- all bound records atomically before removing only this cooldown.
          local function read_object(key)
            local valid, value = pcall(cjson.decode, redis.call('GET', key) or '')
            if valid and type(value) == 'table' then return value end
            return {}
          end
          local function empty(value) return value == nil or value == cjson.null or value == '' end
          local spec = type(job['spec']) == 'table' and job['spec'] or {}
          local attribution = type(result['youtube']) == 'table' and result['youtube'] or {}
          local upload = read_object(ARGV[6] .. ARGV[2])
          local profile = read_object(KEYS[4])
          local channel = read_object(KEYS[5])
          local plan = type(upload['publish_plan']) == 'table' and upload['publish_plan'] or {}
          local language = type(spec['language']) == 'string' and spec['language'] or ''
          local connection = spec['production_connection_id']
          local revision = spec['production_profile_revision']
          local video = delivered['youtube_video_id']
          local function identity(record)
            return type(record) == 'table' and record['target_channel_id'] == ARGV[4]
              and record['connection_id'] == connection and record['profile_revision'] == revision
          end
          local function public_assets(record)
            return identity(record) and record['privacy_status'] == 'public' and record['release_status'] == 'public'
              and empty(record['release_error_code']) and empty(record['scheduled_publish_at'])
              and record['caption_uploaded'] == true and empty(record['caption_error_code'])
              and empty(record['thumbnail_error_code'])
              and (plan['require_thumbnail'] ~= true and profile['require_thumbnail'] ~= true
                   or record['thumbnail_uploaded'] == true)
              and type(record['contains_synthetic_media']) == 'boolean'
              and record['contains_synthetic_media'] == attribution['contains_synthetic_media']
              and (plan['contains_synthetic_media'] ~= true or record['contains_synthetic_media'] == true)
          end
          local function public_delivery(record)
            if record['idempotent_replay'] ~= true then return public_assets(record) end
            -- Real completed-upload redelivery intentionally omits asset fields.
            -- Its exact IDs still require the full source/ledger proofs below;
            -- a present contradictory field is never treated as an omission.
            local function omitted_or(key, value) return record[key] == nil or record[key] == value end
            return record['stage'] == 'complete' and record['progress'] == 100
              and record['target_channel_id'] == ARGV[4] and record['connection_id'] == connection
              and record['privacy_status'] == 'public' and record['release_status'] == 'public'
              and empty(record['release_error_code']) and empty(record['scheduled_publish_at'])
              and empty(record['caption_error_code']) and empty(record['thumbnail_error_code'])
              and omitted_or('profile_revision', revision) and omitted_or('caption_uploaded', true)
              and omitted_or('thumbnail_uploaded', attribution['thumbnail_uploaded'])
              and omitted_or('contains_synthetic_media', attribution['contains_synthetic_media'])
          end
          continue_public = type(spec) == 'table' and type(attribution) == 'table' and type(plan) == 'table'
            and type(connection) == 'string' and connection ~= '' and type(revision) == 'string' and revision ~= ''
            and language ~= ''
            and spec['mode'] == 'production' and spec['production_scheduled'] == true
            and spec['publish_after_render'] == true and spec['production_channel_id'] == ARGV[4]
            and profile['channel_id'] == ARGV[4] and profile['profile_revision'] == revision
            and profile['production_enabled'] == true and profile['auto_publish'] == true and profile['release_mode'] == 'public'
            and channel['id'] == ARGV[4] and channel['connection_id'] == connection and channel['requires_reconnect'] ~= true
            and redis.call('EXISTS', KEYS[6]) == 1 and redis.call('SISMEMBER', KEYS[7], ARGV[4]) == 1
            and redis.call('HGET', KEYS[3], 'profile_revision') == revision
            and redis.call('HGET', KEYS[3], 'connection_id') == connection
            and result['task_id'] == ARGV[2] and result['status'] == 'complete'
            and result['video_key'] == 'videos/' .. ARGV[2] .. '/final.mp4'
            and result['caption_key'] == 'videos/' .. ARGV[2] .. '/captions.' .. language .. '.srt'
            and #video == 11 and string.match(video, '^[A-Za-z0-9_%-]+$') ~= nil
            and child['task_id'] == child_id and delivered['task_id'] == child_id and delivered['status'] == 'complete'
            and identity(child['spec']) and child['spec']['release_mode'] == 'public' and child['spec']['privacy_status'] == 'private'
            and public_delivery(delivered) and public_assets(attribution) and attribution['video_id'] == video
            and automation['target_channel_id'] == ARGV[4] and automation['profile_revision'] == revision
            and automation['release_mode'] == 'public'
            and upload['version'] == 2 and upload['status'] == 'complete'
            and upload['source_task_id'] == ARGV[2] and upload['publish_task_id'] == child_id
            and upload['youtube_video_id'] == video and upload['target_channel_id'] == ARGV[4]
            and upload['connection_id'] == connection and upload['requested_release_mode'] == 'public'
            and upload['privacy_status'] == 'public' and upload['release_status'] == 'public'
            and upload['side_effect_possible'] == true and upload['release_side_effect_possible'] == true
            and type(upload['release_completed_at']) == 'string' and upload['release_completed_at'] ~= ''
            and empty(upload['requested_publish_at']) and empty(upload['release_error_code'])
            and plan['source_task_id'] == ARGV[2] and plan['target_channel_id'] == ARGV[4]
            and plan['profile_revision'] == revision and plan['release_mode'] == 'public' and empty(plan['publish_at'])
            and type(plan['contains_synthetic_media']) == 'boolean'
          if not continue_public then reason = 'previous_publication_blocked' end
        end
      end
    end
  end
end
if recheck_public then
  -- Reuse the complete public/ownership/assets proof above. Rechecking a
  -- finished hold neither recreates a claim nor edits any publication record.
  if reason ~= '' or not continue_public then return 'publication_still_unverified' end
  redis.call('HDEL', KEYS[3], 'paused_reason')
  redis.call('HSET', KEYS[3], 'next_due', ARGV[5], 'last_public_task_id', ARGV[2],
             'last_public_continued_at', ARGV[5])
  return 'public_hold_cleared'
end
if reason ~= '' then redis.call('HSET', KEYS[3], 'paused_reason', reason) end
if continue_public and reason == '' then
  redis.call('HSET', KEYS[3], 'next_due', ARGV[5], 'last_public_task_id', ARGV[2],
             'last_public_continued_at', ARGV[5])
end
redis.call('HSET', KEYS[3], 'last_result', state, 'dispatch_status', 'finished')
redis.call('HDEL', KEYS[3], 'active_task_id')
table.remove(claims, matched)
if #claims == 0 then
  redis.call('DEL', KEYS[1])
else
  redis.call('SET', KEYS[1], encode_active(claims))
end
if reason ~= '' then return 'channel_paused' end
return 'completed'
'''


_MARK_DISPATCH = r'''
if redis.call('HGET', KEYS[1], 'last_task_id') ~= ARGV[1] then return 0 end
if redis.call('HGET', KEYS[1], 'dispatch_status') ~= 'reserved' then return 0 end
redis.call('HSET', KEYS[1], 'dispatch_status', ARGV[2])
return 1
'''


def get_production_state(channel_id: str) -> dict:
    try:
        return _redis().hgetall(CHANNEL_STATE_PREFIX + _channel_id(channel_id))
    except Exception as exc:
        raise ChannelProductionError('production_state_unavailable') from exc


def reconcile_active_production(*, now: float | None = None) -> str:
    """Reconcile at most two claims independently; never expire or steal one."""
    try:
        now = time.time() if now is None else now
        if type(now) not in (int, float) or not math.isfinite(now) or now < 0:
            raise ValueError('invalid production reconciliation time')
        client = _redis()
        raw = client.get(ACTIVE_KEY)
        if raw is None:
            return 'idle'
        initial = _decode_active_claims(raw)
        finished = []
        for active in initial:
            # Another tick may have completed a sibling. Re-read the shared
            # fence and let Lua compare it again before removing only this job.
            current_raw = client.get(ACTIVE_KEY)
            if current_raw is None:
                break
            current = _decode_active_claims(current_raw)
            if active not in current:
                continue
            channel_id, task_id = active['channel_id'], active['task_id']
            status = client.eval(
                _RECONCILE, 7, ACTIVE_KEY, JOB_PREFIX + task_id,
                CHANNEL_STATE_PREFIX + channel_id, PROFILE_PREFIX + channel_id,
                OAUTH_CHANNEL_PREFIX + channel_id, OAUTH_CREDENTIAL_PREFIX + channel_id, OAUTH_CHANNEL_INDEX,
                current_raw, task_id, JOB_PREFIX, channel_id, now, PUBLICATION_UPLOAD_PREFIX,
            )
            if status in {'active_changed', 'state_unavailable'}:
                return status
            if status not in {'completed', 'channel_paused', 'active'}:
                raise ValueError('invalid reconciliation state')
            finished.append(status)
        remaining = client.get(ACTIVE_KEY)
        if remaining is not None:
            _decode_active_claims(remaining)
            return 'active'
        return 'channel_paused' if 'channel_paused' in finished else 'completed'
    except Exception as exc:
        raise ChannelProductionError('production_state_unavailable') from exc


def reconcile_publication_holds(profiles: list[dict], *, now: float | None = None) -> dict:
    """Recheck only finished public holds; the full atomic proof stays binding."""
    now = time.time() if now is None else now
    if type(now) not in (int, float) or not math.isfinite(now) or now < 0:
        raise ChannelProductionError('production_state_unavailable')
    client, results = _redis(), {}
    for profile in profiles:
        channel_id = _channel_id(profile.get('channel_id'))
        state = client.hgetall(CHANNEL_STATE_PREFIX + channel_id)
        if (state.get('paused_reason') != 'previous_publication_blocked'
                or state.get('last_result') != 'SUCCESS' or state.get('active_task_id')):
            continue
        task_id = state.get('last_task_id')
        if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
            continue
        raw = client.get(ACTIVE_KEY)
        if raw is not None:
            _decode_active_claims(raw)
        results[channel_id] = client.eval(
            _RECONCILE, 7, ACTIVE_KEY, JOB_PREFIX + task_id,
            CHANNEL_STATE_PREFIX + channel_id, PROFILE_PREFIX + channel_id,
            OAUTH_CHANNEL_PREFIX + channel_id, OAUTH_CREDENTIAL_PREFIX + channel_id, OAUTH_CHANNEL_INDEX,
            raw or '', task_id, JOB_PREFIX, channel_id, now, PUBLICATION_UPLOAD_PREFIX, 'public_hold_recheck',
        )
    return results


def _decode_active_claims(raw: str) -> list[dict]:
    if not isinstance(raw, str) or len(raw) > 4096:
        raise ValueError('invalid production active claims')
    active = json.loads(raw)
    if isinstance(active, dict) and type(active.get('version')) is int and active['version'] == 2:
        if set(active) != {'version', 'claims'}:
            raise ValueError('invalid production active claims')
        claims = active['claims']
    else:
        claims = [active]
    if not isinstance(claims, list) or not 1 <= len(claims) <= MAX_ACTIVE_PRODUCTIONS:
        raise ValueError('invalid production active claims')
    channels, tasks = set(), set()
    for claim in claims:
        if not isinstance(claim, dict) or set(claim) != {'channel_id', 'task_id'}:
            raise ValueError('invalid production active claims')
        channel_id = _channel_id(claim['channel_id'])
        task_id = claim['task_id']
        if (
            not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id)
            or channel_id in channels or task_id in tasks
        ):
            raise ValueError('invalid production active claims')
        channels.add(channel_id)
        tasks.add(task_id)
    return claims


def reserve_due_production(profile: dict, connection: dict, *, now: float | None = None) -> dict:
    """Atomically reserve one topic and job before the caller may enqueue it."""
    if profile.get('production_enabled') is not True or profile.get('auto_publish') is not True:
        return {'status': 'disabled'}
    channel_id = _channel_id(profile.get('channel_id'))
    connection_id = str(connection.get('connection_id') or '')
    if connection.get('id') != channel_id or not _ID.fullmatch(connection_id):
        return {'status': 'connection_missing'}
    topics = profile.get('production_topics')
    interval = profile.get('production_interval_hours', 24)
    language = str(profile.get('default_language') or 'tr')
    if (
        not isinstance(topics, list) or len(topics) > 60
        or any(not isinstance(topic, str) or not topic.strip() or len(topic) > 240 for topic in topics)
        or type(interval) is not int or not 6 <= interval <= 168
        or language not in _LANGUAGES
    ):
        raise ChannelProductionError('production_profile_invalid')
    topics = [topic.strip() for topic in topics]
    now = time.time() if now is None else now
    if not isinstance(now, (int, float)) or not math.isfinite(now) or now < 0:
        raise ChannelProductionError('production_time_invalid')
    try:
        client = _redis()
        from app.services.content_plan import owns_channel
        from app.services import daily_voice_priority
        short_priority = owns_channel(channel_id, client=client) and daily_voice_priority.eligible(channel_id, client=client)
        if owns_channel(channel_id, client=client) and not short_priority:
            return {'status': 'owner_content_plan'}
        profile_raw = client.get(PROFILE_PREFIX + channel_id)
        persisted_profile = json.loads(profile_raw or '{}')
        if persisted_profile != profile:
            return {'status': 'profile_changed'}
        state = client.hgetall(CHANNEL_STATE_PREFIX + channel_id)
        cursor = int(state.get('cursor', '0'))
        if str(cursor) != state.get('cursor', '0') or cursor < 0:
            raise ValueError('invalid production cursor')
        if state.get('paused_reason'):
            return {'status': 'paused', 'reason': state['paused_reason']}
        if cursor >= len(topics):
            return {'status': 'topics_exhausted'}
        topic = topics[cursor]
        identity = str(profile.get('channel_identity') or '').strip()[:240]
        brief = topic + (f'\n\nChannel editorial direction: {identity}' if identity else '')
        from app.services.production_editorial import choose_production_editorial

        delivery_enabled = getattr(settings, 'studio_longform_delivery_enabled', False) is True
        editorial = choose_production_editorial(
            topic, identity, **({'long_duration_minutes': 8} if delivery_enabled else {}),
        )
        from app.services.channel_cadence import daily_editorial
        editorial = daily_editorial(channel_id, editorial, client=client, now=now,
            **({'defer_daily_long': True} if short_priority else {}))
        duration_minutes = editorial['duration_minutes']
        if duration_minutes == 3 and editorial.get('reason_code') in {'owner_daily_mix', 'owner_next_day_stock'}:
            from app.services.channel_cadence import install_daily_long
            return install_daily_long(profile, topics[cursor:], client=client, now=now,
                **({'advance': True} if editorial['reason_code'] == 'owner_next_day_stock' else {}))
        if duration_minutes == 8 and getattr(settings, 'studio_spend_enforcement', False) is not True:
            return {'status': 'delivery_budget_not_enabled'}
        from app.services.admission_hold import hold_reason
        held = hold_reason(now=datetime.fromtimestamp(now, timezone.utc))
        if held:
            return {'status': 'admission_held', 'reason_code': held}
        if getattr(settings, 'studio_spend_enforcement', False) is True:
            from app.services.production_spend_runtime import preflight_scheduled_production, SpendBlocked

            try:
                preflight_scheduled_production(channel_id, kind='long' if duration_minutes > 1 else 'shorts')
            except SpendBlocked as error:
                return {'status': 'budget_blocked', 'reason_code': str(error)}
        route = str(profile.get('route_label') or channel_id).strip()
        task_id = str(uuid5(NAMESPACE_URL, f'youtube-production:{channel_id}:{cursor}:{_prefix_digest([topic])}'))
        options = {
            'mode': 'production', 'format': editorial['format'], 'workflow': 'auto',
            'content_style': 'documentary', 'pace': 'balanced',
            'visual_mix': 'real_first', 'music': 'off', 'subtitles': 'sidecar',
            'quality_threshold': 86, 'publish_after_render': True,
            'production_scheduled': True,
            'production_channel_id': channel_id,
            'production_connection_id': connection_id,
            'production_profile_revision': str(profile.get('profile_revision') or ''),
            'production_topic_index': cursor,
            'production_editorial': editorial,
        }
        if duration_minutes == 8:
            from app.services.production_delivery import CONTRACT

            options['production_delivery'] = dict(CONTRACT)
        spec = {'topic': brief, 'duration_minutes': duration_minutes, 'language': language, 'channel_id': route, **options}
        iso_now = datetime.fromtimestamp(now, timezone.utc).isoformat()
        record = {
            'task_id': task_id, 'kind': 'render', 'parent_id': None,
            'spec': spec, 'state': 'PENDING', 'stage': 'queued', 'progress': 0,
            'message': 'Kanal takviminden üretim kuyruğa alındı.',
            'created_ts': now, 'created_at': iso_now, 'updated_at': iso_now,
            'result': None, 'error': None,
        }
        active = json.dumps({'channel_id': channel_id, 'task_id': task_id}, sort_keys=True)
        from app.services.channel_cadence import lua_arguments
        cadence_keys = lua_arguments(channel_id, 'long' if duration_minutes > 1 else 'shorts', now=now)
        reservation = (
            _RESERVE, 13, PROFILE_PREFIX + channel_id,
            CHANNEL_STATE_PREFIX + channel_id, ACTIVE_KEY, JOB_PREFIX + task_id,
            OAUTH_CHANNEL_PREFIX + channel_id, OAUTH_CREDENTIAL_PREFIX + channel_id,
            OAUTH_CHANNEL_INDEX, JOB_INDEX, DISPATCH_CURSOR_KEY,
            'youtube_studio:content_plan:v1:active', *cadence_keys[:3],
            profile_raw, cursor, _prefix_digest(topics[:cursor]),
            _prefix_digest(topics[:cursor + 1]), now, now + interval * 3600,
            channel_id, connection_id, task_id, active,
            json.dumps(record, ensure_ascii=False),
            str(profile.get('profile_revision') or ''), JOB_TTL_SECONDS, *cadence_keys[3:],
        )
        if short_priority:
            # Recheck the exact pending item and full credit history inside
            # the transaction that creates the Short. An edit, voice send or
            # long reservation racing this decision invalidates the commit.
            with client.pipeline() as pipe:
                if not daily_voice_priority.eligible(channel_id, client=client, pipe=pipe):
                    return {'status': 'owner_content_plan'}
                pipe.multi(); pipe.eval(*reservation)
                reply = pipe.execute()
                if type(reply) is not list or len(reply) != 1:
                    raise ValueError('invalid production reservation acknowledgement')
                status = reply[0]
        else:
            status = client.eval(*reservation)
        if status == 'invalid_state':
            raise ValueError('invalid production reservation state')
        if status != 'reserved':
            return {'status': str(status)}
        return {'status': 'reserved', 'task_id': task_id, 'channel_id': channel_id,
                'args': (brief, duration_minutes, language, route, options, None)}
    except Exception as exc:
        # Includes lost replies after Redis accepted a reservation. The caller
        # must never enqueue on this outcome or try the same topic again.
        raise ChannelProductionError('production_reservation_unavailable') from exc


def mark_production_dispatched(channel_id: str, task_id: str, *, uncertain: bool = False) -> None:
    try:
        _redis().eval(
            _MARK_DISPATCH, 1, CHANNEL_STATE_PREFIX + _channel_id(channel_id),
            task_id, 'uncertain' if uncertain else 'enqueued',
        )
    except Exception as exc:
        raise ChannelProductionError('production_dispatch_state_unavailable') from exc


def _dispatch_profile_order(profiles: list[dict]) -> list[dict]:
    """Round-robin priority, not authorization, after the last reserved channel.

    A missing/obsolete cursor bootstraps safely; malformed ordering metadata
    cannot remove a claim or bypass any reservation check. Channel IDs, not
    timestamps, keep the order stable across clock changes and worker restarts.
    Concurrent ticks may see the same priority list; the existing reservation
    Lua still enforces the two slots and one active job per channel.
    """
    ordered = sorted(profiles, key=lambda item: str(item.get('channel_id') or ''))
    try:
        client = _redis()
        kind = client.type(DISPATCH_CURSOR_KEY)
        cursor = client.get(DISPATCH_CURSOR_KEY) if kind == 'string' else None
    except Exception as exc:
        raise ChannelProductionError('production_state_unavailable') from exc
    if not isinstance(cursor, str) or not _ID.fullmatch(cursor):
        return ordered
    return ([profile for profile in ordered if str(profile.get('channel_id') or '') > cursor]
            + [profile for profile in ordered if str(profile.get('channel_id') or '') <= cursor])


def dispatch_due_productions(profiles: list[dict], connections: list[dict], enqueue, *, now: float | None = None) -> dict:
    """One beat tick, including reconciliation; never calls a paid provider."""
    reconciliation = reconcile_active_production(now=now)
    if reconciliation in {'active_changed', 'state_unavailable'}:
        return {'status': reconciliation}
    connected = {str(item.get('id') or ''): item for item in connections if isinstance(item, dict)}
    statuses = {}
    queued = []
    for profile in _dispatch_profile_order(profiles):
        channel_id = str(profile.get('channel_id') or '')
        if profile.get('production_enabled') is not True or profile.get('auto_publish') is not True:
            continue
        if channel_id not in connected:
            statuses[channel_id] = 'connection_missing'
            continue
        try:
            reservation = reserve_due_production(profile, connected[channel_id], now=now)
        except ChannelProductionError:
            # A broken channel configuration must not starve other channels.
            # An ambiguous reservation still owns the global Redis claim, so
            # later candidates cannot turn this into another paid dispatch.
            statuses[channel_id] = 'configuration_blocked'
            continue
        statuses[channel_id] = reservation['status']
        if reservation['status'] != 'reserved':
            continue
        try:
            enqueue(args=reservation['args'], task_id=reservation['task_id'])
        except Exception:
            mark_production_dispatched(channel_id, reservation['task_id'], uncertain=True)
            return {'status': 'dispatch_uncertain', 'task_id': reservation['task_id'], 'channel_id': channel_id,
                    'queued': queued, 'queued_count': len(queued)}
        mark_production_dispatched(channel_id, reservation['task_id'])
        queued.append({'task_id': reservation['task_id'], 'channel_id': channel_id})
    if queued:
        return {'status': 'queued', **queued[0], 'queued': queued, 'queued_count': len(queued)}
    return {'status': 'active' if reconciliation == 'active' else 'idle', 'channels': statuses}
