"""Keep failed, unpublished episodes for review and release only their schedule.

Disabled until an explicit anchored policy is commissioned. This never retries
a provider, changes a budget, approves content or publishes a video. Normal
dispatch, cadence, source, media and publication checks still own the next job.
"""
from datetime import datetime, timezone, timedelta
import hashlib
import json
import math
import re

from app.services import production_spend_runtime as runtime
from app.services import production_included_router as included
from app.services import channel_production as production, studio_state as jobs

PREFIX = 'youtube_studio:quality_hold:v1:'
POLICY_KEY, ANCHOR_KEY = PREFIX + 'policy', PREFIX + 'anchor'
HISTORY_KEY, HISTORY_ANCHOR = PREFIX + 'history', PREFIX + 'history_anchor'
HOLD_PREFIX, DAY_PREFIX = PREFIX + 'episode:', PREFIX + 'day:'
MAX_HOLDS_PER_DAY = 3
MAX_HOPS = 16


def _require(value, code='quality_hold_unverified'):
    if not value:
        raise ValueError(code)


def _raw(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _object(value):
    _require(type(value) is str and 0 < len(value) <= 2_000_000)
    result = json.loads(value)
    _require(type(result) is dict)
    return result


def _date(value):
    _require(type(value) is str)
    return datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)


def _policy(value, now):
    _require(type(value) is dict and set(value) == {
        'version', 'kind', 'allowed_channels', 'max_holds_per_day', 'valid_from',
        'valid_until', 'owner_evidence_sha256'})
    _require(type(value['version']) is int and value['version'] == 1
        and value['kind'] == 'unpublished_quality_holds'
        and type(value['max_holds_per_day']) is int
        and 1 <= value['max_holds_per_day'] <= MAX_HOLDS_PER_DAY
        and type(value['owner_evidence_sha256']) is str
        and re.fullmatch('[0-9a-f]{64}', value['owner_evidence_sha256']))
    channels = value['allowed_channels']
    _require(type(channels) is list and 1 <= len(channels) <= 8
        and len(set(channels)) == len(channels)
        and all(type(v) is str and runtime._CHANNEL_ID.fullmatch(v) for v in channels))
    start, end = _date(value['valid_from']), _date(value['valid_until'])
    _require(start <= now < end and end - start <= timedelta(days=30), 'quality_hold_policy_expired')
    return value


def _financial_keys():
    from app.services import production_cash_disabled as cash, production_credit_ledger as voice
    from app.services import production_prepaid_audio as audio
    return [runtime.LEDGER_KEY, cash.ANCHOR_KEY, voice.STATE_KEY, voice.JOURNAL_KEY,
            included.STATE_KEY, included.JOURNAL_KEY, included.ANCHOR_KEY,
            audio.STATE_KEY, audio.JOURNAL_KEY, audio.ANCHOR_KEY]


def _funding(foundation, channel_id):
    _require(runtime.enforcement_enabled() and included.enabled())
    cash = foundation.snapshot()
    _require(cash.get('cash_spending_enabled') is False
        and cash.get('new_cash_allowance_micro') == 0
        and cash.get('historical_cash_micro') is None
        and set(cash.get('policy', {}).values()) == {0})
    included.preflight_production(channel_id, kind='shorts')


def initialize(policy):
    """Explicit commissioning only; no request or minute tick calls this."""
    foundation = runtime.configured_ledger(read_timeout=2)
    _policy(policy, foundation.clock())
    encoded = _raw(policy)
    with foundation.client.pipeline() as pipe:
        pipe.watch(POLICY_KEY, ANCHOR_KEY, HISTORY_KEY, HISTORY_ANCHOR, *_financial_keys())
        prior, anchor = pipe.get(POLICY_KEY), pipe.get(ANCHOR_KEY)
        if prior is not None or anchor is not None:
            _require(prior == encoded and anchor == _sha(encoded)
                and pipe.pttl(POLICY_KEY) == pipe.pttl(ANCHOR_KEY) == -1)
            _history(pipe)
            pipe.multi(); pipe.ping(); _require(pipe.execute() == [True])
            return False
        for channel_id in policy['allowed_channels']:
            _funding(foundation, channel_id)
        _require(not pipe.exists(HISTORY_KEY, HISTORY_ANCHOR))
        history = _raw({'version': 1, 'days': {}})
        pipe.multi(); pipe.set(POLICY_KEY, encoded, nx=True); pipe.set(ANCHOR_KEY, _sha(encoded), nx=True)
        pipe.set(HISTORY_KEY, history, nx=True); pipe.set(HISTORY_ANCHOR, _sha(history), nx=True)
        _require(pipe.execute() == [True] * 4, 'quality_hold_commission_ack_unknown')
    return True


