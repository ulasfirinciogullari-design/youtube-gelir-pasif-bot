"""Publisher boundary tests: real file hashing, mocked private receipt/YouTube."""
from copy import deepcopy
import hashlib
from pathlib import Path
import sys
import types

import pytest

from test_youtube_lifecycle import _import_publish_tasks_with_stubs


@pytest.fixture
def boundary(monkeypatch, tmp_path):
    module = _import_publish_tasks_with_stubs(monkeypatch)
    monkeypatch.setattr(module, 'Path', lambda _value: tmp_path / 'publish')
    video, captions = b'reviewed MP4 master bytes', b'1\n00:00:00,000 --> 00:00:05,000\nReviewed words.\n'
    sha = lambda value: hashlib.sha256(value).hexdigest()
    source_id, task_id = 'source-editorial-one', 'publisher-editorial-one'
    source = {
        'task_id': source_id, 'kind': 'render', 'state': 'SUCCESS',
        'spec': {'workflow': 'external_import', 'mode': 'production', 'language': 'en',
                 'publish_after_render': True, 'production_channel_id': 'UC_editorial',
                 'production_connection_id': 'connection-editorial',
                 'production_profile_revision': 'revision-editorial'},
        'result': {'video_key': 'external/master.mp4', 'caption_key': 'external/captions.srt',
                   'quality_disposition': 'editorial_review_pass', 'manual_qa_required': False,
                   'qa_approved': False, 'audio_transcription_verified': False,
                   'word_timing_verified': False, 'contains_synthetic_media': True,
                   'external_descriptor_id': 'descriptor', 'editorial_review_id': source_id,
                   'editorial_review_sha256': 'a' * 64,
                   'expected_video_sha256': sha(video), 'expected_video_size': len(video),
                   'expected_caption_sha256': sha(captions), 'expected_caption_size': len(captions)},
    }
    plan = {
        'schema_version': 1, 'source_task_id': source_id, 'target_channel_id': 'UC_editorial',
        'profile_revision': 'revision-editorial', 'profile_snapshot': {'release_mode': 'public'},
        'title': 'Reviewed explanation', 'description': 'Reviewed description.',
        'tags': ['business'], 'hashtags': [], 'category_id': '27', 'default_language': 'en',
        'contains_synthetic_media': True, 'thumbnail_key': None, 'require_thumbnail': False,
        'release_mode': 'public', 'publish_at': None, 'series': None,
        'quality_snapshot': {key: source['result'][key] for key in (
            'quality_disposition', 'manual_qa_required', 'editorial_review_id', 'editorial_review_sha256')},
    }
    receipt = {'version': 1, 'receipt_id': source_id, 'receipt_sha256': 'a' * 64,
               'evidence': {'frames': [{'frame_index': 60, 'sha256': 'c' * 64}],
                            'manifest': {'files': {'video': {'sha256': sha(video), 'size': len(video)}}}},
               'server_proof': {'video_sha256': sha(video), 'captions_sha256': sha(captions),
                                'media_structure': {'frame_rate': '30/1', 'frame_count': 900,
                                                    'width': 1080, 'height': 1920}}}
    record = {'publish_task_id': task_id, 'status': 'queued', 'side_effect_possible': False,
              'target_channel_id': 'UC_editorial', 'connection_id': 'connection-editorial',
              'publish_plan': deepcopy(plan)}
    state = types.SimpleNamespace(
        source=source, receipt=receipt, authority_current=True, plan=plan, record=record,
        video=video, captions=captions, source_id=source_id, task_id=task_id,
        events=[], paths={}, inserts=[], caption_uploads=[], releases=[], failures=[],
        after_insert=None, after_caption=None, before_download=None, validations=0,
    )
    service = types.ModuleType('app.services.external_editorial_review')

    class EditorialReviewError(ValueError):
        pass

    def validate(candidate, frozen_plan=None):
        state.validations += 1
        state.events.append('receipt-validated')
        # The private service independently owns detailed grant validation.
        # Model its stable contract; never accept a caller's result flag alone.
        if (not state.authority_current or not state.receipt or candidate != state.source
                or candidate.get('spec', {}).get('workflow') != 'external_import'
                or candidate.get('result', {}).get('editorial_review_sha256') != state.receipt['receipt_sha256']
                or candidate.get('result', {}).get('editorial_review_id') != state.receipt['receipt_id']
                or (frozen_plan is not None and (
                    frozen_plan != state.record['publish_plan']
                    or any(frozen_plan['quality_snapshot'].get(key) != candidate['result'].get(key)
                           for key in ('quality_disposition', 'manual_qa_required',
                                       'editorial_review_id', 'editorial_review_sha256'))))):
            raise EditorialReviewError('invalid_or_stale')
        return deepcopy(state.receipt)

    def approved(candidate):
        try:
            validate(candidate)
            return True
        except EditorialReviewError:
            return False

    service.EditorialReviewError = EditorialReviewError
    service.validate_editorial_publication = validate
    service.publication_quality_approved = approved
    monkeypatch.setitem(sys.modules, service.__name__, service)
    monkeypatch.setattr(module, 'get_upload_record', lambda *_a: deepcopy(state.record))
    monkeypatch.setattr(module, 'get_job', lambda *_a: deepcopy(state.source))
    monkeypatch.setattr(module, 'acquire_execution_lock', lambda *_a: 'owned-lock')
    monkeypatch.setattr(module, 'release_execution_lock', lambda *_a: None)
    monkeypatch.setattr(module, 'load_credentials', lambda *_a, **_k: object())
    monkeypatch.setattr(module, 'refresh_channel_info', lambda *_a, **_k: {'id': 'UC_editorial'})
    monkeypatch.setattr(module, 'set_stage', lambda *_a, **_k: None)

    def download(key, path):
        path = Path(path)
        state.events.append(('download', key))
        state.paths[key] = path
        if state.before_download:
            state.before_download(key)
        path.write_bytes(state.video if key.endswith('.mp4') else state.captions)

    def upload(_credentials, path, *_args, **kwargs):
        state.events.append('insert')
        state.inserts.append((Path(path).read_bytes(), kwargs))
        if state.after_insert:
            state.after_insert()
        return {'id': 'REAL_EDITORIAL_ID', 'status': {'containsSyntheticMedia': True}}

    def complete(_source, _task, video_id, **kwargs):
        state.events.append('upload-complete')
        state.record.update(status='complete', youtube_video_id=video_id, privacy_status='private',
                            release_status='awaiting_assets' if kwargs else 'private')

    def caption(_credentials, video_id, path, *_args, **_kwargs):
        state.caption_uploads.append((video_id, Path(path).read_bytes(), str(path)))
        if state.after_caption:
            state.after_caption()
        return {'id': 'caption-id'}

    def released(_source, _task, mode, **_kwargs):
        state.record.update(release_status=mode, privacy_status='public' if mode == 'public' else 'private')

    def merge(_source, field, values):
        state.source['result'][field] = {**state.source['result'].get(field, {}), **values}

    monkeypatch.setattr(module, 'download_file', download)
    monkeypatch.setattr(module, 'upload_video_with_credentials', upload)
    monkeypatch.setattr(module, 'upload_caption_with_credentials', caption)
    monkeypatch.setattr(module, 'mark_upload_started', lambda *_a: state.events.append('upload-start'))
    monkeypatch.setattr(module, 'mark_upload_completed', complete)
    monkeypatch.setattr(module, 'mark_release_ready', lambda *_a: state.events.append('release-ready'))
    monkeypatch.setattr(module, 'mark_release_started', lambda *_a: state.events.append('release-start'))
    monkeypatch.setattr(module, 'set_video_release_with_credentials',
                        lambda _creds, video_id, mode, **kwargs: state.releases.append((video_id, mode, kwargs)))
    monkeypatch.setattr(module, 'mark_release_completed', released)
    monkeypatch.setattr(module, 'mark_release_blocked',
                        lambda *_a: state.record.update(release_status='blocked'))
    monkeypatch.setattr(module, 'mark_upload_preflight_failed', lambda *_a: state.events.append('preflight-failed'))
    monkeypatch.setattr(module, 'mark_upload_uncertain', lambda *_a: state.events.append('upload-uncertain'))
    monkeypatch.setattr(module, 'mark_release_uncertain', lambda *_a: state.events.append('release-uncertain'))
    monkeypatch.setattr(module, 'merge_youtube_result_field', merge)
    monkeypatch.setattr(module, 'mark_success', lambda *_a: None)
    monkeypatch.setattr(module, 'mark_failure', lambda *_a: state.failures.append(_a))
    state.module = module
    state.task = types.SimpleNamespace(request=types.SimpleNamespace(id=task_id))
    state.run = lambda: module.publish_video_pipeline(state.task, source_id, 'public')
    return state


