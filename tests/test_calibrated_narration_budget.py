import pytest

from app.services.director import _target_word_budget


def test_reviewed_turkish_short_budget_does_not_request_the_failed_54_words():
    target, minimum, maximum = _target_word_budget(0.5, calibrated_short_words=45)
    assert (target, minimum, maximum) == (45, 42, 48)
    assert maximum < 54


def test_uncalibrated_and_explicit_legacy_contracts_are_unchanged():
    assert _target_word_budget(0.5) == (56, 52, 60)
    assert _target_word_budget(0.5, allow_legacy_short_lock=True) == (56, 40, 60)


@pytest.mark.parametrize('duration,words', [(1.0, 45), (0.5, 54), (0.25, 45)])
def test_calibration_cannot_be_reused_for_an_unmeasured_duration(duration, words):
    with pytest.raises(ValueError):
        _target_word_budget(duration, calibrated_short_words=words)