def _read_policy(pipe, now):
    pipe.watch(POLICY_KEY, ANCHOR_KEY)
    encoded, anchor = pipe.get(POLICY_KEY), pipe.get(ANCHOR_KEY)
    if encoded is None and anchor is None:
        return None
    _require(type(encoded) is str and anchor == _sha(encoded)
        and pipe.pttl(POLICY_KEY) == pipe.pttl(ANCHOR_KEY) == -1)
    from app.services.production_quality_hold_periods import validate_time
    return validate_time(pipe, _object(encoded), now, now=now)


def _history(pipe):
    pipe.watch(HISTORY_KEY, HISTORY_ANCHOR)
    raw, anchor = pipe.get(HISTORY_KEY), pipe.get(HISTORY_ANCHOR)
    _require(type(raw) is str and anchor == _sha(raw)
        and pipe.pttl(HISTORY_KEY) == pipe.pttl(HISTORY_ANCHOR) == -1)
    history = _object(raw)
    _require(set(history) == {'version', 'days'} and type(history['version']) is int
        and history['version'] == 1 and type(history['days']) is dict and len(history['days']) <= 248)
    return history


def _reason(job):
    """Identify a terminal editorial/media failure, never a delivery failure."""
    stage, error = job.get('failure_stage'), job.get('error')
    if type(error) is not str:
        return None
    if stage == 'research' and error in {
        'included_research_unconsulted_source', 'included_research_primary_source_required',
        'included_research_primary_source_unavailable'}:
        return 'research_sources_unavailable'
    if stage == 'director_qc' and error == 'included_factual_audit_invalid':
        return 'story_rejected'
    if stage == 'director_qc' and error.startswith((
        'Source audit rejected unsupported narration', 'Short-preview stock narration',
        'Short-preview story', 'Narration word-count gate', 'Scene-count gate')):
        return 'story_rejected'
    if stage in {'research', 'director_qc', 'audio_qc', 'visual_qc', 'ai_scene', 'pre_runway_budget_rescue'} and error in {
        'included_router_response_unverified', 'included_router_previous_outcome_unknown',
        'prepaid_audio_response_unverified', 'prepaid_audio_previous_outcome_unknown'}:
        return 'review_unverified'
    if stage in {'ai_scene', 'pre_runway_budget_rescue', 'final_visual_qc'} and error.startswith((
        'Included production stock quality remains unresolved', 'Final visual quality gate rejected')):
        return 'stock_rejected'
    # The old worker reserved a local create slot before the cash guard. Keep
    # that occupied slot and every receipt. Only its actual rejected-stock
    # diagnostics qualify, not a generic payment or account failure.
    if stage == 'ai_scene_generation' and error == 'spend_cash_disabled_history_unknown':
        diagnostic = job.get('prepaid_visual_diagnostics')
        if type(diagnostic) is not dict or diagnostic.get('quality_threshold') != 86:
            return None
        rows = diagnostic.get('scenes')
        if type(rows) is not list or not 1 <= len(rows) <= 12:
            return None
        if any(type(r) is dict and r.get('requires_paid_replacement') is True
               and type(r.get('score')) in (int, float) and math.isfinite(r['score'])
               and 0 <= r['score'] < 86 for r in rows):
            return 'stock_rejected_before_cash_submission'
    return None


class _SnapshotReader:
    """Feed existing hold checks into the promotion's watched snapshot."""
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def watch(self, *keys):
        pass  # Every actual read is captured, then WATCHed and compared at commit.

    def get(self, key):
        return self.snapshot.read(key)

    def hgetall(self, key):
        return self.snapshot.read(key, 'hash')

    def pttl(self, key):
        return self.snapshot.read(key, 'pttl')

    def exists(self, *keys):
        return sum(self.get(key) is not None for key in keys)


