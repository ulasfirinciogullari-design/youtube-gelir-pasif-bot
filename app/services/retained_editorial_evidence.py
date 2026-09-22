"""Validate retained blind-delivery records without manufacturing provider evidence.

V3 is an owner-artifact envelope around verbatim pre-manifest records. The
envelope is NOT an assertion that a provider saw the later manifest/captions.
Only the exact current-master blind transcript/prosody is revalidated here;
source ASR stays diagnostic. Normal stored-byte/full-PCM verification remains
mandatory in external_editorial_review before any publication authorization.
"""
from datetime import datetime
import math
import re
from uuid import UUID

from app.services.audio_qc import compare_transcript, _validate_prosody_review


ENVELOPE_ORIGIN = 'owner_artifact_envelope_not_original_provider_binding'
_RECORD_BINDING = ('video_sha256', 'video_size', 'wav_sha256', 'pcm_sha256',
                   'duration_seconds', 'source_audio_sha256', 'source_task_id',
                   'human_listened', 'review_code_sha256')


def validate_retained_audio(pack, manifest):
    # Deferred import keeps the error type/strict JSON boundary identical to
    # the legacy route, without a module-initialization cycle.
    from app.services.external_editorial_review import _require, _exact, _object, _hash, _text, _digest
    _require(pack['version'] == 3 and manifest['version'] == 2)
    envelope = pack['audio_artifact_envelope']
    _exact(envelope, ('version', 'origin', 'manifest_sha256', 'captions_sha256',
                      'sample_rate', 'channels', 'sample_width_bytes', 'sample_frames',
                      'source_profile_revision', 'edit_binding_sha256'))
    _require(type(envelope['version']) is int and envelope['version'] == 1
             and envelope['origin'] == ENVELOPE_ORIGIN
             and envelope['manifest_sha256'] == _digest(manifest)
             and envelope['captions_sha256'] == manifest['files']['captions']['sha256'])
    for key in ('manifest_sha256', 'captions_sha256', 'edit_binding_sha256'):
        _hash(envelope[key])
    _require(type(envelope['source_profile_revision']) is str
             and re.fullmatch(r'[0-9a-f]{32}', envelope['source_profile_revision']))
    # This retained-record format was produced by full-file mono 48k s16le
    # decoding. Other converters/sample layouts need their own explicit format.
    for key, value in (('sample_rate', 48000), ('channels', 1), ('sample_width_bytes', 2)):
        _require(type(envelope[key]) is int and envelope[key] == value)
    _require(type(envelope['sample_frames']) is int and envelope['sample_frames'] > 0)
    attempt, raw, result, source_asr = [_object(pack[key]) for key in (
        'prosody_attempt_json', 'prosody_raw_response_json', 'prosody_result_json', 'source_asr_evidence_json')]
    _exact(raw, ('binding', 'provider', 'model', 'received_at', 'response'))
    retained = raw['binding']
    _exact(retained, _RECORD_BINDING)
    _require(retained['human_listened'] is False
             and type(retained['video_size']) is int
             and retained['video_size'] == manifest['files']['video']['size']
             and retained['video_sha256'] == manifest['files']['video']['sha256']
             and type(retained['source_task_id']) is str
             and str(UUID(retained['source_task_id'])) == retained['source_task_id'])
    for key in ('video_sha256', 'wav_sha256', 'pcm_sha256', 'source_audio_sha256', 'review_code_sha256'):
        _hash(retained[key])
    duration = retained['duration_seconds']
    _require(type(duration) in (float, int) and math.isfinite(duration)
             and abs(duration - manifest['duration_ms'] / 1000) <= .1
             and abs(envelope['sample_frames'] / envelope['sample_rate'] - duration) < .001)
    _exact(attempt, (*_RECORD_BINDING, 'provider', 'model', 'maximum_paid_requests', 'state', 'started_at'))
    _require(all(type(attempt[k]) is type(v) and attempt[k] == v for k, v in retained.items())
             and attempt['provider'] == raw['provider'] == 'gemini'
             and type(attempt['maximum_paid_requests']) is int and attempt['maximum_paid_requests'] == 1
             and attempt['state'] == 'reserved_before_request'
             and type(attempt['model']) is str and re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', attempt['model'])
             and raw['model'] == attempt['model'])
    started, received = [datetime.fromisoformat(row) for row in (attempt['started_at'], raw['received_at'])]
    _require(started.utcoffset() is not None and received.utcoffset() is not None and received >= started)
    response = raw['response']
    _exact(response, ('transcript', 'prosody'))
    _text(response['transcript'])
    expected = ' '.join(row['narration'] for row in manifest['scenes'])
    language = manifest['language']
    compared = compare_transcript(expected, response['transcript'], provider='gemini', comparison_language=language)
    _require(compared['pass'] is True and compared['score'] == 100
             and compared['mismatch_details']['exact_match'] is True)
    validated = _validate_prosody_review(response['prosody'], expected,
        audio_duration_seconds=duration, transcript_evidence=None, language=language)
    _require(validated is not None and validated['pass'] is True and validated['issues'] == [])
    scores = validated['scores']
    _exact(scores, ('pronunciation', 'naturalness', 'pacing', 'sentence_flow', 'emphasis', 'roboticness'))
    _require(all(type(v) in (float, int) and math.isfinite(v) and 0 <= v <= 100 for v in scores.values())
             and all(v >= 70 for k, v in scores.items() if k != 'roboticness') and scores['roboticness'] <= 30)
    _exact(result, ('binding', 'provider', 'model', 'request_count', 'status', 'pass',
                    'normalized_transcript_exact', 'transcript_comparison', 'validated_review',
                    'human_listened', 'visual_qa_approved', 'published', 'word_timing_verified'))
    _require(result['binding'] == retained and result['provider'] == 'gemini'
             and result['model'] == attempt['model'] and type(result['request_count']) is int
             and result['request_count'] == 1 and result['status'] == 'audible_delivery_pass'
             and result['pass'] is True and result['normalized_transcript_exact'] is True
             and result['transcript_comparison'] == compared and result['validated_review'] == validated
             and all(result[k] is False for k in ('human_listened', 'visual_qa_approved', 'published', 'word_timing_verified')))

    # The older source transcript is retained under its real source audio/task
    # identity. Never wrap it as a new-master ASR attempt or promote its verdict.
    _exact(source_asr, ('version', 'audio_sha256', 'audio_size', 'diagnostic_only', 'language', 'model',
                        'payload', 'provider', 'qa_approved', 'requires_full_qa', 'source_task_id', 'status'))
    _require(type(source_asr['version']) is int and source_asr['version'] == 1
             and source_asr['audio_sha256'] == retained['source_audio_sha256']
             and source_asr['source_task_id'] == retained['source_task_id']
             and type(source_asr['audio_size']) is int and source_asr['audio_size'] > 0
             and source_asr['diagnostic_only'] is True and source_asr['qa_approved'] is False
             and source_asr['requires_full_qa'] is True and source_asr['status'] == 'unvalidated_provider_evidence'
             and source_asr['provider'] == 'openai' and source_asr['model'] == 'whisper-1'
             and source_asr['language'] == language)
    payload = source_asr['payload']
    _exact(payload, ('language', 'text', 'words'))
    _require(payload['language'] in ({'en', 'english'} if language == 'en' else {'tr', 'turkish'})
             and type(payload['words']) is list and 1 <= len(payload['words']) <= 500)
    _text(payload['text'])
    unknown = []
    for index, word in enumerate(payload['words']):
        _exact(word, ('word', 'start', 'end'))
        _text(word['word'])
        _require(all(type(word[k]) in (float, int) and math.isfinite(word[k]) for k in ('start', 'end'))
                 and 0 <= word['start'] <= word['end'] <= duration + .35)
        if word['start'] == word['end']:
            unknown.append(index)
    diagnostic = compare_transcript(expected, payload['text'], words=payload['words'],
                                    provider='openai', comparison_language=language)
    _require(diagnostic['mismatch_details']['exact_match'] is True
             and diagnostic['mismatch_details']['timestamp_sequence_match'] is True)
    # Separate, derived storage envelope: no original record is changed. The
    # full master/PCM proof still runs after this consistency-only validation.
    binding = {'version': 1,
        **{k: retained[k] for k in ('video_sha256', 'video_size', 'wav_sha256', 'pcm_sha256',
                                  'duration_seconds', 'source_audio_sha256', 'source_task_id')},
        **{k: envelope[k] for k in ('manifest_sha256', 'captions_sha256', 'sample_rate', 'channels',
                                   'sample_width_bytes', 'sample_frames', 'source_profile_revision', 'edit_binding_sha256')},
        'conversion': 'decode_complete_master_audio_to_pcm_s16le_no_other_filters',
        'review_code_sha256': {'retained_blind_delivery': retained['review_code_sha256']}}
    return binding, unknown
