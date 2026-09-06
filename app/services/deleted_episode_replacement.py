"""Owner reserves a deleted episode's number, never its quality or delivery."""
import math
import time
from uuid import UUID

from app.services import external_episode_delivery as delivery, studio_state as state
from app.services import external_editorial_review as editorial
from app.services.channel_production import ACTIVE_KEY, CHANNEL_STATE_PREFIX, _decode_active_claims, _prefix_digest
from app.services.youtube_metrics import CACHE_PREFIX, REFRESH_SECONDS
from app.services.youtube_publish_state import UPLOAD_PREFIX, EXECUTION_LOCK_PREFIX


RECEIPT_PREFIX = 'youtube_studio:deleted_episode_replacement:v1:receipt:'
PREVIOUS_INDEX_PREFIX = 'youtube_studio:deleted_episode_replacement:v1:previous:'
_read, _object, _digest = delivery._read, delivery._object, editorial._digest


class DeletedEpisodeReplacementError(ValueError):
    pass


def _require(value):
    if not value:
        raise DeletedEpisodeReplacementError('deleted_episode_replacement_not_eligible')


def validate_replacement_request(task_id, value):
    _require(type(value) is dict and set(value) == {'previous_source_task_id', 'deferred_failed_leaf_id',
             'expected_channel_id', 'expected_profile_revision', 'owner_reason'})
    ids = (task_id, value['previous_source_task_id'], value['deferred_failed_leaf_id'])
    _require(all(type(t) is str and str(UUID(t)) == t for t in ids) and len(set(ids)) == 3)
    _require(all(type(value[k]) is str and editorial.ingest._ID.fullmatch(value[k])
                 for k in ('expected_channel_id', 'expected_profile_revision')))
    editorial._text(value['owner_reason'])
    _require(30 <= len(value['owner_reason']) <= 1200)
    return value


def _channel_idle(pipe, channel_id, old_id, new_id, deferred_id):
    raw = _read(pipe, ACTIVE_KEY, optional=True)
    if raw is not None:
        _require(all(c['channel_id'] != channel_id for c in _decode_active_claims(raw)))
    pipe.watch(state.JOB_INDEX)
    _require(pipe.type(state.JOB_INDEX) == 'zset' and pipe.zcard(state.JOB_INDEX) <= state.MAX_INDEXED_JOBS)
    for task in pipe.zrange(state.JOB_INDEX, 0, -1):
        raw = _read(pipe, state.JOB_PREFIX + task, optional=True)
        if raw is None:
            continue
        job = editorial._object(raw); spec = job.get('spec') or {}
        _require(job.get('task_id') == task and type(spec) is dict)
        same_channel = (spec.get('production_channel_id') == channel_id or spec.get('target_channel_id') == channel_id)
        if job.get('state') in {'PENDING', 'RECEIVED', 'STARTED', 'PROGRESS', 'RETRY'}:
            _require(not same_channel)
        if job.get('kind') == 'publish':
            _require(not (job.get('parent_id') in {new_id, deferred_id}
                          or spec.get('source_task_id') in {new_id, deferred_id}))
    for task in (old_id, new_id, deferred_id):
        _require(_read(pipe, EXECUTION_LOCK_PREFIX + task, optional=True) is None)


def _deferred_proof(pipe, root_id, leaf_id, channel_id, revision, connection, topics, cursor, profile):
    # Read the genuine terminal chain; never cancel, consume or resolve it.
    chain = delivery._failed_chain(pipe, root_id, leaf_id)
    spec = chain[-1]['spec']
    _require(spec.get('production_channel_id') == channel_id and spec.get('production_connection_id') == connection
             and spec.get('production_profile_revision') == revision and spec.get('production_scheduled') is True
             and type(spec.get('production_topic_index')) is int and spec['production_topic_index'] == cursor - 1
             and spec.get('publish_after_render') is True and spec.get('mode') == 'production'
             and spec.get('format') == 'shorts' and spec.get('duration_minutes') == .5
             and spec.get('language') == profile.get('default_language')
             and spec.get('channel_id') == str(profile.get('route_label') or channel_id).strip())
    identity = str(profile.get('channel_identity') or '').strip()[:240]
    brief = topics[cursor - 1] + (f'\n\nChannel editorial direction: {identity}' if identity else '')
    _require(spec.get('topic') == brief)
    return {'root_task_id': root_id, 'leaf_task_id': leaf_id, 'chain_sha256': _digest(chain)}


