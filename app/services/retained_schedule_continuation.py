"""Reconcile one completed retained episode into a funded normal schedule.

The historical publication receipt remains read-only. A separate permanent
audit records the original pause and connection migration. This transaction
never reserves a topic, resets a budget, queues work or calls a provider.
"""
from __future__ import annotations

import math
import time

from app.services import production_connection_continuity as continuity
from app.services import production_spend_runtime as spending
from app.services import retained_delivery_completion as completion
from app.services import retained_delivery_dispatch as delivery
from app.services import retained_production_admission as admission
from app.services.channel_production import _prefix_digest
from app.services.production_spend import LEDGER_KEY, SpendBlocked


CONTINUATION_KEY = admission.PREFIX + ':schedule_continuation'


class RetainedContinuationError(RuntimeError):
    pass


def _require(value):
    if not value:
        raise RetainedContinuationError('retained_continuation_unverified')


def _result(audit, status):
    return {'status': status, 'child_id': audit['child_id'],
        'video_id': audit['video_id'], 'cursor': audit['cursor'],
        'next_due': audit['next_due'], 'production_dispatched': False}


def _prior(pipe, manifest, receipt):
    pipe.watch(CONTINUATION_KEY)
    if pipe.get(CONTINUATION_KEY) is None:
        return None
    audit = delivery._stored(pipe, CONTINUATION_KEY)
    expected = {'version', 'kind', 'channel_id', 'original_task_id', 'child_id',
        'manifest_sha256', 'completion_sha256', 'video_id', 'profile_revision',
        'previous_connection_id', 'connection_id', 'cursor', 'next_due',
        'previous_state_sha256', 'funding_state_sha256', 'resumed_at',
        'next_request_requires_budget_reservation'}
    _require(set(audit) == expected and type(audit['version']) is int and audit['version'] == 1
        and audit['kind'] == 'retained_schedule_continuation'
        and audit['channel_id'] == continuity.CHANNEL_ID
        and audit['original_task_id'] == continuity.ROOT_ID
        and audit['child_id'] == manifest['child_id']
        and audit['manifest_sha256'] == admission._hash(manifest)
        and audit['completion_sha256'] == admission._hash(receipt)
        and audit['video_id'] == receipt['video_id']
        and audit['profile_revision'] == manifest['pending_child']['spec']['production_profile_revision']
        and audit['previous_connection_id'] == manifest['pending_child']['spec']['production_connection_id']
        and audit['connection_id'] == manifest['pending_child']['spec']['current_delivery_connection_id']
        and audit['previous_state_sha256'] == manifest['prepared_final']['source_snapshot']['records'][
            continuity._STATE + continuity.CHANNEL_ID]['value_sha256']
        and type(audit['cursor']) is int and audit['cursor'] == 5
        and type(audit['next_due']) in (int, float) and math.isfinite(audit['next_due'])
        and audit['next_due'] >= 0 and audit['resumed_at'] == audit['next_due']
        and audit['next_request_requires_budget_reservation'] is True)
    for field in ('previous_state_sha256', 'funding_state_sha256'):
        continuity._hash(audit[field])
    return audit


