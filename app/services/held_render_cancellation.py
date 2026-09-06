"""Owner cancellation of a publication-held, unregistered orphan retry.

The request is a durable entry fence, not proof of stopped execution. Only a
subsequent complete server-owned worker inventory can close the job. Removed
deployment/current-worker coverage remains explicitly owner-attested.
"""
from datetime import datetime, timezone
import math
import re
import time
from uuid import UUID

from app.services import studio_state as state
from app.services.external_artifact_import import _json, _object, _text
from app.services.source_publication_hold import HOLD_PREFIX, _ID, _digest, _fence
from app.services.channel_production import ACTIVE_KEY, CHANNEL_STATE_PREFIX, _decode_active_claims
from app.services.youtube_automation import PROFILE_PREFIX
from app.services.youtube_auth import CHANNEL_PREFIX
from app.services.youtube_publish_state import UPLOAD_PREFIX, EXECUTION_LOCK_PREFIX


CANCELLATION_PREFIX = state.RENDER_CANCELLATION_PREFIX
_ACTIVE = {'PENDING', 'RECEIVED', 'STARTED', 'RETRY', 'PROGRESS'}


class HeldRenderCancellationError(ValueError):
    """Safe fixed failure, with no worker payloads or credentials."""


def _require(value):
    if not value:
        raise HeldRenderCancellationError('held_render_cancellation_not_eligible')


def validate_cancellation_request(task_id, value):
    _require(type(task_id) is str and str(UUID(task_id)) == task_id)
    _require(type(value) is dict and set(value) == {
        'expected_channel_id', 'expected_profile_revision', 'reason',
        'retired_worker_deployment_id', 'expected_worker_names'})
    _require(all(type(value[k]) is str and _ID.fullmatch(value[k])
                 for k in ('expected_channel_id', 'expected_profile_revision')))
    _text(value['reason'], 800); _require(len(value['reason']) >= 12)
    deployment = value['retired_worker_deployment_id']
    _require(type(deployment) is str and str(UUID(deployment)) == deployment)
    workers = value['expected_worker_names']
    _require(type(workers) is list and 1 <= len(workers) <= 8)
    for worker in workers:
        _text(worker, 200); _require('@' in worker and not any(c.isspace() for c in worker))
    _require(len(set(workers)) == len(workers) and workers == sorted(workers))
    return value


def _read(pipe, key, *, optional=False):
    pipe.watch(key)
    raw = pipe.get(key)
    return None if optional and raw is None else _object(raw, limit=2 * 1024 * 1024)


