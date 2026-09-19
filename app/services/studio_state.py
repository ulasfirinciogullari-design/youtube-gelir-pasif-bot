from __future__ import annotations

from datetime import datetime, timezone
import json
import re
import time
from typing import Any

import redis

from app.config import settings

JOB_PREFIX = 'youtube_studio:job:'
JOB_INDEX = 'youtube_studio:jobs'
JOB_TTL_SECONDS = 60 * 60 * 24 * 90
MAX_INDEXED_JOBS = 500
REPAIR_CHECKPOINT_PREFIX = 'youtube_studio:repair_checkpoint:'
REPAIR_CHECKPOINT_CLAIM_PREFIX = 'youtube_studio:repair_checkpoint_claim:'
# A durable private staging receipt is not a runnable recovery checkpoint.
SELECTED_VISUAL_RECOVERY_PREFIX = 'youtube_studio:selected_visual_recovery:v6:'
RETRY_DISPATCH_PREFIX = 'youtube_studio:retry_dispatch:'
RETRY_CHILD_CLAIM_PREFIX = 'youtube_studio:retry_child_claim:'
RETRY_CHILD_EXECUTION_PREFIX = 'youtube_studio:retry_child_execution:'
EXTERNAL_EPISODE_LEAF_PREFIX = 'youtube_studio:external_episode_delivery:v1:leaf:'
RENDER_CANCELLATION_PREFIX = 'youtube_studio:render_cancellation:v1:'
RETAINED_DELIVERY_CHILD_PREFIX = 'youtube_studio:retained_delivery_child:v1:'
_RETAINED_LINEAGE = ('aad98516-eee0-5f39-b49d-af33f01e688e',
                     '69ce7728-acce-4e5d-b30f-d5432cf7f3ac',
                     'f5315330-e927-44c7-aed7-394a331111c8')
_RETAINED_ROOT_KEYS = tuple('youtube_studio:retained_delivery:v1:' + _RETAINED_LINEAGE[0] + suffix
                            for suffix in (':manifest', ':journal', ':anchor'))
PAID_CREATE_BUDGET_PREFIX = 'youtube_studio:paid_create_budget:'
REPAIR_CHECKPOINT_TTL_SECONDS = 60 * 60 * 24 * 30
RETRY_DISPATCH_TTL_SECONDS = REPAIR_CHECKPOINT_TTL_SECONDS
_TASK_ID_PATTERN = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
)


def _client() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_default(value: Any) -> str:
    return str(value)


def _job_key(task_id: str) -> str:
    return JOB_PREFIX + task_id


def _repair_checkpoint_key(task_id: str) -> str:
    normalized = str(task_id or '').strip().lower()
    if not _TASK_ID_PATTERN.fullmatch(normalized):
        raise ValueError('valid task_id is required')
    return REPAIR_CHECKPOINT_PREFIX + normalized


def _repair_checkpoint_claim_key(task_id: str) -> str:
    normalized = str(task_id or '').strip().lower()
    if not _TASK_ID_PATTERN.fullmatch(normalized):
        raise ValueError('valid task_id is required')
    return REPAIR_CHECKPOINT_CLAIM_PREFIX + normalized


def _retry_dispatch_key(task_id: str) -> str:
    normalized = str(task_id or '').strip().lower()
    if not _TASK_ID_PATTERN.fullmatch(normalized):
        raise ValueError('valid task_id is required')
    return RETRY_DISPATCH_PREFIX + normalized


def _retry_child_claim_key(task_id: str) -> str:
    normalized = str(task_id or '').strip().lower()
    if not _TASK_ID_PATTERN.fullmatch(normalized):
        raise ValueError('valid task_id is required')
    return RETRY_CHILD_CLAIM_PREFIX + normalized


def _retry_child_execution_key(task_id: str) -> str:
    normalized = str(task_id or '').strip().lower()
    if not _TASK_ID_PATTERN.fullmatch(normalized):
        raise ValueError('valid task_id is required')
    return RETRY_CHILD_EXECUTION_PREFIX + normalized


