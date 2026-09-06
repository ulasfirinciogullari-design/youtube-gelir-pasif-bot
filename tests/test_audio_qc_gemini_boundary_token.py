"""Coarse evidence preserves zero-width tokens without inventing word times."""
from copy import deepcopy
from unittest.mock import Mock

import pytest

from test_audio_qc import audio_qc as qc, _Response


# Synthetic compact sentence; the three central intervals are the observed
# Costco boundary. The full immutable 60-row recording is tested separately.
ROWS = [
    ('Why', '0.100s', '0.400s'), ('pay', '0.400s', '0.700s'),
    ('before', '1.600s', '1.900s'), ('buying', '1.900s', '2.300s'),
    ('a', '2.300s', '2.300s'), ('single', '2.300s', '2.700s'),
    ('item?', '2.700s', '3.100s'),
]


# Exact retained Gemini3.5-transcribe annotation tuples for source
# eca5ba2c-cb23-5c73-984a-3ac793c7142a, audio SHA256
# d33a2a97fbe3acbda77382f80fd72a2fd4df37a5622e3297e3bba415424a9c83.
# No generated/interpolated timestamps are used in this regression fixture.
ACTUAL_ROWS = [
    ('Why', '0.100s', '0.400s'), ('pay', '0.400s', '0.700s'),
    ('an', '0.700s', '0.800s'), ('annual', '0.800s', '1.300s'),
    ('fee', '1.300s', '1.600s'), ('before', '1.600s', '1.900s'),
    ('buying', '1.900s', '2.300s'), ('a', '2.300s', '2.300s'),
    ('single', '2.300s', '2.700s'), ('item?', '2.700s', '3.100s'),
    ('Costco', '3.400s', '4s'), ('caps', '4s', '4.400s'),
    ('merchandise', '4.400s', '5s'), ('markups,', '5s', '5.600s'),
    ('keeping', '5.800s', '6.100s'), ('retail', '6.100s', '6.600s'),
    ('product', '6.600s', '7s'), ('prices', '7s', '7.500s'),
    ('exceptionally', '7.500s', '8.500s'), ('low.', '8.500s', '8.800s'),
    ('No', '9.200s', '9.500s'), ('frills', '9.500s', '9.900s'),
    ('buildings', '9.900s', '10.500s'), ('and', '10.500s', '10.700s'),
    ('rapid', '10.700s', '11s'), ('inventory', '11s', '11.600s'),
    ('turnover', '11.600s', '12.200s'), ('keep', '12.200s', '12.400s'),
    ('store', '12.400s', '12.900s'), ('overhead', '12.900s', '13.400s'),
    ('minimal.', '13.400s', '13.900s'), ('Upfront', '14.200s', '14.800s'),
    ('membership', '14.800s', '15.400s'), ('fees', '15.400s', '15.800s'),
    ('provide', '15.800s', '16.300s'), ('stable', '16.300s', '16.800s'),
    ('cash', '16.800s', '17.200s'), ('flow', '17.200s', '17.500s'),
    ('across', '17.500s', '18s'), ('the', '18s', '18.100s'),
    ('year.', '18.100s', '18.700s'), ('This', '18.900s', '19.200s'),
    ('reliable', '19.200s', '19.800s'), ('income', '19.800s', '20.300s'),
    ('cushions', '20.300s', '20.800s'), ('razor-thin', '20.800s', '21.600s'),
    ('margins', '21.600s', '22.200s'), ('on', '22.200s', '22.400s'),
    ('bulk', '22.400s', '22.700s'), ('merchandise.', '22.700s', '23.700s'),
    ('Members', '24s', '24.500s'), ('fund', '24.500s', '24.800s'),
    ('club', '24.800s', '25.200s'), ('access', '25.200s', '25.700s'),
    ('upfront', '25.800s', '26.400s'), ('to', '26.400s', '26.700s'),
    ('unlock', '26.700s', '27.100s'), ('genuine', '27.100s', '27.600s'),
    ('wholesale', '27.600s', '28.200s'), ('pricing.', '28.200s', '28.800s'),
]
ACTUAL_TRANSCRIPT = (
    'Why pay an annual fee before buying a single item? '
    'Costco caps merchandise markups, keeping retail product prices exceptionally low. '
    'No frills buildings and rapid inventory turnover keep store overhead minimal. '
    'Upfront membership fees provide stable cash flow across the year. '
    'This reliable income cushions razor-thin margins on bulk merchandise. '
    'Members fund club access upfront to unlock genuine wholesale pricing.'
)


