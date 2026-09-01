from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import re
import shutil

from app.celery_app import celery
from app.services.storage import download_file
from app.services.studio_state import (
    get_job,
    mark_failure,
    mark_success,
    set_stage,
    update_job,
)
from app.services.youtube import (
    upload_caption_with_credentials,
    upload_video_with_credentials,
)
from app.services.youtube_auth import load_credentials, refresh_channel_info
from app.services.youtube_publish_state import (
    UploadAlreadyInProgress,
    acquire_execution_lock,
    get_upload_record,
    mark_upload_completed,
    mark_upload_preflight_failed,
    mark_upload_started,
    mark_upload_uncertain,
    release_execution_lock,
)


def _safe_error_code(exc: Exception) -> str:
    status = getattr(getattr(exc, 'resp', None), 'status', None)
    name = type(exc).__name__[:48] or 'Error'
    return f'{name}_{status}' if status else name


def _result_from_existing_record(
    task_id: str,
    source_task_id: str,
    record: dict,
) -> dict:
    video_id = str(record.get('youtube_video_id') or '')
    target_channel_id = str(record.get('target_channel_id') or '')
    connection_id = str(record.get('connection_id') or '')
    if record.get('status') != 'complete' or not video_id:
        raise UploadAlreadyInProgress('A YouTube upload is already reserved')
    if not target_channel_id or not connection_id:
        raise RuntimeError('YouTube upload target is missing')
    return {
        'status': 'complete',
        'stage': 'complete',
        'progress': 100,
        'task_id': task_id,
        'source_task_id': source_task_id,
        'youtube_video_id': video_id,
        'youtube_url': f'https://www.youtube.com/watch?v={video_id}',
        'privacy_status': 'private',
        'target_channel_id': target_channel_id,
        'connection_id': connection_id,
        'idempotent_replay': True,
    }


def _reconcile_source_upload(
    source_task_id: str,
    video_id: str,
    *,
    target_channel_id: str,
    connection_id: str,
    uploaded_at: str | None = None,
) -> None:
    source = get_job(source_task_id)
    if not source:
        return
    source_result = source.get('result')
    if not isinstance(source_result, dict):
        return
    existing = source_result.get('youtube')
    youtube = dict(existing) if isinstance(existing, dict) else {}
    if youtube.get('video_id') == video_id and (
        (youtube.get('target_channel_id') and youtube.get('target_channel_id') != target_channel_id)
        or (youtube.get('connection_id') and youtube.get('connection_id') != connection_id)
    ):
        raise RuntimeError('YouTube upload attribution conflicts with its reservation')
    if (
        youtube.get('video_id') == video_id
        and youtube.get('target_channel_id') == target_channel_id
        and youtube.get('connection_id') == connection_id
    ):
        return
    source_result = dict(source_result)
    youtube.update({
        'video_id': video_id,
        'url': f'https://www.youtube.com/watch?v={video_id}',
        'privacy_status': 'private',
        'uploaded_at': (
            uploaded_at
            or youtube.get('uploaded_at')
            or datetime.now(timezone.utc).isoformat()
        ),
        'target_channel_id': str(target_channel_id or ''),
        'connection_id': str(connection_id or ''),
        'reconciled': True,
    })
    source_result['youtube'] = youtube
    update_job(source_task_id, result=source_result)