# This separate hash is authoritative even if a concurrent dashboard write
# saves an older job document. Reserve before any potentially paid request;
# a lost Redis reply therefore consumes a slot without authorizing a POST.
_PAID_CREATE_BUDGET = r'''
local raw_job = redis.call('GET', KEYS[2])
if not raw_job then return {-1, 0, 0} end
local decoded, job = pcall(cjson.decode, raw_job)
if not decoded or type(job) ~= 'table' then return {-1, 0, 0} end
local cap = tonumber(ARGV[1])
local used = 0
if redis.call('EXISTS', KEYS[1]) == 1 then
  local saved_cap = tonumber(redis.call('HGET', KEYS[1], 'cap'))
  local saved_used = tonumber(redis.call('HGET', KEYS[1], 'used'))
  if not saved_cap or saved_cap < 1 or saved_cap % 1 ~= 0
     or not saved_used or saved_used < 0 or saved_used % 1 ~= 0 then
    return {-1, 0, 0}
  end
  cap = math.min(cap, saved_cap)
  used = saved_used
else
  local prior = job['paid_create_slots_used']
  if prior == nil and type(job['result']) == 'table' then
    prior = job['result']['paid_create_slots_used']
        or job['result']['runway_attempts']
  end
  if prior ~= nil and prior ~= cjson.null then
    if type(prior) ~= 'number' or prior < 0 or prior % 1 ~= 0 then
      return {-1, 0, 0}
    end
    used = prior
  end
end
local accepted = 1
if ARGV[2] == '1' then
  if used >= cap then accepted = 0 else used = used + 1 end
end
redis.call('HSET', KEYS[1], 'cap', cap, 'used', used)
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[4]))
job['preview_total_paid_create_cap'] = cap
job['paid_create_slots_used'] = used
job['paid_create_slots_remaining'] = math.max(0, cap - used)
job['updated_at'] = ARGV[3]
redis.call('SETEX', KEYS[2], tonumber(ARGV[4]), cjson.encode(job))
return {accepted, used, cap}
'''


def paid_create_budget_state(
    task_id: str,
    cap: int,
    *,
    reserve: bool = False,
) -> dict[str, int]:
    """Read or atomically reserve a paid slot; Redis errors must propagate."""
    normalized = str(task_id or '').strip().lower()
    if not _TASK_ID_PATTERN.fullmatch(normalized):
        raise ValueError('valid task_id is required')
    if type(cap) is not int or cap < 1:
        raise ValueError('a positive paid-create cap is required')
    result = _client().eval(
        _PAID_CREATE_BUDGET,
        2,
        PAID_CREATE_BUDGET_PREFIX + normalized,
        _job_key(normalized),
        cap,
        '1' if reserve else '0',
        _now_iso(),
        JOB_TTL_SECONDS,
    )
    if (
        not isinstance(result, (list, tuple))
        or len(result) != 3
        or any(type(value) is not int for value in result)
    ):
        raise RuntimeError('Paid-create budget state is invalid')
    accepted, used, saved_cap = result
    if accepted == 0:
        raise RuntimeError('Paid-create cap is exhausted')
    if accepted != 1 or used < 0 or not 1 <= saved_cap <= cap:
        raise RuntimeError('Paid-create budget state is unavailable')
    return {'used': used, 'cap': saved_cap, 'remaining': max(0, saved_cap - used)}


# The public job flag and the private, single-use checkpoint must move as one
# Redis state machine.  Otherwise a late job-record write can hide a real
# checkpoint or resurrect an already-consumed one.  The synchronization script
# deliberately uses only EXISTS for the checkpoint and claim keys; it never
# opens the private recovery package.
_SYNC_REPAIR_CHECKPOINT_STATE = (
    "local selected_staged = redis.call('EXISTS', '" + SELECTED_VISUAL_RECOVERY_PREFIX
    + "' .. string.sub(KEYS[1], " + str(len(JOB_PREFIX) + 1) + ")) == 1\n"
) + r'''
local dispatch_exists = redis.call('EXISTS', KEYS[4]) == 1
local checkpoint_exists = false
if not dispatch_exists and not selected_staged then
  checkpoint_exists = redis.call('EXISTS', KEYS[2]) == 1
end
local claim_exists = redis.call('EXISTS', KEYS[3]) == 1
local retry_mode = nil
local retry_state = nil
local retry_child_task_id = nil
if dispatch_exists then
  retry_mode = redis.call('HGET', KEYS[4], 'mode')
  retry_state = redis.call('HGET', KEYS[4], 'state')
  retry_child_task_id = redis.call('HGET', KEYS[4], 'child_task_id')
end

local raw_job = redis.call('GET', KEYS[1])
if raw_job then
  local decoded, job = pcall(cjson.decode, raw_job)
  if decoded and type(job) == 'table' then
    local selected = job['selected_visual_recovery']
    if type(selected) == 'table' and selected['status'] == 'staged_private_checkpoint' then
      checkpoint_exists = false
    end
    local desired_available = checkpoint_exists
    local desired_claimed = claim_exists or retry_mode == 'repair'
    local desired_retry_claimed = dispatch_exists
    if job['repair_available'] ~= desired_available
       or job['repair_claimed'] ~= desired_claimed
       or job['retry_claimed'] ~= desired_retry_claimed
       or job['retry_child_task_id'] ~= retry_child_task_id
       or job['retry_dispatch_state'] ~= retry_state then
      job['repair_available'] = desired_available
      job['repair_claimed'] = desired_claimed
      job['retry_claimed'] = desired_retry_claimed
      job['retry_child_task_id'] = retry_child_task_id
      job['retry_dispatch_state'] = retry_state
      job['updated_at'] = ARGV[1]
      redis.call('SETEX', KEYS[1], tonumber(ARGV[2]), cjson.encode(job))
    end
  end
end

if checkpoint_exists then
  return 2
end
if claim_exists or dispatch_exists then
  return 1
end
return 0
'''


