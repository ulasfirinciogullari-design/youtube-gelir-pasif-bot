"""Actual retained IKEA STT evidence; parsing never invents timing/approval."""
from copy import deepcopy
from unittest.mock import Mock

import pytest

from test_audio_qc import audio_qc as qc, _Response


TRANSCRIPT = (
    'Shipping built wooden tables across Sweden broke legs and wasted space. '
    'In 1953, IKEA cataloged its first self-assembly table. '
    'Workers unscrewed the table legs to pack everything flat. '
    'A bulky table instantly compressed into one slim cardboard box. '
    'Freight trucks suddenly carried vastly more inventory on every route. '
    'Shoppers assembled the pieces at home, slashing shipping costs.'
)
EXPECTED = TRANSCRIPT.replace('In 1953,', 'In nineteen fifty-three,')
# Retained Gemini evidence for source834df410-4790-5d77-8d5f-d9f9f88e4afb.
# Evidence SHA256:0f39464f10d6c3a315a249a751a26d45bcea5e933bed690a9741460a89e50c58.
# These are the original57 text/time tuples, not interpolated test timings.
ACTUAL_WORDS = [
    ('Shipping', '0s', '0.600s'), ('built', '0.600s', '0.900s'),
    ('wooden', '0.900s', '1.200s'), ('tables', '1.200s', '1.700s'),
    ('across', '1.700s', '2.100s'), ('Sweden', '2.100s', '2.600s'),
    ('broke', '2.800s', '3.200s'), ('legs', '3.200s', '3.600s'),
    ('and', '3.700s', '3.900s'), ('wasted', '3.900s', '4.200s'),
    ('space.', '4.200s', '4.800s'), ('In', '5.200s', '5.400s'),
    ('1953,', '5.400s', '6.800s'), ('IKEA', '7.200s', '7.700s'),
    ('cataloged', '7.700s', '8.400s'), ('its', '8.400s', '8.600s'),
    ('first', '8.600s', '9.200s'), ('self-assembly', '9.200s', '9.900s'),
    ('table.', '9.900s', '10.400s'), ('Workers', '10.800s', '11.400s'),
    ('unscrewed', '11.500s', '12.100s'), ('the', '12.100s', '12.200s'),
    ('table', '12.200s', '12.600s'), ('legs', '12.600s', '13.200s'),
    ('to', '13.300s', '13.400s'), ('pack', '13.400s', '13.700s'),
    ('everything', '13.700s', '14.200s'), ('flat.', '14.200s', '14.800s'),
    ('A', '15.100s', '15.200s'), ('bulky', '15.200s', '15.700s'),
    ('table', '15.700s', '16.200s'), ('instantly', '16.300s', '16.900s'),
    ('compressed', '16.900s', '17.500s'), ('into', '17.500s', '17.900s'),
    ('one', '17.900s', '18.300s'), ('slim', '18.300s', '18.700s'),
    ('cardboard', '18.700s', '19.300s'), ('box.', '19.300s', '19.900s'),
    ('Freight', '20.100s', '20.500s'), ('trucks', '20.500s', '20.900s'),
    ('suddenly', '20.900s', '21.400s'), ('carried', '21.400s', '21.800s'),
    ('vastly', '21.800s', '22.300s'), ('more', '22.300s', '22.600s'),
    ('inventory', '22.600s', '23.100s'), ('on', '23.100s', '23.400s'),
    ('every', '23.400s', '23.700s'), ('route.', '23.700s', '24.200s'),
    ('Shoppers', '24.600s', '25.100s'), ('assembled', '25.100s', '25.600s'),
    ('the', '25.600s', '25.700s'), ('pieces', '25.700s', '26.100s'),
    ('at', '26.100s', '26.300s'), ('home,', '26.300s', '26.700s'),
    ('slashing', '26.900s', '27.400s'), ('shipping', '27.400s', '27.900s'),
    ('costs.', '27.900s', '28.600s'),
]


def _envelope():
    return {'status': 'completed', 'steps': [{'type': 'model_output', 'content': [{
        'type': 'text', 'text': TRANSCRIPT,
        'annotations': [{'type': 'word_info', 'text': text, 'start_offset': start, 'end_offset': end}
                        for text, start, end in ACTUAL_WORDS],
    }]}]}


def _compare_payload(envelope, expected=EXPECTED):
    payload = qc._gemini_interaction_payload(_Response(envelope))
    return qc._require_word_timing_evidence(qc.compare_transcript(
        expected, payload['text'], words=payload['words'], comparison_language='en', provider='gemini',
    ), 'Gemini')


