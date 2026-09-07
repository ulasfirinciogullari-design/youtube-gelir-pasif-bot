"""Continue the next topic after an owner-cancelled chain, not a QA recovery.

Old jobs, media, upload fences, series assignments and all paid ledgers remain
unchanged. A current owner authorization may abandon an undelivered episode;
it cannot turn it into a delivered episode or repeat an old paid request.
"""
from datetime import datetime
import math
import re
import time
from uuid import UUID

from app.services import external_episode_delivery as delivery, studio_state as jobs
from app.services import held_render_cancellation as cancellation
from app.services.channel_production import CHANNEL_STATE_PREFIX, _prefix_digest
from app.services.source_publication_hold import HOLD_PREFIX, _fence
from app.services.youtube_auth import CHANNEL_PREFIX, CHANNEL_INDEX_KEY, CREDENTIAL_PREFIX, AUTH_EPOCH_KEY
from app.services.youtube_automation import PROFILE_PREFIX, SERIES_COUNTER_PREFIX
from app.services.youtube_publish_state import UPLOAD_PREFIX, EXECUTION_LOCK_PREFIX

PREFIX = 'youtube_studio:owner_cancelled_continuation:v1:'
MAX_HOPS = 16
FRESH_SECONDS = 900
_read, _object = delivery._read, delivery._object
_digest, _json = cancellation._digest, cancellation._json
_ID = re.compile(r'[A-Za-z0-9_-]{8,128}\Z')


class OwnerContinuationError(ValueError):
    pass


def _require(value):
    if not value:
        raise OwnerContinuationError('owner_continuation_not_eligible')


def validate_request(channel_id, value):
    _require(type(channel_id) is str and re.fullmatch(r'UC[A-Za-z0-9_-]{22}', channel_id))
    _require(type(value) is dict and set(value) == {'expected_profile_revision',
        'expected_state_revision', 'expected_connection_id', 'root_task_id', 'cancelled_leaf_id',
        'expected_next_topic_sha256', 'expected_worker_names', 'owner_reason'})
    _require(all(type(value[k]) is str and _ID.fullmatch(value[k]) for k in (
        'expected_profile_revision', 'expected_state_revision', 'expected_connection_id')))
    _require(all(type(value[k]) is str and str(UUID(value[k])) == value[k]
                 for k in ('root_task_id', 'cancelled_leaf_id'))
             and value['root_task_id'] != value['cancelled_leaf_id'])
    _require(type(value['expected_next_topic_sha256']) is str
             and re.fullmatch(r'[0-9a-f]{64}', value['expected_next_topic_sha256']))
    cancellation._text(value['owner_reason'], 1000)
    _require(30 <= len(value['owner_reason']) <= 1000)
    workers = value['expected_worker_names']
    _require(type(workers) is list and 1 <= len(workers) <= 8)
    for worker in workers:
        cancellation._text(worker, 200)
        _require('@' in worker and not any(c.isspace() for c in worker))
    _require(workers == sorted(set(workers)))
    return value


def _receipt(pipe, key):
    value = _object(pipe, key)
    _require(value.get('receipt_sha256') == _digest({k: v for k, v in value.items() if k != 'receipt_sha256'}))
    return value