_SELECTED_V6_CHECK = r'''
local function selected_v6_checkpoint(raw)
  if not raw then return false end
  local decoded, checkpoint = pcall(cjson.decode, raw)
  if not decoded or type(checkpoint) ~= 'table'
     or type(checkpoint['approved_package']) ~= 'table' then return false end
  local package = checkpoint['approved_package']
  local media = package['_recovered_generated_media']
  local voice = package['_recovered_voice']
  return (type(media) == 'table' and media['version'] == 6)
      or (type(voice) == 'table' and voice['version'] == 6)
end
'''


_RETAINED_DELIVERY_FENCE = (
    "local function retained_delivery(task_id)\n"
    " if redis.call('EXISTS', '" + RETAINED_DELIVERY_CHILD_PREFIX + "' .. task_id) == 1 then return true end\n"
    " if " + ' or '.join("task_id == '" + task + "'" for task in _RETAINED_LINEAGE) + " then\n"
    "  return redis.call('EXISTS', " + ', '.join("'" + key + "'" for key in _RETAINED_ROOT_KEYS) + ") > 0\n"
    " end\n return false\nend\n"
)


_CLAIM_RETRY_DISPATCH = _RETAINED_DELIVERY_FENCE + _SELECTED_V6_CHECK + (
    "if retained_delivery(ARGV[7]) or retained_delivery(ARGV[2]) then return {-7, ''} end\n"
    "if redis.call('EXISTS', '" + EXTERNAL_EPISODE_LEAF_PREFIX + "' .. ARGV[7]) == 1 then return {-4, ''} end\n"
    "if redis.call('EXISTS', '" + RENDER_CANCELLATION_PREFIX + "' .. ARGV[7]) == 1 then return {-5, ''} end\n"
    "if redis.call('EXISTS', '" + SELECTED_VISUAL_RECOVERY_PREFIX + "' .. ARGV[7]) == 1 then return {-6, ''} end\n"
) + r'''
-- V6 needs commissioned exact-cut QA and atomic funding admission first.
-- Reject before DEL/claim, including allow_repair=false and lost staging flags.
if selected_v6_checkpoint(redis.call('GET', KEYS[2])) then return {-6, ''} end
local raw_job = redis.call('GET', KEYS[1])
if not raw_job then
  return {-2, ''}
end
local decoded, job = pcall(cjson.decode, raw_job)
if not decoded or type(job) ~= 'table' or job['state'] ~= 'FAILURE' then
  return {-2, ''}
end
if redis.call('EXISTS', KEYS[4]) == 1 then
  local existing_child = redis.call('HGET', KEYS[4], 'child_task_id') or ''
  return {0, existing_child}
end

local raw_checkpoint = nil
local mode = 'full'
if ARGV[5] == '1' and redis.call('EXISTS', KEYS[2]) == 1 then
  raw_checkpoint = redis.call('GET', KEYS[2])
  redis.call('DEL', KEYS[2])
  redis.call('SETEX', KEYS[3], tonumber(ARGV[6]), ARGV[1])
  mode = 'repair'
elseif redis.call('EXISTS', KEYS[3]) == 1 then
  return {0, ''}
end

redis.call('HSET', KEYS[4],
  'token', ARGV[1],
  'child_task_id', ARGV[2],
  'mode', mode,
  'state', 'reserved',
  'created_at', ARGV[3])
redis.call('EXPIRE', KEYS[4], tonumber(ARGV[6]))
redis.call('HSET', KEYS[5],
  'source_task_id', ARGV[7],
  'token', ARGV[1])
redis.call('EXPIRE', KEYS[5], tonumber(ARGV[6]))
job['repair_available'] = false
job['repair_claimed'] = mode == 'repair'
job['retry_claimed'] = true
job['retry_child_task_id'] = ARGV[2]
job['retry_dispatch_state'] = 'reserved'
job['updated_at'] = ARGV[3]
redis.call('SETEX', KEYS[1], tonumber(ARGV[4]), cjson.encode(job))
if mode == 'repair' then
  return {1, raw_checkpoint or ''}
end
return {2, ''}
'''


