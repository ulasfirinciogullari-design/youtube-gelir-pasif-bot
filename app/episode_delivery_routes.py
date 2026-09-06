"""Owner-only recognition of an already-public external episode; no dispatch."""
import re
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.external_routes import _require_auth
from app.services.external_artifact_import import _object, _text
from app.services.external_episode_delivery import resolve_external_episode, ExternalEpisodeDeliveryError


router = APIRouter()
MAX_DELIVERY_REQUEST_BYTES = 16 * 1024
_FIELDS = {'channel_id', 'original_task_id', 'failed_leaf_id', 'expected_profile_revision',
           'expected_topic_sha256', 'editorial_explanation'}


def _uuid(value):
    if type(value) is not str or str(UUID(value)) != value:
        raise ValueError()


def _resolve_and_present(external_task_id, value):
    receipt = resolve_external_episode(external_task_id=external_task_id, **value)
    # Never serialize the private receipt, explanation, source evidence or QA.
    if receipt.get('status') not in {'resolved', 'already_resolved'}:
        raise ValueError()
    proof = receipt['public_delivery']
    _uuid(proof['publish_task_id'])
    if (proof.get('external_task_id') != external_task_id or type(proof.get('youtube_video_id')) is not str
            or not re.fullmatch(r'[A-Za-z0-9_-]{11}', proof['youtube_video_id'])):
        raise ValueError()
    return {'status': receipt['status'], 'channel_id': value['channel_id'],
            'original_task_id': value['original_task_id'], 'failed_leaf_id': value['failed_leaf_id'],
            'external_task_id': external_task_id, 'publish_task_id': proof['publish_task_id'],
            'youtube_video_id': proof['youtube_video_id']}


@router.post('/studio/api/external-masters/{external_task_id}/resolve-episode')
async def resolve_external_episode_delivery(external_task_id: str, request: Request,
                                           x_factory_token: str | None = Header(default=None)):
    _require_auth(x_factory_token)  # BEFORE identifiers, content type or body consumption.
    try:
        _uuid(external_task_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=422, detail='episode_delivery_task_invalid') from None
    if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
        raise HTTPException(status_code=415, detail='episode_delivery_json_required')
    length = request.headers.get('content-length')
    if length is not None and (len(length) > 12 or not length.isascii() or not length.isdigit()
                               or int(length) > MAX_DELIVERY_REQUEST_BYTES):
        raise HTTPException(status_code=413, detail='episode_delivery_request_too_large')
    payload = bytearray()
    try:
        async for chunk in request.stream():
            if len(payload) + len(chunk) > MAX_DELIVERY_REQUEST_BYTES:
                raise HTTPException(status_code=413, detail='episode_delivery_request_too_large')
            payload.extend(chunk)
        value = _object(bytes(payload), limit=MAX_DELIVERY_REQUEST_BYTES)
        if set(value) != _FIELDS:
            raise ValueError()
        for key in ('original_task_id', 'failed_leaf_id'):
            _uuid(value[key])
        for key in ('channel_id', 'expected_profile_revision'):
            if type(value[key]) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{8,128}', value[key]):
                raise ValueError()
        if type(value['expected_topic_sha256']) is not str or not re.fullmatch(r'[0-9a-f]{64}', value['expected_topic_sha256']):
            raise ValueError()
        _text(value['editorial_explanation'], 1200)
        if len(value['editorial_explanation']) < 30 or external_task_id in {value['original_task_id'], value['failed_leaf_id']}:
            raise ValueError()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=422, detail='episode_delivery_schema_invalid') from None
    try:
        return await run_in_threadpool(_resolve_and_present, external_task_id, value)
    except ExternalEpisodeDeliveryError:
        raise HTTPException(status_code=409, detail='episode_delivery_not_eligible') from None
    except Exception:
        # A commit may already exist. Repeating this SAME request is idempotent;
        # this response does not authorize another source, retry or publish.
        raise HTTPException(status_code=503, detail='episode_delivery_unavailable') from None
