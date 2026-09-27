import importlib

import pytest

from app.config import settings
from app.services import channel_ids

NEW = 'UC' + 'n' * 22


@pytest.fixture
def reload_ids(monkeypatch):
    def load(**values):
        for name, value in values.items():
            monkeypatch.setattr(settings, name, value, raising=False)
        return importlib.reload(channel_ids)
    yield load
    monkeypatch.undo()
    importlib.reload(channel_ids)


def test_defaults_are_the_live_channels():
    assert channel_ids.MANAGED == (channel_ids.CAPITAL_DEFAULT, channel_ids.MARGIN_DEFAULT)


def test_new_system_manages_only_its_own_test_channel(reload_ids):
    ids = reload_ids(studio_capital_channel_id=NEW, studio_margin_channel_id='')
    assert ids.CAPITAL == NEW and ids.MARGIN == '' and ids.MANAGED == (NEW,)


@pytest.mark.parametrize('values', [
    {'studio_capital_channel_id': 'not-a-channel'},
    {'studio_capital_channel_id': ''},
    {'studio_capital_channel_id': NEW, 'studio_margin_channel_id': NEW},
])
def test_bad_channel_settings_fail_at_startup(reload_ids, values):
    with pytest.raises(ValueError):
        reload_ids(**values)
