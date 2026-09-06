"""Explicit owner-delegated episode substitution after an actual PUBLIC delivery.

This is neither a retry nor approval of an old failure. Rewritten narration is
allowed: semantic suitability is the authenticated editor's recorded decision.
Only this resolution receipt/indexes and pause/next_due may change. Normal beat
still owns dispatch, concurrency and every later job's independent QA/budget.
"""
import hashlib
import math
import re
import time
from uuid import UUID

from app.services import external_editorial_review as editorial, studio_state
from app.services.channel_production import ACTIVE_KEY, CHANNEL_STATE_PREFIX, _prefix_digest
from app.services.production_recovery import MAX_RETRY_HOPS, _completed_public_replay
from app.services.youtube_publish_state import UPLOAD_PREFIX, EXECUTION_LOCK_PREFIX


PREFIX = 'youtube_studio:external_episode_delivery:v1:'
RECEIPT_PREFIX, EXTERNAL_INDEX_PREFIX = PREFIX + 'receipt:', PREFIX + 'external:'
LEAF_INDEX_PREFIX = studio_state.EXTERNAL_EPISODE_LEAF_PREFIX


class ExternalEpisodeDeliveryError(ValueError):
    """Fixed safe code, no provider data or credential values."""


def _require(condition):
    if not condition:
        raise ExternalEpisodeDeliveryError('external_episode_delivery_invalid')


def topic_sha256(topic):
    return hashlib.sha256(topic.strip().encode('utf-8')).hexdigest()


def _read(pipe, key, kind='string', optional=False):
    pipe.watch(key)
    actual = pipe.type(key)
    if actual == 'none' and optional:
        return None
    _require(actual == kind)
    if kind == 'hash':
        return pipe.hgetall(key)
    return pipe.get(key)


def _object(pipe, key):
    return editorial._object(_read(pipe, key))


def _frozen(spec):
    return {k: v for k, v in spec.items() if k not in {'workflow', 'repair_source_task_id'}}


def _failed_chain(pipe, root_id, leaf_id):
    chain, task = [], leaf_id
    for _ in range(MAX_RETRY_HOPS + 1):
        _require(str(UUID(task)) == task and task not in {row['task_id'] for row in chain})
        job = _object(pipe, studio_state.JOB_PREFIX + task)
        _require(job.get('task_id') == task and job.get('kind') == 'render' and job.get('state') == 'FAILURE'
                 and isinstance(job.get('spec'), dict) and not job.get('result')
                 and not any(job.get(k) for k in ('youtube', 'youtube_automation', 'video_key', 'youtube_video_id')))
        _require(_read(pipe, UPLOAD_PREFIX + task, optional=True) is None
                 and _read(pipe, EXECUTION_LOCK_PREFIX + task, optional=True) is None
                 and _read(pipe, LEAF_INDEX_PREFIX + task, optional=True) is None)
        # Spending records are never reset; watch existing ledgers as evidence
        # that a supposedly terminal old worker is not still consuming media.
        _read(pipe, studio_state.PAID_CREATE_BUDGET_PREFIX + task, 'hash', optional=True)
        dispatch = _read(pipe, studio_state.RETRY_DISPATCH_PREFIX + task, 'hash', optional=True)
        if not chain:
            _require(not job.get('retry_child_task_id') and job.get('retry_claimed') is not True
                     and job.get('repair_claimed') is not True and dispatch is None
                     and _read(pipe, studio_state.REPAIR_CHECKPOINT_CLAIM_PREFIX + task, optional=True) is None)
        else:
            child = chain[-1]
            claim = _read(pipe, studio_state.RETRY_CHILD_CLAIM_PREFIX + child['task_id'], 'hash')
            _require(isinstance(dispatch, dict) and job.get('retry_child_task_id') == child['task_id']
                     and job.get('retry_claimed') is True and job.get('retry_dispatch_state') == dispatch.get('state')
                     and dispatch.get('child_task_id') == child['task_id'] and dispatch.get('mode') in {'full', 'repair'}
                     and dispatch.get('state') in {'reserved', 'dispatched', 'uncertain'}
                     and isinstance(dispatch.get('token'), str) and 16 <= len(dispatch['token']) <= 256
                     and claim.get('source_task_id') == task and claim.get('token') == dispatch['token']
                     and _read(pipe, studio_state.RETRY_CHILD_EXECUTION_PREFIX + child['task_id']) == dispatch['token'])
            if dispatch['mode'] == 'repair':
                _require(job.get('repair_claimed') is True
                         and _read(pipe, studio_state.REPAIR_CHECKPOINT_CLAIM_PREFIX + task) == dispatch['token'])
        chain.append(job)
        if task == root_id:
            _require(job.get('parent_id') is None)
            _require(all(_frozen(row['spec']) == _frozen(job['spec']) for row in chain))
            return chain
        task = job.get('parent_id')
        _require(isinstance(task, str))
    raise ExternalEpisodeDeliveryError('external_episode_delivery_invalid')


