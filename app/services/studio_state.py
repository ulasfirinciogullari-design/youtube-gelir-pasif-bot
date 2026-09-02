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
RETRY_DISPATCH_PREFIX = 'youtube_studio:retry_dispatch:'
RETRY_CHILD_CLAIM_PREFIX = 'youtube_studio:retry_child_claim:'
RETRY_CHILD_EXECUTION_PREFIX = 'youtube_studio:retry_child_execution:'
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


# The public job flag and the private, single-use checkpoint must move as one
# Redis state machine.  Otherwise a late job-record write can hide a real
# checkpoint or resurrect an already-consumed one.  The synchronization script
# deliberately uses only EXISTS for the checkpoint and claim keys; it never
# opens the private recovery package.
_SYNC_REPAIR_CHECKPOINT_STATE = r'''
local dispatch_exists = redis.call('EXISTS', KEYS[4]) == 1
local checkpoint_exists = false
if not dispatch_exists then
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


_CLAIM_RETRY_DISPATCH = r'''
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


_MARK_RETRY_DISPATCH = r'''
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


_ACQUIRE_RETRY_CHILD_EXECUTION = r'''
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


_CONSUME_REPAIR_CHECKPOINT = r'''
local raw_checkpoint = redis.call('GET', KEYS[2])
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
) -> dict:
    """Atomically reserve exactly one retry and, when present, its checkpoint."""
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
        pipe = client.pipeline()
        pipe.setex(_job_key(task_id), JOB_TTL_SECONDS, encoded)
        pipe.zadd(JOB_INDEX, {task_id: float(payload.get('created_ts') or time.time())})
        pipe.expire(JOB_INDEX, JOB_TTL_SECONDS)
        pipe.execute()

        overflow = client.zcard(JOB_INDEX) - MAX_INDEXED_JOBS
        if overflow > 0:
            old_ids = client.zrange(JOB_INDEX, 0, overflow - 1)
            cleanup = client.pipeline()
            for old_id in old_ids:
                cleanup.delete(_job_key(old_id))
                cleanup.zrem(JOB_INDEX, old_id)
            cleanup.execute()
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


def list_jobs(limit: int = 30) -> list[dict]:
    limit = max(1, min(int(limit), 100))
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
    return update_job(
        task_id,
        state=state,
        stage='complete' if state == 'SUCCESS' else 'awaiting_approval',
        progress=100,
        message='Video hazır.' if state == 'SUCCESS' else 'Storyboard onay bekliyor.',
        result=result,
        error=None,
    )


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
