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


def _client() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_default(value: Any) -> str:
    return str(value)


def _job_key(task_id: str) -> str:
    return JOB_PREFIX + task_id


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
