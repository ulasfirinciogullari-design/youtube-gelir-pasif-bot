"""New system: English sources heading and #Shorts after the topic hashtags."""
from fakeredis import FakeRedis
import pytest

from app.config import settings
from app.services import youtube_automation as automation
from test_youtube_automation import _profile, _source


def _plan(monkeypatch, *, enabled, language='tr', fmt='shorts'):
    monkeypatch.setattr(automation, '_redis', lambda: FakeRedis())
    monkeypatch.setattr(settings, 'studio_publish_metadata_v2', enabled)
    source = _source()
    source['spec'].update(language=language, format=fmt)
    profile = _profile(languages=[language], default_language=language, hashtags=['Bilim'])
    return automation.build_publish_plan('source-task-plan', source, profile)


@pytest.mark.new_system_defaults
def test_shorts_tag_goes_last_and_english_sources_say_sources(monkeypatch):
    plan = _plan(monkeypatch, enabled=True, language='en')
    assert '\n\nSources:\nFAA passenger safety guidance' in plan['description']
    assert 'Kaynaklar:' not in plan['description']
    assert plan['description'].endswith('#Uçak #Bilim #Shorts')


@pytest.mark.new_system_defaults
def test_turkish_heading_stays(monkeypatch):
    plan = _plan(monkeypatch, enabled=True, language='tr')
    assert '\n\nKaynaklar:\nFAA passenger safety guidance' in plan['description']


def test_live_system_keeps_its_order_and_heading(monkeypatch):
    plan = _plan(monkeypatch, enabled=False, language='en')
    assert '\n\nKaynaklar:\n' in plan['description']
    assert plan['description'].endswith('#Shorts #Uçak #Bilim')


def test_new_system_default_is_on():
    from app.config import Settings
    assert Settings.model_fields['studio_publish_metadata_v2'].default is True
