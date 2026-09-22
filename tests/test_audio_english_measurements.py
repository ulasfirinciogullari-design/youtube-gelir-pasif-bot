from copy import deepcopy
import pytest
from app.services import audio_qc as qc


@pytest.mark.parametrize('spoken,heard', [
    ('forty-five-gram headphones', '45 gram headphones'),
    ('forty five grams', '45 grams'), ('one hundred and five watts', '105 watts'),
    ('twenty-one-centimeter cable', '21 centimeter cable'), ('zero volts', '0 volts'),
    ('nine hundred ninety nine meters', '999 meters')])
def test_exact_whole_measurement_spelling_in_either_direction(spoken, heard):
    for expected, actual in [(spoken, heard), (heard, spoken)]:
        result = qc.compare_transcript(expected, actual, comparison_language='en')
        assert result['pass'] is True and result['score'] == 100


@pytest.mark.parametrize('spoken,heard', [
    ('forty-five-gram headphones', '46 gram headphones'),
    ('forty-five-gram headphones', '45 kilogram headphones'),
    ('forty five grams', '45 gram'), ('forty five grams', '045 grams'),
    ('forty five grams', '45.0 grams'), ('forty, five grams', '45 grams'),
    ('forty/five grams', '45 grams'), ('forty. Five grams', '45 grams'),
    ('forty five six grams', '46 grams'), ('forty five grams', '-45 grams'),
    ('-forty-five grams', '45 grams'), ('minus forty five grams', '45 grams'),
    ('forty five grams', '+45 grams'), ('forty five', '45'),
    ('forty five headphones', '45 headphones'), ('forty five percent', '45 percent')])
def test_wrong_values_units_or_ambiguous_tokens_still_fail(spoken, heard):
    assert qc.compare_transcript(spoken, heard, comparison_language='en')['pass'] is False


def test_equivalence_does_not_change_words_or_create_timestamps():
    words = [{'word': '45', 'start': 0, 'end': .35}, {'word': 'gram', 'start': .4, 'end': .7}]
    before = deepcopy(words)
    result = qc.compare_transcript('forty-five-gram', '45 gram', words=words,
                                   provider='openai', comparison_language='en')
    assert qc._require_word_timing_evidence(result, 'OpenAI')['pass'] is True and words == before
    words[1]['start'] = words[1]['end'] = .4
    broken = qc.compare_transcript('forty-five-gram', '45 gram', words=words,
                                   provider='openai', comparison_language='en')
    with pytest.raises(qc.AudioQCError): qc._require_word_timing_evidence(broken, 'OpenAI')
