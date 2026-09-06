"""Settle recovered assets and release their existing video, at most once.

Historical publisher/upload errors and asset audits remain untouched. This
separate receipt records the requested transition and freshly observed public
state; it is not permission to replay an uncertain visibility update.
"""
from __future__ import annotations

import hashlib
import re

from app.services import blocked_public_recovery as assets_core


PUBLIC_RECOVERY_PREFIX = 'youtube_studio:blocked_public_release:v1:'
REENACTMENT_NOTES = {
    'en': 'Some visuals are AI-generated reconstructions, not archival footage.',
    'tr': 'Bazı görseller yapay zekâ ile oluşturulmuş canlandırmalardır; arşiv görüntüsü değildir.',
}
_SNIPPET_FIELDS = ('title', 'description', 'categoryId', 'tags', 'defaultLanguage', 'defaultAudioLanguage')
_IDENTITY_FIELDS = ('source_task_id', 'publish_task_id', 'youtube_video_id', 'target_channel_id',
                    'connection_id', 'profile_revision', 'language', 'original_plan_sha256')
_STATUSES = {'assets_pending_verification', 'assets_ready', 'reserved_before_http', 'uncertain',
             'rejected', 'public'}


class BlockedPublicReleaseError(RuntimeError):
    """A fixed safe rejection; never a provider body or credential."""


def _require(condition, code='public_recovery_ineligible'):
    if not condition:
        raise BlockedPublicReleaseError(code)


def _hash_text(value):
    _require(value is None or isinstance(value, str))
    return hashlib.sha256(assets_core._json(value).encode('utf-8')).hexdigest()


def _snippet(value):
    _require(isinstance(value, dict), 'public_recovery_snippet_invalid')
    result = {key: value[key] for key in _SNIPPET_FIELDS if key in value}
    _require(isinstance(result.get('title'), str) and 1 <= len(result['title']) <= 100
             and isinstance(result.get('description'), str) and len(result['description'].encode('utf-8')) <= 5000
             and isinstance(result.get('categoryId'), str) and re.fullmatch(r'[0-9]{1,3}', result['categoryId']),
             'public_recovery_snippet_invalid')
    for key in ('title', 'description'):
        _require(not any(ord(char) < 32 and char not in '\n\r\t' for char in result[key]),
                 'public_recovery_snippet_invalid')
    if 'tags' in result:
        _require(isinstance(result['tags'], list) and len(result['tags']) <= 100
                 and all(isinstance(tag, str) and 0 < len(tag) <= 100 for tag in result['tags'])
                 and sum(map(len, result['tags'])) <= 1000, 'public_recovery_snippet_invalid')
    for key in ('defaultLanguage', 'defaultAudioLanguage'):
        if key in result:
            _require(isinstance(result[key], str) and re.fullmatch(r'[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*', result[key])
                     and len(result[key]) <= 35, 'public_recovery_snippet_invalid')
    return result


def _metadata_plan(remote, note):
    original = _snippet(remote.get('writable_snippet'))
    etag = remote.get('etag')
    _require(isinstance(etag, str) and re.fullmatch(r'"[A-Za-z0-9_./=+\-]{1,256}"', etag),
             'public_recovery_etag_missing')
    requested = dict(original)
    if note not in original['description']:
        requested['description'] += ('\n\n' if original['description'] else '') + note
    _require(len(requested['description'].encode('utf-8')) <= 5000, 'public_recovery_description_too_long')
    delta = {'note': note, 'original_description_sha256': hashlib.sha256(original['description'].encode('utf-8')).hexdigest(),
             'new_description_sha256': hashlib.sha256(requested['description'].encode('utf-8')).hexdigest(),
             'original_snippet_sha256': assets_core._digest(original),
             'requested_snippet_sha256': assets_core._digest(requested), 'if_match': etag}
    return requested, delta


