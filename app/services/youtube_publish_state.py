from __future__ import annotations

from datetime import datetime, timezone
import json
import re
import secrets
from typing import Any

import redis

from app.config import settings


UPLOAD_PREFIX = 'youtube_studio:youtube_upload:v2:'
EXECUTION_LOCK_PREFIX = 'youtube_studio:youtube_upload_lock:v2:'
UPLOAD_RECORD_TTL_SECONDS = 60 * 60 * 24 * 180
EXECUTION_LOCK_TTL_SECONDS = 60 * 60 * 6

_ID_PATTERN = re.compile(r'^[A-Za-z0-9_-]{8,128}$')
_VIDEO_ID_PATTERN = re.compile(r'^[A-Za-z0-9_-]{6,128}$')

_CHECK_RETAINED_FENCE = '''
for i = 2, #KEYS do
  if redis.call('EXISTS', KEYS[i]) == 1 then return 0 end
end
'''

_CREATE_RECORD = _CHECK_RETAINED_FENCE + '''
if redis.call('EXISTS', KEYS[1]) == 1 then return 0 end
redis.call('SET', KEYS[1], ARGV[1], 'EX', tonumber(ARGV[2]), 'NX')
return 1
'''

_CAS_RECORD = _CHECK_RETAINED_FENCE + '''
local current = redis.call('GET', KEYS[1])
if current == ARGV[1] then
  redis.call('SET', KEYS[1], ARGV[2], 'EX', tonumber(ARGV[3]))
  return 1
end
return 0
'''

_RELEASE_LOCK = _CHECK_RETAINED_FENCE + '''
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
'''


class UploadReservationError(RuntimeError):
    pass


class UploadAlreadyInProgress(UploadReservationError):
    pass


def _redis() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


def _safe_id(value: str, name: str) -> str:
    value = str(value or '').strip()
    if not _ID_PATTERN.fullmatch(value):
        raise ValueError(f'{name} is invalid')
    return value


def _key(source_task_id: str) -> str:
    return UPLOAD_PREFIX + _safe_id(source_task_id, 'source_task_id')


def _lock_key(source_task_id: str) -> str:
    return EXECUTION_LOCK_PREFIX + _safe_id(source_task_id, 'source_task_id')


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _encode(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, separators=(',', ':'), sort_keys=True)


def _decode(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        record = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return record if isinstance(record, dict) else None


def _guarded_keys(source_task_id, key):
    from app.services.studio_state import retained_delivery_fence_keys

    return (key, *retained_delivery_fence_keys(source_task_id))


def _create_record(client, source_task_id, key, encoded, ttl):
    keys = _guarded_keys(source_task_id, key)
    return client.eval(_CREATE_RECORD, len(keys), *keys, encoded, ttl) == 1


def get_upload_record(source_task_id: str) -> dict[str, Any] | None:
    try:
        raw = _redis().get(_key(source_task_id))
    except Exception as exc:
        raise UploadReservationError('YouTube upload registry is unavailable') from exc
    record = _decode(raw)
    if raw and record is None:
        raise UploadReservationError('YouTube upload registry is inconsistent')
    return record


def reserve_upload(
    source_task_id: str,
    publish_task_id: str,
    *,
    target_channel_id: str,
    connection_id: str,
    publish_plan: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], bool]:
    source_task_id = _safe_id(source_task_id, 'source_task_id')
    publish_task_id = _safe_id(publish_task_id, 'publish_task_id')
    target_channel_id = _safe_id(target_channel_id, 'target_channel_id')
    connection_id = _safe_id(connection_id, 'connection_id')
    if publish_plan is not None:
        from app.services.youtube_automation import validate_publish_plan

        publish_plan = validate_publish_plan(publish_plan)
        if publish_plan.get('source_task_id') != source_task_id:
            raise ValueError('publish_plan source does not match reservation')
        if publish_plan.get('target_channel_id') != target_channel_id:
            raise ValueError('publish_plan target does not match reservation')
    key = _key(source_task_id)
    record = {
        'version': 2,
        'source_task_id': source_task_id,
        'publish_task_id': publish_task_id,
        'status': 'reserved',
        'side_effect_possible': False,
        'target_channel_id': target_channel_id,
        'connection_id': connection_id,
        'publish_plan': publish_plan,
        'created_at': _now(),
        'updated_at': _now(),
    }
    encoded = _encode(record)
    client = _redis()
    try:
        if _create_record(client, source_task_id, key, encoded, UPLOAD_RECORD_TTL_SECONDS):
            return record, True
        for _ in range(3):
            existing_raw = client.get(key)
            existing = _decode(existing_raw)
            if existing is None:
                if _create_record(client, source_task_id, key, encoded, UPLOAD_RECORD_TTL_SECONDS):
                    return record, True
                continue
            # A source final has exactly one immutable channel target. A retry
            # proven to be pre-insert may adopt that channel's newer connection
            # generation, but it can never be redirected to another channel.
            if existing.get('target_channel_id') != target_channel_id:
                return existing, False
            # Only failures proven to have happened before videos.insert may be
            # retried. An uncertain upload is deliberately never duplicated.
            if (
                existing.get('status') not in {'failed_preflight', 'reserved'}
                or existing.get('side_effect_possible')
            ):
                return existing, False
            guarded = _guarded_keys(source_task_id, key)
            replaced = client.eval(
                _CAS_RECORD,
                len(guarded),
                *guarded,
                existing_raw,
                encoded,
                UPLOAD_RECORD_TTL_SECONDS,
            )
            if replaced:
                return record, True
        existing = _decode(client.get(key))
        if existing is not None:
            return existing, False
    except Exception as exc:
        raise UploadReservationError('YouTube upload registry is unavailable') from exc
    raise UploadReservationError('YouTube upload could not be reserved')


