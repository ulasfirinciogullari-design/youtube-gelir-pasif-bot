"""Authenticated, bounded external-master intake. Never starts production."""
import hmac
from pathlib import Path
import tempfile

from fastapi import APIRouter, Header, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException

from app.config import settings
from app.services import external_artifact_import as artifact
from app.services.external_master_ingest import (
    ExternalMasterIngestError, ingest_external_master, list_external_master_targets,
)


router = APIRouter()
MAX_REQUEST_BYTES = artifact.MAX_VIDEO_BYTES + artifact.MAX_CAPTION_BYTES + artifact.MAX_MANIFEST_BYTES + 65536
_FIELDS = {'video', 'captions', 'manifest', 'target_channel_id',
           'expected_connection_id', 'expected_profile_revision'}


async def _copy(upload, target, maximum):
    size = 0
    with target.open('xb') as stream:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > maximum:
                raise HTTPException(status_code=413, detail='external_upload_too_large')
            stream.write(chunk)
    if not size:
        raise HTTPException(status_code=422, detail='external_upload_empty')


def _require_auth(x_factory_token):
    configured = str(getattr(settings, 'factory_api_token', '') or '')
    if not configured:
        raise HTTPException(status_code=503, detail='external_import_auth_unavailable')
    if not isinstance(x_factory_token, str) or len(x_factory_token) > 4096 or not hmac.compare_digest(
            x_factory_token.encode('utf-8'), configured.encode('utf-8')):
        raise HTTPException(status_code=401, detail='external_import_unauthorized')


@router.get('/studio/api/external-masters/targets')
def external_master_targets(x_factory_token: str | None = Header(default=None)):
    _require_auth(x_factory_token)
    try:
        return list_external_master_targets()
    except ExternalMasterIngestError:
        raise HTTPException(status_code=503, detail='external_ingest_targets_unavailable') from None


@router.post('/studio/api/external-masters')
async def import_external_master(request: Request, x_factory_token: str | None = Header(default=None)):
    _require_auth(x_factory_token)
    length = request.headers.get('content-length')
    if length is not None and (len(length) > 12 or not length.isascii() or not length.isdigit() or int(length) > MAX_REQUEST_BYTES):
        raise HTTPException(status_code=413, detail='external_upload_too_large')
    if not request.headers.get('content-type', '').lower().startswith('multipart/form-data;'):
        raise HTTPException(status_code=415, detail='external_multipart_required')
    received = 0
    async def receive_bounded():
        nonlocal received
        message = await request.receive()
        if message['type'] == 'http.disconnect':
            raise MultiPartException('external_upload_interrupted')
        if message['type'] == 'http.request':
            received += len(message.get('body', b''))
            if received > MAX_REQUEST_BYTES:
                # MultipartParser closes every spooled file on this exception.
                raise MultiPartException('external_upload_too_large')
        return message
    bounded = Request(request.scope, receive=receive_bounded)
    try:
        async with bounded.form(max_files=2, max_fields=4, max_part_size=artifact.MAX_MANIFEST_BYTES) as form:
            items = list(form.multi_items())
            if len(items) != 6 or set(form) != _FIELDS:
                raise HTTPException(status_code=422, detail='external_fields_invalid')
            if not all(isinstance(form[name], UploadFile) for name in ('video', 'captions')):
                raise HTTPException(status_code=422, detail='external_files_required')
            if not all(type(form[name]) is str for name in _FIELDS - {'video', 'captions'}):
                raise HTTPException(status_code=422, detail='external_fields_invalid')
            manifest = artifact.validate_external_manifest(form['manifest'])
            with tempfile.TemporaryDirectory(prefix='external_master_ingest_') as directory:
                staging = Path(directory).resolve()
                video, captions = staging / 'master.mp4', staging / 'captions.srt'
                await _copy(form['video'], video, artifact.MAX_VIDEO_BYTES)
                await _copy(form['captions'], captions, artifact.MAX_CAPTION_BYTES)
                return await run_in_threadpool(ingest_external_master,
                    staging, video, captions, manifest,
                    target_channel_id=form['target_channel_id'],
                    expected_connection_id=form['expected_connection_id'],
                    expected_profile_revision=form['expected_profile_revision'])
    except StarletteHTTPException as exc:
        if received > MAX_REQUEST_BYTES:
            raise HTTPException(status_code=413, detail='external_upload_too_large') from None
        if exc.status_code == 400:
            raise HTTPException(status_code=422, detail='external_multipart_invalid') from None
        raise
    except artifact.ExternalArtifactValidationError:
        raise HTTPException(status_code=422, detail='external_artifact_invalid') from None
    except ExternalMasterIngestError as exc:
        code = str(exc)
        status = 409 if code in {'external_ingest_connection_missing', 'external_ingest_connection_changed',
            'external_ingest_profile_changed', 'external_ingest_language_not_allowed',
            'external_ingest_busy_or_uncertain', 'external_ingest_reservation_conflict',
            'external_ingest_existing_job_conflict', 'external_ingest_binding_changed'} else 503
        raise HTTPException(status_code=status, detail=code) from None
    except Exception:
        raise HTTPException(status_code=503, detail='external_ingest_unavailable') from None
