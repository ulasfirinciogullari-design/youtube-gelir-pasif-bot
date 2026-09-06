"""One-shot asset recovery for an already-uploaded, blocked public-plan video.

This first-stage helper never releases a video. It writes an isolated audit,
not the immutable publish plan, historical publisher, source QA, or upload
ledger. Ambiguous asset requests are never replayed.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

import redis

from app.config import settings
from app.services.studio_state import JOB_PREFIX
from app.services.youtube_automation import PROFILE_PREFIX, automated_quality_approved, validate_publish_plan
from app.services.youtube_auth import CHANNEL_PREFIX, CREDENTIAL_PREFIX, CHANNEL_INDEX_KEY, AUTH_EPOCH_KEY
from app.services.youtube_publish_state import (
    UPLOAD_PREFIX, EXECUTION_LOCK_PREFIX, acquire_execution_lock, release_execution_lock,
)


RECOVERY_PREFIX = 'youtube_studio:blocked_public_assets:v1:'
_TASK = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
_ID = re.compile(r'^[A-Za-z0-9_-]{8,128}$')
_VIDEO = re.compile(r'^[A-Za-z0-9_-]{11}$')
_SHA = re.compile(r'^[0-9a-f]{64}$')
_CAPTION_ID = re.compile(r'^[A-Za-z0-9_=-]{8,256}$')
_GOOGLE_REASONS = {'forbidden', 'insufficientPermissions', 'quotaExceeded', 'dailyLimitExceeded',
                   'rateLimitExceeded', 'userRateLimitExceeded', 'uploadLimitExceeded', 'videoNotFound',
                   'captionExists', 'invalidMetadata', 'nameTooLong', 'contentRequired', 'invalidImage',
                   'unsupportedMediaType', 'thumbnailUploadNotAllowed', 'processingFailure'}
_PHASE_STATUS = {'pending', 'reserved_before_http', 'verified', 'verified_existing', 'awaiting_processing',
                 'rejected', 'uncertain', 'verification_failed'}


class BlockedPublicRecoveryError(RuntimeError):
    """A fixed rejection code, never a provider body or credential."""


def _redis():
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


def _require(condition, code='asset_recovery_ineligible'):
    if not condition:
        raise BlockedPublicRecoveryError(code)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _object(raw):
    _require(isinstance(raw, str) and 0 < len(raw.encode('utf-8')) <= 4 * 1024 * 1024)
    result = json.loads(raw)
    _require(isinstance(result, dict))
    return result


def _now():
    return datetime.now(timezone.utc).isoformat()


def _read(client, source_id, channel_id):
    snapshots = {}
    def read(key):
        raw = client.get(key)
        snapshots[key] = raw
        return _object(raw)
    records = {'source': read(JOB_PREFIX + source_id), 'ledger': read(UPLOAD_PREFIX + source_id),
               'profile': read(PROFILE_PREFIX + channel_id), 'channel': read(CHANNEL_PREFIX + channel_id)}
    publisher_id = records['ledger'].get('publish_task_id')
    _require(isinstance(publisher_id, str) and _TASK.fullmatch(publisher_id) and publisher_id != source_id)
    records['publisher'] = read(JOB_PREFIX + publisher_id)
    for key in (CREDENTIAL_PREFIX + channel_id, AUTH_EPOCH_KEY):
        snapshots[key] = client.get(key)
    _require(isinstance(snapshots[CREDENTIAL_PREFIX + channel_id], str)
             and 0 < len(snapshots[CREDENTIAL_PREFIX + channel_id]) <= 32768
             and client.sismember(CHANNEL_INDEX_KEY, channel_id), 'asset_connection_missing')
    return records, snapshots


def _validate(records, source_id, video_id, channel_id, revision):
    source, ledger, publisher, profile, channel = (records[k] for k in ('source', 'ledger', 'publisher', 'profile', 'channel'))
    _require(source.get('task_id') == source_id and automated_quality_approved(source)
             and not source.get('retry_child_task_id'), 'asset_quality_not_approved')
    result, spec = source['result'], source.get('spec')
    attribution, plan = result.get('youtube'), ledger.get('publish_plan')
    delivered, pub_spec = publisher.get('result'), publisher.get('spec')
    _require(all(isinstance(x, dict) for x in (spec, attribution, plan, delivered, pub_spec)))
    _require(result.get('task_id') == source_id and spec.get('mode') == 'production'
             and spec.get('format') == 'shorts' and type(spec.get('duration_minutes')) in (int, float)
             and spec['duration_minutes'] == .5 and spec.get('language') in {'tr', 'en'})
    language = spec['language']
    _require(result.get('video_key') == f'videos/{source_id}/final.mp4'
             and result.get('caption_key') == f'videos/{source_id}/captions.{language}.srt'
             and result.get('metadata_key') == f'videos/{source_id}/metadata.json')
    connection = ledger.get('connection_id')
    _require(isinstance(connection, str) and _ID.fullmatch(connection))
    _require(type(ledger.get('version')) is int and ledger['version'] == 2
             and ledger.get('source_task_id') == source_id and ledger.get('status') == 'complete'
             and ledger.get('side_effect_possible') is True and ledger.get('youtube_video_id') == video_id
             and ledger.get('target_channel_id') == channel_id and ledger.get('requested_release_mode') == 'public'
             and not ledger.get('requested_publish_at') and ledger.get('release_status') == 'blocked'
             and ledger.get('release_side_effect_possible') is False and not ledger.get('explicit_owner_release'),
             'asset_upload_or_release_ambiguous')
    _require(profile.get('channel_id') == channel_id and profile.get('profile_revision') == revision
             and profile.get('auto_publish') is True and profile.get('production_enabled') is True
             and profile.get('release_mode') == 'public' and isinstance(profile.get('languages'), list)
             and language in profile['languages'],
             'asset_profile_changed')
    _require(channel.get('id') == channel_id and channel.get('connection_id') == connection
             and channel.get('requires_reconnect') is not True, 'asset_connection_changed')
    _require(validate_publish_plan(plan) == plan and plan.get('source_task_id') == source_id
             and plan.get('target_channel_id') == channel_id and plan.get('profile_revision') == revision
             and plan.get('release_mode') == 'public' and plan.get('publish_at') is None
             and plan.get('default_language') == language
             and plan.get('quality_snapshot') == {'quality_disposition': 'automated_qc_pass', 'manual_qa_required': False},
             'asset_frozen_plan_changed')
    publisher_id = ledger['publish_task_id']
    _require(publisher.get('task_id') == publisher_id and publisher.get('kind') == 'publish'
             and publisher.get('state') == 'SUCCESS' and publisher.get('parent_id') == source_id
             and delivered.get('task_id') == publisher_id and delivered.get('status') == 'complete'
             and delivered.get('source_task_id') == source_id and delivered.get('youtube_video_id') == video_id
             and pub_spec.get('source_task_id') == source_id and pub_spec.get('release_mode') == 'public'
             and pub_spec.get('privacy_status') == 'private', 'asset_publication_unverified')
    _require(attribution.get('video_id') == video_id)
    for record in (pub_spec, delivered, attribution):
        _require(record.get('target_channel_id') == channel_id and record.get('connection_id') == connection
                 and record.get('profile_revision') == revision, 'asset_binding_changed')
    for record in (delivered, attribution):
        _require(record.get('privacy_status') == 'private' and record.get('release_status') == 'blocked'
                 and not record.get('scheduled_publish_at') and record.get('release_error_code') == ledger.get('release_error_code'))
        _require(type(record.get('caption_uploaded')) is bool and type(record.get('thumbnail_uploaded')) is bool)
    # Derivation is only for a genuinely absent thumbnail; an authored but
    # failed thumbnail must not be replaced by silently disregarding its plan.
    _require(attribution['thumbnail_uploaded'] is True or (
        plan.get('thumbnail_key') is None and result.get('thumbnail_key') is None),
        'asset_authored_thumbnail_requires_review')
    _require(spec.get('production_channel_id') == channel_id and spec.get('production_connection_id') == connection
             and spec.get('production_profile_revision') == revision and spec.get('publish_after_render') is True)
    _require((result.get('youtube_automation') or {}).get('publish_task_id') == publisher_id)
    error = ledger.get('release_error_code')
    known_asset_errors = {attribution.get('caption_error_code'), attribution.get('thumbnail_error_code'), 'thumbnail_required'}
    _require(isinstance(error, str) and bool(error) and error in known_asset_errors,
             'asset_block_reason_not_recoverable')
    return {'source_task_id': source_id, 'publish_task_id': publisher_id, 'youtube_video_id': video_id,
            'target_channel_id': channel_id, 'connection_id': connection, 'profile_revision': revision,
            'language': language, 'original_plan_sha256': _digest(plan),
            'source_snapshot_sha256': _digest(source), 'publisher_snapshot_sha256': _digest(publisher),
            'ledger_snapshot_sha256': _digest(ledger)}


def _commit(client, snapshots, channel_id, key, previous, record):
    """One WATCH transaction; lost replies never authorize a repeated request."""
    with client.pipeline() as pipe:
        pipe.watch(*snapshots, CHANNEL_INDEX_KEY, key)
        _require(all(pipe.get(k) == v for k, v in snapshots.items()) and pipe.get(key) == previous
                 and pipe.sismember(CHANNEL_INDEX_KEY, channel_id), 'asset_state_changed')
        pipe.multi()
        pipe.set(key, _json(record), nx=previous is None)
        result = pipe.execute()
        _require(result and result[0] is True, 'asset_state_changed')
    return _json(record)


def _credentials(cipher, binding):
    from app.services import youtube_auth
    payload = youtube_auth._decrypt_json(cipher)
    _require(type(payload.get('version')) is int and payload['version'] == 3
             and payload.get('channel_id') == binding['target_channel_id']
             and payload.get('connection_id') == binding['connection_id']
             and isinstance(payload.get('refresh_token'), str) and 1 <= len(payload['refresh_token']) <= 4096,
             'asset_connection_changed')
    credentials = youtube_auth._credential_from_refresh_token(payload['refresh_token'])
    request = youtube_auth.GoogleRequest()
    credentials.refresh(lambda *a, **k: request(*a, **{**k, 'timeout': 20}))
    _require(bool(credentials.token), 'asset_connection_missing')
    return credentials


def _service(credentials):
    import httplib2
    from google_auth_httplib2 import AuthorizedHttp
    from googleapiclient.discovery import build
    return build('youtube', 'v3', http=AuthorizedHttp(credentials, http=httplib2.Http(timeout=20),
                 max_refresh_attempts=0), cache_discovery=False, static_discovery=True)


def _remote_private(service, binding):
    channel_id, video_id = binding['target_channel_id'], binding['youtube_video_id']
    channels = service.channels().list(part='id', mine=True, maxResults=50).execute(num_retries=0)
    items = channels.get('items') if isinstance(channels, dict) else None
    _require(isinstance(items, list) and len(items) <= 50
             and len([i for i in items if isinstance(i, dict) and i.get('id') == channel_id]) == 1,
             'asset_remote_channel_changed')
    response = service.videos().list(part='snippet,status', id=video_id, maxResults=1).execute(num_retries=0)
    items = response.get('items') if isinstance(response, dict) else None
    _require(isinstance(items, list) and len(items) == 1 and isinstance(items[0], dict)
             and items[0].get('id') == video_id, 'asset_remote_video_changed')
    item = items[0]
    _require(isinstance(item.get('snippet'), dict) and item['snippet'].get('channelId') == channel_id
             and isinstance(item.get('status'), dict) and item['status'].get('privacyStatus') == 'private'
             and item['status'].get('uploadStatus') == 'processed' and not item['status'].get('publishAt'),
             'asset_remote_not_private_processed')
    return item


def _caption_tracks(service, video_id):
    response = service.captions().list(part='snippet', videoId=video_id).execute(num_retries=0)
    items = response.get('items') if isinstance(response, dict) else None
    _require(isinstance(items, list) and len(items) <= 100 and all(isinstance(i, dict) for i in items),
             'asset_caption_list_invalid')
    return items


def _caption_identity(item, video_id, language, name, *, serving=False):
    snippet = item.get('snippet') if isinstance(item, dict) else None
    return bool(isinstance(item, dict) and isinstance(item.get('id'), str) and _CAPTION_ID.fullmatch(item['id'])
                and isinstance(snippet, dict) and snippet.get('videoId') == video_id
                and snippet.get('language') == language and snippet.get('name') == name
                and snippet.get('isDraft') is False and snippet.get('trackKind') == 'standard'
                and (not serving or snippet.get('status') == 'serving'))


def _file_bytes(asset, work_dir, maximum):
    _require(isinstance(asset, dict) and isinstance(asset.get('path'), str)
             and isinstance(asset.get('sha256'), str) and _SHA.fullmatch(asset['sha256'])
             and type(asset.get('size')) is int and 1 <= asset['size'] <= maximum)
    path, work = Path(asset['path']), Path(work_dir).resolve(strict=True)
    _require(not path.is_symlink() and path.resolve(strict=True).is_relative_to(work)
             and path.is_file(), 'asset_local_binding_invalid')
    with path.open('rb') as stream:
        data = stream.read(maximum + 1)
    _require(len(data) == asset['size'] and hashlib.sha256(data).hexdigest() == asset['sha256'],
             'asset_local_hash_changed')
    return data


def _asset_manifest(assets, language):
    result = {}
    for name in ('caption', 'thumbnail', 'final', 'metadata'):
        item = assets.get(name)
        _require(isinstance(item, dict) and isinstance(item.get('sha256'), str) and _SHA.fullmatch(item['sha256'])
                 and type(item.get('size')) is int and item['size'] > 0, 'asset_manifest_invalid')
        result[name] = {'sha256': item['sha256'], 'size': item['size']}
    _require(assets['caption'].get('language') == language)
    result['caption']['language'] = language
    return result


def _media(data, mime):
    from googleapiclient.http import MediaIoBaseUpload
    return MediaIoBaseUpload(BytesIO(data), mimetype=mime, resumable=False)


def _thumbnail_receipt(response):
    _require(isinstance(response, dict) and response.get('kind') == 'youtube#thumbnailSetResponse'
             and isinstance(response.get('items'), list) and len(response['items']) == 1,
             'asset_thumbnail_receipt_invalid')
    item = response['items'][0]
    _require(isinstance(item, dict), 'asset_thumbnail_receipt_invalid')
    safe = []
    for name in ('default', 'medium', 'high', 'standard', 'maxres'):
        image = item.get(name)
        if image is None:
            continue
        _require(isinstance(image, dict) and type(image.get('width')) is int and type(image.get('height')) is int
                 and 1 <= image['width'] <= 8192 and 1 <= image['height'] <= 8192
                 and isinstance(image.get('url'), str) and len(image['url']) <= 2000, 'asset_thumbnail_receipt_invalid')
        parsed = urlsplit(image['url'])
        host = parsed.hostname or ''
        _require(parsed.scheme == 'https' and not parsed.username and not parsed.password
                 and any(host == domain or host.endswith('.' + domain) for domain in ('ytimg.com', 'googleusercontent.com', 'ggpht.com')),
                 'asset_thumbnail_receipt_invalid')
        safe.append({'name': name, 'width': image['width'], 'height': image['height']})
    _require(bool(safe), 'asset_thumbnail_receipt_invalid')
    return {'receipt_sha256': _digest(safe), 'returned_sizes': safe}


def _safe_http_error(exc):
    status = getattr(getattr(exc, 'resp', None), 'status', None)
    status = status if type(status) is int and 400 <= status <= 599 else None
    reason = 'api_unavailable'
    body = getattr(exc, 'content', None)
    try:
        if isinstance(body, bytes) and len(body) <= 16384:
            detail = json.loads(body.decode('utf-8')).get('error')
            reasons = detail.get('errors') if isinstance(detail, dict) else None
            codes = [item.get('reason') for item in reasons if isinstance(item, dict)] if isinstance(reasons, list) else []
            if len(codes) == 1 and codes[0] in _GOOGLE_REASONS:
                reason = codes[0]
    except Exception:
        pass
    return {'status': 'rejected' if status and 400 <= status < 500 else 'uncertain',
            'http_status': status, 'reason': reason}


def _outcome(record):
    def phase(name):
        item = record[name]
        result = {'status': item['status'], 'attempts': item.get('attempts')}
        _require(type(result['attempts']) is int and result['attempts'] in (0, 1), 'asset_previous_audit_invalid')
        if isinstance(item.get('caption_id'), str) and _CAPTION_ID.fullmatch(item['caption_id']):
            result['caption_id'] = item['caption_id']
        if type(item.get('http_status')) is int and 400 <= item['http_status'] <= 599:
            result['http_status'] = item['http_status']
        if item.get('reason') in _GOOGLE_REASONS | {
            'api_unavailable', 'asset_caption_receipt_invalid', 'asset_thumbnail_receipt_invalid',
            'asset_caption_list_invalid', 'asset_remote_channel_changed', 'asset_remote_video_changed',
            'asset_remote_not_private_processed',
        }:
            result['reason'] = item['reason']
        if isinstance(item.get('receipt_sha256'), str) and _SHA.fullmatch(item['receipt_sha256']):
            result['receipt_sha256'] = item['receipt_sha256']
        return result
    return {'status': record['status'], 'source_task_id': record['source_task_id'],
            'youtube_video_id': record['youtube_video_id'], 'target_channel_id': record['target_channel_id'],
            'release_performed': False, 'caption': phase('caption'), 'thumbnail': phase('thumbnail')}


def recover_blocked_public_assets(source_task_id, expected_video_id, expected_channel_id,
                                  expected_profile_revision, *, work_dir):
    """One caption/thumbnail attempt, then evidence only. Never videos.insert/update."""
    _require(isinstance(source_task_id, str) and _TASK.fullmatch(source_task_id)
             and isinstance(expected_video_id, str) and _VIDEO.fullmatch(expected_video_id)
             and all(isinstance(x, str) and _ID.fullmatch(x) for x in (expected_channel_id, expected_profile_revision)),
             'asset_input_invalid')
    lock_token = service = None
    source_id, channel_id, video_id = source_task_id, expected_channel_id, expected_video_id
    key = RECOVERY_PREFIX + source_id
    try:
        client = _redis()
        records, snapshots = _read(client, source_id, channel_id)
        binding = _validate(records, source_id, video_id, channel_id, expected_profile_revision)
        previous = client.get(key)
        if previous is not None:
            record = _object(previous)
            _require(all(record.get(k) == v for k, v in binding.items()), 'asset_previous_binding_changed')
            _require(type(record.get('version')) is int and record['version'] == 1 and record.get('status') in {
                'prepared', 'assets_blocked', 'assets_pending_verification', 'assets_ready_needs_release_integration'}
                and all(isinstance(record.get(n), dict) and record[n].get('status') in _PHASE_STATUS
                        for n in ('caption', 'thumbnail')), 'asset_previous_audit_invalid')
            return _outcome(record)  # Existing attempts, including uncertain ones, are never re-posted.
        lock_token = acquire_execution_lock(source_id, binding['publish_task_id'])
        snapshots[EXECUTION_LOCK_PREFIX + source_id] = lock_token
        from app.services.publication_recovery_assets import prepare_publication_recovery_assets
        assets = prepare_publication_recovery_assets(records['source'], source_task_id=source_id, work_dir=work_dir)
        manifest = _asset_manifest(assets, binding['language'])
        caption_bytes = _file_bytes(assets['caption'], work_dir, 256 * 1024)
        thumbnail_bytes = _file_bytes(assets['thumbnail'], work_dir, 2 * 1024 * 1024)
        service = _service(_credentials(snapshots[CREDENTIAL_PREFIX + channel_id], binding))
        _remote_private(service, binding)
        name, language = f"{binding['language'].upper()} captions", binding['language']
        tracks = _caption_tracks(service, video_id)
        prior = records['source']['result']['youtube']
        existing = [i for i in tracks if _caption_identity(i, video_id, language, name, serving=True)]
        _require(len(existing) <= 1, 'asset_existing_caption_ambiguous')
        if prior['caption_uploaded'] is not True:
            _require(not any(isinstance(i.get('snippet'), dict) and i['snippet'].get('language') == language
                             and i['snippet'].get('name') == name for i in tracks), 'asset_existing_caption_unverified')
        else:
            _require(len(existing) == 1 and not prior.get('caption_error_code'), 'asset_existing_caption_unverified')
        record = {'version': 1, **binding, 'assets': manifest, 'created_at': _now(), 'status': 'prepared',
                  'caption': {'status': 'verified_existing', 'caption_id': existing[0]['id'], 'attempts': 0} if existing else {'status': 'pending', 'attempts': 0},
                  'thumbnail': {'status': 'verified_existing', 'attempts': 0} if prior['thumbnail_uploaded'] is True and not prior.get('thumbnail_error_code') else {'status': 'pending', 'attempts': 0}}
        previous = _commit(client, snapshots, channel_id, key, None, record)
        for phase, data, mime in (('caption', caption_bytes, 'application/octet-stream'), ('thumbnail', thumbnail_bytes, 'image/jpeg')):
            if record[phase]['status'] != 'pending':
                continue
            record[phase] = {'status': 'reserved_before_http', 'attempts': 1, 'started_at': _now()}
            previous = _commit(client, snapshots, channel_id, key, previous, record)
            media = None
            try:
                media = _media(data, mime)
                if phase == 'caption':
                    response = service.captions().insert(part='snippet', body={'snippet': {
                        'videoId': video_id, 'language': language, 'name': name, 'isDraft': False}},
                        media_body=media).execute(num_retries=0)
                    _require(_caption_identity(response, video_id, language, name), 'asset_caption_receipt_invalid')
                    returned_id = response['id']
                    verified = [i for i in _caption_tracks(service, video_id) if i.get('id') == returned_id
                                and _caption_identity(i, video_id, language, name, serving=True)]
                    _require(len(verified) <= 1, 'asset_caption_receipt_invalid')
                    record[phase].update(status='verified' if verified else 'awaiting_processing', caption_id=returned_id)
                else:
                    response = service.thumbnails().set(videoId=video_id, media_body=media).execute(num_retries=0)
                    proof = _thumbnail_receipt(response)
                    _remote_private(service, binding)
                    record[phase].update(status='verified', **proof)
            except BlockedPublicRecoveryError as exc:
                record[phase].update(status='verification_failed', reason=str(exc))
            except Exception as exc:
                record[phase].update(_safe_http_error(exc))
            finally:
                if media is not None:
                    media.stream().close()
            record[phase]['completed_at'] = _now()
            previous = _commit(client, snapshots, channel_id, key, previous, record)
        states = {record[p]['status'] for p in ('caption', 'thumbnail')}
        record['status'] = ('assets_ready_needs_release_integration' if states <= {'verified', 'verified_existing'}
                            else 'assets_pending_verification' if states <= {'verified', 'verified_existing', 'awaiting_processing'}
                            else 'assets_blocked')
        record['updated_at'] = _now()
        _commit(client, snapshots, channel_id, key, previous, record)
        return _outcome(record)
    except BlockedPublicRecoveryError:
        raise
    except Exception:
        raise BlockedPublicRecoveryError('asset_recovery_unavailable_or_uncertain') from None
    finally:
        if service is not None:
            try:
                service.close()
            except Exception:
                pass
        if lock_token:
            release_execution_lock(source_id, lock_token)
