"""Synthetic retained-record fixtures exercise V3 without live provider calls."""
from copy import deepcopy
import json

import pytest

from app.services import external_editorial_review as review
from app.services.audio_qc import compare_transcript, _validate_prosody_review
from app.services.retained_editorial_evidence import ENVELOPE_ORIGIN
from test_external_editorial_review import case, raw, saved


REVISION = '669c40ecf39a40e0bff60781cee20c2c'
SOURCE_TASK = '80970d72-1f54-5253-a12b-9d6abaac2614'


@pytest.fixture
def retained(case):
    c = case
    manifest = c.pack['manifest']
    manifest.update(version=2, duration_ms=30000, language='en')
    old_asr = json.loads(c.pack.pop('asr_provider_evidence_json'))['unapproved_provider_evidence']
    c.pack.pop('asr_attempt_json')
    expected = ' '.join(row['narration'] for row in manifest['scenes'])
    binding = {'video_sha256': manifest['files']['video']['sha256'],
        'video_size': manifest['files']['video']['size'], 'wav_sha256': '3' * 64, 'pcm_sha256': '2' * 64,
        'duration_seconds': 30, 'source_audio_sha256': 'b' * 64, 'source_task_id': SOURCE_TASK,
        'human_listened': False, 'review_code_sha256': 'c' * 64}
    model = 'gemini-fixture'
    response = {'transcript': expected, 'prosody': {'pass': True, 'summary': 'Clear fixture narration.',
        'scores': {'pronunciation': 95, 'naturalness': 90, 'pacing': 92, 'sentence_flow': 92,
                   'emphasis': 90, 'roboticness': 12}, 'issues': []}}
    c.pack.update(version=3,
        source_asr_evidence_json=raw({'version': 1, 'audio_sha256': 'b' * 64, 'audio_size': 708524,
            'diagnostic_only': True, 'language': 'en', 'model': 'whisper-1', 'provider': 'openai',
            'payload': old_asr['payload'], 'qa_approved': False, 'requires_full_qa': True,
            'source_task_id': SOURCE_TASK, 'status': 'unvalidated_provider_evidence'}),
        prosody_attempt_json=raw({**binding, 'provider': 'gemini', 'model': model, 'maximum_paid_requests': 1,
            'state': 'reserved_before_request', 'started_at': '2026-09-07T15:21:48+00:00'}),
        prosody_raw_response_json=raw({'binding': deepcopy(binding), 'provider': 'gemini', 'model': model,
            'received_at': '2026-09-07T15:21:54+00:00', 'response': response}),
        prosody_result_json=raw({'binding': deepcopy(binding), 'provider': 'gemini', 'model': model,
            'request_count': 1, 'status': 'audible_delivery_pass', 'pass': True, 'normalized_transcript_exact': True,
            'transcript_comparison': compare_transcript(expected, expected, provider='gemini', comparison_language='en'),
            'validated_review': _validate_prosody_review(response['prosody'], expected,
                audio_duration_seconds=30, transcript_evidence=None, language='en'),
            'human_listened': False, 'visual_qa_approved': False, 'published': False, 'word_timing_verified': False}),
        audio_artifact_envelope={'version': 1, 'origin': ENVELOPE_ORIGIN,
            'manifest_sha256': review._digest(manifest), 'captions_sha256': manifest['files']['captions']['sha256'],
            'sample_rate': 48000, 'channels': 1, 'sample_width_bytes': 2, 'sample_frames': 1440000,
            'source_profile_revision': REVISION, 'edit_binding_sha256': 'd' * 64})
    c.source['spec']['production_profile_revision'] = REVISION
    c.source['result']['language'] = 'en'
    c.source['result']['external_provenance'].update(manifest_version=2, duration_ms=30000,
        manifest_sha256=review._digest(manifest))
    c.proof['manifest_sha256'] = review._digest(manifest)
    c.client.set(review.JOB_PREFIX + c.task, raw(c.source))
    key = review.ingest.PROFILE_PREFIX + c.source['spec']['production_channel_id']
    profile = json.loads(c.client.get(key)); profile['profile_revision'] = REVISION; c.client.set(key, raw(profile))
    key = review.ingest.RESERVATION_PREFIX + c.task
    reservation = json.loads(c.client.get(key)); reservation['job_sha256'] = review._digest(c.source)
    c.client.set(key, raw(reservation))
    return c


def test_v3_original_records_are_preserved_and_not_rebound_or_promoted(retained):
    c = retained
    original = deepcopy(c.pack)
    pack, manifest, binding, unknown = review._evidence(c.pack)
    assert pack == original and unknown == [43, 51]
    assert binding['manifest_sha256'] == review._digest(manifest)
    assert binding['sample_frames'] == 1440000 and binding['channels'] == 1
    assert 'manifest_sha256' not in json.loads(pack['prosody_raw_response_json'])['binding']
    assert 'binding' not in json.loads(pack['source_asr_evidence_json'])
    receipt = review.create_editorial_review(c.task, c.pack)
    assert receipt['evidence'] == original and c.pack == original and len(c.calls) == 1
    assert receipt['word_timing_verified'] is receipt['automated_qa_approved'] is False
    assert saved(c)['result']['audio_transcription_verified'] is saved(c)['result']['qa_approved'] is False
    assert review.validate_editorial_publication(saved(c)) == receipt
    assert review.create_editorial_review(c.task, c.pack) == receipt
    assert c.client.get('old-failed-job') == 'unchanged' and c.client.get('old-series-cursor') == '2'


