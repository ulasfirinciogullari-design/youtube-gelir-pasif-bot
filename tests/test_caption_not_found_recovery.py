import json
from types import SimpleNamespace
from unittest.mock import Mock, call

from googleapiclient.errors import HttpError
import httplib2
import pytest

from app.services import youtube


VIDEO_ID = 'hGsspOyWEIU'


def error(status=404, *, reason='videoNotFound', payload=None):
    raw = json.dumps({'error': {'code': status, 'message': 'Rejected insert',
                               'errors': [{'reason': reason}]}}).encode() if payload is None else payload
    return HttpError(httplib2.Response({'status': str(status), 'reason': 'Rejected insert'}), raw)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    path = tmp_path / 'captions.tr.srt'
    path.write_text('1\n00:00:00,000 --> 00:00:02,000\nMevcut altyazı.\n', encoding='utf-8')
    service, captions, videos = Mock(), Mock(), Mock()
    service.captions.return_value = captions
    service.videos.return_value = videos
    captions.insert.return_value.execute.return_value = {'id': 'existing-caption'}
    videos.list.return_value.execute.return_value = {'items': [
        {'id': VIDEO_ID, 'status': {'uploadStatus': 'processed', 'privacyStatus': 'private'}}
    ]}
    factory = Mock(return_value=service)
    media = Mock(side_effect=lambda *args, **kwargs: object())
    sleeps = Mock()
    monkeypatch.setattr(youtube, '_service', factory)
    monkeypatch.setattr(youtube, 'MediaFileUpload', media)
    monkeypatch.setattr(youtube.time, 'sleep', sleeps)
    return SimpleNamespace(path=path, service=service, captions=captions, videos=videos,
                           factory=factory, media=media, sleeps=sleeps, credentials=object())


def upload(case, *, video_id=VIDEO_ID):
    return youtube.upload_caption_with_credentials(case.credentials, video_id, str(case.path), 'tr', name='TR captions')


def assert_no_video_mutation(case):
    case.videos.insert.assert_not_called()
    case.videos.update.assert_not_called()
    case.videos.delete.assert_not_called()


def test_normal_success_has_no_new_status_read_delay_or_retry(setup):
    assert upload(setup) == {'id': 'existing-caption'}
    setup.factory.assert_called_once_with(setup.credentials)
    setup.captions.insert.return_value.execute.assert_called_once_with(num_retries=2)
    setup.service.videos.assert_not_called()
    setup.sleeps.assert_not_called()
    assert_no_video_mutation(setup)


@pytest.mark.parametrize('upload_status', ['uploaded', 'processed'])
def test_explicit_404_retries_same_caption_only_after_exact_private_target_proof(setup, upload_status):
    failure = error()
    setup.captions.insert.return_value.execute.side_effect = [failure, {'id': 'caption-ok'}]
    setup.videos.list.return_value.execute.return_value['items'][0]['status']['uploadStatus'] = upload_status
    before = setup.path.read_bytes()
    assert upload(setup) == {'id': 'caption-ok'}
    assert setup.path.read_bytes() == before
    assert setup.captions.insert.call_count == 2
    setup.videos.list.assert_called_once_with(part='status', id=VIDEO_ID, maxResults=1)
    setup.videos.list.return_value.execute.assert_called_once_with(num_retries=2)
    setup.sleeps.assert_called_once_with(2)
    setup.factory.assert_called_once_with(setup.credentials)
    for request in setup.captions.insert.call_args_list:
        assert request.kwargs['body']['snippet'] == {'videoId': VIDEO_ID, 'language': 'tr',
                                                   'name': 'TR captions', 'isDraft': False}
        assert request.kwargs['part'] == 'snippet'
    assert setup.captions.insert.return_value.execute.call_args_list == [call(num_retries=2)] * 2
    assert_no_video_mutation(setup)


def test_total_caption_attempts_are_three_with_only_two_bounded_delays(setup):
    failures = [error(), error(), error()]
    setup.captions.insert.return_value.execute.side_effect = failures
    with pytest.raises(HttpError) as caught:
        upload(setup)
    assert caught.value is failures[-1]
    assert setup.captions.insert.call_count == 3
    assert setup.videos.list.call_count == 2
    assert setup.sleeps.call_args_list == [call(2), call(5)]
    assert_no_video_mutation(setup)


def test_third_attempt_can_succeed_without_more_reads_or_delays(setup):
    setup.captions.insert.return_value.execute.side_effect = [error(), error(), {'id': 'caption-ok'}]
    assert upload(setup) == {'id': 'caption-ok'}
    assert setup.captions.insert.call_count == 3 and setup.videos.list.call_count == 2
    assert setup.sleeps.call_args_list == [call(2), call(5)]


