from copy import deepcopy
from pathlib import Path
import json

import pytest

from app.services import audio_qc as qc


@pytest.mark.parametrize('spoken,written', [
    ('It cost four thousand kroner.', 'It cost 4,000 kroner.'),
    ('It cost four thousand kroner.', 'It cost 4000 kroner.'),
    ('It cost four thousand kroner.', 'It cost 4 thousand kroner.'),
    ('They paid twenty-three dollars.', 'They paid 23 dollars.'),
    ('They paid one hundred and two euros.', 'They paid 102 euros.'),
    ('It lost two billion Danish kroner.', 'It lost 2 billion Danish kroner.'),
    ('Zero billion dollars remained.', '0 dollars remained.'),
    ('They used hand saws.', 'They used handsaws.'),
    ('They used a hand-saw.', 'They used a handsaw.'),
    ('Postwar demand changed.', 'Post war demand changed.'),
    ('Prewar demand changed.', 'Pre-war demand changed.'),
])
def test_exact_amount_and_compound_spellings_keep_meaning(spoken, written):
    for expected, heard in ((spoken, written), (written, spoken)):
        result = qc.compare_transcript(expected, heard, comparison_language='en')
        assert result['pass'] is True and result['score'] == 100


@pytest.mark.parametrize('heard', [
    'It cost 4.000 kroner.', 'It cost 4,00 kroner.', 'It cost -4,000 kroner.',
    'It cost 400 kroner.', 'It cost 4,000 dollars.', 'It cost 4,000.',
    'It earned 4,000 kroner.', 'It cost four, thousand kroner.',
    'It cost four / thousand kroner.', 'It cost four point thousand kroner.',
    'It cost 4,000 percent kroner.', 'It cost 4,000 million kroner.',
])
def test_sign_value_decimal_currency_and_narration_changes_stay_rejected(heard):
    assert qc.compare_transcript('It cost four thousand kroner.', heard,
                                 comparison_language='en')['pass'] is False


def test_amount_equivalence_cannot_supply_missing_or_reordered_word_times():
    heard = 'It cost 4,000 kroner.'
    words = [{'word': word, 'start': i, 'end': i + .5} for i, word in enumerate(heard.split())]
    words[1]['word'], words[2]['word'] = words[2]['word'], words[1]['word']
    before = deepcopy(words)
    result = qc.compare_transcript('It cost four thousand kroner.', heard,
        words=words, provider='openai', comparison_language='en')
    assert result['pass'] is True and words == before
    with pytest.raises(qc.AudioQCError, match='inconsistent word timestamps'):
        qc._require_word_timing_evidence(result, 'OpenAI')


def test_actual_lego_text_preserves_name_disagreement_instead_of_approving_the_audio():
    fixture = json.loads((Path(__file__).parent/'fixtures/lego_audio_text_observations_20260922.json').read_text())
    for heard in fixture['heard']:
        result = qc.compare_transcript(fixture['expected'], heard, comparison_language='en')
        assert result['pass'] is False
        details = result['mismatch_details']
        assert 'kristiansen' in details['missing_words']
        assert 'christiansen' in details['unexpected_words']
        assert all(word not in details['missing_words'] for word in ('four', 'thousand', 'hand', 'saws'))


def test_english_currency_spelling_does_not_apply_to_turkish_decimal_conventions():
    assert qc.compare_transcript('It cost four thousand kroner.', 'It cost 4,000 kroner.',
                                 comparison_language='tr')['pass'] is False


@pytest.mark.parametrize('expected,heard', [
    ('Postwar demand changed.', 'Post, war demand changed.'),
    ('Postwar demand changed.', 'Post / war demand changed.'),
    ('Postwar demand changed.', 'Prewar demand changed.'),
    ('They were apart.', 'They were a part.'),
    ('It happened sometime.', 'It happened some time.'),
])
def test_named_compounds_do_not_merge_punctuation_or_ambiguous_words(expected, heard):
    assert qc.compare_transcript(expected, heard, comparison_language='en')['pass'] is False


def test_split_postwar_retains_actual_provider_intervals_and_missing_time_still_fails():
    heard = 'Post war demand changed.'
    words = [{'word': word, 'start': i, 'end': i + .5} for i, word in enumerate(heard.split())]
    before = deepcopy(words)
    result = qc.compare_transcript('Postwar demand changed.', heard,
        words=words, provider='openai', comparison_language='en')
    assert result['pass'] is True and words == before
    qc._require_word_timing_evidence(result, 'OpenAI')
    words[1]['end'] = words[1]['start']
    result = qc.compare_transcript('Postwar demand changed.', heard,
        words=words, provider='openai', comparison_language='en')
    with pytest.raises(qc.AudioQCError): qc._require_word_timing_evidence(result, 'OpenAI')
