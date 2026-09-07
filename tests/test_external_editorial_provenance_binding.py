"""Strict optional audit bindings; actual media, receipts and QC remain mandatory."""
from copy import deepcopy
import json
from uuid import uuid4

import pytest

from app.services import external_editorial_review as review
from test_external_editorial_review import case, raw, saved
from test_editorial_evidence_v2 import v2, blind_crosscheck

REVISION = '669c40ecf39a40e0bff60781cee20c2c'
EDIT = {'source_task_id': 'bf838406-f1e8-5a87-ad3e-db82ab8772a5',
        'source_profile_revision': REVISION, 'edit_binding_sha256': 'a' * 64}


def bind(c, extra, *, update=None):
    asr = json.loads(c.pack['asr_provider_evidence_json'])
    binding = asr['binding']
    binding.update(video_sha256=c.pack['manifest']['files']['video']['sha256'],
                   video_size=c.pack['manifest']['files']['video']['size'], **extra)
    if update:
        update(binding)
    asr['binding'] = deepcopy(binding)
    c.pack['asr_provider_evidence_json'] = raw(asr)
    for key in ('asr_attempt_json', 'prosody_attempt_json'):
        value = json.loads(c.pack[key]); value.update(binding); c.pack[key] = raw(value)
    value = json.loads(c.pack['prosody_result_json'])
    value['binding'] = deepcopy(binding)
    value['result']['preflight']['provider_evidence_sha256'] = review._sha(c.pack['asr_provider_evidence_json'].encode())
    c.pack['prosody_result_json'] = raw(value)
    if 'prosody_raw_response_json' in c.pack:
        value = json.loads(c.pack['prosody_raw_response_json'])
        value['binding'] = deepcopy(binding); c.pack['prosody_raw_response_json'] = raw(value)
    c.source['spec']['production_profile_revision'] = REVISION
    c.client.set(review.JOB_PREFIX + c.task, raw(c.source))
    key = review.ingest.PROFILE_PREFIX + c.source['spec']['production_channel_id']
    profile = json.loads(c.client.get(key)); profile['profile_revision'] = REVISION
    c.client.set(key, raw(profile))
    key = review.ingest.RESERVATION_PREFIX + c.task
    reservation = json.loads(c.client.get(key)); reservation['job_sha256'] = review._digest(c.source)
    c.client.set(key, raw(reservation))
    return c


@pytest.mark.parametrize('extra', [EDIT, {'source_audio_sha256': 'b' * 64}, {**EDIT, 'source_audio_sha256': 'b' * 64}])
def test_supported_complete_audit_bindings_preserve_raw_evidence_and_false_global_qa(blind_crosscheck, extra):
    c = bind(blind_crosscheck, extra)
    original = deepcopy(c.pack)
    receipt = review.create_editorial_review(c.task, c.pack)
    assert c.pack == original and receipt['evidence'] == original
    assert receipt['automated_qa_approved'] is receipt['word_timing_verified'] is False
    assert saved(c)['result']['audio_transcription_verified'] is False
    assert review.validate_editorial_publication(saved(c)) == receipt
    assert review.create_editorial_review(c.task, c.pack) == receipt


@pytest.mark.parametrize('keys', [
    ['source_task_id'], ['source_profile_revision'], ['edit_binding_sha256'],
    ['source_task_id', 'source_profile_revision'], ['source_task_id', 'edit_binding_sha256'],
    ['source_profile_revision', 'edit_binding_sha256'],
])
def test_edit_bundle_is_all_or_none_before_storage_or_writes(v2, keys):
    c = bind(v2, {key: EDIT[key] for key in keys})
    before = c.client.get(review.JOB_PREFIX + c.task)
    with pytest.raises(review.EditorialReviewError):
        review.create_editorial_review(c.task, c.pack)
    assert c.calls == [] and c.client.get(review.JOB_PREFIX + c.task) == before
    assert not c.client.exists(review.EDITORIAL_RECEIPT_PREFIX + c.task)


