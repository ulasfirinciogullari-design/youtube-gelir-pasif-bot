"""One explicitly reserved replacement voice; never a reusable TTS permission.

Only Redis state is touched. The worker must still hash-check the original
candidate, revalidate its unchanged story, and run every normal QA gate.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re

import redis

from app.config import settings
from app.services.studio_state import (
    JOB_PREFIX, JOB_TTL_SECONDS, RETRY_DISPATCH_PREFIX, RETRY_CHILD_CLAIM_PREFIX,
    RETRY_CHILD_EXECUTION_PREFIX, REPAIR_CHECKPOINT_PREFIX,
    REPAIR_CHECKPOINT_CLAIM_PREFIX, RETRY_DISPATCH_TTL_SECONDS,
    PAID_CREATE_BUDGET_PREFIX, _CLAIM_RETRY_DISPATCH,
)
from app.services.channel_production import (
    PROFILE_PREFIX, OAUTH_CHANNEL_PREFIX, OAUTH_CREDENTIAL_PREFIX, OAUTH_CHANNEL_INDEX,
)
from app.services.youtube_auth import AUTH_EPOCH_KEY
from app.services.youtube_publish_state import UPLOAD_PREFIX, EXECUTION_LOCK_PREFIX


POLICY_PREFIX = 'youtube_studio:voice_replacement:v1:policy:'
LINEAGE_PREFIX = 'youtube_studio:voice_replacement:v1:lineage:'
ATTEMPT_PREFIX = 'youtube_studio:voice_replacement:v1:attempt:'
_TASK = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
_ID = re.compile(r'^[A-Za-z0-9_-]{8,128}$')
_SHA = re.compile(r'^[0-9a-f]{64}$')
_TOKEN = re.compile(r'^[A-Za-z0-9_-]{16,256}$')
_POINTER_FIELDS = {'version', 'status', 'qa_approved', 'requires_full_qa', 'audio_key',
                   'metadata_key', 'audio_sha256', 'metadata_sha256', 'package_sha256', 'size'}


class VoiceReplacementError(RuntimeError):
    """Fixed-code rejection, with no credential or provider response details."""


def _redis():
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


def _require(condition, code='voice_replacement_ineligible'):
    if not condition:
        raise VoiceReplacementError(code)


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
    value = client.hgetall(key) if actual == 'hash' else client.get(key)
    snapshots[key] = {'kind': actual, 'value': value}
    return value


def _absent(client, key, snapshots):
    _require(_snapshot(client, key, snapshots, optional=True) is None)


def _paid_zero(client, task_id, snapshots):
    ledger = _snapshot(client, PAID_CREATE_BUDGET_PREFIX + task_id, snapshots, kind='hash')
    _require(ledger.get('used') == '0' and isinstance(ledger.get('cap'), str)
             and re.fullmatch(r'[1-9][0-9]?', ledger['cap']) is not None)
    return int(ledger['cap'])


def _checkpoint(source, expected_sha):
    pointer = source.get('audio_candidate_checkpoint')
    _require(isinstance(pointer, dict) and set(pointer) == _POINTER_FIELDS
             and type(pointer.get('version')) is int and pointer['version'] == 1
             and pointer.get('status') == 'unapproved_candidate'
             and pointer.get('qa_approved') is False and pointer.get('requires_full_qa') is True
             and pointer.get('audio_sha256') == expected_sha)
    _require(all(isinstance(pointer.get(k), str) and _SHA.fullmatch(pointer[k])
                 for k in ('audio_sha256', 'metadata_sha256', 'package_sha256')))
    _require(type(pointer.get('size')) is int and 1024 <= pointer['size'] <= 14 * 1024 * 1024)
    prefix = f"audio_candidates/{source['task_id']}/{expected_sha}"
    _require(pointer['audio_key'] == prefix + '/candidate.mp3'
             and pointer['metadata_key'] == prefix + '/metadata-' + pointer['metadata_sha256'] + '.json')
    return pointer


def _failed_audio(job, task_id, spec):
    _require(job.get('task_id') == task_id and job.get('kind') == 'render'
             and job.get('state') == 'FAILURE'
             and job.get('failure_stage') in {'audio_qc', 'audio_qc_retry', 'audio_pause_recheck'}
             and job.get('spec') == spec and not job.get('result'))
    _require(not any(job.get(k) for k in ('youtube', 'youtube_automation', 'video_key', 'youtube_video_id')))


def _auth(client, spec, snapshots):
    channel_id = spec.get('production_channel_id')
    _require(isinstance(channel_id, str) and _ID.fullmatch(channel_id)
             and spec.get('production_scheduled') is True and spec.get('publish_after_render') is True)
    profile = _object(_snapshot(client, PROFILE_PREFIX + channel_id, snapshots))
    channel = _object(_snapshot(client, OAUTH_CHANNEL_PREFIX + channel_id, snapshots))
    credential = _snapshot(client, OAUTH_CREDENTIAL_PREFIX + channel_id, snapshots)
    epoch = _snapshot(client, AUTH_EPOCH_KEY, snapshots, optional=True)
    _require(profile.get('channel_id') == channel_id and profile.get('production_enabled') is True
             and profile.get('auto_publish') is True and profile.get('release_mode') == 'public'
             and profile.get('profile_revision') == spec.get('production_profile_revision'))
    # The scheduler stores its editorial route in spec.channel_id; the actual
    # OAuth destination remains the separate production_channel_id binding.
    route = str(profile.get('route_label') or channel_id).strip()
    _require(spec.get('channel_id') == route)
    connection_id = spec.get('production_connection_id')
    _require(isinstance(connection_id, str) and _ID.fullmatch(connection_id)
             and channel.get('id') == channel_id and channel.get('connection_id') == connection_id
             and channel.get('requires_reconnect') is not True
             and isinstance(credential, str) and bool(credential)
             and client.sismember(OAUTH_CHANNEL_INDEX, channel_id))
    _require(epoch is None or isinstance(epoch, str) and re.fullmatch(r'[0-9]+', epoch))
    return {'channel_id': channel_id, 'connection_id': connection_id,
            'profile_revision': profile['profile_revision'], 'profile_sha256': _digest(profile),
            'channel_sha256': _digest(channel), 'credential_sha256': _digest(credential),
            'authorization_epoch_sha256': _digest(epoch)}


def _source(client, source_id, audio_sha, snapshots, *, reserved=False):
    source = _object(_snapshot(client, JOB_PREFIX + source_id, snapshots))
    spec = source.get('spec')
    _require(isinstance(spec, dict) and spec.get('mode') == 'production'
             and spec.get('format') == 'shorts' and type(spec.get('duration_minutes')) in (int, float)
             and spec['duration_minutes'] == 0.5 and spec.get('language') == 'tr')
    _failed_audio(source, source_id, spec)
    pointer = _checkpoint(source, audio_sha)
    paid_cap = _paid_zero(client, source_id, snapshots)
    for prefix in (UPLOAD_PREFIX, EXECUTION_LOCK_PREFIX, REPAIR_CHECKPOINT_PREFIX, REPAIR_CHECKPOINT_CLAIM_PREFIX):
        _absent(client, prefix + source_id, snapshots)
    if not reserved:
        _require(not source.get('retry_child_task_id') and source.get('retry_claimed') is not True
                 and not source.get('voice_replacement'))
        _absent(client, RETRY_DISPATCH_PREFIX + source_id, snapshots)
    auth = _auth(client, spec, snapshots)
    return source, spec, pointer, paid_cap, auth


def _lineage(client, source, spec, snapshots):
    seen = set()
    child = source
    for _ in range(17):
        task_id = child['task_id']
        _require(task_id not in seen and not child.get('voice_replacement'))
        seen.add(task_id)
        _absent(client, POLICY_PREFIX + task_id, snapshots)
        _absent(client, LINEAGE_PREFIX + task_id, snapshots)
        parent_id = child.get('parent_id')
        if not parent_id:
            return task_id
        _require(isinstance(parent_id, str) and _TASK.fullmatch(parent_id))
        parent = _object(_snapshot(client, JOB_PREFIX + parent_id, snapshots))
        _failed_audio(parent, parent_id, spec)
        _paid_zero(client, parent_id, snapshots)
        for prefix in (UPLOAD_PREFIX, EXECUTION_LOCK_PREFIX):
            _absent(client, prefix + parent_id, snapshots)
        dispatch = _snapshot(client, RETRY_DISPATCH_PREFIX + parent_id, snapshots, kind='hash')
        claim = _snapshot(client, RETRY_CHILD_CLAIM_PREFIX + task_id, snapshots, kind='hash')
        execution = _snapshot(client, RETRY_CHILD_EXECUTION_PREFIX + task_id, snapshots)
        token = dispatch.get('token')
        _require(isinstance(token, str) and _TOKEN.fullmatch(token)
                 and parent.get('retry_child_task_id') == task_id and parent.get('retry_claimed') is True
                 and dispatch.get('child_task_id') == task_id and dispatch.get('mode') == 'full'
                 and dispatch.get('state') in {'reserved', 'dispatched', 'uncertain'}
                 and claim.get('source_task_id') == parent_id and claim.get('token') == token
                 and execution == token)
        child = parent
    raise VoiceReplacementError('voice_replacement_lineage_invalid')


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
  end
end
if redis.call('SISMEMBER', KEYS[9], ARGV[12]) ~= 1 then return {-3, ''} end
'''
# Reuse the existing full-retry state machine verbatim, after eligibility CAS.
_RESERVE = _COMPARE + '\nlocal function claim_existing_retry()\n' + _CLAIM_RETRY_DISPATCH + r'''
end
local result = claim_existing_retry()
if result[1] ~= 2 then return result end
redis.call('SET', KEYS[6], ARGV[9])
redis.call('SET', KEYS[7], ARGV[9])
local job = cjson.decode(redis.call('GET', KEYS[1]))
job['voice_replacement'] = cjson.decode(ARGV[10])
redis.call('SETEX', KEYS[1], ARGV[4], cjson.encode(job))
return result
'''
_ACQUIRE = _COMPARE + r'''
if redis.call('EXISTS', KEYS[8]) ~= 0 then return {-3, ''} end
redis.call('SET', KEYS[8], ARGV[11])
return {1, ''}
'''


def _keys(source_id, child_id, root_id):
    return [JOB_PREFIX + source_id, REPAIR_CHECKPOINT_PREFIX + source_id,
            REPAIR_CHECKPOINT_CLAIM_PREFIX + source_id, RETRY_DISPATCH_PREFIX + source_id,
            RETRY_CHILD_CLAIM_PREFIX + child_id, POLICY_PREFIX + child_id,
            LINEAGE_PREFIX + root_id, ATTEMPT_PREFIX + child_id, OAUTH_CHANNEL_INDEX]


def _evaluate(client, script, keys, snapshots, source_id, child_id, token, policy, attempt=None):
    marker = {k: policy[k] for k in ('version', 'source_task_id', 'child_task_id', 'audio_sha256',
                                     'model', 'model_id', 'max_attempts', 'requires_full_qa')}
    now = datetime.now(timezone.utc).isoformat()
    return client.eval(script, len(keys), *keys, token, child_id, now, JOB_TTL_SECONDS,
                       '0', RETRY_DISPATCH_TTL_SECONDS, source_id,
                       _json([{'key': key, **value} for key, value in snapshots.items()]),
                       _json(policy), _json(marker), _json(attempt), policy['channel_id'])


def _inputs(source_id, child_id, audio_sha):
    _require(all(isinstance(value, str) and _TASK.fullmatch(value) for value in (source_id, child_id))
             and source_id != child_id and isinstance(audio_sha, str) and _SHA.fullmatch(audio_sha),
             'voice_replacement_identity_invalid')


def reserve_voice_replacement(source_id, child_id, token, expected_audio_sha256):
    """Reserve one child and one new-voice allowance without clearing claims."""
    try:
        _inputs(source_id, child_id, expected_audio_sha256)
        _require(isinstance(token, str) and _TOKEN.fullmatch(token))
        client, snapshots = _redis(), {}
        # An old reservation never returns dispatch authority a second time.
        existing = client.hgetall(RETRY_DISPATCH_PREFIX + source_id)
        if existing:
            existing_child = existing.get('child_task_id')
            _require(isinstance(existing_child, str) and _TASK.fullmatch(existing_child))
            return {'claimed': False, 'child_task_id': existing_child}
        source, spec, pointer, cap, auth = _source(client, source_id, expected_audio_sha256, snapshots)
        root_id = _lineage(client, source, spec, snapshots)
        for prefix in (JOB_PREFIX, RETRY_CHILD_CLAIM_PREFIX, RETRY_CHILD_EXECUTION_PREFIX,
                       POLICY_PREFIX, ATTEMPT_PREFIX):
            _absent(client, prefix + child_id, snapshots)
        policy = {'version': 1, 'source_task_id': source_id, 'child_task_id': child_id,
                  'lineage_root_task_id': root_id, 'audio_sha256': expected_audio_sha256,
                  'checkpoint_sha256': _digest(pointer), 'spec_sha256': _digest(spec),
                  'package_sha256': pointer['package_sha256'], 'paid_create_cap': cap,
                  'model': 'eleven_multilingual_v2', 'model_id': 'eleven_multilingual_v2',
                  'voice_profile': 'turkish_multilingual_v2',
                  'max_attempts': 1, 'requires_full_qa': True, 'dispatch_token_sha256': _digest(token), **auth}
        result = _evaluate(client, _RESERVE, _keys(source_id, child_id, root_id), snapshots,
                           source_id, child_id, token, policy)
        _require(isinstance(result, (list, tuple)) and len(result) == 2 and result[0] == 2,
                 'voice_replacement_state_changed')
        return {'claimed': True, 'child_task_id': child_id, 'mode': 'full', 'checkpoint': None,
                'spec': json.loads(_json(spec)),
                'voice_replacement': {k: policy[k] for k in ('version', 'source_task_id', 'child_task_id',
                                      'audio_sha256', 'model', 'model_id', 'max_attempts', 'requires_full_qa')}}
    except VoiceReplacementError:
        raise
    except Exception:
        raise VoiceReplacementError('voice_replacement_unavailable') from None


def _policy(client, child_id, source_id, audio_sha256, spec, snapshots):
    _inputs(source_id, child_id, audio_sha256)
    policy = _object(_snapshot(client, POLICY_PREFIX + child_id, snapshots))
    source, actual_spec, pointer, cap, auth = _source(client, source_id, audio_sha256, snapshots, reserved=True)
    _require(actual_spec == spec and type(policy.get('version')) is int and policy['version'] == 1
             and policy.get('child_task_id') == child_id and policy.get('source_task_id') == source_id
             and policy.get('audio_sha256') == audio_sha256 and policy.get('spec_sha256') == _digest(spec)
             and policy.get('checkpoint_sha256') == _digest(pointer)
             and policy.get('package_sha256') == pointer['package_sha256']
             and type(policy.get('paid_create_cap')) is int and policy['paid_create_cap'] == cap
             and policy.get('model') == 'eleven_multilingual_v2' and policy.get('model_id') == 'eleven_multilingual_v2'
             and policy.get('voice_profile') == 'turkish_multilingual_v2'
             and type(policy.get('max_attempts')) is int and policy['max_attempts'] == 1
             and policy.get('requires_full_qa') is True and all(policy.get(k) == v for k, v in auth.items()))
    root_id = policy.get('lineage_root_task_id')
    _require(isinstance(root_id, str) and _TASK.fullmatch(root_id))
    _require(_object(_snapshot(client, LINEAGE_PREFIX + root_id, snapshots)) == policy)
    _absent(client, ATTEMPT_PREFIX + child_id, snapshots)
    child = _object(_snapshot(client, JOB_PREFIX + child_id, snapshots))
    _require(child.get('task_id') == child_id and child.get('parent_id') == source_id
             and child.get('kind') == 'render' and child.get('state') in {'PENDING', 'STARTED', 'PROGRESS'}
             and child.get('spec') == spec and not child.get('result') and not child.get('retry_child_task_id'))
    _require(_paid_zero(client, child_id, snapshots) == cap)
    for prefix in (UPLOAD_PREFIX, EXECUTION_LOCK_PREFIX):
        _absent(client, prefix + child_id, snapshots)
    dispatch = _snapshot(client, RETRY_DISPATCH_PREFIX + source_id, snapshots, kind='hash')
    claim = _snapshot(client, RETRY_CHILD_CLAIM_PREFIX + child_id, snapshots, kind='hash')
    execution = _snapshot(client, RETRY_CHILD_EXECUTION_PREFIX + child_id, snapshots)
    token = dispatch.get('token')
    _require(isinstance(token, str) and _TOKEN.fullmatch(token) and _digest(token) == policy['dispatch_token_sha256']
             and dispatch.get('child_task_id') == child_id and dispatch.get('mode') == 'full'
             and dispatch.get('state') in {'reserved', 'dispatched', 'uncertain'}
             and claim.get('token') == token and claim.get('source_task_id') == source_id and execution == token
             and source.get('retry_claimed') is True and source.get('retry_child_task_id') == child_id
             and isinstance(source.get('voice_replacement'), dict)
             and source['voice_replacement'].get('child_task_id') == child_id)
    return policy, token


def get_voice_replacement_policy(child_id, source_id, audio_sha256, spec):
    """Read-only validation; this result alone never authorizes an HTTP call."""
    try:
        policy, _ = _policy(_redis(), child_id, source_id, audio_sha256, spec, {})
        return _safe_policy(policy)
    except VoiceReplacementError:
        raise
    except Exception:
        raise VoiceReplacementError('voice_replacement_unavailable') from None


def _safe_policy(policy):
    # Internal authorization/credential fingerprints never become job/UI data.
    return {key: policy[key] for key in (
        'version', 'source_task_id', 'child_task_id', 'audio_sha256', 'package_sha256',
        'spec_sha256', 'model', 'model_id', 'voice_profile', 'max_attempts', 'requires_full_qa',
    )}


def acquire_voice_replacement_attempt(child_id, source_id, audio_sha256, spec, voice_id=None):
    """Consume before the sole TTS POST; lost replies or failures never reopen it."""
    try:
        _require(isinstance(voice_id, str) and _ID.fullmatch(voice_id), 'voice_replacement_voice_invalid')
        client, snapshots = _redis(), {}
        policy, token = _policy(client, child_id, source_id, audio_sha256, spec, snapshots)
        attempt = {'version': 1, 'child_task_id': child_id, 'source_task_id': source_id,
                   'audio_sha256': audio_sha256, 'spec_sha256': policy['spec_sha256'],
                   'model': policy['model'], 'voice_id': voice_id, 'attempts': 1,
                   'status': 'reserved_before_http', 'requires_full_qa': True}
        result = _evaluate(client, _ACQUIRE, _keys(source_id, child_id, policy['lineage_root_task_id']),
                           snapshots, source_id, child_id, token, policy, attempt)
        _require(isinstance(result, (list, tuple)) and len(result) == 2 and result[0] == 1,
                 'voice_replacement_attempt_unavailable')
        return {**_safe_policy(policy), 'voice_id': voice_id}
    except VoiceReplacementError:
        raise
    except Exception:
        raise VoiceReplacementError('voice_replacement_unavailable') from None