_MARK_RETRY_DISPATCH = _RETAINED_DELIVERY_FENCE + (
    "if retained_delivery(string.sub(KEYS[1], " + str(len(JOB_PREFIX) + 1) + ")) then return 0 end\n"
) + r'''
if redis.call('HGET', KEYS[2], 'token') ~= ARGV[1] then
  return 0
end
redis.call('HSET', KEYS[2], 'state', ARGV[2])
redis.call('EXPIRE', KEYS[2], tonumber(ARGV[4]))
local raw_job = redis.call('GET', KEYS[1])
if raw_job then
  local decoded, job = pcall(cjson.decode, raw_job)
  if decoded and type(job) == 'table' then
    job['retry_claimed'] = true
    job['retry_dispatch_state'] = ARGV[2]
    job['updated_at'] = ARGV[3]
    redis.call('SETEX', KEYS[1], tonumber(ARGV[5]), cjson.encode(job))
  end
end
return 1
'''


_ACQUIRE_RETRY_CHILD_EXECUTION = _RETAINED_DELIVERY_FENCE + (
    "if retained_delivery(string.sub(KEYS[2], " + str(len(RETRY_CHILD_EXECUTION_PREFIX) + 1) + "))\n"
    " or retained_delivery(ARGV[2]) then return 0 end\n"
) + r'''
if redis.call('EXISTS', KEYS[1]) == 0 then
  return 0
end
local source_task_id = redis.call('HGET', KEYS[1], 'source_task_id')
local token = redis.call('HGET', KEYS[1], 'token')
if not source_task_id or source_task_id ~= ARGV[2] or not token then
  return 0
end
local acquired = redis.call('SET', KEYS[2], token, 'NX', 'EX', ARGV[1])
if not acquired then
  return 0
end
return 1
'''


_SAVE_REPAIR_CHECKPOINT = r'''
redis.call('SETEX', KEYS[2], tonumber(ARGV[2]), ARGV[1])
redis.call('DEL', KEYS[3])

local raw_job = redis.call('GET', KEYS[1])
if raw_job then
  local decoded, job = pcall(cjson.decode, raw_job)
  if decoded and type(job) == 'table' then
    job['repair_available'] = true
    job['repair_claimed'] = false
    job['updated_at'] = ARGV[3]
    redis.call('SETEX', KEYS[1], tonumber(ARGV[4]), cjson.encode(job))
  end
end
return 1
'''


_CONSUME_REPAIR_CHECKPOINT = _SELECTED_V6_CHECK + (
    "if redis.call('EXISTS', '" + SELECTED_VISUAL_RECOVERY_PREFIX
    + "' .. string.sub(KEYS[1], " + str(len(JOB_PREFIX) + 1) + ")) == 1 then return nil end\n"
) + r'''
local raw_checkpoint = redis.call('GET', KEYS[2])
if selected_v6_checkpoint(raw_checkpoint) then return nil end
if raw_checkpoint then
  redis.call('DEL', KEYS[2])
  redis.call('SETEX', KEYS[3], tonumber(ARGV[1]), '1')
end

local claim_exists = redis.call('EXISTS', KEYS[3]) == 1
local raw_job = redis.call('GET', KEYS[1])
if raw_job then
  local decoded, job = pcall(cjson.decode, raw_job)
  if decoded and type(job) == 'table' then
    job['repair_available'] = false
    job['repair_claimed'] = claim_exists
    job['updated_at'] = ARGV[2]
    redis.call('SETEX', KEYS[1], tonumber(ARGV[3]), cjson.encode(job))
  end
end
return raw_checkpoint
'''


def sync_repair_checkpoint_state(task_id: str) -> dict[str, bool]:
    """Reconcile public repair state without reading the private payload."""
    normalized_task_id = str(task_id or '').strip().lower()
    result = int(_client().eval(
        _SYNC_REPAIR_CHECKPOINT_STATE,
        4,
        _job_key(normalized_task_id),
        _repair_checkpoint_key(normalized_task_id),
        _repair_checkpoint_claim_key(normalized_task_id),
        _retry_dispatch_key(normalized_task_id),
        _now_iso(),
        JOB_TTL_SECONDS,
    ))
    if result not in {0, 1, 2}:
        raise RuntimeError('repair checkpoint state is invalid')
    return {
        'repair_available': result == 2,
        'repair_claimed': result == 1,
    }