def _envelope(rows=ROWS, text=None):
    return {'status': 'completed', 'steps': [{'type': 'model_output', 'content': [{
        'type': 'text', 'text': text or ' '.join(row[0] for row in rows),
        'annotations': [{'type': 'word_info', 'text': word, 'start_offset': start, 'end_offset': end}
                        for word, start, end in rows],
    }]}]}


def _compare(envelope=None, *, expected=None, provider='gemini'):
    payload = qc._gemini_interaction_payload(_Response(envelope or _envelope()))
    return qc._require_word_timing_evidence(qc.compare_transcript(
        expected or payload['text'], payload['text'], words=payload['words'],
        comparison_language='en', provider=provider,
    ), provider.title())


def _review(*, passed=False, phrase='buying a single'):
    return {'pass': passed, 'summary': 'Actual audible delivery still determines quality.',
        'scores': {'pronunciation': 90, 'naturalness': 90 if passed else 52,
                   'pacing': 90 if passed else 50, 'sentence_flow': 90 if passed else 48,
                   'emphasis': 90, 'roboticness': 10 if passed else 60},
        'issues': [] if passed else [{
            'code': 'choppy_phrase_grouping', 'phrase': phrase,
            'start_seconds': 2.0, 'end_seconds': 2.6,
            'detail': 'The phrase sounds segmented rather than naturally connected.',
        }]}


def test_isolated_boundary_keeps_every_raw_word_and_its_original_interval():
    envelope = _envelope()
    before = deepcopy(envelope)
    result = _compare(envelope)
    assert result['available'] is True and result['pass'] is True and result['score'] == 100
    assert result['mismatch_details']['timestamp_sequence_match'] is True
    assert result['word_timestamps'] == [
        {'text': word, 'start': float(start[:-1]), 'end': float(end[:-1])}
        for word, start, end in ROWS]
    assert result['coarse_timestamp_groups'] == [{
        'kind': 'coincident_boundary_token', 'boundary_token_index': 4,
        'word_indices': [3, 4, 5], 'text': 'buying a single', 'start': 1.9, 'end': 2.7,
    }]
    assert result['ending_word_time'] == 3.1
    assert result['word_timing_precision'] == 'mixed_word_and_coarse_group'
    assert envelope == before


def test_actual_60_annotations_preserve_all_words_intervals_and_real_final_word():
    envelope = _envelope(ACTUAL_ROWS, ACTUAL_TRANSCRIPT)
    before = deepcopy(envelope)
    result = _compare(envelope, expected=ACTUAL_TRANSCRIPT)
    assert result['pass'] is True and result['score'] == 100
    assert result['mismatch_details']['timestamp_sequence_match'] is True
    assert result['mismatch_details']['missing_words'] == []
    assert result['mismatch_details']['unexpected_words'] == []
    assert len(result['word_timestamps']) == 60
    assert result['word_timestamps'] == [
        {'text': word, 'start': float(start[:-1]), 'end': float(end[:-1])}
        for word, start, end in ACTUAL_ROWS]
    assert result['word_timestamps'][7] == {'text': 'a', 'start': 2.3, 'end': 2.3}
    assert result['coarse_timestamp_groups'] == [{
        'kind': 'coincident_boundary_token', 'boundary_token_index': 7,
        'word_indices': [6, 7, 8], 'text': 'buying a single', 'start': 1.9, 'end': 2.7,
    }]
    assert result['ending_word_time'] == 28.8
    assert result['word_timestamps'][-1] == {'text': 'pricing.', 'start': 28.2, 'end': 28.8}
    assert envelope == before


