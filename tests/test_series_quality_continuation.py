"""Real native receipts, held media and atomic series transitions; no providers."""
from copy import deepcopy
from datetime import timedelta
import json
from types import SimpleNamespace

import pytest

from app.services import production_series_promotion as promotion
from app.services import production_next_series as planning, production_scheduler as scheduler
from app.services import production_quality_holds as holds, channel_production as production
from app.services import studio_state as jobs, production_included_router as included
from app.services.youtube_publish_state import UPLOAD_PREFIX, EXECUTION_LOCK_PREFIX
from test_production_quality_holds import ready, commission, seed_child
from test_native_story_correction import native
from test_full_video_rebuild import case, SOURCE, CHILD, _all, _write, _edit
from test_production_credit_ledger import policy, NOW


@pytest.fixture
def series(ready, monkeypatch):
    n = ready
    n.profile.update(series_id='original-series', series_name='Earlier ideas', series_total=2)
    _write(n.client, production.PROFILE_PREFIX + n.profile['channel_id'], n.profile)
    n.channel = json.loads(n.client.get(production.OAUTH_CHANNEL_PREFIX + n.profile['channel_id']))
    monkeypatch.setattr(promotion, 'redis', SimpleNamespace(Redis=SimpleNamespace(from_url=lambda *a, **kw: n.client)))
    n.attempt = 'a' * 32
    context = planning._context(n.profile, n.channel)
    n.batch = {'version': 1, 'status': 'ready', 'attempt_id': n.attempt,
        'day': NOW.date().isoformat(), 'created_at': NOW.isoformat(),
        'channel_id': n.profile['channel_id'], 'connection_id': n.channel['connection_id'],
        'profile_revision': n.profile['profile_revision'], 'context_sha256': planning._digest(context),
        'profile_sha256': planning._digest(n.profile),
        'channel_sha256': planning._digest(planning._planning_channel_identity(n.channel)),
        'provider': 'abacus_included', 'model': 'route-llm', 'language': 'tr',
        'series_title': 'Kâğıt paranın ayrıntıları', 'briefs': [{
            'brief': 'Bir banknotun seri numarası ne anlatır? https://www.bep.gov/currency/serial-numbers',
            'sources': [{'url': 'https://www.bep.gov/currency/serial-numbers',
                         'evidence': 'This primary source describes the serial numbers printed on banknotes.'}]}], **planning._FLAGS}
    n.pending_key = planning.PENDING_PREFIX + n.profile['channel_id']
    n.daily_key = planning.DAILY_PREFIX + n.profile['channel_id'] + ':' + n.batch['day']
    for key in (n.pending_key, n.daily_key): _write(n.client, key, n.batch)
    commission(n)
    return n


def promote(n):
    return promotion.promote_ready_series(n.profile['channel_id'], n.profile['profile_revision'],
        n.attempt, now=NOW.timestamp() + 1)


def retire(n):
    return promotion.retire_stale_ready_batch(n.profile['channel_id'], n.profile['profile_revision'],
        n.attempt, now=NOW.timestamp() + 1)


def reconnect(n):
    n.channel['connection_id'] = 'new-current-channel-connection'
    _write(n.client, production.OAUTH_CHANNEL_PREFIX + n.profile['channel_id'], n.channel)
    n.client.hset(n.state_key, 'connection_id', n.channel['connection_id'])


@pytest.mark.parametrize('with_child', [False, True])
def test_real_held_last_episode_rotates_without_publication_and_preserves_cadence_and_receipts(series, with_child):
    n = series
    if with_child: seed_child(n)
    due = str(NOW.timestamp() + 6 * 3600)
    n.client.hset(n.state_key, 'next_due', due)
    holds.hold_failed_episode(n.profile)
    before = _all(n.client)
    result = promote(n)
    assert result['status'] == 'promoted'
    archive = json.loads(n.client.get(result['archive_key']))
    proof = archive['unpublished_proof']
    assert 'public_proof' not in archive and proof['completion_kind'] == 'held_unpublished'
    assert proof['source_task_id'] == (CHILD if with_child else SOURCE)
    assert proof['publish_eligible'] is False and 'youtube_video_id' not in proof
    assert archive['state']['last_result'] == 'FAILURE'
    assert n.client.hget(n.state_key, 'next_due') == due
    assert n.client.hget(n.state_key, 'cursor') == '0'
    assert n.client.hget(n.state_key, 'last_public_task_id') is None
    mutable = {n.state_key, n.pending_key, production.PROFILE_PREFIX + n.profile['channel_id']}
    after = _all(n.client)
    assert all(after[k] == v for k, v in before.items() if k not in mutable)
    assert not n.case.calls and n.foundation.snapshot()['new_cash_allowance_micro'] == 0
    assert promote(n)['status'] == 'already_promoted' and _all(n.client) == after


