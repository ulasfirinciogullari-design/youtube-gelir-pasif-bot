"""Quota backoff and verified release of an existing private upload.

No videos.insert is implemented here. A failed publisher remains immutable;
an independently verified public outcome receives a new publisher receipt.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from app.config import settings
from app.services import content_plan as plan, studio_state as jobs
from app.services import youtube_publish_state as upload, youtube_automation as automation
from app.services import youtube_auth as auth
from app.services import shorts_experiment as batch

PREFIX = 'youtube_studio:youtube_quota_release:v1:'
INDEX = PREFIX + 'sources'
PACIFIC = ZoneInfo('America/Los_Angeles')


def _now(now=None):
    return datetime.now(timezone.utc) if now is None else datetime.fromtimestamp(now, timezone.utc)


def reset_at(now=None):
    local = _now(now).astimezone(PACIFIC)
    midnight = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), PACIFIC)
    return (midnight + timedelta(minutes=5)).astimezone(timezone.utc).isoformat()


def quota_error(error):
    """A typed 403 rejection, not a substring in an arbitrary exception."""
    if getattr(getattr(error, 'resp', None), 'status', None) != 403:
        return None
    content = getattr(error, 'content', None)
    if not isinstance(content, bytes) or not 0 < len(content) <= 65536:
        return None
    try:
        body = json.loads(content)
        reasons = {e.get('reason') for e in body['error']['errors'] if isinstance(e, dict)}
        if body['error'].get('code') == 403 and reasons and reasons <= {'quotaExceeded', 'dailyLimitExceeded'}:
            return {'http_status': 403, 'reasons': sorted(reasons),
                    'response_sha256': hashlib.sha256(content).hexdigest()}
    except (ValueError, KeyError, TypeError):
        pass
    return None


def _wait_key():
    return PREFIX + 'project_wait:' + hashlib.sha256(settings.google_client_id.encode()).hexdigest()


def observe_quota(error, *, client=None, now=None):
    evidence = quota_error(error)
    if evidence is None:
        return None
    client = client or plan._client()
    record = {**evidence, 'observed_at': _now(now).isoformat(), 'retry_at': reset_at(now)}
    client.set(_wait_key(), plan._raw(record))
    return record


def waiting(*, client=None, now=None):
    client = client or plan._client()
    raw = client.get(_wait_key())
    if raw is None:
        return None
    record = plan._object(raw)
    return record if datetime.fromisoformat(record['retry_at']) > _now(now) else None


def _read(client, source_id):
    snapshots = {}
    def read(key):
        raw = client.get(key); snapshots[key] = raw
        return plan._object(raw)
    source = read(jobs.JOB_PREFIX + source_id)
    ledger = read(upload.UPLOAD_PREFIX + source_id)
    publisher = read(jobs.JOB_PREFIX + ledger['publish_task_id'])
    channel_id = source['spec']['production_channel_id']
    profile = read(automation.PROFILE_PREFIX + channel_id)
    channel = read(auth.CHANNEL_PREFIX + channel_id)
    for key in (auth.CREDENTIAL_PREFIX + channel_id, auth.AUTH_EPOCH_KEY):
        snapshots[key] = client.get(key)
    from app.services.source_publication_hold import HOLD_PREFIX
    for key in (HOLD_PREFIX + source_id, jobs.RENDER_CANCELLATION_PREFIX + source_id,
                jobs.QUALITY_HOLD_JOB_FENCE_PREFIX + source_id,
                *jobs.retained_delivery_fence_keys(source_id)):
        snapshots[key] = client.get(key)
        plan._require(snapshots[key] is None, 'quota_release_held')
    plan._require(channel_id in batch.COUNTS
        and automation.automated_quality_approved(source) and not source.get('retry_child_task_id')
        and source['spec'].get('format') == 'shorts' and source['spec'].get('publish_after_render') is True
        and profile.get('production_enabled') is True and profile.get('auto_publish') is True
        and profile.get('release_mode') == 'public'
        and profile.get('profile_revision') == source['spec'].get('production_profile_revision')
        and channel.get('id') == channel_id and channel.get('requires_reconnect') is not True
        and channel.get('connection_id') == source['spec'].get('production_connection_id')
        and snapshots[auth.CREDENTIAL_PREFIX + channel_id]
        and client.sismember(auth.CHANNEL_INDEX_KEY, channel_id), 'quota_release_binding_changed')
    frozen = automation.validate_publish_plan(ledger['publish_plan'])
    plan._require(ledger.get('source_task_id') == source_id and ledger.get('status') == 'complete'
        and ledger.get('requested_release_mode') == 'public' and not ledger.get('requested_publish_at')
        and ledger.get('target_channel_id') == channel_id and ledger.get('connection_id') == channel['connection_id']
        and re.fullmatch('[A-Za-z0-9_-]{11}', str(ledger.get('youtube_video_id', '')))
        and frozen['source_task_id'] == source_id and frozen['target_channel_id'] == channel_id
        and frozen['profile_revision'] == profile['profile_revision'] and frozen['release_mode'] == 'public'
        and frozen.get('caption_required') is False
        and frozen['quality_snapshot'] == {'quality_disposition': 'automated_qc_pass', 'manual_qa_required': False},
        'quota_release_plan_changed')
    plan._require(publisher.get('task_id') == ledger['publish_task_id'] and publisher.get('kind') == 'publish'
        and publisher.get('parent_id') == source_id, 'quota_release_publisher_changed')
    item_id = source['spec'].get('content_plan_item_id')
    if item_id:
        document = read(plan.PLAN_PREFIX + channel_id)
        plan._require(document.get('enabled') is True, 'quota_release_plan_paused')
        read(plan.DISPATCH_PREFIX + item_id)
        for entry in document['items']:
            if entry['id'] == item_id:
                break
            key = plan.COMPLETION_PREFIX + entry['id']; snapshots[key] = client.get(key)
    return source, ledger, publisher, snapshots


def _render_digest(source):
    return plan._sha({k: v for k, v in source['result'].items() if k not in {'youtube', 'youtube_automation'}})


def register(source_id, evidence, *, expected_ledger_sha256=None, client=None, now=None):
    """Capture a known quota rejection; old generic failures require a bound audit.

    The migration caller supplies the reviewed log digest and exact ledger hash.
    Future failures supply the captured typed Google response digest instead.
    """
    client = client or plan._client()
    plan._id(source_id)
    plan._require(evidence.get('http_status') == 403
        and evidence.get('reasons') and set(evidence['reasons']) <= {'quotaExceeded', 'dailyLimitExceeded'}
        and re.fullmatch('[0-9a-f]{64}', str(evidence.get('response_sha256', ''))))
    source, ledger, publisher, snapshots = _read(client, source_id)
    plan._require(ledger.get('release_status') in {'uncertain', 'blocked'}
        and ledger.get('release_error_code') == 'HttpError_403'
        and publisher.get('state') == ('FAILURE' if ledger['release_status'] == 'uncertain' else 'SUCCESS'),
        'quota_release_not_rejected')
    if expected_ledger_sha256 is not None:
        plan._require(plan._sha(ledger) == expected_ledger_sha256, 'quota_release_state_changed')
    key = PREFIX + source_id
    record = {'version': 1, 'source_task_id': source_id, 'video_id': ledger['youtube_video_id'],
        'channel_id': ledger['target_channel_id'], 'state': 'waiting', 'evidence': evidence,
        'retry_at': reset_at(now), 'created_at': _now(now).isoformat(), 'attempts': 0,
        'original_ledger': ledger, 'original_publisher': publisher,
        'source_spec_sha256': plan._sha(source['spec']), 'render_result_sha256': _render_digest(source)}
    with client.pipeline() as pipe:
        pipe.watch(key, *snapshots)
        if pipe.get(key):
            return plan._object(pipe.get(key))
        plan._require(all(pipe.get(k) == v for k, v in snapshots.items()), 'quota_release_state_changed')
        pipe.multi(); pipe.set(key, plan._raw(record), nx=True); pipe.sadd(INDEX, source_id)
        pipe.set(_wait_key(), plan._raw({'retry_at': record['retry_at'], 'observed_at': record['created_at'], **evidence}))
        plan._require(pipe.execute()[0] is True)
    return record


def _remote(service, record):
    response = service.videos().list(part='snippet,status', id=record['video_id'], maxResults=1).execute(num_retries=0)
    items = response.get('items') if isinstance(response, dict) else None
    plan._require(isinstance(items, list) and len(items) == 1)
    item = items[0]
    plan._require(item.get('id') == record['video_id'] and item.get('snippet', {}).get('channelId') == record['channel_id']
        and item.get('status', {}).get('uploadStatus') == 'processed' and not item['status'].get('publishAt'),
        'quota_release_remote_changed')
    return item['status']


def _cas(client, key, previous, record, snapshots):
    with client.pipeline() as pipe:
        pipe.watch(key, *snapshots)
        plan._require(pipe.get(key) == previous and all(pipe.get(k) == v for k,v in snapshots.items()),
                      'quota_release_state_changed')
        pipe.multi(); pipe.set(key, plan._raw(record)); plan._require(pipe.execute() == [True])
    return plan._raw(record)


def _thumbnail(service, source, record):
    """Reapply the authored thumbnail to the same ID, with a positive receipt."""
    from pathlib import Path
    from tempfile import TemporaryDirectory
    from app.services.storage import download_file
    from app.services.blocked_public_recovery import _thumbnail_receipt
    from googleapiclient.http import MediaFileUpload
    frozen = record['original_ledger']['publish_plan']
    if not frozen.get('require_thumbnail') and not frozen.get('thumbnail_key'):
        return None
    key = frozen.get('thumbnail_key')
    plan._require(key and key == source['result'].get('thumbnail_key'), 'quota_release_thumbnail_missing')
    suffix = Path(key).suffix.lower()
    plan._require(suffix in {'.png', '.jpg', '.jpeg'})
    with TemporaryDirectory(prefix='youtube-quota-thumbnail-') as work:
        path = Path(work) / ('thumbnail' + suffix)
        download_file(key, path)
        plan._require(path.is_file() and 0 < path.stat().st_size <= 2 * 1024 * 1024)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        response = service.thumbnails().set(videoId=record['video_id'], media_body=MediaFileUpload(
            str(path), mimetype='image/png' if suffix == '.png' else 'image/jpeg', resumable=False)).execute(num_retries=0)
        return {'asset_sha256': digest, 'response': _thumbnail_receipt(response)}


def resume(source_id, *, client=None, now=None):
    client = client or plan._client(); key = PREFIX + source_id
    raw = client.get(key); record = plan._object(raw)
    if record['state'] == 'public':
        return {'status': 'already_public'}
    if waiting(client=client, now=now) or datetime.fromisoformat(record['retry_at']) > _now(now):
        return {'status': 'youtube_quota_wait', 'retry_at': record['retry_at']}
    token = None
    try:
        source, ledger, publisher, snapshots = _read(client, source_id)
        plan._require(ledger == record['original_ledger'] and publisher == record['original_publisher']
            and plan._sha(source['spec']) == record['source_spec_sha256']
            and _render_digest(source) == record['render_result_sha256'], 'quota_release_state_changed')
        plan.check_publication(source, ledger['publish_plan'])
        token = upload.acquire_execution_lock(source_id, publisher['task_id'])
        snapshots[upload.EXECUTION_LOCK_PREFIX + source_id] = token
        from app.services.blocked_public_recovery import _credentials, _service
        binding = {'target_channel_id': record['channel_id'], 'connection_id': ledger['connection_id']}
        service = _service(_credentials(snapshots[auth.CREDENTIAL_PREFIX + record['channel_id']], binding))
        remote = _remote(service, record)
        plan._require(remote.get('privacyStatus') in {'private', 'public'}, 'quota_release_remote_changed')
        if record['state'] == 'waiting':
            # A crash or ambiguous network reply can only be reconciled by a
            # positive public readback. It cannot authorize another mutation.
            plan._require(record['state'] == 'waiting' and record['attempts'] < 2,
                          'quota_release_outcome_uncertain')
            record.update(state='releasing', attempts=record['attempts'] + 1, started_at=_now(now).isoformat(),
                          prior_privacy=remote['privacyStatus'], update_response_verified=False)
            raw = _cas(client, key, raw, record, snapshots)
            try:
                if (ledger['publish_plan'].get('require_thumbnail') or ledger['publish_plan'].get('thumbnail_key')) and not record.get('thumbnail_proof'):
                    record['thumbnail_proof'] = _thumbnail(service, source, record)
                    raw = _cas(client, key, raw, record, snapshots)
                response = service.videos().update(part='status', body={'id': record['video_id'], 'status': {
                    'privacyStatus': 'public', 'selfDeclaredMadeForKids': False,
                    'containsSyntheticMedia': True}}).execute(num_retries=0)
                plan._require(isinstance(response, dict) and response.get('id') == record['video_id']
                    and response.get('status', {}).get('privacyStatus') == 'public'
                    and ('containsSyntheticMedia' not in response['status']
                         or response['status']['containsSyntheticMedia'] is True), 'quota_release_response_unverified')
                record['update_response_verified'] = True
                raw = _cas(client, key, raw, record, snapshots)
            except Exception as error:
                evidence = observe_quota(error, client=client, now=now)
                if evidence:
                    record.update(state='waiting', retry_at=evidence['retry_at'], last_rejection=evidence)
                    _cas(client, key, raw, record, snapshots)
                    return {'status': 'youtube_quota_wait', 'retry_at': record['retry_at']}
                raise
            remote = _remote(service, record)
        plan._require(remote.get('privacyStatus') == 'public'
            and (remote.get('containsSyntheticMedia') is True or ('containsSyntheticMedia' not in remote
                 and record.get('update_response_verified') is True))
            and (not (ledger['publish_plan'].get('require_thumbnail') or ledger['publish_plan'].get('thumbnail_key'))
                 or record.get('thumbnail_proof')),
                      'quota_release_public_unverified')
        stamp = _now(now).isoformat(); task = str(uuid5(NAMESPACE_URL, PREFIX + source_id))
        old_publisher = publisher['task_id']; frozen = ledger['publish_plan']
        result = {'status': 'complete', 'stage': 'complete', 'progress': 100, 'task_id': task,
            'source_task_id': source_id, 'youtube_video_id': record['video_id'],
            'youtube_url': 'https://www.youtube.com/watch?v=' + record['video_id'],
            'privacy_status': 'public', 'release_status': 'public', 'scheduled_publish_at': None,
            'contains_synthetic_media': True, 'release_error_code': None,
            'target_channel_id': record['channel_id'], 'connection_id': ledger['connection_id'],
            'profile_revision': frozen['profile_revision'], 'series': frozen['series'],
            'caption_uploaded': bool((publisher.get('result') or {}).get('caption_uploaded')),
            'thumbnail_uploaded': bool(record.get('thumbnail_proof')),
            'caption_error_code': 'prior_asset_status_unverified', 'thumbnail_error_code': None,
            'uploaded_at': ledger.get('completed_at'), 'verified_public_at': stamp,
            'quota_recovery_of': old_publisher}
        new_publisher = {'task_id': task, 'kind': 'publish', 'parent_id': source_id, 'state': 'SUCCESS',
            'stage': 'complete', 'progress': 100, 'spec': deepcopy(publisher['spec']), 'result': result,
            'created_at': stamp, 'updated_at': stamp, 'created_ts': _now(now).timestamp(), 'error': None}
        ledger = {**ledger, 'publish_task_id': task, 'release_status': 'public', 'privacy_status': 'public',
            'release_completed_at': stamp, 'release_error_code': None, 'quota_recovery_of': old_publisher,
            'release_side_effect_possible': True, 'updated_at': stamp}
        source = deepcopy(source)
        source['result']['youtube'] = {**result, 'video_id': record['video_id'], 'url': result['youtube_url'],
            'title': frozen['title'], 'default_language': frozen['default_language']}
        source['result']['youtube_automation'] = {'status': 'complete', 'publish_task_id': task,
            'target_channel_id': record['channel_id'], 'profile_revision': frozen['profile_revision'], 'release_mode': 'public'}
        source['updated_at'] = stamp
        record.update(state='public', verified_public_at=stamp, publisher_task_id=task)
        with client.pipeline() as pipe:
            pipe.watch(key, *snapshots, jobs.JOB_PREFIX + task, auth.CHANNEL_INDEX_KEY)
            plan._require(pipe.get(key) == raw and all(pipe.get(k) == v for k,v in snapshots.items())
                and not pipe.exists(jobs.JOB_PREFIX + task)
                and pipe.sismember(auth.CHANNEL_INDEX_KEY, record['channel_id']), 'quota_release_state_changed')
            pipe.multi(); pipe.set(key, plan._raw(record)); pipe.set(jobs.JOB_PREFIX + task, plan._raw(new_publisher), nx=True)
            pipe.zadd(jobs.JOB_INDEX, {task: _now(now).timestamp()})
            pipe.set(upload.UPLOAD_PREFIX + source_id, plan._raw(ledger), ex=upload.UPLOAD_RECORD_TTL_SECONDS)
            pipe.set(jobs.JOB_PREFIX + source_id, plan._raw(source), ex=jobs.JOB_TTL_SECONDS)
            plan._require(pipe.execute()[1] is True)
        from app.services.channel_cadence import publication_completed
        publication_completed(source, client=client, now=now)
        return {'status': 'public', 'video_id': record['video_id']}
    finally:
        if token:
            upload.release_execution_lock(source_id, token)


def maintain(*, client=None):
    client = client or plan._client()
    if waiting(client=client):
        return {'status': 'youtube_quota_wait'}
    sources = client.smembers(INDEX)
    plan._require(len(sources) <= 100)
    for source_id in sorted(sources):
        key = PREFIX + source_id; original = client.get(key); row = plan._object(original)
        if (row['state'] != 'public' and datetime.fromisoformat(row['retry_at']) <= _now()
            and (not row.get('next_check_at') or datetime.fromisoformat(row['next_check_at']) <= _now())):
            # One bounded operation per minute; the next tick handles another
            # channel. No unknown transport outcome ever re-enters insert.
            try:
                return resume(source_id, client=client)
            except Exception as error:
                # A held channel cannot starve another channel's valid release.
                current = client.get(key)
                record = plan._object(current)
                if record['state'] != 'public':
                    record['next_check_at'] = (_now() + timedelta(minutes=5)).isoformat()
                    _cas(client, key, current, record, {})
                return {'status': 'waiting', 'reason': str(error) if isinstance(error, plan.ContentPlanError) else type(error).__name__}
    return {'status': 'idle'}
