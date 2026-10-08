"""Explicit, delivery-proven recovery of a failed production schedule.

This does not retry, enqueue, upload, or publish anything. It releases only a
known failed-render pause after its bounded, claimed retry lineage has already
completed automated QA and the specifically required private or public delivery.
All evidence is compared again in
one Redis transaction; retry, spending, upload, and series ledgers are read-only.
"""
from __future__ import annotations

import json
import math
import re
import time

import redis

from app.config import settings
from app.services.channel_production import (
    ACTIVE_KEY, CHANNEL_STATE_PREFIX, OAUTH_CHANNEL_INDEX, OAUTH_CHANNEL_PREFIX,
    OAUTH_CREDENTIAL_PREFIX, PROFILE_PREFIX, PRODUCTION_PREFIX, _prefix_digest,
)
from app.services.studio_state import (
    JOB_PREFIX, RETRY_CHILD_CLAIM_PREFIX, RETRY_DISPATCH_PREFIX,
    RETRY_CHILD_EXECUTION_PREFIX, REPAIR_CHECKPOINT_CLAIM_PREFIX,
    RENDER_CANCELLATION_PREFIX,
)
from app.services.source_publication_hold import HOLD_PREFIX
from app.services.youtube_automation import contains_synthetic_media
from app.services.youtube_publish_state import UPLOAD_PREFIX


RESUME_PREFIX = PRODUCTION_PREFIX + 'resume:'
PUBLIC_RESUME_PREFIX = PRODUCTION_PREFIX + 'resume_public:'
PUBLIC_RECOVERY_RESUME_PREFIX = PRODUCTION_PREFIX + 'resume_public_recovery:'
MAX_RETRY_HOPS = 16
_ID = re.compile(r'^[A-Za-z0-9_-]{8,128}$')
_TASK_ID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
_VIDEO_ID = re.compile(r'^[A-Za-z0-9_-]{6,128}$')
_STABLE_SPEC = (
    'topic', 'duration_minutes', 'language', 'channel_id', 'mode', 'format',
    'content_style', 'pace', 'visual_mix', 'music', 'subtitles', 'quality_threshold',
    'publish_after_render', 'production_scheduled', 'production_channel_id',
    'production_connection_id', 'production_profile_revision', 'production_topic_index',
)


class ProductionRecoveryError(RuntimeError):
    """A sanitized, fail-closed recovery rejection."""


def _redis():
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


# No writes until every snapshot, the global idle claim, and live connection
# membership have been checked. The audit handles duplicate/lost EVAL replies.
_RESUME = r'''
local prior = redis.call('GET', KEYS[1])
if prior then return {'already_resumed', prior} end
local snapshots = cjson.decode(ARGV[1])
for i, snapshot in ipairs(snapshots) do
  local key = KEYS[i + 5]
  local kind = redis.call('TYPE', key)['ok']
  if kind ~= snapshot['kind'] then return {'state_changed', ''} end
  if kind == 'string' then
    if redis.call('GET', key) ~= snapshot['value'] then return {'state_changed', ''} end
  elseif kind == 'hash' then
    local current = redis.call('HGETALL', key)
    local count = 0
    for _, _ in pairs(snapshot['value']) do count = count + 1 end
    if #current ~= count * 2 then return {'state_changed', ''} end
    for j = 1, #current, 2 do
      if snapshot['value'][current[j]] ~= current[j + 1] then
        return {'state_changed', ''}
      end
    end
  end
end
if redis.call('EXISTS', KEYS[3]) ~= 0 then return {'state_changed', ''} end
if redis.call('EXISTS', KEYS[4]) ~= 1
   or redis.call('SISMEMBER', KEYS[5], ARGV[3]) ~= 1 then
  return {'connection_missing', ''}
end
redis.call('SET', KEYS[1], ARGV[2])
redis.call('HDEL', KEYS[2], 'paused_reason')
redis.call('HSET', KEYS[2], 'next_due', ARGV[4])
return {'resumed', ARGV[2]}
'''


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ProductionRecoveryError(code)


def _object(raw: str | None, code: str) -> dict:
    try:
        value = json.loads(raw or '')
    except (TypeError, ValueError):
        raise ProductionRecoveryError(code) from None
    _require(isinstance(value, dict), code)
    return value