@pytest.mark.parametrize('damage', ['receipt', 'policy', 'history', 'daily', 'job', 'child', 'upload',
    'execution', 'cancelled', 'owner_hold', 'hold_ttl', 'cursor', 'false_approval', 'reason'])
def test_held_resolution_is_rechecked_and_never_substitutes_for_uncertain_publication(series, damage):
    n = series; holds.hold_failed_episode(n.profile)
    hold_key = holds.HOLD_PREFIX + SOURCE
    if damage == 'receipt': n.client.delete(hold_key)
    elif damage == 'policy': n.client.delete(holds.ANCHOR_KEY)
    elif damage == 'history': n.client.delete(holds.HISTORY_ANCHOR)
    elif damage == 'daily': n.client.delete(n.day_key)
    elif damage == 'job': _edit(n.case, jobs.JOB_PREFIX + SOURCE, 'error', 'Changed terminal finding')
    elif damage == 'child': seed_child(n, state='PROGRESS')
    elif damage == 'upload': n.client.set(UPLOAD_PREFIX + SOURCE, 'unknown upload attempt')
    elif damage == 'execution': n.client.set(EXECUTION_LOCK_PREFIX + SOURCE, 'unknown execution')
    elif damage == 'cancelled': n.client.set(jobs.RENDER_CANCELLATION_PREFIX + SOURCE, 'owner cancellation')
    elif damage == 'owner_hold': n.client.set('youtube_studio:source_publication_hold:v1:' + SOURCE, 'owner hold')
    elif damage == 'hold_ttl': n.client.expire(hold_key, 60)
    elif damage == 'cursor': n.client.hset(n.state_key, 'cursor', '1')
    elif damage == 'false_approval': _edit(n.case, hold_key, 'publish_eligible', True)
    elif damage == 'reason': _edit(n.case, hold_key, 'reason', 'approved')
    before = _all(n.client)
    with pytest.raises(promotion.SeriesPromotionError): promote(n)
    assert _all(n.client) == before and not n.case.calls


def test_hold_history_change_during_promotion_is_detected_before_any_rotation(series, monkeypatch):
    n = series; holds.hold_failed_episode(n.profile)
    compare = promotion._Snapshot.compare
    def raced(snapshot, pipe):
        n.client.set(holds.HISTORY_ANCHOR, 'changed by another writer')
        return compare(snapshot, pipe)
    monkeypatch.setattr(promotion._Snapshot, 'compare', raced)
    before = _all(n.client)
    with pytest.raises(promotion.SeriesPromotionError): promote(n)
    after = _all(n.client)
    assert {k: v for k, v in after.items() if k != holds.HISTORY_ANCHOR} == {
        k: v for k, v in before.items() if k != holds.HISTORY_ANCHOR}


@pytest.mark.parametrize('damage', [False, True])
def test_public_final_episode_accounts_for_a_verified_earlier_hold_without_resetting_numbers(series, damage):
    from test_production_continuous_public import _public_finish
    n = series
    n.profile['production_topics'].append('How a different ordinary object changed daily shopping')
    n.profile['series_total'] = 3
    _write(n.client, production.PROFILE_PREFIX + n.profile['channel_id'], n.profile)
    holds.hold_failed_episode(n.profile)
    final = '99900000-0000-4000-8000-000000000009'
    job = {**deepcopy(n.job), 'task_id': final, 'spec': {**n.job['spec'],
        'topic': n.profile['production_topics'][-1], 'production_topic_index': 2}}
    _write(n.client, jobs.JOB_PREFIX + final, job)
    n.client.zadd(jobs.JOB_INDEX, {final: 3})
    keys = _public_finish(SimpleNamespace(JOB_PREFIX=jobs.JOB_PREFIX, PUBLICATION_UPLOAD_PREFIX=UPLOAD_PREFIX,
        PROFILE_PREFIX=production.PROFILE_PREFIX, OAUTH_CHANNEL_PREFIX=production.OAUTH_CHANNEL_PREFIX), n.client, final)
    ledger = json.loads(n.client.get(keys['ledger']))
    ledger['publish_plan']['series'] = {'id': n.profile['series_id'], 'name': n.profile['series_name'], 'number': 2, 'total': 3}
    _write(n.client, keys['ledger'], ledger)
    scope = n.profile['channel_id'] + ':' + n.profile['series_id']
    n.client.set(promotion.SERIES_COUNTER_PREFIX + scope, '2')
    n.client.set(promotion.SERIES_ASSIGNMENT_PREFIX + scope + ':' + final, '2')
    n.client.hset(n.state_key, mapping={'cursor': '3', 'consumed_prefix': production._prefix_digest(n.profile['production_topics']),
        'last_task_id': final, 'last_result': 'SUCCESS', 'last_public_task_id': final,
        'last_public_continued_at': str(NOW.timestamp())})
    n.batch.update(profile_sha256=planning._digest(n.profile),
        context_sha256=planning._digest(planning._context(n.profile, n.channel)))
    for key in (n.pending_key, n.daily_key): _write(n.client, key, n.batch)
    if damage: n.client.set(UPLOAD_PREFIX + SOURCE, 'uncertain publication of held episode')
    before = _all(n.client)
    if damage:
        with pytest.raises(promotion.SeriesPromotionError): promote(n)
        assert _all(n.client) == before
    else:
        result = promote(n)
        archive = json.loads(n.client.get(result['archive_key']))
        assert archive['public_proof']['privacy_status'] == 'public'
        assert archive['public_proof']['prior_unpublished_holds'][0]['original_task_id'] == SOURCE
        assert archive['public_proof']['series']['number'] == 2
        assert n.client.get(promotion.SERIES_COUNTER_PREFIX + scope) == '2'
        assert n.client.get(jobs.JOB_PREFIX + SOURCE) == before[jobs.JOB_PREFIX + SOURCE][1]


