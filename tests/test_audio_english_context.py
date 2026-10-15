from copy import deepcopy
import pytest
from app.services import audio_qc as qc


@pytest.mark.parametrize('spoken,heard', [
    ('Goldman opened twenty-one Sun Grocery stores.', 'Goldman opened 21 Sun Grocery stores.'),
    ('She operated one hundred and five retail outlets.', 'She operated 105 retail outlets.'),
    ('They closed nine hundred ninety-nine branches.', 'They closed 999 branches.'),
    ('between three hundred and four hundred total grams', 'between 300 and 400 total grams'),
    ('between one hundred and five and two hundred and six watts', 'between 105 and 206 watts'),
    ('On July first, nineteen seventy-nine, Sony launched it.', 'On July 1st, 1979, Sony launched it.'),
    ('July thirty-first nineteen ninety-nine', 'July 31st 1999'),
    ('February twenty-ninth, two thousand and four', 'February 29th, 2004'),
    ('January eleventh, two thousand twenty-six', 'January 11th, 2026'),
])
def test_exact_contextual_representation_in_both_directions(spoken, heard):
    for expected, actual in ((spoken, heard), (heard, spoken)):
        result = qc.compare_transcript(expected, actual, comparison_language='en')
        assert result['pass'] is True and result['score'] == 100


@pytest.mark.parametrize('spoken,heard', [
    ('opened twenty-one Sun Grocery stores', 'opened 22 Sun Grocery stores'),
    ('opened twenty-one Sun Grocery stores', 'opened 21 Son Grocery stores'),
    ('opened twenty-one Sun Grocery stores', 'opened 21 Grocery stores'),
    ('opened twenty-one Sun Grocery stores', 'closed 21 Sun Grocery stores'),
    ('opened twenty-one Sun Grocery stores', 'opened 021 Sun Grocery stores'),
    ('opened twenty-one Sun Grocery stores', 'opened -21 Sun Grocery stores'),
    ('opened twenty, one Sun Grocery stores', 'opened 21 Sun Grocery stores'),
    ('opened twenty/one Sun Grocery stores', 'opened 21 Sun Grocery stores'),
    ('opened twenty one two stores', 'opened 23 stores'),
    ('twenty-one Sun Grocery stores', '21 Sun Grocery stores'),
    ('between three hundred and four hundred total grams', 'between 400 and 300 total grams'),
    ('between three hundred and four hundred total grams', 'between 300 and 401 total grams'),
    ('between three hundred and four hundred total grams', 'between 300 and 400 grams'),
    ('between three hundred and four hundred grams', 'between 300 and 400 kilograms'),
    ('between three, hundred and four hundred grams', 'between 300 and 400 grams'),
    ('between three hundred and four hundred grams', 'between 0300 and 400 grams'),
    ('between three hundred and four hundred grams', 'between -300 and 400 grams'),
    ('between three hundred and four hundred grams', 'between 300.0 and 400 grams'),
    ('On July first, nineteen seventy-nine', 'On July 2nd, 1979'),
    ('On July first, nineteen seventy-nine', 'On July 1st, 1978'),
    ('On July first, nineteen seventy-nine', 'On June 1st, 1979'),
    ('On July first, nineteen seventy-nine', 'On July 1th, 1979'),
    ('On July first, nineteen seventy-nine', 'On July 01st, 1979'),
    ('On July first, nineteen seventy-nine', 'On July 1st, -1979'),
    ('On July first, nineteen seventy-nine', 'On July 1st, 1979.5'),
    ('On July first, nineteen, seventy-nine', 'On July 1st, 1979'),
    ('On July first / nineteen seventy-nine', 'On July 1st, 1979'),
    ('On July first, nineteen seventy-nine ten', 'On July 1st, 1989'),
    ('February twenty-ninth, two thousand three', 'February 29th, 2003'),
    ('July first', 'July 1st'), ('first', '1st'),
])
def test_actual_errors_or_ambiguous_fragments_are_not_hidden(spoken, heard):
    assert qc.compare_transcript(spoken, heard, comparison_language='en')['pass'] is False


def test_original_word_spans_and_independent_timing_evidence_survive():
    expected = 'On July first, nineteen seventy-nine, Sony launched it.'
    heard = 'On July 1st, 1979, Sony launched it.'
    words = [{'word': word, 'start': i, 'end': i + .5} for i, word in enumerate(heard.split())]
    original = deepcopy(words)
    result = qc.compare_transcript(expected, heard, words=words, provider='elevenlabs', comparison_language='en')
    assert qc._require_word_timing_evidence(result, 'ElevenLabs')['pass'] is True
    assert words == original
    wrong = qc.compare_transcript(expected, heard.replace('1979', '1978'), comparison_language='en')
    assert wrong['mismatch_details']['missing_words'] == ['july', 'first', 'nineteen', 'seventy', 'nine']
    words[2]['end'] = words[2]['start']
    broken = qc.compare_transcript(expected, heard, words=words, provider='elevenlabs', comparison_language='en')
    with pytest.raises(qc.AudioQCError):
        qc._require_word_timing_evidence(broken, 'ElevenLabs')
    assert qc.compare_transcript(expected, heard, comparison_language='tr')['pass'] is False
