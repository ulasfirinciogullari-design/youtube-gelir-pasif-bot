"""Operator-only, one-shot fresh rebuild of an exact failed production leaf.

Private authorization and the existing full retry claim are reserved atomically.
No prior checkpoint, paid ledger, upload, pause, or series cursor is reset. A
lost broker reply never grants dispatch twice. This is not a QA/publication
permission: the ordinary worker, upload and production-resume gates still apply.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import re
import secrets
import time
from uuid import uuid4

import redis

from app.config import settings
from app.services.studio_state import (
    JOB_PREFIX, JOB_INDEX, JOB_TTL_SECONDS, MAX_INDEXED_JOBS,
    RETRY_DISPATCH_PREFIX, RETRY_CHILD_CLAIM_PREFIX, RETRY_CHILD_EXECUTION_PREFIX,
    REPAIR_CHECKPOINT_PREFIX, REPAIR_CHECKPOINT_CLAIM_PREFIX,
    RETRY_DISPATCH_TTL_SECONDS, PAID_CREATE_BUDGET_PREFIX, _CLAIM_RETRY_DISPATCH,
    claim_retry_dispatch, mark_retry_dispatch,
)
from app.services.channel_production import (
    ACTIVE_KEY, CHANNEL_STATE_PREFIX, PROFILE_PREFIX, OAUTH_CHANNEL_PREFIX,
    OAUTH_CREDENTIAL_PREFIX, OAUTH_CHANNEL_INDEX, _prefix_digest,
)
from app.services.youtube_auth import AUTH_EPOCH_KEY
from app.services.youtube_publish_state import UPLOAD_PREFIX, EXECUTION_LOCK_PREFIX


POLICY_PREFIX = 'youtube_studio:full_video_rebuild:v1:policy:'
SOURCE_PREFIX = 'youtube_studio:full_video_rebuild:v1:source:'
MAX_RETRY_HOPS = 16
_TASK = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
_ID = re.compile(r'^[A-Za-z0-9_-]{8,128}$')
_TOKEN = re.compile(r'^[A-Za-z0-9_-]{16,256}$')
_SHA = re.compile(r'^[0-9a-f]{64}$')
_AUDIO_POINTER_FIELDS = {'version', 'status', 'qa_approved', 'requires_full_qa', 'audio_key',
                         'metadata_key', 'audio_sha256', 'metadata_sha256', 'package_sha256', 'size'}
_ACTIVE_STATES = {'PENDING', 'STARTED', 'PROGRESS', 'RETRY'}
_FLAGS = ('fresh_story', 'fresh_voice', 'fresh_media', 'requires_full_qa')


class FullVideoRebuildError(RuntimeError):
    """Fixed safe code; never expose provider or credential details."""


def _redis():
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


def _require(condition, code='full_rebuild_ineligible'):
    if not condition:
        raise FullVideoRebuildError(code)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _object(raw):
    _require(isinstance(raw, str) and 0 < len(raw.encode()) <= 4 * 1024 * 1024)
    value = json.loads(raw)
    _require(isinstance(value, dict))
    return value


def _snapshot(client, key, snapshots, *, kind='string', optional=False):
    actual = client.type(key)
    _require(actual in ({kind, 'none'} if optional else {kind}))
    if actual == 'hash':
        value = client.hgetall(key)
    elif actual == 'zset':
        _require(client.zcard(key) <= MAX_INDEXED_JOBS, 'full_rebuild_registry_unbounded')
        value = client.zrange(key, 0, -1)
    else:
        value = client.get(key)
    snapshot = {'kind': actual, 'value': value}
    # A repeated read must not replace an earlier eligibility observation with
    # newer bytes while retaining decisions made from the old record.
    _require(key not in snapshots or snapshots[key] == snapshot, 'full_rebuild_state_changed')
    snapshots[key] = snapshot
    return value


def _absent(client, key, snapshots):
    _require(_snapshot(client, key, snapshots, optional=True) is None)


def _frozen(spec):
    return {k: v for k, v in spec.items() if k not in {'workflow', 'repair_source_task_id'}}


def _failed(job, task_id, spec):
    _require(job.get('task_id') == task_id and job.get('kind') == 'render'
             and job.get('state') == 'FAILURE' and isinstance(job.get('spec'), dict)
             and _frozen(job['spec']) == _frozen(spec) and not job.get('result'))
    _require(not any(job.get(k) for k in ('youtube', 'youtube_automation', 'video_key', 'youtube_video_id')))


def _lineage(client, source, spec, snapshots):
    child, seen = source, set()
    # Leave room for the new child in the existing public-recovery hop bound.
    for _ in range(MAX_RETRY_HOPS):
        task_id = child['task_id']
        _require(task_id not in seen and _TASK.fullmatch(task_id))
        seen.add(task_id)
        _failed(child, task_id, spec)
        for prefix in (UPLOAD_PREFIX, EXECUTION_LOCK_PREFIX):
            _absent(client, prefix + task_id, snapshots)
        parent_id = child.get('parent_id')
        if not parent_id:
            return task_id
        _require(isinstance(parent_id, str) and _TASK.fullmatch(parent_id))
        parent = _object(_snapshot(client, JOB_PREFIX + parent_id, snapshots))
        dispatch = _snapshot(client, RETRY_DISPATCH_PREFIX + parent_id, snapshots, kind='hash')
        claim = _snapshot(client, RETRY_CHILD_CLAIM_PREFIX + task_id, snapshots, kind='hash')
        execution = _snapshot(client, RETRY_CHILD_EXECUTION_PREFIX + task_id, snapshots)
        token = dispatch.get('token')
        _require(isinstance(token, str) and _TOKEN.fullmatch(token)
                 and parent.get('retry_child_task_id') == task_id and parent.get('retry_claimed') is True
                 and parent.get('retry_dispatch_state') == dispatch.get('state')
                 and dispatch.get('child_task_id') == task_id and dispatch.get('mode') in {'full', 'repair'}
                 and dispatch.get('state') in {'reserved', 'dispatched', 'uncertain'}
                 and claim.get('source_task_id') == parent_id and claim.get('token') == token and execution == token)
        if dispatch['mode'] == 'repair':
            _require(parent.get('repair_claimed') is True
                     and _snapshot(client, REPAIR_CHECKPOINT_CLAIM_PREFIX + parent_id, snapshots) == token)
        child = parent
    raise FullVideoRebuildError('full_rebuild_lineage_too_deep')


def _authorization(client, spec, root_id, snapshots):
    channel_id, connection_id = spec.get('production_channel_id'), spec.get('production_connection_id')
    _require(all(isinstance(v, str) and _ID.fullmatch(v) for v in (channel_id, connection_id)))
    profile = _object(_snapshot(client, PROFILE_PREFIX + channel_id, snapshots))
    channel = _object(_snapshot(client, OAUTH_CHANNEL_PREFIX + channel_id, snapshots))
    credential = _snapshot(client, OAUTH_CREDENTIAL_PREFIX + channel_id, snapshots)
    epoch = _snapshot(client, AUTH_EPOCH_KEY, snapshots, optional=True)
    state = _snapshot(client, CHANNEL_STATE_PREFIX + channel_id, snapshots, kind='hash')
    _require(profile.get('channel_id') == channel_id and profile.get('production_enabled') is True
             and profile.get('auto_publish') is True and profile.get('release_mode') == 'public'
             and isinstance(profile.get('profile_revision'), str) and bool(profile['profile_revision'])
             and profile['profile_revision'] == spec.get('production_profile_revision'))
    _require(channel.get('id') == channel_id and channel.get('connection_id') == connection_id
             and channel.get('requires_reconnect') is not True and isinstance(credential, str) and bool(credential)
             and client.sismember(OAUTH_CHANNEL_INDEX, channel_id)
             and (epoch is None or isinstance(epoch, str) and re.fullmatch(r'[0-9]+', epoch)))
    topics, interval = profile.get('production_topics'), profile.get('production_interval_hours')
    _require(isinstance(topics, list) and 1 <= len(topics) <= 60
             and all(isinstance(t, str) and t.strip() and len(t) <= 240 for t in topics)
             and type(interval) is int and 6 <= interval <= 168)
    topics = [topic.strip() for topic in topics]
    cursor = int(state.get('cursor', '-1'))
    due = float(state.get('next_due', 'nan'))
    _require(str(cursor) == state.get('cursor') and 1 <= cursor <= len(topics)
             and math.isfinite(due) and due >= 0
             and state.get('consumed_prefix') == _prefix_digest(topics[:cursor])
             and state.get('paused_reason') == 'previous_render_failed'
             and state.get('last_task_id') == root_id and state.get('last_result') == 'FAILURE'
             and state.get('dispatch_status') == 'finished' and not state.get('active_task_id')
             and state.get('profile_revision') == profile['profile_revision']
             and state.get('connection_id') == connection_id)
    identity = str(profile.get('channel_identity') or '').strip()[:240]
    brief = topics[cursor - 1] + (f'\n\nChannel editorial direction: {identity}' if identity else '')
    _require(spec.get('topic') == brief and spec.get('language') == profile.get('default_language')
             and spec.get('channel_id') == str(profile.get('route_label') or channel_id).strip()
             and type(spec.get('production_topic_index')) is int and spec['production_topic_index'] == cursor - 1)
    return {'channel_id': channel_id, 'connection_id': connection_id,
            'profile_revision': profile['profile_revision'], 'profile_sha256': _digest(profile),
            'channel_sha256': _digest(channel), 'credential_sha256': _digest(credential),
            'authorization_epoch_sha256': _digest(epoch), 'schedule_sha256': _digest(state)}


def _premedia_checkpoint_binding(source, spec):
    """Retain a failed English narration without granting its reuse or approval.

    This is a new-story continuation of an existing private full-rebuild grant,
    not a general retry for any job with an audio pointer. Canonical shape is
    checked here and the entire pointer is bound into the new private policy;
    neither the old artifact nor its claims/ledger is rewritten. The fresh
    worker never consumes this audio, so this is not an asset-integrity check.
    """
    _require(spec.get('language') == 'en' and source.get('failure_stage') == 'audio_qc_retry',
             'full_rebuild_paid_source_required')
    pointer = source.get('audio_candidate_checkpoint')
    _require(isinstance(pointer, dict) and set(pointer) == _AUDIO_POINTER_FIELDS
             and type(pointer.get('version')) is int and pointer['version'] == 1
             and pointer.get('status') == 'unapproved_candidate'
             and pointer.get('qa_approved') is False and pointer.get('requires_full_qa') is True,
             'full_rebuild_audio_checkpoint_invalid')
    _require(all(isinstance(pointer.get(k), str) and _SHA.fullmatch(pointer[k])
                 for k in ('audio_sha256', 'metadata_sha256', 'package_sha256'))
             and type(pointer.get('size')) is int and 1024 <= pointer['size'] <= 14 * 1024 * 1024,
             'full_rebuild_audio_checkpoint_invalid')
    prefix = f"audio_candidates/{source['task_id']}/{pointer['audio_sha256']}"
    _require(pointer['audio_key'] == prefix + '/candidate.mp3'
             and pointer['metadata_key'] == prefix + '/metadata-' + pointer['metadata_sha256'] + '.json',
             'full_rebuild_audio_checkpoint_invalid')
    return {'retained_audio_checkpoint_sha256': _digest(pointer)}


def _planning_retry_authorization(client, source, spec, cap, root_id, auth, snapshots):
    """Inherit a genuine full-rebuild grant after a validated pre-media failure.

    This does not restart a worker or reopen its old execution claim. An
    explicit operator dispatch still reserves exactly one new child below.
    """
    _require(all(source.get(k) is None for k in (
                 'audio_candidate_checkpoint_error',
                 'generated_asset_candidates', 'voice_candidate_reuse', 'voice_replacement',
                 'repair_checkpoint', 'qa_workprint', 'result', 'video_key', 'youtube_video_id',
                 'youtube', 'youtube_automation',
             )), 'full_rebuild_paid_source_required')
    if source.get('failure_stage') == 'director_qc':
        _require(source.get('audio_candidate_checkpoint') is None, 'full_rebuild_paid_source_required')
        retained = {}
    else:
        retained = _premedia_checkpoint_binding(source, spec)
    source_id, parent_id = source['task_id'], source.get('parent_id')
    _require(isinstance(parent_id, str) and _TASK.fullmatch(parent_id))
    policy = _object(_snapshot(client, POLICY_PREFIX + source_id, snapshots))
    _require(_object(_snapshot(client, SOURCE_PREFIX + parent_id, snapshots)) == policy)
    parent = _object(_snapshot(client, JOB_PREFIX + parent_id, snapshots))
    parent_ledger = _snapshot(client, PAID_CREATE_BUDGET_PREFIX + parent_id, snapshots, kind='hash')
    dispatch = _snapshot(client, RETRY_DISPATCH_PREFIX + parent_id, snapshots, kind='hash')
    # _lineage has already proved the reciprocal claim/execution and failed
    # ancestors. Bind that exact full claim to its immutable private grant.
    _require(type(policy.get('version')) is int and policy['version'] == 1
             and policy.get('mode') == dispatch.get('mode') == 'full'
             and policy.get('child_task_id') == source_id and policy.get('source_task_id') == parent_id
             and policy.get('lineage_root_task_id') == root_id
             and parent.get('spec') == spec and policy.get('spec_sha256') == _digest(spec)
             and type(policy.get('original_paid_create_cap')) is int and policy['original_paid_create_cap'] == cap
             and all(policy.get(k) is True for k in _FLAGS)
             and all(policy.get(k) == v for k, v in auth.items())
             and policy.get('dispatch_token_sha256') == _digest(dispatch['token'])
             and policy.get('source_paid_ledger_sha256') == _digest(parent_ledger))
    for prefix in (REPAIR_CHECKPOINT_PREFIX, REPAIR_CHECKPOINT_CLAIM_PREFIX):
        _absent(client, prefix + source_id, snapshots)
    return {'inherited_policy_sha256': _digest(policy), **retained}


def _source(client, source_id, snapshots, *, reserved=False):
    source = _object(_snapshot(client, JOB_PREFIX + source_id, snapshots))
    spec = source.get('spec')
    _require(isinstance(spec, dict) and spec.get('mode') == 'production' and spec.get('format') == 'shorts'
             and type(spec.get('duration_minutes')) in (int, float) and spec['duration_minutes'] == .5
             and spec.get('workflow') in {'auto', 'scene_repair'} and spec.get('music') == 'off'
             and spec.get('production_scheduled') is True and spec.get('publish_after_render') is True)
    _failed(source, source_id, spec)
    ledger = _snapshot(client, PAID_CREATE_BUDGET_PREFIX + source_id, snapshots, kind='hash')
    _require(all(isinstance(ledger.get(k), str) and re.fullmatch(r'[0-9]+', ledger[k]) for k in ('cap', 'used')))
    cap, used = int(ledger['cap']), int(ledger['used'])
    _require(0 <= used <= cap and 2 <= cap <= 6, 'full_rebuild_paid_source_required')
    root_id = _lineage(client, source, spec, snapshots)
    auth = _authorization(client, spec, root_id, snapshots)
    if used == 0:
        _require(ledger['used'] == '0', 'full_rebuild_paid_source_required')
        auth.update(_planning_retry_authorization(client, source, spec, cap, root_id, auth, snapshots))
    auth['source_paid_ledger_sha256'] = _digest(ledger)
    if not reserved:
        _require(not source.get('retry_child_task_id') and source.get('retry_claimed') is not True)
        _absent(client, RETRY_DISPATCH_PREFIX + source_id, snapshots)
        _absent(client, REPAIR_CHECKPOINT_CLAIM_PREFIX + source_id, snapshots)
        # Do not consume or even rewrite an existing approved repair package.
        _snapshot(client, REPAIR_CHECKPOINT_PREFIX + source_id, snapshots, optional=True)
    return source, spec, cap, root_id, auth


def _idle_channel(client, channel_id, snapshots, *, ignore_task_id=None):
    _absent(client, ACTIVE_KEY, snapshots)
    indexed = _snapshot(client, JOB_INDEX, snapshots, kind='zset', optional=True) or []
    _require(ignore_task_id is not None or len(indexed) < MAX_INDEXED_JOBS,
             'full_rebuild_registry_unbounded')
    for task_id in indexed:
        _require(isinstance(task_id, str) and _TASK.fullmatch(task_id))
        raw = _snapshot(client, JOB_PREFIX + task_id, snapshots, optional=True)
        if raw is None:
            continue  # expired job; index membership alone is not running work
        job = _object(raw)
        _require(job.get('task_id') == task_id and isinstance(job.get('spec'), dict))
        if task_id == ignore_task_id:
            continue
        if job.get('state') in _ACTIVE_STATES:
            destination = job['spec'].get('production_channel_id') or job['spec'].get('youtube_channel_id')
            _require(isinstance(destination, str) and bool(destination), 'full_rebuild_active_channel_unknown')
            _require(destination != channel_id, 'full_rebuild_channel_busy')


_COMPARE = r'''
local snapshots = cjson.decode(ARGV[8])
for _, item in ipairs(snapshots) do
  local kind = redis.call('TYPE', item['key'])['ok']
  if kind ~= item['kind'] then return {-3, ''} end
  if kind == 'string' then
    if redis.call('GET', item['key']) ~= item['value'] then return {-3, ''} end
  elseif kind == 'hash' then
    local current = redis.call('HGETALL', item['key'])
    local count = 0
    for _, _ in pairs(item['value']) do count = count + 1 end
    if #current ~= count * 2 then return {-3, ''} end
    for i = 1, #current, 2 do
      if item['value'][current[i]] ~= current[i + 1] then return {-3, ''} end
    end
  elseif kind == 'zset' then
    local current = redis.call('ZRANGE', item['key'], 0, -1)
    if #current ~= #item['value'] then return {-3, ''} end
    for i = 1, #current do
      if current[i] ~= item['value'][i] then return {-3, ''} end
    end
  end
end
if redis.call('SISMEMBER', KEYS[9], ARGV[12]) ~= 1 then return {-3, ''} end
'''
_RESERVE = _COMPARE + '\nlocal function claim_existing_retry()\n' + _CLAIM_RETRY_DISPATCH + r'''
end
local result = claim_existing_retry()
if result[1] ~= 2 then return result end
redis.call('SET', KEYS[6], ARGV[9])
redis.call('SET', KEYS[7], ARGV[9])
redis.call('SETEX', KEYS[8], ARGV[4], ARGV[10])
redis.call('ZADD', KEYS[10], ARGV[11], ARGV[2])
redis.call('EXPIRE', KEYS[10], ARGV[4])
return result
'''
_VERIFY = _COMPARE + "\nreturn {1, ''}\n"


def _keys(source_id, child_id):
    return [JOB_PREFIX + source_id, REPAIR_CHECKPOINT_PREFIX + source_id,
            REPAIR_CHECKPOINT_CLAIM_PREFIX + source_id, RETRY_DISPATCH_PREFIX + source_id,
            RETRY_CHILD_CLAIM_PREFIX + child_id, POLICY_PREFIX + child_id,
            SOURCE_PREFIX + source_id, JOB_PREFIX + child_id, OAUTH_CHANNEL_INDEX, JOB_INDEX]


def _evaluate(client, script, source_id, child_id, token, snapshots, policy, child=None):
    now = time.time()
    keys = _keys(source_id, child_id)
    return client.eval(script, len(keys), *keys, token, child_id,
                       datetime.fromtimestamp(now, timezone.utc).isoformat(), JOB_TTL_SECONDS,
                       '0', RETRY_DISPATCH_TTL_SECONDS, source_id,
                       _json([{'key': key, **value} for key, value in snapshots.items()]),
                       _json(policy), _json(child), now, policy['channel_id'])


def _existing(client, source_id):
    raw = client.get(SOURCE_PREFIX + source_id)
    if raw:
        policy = _object(raw)
        _require(policy.get('source_task_id') == source_id and policy.get('mode') == 'full')
        child_id = policy.get('child_task_id')
    else:
        child_id = client.hgetall(RETRY_DISPATCH_PREFIX + source_id).get('child_task_id')
    if child_id:
        _require(isinstance(child_id, str) and _TASK.fullmatch(child_id))
        return {'claimed': False, 'child_task_id': child_id}
    return None


class _FullRebuildBinding:
    """Internal object, not a user-provided render option or JSON authorization."""

    def __init__(self, client, source_id, child_id, token, snapshots, policy, child):
        self.client, self.source_id, self.child_id, self.token = client, source_id, child_id, token
        self.snapshots, self.policy, self.child = snapshots, policy, child


def _claim_bound_full_rebuild(source_id, child_id, token, binding):
    _require(type(binding) is _FullRebuildBinding
             and (source_id, child_id, token) == (binding.source_id, binding.child_id, binding.token))
    result = _evaluate(binding.client, _RESERVE, source_id, child_id, token,
                       binding.snapshots, binding.policy, binding.child)
    if isinstance(result, (list, tuple)) and len(result) == 2 and result[0] == 2:
        return {'claimed': True, 'child_task_id': child_id, 'mode': 'full', 'checkpoint': None}
    existing = _existing(binding.client, source_id)
    if existing:
        return existing
    raise FullVideoRebuildError('full_rebuild_state_changed')


def reserve_full_video_rebuild(source_id, child_id, token):
    """Private server call. Only the first successful reservation may enqueue."""
    try:
        _require(all(isinstance(v, str) and _TASK.fullmatch(v) for v in (source_id, child_id))
                 and source_id != child_id and isinstance(token, str) and _TOKEN.fullmatch(token))
        client, snapshots = _redis(), {}
        existing = _existing(client, source_id)
        if existing:
            return existing
        source, spec, cap, root_id, auth = _source(client, source_id, snapshots)
        _idle_channel(client, auth['channel_id'], snapshots)
        for prefix in (JOB_PREFIX, RETRY_CHILD_CLAIM_PREFIX, RETRY_CHILD_EXECUTION_PREFIX,
                       POLICY_PREFIX, PAID_CREATE_BUDGET_PREFIX, UPLOAD_PREFIX, EXECUTION_LOCK_PREFIX):
            _absent(client, prefix + child_id, snapshots)
        _absent(client, SOURCE_PREFIX + source_id, snapshots)
        policy = {'version': 1, 'mode': 'full', 'source_task_id': source_id, 'child_task_id': child_id,
                  'lineage_root_task_id': root_id, 'spec_sha256': _digest(spec),
                  'original_paid_create_cap': cap, 'dispatch_token_sha256': _digest(token),
                  **{k: True for k in _FLAGS}, **auth}
        now = time.time()
        iso = datetime.fromtimestamp(now, timezone.utc).isoformat()
        child = {'task_id': child_id, 'kind': 'render', 'parent_id': source_id, 'spec': spec,
                 'state': 'PENDING', 'stage': 'queued', 'progress': 0,
                 'message': 'Tam yeniden üretim kuyruğa alındı; kalite onayı bekleniyor.',
                 'created_ts': now, 'created_at': iso, 'updated_at': iso, 'result': None, 'error': None}
        binding = _FullRebuildBinding(client, source_id, child_id, token, snapshots, policy, child)
        result = claim_retry_dispatch(source_id, child_id, token, allow_repair=False, full_rebuild_binding=binding)
        if result['claimed']:
            return {**result, 'spec': json.loads(_json(spec)), 'full_rebuild': _safe_policy(policy)}
        return result
    except FullVideoRebuildError:
        raise
    except Exception:
        raise FullVideoRebuildError('full_rebuild_unavailable') from None


def _safe_policy(policy):
    return {k: policy[k] for k in ('version', 'mode', 'source_task_id', 'child_task_id',
                                  'spec_sha256', 'original_paid_create_cap', *_FLAGS)}


def get_full_rebuild_policy(task_id, source_id, runtime_spec):
    """Read-only proof after the existing one-shot execution guard, before cost."""
    try:
        _require(all(isinstance(v, str) and _TASK.fullmatch(v) for v in (task_id, source_id))
                 and task_id != source_id and isinstance(runtime_spec, dict))
        client, snapshots = _redis(), {}
        policy = _object(_snapshot(client, POLICY_PREFIX + task_id, snapshots))
        source, spec, cap, root_id, auth = _source(client, source_id, snapshots, reserved=True)
        _require(type(policy.get('version')) is int and policy['version'] == 1 and policy.get('mode') == 'full'
                 and policy.get('source_task_id') == source_id and policy.get('child_task_id') == task_id
                 and policy.get('lineage_root_task_id') == root_id
                 and spec == runtime_spec and policy.get('spec_sha256') == _digest(runtime_spec)
                 and type(policy.get('original_paid_create_cap')) is int and policy['original_paid_create_cap'] == cap
                 and all(policy.get(k) is True for k in _FLAGS)
                 and all(policy.get(k) == v for k, v in auth.items())
                 and _object(_snapshot(client, SOURCE_PREFIX + source_id, snapshots)) == policy)
        child = _object(_snapshot(client, JOB_PREFIX + task_id, snapshots))
        _require(child.get('task_id') == task_id and child.get('parent_id') == source_id
                 and child.get('kind') == 'render' and child.get('state') in {'PENDING', 'STARTED', 'PROGRESS'}
                 and child.get('spec') == runtime_spec
                 and not any(child.get(k) for k in ('result', 'retry_child_task_id', 'youtube', 'youtube_automation',
                                                    'video_key', 'audio_candidate_checkpoint', 'generated_asset_candidates')))
        _idle_channel(client, auth['channel_id'], snapshots, ignore_task_id=task_id)
        ledger = _snapshot(client, PAID_CREATE_BUDGET_PREFIX + task_id, snapshots, kind='hash', optional=True)
        _require(ledger is None or ledger.get('used') == '0' and ledger.get('cap') == str(cap))
        for prefix in (UPLOAD_PREFIX, EXECUTION_LOCK_PREFIX, REPAIR_CHECKPOINT_PREFIX, REPAIR_CHECKPOINT_CLAIM_PREFIX,
                       RETRY_DISPATCH_PREFIX):
            _absent(client, prefix + task_id, snapshots)
        dispatch = _snapshot(client, RETRY_DISPATCH_PREFIX + source_id, snapshots, kind='hash')
        claim = _snapshot(client, RETRY_CHILD_CLAIM_PREFIX + task_id, snapshots, kind='hash')
        execution = _snapshot(client, RETRY_CHILD_EXECUTION_PREFIX + task_id, snapshots)
        token = dispatch.get('token')
        _require(isinstance(token, str) and _TOKEN.fullmatch(token) and _digest(token) == policy.get('dispatch_token_sha256')
                 and dispatch.get('child_task_id') == task_id and dispatch.get('mode') == 'full'
                 and dispatch.get('state') in {'reserved', 'dispatched', 'uncertain'}
                 and claim.get('source_task_id') == source_id and claim.get('token') == token and execution == token
                 and source.get('retry_child_task_id') == task_id and source.get('retry_claimed') is True
                 and source.get('retry_dispatch_state') == dispatch.get('state'))
        result = _evaluate(client, _VERIFY, source_id, task_id, token, snapshots, policy)
        _require(result == [1, ''], 'full_rebuild_state_changed')
        return _safe_policy(policy)
    except FullVideoRebuildError:
        raise
    except Exception:
        raise FullVideoRebuildError('full_rebuild_unavailable') from None


def dispatch_full_video_rebuild(source_id):
    """Operator entry point: reserve once, enqueue once, never retry ambiguity."""
    child_id, token = str(uuid4()), secrets.token_urlsafe(32)
    reserved = reserve_full_video_rebuild(source_id, child_id, token)
    if not reserved['claimed']:
        return {**reserved, 'status': 'already_claimed'}
    spec = reserved['spec']
    options = {k: v for k, v in spec.items() if k not in {'topic', 'duration_minutes', 'language', 'channel_id'}}
    try:
        from app.tasks import run_video_pipeline

        run_video_pipeline.apply_async(
            args=(spec['topic'], spec['duration_minutes'], spec['language'], spec['channel_id'], options, None, source_id),
            kwargs={'full_rebuild_source_id': source_id}, task_id=child_id,
        )
        marked = mark_retry_dispatch(source_id, token, 'dispatched')
        status = 'dispatched' if marked else 'dispatch_uncertain'
    except Exception:
        status = 'dispatch_uncertain'
        try:
            mark_retry_dispatch(source_id, token, 'uncertain')
        except Exception:
            pass  # the durable reservation remains fenced; never redispatch
    return {'claimed': True, 'child_task_id': child_id, 'status': status,
            'full_rebuild': reserved['full_rebuild']}