@celery.task(bind=True, acks_late=True, reject_on_worker_lost=True)
def publish_video_pipeline(
    self,
    source_task_id: str,
    privacy_status: str = 'private',
):
    task_id = str(self.request.id)
    # No Celery autoretry is allowed here: once videos.insert starts, a crash
    # can leave an accepted remote upload without a locally observed response.
    # Retrying as a new insert could create a duplicate video.
    privacy_status = 'private'
    work = Path('/tmp/youtube_publish') / task_id
    work.mkdir(parents=True, exist_ok=True)
    lock_token: str | None = None
    upload_started = False
    upload_completed = False
    try:
        reservation = get_upload_record(source_task_id)
        if not reservation:
            raise RuntimeError('YouTube upload reservation was not found')
        if reservation.get('status') == 'complete':
            result = _result_from_existing_record(task_id, source_task_id, reservation)
            _reconcile_source_upload(
                source_task_id,
                result['youtube_video_id'],
                target_channel_id=result['target_channel_id'],
                connection_id=result['connection_id'],
                uploaded_at=reservation.get('completed_at'),
            )
            mark_success(task_id, result)
            return result
        if reservation.get('publish_task_id') != task_id:
            raise UploadAlreadyInProgress(
                'A YouTube upload is already reserved by another task'
            )
        if reservation.get('status') in {'uploading', 'uncertain'} or reservation.get('side_effect_possible'):
            raise UploadAlreadyInProgress(
                'A YouTube upload is already reserved or its outcome is uncertain'
            )
        lock_token = acquire_execution_lock(source_task_id, task_id)

        source = get_job(source_task_id)
        if not source:
            raise RuntimeError('Source Studio job was not found')
        source_result = source.get('result') or {}
        if not isinstance(source_result, dict) or not source_result.get('video_key'):
            raise RuntimeError('Source job has no completed video')

        prior_youtube = source_result.get('youtube') if isinstance(source_result.get('youtube'), dict) else {}
        prior_video_id = str(prior_youtube.get('video_id') or '').strip()
        if prior_video_id:
            target_channel_id = str(reservation.get('target_channel_id') or '')
            connection_id = str(reservation.get('connection_id') or '')
            if not target_channel_id or not connection_id:
                raise RuntimeError('YouTube upload target is missing')
            mark_upload_completed(source_task_id, task_id, prior_video_id)
            upload_completed = True
            _reconcile_source_upload(
                source_task_id,
                prior_video_id,
                target_channel_id=target_channel_id,
                connection_id=connection_id,
                uploaded_at=prior_youtube.get('uploaded_at'),
            )
            result = {
                'status': 'complete',
                'stage': 'complete',
                'progress': 100,
                'task_id': task_id,
                'source_task_id': source_task_id,
                'youtube_video_id': prior_video_id,
                'youtube_url': f'https://www.youtube.com/watch?v={prior_video_id}',
                'privacy_status': 'private',
                'target_channel_id': target_channel_id,
                'connection_id': connection_id,
                'idempotent_replay': True,
            }
            mark_success(task_id, result)
            return result

        target_channel_id = str(reservation.get('target_channel_id') or '')
        connection_id = str(reservation.get('connection_id') or '')
        if not target_channel_id or not connection_id:
            raise RuntimeError('YouTube upload target is missing')
        credentials = load_credentials(
            target_channel_id,
            expected_connection_id=connection_id,
            refresh=True,
        )
        if not credentials:
            raise RuntimeError('YouTube account is not connected')
        # channels.list(mine=true) verifies that the refreshed credential still
        # resolves to a real channel before any upload side effect is possible.
        channel = refresh_channel_info(
            target_channel_id,
            credentials,
            expected_connection_id=connection_id,
        )

        set_stage(
            self,
            task_id,
            'youtube_download',
            12,
            'Final master depolamadan alınıyor.',
        )
        video_path = work / 'final.mp4'
        download_file(source_result['video_key'], video_path)

        metadata: dict = {}
        metadata_key = source_result.get('metadata_key')
        if metadata_key:
            try:
                metadata_path = work / 'metadata.json'
                download_file(metadata_key, metadata_path)
                value = json.loads(metadata_path.read_text(encoding='utf-8'))
                metadata = value if isinstance(value, dict) else {}
            except Exception:
                metadata = {}

        title = str(metadata.get('title') or source_result.get('title') or 'Video')[:100]
        description = str(metadata.get('description') or '')
        sources = metadata.get('sources') or []
        if isinstance(sources, list) and sources:
            source_lines = [str(item).strip() for item in sources[:20] if str(item).strip()]
            if source_lines:
                description += '\n\nKaynaklar:\n' + '\n'.join(source_lines)

        # Persist the side-effect boundary before videos.insert. If anything
        # after this point fails without a video ID, the reservation becomes
        # uncertain and cannot be retried into a duplicate.
        mark_upload_started(source_task_id, task_id)
        upload_started = True
        set_stage(
            self,
            task_id,
            'youtube_upload',
            35,
            'Video YouTube’a yükleniyor. İlk yükleme gizli tutuluyor.',
        )

        last_progress = -1

        def report_progress(fraction: float) -> None:
            nonlocal last_progress
            progress = min(80, max(35, 35 + int(float(fraction) * 45)))
            if progress <= last_progress:
                return
            last_progress = progress
            set_stage(
                self,
                task_id,
                'youtube_upload',
                progress,
                'Video YouTube’a gizli olarak yükleniyor.',
            )

        youtube_response = upload_video_with_credentials(
            credentials,
            str(video_path),
            title,
            description,
            privacy_status=privacy_status,
            progress_callback=report_progress,
        )
        video_id = str(youtube_response.get('id') or '').strip()
        if not video_id:
            raise RuntimeError('YouTube upload completed without a video ID')

        # This is the first write after the remote side effect and therefore is
        # intentionally done before optional captions or dashboard updates.
        mark_upload_completed(source_task_id, task_id, video_id)
        upload_completed = True

        caption_result = None
        caption_error_code = None
        caption_key = source_result.get('caption_key')
        language = str((source.get('spec') or {}).get('language') or 'tr')
        language = re.sub(r'[^A-Za-z0-9_-]+', '', language)[:12] or 'tr'
        if caption_key:
            set_stage(
                self,
                task_id,
                'youtube_caption',
                84,
                'Ayrı altyazı parçası YouTube’a ekleniyor.',
            )
            try:
                caption_path = work / f'captions.{language}.srt'
                download_file(caption_key, caption_path)
                caption_result = upload_caption_with_credentials(
                    credentials,
                    video_id,
                    str(caption_path),
                    language,
                    name=f'{language.upper()} captions',
                )
            except Exception as exc:
                caption_error_code = _safe_error_code(exc)

        uploaded_at = datetime.now(timezone.utc).isoformat()
        youtube_url = f'https://www.youtube.com/watch?v={video_id}'
        result = {
            'status': 'complete',
            'stage': 'complete',
            'progress': 100,
            'task_id': task_id,
            'source_task_id': source_task_id,
            'youtube_video_id': video_id,
            'youtube_url': youtube_url,
            'privacy_status': 'private',
            'uploaded_at': uploaded_at,
            'caption_uploaded': bool(caption_result),
            'caption_error_code': caption_error_code,
            'target_channel_id': target_channel_id,
            'connection_id': connection_id,
            'channel': channel,
        }
        source_result = dict(source_result)
        source_result['youtube'] = {
            'video_id': video_id,
            'url': youtube_url,
            'privacy_status': 'private',
            'uploaded_at': uploaded_at,
            'caption_uploaded': bool(caption_result),
            'caption_error_code': caption_error_code,
            'target_channel_id': target_channel_id,
            'connection_id': connection_id,
        }
        update_job(source_task_id, result=source_result)
        mark_success(task_id, result)
        return result
    except Exception as exc:
        error_code = _safe_error_code(exc)
        try:
            if upload_started and not upload_completed:
                mark_upload_uncertain(source_task_id, task_id, error_code)
            elif not upload_completed:
                mark_upload_preflight_failed(source_task_id, task_id, error_code)
        except Exception:
            pass
        safe_error = RuntimeError(
            'YouTube upload outcome is uncertain; automatic retry is blocked'
            if upload_started and not upload_completed
            else 'YouTube private upload preflight failed'
        )
        mark_failure(task_id, safe_error)
        raise safe_error from None
    finally:
        if lock_token:
            release_execution_lock(source_task_id, lock_token)
        shutil.rmtree(work, ignore_errors=True)
