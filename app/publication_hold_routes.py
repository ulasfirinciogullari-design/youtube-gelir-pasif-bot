"""Authenticated narrow publication hold; never cancel a running render."""
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.external_routes import _require_auth
from app.services.external_artifact_import import _object, _text
from app.services.source_publication_hold import hold_source_publication, SourcePublicationHoldError, _ID


router = APIRouter()
MAX_HOLD_REQUEST_BYTES = 8192


@router.post('/studio/api/job/{task_id}/hold-publication')
async def hold_publication(task_id: str, request: Request, x_factory_token: str | None = Header(default=None)):
    _require_auth(x_factory_token)
    try:
        if str(UUID(task_id)) != task_id:
            raise ValueError()
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=422, detail='publication_hold_task_invalid') from None
    if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
        raise HTTPException(status_code=415, detail='publication_hold_json_required')
    length = request.headers.get('content-length')
    if length is not None and (len(length) > 12 or not length.isascii() or not length.isdigit()
                               or int(length) > MAX_HOLD_REQUEST_BYTES):
        raise HTTPException(status_code=413, detail='publication_hold_request_too_large')
    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > MAX_HOLD_REQUEST_BYTES:
            raise HTTPException(status_code=413, detail='publication_hold_request_too_large')
        payload.extend(chunk)
    try:
        value = _object(bytes(payload), limit=MAX_HOLD_REQUEST_BYTES)
        if set(value) != {'expected_channel_id', 'expected_profile_revision', 'reason'}:
            raise ValueError()
        if not all(type(value[key]) is str and _ID.fullmatch(value[key])
                   for key in ('expected_channel_id', 'expected_profile_revision')):
            raise ValueError()
        _text(value['reason'], 800)
        if len(value['reason']) < 12:
            raise ValueError()
    except Exception:
        raise HTTPException(status_code=422, detail='publication_hold_schema_invalid') from None
    try:
        held = await run_in_threadpool(hold_source_publication, task_id, **value)
        if held.get('status') not in {'held', 'already_held'} or held.get('task_id') != task_id:
            raise ValueError()
        return {key: held[key] for key in ('status', 'task_id', 'channel_id', 'profile_revision')}
    except SourcePublicationHoldError:
        raise HTTPException(status_code=409, detail='publication_hold_not_eligible') from None
    except Exception:
        # Retry this exact source/request after an uncertain reply; never make a new publisher.
        raise HTTPException(status_code=503, detail='publication_hold_unavailable') from None
