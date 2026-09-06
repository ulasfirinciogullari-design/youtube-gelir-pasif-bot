"""Exact mixed-digit Turkish scale spelling; no tolerance or timing approval."""
from copy import deepcopy

import pytest

from app.services import audio_qc as qc


EXPECTED = (
    'Cebinizdeki bir milyon lira, bir sabah nasıl bir liraya dönüştü? '
    'Bir Ocak iki bin beşte, altı sıfır atıldı. Bir milyon eski Türk lirası, '
    'bir Yeni Türk Lirası oldu. Bu değişiklik, insanları bir gecede daha zengin yapmadı. '
    'Eski ve yeni banknotlar bir yıl birlikte kullanıldı. Çünkü değişen, '
    'paranın satın alma gücü değil, üzerindeki sayıydı.'
)
HEARD = (
    'Cebinizdeki 1 milyon lira, bir sabah nasıl 1 liraya dönüştü? '
    "1 Ocak. 2005'te 6 sıfır atıldı. 1 milyon eski Türk lirası, "
    '1 yeni Türk lirası oldu. Bu değişiklik insanları bir gecede daha zengin yapmadı. '
    'Eski ve yeni banknotlar bir yıl birlikte kullanıldı. Çünkü değişen, '
    'paranın satın alma gücü değil, üzerindeki sayıydı.'
)


def test_actual_capital_texts_match_without_modifying_provider_transcript():
    result = qc.compare_transcript(EXPECTED, HEARD)
    assert result['pass'] is True and result['score'] == 100
    assert result['transcript'] == HEARD
    assert result['mismatch_details']['operations'] == []
    assert result['mismatch_details']['timestamp_sequence_match'] is None
    assert result['word_timestamps'] == []


@pytest.mark.parametrize('spoken,digits', [
    ('bir milyon lira', '1 milyon lira'),
    ('iki milyon lira', '2 milyon lira'),
    ('yirmi üç bin adet', '23 bin adet'),
    ('yüz iki milyar lira', '102 milyar lira'),
    ('dokuz yüz doksan dokuz trilyon lira', '999 trilyon lira'),
    ('BİR MİLYON LİRA', '1 MİLYON LİRA'),
])
def test_unsigned_complete_coefficient_scale_is_symmetric(spoken, digits):
    for expected, heard in ((spoken, digits), (digits, spoken)):
        assert qc.compare_transcript(expected, heard)['pass'] is True


@pytest.mark.parametrize('heard', [
    '2 milyon lira', '1 milyar lira', '1 bin lira', '1 milyon dolar',
    '1 milyon lira değil', '01 milyon lira', '+1 milyon lira', '-1 milyon lira',
    '1.0 milyon lira', '1,5 milyon lira', '1-2 milyon lira', '1–2 milyon lira',
    '1/2 milyon lira', '1 2 milyon lira', '1milyon lira', '1, milyon lira',
    '1 milyon 2 lira', '1 milyon2 lira',
])
def test_values_negation_signs_decimals_ranges_and_boundaries_are_not_lost(heard):
    result = qc.compare_transcript('bir milyon lira', heard)
    assert result['pass'] is False and result['score'] < 100


@pytest.mark.parametrize('expected,heard', [
    ('A1000000 kodu', 'A1 milyon kodu'),
    ('1000000X kodu', '1 milyonX kodu'),
    ('Kod_1000000', 'Kod_1 milyon'),
    ('bir milyon lira', '1000000 milyon lira'),
])
def test_adjacent_identifiers_and_oversized_coefficients_are_not_rewritten(expected, heard):
    assert qc.compare_transcript(expected, heard)['pass'] is False


def test_existing_decimal_number_and_scale_representation_is_unchanged():
    result = qc.compare_transcript('dört virgül sekiz milyon lira', '4,8 milyon lira')
    assert result['pass'] is True and result['score'] == 100
    assert qc._comparison_units('4,8 milyon') == [
        (qc._numeric_key('4,8'), ('4,8',)), ('milyon', ('milyon',))]


def test_mismatch_preserves_both_original_word_spans():
    result = qc.compare_transcript('bir milyon', '2 milyon')
    assert result['mismatch_details']['missing_words'] == ['bir', 'milyon']
    assert result['mismatch_details']['unexpected_words'] == ['2', 'milyon']
    assert qc._comparison_units('1 milyon')[0][1] == ('1', 'milyon')


@pytest.mark.parametrize('language', ['en', 'de', 'es', 'ar'])
def test_turkish_scale_rule_is_language_bound(language):
    assert qc.compare_transcript('bir milyon', '1 milyon', comparison_language=language)['pass'] is False


def test_numeric_equality_does_not_rewrite_words_or_fix_invalid_actual_timestamps():
    words = [{'word':'1','start':0,'end':0}, {'word':'milyon','start':.2,'end':.6}]
    original = deepcopy(words)
    result = qc.compare_transcript('bir milyon','1 milyon',words=words,provider='openai')
    assert result['pass'] is True and result['score'] == 100
    assert words == original
    assert result['word_timestamps'][0]['start'] == result['word_timestamps'][0]['end'] == 0
    with pytest.raises(qc.AudioQCError):
        qc._require_word_timing_evidence(result,'OpenAI')
    wrong = qc.compare_transcript('bir milyon','1 milyon',
        words=[{'word':'1000000','start':0,'end':.6}],provider='openai')
    assert wrong['pass'] is True
    assert wrong['mismatch_details']['timestamp_sequence_match'] is False
