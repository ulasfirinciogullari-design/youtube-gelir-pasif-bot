"""Reviewer attribution cannot weaken the shared audible prosody validator."""
from copy import deepcopy

import pytest

from test_audio_qc import audio_qc as qc


EXPECTED = 'Yirmi dokuz kişi geldi.'


def _output():
    return {'pass': True, 'summary': 'Clear audible delivery.',
        'scores': {'pronunciation': 70, 'naturalness': 70, 'pacing': 70,
                   'sentence_flow': 70, 'emphasis': 70, 'roboticness': 30},
        'issues': []}


def _evidence():
    return qc._require_word_timing_evidence(qc.compare_transcript(
        EXPECTED, '29 kişi geldi.', provider='abacus_router', words=[
            {'word': '29', 'start': .1, 'end': .8},
            {'word': 'kişi', 'start': .8, 'end': 1.2},
            {'word': 'geldi.', 'start': 1.2, 'end': 1.8},
        ]), 'Abacus Router')


def _validate(output, **options):
    return qc._validate_prosody_review(output, EXPECTED,
        audio_duration_seconds=2.0, transcript_evidence=_evidence(), **options)


def test_default_native_result_is_unchanged_and_router_only_changes_attribution():
    output = _output()
    before = deepcopy(output)
    expected = {'available': True, 'pass': True, 'provider': 'gemini',
        'reason': None, 'scores': output['scores'], 'issues': [],
        'summary': output['summary'], 'timestamp_source': None,
        'timestamp_provider': None}
    assert _validate(output) == expected
    assert _validate(output, provider='gemini') == expected
    assert _validate(output, provider='abacus_router') == {
        **expected, 'provider': 'abacus_router'}
    assert output == before
    assert not {'qa_approved', 'publish_eligible', 'reservation', 'authorization'}.intersection(expected)


class _ProviderString(str):
    pass


@pytest.mark.parametrize('provider', [
    None, True, 1, b'gemini', [], {}, '', 'openai', 'abacus', 'route-llm',
    'Gemini', ' abacus_router', 'abacus_router ', _ProviderString('gemini'),
])
def test_invalid_provider_is_rejected_without_coercion(provider):
    assert _validate(_output(), provider=provider) is None


@pytest.mark.parametrize('field', qc._PROSODY_SCORE_FIELDS)
def test_router_keeps_every_existing_positive_verdict_score_bound(field):
    output = _output()
    output['scores'][field] = 31 if field == 'roboticness' else 69
    assert _validate(output, provider='abacus_router') is None


def _rejected_output():
    output = _output()
    output.update({'pass': False, 'issues': [{
        'code': 'mispronunciation', 'start_seconds': .2, 'end_seconds': .7,
        'phrase': 'yirmi dokuz', 'detail': 'The number is not clearly pronounced.',
    }]})
    return output


def test_router_negative_verdict_keeps_numeric_phrase_and_real_timestamp_binding():
    output = _rejected_output()
    evidence = _evidence()
    before = deepcopy((output, evidence))
    result = qc._validate_prosody_review(output, EXPECTED,
        audio_duration_seconds=2.0, transcript_evidence=evidence, provider='abacus_router')
    assert result['available'] is True and result['pass'] is False
    assert result['provider'] == result['timestamp_provider'] == 'abacus_router'
    assert result['timestamp_source'] == 'stt_word_timestamps'
    assert result['issues'] == [{**output['issues'][0], 'start_seconds': .1, 'end_seconds': .8}]
    assert result['scores'] == _output()['scores']  # Passing scores cannot reverse an audible rejection.
    assert (output, evidence) == before


@pytest.mark.parametrize('patch', [
    {'phrase': 'otuz'}, {'end_seconds': 2.3}, {'end_seconds': .1},
    {'code': 'unsupported_reason'}, {'start_seconds': float('nan')},
])
def test_router_rejects_ungrounded_or_malformed_issues(patch):
    output = _rejected_output()
    output['issues'][0].update(patch)
    assert _validate(output, provider='abacus_router') is None


def test_router_issue_still_requires_passing_transcript_timing_evidence():
    for evidence in (None, {}, {**_evidence(), 'pass': False}):
        assert qc._validate_prosody_review(_rejected_output(), EXPECTED,
            audio_duration_seconds=2.0, transcript_evidence=evidence,
            provider='abacus_router') is None
