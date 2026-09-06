"""Exact Turkish ASR boundary equivalence; never a pronunciation approval."""
from copy import deepcopy

import pytest

from test_audio_qc import audio_qc as qc, _words


@pytest.mark.parametrize('apostrophe', sorted(qc._APOSTROPHES))
@pytest.mark.parametrize('base,suffix', [('Troy', 'daki'), ('İzmir', 'deki'),
                                         ('Tokat', 'taki'), ('İzmit', 'teki')])
def test_explicit_expected_apostrophe_proves_only_identical_split(base, suffix, apostrophe):
    expected = f'{base}{apostrophe}{suffix} market açıldı.'
    heard = f'{base} {suffix} market açıldı.'
    result = qc.compare_transcript(expected, heard)
    assert result['pass'] is True
    assert result['score'] == 100
    assert result['mismatch_details']['sequence_ratio'] == 1
    assert result['mismatch_details']['operations'] == []
    assert result['normalized_transcript'].startswith(f'{qc._turkish_lower(base)} {suffix}')


def test_actual_capital_sentence_keeps_real_provider_word_boundaries():
    expected = 'Troy\u2019daki Marsh süpermarketinde, bildiğimiz market barkodu ilk kez okutuldu.'
    heard = 'Troy daki Marsh süpermarketinde bildiğimiz market barkodu ilk kez okutuldu.'
    words = _words(*heard.split())
    before = deepcopy(words)
    result = qc.compare_transcript(expected, heard, provider='elevenlabs', words=words)
    assert result['pass'] is True
    assert result['score'] == 100
    assert result['mismatch_details']['timestamp_sequence_match'] is True
    assert [word['text'] for word in result['word_timestamps'][:2]] == ['Troy', 'daki']
    assert [(word['start'], word['end']) for word in result['word_timestamps'][:2]] == [(0, .4), (.4, .8)]
    assert words == before


@pytest.mark.parametrize('expected,heard', [
    ('Troydaki market', 'Troy daki market'),  # No expected apostrophe proof.
    ('Troy\u2019daki market', 'Troy deki market'),
    ('Troy\u2019daki market', 'Truva daki market'),
    ('Troy\u2019daki market', 'Troy market'),
    ('Troy\u2019daki market', 'Troy daki daki market'),
    ('Troy\u2019daki market', 'Troy, daki market'),
    ('Troy\u2019daki market', 'Troy. daki market'),
    ('Troy\u2019daki market', 'Troy-daki market'),
    ('Troy\u2019daki market', 'Troy 2 daki market'),
    ('Troy\u2019daki market', 'Troy da ki market'),
    ('Troy\u2019daydı market', 'Troy daydı market'),  # Unsupported suffix.
    ('1974\u2019teki market', '1974 teki market'),
    ('1974\u2019teki market', '1975 teki market'),
    ('Troy\u2019daki 29 market', 'Troy daki 2 9 market'),
    ('Troy\u2019daki 29 market', 'Troy daki 30 market'),
    ('Troy\u2019daki 29 market', 'Troy daki -29 market'),
    ('Troy\u2019daki oldu', 'Troy daki öldü'),
    ('12Troy\u2019daki market', '12Troy daki market'),
    ('T\u2019daki market', 'T daki market'),
])
def test_unproven_split_or_real_difference_still_fails(expected, heard):
    result = qc.compare_transcript(expected, heard)
    assert result['pass'] is False
    assert result['score'] < 100
    assert result['mismatch_details']['operations']


@pytest.mark.parametrize('expected,heard', [
    ('Troy\u2019daki market', 'Troy daki market'),
    ('therapist', 'the rapist'),
])
def test_english_never_gets_turkish_suffix_equivalence(expected, heard):
    assert qc.compare_transcript(expected, heard, comparison_language='en')['pass'] is False


def test_merged_comparison_unit_keeps_both_source_words_for_diagnostics():
    proof = qc._expected_apostrophe_suffixes('Troy\u2019daki market')
    units = qc._comparison_units('Troy daki market', expected_apostrophe_suffixes=proof)
    assert units[0] == ('troydaki', ('troy', 'daki'))
    details = qc._mismatch_details(['elsewhere'], [units[0][0]], heard_sources=[units[0][1]])
    assert details['unexpected_words'] == ['troy', 'daki']


def test_semantic_equivalence_does_not_invent_missing_timestamp_coverage():
    result = qc.compare_transcript('Troy\u2019daki market', 'Troy daki market',
                                   words=_words('Troy', 'market'), provider='elevenlabs')
    assert result['mismatch_details']['timestamp_sequence_match'] is False
    assert [word['text'] for word in result['word_timestamps']] == ['Troy', 'market']