def _metadata_matches(remote, receipt):
    if receipt.get('reenactment_note') is None:
        return True
    try:
        snippet = _snippet(remote.get('writable_snippet'))
        delta = receipt['metadata_delta']
        return (receipt['reenactment_note'] in snippet['description']
                and hashlib.sha256(snippet['description'].encode('utf-8')).hexdigest() == delta['new_description_sha256']
                and assets_core._digest(snippet) == delta['requested_snippet_sha256'])
    except Exception:
        return False


def _semantic_identity(records):
    # These are newly recorded semantic identities, not normalized replacements
    # for the historical whole-job hashes. Only outer bookkeeping is omitted.
    def job(record):
        return {key: record.get(key) for key in (
            'task_id', 'kind', 'state', 'parent_id', 'spec', 'result', 'retry_child_task_id',
            'retry_claimed', 'repair_claimed', 'retry_dispatch_state')}
    return assets_core._digest({
        'source': job(records['source']), 'publisher': job(records['publisher']),
        'ledger': {key: value for key, value in records['ledger'].items() if key != 'updated_at'},
        'profile': {key: value for key, value in records['profile'].items() if key != 'updated_at'},
        'channel': {key: records['channel'].get(key) for key in ('id', 'connection_id', 'requires_reconnect')},
    })


def _asset_context(records, audit):
    _require(isinstance(audit, dict) and type(audit.get('version')) is int and audit['version'] == 1
             and audit.get('status') in {'assets_ready_needs_release_integration', 'assets_pending_verification'},
             'public_recovery_asset_audit_invalid')
    binding = assets_core._validate(records, audit.get('source_task_id'), audit.get('youtube_video_id'),
                                    audit.get('target_channel_id'), audit.get('profile_revision'))
    _require(all(audit.get(key) == binding[key] for key in _IDENTITY_FIELDS), 'public_recovery_asset_binding_changed')
    for key in ('source_snapshot_sha256', 'publisher_snapshot_sha256', 'ledger_snapshot_sha256'):
        _require(isinstance(audit.get(key), str) and assets_core._SHA.fullmatch(audit[key]),
                 'public_recovery_asset_audit_invalid')
    # This bounded path preserves the original AI disclosure, not a new or
    # silently changed classification of an immutable publish plan.
    _require(records['ledger']['publish_plan'].get('contains_synthetic_media') is True,
             'public_recovery_disclosure_invalid')
    manifest = audit.get('assets')
    _require(isinstance(manifest, dict))
    for name, maximum in (('caption', 256 * 1024), ('thumbnail', 2 * 1024 * 1024),
                          ('metadata', 1024 * 1024), ('final', 64 * 1024 * 1024)):
        item = manifest.get(name)
        _require(isinstance(item, dict) and isinstance(item.get('sha256'), str)
                 and assets_core._SHA.fullmatch(item['sha256']) and type(item.get('size')) is int
                 and 1 <= item['size'] <= maximum, 'public_recovery_asset_audit_invalid')
    _require(manifest['caption'].get('language') == binding['language'])
    caption, thumbnail = audit.get('caption'), audit.get('thumbnail')
    _require(isinstance(caption, dict) and isinstance(thumbnail, dict))
    _require(isinstance(caption.get('caption_id'), str) and assets_core._CAPTION_ID.fullmatch(caption['caption_id'])
             and ((caption.get('status') in {'verified', 'awaiting_processing'}
                   and type(caption.get('attempts')) is int and caption['attempts'] == 1)
                  or (caption.get('status') == 'verified_existing' and type(caption.get('attempts')) is int
                      and caption['attempts'] == 0 and records['source']['result']['youtube']['caption_uploaded'] is True)),
             'public_recovery_caption_unverified')
    if thumbnail.get('status') == 'verified':
        _require(type(thumbnail.get('attempts')) is int and thumbnail['attempts'] == 1
                 and isinstance(thumbnail.get('returned_sizes'), list) and bool(thumbnail['returned_sizes'])
                 and thumbnail.get('receipt_sha256') == assets_core._digest(thumbnail['returned_sizes']),
                 'public_recovery_thumbnail_unverified')
    else:
        _require(thumbnail.get('status') == 'verified_existing' and type(thumbnail.get('attempts')) is int
                 and thumbnail['attempts'] == 0 and records['source']['result']['youtube']['thumbnail_uploaded'] is True
                 and not records['source']['result']['youtube'].get('thumbnail_error_code'),
                 'public_recovery_thumbnail_unverified')
    return {key: binding[key] for key in _IDENTITY_FIELDS}