def test_actual_57_annotations_parse_and_match_without_changing_a_single_interval():
    envelope = _envelope()
    before = deepcopy(envelope)
    result = _compare_payload(envelope)
    assert result['available'] is True and result['pass'] is True and result['score'] == 100
    assert result['mismatch_details']['exact_match'] is True
    assert result['mismatch_details']['timestamp_sequence_match'] is True
    assert result['word_timestamps'] == [
        {'text': text, 'start': float(start[:-1]), 'end': float(end[:-1])}
        for text, start, end in ACTUAL_WORDS
    ]
    assert len(result['word_timestamps']) == 57 and result['ending_word_time'] == 28.6
    assert result['word_timestamps'][17] == {'text': 'self-assembly', 'start': 9.2, 'end': 9.9}
    assert envelope == before


def test_real_gemini_provider_route_uses_english_and_preserves_actual_payload(tmp_path, monkeypatch):
    audio = tmp_path / 'existing.mp3'
    audio.write_bytes(b'ID3local-test-only')
    upload = Mock(return_value={'name': 'files/fixture', 'uri': 'https://fixture.invalid/audio', 'mime_type': 'audio/mpeg'})
    post = Mock(return_value=_Response(_envelope()))
    cleanup = Mock()
    monkeypatch.setattr(qc, '_upload_gemini_audio_file', upload)
    monkeypatch.setattr(qc.httpx, 'post', post)
    monkeypatch.setattr(qc, '_delete_gemini_file', cleanup)
    result = qc._verify_with_gemini(audio, EXPECTED, 'test-only-key', qc._speech_language_codes('en'))
    assert result['pass'] is True and result['score'] == 100 and result['language_code'] == 'en-US'
    assert post.call_count == 1
    assert post.call_args.kwargs['json']['generation_config']['transcription_config']['language_codes'] == ['en-US']
    assert len(result['word_timestamps']) == 57
    cleanup.assert_called_once_with('files/fixture', 'test-only-key')


@pytest.mark.parametrize('word', ['self-assembly', 'self-assembly.', 'flat-pack', 'mail-order',
                                 'state-of-the-art', 'nineteen-fifty-three', 'self\u2011assembly'])
def test_single_lexical_compound_does_not_need_fake_subword_timings(word):
    assert qc._valid_gemini_annotation_text(word) is True


@pytest.mark.parametrize('word', ['self assembly', 'self--assembly', '-assembly', 'assembly-',
                                 'self - assembly', '19-53', '1953-1954', '4+8', '4/8', '4:8',
                                 '4%8', 'self-53', '53-assembly', 'self-assembly..', 'self,assembly'])
def test_phrases_ranges_and_operators_are_still_invalid_word_annotations(word):
    assert qc._valid_gemini_annotation_text(word) is False


@pytest.mark.parametrize('spoken,digits', [
    ('nineteen fifty-three', '1953'), ('nineteen fifty three', '1953'),
    ('nineteen thirty-three', '1933'), ('eighteen sixty-five', '1865'),
    ('nineteen ninety-nine', '1999'), ('twenty twenty-six', '2026'),
    ('nineteen oh five', '1905'), ('nineteen hundred', '1900'),
])
def test_exact_english_year_readings_match_the_same_year(spoken, digits):
    result = qc.compare_transcript(f'In {spoken}, it opened.', f'In {digits}, it opened.', comparison_language='en')
    assert result['pass'] is True and result['score'] == 100
    reverse = qc.compare_transcript(f'In {digits}, it opened.', f'In {spoken}, it opened.', comparison_language='en')
    assert reverse['pass'] is True


@pytest.mark.parametrize('heard', ['In 1954, it opened.', 'In 1935, it opened.',
                                  'In 19 53, it opened.', 'In 19-53, it opened.',
                                  'In 19/53, it opened.', 'In 19.53, it opened.',
                                  'In 1953%, it opened.', 'In -1953, it opened.',
                                  'In 1953, it closed.', 'It opened in 1953.'])
def test_changed_year_number_boundaries_words_or_order_still_fail(heard):
    result = qc.compare_transcript('In nineteen fifty-three, it opened.', heard, comparison_language='en')
    assert result['pass'] is False


@pytest.mark.parametrize('spoken', ['nineteen, fifty-three', 'nineteen / fifty-three',
                                   'nineteen: fifty-three', 'nineteen. Fifty-three',
                                   'fifty-three nineteen', 'nineteen three fifty',
                                   'nineteen fifty-three thousand', 'nineteen fifty-three three'])
def test_number_lists_operators_or_malformed_runs_are_not_guessed_as_a_year(spoken):
    assert qc.compare_transcript(f'In {spoken}, it opened.', 'In 1953, it opened.', comparison_language='en')['pass'] is False


