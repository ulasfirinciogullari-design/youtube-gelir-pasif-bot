"""Real Lua polling and current terminal proofs; never call a live provider."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json

import pytest
from redis.exceptions import ConnectionError, WatchError

from app.services import production_quality_hold_revalidation as revalidation
from app.services import production_quality_holds as holds, studio_state as jobs
from app.services import production_series_promotion as promotion, production_spend_runtime as runtime
from app.services.youtube_publish_state import UPLOAD_PREFIX, EXECUTION_LOCK_PREFIX
from test_series_quality_continuation import series, ready, native, case, policy, NOW, promote
from test_production_quality_holds import seed_child
from test_full_video_rebuild import SOURCE, CHILD, TOKEN, _all, _edit, _write
from test_production_credit_ledger import InterceptClient


def legacy_poll(n, task):
    # Run the pre-fix synchronization body: the exact original Lua round trip
    # adds presentation flags and changes serialization after the hold seal.
    script = n.case.state._SYNC_REPAIR_CHECKPOINT_STATE
    script = script[script.index('local selected_staged'):]
    return n.client.eval(script, 4, jobs.JOB_PREFIX + task, jobs.REPAIR_CHECKPOINT_PREFIX + task,
        jobs.REPAIR_CHECKPOINT_CLAIM_PREFIX + task, jobs.RETRY_DISPATCH_PREFIX + task,
        NOW.isoformat(), jobs.JOB_TTL_SECONDS)


def legacy_hold(n, *, with_child=False):
    if with_child:
        seed_child(n)
    holds.hold_failed_episode(n.profile)
    for task in ([SOURCE, CHILD] if with_child else [SOURCE]):
        n.client.delete(jobs.QUALITY_HOLD_JOB_FENCE_PREFIX + task)
        legacy_poll(n, task)


@pytest.mark.parametrize('with_child', [False, True])
def test_polling_and_late_writes_preserve_new_seal_and_promotion(series, with_child):
    n = series
    if with_child:
        seed_child(n)
    holds.hold_failed_episode(n.profile)
    before = _all(n.client)
    for task in ([SOURCE, CHILD] if with_child else [SOURCE]):
        assert n.case.state.sync_repair_checkpoint_state(task) == {'repair_available': False, 'repair_claimed': False}
        n.case.state.update_job(task, message='a late snapshot', state='PROGRESS')
        n.case.state.mark_failure(task, 'a stale Celery error')
        n.case.state.save_repair_checkpoint(task, {'version':1,'source_task_id':task,'approved_package':{'new':True}})
        assert n.case.state.consume_repair_checkpoint(task) is None
        with pytest.raises(ValueError, match='dedicated delivery'):
            n.case.state.claim_retry_dispatch(task, '00000000-0000-4000-8000-000000000012',
                TOKEN, allow_repair=True)
        assert n.client.pttl(jobs.JOB_PREFIX + task) == -1
    assert _all(n.client) == before
    assert promote(n)['status'] == 'promoted'
    assert not n.case.calls


@pytest.mark.parametrize('with_child', [False, True])
def test_old_lua_mutation_requires_new_terminal_observation_and_keeps_every_old_record(series, with_child):
    n = series; legacy_hold(n, with_child=with_child)
    before = _all(n.client)
    with pytest.raises(promotion.SeriesPromotionError):
        promote(n)
    assert revalidation.revalidate_held_completion(n.profile, dry_run=True)['status'] == 'eligible_no_writes'
    assert _all(n.client) == before
    assert revalidation.revalidate_held_completion(n.profile)['status'] == 'revalidated_unpublished'
    after = _all(n.client)
    assert all(after[k] == v for k, v in before.items())
    assert json.loads(n.client.get(n.day_key)) == [SOURCE]
    assert revalidation.revalidate_held_completion(n.profile)['status'] == 'already_revalidated'
    assert _all(n.client) == after
    result = promote(n)
    archive = json.loads(n.client.get(result['archive_key']))
    assert archive['unpublished_proof']['revalidation_sha256']
    assert archive['unpublished_proof']['publish_eligible'] is False
    assert 'public_proof' not in archive and not n.case.calls


def test_ordinary_tick_revalidates_then_series_promotion_uses_the_receipt(series):
    n = series; legacy_hold(n)
    result = holds.maintain_quality_holds([n.profile])['channels'][n.profile['channel_id']]
    assert result['status'] == 'revalidated_unpublished'
    assert promote(n)['status'] == 'promoted'


def test_history_bound_archives_old_index_entry_without_deleting_held_proof(series, monkeypatch):
    n = series; holds.hold_failed_episode(n.profile)
    before = n.client.get(jobs.JOB_PREFIX + SOURCE)
    monkeypatch.setattr(n.case.state, 'MAX_INDEXED_JOBS', 1)
    new_id = '00000000-0000-4000-8000-000000000099'
    n.case.state.create_job(new_id, {'topic':'a new independent episode'})
    assert n.client.zcard(jobs.JOB_INDEX) == 1
    assert n.client.zrange(jobs.JOB_INDEX, 0, -1) == [new_id]
    assert n.client.get(jobs.JOB_PREFIX + SOURCE) == before
    assert n.client.pttl(jobs.JOB_PREFIX + SOURCE) == -1


@pytest.mark.parametrize('damage', ['result', 'active', 'upload', 'upload_execution', 'owner_hold',
    'cancelled', 'reason', 'voice', 'new_asset', 'spec', 'connection', 'profile', 'hold',
    'daily', 'history', 'policy', 'policy_expired', 'funding', 'new_child', 'receipt_exists', 'fence_exists'])
def test_reobservation_cannot_accept_another_episode_or_unresolved_publication(series, damage):
    n = series; legacy_hold(n)
    if damage == 'result': _edit(n.case, jobs.JOB_PREFIX + SOURCE, 'result', {'video_key':'private.mp4'})
    elif damage == 'active': n.client.hset(n.state_key, 'active_task_id', SOURCE)
    elif damage == 'upload': n.client.set(UPLOAD_PREFIX + SOURCE, 'unknown')
    elif damage == 'upload_execution': n.client.set(EXECUTION_LOCK_PREFIX + SOURCE, 'unknown')
    elif damage == 'owner_hold': n.client.set('youtube_studio:source_publication_hold:v1:' + SOURCE, 'held')
    elif damage == 'cancelled': n.client.set(jobs.RENDER_CANCELLATION_PREFIX + SOURCE, 'cancelled')
    elif damage == 'reason': _edit(n.case, jobs.JOB_PREFIX + SOURCE, 'error', 'changed terminal failure')
    elif damage == 'voice': _edit(n.case, jobs.JOB_PREFIX + SOURCE, 'audio_candidate_checkpoint', {'new': True})
    elif damage == 'new_asset': _edit(n.case, jobs.JOB_PREFIX + SOURCE, 'qa_workprint', {'new': True})
    elif damage == 'spec': _edit(n.case, jobs.JOB_PREFIX + SOURCE, 'spec', {**n.job['spec'], 'topic':'changed'})
    elif damage == 'connection': _edit(n.case, holds.production.OAUTH_CHANNEL_PREFIX + n.profile['channel_id'], 'connection_id','changed')
    elif damage == 'profile': n.profile['auto_publish'] = False
    elif damage == 'hold': n.client.delete(holds.HOLD_PREFIX + SOURCE)
    elif damage == 'daily': n.client.delete(n.day_key)
    elif damage == 'history': n.client.delete(holds.HISTORY_ANCHOR)
    elif damage == 'policy': n.client.delete(holds.ANCHOR_KEY)
    elif damage == 'policy_expired':
        from datetime import timedelta
        n.foundation.clock = lambda: NOW + timedelta(days=2)
    elif damage == 'funding': n.client.hdel(runtime.LEDGER_KEY, 'binding:' + SOURCE)
    elif damage == 'new_child': seed_child(n)
    elif damage == 'receipt_exists': n.client.set(revalidation.PREFIX + SOURCE, '{}')
    elif damage == 'fence_exists': n.client.set(jobs.QUALITY_HOLD_JOB_FENCE_PREFIX + SOURCE, 'changed')
    before = _all(n.client)
    with pytest.raises((ValueError, promotion.SeriesPromotionError)):
        revalidation.revalidate_held_completion(n.profile)
    assert _all(n.client) == before and not n.case.calls


@pytest.mark.parametrize('damage', ['job', 'receipt', 'fence', 'fence_ttl', 'job_ttl', 'receipt_ttl'])
def test_revalidated_current_seal_is_not_a_permanent_exception_to_future_changes(series, damage):
    n = series; legacy_hold(n); revalidation.revalidate_held_completion(n.profile)
    if damage == 'job': _edit(n.case, jobs.JOB_PREFIX + SOURCE, 'message', 'another changed snapshot')
    elif damage == 'receipt': n.client.delete(revalidation.PREFIX + SOURCE)
    elif damage == 'fence': n.client.delete(jobs.QUALITY_HOLD_JOB_FENCE_PREFIX + SOURCE)
    else:
        prefix = {'fence_ttl':jobs.QUALITY_HOLD_JOB_FENCE_PREFIX, 'job_ttl':jobs.JOB_PREFIX,
                  'receipt_ttl':revalidation.PREFIX}[damage]
        n.client.expire(prefix + SOURCE, 60)
    before = _all(n.client)
    with pytest.raises(promotion.SeriesPromotionError): promote(n)
    assert _all(n.client) == before


def test_lost_revalidation_reply_never_repeats_append_or_daily_hold(series):
    n = series; legacy_hold(n)
    def lost(number, result):
        if type(result) is list and len(result) == 3:
            raise ConnectionError('lost after commit')
        return result
    class ForwardingIntercept(InterceptClient):
        def __getattr__(self, name): return getattr(n.client, name)
    n.foundation.client = ForwardingIntercept(n.client, after=lost)
    with pytest.raises(ConnectionError): revalidation.revalidate_held_completion(n.profile)
    n.foundation.client = n.client
    before = _all(n.client)
    assert revalidation.revalidate_held_completion(n.profile)['status'] == 'already_revalidated'
    assert _all(n.client) == before and json.loads(n.client.get(n.day_key)) == [SOURCE]


def test_concurrent_revalidation_and_provider_race_cannot_duplicate_or_use_stale_funding(series, monkeypatch):
    n = series; legacy_hold(n)
    original = holds._funding
    def raced(*args):
        original(*args)
        n.client.hset(runtime.LEDGER_KEY, 'concurrent-change', 'occupied')
    monkeypatch.setattr(holds, '_funding', raced)
    with pytest.raises(WatchError): revalidation.revalidate_held_completion(n.profile)
    assert not n.client.exists(revalidation.PREFIX + SOURCE)
    monkeypatch.setattr(holds, '_funding', original)
    n.client.hdel(runtime.LEDGER_KEY, 'concurrent-change')
    def run(_):
        try: return revalidation.revalidate_held_completion(n.profile)['status']
        except (ValueError, WatchError, promotion.SeriesPromotionError): return 'changed'
    with ThreadPoolExecutor(max_workers=4) as executor:
        results=list(executor.map(run,range(8)))
    assert results.count('revalidated_unpublished') == 1
    assert json.loads(n.client.get(n.day_key)) == [SOURCE]
