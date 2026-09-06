from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from test_blocked_public_recovery import (
    case as asset_case, _load, _edit, _write, _run as recover_assets, _track,
    SOURCE, PUBLISHER, VIDEO, CHANNEL, REVISION, GoogleError,
)


@pytest.fixture
def case(asset_case):
    case = asset_case
    _edit(case, 'source', ['result', 'status'], 'complete')
    assert recover_assets(case)['status'] == 'assets_ready_needs_release_integration'
    core = case.module
    case.core = core
    case.module = _load('blocked_public_release', {'assets_core': core})
    case.release_key = case.module.PUBLIC_RECOVERY_PREFIX + SOURCE
    case.api.requests.clear()
    case.captions.insert.reset_mock()
    case.thumbnails.set.reset_mock()
    case.prep.prepare_publication_recovery_assets.reset_mock()
    case.api.update_error = None
    case.api.update_response = None
    case.api.apply_update = True
    def update(**kwargs):
        assert kwargs == {'part': 'status', 'body': {'id': VIDEO, 'status': {
            'privacyStatus': 'public', 'containsSyntheticMedia': True, 'selfDeclaredMadeForKids': False}}}
        def execute(*, num_retries):
            assert num_retries == 0
            receipt = _receipt(case)
            assert receipt['status'] == 'reserved_before_http' and receipt['attempts'] == 1
            assert receipt['side_effect_possible'] is True and receipt['prior_private_proof']['privacy_status'] == 'private'
            case.api.requests.append('videos.update')
            if case.api.apply_update:
                case.api.remote_video['status'].update(privacyStatus='public', containsSyntheticMedia=True)
            if case.api.update_error:
                raise case.api.update_error
            return deepcopy(case.api.update_response if case.api.update_response is not None else {
                'id': VIDEO, 'status': case.api.remote_video['status']})
        return SimpleNamespace(execute=Mock(side_effect=execute))
    case.videos.update = Mock(side_effect=update)
    return case


def _run(case, *, settle=False, **kwargs):
    function = case.module.settle_blocked_public_assets if settle else case.module.release_blocked_public_video
    return function(**{'source_task_id': SOURCE, 'expected_video_id': VIDEO, 'expected_channel_id': CHANNEL,
                       'expected_profile_revision': REVISION, 'work_dir': case.work_dir, **kwargs})


def _receipt(case):
    return json.loads(case.client.get(case.release_key))


def _inputs(case):
    records, snapshots = case.core._read(case.client, SOURCE, CHANNEL)
    audit = json.loads(case.client.get(case.keys['audit']))
    return records, audit, _receipt(case), {
        'credential_cipher': snapshots[case.core.CREDENTIAL_PREFIX + CHANNEL],
        'authorization_epoch': snapshots[case.core.AUTH_EPOCH_KEY]}


def _proof(case):
    records, audit, receipt, auth = _inputs(case)
    return case.module.validate_public_recovery_receipt(records, audit, receipt, **auth)


def test_exact_same_existing_video_becomes_public_once_with_separate_truthful_proof(case):
    originals = {key: case.client.get(key) for name, key in case.keys.items() if name != 'lock'}
    result = _run(case)
    assert result['status'] == 'public' and result['release_attempts'] == 1 and result['public_confirmed'] is True
    assert _proof(case)['youtube_video_id'] == VIDEO
    assert _proof(case)['caption_uploaded'] is _proof(case)['thumbnail_uploaded'] is True
    assert _proof(case)['contains_synthetic_media'] is True
    assert {key: case.client.get(key) for key in originals} == originals
    assert case.client.ttl(case.release_key) == -1
    assert case.api.requests.count('videos.update') == 1
    case.videos.insert.assert_not_called()
    case.captions.insert.assert_not_called()
    case.thumbnails.set.assert_not_called()
    assert 'https://' not in json.dumps(result) and 'opaque' not in json.dumps(result)
    assert case.client.get(case.keys['lock']) is None


