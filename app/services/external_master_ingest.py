"""Import existing media for private Studio review, never approve or publish.

A durable reservation fences every possible Storage write. Unknown outcomes
are not retried. No existing job, retry claim, series cursor or QA is amended.
"""
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
from uuid import UUID, uuid4, uuid5

import redis

from app.config import settings
from app.services import external_artifact_import as artifact, storage
from app.services.studio_state import JOB_PREFIX, JOB_INDEX, JOB_TTL_SECONDS
from app.services.youtube_auth import CHANNEL_PREFIX, CHANNEL_INDEX_KEY, CREDENTIAL_PREFIX, AUTH_EPOCH_KEY
from app.services.youtube_automation import PROFILE_PREFIX


RESERVATION_PREFIX = 'youtube_studio:external_master_ingest:v1:'
_NAMESPACE = UUID('dc0651aa-1c49-4c76-93e2-88dd6d8e6737')
_ID = re.compile(r'[A-Za-z0-9_-]{8,128}')


class ExternalMasterIngestError(ValueError):
    """Closed codes only; no provider response, path or credential values."""


def _require(value, code):
    if not value:
        raise ExternalMasterIngestError(code)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _redis():
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


def _object(raw):
    _require(isinstance(raw, str) and 0 < len(raw.encode()) <= 512 * 1024,
             'external_ingest_record_invalid')
    value = artifact._object(raw, limit=512 * 1024)
    return value


def _snapshot(reader, channel_id, connection_id, revision, language):
    keys = (CHANNEL_PREFIX + channel_id, CREDENTIAL_PREFIX + channel_id,
            PROFILE_PREFIX + channel_id, AUTH_EPOCH_KEY)
    raw = {key: reader.get(key) for key in keys}
    channel, profile = _object(raw[keys[0]]), _object(raw[keys[2]])
    _require(isinstance(raw[keys[1]], str) and bool(raw[keys[1]])
             and reader.sismember(CHANNEL_INDEX_KEY, channel_id), 'external_ingest_connection_missing')
    _require(channel.get('id') == channel_id and channel.get('connection_id') == connection_id
             and (channel.get('requires_reconnect') is None or channel.get('requires_reconnect') is False),
             'external_ingest_connection_changed')
    _require(profile.get('channel_id') == channel_id and profile.get('profile_revision') == revision,
             'external_ingest_profile_changed')
    languages = profile.get('languages')
    if languages is None:
        languages = [profile.get('default_language')]
    _require(type(languages) is list and 1 <= len(languages) <= 5
             and all(type(item) is str for item in languages) and language in languages,
             'external_ingest_language_not_allowed')
    return raw


def _response(record):
    return {'task_id': record['task_id'], 'descriptor_id': record['descriptor_id'],
            'target_channel_id': record['target_channel_id'], 'status': 'imported_unreviewed',
            'quality_disposition': 'manual_qa_preview', 'manual_qa_required': True,
            'qa_approved': False, 'publish_eligible': False, 'media_generation_authorized': False,
            'studio_url': '/studio/job/' + record['task_id']}


def _binding_digest(snapshot, channel_id):
    """Bind authority, not incidental channel counts or verification timestamps.

The whole current profile, credential ciphertext and authorization epoch stay
bound. Reservation/finalization still WATCH the exact raw records during writes.
"""
    channel = _object(snapshot[CHANNEL_PREFIX + channel_id])
    return _digest({'channel': {'id': channel.get('id'),
        'connection_id': channel.get('connection_id'),
        'requires_reconnect': channel.get('requires_reconnect') is True},
        'profile': _object(snapshot[PROFILE_PREFIX + channel_id]),
        'credential': snapshot[CREDENTIAL_PREFIX + channel_id],
        'authorization_epoch': snapshot[AUTH_EPOCH_KEY]})


def list_external_master_targets():
    """Bounded current display metadata only; POST performs its own checks."""
    try:
        client = _redis()
        _require(client.scard(CHANNEL_INDEX_KEY) <= 10, 'external_ingest_channel_index_invalid')
        channel_ids = client.smembers(CHANNEL_INDEX_KEY)
        _require(len(channel_ids) <= 10, 'external_ingest_channel_index_invalid')
        targets = []
        for channel_id in sorted(channel_ids):
            try:
                _require(type(channel_id) is str and _ID.fullmatch(channel_id), 'external_ingest_binding_invalid')
                channel = _object(client.get(CHANNEL_PREFIX + channel_id))
                profile = _object(client.get(PROFILE_PREFIX + channel_id))
                connection, revision = channel.get('connection_id'), profile.get('profile_revision')
                _require(all(type(value) is str and _ID.fullmatch(value) for value in (connection, revision)),
                         'external_ingest_binding_invalid')
                languages = profile.get('languages') or [profile.get('default_language')]
                _require(type(languages) is list and languages, 'external_ingest_language_not_allowed')
                _snapshot(client, channel_id, connection, revision, languages[0])
                title = artifact._text(channel.get('title') or channel_id, 140)
                targets.append({'target_channel_id': channel_id, 'title': title,
                    'languages': languages, 'expected_connection_id': connection,
                    'expected_profile_revision': revision})
            except (ExternalMasterIngestError, artifact.ExternalArtifactValidationError):
                continue  # An unlinked, incomplete or stale target is not selectable.
        return {'targets': targets}
    except ExternalMasterIngestError:
        raise
    except Exception:
        raise ExternalMasterIngestError('external_ingest_targets_unavailable') from None