def held_series_completion(snapshot, profile, channel, state, now):
    """Prove an unpublished final disposition; never fabricate PUBLIC delivery."""
    root = state.get('last_task_id')
    _require(state.get('quality_hold_task_id') == root and state.get('last_result') == 'FAILURE'
        and not state.get('paused_reason') and not state.get('active_task_id')
        and state.get('cursor') == str(len(profile['production_topics'])))
    return _held_episode_completion(snapshot, profile, channel, root, len(profile['production_topics']), now)


def _held_evidence(snapshot, profile, channel, root, cursor, now):
    _require(included.enabled() and runtime.enforcement_enabled())
    reader = _SnapshotReader(snapshot)
    policy = _read_policy(reader, datetime.fromtimestamp(now, timezone.utc))
    _require(policy is not None and profile['channel_id'] in policy['allowed_channels'])
    _require(type(root) is str and runtime._JOB_ID.fullmatch(root)
        and type(cursor) is int and 1 <= cursor <= len(profile['production_topics']))
    record_raw = reader.get(HOLD_PREFIX + root)
    record = _object(record_raw)
    _require(record.get('version') == 1 and record.get('status') == 'held_unpublished'
        and record.get('publish_eligible') is False and record.get('retry_dispatched') is False
        and record.get('channel_id') == profile['channel_id'] and record.get('root_task_id') == root
        and record.get('connection_id') == channel['connection_id']
        and record.get('profile_revision') == profile['profile_revision']
        and type(record.get('cursor')) is int and record['cursor'] == cursor
        and record.get('policy_sha256') == _sha(_raw(policy)))
    held_at = datetime.fromisoformat(record['held_at'])
    _require(held_at.tzinfo is not None and held_at.utcoffset().total_seconds() == 0
        and _date(policy['valid_from']) <= held_at <= datetime.fromtimestamp(now, timezone.utc))
    from app.services.production_quality_hold_periods import validate_time
    validate_time(reader, policy, held_at, now=datetime.fromtimestamp(now, timezone.utc))
    day_key = DAY_PREFIX + profile['channel_id'] + ':' + held_at.strftime('%Y-%m-%d')
    history, day_raw = _history(reader), reader.get(day_key)
    _require(type(day_raw) is str and history['days'].get(day_key) == _sha(day_raw)
        and reader.pttl(day_key) == -1 and reader.pttl(HOLD_PREFIX + root) == -1)
    day = json.loads(day_raw)
    _require(type(day) is list and 1 <= len(day) <= policy['max_holds_per_day']
        and len(day) == len(set(day)) and root in day)
    spec = snapshot.object(jobs.JOB_PREFIX + root)['spec']
    topic = profile['production_topics'][cursor - 1].strip()
    if profile.get('channel_identity'):
        topic += '\n\nChannel editorial direction: ' + profile['channel_identity'].strip()[:240]
    _require(record.get('spec') == spec and spec.get('quality_threshold') == 86
        and spec.get('production_channel_id') == profile['channel_id']
        and spec.get('production_connection_id') == channel['connection_id']
        and spec.get('production_profile_revision') == profile['profile_revision']
        and spec.get('production_topic_index') == cursor - 1 and spec.get('topic') == topic
        and spec.get('mode') == 'production' and spec.get('format') == 'shorts'
        and spec.get('duration_minutes') == .5 and spec.get('production_scheduled') is True
        and spec.get('publish_after_render') is True)
    leaf, evidence = _lineage(reader, root, spec)
    _require(record.get('leaf_task_id') == leaf['task_id']
        and record.get('reason') == _reason(leaf) and _reason(leaf) is not None)
    return record, record_raw, leaf, evidence


def _held_episode_completion(snapshot, profile, channel, root, cursor, now):
    record, record_raw, leaf, evidence = _held_evidence(snapshot, profile, channel, root, cursor, now)
    revalidated = {}
    if record.get('lineage') != evidence:
        from app.services.production_quality_hold_revalidation import verified_revalidation
        revalidated = verified_revalidation(snapshot, record, record_raw, leaf, evidence, now)
    return {'completion_kind': 'held_unpublished', 'original_task_id': root,
        'source_task_id': leaf['task_id'], 'lineage': [row['task_id'] for row in evidence],
        'hold_sha256': _sha(record_raw), 'publish_eligible': False, **revalidated}


