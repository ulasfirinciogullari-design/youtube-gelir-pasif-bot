"""Existing tests pin the live system's behaviour; new-system defaults have their own tests."""
import pytest


@pytest.fixture(autouse=True)
def _live_system_defaults(monkeypatch, request):
    if request.node.get_closest_marker('new_system_defaults'):
        return
    from app.config import settings
    from app.services import admission_hold
    # Dispatch tests run without an ElevenLabs key or voice.
    monkeypatch.setattr(admission_hold, '_voice_missing', lambda: False)
    # The live system keeps its daily long film plus Shorts mix, and its prompts.
    monkeypatch.setattr(settings, 'studio_default_shorts_per_day', 0, raising=False)
    for name in ('studio_shorts_growth_rule', 'studio_research_audience_rule',
                 'studio_research_turkish_short_budget', 'studio_publish_metadata_v2',
                 'studio_shorts_topic_appeal', 'studio_release_window'):
        monkeypatch.setattr(settings, name, False, raising=False)
    monkeypatch.setattr(settings, 'studio_localize_shorts', True, raising=False)
    # Far-future test clocks are past the reviewed Fal price list.
    monkeypatch.setattr(settings, 'studio_hold_on_expired_video_prices', False, raising=False)


def pytest_configure(config):
    config.addinivalue_line('markers', 'new_system_defaults: keep the new system defaults that '
                            'conftest otherwise resets to the live behaviour')