def _terminal_chain(pipe, channel_id, request):
    task, chain, budgets = request['cancelled_leaf_id'], [], {}
    hold = _receipt(pipe, HOLD_PREFIX + task)
    cancelled = _receipt(pipe, cancellation.CANCELLATION_PREFIX + task)
    _require(hold.get('version') == cancelled.get('version') == 1
        and hold.get('task_id') == cancelled.get('task_id') == task
        and hold.get('disposition') == 'owner_publication_hold'
        and hold.get('original_publish_after_render') is True
        and cancelled.get('status') == 'cancelled'
        and cancelled.get('disposition') == 'owner_cancelled_held_orphan_retry'
        and cancelled.get('binding', {}).get('hold_sha256') == hold['receipt_sha256']
        and cancelled.get('worker_inventory', {}).get('task_absent') is True)
    for record in (hold, cancelled):
        _require(record.get('request', {}).get('expected_channel_id') == channel_id
            and record['request'].get('expected_profile_revision') == request['expected_state_revision'])
    _require(hold.get('connection_id') == request['expected_connection_id']
             and _object(pipe, UPLOAD_PREFIX + task) == _fence(hold))
    for _ in range(MAX_HOPS + 1):
        _require(type(task) is str and str(UUID(task)) == task and task not in {j['task_id'] for j in chain})
        job = _object(pipe, jobs.JOB_PREFIX + task)
        _require(job.get('task_id') == task and job.get('kind') == 'render'
                 and isinstance(job.get('spec'), dict) and not job.get('result'))
        _require(not any(job.get(k) for k in ('provider_in_flight', 'provider_pending', 'paid_request_uncertain')))
        _require(job.get('provider_request_state') not in {'pending', 'accepted', 'uncertain', 'running', 'queued', 'polling'})
        _require(_read(pipe, EXECUTION_LOCK_PREFIX + task, optional=True) is None
                 and _read(pipe, jobs.EXTERNAL_EPISODE_LEAF_PREFIX + task, optional=True) is None)
        budgets[task] = _read(pipe, jobs.PAID_CREATE_BUDGET_PREFIX + task, 'hash', optional=True)
        dispatch = _read(pipe, jobs.RETRY_DISPATCH_PREFIX + task, 'hash', optional=True)
        spec = job['spec']
        if not chain:
            _require(job.get('state') == 'CANCELLED' and job.get('stage') == 'cancelled'
                and not job.get('retry_child_task_id') and job.get('retry_claimed') is not True
                and job.get('repair_claimed') is not True and dispatch is None
                and spec.get('publish_after_render') is False
                and job.get('owner_cancellation') == {'receipt_key': cancellation.CANCELLATION_PREFIX + task,
                                                     'receipt_sha256': cancelled['receipt_sha256']}
                and job.get('publication_hold') == {'receipt_key': HOLD_PREFIX + task, 'receipt_sha256': hold['receipt_sha256']}
                and _digest({**spec, 'publish_after_render': True}) == hold.get('original_spec_sha256')
                and cancelled.get('binding', {}).get('parent_task_id') == job.get('parent_id')
                and _read(pipe, jobs.REPAIR_CHECKPOINT_CLAIM_PREFIX + task, optional=True) is None)
        else:
            child = chain[-1]
            claim = _read(pipe, jobs.RETRY_CHILD_CLAIM_PREFIX + child['task_id'], 'hash')
            _require(job.get('state') == 'FAILURE' and job.get('stage') == 'failed'
                and _read(pipe, UPLOAD_PREFIX + task, optional=True) is None
                and isinstance(dispatch, dict) and dispatch.get('state') == 'dispatched'
                and job.get('retry_dispatch_state') == 'dispatched' and job.get('retry_claimed') is True
                and job.get('retry_child_task_id') == child['task_id']
                and dispatch.get('child_task_id') == child['task_id'] and dispatch.get('mode') in {'repair', 'full'}
                and type(dispatch.get('token')) is str and 16 <= len(dispatch['token']) <= 256
                and claim.get('source_task_id') == task and claim.get('token') == dispatch['token']
                and _read(pipe, jobs.RETRY_CHILD_EXECUTION_PREFIX + child['task_id']) == dispatch['token'])
            if dispatch['mode'] == 'repair':
                _require(job.get('repair_claimed') is True
                         and _read(pipe, jobs.REPAIR_CHECKPOINT_CLAIM_PREFIX + task) == dispatch['token'])
        chain.append(job)
        if task == request['root_task_id']:
            _require(job.get('parent_id') is None)
            frozen = delivery._frozen(spec)
            _require(all(delivery._frozen({**j['spec'], 'publish_after_render': True}) == frozen for j in chain))
            return chain, {'hold_sha256': hold['receipt_sha256'], 'cancellation_sha256': cancelled['receipt_sha256'],
                           'chain_sha256': _digest(chain), 'paid_ledgers_sha256': _digest(budgets)}
        task = job.get('parent_id')
    raise OwnerContinuationError('owner_continuation_not_eligible')