@pytest.mark.parametrize('expected,heard', [
    ('It cost nineteen fifty-three dollars.', 'It cost 1953 dollars.'),
    ('nineteen fifty-three', '1953'), ('twenty nine', '29'),
    ('Turn it on', 'Turn it 10'), ('In nineteen fifty-three percent', 'In 1953 percent'),
])
def test_year_normalization_does_not_guess_prices_or_use_turkish_number_words(expected, heard):
    assert qc.compare_transcript(expected, heard, comparison_language='en')['pass'] is False


def test_actual_payload_changed_year_is_a_semantic_failure_not_a_timing_approval():
    envelope = _envelope()
    content = envelope['steps'][0]['content'][0]
    content['text'] = TRANSCRIPT.replace('1953', '1954')
    content['annotations'][12]['text'] = '1954,'
    result = _compare_payload(envelope)
    assert result['pass'] is False and result['score'] < 100
    assert result['mismatch_details']['timestamp_sequence_match'] is True
    assert 'nineteen' in ' '.join(result['mismatch_details']['missing_words'])


def test_matching_year_does_not_hide_reordered_words_in_actual_full_story():
    envelope = _envelope()
    content = envelope['steps'][0]['content'][0]
    content['text'] = TRANSCRIPT.replace('built wooden', 'wooden built')
    content['annotations'][1]['text'], content['annotations'][2]['text'] = 'wooden', 'built'
    result = _compare_payload(envelope)
    assert result['pass'] is False and result['mismatch_details']['timestamp_sequence_match'] is True


def test_word_timing_completeness_does_not_gain_number_word_equivalence():
    envelope = _envelope()
    content = envelope['steps'][0]['content'][0]
    content['annotations'][12]['text'] = 'nineteen-fifty-three'
    with pytest.raises(qc.AudioQCError, match='inconsistent word timestamps'):
        _compare_payload(envelope)


@pytest.mark.parametrize('text,start,end,left,right', [
    ('wasted', 4.21999979019165, 4.21999979019165,
     {'word': 'and', 'start': 3.5399999618530273, 'end': 4.21999979019165},
     {'word': 'space', 'start': 4.21999979019165, 'end': 4.78000020980835}),
    ('Workers', 11.34000015258789, 11.34000015258789,
     {'word': 'table', 'start': 10.0, 'end': 10.34000015258789},
     {'word': 'unscrewed', 'start': 11.34000015258789, 'end': 12.15999984741211}),
])
def test_actual_openai_zero_duration_words_still_reject_without_inventing_intervals(text, start, end, left, right):
    words = [left, {'word': text, 'start': start, 'end': end}, right]
    before = deepcopy(words)
    transcript = ' '.join(item['word'] for item in words)
    result = qc.compare_transcript(transcript, transcript, words=words, provider='openai', comparison_language='en')
    with pytest.raises(qc.AudioQCError, match='incomplete word timestamps'):
        qc._require_word_timing_evidence(result, 'OpenAI')
    assert words == before


def _prosody_output(*, phrase=None, code='choppy_phrase_grouping'):
    return {
        'pass': phrase is None, 'summary': 'Natural delivery.' if phrase is None else 'Audibly broken phrase.',
        'scores': {'pronunciation': 90, 'naturalness': 90, 'pacing': 90,
                   'sentence_flow': 90, 'emphasis': 90, 'roboticness': 10},
        'issues': [] if phrase is None else [{
            'code': code, 'start_seconds': 5.3, 'end_seconds': 6.7,
            'phrase': phrase, 'detail': 'The phrase audibly breaks apart.',
        }],
    }


@pytest.fixture
def english_prosody(tmp_path, monkeypatch):
    audio = tmp_path / 'english.mp3'
    audio.write_bytes(b'ID3local-prosody-test-only')
    generate = Mock(return_value=_prosody_output())
    monkeypatch.setattr(qc, 'generate_gemini_audio_json', generate)
    monkeypatch.setattr(qc.settings, 'gemini_api_key', 'local-test-key')
    evidence = _compare_payload(_envelope())
    return audio, generate, evidence


def test_english_prosody_uses_english_rubric_same_schema_scores_and_single_retry_budget(english_prosody):
    audio, generate, evidence = english_prosody
    result = qc.verify_audio_prosody(audio, EXPECTED, language='en-US',
                                    audio_duration_seconds=30.0, transcript_evidence=evidence)
    assert result['pass'] is True and result['available'] is True and result['review_attempts'] == 1
    assert generate.call_count == 1
    request = generate.call_args
    assert 'expected English text' in request.args[2] and 'Turkish' not in request.args[2]
    assert '<UNTRUSTED_EXPECTED_NARRATION>' in request.args[2]
    assert EXPECTED in request.args[2]
    assert request.kwargs['system_instruction'] == qc._PROSODY_SYSTEM_INSTRUCTION.replace('Turkish', 'English')
    assert 'native-English' in request.kwargs['system_instruction']
    assert request.kwargs['json_schema'] is qc._PROSODY_REVIEW_SCHEMA
    assert request.kwargs['retry_once'] is False