@pytest.mark.parametrize('index', [3, 4])
@pytest.mark.parametrize('punctuation', ['.', '?', '!', '…'])
def test_coarse_group_cannot_cross_an_annotated_sentence_boundary(index, punctuation):
    rows = list(ROWS)
    word, start, end = rows[index]
    rows[index] = (word + punctuation, start, end)
    with pytest.raises(qc.AudioQCError, match='invalid word annotations'):
        _compare(_envelope(rows))


@pytest.mark.parametrize('index,start,end', [
    (0, '0.100s', '0.100s'), (6, '2.700s', '2.700s'),  # No zero first/final.
    (3, '1.900s', '1.900s'), (5, '2.300s', '2.300s'),  # No adjacent/multiple zero.
    (3, '1.900s', '2.299s'), (5, '2.301s', '2.700s'),  # Exact shared boundary only.
    (5, '2.299s', '2.700s'), (4, '2.300s', '2.200s'),  # Overlap/reversal.
    (4, None, '2.300s'), (4, True, '2.300s'),
    (4, 'NaNs', '2.300s'), (4, '-2.300s', '2.300s'),
])
def test_other_invalid_timing_is_not_reclassified_as_a_boundary(index, start, end):
    rows = list(ROWS)
    rows[index] = (rows[index][0], start, end)
    with pytest.raises(qc.AudioQCError, match='invalid word annotations'):
        _compare(_envelope(rows))


def test_openai_and_elevenlabs_do_not_gain_the_gemini_exception():
    for provider in ('openai', 'elevenlabs'):
        with pytest.raises(qc.AudioQCError, match='incomplete word timestamps'):
            _compare(provider=provider)


def test_no_word_can_be_dropped_to_make_timing_complete():
    text = ' '.join(row[0] for row in ROWS)
    with pytest.raises(qc.AudioQCError, match='inconsistent word timestamps'):
        _compare(_envelope(ROWS[:4] + ROWS[5:], text))


def test_coarse_timing_does_not_approve_a_changed_or_missing_spoken_word():
    result = _compare(expected='Why pay before buying two single items?')
    assert result['pass'] is False
    assert result['score'] < 100
    assert result['mismatch_details']['timestamp_sequence_match'] is True
    assert qc._validated_prosody_timestamp_evidence(result, allow_coarse=True) is None


@pytest.mark.parametrize('mutation', ['missing', 'width', 'text', 'index', 'bool_index', 'precision', 'provider'])
def test_groups_are_recomputed_from_raw_rows_not_trusted_as_assertions(mutation):
    evidence = _compare()
    if mutation == 'missing':
        evidence.pop('coarse_timestamp_groups')
    elif mutation == 'precision':
        evidence['word_timing_precision'] = 'exact'
    elif mutation == 'provider':
        evidence['provider'] = 'openai'
    else:
        field, value = {'width': ('end', 2.8), 'text': ('text', 'buying two single'),
                        'index': ('boundary_token_index', 3),
                        'bool_index': ('word_indices', [3, True, 5])}[mutation]
        evidence['coarse_timestamp_groups'][0][field] = value
    assert qc._validated_prosody_timestamp_evidence(evidence, allow_coarse=True) is None
    with pytest.raises(qc.AudioQCError, match='incomplete word timestamps'):
        qc._require_word_timing_evidence(evidence, 'Gemini')


