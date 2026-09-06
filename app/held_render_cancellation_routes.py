"""Owner-only cancellation of an already publication-held orphan retry."""
from fastapi import APIRouter, Header, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.external_routes import _require_auth
from app.services.external_artifact_import import _object
from app.services.held_render_cancellation import (
    cancel_held_render, validate_cancellation_request, HeldRenderCancellationError,
)


router = APIRouter()
MAX_CANCELLATION_REQUEST_BYTES = 8192


@router.post('/studio/api/job/{task_id}/cancel-held-render')
async def cancel_render(task_id: str, request: Request, x_factory_token: str | None = Header(default=None)):
    _require_auth(x_factory_token)
    if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
        raise HTTPException(status_code=415, detail='held_render_cancellation_json_required')
    length = request.headers.get('content-length')
    if length is not None and (len(length) > 12 or not length.isascii() or not length.isdigit()
                               or int(length) > MAX_CANCELLATION_REQUEST_BYTES):
        raise HTTPException(status_code=413, detail='held_render_cancellation_request_too_large')
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_CANCELLATION_REQUEST_BYTES:
            raise HTTPException(status_code=413, detail='held_render_cancellation_request_too_large')
        body.extend(chunk)
    try:
        value = validate_cancellation_request(task_id, _object(bytes(body), limit=MAX_CANCELLATION_REQUEST_BYTES))
    except Exception:
        raise HTTPException(status_code=422, detail='held_render_cancellation_schema_invalid') from None
    try:
        result = await run_in_threadpool(cancel_held_render, task_id, **value)
        if result.get('status') not in {'cancelled', 'already_cancelled'} or result.get('task_id') != task_id:
            raise ValueError()
        return {key: result[key] for key in ('status', 'task_id', 'channel_id', 'profile_revision')}
    except HeldRenderCancellationError:
        # A 409 can retain a cancel_requested fence; it never means stopped.
        raise HTTPException(status_code=409, detail='held_render_cancellation_not_eligible') from None
    except Exception:
        raise HTTPException(status_code=503, detail='held_render_cancellation_unavailable') from None
