"""Explicit owner release of one already-delivered private video; never upload.

The old private publish plan and publisher remain historical evidence. A new,
revision-bound authorization owns the visibility transition. An uncertain
transition is never replayed, even when the remote video still appears private.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re

import redis

from app.config import settings
from app.services.studio_state import JOB_PREFIX, JOB_TTL_SECONDS
from app.services.youtube_automation import PROFILE_PREFIX, automated_quality_approved
from app.services.youtube_auth import (
    CHANNEL_PREFIX, CREDENTIAL_PREFIX, CHANNEL_INDEX_KEY, AUTH_EPOCH_KEY,
    load_credentials, refresh_channel_info,
)
from app.services.youtube import (
    get_video_status_with_credentials, set_video_release_with_credentials,
)
from app.services.youtube_publish_state import (
    UPLOAD_PREFIX, EXECUTION_LOCK_PREFIX, UPLOAD_RECORD_TTL_SECONDS,
    acquire_execution_lock, release_execution_lock,
    mark_release_completed, mark_release_uncertain,
)


_TASK = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
_ID = re.compile(r'^[A-Za-z0-9_-]{8,128}$')
_VIDEO = re.compile(r'^[A-Za-z0-9_-]{11}$')


class ExistingVideoReleaseError(RuntimeError):
    """Safe fixed-code rejection; never includes credentials or provider errors."""


def _redis():
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ExistingVideoReleaseError(code)


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _digest(value) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _object(raw) -> dict:
    _require(isinstance(raw, str) and 0 < len(raw.encode()) <= 4 * 1024 * 1024,
             'release_record_invalid')
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        raise ExistingVideoReleaseError('release_record_invalid') from None
    _require(isinstance(value, dict), 'release_record_invalid')
    return value


def _private(record: dict) -> bool:
    return (record.get('privacy_status') == 'private'
            and record.get('release_status') == 'private'
            and not record.get('scheduled_publish_at')
            and not record.get('release_error_code'))


def _read(client, source_id: str, channel_id: str) -> tuple[dict, dict]:
    snapshots = {}

    def read(key):
        raw = client.get(key)
        snapshots[key] = raw
        return _object(raw)

    records = {
        'source': read(JOB_PREFIX + source_id),
        'ledger': read(UPLOAD_PREFIX + source_id),
        'profile': read(PROFILE_PREFIX + channel_id),
        'channel': read(CHANNEL_PREFIX + channel_id),
    }
    publisher_id = records['ledger'].get('publish_task_id')
    _require(isinstance(publisher_id, str) and _TASK.fullmatch(publisher_id) is not None
             and publisher_id != source_id, 'release_publisher_invalid')
    records['publisher'] = read(JOB_PREFIX + publisher_id)
    credential_key = CREDENTIAL_PREFIX + channel_id
    snapshots[credential_key] = client.get(credential_key)
    snapshots[AUTH_EPOCH_KEY] = client.get(AUTH_EPOCH_KEY)
    _require(bool(snapshots[credential_key]) and client.sismember(CHANNEL_INDEX_KEY, channel_id),
             'release_connection_missing')
    return records, snapshots


def _validate(records: dict, source_id: str, video_id: str, channel_id: str,
              revision: str, *, completed: bool = False) -> dict:
    source, ledger, publisher = (records[k] for k in ('source', 'ledger', 'publisher'))
    profile, channel = records['profile'], records['channel']
    _require(source.get('task_id') == source_id and automated_quality_approved(source)
             and not source.get('retry_child_task_id'), 'release_quality_not_approved')
    result = source['result']
    _require(result.get('task_id', source_id) == source_id, 'release_source_mismatch')
    spec = source.get('spec')
    attribution = result.get('youtube')
    plan = ledger.get('publish_plan')
    pub_spec, delivered = publisher.get('spec'), publisher.get('result')
    _require(all(isinstance(x, dict) for x in (spec, attribution, plan, pub_spec, delivered)),
             'release_record_invalid')
    publisher_id = ledger['publish_task_id']
    connection_id = ledger.get('connection_id')
    old_revision = plan.get('profile_revision')
    _require(isinstance(connection_id, str) and _ID.fullmatch(connection_id) is not None
             and isinstance(old_revision, str) and _ID.fullmatch(old_revision) is not None,
             'release_original_binding_invalid')
    _require(profile.get('channel_id') == channel_id
             and profile.get('profile_revision') == revision
             and profile.get('release_mode') == 'public' and profile.get('auto_publish') is True,
             'release_profile_changed')
    _require(channel.get('id') == channel_id and channel.get('connection_id') == connection_id,
             'release_connection_changed')
    # An acks-late publisher replay can reconstruct its minimal result from
    # the now-public ledger. Accept that exact legacy shape only for read-only
    # completion reconciliation, never to authorize the first public update.
    completed_replay = bool(completed and delivered.get('idempotent_replay') is True
                            and delivered.get('task_id') == publisher_id
                            and delivered.get('privacy_status') == 'public'
                            and delivered.get('release_status') == 'public'
                            and not delivered.get('scheduled_publish_at')
                            and not delivered.get('release_error_code')
                            and delivered.get('profile_revision') in (None, old_revision))
    _require(publisher.get('task_id') == publisher_id and publisher.get('kind') == 'publish'
             and publisher.get('state') == 'SUCCESS' and publisher.get('parent_id') == source_id
             and pub_spec.get('source_task_id') == source_id
             and delivered.get('source_task_id') == source_id
             and delivered.get('task_id', publisher_id) == publisher_id
             and delivered.get('status') == 'complete' and (_private(delivered) or completed_replay)
             and delivered.get('youtube_video_id') == video_id,
             'release_original_publication_invalid')
    _require(ledger.get('version') == 2 and ledger.get('source_task_id') == source_id
             and ledger.get('status') == 'complete' and ledger.get('side_effect_possible') is True
             and ledger.get('youtube_video_id') == video_id
             and ledger.get('target_channel_id') == channel_id
             and ledger.get('requested_release_mode') == 'private'
             and not ledger.get('requested_publish_at') and not ledger.get('release_error_code')
             and plan.get('source_task_id') == source_id and plan.get('target_channel_id') == channel_id
             and plan.get('release_mode') == 'private' and not plan.get('publish_at')
             and pub_spec.get('release_mode') == 'private' and pub_spec.get('privacy_status') == 'private',
             'release_original_upload_invalid')
    for record in (pub_spec, delivered, attribution):
        _require(record.get('target_channel_id') == channel_id
                 and record.get('connection_id') == connection_id
                 and (record.get('profile_revision') == old_revision
                      or record is delivered and completed_replay), 'release_binding_changed')
    _require(attribution.get('video_id') == video_id, 'release_video_mismatch')
    for field, expected in (
        ('production_channel_id', channel_id), ('production_connection_id', connection_id),
        ('production_profile_revision', old_revision),
    ):
        _require(field not in spec or spec[field] == expected, 'release_frozen_source_changed')
    automation = result.get('youtube_automation')
    if isinstance(automation, dict) and automation.get('publish_task_id'):
        _require(automation['publish_task_id'] == publisher_id, 'release_publisher_mismatch')
    # A later, verified caption recovery belongs to the source. Do not replace
    # it with the historical publisher's initial caption 404/False outcome.
    _require(attribution.get('caption_uploaded') is True and not attribution.get('caption_error_code'),
             'release_captions_missing')
    thumbnail_required = plan.get('require_thumbnail') is True or profile.get('require_thumbnail') is True
    _require(not thumbnail_required or (attribution.get('thumbnail_uploaded') is True
             and not attribution.get('thumbnail_error_code')), 'release_thumbnail_missing')
    if completed:
        audit = ledger.get('explicit_owner_release')
        _require(ledger.get('release_status') == 'public' and ledger.get('privacy_status') == 'public'
                 and ledger.get('release_side_effect_possible') is True
                 and not ledger.get('scheduled_publish_at') and isinstance(audit, dict),
                 'release_completion_unverified')
        for key, expected in {
            'version': 1, 'source_task_id': source_id, 'publish_task_id': publisher_id,
            'youtube_video_id': video_id, 'target_channel_id': channel_id,
            'connection_id': connection_id, 'profile_revision': revision,
            'original_profile_revision': old_revision, 'original_plan_sha256': _digest(plan),
            'release_mode': 'public',
        }.items():
            _require(audit.get(key) == expected, 'release_authorization_mismatch')
        proof = audit.get('prior_private_proof')
        _require(isinstance(proof, dict) and proof.get('privacy_status') == 'private'
                 and proof.get('upload_status') == 'processed' and proof.get('caption_uploaded') is True,
                 'release_authorization_invalid')
        _require(_private(attribution) or (
            attribution.get('privacy_status') == 'public' and attribution.get('release_status') == 'public'
            and not attribution.get('scheduled_publish_at') and not attribution.get('release_error_code')
            and attribution.get('explicit_owner_release') == audit), 'release_source_status_ambiguous')
    else:
        _require(ledger.get('release_status') == 'private'
                 and ledger.get('release_side_effect_possible') is False
                 and not ledger.get('explicit_owner_release') and _private(attribution),
                 'release_already_started_or_uncertain')
    return {'publisher_id': publisher_id, 'connection_id': connection_id,
            'old_revision': old_revision, 'thumbnail_required': thumbnail_required}


def _compare_transaction(client, snapshots: dict, channel_id: str, *, ledger_write=None) -> None:
    # One attempt: a lost EXEC reply or any concurrent change is not permission
    # to reserve again. The execution lease is part of the watched snapshot.
    with client.pipeline() as pipe:
        pipe.watch(*snapshots, CHANNEL_INDEX_KEY)
        _require(all(pipe.get(key) == raw for key, raw in snapshots.items())
                 and pipe.sismember(CHANNEL_INDEX_KEY, channel_id), 'release_state_changed')
        pipe.multi()
        if ledger_write is not None:
            key, value = ledger_write
            pipe.set(key, _json(value), ex=UPLOAD_RECORD_TTL_SECONDS)
        else:
            pipe.ping()
        pipe.execute()


def _merge_completed_source(client, source_id: str, video_id: str, channel_id: str,
                            revision: str, lock_token: str) -> None:
    records, snapshots = _read(client, source_id, channel_id)
    _validate(records, source_id, video_id, channel_id, revision, completed=True)
    snapshots[EXECUTION_LOCK_PREFIX + source_id] = lock_token
    source = deepcopy(records['source'])
    patch = {
        'privacy_status': 'public', 'release_status': 'public', 'scheduled_publish_at': None,
        'release_error_code': None, 'contains_synthetic_media': True,
        'explicit_owner_release': records['ledger']['explicit_owner_release'],
    }
    if all(source['result']['youtube'].get(key) == value for key, value in patch.items()):
        _compare_transaction(client, snapshots, channel_id)
        return
    source['result']['youtube'].update(patch)
    source['updated_at'] = datetime.now(timezone.utc).isoformat()
    with client.pipeline() as pipe:
        pipe.watch(*snapshots, CHANNEL_INDEX_KEY)
        _require(all(pipe.get(key) == raw for key, raw in snapshots.items())
                 and pipe.sismember(CHANNEL_INDEX_KEY, channel_id), 'release_state_changed')
        pipe.multi()
        pipe.set(JOB_PREFIX + source_id, _json(source), ex=JOB_TTL_SECONDS)
        pipe.execute()


def release_existing_private_video(source_task_id: str, expected_video_id: str,
                                   expected_channel_id: str, expected_profile_revision: str) -> dict:
    """Release exactly the owner-authorized existing video, with no new upload.

    Call explicitly, not from beat or a retrying Celery task. On any uncertain
    result inspect the same ledger/video; never reset the authorization. Only a
    completed public ledger can be reconciled idempotently, using fresh remote
    proof without another visibility update.
    """
    _require(isinstance(source_task_id, str) and _TASK.fullmatch(source_task_id) is not None
             and isinstance(expected_video_id, str) and _VIDEO.fullmatch(expected_video_id) is not None
             and all(isinstance(value, str) and _ID.fullmatch(value) is not None
                     for value in (expected_channel_id, expected_profile_revision)), 'release_input_invalid')
    source_id, video_id, channel_id, revision = (
        source_task_id, expected_video_id, expected_channel_id, expected_profile_revision,
    )
    lock_token = None
    started = False
    publisher_id = None
    try:
        client = _redis()
        records, initial = _read(client, source_id, channel_id)
        completed = records['ledger'].get('release_status') == 'public'
        binding = _validate(records, source_id, video_id, channel_id, revision, completed=completed)
        publisher_id = binding['publisher_id']
        lock_token = acquire_execution_lock(source_id, publisher_id)
        credentials = load_credentials(channel_id, expected_connection_id=binding['connection_id'], refresh=True)
        _require(credentials is not None, 'release_connection_missing')
        channel = refresh_channel_info(channel_id, credentials=credentials,
                                       expected_connection_id=binding['connection_id'])
        _require(channel.get('id') == channel_id and channel.get('connection_id') == binding['connection_id'],
                 'release_connection_changed')
        records, snapshots = _read(client, source_id, channel_id)
        # refresh_channel_info legitimately updates channel metadata. No other
        # proof, encrypted credential generation, or authorization may change.
        _require(all(snapshots.get(key) == raw for key, raw in initial.items()
                     if key != CHANNEL_PREFIX + channel_id), 'release_state_changed')
        _validate(records, source_id, video_id, channel_id, revision, completed=completed)
        snapshots[EXECUTION_LOCK_PREFIX + source_id] = lock_token
        remote = get_video_status_with_credentials(credentials, video_id)
        _require(isinstance(remote, dict) and remote.get('uploadStatus') == 'processed'
                 and not remote.get('publishAt'), 'release_remote_not_processed')
        if completed:
            _require(remote.get('privacyStatus') == 'public' and remote.get('containsSyntheticMedia') is True,
                     'release_remote_completion_unverified')
            _compare_transaction(client, snapshots, channel_id)
            _merge_completed_source(client, source_id, video_id, channel_id, revision, lock_token)
            outcome = 'already_public'
        else:
            _require(remote.get('privacyStatus') == 'private', 'release_remote_not_private')
            ledger = deepcopy(records['ledger'])
            now = datetime.now(timezone.utc).isoformat()
            audit = {
                'version': 1, 'source_task_id': source_id, 'publish_task_id': publisher_id,
                'youtube_video_id': video_id, 'target_channel_id': channel_id,
                'connection_id': binding['connection_id'], 'profile_revision': revision,
                'original_profile_revision': binding['old_revision'],
                'original_plan_sha256': _digest(ledger['publish_plan']),
                'release_mode': 'public', 'authorized_at': now,
                'prior_private_proof': {
                    'privacy_status': 'private', 'upload_status': 'processed', 'caption_uploaded': True,
                    'thumbnail_uploaded': records['source']['result']['youtube'].get('thumbnail_uploaded') is True,
                    'source_sha256': _digest(records['source']),
                    'publisher_sha256': _digest(records['publisher']), 'ledger_sha256': _digest(ledger),
                },
            }
            ledger.update(explicit_owner_release=audit, release_status='ready',
                          release_ready_at=now, updated_at=now)
            _compare_transaction(client, snapshots, channel_id,
                                 ledger_write=(UPLOAD_PREFIX + source_id, ledger))
            snapshots[UPLOAD_PREFIX + source_id] = _json(ledger)
            # The shared legacy start helper also accepts uncertain. This path
            # instead requires the exact newly-authorized ready snapshot and
            # every live proof, atomically, before crossing the side-effect line.
            ledger.update(release_status='releasing', release_side_effect_possible=True,
                          release_started_at=datetime.now(timezone.utc).isoformat())
            ledger['updated_at'] = ledger['release_started_at']
            started = True  # A lost started EXEC reply must never authorize replay.
            _compare_transaction(client, snapshots, channel_id,
                                 ledger_write=(UPLOAD_PREFIX + source_id, ledger))
            snapshots[UPLOAD_PREFIX + source_id] = _json(ledger)
            response = set_video_release_with_credentials(
                credentials, video_id, 'public', contains_synthetic_media=True,
            )
            status = response.get('status') if isinstance(response, dict) else None
            _require(isinstance(status, dict) and response.get('id') == video_id
                     and status.get('privacyStatus') == 'public'
                     and status.get('containsSyntheticMedia') is True and not status.get('publishAt'),
                     'release_response_unverified')
            _compare_transaction(client, snapshots, channel_id)
            mark_release_completed(source_id, publisher_id, release_mode='public')
            _merge_completed_source(client, source_id, video_id, channel_id, revision, lock_token)
            outcome = 'released'
        return {'status': outcome, 'source_task_id': source_id, 'youtube_video_id': video_id,
                'target_channel_id': channel_id, 'profile_revision': revision,
                'privacy_status': 'public', 'release_status': 'public', 'contains_synthetic_media': True}
    except Exception as exc:
        if started and publisher_id:
            try:
                mark_release_uncertain(source_id, publisher_id, error_code='explicit_owner_release_unverified')
            except Exception:
                pass  # releasing/ready is already a durable no-replay barrier.
        if isinstance(exc, ExistingVideoReleaseError):
            raise
        raise ExistingVideoReleaseError('release_unavailable_or_uncertain') from None
    finally:
        if lock_token:
            release_execution_lock(source_id, lock_token)
