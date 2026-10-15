from copy import deepcopy
from pathlib import Path
import json

import pytest

from app.services import audio_qc as qc


@pytest.mark.parametrize('british,american', [
    ('tokenised deposits', 'tokenized deposits'), ('tokenisation grew', 'tokenization grew'),
    ('organised production', 'organized production'), ('specialised machinery', 'specialized machinery'),
    ('recognised the design', 'recognized the design'), ('standardising work', 'standardizing work'),
    ('optimisation improved', 'optimization improved'), ('colour changed', 'color changed'),
    ('labour costs grew', 'labor costs grew'), ('distribution centres', 'distribution centers'),
])
def test_known_spelling_variants_match_without_rewriting_observations(british, american):
    for expected, heard in ((british, american), (american, british)):
        assert qc.compare_transcript(expected, heard, comparison_language='en')['pass'] is True


@pytest.mark.parametrize('expected,heard', [
    ('It advised buyers.', 'It advized buyers.'), ('They devised a plan.', 'They devized a plan.'),
    ('They tokenised deposits.', 'They tokenized debts.'),
    ('It tokenised deposits.', 'It tokenizes deposits.'),
    ('It did not standardise.', 'It did standardize.'),
    ('The organiser left.', 'The organization left.'),
    ('Tokenised deposits.', 'Token deposits.'),
    ('The analyses changed.', 'The analyzes changed.'),
])
def test_changed_words_tense_negation_and_arbitrary_suffixes_still_fail(expected, heard):
    assert qc.compare_transcript(expected, heard, comparison_language='en')['pass'] is False


def test_actual_six_transcripts_preserve_each_provider_word_interval():
    fixture = json.loads((Path(__file__).parent/'fixtures/tokenized_audio_observations_20260922.json').read_text())
    before = deepcopy(fixture)
    passes = 0
    for index, row in enumerate(fixture['observations']):
        payload = row['payload']
        result = qc.compare_transcript(fixture['expected'], payload['text'], words=payload['words'],
            language_code=payload.get('language', payload.get('language_code')),
            provider=row['provider'], comparison_language='en')
        # The first Whisper observation also changed the proper name to PONTEZ.
        assert result['pass'] is (index != 0)
        if index == 0:
            assert result['mismatch_details']['missing_words'] == ['pontes']
            assert result['mismatch_details']['unexpected_words'] == ['pontez']
        if row['provider'] == 'elevenlabs':
            assert qc._require_word_timing_evidence(result, 'ElevenLabs')['pass'] is True
            passes += 1
        else:
            with pytest.raises(qc.AudioQCError, match='incomplete word timestamps'):
                qc._require_word_timing_evidence(result, 'OpenAI')
    assert passes == 3 and fixture == before