def _identity(records, audit, credential, epoch):
    return {**_asset_context(records, audit), 'asset_audit_sha256': assets_core._digest(audit),
            'semantic_identity_sha256': _semantic_identity(records),
            'credential_sha256': _hash_text(credential), 'authorization_epoch_sha256': _hash_text(epoch)}


def _receipt_identity(receipt, identity):
    _require(isinstance(receipt, dict) and type(receipt.get('version')) is int and receipt['version'] == 1
             and all(receipt.get(key) == value for key, value in identity.items())
             and receipt.get('status') in _STATUSES and type(receipt.get('attempts')) is int
             and receipt['attempts'] in (0, 1) and type(receipt.get('side_effect_possible')) is bool
             and receipt['side_effect_possible'] is (receipt['attempts'] == 1),
             'public_recovery_receipt_binding_changed')
    note = receipt.get('reenactment_note')
    _require(note is None or note == REENACTMENT_NOTES.get(identity['language']), 'public_recovery_note_invalid')
    if receipt['attempts'] == 0:
        _require(receipt['status'] in {'assets_pending_verification', 'assets_ready'})
    else:
        _require(receipt['status'] in {'reserved_before_http', 'uncertain', 'rejected', 'public'})
        expected_request = {
            'youtube_video_id': identity['youtube_video_id'], 'privacy_status': 'public',
            'contains_synthetic_media': True, 'self_declared_made_for_kids': False,
        }
        if note is not None:
            _requested, delta = _metadata_plan(receipt.get('prior_private_proof', {}), note)
            _require(receipt.get('metadata_delta') == delta, 'public_recovery_metadata_delta_invalid')
            expected_request.update(snippet_sha256=delta['requested_snippet_sha256'], if_match=delta['if_match'])
        _require(receipt.get('release_request') == expected_request, 'public_recovery_request_invalid')
        _require(isinstance(receipt.get('reserved_at'), str) and bool(receipt['reserved_at']),
                 'public_recovery_request_invalid')
        prior = receipt.get('prior_private_proof')
        _require(isinstance(prior, dict) and prior.get('youtube_video_id') == identity['youtube_video_id']
                 and prior.get('target_channel_id') == identity['target_channel_id']
                 and prior.get('privacy_status') == 'private' and prior.get('upload_status') == 'processed'
                 and prior.get('scheduled_publish_at') is None, 'public_recovery_prior_private_proof_invalid')


def _public_proof(proof, binding):
    return bool(isinstance(proof, dict) and proof.get('youtube_video_id') == binding['youtube_video_id']
                and proof.get('target_channel_id') == binding['target_channel_id']
                and proof.get('privacy_status') == 'public' and proof.get('upload_status') == 'processed'
                and proof.get('contains_synthetic_media') is True and proof.get('scheduled_publish_at') is None
                and isinstance(proof.get('verified_at'), str) and bool(proof['verified_at']))


