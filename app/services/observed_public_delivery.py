"""Reconcile an approved upload that became public outside its old publisher.

Never uploads a video or changes visibility. One missing original-caption
insert is fenced before HTTP. All original registry documents are archived;
only a fresh owned public read and serving caption can update current status.
"""
from copy import deepcopy
from io import BytesIO
from pathlib import Path
import shutil
from uuid import uuid4

from app.services import blocked_public_recovery as core, studio_state as jobs
from app.services.youtube_publish_state import UPLOAD_PREFIX

PREFIX = 'youtube_studio:observed_public_delivery:v1:'


def _public(service, binding):
    response = service.channels().list(part='id', mine=True, maxResults=50).execute(num_retries=0)
    core._require(any(row.get('id') == binding['target_channel_id'] for row in response.get('items', [])))
    response = service.videos().list(part='snippet,status', id=binding['youtube_video_id'], maxResults=1).execute(num_retries=0)
    items = response.get('items') or []
    core._require(len(items) == 1 and items[0].get('id') == binding['youtube_video_id'])
    item = items[0]; status = item.get('status') or {}
    core._require(item.get('snippet', {}).get('channelId') == binding['target_channel_id']
        and status.get('privacyStatus') == 'public' and status.get('uploadStatus') == 'processed'
        and not status.get('publishAt') and status.get('containsSyntheticMedia') is not False,
        'observed_delivery_not_public_processed')
    return {'video_id': binding['youtube_video_id'], 'channel_id': binding['target_channel_id'],
        'privacy_status': 'public', 'upload_status': 'processed', 'observed_at': core._now(),
        'contains_synthetic_media': status.get('containsSyntheticMedia'),
        'response_sha256': core._digest(item)}


def _holds(client, source_id, snapshots):
    from app.services.source_publication_hold import HOLD_PREFIX
    for prefix in (HOLD_PREFIX, jobs.QUALITY_HOLD_PREFIX, jobs.RENDER_CANCELLATION_PREFIX,
                   jobs.EXTERNAL_EPISODE_LEAF_PREFIX, jobs.QUALITY_HOLD_JOB_FENCE_PREFIX):
        key = prefix + source_id
        snapshots[key] = client.get(key)
        core._require(snapshots[key] is None, 'observed_delivery_owner_or_quality_hold')


def _settle(client, snapshots, key, previous, record, records, observed, caption):
    """Archive original bytes before an atomic, explicitly attributed update."""
    core._require(record.get('caption_status') == 'awaiting_processing'
        and caption.get('id') == record.get('caption_id')
        and core._caption_identity(caption, record['youtube_video_id'], record['language'],
            record['caption_name'], serving=True))
    original_keys = (jobs.JOB_PREFIX + record['source_task_id'],
                     jobs.JOB_PREFIX + record['publish_task_id'],
                     UPLOAD_PREFIX + record['source_task_id'])
    core._require(record.get('original_records') == {k: snapshots[k] for k in original_keys},
                  'observed_delivery_original_records_changed')
    source, publisher, upload = (deepcopy(records[k]) for k in ('source', 'publisher', 'ledger'))
    timestamp = core._now()
    for result in (source['result']['youtube'], publisher['result']):
        result.update(privacy_status='public', release_status='public', release_error_code=None,
            caption_uploaded=True, caption_error_code=None,
            release_origin='observed_existing_public', public_observed_at=timestamp)
    upload.update(privacy_status='public', release_status='public', release_error_code=None,
        release_side_effect_possible=True, release_completed_at=timestamp,
        release_origin='observed_existing_public', public_observation_key=key)
    # The public effect is observed; no request to change visibility is claimed.
    record.update(status='reconciled', caption_status='serving', caption_proof=caption,
        public_proof=observed, visibility_requests=0, reconciled_at=timestamp)
    with client.pipeline() as pipe:
        pipe.watch(*snapshots, key, core.CHANNEL_INDEX_KEY)
        core._require(pipe.get(key) == previous and all(pipe.get(k) == v for k, v in snapshots.items())
            and pipe.sismember(core.CHANNEL_INDEX_KEY, record['target_channel_id']), 'observed_delivery_state_changed')
        pipe.multi(); pipe.set(key, core._json(record))
        pipe.set(jobs.JOB_PREFIX + record['source_task_id'], core._json(source))
        pipe.set(jobs.JOB_PREFIX + record['publish_task_id'], core._json(publisher))
        pipe.set(UPLOAD_PREFIX + record['source_task_id'], core._json(upload))
        core._require(pipe.execute() == [True, True, True, True], 'observed_delivery_commit_uncertain')
    return {'status': 'reconciled', 'video_id': record['youtube_video_id'],
        'original_records_archived': True, 'visibility_requests': 0, 'video_uploads': 0}


