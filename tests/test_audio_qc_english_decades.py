"""Replay the actual IKEA voice observations, retaining independent timings."""
from copy import deepcopy
from pathlib import Path
import json

import pytest

from app.services import audio_qc as qc


@pytest.mark.parametrize('spoken,digits', [
    ('In the nineteen-fifties', 'In the 1950s'),
    ('During the nineteen sixties', 'During the 1960s'),
    ('Since nineteen-seventies', 'Since 1970s'),
    ('Before the eighteen-eighties', 'Before the 1880s'),
    ('In the twenty-twenties', 'In the 2020s'),
])
def test_complete_contextual_decades_match_both_ways(spoken, digits):
    for expected, heard in ((spoken, digits), (digits, spoken)):
        assert qc.compare_transcript(expected + ', stores opened.', heard + ', stores opened.',
                                     comparison_language='en')['pass'] is True


@pytest.mark.parametrize('heard', [
    'In the 1960s, stores opened.', 'In the 1950, stores opened.',
    'In the fifties, stores opened.', 'In the 1950s, stores closed.',
    'In the 19 50s, stores opened.', 'In the 1950s, opened stores.',
])
def test_changed_decade_words_and_order_fail(heard):
    assert qc.compare_transcript('In the nineteen-fifties, stores opened.', heard,
                                  comparison_language='en')['pass'] is False


@pytest.mark.parametrize('spoken,digits', [
    ('nineteen fifties', '1950s'), ('They counted nineteen fifties.', 'They counted 1950s.'),
    ('In the nineteen, fifties', 'In the 1950s'),
    ('In the nineteen / fifties', 'In the 1950s'),
    ('In the nineteen-fifties thousand', 'In the 1950s thousand'),
    ('In the nineteen-fifties percent', 'In the 1950s percent'),
    ('In the nineteen-fifties 2', 'In the 1950s 2'),
])
def test_ambiguous_numbers_are_not_reinterpreted(spoken, digits):
    assert qc.compare_transcript(spoken, digits, comparison_language='en')['pass'] is False


def test_actual_secondary_is_exact_and_primary_still_has_missing_word_timings():
    data = json.loads((Path(__file__).parent / 'fixtures/ikea_restaurant_audio_observations.json').read_text())
    before = deepcopy(data)
    for row in data['observations']:
        p = row['payload']
        result = qc.compare_transcript(data['expected'], p['text'], words=p['words'],
            language_code=p.get('language_code', p.get('language')), provider=row['provider'], comparison_language='en')
        if row['provider'] == 'openai':
            assert result['pass'] is False  # Wrong place name remains wrong.
            with pytest.raises(qc.AudioQCError, match='incomplete word timestamps'):
                qc._require_word_timing_evidence(result, 'OpenAI')
        else:
            assert qc._require_word_timing_evidence(result, 'ElevenLabs')['pass'] is True
            assert result['score'] == 100 and result['mismatch_details']['timestamp_sequence_match'] is True
    assert data == before


def test_decade_normalization_does_not_invent_provider_timestamp_tokens():
    result = qc.compare_transcript('In the nineteen-fifties', 'In the 1950s',
        comparison_language='en', provider='elevenlabs', words=[
            {'text': t, 'start': i, 'end': i + .5} for i, t in enumerate(['In', 'the', 'nineteen', 'fifties'])])
    assert result['pass'] is True
    with pytest.raises(qc.AudioQCError, match='inconsistent word timestamps'):
        qc._require_word_timing_evidence(result, 'ElevenLabs')
