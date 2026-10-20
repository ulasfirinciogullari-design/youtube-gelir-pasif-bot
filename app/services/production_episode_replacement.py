"""One fresh stock-only production for a failed, unpublished scheduled episode.

An OAuth renewal does not make the old failed render executable. This explicit
operator operation preserves every old job, spend receipt and media checkpoint,
archives the failed schedule, and admits a genuinely fresh root using the current
connection. The episode cursor does not move; normal QA and public-delivery
reconciliation must finish this same episode before the next one can run.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import re
from uuid import uuid4

from app.services import channel_production as production, production_included_router as included
from app.services import production_spend_runtime as spending
from app.services import studio_state as jobs
from app.services.youtube_auth import AUTH_EPOCH_KEY
from app.services.youtube_publish_state import UPLOAD_PREFIX, EXECUTION_LOCK_PREFIX
from app.services.production_spend import SpendBlocked

PREFIX = 'youtube_studio:episode_replacement:v1:'
_UUID = re.compile(r'^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$')
_ACTIVE = {'PENDING', 'STARTED', 'PROGRESS', 'RETRY'}


def _require(value, code='episode_replacement_ineligible'):
    if not value:
        raise SpendBlocked(code)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _object(raw):
    _require(type(raw) is str and 0 < len(raw.encode()) <= 4 * 1024 * 1024)
    value = json.loads(raw)
    _require(type(value) is dict)
    return value


def _read(pipe, key, *, kind='string', optional=False):
    pipe.watch(key)
    actual = pipe.type(key)
    _require(actual in ({kind, 'none'} if optional else {kind}))
    return pipe.hgetall(key) if actual == 'hash' else pipe.get(key)


def _empty(pipe, *keys):
    pipe.watch(*keys)
    _require(pipe.exists(*keys) == 0, 'episode_replacement_existing_work')


def _failed_chain(pipe, root_id):
    result, task_id, parent, first = {}, root_id, None, None
    for _ in range(16):
        _require(type(task_id) is str and _UUID.fullmatch(task_id) and task_id not in result)
        job = _object(_read(pipe, jobs.JOB_PREFIX + task_id))
        spec = job.get('spec')
        _require(job.get('task_id') == task_id and job.get('parent_id') == parent
            and job.get('kind') == 'render' and job.get('state') == 'FAILURE' and not job.get('result')
            and type(spec) is dict and not any(job.get(key) for key in
                ('youtube', 'youtube_automation', 'video_key', 'youtube_video_id')))
        _empty(pipe, UPLOAD_PREFIX + task_id, EXECUTION_LOCK_PREFIX + task_id)
        frozen = {k: v for k, v in spec.items() if k not in {'workflow', 'repair_source_task_id'}}
        if first is None:
            first = frozen
        _require(frozen == first)
        result[task_id] = {'sha256': _hash(job), 'spec_sha256': _hash(spec)}
        child = job.get('retry_child_task_id')
        if not child:
            return first, result
        parent, task_id = task_id, child
    raise SpendBlocked('episode_replacement_lineage_unbounded')


def reserve_failed_episode_replacement(root_id, child_id):
    """Private operator call. A lost EXEC acknowledgement never allows enqueue."""
    _require(included.enabled() and spending.enforcement_enabled(), 'episode_replacement_subscription_required')
    _require(type(root_id) is str and type(child_id) is str and _UUID.fullmatch(root_id)
        and _UUID.fullmatch(child_id) and root_id != child_id)
    foundation = spending.configured_ledger(read_timeout=2)
    client = foundation.client
    record_key, anchor_key = PREFIX + 'source:' + root_id, PREFIX + 'anchor:' + root_id
    try:
        with client.pipeline() as pipe:
            pipe.watch(record_key, anchor_key)
            if pipe.exists(record_key, anchor_key):
                record = _object(pipe.get(record_key))
                _require(pipe.pttl(record_key) == pipe.pttl(anchor_key) == -1
                    and record.get('source_root_id') == root_id and pipe.get(anchor_key) == _hash(record),
                    'episode_replacement_record_incomplete')
                pipe.multi(); pipe.ping(); included.IncludedRouterLedger._ack(pipe, [True])
                return {'claimed': False, 'task_id': record['task_id'], 'status': 'already_reserved'}
            spec, old_jobs = _failed_chain(pipe, root_id)
            channel_id = spec.get('production_channel_id')
            _require(type(channel_id) is str and re.fullmatch(r'UC[A-Za-z0-9_-]{22}', channel_id))
            profile = _object(_read(pipe, production.PROFILE_PREFIX + channel_id))
            channel = _object(_read(pipe, production.OAUTH_CHANNEL_PREFIX + channel_id))
            credential = _read(pipe, production.OAUTH_CREDENTIAL_PREFIX + channel_id)
            epoch = _read(pipe, AUTH_EPOCH_KEY)
            state = _read(pipe, production.CHANNEL_STATE_PREFIX + channel_id, kind='hash')
            pipe.watch(production.OAUTH_CHANNEL_INDEX)
            _require(channel.get('id') == channel_id and channel.get('requires_reconnect') is not True
                and type(channel.get('connection_id')) is str and re.fullmatch(r'[A-Za-z0-9_-]{8,128}', channel['connection_id'])
                and type(credential) is str and credential and type(epoch) is str and epoch.isdecimal()
                and pipe.sismember(production.OAUTH_CHANNEL_INDEX, channel_id))
            topics = profile.get('production_topics')
            _require(profile.get('channel_id') == channel_id and profile.get('production_enabled') is True
                and profile.get('auto_publish') is True and profile.get('release_mode') == 'public'
                and profile.get('profile_revision') == spec.get('production_profile_revision')
                and type(topics) is list and 1 <= len(topics) <= 60
                and all(type(t) is str and 1 <= len(t.strip()) <= 240 for t in topics)
                and type(profile.get('production_interval_hours')) is int
                and 6 <= profile['production_interval_hours'] <= 168)
            topics = [t.strip() for t in topics]
            cursor = int(state.get('cursor', '-1'))
            _require(str(cursor) == state.get('cursor') and 1 <= cursor <= len(topics)
                and state.get('paused_reason') == 'previous_render_failed' and state.get('last_result') == 'FAILURE'
                and state.get('last_task_id') == root_id and state.get('dispatch_status') == 'finished'
                and not state.get('active_task_id') and state.get('profile_revision') == profile['profile_revision']
                and state.get('consumed_prefix') == production._prefix_digest(topics[:cursor])
                and state.get('connection_id') in {spec.get('production_connection_id'), channel['connection_id']}
                and math.isfinite(float(state.get('next_due', 'nan'))) and float(state['next_due']) >= 0)
            direction = str(profile.get('channel_identity') or '').strip()[:240]
            brief = topics[cursor - 1] + ('\n\nChannel editorial direction: ' + direction if direction else '')
            _require(spec.get('topic') == brief and spec.get('production_topic_index') == cursor - 1
                and spec.get('duration_minutes') == .5 and spec.get('mode') == 'production'
                and spec.get('format') == 'shorts' and spec.get('music') == 'off'
                and spec.get('production_scheduled') is True and spec.get('publish_after_render') is True
                and spec.get('language') == profile.get('default_language') and spec['language'] in {'tr', 'en'})
            _empty(pipe, production.ACTIVE_KEY, jobs.JOB_PREFIX + child_id)
            pipe.watch(jobs.JOB_INDEX)
            _require(pipe.zcard(jobs.JOB_INDEX) < jobs.MAX_INDEXED_JOBS)
            for task in pipe.zrange(jobs.JOB_INDEX, 0, -1):
                row = _read(pipe, jobs.JOB_PREFIX + task, optional=True)
                if row is None:
                    continue
                item = _object(row)
                _require(item.get('replacement_for_root') != root_id, 'episode_replacement_record_incomplete')
                if item.get('state') in _ACTIVE:
                    _require((item.get('spec') or {}).get('production_channel_id') != channel_id,
                        'episode_replacement_channel_busy')
            # Recheck native capacity under this transaction as well as the
            # voice preflight. This never reserves a model or voice request.
            included.preflight_production(channel_id, kind='shorts')
            included.IncludedRouterLedger(foundation).check_capacity(pipe, channel_id, minimum_requests=8)
            current_spec = {**deepcopy(spec), 'workflow': 'auto',
                            'production_connection_id': channel['connection_id']}
            current_spec.pop('repair_source_task_id', None)
            now = foundation.clock(); stamp = now.astimezone(timezone.utc).isoformat()
            job = {'task_id': child_id, 'kind': 'render', 'parent_id': None,
                'replacement_for_root': root_id, 'spec': current_spec, 'state': 'PENDING', 'stage': 'queued',
                'progress': 0, 'message': 'Bölüm güncel kanal bağlantısıyla yeniden üretiliyor; kalite kontrolü bekleniyor.',
                'created_ts': now.timestamp(), 'created_at': stamp, 'updated_at': stamp, 'result': None, 'error': None}
            record = {'version': 1, 'source_root_id': root_id, 'task_id': child_id, 'channel_id': channel_id,
                'before_schedule': state, 'prior_jobs': old_jobs, 'profile_sha256': _hash(profile),
                'current_channel_sha256': _hash(channel), 'credential_sha256': _hash(credential),
                'authorization_epoch_sha256': _hash(epoch), 'spec_sha256': _hash(current_spec),
                'created_at': stamp, 'episode_cursor': cursor, 'qa_approved': False, 'publish_eligible': False,
                'funding_mode': 'existing_subscription_included_router', 'new_cash_allowance_micro': 0}
            pipe.multi()
            pipe.set(record_key, _json(record), nx=True)
            pipe.set(anchor_key, _hash(record), nx=True)
            pipe.set(jobs.JOB_PREFIX + child_id, _json(job), nx=True, ex=jobs.JOB_TTL_SECONDS)
            pipe.zadd(jobs.JOB_INDEX, {child_id: now.timestamp()})
            pipe.set(production.ACTIVE_KEY, _json({'channel_id': channel_id, 'task_id': child_id}), nx=True)
            pipe.hset(production.CHANNEL_STATE_PREFIX + channel_id, mapping={
                'active_task_id': child_id, 'last_task_id': child_id, 'last_result': 'PENDING',
                'dispatch_status': 'reserved', 'connection_id': channel['connection_id']})
            pipe.hdel(production.CHANNEL_STATE_PREFIX + channel_id, 'paused_reason')
            reply = pipe.execute()
            _require(type(reply) is list and len(reply) == 7 and all(v is True for v in reply[:3])
                and type(reply[3]) is int and reply[3] == 1 and reply[4] is True
                and type(reply[5]) is int and reply[5] in (0, 1)
                and type(reply[6]) is int and reply[6] == 1, 'episode_replacement_ack_uncertain')
            return {'claimed': True, 'task_id': child_id, 'channel_id': channel_id,
                    'spec': current_spec, 'status': 'reserved'}
    except SpendBlocked:
        raise
    except Exception:
        raise SpendBlocked('episode_replacement_outcome_unverified') from None


def dispatch_failed_episode_replacement(root_id):
    result = reserve_failed_episode_replacement(root_id, str(uuid4()))
    if not result['claimed']:
        return result
    spec, task_id = result['spec'], result['task_id']
    options = {k: v for k, v in spec.items() if k not in {'topic', 'duration_minutes', 'language', 'channel_id'}}
    try:
        from app.tasks import run_video_pipeline
        run_video_pipeline.apply_async(args=(spec['topic'], spec['duration_minutes'], spec['language'],
            spec['channel_id'], options, None), task_id=task_id, retry=False)
        production.mark_production_dispatched(result['channel_id'], task_id)
        status = 'dispatched'
    except Exception:
        status = 'dispatch_uncertain'
        try:
            production.mark_production_dispatched(result['channel_id'], task_id, uncertain=True)
        except Exception:
            pass
    return {'claimed': True, 'task_id': task_id, 'status': status}
