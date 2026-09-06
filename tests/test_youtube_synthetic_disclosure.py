"""No network: disclosure survives the real plan and private-first worker paths."""
import ast
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import shutil
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import youtube
from app.services import youtube_automation as automation


def source(**result_updates):
    return {
        'kind': 'render', 'state': 'SUCCESS',
        'spec': {'language': 'tr', 'format': 'shorts'},
        'result': {
            'video_key': 'videos/source-disclosure/final.mp4',
            'title': 'Banknotun bileşimi',
            'quality_disposition': 'automated_qc_pass', 'manual_qa_required': False,
            'runway_scenes_used': 0, 'video_generation_provider_records': [],
            'runway_success_scene_indices': [], 'final_runway_repair_scene_indices': [],
            'publish_metadata': {'title': 'Banknotun bileşimi', 'description': 'Kaynaklı mevcut anlatım.'},
            **result_updates,
        },
    }


def profile(**updates):
    return {'channel_id': 'UC_disclosure', 'default_language': 'tr', 'languages': ['tr'],
            'release_mode': 'public', **updates}


def plan(job=None, **updates):
    value = automation.build_publish_plan('source-disclosure', job or source(), profile())
    return {**value, **updates}


def test_complete_stock_only_provenance_does_not_label_title_or_script_assistance():
    job = source(paid_create_slots_used=0, voice_model='synthetic-narrator')
    assert automation.contains_synthetic_media(job) is False
    assert plan(job)['contains_synthetic_media'] is False


@pytest.mark.parametrize('field,value', [
    ('runway_scenes_used', 1), ('runway_scenes_used', True), ('runway_scenes_used', '0'),
    ('video_generation_provider_records', [{'provider': 'gemini_veo'}]),
    ('video_generation_provider_records', None), ('runway_success_scene_indices', [3]),
    ('final_runway_repair_scene_indices', [4]), ('final_runway_repair_scene_indices', ()),
    ('contains_synthetic_media', True), ('contains_synthetic_media', 'false'),
])
def test_generated_recovered_and_malformed_provenance_disclose(field, value):
    job = source(paid_create_slots_used=0, **{field: value})
    job['result']['publish_metadata']['contains_synthetic_media'] = False
    before = deepcopy(job)
    assert plan(job)['contains_synthetic_media'] is True
    assert job == before


@pytest.mark.parametrize('missing', [
    'runway_scenes_used', 'video_generation_provider_records',
    'runway_success_scene_indices', 'final_runway_repair_scene_indices',
])
def test_missing_legacy_provenance_is_not_a_false_attestation(missing):
    job = source()
    del job['result'][missing]
    assert automation.contains_synthetic_media(job) is True


@pytest.mark.parametrize('value', [True, None, 'false', 0])
def test_explicit_metadata_positive_or_invalid_never_becomes_false(value):
    job = source()
    job['result']['publish_metadata']['contains_synthetic_media'] = value
    assert automation.contains_synthetic_media(job) is True


def test_frozen_plan_default_is_disclosed_without_mutating_the_legacy_plan():
    frozen = plan()
    del frozen['contains_synthetic_media']
    before = deepcopy(frozen)
    assert automation.validate_publish_plan(frozen)['contains_synthetic_media'] is True
    assert frozen == before


@pytest.mark.parametrize('value', [None, 0, 1, 'false', 'true', [], {}])
def test_plan_disclosure_rejects_non_booleans(value):
    with pytest.raises(automation.MetadataValidationError, match='disclosure'):
        automation.validate_publish_plan(plan(contains_synthetic_media=value))


@pytest.fixture
def api(tmp_path, monkeypatch):
    path = tmp_path / 'final.mp4'
    path.write_bytes(b'existing video')
    service = Mock()
    service.videos.return_value.insert.return_value.next_chunk.return_value = (
        None, {'id': 'same-video', 'status': {'privacyStatus': 'private'}},
    )
    service.videos.return_value.update.return_value.execute.return_value = {
        'id': 'same-video', 'status': {'privacyStatus': 'public', 'containsSyntheticMedia': True},
    }
    factory = Mock(return_value=service)
    monkeypatch.setattr(youtube, '_service', factory)
    monkeypatch.setattr(youtube, 'MediaFileUpload', Mock())
    return SimpleNamespace(path=path, service=service, factory=factory)