def claim_retry_dispatch(
    task_id: str,
    child_task_id: str,
    token: str,
    *,
    allow_repair: bool,
    full_rebuild_binding=None,
) -> dict:
    """Atomically reserve exactly one retry and, when present, its checkpoint."""
    if full_rebuild_binding is not None:
        from app.services.full_video_rebuild import _claim_bound_full_rebuild

        if allow_repair is not False:
            raise ValueError('full rebuild cannot consume a repair checkpoint')
        return _claim_bound_full_rebuild(task_id, child_task_id, token, full_rebuild_binding)
    normalized_task_id = str(task_id or '').strip().lower()
    normalized_child_id = str(child_task_id or '').strip().lower()
    normalized_token = str(token or '').strip()
    if not _TASK_ID_PATTERN.fullmatch(normalized_child_id):
        raise ValueError('valid child_task_id is required')
    if not 16 <= len(normalized_token) <= 256:
        raise ValueError('valid retry token is required')
    result = _client().eval(
        _CLAIM_RETRY_DISPATCH,
        5,
        _job_key(normalized_task_id),
        _repair_checkpoint_key(normalized_task_id),
        _repair_checkpoint_claim_key(normalized_task_id),
        _retry_dispatch_key(normalized_task_id),
        _retry_child_claim_key(normalized_child_id),
        normalized_token,
        normalized_child_id,
        _now_iso(),
        JOB_TTL_SECONDS,
        '1' if allow_repair else '0',
        RETRY_DISPATCH_TTL_SECONDS,
        normalized_task_id,
    )
    if not isinstance(result, (list, tuple)) or len(result) != 2:
        raise RuntimeError('retry dispatch claim returned an invalid result')
    status = int(result[0])
    raw = result[1]
    if isinstance(raw, bytes):
        raw = raw.decode('utf-8')
    if status == -2:
        raise ValueError('retry source must be a failed job')
    if status == -4:
        raise ValueError('retry source was separately delivered by editorial replacement')
    if status == -5:
        raise ValueError('retry source has an owner cancellation fence')
    if status == -6:
        raise ValueError('selected visual recovery awaits commissioned quality and spending admission')
    if status == -7:
        raise ValueError('retained final requires its dedicated delivery task')
    if status == 0:
        return {
            'claimed': False,
            'child_task_id': str(raw or '').strip() or None,
        }
    if status not in {1, 2}:
        raise RuntimeError('retry dispatch claim status is invalid')
    checkpoint = None
    if status == 1:
        try:
            checkpoint = json.loads(raw)
        except Exception as exc:
            raise RuntimeError('claimed repair checkpoint is invalid') from exc
        if (
            not isinstance(checkpoint, dict)
            or checkpoint.get('version') != 1
            or checkpoint.get('source_task_id') != normalized_task_id
            or not isinstance(checkpoint.get('approved_package'), dict)
        ):
            raise RuntimeError('claimed repair checkpoint is invalid')
    return {
        'claimed': True,
        'mode': 'repair' if status == 1 else 'full',
        'child_task_id': normalized_child_id,
        'checkpoint': checkpoint,
    }


def mark_retry_dispatch(task_id: str, token: str, state: str) -> bool:
    """CAS-update a retry dispatch without ever reopening its paid claim."""
    if state not in {'dispatched', 'uncertain'}:
        raise ValueError('invalid retry dispatch state')
    normalized_task_id = str(task_id or '').strip().lower()
    return bool(_client().eval(
        _MARK_RETRY_DISPATCH,
        2,
        _job_key(normalized_task_id),
        _retry_dispatch_key(normalized_task_id),
        str(token or '').strip(),
        state,
        _now_iso(),
        RETRY_DISPATCH_TTL_SECONDS,
        JOB_TTL_SECONDS,
    ))


def acquire_retry_child_execution(
    task_id: str,
    source_task_id: str,
) -> bool:
    """Return false for a duplicate retry message before any paid work."""
    normalized_task_id = str(task_id or '').strip().lower()
    normalized_source_id = str(source_task_id or '').strip().lower()
    _repair_checkpoint_key(normalized_source_id)
    result = int(_client().eval(
        _ACQUIRE_RETRY_CHILD_EXECUTION,
        2,
        _retry_child_claim_key(normalized_task_id),
        _retry_child_execution_key(normalized_task_id),
        RETRY_DISPATCH_TTL_SECONDS,
        normalized_source_id,
    ))
    if result not in {0, 1}:
        raise RuntimeError('retry child execution state is invalid')
    return result == 1


