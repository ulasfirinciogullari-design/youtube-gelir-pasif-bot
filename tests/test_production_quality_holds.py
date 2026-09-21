from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
import json
from types import SimpleNamespace

import pytest
from redis.exceptions import WatchError, ConnectionError

from app.services import production_quality_holds as holds, channel_production as production
from app.services import studio_state as jobs, production_included_router as included
from app.services import production_spend_runtime as runtime
from app.services.youtube_publish_state import UPLOAD_PREFIX, EXECUTION_LOCK_PREFIX
from test_native_story_correction import native
from test_full_video_rebuild import case, SOURCE, CHILD, _all, _write, _edit
from test_production_credit_ledger import policy, NOW, InterceptClient


@pytest.fixture
def ready(native):
    n = native
    job = json.loads(n.client.get(jobs.JOB_PREFIX + SOURCE))
    job['spec']['quality_threshold'] = 86
    job.update(failure_stage='audio_qc', error='included_router_response_unverified')
    _write(n.client, jobs.JOB_PREFIX + SOURCE, job)
    profile = json.loads(n.client.get(production.PROFILE_PREFIX + n.context['channel_id']))
    policy = {'version': 1, 'kind': 'unpublished_quality_holds',
        'allowed_channels': [n.context['channel_id']], 'max_holds_per_day': 3,
        'valid_from': included._stamp(NOW), 'valid_until': included._stamp(NOW + timedelta(hours=6)),
        'owner_evidence_sha256': 'e' * 64}
    return SimpleNamespace(**vars(n), job=job, profile=profile, hold_policy=policy,
        state_key=production.CHANNEL_STATE_PREFIX + n.context['channel_id'],
        day_key=holds.DAY_PREFIX + n.context['channel_id'] + ':' + NOW.strftime('%Y-%m-%d'))


def commission(n):
    assert holds.initialize(n.hold_policy) is True


def test_uncommissioned_tick_cannot_clear_a_pause_or_initialize_a_policy(ready):
    n = ready; before = _all(n.client)
    assert holds.maintain_quality_holds([n.profile])['channels'][n.profile['channel_id']]['status'] == 'disabled'
    assert _all(n.client) == before


@pytest.mark.parametrize('code,stage,reason', [
    ('story_quality_exhausted', 'director_qc', 'story_rejected'),
    ('audio_quality_exhausted', 'audio_pause_recheck', 'audio_rejected'),
    ('audio_review_unverified', 'audio_qc_retry', 'review_unverified'),
    ('visual_quality_exhausted', 'final_visual_qc_rescue', 'stock_rejected'),
    ('render_quality_exhausted', 'render', 'render_rejected'),
])
def test_terminal_worker_contract_releases_only_schedule_and_retains_exact_failure(ready, monkeypatch, code, stage, reason):
    from app.services.production_failures import content_rejection

    n = ready; commission(n)
    monkeypatch.setattr(jobs, '_client', lambda: n.client)
    job = deepcopy(n.job)
    job.update(state='PROGRESS', stage=stage, failure_stage=stage)
    _write(n.client, jobs.JOB_PREFIX + SOURCE, job)
    failure = content_rejection(RuntimeError('Previously unseen quality rejection wording.'), code)
    persisted = jobs.mark_failure(SOURCE, failure)
    assert persisted['failure_classification']['code'] == code
    # A later Celery string-only status sync cannot erase this worker evidence.
    assert jobs.mark_failure(SOURCE, str(failure))['failure_classification'] == persisted['failure_classification']
    before = _all(n.client)
    result = holds.hold_failed_episode(n.profile)
    after = _all(n.client)
    assert result['status'] == 'held_unpublished' and result['reason'] == reason
    record = json.loads(n.client.get(holds.HOLD_PREFIX + SOURCE))
    assert record['publish_eligible'] is False and record['retry_dispatched'] is False
    assert record['retained_candidates']['audio_candidate_checkpoint'] == n.job['audio_candidate_checkpoint']
    assert record['lineage'][0]['job_sha256'] == holds._sha(n.client.get(jobs.JOB_PREFIX + SOURCE))
    mutable = {n.state_key, holds.HOLD_PREFIX + SOURCE, n.day_key, holds.HISTORY_KEY,
               holds.HISTORY_ANCHOR, jobs.QUALITY_HOLD_JOB_FENCE_PREFIX + SOURCE}
    assert {k:v for k,v in after.items() if k not in mutable} == {k:v for k,v in before.items() if k not in mutable}
    expected = dict(before[n.state_key][1]); expected.pop('paused_reason'); expected['quality_hold_task_id'] = SOURCE
    assert n.client.hgetall(n.state_key) == expected and not n.case.calls
    assert holds.hold_failed_episode(n.profile)['status'] == 'not_quality_paused'
    assert _all(n.client) == after


