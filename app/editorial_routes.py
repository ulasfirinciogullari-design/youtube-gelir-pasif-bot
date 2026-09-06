"""Owner-authorized editorial review of an imported, existing master only."""
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.external_routes import _require_auth
from app.services.external_artifact_import import _object, ExternalArtifactValidationError
from app.services.external_editorial_review import create_editorial_review, EditorialReviewError


router = APIRouter()
MAX_REVIEW_REQUEST_BYTES = 1024 * 1024


def _review_and_queue(task_id, evidence_pack):
    from app.services.studio_state import get_job
    from app.publish_tasks import queue_automatic_publish

    create_editorial_review(task_id, evidence_pack)
    source = get_job(task_id)
    result = source.get('result') or {}
    # This uses the existing immutable reservation / private-first publisher.
    # A retry after an uncertain HTTP reply cannot enqueue another insert.
    publication = queue_automatic_publish(task_id)
    return {'task_id': task_id,
            'quality_disposition': result.get('quality_disposition'),
            'editorial_review_id': result.get('editorial_review_id'),
            'editorial_review_sha256': result.get('editorial_review_sha256'),
            'publication': publication,
            'studio_url': '/studio/job/' + task_id}


@router.post('/studio/api/external-masters/{task_id}/review')
async def review_external_master(task_id: str, request: Request,
                                 x_factory_token: str | None = Header(default=None)):
    _require_auth(x_factory_token)
    try:
        if str(UUID(task_id)) != task_id:
            raise ValueError()
    except (ValueError, AttributeError):
        raise HTTPException(status_code=422, detail='editorial_task_invalid') from None
    if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
        raise HTTPException(status_code=415, detail='editorial_json_required')
    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > MAX_REVIEW_REQUEST_BYTES:
            raise HTTPException(status_code=413, detail='editorial_request_too_large')
        payload.extend(chunk)
    try:
        value = _object(bytes(payload), limit=MAX_REVIEW_REQUEST_BYTES)
        if set(value) != {'evidence_pack'} or type(value['evidence_pack']) is not dict:
            raise ExternalArtifactValidationError('editorial_schema_invalid')
    except (ValueError, UnicodeError):
        raise HTTPException(status_code=422, detail='editorial_schema_invalid') from None
    try:
        return await run_in_threadpool(_review_and_queue, task_id, value['evidence_pack'])
    except EditorialReviewError:
        raise HTTPException(status_code=409, detail='editorial_review_not_eligible') from None
    except Exception:
        # Publication could already be reserved; never report that retrying a
        # brand-new source or calling videos.insert directly is safe.
        raise HTTPException(status_code=503, detail='editorial_review_or_publication_unavailable') from None