def test_reviewed_master_is_private_first_then_public_same_id_with_one_caption_download(boundary):
    b = boundary
    result = b.run()
    assert result['privacy_status'] == 'public' and result['release_status'] == 'public'
    assert len(b.inserts) == len(b.releases) == 1
    assert b.inserts[0][0] == b.video and b.inserts[0][1]['privacy_status'] == 'private'
    assert b.inserts[0][1]['contains_synthetic_media'] is True
    assert b.releases[0][:2] == ('REAL_EDITORIAL_ID', 'public')
    assert b.caption_uploads[0][:2] == ('REAL_EDITORIAL_ID', b.captions)
    assert b.events.count(('download', 'external/captions.srt')) == 1
    assert b.events.index(('download', 'external/captions.srt')) < b.events.index('insert')
    assert b.events.index('receipt-validated') < b.events.index('upload-start') < b.events.index('insert')
    assert b.validations == 2 and not b.failures
    assert b.source['result']['qa_approved'] is False  # Editorial is not automated QA.
    assert b.source['result']['word_timing_verified'] is False
    assert b.source['result']['youtube']['video_id'] == 'REAL_EDITORIAL_ID'
    replay = b.run()
    assert replay['idempotent_replay'] is True and len(b.inserts) == len(b.releases) == 1