@pytest.mark.parametrize('key,value', [
    ('source_task_id', 'not-a-uuid'), ('source_task_id', EDIT['source_task_id'].upper()),
    ('source_task_id', True), ('source_profile_revision', 'revision-short'),
    ('source_profile_revision', REVISION.upper()), ('source_profile_revision', 1),
    ('edit_binding_sha256', 'x' * 64), ('edit_binding_sha256', 'a' * 63),
    ('source_audio_sha256', 'A' * 64), ('source_audio_sha256', False),
    ('source_audio_sha256', 'a' * 65),
])
def test_bad_provenance_values_are_rejected(v2, key, value):
    c = bind(v2, {**EDIT, key: value})
    with pytest.raises((review.EditorialReviewError, ValueError)):
        review._evidence(c.pack)


@pytest.mark.parametrize('field,value', [('video_sha256', '0' * 64), ('video_size', 123456),
                                       ('manifest_sha256', '0' * 64), ('captions_sha256', '0' * 64)])
def test_optional_audit_cannot_rebind_old_media_or_manifest(v2, field, value):
    c = bind(v2, EDIT, update=lambda b: b.update({field: value}))
    with pytest.raises(review.EditorialReviewError):
        review._evidence(c.pack)


def test_actual_saved_profile_revision_must_match_audited_revision(v2):
    c = bind(v2, {**EDIT, 'source_profile_revision': 'f' * 32})
    before = c.client.get(review.JOB_PREFIX + c.task)
    with pytest.raises(review.EditorialReviewError) as failure:
        review.create_editorial_review(c.task, c.pack)
    assert failure.value.phase == 'editorial_review_source_contract'
    assert not c.calls and c.client.get(review.JOB_PREFIX + c.task) == before


def test_unknown_binding_fields_still_fail(v2):
    c = bind(v2, {**EDIT, 'qa_approved': True})
    with pytest.raises(review.EditorialReviewError):
        review._evidence(c.pack)


def test_legacy_version_does_not_gain_optional_edit_bindings(case):
    bind(case, EDIT)
    with pytest.raises(review.EditorialReviewError):
        review._evidence(case.pack)


@pytest.mark.parametrize('name', ['capital5_v2_runner', 'runner', '_legacy_name', 'x' * 40])
def test_safe_review_code_identifier_names_with_digits_are_supported(v2, name):
    c = bind(v2, {'source_audio_sha256': 'b' * 64}, update=lambda b: b.update(review_code_sha256={name: 'c' * 64}))
    review._evidence(c.pack)


@pytest.mark.parametrize('name', ['5capital', 'bad/name', 'name-with-dash', 'NAME', 'x' * 41, '', 'runner.name'])
def test_invalid_review_code_identifiers_remain_rejected(v2, name):
    c = bind(v2, {'source_audio_sha256': 'b' * 64}, update=lambda b: b.update(review_code_sha256={name: 'c' * 64}))
    with pytest.raises(review.EditorialReviewError):
        review._evidence(c.pack)


@pytest.mark.parametrize('damage', ['record_binding', 'low_score', 'false_qa', 'missing_frame'])
def test_audit_fields_do_not_relax_existing_qc_or_record_requirements(blind_crosscheck, damage):
    c = bind(blind_crosscheck, EDIT)
    value = json.loads(c.pack['prosody_result_json'])
    if damage == 'record_binding': value['binding']['edit_binding_sha256'] = 'f' * 64
    elif damage == 'low_score': value['result']['scores']['naturalness'] = 69
    elif damage == 'false_qa': value['qa_approved'] = True
    elif damage == 'missing_frame': c.pack['frames'].pop()
    c.pack['prosody_result_json'] = raw(value)
    with pytest.raises(review.EditorialReviewError):
        review._evidence(c.pack)