def reconcile(source_id, video_id, channel_id, revision):
    """Owner-authorized operator entry; repeated calls only reconcile receipts."""
    core._require(core._TASK.fullmatch(str(source_id)) and core._VIDEO.fullmatch(str(video_id)))
    client = core._redis(); key = PREFIX + source_id
    previous = client.get(key)
    if previous and core._object(previous).get('status') == 'reconciled':
        done = core._object(previous)
        core._require(done.get('source_task_id') == source_id and done.get('youtube_video_id') == video_id
            and done.get('target_channel_id') == channel_id and done.get('profile_revision') == revision,
            'observed_delivery_binding_changed')
        return {'status': 'already_reconciled', 'visibility_requests': 0}
    records, snapshots = core._read(client, source_id, channel_id)
    binding = core._validate(records, source_id, video_id, channel_id, revision)
    core._require(not records['source'].get('owner_cancellation')
        and not records['source'].get('publication_hold')
        and records['source']['result']['youtube']['thumbnail_uploaded'] is True
        and not records['source']['result']['youtube'].get('thumbnail_error_code')
        and records['publisher']['result'].get('thumbnail_uploaded') is True
        and not records['publisher']['result'].get('thumbnail_error_code')
        and records['source']['result']['youtube'].get('contains_synthetic_media') is True
        and records['publisher']['result'].get('contains_synthetic_media') is True
        # Frozen legacy plans can predate the publisher's disclosure fix.
        # Preserve the plan and both actual publisher fields verbatim.
        and type(records['ledger']['publish_plan'].get('contains_synthetic_media')) is bool)
    _holds(client, source_id, snapshots)
    service = lock = None
    work = Path('/tmp/youtube_asset_recovery') / source_id / str(uuid4())
    try:
        lock = core.acquire_execution_lock(source_id, binding['publish_task_id'])
        snapshots[core.EXECUTION_LOCK_PREFIX + source_id] = lock
        service = core._service(core._credentials(snapshots[core.CREDENTIAL_PREFIX + channel_id], binding))
        observed = _public(service, binding)
        if previous:
            record = core._object(previous)
            core._require(all(record.get(k) == v for k, v in binding.items()), 'observed_delivery_binding_changed')
        else:
            from app.services.publication_recovery_assets import prepare_publication_recovery_assets
            assets = prepare_publication_recovery_assets(records['source'], source_task_id=source_id, work_dir=work)
            caption_bytes = core._file_bytes(assets['caption'], work, 256 * 1024)
            name = binding['language'].upper() + ' captions ' + assets['caption']['sha256'][:12]
            tracks = core._caption_tracks(service, video_id)
            core._require(not any(core._caption_identity(t, video_id, binding['language'], name) for t in tracks),
                          'observed_delivery_existing_caption_unverified')
            record = {'version': 1, **binding, 'status': 'prepared', 'caption_status': 'reserved_before_http',
                'caption_name': name, 'caption_attempts': 1, 'caption_sha256': assets['caption']['sha256'],
                'public_before_caption': observed, 'created_at': core._now(),
                'original_records': {k: snapshots[k] for k in (jobs.JOB_PREFIX + source_id,
                    jobs.JOB_PREFIX + binding['publish_task_id'], UPLOAD_PREFIX + source_id)}}
            previous = core._commit(client, snapshots, channel_id, key, None, record)
            from googleapiclient.http import MediaIoBaseUpload
            try:
                # A contrary owner visibility change stops before the insert.
                _public(service, binding)
                with BytesIO(caption_bytes) as body:
                    result = service.captions().insert(part='snippet', body={'snippet': {
                        'videoId': video_id, 'language': binding['language'], 'name': name, 'isDraft': False}},
                        media_body=MediaIoBaseUpload(body, mimetype='application/octet-stream', resumable=False)).execute(num_retries=0)
                core._require(core._caption_identity(result, video_id, binding['language'], name))
                record.update(caption_status='awaiting_processing', caption_id=result['id'])
            except Exception as error:
                detail = core._safe_http_error(error)
                record.update(caption_status='rejected' if detail.get('status') == 'rejected' else 'uncertain', error=detail)
            previous = core._commit(client, snapshots, channel_id, key, previous, record)
        tracks = core._caption_tracks(service, video_id)
        matches = [t for t in tracks if core._caption_identity(t, video_id, binding['language'], record['caption_name'], serving=True)]
        core._require(len(matches) <= 1)
        if matches and record['caption_status'] in {'reserved_before_http', 'uncertain', 'awaiting_processing'}:
            record.update(caption_status='awaiting_processing', caption_id=matches[0]['id'])
            previous = core._commit(client, snapshots, channel_id, key, previous, record)
            return _settle(client, snapshots, key, previous, record, records, _public(service, binding), matches[0])
        return {'status': record['caption_status'], 'video_id': video_id, 'visibility_requests': 0}
    finally:
        if service is not None: service.close()
        if lock: core.release_execution_lock(source_id, lock)
        if work.exists(): shutil.rmtree(work)