@pytest.mark.parametrize('damage', ['video_bytes', 'video_size', 'caption_bytes', 'caption_size',
                                   'missing_receipt', 'forged_receipt', 'stale_authority',
                                   'missing_plan', 'wrong_snapshot', 'missing_caption',
                                   'receipt_video_hash', 'receipt_caption_hash', 'boolean_size'])
def test_invalid_editorial_candidate_cannot_even_insert_private(boundary, damage):
    b = boundary
    if damage == 'video_bytes': b.video = b'X' * len(b.video)
    elif damage == 'video_size': b.video += b'x'
    elif damage == 'caption_bytes': b.captions = b'X' * len(b.captions)
    elif damage == 'caption_size': b.captions += b'x'
    elif damage == 'missing_receipt': b.receipt = None
    elif damage == 'forged_receipt': b.source['result']['editorial_review_sha256'] = 'b' * 64
    elif damage == 'stale_authority': b.authority_current = False
    elif damage == 'missing_plan': b.record.pop('publish_plan')
    elif damage == 'wrong_snapshot': b.record['publish_plan']['quality_snapshot']['editorial_review_id'] = 'forged'
    elif damage == 'missing_caption': b.source['result'].pop('caption_key')
    elif damage == 'receipt_video_hash': b.receipt['server_proof']['video_sha256'] = 'b' * 64
    elif damage == 'receipt_caption_hash': b.receipt['server_proof']['captions_sha256'] = 'b' * 64
    elif damage == 'boolean_size': b.source['result']['expected_video_size'] = True
    with pytest.raises(RuntimeError, match='preflight failed'):
        b.run()
    assert not b.inserts and not b.releases and not b.caption_uploads
    assert 'preflight-failed' in b.events and 'upload-start' not in b.events


@pytest.mark.parametrize('damage', ['oauth', 'profile', 'receipt', 'source', 'video_bytes', 'caption_bytes'])
def test_change_during_private_upload_blocks_release_and_preserves_real_id(boundary, damage):
    b = boundary
    def change():
        if damage in {'oauth', 'profile'}:
            b.authority_current = False
        elif damage == 'receipt': b.receipt = None
        elif damage == 'source': b.source['result']['editorial_review_sha256'] = 'b' * 64
        elif damage == 'video_bytes': b.paths['external/master.mp4'].write_bytes(b'changed')
        elif damage == 'caption_bytes': b.paths['external/captions.srt'].write_bytes(b'changed')
    b.after_insert = change
    result = b.run()
    assert result['privacy_status'] == 'private' and result['release_status'] == 'blocked'
    assert result['youtube_video_id'] == 'REAL_EDITORIAL_ID'
    assert result['release_error_code'] == 'editorial_review_changed_or_unavailable'
    assert b.source['result']['youtube']['video_id'] == 'REAL_EDITORIAL_ID'
    assert b.source['result']['youtube']['release_status'] == 'blocked'
    assert not b.releases and not b.failures and len(b.inserts) == 1
    assert 'preflight-failed' not in b.events and 'upload-uncertain' not in b.events
    if damage in {'video_bytes', 'caption_bytes'}:
        assert not b.caption_uploads
    replay = b.run()
    assert replay['idempotent_replay'] is True and replay['privacy_status'] == 'private'
    assert len(b.inserts) == 1 and not b.releases