def validate_public_recovery_receipt(records, asset_audit, receipt, *, credential_cipher, authorization_epoch):
    """Pure proof consumer; callers must WATCH these same records through use."""
    try:
        _require(isinstance(credential_cipher, str) and bool(credential_cipher), 'public_recovery_connection_missing')
        identity = _identity(records, asset_audit, credential_cipher, authorization_epoch)
        _receipt_identity(receipt, identity)
        _require(receipt['status'] == 'public' and receipt['attempts'] == 1
                 and _public_proof(receipt.get('public_proof'), identity)
                 and _metadata_matches(receipt.get('public_proof'), receipt)
                 and receipt.get('confirmation') in {'post_request_readback', 'readback_after_reserved_attempt'},
                 'public_recovery_not_confirmed')
        caption = receipt.get('caption_proof')
        _require(isinstance(caption, dict) and caption.get('caption_id') == asset_audit['caption']['caption_id']
                 and caption.get('language') == identity['language'] and caption.get('status') == 'serving'
                 and caption.get('track_kind') == 'standard' and caption.get('is_draft') is False,
                 'public_recovery_caption_unverified')
        _require(receipt.get('asset_fingerprints') == asset_audit['assets']
                 and receipt.get('thumbnail_verified') is True, 'public_recovery_assets_changed')
        _require(receipt.get('historical_snapshot_hashes') == {name: asset_audit[name] for name in (
            'source_snapshot_sha256', 'publisher_snapshot_sha256', 'ledger_snapshot_sha256')},
            'public_recovery_history_changed')
        return {key: identity[key] for key in _IDENTITY_FIELDS} | {
            'privacy_status': 'public', 'release_status': 'public', 'contains_synthetic_media': True,
            'caption_uploaded': True, 'thumbnail_uploaded': True,
            'receipt_sha256': assets_core._digest(receipt),
        }
    except BlockedPublicReleaseError:
        raise
    except Exception:
        raise BlockedPublicReleaseError('public_recovery_receipt_invalid') from None


def get_verified_public_recovery_for_source(source_task_id):
    """Cached proof only, for presentation. No Google, Storage, or state writes."""
    try:
        _require(isinstance(source_task_id, str) and assets_core._TASK.fullmatch(source_task_id))
        client = assets_core._redis()
        key = PUBLIC_RECOVERY_PREFIX + source_task_id
        raw = client.get(key)
        if raw is None:
            return None
        receipt = assets_core._object(raw)
        channel_id = receipt.get('target_channel_id')
        _require(isinstance(channel_id, str) and assets_core._ID.fullmatch(channel_id))
        records, snapshots = assets_core._read(client, source_task_id, channel_id)
        audit_key = assets_core.RECOVERY_PREFIX + source_task_id
        audit_raw = client.get(audit_key)
        snapshots.update({key: raw, audit_key: audit_raw})
        proof = validate_public_recovery_receipt(records, assets_core._object(audit_raw), receipt,
            credential_cipher=snapshots[assets_core.CREDENTIAL_PREFIX + channel_id],
            authorization_epoch=snapshots[assets_core.AUTH_EPOCH_KEY])
        with client.pipeline() as pipe:
            pipe.watch(*snapshots, assets_core.CHANNEL_INDEX_KEY)
            _require(all(pipe.get(k) == value for k, value in snapshots.items())
                     and pipe.sismember(assets_core.CHANNEL_INDEX_KEY, channel_id))
            pipe.multi()
            pipe.ping()
            pipe.execute()
        return proof
    except Exception:
        return None