def _context(pipe, task_id, request):
    channel_id = request['expected_channel_id']
    job = _read(pipe, state.JOB_PREFIX + task_id)
    spec, result = job.get('spec') or {}, job.get('result') or {}
    _require(job.get('task_id') == task_id and job.get('kind') == 'render'
             and job.get('state') in _ACTIVE and type(spec) is dict and type(result) is dict
             and spec.get('mode') == 'production'
             and spec.get('production_channel_id') == channel_id
             and spec.get('production_profile_revision') == request['expected_profile_revision']
             and not job.get('retry_child_task_id') and not job.get('owner_cancellation')
             and not result.get('youtube') and not result.get('youtube_video_id')
             and not (result.get('youtube_automation') or {}).get('publish_task_id'))
    profile = _read(pipe, PROFILE_PREFIX + channel_id)
    channel = _read(pipe, CHANNEL_PREFIX + channel_id)
    _require(profile.get('channel_id') == channel_id
             and profile.get('profile_revision') == request['expected_profile_revision']
             and channel.get('id') == channel_id and not channel.get('requires_reconnect')
             and type(channel.get('connection_id')) is str and _ID.fullmatch(channel['connection_id'])
             and spec.get('production_connection_id') == channel['connection_id'])
    hold = _read(pipe, HOLD_PREFIX + task_id)
    _require(hold.get('version') == 1 and hold.get('task_id') == task_id
             and hold.get('disposition') == 'owner_publication_hold'
             and hold.get('original_publish_after_render') is True
             and hold.get('connection_id') == channel['connection_id']
             and (hold.get('request') or {}).get('expected_channel_id') == channel_id
             and (hold.get('request') or {}).get('expected_profile_revision') == request['expected_profile_revision']
             and hold.get('receipt_sha256') == _digest({k: v for k, v in hold.items() if k != 'receipt_sha256'})
             and hold.get('original_spec_sha256') == _digest({**spec, 'publish_after_render': True})
             and _read(pipe, UPLOAD_PREFIX + task_id) == _fence(hold))
    pipe.watch(EXECUTION_LOCK_PREFIX + task_id, ACTIVE_KEY, CHANNEL_STATE_PREFIX + channel_id)
    _require(not pipe.exists(EXECUTION_LOCK_PREFIX + task_id)
             and not pipe.hget(CHANNEL_STATE_PREFIX + channel_id, 'active_task_id'))
    active = pipe.get(ACTIVE_KEY)
    if active is not None:
        _require(all(c['task_id'] != task_id and c['channel_id'] != channel_id
                     for c in _decode_active_claims(active)))
    parent_id = job.get('parent_id')
    _require(type(parent_id) is str and str(UUID(parent_id)) == parent_id and parent_id != task_id)
    parent = _read(pipe, state.JOB_PREFIX + parent_id)
    claim_key, dispatch_key = state.RETRY_CHILD_CLAIM_PREFIX + task_id, state.RETRY_DISPATCH_PREFIX + parent_id
    pipe.watch(claim_key, dispatch_key, state.RETRY_CHILD_EXECUTION_PREFIX + task_id,
               state.PAID_CREATE_BUDGET_PREFIX + task_id, state.RETRY_DISPATCH_PREFIX + task_id,
               state.EXTERNAL_EPISODE_LEAF_PREFIX + task_id)
    claim, dispatch = pipe.hgetall(claim_key), pipe.hgetall(dispatch_key)
    token = claim.get('token')
    _require(parent.get('state') == 'FAILURE' and parent.get('kind') == 'render'
             and parent.get('retry_child_task_id') == task_id
             and claim.get('source_task_id') == parent_id and type(token) is str
             and re.fullmatch(r'[A-Za-z0-9_-]{16,256}', token)
             and dispatch.get('child_task_id') == task_id and dispatch.get('token') == token
             and pipe.get(state.RETRY_CHILD_EXECUTION_PREFIX + task_id) == token
             and not pipe.exists(state.RETRY_DISPATCH_PREFIX + task_id)
             and not pipe.exists(state.EXTERNAL_EPISODE_LEAF_PREFIX + task_id))
    pipe.watch(state.JOB_INDEX)
    _require(pipe.type(state.JOB_INDEX) == 'zset' and pipe.zcard(state.JOB_INDEX) <= state.MAX_INDEXED_JOBS
             and pipe.zscore(state.JOB_INDEX, task_id) is not None)
    for other_id in pipe.zrange(state.JOB_INDEX, 0, -1):
        if other_id == task_id:
            continue
        other = _read(pipe, state.JOB_PREFIX + other_id, optional=True)
        if other is None:
            continue
        other_spec = other.get('spec') or {}
        _require(not (other.get('kind') == 'publish' and (
            other.get('parent_id') == task_id or other_spec.get('source_task_id') == task_id
            or (other.get('result') or {}).get('source_task_id') == task_id)))
        _require(not (other.get('state') in _ACTIVE and other_spec.get('production_channel_id') == channel_id))
    # View counts / analytics refreshes do not change the authorized identity.
    channel_identity = {k: channel.get(k) for k in ('id', 'connection_id', 'title', 'description')}
    channel_identity['requires_reconnect'] = bool(channel.get('requires_reconnect'))
    binding = {'spec_sha256': hold['original_spec_sha256'], 'hold_sha256': hold['receipt_sha256'],
               'profile_sha256': _digest(profile), 'channel_sha256': _digest(channel_identity),
               'parent_task_id': parent_id, 'claim_sha256': _digest(claim), 'dispatch_sha256': _digest(dispatch)}
    return job, binding


