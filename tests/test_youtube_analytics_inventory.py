from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from app.services import youtube_analytics_inventory as inventory

CHANNEL = 'UCgvESYtYbn2w9R2ExBOF_cw'
PLAYLIST = 'UUgvESYtYbn2w9R2ExBOF_cw'
PUBLIC, PRIVATE, UNLISTED = 'a1234567890', 'b1234567890', 'c1234567890'


@pytest.fixture
def box(monkeypatch):
    channel = {'items': [{'id': CHANNEL, 'contentDetails': {'relatedPlaylists': {'uploads': PLAYLIST}}}]}
    playlist = {'items': [{'contentDetails': {'videoId': v}} for v in (PUBLIC, PRIVATE, UNLISTED)]}
    videos = {'items': [{'id': v, 'snippet': {'channelId': CHANNEL, 'title': 'Current title'},
        'status': {'privacyStatus': status}} for v, status in (
            (PUBLIC, 'public'), (PRIVATE, 'private'), (UNLISTED, 'unlisted'))]}
    service = Mock()
    service.channels.return_value.list.return_value.execute.side_effect = lambda **kw: deepcopy(channel)
    service.playlistItems.return_value.list.return_value.execute.side_effect = lambda **kw: deepcopy(playlist)
    service.videos.return_value.list.return_value.execute.side_effect = lambda **kw: deepcopy(videos)
    factory = Mock(return_value=service)
    monkeypatch.setattr(inventory.metrics, '_service', factory)
    return SimpleNamespace(channel=channel, playlist=playlist, videos=videos, service=service, factory=factory)


def test_only_current_public_uploads_from_verified_owner_are_returned(box):
    credentials = object()
    assert inventory.public_uploads(credentials, CHANNEL) == {PUBLIC: 'Current title'}
    box.factory.assert_called_once_with(credentials)
    assert box.service.channels.return_value.list.call_args.kwargs['mine'] is True
    assert box.service.playlistItems.return_value.list.call_args.kwargs['playlistId'] == PLAYLIST
    for resource in (box.service.channels, box.service.playlistItems, box.service.videos):
        resource.return_value.list.return_value.execute.assert_called_once_with(num_retries=0)
    assert box.service.playlistItems.return_value.list.call_args.kwargs['maxResults'] == 50
    box.service.close.assert_called_once()


@pytest.mark.parametrize('damage', ['other_owner', 'two_channels', 'playlist_url', 'bad_video',
    'duplicate_video', 'too_many', 'foreign_video', 'unexpected_video', 'duplicate_response', 'missing_privacy'])
def test_unverified_inventory_is_rejected_and_service_is_closed(box, damage):
    if damage == 'other_owner': box.channel['items'][0]['id'] = 'other-channel'
    if damage == 'two_channels': box.channel['items'] *= 2
    if damage == 'playlist_url': box.channel['items'][0]['contentDetails']['relatedPlaylists']['uploads'] = 'https://foreign.test'
    if damage == 'bad_video': box.playlist['items'][0]['contentDetails']['videoId'] = '../bad'
    if damage == 'duplicate_video': box.playlist['items'].append(deepcopy(box.playlist['items'][0]))
    if damage == 'too_many': box.playlist['items'] *= 17
    if damage == 'foreign_video': box.videos['items'][0]['snippet']['channelId'] = 'other-channel'
    if damage == 'unexpected_video': box.videos['items'][0]['id'] = 'd1234567890'
    if damage == 'duplicate_response': box.videos['items'].append(deepcopy(box.videos['items'][0]))
    if damage == 'missing_privacy': box.videos['items'][0]['status'] = {}
    with pytest.raises(inventory.metrics.YouTubeMetricsError): inventory.public_uploads(object(), CHANNEL)
    box.service.close.assert_called_once()


def test_empty_uploads_and_removed_videos_are_omitted_without_inventing_metrics(box):
    box.videos['items'] = []
    assert inventory.public_uploads(object(), CHANNEL) == {}
    box.playlist['items'] = []
    box.service.videos.reset_mock()
    assert inventory.public_uploads(object(), CHANNEL) == {}
    box.service.videos.assert_not_called()