def _remote(service, binding, *, include_metadata=False):
    channels = service.channels().list(part='id', mine=True, maxResults=50).execute(num_retries=0)
    items = channels.get('items') if isinstance(channels, dict) else None
    _require(isinstance(items, list) and len(items) <= 50 and sum(
        isinstance(item, dict) and item.get('id') == binding['target_channel_id'] for item in items) == 1,
        'public_recovery_remote_channel_changed')
    response = service.videos().list(part='snippet,status', id=binding['youtube_video_id'], maxResults=1).execute(num_retries=0)
    items = response.get('items') if isinstance(response, dict) else None
    _require(isinstance(items, list) and len(items) == 1 and isinstance(items[0], dict),
             'public_recovery_remote_video_invalid')
    item = items[0]
    status, snippet = item.get('status'), item.get('snippet')
    _require(item.get('id') == binding['youtube_video_id'] and isinstance(snippet, dict)
             and snippet.get('channelId') == binding['target_channel_id'] and isinstance(status, dict)
             and status.get('uploadStatus') == 'processed' and not status.get('publishAt'),
             'public_recovery_remote_video_invalid')
    proof = {'youtube_video_id': item['id'], 'target_channel_id': snippet['channelId'],
            'privacy_status': status.get('privacyStatus'), 'upload_status': 'processed',
            'contains_synthetic_media': status.get('containsSyntheticMedia'),
            'scheduled_publish_at': None, 'verified_at': assets_core._now()}
    if include_metadata:
        etag = item.get('etag')
        _require(isinstance(etag, str) and re.fullmatch(r'"?[A-Za-z0-9_./=+\-]{1,256}"?', etag)
                 and etag.startswith('"') == etag.endswith('"'), 'public_recovery_etag_missing')
        proof.update(writable_snippet=_snippet(snippet), etag=etag if etag.startswith('"') else '"' + etag + '"')
    return proof


def _caption(service, binding, audit):
    tracks = assets_core._caption_tracks(service, binding['youtube_video_id'])
    matches = [item for item in tracks if item.get('id') == audit['caption']['caption_id']]
    _require(len(matches) <= 1, 'public_recovery_caption_ambiguous')
    if not matches:
        return None
    item = matches[0]
    _require(assets_core._caption_identity(item, binding['youtube_video_id'], binding['language'],
                                         binding['language'].upper() + ' captions'),
             'public_recovery_caption_changed')
    if item['snippet'].get('status') != 'serving':
        return None
    return {'caption_id': item['id'], 'language': binding['language'], 'status': 'serving',
            'track_kind': 'standard', 'is_draft': False, 'verified_at': assets_core._now()}


def _prepare(records, audit, source_id, work_dir):
    from app.services.publication_recovery_assets import prepare_publication_recovery_assets
    prepared = prepare_publication_recovery_assets(records['source'], source_task_id=source_id, work_dir=work_dir)
    observed = assets_core._asset_manifest(prepared, records['source']['spec']['language'])
    _require(observed == audit['assets'], 'public_recovery_asset_fingerprint_changed')
    return observed


def _outcome(receipt):
    return {'status': receipt['status'], 'source_task_id': receipt['source_task_id'],
            'publish_task_id': receipt['publish_task_id'], 'youtube_video_id': receipt['youtube_video_id'],
            'target_channel_id': receipt['target_channel_id'], 'profile_revision': receipt['profile_revision'],
            'release_attempts': receipt['attempts'], 'public_confirmed': receipt['status'] == 'public',
            'receipt_sha256': assets_core._digest(receipt)}


def _invalidate_public_receipt(client, key, previous, remote, caption):
    # A negative observation can only invalidate the exact old receipt. Unlike
    # promotion, it does not depend on a concurrent job's bookkeeping fields;
    # a harmless updated_at change must not preserve known-stale approval.
    receipt = assets_core._object(previous)
    _require(receipt.get('status') == 'public' and receipt.get('attempts') == 1)
    receipt.update(status='uncertain', last_remote_observation=remote, caption_proof=caption,
                   confirmation_changed_at=assets_core._now())
    with client.pipeline() as pipe:
        pipe.watch(key)
        _require(pipe.get(key) == previous, 'public_recovery_receipt_changed')
        pipe.multi()
        pipe.set(key, assets_core._json(receipt))
        pipe.execute()
    return receipt


def settle_blocked_public_assets(source_task_id, expected_video_id, expected_channel_id,
                                 expected_profile_revision, *, work_dir=None, reenactment_note=None):
    """Only remote GETs: settle captions or a prior uncertain public attempt."""
    return _run(source_task_id, expected_video_id, expected_channel_id, expected_profile_revision,
                work_dir=work_dir, request_release=False, reenactment_note=reenactment_note)