@pytest.mark.parametrize('value', [False, True])
def test_insert_uses_official_disclosure_property_and_stays_private(api, value):
    youtube.upload_video_with_credentials(object(), str(api.path), 'Title', 'Description',
                                          contains_synthetic_media=value)
    body = api.service.videos.return_value.insert.call_args.kwargs['body']
    assert body['status'] == {'privacyStatus': 'private', 'selfDeclaredMadeForKids': False,
                              'containsSyntheticMedia': value}
    assert 'hasSyntheticMedia' not in body['status']
    api.service.videos.return_value.update.assert_not_called()


@pytest.mark.parametrize('mode,value', [('public', False), ('public', True), ('scheduled', True)])
def test_release_carries_same_disclosure_with_no_second_insert(api, mode, value):
    privacy = 'public' if mode == 'public' else 'private'
    api.service.videos.return_value.update.return_value.execute.return_value['status']['privacyStatus'] = privacy
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    if mode == 'scheduled':
        api.service.videos.return_value.update.return_value.execute.return_value['status']['publishAt'] = future
    youtube.set_video_release_with_credentials(object(), 'same-video', mode, publish_at=future,
                                               contains_synthetic_media=value)
    body = api.service.videos.return_value.update.call_args.kwargs['body']
    assert body['id'] == 'same-video'
    assert body['status']['privacyStatus'] == privacy
    assert body['status']['containsSyntheticMedia'] is value
    api.service.videos.return_value.insert.assert_not_called()


@pytest.mark.parametrize('value', [None, 0, 1, 'false', {}])
def test_api_non_boolean_disclosure_fails_before_remote_action(api, value):
    with pytest.raises(ValueError, match='disclosure'):
        youtube.upload_video_with_credentials(object(), str(api.path), 'T', 'D', contains_synthetic_media=value)
    with pytest.raises(ValueError, match='disclosure'):
        youtube.set_video_release_with_credentials(object(), 'same-video', 'public', contains_synthetic_media=value)
    api.factory.assert_not_called()


def test_api_legacy_call_defaults_to_disclosure_and_rejects_explicit_downgrade(api):
    youtube.upload_video_with_credentials(object(), str(api.path), 'T', 'D')
    assert api.service.videos.return_value.insert.call_args.kwargs['body']['status']['containsSyntheticMedia'] is True
    api.service.videos.return_value.update.return_value.execute.return_value['status']['containsSyntheticMedia'] = False
    with pytest.raises(RuntimeError, match='disclosure'):
        youtube.set_video_release_with_credentials(object(), 'same-video', 'public')


@pytest.mark.parametrize('response', [
    None, {}, {'id': 'other-video', 'status': {'privacyStatus': 'public', 'containsSyntheticMedia': True}},
    {'id': 'same-video'}, {'id': 'same-video', 'status': None},
    {'id': 'same-video', 'status': {'containsSyntheticMedia': True}},
    {'id': 'same-video', 'status': {'privacyStatus': 'private', 'containsSyntheticMedia': True}},
    {'id': 'same-video', 'status': {'privacyStatus': 'public'}},
    {'id': 'same-video', 'status': {'privacyStatus': 'public', 'containsSyntheticMedia': None}},
    {'id': 'same-video', 'status': {'privacyStatus': 'public', 'containsSyntheticMedia': 1}},
])
def test_release_requires_exact_video_visibility_and_positive_disclosure_proof(api, response):
    api.service.videos.return_value.update.return_value.execute.return_value = response
    with pytest.raises(RuntimeError):
        youtube.set_video_release_with_credentials(object(), 'same-video', 'public')
    api.service.videos.return_value.update.assert_called_once()
    api.service.videos.return_value.update.return_value.execute.assert_called_once_with(num_retries=3)
    api.service.videos.return_value.insert.assert_not_called()


@pytest.mark.parametrize('returned', [None, '', 'invalid', '2099-01-01T12:00:00', '2099-01-01T11:00:00Z'])
def test_schedule_requires_exact_timezone_aware_returned_publish_time(api, returned):
    api.service.videos.return_value.update.return_value.execute.return_value['status'].update(
        privacyStatus='private', publishAt=returned)
    with pytest.raises(RuntimeError, match='schedule'):
        youtube.set_video_release_with_credentials(object(), 'same-video', 'scheduled',
                                                   publish_at='2099-01-01T12:00:00+00:00')
    api.service.videos.return_value.update.assert_called_once()