def _mutate_owned_record(
    source_task_id: str,
    publish_task_id: str,
    *,
    require_side_effect_possible: bool | None = None,
    require_statuses: set[str] | None = None,
    require_release_side_effect_possible: bool | None = None,
    require_release_statuses: set[str] | None = None,
    **updates: Any,
) -> dict[str, Any]:
    source_task_id = _safe_id(source_task_id, 'source_task_id')
    publish_task_id = _safe_id(publish_task_id, 'publish_task_id')
    key = _key(source_task_id)
    client = _redis()
    try:
        for _ in range(5):
            old_raw = client.get(key)
            record = _decode(old_raw)
            if not record or record.get('publish_task_id') != publish_task_id:
                raise UploadReservationError(
                    'YouTube upload reservation belongs to another task'
                )
            if (
                require_side_effect_possible is not None
                and bool(record.get('side_effect_possible'))
                is not require_side_effect_possible
            ):
                raise UploadReservationError(
                    'YouTube upload reservation crossed its side-effect boundary'
                )
            if require_statuses and record.get('status') not in require_statuses:
                raise UploadReservationError(
                    'YouTube upload reservation is not in the required state'
                )
            if (
                require_release_side_effect_possible is not None
                and bool(record.get('release_side_effect_possible'))
                is not require_release_side_effect_possible
            ):
                raise UploadReservationError(
                    'YouTube release crossed its side-effect boundary'
                )
            if (
                require_release_statuses
                and record.get('release_status') not in require_release_statuses
            ):
                raise UploadReservationError(
                    'YouTube release is not in the required state'
                )
            record.update(updates)
            record['updated_at'] = _now()
            new_raw = _encode(record)
            guarded = _guarded_keys(source_task_id, key)
            if client.eval(
                _CAS_RECORD,
                len(guarded),
                *guarded,
                old_raw,
                new_raw,
                UPLOAD_RECORD_TTL_SECONDS,
            ):
                return record
    except UploadReservationError:
        raise
    except Exception as exc:
        raise UploadReservationError('YouTube upload registry is unavailable') from exc
    raise UploadReservationError('YouTube upload registry changed concurrently')


def mark_upload_started(source_task_id: str, publish_task_id: str) -> dict[str, Any]:
    return _mutate_owned_record(
        source_task_id,
        publish_task_id,
        require_side_effect_possible=False,
        require_statuses={'reserved', 'queued'},
        status='uploading',
        side_effect_possible=True,
        upload_started_at=_now(),
    )


def mark_upload_enqueued(source_task_id: str, publish_task_id: str) -> dict[str, Any]:
    try:
        return _mutate_owned_record(
            source_task_id,
            publish_task_id,
            require_side_effect_possible=False,
            require_statuses={'reserved'},
            status='queued',
            enqueued_at=_now(),
        )
    except UploadReservationError:
        record = get_upload_record(source_task_id)
        if record and record.get('publish_task_id') == publish_task_id:
            # The worker can legitimately start before the web process records
            # that apply_async returned. Never move it backwards to queued.
            return record
        raise


def mark_upload_completed(
    source_task_id: str,
    publish_task_id: str,
    video_id: str,
    *,
    release_mode: str = 'private',
    publish_at: str | None = None,
) -> dict[str, Any]:
    video_id = str(video_id or '').strip()
    if not _VIDEO_ID_PATTERN.fullmatch(video_id):
        raise ValueError('video_id is invalid')
    release_mode = str(release_mode or 'private').strip().casefold()
    if release_mode not in {'private', 'public', 'scheduled'}:
        raise ValueError('release_mode is invalid')
    try:
        return _mutate_owned_record(
            source_task_id,
            publish_task_id,
            require_statuses={'uploading', 'uncertain'},
            status='complete',
            side_effect_possible=True,
            youtube_video_id=video_id,
            requested_release_mode=release_mode,
            requested_publish_at=(str(publish_at) if publish_at else None),
            release_status=(
                'private'
                if release_mode == 'private'
                else 'awaiting_assets'
            ),
            release_side_effect_possible=False,
            completed_at=_now(),
        )
    except UploadReservationError:
        record = get_upload_record(source_task_id)
        if (
            record
            and record.get('publish_task_id') == publish_task_id
            and record.get('status') == 'complete'
            and record.get('youtube_video_id') == video_id
        ):
            return record
        raise