def _summary(receipt, status):
    return {'status': status, 'task_id': receipt['task_id'],
            'previous_source_task_id': receipt['request']['previous_source_task_id'],
            'channel_id': receipt['request']['expected_channel_id'], 'series': receipt['series']}


def reserve_deleted_episode_replacement(task_id, previous_source_task_id, deferred_failed_leaf_id,
                                       expected_channel_id, expected_profile_revision, owner_reason, *, now=None):
    """Write only a new assignment and immutable receipt/index; no source writes.

    The old owner-API absence is not itself a quality/publication grant. This
    explicit owner replacement keeps both historical assignment and counter.
    """
    try:
        request = validate_replacement_request(task_id, dict(previous_source_task_id=previous_source_task_id,
            deferred_failed_leaf_id=deferred_failed_leaf_id, expected_channel_id=expected_channel_id,
            expected_profile_revision=expected_profile_revision, owner_reason=owner_reason))
        now = time.time() if now is None else now
        _require(type(now) in (int, float) and math.isfinite(now) and now > 0)
        client = editorial.ingest._redis()
        key, old_index = RECEIPT_PREFIX + task_id, PREVIOUS_INDEX_PREFIX + previous_source_task_id
        with client.pipeline() as pipe:
            previous = _read(pipe, key, optional=True)
            if previous is not None:
                receipt = editorial._object(previous)
                _require(receipt.get('version') == 1 and receipt.get('task_id') == task_id and receipt.get('request') == request
                         and receipt.get('disposition') == 'owner_reserved_deleted_episode_replacement'
                         and receipt.get('receipt_sha256') == _digest({k: v for k, v in receipt.items() if k != 'receipt_sha256'})
                         and _read(pipe, old_index) == receipt['receipt_sha256']
                         and _read(pipe, receipt['assignment_key']) == str(receipt['series']['number']))
                return _summary(receipt, 'already_reserved')
            _require(_read(pipe, old_index, optional=True) is None)
            new = _object(pipe, state.JOB_PREFIX + task_id)
            spec, result = editorial._contract(new)
            _require(new.get('task_id') == task_id and spec['production_channel_id'] == expected_channel_id
                     and spec['production_profile_revision'] == expected_profile_revision
                     and spec.get('publish_after_render') is False and result.get('manual_qa_required') is True
                     and result.get('quality_disposition') == 'manual_qa_preview'
                     and not any(k in result for k in ('editorial_review_id', 'editorial_review_sha256', 'youtube', 'youtube_automation')))
            pipe.watch(*editorial._keys(new))
            _require(_read(pipe, editorial.EDITORIAL_RECEIPT_PREFIX + task_id, optional=True) is None
                     and _read(pipe, UPLOAD_PREFIX + task_id, optional=True) is None)
            imported = _object(pipe, editorial.ingest.RESERVATION_PREFIX + task_id)
            _require(imported.get('status') == 'complete' and imported.get('task_id') == task_id
                     and imported.get('descriptor_id') == result.get('external_descriptor_id')
                     and imported.get('job_sha256') == _digest(new))
            authorization = editorial._context(pipe, new)
            profile = _object(pipe, editorial.ingest.PROFILE_PREFIX + expected_channel_id)
            channel_state = _read(pipe, CHANNEL_STATE_PREFIX + expected_channel_id, 'hash')
            topics = profile.get('production_topics'); cursor = int(channel_state.get('cursor', '-1'))
            _require(profile.get('production_enabled') is True and profile.get('auto_publish') is True
                     and profile.get('release_mode') == 'public' and type(topics) is list and 2 <= len(topics) <= 60
                     and all(type(t) is str and t.strip() == t and 1 <= len(t) <= 240 for t in topics)
                     and len(set(topics)) == len(topics)
                     and type(profile.get('series_total')) is int and profile['series_total'] == len(topics)
                     and str(cursor) == channel_state.get('cursor') and 1 <= cursor <= len(topics)
                     and channel_state.get('consumed_prefix') == _prefix_digest(topics[:cursor])
                     and channel_state.get('paused_reason') == 'previous_render_failed'
                     and channel_state.get('last_result') == 'FAILURE' and channel_state.get('dispatch_status') == 'finished'
                     and channel_state.get('profile_revision') == expected_profile_revision
                     and channel_state.get('connection_id') == spec['production_connection_id']
                     and not channel_state.get('active_task_id'))
            _channel_idle(pipe, expected_channel_id, previous_source_task_id, task_id, deferred_failed_leaf_id)
            deferred = _deferred_proof(pipe, channel_state.get('last_task_id'), deferred_failed_leaf_id, expected_channel_id,
                expected_profile_revision, spec['production_connection_id'], topics, cursor, profile)
            old = _object(pipe, state.JOB_PREFIX + previous_source_task_id)
            old_ledger = _object(pipe, UPLOAD_PREFIX + previous_source_task_id)
            number = old_ledger.get('publish_plan', {}).get('series', {}).get('number')
            _require(old.get('task_id') == previous_source_task_id and type(number) is int
                     and 1 <= number <= len(topics) and cursor in {number, number + 1}
                     and old.get('spec', {}).get('language') == spec['language'] == profile.get('default_language'))
            # This proves the historical publication, NOT continued availability.
            history = delivery._public_proof(pipe, old, profile, number)
            cache_key = CACHE_PREFIX + expected_channel_id + ':' + spec['production_connection_id']
            cache = _object(pipe, cache_key); absent = cache.get('videos', {}).get(history['youtube_video_id'], {})
            checked = absent.get('availability_checked_at')
            _require(cache.get('version') == 1 and cache.get('channel_id') == expected_channel_id
                     and cache.get('connection_id') == spec['production_connection_id'] and cache.get('last_error') is None
                     and absent.get('availability') == 'unavailable' and absent.get('error') == 'video_unavailable'
                     and type(checked) in (int, float) and math.isfinite(checked)
                     and now - REFRESH_SECONDS <= checked <= now
                     and cache.get('last_attempt_at') == checked)
            old_assignment, counter_key = editorial._series_keys(old, {'series': history['series']})
            assignment = editorial.SERIES_ASSIGNMENT_PREFIX + expected_channel_id + ':' + history['series']['id'] + ':' + task_id
            _require(_read(pipe, assignment, optional=True) is None and _read(pipe, old_assignment) == str(number)
                     and _read(pipe, counter_key) == str(number))
            receipt = {'version': 1, 'task_id': task_id, 'request': request,
                'disposition': 'owner_reserved_deleted_episode_replacement', 'created_at': now,
                'series': history['series'], 'assignment_key': assignment, 'previous_assignment_key': old_assignment,
                'public_history': history, 'owner_api_absence': {'youtube_video_id': history['youtube_video_id'],
                    'checked_at': checked, 'cache_sha256': _digest(cache), 'evidence': 'successful_owner_api_absence'},
                'deletion_attribution': 'owner_attested_deleted_not_inferred_from_api_absence',
                'deferred_failure': deferred, 'new_import_job_sha256': imported['job_sha256'],
                'new_descriptor_id': result['external_descriptor_id'], 'authorization_sha256': authorization,
                'scheduler_cursor': cursor, 'frozen_replaced_topic_sha256': delivery.topic_sha256(topics[number - 1]),
                'previous_episode_accepted': False, 'qa_approved': False, 'publish_eligible': False,
                'scheduler_resumed': False, 'media_generation_authorized': False}
            receipt['receipt_sha256'] = _digest(receipt)
            pipe.multi(); pipe.set(assignment, str(number)); pipe.set(key, editorial.ingest._json(receipt))
            pipe.set(old_index, receipt['receipt_sha256']); pipe.execute()
        return _summary(receipt, 'reserved')
    except Exception:
        raise DeletedEpisodeReplacementError('deleted_episode_replacement_not_eligible') from None