def prior_held_completions(snapshot, profile, channel, now):
    """Explain missing publication numbers with actual prior-topic hold receipts."""
    reader = _SnapshotReader(snapshot)
    history = _history(reader)
    result = {}
    for key, digest in history['days'].items():
        if not key.startswith(DAY_PREFIX + profile['channel_id'] + ':'):
            continue
        day_raw = reader.get(key)
        _require(type(day_raw) is str and _sha(day_raw) == digest and reader.pttl(key) == -1)
        roots = json.loads(day_raw)
        _require(type(roots) is list and len(roots) <= MAX_HOLDS_PER_DAY and len(set(roots)) == len(roots))
        for root in roots:
            _require(type(root) is str and runtime._JOB_ID.fullmatch(root))
            record = _object(reader.get(HOLD_PREFIX + root))
            _require(record.get('channel_id') == profile['channel_id'])
            if record.get('profile_revision') != profile['profile_revision'] or record.get('connection_id') != channel['connection_id']:
                continue
            cursor = record.get('cursor')
            _require(type(cursor) is int and 1 <= cursor < len(profile['production_topics']) and cursor not in result)
            result[cursor] = _held_episode_completion(snapshot, profile, channel, root, cursor, now)
    return [result[index] for index in sorted(result)]


def _lineage(pipe, root, spec):
    from app.services.source_publication_hold import HOLD_PREFIX as PUBLICATION_HOLD
    from app.services.youtube_publish_state import UPLOAD_PREFIX, EXECUTION_LOCK_PREFIX
    from app.services.blocked_public_release import PUBLIC_RECOVERY_PREFIX
    current, parent, seen, evidence = root, None, set(), []
    for _ in range(MAX_HOPS):
        _require(type(current) is str and runtime._JOB_ID.fullmatch(current) and current not in seen)
        seen.add(current)
        key = jobs.JOB_PREFIX + current
        publication_keys = [prefix + current for prefix in (
            jobs.RENDER_CANCELLATION_PREFIX, PUBLICATION_HOLD, UPLOAD_PREFIX,
            EXECUTION_LOCK_PREFIX, PUBLIC_RECOVERY_PREFIX)]
        pipe.watch(key, *publication_keys)
        raw = pipe.get(key); job = _object(raw)
        _require(job.get('task_id') == current and job.get('parent_id') == parent
            and job.get('kind') == 'render' and job.get('state') == 'FAILURE'
            and job.get('spec') == spec
            and not any(job.get(k) for k in ('result', 'youtube', 'youtube_automation', 'video_key', 'cancelled'))
            and not pipe.exists(*publication_keys),
            'quality_hold_episode_not_terminal')
        if parent is not None:
            dispatch_key, claim_key, execution_key = (jobs.RETRY_DISPATCH_PREFIX + parent,
                jobs.RETRY_CHILD_CLAIM_PREFIX + current, jobs.RETRY_CHILD_EXECUTION_PREFIX + current)
            pipe.watch(dispatch_key, claim_key, execution_key)
            dispatch, claim, execution = pipe.hgetall(dispatch_key), pipe.hgetall(claim_key), pipe.get(execution_key)
            token = dispatch.get('token')
            _require(type(token) is str and 8 <= len(token) <= 128
                and dispatch.get('child_task_id') == current
                and dispatch.get('state') in {'reserved', 'dispatched', 'uncertain'}
                and claim.get('source_task_id') == parent and claim.get('token') == token
                and execution == token, 'quality_hold_retry_unverified')
        evidence.append({'task_id': current, 'job_sha256': _sha(raw)})
        child = job.get('retry_child_task_id')
        if not child:
            return job, evidence
        _require(job.get('retry_claimed') is True
            and job.get('retry_dispatch_state') in {'reserved', 'dispatched', 'uncertain'})
        parent, current = current, child
    raise ValueError('quality_hold_lineage_too_long')


