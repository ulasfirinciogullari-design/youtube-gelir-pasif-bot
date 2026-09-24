from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import re
import shutil
from uuid import uuid4

from app.celery_app import celery
from app.services.storage import download_file
from app.services.studio_state import (
    create_job,
    get_job,
    mark_failure,
    mark_success,
    merge_youtube_result_field,
    set_stage,
    update_job,
)
from app.services.youtube import (
    set_video_release_with_credentials,
    upload_caption_with_credentials,
    upload_thumbnail_with_credentials,
    upload_video_with_credentials,
)
from app.services.youtube_auth import (
    connection_status,
    load_credentials,
    refresh_channel_info,
)
from app.services.youtube_automation import (
    MetadataValidationError,
    automated_quality_approved,
    build_publish_plan,
    contains_synthetic_media,
    list_channel_profiles,
    select_channel_profile,
    validate_publish_plan,
)
from app.services.youtube_publish_state import (
    UploadAlreadyInProgress,
    UploadReservationError,
    acquire_execution_lock,
    get_upload_record,
    mark_release_blocked,
    mark_release_completed,
    mark_release_ready,
    mark_release_started,
    mark_release_uncertain,
    mark_upload_completed,
    mark_upload_enqueued,
    mark_upload_preflight_failed,
    mark_upload_started,
    mark_upload_uncertain,
    release_execution_lock,
    reserve_upload,
)


def _set_source_automation(source_task_id: str, **automation: object) -> None:
    merge_youtube_result_field(source_task_id, 'youtube_automation', automation)


def _editorial_candidate(source: dict) -> bool:
    if not isinstance(source, dict):
        return False
    spec = source.get('spec') if isinstance(source.get('spec'), dict) else {}
    result = source.get('result') if isinstance(source.get('result'), dict) else {}
    # Presence, not truthiness: a malformed external marker cannot fall back to
    # the historical private-upload path or to an automated approval flag.
    return bool(
        spec.get('workflow') == 'external_import'
        or result.get('quality_disposition') == 'editorial_review_pass'
        or any(key in result for key in (
            'external_descriptor_id', 'external_provenance',
            'editorial_review_id', 'editorial_review_sha256',
            'expected_video_sha256', 'expected_caption_sha256',
            'expected_video_size', 'expected_caption_size',
        ))
    )


def _publication_quality_approved(source: dict) -> bool:
    if not _editorial_candidate(source):
        return automated_quality_approved(source)
    # Keep external-review dependencies out of unchanged legacy publishing.
    from app.services.external_editorial_review import publication_quality_approved
    return publication_quality_approved(source)


def _verify_editorial_files(source: dict, receipt: dict, video_path: Path, caption_path: Path) -> None:
    from app.services.external_artifact_import import _fingerprint, MAX_VIDEO_BYTES, MAX_CAPTION_BYTES
    from app.services.external_editorial_review import EditorialReviewError

    result = source.get('result') or {}
    proof = receipt.get('server_proof') or {}
    for path, name, proof_key, maximum in (
        (video_path, 'video', 'video_sha256', MAX_VIDEO_BYTES),
        (caption_path, 'caption', 'captions_sha256', MAX_CAPTION_BYTES),
    ):
        digest, size = result.get(f'expected_{name}_sha256'), result.get(f'expected_{name}_size')
        if (type(digest) is not str or not re.fullmatch(r'[0-9a-f]{64}', digest)
                or type(size) is not int or not 0 < size <= maximum
                or proof.get(proof_key) != digest or path is None):
            raise EditorialReviewError('editorial_download_binding_invalid')
        # Reuse the importer's bounded, no-link, before/after identity check.
        # Neither caller flags nor the Storage key alone attest these bytes.
        _fingerprint(path, {'sha256': digest, 'size': size})


def _validate_editorial_downloads(source: dict, publish_plan: dict, video_path: Path, caption_path: Path) -> dict:
    from app.services.external_editorial_review import validate_editorial_publication, EditorialReviewError

    if not isinstance(source, dict) or not isinstance(publish_plan, dict):
        raise EditorialReviewError('editorial_publication_plan_required')
    receipt = validate_editorial_publication(source, frozen_plan=publish_plan)
    _verify_editorial_files(source, receipt, video_path, caption_path)
    return receipt