def _source_and_schedule(pipe, manifest, plan_record, profile):
    channel = continuity.CHANNEL_ID
    snapshot = manifest['prepared_final']['source_snapshot']
    records = snapshot['records']
    profile_key, state_key = continuity._PROFILE + channel, continuity._STATE + channel
    _require(admission._record(pipe, profile_key) == records[profile_key]
        and admission._record(pipe, state_key) == records[state_key])
    _require(admission._object(pipe.get(profile_key)) == profile)
    state = pipe.hgetall(state_key)
    spec = manifest['pending_child']['spec']
    plan = plan_record['plan']
    topics = profile.get('production_topics')
    _require(profile.get('channel_id') == channel
        and profile.get('profile_revision') == spec['production_profile_revision'] == plan['profile_revision']
        and profile.get('production_enabled') is True and profile.get('auto_publish') is True
        and profile.get('release_mode') == 'public' and type(topics) is list
        and 5 < len(topics) <= 60
        and all(type(t) is str and t.strip() == t and 0 < len(t) <= 240 for t in topics)
        and state.get('cursor') == '5' and spec['production_topic_index'] == 4
        and state.get('consumed_prefix') == _prefix_digest(topics[:5])
        and state.get('paused_reason') == 'previous_render_failed'
        and state.get('last_task_id') == continuity.ROOT_ID and state.get('last_result') == 'FAILURE'
        and state.get('dispatch_status') == 'finished' and not state.get('active_task_id')
        and state.get('profile_revision') == profile['profile_revision']
        and state.get('connection_id') == spec['production_connection_id'])
    due = float(state.get('next_due', 'nan'))
    _require(math.isfinite(due) and due >= 0)
    counter_key = plan_record['series_keys'][0]
    pipe.watch(counter_key)
    _require(pipe.pttl(counter_key) == -1 and pipe.get(counter_key) == '5')
    # Current authorization can refresh observation metadata and encrypted
    # credentials. Account identity, consent epoch and membership cannot drift.
    keys = (continuity._CHANNEL + channel, continuity._CREDENTIAL + channel,
            continuity._AUTH_EPOCH, continuity._CHANNEL_INDEX, continuity._ACTIVE)
    pipe.watch(*keys)
    current = admission._object(pipe.get(keys[0]))
    credential = pipe.get(keys[1])
    _require(current.get('id') == channel and current.get('requires_reconnect') is not True
        and current.get('connection_id') == plan_record['connection_id'] == spec['current_delivery_connection_id']
        and type(credential) is str and bool(credential)
        and pipe.get(keys[2]) == str(plan_record['authorization_epoch'])
        and pipe.sismember(keys[3], channel) == 1 and not pipe.exists(keys[4]))
    # Keep every original source, retry claim and paid-create cap unchanged.
    # Later reconciled funding may legitimately exist, so it is checked by the
    # current budget guard instead of being compared with the old empty state.
    prefixes = (continuity._JOB, continuity._DISPATCH, continuity._CHILD_CLAIM,
        continuity._EXECUTION, continuity._REPAIR_CLAIM, continuity._PAID_CAP,
        *continuity._ABSENT_PREFIXES)
    required = {p + task for p in (continuity._JOB, continuity._PAID_CAP)
                for task in continuity.LINEAGE}
    selected = {key for key in records if any(key == p + task
        for p in prefixes for task in continuity.LINEAGE)}
    _require(required <= selected)
    for key in selected:
        expected = records[key]
        if key == continuity._JOB + continuity.LEAF_ID:
            expected = {'type': 'string', 'value_sha256': admission._hash(admission._raw(manifest['claimed_leaf']).decode())}
        elif key == continuity._DISPATCH + continuity.LEAF_ID:
            expected = {'type': 'hash', 'value_sha256': admission._hash(manifest['dispatch'])}
        _require(admission._record(pipe, key) == expected)
        if key in snapshot['permanent_record_keys']:
            _require(pipe.pttl(key) == -1)
    for task in (*continuity.LINEAGE, manifest['child_id']):
        for prefix in continuity._ABSENT_PREFIXES[:2]:
            key = prefix + task
            pipe.watch(key)
            _require(not pipe.exists(key))
    claim_key = continuity._CHILD_CLAIM + manifest['child_id']
    pipe.watch(claim_key)
    _require(pipe.pttl(claim_key) == -1 and pipe.hgetall(claim_key) == {
        'source_task_id': continuity.LEAF_ID, 'token': manifest['dispatch']['token']})
    return state