@pytest.mark.parametrize('items', [
    [], [{'id': 'other-video', 'status': {'privacyStatus': 'private'}}],
    [{'status': {'privacyStatus': 'private'}}], [{'id': 'same-video', 'status': None}],
])
def test_readonly_status_cannot_supply_another_video_or_missing_status(api, items):
    api.service.videos.return_value.list.return_value.execute.return_value = {'items': items}
    with pytest.raises(RuntimeError, match='verified'):
        youtube.get_video_status_with_credentials(object(), 'same-video')
    api.service.videos.return_value.insert.assert_not_called()
    api.service.videos.return_value.update.assert_not_called()


@pytest.fixture
def worker(tmp_path):
    path = Path(__file__).resolve().parents[1] / 'app' / 'publish_tasks.py'
    names = {'publish_video_pipeline', '_safe_error_code', '_result_from_existing_record',
             '_reconcile_source_upload', '_editorial_candidate'}
    nodes = [node for node in ast.parse(path.read_text(encoding='utf-8')).body
             if isinstance(node, ast.FunctionDef) and node.name in names]
    for node in nodes:
        node.decorator_list = []
    namespace = {'datetime': datetime, 'timezone': timezone, 'json': json, 're': re,
                 'shutil': shutil, 'Path': lambda _path: tmp_path / 'publish',
                 'validate_publish_plan': automation.validate_publish_plan,
                 'contains_synthetic_media': automation.contains_synthetic_media,
                 'automated_quality_approved': automation.automated_quality_approved,
                 'MetadataValidationError': automation.MetadataValidationError,
                 'UploadAlreadyInProgress': RuntimeError}
    for name in ('get_upload_record', 'get_job', 'acquire_execution_lock', 'release_execution_lock',
                 'load_credentials', 'refresh_channel_info', 'set_stage', 'download_file',
                 'mark_upload_started', 'upload_video_with_credentials', 'mark_upload_completed',
                 'upload_caption_with_credentials', 'upload_thumbnail_with_credentials',
                 'mark_release_blocked', 'mark_release_ready', 'mark_release_started',
                 'set_video_release_with_credentials', 'mark_release_completed', 'mark_success',
                 'mark_failure', 'mark_release_uncertain', 'mark_upload_uncertain',
                 'mark_upload_preflight_failed', 'merge_youtube_result_field'):
        namespace[name] = Mock()
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)
    namespace['get_job'].return_value = source()
    namespace['get_upload_record'].return_value = {
        'status': 'queued', 'publish_task_id': 'publisher-disclosure',
        'target_channel_id': 'UC_disclosure', 'connection_id': 'connection-disclosure',
        'publish_plan': plan(),
    }
    namespace['upload_video_with_credentials'].return_value = {
        'id': 'same-video', 'status': {'privacyStatus': 'private', 'containsSyntheticMedia': True},
    }
    namespace['refresh_channel_info'].return_value = {'id': 'UC_disclosure'}
    return SimpleNamespace(ns=namespace, run=lambda: namespace['publish_video_pipeline'](
        SimpleNamespace(request=SimpleNamespace(id='publisher-disclosure')), 'source-disclosure', 'public'))


@pytest.mark.parametrize('frozen,generated,expected', [(False, 1, True), (True, 0, True), (False, 0, False)])
def test_actual_worker_frozen_plan_and_provenance_reach_both_api_boundaries(worker, frozen, generated, expected):
    worker.ns['get_job'].return_value = source(runway_scenes_used=generated, paid_create_slots_used=0)
    worker.ns['get_upload_record'].return_value['publish_plan']['contains_synthetic_media'] = frozen
    before = deepcopy(worker.ns['get_upload_record'].return_value)
    result = worker.run()
    upload = worker.ns['upload_video_with_credentials'].call_args.kwargs
    release = worker.ns['set_video_release_with_credentials'].call_args
    assert upload['privacy_status'] == 'private'
    assert upload['contains_synthetic_media'] is expected
    assert release.args[1:] == ('same-video', 'public')
    assert release.kwargs['contains_synthetic_media'] is expected
    assert result['contains_synthetic_media'] is expected
    assert worker.ns['merge_youtube_result_field'].call_args.args[2]['contains_synthetic_media'] is expected
    assert worker.ns['get_upload_record'].return_value == before