def test_later_settlement_is_read_only_and_does_not_prepare_assets_or_repeat_public_update(case):
    _run(case)
    case.prep.prepare_publication_recovery_assets.reset_mock()
    result = _run(case, settle=True, work_dir=None)
    assert result['public_confirmed'] is True
    case.prep.prepare_publication_recovery_assets.assert_not_called()
    assert case.api.requests.count('videos.update') == 1
    assert _run(case, work_dir=None)['public_confirmed'] is True
    assert case.api.requests.count('videos.update') == 1


def test_timestamp_only_changes_do_not_replace_history_or_invalidate_current_semantics(case):
    _run(case, settle=True)
    historical = _receipt(case)['historical_snapshot_hashes']
    for name in ('source', 'publisher', 'ledger', 'profile'):
        _edit(case, name, ['updated_at'], '2026-09-06T12:00:00+00:00')
    assert _run(case)['public_confirmed'] is True
    assert _receipt(case)['historical_snapshot_hashes'] == historical
    for name in ('source', 'publisher', 'ledger', 'profile'):
        _edit(case, name, ['updated_at'], '2026-09-06T13:00:00+00:00')
    assert _proof(case)['privacy_status'] == 'public'


def test_pending_caption_can_be_read_only_settled_then_released_without_duplicate_asset_post(case):
    audit = json.loads(case.client.get(case.keys['audit']))
    audit['status'] = 'assets_pending_verification'
    audit['caption']['status'] = 'awaiting_processing'
    _write(case.client, case.keys['audit'], audit)
    case.api.caption_tracks = [_track(status='syncing')]
    assert _run(case, settle=True)['status'] == 'assets_pending_verification'
    case.videos.update.assert_not_called()
    case.api.caption_tracks = [_track()]
    assert _run(case, settle=True)['status'] == 'assets_ready'
    case.videos.update.assert_not_called()
    assert _run(case)['status'] == 'public'
    case.captions.insert.assert_not_called()
    assert json.loads(case.client.get(case.keys['audit'])) == audit


@pytest.mark.parametrize('response', [{}, {'id': 'Different00', 'status': {'privacyStatus': 'public'}},
                                     {'id': VIDEO, 'status': {'privacyStatus': 'private'}}])
def test_incomplete_update_response_only_uses_get_to_confirm_never_another_update(case, response):
    case.api.update_response = response
    assert _run(case)['status'] == 'public'
    assert _receipt(case)['update_response_verified'] is False
    assert case.api.requests.count('videos.update') == 1
    assert _proof(case)['release_status'] == 'public'


def test_lost_http_response_is_confirmed_by_readback_without_replaying_update(case):
    case.api.update_error = TimeoutError('SECRET unknown request outcome')
    assert _run(case)['status'] == 'public'
    assert _receipt(case)['request_error']['status'] == 'uncertain'
    assert case.api.requests.count('videos.update') == 1
    assert 'SECRET' not in json.dumps(_receipt(case))


@pytest.mark.parametrize('error,expected', [(TimeoutError('SECRET'), 'uncertain'),
                                          (GoogleError(403, 'forbidden'), 'rejected')])
def test_private_after_failed_request_stays_terminal_and_cannot_be_reposted(case, error, expected):
    case.api.apply_update = False
    case.api.update_error = error
    assert _run(case)['status'] == expected
    assert _run(case)['status'] == expected
    assert case.api.requests.count('videos.update') == 1
    assert case.module.get_verified_public_recovery_for_source(SOURCE) is None
    with pytest.raises(case.module.BlockedPublicReleaseError):
        _proof(case)


def test_delayed_public_response_can_be_confirmed_later_without_another_update(case):
    case.api.apply_update = False
    case.api.update_error = TimeoutError('uncertain')
    assert _run(case)['status'] == 'uncertain'
    case.api.remote_video['status'].update(privacyStatus='public', containsSyntheticMedia=True)
    assert _run(case, settle=True, work_dir=None)['status'] == 'public'
    assert case.api.requests.count('videos.update') == 1
    assert _proof(case)['privacy_status'] == 'public'