@pytest.mark.parametrize('phrase', ['buying a single', 'a', 'a single'])
def test_negative_prosody_is_retained_with_explicit_group_bounds(phrase):
    evidence = _compare()
    result = qc._validate_prosody_review(
        _review(phrase=phrase), evidence['transcript'], audio_duration_seconds=3.4,
        transcript_evidence=evidence, language='en',
    )
    assert result['available'] is True and result['pass'] is False
    assert result['issues'][0]['phrase'] == phrase
    assert result['issues'][0]['start_seconds'] == 1.9
    assert result['issues'][0]['end_seconds'] == 2.7
    assert result['timestamp_source'] == 'stt_word_timestamps_with_coarse_boundary_group'
    assert result['timestamp_precision'] == 'mixed_word_and_coarse_group'


def test_unrelated_or_absent_prosody_phrase_is_not_invented_from_a_group():
    evidence = _compare()
    assert qc._validate_prosody_review(
        _review(phrase='buying two items'), evidence['transcript'],
        audio_duration_seconds=3.4, transcript_evidence=evidence, language='en',
    ) is None


def test_default_timestamp_consumer_refuses_coarse_evidence_for_audio_cutting():
    evidence = _compare()
    assert qc._validated_prosody_timestamp_evidence(evidence) is None
    assert qc._validated_prosody_timestamp_evidence(evidence, allow_coarse=True) is not None


def test_normal_positive_word_timings_preserve_the_existing_contract():
    rows = list(ROWS)
    rows[4] = ('a', '2.300s', '2.400s')
    rows[5] = ('single', '2.400s', '2.700s')
    evidence = _compare(_envelope(rows))
    assert 'coarse_timestamp_groups' not in evidence
    assert qc._validated_prosody_timestamp_evidence(evidence) is not None


def test_actual_prosody_provider_still_runs_and_can_reject_the_audio(tmp_path, monkeypatch):
    evidence = _compare()
    audio = tmp_path / 'voice.mp3'
    audio.write_bytes(b'ID3mock-audio-only')
    generate = Mock(return_value=_review())
    monkeypatch.setattr(qc, 'generate_gemini_audio_json', generate)
    monkeypatch.setattr(qc.settings, 'gemini_api_key', 'mock-only-key')
    result = qc.verify_audio_prosody(
        audio, evidence['transcript'], audio_duration_seconds=3.4,
        transcript_evidence=evidence, language='en',
    )
    assert generate.call_count == 1
    assert result['available'] is True and result['pass'] is False


def test_real_gemini_route_keeps_the_actual_payload_and_does_not_invent_a_retry(tmp_path, monkeypatch):
    envelope = _envelope(ACTUAL_ROWS, ACTUAL_TRANSCRIPT)
    before = deepcopy(envelope)
    audio = tmp_path / 'voice.mp3'
    audio.write_bytes(b'ID3mock-audio-only')
    upload = Mock(return_value={'name': 'files/fixture', 'uri': 'https://fixture.invalid/audio', 'mime_type': 'audio/mpeg'})
    post = Mock(return_value=_Response(envelope))
    cleanup = Mock()
    monkeypatch.setattr(qc, '_upload_gemini_audio_file', upload)
    monkeypatch.setattr(qc.httpx, 'post', post)
    monkeypatch.setattr(qc, '_delete_gemini_file', cleanup)
    result = qc._verify_with_gemini(audio, ACTUAL_TRANSCRIPT, 'mock-only-key', qc._speech_language_codes('en'))
    assert result['pass'] is True and result['language_code'] == 'en-US'
    assert result['word_timestamps'][7]['start'] == result['word_timestamps'][7]['end'] == 2.3
    assert len(result['word_timestamps']) == 60
    assert post.call_count == 1
    cleanup.assert_called_once_with('files/fixture', 'mock-only-key')
    assert envelope == before


def test_positive_prosody_verdict_still_requires_unchanged_quality_scores():
    evidence = _compare()
    review = _review(passed=True)
    review['scores']['naturalness'] = 20
    assert qc._validate_prosody_review(
        review, evidence['transcript'], audio_duration_seconds=3.4,
        transcript_evidence=evidence, language='en',
    ) is None