@pytest.mark.parametrize('field,mutate', [
    ('prosody_attempt_json', lambda x: x.update(video_sha256='0' * 64)),
    ('prosody_attempt_json', lambda x: x.update(maximum_paid_requests=True)),
    ('prosody_attempt_json', lambda x: x.update(state='not_run')),
    ('prosody_attempt_json', lambda x: x.update(started_at='2026-09-08T00:00:00+00:00')),
    ('prosody_raw_response_json', lambda x: x['binding'].update(video_size=999)),
    ('prosody_raw_response_json', lambda x: x['binding'].update(pcm_sha256='0' * 64)),
    ('prosody_raw_response_json', lambda x: x['binding'].update(wav_sha256='0' * 64)),
    ('prosody_raw_response_json', lambda x: x['binding'].update(manifest_sha256='0' * 64)),
    ('prosody_raw_response_json', lambda x: x['response'].update(transcript='A different narration.')),
    ('prosody_raw_response_json', lambda x: x['response']['prosody']['scores'].update(naturalness=69)),
    ('prosody_raw_response_json', lambda x: x['response']['prosody']['scores'].update(roboticness=90)),
    ('prosody_result_json', lambda x: x.update(human_listened=True)),
    ('prosody_result_json', lambda x: x.update(word_timing_verified=True)),
    ('prosody_result_json', lambda x: x.update(qa_approved=True)),
    ('prosody_result_json', lambda x: x.update(visual_qa_approved=True)),
    ('prosody_result_json', lambda x: x.update(published=True)),
    ('prosody_result_json', lambda x: x.update(normalized_transcript_exact=False)),
    ('prosody_result_json', lambda x: x['validated_review']['scores'].update(pacing=100)),
    ('source_asr_evidence_json', lambda x: x.update(audio_sha256='0' * 64)),
    ('source_asr_evidence_json', lambda x: x.update(source_task_id='00970d72-1f54-5253-a12b-9d6abaac2614')),
    ('source_asr_evidence_json', lambda x: x.update(diagnostic_only=False)),
    ('source_asr_evidence_json', lambda x: x.update(qa_approved=True)),
    ('source_asr_evidence_json', lambda x: x.update(status='approved')),
    ('source_asr_evidence_json', lambda x: x['payload']['words'][0].update(start=-1)),
    ('source_asr_evidence_json', lambda x: x['payload']['words'][0].update(word='False')),
])
def test_forged_changed_or_promoted_record_rejected_before_storage(retained, field, mutate):
    c = retained
    value = json.loads(c.pack[field]); mutate(value); c.pack[field] = raw(value)
    before = c.client.get(review.JOB_PREFIX + c.task)
    with pytest.raises(review.EditorialReviewError):
        review.create_editorial_review(c.task, c.pack)
    assert not c.calls and c.client.get(review.JOB_PREFIX + c.task) == before
    assert not c.client.exists(review.EDITORIAL_RECEIPT_PREFIX + c.task)


@pytest.mark.parametrize('key,value', [('origin', 'original_provider_manifest_binding'),
    ('manifest_sha256', '0' * 64), ('captions_sha256', '0' * 64), ('sample_rate', 44100),
    ('channels', 2), ('channels', True), ('sample_width_bytes', 4), ('sample_frames', 1430000),
    ('source_profile_revision', 'bad'), ('edit_binding_sha256', 'bad')])
def test_envelope_cannot_rebind_manifest_caption_or_pcm_layout(retained, key, value):
    retained.pack['audio_artifact_envelope'][key] = value
    with pytest.raises(review.EditorialReviewError):
        review._evidence(retained.pack)


@pytest.mark.parametrize('mutate', [lambda p: p['frames'].clear(), lambda p: p['scenes'].pop(),
    lambda p: p['sources'].pop(), lambda p: p['rights_basis'].pop(),
    lambda p: p['limitations'].update(human_listened=True), lambda p: p.update(blocking_issues=['Bad image']),
    lambda p: p['publish_metadata'].update(contains_synthetic_media=False),
    lambda p: p['publish_metadata'].update(title='Episode 3/4')])
def test_v3_keeps_shared_visual_rights_and_publication_gates(retained, mutate):
    mutate(retained.pack)
    with pytest.raises(review.EditorialReviewError):
        review._evidence(retained.pack)


def test_v3_cannot_bypass_real_storage_proof(retained, monkeypatch):
    def reject(*args, **kwargs):
        raise ValueError('synthetic stored-media mismatch')
    monkeypatch.setattr(review, '_storage_proof', reject)
    with pytest.raises(review.EditorialReviewError) as failure:
        review.create_editorial_review(retained.task, retained.pack)
    assert failure.value.phase == 'editorial_review_storage_proof'
    assert not retained.client.exists(review.EDITORIAL_RECEIPT_PREFIX + retained.task)


def test_v3_requires_the_verbatim_blind_raw_response(retained):
    retained.pack.pop('prosody_raw_response_json')
    with pytest.raises(review.EditorialReviewError):
        review._evidence(retained.pack)
