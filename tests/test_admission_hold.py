"""A new scheduled video waits instead of paying for work that cannot finish."""
from datetime import datetime, timezone
from unittest.mock import Mock

import fakeredis
import pytest

from app.services import admission_hold, cost_meter, fal_video_catalog
from test_channel_production import production, _profile, _save, CHANNEL, CONNECTION

NOW = datetime(2026, 10, 2, 9, tzinfo=timezone.utc)


@pytest.fixture
def settings(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, 'studio_spend_enforcement', False)
    monkeypatch.setattr(settings, 'studio_abacus_included_production', False)
    monkeypatch.setattr(settings, 'elevenlabs_api_key', 'key')
    monkeypatch.setattr(settings, 'elevenlabs_voice_id', 'voice')
    monkeypatch.setattr(settings, 'studio_video_provider', 'fal')
    monkeypatch.setattr(settings, 'fal_key', 'fal-test-key')
    monkeypatch.setattr(settings, 'studio_hold_on_expired_video_prices', True)
    monkeypatch.setattr(settings, 'cost_daily_cap_usd', 8.0)
    monkeypatch.setattr(settings, 'cost_short_admission_reserve_usd', 1.5)
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(cost_meter, '_client', lambda: fake)
    import app.services.voice as voice
    monkeypatch.setattr(voice, '_redis', lambda: fake)
    return settings


@pytest.mark.new_system_defaults
def test_ready_system_is_not_held(settings):
    assert admission_hold.hold_reason(now=NOW) is None


@pytest.mark.new_system_defaults
@pytest.mark.parametrize('field', ['elevenlabs_api_key', 'elevenlabs_voice_id'])
def test_missing_voice_holds_before_any_script_is_paid(settings, monkeypatch, field):
    monkeypatch.setattr(settings, field, '')
    assert admission_hold.hold_reason(now=NOW) == 'voice_not_configured'
    monkeypatch.setattr(settings, 'studio_spend_enforcement', True)
    assert admission_hold.hold_reason(now=NOW) is None


@pytest.mark.new_system_defaults
def test_missing_fal_key_holds_while_fal_makes_the_clips(settings, monkeypatch):
    monkeypatch.setattr(settings, 'fal_key', ' ')
    assert admission_hold.hold_reason(now=NOW) == 'video_not_configured'
    assert 'FAL_KEY' in admission_hold.REASONS['video_not_configured']
    monkeypatch.setattr(settings, 'studio_video_provider', 'legacy')
    assert admission_hold.hold_reason(now=NOW) is None
    monkeypatch.setattr(settings, 'studio_video_provider', 'fal')
    monkeypatch.setattr(settings, 'studio_spend_enforcement', True)
    assert admission_hold.hold_reason(now=NOW) != 'video_not_configured'


@pytest.mark.new_system_defaults
def test_expired_video_prices_hold_new_videos(settings, monkeypatch):
    assert admission_hold.hold_reason(now=fal_video_catalog.VALID_UNTIL) == 'video_price_review_expired'
    monkeypatch.setattr(settings, 'studio_video_provider', 'legacy')
    assert admission_hold.hold_reason(now=fal_video_catalog.VALID_UNTIL) is None
    monkeypatch.setattr(settings, 'studio_video_provider', 'fal')
    monkeypatch.setattr(settings, 'studio_hold_on_expired_video_prices', False)
    assert admission_hold.hold_reason(now=fal_video_catalog.VALID_UNTIL) is None


@pytest.mark.new_system_defaults
def test_new_video_waits_when_the_day_has_no_room_left(settings, monkeypatch):
    cost_meter.record({'provider': 'openai', 'usd': 6.4, 'priced': True}, now=NOW)
    assert admission_hold.hold_reason(now=NOW) is None
    cost_meter.record({'provider': 'openai', 'usd': 0.2, 'priced': True}, now=NOW)
    assert admission_hold.hold_reason(now=NOW) == 'daily_cap_near'
    monkeypatch.setattr(settings, 'cost_short_admission_reserve_usd', 0)
    assert admission_hold.hold_reason(now=NOW) is None


@pytest.mark.new_system_defaults
def test_unreadable_inputs_never_hold(settings, monkeypatch):
    def broken(*_args, **_kwargs):
        raise ConnectionError('down')
    monkeypatch.setattr(cost_meter, '_client', broken)
    monkeypatch.setattr(admission_hold, '_voice_missing', broken)
    assert admission_hold.hold_reason(now=NOW) is None


