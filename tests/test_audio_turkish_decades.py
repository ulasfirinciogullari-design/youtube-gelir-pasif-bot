from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.services import audio_qc as qc
from app.services.word_timed_narration import edit_plan
from app.services.voice import VoiceQualityError


def keys(text):
    return [key for key, _ in qc._comparison_units(text, 'tr')]


@pytest.mark.parametrize('spoken,numeric', [
    ('Bin dokuz yüz ellilerdeki fiyat savaşı.', "1950'lerdeki fiyat savaşı."),
    ('Bin dokuz yüz seksenlerde büyüdü.', "1980'lerde büyüdü."),
    ('İki bin yirmilerden kalma.', "2020'lerden kalma."),
])
def test_only_complete_identical_decade_and_suffix_are_equivalent(spoken, numeric):
    assert keys(spoken) == keys(numeric)


@pytest.mark.parametrize('different', ["1960'lerdeki fiyat savaşı.", "1950'lerde fiyat savaşı.",
    "1951'lerdeki fiyat savaşı.", "1950 fiyat savaşı.", "1950'lerdeki fiyat düşüşü.",
    'Ellilerdeki fiyat savaşı.', 'Bin dokuz yüz. Ellilerdeki fiyat savaşı.'])
def test_changed_or_ambiguous_decades_and_meaning_remain_different(different):
    assert keys('Bin dokuz yüz ellilerdeki fiyat savaşı.') != keys(different)


def test_verified_full_name_equivalence_does_not_merge_other_people():
    assert keys('Marangoz Ole Kirk Kristiansen geldi.') == keys('Marangoz Ole Kirk Christiansen geldi.')
    for left, right in [('Kristiansen geldi.', 'Christiansen geldi.'),
            ('Hans Kirk Kristiansen geldi.', 'Hans Kirk Christiansen geldi.'),
            ('Ole Kirk Kristiansen geldi.', 'Ole Krik Christiansen geldi.'),
            ('Ole Kirk Kristiansen geldi.', 'Ole Kirk Christiansen gitti.')]:
        assert keys(left) != keys(right)


@pytest.mark.parametrize('case', ['ikea', 'lego'])
def test_actual_captured_speech_preserves_original_timestamps_and_all_words(case):
    captured = json.loads((Path(__file__).parent/'fixtures/capital_turkish_speech_20260925.json').read_text())[case]
    original = deepcopy(captured)
    evidence = captured['evidence']
    result = edit_plan(captured['scenes'], evidence, captured['seconds'], language='tr')
    assert result['audio_edited'] is False and result['cuts'] == []
    assert sum(result['scene_durations']) == pytest.approx(captured['seconds'])
    assert captured == original
    changed = deepcopy(evidence)
    changed['text'] = changed['text'].replace('mobilya', 'oyuncak', 1) if case == 'ikea' else changed['text'].replace('iyi oyna', 'iyi oyunu')
    with pytest.raises(VoiceQualityError):
        edit_plan(captured['scenes'], changed, captured['seconds'], language='tr')