def _reserve(client, descriptor, channel_id, connection_id, revision):
    task_id = str(uuid5(_NAMESPACE, descriptor['descriptor_id'] + ':' + channel_id))
    key, job_key = RESERVATION_PREFIX + task_id, JOB_PREFIX + task_id
    watched = [key, job_key, CHANNEL_INDEX_KEY, CHANNEL_PREFIX + channel_id,
               CREDENTIAL_PREFIX + channel_id, PROFILE_PREFIX + channel_id, AUTH_EPOCH_KEY]
    for _ in range(3):
        try:
            with client.pipeline() as pipe:
                pipe.watch(*watched)
                snapshot = _snapshot(pipe, channel_id, connection_id, revision, descriptor['manifest']['language'])
                previous = pipe.get(key)
                if previous is not None:
                    record = _object(previous)
                    _require(record.get('task_id') == task_id
                             and record.get('descriptor_id') == descriptor['descriptor_id']
                             and record.get('target_channel_id') == channel_id
                             and record.get('connection_id') == connection_id
                             and record.get('profile_revision') == revision
                             and record.get('binding_sha256') == _binding_digest(snapshot, channel_id),
                             'external_ingest_reservation_conflict')
                    _require(record.get('status') == 'complete', 'external_ingest_busy_or_uncertain')
                    job = _object(pipe.get(job_key))
                    _require(job.get('task_id') == task_id and job.get('kind') == 'render'
                             and job.get('state') == 'SUCCESS'
                             and record.get('job_sha256') == _digest(job)
                             and (job.get('result') or {}).get('external_descriptor_id') == descriptor['descriptor_id']
                             and (job.get('spec') or {}).get('production_channel_id') == channel_id
                             and (job.get('spec') or {}).get('production_connection_id') == connection_id
                             and (job.get('spec') or {}).get('production_profile_revision') == revision,
                             'external_ingest_existing_job_conflict')
                    return record, snapshot, True
                _require(pipe.get(job_key) is None, 'external_ingest_existing_job_conflict')
                record = {'version': 1, 'task_id': task_id, 'descriptor_id': descriptor['descriptor_id'],
                          'target_channel_id': channel_id, 'connection_id': connection_id,
                          'profile_revision': revision, 'binding_sha256': _binding_digest(snapshot, channel_id),
                          'status': 'reserved', 'owner': uuid4().hex,
                          'created_at': datetime.now(timezone.utc).isoformat()}
                pipe.multi()
                pipe.set(key, _json(record))  # No expiry can silently reopen an uncertain import.
                pipe.execute()
                return record, snapshot, False
        except redis.WatchError:
            continue
    raise ExternalMasterIngestError('external_ingest_reservation_changed')


def _put(client, key, body, size, checksum, mime):
    try:
        client.put_object(Bucket=storage.settings.bucket, Key=key, Body=body,
                          ContentLength=size, ContentType=mime, CacheControl='private, no-store',
                          Metadata={'sha256': checksum}, IfNoneMatch='*')
    except Exception as exc:
        response = getattr(exc, 'response', {})
        code = (response.get('Error') or {}).get('Code') if isinstance(response, dict) else None
        if str(code) not in {'412', 'PreconditionFailed'}:
            raise ExternalMasterIngestError('external_ingest_storage_uncertain') from None
        existing = client.head_object(Bucket=storage.settings.bucket, Key=key)
        _require(existing.get('ContentLength') == size and existing.get('ContentType') == mime
                 and (existing.get('Metadata') or {}).get('sha256') == checksum,
                 'external_ingest_storage_conflict')


def _uncertain(client, record):
    try:
        with client.pipeline() as pipe:
            key = RESERVATION_PREFIX + record['task_id']
            pipe.watch(key)
            current = _object(pipe.get(key))
            if current.get('owner') == record['owner'] and current.get('status') == 'reserved':
                pipe.multi()
                pipe.set(key, _json({**current, 'status': 'uncertain'}))
                pipe.execute()
    except Exception:
        pass  # The durable reserved record already fences every later retry.