def release_blocked_public_video(source_task_id, expected_video_id, expected_channel_id,
                                 expected_profile_revision, *, work_dir, reenactment_note=None):
    """One durable public request; later invocations perform only read-back."""
    return _run(source_task_id, expected_video_id, expected_channel_id, expected_profile_revision,
                work_dir=work_dir, request_release=True, reenactment_note=reenactment_note)


def _run(source_id, video_id, channel_id, revision, *, work_dir, request_release, reenactment_note):
    _require(isinstance(source_id, str) and assets_core._TASK.fullmatch(source_id)
             and isinstance(video_id, str) and assets_core._VIDEO.fullmatch(video_id)
             and all(isinstance(value, str) and assets_core._ID.fullmatch(value) for value in (channel_id, revision)),
             'public_recovery_input_invalid')
    _require(reenactment_note is None or isinstance(reenactment_note, str)
             and reenactment_note in REENACTMENT_NOTES.values(), 'public_recovery_note_invalid')
    service = lock_token = None
    try:
        client = assets_core._redis()
        records, snapshots = assets_core._read(client, source_id, channel_id)
        audit_key, key = assets_core.RECOVERY_PREFIX + source_id, PUBLIC_RECOVERY_PREFIX + source_id
        raw_audit = client.get(audit_key)
        audit = assets_core._object(raw_audit)
        snapshots[audit_key] = raw_audit
        credential, epoch = snapshots[assets_core.CREDENTIAL_PREFIX + channel_id], snapshots[assets_core.AUTH_EPOCH_KEY]
        identity = _identity(records, audit, credential, epoch)
        _require(identity['source_task_id'] == source_id and identity['youtube_video_id'] == video_id
                 and identity['target_channel_id'] == channel_id and identity['profile_revision'] == revision)
        previous = client.get(key)
        receipt = assets_core._object(previous) if previous is not None else None
        if receipt is not None:
            _receipt_identity(receipt, identity)
            if receipt['attempts'] == 1:
                _require(reenactment_note is None or reenactment_note == receipt.get('reenactment_note'),
                         'public_recovery_note_changed_after_attempt')
            reenactment_note = reenactment_note or receipt.get('reenactment_note')
        _require(reenactment_note is None or reenactment_note == REENACTMENT_NOTES[identity['language']],
                 'public_recovery_note_language_mismatch')
        lock_token = assets_core.acquire_execution_lock(source_id, identity['publish_task_id'])
        snapshots[assets_core.EXECUTION_LOCK_PREFIX + source_id] = lock_token
        if receipt is None or receipt['attempts'] == 0:
            _require(work_dir is not None, 'public_recovery_fresh_work_directory_required')
            fingerprints = _prepare(records, audit, source_id, work_dir)
        else:
            fingerprints = receipt.get('asset_fingerprints')
            _require(fingerprints == audit['assets'], 'public_recovery_asset_fingerprint_changed')
        service = assets_core._service(assets_core._credentials(credential, identity))
        remote = (_remote(service, identity, include_metadata=True) if reenactment_note else _remote(service, identity))
        caption = _caption(service, identity, audit)
        if receipt is None:
            receipt = {'version': 1, **identity, 'attempts': 0, 'side_effect_possible': False,
                       'status': 'assets_pending_verification', 'created_at': assets_core._now(),
                       'historical_snapshot_hashes': {name: audit[name] for name in (
                           'source_snapshot_sha256', 'publisher_snapshot_sha256', 'ledger_snapshot_sha256')}}
        receipt.update(asset_fingerprints=fingerprints, thumbnail_verified=True, caption_proof=caption,
                       checked_at=assets_core._now())
        if reenactment_note is not None:
            receipt['reenactment_note'] = reenactment_note
        if receipt['attempts'] == 1:
            # A conclusive GET can settle a lost update response, but never
            # authorizes another update when the target is still private.
            if _public_proof(remote, identity) and caption is not None and _metadata_matches(remote, receipt):
                receipt.update(status='public', public_proof=remote, confirmation='readback_after_reserved_attempt')
            elif receipt['status'] == 'public':
                # Preserve the earlier evidence as history, but do not leave a
                # usable public receipt after a conclusive contrary GET.
                return _outcome(_invalidate_public_receipt(client, key, previous, remote, caption))
            elif receipt['status'] != 'rejected':
                receipt['status'] = 'uncertain'
            assets_core._commit(client, snapshots, channel_id, key, previous, receipt)
            return _outcome(receipt)
        _require(remote['privacy_status'] == 'private', 'public_recovery_target_not_private')
        receipt['status'] = 'assets_ready' if caption is not None else 'assets_pending_verification'
        previous = assets_core._commit(client, snapshots, channel_id, key, previous, receipt)
        if caption is None or not request_release:
            return _outcome(receipt)
        requested_snippet = None
        if reenactment_note is not None:
            requested_snippet, delta = _metadata_plan(remote, reenactment_note)
            receipt['metadata_delta'] = delta
        receipt.update(status='reserved_before_http', attempts=1, side_effect_possible=True,
                       reserved_at=assets_core._now(), prior_private_proof=remote,
                       release_request={'youtube_video_id': video_id, 'privacy_status': 'public',
                                        'contains_synthetic_media': True, 'self_declared_made_for_kids': False})
        if requested_snippet is not None:
            receipt['release_request'].update(snippet_sha256=delta['requested_snippet_sha256'], if_match=delta['if_match'])
        previous = assets_core._commit(client, snapshots, channel_id, key, previous, receipt)
        request_error = None
        try:
            body = {'id': video_id, 'status': {'privacyStatus': 'public', 'containsSyntheticMedia': True,
                                              'selfDeclaredMadeForKids': False}}
            if requested_snippet is not None:
                body['snippet'] = requested_snippet
            request = service.videos().update(part='status,snippet' if requested_snippet is not None else 'status', body=body)
            if requested_snippet is not None:
                _require(isinstance(request.headers, dict), 'public_recovery_conditional_update_unavailable')
                request.headers['If-Match'] = delta['if_match']
            response = request.execute(num_retries=0)
            status = response.get('status') if isinstance(response, dict) else None
            receipt['update_response_verified'] = bool(isinstance(status, dict) and response.get('id') == video_id
                and status.get('privacyStatus') == 'public' and status.get('containsSyntheticMedia') is True
                and not status.get('publishAt'))
        except Exception as exc:
            request_error = assets_core._safe_http_error(exc)
            if request_error.get('http_status') == 412:
                request_error['reason'] = 'precondition_failed'
            receipt['request_error'] = request_error
        # Even a complete update response is followed by an exact same-channel
        # read-back. Incomplete responses do not trigger another write.
        try:
            remote = (_remote(service, identity, include_metadata=True) if reenactment_note else _remote(service, identity))
            caption = _caption(service, identity, audit)
            if _public_proof(remote, identity) and caption is not None and _metadata_matches(remote, receipt):
                receipt.update(status='public', public_proof=remote, caption_proof=caption,
                               confirmation='post_request_readback')
            else:
                receipt['status'] = 'rejected' if request_error and request_error['status'] == 'rejected' else 'uncertain'
        except Exception:
            receipt['status'] = 'uncertain'
        receipt['checked_at'] = assets_core._now()
        assets_core._commit(client, snapshots, channel_id, key, previous, receipt)
        if receipt['status'] == 'public':
            validate_public_recovery_receipt(records, audit, receipt,
                                            credential_cipher=credential, authorization_epoch=epoch)
        return _outcome(receipt)
    except BlockedPublicReleaseError:
        raise
    except Exception:
        raise BlockedPublicReleaseError('public_recovery_unavailable_or_uncertain') from None
    finally:
        if service is not None:
            try:
                service.close()
            except Exception:
                pass
        if lock_token:
            assets_core.release_execution_lock(source_id, lock_token)