def _context(pipe, channel_id, request, now):
    profile = _object(pipe, PROFILE_PREFIX + channel_id)
    channel = _object(pipe, CHANNEL_PREFIX + channel_id)
    schedule = _read(pipe, CHANNEL_STATE_PREFIX + channel_id, 'hash')
    _require(profile.get('channel_id') == channel_id and profile.get('profile_revision') == request['expected_profile_revision']
        and profile.get('production_enabled') is True and profile.get('auto_publish') is True
        and profile.get('release_mode') == 'public' and 'series_epoch' not in profile
        and channel.get('id') == channel_id and channel.get('connection_id') == request['expected_connection_id']
        and not channel.get('requires_reconnect'))
    verified = datetime.fromisoformat(channel.get('verified_at', ''))
    _require(verified.tzinfo is not None and 0 <= now - verified.timestamp() <= FRESH_SECONDS)
    pipe.watch(CREDENTIAL_PREFIX + channel_id, CHANNEL_INDEX_KEY, AUTH_EPOCH_KEY)
    _require(pipe.type(CREDENTIAL_PREFIX + channel_id) == 'string' and pipe.strlen(CREDENTIAL_PREFIX + channel_id) > 0
        and pipe.sismember(CHANNEL_INDEX_KEY, channel_id))
    epoch = _read(pipe, AUTH_EPOCH_KEY)
    _require(type(epoch) is str and epoch.isdigit())
    topics = profile.get('production_topics')
    _require(type(topics) is list and 2 <= len(topics) <= 60 and len(set(topics)) == len(topics)
        and all(type(t) is str and t.strip() == t and 1 <= len(t) <= 240 for t in topics)
        and type(profile.get('series_total')) is int and profile['series_total'] == len(topics))
    cursor = int(schedule.get('cursor', '-1'))
    _require(str(cursor) == schedule.get('cursor') and 1 <= cursor < len(topics)
        and schedule.get('consumed_prefix') == _prefix_digest(topics[:cursor])
        and schedule.get('paused_reason') == 'previous_render_failed' and schedule.get('last_result') == 'FAILURE'
        and schedule.get('dispatch_status') == 'finished' and not schedule.get('active_task_id')
        and schedule.get('last_task_id') == request['root_task_id']
        and schedule.get('profile_revision') == request['expected_state_revision']
        and schedule.get('connection_id') == request['expected_connection_id']
        and delivery.topic_sha256(topics[cursor]) == request['expected_next_topic_sha256'])
    due = float(schedule.get('next_due', 'nan'))
    _require(math.isfinite(due) and due >= 0)
    series_id = profile.get('series_id')
    _require(type(series_id) is str and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', series_id))
    counter = _read(pipe, SERIES_COUNTER_PREFIX + channel_id + ':' + series_id)
    _require(counter == str(cursor))  # The next normal publication keeps its ordinary next number.
    delivery._idle(pipe)  # Includes global claims and legacy jobs not holding a slot.
    chain, proof = _terminal_chain(pipe, channel_id, request)
    spec = chain[-1]['spec']
    identity = str(profile.get('channel_identity') or '').strip()[:240]
    brief = topics[cursor - 1] + (f'\n\nChannel editorial direction: {identity}' if identity else '')
    _require(spec.get('topic') == brief and spec.get('production_topic_index') == cursor - 1
        and type(spec.get('production_topic_index')) is int and spec.get('production_scheduled') is True
        and spec.get('publish_after_render') is True and spec.get('production_channel_id') == channel_id
        and spec.get('production_connection_id') == request['expected_connection_id']
        and spec.get('production_profile_revision') == request['expected_state_revision']
        and spec.get('mode') == 'production' and spec.get('format') == 'shorts'
        and spec.get('language') == profile.get('default_language')
        and spec.get('channel_id') == str(profile.get('route_label') or channel_id).strip())
    return {'profile_sha256': _digest(profile), 'channel_sha256': _digest(channel),
        'schedule_sha256': _digest(schedule), 'authorization_epoch': epoch,
        'cursor': cursor, 'series_id': series_id, 'series_counter': counter,
        'consumed_prefix': schedule['consumed_prefix'], 'previous_next_due': due,
        'chain': [job['task_id'] for job in reversed(chain)], **proof}


def _summary(record, status):
    return {'status': status, 'channel_id': record['channel_id'], 'root_task_id': record['request']['root_task_id'],
        'next_topic_index': record['proof']['cursor'], 'profile_revision': record['request']['expected_profile_revision'],
        'disposition': 'abandoned_not_delivered', 'old_episode_delivered': False, 'old_qa_approved': False,
        'old_media_reused': False, 'new_task_enqueued': False}


def continue_after_owner_cancellation(channel_id, *, now=None, **request):
    """Fresh inventory then CAS; only audit + three scheduler fields may change."""
    try:
        request = validate_request(channel_id, request)
        now = time.time() if now is None else now
        _require(type(now) in (int, float) and math.isfinite(now) and now > 0)
        key = PREFIX + channel_id + ':' + request['root_task_id']
        client = jobs._client()
        with client.pipeline() as pipe:
            prior = _read(pipe, key, optional=True)
            if prior is not None:
                record = cancellation._object(prior, limit=65536)
                _require(record.get('version') == 1 and record.get('channel_id') == channel_id
                    and record.get('request') == request and record.get('disposition') == 'abandoned_not_delivered'
                    and record.get('receipt_sha256') == _digest({k: v for k, v in record.items() if k != 'receipt_sha256'}))
                return _summary(record, 'already_continued')  # No fresh dispatch authority or state overwrite.
            proof = _context(pipe, channel_id, request, now)
        inventory = cancellation._worker_inventory(request['cancelled_leaf_id'], request['expected_worker_names'])
        _require(all(count == 0 for counts in inventory['counts'].values() for count in counts.values()))
        with client.pipeline() as pipe:
            _require(_read(pipe, key, optional=True) is None and _context(pipe, channel_id, request, now) == proof)
            record = {'version': 1, 'channel_id': channel_id, 'request': request,
                'disposition': 'abandoned_not_delivered', 'continued_at': now, 'next_due': now,
                'proof': proof, 'worker_inventory': inventory,
                'old_episode_delivered': False, 'old_qa_approved': False, 'old_media_reused': False,
                'old_paid_requests_retried': False, 'series_counter_reset': False}
            record['receipt_sha256'] = _digest(record)
            pipe.multi()
            pipe.set(key, _json(record))
            pipe.hdel(CHANNEL_STATE_PREFIX + channel_id, 'paused_reason')
            pipe.hset(CHANNEL_STATE_PREFIX + channel_id, mapping={
                'profile_revision': request['expected_profile_revision'], 'next_due': str(now)})
            pipe.execute()
        return _summary(record, 'continued')
    except Exception:
        raise OwnerContinuationError('owner_continuation_not_eligible') from None