def test_hold_preserves_budget_unknown_requests_audio_job_cadence_and_consumed_cursor(ready):
    n = ready; commission(n); before = _all(n.client)
    assert holds.hold_failed_episode(n.profile, dry_run=True)['status'] == 'eligible_no_writes'
    assert _all(n.client) == before
    result = holds.hold_failed_episode(n.profile)
    assert result['status'] == 'held_unpublished'
    after = _all(n.client)
    record = json.loads(after[holds.HOLD_PREFIX + SOURCE][1])
    assert record['publish_eligible'] is False and record['retry_dispatched'] is False
    assert record['retained_candidates']['audio_candidate_checkpoint'] == n.job['audio_candidate_checkpoint']
    assert record['spec'] == n.job['spec']
    mutable = {n.state_key, holds.HOLD_PREFIX + SOURCE, n.day_key, holds.HISTORY_KEY, holds.HISTORY_ANCHOR,
               jobs.QUALITY_HOLD_JOB_FENCE_PREFIX + SOURCE}
    fence = json.loads(n.client.get(jobs.QUALITY_HOLD_JOB_FENCE_PREFIX + SOURCE))
    assert fence['job_sha256'] == record['lineage'][0]['job_sha256']
    assert n.client.pttl(jobs.JOB_PREFIX + SOURCE) == -1
    assert {k: v for k, v in after.items() if k not in mutable} == {k: v for k, v in before.items() if k not in mutable}
    state = after[n.state_key][1]
    expected = dict(before[n.state_key][1]); expected.pop('paused_reason'); expected['quality_hold_task_id'] = SOURCE
    assert state == expected
    assert state['cursor'] == '2' and state['last_result'] == 'FAILURE'
    assert n.foundation.snapshot()['historical_cash_micro'] is None
    assert len(json.loads(n.client.get(included.JOURNAL_KEY))['requests']) == 1
    assert holds.hold_failed_episode(n.profile)['status'] == 'not_quality_paused'
    assert _all(n.client) == after


@pytest.mark.parametrize('stage,error,eligible', [
    ('director_qc', 'included_factual_audit_invalid', True),
    ('audio_qc', 'included_factual_audit_invalid', False),
    ('youtube_publish', 'included_factual_audit_invalid', False),
    ('director_qc', 'included_factual_audit_invalid: unknown provider outcome', False),
    ('director_qc', 'Unknown upload or payment result', False),
])
def test_unusable_factual_review_is_held_without_approval_replay_or_cadence_reset(ready, stage, error, eligible):
    n = ready; commission(n)
    job = deepcopy(n.job)
    job.update(failure_stage=stage, error=error)
    _write(n.client, jobs.JOB_PREFIX + SOURCE, job)
    before = _all(n.client)
    result = holds.maintain_quality_holds([n.profile])['channels'][n.profile['channel_id']]
    after = _all(n.client)
    if not eligible:
        assert result['status'] == 'requires_review' and after == before
        return
    assert result['status'] == 'held_unpublished' and result['reason'] == 'story_rejected'
    record = json.loads(n.client.get(holds.HOLD_PREFIX + SOURCE))
    assert record['publish_eligible'] is False and record['retry_dispatched'] is False
    assert record['lineage'][0]['job_sha256'] == holds._sha(n.client.get(jobs.JOB_PREFIX + SOURCE))
    assert json.loads(n.client.get(n.day_key)) == [SOURCE]
    mutable = {n.state_key, holds.HOLD_PREFIX + SOURCE, n.day_key, holds.HISTORY_KEY,
               holds.HISTORY_ANCHOR, jobs.QUALITY_HOLD_JOB_FENCE_PREFIX + SOURCE}
    assert {k:v for k,v in after.items() if k not in mutable} == {k:v for k,v in before.items() if k not in mutable}
    expected = dict(before[n.state_key][1]); expected.pop('paused_reason'); expected['quality_hold_task_id'] = SOURCE
    assert n.client.hgetall(n.state_key) == expected and not n.case.calls
    assert holds.maintain_quality_holds([n.profile])['channels'][n.profile['channel_id']]['status'] == 'not_quality_paused'
    assert _all(n.client) == after