def save_repair_checkpoint(task_id: str, checkpoint: dict) -> dict:
    """Persist one private, server-authored scene-repair checkpoint.

    This payload deliberately lives outside the public Studio job record.  A
    job exposes only the boolean ``repair_available`` flag; storyboard, object
    keys and integrity metadata never cross the polling API boundary.
    """
    key = _repair_checkpoint_key(task_id)
    normalized_task_id = str(task_id).strip().lower()
    if (
        not isinstance(checkpoint, dict)
        or checkpoint.get('version') != 1
        or checkpoint.get('source_task_id') != normalized_task_id
        or not isinstance(checkpoint.get('approved_package'), dict)
    ):
        raise ValueError('repair checkpoint is invalid')
    encoded = json.dumps(
        checkpoint,
        ensure_ascii=False,
        separators=(',', ':'),
        default=_json_default,
    )
    _client().eval(
        _SAVE_REPAIR_CHECKPOINT,
        3,
        _job_key(normalized_task_id),
        key,
        _repair_checkpoint_claim_key(normalized_task_id),
        encoded,
        REPAIR_CHECKPOINT_TTL_SECONDS,
        _now_iso(),
        JOB_TTL_SECONDS,
    )
    return checkpoint


def consume_repair_checkpoint(task_id: str) -> dict | None:
    """Atomically claim a repair package so one click means one paid repair."""
    normalized_task_id = str(task_id or '').strip().lower()
    key = _repair_checkpoint_key(normalized_task_id)
    raw = _client().eval(
        _CONSUME_REPAIR_CHECKPOINT,
        3,
        _job_key(normalized_task_id),
        key,
        _repair_checkpoint_claim_key(normalized_task_id),
        REPAIR_CHECKPOINT_TTL_SECONDS,
        _now_iso(),
        JOB_TTL_SECONDS,
    )
    if not raw:
        return None
    try:
        checkpoint = json.loads(raw)
    except Exception:
        return None
    if (
        not isinstance(checkpoint, dict)
        or checkpoint.get('version') != 1
        or checkpoint.get('source_task_id') != normalized_task_id
        or not isinstance(checkpoint.get('approved_package'), dict)
    ):
        return None
    return checkpoint


def get_job(task_id: str) -> dict | None:
    if not task_id:
        return None
    try:
        raw = _client().get(_job_key(task_id))
        if not raw:
            return None
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def render_cancellation_requested(task_id: str) -> bool:
    """Any durable owner fence stops entry; Redis uncertainty must not allow work."""
    if not isinstance(task_id, str) or not _TASK_ID_PATTERN.fullmatch(task_id):
        raise ValueError('invalid render task identity')
    return bool(_client().exists(RENDER_CANCELLATION_PREFIX + task_id))


def retained_delivery_blocked(task_id: str) -> bool:
    """A retained child or occupied ancestor cannot enter ordinary generation."""
    if not isinstance(task_id, str) or not _TASK_ID_PATTERN.fullmatch(task_id):
        raise ValueError('invalid retained delivery task identity')
    keys = (RETAINED_DELIVERY_CHILD_PREFIX + task_id,)
    if task_id in _RETAINED_LINEAGE:
        keys += _RETAINED_ROOT_KEYS
    return bool(_client().exists(*keys))


def retained_delivery_fence_keys(task_id: str) -> tuple[str, ...]:
    """Keys watched by ordinary writers before touching retained history."""
    return (RETAINED_DELIVERY_CHILD_PREFIX + task_id,
            *(_RETAINED_ROOT_KEYS if task_id in _RETAINED_LINEAGE else ()))


def _watch_retained_delivery(pipe, task_id: str) -> bool:
    keys = retained_delivery_fence_keys(task_id)
    pipe.watch(*keys)
    return bool(pipe.exists(*keys))


