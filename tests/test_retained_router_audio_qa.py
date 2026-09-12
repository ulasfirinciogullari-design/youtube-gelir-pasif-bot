"""Actual HTTPX observer evidence and unchanged local QA, without a sender."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from unittest.mock import Mock

import httpx
import pytest

from app.services import abacus_router_audio_adapter as adapter, audio_qc
from app.services import retained_router_audio_qa as bridge
from test_abacus_router_audio_adapter import (
    ASR, EXPECTED, KEY, PROSODY, envelope, mp3, prepare, prosody, response,
)


POSITIVE = {'pass': True, 'summary': 'Clear and natural delivery.',
    'scores': {'pronunciation': 80, 'naturalness': 80, 'pacing': 80,
               'sentence_flow': 80, 'emphasis': 80, 'roboticness': 20}, 'issues': []}


@pytest.fixture(autouse=True)
def no_sender_or_native_fallback(monkeypatch):
    forbidden = Mock(side_effect=AssertionError('Pure QA must not send or fall back.'))
    for name in ('post', 'request', 'stream', 'get', 'Client', 'AsyncClient'):
        monkeypatch.setattr(httpx, name, forbidden)
    monkeypatch.setattr(audio_qc, 'verify_audio_narration', forbidden)
    monkeypatch.setattr(audio_qc, 'verify_audio_prosody', forbidden)
    yield
    forbidden.assert_not_called()


def observed(prepared, output=None, **patch):
    actual = response(prepared, payload=envelope(output, **patch))
    return actual, adapter.observe_audio_router_response(prepared, actual)


def asr_arguments(output=None, **changes):
    prepared = prepare()
    actual, observation = observed(prepared, output)
    return {'prepared': prepared, 'response': actual, 'observed': observation,
            'expected_narration': EXPECTED, 'original_audio': prepared.audio, **changes}


def prosody_arguments(output=None, **changes):
    first = asr_arguments()
    prepared = prosody()
    actual, observation = observed(prepared, POSITIVE if output is None else output)
    return {'prepared': prepared, 'response': actual, 'observed': observation,
            'asr_prepared': first['prepared'], 'asr_response': first['response'],
            'asr_observed': first['observed'], 'expected_narration': EXPECTED,
            'original_audio': prepared.audio, **changes}


def assert_diagnostic(report):
    assert report['diagnostic_only'] is True
    for name in ('qa_approved', 'publish_eligible', 'full_qa_complete',
                 'edit_duration_qa_complete', 'journal_acknowledged'):
        assert report[name] is False
    assert report['provider'] == 'abacus_router'
    assert report['observation']['underlying_model_verified'] is False
    assert KEY not in json.dumps(report, ensure_ascii=False)


def test_real_observer_transcript_and_same_audio_full_prosody_remain_diagnostic():
    first = asr_arguments()
    report = bridge.validate_retained_router_asr(**first)
    assert_diagnostic(report)
    comparison = report['comparison']
    assert report['component_pass'] is comparison['pass'] is True
    assert comparison['score'] == 100 and comparison['mismatch_details']['exact_match'] is True
    assert comparison['mismatch_details']['timestamp_sequence_match'] is True
    assert report['word_timing_consistent'] is True
    assert report['audio_duration_seconds'] == first['prepared'].audio['decoded_samples'] / 48000
    assert report['expected_narration_sha256'] == hashlib.sha256(EXPECTED.encode()).hexdigest()
    assert report['observation'] == first['observed'].evidence
    assert EXPECTED not in json.dumps(first['prepared'].payload, ensure_ascii=False)

    second = prosody_arguments(asr_prepared=first['prepared'], asr_response=first['response'],
                               asr_observed=first['observed'])
    result = bridge.validate_retained_router_prosody(**second)
    assert_diagnostic(result)
    assert result['component_pass'] is result['prosody']['pass'] is True
    assert result['prosody']['provider'] == 'abacus_router' and result['prosody']['review_attempts'] == 1
    assert result['asr_comparison'] == comparison
    assert result['asr_binding'] == {
        'request_sha256': report['observation']['request_sha256'],
        'parsed_result_sha256': report['observation']['parsed_result_sha256'],
        'response_proof_sha256': report['observation']['response_proof_sha256'],
        'comparison_sha256': report['comparison_sha256']}
    assert result['schema_sha256'] == hashlib.sha256(adapter._canonical(audio_qc._PROSODY_REVIEW_SCHEMA)).hexdigest()
    assert result['rubric_sha256'] == hashlib.sha256(audio_qc._PROSODY_SYSTEM_INSTRUCTION.encode()).hexdigest()
    result['asr_comparison']['pass'] = False
    result['observation']['audio']['sha256'] = 'a' * 64
    assert first['observed'].result == ASR and first['observed'].evidence['audio'] == first['prepared'].audio


def test_valid_negative_prosody_keeps_real_phrase_timing_and_verdict():
    result = bridge.validate_retained_router_prosody(**prosody_arguments(PROSODY))
    assert_diagnostic(result)
    assert result['component_pass'] is result['prosody']['pass'] is False
    assert result['prosody']['timestamp_provider'] == 'abacus_router'
    assert result['prosody']['issues'][0]['phrase'] == 'dünya'
    assert result['prosody']['issues'][0]['start_seconds'] == .4
    assert result['prosody']['issues'][0]['end_seconds'] == .9


def test_wrong_heard_number_is_negative_and_cannot_supply_prosody_evidence():
    wrong = {'text': 'Yüzde beş.', 'language': 'tr', 'words': [
        {'word': 'Yüzde', 'start': .05, 'end': .4}, {'word': 'beş.', 'start': .4, 'end': .9}]}
    first = asr_arguments(wrong, expected_narration='Yüzde dört.')
    report = bridge.validate_retained_router_asr(**first)
    assert_diagnostic(report)
    assert report['component_pass'] is report['comparison']['pass'] is False
    second = prosody_arguments(asr_prepared=first['prepared'], asr_response=first['response'],
                               asr_observed=first['observed'], expected_narration='Yüzde dört.')
    second['prepared'] = prosody(expected_narration='Yüzde dört.')
    second['response'], second['observed'] = observed(second['prepared'], POSITIVE)
    with pytest.raises(bridge.RetainedRouterAudioQAError, match='asr_not_exact'):
        bridge.validate_retained_router_prosody(**second)


@pytest.mark.parametrize('field', [
    'sha256', 'bytes', 'mime_type', 'decoded_sample_rate', 'decoded_samples', 'decoded_pcm_sha256',
])
def test_supplied_original_descriptor_must_match_all_measured_fields(field):
    arguments = asr_arguments()
    value = arguments['original_audio'][field]
    arguments['original_audio'][field] = value + 1 if type(value) is int else 'changed'
    with pytest.raises(bridge.RetainedRouterAudioQAError):
        bridge.validate_retained_router_asr(**arguments)


@pytest.mark.parametrize('field', [
    'request_sha256', 'credential_sha256', 'wire_body_sha256', 'response_body_sha256',
    'parsed_result_sha256', 'response_proof_sha256', 'purpose', 'audio', 'returned_model',
])
def test_observation_fields_cannot_be_forged_even_with_recomputed_outer_hash(field):
    arguments = asr_arguments()
    evidence = arguments['observed'].evidence
    evidence[field] = {'sha256': 'a' * 64} if field == 'audio' else 'a' * 64
    if field != 'response_proof_sha256':
        evidence['response_proof_sha256'] = hashlib.sha256(adapter._canonical({
            key: value for key, value in evidence.items() if key != 'response_proof_sha256'})).hexdigest()
    arguments['observed'] = replace(arguments['observed'], _evidence_bytes=adapter._canonical(evidence))
    with pytest.raises(bridge.RetainedRouterAudioQAError):
        bridge.validate_retained_router_asr(**arguments)


def test_forged_parsed_result_and_rehashed_observation_still_must_match_actual_response():
    arguments = asr_arguments()
    output = {**ASR, 'text': 'Completely forged transcript.'}
    evidence = arguments['observed'].evidence
    evidence['parsed_result_sha256'] = hashlib.sha256(adapter._canonical(output)).hexdigest()
    evidence['response_proof_sha256'] = hashlib.sha256(adapter._canonical({
        key: value for key, value in evidence.items() if key != 'response_proof_sha256'})).hexdigest()
    arguments['observed'] = adapter.ObservedAudioRouterResult(adapter._canonical(output), adapter._canonical(evidence))
    with pytest.raises(bridge.RetainedRouterAudioQAError):
        bridge.validate_retained_router_asr(**arguments)


@pytest.mark.parametrize('change', ['purpose', 'descriptor', 'body', 'key', 'response'])
def test_frozen_request_or_httpx_response_mutations_do_not_become_qa(change):
    arguments = asr_arguments()
    original = arguments['prepared']
    if change == 'purpose':
        arguments['prepared'] = replace(original, purpose=adapter.AudioReviewPurpose.PROSODY)
    elif change == 'descriptor':
        arguments['prepared'] = replace(original, _audio_bytes=adapter._canonical({**original.audio, 'decoded_samples': 1}))
    elif change == 'body':
        payload = original.payload
        payload['messages'][0]['content'] += EXPECTED
        arguments['prepared'] = replace(original, _body_bytes=adapter._canonical(payload))
    elif change == 'key':
        arguments['prepared'] = prepare(api_key='other-private-key')
    else:
        arguments['response'] = response(original, payload=envelope(id='changed-provider-response'))
    with pytest.raises(bridge.RetainedRouterAudioQAError) as error:
        bridge.validate_retained_router_asr(**arguments)
    assert KEY not in str(error.value) and EXPECTED not in str(error.value)


@pytest.mark.parametrize('change', ['overlap', 'past_end', 'zero_width', 'omitted_word'])
def test_bad_real_response_word_timing_is_reobserved_before_use(change):
    arguments = asr_arguments()
    output = deepcopy(ASR)
    if change == 'overlap': output['words'][1]['start'] = .2
    elif change == 'past_end': output['words'][1]['end'] = 31
    elif change == 'zero_width': output['words'][0]['end'] = .05
    else: output['words'].pop()
    arguments['response'] = response(arguments['prepared'], payload=envelope(output))
    with pytest.raises(bridge.RetainedRouterAudioQAError):
        bridge.validate_retained_router_asr(**arguments)


@pytest.mark.parametrize('change', ['audio', 'credential', 'narration', 'rubric', 'schema'])
def test_prosody_requires_same_audio_key_expected_text_and_entire_original_contract(change):
    arguments = prosody_arguments()
    if change == 'audio':
        frozen = adapter.prepare_audio_prosody_request(mp3(frequency=600), api_key=KEY,
            expected_narration=EXPECTED, system_instruction=audio_qc._PROSODY_SYSTEM_INSTRUCTION,
            json_schema=deepcopy(audio_qc._PROSODY_REVIEW_SCHEMA))
    elif change == 'credential': frozen = prosody(api_key='different-private-key')
    elif change == 'narration': frozen = prosody(expected_narration='Another expected text.')
    elif change == 'rubric': frozen = prosody(system_instruction='A shorter rubric must fail.')
    else:
        schema = deepcopy(audio_qc._PROSODY_REVIEW_SCHEMA)
        schema['properties']['scores']['properties']['naturalness']['description'] = 'Replacement criterion'
        frozen = prosody(json_schema=schema)
    arguments.update(prepared=frozen)
    arguments['response'], arguments['observed'] = observed(frozen, POSITIVE)
    with pytest.raises(bridge.RetainedRouterAudioQAError):
        bridge.validate_retained_router_prosody(**arguments)


@pytest.mark.parametrize('change', ['score_contradiction', 'false_without_issue', 'wrong_phrase', 'wrong_interval'])
def test_schema_valid_but_semantically_ungrounded_prosody_is_terminal(change):
    output = deepcopy(POSITIVE if change in {'score_contradiction', 'false_without_issue'} else PROSODY)
    if change == 'score_contradiction': output['scores']['naturalness'] = 69
    elif change == 'false_without_issue': output['pass'] = False
    elif change == 'wrong_phrase': output['issues'][0]['phrase'] = 'never spoken'
    else:
        output['issues'][0]['start_seconds'] = 20
        output['issues'][0]['end_seconds'] = 21
    with pytest.raises(bridge.RetainedRouterAudioQAError, match='prosody_unverified'):
        bridge.validate_retained_router_prosody(**prosody_arguments(output))


def test_passing_synthetic_httpx_exchange_never_claims_live_receipt_or_publication_authority():
    # These constructed HTTPX objects pass protocol checks. They are deliberately
    # not network receipts, and the bridge must remain honest about that limit.
    report = bridge.validate_retained_router_prosody(**prosody_arguments())
    assert report['component_pass'] is True
    assert_diagnostic(report)
    assert report['journal_acknowledged'] is False


def test_separate_journal_and_pure_bridge_agree_on_the_same_asr_prosody_binding():
    import test_abacus_router_audio_review_journal as admission

    case = admission.case.__wrapped__()
    try:
        source = admission.source.__wrapped__(case)
        ledger = admission.commission(case, source)
        first = source.prepared
        first_response = admission.response(first, source.asr_result)
        ledger.reserve(adapter.AudioReviewPurpose.BLIND_ASR, first)
        first_settled = ledger.settle(adapter.AudioReviewPurpose.BLIND_ASR, first, first_response)
        first_observed = adapter.observe_audio_router_response(first, first_response)
        assert first_settled['result'] == first_observed.result
        assert first_settled['evidence'] == first_observed.evidence
        asr = bridge.validate_retained_router_asr(
            first, first_response, first_observed,
            expected_narration=source.expected, original_audio=source.policy['audio'])
        assert asr['component_pass'] is True

        second = admission.prosody(source)
        reserved = ledger.reserve(adapter.AudioReviewPurpose.PROSODY, second,
            expected_narration=source.expected, asr_result=first_settled['result'])
        second_response = admission.response(second, POSITIVE)
        second_settled = ledger.settle(adapter.AudioReviewPurpose.PROSODY, second, second_response)
        result = bridge.validate_retained_router_prosody(
            second, second_response, adapter.observe_audio_router_response(second, second_response),
            asr_prepared=first, asr_response=first_response, asr_observed=first_observed,
            expected_narration=source.expected, original_audio=source.policy['audio'])
        assert result['component_pass'] is True
        assert_diagnostic(result)
        assert result['observation'] == second_settled['evidence']
        for field in ('parsed_result_sha256', 'response_proof_sha256', 'comparison_sha256'):
            assert result['asr_binding'][field] == reserved['asr_binding'][field]
        # The separate journal owns its ACK. A reusable pure report still does
        # not turn this successful test exchange into publication authority.
        assert result['journal_acknowledged'] is False
    finally:
        case.client.close()