@pytest.mark.parametrize('prefix', [UPLOAD_PREFIX, EXECUTION_LOCK_PREFIX,
    'youtube_studio:blocked_public_release:v1:', 'youtube_studio:source_publication_hold:v1:',
    jobs.RENDER_CANCELLATION_PREFIX])
def test_any_publication_execution_or_owner_hold_stops_without_changes(ready, prefix):
    n = ready; commission(n); n.client.set(prefix + SOURCE, 'occupied even if unparseable')
    before = _all(n.client)
    with pytest.raises(ValueError): holds.hold_failed_episode(n.profile)
    assert _all(n.client) == before


@pytest.mark.parametrize('change', ['active', 'result', 'success', 'channel', 'credential',
    'revision', 'profile', 'cursor', 'prefix', 'binding', 'manual_pause', 'unknown_error'])
def test_unverified_changed_or_manual_states_cannot_release_production(ready, change):
    n = ready; commission(n)
    if change == 'active': n.client.set(production.ACTIVE_KEY, 'occupied')
    elif change == 'result': _edit(n.case, jobs.JOB_PREFIX + SOURCE, 'result', {'video_key': 'private.mp4'})
    elif change == 'success': _edit(n.case, jobs.JOB_PREFIX + SOURCE, 'state', 'SUCCESS')
    elif change == 'channel': _edit(n.case, runtime._CHANNEL_PREFIX + n.context['channel_id'], 'connection_id', 'different')
    elif change == 'credential': n.client.delete(production.OAUTH_CREDENTIAL_PREFIX + n.context['channel_id'])
    elif change == 'revision': _edit(n.case, production.PROFILE_PREFIX + n.context['channel_id'], 'profile_revision', 'changed')
    elif change == 'profile': n.profile['auto_publish'] = False; _write(n.client, production.PROFILE_PREFIX + n.context['channel_id'], n.profile)
    elif change == 'cursor': n.client.hset(n.state_key, 'cursor', '3')
    elif change == 'prefix': n.client.hset(n.state_key, 'consumed_prefix', 'changed')
    elif change == 'binding': n.client.hdel(runtime.LEDGER_KEY, 'binding:' + SOURCE)
    elif change == 'manual_pause': n.client.hset(n.state_key, 'paused_reason', 'owner_paused')
    elif change == 'unknown_error': _edit(n.case, jobs.JOB_PREFIX + SOURCE, 'error', 'Unknown upload or payment result')
    before = _all(n.client)
    result = holds.maintain_quality_holds([n.profile])
    assert result['channels'][n.context['channel_id']]['status'] in {'unavailable', 'not_quality_paused', 'requires_review'}
    assert _all(n.client) == before


def seed_child(n, state='FAILURE'):
    job = deepcopy(n.job)
    job.update(task_id=CHILD, parent_id=SOURCE, state=state)
    _write(n.client, jobs.JOB_PREFIX + CHILD, job)
    parent = deepcopy(n.job)
    parent.update(retry_child_task_id=CHILD, retry_claimed=True, retry_dispatch_state='dispatched')
    _write(n.client, jobs.JOB_PREFIX + SOURCE, parent)
    n.client.hset(jobs.RETRY_DISPATCH_PREFIX + SOURCE, mapping={
        'token': 'actual-executed-token', 'child_task_id': CHILD, 'state': 'dispatched'})
    n.client.hset(jobs.RETRY_CHILD_CLAIM_PREFIX + CHILD, mapping={
        'token': 'actual-executed-token', 'source_task_id': SOURCE})
    n.client.set(jobs.RETRY_CHILD_EXECUTION_PREFIX + CHILD, 'actual-executed-token')