def _idle(pipe):
    _require(_read(pipe, ACTIVE_KEY, optional=True) is None)
    pipe.watch(studio_state.JOB_INDEX)
    _require(pipe.type(studio_state.JOB_INDEX) in {'none', 'zset'}
             and pipe.zcard(studio_state.JOB_INDEX) <= studio_state.MAX_INDEXED_JOBS)
    for task in pipe.zrange(studio_state.JOB_INDEX, 0, -1):
        _require(str(UUID(task)) == task)
        raw = _read(pipe, studio_state.JOB_PREFIX + task, optional=True)
        if raw is None:
            continue
        job = editorial._object(raw)
        _require(job.get('task_id') == task and isinstance(job.get('spec'), dict))
        # A legacy manual retry may not own a normal scheduler slot. Do not
        # reopen a two-slot scheduler while such unregistered work is active.
        _require(job.get('state') not in {'PENDING', 'RECEIVED', 'STARTED', 'RETRY', 'PROGRESS'})


def _empty(row, *fields):
    return all(row.get(k) is None or row.get(k) == '' for k in fields)


def _public_proof(pipe, source, profile, cursor):
    task, result = source['task_id'], source['result']
    _require(_read(pipe, EXECUTION_LOCK_PREFIX + task, optional=True) is None)
    ledger = _object(pipe, UPLOAD_PREFIX + task)
    plan = ledger.get('publish_plan')
    _require(isinstance(plan, dict))
    pipe.watch(*editorial._keys(source), *editorial._series_keys(source, plan))
    review = editorial._validate(pipe, source, plan)
    channel = profile['channel_id']; connection = source['spec']['production_connection_id']
    revision = profile['profile_revision']
    automation, attribution = result.get('youtube_automation'), result.get('youtube')
    _require(isinstance(automation, dict) and isinstance(attribution, dict))
    publish_id = automation.get('publish_task_id')
    _require(isinstance(publish_id, str) and str(UUID(publish_id)) == publish_id and publish_id != task)
    publisher = _object(pipe, studio_state.JOB_PREFIX + publish_id)
    delivered, pubspec = publisher.get('result'), publisher.get('spec')
    _require(isinstance(delivered, dict) and isinstance(pubspec, dict))
    video_id = delivered.get('youtube_video_id')
    _require(isinstance(video_id, str) and re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id))
    binding = {'target_channel_id': channel, 'connection_id': connection, 'profile_revision': revision}
    replay = delivered.get('idempotent_replay') is True
    if replay:
        _require(_completed_public_replay(delivered, attribution, plan, channel, connection, revision))
    for row in (attribution, delivered):
        _require(row.get('privacy_status') == row.get('release_status') == 'public'
                 and _empty(row, 'release_error_code', 'scheduled_publish_at', 'caption_error_code', 'thumbnail_error_code')
                 and all(row.get(k) == v or row is delivered and replay and k == 'profile_revision' and k not in row
                         for k, v in binding.items()))
    for row in (attribution,) if replay else (attribution, delivered):
        _require(row.get('caption_uploaded') is True and row.get('contains_synthetic_media') is True
                 and (not (profile.get('require_thumbnail') is True or plan.get('require_thumbnail') is True)
                      or row.get('thumbnail_uploaded') is True))
    _require(publisher.get('kind') == 'publish' and publisher.get('task_id') == publish_id
             and publisher.get('state') == 'SUCCESS' and publisher.get('parent_id') == task
             and delivered.get('task_id') == publish_id and delivered.get('source_task_id') == task
             and delivered.get('status') == 'complete' and attribution.get('video_id') == video_id
             and pubspec.get('source_task_id') == task and pubspec.get('release_mode') == 'public'
             and pubspec.get('privacy_status') == 'private' and all(pubspec.get(k) == v for k, v in binding.items())
             and automation.get('status') in {'queued', 'reserved', 'uploading', 'complete'}
             and automation.get('target_channel_id') == channel and automation.get('profile_revision') == revision
             and automation.get('release_mode') == 'public')
    _require(type(ledger.get('version')) is int and ledger['version'] == 2
             and ledger.get('status') == 'complete' and ledger.get('source_task_id') == task
             and ledger.get('publish_task_id') == publish_id and ledger.get('youtube_video_id') == video_id
             and ledger.get('target_channel_id') == channel and ledger.get('connection_id') == connection
             and ledger.get('requested_release_mode') == ledger.get('privacy_status') == ledger.get('release_status') == 'public'
             and ledger.get('side_effect_possible') is True and ledger.get('release_side_effect_possible') is True
             and isinstance(ledger.get('release_completed_at'), str) and ledger['release_completed_at']
             and _empty(ledger, 'requested_publish_at', 'scheduled_publish_at', 'release_error_code')
             and plan.get('release_mode') == 'public' and _empty(plan, 'publish_at'))
    series = plan['series']
    _require(type(series['number']) is int and series['number'] == cursor and series['total'] == len(profile['production_topics'])
             and attribution.get('series') == series and (replay and 'series' not in delivered or delivered.get('series') == series))
    _require(_read(pipe, editorial._series_keys(source, plan)[1]) == str(cursor))
    return {'external_task_id': task, 'publish_task_id': publish_id, 'youtube_video_id': video_id,
            'editorial_receipt_sha256': review['receipt_sha256'], 'manifest_sha256': review['server_proof']['manifest_sha256'],
            'video_sha256': review['server_proof']['video_sha256'], 'captions_sha256': review['server_proof']['captions_sha256'],
            'source_sha256': editorial._digest(source), 'publisher_sha256': editorial._digest(publisher),
            'upload_sha256': editorial._digest(ledger), 'authorization_sha256': review['authorization_sha256'], 'series': series}


