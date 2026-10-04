"""New system: broad-appeal Shorts topics and trend hints in the channel's language."""
from app.config import settings
from app.services import audience_strategy, audience_trends

CHANNEL = 'UC' + 'd' * 22


def _rows():
    return [{'term': 'kahve fiyatı', 'fresh': True, 'regions': ['TR']},
            {'term': 'coffee price', 'fresh': True, 'regions': ['US', 'BR']},
            {'term': 'café preço', 'fresh': True, 'regions': ['BR']}]


def _context(monkeypatch, language, enabled):
    monkeypatch.setattr(settings, 'studio_shorts_topic_appeal', enabled)
    monkeypatch.setattr(audience_strategy, 'read_settings', lambda _channel, client=None: {'trend_enabled': True})
    monkeypatch.setattr(audience_trends, 'snapshot', lambda client=None: {})
    monkeypatch.setattr(audience_trends, 'relevant', lambda _profile, data=None: _rows())
    return audience_trends.planning_context({'channel_id': CHANNEL, 'default_language': language})


def test_trend_hints_follow_the_channel_language(monkeypatch):
    assert [row['term'] for row in _context(monkeypatch, 'tr', True)['signals']] == ['kahve fiyatı']
    assert [row['term'] for row in _context(monkeypatch, 'en', True)['signals']] == ['coffee price']


def test_live_system_and_unmapped_languages_keep_every_region(monkeypatch):
    assert len(_context(monkeypatch, 'tr', False)['signals']) == 3
    assert len(_context(monkeypatch, 'de', True)['signals']) == 3


def test_no_matching_language_trend_means_no_hint(monkeypatch):
    monkeypatch.setattr(settings, 'studio_shorts_topic_appeal', True)
    monkeypatch.setattr(audience_strategy, 'read_settings', lambda _channel, client=None: {'trend_enabled': True})
    monkeypatch.setattr(audience_trends, 'snapshot', lambda client=None: {})
    monkeypatch.setattr(audience_trends, 'relevant', lambda _profile, data=None: _rows()[2:])
    assert audience_trends.planning_context({'channel_id': CHANNEL, 'default_language': 'tr'}) is None


def test_new_system_default_is_on():
    from app.config import Settings
    assert Settings.model_fields['studio_shorts_topic_appeal'].default is True
