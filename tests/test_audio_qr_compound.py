from copy import deepcopy
import pytest

from app.services import audio_qc as qc
from app.services.word_timed_narration import edit_plan
from app.services.voice import VoiceQualityError
from test_word_timed_narration import evidence


def test_spelling_difference_keeps_both_real_word_times():
    heard = evidence('Bu kare kodun köşesi. Okuyucu yönünü bulur.')
    original = deepcopy(heard)
    result = edit_plan(['Bu karekodun köşesi.', 'Okuyucu yönünü bulur.'], heard, 4, language='tr')
    assert result['scene_durations'] == pytest.approx([1.95, 2.05])
    assert result['audio_edited'] is False and heard == original
    assert qc._comparison_units('kare kodun', 'tr') == [('karekodun', ('kare', 'kodun'))]


@pytest.mark.parametrize('text', ['Bu kare kod köşesi.', 'Bu kare kodunun köşesi.',
    'Bu karo kodun köşesi.', 'Bu kare. Kodun köşesi.', 'Bu kare, kodun köşesi.',
    'Bu karekodun kenarı.'])
def test_wrong_word_suffix_or_sentence_boundary_still_rejects(text):
    with pytest.raises((VoiceQualityError, qc.AudioQCError)):
        edit_plan(['Bu karekodun köşesi.'], evidence(text), 4, language='tr')


def test_display_compound_cannot_cover_different_timestamp_words():
    heard = evidence('Bu kare kodun köşesi.')
    heard['words'][2]['word'] = 'kodunun'
    with pytest.raises((VoiceQualityError, qc.AudioQCError)):
        edit_plan(['Bu karekodun köşesi.'], heard, 4, language='tr')


def test_compound_cannot_hide_cut_between_two_scenes():
    with pytest.raises(VoiceQualityError):
        edit_plan(['Bu kare', 'kodun köşesi.'], evidence('Bu kare kodun köşesi.'), 4, language='tr')


def test_english_words_are_not_joined():
    assert qc._comparison_units('kare kodun', 'en') != qc._comparison_units('karekodun', 'en')