def test_lost_redis_reservation_reply_consumes_the_only_possible_attempt(case):
    original = case.core._commit
    calls = 0
    def commit(*args):
        nonlocal calls
        calls += 1
        result = original(*args)
        if calls == 2:
            raise TimeoutError('EXEC succeeded, response lost')
        return result
    case.core._commit = commit
    with pytest.raises(case.module.BlockedPublicReleaseError):
        _run(case)
    assert _receipt(case)['status'] == 'reserved_before_http'
    assert _run(case)['status'] == 'uncertain'
    case.videos.update.assert_not_called()


@pytest.mark.parametrize('mutation', ['source', 'ledger', 'profile', 'credential', 'epoch', 'membership', 'lock', 'audit'])
def test_current_snapshot_race_prevents_public_request(case, mutation):
    original = case.core._commit
    calls = 0
    def commit(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            if mutation in {'source', 'ledger', 'profile'}:
                _edit(case, mutation, ['concurrent_change'], True)
            elif mutation == 'membership':
                case.client.srem(case.core.CHANNEL_INDEX_KEY, CHANNEL)
            elif mutation == 'epoch':
                case.client.set(case.core.AUTH_EPOCH_KEY, 'new-epoch')
            else:
                case.client.set(case.keys[mutation], 'changed')
        return original(*args)
    case.core._commit = commit
    with pytest.raises(case.module.BlockedPublicReleaseError):
        _run(case)
    case.videos.update.assert_not_called()
    assert _receipt(case)['attempts'] == 0


def test_real_watch_conflict_fails_closed_before_request(case, monkeypatch):
    original_pipeline = case.client.pipeline
    def pipeline(*args, **kwargs):
        pipe = original_pipeline(*args, **kwargs)
        execute = pipe.execute
        def conflict(*a, **k):
            _edit(case, 'source', ['concurrent_change'], True)
            return execute(*a, **k)
        pipe.execute = conflict
        return pipe
    monkeypatch.setattr(case.client, 'pipeline', pipeline)
    with pytest.raises(case.module.BlockedPublicReleaseError):
        _run(case)
    case.videos.update.assert_not_called()
    assert case.client.get(case.release_key) is None


def test_accepted_release_then_failed_receipt_commit_cannot_repeat_and_can_be_read_only_settled(case):
    original = case.core._commit
    calls = 0
    def commit(*args):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise TimeoutError('receipt commit unavailable')
        return original(*args)
    case.core._commit = commit
    with pytest.raises(case.module.BlockedPublicReleaseError):
        _run(case)
    assert _receipt(case)['status'] == 'reserved_before_http'
    assert _run(case, settle=True)['status'] == 'public'
    assert case.api.requests.count('videos.update') == 1


@pytest.mark.parametrize('name,path,value', [
    ('source', ['spec', 'topic'], 'Different topic'),
    ('source', ['result', 'title'], 'Different title'),
    ('source', ['result', 'quality_disposition'], 'needs_review'),
    ('publisher', ['result', 'caption_error_code'], 'different error'),
    ('ledger', ['publish_plan', 'description'], 'Changed description'),
    ('profile', ['languages'], ['tr']),
    ('channel', ['connection_id'], 'different-connection'),
])
def test_meaningful_change_after_settlement_cannot_authorize_release(case, name, path, value):
    _run(case, settle=True)
    _edit(case, name, path, value)
    with pytest.raises(case.module.BlockedPublicReleaseError):
        _run(case)
    case.videos.update.assert_not_called()


@pytest.mark.parametrize('asset', ['caption', 'thumbnail', 'metadata', 'final'])
def test_observed_approved_asset_fingerprint_must_equal_prior_asset_audit(case, asset):
    case.assets[asset]['sha256'] = 'f' * 64
    with pytest.raises(case.module.BlockedPublicReleaseError, match='fingerprint_changed'):
        _run(case)
    case.videos.update.assert_not_called()


@pytest.mark.parametrize('field,value', [('language', 'tr'), ('videoId', 'Different00'),
                                       ('trackKind', 'ASR'), ('isDraft', True), ('name', 'not original')])
def test_caption_identity_is_the_exact_existing_insert_not_any_track(case, field, value):
    case.api.caption_tracks = [_track(**{field: value})]
    with pytest.raises(case.module.BlockedPublicReleaseError, match='caption_changed'):
        _run(case)
    case.videos.update.assert_not_called()


def test_missing_caption_remains_unverified_without_reinsertion(case):
    case.api.caption_tracks = []
    assert _run(case)['status'] == 'assets_pending_verification'
    case.videos.update.assert_not_called()
    case.captions.insert.assert_not_called()


@pytest.mark.parametrize('path,value', [
    (['status'], 'uncertain'), (['attempts'], 0), (['attempts'], True), (['side_effect_possible'], False),
    (['release_request', 'youtube_video_id'], 'Different00'),
    (['prior_private_proof', 'privacy_status'], 'public'),
    (['public_proof', 'privacy_status'], 'private'), (['public_proof', 'contains_synthetic_media'], False),
    (['public_proof', 'youtube_video_id'], 'Different00'),
    (['public_proof', 'target_channel_id'], 'other-channel'),
    (['caption_proof', 'caption_id'], 'different-caption'), (['caption_proof', 'status'], 'syncing'),
    (['thumbnail_verified'], False), (['asset_fingerprints', 'final', 'sha256'], 'f' * 64),
    (['historical_snapshot_hashes', 'source_snapshot_sha256'], 'f' * 64),
    (['confirmation'], 'claimed_without_readback'),
])
def test_pure_proof_consumer_rejects_incomplete_or_tampered_receipts(case, path, value):
    _run(case)
    receipt = _receipt(case)
    target = receipt
    for segment in path[:-1]:
        target = target[segment]
    target[path[-1]] = value
    _write(case.client, case.release_key, receipt)
    with pytest.raises(case.module.BlockedPublicReleaseError):
        _proof(case)
    assert case.module.get_verified_public_recovery_for_source(SOURCE) is None


def test_cached_presentation_loader_does_not_call_google_storage_or_modify_records(case):
    _run(case)
    before = {key: case.client.get(key) for key in [case.release_key, *case.keys.values()]}
    case.core._service.reset_mock()
    case.core._credentials.reset_mock()
    case.prep.prepare_publication_recovery_assets.reset_mock()
    assert case.module.get_verified_public_recovery_for_source(SOURCE)['receipt_sha256'] == _proof(case)['receipt_sha256']
    case.core._service.assert_not_called()
    case.core._credentials.assert_not_called()
    case.prep.prepare_publication_recovery_assets.assert_not_called()
    assert {key: case.client.get(key) for key in before} == before


@pytest.mark.parametrize('mutation', ['credential', 'epoch', 'membership'])
def test_cached_verified_badge_disappears_after_auth_change(case, mutation):
    _run(case)
    if mutation == 'membership':
        case.client.srem(case.core.CHANNEL_INDEX_KEY, CHANNEL)
    else:
        key = case.core.AUTH_EPOCH_KEY if mutation == 'epoch' else case.keys['credential']
        case.client.set(key, 'changed')
    assert case.module.get_verified_public_recovery_for_source(SOURCE) is None


def test_future_remote_false_disclosure_is_not_reported_as_confirmed(case):
    _run(case)
    case.api.remote_video['status']['containsSyntheticMedia'] = False
    assert _run(case, settle=True)['status'] == 'uncertain'
    assert _receipt(case)['public_proof']['contains_synthetic_media'] is True
    assert _receipt(case)['last_remote_observation']['contains_synthetic_media'] is False
    assert case.module.get_verified_public_recovery_for_source(SOURCE) is None
    assert case.api.requests.count('videos.update') == 1


def test_negative_readback_invalidates_even_when_job_timestamp_changes_during_remote_read(case):
    _run(case)
    original = case.module._remote
    def remote(*args):
        result = original(*args)
        _edit(case, 'source', ['updated_at'], '2026-09-06T18:00:00+00:00')
        return result
    case.module._remote = remote
    case.api.remote_video['status']['privacyStatus'] = 'private'
    assert _run(case, settle=True)['status'] == 'uncertain'
    assert case.module.get_verified_public_recovery_for_source(SOURCE) is None
    assert case.api.requests.count('videos.update') == 1


def test_negative_readback_never_overwrites_a_newer_receipt(case):
    _run(case)
    original = case.module._remote
    newer = {}
    def remote(*args):
        result = original(*args)
        record = _receipt(case)
        record['newer_observation_marker'] = 'concurrently-verified'
        _write(case.client, case.release_key, record)
        newer.update(record)
        return result
    case.module._remote = remote
    case.api.remote_video['status']['privacyStatus'] = 'private'
    with pytest.raises(case.module.BlockedPublicReleaseError, match='receipt_changed'):
        _run(case, settle=True)
    assert _receipt(case) == newer
    assert case.api.requests.count('videos.update') == 1


@pytest.fixture
def note_case(case):
    case.note = case.module.REENACTMENT_NOTES['en']
    case.api.remote_video.update(etag='original_etag', snippet={
        'channelId': CHANNEL, 'title': 'Original title 1/4', 'categoryId': '28',
        'description': 'Existing factual description.\nSource: https://example.org/source',
        'tags': ['IKEA', 'Business decisions'], 'defaultLanguage': 'en', 'defaultAudioLanguage': 'en',
        'channelTitle': 'Margin', 'thumbnails': {'ignored_read_only': True},
    })
    case.api.note_drop_field = None
    case.api.force_precondition_failure = False
    case.api.last_update_body = None
    def update(**kwargs):
        assert kwargs['part'] == 'status,snippet'
        body = deepcopy(kwargs['body'])
        case.api.last_update_body = body
        request = SimpleNamespace(headers={})
        def execute(*, num_retries):
            assert num_retries == 0
            assert request.headers == {'If-Match': '"original_etag"'}
            assert _receipt(case)['status'] == 'reserved_before_http'
            case.api.requests.append('videos.update')
            if case.api.force_precondition_failure:
                raise GoogleError(412, 'conditionNotMet')
            case.api.remote_video['status'].update(privacyStatus='public', containsSyntheticMedia=True)
            case.api.remote_video['snippet'] = {'channelId': CHANNEL, **deepcopy(body['snippet'])}
            case.api.remote_video['etag'] = 'new_etag'
            field = case.api.note_drop_field
            if field:
                if field == 'description':
                    case.api.remote_video['snippet'][field] = 'Description without the approved note'
                else:
                    del case.api.remote_video['snippet'][field]
            return {'id': VIDEO, 'status': deepcopy(case.api.remote_video['status'])}
        request.execute = Mock(side_effect=execute)
        return request
    case.videos.update = Mock(side_effect=update)
    return case


def test_optional_note_uses_same_conditional_update_and_preserves_all_writable_snippet_fields(note_case):
    case = note_case
    originals = {key: case.client.get(key) for name, key in case.keys.items() if name != 'lock'}
    snippet = deepcopy(case.api.remote_video['snippet'])
    assert _run(case, reenactment_note=case.note)['status'] == 'public'
    requested = case.api.last_update_body['snippet']
    assert set(requested) == set(case.module._SNIPPET_FIELDS)
    assert requested['description'] == snippet['description'] + '\n\n' + case.note
    assert all(requested[key] == snippet[key] for key in case.module._SNIPPET_FIELDS if key != 'description')
    delta = _receipt(case)['metadata_delta']
    assert delta['original_description_sha256'] == hashlib.sha256(snippet['description'].encode()).hexdigest()
    assert delta['new_description_sha256'] == hashlib.sha256(requested['description'].encode()).hexdigest()
    assert delta['note'] == case.note and delta['if_match'] == '"original_etag"'
    assert {key: case.client.get(key) for key in originals} == originals
    assert _proof(case)['release_status'] == 'public'
    assert _run(case, settle=True, work_dir=None)['status'] == 'public'
    assert case.api.requests.count('videos.update') == 1
    case.captions.insert.assert_not_called()
    case.thumbnails.set.assert_not_called()


def test_already_present_exact_note_is_not_duplicated(note_case):
    case = note_case
    case.api.remote_video['snippet']['description'] += '\n\n' + case.note
    description = case.api.remote_video['snippet']['description']
    assert _run(case, reenactment_note=case.note)['status'] == 'public'
    assert case.api.last_update_body['snippet']['description'] == description
    assert _receipt(case)['metadata_delta']['original_description_sha256'] == _receipt(case)['metadata_delta']['new_description_sha256']


@pytest.mark.parametrize('note', ['', True, {}, 'Please replace the description.',
    'some visuals are AI-generated reconstructions, not archival footage.'])
def test_arbitrary_metadata_edits_cannot_be_smuggled_through_note_argument(case, note):
    with pytest.raises(case.module.BlockedPublicReleaseError, match='note_invalid'):
        _run(case, reenactment_note=note)
    case.videos.update.assert_not_called()


def test_note_language_must_match_frozen_video_language(case):
    with pytest.raises(case.module.BlockedPublicReleaseError, match='note_language_mismatch'):
        _run(case, reenactment_note=case.module.REENACTMENT_NOTES['tr'])
    case.videos.update.assert_not_called()


@pytest.mark.parametrize('field', ['title', 'categoryId', 'tags', 'defaultLanguage', 'defaultAudioLanguage', 'description'])
def test_public_without_exact_preserved_snippet_or_note_remains_uncertain(note_case, field):
    case = note_case
    case.api.note_drop_field = field
    assert _run(case, reenactment_note=case.note)['status'] == 'uncertain'
    with pytest.raises(case.module.BlockedPublicReleaseError):
        _proof(case)
    assert case.module.get_verified_public_recovery_for_source(SOURCE) is None
    assert case.api.requests.count('videos.update') == 1


@pytest.mark.parametrize('etag', [None, '', '*', 'W/"weak"', 'bad\r\nInjected: value', '"unpaired'])
def test_missing_or_unsafe_etag_blocks_metadata_write_before_any_attempt(note_case, etag):
    case = note_case
    case.api.remote_video['etag'] = etag
    with pytest.raises(case.module.BlockedPublicReleaseError, match='etag_missing'):
        _run(case, reenactment_note=case.note)
    case.videos.update.assert_not_called()


def test_if_match_rejection_preserves_metadata_and_never_retries(note_case):
    case = note_case
    original = deepcopy(case.api.remote_video['snippet'])
    case.api.force_precondition_failure = True
    assert _run(case, reenactment_note=case.note)['status'] == 'rejected'
    assert _receipt(case)['request_error']['http_status'] == 412
    assert _receipt(case)['request_error']['reason'] == 'precondition_failed'
    assert case.api.remote_video['snippet'] == original
    assert _run(case, settle=True)['status'] == 'rejected'
    assert case.api.requests.count('videos.update') == 1


@pytest.mark.parametrize('description', ['x' * 4990, 'ş' * 2480], ids=['ascii', 'utf8-byte-limit'])
def test_note_is_not_added_by_truncating_existing_description(note_case, description):
    case = note_case
    case.api.remote_video['snippet']['description'] = description
    with pytest.raises(case.module.BlockedPublicReleaseError, match='description_too_long'):
        _run(case, reenactment_note=case.note)
    case.videos.update.assert_not_called()
    assert _receipt(case)['attempts'] == 0


def test_later_note_removal_invalidates_public_proof_without_another_write(note_case):
    case = note_case
    assert _run(case, reenactment_note=case.note)['status'] == 'public'
    case.api.remote_video['snippet']['description'] = 'The note was removed externally.'
    assert _run(case, settle=True)['status'] == 'uncertain'
    assert case.module.get_verified_public_recovery_for_source(SOURCE) is None
    assert case.api.requests.count('videos.update') == 1


def _set_public_disclosure_readback(case, *, omitted=True, value=None):
    """Model list omitting a field that the preceding update did return."""
    original = case.module._remote
    case.api.disclosure_readback = {'omitted': omitted, 'value': value}
    def remote(*args, **kwargs):
        status = case.api.remote_video['status']
        if status.get('privacyStatus') == 'public':
            readback = case.api.disclosure_readback
            if readback['omitted']:
                status.pop('containsSyntheticMedia', None)
            else:
                status['containsSyntheticMedia'] = readback['value']
        return original(*args, **kwargs)
    case.module._remote = remote


def test_accepted_update_and_omitted_list_disclosure_are_distinct_truthful_evidence(case):
    originals = {key: case.client.get(key) for name, key in case.keys.items() if name != 'lock'}
    _set_public_disclosure_readback(case)
    assert _run(case)['status'] == 'public'
    receipt = _receipt(case)
    assert receipt['update_response_verified'] is True
    assert receipt['public_proof']['contains_synthetic_media'] is None
    assert receipt['public_proof']['synthetic_media_field_present'] is False
    proof = _proof(case)
    assert proof['contains_synthetic_media'] is True  # Original declared classification, not GET data.
    assert proof['synthetic_disclosure_confirmation'] == 'accepted_update_response'
    assert proof['synthetic_disclosure_readback'] is None
    assert proof['synthetic_disclosure_request_accepted'] is True
    assert case.module.get_verified_public_recovery_for_source(SOURCE) == proof
    assert _run(case, settle=True, work_dir=None)['status'] == 'public'
    assert case.api.requests.count('videos.update') == 1
    case.captions.insert.assert_not_called()
    case.thumbnails.set.assert_not_called()
    assert {key: case.client.get(key) for key in originals} == originals


def test_existing_uncertain_receipt_with_accepted_update_settles_only_by_get(case):
    _set_public_disclosure_readback(case)
    _run(case)
    receipt = _receipt(case)
    receipt['status'] = 'uncertain'
    receipt.pop('public_proof')
    receipt.pop('confirmation')
    _write(case.client, case.release_key, receipt)
    case.prep.prepare_publication_recovery_assets.reset_mock()
    _edit(case, 'source', ['updated_at'], '2026-09-06T20:00:00+00:00')
    assert _run(case, settle=True, work_dir=None)['status'] == 'public'
    assert _proof(case)['synthetic_disclosure_confirmation'] == 'accepted_update_response'
    case.prep.prepare_publication_recovery_assets.assert_not_called()
    assert case.api.requests.count('videos.update') == 1


@pytest.mark.parametrize('response', [
    {}, {'id': VIDEO, 'status': {'privacyStatus': 'public'}},
    {'id': VIDEO, 'status': {'privacyStatus': 'public', 'containsSyntheticMedia': False}},
    {'id': 'Different00', 'status': {'privacyStatus': 'public', 'containsSyntheticMedia': True}},
])
def test_missing_list_disclosure_cannot_cover_an_unverified_update(case, response):
    case.api.update_response = response
    _set_public_disclosure_readback(case)
    assert _run(case)['status'] == 'uncertain'
    assert _receipt(case)['update_response_verified'] is False
    assert _run(case, settle=True)['status'] == 'uncertain'
    assert case.module.get_verified_public_recovery_for_source(SOURCE) is None
    assert case.api.requests.count('videos.update') == 1


def test_lost_update_response_plus_omitted_list_field_stays_uncertain(case):
    case.api.update_error = TimeoutError('SECRET response lost')
    _set_public_disclosure_readback(case)
    assert _run(case)['status'] == 'uncertain'
    assert _run(case, settle=True)['status'] == 'uncertain'
    assert 'SECRET' not in json.dumps(_receipt(case))
    assert case.api.requests.count('videos.update') == 1


@pytest.mark.parametrize('value', [False, None, 0, 1, 'true', '', {}, []])
def test_present_false_null_or_malformed_readback_never_uses_accepted_update(case, value):
    _set_public_disclosure_readback(case, omitted=False, value=value)
    assert _run(case)['status'] == 'uncertain'
    assert _receipt(case)['update_response_verified'] is True
    assert case.module.get_verified_public_recovery_for_source(SOURCE) is None
    assert _run(case, settle=True)['status'] == 'uncertain'
    assert case.api.requests.count('videos.update') == 1


@pytest.mark.parametrize('value', [False, None, 1, 'true'])
def test_omission_proof_requires_strict_durable_verified_response(case, value):
    _set_public_disclosure_readback(case)
    _run(case)
    receipt = _receipt(case)
    receipt['update_response_verified'] = value
    _write(case.client, case.release_key, receipt)
    with pytest.raises(case.module.BlockedPublicReleaseError):
        _proof(case)


@pytest.mark.parametrize('mutation', ['presence_missing', 'presence_null', 'request_error', 'request_changed'])
def test_omission_proof_requires_precise_presence_and_uncorrupted_request_evidence(case, mutation):
    _set_public_disclosure_readback(case)
    _run(case)
    receipt = _receipt(case)
    if mutation == 'presence_missing':
        receipt['public_proof'].pop('synthetic_media_field_present')
    elif mutation == 'presence_null':
        receipt['public_proof']['synthetic_media_field_present'] = None
    elif mutation == 'request_error':
        receipt['request_error'] = {'status': 'uncertain'}
    else:
        receipt['release_request']['contains_synthetic_media'] = False
    _write(case.client, case.release_key, receipt)
    with pytest.raises(case.module.BlockedPublicReleaseError):
        _proof(case)


def test_explicit_negative_observation_cannot_be_revived_by_later_omission(case):
    _set_public_disclosure_readback(case)
    assert _run(case)['status'] == 'public'
    case.api.disclosure_readback = {'omitted': False, 'value': False}
    assert _run(case, settle=True)['status'] == 'uncertain'
    assert _receipt(case)['disclosure_contradiction_observed'] is True
    case.api.disclosure_readback = {'omitted': True, 'value': None}
    assert _run(case, settle=True)['status'] == 'uncertain'
    assert case.module.get_verified_public_recovery_for_source(SOURCE) is None
    # An actual later positive GET is separate new evidence, not a score/policy bypass.
    case.api.disclosure_readback = {'omitted': False, 'value': True}
    assert _run(case, settle=True)['status'] == 'public'
    assert _proof(case)['synthetic_disclosure_confirmation'] == 'readback'
    assert case.api.requests.count('videos.update') == 1


def test_omitted_disclosure_still_requires_exact_note_snippet_and_serving_caption(note_case):
    case = note_case
    _set_public_disclosure_readback(case)
    assert _run(case, reenactment_note=case.note)['status'] == 'public'
    assert _proof(case)['synthetic_disclosure_readback'] is None
    case.api.caption_tracks = [_track(status='syncing')]
    assert _run(case, settle=True)['status'] == 'uncertain'
    case.api.caption_tracks = [_track()]
    case.api.remote_video['snippet']['description'] = 'Note removed externally.'
    assert _run(case, settle=True)['status'] == 'uncertain'
    assert case.module.get_verified_public_recovery_for_source(SOURCE) is None
    assert case.api.requests.count('videos.update') == 1
