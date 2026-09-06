"""Recovered-public UI never rewrites historical delivery or trusts metrics alone."""
from copy import deepcopy

import pytest

from test_studio_ui import ui_modules
from test_studio_performance_dashboard import _job, TASK, VIDEO, CHANNEL


def fixture():
    job = _job(release='blocked')
    job['kind'] = 'render'
    job['spec'].update(production_profile_revision='revision', production_connection_id='connection')
    job['result']['youtube'].update(profile_revision='revision', connection_id='connection')
    publisher = '521794fa-6168-4a34-ae58-15a57d50634c'
    job['result']['youtube_automation'] = {'publish_task_id': publisher}
    proof = dict(source_task_id=TASK, youtube_video_id=VIDEO, target_channel_id=CHANNEL,
                 publish_task_id=publisher, profile_revision='revision', connection_id='connection',
                 privacy_status='public', release_status='public', caption_uploaded=True,
                 thumbnail_uploaded=True, contains_synthetic_media=True, receipt_sha256='a' * 64)
    return job, proof


def test_verified_receipt_changes_only_presentation(ui_modules):
    studio, _ = ui_modules
    job, proof = fixture()
    before = deepcopy(job)
    displayed = studio._with_verified_public_recovery(job, lookup=lambda _: proof)
    assert studio._video_delivery(displayed) == dict(key='public', label='YouTube’da yayında', attention=False)
    assert studio._ready_privacy_label(displayed) == 'Herkese açık'
    assert studio._ready_release_status(displayed) == 'public'
    assert studio._publication_status(displayed) == ''
    assert studio._job_upload_allowed(displayed) is False
    html = studio._ready_video_card(displayed)
    assert 'YouTube’da yayında' in html and 'Kontrol gerekiyor' not in html
    assert 'Henüz herkese açık değil' not in html
    assert job == before and displayed['result'] == before['result']


@pytest.mark.parametrize('field,value', [
    ('source_task_id', 'other'), ('youtube_video_id', 'ZZZZZZZZZZZ'),
    ('target_channel_id', 'otherChannel'), ('publish_task_id', 'other'),
    ('profile_revision', 'other'), ('connection_id', 'other'),
    ('privacy_status', 'private'), ('release_status', 'uncertain'),
    ('caption_uploaded', False), ('thumbnail_uploaded', False),
    ('contains_synthetic_media', False), ('receipt_sha256', 'invalid'),
])
def test_mismatched_proof_keeps_warning(ui_modules, field, value):
    studio, _ = ui_modules
    job, proof = fixture()
    proof[field] = value
    displayed = studio._with_verified_public_recovery(job, lookup=lambda _: proof)
    assert '_verified_public_recovery' not in displayed
    assert studio._video_delivery(displayed)['attention'] is True
    assert studio._ready_release_status(displayed) == 'blocked'


@pytest.mark.parametrize('privacy', ['private', 'unlisted'])
def test_newer_observed_privacy_change_is_not_hidden(ui_modules, privacy):
    studio, _ = ui_modules
    job, proof = fixture()
    job['_youtube_metrics'] = {'status': 'fresh', 'privacy_status': privacy}
    displayed = studio._with_verified_public_recovery(job, lookup=lambda _: proof)
    assert studio._video_delivery(displayed)['key'] == privacy
    assert studio._video_delivery(displayed)['attention'] is True


def test_absent_or_unavailable_proof_and_manual_qa_fail_closed(ui_modules):
    studio, _ = ui_modules
    job, proof = fixture()
    job['_verified_public_recovery'] = proof
    assert '_verified_public_recovery' not in studio._with_verified_public_recovery(job, lookup=lambda _: None)
    def unavailable(_):
        raise RuntimeError('private diagnostic')
    assert '_verified_public_recovery' not in studio._with_verified_public_recovery(job, lookup=unavailable)
    job['result']['manual_qa_required'] = True
    assert '_verified_public_recovery' not in studio._with_verified_public_recovery(job, lookup=lambda _: proof)


@pytest.mark.parametrize('stage', ['final_visual_qc', 'final_visual_qc_rescue',
                                  'final_visual_qc_ai_repair', 'pre_runway_budget_rescue'])
def test_final_quality_stages_have_readable_names(ui_modules, stage):
    studio, _ = ui_modules
    assert stage in studio.STAGE_LABELS and '_' not in studio.STAGE_LABELS[stage]