@pytest.mark.parametrize('change', ['none', 'active_child', 'execution', 'parent', 'spec', 'child_publication'])
def test_terminal_reciprocal_retry_chain_is_checked_to_its_actual_leaf(ready, change):
    n = ready; commission(n); seed_child(n)
    if change == 'active_child': _edit(n.case, jobs.JOB_PREFIX + CHILD, 'state', 'PROGRESS')
    elif change == 'execution': n.client.delete(jobs.RETRY_CHILD_EXECUTION_PREFIX + CHILD)
    elif change == 'parent': _edit(n.case, jobs.JOB_PREFIX + CHILD, 'parent_id', None)
    elif change == 'spec': _edit(n.case, jobs.JOB_PREFIX + CHILD, 'spec', {**n.job['spec'], 'topic': 'different'})
    elif change == 'child_publication': n.client.set(UPLOAD_PREFIX + CHILD, 'pending')
    before = _all(n.client)
    if change == 'none':
        result = holds.hold_failed_episode(n.profile)
        assert result['leaf_task_id'] == CHILD
        assert n.client.get(jobs.JOB_PREFIX + CHILD) == before[jobs.JOB_PREFIX + CHILD][1]
    else:
        with pytest.raises(ValueError): holds.hold_failed_episode(n.profile)
        assert _all(n.client) == before


def test_local_paid_slot_blocked_before_cash_is_retained_while_failed_stock_is_held(ready):
    n = ready; commission(n)
    job = deepcopy(n.job)
    job.update(failure_stage='ai_scene_generation', error='spend_cash_disabled_history_unknown',
        prepaid_visual_diagnostics={'quality_threshold': 86, 'scenes': [
            {'scene_index': 0, 'score': 35, 'requires_paid_replacement': True}]})
    _write(n.client, jobs.JOB_PREFIX + SOURCE, job)
    key = n.module.PAID_CREATE_BUDGET_PREFIX + SOURCE
    n.client.hset(key, 'used', '1')
    before = n.client.hgetall(key)
    assert holds.hold_failed_episode(n.profile)['reason'] == 'stock_rejected_before_cash_submission'
    assert n.client.hgetall(key) == before


@pytest.mark.parametrize('damage', ['policy', 'anchor', 'history', 'history_anchor', 'policy_ttl'])
def test_partial_policy_or_history_loss_never_reopens_a_new_allowance(ready, damage):
    n = ready; commission(n)
    key = {'policy': holds.POLICY_KEY, 'anchor': holds.ANCHOR_KEY,
        'history': holds.HISTORY_KEY, 'history_anchor': holds.HISTORY_ANCHOR}.get(damage)
    if key: n.client.delete(key)
    else: n.client.expire(holds.POLICY_KEY, 60)
    before = _all(n.client)
    with pytest.raises(ValueError): holds.hold_failed_episode(n.profile)
    with pytest.raises(ValueError): holds.initialize(n.hold_policy)
    assert _all(n.client) == before


def test_independent_history_detects_deleted_daily_counter_and_missing_hold_receipt(ready):
    n = ready; commission(n); holds.hold_failed_episode(n.profile)
    n.client.hset(n.state_key, 'paused_reason', 'previous_render_failed')
    n.client.delete(holds.HOLD_PREFIX + SOURCE, n.day_key)
    before = _all(n.client)
    with pytest.raises(ValueError): holds.hold_failed_episode(n.profile)
    assert _all(n.client) == before


