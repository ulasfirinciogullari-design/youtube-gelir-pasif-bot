"""Owner-only source hold; preserve render work and fence old upload dispatchers."""
from datetime import datetime, timezone
import hashlib
import math
import re
import time
from uuid import UUID

from app.services import studio_state
from app.services.external_artifact_import import _json, _object, _text
from app.services.youtube_automation import PROFILE_PREFIX
from app.services.youtube_auth import CHANNEL_PREFIX
from app.services.youtube_publish_state import UPLOAD_PREFIX, EXECUTION_LOCK_PREFIX


HOLD_PREFIX = 'youtube_studio:source_publication_hold:v1:'
_ID = re.compile(r'[A-Za-z0-9_-]{8,128}')


class SourcePublicationHoldError(ValueError):
    """Fixed safe error; never a provider or credential response."""


def _require(value):
    if not value:
        raise SourcePublicationHoldError('publication_hold_not_eligible')


def _digest(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _fence(receipt):
    return {'version': 2, 'status': 'held_by_owner', 'source_task_id': receipt['task_id'],
            'target_channel_id': receipt['request']['expected_channel_id'],
            'connection_id': receipt['connection_id'], 'side_effect_possible': False,
            'release_side_effect_possible': False, 'owner_hold_receipt_key': HOLD_PREFIX + receipt['task_id'],
            'owner_hold_receipt_sha256': receipt['receipt_sha256'],
            'created_at': receipt['held_at'], 'updated_at': receipt['held_at']}


def hold_source_publication(task_id, expected_channel_id, expected_profile_revision, reason, *, now=None):
    """No cancel/revoke, media writes, task enqueue, profile or series mutation.

    The upload tombstone, not the advisory stored spec, is authoritative: older
    progress writers/redelivery may restore their captured spec. Existing
    reserve_upload refuses every held_by_owner record before a publisher exists.
    """
    try:
        _require(type(task_id) is str and str(UUID(task_id)) == task_id)
        _require(all(type(value) is str and _ID.fullmatch(value)
                     for value in (expected_channel_id, expected_profile_revision)))
        _text(reason, 800); _require(len(reason) >= 12)
        now = time.time() if now is None else now
        _require(type(now) in (int, float) and math.isfinite(now) and now >= 0)
        request = {'expected_channel_id': expected_channel_id,
                   'expected_profile_revision': expected_profile_revision, 'reason': reason}
        key, upload_key = HOLD_PREFIX + task_id, UPLOAD_PREFIX + task_id
        job_key = studio_state.JOB_PREFIX + task_id
        profile_key, channel_key = PROFILE_PREFIX + expected_channel_id, CHANNEL_PREFIX + expected_channel_id
        client = studio_state._client()
        with client.pipeline() as pipe:
            pipe.watch(key, upload_key, job_key, EXECUTION_LOCK_PREFIX + task_id,
                       studio_state.JOB_INDEX, profile_key, channel_key)
            prior = pipe.get(key)
            if prior is not None:
                receipt = _object(prior, limit=8192)
                _require(receipt.get('version') == 1 and receipt.get('task_id') == task_id
                         and receipt.get('request') == request
                         and receipt.get('disposition') == 'owner_publication_hold'
                         and receipt.get('original_publish_after_render') is True
                         and receipt.get('receipt_sha256') == _digest({k: v for k, v in receipt.items() if k != 'receipt_sha256'})
                         and _object(pipe.get(upload_key), limit=8192) == _fence(receipt))
                return {'status': 'already_held', 'task_id': task_id, 'channel_id': expected_channel_id,
                        'profile_revision': expected_profile_revision}
            _require(not pipe.exists(upload_key) and not pipe.exists(EXECUTION_LOCK_PREFIX + task_id))
            job = _object(pipe.get(job_key), limit=2 * 1024 * 1024)
            spec = job.get('spec') or {}; result = job.get('result') or {}
            profile = _object(pipe.get(profile_key), limit=65536)
            channel = _object(pipe.get(channel_key), limit=65536)
            _require(job.get('task_id') == task_id and job.get('kind') == 'render'
                     and job.get('state') in {'PENDING', 'PROGRESS', 'SUCCESS', 'FAILURE'}
                     and type(spec) is dict and spec.get('mode') == 'production'
                     and spec.get('publish_after_render') is True and not job.get('publication_hold')
                     and spec.get('production_channel_id') == expected_channel_id
                     and spec.get('production_profile_revision') == expected_profile_revision
                     and profile.get('channel_id') == expected_channel_id
                     and profile.get('profile_revision') == expected_profile_revision
                     and channel.get('id') == expected_channel_id
                     and type(channel.get('connection_id')) is str and _ID.fullmatch(channel['connection_id'])
                     and spec.get('production_connection_id') == channel['connection_id']
                     and not result.get('youtube') and not result.get('youtube_video_id')
                     and not (result.get('youtube_automation') or {}).get('publish_task_id'))
            # Also reject a linked publisher whose upload record is missing.
            _require(pipe.type(studio_state.JOB_INDEX) in {'none', 'zset'})
            _require(pipe.zcard(studio_state.JOB_INDEX) <= studio_state.MAX_INDEXED_JOBS)
            for identifier in pipe.zrange(studio_state.JOB_INDEX, 0, -1):
                candidate_key = studio_state.JOB_PREFIX + identifier
                pipe.watch(candidate_key)
                raw = pipe.get(candidate_key)
                if raw is None:
                    continue
                candidate = _object(raw, limit=2 * 1024 * 1024)
                _require(not (candidate.get('kind') == 'publish' and (
                    candidate.get('parent_id') == task_id
                    or (candidate.get('spec') or {}).get('source_task_id') == task_id
                    or (candidate.get('result') or {}).get('source_task_id') == task_id)))
            receipt = {'version': 1, 'task_id': task_id, 'request': request,
                       'disposition': 'owner_publication_hold', 'connection_id': channel['connection_id'],
                       'held_at': datetime.fromtimestamp(now, timezone.utc).isoformat(),
                       'original_publish_after_render': True, 'original_spec_sha256': _digest(spec)}
            receipt['receipt_sha256'] = _digest(receipt)
            held = {**job, 'spec': {**spec, 'publish_after_render': False},
                    'publication_hold': {'receipt_key': key, 'receipt_sha256': receipt['receipt_sha256']}}
            pipe.multi()
            pipe.set(job_key, _json(held), keepttl=True)
            pipe.set(key, _json(receipt))
            pipe.set(upload_key, _json(_fence(receipt)))
            pipe.execute()
        return {'status': 'held', 'task_id': task_id, 'channel_id': expected_channel_id,
                'profile_revision': expected_profile_revision}
    except Exception:
        raise SourcePublicationHoldError('publication_hold_not_eligible') from None