def save_job(record: dict) -> dict:
    task_id = str(record.get('task_id') or '').strip()
    if not task_id:
        raise ValueError('task_id is required')

    payload = dict(record)
    payload['task_id'] = task_id
    payload.setdefault('created_at', _now_iso())
    payload.setdefault('created_ts', time.time())
    payload['updated_at'] = _now_iso()

    try:
        client = _client()
        encoded = json.dumps(payload, ensure_ascii=False, default=_json_default)
        for _ in range(3):
            try:
                with client.pipeline() as pipe:
                    # Requested cancellations still retain real in-flight progress.
                    # Once terminal, stale snapshots cannot resurrect the job.
                    pipe.watch(RENDER_CANCELLATION_PREFIX + task_id)
                    if _watch_retained_delivery(pipe, task_id):
                        return get_job(task_id) or payload
                    cancellation = pipe.get(RENDER_CANCELLATION_PREFIX + task_id)
                    if cancellation is not None and json.loads(cancellation).get('status') == 'cancelled':
                        return get_job(task_id) or payload
                    pipe.multi()
                    pipe.setex(_job_key(task_id), JOB_TTL_SECONDS, encoded)
                    pipe.zadd(JOB_INDEX, {task_id: float(payload.get('created_ts') or time.time())})
                    pipe.expire(JOB_INDEX, JOB_TTL_SECONDS)
                    pipe.execute()
                break
            except redis.WatchError:
                continue
        else:
            return get_job(task_id) or payload

        overflow = client.zcard(JOB_INDEX) - MAX_INDEXED_JOBS
        if overflow > 0:
            old_ids = client.zrange(JOB_INDEX, 0, overflow - 1)
            for old_id in old_ids:
                try:
                    with client.pipeline() as cleanup:
                        if _watch_retained_delivery(cleanup, old_id):
                            continue
                        cleanup.multi()
                        cleanup.delete(_job_key(old_id))
                        cleanup.zrem(JOB_INDEX, old_id)
                        cleanup.execute()
                except redis.WatchError:
                    continue
    except Exception:
        # Celery must keep working even when the dashboard registry is unavailable.
        pass
    return payload


def create_job(
    task_id: str,
    spec: dict,
    *,
    kind: str = 'render',
    parent_id: str | None = None,
) -> dict:
    now = time.time()
    return save_job({
        'task_id': task_id,
        'kind': kind,
        'parent_id': parent_id,
        'spec': spec,
        'state': 'PENDING',
        'stage': 'queued',
        'progress': 0,
        'message': 'Görev kuyruğa alındı.',
        'created_ts': now,
        'created_at': datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
        'result': None,
        'error': None,
    })


def update_job(task_id: str, **fields: Any) -> dict:
    record = get_job(task_id) or {
        'task_id': task_id,
        'created_ts': time.time(),
        'created_at': _now_iso(),
        'spec': {},
        'kind': 'render',
    }
    for key, value in fields.items():
        if value is not None:
            record[key] = value
    return save_job(record)


_MERGE_YOUTUBE_RESULT_FIELD = '''
for i = 2, #KEYS do
  if redis.call('EXISTS', KEYS[i]) == 1 then return 0 end
end
local raw = redis.call('GET', KEYS[1])
if not raw then return 0 end
local ok, job = pcall(cjson.decode, raw)
if not ok or type(job) ~= 'table' or type(job['result']) ~= 'table' then return -1 end
local patch = cjson.decode(ARGV[2])
local current = job['result'][ARGV[1]]
if type(current) ~= 'table' then current = {} end
if ARGV[5] == '1' then
  local function present(value)
    return value ~= nil and value ~= cjson.null and value ~= false and value ~= ''
  end
  local youtube = job['result']['youtube']
  if present(current['status']) or present(current['publish_task_id'])
     or present(job['result']['youtube_url'])
     or (type(youtube) == 'table' and present(youtube['video_id'])) then
    return 0
  end
end
for key, value in pairs(patch) do current[key] = value end
job['result'][ARGV[1]] = current
job['updated_at'] = ARGV[3]
redis.call('SETEX', KEYS[1], tonumber(ARGV[4]), cjson.encode(job))
return 1
'''


def merge_youtube_result_field(
    task_id: str, field: str, values: dict, *, only_if_missing: bool = False,
) -> bool:
    """Atomically update one publisher-owned field without replacing render results."""
    if field not in {'youtube', 'youtube_automation'} or not isinstance(values, dict):
        raise ValueError('YouTube result field is invalid')
    if type(only_if_missing) is not bool or only_if_missing and field != 'youtube_automation':
        raise ValueError('Conditional merge is only valid for a missing automation outcome')
    fence_keys = retained_delivery_fence_keys(task_id)
    status = int(_client().eval(
        _MERGE_YOUTUBE_RESULT_FIELD,
        1 + len(fence_keys),
        _job_key(task_id),
        *fence_keys,
        field,
        json.dumps(values, ensure_ascii=False, separators=(',', ':'), default=_json_default),
        _now_iso(),
        JOB_TTL_SECONDS,
        '1' if only_if_missing else '0',
    ))
    if status not in {0, 1}:
        raise RuntimeError('YouTube source result is unavailable')
    return status == 1


def list_jobs(limit: int = 30) -> list[dict]:
    limit = max(1, min(int(limit), MAX_INDEXED_JOBS))
    try:
        client = _client()
        ids = client.zrevrange(JOB_INDEX, 0, limit - 1)
        if not ids:
            return []
        pipe = client.pipeline()
        for task_id in ids:
            pipe.get(_job_key(task_id))
        raw_values = pipe.execute()
        jobs: list[dict] = []
        for raw in raw_values:
            if not raw:
                continue
            try:
                value = json.loads(raw)
            except Exception:
                continue
            if isinstance(value, dict):
                jobs.append(value)
        return jobs
    except Exception:
        return []