def ingest_external_master(staging_root, video_path, captions_path, manifest, *,
                           target_channel_id, expected_connection_id, expected_profile_revision):
    for value in (target_channel_id, expected_connection_id, expected_profile_revision):
        _require(type(value) is str and _ID.fullmatch(value), 'external_ingest_binding_invalid')
    record = None
    try:
        descriptor = artifact.validate_staged_external_artifact(staging_root, video_path, captions_path, manifest)
        client = _redis()
        record, snapshot, existing = _reserve(client, descriptor, target_channel_id,
                                               expected_connection_id, expected_profile_revision)
        if existing:
            return {**_response(record), 'idempotent_replay': True}
        value, task_id = descriptor['manifest'], record['task_id']
        s3 = storage._client()
        stored = {}
        for name, suffix in (('video', 'master.mp4'), ('captions', 'captions.srt')):
            declaration = value['files'][name]
            path = Path(descriptor['files'][name]['local_path'])
            _, identity = artifact._fingerprint(path, declaration)
            key = 'external-masters/v1/' + declaration['sha256'] + '/' + suffix
            with path.open('rb') as body:
                _put(s3, key, body, declaration['size'], declaration['sha256'], declaration['content_type'])
            _require(artifact._fingerprint(path, declaration)[1] == identity, 'external_ingest_file_changed')
            stored[name] = key
        metadata = {key: value for key, value in descriptor.items() if key != 'files'}
        metadata_bytes = _json(metadata).encode('utf-8')
        metadata_sha = hashlib.sha256(metadata_bytes).hexdigest()
        metadata_key = 'external-masters/v1/' + metadata_sha + '/metadata.json'
        _put(s3, metadata_key, io.BytesIO(metadata_bytes), len(metadata_bytes), metadata_sha, 'application/json')
        now = datetime.now(timezone.utc)
        result = {'task_id': task_id, 'title': value['title'], 'language': value['language'],
                  'duration': descriptor['media_structure']['video_duration_seconds'], 'scenes': 6,
                  'resolution': {'width': descriptor['media_structure']['width'],
                                 'height': descriptor['media_structure']['height']},
                  'video_key': stored['video'], 'caption_key': stored['captions'], 'metadata_key': metadata_key,
                  'quality_disposition': 'manual_qa_preview', 'manual_qa_required': True,
                  'qa_approved': False, 'publish_eligible': False, 'media_generation_authorized': False,
                  'audio_transcription_verified': False, 'source_evidence_verified': False,
                  'external_descriptor_id': descriptor['descriptor_id'],
                  'external_provenance': {'origin': value['origin'], 'status': 'caller_declared_unverified',
                                          'review_status': 'unreviewed', 'video_sha256': descriptor['video_sha256'],
                                          'captions_sha256': descriptor['captions_sha256'],
                                          'manifest_sha256': descriptor['manifest_sha256']},
                  'contains_synthetic_media': True, 'new_media_generated': False}
        if value['version'] == 2:
            # Server-selected from the validated, hash-bound manifest; never QA.
            result['external_provenance'].update(manifest_version=2, duration_ms=value['duration_ms'])
        for field, key in (('download_url', stored['video']), ('caption_url', stored['captions'])):
            result[field] = s3.generate_presigned_url('get_object',
                Params={'Bucket': storage.settings.bucket, 'Key': key}, ExpiresIn=86400)
        job = {'task_id': task_id, 'kind': 'render', 'parent_id': None,
               'spec': {'topic': value['title'],
                        'duration_minutes': .5 if value['version'] == 1 else value['duration_ms'] / 60000,
                        'language': value['language'],
                        'format': 'shorts', 'mode': 'production', 'workflow': 'external_import',
                        'production_channel_id': target_channel_id,
                        'production_connection_id': expected_connection_id,
                        'production_profile_revision': expected_profile_revision,
                        'production_scheduled': False, 'publish_after_render': False},
               'state': 'SUCCESS', 'stage': 'complete', 'progress': 100, 'error': None,
               'message': 'Harici video alındı. Kalite incelemesi gerekli; yayın kapalı.',
               'created_ts': now.timestamp(), 'created_at': now.isoformat(),
               'updated_at': now.isoformat(), 'result': result}
        with client.pipeline() as pipe:
            key, job_key = RESERVATION_PREFIX + task_id, JOB_PREFIX + task_id
            pipe.watch(key, job_key, CHANNEL_INDEX_KEY, *snapshot)
            current = _snapshot(pipe, target_channel_id, expected_connection_id,
                                expected_profile_revision, value['language'])
            _require(current == snapshot and pipe.get(key) == _json(record)
                     and pipe.get(job_key) is None, 'external_ingest_binding_changed')
            completed = {**record, 'status': 'complete', 'completed_at': now.isoformat(),
                         'job_sha256': _digest(job)}
            pipe.multi()
            pipe.setex(job_key, JOB_TTL_SECONDS, _json(job))
            pipe.zadd(JOB_INDEX, {task_id: now.timestamp()})
            pipe.expire(JOB_INDEX, JOB_TTL_SECONDS)
            pipe.set(key, _json(completed))
            pipe.execute()
        return {**_response(completed), 'idempotent_replay': False}
    except Exception as exc:
        if record is not None:
            _uncertain(client, record)
        if isinstance(exc, (ExternalMasterIngestError, artifact.ExternalArtifactValidationError)):
            raise
        raise ExternalMasterIngestError('external_ingest_unavailable') from None
