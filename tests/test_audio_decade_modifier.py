"""Orthographic decade modifiers never erase genuine signed-number errors."""
from copy import deepcopy

import pytest

from app.services import audio_qc as qc


@pytest.mark.parametrize('modifier', ['early', 'mid', 'late'])
@pytest.mark.parametrize('cue', ['in', 'during', 'since'])
def test_temporal_attached_decade_modifier_is_symmetric(modifier, cue):
    plain = f'They introduced the concept {cue} the {modifier} 1950s.'
    hyphen = f'They introduced the concept {cue} the {modifier}-1950s.'
    for expected, heard in ((plain, hyphen), (hyphen, plain)):
        assert qc.compare_transcript(expected, heard, comparison_language='en')['pass'] is True


@pytest.mark.parametrize('expected,heard', [
    ('During the mid 1950s.', 'During the mid-1960s.'),
    ('During the mid 1950s.', 'During the mid-1950.'),
    ('During the mid 1950s.', 'During the mid -1950s.'),
    ('During the mid 1950s.', 'During the mid−1950s.'),
    ('During the mid 1950s.', 'During the mid--1950s.'),
    ('During the mid 1950s.', 'During the mid-1950s not.'),
    ('They counted 1950 units.', 'They counted -1950 units.'),
    ('The change was 5 percent.', 'The change was -5 percent.'),
    ('They counted mid 1950s.', 'They counted mid-1950s.'),
])
def test_changed_values_signed_amounts_missing_words_and_ambiguous_context_fail(expected, heard):
    assert qc.compare_transcript(expected, heard, comparison_language='en')['pass'] is False


def test_exact_observed_sentence_preserves_provider_words_and_times():
    expected = 'They introduced the System in Play concept during the mid 1950s.'
    heard = 'They introduced the System in Play concept during the mid-1950s.'
    words = [{'text': word, 'start': i * .3, 'end': i * .3 + .25}
             for i, word in enumerate(heard.split())]
    before = deepcopy(words)
    result = qc.compare_transcript(expected, heard, words=words, provider='elevenlabs',
                                   language_code='eng', comparison_language='en')
    assert qc._require_word_timing_evidence(result, 'ElevenLabs')['pass'] is True
    assert result['score'] == 100 and words == before


def test_missing_timestamp_token_is_not_created_by_spelling_equivalence():
    result = qc.compare_transcript('During the mid 1950s.', 'During the mid-1950s.',
        comparison_language='en', provider='elevenlabs', words=[
            {'text': word, 'start': i, 'end': i + .5} for i, word in enumerate(['During', 'the', 'mid'])])
    assert result['pass'] is True
    with pytest.raises(qc.AudioQCError): qc._require_word_timing_evidence(result, 'ElevenLabs')
