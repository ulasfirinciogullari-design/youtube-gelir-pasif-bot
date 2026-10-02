import pytest


@pytest.fixture(autouse=True)
def _voice_configured_for_dispatch(monkeypatch, request):
    """Dispatch tests run without an ElevenLabs key; only the admission tests check it."""
    if request.node.get_closest_marker('real_voice_admission'):
        return
    from app.services import admission_hold
    monkeypatch.setattr(admission_hold, '_voice_missing', lambda: False)


def pytest_configure(config):
    config.addinivalue_line('markers', 'real_voice_admission: keep the real voice admission check')
