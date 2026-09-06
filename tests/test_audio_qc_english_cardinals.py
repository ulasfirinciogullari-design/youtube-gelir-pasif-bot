"""Exact numeric representations in explicit EN contexts; no fuzzy matching."""
from copy import deepcopy

import pytest

from app.services import audio_qc as qc


@pytest.mark.parametrize('spoken,digits', [
    ('In two thousand four, it opened.', 'In 2004, it opened.'),
    ('Since two thousand and four, it grew.', 'Since 2004, it grew.'),
    ('During two thousand twenty-six, it grew.', 'During 2026, it grew.'),
    ('In one thousand nine hundred fifty-three, it opened.', 'In 1953, it opened.'),
    ('In two thousand, it opened.', 'In 2000, it opened.'),
    ('It lost two billion Danish kroner.', 'It lost 2 billion Danish kroner.'),
    ('It sold twenty-three million units.', 'It sold 23 million units.'),
    ('It sold one hundred and two million units.', 'It sold 102 million units.'),
    ('The balance was zero billion dollars.', 'The balance was 0 billion dollars.'),
    ('It sold twelve thousand units.', 'It sold 12 thousand units.'),
])
def test_complete_cardinals_match_same_digits_in_both_directions(spoken, digits):
    for expected, heard in ((spoken, digits), (digits, spoken)):
        result = qc.compare_transcript(expected, heard, comparison_language='en')
        assert result['pass'] is True and result['score'] == 100


@pytest.mark.parametrize('heard', ['In 2005, it lost 2 billion Danish kroner.',
    'In 2004, it lost 3 billion Danish kroner.', 'In 2004, it lost 2 million Danish kroner.',
    'In 2004, it lost 2 billion dollars.', 'In 2004, it earned 2 billion Danish kroner.',
    'In -2004, it lost 2 billion Danish kroner.', 'In 2004, it lost -2 billion Danish kroner.',
    'In 20 04, it lost 2 billion Danish kroner.', 'In 2004, it lost 2.1 billion Danish kroner.',
    'In 2004, it lost 2 billion kroner.'])
def test_changed_year_amount_currency_sign_or_words_still_fail(heard):
    expected = 'In two thousand four, it lost two billion Danish kroner.'
    assert qc.compare_transcript(expected, heard, comparison_language='en')['pass'] is False


@pytest.mark.parametrize('expected,heard', [
    ('two thousand four', '2004'), ('There are two items.', 'There are 2 items.'),
    ('In two, thousand four, it opened.', 'In 2004, it opened.'),
    ('In two thousand / four, it opened.', 'In 2004, it opened.'),
    ('In two thousand four four, it opened.', 'In 2008, it opened.'),
    ('In two thousand and, it opened.', 'In 2000, it opened.'),
    ('In two thousand four percent.', 'In 2004 percent.'),
    ('two, billion dollars', '2 billion dollars'), ('two point five billion dollars', '2.5 billion dollars'),
    ('two three billion dollars', '5 billion dollars'), ('two billion five dollars', '2 billion five dollars'),
    ('two and billion dollars', '2 billion dollars'), ('two billionth item', '2 billionth item'),
])
def test_lists_fragments_ordinals_and_context_free_numbers_are_not_guessed(expected, heard):
    assert qc.compare_transcript(expected, heard, comparison_language='en')['pass'] is False


def test_normalization_preserves_source_word_spans_and_actual_zero_timings():
    expected = 'In two thousand four, it lost two billion Danish kroner.'
    heard = 'In 2004, it lost 2 billion Danish kroner.'
    words = [{'word': w, 'start': i, 'end': i + .5} for i, w in enumerate(heard.split())]
    words[1]['end'] = words[1]['start']
    original = deepcopy(words)
    result = qc.compare_transcript(expected, heard, words=words, provider='openai', comparison_language='en')
    assert result['pass'] is True and result['mismatch_details']['timestamp_sequence_match'] is True
    assert words == original and result['word_timestamps'][1]['start'] == result['word_timestamps'][1]['end']
    with pytest.raises(qc.AudioQCError): qc._require_word_timing_evidence(result, 'OpenAI')
    wrong = qc.compare_transcript(expected, heard.replace('2004', '2005'), comparison_language='en')
    assert wrong['mismatch_details']['missing_words'] == ['two', 'thousand', 'four']


def test_numeric_equivalence_does_not_hide_raw_timestamp_word_reordering():
    expected = 'In two thousand four, it lost two billion Danish kroner.'
    heard = 'In 2004, it lost 2 billion Danish kroner.'
    words = [{'word': w, 'start': i, 'end': i + .5} for i, w in enumerate(heard.split())]
    words[2]['word'], words[3]['word'] = words[3]['word'], words[2]['word']
    result = qc.compare_transcript(expected, heard, words=words, provider='openai', comparison_language='en')
    assert result['pass'] is True and result['mismatch_details']['timestamp_sequence_match'] is False
    with pytest.raises(qc.AudioQCError): qc._require_word_timing_evidence(result, 'OpenAI')


def test_english_cardinal_rules_do_not_apply_to_turkish():
    assert qc.compare_transcript('In two thousand four.', 'In 2004.', comparison_language='tr')['pass'] is False
