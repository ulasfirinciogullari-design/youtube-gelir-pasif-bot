"""Regression for the observed Coca-Cola ASR nineteen hundreds/1900s rejection."""
import pytest
from copy import deepcopy
import json
from pathlib import Path

from app.services import audio_qc as qc


@pytest.mark.parametrize('spoken,digits', [
    ('In the early nineteen hundreds', 'In the early 1900s'),
    ('During the eighteen-hundreds', 'During the 1800s'),
    ('Since seventeen hundreds', 'Since 1700s'),
    ('In the late nineteen-fifties', 'In the late 1950s'),
    ('During mid nineteen sixties', 'During mid 1960s'),
])
def test_complete_temporal_phrase_matches_without_losing_meaning(spoken, digits):
    for expected, heard in ((spoken, digits), (digits, spoken)):
        assert qc.compare_transcript(expected + ', bottles changed.', heard + ', bottles changed.',
                                     comparison_language='en')['pass'] is True


@pytest.mark.parametrize('heard', [
    'In the early 1800s, bottles changed.', 'In the early 1900, bottles changed.',
    'In the late 1900s, bottles changed.', 'In the 1900s, bottles changed.',
    'In the early 1900s, bottles disappeared.', 'In the early 19 00s, bottles changed.',
])
def test_wrong_dates_qualifiers_and_narration_still_fail(heard):
    assert qc.compare_transcript('In the early nineteen hundreds, bottles changed.', heard,
                                  comparison_language='en')['pass'] is False


@pytest.mark.parametrize('spoken', [
    'nineteen hundreds', 'They counted nineteen hundreds', 'In the nineteen, hundreds',
    'In the nineteen / hundreds', 'In, the early nineteen hundreds',
    'In the early, nineteen hundreds', 'In the nineteen hundreds percent',
    'In the nineteen hundreds thousand', 'In the nineteen hundreds 2',
])
def test_counts_and_ambiguous_or_malformed_runs_are_not_reinterpreted(spoken):
    heard = spoken.replace('nineteen hundreds', '1900s').replace('nineteen, hundreds', '1900s').replace('nineteen / hundreds', '1900s')
    assert qc.compare_transcript(spoken, heard, comparison_language='en')['pass'] is False


def test_equivalent_text_does_not_invent_provider_timing_evidence():
    result = qc.compare_transcript('In the early nineteen hundreds', 'In the early 1900s',
        provider='elevenlabs', comparison_language='en', words=[
            {'text': t, 'start': i, 'end': i + .5} for i, t in enumerate(
                ['In', 'the', 'early', 'nineteen', 'hundreds'])])
    assert result['pass'] is True
    with pytest.raises(qc.AudioQCError, match='inconsistent word timestamps'):
        qc._require_word_timing_evidence(result, 'ElevenLabs')


def test_actual_three_takes_pass_spelling_comparison_but_keep_timestamp_requirements():
    data = json.loads((Path(__file__).parent / 'fixtures/coca_cola_century_audio_observations.json').read_text())
    before = deepcopy(data)
    assert len(data['observations']) == 6
    for row in data['observations']:
        payload = row['payload']
        result = qc.compare_transcript(data['expected'], payload['text'], words=payload['words'],
            language_code=payload.get('language_code', payload.get('language')),
            provider=row['provider'], comparison_language='en')
        assert result['pass'] is True and result['score'] == 100
        if row['provider'] == 'openai':
            with pytest.raises(qc.AudioQCError, match='incomplete word timestamps'):
                qc._require_word_timing_evidence(result, 'OpenAI')
        else:
            assert qc._require_word_timing_evidence(result, 'ElevenLabs')['pass'] is True
    assert data == before