@pytest.mark.parametrize('failure', [
    error(400), error(401), error(403), error(409), error(429), error(500),
    error(reason='captionNotFound'), error(reason='forbidden'),
    TimeoutError('ambiguous network outcome'), ConnectionError('connection dropped'),
    RuntimeError('non-HTTP provider exception'),
])
def test_other_errors_never_add_a_custom_retry_or_status_read(setup, failure):
    setup.captions.insert.return_value.execute.side_effect = failure
    with pytest.raises(type(failure)) as caught:
        upload(setup)
    assert caught.value is failure
    assert setup.captions.insert.call_count == 1
    setup.service.videos.assert_not_called()
    setup.sleeps.assert_not_called()
    assert_no_video_mutation(setup)


@pytest.mark.parametrize('raw', [
    b'not JSON', b'{"error":{"code":404,"message":"videoNotFound"}}',
    b'{"error":{"code":"404","errors":[{"reason":"videoNotFound"}]}}',
    b'{"error":{"code":404,"errors":[]}}',
    b'{"error":{"code":404,"errors":[{"reason":"videoNotFound"},{"reason":"forbidden"}]}}',
    b'{"error":{"code":404,"errors":["videoNotFound"]}}',
    b'{"error":{"code":404,"code":403,"errors":[{"reason":"videoNotFound"}]}}',
    b'{"error":{"code":404,"errors":[{"reason":"videoNotFound"}],"extra":NaN}}',
    b'X' * (16 * 1024 + 1), b'\xff\xfe',
])
def test_reason_must_be_explicit_bounded_unambiguous_json(setup, raw):
    failure = error(payload=raw)
    setup.captions.insert.return_value.execute.side_effect = failure
    with pytest.raises(HttpError) as caught:
        upload(setup)
    assert caught.value is failure
    setup.service.videos.assert_not_called()
    setup.sleeps.assert_not_called()


@pytest.mark.parametrize('response', [
    {}, {'items': []}, {'items': {}}, {'items': [None]},
    {'items': [{'id': 'DIFFERENTID', 'status': {'uploadStatus': 'processed', 'privacyStatus': 'private'}}]},
    {'items': [{'status': {'uploadStatus': 'processed', 'privacyStatus': 'private'}}]},
    {'items': [{'id': VIDEO_ID, 'status': {'uploadStatus': 'processed', 'privacyStatus': 'public'}}]},
    {'items': [{'id': VIDEO_ID, 'status': {'uploadStatus': 'processed', 'privacyStatus': 'unlisted'}}]},
    {'items': [{'id': VIDEO_ID, 'status': {'uploadStatus': 'failed', 'privacyStatus': 'private'}}]},
    {'items': [{'id': VIDEO_ID, 'status': {'uploadStatus': 'rejected', 'privacyStatus': 'private'}}]},
    {'items': [{'id': VIDEO_ID, 'status': {'privacyStatus': 'private'}}]},
    {'items': [{'id': VIDEO_ID, 'status': {'uploadStatus': 'processed'}}]},
    {'items': [{'id': VIDEO_ID, 'status': {'uploadStatus': 'processed', 'privacyStatus': 'private'}}] * 2},
])
def test_missing_wrong_or_nonprivate_target_never_retries_caption(setup, response):
    failure = error()
    setup.captions.insert.return_value.execute.side_effect = failure
    setup.videos.list.return_value.execute.return_value = response
    with pytest.raises(HttpError) as caught:
        upload(setup)
    assert caught.value is failure
    assert setup.captions.insert.call_count == 1
    setup.sleeps.assert_not_called()
    assert_no_video_mutation(setup)


@pytest.mark.parametrize('lookup_failure', [error(403), error(404), TimeoutError('read failed')])
def test_status_read_failure_preserves_original_caption_error(setup, lookup_failure):
    failure = error()
    setup.captions.insert.return_value.execute.side_effect = failure
    setup.videos.list.return_value.execute.side_effect = lookup_failure
    with pytest.raises(HttpError) as caught:
        upload(setup)
    assert caught.value is failure
    assert setup.captions.insert.call_count == 1
    setup.sleeps.assert_not_called()


def test_status_must_still_be_verified_before_each_extra_attempt(setup):
    first, second = error(), error()
    setup.captions.insert.return_value.execute.side_effect = [first, second]
    setup.videos.list.return_value.execute.side_effect = [
        {'items': [{'id': VIDEO_ID, 'status': {'uploadStatus': 'processed', 'privacyStatus': 'private'}}]},
        {'items': []},
    ]
    with pytest.raises(HttpError) as caught:
        upload(setup)
    assert caught.value is second
    assert setup.captions.insert.call_count == 2
    setup.sleeps.assert_called_once_with(2)


def test_ambiguous_error_after_first_explicit_rejection_is_not_retried(setup):
    ambiguous = TimeoutError('unknown insert outcome')
    setup.captions.insert.return_value.execute.side_effect = [error(), ambiguous]
    with pytest.raises(TimeoutError) as caught:
        upload(setup)
    assert caught.value is ambiguous
    assert setup.captions.insert.call_count == 2 and setup.videos.list.call_count == 1
    setup.sleeps.assert_called_once_with(2)
