import pytest
from app.services.audio_qc import compare_transcript, _require_word_timing_evidence, AudioQCError


@pytest.mark.parametrize('suffix', ['baskı', 'baskıyla', 'baskıya', 'baskıda', 'baskının', 'baskısı'])
def test_exact_turkish_printing_term_retains_all_words_and_timings(suffix):
    expected = 'Seri numaraları tipo ' + suffix + ' işlenir.'
    heard = expected.replace('tipo', 'typo'); words = heard.split()
    timings = [{'text': word, 'start': i*.3, 'end': (i+1)*.3} for i, word in enumerate(words)]
    review = _require_word_timing_evidence(compare_transcript(expected, heard, words=timings,
        comparison_language='tr', language_code='tr', language_probability=1), 'Scribe')
    assert review['pass'] is True and review['score'] == 100
    assert review['word_timestamps'][2]['text'] == 'typo'


@pytest.mark.parametrize('expected,heard,language', [
    ('tipo baskı', 'typo baskı', 'en'), ('tipo', 'typo', 'tr'),
    ('tipo yazım hatası', 'typo yazım hatası', 'tr'),
    ('tipo. Baskıyla', 'typo. Baskıyla', 'tr'), ('tipo baskıyla', 'tifo baskıyla', 'tr'),
    ('tipo baskıyla', 'typo baskıya', 'tr'), ('tipo baskıyla', 'tipo ofset baskıyla', 'tr'),
    ('iki tipo baskı', 'üç typo baskı', 'tr'), ('tipo baskı', 'typo baskını', 'tr'),
])
def test_other_words_languages_values_or_sentence_boundaries_do_not_alias(expected, heard, language):
    assert compare_transcript(expected, heard, comparison_language=language)['pass'] is False


def test_missing_timestamps_still_block_the_printing_equivalence():
    review = compare_transcript('tipo baskıyla', 'typo baskıyla', comparison_language='tr')
    with pytest.raises(AudioQCError, match='incomplete word timestamps'):
        _require_word_timing_evidence(review, 'Scribe')