def resume_retained_schedule(profile, original_task_id, *, now=None, discovery_client=None):
    """One current-authority/funding transaction; repeated success is read-only."""
    if (type(profile) is not dict or profile.get('channel_id') != continuity.CHANNEL_ID
            or original_task_id != continuity.ROOT_ID):
        return {'status': 'not_applicable'}
    try:
        now = time.time() if now is None else now
        _require(type(now) in (int, float) and math.isfinite(now) and now >= 0)
        if discovery_client is not None and not discovery_client.exists(admission.MANIFEST_KEY):
            return {'status': 'not_applicable'}
        ledger = spending.configured_ledger(read_timeout=2)
        client = ledger.client
        with client.pipeline() as pipe:
            pipe.watch(admission.MANIFEST_KEY, completion.COMPLETION_KEY, CONTINUATION_KEY)
            if pipe.get(admission.MANIFEST_KEY) is None:
                return {'status': 'not_applicable'}
            if pipe.get(completion.COMPLETION_KEY) is None:
                return {'status': 'waiting_for_retained_publication'}
            manifest = delivery._stored(pipe, admission.MANIFEST_KEY)
            receipt = completion._read_history(pipe, manifest['child_id'], admission._hash(manifest))
            prior = _prior(pipe, manifest, receipt)
            if prior is not None:
                admission._read_ack(pipe)
                return _result(prior, 'already_resumed')
            if not spending.enforcement_enabled():
                return {'status': 'budget_blocked', 'reason_code': 'spend_enforcement_required'}
            plan = delivery._stored(pipe, completion.publication.PLAN_KEY)
            state = _source_and_schedule(pipe, manifest, plan, profile)
            from app.services.production_editorial import choose_production_editorial

            options = ({'long_duration_minutes': 8}
                       if getattr(spending.settings, 'studio_longform_delivery_enabled', False) is True else {})
            editorial = choose_production_editorial(profile['production_topics'][5],
                str(profile.get('channel_identity') or '').strip()[:240], **options)
            kind = 'long' if editorial['duration_minutes'] > 1 else 'shorts'
            pipe.watch(LEDGER_KEY)
            ledger.check_dispatch_capacity(channel_id=continuity.CHANNEL_ID, kind=kind)
            _require(pipe.pttl(LEDGER_KEY) == -1)
            funding_hash = admission._hash(pipe.hgetall(LEDGER_KEY))
            spec = manifest['pending_child']['spec']
            audit = {'version': 1, 'kind': 'retained_schedule_continuation',
                'channel_id': continuity.CHANNEL_ID, 'original_task_id': continuity.ROOT_ID,
                'child_id': manifest['child_id'], 'manifest_sha256': admission._hash(manifest),
                'completion_sha256': admission._hash(receipt), 'video_id': receipt['video_id'],
                'profile_revision': profile['profile_revision'],
                'previous_connection_id': spec['production_connection_id'],
                'connection_id': plan['connection_id'], 'cursor': 5, 'next_due': now,
                'previous_state_sha256': admission._hash(state), 'funding_state_sha256': funding_hash,
                'resumed_at': now, 'next_request_requires_budget_reservation': True}
            state_key = continuity._STATE + continuity.CHANNEL_ID
            pipe.multi()
            pipe.set(CONTINUATION_KEY, admission._raw(audit).decode(), nx=True)
            pipe.hdel(state_key, 'paused_reason')
            pipe.hset(state_key, mapping={'next_due': str(now), 'connection_id': plan['connection_id']})
            reply = pipe.execute()
            _require(type(reply) is list and len(reply) == 3 and reply[0] is True
                and type(reply[1]) is int and reply[1] == 1 and type(reply[2]) is int and reply[2] == 0)
        return _result(audit, 'resumed')
    except SpendBlocked as error:
        return {'status': 'budget_blocked', 'reason_code': str(error)}
    except Exception:
        # A lost EXEC reply may already have committed. Reconciliation can read
        # the permanent audit; it must never reconstruct or replay old work.
        return {'status': 'retained_continuation_unverified'}
