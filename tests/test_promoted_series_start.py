from copy import deepcopy
import json

import pytest

from app.services import production_schedule_control as control
from test_channel_production import production, CHANNEL, CONNECTION
import test_production_series_promotion as promotion_fixture
from test_production_series_promotion import _run, _write, _snapshot, NOW


@pytest.fixture
def promoted(production, monkeypatch):
    original_profile = promotion_fixture._profile
    monkeypatch.setattr(promotion_fixture, '_profile',
                        lambda **kwargs: original_profile(languages=['tr'], **kwargs))
    case = promotion_fixture.case.__wrapped__(production)
    case.client.hset(case.state_key, 'next_due', str(NOW + 21600))
    case.receipt = _run(case)
    assert case.receipt['status'] == 'promoted'
    case.profile = json.loads(case.client.get(case.profile_key))
    case.state = case.client.hgetall(case.state_key)
    case.revision = case.profile['profile_revision']
    case.audit_key = control.EXPEDITE_PREFIX + CHANNEL + ':' + case.revision
    case.receipt_key = case.state['last_series_promotion']
    case.epoch_key = case.ns['EPOCH_PREFIX'] + CHANNEL
    case.history_key = case.ns['TOPIC_HISTORY_PREFIX'] + CHANNEL
    monkeypatch.setattr(control, '_redis', lambda: case.client)
    return case


def _start(case):
    return control.expedite_next_production(CHANNEL, case.revision, now=NOW + 10)


def test_real_promotion_can_advance_first_due_once_then_normal_scheduler_reserves_one_topic(promoted):
    p = promoted; before = _snapshot(p)
    result = _start(p)
    assert result['status'] == 'expedited' and result['version'] == 2 and result['cursor'] == 0
    assert result['promotion_receipt_key'] == p.receipt_key
    assert result['promotion_receipt_sha256'] == p.ns['_digest'](p.receipt)
    assert p.client.hgetall(p.state_key) == {**p.state, 'next_due': str(NOW + 10)}
    assert p.client.pttl(p.audit_key) == -1
    after = _snapshot(p)
    assert set(after) == set(before) | {p.audit_key}
    assert all(after[k] == v for k, v in before.items() if k != p.state_key)
    calls = []
    queued = p.scheduler.dispatch_due_productions([p.profile], [CONNECTION],
        lambda **kwargs: calls.append(kwargs), now=NOW + 11)
    assert queued['status'] == 'queued' and len(calls) == 1
    job = json.loads(p.client.get(p.ns['JOB_PREFIX'] + queued['task_id']))
    assert job['spec']['production_topic_index'] == 0 and job['state'] == 'PENDING'
    occupied = _snapshot(p)
    assert _start(p) == {**result, 'status': 'already_expedited'}
    assert _snapshot(p) == occupied
    assert p.scheduler.dispatch_due_productions([p.profile], [CONNECTION],
        lambda **kwargs: calls.append(kwargs), now=NOW + 12)['status'] == 'active'
    assert len(calls) == 1


@pytest.mark.parametrize('damage', ['no_receipt', 'no_archive', 'no_epoch', 'history_missing', 'history_extra',
    'receipt_digest', 'receipt_flags', 'receipt_epoch', 'profile_changed', 'profile_epoch',
    'state_epoch', 'state_revision', 'state_cursor', 'state_prefix', 'prior_task', 'prior_result',
    'owner_pause', 'active_task', 'connection', 'requires_reconnect', 'expired_receipt', 'archive_changed'])
def test_unproven_initial_or_changed_series_never_advances_or_reserves(promoted, damage):
    p = promoted
    if damage in {'no_receipt', 'no_archive', 'no_epoch', 'history_missing'}:
        p.client.delete({'no_receipt': p.receipt_key, 'no_archive': p.receipt['archive_key'],
                         'no_epoch': p.epoch_key, 'history_missing': p.history_key}[damage])
    elif damage == 'history_extra': p.client.sadd(p.history_key, 'unaccounted-topic')
    elif damage.startswith('receipt_'):
        receipt = deepcopy(p.receipt)
        field, value = {'receipt_digest': ('new_profile_sha256', '0'*64),
                        'receipt_flags': ('qa_approved', True), 'receipt_epoch': ('epoch', 2)}[damage]
        receipt[field] = value; _write(p.client, p.receipt_key, receipt)
    elif damage.startswith('profile_'):
        profile = deepcopy(p.profile)
        profile['series_name' if damage == 'profile_changed' else 'series_epoch'] = 'changed'
        _write(p.client, p.profile_key, profile)
    elif damage == 'archive_changed':
        archive = json.loads(p.client.get(p.receipt['archive_key']))
        archive['pending_batch']['series_title'] = 'changed'
        _write(p.client, p.receipt['archive_key'], archive)
    elif damage == 'expired_receipt': p.client.expire(p.receipt_key, 60)
    elif damage == 'requires_reconnect':
        _write(p.client, control.OAUTH_CHANNEL_PREFIX + CHANNEL, {**CONNECTION, 'requires_reconnect': True})
    else:
        field, value = {
            'state_epoch': ('series_epoch', '2'), 'state_revision': ('profile_revision', 'changed'),
            'state_cursor': ('cursor', '1'), 'state_prefix': ('consumed_prefix', 'changed'),
            'prior_task': ('last_task_id', p.source_id), 'prior_result': ('last_result', 'FAILURE'),
            'owner_pause': ('paused_reason', 'owner_paused'), 'active_task': ('active_task_id', p.source_id),
            'connection': ('connection_id', 'another-connection'),
        }[damage]
        p.client.hset(p.state_key, field, value)
    before = _snapshot(p)
    with pytest.raises(control.ProductionScheduleControlError): _start(p)
    assert _snapshot(p) == before and p.client.get(p.audit_key) is None


@pytest.mark.parametrize('target', ['receipt_key', 'epoch_key', 'history_key', 'archive'])
def test_promotion_evidence_race_cannot_commit_early_due(promoted, monkeypatch, target):
    p = promoted; factory = p.client.pipeline
    key = p.receipt['archive_key'] if target == 'archive' else getattr(p, target)
    def pipeline(*args, **kwargs):
        pipe = factory(*args, **kwargs); execute = pipe.execute
        def racing_execute(*args, **kwargs):
            p.client.delete(key)
            return execute(*args, **kwargs)
        pipe.execute = racing_execute
        return pipe
    monkeypatch.setattr(p.client, 'pipeline', pipeline)
    with pytest.raises(control.ProductionScheduleControlError, match='schedule_state_changed'):
        _start(p)
    assert p.client.hgetall(p.state_key) == p.state and p.client.get(p.audit_key) is None
