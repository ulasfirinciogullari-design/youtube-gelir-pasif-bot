"""Explicit percentage formatting does not manufacture word timings or meaning."""
from copy import deepcopy

import httpx
import pytest

from app.services import audio_qc as qc


def response(text='Kağıdı %75 pamuk ve %25 keten içerir.',
             rows=None):
    # The %75/%25 offsets are actual stored long-form provider observations.
    rows = rows if rows is not None else [
        ('Kağıdı', '16s', '16.600s'),
        ('%75', '16.600s', '17.700s'),
        ('pamuk', '17.700s', '18.200s'),
        ('ve', '18.200s', '18.600s'),
        ('%25', '18.600s', '19.700s'),
        ('keten', '19.700s', '20.100s'),
        ('içerir.', '20.100s', '20.600s'),
    ]
    return httpx.Response(200, json={'status': 'completed', 'steps': [{
        'type': 'model_output', 'content': [{'type': 'text', 'text': text,
            'annotations': [{'type': 'word_info', 'text': word,
                'start_offset': start, 'end_offset': end} for word, start, end in rows]}]}]})


@pytest.mark.parametrize('text', ['%0', '%25', '%29', '%75', '%100', '%999', '%75,', '%25.'])
def test_unsigned_prefix_percentage_is_one_timed_annotation(text):
    assert qc._valid_gemini_annotation_text(text) is True


@pytest.mark.parametrize('text', [
    '%', '75%', '% 75', '%75 25', '%75 pamuk', '%75%25', '%%75',
    '%075', '%00', '%1000', '%-75', '%+75', '%−75', '%±75',
    '%75/25', '%75-25', '%75×25', '%75:25', '%75.25', '%75,25',
    '%75e2', '%75i', "%75'i", '%75\n25', '%75..', '%75 ,',
])
def test_unobserved_signs_phrases_ranges_operators_and_suffixes_stay_invalid(text):
    assert qc._valid_gemini_annotation_text(text) is False


def test_source_offsets_text_and_single_interval_are_preserved_without_input_mutation():
    source = response()
    original = deepcopy(source.json())
    parsed = qc._gemini_interaction_payload(source)
    assert source.json() == original
    assert parsed['text'] == original['steps'][0]['content'][0]['text']
    assert len(parsed['words']) == 7
    assert parsed['words'][1] == {'word': '%75', 'start': 16.6, 'end': 17.7}
    assert parsed['words'][4] == {'word': '%25', 'start': 18.6, 'end': 19.7}
    comparison = qc.compare_transcript(
        'Kağıdı yüzde yetmiş beş pamuk ve yüzde yirmi beş keten içerir.',
        parsed['text'], words=parsed['words'], provider='gemini', comparison_language='tr')
    assert comparison['pass'] is True and comparison['score'] == 100
    assert comparison['mismatch_details']['timestamp_sequence_match'] is True
    assert comparison['word_timestamps'][1] == {'text': '%75', 'start': 16.6, 'end': 17.7}
    assert 'coarse_timestamp_groups' not in comparison
    assert qc._require_word_timing_evidence(comparison, 'gemini') is comparison


@pytest.mark.parametrize('heard', [
    'Kağıdı %25 pamuk ve %75 keten içerir.',
    'Kağıdı %74 pamuk ve %25 keten içerir.',
    'Kağıdı %75 pamuk ve %24 keten içerir.',
    'Kağıdı %75 pamuk ve %25 keten içermez.',
    'Kağıdı %75 pamuk ve %25 keten içerir değil.',
    'Kağıdı 75 pamuk ve 25 keten içerir.',
    'Kağıdı %-75 pamuk ve %25 keten içerir.',
])
def test_different_amount_unit_or_negation_is_not_equivalent(heard):
    result = qc.compare_transcript(
        'Kağıdı yüzde yetmiş beş pamuk ve yüzde yirmi beş keten içerir.', heard,
        provider='gemini', comparison_language='tr')
    assert result['pass'] is False


def test_correct_transcript_cannot_cover_changed_annotation_number():
    envelope = response().json()
    envelope['steps'][0]['content'][0]['annotations'][1]['text'] = '%74'
    parsed = qc._gemini_interaction_payload(httpx.Response(200, json=envelope))
    result = qc.compare_transcript(parsed['text'], parsed['text'],
        words=parsed['words'], provider='gemini', comparison_language='tr')
    assert result['pass'] is True
    assert result['mismatch_details']['timestamp_sequence_match'] is False
    with pytest.raises(qc.AudioQCError, match='inconsistent word timestamps'):
        qc._require_word_timing_evidence(result, 'gemini')


@pytest.mark.parametrize('start,end', [('17s', '16s'), ('bad', '17s'), ('16s', '16s')])
def test_percentage_cannot_launder_invalid_or_zero_duration(start, end):
    with pytest.raises(qc.AudioQCError):
        qc._gemini_interaction_payload(response('%75', [('%75', start, end)]))


def test_whisper_zero_width_rows_remain_rejected_without_fabricated_widths():
    words = [{'word': 'mavi', 'start': 37.44, 'end': 37.44},
             {'word': 'güvenlik', 'start': 37.44, 'end': 38.10}]
    before = deepcopy(words)
    result = qc.compare_transcript('mavi güvenlik', 'mavi güvenlik', words=words, provider='openai')
    assert result['pass'] is True and words == before
    with pytest.raises(qc.AudioQCError, match='incomplete word timestamps'):
        qc._require_word_timing_evidence(result, 'openai')


def test_real_remaining_lexical_mismatch_does_not_turn_into_a_pass():
    result = qc.compare_transcript('Bir dahaki sefere yüzeyine bak.',
                                  'Bir sonraki sefere yüzeyine bak.', provider='gemini')
    assert result['pass'] is False
    assert result['mismatch_details']['missing_words'] == ['dahaki']
    assert result['mismatch_details']['unexpected_words'] == ['sonraki']