def _json_snapshot(client, key: str, snapshots: list, *, optional: bool = False) -> dict | None:
    raw = client.get(key)
    snapshots.append((key, {'kind': 'none' if raw is None else 'string', 'value': raw}))
    if raw is None and optional:
        return None
    return _object(raw, 'recovery_record_missing_or_invalid')


def _hash_snapshot(client, key: str, snapshots: list) -> dict:
    value = client.hgetall(key)
    _require(bool(value), 'recovery_record_missing_or_invalid')
    snapshots.append((key, {'kind': 'hash', 'value': value}))
    return value


def _private(record: dict) -> bool:
    return (
        record.get('privacy_status') == 'private'
        and record.get('release_status') == 'private'
        and not record.get('scheduled_publish_at')
        and not record.get('release_error_code')
    )


def _public(record: dict) -> bool:
    return (
        record.get('privacy_status') == 'public'
        and record.get('release_status') == 'public'
        and not record.get('scheduled_publish_at')
        and not record.get('release_error_code')
    )


def _completed_public_replay(record: dict, attribution: dict, plan: dict,
                             channel_id: str, connection_id: str, revision: str) -> bool:
    """Match the scheduler's compact public-delivery predicate, not QA proof.

    Actual completed publisher redelivery omits asset/revision fields. Only
    omissions are compatible; a present contradiction still fails. The caller
    must also verify the complete source attribution, plan, ledger and lineage.
    """
    def omitted_or(key, expected):
        return key not in record or (type(record[key]) is type(expected) and record[key] == expected)

    def empty(key):
        return record.get(key) is None or record.get(key) == ''

    return (
        record.get('idempotent_replay') is True
        and record.get('stage') == 'complete' and record.get('progress') == 100
        and _public(record)
        and record.get('target_channel_id') == channel_id
        and record.get('connection_id') == connection_id
        and all(empty(key) for key in ('scheduled_publish_at', 'release_error_code',
                                      'caption_error_code', 'thumbnail_error_code'))
        and omitted_or('profile_revision', revision)
        and omitted_or('caption_uploaded', True)
        and omitted_or('thumbnail_uploaded', attribution.get('thumbnail_uploaded'))
        and omitted_or('contains_synthetic_media', attribution.get('contains_synthetic_media'))
    )


def _audit_result(raw: str, status: str, channel_id: str, original_id: str,
                  recovered_id: str, revision: str, release_mode: str = 'private',
                  public_recovery: bool = False, continue_immediately: bool = False) -> dict:
    audit = _object(raw, 'recovery_audit_invalid')
    _require(
        audit.get('version') == 1 and audit.get('channel_id') == channel_id
        and audit.get('original_task_id') == original_id
        and audit.get('recovered_task_id') == recovered_id
        and audit.get('profile_revision') == revision,
        'recovery_audit_conflict',
    )
    _require(
        _TASK_ID.fullmatch(str(audit.get('publish_task_id') or '')) is not None
        and _VIDEO_ID.fullmatch(str(audit.get('youtube_video_id') or '')) is not None
        and audit.get('previous_paused_reason') == 'previous_render_failed'
        and type(audit.get('cursor')) is int and audit['cursor'] > 0
        and all(type(audit.get(k)) in (int, float) and math.isfinite(audit[k])
                and audit[k] >= 0 for k in ('resumed_at', 'next_due')),
        'recovery_audit_invalid',
    )
    if release_mode == 'public':
        _require(audit.get('release_mode') == 'public'
                 and audit.get('release_status') == 'public'
                 and audit.get('caption_uploaded') is True
                 and audit.get('continue_immediately', False) is continue_immediately
                 and type(audit.get('contains_synthetic_media')) is bool,
                 'recovery_audit_invalid')
    if public_recovery:
        _require(audit.get('publication_proof') == 'blocked_public_recovery'
                 and audit.get('continue_immediately', False) is continue_immediately
                 and re.fullmatch(r'[0-9a-f]{64}', str(audit.get('public_recovery_receipt_sha256') or ''))
                 and audit.get('thumbnail_uploaded') is True,
                 'recovery_audit_invalid')
    # An idempotent response describes the earlier transition, not eligibility
    # now: a later production job may already have reserved the next topic.
    return {**audit, 'status': status}