@pytest.mark.parametrize('phrase', ['In nineteen fifty-three', 'In 1953'])
def test_english_bad_year_delivery_binds_to_actual_unsplit_year_interval(english_prosody, phrase):
    audio, generate, evidence = english_prosody
    before = deepcopy(evidence)
    generate.return_value = _prosody_output(phrase=phrase)
    result = qc.verify_audio_prosody(audio, EXPECTED, language='en',
                                    audio_duration_seconds=30.0, transcript_evidence=evidence)
    assert result['available'] is True and result['pass'] is False
    assert result['reason'] == 'choppy_phrase_grouping' and result['review_attempts'] == 1
    assert result['issues'][0]['start_seconds'] == 5.2 and result['issues'][0]['end_seconds'] == 6.8
    assert result['timestamp_source'] == 'stt_word_timestamps' and result['timestamp_provider'] == 'gemini'
    assert generate.call_count == 1 and evidence == before


@pytest.mark.parametrize('phrase', ['In nineteen fifty-four', 'In 1954', 'In fifty nineteen-three',
                                 'In nineteen, fifty-three', 'In nineteen / fifty-three',
                                 'nineteen fifty-three'])
def test_english_wrong_or_unanchored_year_issue_fails_closed_after_two_reviews(english_prosody, phrase):
    audio, generate, evidence = english_prosody
    generate.return_value = _prosody_output(phrase=phrase)
    result = qc.verify_audio_prosody(audio, EXPECTED, language='en',
                                    audio_duration_seconds=30.0, transcript_evidence=evidence)
    assert result['available'] is False and result['pass'] is False
    assert result['reason'] == 'gemini_prosody_protocol_invalid' and result['review_attempts'] == 2
    assert generate.call_count == 2
    assert all(call.kwargs['retry_once'] is False for call in generate.call_args_list)


def test_english_compound_issue_keeps_original_single_word_time(english_prosody):
    audio, generate, evidence = english_prosody
    generate.return_value = _prosody_output(phrase='self-assembly', code='mispronunciation')
    result = qc.verify_audio_prosody(audio, EXPECTED, language='en',
                                    audio_duration_seconds=30.0, transcript_evidence=evidence)
    assert result['available'] is True and result['pass'] is False
    assert result['issues'][0]['start_seconds'] == 9.2 and result['issues'][0]['end_seconds'] == 9.9


@pytest.mark.parametrize('left,right', [('on', '10'), ('the rapist', 'therapist'),
                                     ('a part', 'apart'), ('nineteen fifty-three', '1953'),
                                     ('IKEA cataloged', 'IKEA catalogues')])
def test_english_issue_identity_has_no_turkish_number_or_fuzzy_boundary_fallback(left, right):
    assert qc._prosody_phrases_equivalent(left, right, 'en') is False


@pytest.mark.parametrize('field,bad', [('pronunciation', 69), ('naturalness', 69), ('pacing', 69),
                                    ('sentence_flow', 69), ('emphasis', 69), ('roboticness', 31)])
def test_english_positive_verdict_cannot_bypass_existing_score_consistency(english_prosody, field, bad):
    audio, generate, evidence = english_prosody
    output = _prosody_output()
    output['scores'][field] = bad
    generate.return_value = output
    result = qc.verify_audio_prosody(audio, EXPECTED, language='en', transcript_evidence=evidence)
    assert result['available'] is False and result['pass'] is False
    assert result['review_attempts'] == generate.call_count == 2


@pytest.mark.parametrize('language', ['de', 'es', 'ar'])
def test_untested_prosody_languages_fail_before_provider_call(english_prosody, language):
    audio, generate, evidence = english_prosody
    with pytest.raises(ValueError, match='prosody language is unsupported'):
        qc.verify_audio_prosody(audio, EXPECTED, language=language, transcript_evidence=evidence)
    generate.assert_not_called()


def test_default_turkish_prosody_rubric_and_number_binding_stay_unchanged(english_prosody):
    audio, generate, _evidence = english_prosody
    result = qc.verify_audio_prosody(audio, 'On işçi çalışıyor.')
    assert result['pass'] is True
    assert generate.call_args.kwargs['system_instruction'] == qc._PROSODY_SYSTEM_INSTRUCTION
    assert 'expected Turkish text' in generate.call_args.args[2]
    assert qc._prosody_phrases_equivalent('on', '10') is True
