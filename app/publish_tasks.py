from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import shutil

from app.celery_app import celery
from app.services.storage import download_file
from app.services.studio_state import get_job, mark_failure, mark_success, set_stage, update_job
from app.services.youtube import upload_caption_with_credentials, upload_video_with_credentials
from app.services.youtube_auth import load_credentials, refresh_channel_info


@celery.task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=1)
def publish_video_pipeline(
    self,
    source_task_id: str,
    privacy_status: str = 'private',
):
    task_id = self.request.id
    # Initial uploads are always private. Public release is a separate,
    # deliberately confirmed future operation.
    privacy_status = 'private'
    work = Path('/tmp/youtube_publish') / task_id
    work.mkdir(parents=True, exist_ok=True)
    try:
        source = get_job(source_task_id)
        if not source:
            raise RuntimeError('Source Studio job was not found')
        source_result = source.get('result') or {}
        if not isinstance(source_result, dict) or not source_result.get('video_key'):
            raise RuntimeError('Source job has no completed video')

        set_stage(self, task_id, 'youtube_download', 12, 'Final master depolamadan alınıyor.')
        video_path = work / 'final.mp4'
        download_file(source_result['video_key'], video_path)

        metadata: dict = {}
        metadata_key = source_result.get('metadata_key')
        if metadata_key:
            try:
                metadata_path = work / 'metadata.json'
                download_file(metadata_key, metadata_path)
                metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
            except Exception:
                metadata = {}

        credentials = load_credentials()
        if not credentials:
            raise RuntimeError('YouTube account is not connected or its authorization expired')

        title = str(metadata.get('title') or source_result.get('title') or 'Video')[:100]
        description = str(metadata.get('description') or '')
        sources = metadata.get('sources') or []
        if sources:
            description += '\n\nKaynaklar:\n' + '\n'.join(str(item) for item in sources[:20])

        set_stage(self, task_id, 'youtube_upload', 35, 'Video YouTube’a yükleniyor. İlk yükleme gizli tutuluyor.')
        youtube_response = upload_video_with_credentials(
            credentials,
            str(video_path),
            title,
            description,
            privacy_status=privacy_status,
        )
        video_id = str(youtube_response.get('id') or '')
        if not video_id:
            raise RuntimeError('YouTube upload completed without a video ID')

        caption_result = None
        caption_error = None
        caption_key = source_result.get('caption_key')
        language = str((source.get('spec') or {}).get('language') or 'tr')
        if caption_key:
            set_stage(self, task_id, 'youtube_caption', 82, 'Ayrı altyazı parçası YouTube’a ekleniyor.')
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
                caption_error = str(exc)[:600]

        try:
            channel = refresh_channel_info(credentials)
        except Exception:
            channel = None

        published_at = datetime.now(timezone.utc).isoformat()
        result = {
            'status': 'complete',
            'stage': 'complete',
            'progress': 100,
            'task_id': task_id,
            'source_task_id': source_task_id,
            'youtube_video_id': video_id,
            'youtube_url': f'https://www.youtube.com/watch?v={video_id}',
            'privacy_status': privacy_status,
            'published_at': published_at,
            'caption_uploaded': bool(caption_result),
            'caption_error': caption_error,
            'channel': channel,
        }
        source_result = dict(source_result)
        source_result['youtube'] = {
            'video_id': video_id,
            'url': result['youtube_url'],
            'privacy_status': privacy_status,
            'published_at': published_at,
            'caption_uploaded': bool(caption_result),
            'caption_error': caption_error,
        }
        update_job(source_task_id, result=source_result)
        mark_success(task_id, result)
        return result
    except Exception as exc:
        mark_failure(task_id, exc)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