def test_explicit_insert_disclosure_rejection_blocks_release_but_keeps_uploaded_id(worker):
    worker.ns['get_job'].return_value = source(runway_scenes_used=1)
    worker.ns['upload_video_with_credentials'].return_value['status']['containsSyntheticMedia'] = False
    result = worker.run()
    assert result['youtube_video_id'] == 'same-video'
    assert result['privacy_status'] == 'private'
    assert result['release_status'] == 'blocked'
    worker.ns['set_video_release_with_credentials'].assert_not_called()
    worker.ns['mark_release_blocked'].assert_called_once_with('source-disclosure', 'publisher-disclosure',
                                                           'synthetic_disclosure_unconfirmed')
    worker.ns['mark_upload_completed'].assert_called_once()


def test_disclosure_never_substitutes_for_automated_quality(worker):
    worker.ns['get_job'].return_value = source(runway_scenes_used=1, quality_disposition='manual_qa_preview',
                                              manual_qa_required=True)
    result = worker.run()
    assert result['privacy_status'] == 'private'
    assert result['contains_synthetic_media'] is True
    worker.ns['set_video_release_with_credentials'].assert_not_called()


def test_ambiguous_release_proof_is_uncertain_without_losing_id_or_repeating_insert(worker):
    worker.ns['get_job'].return_value = source(runway_scenes_used=1)
    worker.ns['set_video_release_with_credentials'].side_effect = RuntimeError('Unverified response')
    with pytest.raises(RuntimeError, match='release outcome is uncertain'):
        worker.run()
    worker.ns['mark_upload_completed'].assert_called_once_with(
        'source-disclosure', 'publisher-disclosure', 'same-video', release_mode='public', publish_at=None)
    worker.ns['upload_video_with_credentials'].assert_called_once()
    worker.ns['set_video_release_with_credentials'].assert_called_once()
    worker.ns['mark_release_uncertain'].assert_called_once()
    worker.ns['mark_release_completed'].assert_not_called()
    worker.ns['mark_success'].assert_not_called()


@pytest.mark.parametrize('state', ['uploading', 'uncertain'])
def test_disclosure_does_not_reopen_upload_side_effect_lock(worker, state):
    worker.ns['get_upload_record'].return_value['status'] = state
    with pytest.raises(RuntimeError):
        worker.run()
    worker.ns['upload_video_with_credentials'].assert_not_called()
    worker.ns['set_video_release_with_credentials'].assert_not_called()


def test_completed_private_video_is_not_released_by_new_profile_or_redelivery(worker):
    worker.ns['get_upload_record'].return_value.update(
        status='complete', youtube_video_id='same-video', privacy_status='private', release_status='private')
    result = worker.run()
    assert result['privacy_status'] == 'private'
    worker.ns['upload_video_with_credentials'].assert_not_called()
    worker.ns['set_video_release_with_credentials'].assert_not_called()


@pytest.mark.parametrize('format', ['shorts', 'landscape'])
def test_format_specific_hashtags_do_not_mislabel_landscape(format):
    job = source()
    job['spec']['format'] = format
    job['result']['publish_metadata'].update(tags=['shorts', 'banknot'], hashtags=['#SHORTS', 'Para'])
    before = deepcopy(job)
    result = automation.build_publish_plan('source-disclosure', job,
                                           profile(default_tags=['Shorts', 'belgesel'], hashtags=['Shorts']))
    if format == 'shorts':
        assert sum(value.casefold() == 'shorts' for value in result['hashtags']) == 1
        assert '#Shorts' in result['description']
    else:
        assert all(value.lstrip('#').casefold() != 'shorts' for value in result['hashtags'] + result['tags'])
        assert '#Shorts' not in result['description']
    assert job == before


def test_shorts_gets_one_format_hashtag_without_a_fixed_profile_hashtag():
    assert 'Shorts' in plan()['hashtags']
