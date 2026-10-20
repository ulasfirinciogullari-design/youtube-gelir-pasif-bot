from datetime import timedelta
import json

import pytest

from app.services import production_quality_holds as holds
from app.services import production_quality_hold_periods as windows
from app.services import production_credit_ledger as voice
from app.services import production_credit_periods as periods
from test_production_quality_holds import ready, commission
from test_native_story_correction import native
from test_full_video_rebuild import case, SOURCE, _all
from test_production_credit_ledger import policy as original_policy, NOW


@pytest.fixture
def policy(original_policy):
    end = periods._stamp(NOW + timedelta(hours=6))
    original_policy['valid_until'] = end
    original_policy['balance']['provider_reset_at'] = end
    return original_policy


def renew_voice(n):
    n.foundation.clock = lambda: NOW + timedelta(hours=6)
    ledger = voice.CreditLedger(n.client, foundation=n.foundation, clock=n.foundation.clock)
    before = periods.renewal_snapshot(ledger)
    policy = before['policy']
    observed = {'version': 1, 'source': 'verified_GET_v1_user',
        **{key: policy[key] for key in ('account_sha256', 'credential_sha256')},
        'observed_at': periods._stamp(n.foundation.clock()),
        'provider_reset_at': periods._stamp(NOW + timedelta(days=30)),
        'quota_credits': 131000, 'used_credits': 50, 'status': 'active',
        'max_credit_limit_extension': 0, 'can_extend_character_limit': False,
        'response_sha256': '1' * 64}
    return periods.renew(ledger, observed, expected_policy_sha256=before['policy_sha256'],
                         expected_state_sha256=before['state_sha256'])


def test_current_hold_policy_tick_changes_nothing(ready):
    commission(ready); before = _all(ready.client)
    assert windows.maintain_quality_hold_period()['status'] == 'current'
    assert _all(ready.client) == before


def test_expired_hold_window_needs_an_actual_fully_checked_credit_renewal(ready):
    n = ready; commission(n)
    n.foundation.clock = lambda: NOW + timedelta(hours=6)
    before = _all(n.client)
    assert windows.maintain_quality_hold_period()['status'] == 'unavailable'
    assert _all(n.client) == before


def test_new_funding_window_preserves_original_policy_budget_day_and_hold_receipts(ready):
    n = ready; commission(n)
    assert holds.hold_failed_episode(n.profile)['status'] == 'held_unpublished'
    renew_voice(n)
    before = _all(n.client)
    result = windows.maintain_quality_hold_period()
    assert result == {'status': 'renewed', 'valid_until': periods._stamp(NOW + timedelta(days=1))}
    after = _all(n.client)
    new = {windows.WINDOWS_KEY, windows.ANCHOR_KEY, windows.WITNESS_KEY}
    assert {key: row for key, row in after.items() if key not in new} == before
    assert all(n.client.pttl(key) == -1 for key in new)
    with n.client.pipeline() as pipe:
        assert holds._read_policy(pipe, n.foundation.clock()) == n.hold_policy
        # The old episode's receipt remains valid in its original window.
        windows.validate_time(pipe, n.hold_policy, NOW, now=n.foundation.clock())
    assert windows.maintain_quality_hold_period()['status'] == 'current'
    assert _all(n.client) == after
    record = json.loads(n.client.get(holds.HOLD_PREFIX + SOURCE))
    assert record['publish_eligible'] is False and record['retry_dispatched'] is False


def test_first_hold_after_expiry_requires_renewed_window_and_keeps_cash_closed(ready):
    n = ready; commission(n); renew_voice(n)
    with pytest.raises(ValueError, match='policy_expired'):
        holds.hold_failed_episode(n.profile)
    assert windows.maintain_quality_hold_period()['status'] == 'renewed'
    assert holds.hold_failed_episode(n.profile)['status'] == 'held_unpublished'
    assert n.foundation.snapshot()['new_cash_allowance_micro'] == 0
    assert n.foundation.snapshot()['historical_cash_micro'] is None


@pytest.mark.parametrize('key', [windows.WINDOWS_KEY, windows.ANCHOR_KEY,
    windows.WITNESS_KEY, periods.HISTORY_KEY, periods.HISTORY_ANCHOR])
def test_lost_window_or_native_history_never_recommissions_capacity(ready, key):
    n = ready; commission(n); renew_voice(n)
    assert windows.maintain_quality_hold_period()['status'] == 'renewed'
    n.client.delete(key)
    before = _all(n.client)
    assert windows.maintain_quality_hold_period()['status'] == 'unavailable'
    with pytest.raises((ValueError, RuntimeError)):
        holds.hold_failed_episode(n.profile)
    assert _all(n.client) == before


def test_expired_other_subscription_cannot_extend_hold_schedule(ready):
    n = ready; commission(n); renew_voice(n)
    n.foundation.clock = lambda: NOW + timedelta(days=1)
    before = _all(n.client)
    assert windows.maintain_quality_hold_period()['status'] == 'unavailable'
    assert _all(n.client) == before