def mark_release_ready(
    source_task_id: str,
    publish_task_id: str,
) -> dict[str, Any]:
    return _mutate_owned_record(
        source_task_id,
        publish_task_id,
        require_statuses={'complete'},
        require_release_side_effect_possible=False,
        require_release_statuses={'awaiting_assets', 'ready'},
        release_status='ready',
        release_ready_at=_now(),
    )


def mark_release_started(
    source_task_id: str,
    publish_task_id: str,
) -> dict[str, Any]:
    return _mutate_owned_record(
        source_task_id,
        publish_task_id,
        require_statuses={'complete'},
        require_release_statuses={'ready', 'releasing', 'uncertain'},
        release_status='releasing',
        release_side_effect_possible=True,
        release_started_at=_now(),
    )


def mark_release_completed(
    source_task_id: str,
    publish_task_id: str,
    release_mode: str,
    *,
    publish_at: str | None = None,
) -> dict[str, Any]:
    release_mode = str(release_mode or '').strip().casefold()
    if release_mode not in {'public', 'scheduled'}:
        raise ValueError('release_mode is invalid')
    return _mutate_owned_record(
        source_task_id,
        publish_task_id,
        require_statuses={'complete'},
        require_release_statuses={'releasing', 'ready'},
        release_status=release_mode,
        release_side_effect_possible=True,
        privacy_status=('public' if release_mode == 'public' else 'private'),
        scheduled_publish_at=(str(publish_at) if publish_at else None),
        release_completed_at=_now(),
    )


def mark_release_blocked(
    source_task_id: str,
    publish_task_id: str,
    error_code: str,
) -> dict[str, Any]:
    return _mutate_owned_record(
        source_task_id,
        publish_task_id,
        require_statuses={'complete'},
        require_release_side_effect_possible=False,
        require_release_statuses={'awaiting_assets', 'ready'},
        release_status='blocked',
        release_side_effect_possible=False,
        release_error_code=str(error_code or 'release_blocked')[:80],
        release_blocked_at=_now(),
    )


def mark_release_uncertain(
    source_task_id: str,
    publish_task_id: str,
    error_code: str,
) -> dict[str, Any]:
    try:
        return _mutate_owned_record(
            source_task_id,
            publish_task_id,
            require_statuses={'complete'},
            require_release_statuses={'releasing', 'uncertain'},
            release_status='uncertain',
            release_side_effect_possible=True,
            release_error_code=str(error_code or 'release_uncertain')[:80],
            release_failed_at=_now(),
        )
    except UploadReservationError:
        record = get_upload_record(source_task_id)
        if record and record.get('release_status') in {'public', 'scheduled'}:
            return record
        raise


def mark_upload_preflight_failed(
    source_task_id: str,
    publish_task_id: str,
    error_code: str,
) -> dict[str, Any]:
    try:
        return _mutate_owned_record(
            source_task_id,
            publish_task_id,
            require_side_effect_possible=False,
            status='failed_preflight',
            side_effect_possible=False,
            error_code=str(error_code or 'preflight_failed')[:80],
            failed_at=_now(),
        )
    except UploadReservationError:
        record = get_upload_record(source_task_id)
        if (
            record
            and record.get('publish_task_id') == publish_task_id
            and record.get('status') == 'complete'
        ):
            # Completion is terminal. A late/redelivered copy that failed its
            # lock or preflight cannot downgrade an already-persisted video ID.
            return record
        if (
            record
            and record.get('publish_task_id') == publish_task_id
            and record.get('side_effect_possible')
        ):
            return mark_upload_uncertain(source_task_id, publish_task_id, error_code)
        raise


def mark_upload_uncertain(
    source_task_id: str,
    publish_task_id: str,
    error_code: str,
) -> dict[str, Any]:
    try:
        return _mutate_owned_record(
            source_task_id,
            publish_task_id,
            require_statuses={'uploading', 'uncertain'},
            status='uncertain',
            side_effect_possible=True,
            error_code=str(error_code or 'upload_outcome_uncertain')[:80],
            failed_at=_now(),
        )
    except UploadReservationError:
        record = get_upload_record(source_task_id)
        if (
            record
            and record.get('publish_task_id') == publish_task_id
            and record.get('status') == 'complete'
        ):
            return record
        raise


def acquire_execution_lock(source_task_id: str, publish_task_id: str) -> str:
    _safe_id(publish_task_id, 'publish_task_id')
    token = secrets.token_urlsafe(32)
    try:
        acquired = _create_record(_redis(), source_task_id, _lock_key(source_task_id),
                                  token, EXECUTION_LOCK_TTL_SECONDS)
    except Exception as exc:
        raise UploadReservationError('YouTube upload lock is unavailable') from exc
    if not acquired:
        raise UploadAlreadyInProgress('A YouTube upload is already in progress')
    return token


def release_execution_lock(source_task_id: str, token: str) -> None:
    try:
        keys = _guarded_keys(source_task_id, _lock_key(source_task_id))
        _redis().eval(_RELEASE_LOCK, len(keys), *keys, str(token or ''))
    except Exception:
        # The lease expires automatically. A release outage must not turn an
        # already-completed upload into a failed/retried upload.
        pass