def test_old_ready_draft_is_archived_after_reconnect_without_losing_attempt_fences(series):
    n = series; holds.hold_failed_episode(n.profile); reconnect(n)
    before = _all(n.client)
    assert retire(n)['status'] == 'stale_ready_archived'
    archive_key = promotion.SUPERSEDED_PREFIX + n.profile['channel_id'] + ':' + n.attempt
    archive = json.loads(n.client.get(archive_key))
    assert archive['pending_batch'] == n.batch and archive['publish_eligible'] is False
    assert n.client.get(n.pending_key) is None
    after = _all(n.client)
    assert all(after[k] == v for k, v in before.items() if k != n.pending_key)
    assert scheduler._pending_status(n.client, n.profile['channel_id'], n.batch['day'])[0] == 'daily_fenced'
    assert scheduler._pending_status(n.client, n.profile['channel_id'], (NOW + timedelta(days=1)).date().isoformat())[0] == 'available'
    assert not n.case.calls


@pytest.mark.parametrize('damage', ['none', 'reserved', 'uncertain', 'daily', 'future', 'dispatch', 'manual_pause'])
def test_current_or_unverified_ready_data_does_not_release_another_preparation(series, damage):
    n = series; holds.hold_failed_episode(n.profile)
    if damage != 'none': reconnect(n)
    if damage in {'reserved', 'uncertain'}:
        for key in (n.pending_key, n.daily_key): _edit(n.case, key, 'status', damage)
    elif damage == 'daily': n.client.delete(n.daily_key)
    elif damage == 'future':
        for key in (n.pending_key, n.daily_key): _edit(n.case, key, 'created_at', (NOW + timedelta(days=3)).isoformat())
    elif damage == 'dispatch':
        _write(n.client, planning.PREPARATION_DISPATCH_PREFIX + n.profile['channel_id'] + ':' + n.batch['day'],
            {'status': 'uncertain', 'outcome': 'unknown', 'channel_id': n.profile['channel_id']})
    elif damage == 'manual_pause': n.client.hset(n.state_key, 'paused_reason', 'owner_paused')
    before = _all(n.client)
    if damage == 'none': assert retire(n)['status'] == 'ready'
    else:
        with pytest.raises(promotion.SeriesPromotionError): retire(n)
    assert _all(n.client) == before


@pytest.mark.parametrize('error', ['included_research_unconsulted_source', 'included_research_primary_source_required',
    'included_research_primary_source_unavailable'])
def test_terminal_source_failure_is_held_with_original_provider_request_still_occupied(ready, error):
    n = ready; commission(n)
    _edit(n.case, jobs.JOB_PREFIX + SOURCE, 'failure_stage', 'research')
    _edit(n.case, jobs.JOB_PREFIX + SOURCE, 'error', error)
    before = n.client.get(included.JOURNAL_KEY)
    assert holds.hold_failed_episode(n.profile)['reason'] == 'research_sources_unavailable'
    assert n.client.get(included.JOURNAL_KEY) == before and not n.case.calls