def test_caption_change_after_caption_upload_still_blocks_public_promotion(boundary):
    b = boundary
    b.after_caption = lambda: b.paths['external/captions.srt'].write_bytes(b'new caption bytes')
    result = b.run()
    assert b.caption_uploads[0][1] == b.captions
    assert result['release_status'] == 'blocked' and not b.releases and not b.failures


def test_current_authority_is_checked_after_download_not_only_at_task_start(boundary):
    b = boundary
    b.before_download = lambda _key: setattr(b, 'authority_current', False)
    with pytest.raises(RuntimeError, match='preflight failed'):
        b.run()
    assert not b.inserts


def test_editorial_plan_cannot_fall_back_when_source_markers_removed(boundary):
    b = boundary
    b.source['spec'].pop('workflow')
    b.source['result'] = {'video_key': 'external/master.mp4', 'caption_key': 'external/captions.srt',
                          'quality_disposition': 'automated_qc_pass', 'manual_qa_required': False}
    with pytest.raises(RuntimeError, match='preflight failed'):
        b.run()
    assert not b.inserts and b.validations == 1


def test_caption_upload_error_still_blocks_editorial_release(boundary, monkeypatch):
    b = boundary
    def failed_caption(*_a, **_k):
        raise RuntimeError('provider details must not escape')
    monkeypatch.setattr(b.module, 'upload_caption_with_credentials', failed_caption)
    result = b.run()
    assert result['release_status'] == 'blocked' and result['privacy_status'] == 'private'
    assert result['release_error_code'] == result['caption_error_code'] == 'RuntimeError'
    assert len(b.inserts) == 1 and not b.releases and not b.failures
    assert result['youtube_video_id'] == 'REAL_EDITORIAL_ID'


@pytest.mark.parametrize('marker', ['external_descriptor_id', 'external_provenance', 'editorial_review_id',
                                   'editorial_review_sha256', 'expected_video_sha256',
                                   'expected_caption_sha256', 'expected_video_size', 'expected_caption_size'])
def test_empty_external_marker_never_uses_legacy_private_or_automated_fallback(boundary, marker):
    b = boundary
    b.source['spec'].pop('workflow')
    b.source['result'] = {'video_key': 'external/master.mp4', 'quality_disposition': 'automated_qc_pass',
                          'manual_qa_required': False, marker: None}
    assert b.module._editorial_candidate(b.source) is True
    with pytest.raises(RuntimeError, match='preflight failed'):
        b.run()
    assert not b.inserts


def test_queue_checks_private_receipt_not_editorial_result_flag(boundary, monkeypatch):
    b = boundary
    b.receipt = None
    monkeypatch.setattr(b.module, 'connection_status', lambda: pytest.fail('no routing without receipt'))
    assert b.module.queue_automatic_publish(b.source_id) == {'status': 'quality_blocked'}
    assert b.validations == 1 and not b.inserts


def test_queue_accepts_real_editorial_predicate_without_converting_automated_quality(boundary, monkeypatch):
    b = boundary
    monkeypatch.setattr(b.module, 'connection_status', lambda: {'connections': []})
    monkeypatch.setattr(b.module, 'list_channel_profiles', lambda: [])
    monkeypatch.setattr(b.module, 'select_channel_profile', lambda *_a, **_k: None)
    assert b.module.automated_quality_approved(b.source) is False
    assert b.module.queue_automatic_publish(b.source_id) == {'status': 'no_unique_route'}
    assert b.validations == 1
