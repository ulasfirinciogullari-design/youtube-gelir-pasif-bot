"""Currency spelling equivalence without deleting punctuation or evidence."""
from copy import deepcopy

import pytest

from app.services import audio_qc as qc


@pytest.mark.parametrize('expected,heard', [
    ('Üzerindeki bir sent, maliyet değildir.', 'Üzerindeki bir cent, maliyet değildir.'),
    ('Maliyet 3, 69 sent.', 'Maliyet 3,69 cent.'),
    ('Maliyet 3,69 sent.', 'Maliyet 3.69 cent.'),
    ('Maliyet üç virgül altmış dokuz sent.', 'Maliyet 3,69 cent.'),
    ('Maliyet 0, 05 sent.', 'Maliyet 0,05 cent.'),
    ('MALİYET 3, 69 SENT.', 'MALİYET 3,69 CENT.'),
])
def test_explicit_cent_amount_is_symmetric(expected, heard):
    for left, right in ((expected, heard), (heard, expected)):
        result = qc.compare_transcript(left, right)
        assert result['pass'] is True and result['score'] == 100
        assert result['transcript'] == right
        assert result['word_timestamps'] == []
        assert result['mismatch_details']['timestamp_sequence_match'] is None


@pytest.mark.parametrize('heard', [
    'Maliyet 3 69 cent.', 'Maliyet 3. 69 cent.', 'Maliyet 3; 69 cent.',
    'Maliyet 3: 69 cent.', 'Maliyet 369 cent.', 'Maliyet 3,96 cent.',
    'Maliyet 3,69 dolar.', 'Maliyet 3,69 cent değil.', 'Maliyet 03,69 cent.',
    'Maliyet -3,69 cent.', 'Maliyet +3,69 cent.', 'Maliyet 3,690 cent.',
    'Maliyet 3/69 cent.', 'Maliyet 3-69 cent.', 'Maliyet 3,\n69 cent.',
    'Maliyet 3, 69cent.', 'Maliyet 3, 69 sent2.',
])
def test_changed_value_unit_polarity_or_missing_separator_fails(heard):
    assert qc.compare_transcript('Maliyet 3,69 sent.', heard)['pass'] is False


@pytest.mark.parametrize('expected,heard', [
    ('Bu sent sözcüğüdür.', 'Bu cent sözcüğüdür.'),
    ('bir,sent', 'bir cent'), ('sent', 'cent'), ('senti', 'centi'),
    ('sentler', 'centler'), ('bir sentetik', 'bir centetik'),
    ('Kod_3,69sent', 'Kod_3, 69cent'), ('3, 69 lira', '3,69 lira'),
    ('3, 69', '3,69'), ('3,69 metre', '3, 69 metre'),
])
def test_non_currency_or_unbounded_lexical_context_is_not_rewritten(expected, heard):
    assert qc.compare_transcript(expected, heard)['pass'] is False


@pytest.mark.parametrize('language', ['en', 'de', 'es', 'ar'])
def test_cent_alias_is_turkish_only(language):
    assert qc.compare_transcript('1 sent', '1 cent', comparison_language=language)['pass'] is False


def test_raw_spans_and_timestamp_guards_remain_independent():
    words = [{'word': '3,69', 'start': 0, 'end': 0}, {'word': 'cent', 'start': .2, 'end': .6}]
    before = deepcopy(words)
    result = qc.compare_transcript('3, 69 sent', '3,69 cent', words=words, provider='openai')
    assert result['pass'] is True
    assert words == before and result['word_timestamps'][0]['end'] == 0
    with pytest.raises(qc.AudioQCError):
        qc._require_word_timing_evidence(result, 'OpenAI')
    wrong = qc.compare_transcript('3, 69 sent', '3,69 cent',
        words=[{'word': '369', 'start': 0, 'end': .3}, {'word': 'cent', 'start': .3, 'end': .6}], provider='openai')
    assert wrong['pass'] is True
    assert wrong['mismatch_details']['timestamp_sequence_match'] is False
    mismatch = qc.compare_transcript('3, 69 sent', '3,96 cent')
    assert mismatch['mismatch_details']['missing_words'] == ['3', '69', 'sent']
    assert mismatch['mismatch_details']['unexpected_words'] == ['3,96', 'cent']
