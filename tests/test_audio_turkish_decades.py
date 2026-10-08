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
    ('Ellili yıllarda büyüdü.', "50'li yıllarda büyüdü."),
    ('Otuzlu yıllardan kalma.', "30’lu yıllardan kalma."),
    ('Kırklı yıllardaki rekabet.', "40'lı yıllardaki rekabet."),
    ('Bin dokuz yüz ellili yıllarda büyüdü.', "1950'li yıllarda büyüdü."),
    ('İki bin yirmili yıllarda büyüdü.', "2020'li yıllarda büyüdü."),
])
def test_only_complete_identical_decade_and_suffix_are_equivalent(spoken, numeric):
    assert keys(spoken) == keys(numeric)


@pytest.mark.parametrize('different', ["1960'lerdeki fiyat savaşı.", "1950'lerde fiyat savaşı.",
    "1951'lerdeki fiyat savaşı.", "1950 fiyat savaşı.", "1950'lerdeki fiyat düşüşü.",
    'Ellilerdeki fiyat savaşı.', 'Bin dokuz yüz. Ellilerdeki fiyat savaşı.'])
def test_changed_or_ambiguous_decades_and_meaning_remain_different(different):
    assert keys('Bin dokuz yüz ellilerdeki fiyat savaşı.') != keys(different)


@pytest.mark.parametrize('left,right', [
    ('Ellili yıllarda büyüdü.', "1950'li yıllarda büyüdü."),
    ('Ellili yıllarda büyüdü.', "60'lı yıllarda büyüdü."),
    ('Ellili yıllarda büyüdü.', "50'li yıllardan büyüdü."),
    ('Ellili yıllarda büyüdü.', "50'lı yıllarda büyüdü."),
    ('Ellili yıllarda büyüdü.', "51'li yıllarda büyüdü."),
    ('Ellili gruplar.', "50'li gruplar."),
    ('Ellili. Yıllarda büyüdü.', "50'li. Yıllarda büyüdü."),
    ('Bin dokuz yüz. Ellili yıllarda büyüdü.', "1950'li yıllarda büyüdü."),
    ('Bin dokuz yüz ellili yıllarda büyüdü.', "1850'li yıllarda büyüdü."),
])
def test_decade_adjective_requires_same_number_suffix_and_year_context(left, right):
    assert keys(left) != keys(right)


def test_verified_full_name_equivalence_does_not_merge_other_people():
    assert keys('Marangoz Ole Kirk Kristiansen geldi.') == keys('Marangoz Ole Kirk Christiansen geldi.')
    for left, right in [('Kristiansen geldi.', 'Christiansen geldi.'),
            ('Hans Kirk Kristiansen geldi.', 'Hans Kirk Christiansen geldi.'),
            ('Ole Kirk Kristiansen geldi.', 'Ole Krik Christiansen geldi.'),
            ('Ole Kirk Kristiansen geldi.', 'Ole Kirk Christiansen gitti.')]:
        assert keys(left) != keys(right)


@pytest.mark.parametrize('case', ['ikea', 'lego', 'ikea_boycott'])
def test_actual_captured_speech_preserves_original_timestamps_and_all_words(case):
    captured = json.loads((Path(__file__).parent/'fixtures/capital_turkish_speech_20260925.json').read_text())[case]
    original = deepcopy(captured)
    evidence = captured['evidence']
    result = edit_plan(captured['scenes'], evidence, captured['seconds'], language='tr')
    assert result['audio_edited'] is False and result['cuts'] == []
    assert sum(result['scene_durations']) == pytest.approx(captured['seconds'])
    assert captured == original
    changed = deepcopy(evidence)
    changed['text'] = changed['text'].replace('mobilya', 'oyuncak', 1) if case != 'lego' else changed['text'].replace('iyi oyna', 'iyi oyunu')
    with pytest.raises(VoiceQualityError):
        edit_plan(captured['scenes'], changed, captured['seconds'], language='tr')


def test_actual_truncated_boycott_takes_still_fail_after_decade_normalization():
    cases = json.loads((Path(__file__).parent/'fixtures/capital_turkish_speech_20260925.json').read_text())
    scenes = cases['ikea_boycott']['scenes']
    for captured in cases['ikea_boycott_truncated']:
        with pytest.raises(VoiceQualityError):
            edit_plan(scenes, captured['evidence'], captured['seconds'], language='tr')
