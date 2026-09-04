import pytest

from app.services.director import _target_word_budget


def test_turkish_short_planning_budget_uses_latest_measured_natural_voice_rate():
    target, minimum, maximum = _target_word_budget(0.5, calibrated_short_words=51)
    assert (target, minimum, maximum) == (51, 48, 54)
    assert abs(target - (43 / 25.128 * 29.5)) < 1


def test_uncalibrated_and_explicit_legacy_contracts_are_unchanged():
    assert _target_word_budget(0.5) == (56, 52, 60)
    assert _target_word_budget(0.5, allow_legacy_short_lock=True) == (56, 40, 60)


@pytest.mark.parametrize('duration,words', [(1.0, 51), (0.5, 45), (0.5, 54), (0.25, 51)])
def test_calibration_cannot_be_reused_for_an_unmeasured_duration(duration, words):
    with pytest.raises(ValueError):
        _target_word_budget(duration, calibrated_short_words=words)
