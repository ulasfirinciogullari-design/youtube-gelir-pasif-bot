"""Pure diagnostic QA for two separate original-audio router observations.

This bridge never sends, admits, reserves or acknowledges a provider request.
It re-observes supplied HTTPX bytes and binds the results to one supplied
original-audio descriptor and expected narration. A constructed HTTPX response
is not proof of network receipt, and caller inputs are not source authority.
Even a passing component report is diagnostic, never final QA or publication
permission. Durable source admission and reservation ACKs belong elsewhere.
Measured audio/word duration bounds are checked here; the source's frozen edit
target and ordinary edit-duration QA remain for the recovery/final-QA caller.
"""
import hashlib
import json

import httpx

from app.services import abacus_router_audio_adapter as adapter, audio_qc


_FLAGS = {'version': 1, 'diagnostic_only': True, 'qa_approved': False,
          'publish_eligible': False, 'full_qa_complete': False,
          'edit_duration_qa_complete': False, 'journal_acknowledged': False}


class RetainedRouterAudioQAError(ValueError):
    """Fixed diagnostic rejection without provider text, audio or credentials."""


def _require(condition, code='retained_router_audio_binding_invalid'):
    if not condition:
        raise RetainedRouterAudioQAError(code)


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _checked_observation(prepared, response, observed, purpose, original_audio):
    _require(type(prepared) is adapter.PreparedAudioRouterRequest
             and prepared.purpose is purpose
             and type(response) is httpx.Response
             and type(observed) is adapter.ObservedAudioRouterResult)
    # Recompute all request/audio/wire/parsed-result/response-proof bindings;
    # accepting a self-consistent, freely constructed observation is insufficient.
    actual = adapter.observe_audio_router_response(prepared, response)
    _require(actual == observed and type(original_audio) is dict
             and adapter._canonical(original_audio) == adapter._canonical(prepared.audio))
    return actual


def _expected_narration(value):
    _require(type(value) is str and value.strip()
             and len(value) <= adapter.MAX_METADATA_BYTES
             and len(value.encode('utf-8')) <= adapter.MAX_METADATA_BYTES,
             'retained_router_audio_expected_narration_invalid')
    return _sha(value.encode('utf-8'))


def _asr_report(prepared, response, observed, expected_narration, original_audio):
    expected_sha = _expected_narration(expected_narration)
    actual = _checked_observation(prepared, response, observed,
                                  adapter.AudioReviewPurpose.BLIND_ASR, original_audio)
    output, evidence = actual.result, actual.evidence
    audio = evidence['audio']
    duration = audio['decoded_samples'] / audio['decoded_sample_rate']
    comparison = audio_qc.compare_transcript(
        expected_narration, output['text'], language_code=output['language'],
        words=output['words'], provider='abacus_router', comparison_language='tr')
    audio_qc._require_word_timing_evidence(comparison, 'Abacus router')
    _require(all(0 <= word['start'] < word['end'] <= duration
                 for word in comparison['word_timestamps']),
             'retained_router_audio_timing_invalid')
    exact = (comparison['available'] is True and comparison['pass'] is True
             and comparison['score'] == 100
             and comparison['mismatch_details']['exact_match'] is True
             and comparison['mismatch_details']['timestamp_sequence_match'] is True)
    if exact:
        _require(audio_qc._validated_prosody_timestamp_evidence(
            comparison, allow_coarse=False) is not None,
            'retained_router_audio_timing_invalid')
    return {**_FLAGS, 'kind': 'retained_router_asr_diagnostic',
            'provider': 'abacus_router', 'component_pass': exact,
            'expected_narration_sha256': expected_sha,
            'audio': audio, 'audio_duration_seconds': duration,
            'word_timing_consistent': True, 'comparison': comparison,
            'comparison_sha256': _sha(adapter._canonical(comparison)),
            'observation': evidence}


def validate_retained_router_asr(prepared, response, observed, *,
                                 expected_narration, original_audio):
    """Compare blind heard words with the supplied frozen spoken contract.

    A well-formed transcript mismatch remains a negative diagnostic report;
    inconsistent requests, observations or intervals raise a fixed error.
    ``original_audio`` is the complete measured descriptor, never just a path.
    """
    try:
        return _asr_report(prepared, response, observed, expected_narration, original_audio)
    except RetainedRouterAudioQAError:
        raise
    except Exception:
        raise RetainedRouterAudioQAError('retained_router_audio_asr_unverified') from None


def validate_retained_router_prosody(prepared, response, observed, *,
                                     asr_prepared, asr_response, asr_observed,
                                     expected_narration, original_audio):
    """Bind full-rubric prosody to independently supplied blind-ASR evidence.

    Re-derive the ASR comparison from its actual request/response, rather than
    accepting a copied pass flag or transcript report. This verifies supplied
    evidence only; it does not establish send order or a durable journal ACK.
    """
    try:
        asr = _asr_report(asr_prepared, asr_response, asr_observed,
                          expected_narration, original_audio)
        _require(asr['component_pass'] is True, 'retained_router_audio_asr_not_exact')
        actual = _checked_observation(prepared, response, observed,
                                      adapter.AudioReviewPurpose.PROSODY, original_audio)
        _require(prepared.credential_sha256 == asr_prepared.credential_sha256)
        body = prepared.payload
        expected_prompt = (adapter._PROSODY_PREFIX
                           + json.dumps(expected_narration, ensure_ascii=False)
                           + adapter._PROSODY_SUFFIX)
        _require(body['messages'][0]['content'] == audio_qc._PROSODY_SYSTEM_INSTRUCTION
                 and adapter._canonical(adapter.schema_for_request(body, prepared.purpose))
                 == adapter._canonical(audio_qc._PROSODY_REVIEW_SCHEMA)
                 and body['messages'][1]['content'][1]['text'] == expected_prompt,
                 'retained_router_audio_prosody_contract_changed')
        prosody = audio_qc._validate_prosody_review(
            actual.result, expected_narration,
            audio_duration_seconds=asr['audio_duration_seconds'],
            transcript_evidence=asr['comparison'], language='tr', provider='abacus_router')
        _require(prosody is not None, 'retained_router_audio_prosody_unverified')
        prosody['review_attempts'] = 1
        return {**_FLAGS, 'kind': 'retained_router_prosody_diagnostic',
                'provider': 'abacus_router', 'component_pass': prosody['pass'] is True,
                'expected_narration_sha256': asr['expected_narration_sha256'],
                'audio': asr['audio'], 'audio_duration_seconds': asr['audio_duration_seconds'],
                'rubric_sha256': _sha(audio_qc._PROSODY_SYSTEM_INSTRUCTION.encode('utf-8')),
                'schema_sha256': _sha(adapter._canonical(audio_qc._PROSODY_REVIEW_SCHEMA)),
                'asr_binding': {
                    'request_sha256': asr['observation']['request_sha256'],
                    'parsed_result_sha256': asr['observation']['parsed_result_sha256'],
                    'response_proof_sha256': asr['observation']['response_proof_sha256'],
                    'comparison_sha256': asr['comparison_sha256']},
                'asr_comparison': asr['comparison'], 'prosody': prosody,
                'observation': actual.evidence}
    except RetainedRouterAudioQAError:
        raise
    except Exception:
        raise RetainedRouterAudioQAError('retained_router_audio_prosody_unverified') from None