def test_daily_limit_does_not_reset_any_provider_counter(ready):
    n = ready; commission(n)
    roots = ['10000000-0000-4000-8000-00000000000' + str(i) for i in range(3)]
    value = holds._raw(roots)
    n.client.set(n.day_key, value)
    history = holds._raw({'version': 1, 'days': {n.day_key: holds._sha(value)}})
    n.client.set(holds.HISTORY_KEY, history); n.client.set(holds.HISTORY_ANCHOR, holds._sha(history))
    before = _all(n.client)
    assert holds.hold_failed_episode(n.profile) == {
        'status': 'daily_hold_limit', 'root_task_id': SOURCE,
        'profile_revision': n.profile['profile_revision'],
        'retry_after': (NOW + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()}
    assert _all(n.client) == before


@pytest.fixture
def overnight_ready(policy, request):
    # Both the original funding window and the original hold policy cover
    # midnight; this test never renews or edits commissioned accounting.
    policy['valid_until'] = '2026-09-10T12:00:00Z'
    n = request.getfixturevalue('ready')
    n.hold_policy['valid_until'] = included._stamp(NOW + timedelta(hours=20))
    return n


def test_ordinary_maintenance_reconsiders_daily_limit_after_utc_midnight(overnight_ready):
    n = overnight_ready; commission(n)
    roots = ['10000000-0000-4000-8000-00000000000' + str(i) for i in range(3)]
    value = holds._raw(roots)
    n.client.set(n.day_key, value)
    history = holds._raw({'version': 1, 'days': {n.day_key: holds._sha(value)}})
    n.client.set(holds.HISTORY_KEY, history); n.client.set(holds.HISTORY_ANCHOR, holds._sha(history))
    before = _all(n.client)
    channel = n.profile['channel_id']
    assert holds.maintain_quality_holds([n.profile])['channels'][channel]['status'] == 'daily_hold_limit'
    assert _all(n.client) == before
    tomorrow = (NOW + timedelta(days=1)).replace(hour=0, minute=1, second=0, microsecond=0)
    n.foundation.clock = lambda: tomorrow
    assert holds.maintain_quality_holds([n.profile])['channels'][channel]['status'] == 'held_unpublished'
    assert n.client.get(n.day_key) == value
    next_day_key = holds.DAY_PREFIX + channel + ':' + tomorrow.strftime('%Y-%m-%d')
    assert json.loads(n.client.get(next_day_key)) == [SOURCE]
    assert n.client.hget(n.state_key, 'next_due') == before[n.state_key][1]['next_due']
    assert n.client.hget(n.state_key, 'cursor') == before[n.state_key][1]['cursor']
    after = _all(n.client)
    assert all(after[k] == v for k, v in before.items() if k.startswith('youtube_studio:{production_spend}:'))
    assert json.loads(n.client.get(included.JOURNAL_KEY))['requests'] == json.loads(before[included.JOURNAL_KEY][1])['requests']
    assert json.loads(n.client.get(holds.HOLD_PREFIX + SOURCE))['publish_eligible'] is False


def test_expired_policy_stays_stopped_without_renewal_or_budget_writes(ready):
    n = ready; commission(n)
    n.foundation.clock = lambda: NOW + timedelta(days=1)
    before = _all(n.client)
    with pytest.raises(ValueError, match='policy_expired'):
        holds.hold_failed_episode(n.profile)
    assert _all(n.client) == before


def test_lost_commit_reply_cannot_repeat_a_hold_or_reset_daily_usage(ready):
    n = ready; commission(n)
    def lose_commit(_number, result):
        if type(result) is list and len(result) == 8:
            raise ConnectionError('Lost acknowledgement after atomic hold')
        return result
    n.foundation.client = InterceptClient(n.client, after=lose_commit)
    with pytest.raises(ConnectionError): holds.hold_failed_episode(n.profile)
    n.foundation.client = n.client
    after = _all(n.client)
    assert holds.hold_failed_episode(n.profile)['status'] == 'not_quality_paused'
    assert _all(n.client) == after and json.loads(n.client.get(n.day_key)) == [SOURCE]
    # A manually restored pause cannot replay the already consumed transition.
    n.client.hset(n.state_key, 'paused_reason', 'previous_render_failed')
    before = _all(n.client)
    assert holds.hold_failed_episode(n.profile)['status'] == 'already_held'
    assert _all(n.client) == before


def test_financial_race_aborts_before_hold_or_schedule_changes(ready, monkeypatch):
    n = ready; commission(n); original = holds._funding
    def race(*args):
        original(*args)
        n.client.hset(runtime.LEDGER_KEY, 'race', 'changed')
    monkeypatch.setattr(holds, '_funding', race)
    before_state = n.client.hgetall(n.state_key)
    with pytest.raises(WatchError): holds.hold_failed_episode(n.profile)
    assert not n.client.exists(holds.HOLD_PREFIX + SOURCE, n.day_key)
    assert n.client.hgetall(n.state_key) == before_state


def test_parallel_ticks_create_one_receipt_and_never_dispatch_a_provider(ready):
    n = ready; commission(n)
    def run(_):
        # Use the actual tick entry point: another commit can become visible
        # between watched reads, before EXEC. It safely reports unavailable
        # for that mixed snapshot without releasing another episode.
        return holds.maintain_quality_holds([n.profile])['channels'][n.profile['channel_id']]['status']
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(run, range(8)))
    assert results.count('held_unpublished') == 1
    assert set(results) <= {'held_unpublished', 'not_quality_paused', 'already_held', 'unavailable'}
    assert json.loads(n.client.get(n.day_key)) == [SOURCE]
    assert not n.case.calls