def set_stage(
    celery_task,
    task_id: str,
    stage: str,
    progress: int,
    message: str,
    **extra: Any,
) -> dict:
    progress = max(0, min(100, int(progress)))
    meta = {'stage': stage, 'progress': progress, 'message': message, **extra}
    celery_task.update_state(state='PROGRESS', meta=meta)
    return update_job(
        task_id,
        state='PROGRESS',
        stage=stage,
        progress=progress,
        message=message,
        **extra,
    )


def mark_success(task_id: str, result: dict, *, state: str = 'SUCCESS') -> dict:
    if not isinstance(result, dict) or (
        result.get('task_id') is not None and result['task_id'] != task_id
    ):
        raise ValueError('Completion result does not match task')
    # A Celery result is a snapshot from before the asynchronous publisher ran.
    # Read its two owned fields inside WATCH, never merge a stale dashboard read
    # and then save the whole job over a concurrent publisher update.
    incoming = json.loads(json.dumps(result, default=_json_default))
    now = time.time()
    completion = {
        'state': state,
        'stage': 'complete' if state == 'SUCCESS' else 'awaiting_approval',
        'progress': 100,
        'message': 'Video hazır.' if state == 'SUCCESS' else 'Storyboard onay bekliyor.',
        'result': incoming,
        'error': None,
    }
    fallback = {
        'task_id': task_id, 'created_ts': now, 'created_at': _now_iso(),
        'spec': {}, 'kind': 'render', **completion,
    }
    fallback['result'] = {
        key: value for key, value in incoming.items()
        if key not in {'youtube', 'youtube_automation'}
    }
    try:
        client = _client()
        for _ in range(5):
            try:
                with client.pipeline() as pipe:
                    pipe.watch(_job_key(task_id), RENDER_CANCELLATION_PREFIX + task_id)
                    raw = pipe.get(_job_key(task_id))
                    record = json.loads(raw) if raw is not None else {**fallback, 'result': {}}
                    if not isinstance(record, dict) or record.get('task_id') != task_id:
                        raise ValueError('Completion record does not match task')
                    if _watch_retained_delivery(pipe, task_id):
                        return record
                    cancellation = pipe.get(RENDER_CANCELLATION_PREFIX + task_id)
                    if cancellation is not None and json.loads(cancellation).get('status') == 'cancelled':
                        return record
                    if incoming.get('source_task_id') is not None:
                        spec = record.get('spec') if isinstance(record.get('spec'), dict) else {}
                        if (
                            record.get('kind') != 'publish'
                            or incoming['source_task_id'] != spec.get('source_task_id')
                            or incoming['source_task_id'] != record.get('parent_id')
                        ):
                            raise ValueError('Completion source does not match task')
                    merged = dict(incoming)
                    if record.get('kind', 'render') == 'render':
                        prior = record.get('result') if isinstance(record.get('result'), dict) else {}
                        for field in ('youtube', 'youtube_automation'):
                            # Only the publisher's narrow atomic writer may add
                            # these fields; cached render output cannot invent them.
                            merged.pop(field, None)
                            if field in prior:
                                merged[field] = prior[field]
                    record.update(completion)
                    record['result'] = merged
                    record['updated_at'] = _now_iso()
                    encoded = json.dumps(record, ensure_ascii=False, default=_json_default)
                    pipe.multi()
                    pipe.setex(_job_key(task_id), JOB_TTL_SECONDS, encoded)
                    pipe.zadd(JOB_INDEX, {task_id: float(record.get('created_ts') or now)})
                    pipe.expire(JOB_INDEX, JOB_TTL_SECONDS)
                    pipe.execute()
                    return record
            except redis.WatchError:
                continue
    except ValueError:
        raise
    except Exception:
        # Keep the existing registry-outage best effort, but never fall back to
        # an unguarded write that could erase a completed remote upload.
        pass
    return get_job(task_id) or fallback


def mark_failure(task_id: str, error: Exception | str) -> dict:
    current = get_job(task_id) or {}
    failure_stage = str(
        current.get('failure_stage') or current.get('stage') or 'unknown'
    ).strip()
    if not re.fullmatch(r'[a-z0-9_]{1,64}', failure_stage):
        failure_stage = 'unknown'
    return update_job(
        task_id,
        state='FAILURE',
        stage='failed',
        failure_stage=failure_stage,
        progress=100,
        message='Görev başarısız oldu.',
        error=str(error),
    )
