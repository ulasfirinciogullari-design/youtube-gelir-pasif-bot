"""Externally public videos keep their identity and immutable delivery history."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from test_blocked_public_recovery import (
    case, _load, _edit, _write, _track, GoogleError,
    SOURCE, PUBLISHER, VIDEO, CHANNEL, REVISION,
)


@pytest.fixture
def observed(case, monkeypatch):
    # Other offline suites intentionally install a minimal Google module.
    # Keep this stream adapter local to the fixture, independent of test order.
    import googleapiclient.http as google_http
    class MediaStream:
        def __init__(self, stream, *, mimetype, resumable):
            assert mimetype == 'application/octet-stream' and resumable is False
            self.stream = stream
        def getbytes(self, begin, length):
            self.stream.seek(begin)
            return self.stream.read(length)
    monkeypatch.setattr(google_http, 'MediaIoBaseUpload', MediaStream, raising=False)
    from app.services import studio_state as jobs
    module = _load('observed_public_delivery', {
        'core': case.module, 'jobs': jobs, 'UPLOAD_PREFIX': case.module.UPLOAD_PREFIX})
    for name, prefix in (('source', ['result', 'youtube']), ('publisher', ['result'])):
        _edit(case, name, prefix + ['contains_synthetic_media'], True)
        _edit(case, name, prefix + ['thumbnail_uploaded'], True)
    case.api.remote_video['status']['privacyStatus'] = 'public'
    case.original = {case.keys[k]: case.client.get(case.keys[k]) for k in ('source', 'publisher', 'ledger')}
    data = {k: Path(v['path']).read_bytes() for k, v in case.assets.items()}
    def prepare(source, *, source_task_id, work_dir):
        work_dir.mkdir(parents=True)
        assets = deepcopy(case.assets)
        for k, v in assets.items():
            target = work_dir / Path(v['path']).name
            target.write_bytes(data[k]); v['path'] = str(target)
        return assets
    case.prep.prepare_publication_recovery_assets.side_effect = prepare
    key = module.PREFIX + SOURCE
    name = 'EN captions ' + case.assets['caption']['sha256'][:12]
    case.api.inserted = _track(name=name)
    def insert(**kwargs):
        assert kwargs['body']['snippet'] == {
            'videoId': VIDEO, 'language': 'en', 'name': name, 'isDraft': False}
        assert kwargs['media_body'].getbytes(0, len(data['caption'])) == data['caption']
        def execute(*, num_retries):
            assert num_retries == 0
            record = json.loads(case.client.get(key))
            assert record['caption_status'] == 'reserved_before_http' and record['caption_attempts'] == 1
            assert record['original_records'] == case.original
            case.api.requests.append('captions.insert')
            if case.api.caption_error: raise case.api.caption_error
            case.api.caption_tracks = deepcopy(case.api.post_insert_tracks
                if case.api.post_insert_tracks is not None else [case.api.inserted])
            return deepcopy(case.api.inserted)
        return SimpleNamespace(execute=execute)
    case.captions.insert.side_effect = insert
    case.observed = module; case.audit_key = key
    return case


def run(case, **kwargs):
    return case.observed.reconcile(**dict(source_id=SOURCE, video_id=VIDEO,
        channel_id=CHANNEL, revision=REVISION, **kwargs))


def test_public_adoption_archives_exact_bytes_without_visibility_or_upload_requests(observed):
    result = run(observed)
    assert result == {'status': 'reconciled', 'video_id': VIDEO,
        'original_records_archived': True, 'visibility_requests': 0, 'video_uploads': 0}
    audit = json.loads(observed.client.get(observed.audit_key))
    assert audit['original_records'] == observed.original
    assert audit['caption_status'] == 'serving' and audit['caption_attempts'] == 1
    source = json.loads(observed.client.get(observed.keys['source']))
    assert source['result']['quality_disposition'] == 'automated_qc_pass'
    assert source['result']['youtube']['release_origin'] == 'observed_existing_public'
    assert source['result']['youtube']['caption_uploaded'] is True
    assert observed.client.get('never-reset-paid-ledger') == 'used:1'
    assert observed.client.get('never-reset-topic-cursor') == 'consumed:1'
    assert run(observed)['status'] == 'already_reconciled'
    assert observed.api.requests.count('captions.insert') == 1
    observed.videos.insert.assert_not_called(); observed.videos.update.assert_not_called()
    assert observed.client.get(observed.keys['lock']) is None


@pytest.mark.parametrize('field,value', [('privacyStatus', 'private'), ('privacyStatus', 'unlisted'),
    ('uploadStatus', 'uploaded'), ('containsSyntheticMedia', False), ('publishAt', 'future')])
def test_remote_nonpublic_or_changed_disclosure_cannot_mutate(observed, field, value):
    observed.api.remote_video['status'][field] = value
    with pytest.raises(observed.module.BlockedPublicRecoveryError): run(observed)
    observed.captions.insert.assert_not_called()
    assert observed.client.get(observed.audit_key) is None


def test_wrong_channel_cannot_adopt(observed):
    observed.api.remote_video['snippet']['channelId'] = 'other'
    with pytest.raises(observed.module.BlockedPublicRecoveryError): run(observed)
    observed.captions.insert.assert_not_called()


@pytest.mark.parametrize('name,path,value', [
    ('source', ['result', 'youtube', 'contains_synthetic_media'], False),
    ('publisher', ['result', 'contains_synthetic_media'], None),
    ('publisher', ['result', 'thumbnail_uploaded'], False),
    ('source', ['publication_hold'], True),
    ('source', ['result', 'manual_qa_required'], True),
])
def test_original_approval_disclosure_and_asset_proofs_required(observed, name, path, value):
    _edit(observed, name, path, value)
    with pytest.raises(observed.module.BlockedPublicRecoveryError): run(observed)
    observed.captions.insert.assert_not_called()


def test_owner_hold_stops_before_caption_request(observed):
    from app.services.source_publication_hold import HOLD_PREFIX
    observed.client.set(HOLD_PREFIX + SOURCE, 'owner-held')
    with pytest.raises(observed.module.BlockedPublicRecoveryError): run(observed)
    observed.captions.insert.assert_not_called()


def test_legacy_frozen_disclosure_plan_is_preserved(observed):
    _edit(observed, 'ledger', ['publish_plan', 'contains_synthetic_media'], False)
    observed.original[observed.keys['ledger']] = observed.client.get(observed.keys['ledger'])
    assert run(observed)['status'] == 'reconciled'
    ledger = json.loads(observed.client.get(observed.keys['ledger']))
    assert ledger['publish_plan']['contains_synthetic_media'] is False
    source = json.loads(observed.client.get(observed.keys['source']))
    assert source['result']['youtube']['contains_synthetic_media'] is True


def test_ambiguous_insert_is_not_replayed_and_readback_can_settle(observed):
    observed.api.caption_error = TimeoutError('unknown provider outcome')
    assert run(observed)['status'] == 'uncertain'
    assert run(observed)['status'] == 'uncertain'
    assert observed.api.requests.count('captions.insert') == 1
    assert {k: observed.client.get(k) for k in observed.original} == observed.original
    observed.api.caption_tracks.append(observed.api.inserted)
    assert run(observed)['status'] == 'reconciled'
    assert observed.api.requests.count('captions.insert') == 1


def test_unprocessed_caption_keeps_old_status_until_serving(observed):
    observed.api.inserted['snippet']['status'] = 'syncing'
    assert run(observed)['status'] == 'awaiting_processing'
    assert {k: observed.client.get(k) for k in observed.original} == observed.original
    observed.api.caption_tracks[0]['snippet']['status'] = 'serving'
    assert run(observed)['status'] == 'reconciled'
    assert observed.api.requests.count('captions.insert') == 1


def test_definitive_rejection_never_retries_or_adopts_unproven_track(observed):
    observed.api.caption_error = GoogleError()
    assert run(observed)['status'] == 'rejected'
    observed.api.caption_tracks.append(observed.api.inserted)
    assert run(observed)['status'] == 'rejected'
    assert {k: observed.client.get(k) for k in observed.original} == observed.original
    assert observed.api.requests.count('captions.insert') == 1


def test_tampered_archive_cannot_settle(observed):
    observed.api.inserted['snippet']['status'] = 'syncing'
    assert run(observed)['status'] == 'awaiting_processing'
    audit = json.loads(observed.client.get(observed.audit_key)); audit['original_records'] = {}
    _write(observed.client, observed.audit_key, audit)
    observed.api.caption_tracks[0]['snippet']['status'] = 'serving'
    with pytest.raises(observed.module.BlockedPublicRecoveryError, match='original_records_changed'): run(observed)
    assert {k: observed.client.get(k) for k in observed.original} == observed.original


def test_completed_identity_cannot_be_rebound(observed):
    run(observed)
    with pytest.raises(observed.module.BlockedPublicRecoveryError, match='binding_changed'):
        observed.observed.reconcile(SOURCE, 'XXXXXXXXXXX', CHANNEL, REVISION)


def test_racing_owner_edit_prevents_current_status_rewrite(observed, monkeypatch):
    old = observed.observed._settle
    def settle(*args):
        observed.client.set(observed.keys['profile'], 'changed by owner')
        return old(*args)
    monkeypatch.setattr(observed.observed, '_settle', settle)
    with pytest.raises(observed.module.BlockedPublicRecoveryError, match='state_changed'): run(observed)
    assert {k: observed.client.get(k) for k in observed.original} == observed.original
    observed.videos.update.assert_not_called()