def _worker_inventory(task_id, workers):
    # No caller-supplied inventory, no revoke/terminate and no lease-expiry inference.
    from app.celery_app import celery
    inspector = celery.control.inspect(timeout=3.0)
    counts = {}
    for method in ('active', 'reserved', 'scheduled'):
        reply = getattr(inspector, method)()
        _require(type(reply) is dict and set(reply) == set(workers))
        counts[method] = {}
        for worker, rows in reply.items():
            _require(type(rows) is list and len(rows) <= 500)
            for row in rows:
                _require(type(row) is dict)
                item = row.get('request') if method == 'scheduled' else row
                _require(type(item) is dict and type(item.get('id')) is str and item['id'] != task_id)
            counts[method][worker] = len(rows)
    return {'scope': 'owner_attested_complete_current_worker_names', 'task_absent': True,
            'counts': counts, 'observed_at': datetime.now(timezone.utc).isoformat()}


def _summary(task_id, request, status):
    return {'status': status, 'task_id': task_id, 'channel_id': request['expected_channel_id'],
            'profile_revision': request['expected_profile_revision']}


def cancel_held_render(task_id, expected_channel_id, expected_profile_revision, reason,
                       retired_worker_deployment_id, expected_worker_names, *, now=None):
    """Request fence -> inspect -> CAS terminal cancellation; never dispatch a task.

    Exact request replay completes a pending fence after an uncertain outcome.
    An unavailable/active inventory leaves cancel_requested, not CANCELLED.
    """
    try:
        request = validate_cancellation_request(task_id, {
            'expected_channel_id': expected_channel_id, 'expected_profile_revision': expected_profile_revision,
            'reason': reason, 'retired_worker_deployment_id': retired_worker_deployment_id,
            'expected_worker_names': expected_worker_names})
        now = time.time() if now is None else now
        _require(type(now) in (int, float) and math.isfinite(now) and now >= 0)
        key, client = CANCELLATION_PREFIX + task_id, state._client()
        with client.pipeline() as pipe:
            prior = _read(pipe, key, optional=True)
            if prior is not None:
                _require(prior.get('version') == 1 and prior.get('task_id') == task_id
                         and prior.get('request') == request)
                if prior.get('status') == 'cancelled':
                    current = _read(pipe, state.JOB_PREFIX + task_id)
                    _require(prior.get('receipt_sha256') == _digest({k: v for k, v in prior.items() if k != 'receipt_sha256'})
                             and current.get('state') == 'CANCELLED'
                             and current.get('owner_cancellation') == {'receipt_key': key, 'receipt_sha256': prior['receipt_sha256']})
                    return _summary(task_id, request, 'already_cancelled')
                _require(prior.get('status') == 'cancel_requested')
            job, binding = _context(pipe, task_id, request)
            if prior is None:
                prior = {'version': 1, 'task_id': task_id, 'request': request, 'status': 'cancel_requested',
                         'requested_at': datetime.fromtimestamp(now, timezone.utc).isoformat(), 'binding': binding,
                         'retired_deployment_evidence': 'owner_attested_removed_not_server_verified'}
                pipe.multi(); pipe.set(key, _json(prior)); pipe.execute()
            else:
                _require(prior.get('binding') == binding)
        inventory = _worker_inventory(task_id, expected_worker_names)
        with client.pipeline() as pipe:
            _require(_read(pipe, key) == prior)
            job, binding = _context(pipe, task_id, request)
            _require(binding == prior['binding'])
            receipt = {**prior, 'status': 'cancelled', 'disposition': 'owner_cancelled_held_orphan_retry',
                       'worker_inventory': inventory, 'cancelled_at': datetime.now(timezone.utc).isoformat(),
                       'original_job_sha256': _digest(job),
                       'original_status': {k: job.get(k) for k in ('state', 'stage', 'progress', 'message', 'updated_at')}}
            receipt['receipt_sha256'] = _digest(receipt)
            cancelled = {**job, 'state': 'CANCELLED', 'stage': 'cancelled',
                         'message': 'Sahibi tarafından iptal edildi; mevcut çıktı ve harcama kayıtları korunuyor.',
                         'updated_at': receipt['cancelled_at'],
                         'owner_cancellation': {'receipt_key': key, 'receipt_sha256': receipt['receipt_sha256']}}
            pipe.multi(); pipe.set(key, _json(receipt)); pipe.set(state.JOB_PREFIX + task_id, _json(cancelled), keepttl=True)
            pipe.execute()
        return _summary(task_id, request, 'cancelled')
    except Exception:
        raise HeldRenderCancellationError('held_render_cancellation_not_eligible') from None