def resume_after_private_retry(
    channel_id: str,
    original_task_id: str,
    recovered_task_id: str,
    expected_profile_revision: str,
    *,
    now: float | None = None,
) -> dict:
    """Clear one failed-render pause after a proven private retry delivery.

    The caller supplies the original scheduled job, the successful descendant,
    and the current profile revision explicitly. At most sixteen retry edges
    are accepted. The next topic is delayed by at least one current interval
    from the first successful call. Repeated calls never shift that due time.
    """
    return _resume_after_retry(channel_id, original_task_id, recovered_task_id,
                               expected_profile_revision, now=now, release_mode='private')


def resume_after_public_retry(
    channel_id: str,
    original_task_id: str,
    recovered_task_id: str,
    expected_profile_revision: str,
    *,
    now: float | None = None,
    continue_immediately: bool = False,
) -> dict:
    """Clear a failed-render pause only after its claimed retry is public.

    This is an explicit server-side reconciliation, not an automatic retry or
    publication action. The current public profile and original frozen profile
    must be identical. All caption/disclosure/required-thumbnail proofs must
    already exist. Separate public audits cannot satisfy private recovery.
    Explicit immediate continuation changes only next_due, once; the default
    retains the existing interval and neither choice can rewrite a prior audit.
    """
    return _resume_after_retry(channel_id, original_task_id, recovered_task_id,
                               expected_profile_revision, now=now, release_mode='public',
                               continue_immediately=continue_immediately)


def resume_after_blocked_public_retry(
    channel_id: str, original_task_id: str, recovered_task_id: str,
    expected_profile_revision: str, *, now: float | None = None, continue_immediately: bool = False,
) -> dict:
    """Resume only from a separately verified, already-public recovery receipt.

    Historical blocked source attribution, publisher and upload records remain
    unchanged. This never performs a release or turns their errors into passes.
    Explicit immediate continuation changes only the next due time; it never
    resets a consumed topic or overrides a prior resume audit's timing choice.
    """
    return _resume_after_retry(channel_id, original_task_id, recovered_task_id,
                               expected_profile_revision, now=now, release_mode='public',
                               public_recovery=True, continue_immediately=continue_immediately)


def _public_recovery_proof(client, snapshots, records, credential):
    from app.services.blocked_public_recovery import RECOVERY_PREFIX
    from app.services.blocked_public_release import PUBLIC_RECOVERY_PREFIX, validate_public_recovery_receipt
    from app.services.youtube_auth import AUTH_EPOCH_KEY

    source_id = records['source']['task_id']
    receipt = _json_snapshot(client, PUBLIC_RECOVERY_PREFIX + source_id, snapshots)
    assets = _json_snapshot(client, RECOVERY_PREFIX + source_id, snapshots)
    epoch = client.get(AUTH_EPOCH_KEY)
    snapshots.append((AUTH_EPOCH_KEY, {'kind': 'none' if epoch is None else 'string', 'value': epoch}))
    proof = validate_public_recovery_receipt(records, assets, receipt,
                                            credential_cipher=credential, authorization_epoch=epoch)
    _require(isinstance(proof, dict)
             and proof.get('source_task_id') == source_id
             and proof.get('publish_task_id') == records['publisher'].get('task_id')
             and proof.get('youtube_video_id') == records['ledger'].get('youtube_video_id')
             and proof.get('target_channel_id') == records['profile'].get('channel_id')
             and proof.get('connection_id') == records['channel'].get('connection_id')
             and proof.get('profile_revision') == records['profile'].get('profile_revision')
             and proof.get('privacy_status') == proof.get('release_status') == 'public'
             and proof.get('caption_uploaded') is True and proof.get('thumbnail_uploaded') is True
             and proof.get('contains_synthetic_media') is True
             and re.fullmatch(r'[0-9a-f]{64}', str(proof.get('receipt_sha256') or '')),
             'recovery_public_receipt_invalid')
    return proof