def test_held_dispatch_reserves_no_topic_and_enqueues_nothing(production, monkeypatch):
    module, client = production
    profile = _profile()
    _save(module, client, profile)
    monkeypatch.setattr(admission_hold, 'hold_reason', lambda **_: 'daily_cap_near')
    before = {key: client.dump(key) for key in client.scan_iter('*')}
    assert module.reserve_due_production(profile, CONNECTION, now=1000) == {
        'status': 'admission_held', 'reason_code': 'daily_cap_near'}
    enqueue = Mock()
    result = module.dispatch_due_productions([profile], [CONNECTION], enqueue, now=1000)
    assert result['channels'] == {CHANNEL: 'admission_held'}
    enqueue.assert_not_called()
    assert {key: client.dump(key) for key in client.scan_iter('*')} == before
    monkeypatch.setattr(admission_hold, 'hold_reason', lambda **_: None)
    assert module.dispatch_due_productions([profile], [CONNECTION], enqueue, now=1000)['status'] == 'queued'


def test_only_managed_channels_produce_when_asked(production, monkeypatch):
    from types import SimpleNamespace
    from app.services import channel_ids
    module, client = production
    profile = _profile()
    _save(module, client, profile)
    monkeypatch.setattr(admission_hold, 'hold_reason', lambda **_: None)
    namespace = module.reserve_due_production.__globals__
    monkeypatch.setitem(namespace, 'settings', SimpleNamespace(
        **{**vars(namespace['settings']), 'studio_production_managed_only': True}))
    assert CHANNEL not in channel_ids.MANAGED
    assert module.reserve_due_production(profile, CONNECTION, now=1000) == {'status': 'channel_not_managed'}
    monkeypatch.setattr(channel_ids, 'MANAGED', (*channel_ids.MANAGED, CHANNEL))
    assert module.reserve_due_production(profile, CONNECTION, now=1000)['status'] == 'reserved'


def test_new_system_defaults_guard_admission():
    from app.config import Settings
    fields = Settings.model_fields
    assert fields['studio_production_managed_only'].default is True
    # Room for a full script stage (2.5) plus voice, checks and two clips.
    assert fields['cost_short_admission_reserve_usd'].default == 3.5
    assert fields['studio_hold_on_expired_video_prices'].default is True


def test_live_channels_never_get_scheduled_videos(production, monkeypatch):
    from types import SimpleNamespace
    from app.services import channel_ids
    module, client = production
    monkeypatch.setattr(admission_hold, 'hold_reason', lambda **_: None)
    namespace = module.reserve_due_production.__globals__
    monkeypatch.setitem(namespace, 'settings', SimpleNamespace(
        **{**vars(namespace['settings']), 'studio_block_live_channels': True}))
    for live in (channel_ids.CAPITAL_DEFAULT, channel_ids.MARGIN_DEFAULT):
        assert module.reserve_due_production(_profile(channel_id=live), {**CONNECTION, 'id': live},
                                             now=1000) == {'status': 'live_channel_blocked'}
    profile = _profile()
    _save(module, client, profile)
    assert module.reserve_due_production(profile, CONNECTION, now=1000)['status'] == 'reserved'


def test_cost_page_says_when_the_new_channel_is_not_set(monkeypatch):
    from app import cost_routes
    from app.config import settings
    from app.services import channel_ids
    monkeypatch.setattr(settings, 'studio_block_live_channels', True)
    monkeypatch.setattr(channel_ids, 'MANAGED', (channel_ids.CAPITAL_DEFAULT,))
    state = cost_routes.guards(now=NOW)
    assert state['channel_not_set'] is True
    assert 'STUDIO_CAPITAL_CHANNEL_ID' in cost_routes._guard_notices({'guards': state})
    monkeypatch.setattr(channel_ids, 'MANAGED', ('UC' + 'e' * 22,))
    assert 'channel_not_set' not in cost_routes.guards(now=NOW)


def test_new_system_blocks_live_channels_by_default():
    from app.config import Settings
    assert Settings.model_fields['studio_block_live_channels'].default is True
    assert Settings.model_fields['studio_series_multiple_attempts_enabled'].default is True
