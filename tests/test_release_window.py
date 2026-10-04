"""New system: start the next video in the audience's waking hours, spaced from the last one."""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from app.services import admission_hold, release_window
from test_channel_production import production, _profile, _save, CHANNEL, CONNECTION  # noqa: F401


def _at(zone, hour, minute=0):
    return datetime(2026, 10, 5, hour, minute, tzinfo=ZoneInfo(zone)).timestamp()


@pytest.fixture
def on(monkeypatch):
    values = SimpleNamespace(studio_release_window=True, studio_release_min_gap_minutes=90)
    monkeypatch.setattr(release_window, 'settings', values)
    return values


@pytest.mark.parametrize('hour,expected', [(7, 'outside_audience_hours'), (8, None), (22, None),
                                           (23, 'outside_audience_hours'), (0, 'outside_audience_hours')])
def test_turkish_videos_start_between_eight_and_eleven_pm_istanbul(on, hour, expected):
    assert release_window.wait_reason('tr', {}, _at('Europe/Istanbul', hour)) == expected


def test_english_videos_follow_new_york_hours(on):
    assert release_window.wait_reason('en', {}, _at('America/New_York', 6)) == 'outside_audience_hours'
    assert release_window.wait_reason('en', {}, _at('America/New_York', 12)) is None
    assert release_window.wait_reason('de', {}, _at('America/New_York', 3)) is None


def test_next_video_waits_for_the_gap_after_the_last_public_one(on):
    now = _at('Europe/Istanbul', 12)
    state = {'last_public_continued_at': str(now - 89 * 60)}
    assert release_window.wait_reason('tr', state, now) == 'spacing_after_last_video'
    state = {'last_public_continued_at': str(now - 90 * 60)}
    assert release_window.wait_reason('tr', state, now) is None
    for broken in ('', 'nan-ish', None):
        assert release_window.wait_reason('tr', {'last_public_continued_at': broken}, now) is None
    on.studio_release_min_gap_minutes = 0
    assert release_window.wait_reason('tr', {'last_public_continued_at': str(now)}, now) is None


@pytest.mark.parametrize('value', [False, 'true', 1, None])
def test_needs_the_literal_setting(monkeypatch, value):
    monkeypatch.setattr(release_window, 'settings', SimpleNamespace(studio_release_window=value,
                                                                    studio_release_min_gap_minutes=90))
    assert release_window.wait_reason('tr', {}, _at('Europe/Istanbul', 3)) is None


def test_waiting_dispatch_reserves_nothing_then_starts(production, monkeypatch):  # noqa: F811
    module, client = production
    profile = _profile()
    _save(module, client, profile)
    monkeypatch.setattr(admission_hold, 'hold_reason', lambda **_: None)
    monkeypatch.setattr(release_window, 'wait_reason', lambda *_: 'outside_audience_hours')
    before = {key: client.dump(key) for key in client.scan_iter('*')}
    enqueue = Mock()
    result = module.dispatch_due_productions([profile], [CONNECTION], enqueue, now=1000)
    assert result['channels'] == {CHANNEL: 'release_waiting'}
    enqueue.assert_not_called()
    assert {key: client.dump(key) for key in client.scan_iter('*')} == before
    monkeypatch.setattr(release_window, 'wait_reason', lambda *_: None)
    assert module.dispatch_due_productions([profile], [CONNECTION], enqueue, now=1000)['status'] == 'queued'


def test_new_system_defaults():
    from app.config import Settings
    fields = Settings.model_fields
    assert fields['studio_release_window'].default is True
    assert fields['studio_release_min_gap_minutes'].default == 90