def _resume_after_retry(
    channel_id: str, original_task_id: str, recovered_task_id: str,
    expected_profile_revision: str, *, now: float | None, release_mode: str,
    public_recovery: bool = False,
    continue_immediately: bool = False,
) -> dict:
    public = release_mode == 'public'
    _require(not public_recovery or public, 'recovery_mode_invalid')
    _require(type(continue_immediately) is bool and (not continue_immediately or public),
             'recovery_continuation_invalid')
    _require(isinstance(channel_id, str) and _ID.fullmatch(channel_id) is not None,
             'recovery_channel_invalid')
    _require(all(isinstance(value, str) and _TASK_ID.fullmatch(value) is not None
                 for value in (original_task_id, recovered_task_id))
             and original_task_id != recovered_task_id, 'recovery_task_invalid')
    _require(isinstance(expected_profile_revision, str) and bool(expected_profile_revision)
             and len(expected_profile_revision) <= 128, 'recovery_revision_invalid')
    now = time.time() if now is None else now
    _require(type(now) in (int, float) and math.isfinite(now) and now >= 0,
             'recovery_time_invalid')
    try:
        client = _redis()
        prefix = PUBLIC_RECOVERY_RESUME_PREFIX if public_recovery else PUBLIC_RESUME_PREFIX if public else RESUME_PREFIX
        audit_key = prefix + channel_id + ':' + original_task_id
        prior = client.get(audit_key)
        if prior is not None:
            return _audit_result(prior, 'already_resumed', channel_id, original_task_id,
                                 recovered_task_id, expected_profile_revision, release_mode, public_recovery, continue_immediately)
        snapshots = []
        profile = _json_snapshot(client, PROFILE_PREFIX + channel_id, snapshots)
        state = _hash_snapshot(client, CHANNEL_STATE_PREFIX + channel_id, snapshots)
        connection = _json_snapshot(client, OAUTH_CHANNEL_PREFIX + channel_id, snapshots)
        if public:
            # Public recovery also binds the actual encrypted credential bytes,
            # not just existence, through the final atomic compare.
            credential_key = OAUTH_CREDENTIAL_PREFIX + channel_id
            credential = client.get(credential_key)
            _require(isinstance(credential, str) and bool(credential), 'recovery_connection_missing')
            snapshots.append((credential_key, {'kind': 'string', 'value': credential}))
        _require(
            profile.get('channel_id') == channel_id
            and profile.get('profile_revision') == expected_profile_revision
            and profile.get('production_enabled') is True
            and profile.get('auto_publish') is True and profile.get('release_mode') == release_mode,
            'recovery_profile_ineligible',
        )
        interval = profile.get('production_interval_hours')
        topics = profile.get('production_topics')
        _require(type(interval) is int and 6 <= interval <= 168
                 and isinstance(topics, list) and 1 <= len(topics) <= 60
                 and all(isinstance(topic, str) and topic.strip() and len(topic) <= 240
                         for topic in topics), 'recovery_profile_invalid')
        topics = [topic.strip() for topic in topics]
        _require(
            state.get('paused_reason') == 'previous_render_failed'
            and state.get('last_task_id') == original_task_id
            and state.get('last_result') == 'FAILURE'
            and state.get('dispatch_status') == 'finished'
            and not state.get('active_task_id')
            and state.get('profile_revision') == expected_profile_revision,
            'recovery_schedule_ineligible',
        )
        cursor = int(state.get('cursor', '-1'))
        next_due = float(state.get('next_due', 'nan'))
        _require(str(cursor) == state.get('cursor') and 1 <= cursor <= len(topics)
                 and math.isfinite(next_due) and next_due >= 0
                 and state.get('consumed_prefix') == _prefix_digest(topics[:cursor]),
                 'recovery_cursor_invalid')
        connection_id = connection.get('connection_id')
        _require(connection.get('id') == channel_id and isinstance(connection_id, str)
                 and _ID.fullmatch(connection_id) is not None
                 and state.get('connection_id') == connection_id, 'recovery_connection_changed')

        # Walk only durable, reciprocal retry claims, never arbitrary parent IDs.
        chain = []
        seen = set()
        task_id = recovered_task_id
        while True:
            _require(task_id not in seen and _TASK_ID.fullmatch(task_id) is not None,
                     'recovery_lineage_invalid')
            seen.add(task_id)
            job = _json_snapshot(client, JOB_PREFIX + task_id, snapshots)
            if public_recovery:
                _require('publication_hold' not in job and 'owner_cancellation' not in job,
                         'recovery_owner_hold')
                for fence in (RENDER_CANCELLATION_PREFIX, HOLD_PREFIX):
                    raw_fence = client.get(fence + task_id)
                    snapshots.append((fence + task_id, {
                        'kind': 'none' if raw_fence is None else 'string', 'value': raw_fence,
                    }))
                    _require(raw_fence is None, 'recovery_owner_hold')
            _require(job.get('task_id') == task_id and job.get('kind') == 'render'
                     and isinstance(job.get('spec'), dict), 'recovery_lineage_invalid')
            _require(job.get('state') == ('SUCCESS' if not chain else 'FAILURE'),
                     'recovery_lineage_not_complete')
            if not chain:
                _require(not job.get('retry_child_task_id'), 'recovery_lineage_ambiguous')
            else:
                child = chain[-1]
                dispatch = _hash_snapshot(client, RETRY_DISPATCH_PREFIX + task_id, snapshots)
                claim = _hash_snapshot(client, RETRY_CHILD_CLAIM_PREFIX + child['task_id'], snapshots)
                _require(
                    job.get('retry_child_task_id') == child['task_id']
                    and job.get('retry_claimed') is True
                    and dispatch.get('child_task_id') == child['task_id']
                    and dispatch.get('state') in {'reserved', 'dispatched', 'uncertain'}
                    and job.get('retry_dispatch_state') == dispatch.get('state')
                    and claim.get('source_task_id') == task_id
                    and bool(dispatch.get('token')) and claim.get('token') == dispatch['token'],
                    'recovery_lineage_invalid',
                )
                if public:
                    execution_key = RETRY_CHILD_EXECUTION_PREFIX + child['task_id']
                    execution = client.get(execution_key)
                    snapshots.append((execution_key, {
                        'kind': 'none' if execution is None else 'string', 'value': execution,
                    }))
                    _require(execution == dispatch['token'] and dispatch.get('mode') in {'full', 'repair'},
                             'recovery_lineage_invalid')
                    if dispatch['mode'] == 'repair':
                        repair_key = REPAIR_CHECKPOINT_CLAIM_PREFIX + task_id
                        repair_token = client.get(repair_key)
                        snapshots.append((repair_key, {
                            'kind': 'none' if repair_token is None else 'string', 'value': repair_token,
                        }))
                        _require(job.get('repair_claimed') is True and repair_token == dispatch['token'],
                                 'recovery_lineage_invalid')
            chain.append(job)
            if task_id == original_task_id:
                _require(not job.get('parent_id'), 'recovery_original_not_scheduled')
                break
            _require(len(chain) <= MAX_RETRY_HOPS, 'recovery_lineage_too_deep')
            parent_id = job.get('parent_id')
            _require(isinstance(parent_id, str) and _TASK_ID.fullmatch(parent_id) is not None,
                     'recovery_lineage_invalid')
            task_id = parent_id

        original = chain[-1]['spec']
        identity = str(profile.get('channel_identity') or '').strip()[:240]
        brief = topics[cursor - 1] + (f'\n\nChannel editorial direction: {identity}' if identity else '')
        _require(
            original.get('topic') == brief and original.get('mode') == 'production'
            and original.get('format') == 'shorts' and original.get('duration_minutes') == 0.5
            and original.get('production_scheduled') is True
            and original.get('publish_after_render') is True
            and original.get('production_channel_id') == channel_id
            and original.get('production_connection_id') == connection_id
            and original.get('production_profile_revision') == expected_profile_revision
            and type(original.get('production_topic_index')) is int
            and original['production_topic_index'] == cursor - 1
            and original.get('language') == profile.get('default_language')
            and original.get('channel_id') == str(profile.get('route_label') or channel_id).strip(),
            'recovery_spec_changed',
        )
        stable = json.dumps({key: original.get(key) for key in _STABLE_SPEC}, sort_keys=True)
        for job in chain:
            _require(json.dumps({key: job['spec'].get(key) for key in _STABLE_SPEC}, sort_keys=True)
                     == stable, 'recovery_spec_changed')
        if public:
            # Only server-created transport metadata may differ between retries;
            # preserve editorial decisions and any future frozen render options.
            def frozen_spec(spec):
                return json.dumps({key: value for key, value in spec.items()
                                   if key not in {'workflow', 'repair_source_task_id'}}, sort_keys=True)
            expected_spec = frozen_spec(original)
            _require(all(frozen_spec(job['spec']) == expected_spec for job in chain),
                     'recovery_spec_changed')
        result = chain[0].get('result')
        _require(isinstance(result, dict) and bool(result.get('video_key'))
                 and result.get('quality_disposition') == 'automated_qc_pass'
                 and result.get('manual_qa_required') is False, 'recovery_qa_not_approved')
        if public:
            _require(result.get('task_id') == recovered_task_id and result.get('status') == 'complete'
                     and result.get('video_key') == f'videos/{recovered_task_id}/final.mp4'
                     and result.get('caption_key') == f'videos/{recovered_task_id}/captions.{original["language"]}.srt',
                     'recovery_qa_not_approved')
        automation = result.get('youtube_automation')
        _require(isinstance(automation, dict)
                 and automation.get('status') in {'queued', 'reserved', 'uploading', 'complete'},
                 'recovery_publication_not_complete')
        publish_id = automation.get('publish_task_id')
        _require(isinstance(publish_id, str) and _TASK_ID.fullmatch(publish_id) is not None
                 and publish_id not in seen, 'recovery_publisher_invalid')
        publisher = _json_snapshot(client, JOB_PREFIX + publish_id, snapshots)
        delivered = publisher.get('result')
        publish_spec = publisher.get('spec')
        upload = _json_snapshot(client, UPLOAD_PREFIX + recovered_task_id, snapshots)
        recovered_proof = None
        if public_recovery:
            recovered_proof = _public_recovery_proof(client, snapshots, {
                'source': chain[0], 'publisher': publisher, 'ledger': upload,
                'profile': profile, 'channel': connection,
            }, credential)
        _require(
            publisher.get('task_id') == publish_id and publisher.get('kind') == 'publish'
            and publisher.get('state') == 'SUCCESS' and publisher.get('parent_id') == recovered_task_id
            and isinstance(publish_spec, dict) and publish_spec.get('source_task_id') == recovered_task_id
            and isinstance(delivered, dict) and delivered.get('source_task_id') == recovered_task_id
            and delivered.get('status') == 'complete'
            and (recovered_proof is not None or (_public(delivered) if public else _private(delivered))),
            'recovery_publication_not_complete',
        )
        video_id = delivered.get('youtube_video_id')
        _require(isinstance(video_id, str) and _VIDEO_ID.fullmatch(video_id) is not None,
                 'recovery_video_invalid')
        attribution = result.get('youtube')
        _require(isinstance(attribution, dict)
                 and (recovered_proof is not None or (_public(attribution) if public else _private(attribution)))
                 and attribution.get('video_id') == video_id, 'recovery_attribution_invalid')
        plan = upload.get('publish_plan')
        _require(
            upload.get('source_task_id') == recovered_task_id and upload.get('publish_task_id') == publish_id
            and upload.get('status') == 'complete' and upload.get('youtube_video_id') == video_id
            and upload.get('requested_release_mode') == release_mode
            and upload.get('side_effect_possible') is True
            and (recovered_proof is not None or (
                upload.get('release_status') == release_mode
                and upload.get('release_side_effect_possible') is public and not upload.get('release_error_code')))
            and not upload.get('requested_publish_at')
            and isinstance(plan, dict) and plan.get('source_task_id') == recovered_task_id
            and plan.get('release_mode') == release_mode and not plan.get('publish_at')
            and plan.get('profile_revision') == expected_profile_revision
            and publish_spec.get('release_mode') == release_mode
            and publish_spec.get('privacy_status') == 'private',
            'recovery_upload_not_public' if public else 'recovery_upload_not_private',
        )
        compact_public_replay = public and recovered_proof is None and delivered.get('idempotent_replay') is True
        if compact_public_replay:
            _require(_completed_public_replay(delivered, attribution, plan,
                                              channel_id, connection_id, expected_profile_revision),
                     'recovery_publication_binding_changed')
        for record in (publish_spec, delivered, attribution):
            _require(record.get('target_channel_id') == channel_id
                     and record.get('connection_id') == connection_id
                     and (record.get('profile_revision') == expected_profile_revision
                          or compact_public_replay and record is delivered and 'profile_revision' not in record),
                     'recovery_publication_binding_changed')
        _require(upload.get('target_channel_id') == channel_id
                 and upload.get('connection_id') == connection_id
                 and plan.get('target_channel_id') == channel_id, 'recovery_publication_binding_changed')
        if public:
            _require(upload.get('version') == 2
                     and (recovered_proof is not None or (_public(upload) and bool(upload.get('release_completed_at'))))
                     and delivered.get('task_id') == publish_id
                     and automation.get('target_channel_id') == channel_id
                     and automation.get('profile_revision') == expected_profile_revision
                     and automation.get('release_mode') == 'public',
                     'recovery_publication_binding_changed')
            planned_disclosure = plan.get('contains_synthetic_media')
            disclosure = attribution.get('contains_synthetic_media')
            # The publisher may strengthen a frozen False when render provenance
            # is positive or unknown (including empty lists round-tripped by
            # Redis Lua). Require the actual delivery proof, never downgrade the
            # frozen plan or trust an omitted/unknown disclosure flag.
            _require(type(planned_disclosure) is bool and type(disclosure) is bool
                     and (not planned_disclosure or disclosure is True)
                     and (not contains_synthetic_media(chain[0]) or disclosure is True),
                     'recovery_disclosure_unverified')
            # A compact replay never supplies missing proof: the full source
            # and ledger stay mandatory in this same atomic snapshot set.
            asset_records = ((recovered_proof,) if recovered_proof is not None
                             else (attribution,) if compact_public_replay else (delivered, attribution))
            for record in asset_records:
                _require(record.get('contains_synthetic_media') is disclosure,
                         'recovery_disclosure_unverified')
                _require(record.get('caption_uploaded') is True
                         and not record.get('caption_error_code')
                         and not record.get('thumbnail_error_code'), 'recovery_assets_unverified')
                if plan.get('require_thumbnail') is True or profile.get('require_thumbnail') is True:
                    _require(record.get('thumbnail_uploaded') is True, 'recovery_assets_unverified')
        for ancestor in chain[1:]:
            prior_upload = _json_snapshot(client, UPLOAD_PREFIX + ancestor['task_id'], snapshots, optional=True)
            _require(prior_upload is None or (
                prior_upload.get('source_task_id') == ancestor['task_id']
                and prior_upload.get('status') == 'failed_preflight'
                and prior_upload.get('side_effect_possible') is False
                and not prior_upload.get('youtube_video_id')
                and not prior_upload.get('release_side_effect_possible')
            ), 'recovery_ancestor_upload_ambiguous')

        audit = {
            'version': 1, 'channel_id': channel_id, 'original_task_id': original_task_id,
            'recovered_task_id': recovered_task_id, 'publish_task_id': publish_id,
            'youtube_video_id': video_id, 'connection_id': connection_id,
            'profile_revision': expected_profile_revision, 'cursor': cursor,
            'previous_paused_reason': state['paused_reason'], 'previous_last_result': state['last_result'],
            'resumed_at': now, 'next_due': now if continue_immediately else max(next_due, now + interval * 3600),
        }
        if public:
            audit.update(release_mode='public', release_status='public', caption_uploaded=True,
                         continue_immediately=continue_immediately,
                         contains_synthetic_media=disclosure)
        if recovered_proof is not None:
            audit.update(publication_proof='blocked_public_recovery', thumbnail_uploaded=True,
                         continue_immediately=continue_immediately,
                         public_recovery_receipt_sha256=recovered_proof['receipt_sha256'])
        keys = [audit_key, CHANNEL_STATE_PREFIX + channel_id, ACTIVE_KEY,
                OAUTH_CREDENTIAL_PREFIX + channel_id, OAUTH_CHANNEL_INDEX]
        keys.extend(key for key, _ in snapshots)
        status, raw = client.eval(
            _RESUME, len(keys), *keys,
            json.dumps([value for _, value in snapshots], ensure_ascii=False),
            json.dumps(audit, ensure_ascii=False), channel_id, str(audit['next_due']),
        )
        _require(status in {'resumed', 'already_resumed'}, 'recovery_' + str(status))
        return _audit_result(raw, status, channel_id, original_task_id, recovered_task_id,
                             expected_profile_revision, release_mode, public_recovery, continue_immediately)
    except ProductionRecoveryError:
        # Another identical caller can finish after our initial audit read but
        # before we finish validation. Its durable result wins; do not mistake
        # the newly cleared pause for a fresh failure or shift next_due again.
        try:
            prior = client.get(audit_key)
        except Exception:
            raise ProductionRecoveryError('recovery_state_unavailable') from None
        if prior is not None:
            return _audit_result(prior, 'already_resumed', channel_id, original_task_id,
                                 recovered_task_id, expected_profile_revision, release_mode, public_recovery, continue_immediately)
        raise
    except Exception:
        # Do not log raw records, claim tokens, credentials, or provider errors.
        # A lost EVAL response may already have committed: repeat this exact call.
        raise ProductionRecoveryError('recovery_state_unavailable') from None
