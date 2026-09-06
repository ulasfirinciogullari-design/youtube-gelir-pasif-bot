"""Turkish locative-past numbers retain both exact value and spoken suffix."""
from copy import deepcopy

import pytest

from test_audio_qc import audio_qc as qc, _words


EXPECTED = 'İlk tarama, yirmi altı Haziran bin dokuz yüz yetmiş dörtteydi.'


@pytest.mark.parametrize('heard', [
    'İlk tarama, yirmi altı Haziran 1974teydi.',
    "İlk tarama, 26 Haziran 1974'teydi.",
    'İlk tarama, 26 Haziran 1974’teydi.',
    'İLK TARAMA, 26 HAZİRAN 1974’TEYDİ.',
])
def test_observed_date_matches_exact_digit_suffix_without_tolerance(heard):
    result = qc.compare_transcript(EXPECTED, heard)
    assert result['pass'] is True and result['score'] == 100.0
    assert result['mismatch_details']['exact_match'] is True
    assert result['mismatch_details']['missing_words'] == []
    assert result['mismatch_details']['unexpected_words'] == []
    assert result['mismatch_details']['operations'] == []


@pytest.mark.parametrize('spoken,digits', [
    ('bin dokuz yüz yetmiş dörtteydi', "1974'teydi"),
    ('bin dokuz yüz altmıştaydı', "1960'taydı"),
    ('bin dokuz yüz seksen altıdaydı', "1986'daydı"),
    ('bin dokuz yüz doksan yedideydi', "1997'deydi"),
    ('iki bin yirmi altıdaydı', "2026'daydı"),
])
@pytest.mark.parametrize('reverse', [False, True])
def test_each_exact_locative_past_form_has_symmetric_numeric_identity(spoken, digits, reverse):
    expected, heard = (digits, spoken) if reverse else (spoken, digits)
    assert qc.compare_transcript(expected, heard)['pass'] is True


@pytest.mark.parametrize('heard', [
    'İlk tarama, 26 Haziran 1973teydi.',
    'İlk tarama, 26 Haziran 1975teydi.',
    'İlk tarama, 27 Haziran 1974teydi.',
    'İlk tarama, 26 Temmuz 1974teydi.',
    'İlk tarama, 26 Haziran 1974te.',
    'İlk tarama, 26 Haziran 1974deydi.',
    'İlk tarama, 26 Haziran 1974teymiş.',
    'İlk tarama, 26 Haziran 1974teydi değil.',
    'İlk tarama, 26 Haziran 01974teydi.',
    'İlk tarama, 26 Haziran -1974teydi.',
    'İlk tarama, 26 Haziran +1974teydi.',
    'İlk tarama, 26 Haziran %1974teydi.',
    'İlk tarama, 26 Haziran 19 74teydi.',
    'İlk tarama, 26 Haziran 1974/2teydi.',
])
def test_different_date_suffix_number_boundary_or_spoken_meaning_still_fails(heard):
    result = qc.compare_transcript(EXPECTED, heard)
    assert result['pass'] is False and result['score'] < 100.0
    assert result['mismatch_details']['exact_match'] is False
    assert result['mismatch_details']['operations']


def test_wrong_year_diagnostics_preserve_the_original_spoken_words():
    result = qc.compare_transcript('bin dokuz yüz yetmiş dörtteydi', '1973teydi')
    assert result['mismatch_details']['missing_words'] == ['bin', 'dokuz', 'yüz', 'yetmiş', 'dörtteydi']
    assert result['mismatch_details']['unexpected_words'] == ['1973teydi']


@pytest.mark.parametrize('spoken,digits', [
    ('ondaydı', '10daydı'), ('yüzdeydi', '100deydi'), ('birdeydi', '1deydi'),
    ('dört teydi', '4teydi'), ('dörtteydiler', '4teydiler'),
    ('dörtteydi', '4teydi idi'),
])
def test_ambiguous_words_and_unrecognized_suffixes_are_not_guessed(spoken, digits):
    assert qc.compare_transcript(spoken, digits)['pass'] is False


@pytest.mark.parametrize('language', ['en', 'en-US', 'de', 'ar', 'es'])
def test_turkish_number_inflection_does_not_apply_to_other_languages(language):
    assert qc.compare_transcript('bin dokuz yüz yetmiş dörtteydi', '1974teydi',
                                 comparison_language=language)['pass'] is False


def test_unsupported_languages_are_not_silently_enabled():
    with pytest.raises(ValueError, match='language is unsupported'):
        qc.compare_transcript(EXPECTED, '1974teydi', comparison_language='fr')


@pytest.mark.parametrize('language', ['tr', 'tr-TR', 'turkish'])
def test_supported_turkish_language_aliases_use_the_same_exact_contract(language):
    assert qc.compare_transcript(EXPECTED, 'İlk tarama, 26 Haziran 1974teydi.',
                                 comparison_language=language)['pass'] is True


def test_numeric_equivalence_keeps_real_annotation_values_and_times_unchanged():
    words = _words('İlk', 'tarama,', '26', 'Haziran', '1974teydi.')
    original = deepcopy(words)
    result = qc.compare_transcript(EXPECTED, 'İlk tarama, 26 Haziran 1974teydi.',
                                   words=words, provider='openai')
    assert result['pass'] is True
    assert result['mismatch_details']['timestamp_sequence_match'] is True
    assert [(row['text'], row['start'], row['end']) for row in result['word_timestamps']] == [
        (row['text'], row['start'], row['end']) for row in original
    ]
    assert words == original


@pytest.mark.parametrize('annotated_year', [('1973teydi',), ('19', '74teydi'),
                                         ('bin', 'dokuz', 'yüz', 'yetmiş', 'dörtteydi')])
def test_transcript_equivalence_does_not_invent_matching_word_timestamps(annotated_year):
    result = qc.compare_transcript(EXPECTED, 'İlk tarama, 26 Haziran 1974teydi.',
                                   words=_words('İlk', 'tarama', '26', 'Haziran', *annotated_year),
                                   provider='openai')
    assert result['pass'] is True  # lexical comparison only
    assert result['mismatch_details']['timestamp_sequence_match'] is False


def test_prosody_quote_identity_preserves_the_year_and_suffix():
    spoken = 'bin dokuz yüz yetmiş dörtteydi'
    assert qc._prosody_phrases_equivalent(spoken, "1974'teydi", 'tr') is True
    for changed in ('1973teydi', '1974te', '1974teymiş'):
        assert qc._prosody_phrases_equivalent(spoken, changed, 'tr') is False
    assert qc._prosody_phrases_equivalent(spoken, '1974teydi', 'en') is False
