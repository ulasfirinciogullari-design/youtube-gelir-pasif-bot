"""Owner-only number reservation, not review, publishing or scheduler resume."""
from fastapi import APIRouter, Header, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.external_routes import _require_auth
from app.services.external_artifact_import import _object
from app.services.deleted_episode_replacement import (
    DeletedEpisodeReplacementError, reserve_deleted_episode_replacement, validate_replacement_request,
)

router = APIRouter()
MAX_REQUEST_BYTES = 8192


@router.post('/studio/api/job/{task_id}/reserve-deleted-episode-replacement')
async def reserve_replacement(task_id: str, request: Request, x_factory_token: str | None = Header(default=None)):
    _require_auth(x_factory_token)
    if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
        raise HTTPException(status_code=415, detail='deleted_episode_replacement_json_required')
    length = request.headers.get('content-length')
    if length is not None and (len(length) > 12 or not length.isascii() or not length.isdigit() or int(length) > MAX_REQUEST_BYTES):
        raise HTTPException(status_code=413, detail='deleted_episode_replacement_request_too_large')
    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > MAX_REQUEST_BYTES:
            raise HTTPException(status_code=413, detail='deleted_episode_replacement_request_too_large')
        payload.extend(chunk)
    try:
        value = validate_replacement_request(task_id, _object(bytes(payload), limit=MAX_REQUEST_BYTES))
    except Exception:
        raise HTTPException(status_code=422, detail='deleted_episode_replacement_schema_invalid') from None
    try:
        result = await run_in_threadpool(reserve_deleted_episode_replacement, task_id, **value)
        if result.get('status') not in {'reserved', 'already_reserved'} or result.get('task_id') != task_id:
            raise ValueError()
        return {k: result[k] for k in ('status', 'task_id', 'previous_source_task_id', 'channel_id', 'series')}
    except DeletedEpisodeReplacementError:
        raise HTTPException(status_code=409, detail='deleted_episode_replacement_not_eligible') from None
    except Exception:
        raise HTTPException(status_code=503, detail='deleted_episode_replacement_unavailable') from None