def queue_automatic_publish(source_task_id: str) -> dict:
    """Route one approved final and enqueue exactly one autonomous publish job."""
    source_task_id = str(source_task_id or '').strip()
    source = get_job(source_task_id)
    if not source or not _publication_quality_approved(source):
        return {'status': 'quality_blocked'}
    spec = source.get('spec') if isinstance(source.get('spec'), dict) else {}
    if spec.get('mode') == 'preview':
        return {'status': 'preview_blocked'}
    if spec.get('mode') != 'production' or spec.get('publish_after_render') is not True:
        return {'status': 'not_enabled'}
    if spec.get('production_scheduled') is True and not spec.get('production_channel_id'):
        return {'status': 'no_unique_route'}
    try:
        status = connection_status()
        connections = (
            status.get('connections')
            if isinstance(status.get('connections'), list)
            else []
        )
        connected_ids = {
            str(item.get('id') or '')
            for item in connections
            if isinstance(item, dict)
        }
        profile = select_channel_profile(
            source,
            list_channel_profiles(),
            connected_channel_ids=connected_ids,
        )
        if not profile:
            _set_source_automation(source_task_id, status='no_unique_route')
            return {'status': 'no_unique_route'}
        target_channel_id = str(profile.get('channel_id') or '')
        channel = next(
            (
                item
                for item in connections
                if isinstance(item, dict)
                and str(item.get('id') or '') == target_channel_id
            ),
            None,
        )
        connection_id = str((channel or {}).get('connection_id') or '')
        if not connection_id:
            _set_source_automation(source_task_id, status='connection_missing')
            return {'status': 'connection_missing'}
        expected_connection_id = str(spec.get('production_connection_id') or '')
        if (
            expected_connection_id and connection_id != expected_connection_id
            or spec.get('production_scheduled') is True and not expected_connection_id
        ):
            _set_source_automation(source_task_id, status='connection_changed')
            return {'status': 'connection_changed'}
        expected_revision = str(spec.get('production_profile_revision') or '')
        if expected_revision and expected_revision != str(profile.get('profile_revision') or ''):
            _set_source_automation(source_task_id, status='profile_changed')
            return {'status': 'profile_changed'}
        plan = build_publish_plan(source_task_id, source, profile)
    except Exception as exc:
        _set_source_automation(
            source_task_id,
            status='metadata_blocked',
            error_code=_safe_error_code(exc),
        )
        return {'status': 'metadata_blocked', 'error_code': _safe_error_code(exc)}

    from app.services import framecase_cadence, channel_cadence
    cadence = framecase_cadence if source.get('spec', {}).get('production_channel_id') == framecase_cadence.CHANNEL_ID else channel_cadence
    if not cadence.publication_slot(source):
        _set_source_automation(source_task_id, status='daily_limit_wait')
        return {'status': 'daily_limit_wait'}
    task_id = str(uuid4())
    try:
        reservation, created = reserve_upload(
            source_task_id,
            task_id,
            target_channel_id=target_channel_id,
            connection_id=connection_id,
            publish_plan=plan,
        )
    except Exception as exc:
        _set_source_automation(
            source_task_id,
            status='reservation_blocked',
            error_code=_safe_error_code(exc),
        )
        return {'status': 'reservation_blocked', 'error_code': _safe_error_code(exc)}
    if not created:
        return {
            'status': str(reservation.get('status') or 'already_reserved'),
            'publish_task_id': reservation.get('publish_task_id'),
        }

    create_job(
        task_id,
        {
            'topic': (source.get('spec') or {}).get('topic') or plan['title'],
            'source_task_id': source_task_id,
            'privacy_status': 'private',
            'mode': 'autonomous_publish',
            'target_channel_id': target_channel_id,
            'connection_id': connection_id,
            'profile_revision': plan.get('profile_revision'),
            'release_mode': plan.get('release_mode'),
            'series': plan.get('series'),
        },
        kind='publish',
        parent_id=source_task_id,
    )
    try:
        publish_video_pipeline.apply_async(
            args=(source_task_id, 'private'),
            task_id=task_id,
        )
    except Exception as exc:
        mark_upload_preflight_failed(source_task_id, task_id, 'queue_unavailable')
        mark_failure(task_id, 'YouTube autonomous upload could not be queued')
        _set_source_automation(
            source_task_id,
            status='queue_blocked',
            error_code=_safe_error_code(exc),
        )
        return {'status': 'queue_blocked', 'error_code': _safe_error_code(exc)}
    try:
        mark_upload_enqueued(source_task_id, task_id)
    except UploadReservationError:
        # The new worker may legitimately cross the reservation boundary before
        # this process records apply_async's success. Never move it backwards.
        pass
    _set_source_automation(
        source_task_id,
        status='queued',
        publish_task_id=task_id,
        target_channel_id=target_channel_id,
        profile_revision=plan.get('profile_revision'),
        release_mode=plan.get('release_mode'),
        series=plan.get('series'),
    )
    return {
        'status': 'queued',
        'publish_task_id': task_id,
        'target_channel_id': target_channel_id,
    }


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
    release_status = str(record.get('release_status') or 'private')
    privacy_status = str(record.get('privacy_status') or 'private')
    return {
        'status': 'complete',
        'stage': 'complete',
        'progress': 100,
        'task_id': task_id,
        'source_task_id': source_task_id,
        'youtube_video_id': video_id,
        'youtube_url': f'https://www.youtube.com/watch?v={video_id}',
        'privacy_status': privacy_status,
        'release_status': release_status,
        'scheduled_publish_at': record.get('scheduled_publish_at'),
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
    privacy_status: str = 'private',
    release_status: str = 'private',
    scheduled_publish_at: str | None = None,
    publish_plan: dict | None = None,
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
    attribution = {
        'video_id': video_id,
        'url': f'https://www.youtube.com/watch?v={video_id}',
        'privacy_status': str(privacy_status or 'private'),
        'release_status': str(release_status or 'private'),
        'scheduled_publish_at': scheduled_publish_at,
        'uploaded_at': (
            uploaded_at
            or youtube.get('uploaded_at')
            or datetime.now(timezone.utc).isoformat()
        ),
        'target_channel_id': str(target_channel_id or ''),
        'connection_id': str(connection_id or ''),
        'reconciled': True,
    }
    if isinstance(publish_plan, dict):
        attribution.update({
            'title': publish_plan.get('title'),
            'default_language': publish_plan.get('default_language'),
            'category_id': publish_plan.get('category_id'),
            'series': publish_plan.get('series'),
            'profile_revision': publish_plan.get('profile_revision'),
        })
    merge_youtube_result_field(source_task_id, 'youtube', attribution)


def _wake_after_public_success(source_task_id: str, task_id: str, result: dict) -> None:
    """Check stored public proof before a hint; never approve or dispatch here."""
    try:
        from app.services.production_events import request_production_tick

        if result.get('privacy_status') != 'public' or result.get('release_status') != 'public':
            return
        record = get_upload_record(source_task_id)
        source = get_job(source_task_id)
        publisher = get_job(task_id)
        plan = record.get('publish_plan') or {}
        source_result = source.get('result') or {}
        attribution = source_result.get('youtube') or {}
        video, channel, connection = (record.get(key) for key in (
            'youtube_video_id', 'target_channel_id', 'connection_id'))
        revision = plan.get('profile_revision')
        if not (
            source.get('task_id') == source_task_id and source.get('state') == 'SUCCESS'
            and publisher.get('task_id') == task_id and publisher.get('state') == 'SUCCESS'
            and publisher.get('kind') == 'publish' and publisher.get('parent_id') == source_task_id
            and publisher.get('spec', {}).get('source_task_id') == source_task_id
            and publisher.get('result') == result
            and source.get('kind') == 'render' and source_result.get('video_key')
            and source_result.get('caption_key') and source_result.get('manual_qa_required') is False
            and source_result.get('quality_disposition') in {'automated_qc_pass', 'editorial_review_pass'}
            and record.get('version') == 2 and record.get('status') == 'complete'
            and record.get('source_task_id') == source_task_id and record.get('publish_task_id') == task_id
            and record.get('side_effect_possible') is True and record.get('release_side_effect_possible') is True
            and record.get('requested_release_mode') == 'public' and not record.get('requested_publish_at')
            and type(record.get('release_completed_at')) is str and record['release_completed_at']
            and all(type(value) is str and value for value in (video, channel, connection, revision))
            and result.get('task_id') == task_id and result.get('source_task_id') == source_task_id
            and result.get('status') == 'complete' and result.get('youtube_video_id') == video
            and attribution.get('video_id') == video and attribution.get('profile_revision') == revision
            and plan.get('source_task_id') == source_task_id and plan.get('target_channel_id') == channel
            and plan.get('release_mode') == 'public' and not plan.get('publish_at')
            and type(plan.get('contains_synthetic_media')) is bool
            and type(attribution.get('contains_synthetic_media')) is bool
            and (plan['contains_synthetic_media'] is False or attribution['contains_synthetic_media'] is True)
            and attribution.get('caption_uploaded') is True
            and (plan.get('require_thumbnail') is not True or attribution.get('thumbnail_uploaded') is True)
            and all(row.get('privacy_status') == 'public' and row.get('release_status') == 'public'
                    and row.get('target_channel_id') == channel and row.get('connection_id') == connection
                    and all(row.get(key) in (None, '') for key in (
                        'scheduled_publish_at', 'release_error_code', 'caption_error_code', 'thumbnail_error_code'))
                    for row in (record, attribution, result))
            and all(key not in result or (type(result[key]) is type(expected) and result[key] == expected)
                    for key, expected in (('profile_revision', revision), ('caption_uploaded', True),
                        ('thumbnail_uploaded', attribution.get('thumbnail_uploaded')),
                        ('contains_synthetic_media', attribution['contains_synthetic_media'])))
        ):
            return
        from app.services import youtube_publish_state as publication_state
        if publication_state._redis().exists(publication_state._lock_key(source_task_id)):
            return  # An unlock outage/redelivery must leave the minute beat as fallback.
        # Duplicate hints are harmless: the existing Redis reservation remains
        # the only render authorizer, with two slots and one job per channel.
        request_production_tick()
    except Exception:
        pass  # The committed PUBLIC result must survive read/broker failures.


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
    release_started = False
    completed_result = None
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
                privacy_status=result['privacy_status'],
                release_status=result['release_status'],
                scheduled_publish_at=result.get('scheduled_publish_at'),
                publish_plan=(
                    reservation.get('publish_plan')
                    if isinstance(reservation.get('publish_plan'), dict)
                    else None
                ),
            )
            mark_success(task_id, result)
            completed_result = result
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
        editorial_candidate = _editorial_candidate(source)

        prior_youtube = source_result.get('youtube') if isinstance(source_result.get('youtube'), dict) else {}
        prior_video_id = str(prior_youtube.get('video_id') or '').strip()
        if prior_video_id:
            target_channel_id = str(reservation.get('target_channel_id') or '')
            connection_id = str(reservation.get('connection_id') or '')
            if not target_channel_id or not connection_id:
                raise RuntimeError('YouTube upload target is missing')
            prior_target = str(prior_youtube.get('target_channel_id') or '')
            prior_connection = str(prior_youtube.get('connection_id') or '')
            if prior_target and prior_target != target_channel_id:
                raise RuntimeError('YouTube upload attribution conflicts with reservation')
            if prior_connection and prior_connection != connection_id:
                raise RuntimeError('YouTube connection changed before reconciliation')
            mark_upload_started(source_task_id, task_id)
            upload_started = True
            mark_upload_completed(source_task_id, task_id, prior_video_id)
            upload_completed = True
            _reconcile_source_upload(
                source_task_id,
                prior_video_id,
                target_channel_id=target_channel_id,
                connection_id=connection_id,
                uploaded_at=prior_youtube.get('uploaded_at'),
                privacy_status=str(prior_youtube.get('privacy_status') or 'private'),
                release_status=str(prior_youtube.get('release_status') or 'private'),
                scheduled_publish_at=prior_youtube.get('scheduled_publish_at'),
                publish_plan=(
                    reservation.get('publish_plan')
                    if isinstance(reservation.get('publish_plan'), dict)
                    else None
                ),
            )
            result = {
                'status': 'complete',
                'stage': 'complete',
                'progress': 100,
                'task_id': task_id,
                'source_task_id': source_task_id,
                'youtube_video_id': prior_video_id,
                'youtube_url': f'https://www.youtube.com/watch?v={prior_video_id}',
                'privacy_status': str(prior_youtube.get('privacy_status') or 'private'),
                'release_status': str(prior_youtube.get('release_status') or 'private'),
                'scheduled_publish_at': prior_youtube.get('scheduled_publish_at'),
                'target_channel_id': target_channel_id,
                'connection_id': connection_id,
                'idempotent_replay': True,
            }
            mark_success(task_id, result)
            completed_result = result
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

        raw_plan = reservation.get('publish_plan')
        publish_plan = (
            validate_publish_plan(raw_plan)
            if isinstance(raw_plan, dict)
            else None
        )
        if publish_plan:
            editorial_candidate = editorial_candidate or _editorial_candidate(
                {'result': publish_plan.get('quality_snapshot')},
            )
        editorial_receipt = None
        caption_path = None
        if editorial_candidate:
            # Editorial acceptance is distinct from automated QA. Even a
            # private insert requires its real current receipt and frozen plan.
            if not source_result.get('caption_key'):
                raise RuntimeError('Editorial publication requires its reviewed captions')
            caption_path = work / 'captions.editorial.srt'
            download_file(source_result['caption_key'], caption_path)
            editorial_receipt = _validate_editorial_downloads(
                get_job(source_task_id), publish_plan, video_path, caption_path,
            )
        if publish_plan:
            if publish_plan['source_task_id'] != source_task_id:
                raise MetadataValidationError('Publish plan source changed')
            if publish_plan['target_channel_id'] != target_channel_id:
                raise MetadataValidationError('Publish plan target changed')
            from app.services.content_plan import check_publication
            check_publication(source, publish_plan)
            title = publish_plan['title']
            description = publish_plan['description']
            tags = publish_plan['tags']
            category_id = publish_plan['category_id']
            language = publish_plan['default_language']
            thumbnail_key = publish_plan.get('thumbnail_key')
            require_thumbnail = bool(publish_plan.get('require_thumbnail'))
            quality_snapshot = publish_plan.get('quality_snapshot')
            release_allowed = bool(
                editorial_receipt
                or (
                    not editorial_candidate
                    and automated_quality_approved(source)
                    and isinstance(quality_snapshot, dict)
                    and quality_snapshot.get('quality_disposition') == 'automated_qc_pass'
                    and quality_snapshot.get('manual_qa_required') is False
                )
            )
            requested_release_mode = str(publish_plan.get('release_mode') or 'private')
            release_mode = requested_release_mode if release_allowed else 'private'
            publish_at = publish_plan.get('publish_at') if release_mode == 'scheduled' else None
        else:
            # Older completed finals do not have an immutable automation plan.
            # They remain private and use only their stored, pre-upload metadata.
            title = str(metadata.get('title') or source_result.get('title') or 'Video')[:100]
            description = str(metadata.get('description') or '')
            sources = metadata.get('sources') or []
            if isinstance(sources, list) and sources:
                source_lines = [
                    str(item).strip()
                    for item in sources[:20]
                    if str(item).strip()
                ]
                if source_lines:
                    description += '\n\nKaynaklar:\n' + '\n'.join(source_lines)
            tags = []
            category_id = '28'
            language = str((source.get('spec') or {}).get('language') or 'tr')
            thumbnail_key = None
            require_thumbnail = False
            release_mode = 'private'
            publish_at = None
        language = re.sub(r'[^A-Za-z0-9_-]+', '', language)[:24] or 'tr'
        editorial_thumbnail = None
        editorial_thumbnail_path = None
        if editorial_receipt and require_thumbnail and not thumbnail_key:
            from app.services.editorial_thumbnail import prepare_editorial_thumbnail
            editorial_thumbnail_path = work / 'editorial-thumbnail.jpg'
            editorial_thumbnail = prepare_editorial_thumbnail(
                video_path, editorial_thumbnail_path, editorial_receipt,
            )
        # A legacy False can never overrule positive/unknown render provenance.
        # Do not derive this from paid-create usage: recovered AI costs zero.
        synthetic_disclosure = (
            bool(publish_plan and publish_plan['contains_synthetic_media'])
            or contains_synthetic_media(source)
        )
        if editorial_thumbnail:
            # Local extraction can take time: revocation during FFmpeg must
            # stop even the private insert, not merely the later public step.
            editorial_receipt = _validate_editorial_downloads(
                get_job(source_task_id), publish_plan, video_path, caption_path,
            )
            from app.services.editorial_thumbnail import validate_editorial_thumbnail
            validate_editorial_thumbnail(
                video_path, editorial_thumbnail_path, editorial_receipt, editorial_thumbnail,
            )

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
            tags=tags,
            category_id=category_id,
            default_language=language,
            contains_synthetic_media=synthetic_disclosure,
            progress_callback=report_progress,
        )
        video_id = str(youtube_response.get('id') or '').strip()
        if not video_id:
            raise RuntimeError('YouTube upload completed without a video ID')

        # This is the first write after the remote side effect and therefore is
        # intentionally done before optional captions or dashboard updates.
        if release_mode == 'private':
            mark_upload_completed(source_task_id, task_id, video_id)
        else:
            mark_upload_completed(
                source_task_id,
                task_id,
                video_id,
                release_mode=release_mode,
                publish_at=publish_at,
            )
        upload_completed = True

        caption_result = None
        caption_error_code = None
        caption_key = source_result.get('caption_key')
        caption_language = language[:12]
        if caption_key:
            set_stage(
                self,
                task_id,
                'youtube_caption',
                84,
                'Ayrı altyazı parçası YouTube’a ekleniyor.',
            )
            try:
                if editorial_receipt:
                    # Use the exact pre-insert SRT, never a second download.
                    _verify_editorial_files(source, editorial_receipt, video_path, caption_path)
                else:
                    caption_path = work / f'captions.{caption_language}.srt'
                    download_file(caption_key, caption_path)
                caption_result = upload_caption_with_credentials(
                    credentials,
                    video_id,
                    str(caption_path),
                    caption_language,
                    name=f'{caption_language.upper()} captions',
                )
            except Exception as exc:
                caption_error_code = _safe_error_code(exc)

        thumbnail_result = None
        thumbnail_error_code = None
        if thumbnail_key or editorial_thumbnail:
            set_stage(
                self,
                task_id,
                'youtube_thumbnail',
                88,
                'Özel küçük resim YouTube’a ekleniyor.',
            )
            try:
                if editorial_thumbnail:
                    from app.services.editorial_thumbnail import validate_editorial_thumbnail
                    validate_editorial_thumbnail(
                        video_path, editorial_thumbnail_path, editorial_receipt, editorial_thumbnail,
                    )
                    thumbnail_path = editorial_thumbnail_path
                else:
                    suffix = Path(str(thumbnail_key)).suffix.casefold()
                    suffix = suffix if suffix in {'.jpg', '.jpeg', '.png'} else '.jpg'
                    thumbnail_path = work / f'thumbnail{suffix}'
                    download_file(thumbnail_key, thumbnail_path)
                thumbnail_result = upload_thumbnail_with_credentials(
                    credentials,
                    video_id,
                    str(thumbnail_path),
                )
            except Exception as exc:
                thumbnail_error_code = _safe_error_code(exc)

        release_status = 'private'
        final_privacy_status = 'private'
        release_error_code = None
        scheduled_publish_at = None
        if release_mode in {'public', 'scheduled'}:
            editorial_error = None
            if 'content_plan_item_id' in (source.get('spec') or {}):
                from app.services.content_plan import check_publication
                try:
                    check_publication(get_job(source_task_id), publish_plan)
                except Exception:
                    editorial_error = 'content_plan_publication_changed'
            if editorial_candidate:
                try:
                    # Fresh stored source/receipt/OAuth/profile and the same
                    # local master/SRT must still match immediately pre-release.
                    current_receipt = _validate_editorial_downloads(
                        get_job(source_task_id), publish_plan, video_path, caption_path,
                    )
                    if editorial_thumbnail:
                        from app.services.editorial_thumbnail import validate_editorial_thumbnail
                        validate_editorial_thumbnail(
                            video_path, editorial_thumbnail_path, current_receipt, editorial_thumbnail,
                        )
                except Exception:
                    # videos.insert already returned a real ID. Keep that ID
                    # and report a blocked private release, not a failed upload.
                    editorial_error = 'editorial_review_changed_or_unavailable'
            asset_error = (
                editorial_error
                or ('synthetic_disclosure_unconfirmed' if (
                    synthetic_disclosure
                    and isinstance(youtube_response.get('status'), dict)
                    and youtube_response['status'].get('containsSyntheticMedia') is False
                ) else None)
                or (caption_error_code if not (
                    'content_plan_item_id' in (source.get('spec') or {})
                    and publish_plan.get('caption_required') is False
                    and editorial_error is None
                ) else None)
                or thumbnail_error_code
                or ('thumbnail_required' if require_thumbnail and not thumbnail_result else None)
            )
            if asset_error:
                release_error_code = str(asset_error)
                mark_release_blocked(source_task_id, task_id, release_error_code)
                release_status = 'blocked'
            else:
                mark_release_ready(source_task_id, task_id)
                set_stage(
                    self,
                    task_id,
                    'youtube_release',
                    94,
                    (
                        'Video güvenli yayın saatine planlanıyor.'
                        if release_mode == 'scheduled'
                        else 'Kalite onaylı video herkese açılıyor.'
                    ),
                )
                mark_release_started(source_task_id, task_id)
                release_started = True
                set_video_release_with_credentials(
                    credentials,
                    video_id,
                    release_mode,
                    publish_at=publish_at,
                    contains_synthetic_media=synthetic_disclosure,
                )
                mark_release_completed(
                    source_task_id,
                    task_id,
                    release_mode,
                    publish_at=publish_at,
                )
                release_started = False
                if release_mode == 'public':
                    from app.services import framecase_cadence, channel_cadence
                    cadence = framecase_cadence if source.get('spec', {}).get('production_channel_id') == framecase_cadence.CHANNEL_ID else channel_cadence
                    try:
                        cadence.publication_completed(source)
                    except Exception:
                        pass  # The pending claim counts conservatively until readback.
                release_status = release_mode
                final_privacy_status = (
                    'public' if release_mode == 'public' else 'private'
                )
                scheduled_publish_at = publish_at

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
            'privacy_status': final_privacy_status,
            'contains_synthetic_media': synthetic_disclosure,
            'release_status': release_status,
            'scheduled_publish_at': scheduled_publish_at,
            'release_error_code': release_error_code,
            'uploaded_at': uploaded_at,
            'caption_uploaded': bool(caption_result),
            'caption_error_code': caption_error_code,
            'thumbnail_uploaded': bool(thumbnail_result),
            'thumbnail_error_code': thumbnail_error_code,
            'target_channel_id': target_channel_id,
            'connection_id': connection_id,
            'channel': channel,
            'series': publish_plan.get('series') if publish_plan else None,
            'profile_revision': (
                publish_plan.get('profile_revision') if publish_plan else None
            ),
        }
        youtube_attribution = {
            'video_id': video_id,
            'url': youtube_url,
            'privacy_status': final_privacy_status,
            'contains_synthetic_media': synthetic_disclosure,
            'release_status': release_status,
            'scheduled_publish_at': scheduled_publish_at,
            'release_error_code': release_error_code,
            'uploaded_at': uploaded_at,
            'caption_uploaded': bool(caption_result),
            'caption_error_code': caption_error_code,
            'thumbnail_uploaded': bool(thumbnail_result),
            'thumbnail_error_code': thumbnail_error_code,
            'target_channel_id': target_channel_id,
            'connection_id': connection_id,
            'title': title,
            'default_language': language,
            'category_id': category_id,
            'series': publish_plan.get('series') if publish_plan else None,
            'profile_revision': (
                publish_plan.get('profile_revision') if publish_plan else None
            ),
        }
        merge_youtube_result_field(source_task_id, 'youtube', youtube_attribution)
        mark_success(task_id, result)
        completed_result = result
        return result
    except Exception as exc:
        error_code = _safe_error_code(exc)
        try:
            if release_started:
                mark_release_uncertain(source_task_id, task_id, error_code)
            elif upload_started and not upload_completed:
                mark_upload_uncertain(source_task_id, task_id, error_code)
            elif not upload_completed:
                mark_upload_preflight_failed(source_task_id, task_id, error_code)
        except Exception:
            pass
        safe_error = RuntimeError(
            'YouTube release outcome is uncertain; the private upload ID is preserved'
            if release_started
            else (
                'YouTube upload outcome is uncertain; automatic retry is blocked'
                if upload_started and not upload_completed
                else 'YouTube private upload preflight failed'
            )
        )
        mark_failure(task_id, safe_error)
        raise safe_error from None
    finally:
        if lock_token:
            release_execution_lock(source_task_id, lock_token)
        shutil.rmtree(work, ignore_errors=True)
        if completed_result is not None:
            _wake_after_public_success(source_task_id, task_id, completed_result)