def resolve_external_episode(channel_id, original_task_id, failed_leaf_id, external_task_id,
                             expected_profile_revision, expected_topic_sha256, editorial_explanation, *, now=None):
    """Owner-authenticated callable only; returns historical receipt on replay."""
    try:
        _require(isinstance(channel_id, str) and editorial.ingest._ID.fullmatch(channel_id))
        _require(isinstance(expected_profile_revision, str) and editorial.ingest._ID.fullmatch(expected_profile_revision))
        for task in (original_task_id, failed_leaf_id, external_task_id):
            _require(isinstance(task, str) and str(UUID(task)) == task)
        _require(external_task_id not in {original_task_id, failed_leaf_id})
        editorial._hash(expected_topic_sha256); editorial._text(editorial_explanation)
        _require(30 <= len(editorial_explanation) <= 1200)
        now = time.time() if now is None else now
        _require(type(now) in (int, float) and math.isfinite(now) and now >= 0)
        request = {'channel_id': channel_id, 'original_task_id': original_task_id, 'failed_leaf_id': failed_leaf_id,
                   'external_task_id': external_task_id, 'profile_revision': expected_profile_revision,
                   'topic_sha256': expected_topic_sha256, 'editorial_explanation': editorial_explanation}
        key = RECEIPT_PREFIX + channel_id + ':' + original_task_id
        leaf_key, external_key = LEAF_INDEX_PREFIX + failed_leaf_id, EXTERNAL_INDEX_PREFIX + external_task_id
        client = editorial.ingest._redis()
        with client.pipeline() as pipe:
            prior = _read(pipe, key, optional=True)
            if prior is not None:
                receipt = editorial._object(prior)
                _require(receipt.get('request') == request and receipt.get('version') == 1
                         and receipt.get('delivery_disposition') == 'separately_delivered_editorial_replacement'
                         and receipt.get('reviewer_type') == 'delegated_editorial_agent'
                         and all(receipt.get(k) is False for k in ('qa_approved', 'retry_repaired', 'media_generation_authorized'))
                         and receipt.get('receipt_sha256') == editorial._digest({k: v for k, v in receipt.items() if k != 'receipt_sha256'})
                         and _read(pipe, leaf_key) == _read(pipe, external_key) == receipt['receipt_sha256'])
                return {**receipt, 'status': 'already_resolved'}  # no new scheduling authority
            _require(_read(pipe, leaf_key, optional=True) is None and _read(pipe, external_key, optional=True) is None)
            profile = _object(pipe, editorial.ingest.PROFILE_PREFIX + channel_id)
            state_key = CHANNEL_STATE_PREFIX + channel_id
            state = _read(pipe, state_key, 'hash')
            topics = profile.get('production_topics'); cursor = int(state.get('cursor', '-1'))
            _require(profile.get('channel_id') == channel_id and profile.get('profile_revision') == expected_profile_revision
                     and profile.get('production_enabled') is True and profile.get('auto_publish') is True
                     and profile.get('release_mode') == 'public' and type(topics) is list and 2 <= len(topics) <= 60
                     and all(type(t) is str and t.strip() == t and 1 <= len(t) <= 240 for t in topics)
                     and len(set(topics)) == len(topics) and type(profile.get('series_total')) is int
                     and profile['series_total'] == len(topics) and type(profile.get('production_interval_hours')) is int
                     and 6 <= profile['production_interval_hours'] <= 168
                     and str(cursor) == state.get('cursor') and 1 <= cursor < len(topics)
                     and state.get('consumed_prefix') == _prefix_digest(topics[:cursor])
                     and state.get('paused_reason') == 'previous_render_failed' and state.get('last_result') == 'FAILURE'
                     and state.get('last_task_id') == original_task_id and state.get('dispatch_status') == 'finished'
                     and state.get('profile_revision') == expected_profile_revision and not state.get('active_task_id')
                     and topic_sha256(topics[cursor - 1]) == expected_topic_sha256)
            old_due = float(state.get('next_due', 'nan'))
            _require(math.isfinite(old_due) and old_due >= 0)
            _idle(pipe)
            chain = _failed_chain(pipe, original_task_id, failed_leaf_id)
            original = chain[-1]['spec']; identity = str(profile.get('channel_identity') or '').strip()[:240]
            brief = topics[cursor - 1] + (f'\n\nChannel editorial direction: {identity}' if identity else '')
            _require(original.get('topic') == brief and original.get('production_topic_index') == cursor - 1
                     and type(original.get('production_topic_index')) is int and original.get('production_scheduled') is True
                     and original.get('publish_after_render') is True and original.get('mode') == 'production'
                     and original.get('format') == 'shorts' and original.get('duration_minutes') == .5
                     and original.get('production_channel_id') == channel_id
                     and original.get('production_connection_id') == state.get('connection_id')
                     and original.get('production_profile_revision') == expected_profile_revision
                     and original.get('language') == profile.get('default_language')
                     and original.get('channel_id') == str(profile.get('route_label') or channel_id).strip())
            source = _object(pipe, studio_state.JOB_PREFIX + external_task_id)
            _require(source.get('task_id') == external_task_id and source.get('spec', {}).get('production_channel_id') == channel_id
                     and source['spec'].get('production_connection_id') == state['connection_id']
                     and source['spec'].get('language') == original['language'])
            proof = _public_proof(pipe, source, profile, cursor)
            receipt = {'version': 1, 'delivery_disposition': 'separately_delivered_editorial_replacement',
                'reviewer_type': 'delegated_editorial_agent', 'request': request, 'public_delivery': proof,
                'frozen_topic': topics[cursor - 1], 'frozen_brief_sha256': topic_sha256(brief),
                'cursor': cursor, 'consumed_prefix': state['consumed_prefix'], 'previous_next_due': old_due, 'next_due': now,
                'resolved_at': now, 'failed_chain_sha256': editorial._digest(chain), 'previous_state_sha256': editorial._digest(state),
                'failed_chain': [row['task_id'] for row in reversed(chain)],
                'qa_approved': False, 'retry_repaired': False, 'media_generation_authorized': False}
            receipt['receipt_sha256'] = editorial._digest(receipt)
            pipe.multi()
            pipe.set(key, editorial.ingest._json(receipt))
            pipe.set(leaf_key, receipt['receipt_sha256'])
            pipe.set(external_key, receipt['receipt_sha256'])
            pipe.hdel(state_key, 'paused_reason')
            pipe.hset(state_key, 'next_due', str(now))
            pipe.execute()
        return {**receipt, 'status': 'resolved'}
    except Exception:
        raise ExternalEpisodeDeliveryError('external_episode_delivery_unavailable_or_invalid') from None
