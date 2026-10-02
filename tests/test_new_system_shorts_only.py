"""Without an explicit policy the new system makes only Shorts on its main channel."""
import json

import fakeredis
import pytest

from app.config import settings
from app.services import channel_cadence, channel_formats, channel_ids

pytestmark = pytest.mark.new_system_defaults
NOW = 1_790_000_000.0


def test_default_is_five_shorts_a_day_on_the_main_channel_only():
    assert settings.studio_shorts_policy_json == '' and settings.studio_default_shorts_per_day == 5
    assert channel_formats.shorts_only(channel_ids.CAPITAL)
    assert channel_formats.daily_limits(channel_ids.CAPITAL, channel_cadence.LIMITS) == {'long': 0, 'shorts': 5}
    assert not channel_formats.allows(channel_ids.CAPITAL, 'long')
    assert not channel_formats.shorts_only(channel_ids.MARGIN)
    editorial = channel_cadence.daily_editorial(channel_ids.CAPITAL, {'format': 'fallback'},
                                                client=fakeredis.FakeRedis(decode_responses=True), now=NOW)
    assert editorial['format'] == 'shorts' and editorial['duration_minutes'] == .5
    assert editorial['reason_code'] == 'owner_shorts_only'


def test_an_explicit_policy_or_zero_keeps_the_owner_choice(monkeypatch):
    monkeypatch.setattr(settings, 'studio_shorts_policy_json', json.dumps(
        {'version': 1, 'id': 'owner', 'daily_limits': {channel_ids.CAPITAL: 2}}))
    assert channel_formats.daily_limits(channel_ids.CAPITAL, channel_cadence.LIMITS)['shorts'] == 2
    assert channel_formats.policy_id(channel_ids.CAPITAL) == 'owner'
    monkeypatch.setattr(settings, 'studio_shorts_policy_json', '')
    monkeypatch.setattr(settings, 'studio_default_shorts_per_day', 0)
    assert not channel_formats.shorts_only(channel_ids.CAPITAL)
    for invalid in (6, -1, True, '5'):
        monkeypatch.setattr(settings, 'studio_default_shorts_per_day', invalid)
        assert not channel_formats.shorts_only(channel_ids.CAPITAL)
