"""New system: published Shorts are not translated unless the owner turns it on."""
from types import SimpleNamespace
from unittest.mock import Mock

import fakeredis
import pytest

from app.config import settings
from app.services import production_spend_runtime as runtime, shorts_experiment_stock as experiment
from app.services import studio_state, video_localization as localization, youtube_quota_recovery as quota

CHANNEL = 'UC' + 'c' * 22


def _maintain(monkeypatch, fmt):
    from app import production_tasks
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(runtime, 'configured_ledger', lambda **_: SimpleNamespace(client=fake))
    monkeypatch.setattr(quota, 'waiting', lambda **_: False)
    monkeypatch.setattr(experiment, 'pending', lambda _client: False)
    monkeypatch.setattr(localization, 'dashboard', lambda _client: [])
    monkeypatch.setattr(studio_state, 'list_jobs', lambda limit: [
        {'kind': 'render', 'state': 'SUCCESS', 'task_id': 'source-task', 'created_at': '2026-10-01T00:00:00'}])
    monkeypatch.setattr(localization, '_source', lambda _client, _id: (
        {'spec': {'format': fmt, 'language': 'tr'}},
        {'target_channel_id': CHANNEL, 'youtube_video_id': 'abcdefghijk'}))
    monkeypatch.setattr(localization.strategy, 'read_settings', lambda _channel, client: {'languages': ['en']})
    monkeypatch.setattr(localization, '_queue_record', lambda *_args: {'status': 'pending'})
    queued = Mock()
    monkeypatch.setattr(production_tasks.localize_published_video, 'apply_async', queued)
    return localization.maintain(), queued


@pytest.mark.new_system_defaults
def test_new_system_skips_shorts_but_still_translates_long_videos(monkeypatch):
    status, queued = _maintain(monkeypatch, 'shorts')
    assert status == {'status': 'idle'} and not queued.called
    status, queued = _maintain(monkeypatch, 'landscape')
    assert status['status'] == 'queued' and queued.called


def test_live_system_still_translates_shorts(monkeypatch):
    assert settings.studio_localize_shorts is True
    status, queued = _maintain(monkeypatch, 'shorts')
    assert status['status'] == 'queued' and queued.called


def test_new_system_default_is_off():
    from app.config import Settings
    assert Settings.model_fields['studio_localize_shorts'].default is False