def hold_failed_episode(profile, *, dry_run=False):
    """One watched transition; only a schedule and separate hold receipts change."""
    foundation = runtime.configured_ledger(read_timeout=2)
    client, now = foundation.client, foundation.clock()
    channel_id = profile.get('channel_id')
    _require(type(channel_id) is str and runtime._CHANNEL_ID.fullmatch(channel_id))
    with client.pipeline() as pipe:
        policy = _read_policy(pipe, now)
        if policy is None:
            return {'status': 'disabled'}
        _require(channel_id in policy['allowed_channels'])
        state_key, profile_key, channel_key = (production.CHANNEL_STATE_PREFIX + channel_id,
            production.PROFILE_PREFIX + channel_id, runtime._CHANNEL_PREFIX + channel_id)
        pipe.watch(state_key, profile_key, channel_key, production.ACTIVE_KEY,
                   runtime._CHANNEL_INDEX, *_financial_keys())
        state = pipe.hgetall(state_key)
        if state.get('paused_reason') != 'previous_render_failed':
            return {'status': 'not_quality_paused'}
        _require(not pipe.exists(production.ACTIVE_KEY) and not state.get('active_task_id')
            and state.get('last_result') == 'FAILURE', 'quality_hold_active')
        _require(_object(pipe.get(profile_key)) == profile and profile.get('production_enabled') is True
            and profile.get('auto_publish') is True and profile.get('release_mode') == 'public')
        channel = _object(pipe.get(channel_key))
        _require(channel.get('id') == channel_id and channel.get('requires_reconnect') is not True
            and pipe.sismember(runtime._CHANNEL_INDEX, channel_id))
        credential_key = production.OAUTH_CREDENTIAL_PREFIX + channel_id
        pipe.watch(credential_key)
        _require(pipe.exists(credential_key))
        root = state.get('last_task_id'); _require(type(root) is str and runtime._JOB_ID.fullmatch(root))
        root_key, hold_key = jobs.JOB_PREFIX + root, HOLD_PREFIX + root
        day_key = DAY_PREFIX + channel_id + ':' + now.strftime('%Y-%m-%d')
        pipe.watch(root_key, hold_key, day_key)
        if pipe.exists(hold_key):
            return {'status': 'already_held'}  # A lost acknowledgement never repeats the transition.
        _require(state.get('quality_hold_task_id') != root, 'quality_hold_receipt_missing')
        root_job = _object(pipe.get(root_key)); spec = root_job.get('spec')
        _require(type(spec) is dict and spec.get('production_channel_id') == channel_id
            and spec.get('production_connection_id') == channel.get('connection_id')
            and spec.get('production_profile_revision') == profile.get('profile_revision')
            and spec.get('production_scheduled') is True and spec.get('publish_after_render') is True
            and spec.get('mode') == 'production' and spec.get('format') == 'shorts'
            and spec.get('duration_minutes') == .5 and spec.get('quality_threshold') == 86)
        cursor = int(state.get('cursor', '-1'))
        _require(str(cursor) == state.get('cursor') and cursor > 0
            and type(spec.get('production_topic_index')) is int
            and spec['production_topic_index'] == cursor - 1)
        topics = profile.get('production_topics')
        _require(type(topics) is list and cursor <= len(topics)
            and state.get('consumed_prefix') == production._prefix_digest(topics[:cursor]))
        context = {'channel_id': channel_id, 'connection_id': channel['connection_id'],
                   'lineage_id': root, 'kind': 'shorts'}
        _require(_object(pipe.hget(runtime.LEDGER_KEY, 'binding:' + root)) == context)
        leaf, evidence = _lineage(pipe, root, spec)
        reason = _reason(leaf)
        if reason is None:
            return {'status': 'requires_review'}
        day_raw = pipe.get(day_key)
        history = _history(pipe)
        _require((day_raw is None and day_key not in history['days'])
            or day_raw is not None and history['days'].get(day_key) == _sha(day_raw),
            'quality_hold_daily_history_changed')
        day = [] if day_raw is None else json.loads(day_raw)
        _require(type(day) is list and len(day) <= MAX_HOLDS_PER_DAY
            and len(set(day)) == len(day) and all(type(v) is str and runtime._JOB_ID.fullmatch(v) for v in day)
            and root not in day and (day_raw is None or pipe.pttl(day_key) == -1))
        if len(day) >= policy['max_holds_per_day']:
            return {'status': 'daily_hold_limit', 'root_task_id': root,
                    'profile_revision': profile['profile_revision'],
                    'retry_after': (now + timedelta(days=1)).replace(
                        hour=0, minute=0, second=0, microsecond=0).isoformat()}
        due = float(state.get('next_due', 'nan'))
        _require(math.isfinite(due) and due >= 0)
        _funding(foundation, channel_id)
        record = {'version': 1, 'status': 'held_unpublished', 'channel_id': channel_id,
            'root_task_id': root, 'leaf_task_id': leaf['task_id'], 'reason': reason,
            'cursor': cursor, 'profile_revision': profile['profile_revision'],
            'connection_id': channel['connection_id'], 'lineage': evidence,
            'policy_sha256': _sha(_raw(policy)), 'held_at': now.isoformat(),
            'publish_eligible': False, 'retry_dispatched': False, 'spec': spec,
            'retained_candidates': {key: leaf[key] for key in (
                'audio_candidate_checkpoint', 'included_stock_pools', 'generated_asset_candidates',
                'repair_checkpoint', 'qa_workprint') if leaf.get(key)}}
        # Ordinary dashboard/worker writes must not change a sealed disposition.
        from app.services.production_quality_hold_revalidation import fence_record
        fences = {jobs.QUALITY_HOLD_JOB_FENCE_PREFIX + row['task_id']:
                  fence_record(root, _sha(_raw(record)), row) for row in evidence}
        pipe.watch(*fences)
        _require(all(pipe.get(key) is None for key in fences), 'quality_hold_fence_conflict')
        if dry_run:
            pipe.multi(); pipe.ping(); _require(pipe.execute() == [True])
            return {'status': 'eligible_no_writes', 'record': record}
        day_encoded = _raw([*day, root])
        history['days'][day_key] = _sha(day_encoded)
        _require(len(history['days']) <= 248)
        history_encoded = _raw(history)
        pipe.multi(); pipe.set(hold_key, _raw(record), nx=True)
        pipe.set(day_key, day_encoded)
        pipe.hdel(state_key, 'paused_reason')
        # Preserve cadence and the already-consumed cursor. A successful hold
        # is not a publication receipt and never changes last_public_task_id.
        pipe.hset(state_key, 'quality_hold_task_id', root)
        pipe.set(HISTORY_KEY, history_encoded); pipe.set(HISTORY_ANCHOR, _sha(history_encoded))
        for key, value in fences.items():
            pipe.set(key, _raw(value), nx=True)
        for row in evidence:
            pipe.persist(jobs.JOB_PREFIX + row['task_id'])
        ack = pipe.execute()
        _require(type(ack) is list and len(ack) == 6 + 2 * len(evidence)
            and ack[0] is True and ack[1] is True and ack[2] == 1 and type(ack[3]) is int
            and ack[4:6] == [True, True] and all(value is True for value in ack[6:6 + len(evidence)])
            and all(type(value) is bool for value in ack[6 + len(evidence):]), 'quality_hold_ack_unknown')
        return {'status': 'held_unpublished', 'root_task_id': root, 'leaf_task_id': leaf['task_id'], 'reason': reason}


def maintain_quality_holds(profiles):
    if not included.enabled() or not runtime.enforcement_enabled():
        return {'status': 'disabled', 'channels': {}}
    if type(profiles) is not list or len(profiles) > 8:
        return {'status': 'unavailable', 'channels': {}}
    results = {}
    for profile in profiles:
        if type(profile) is not dict or profile.get('production_enabled') is not True:
            continue
        channel_id = profile.get('channel_id')
        if type(channel_id) is not str or not runtime._CHANNEL_ID.fullmatch(channel_id) or channel_id in results:
            continue
        try:
            results[channel_id] = hold_failed_episode(profile)
            if results[channel_id]['status'] == 'not_quality_paused':
                from app.services.production_quality_hold_revalidation import revalidate_held_completion
                result = revalidate_held_completion(profile)
                if result['status'] != 'not_needed':
                    results[channel_id] = result
        except Exception:
            results[channel_id] = {'status': 'unavailable'}
    return {'status': 'checked', 'channels': results}
