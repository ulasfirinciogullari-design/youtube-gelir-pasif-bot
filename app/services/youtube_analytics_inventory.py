"""Read current public uploads from the explicitly authorized owner channel.

Channel reconnection changes internal publication bindings, not ownership of
older public videos. This inventory is for Analytics only and grants no upload,
release, repair or quality authority. At most the 50 newest uploads are read.
"""
import re

from app.services import youtube_metrics as metrics

MAX_UPLOADS = 50


def _require(value):
    if not value:
        raise metrics.YouTubeMetricsError('invalid_response')


def _items(response, maximum):
    _require(type(response) is dict)
    items = response.get('items', [])
    _require(type(items) is list and len(items) <= maximum and all(type(v) is dict for v in items))
    return items


def public_uploads(credentials, channel_id):
    """Verify mine=True, playlist provenance, owner ID and current public status."""
    service = metrics._service(credentials)
    try:
        channels = _items(service.channels().list(part='contentDetails', mine=True,
            fields='items(id,contentDetails/relatedPlaylists/uploads)').execute(num_retries=0), 1)
        _require(len(channels) == 1 and channels[0].get('id') == channel_id)
        playlist = channels[0].get('contentDetails', {}).get('relatedPlaylists', {}).get('uploads')
        _require(type(playlist) is str and re.fullmatch('[A-Za-z0-9_-]{10,128}', playlist))
        items = _items(service.playlistItems().list(part='contentDetails', playlistId=playlist,
            maxResults=MAX_UPLOADS, fields='items/contentDetails/videoId').execute(num_retries=0), MAX_UPLOADS)
        ids = [row.get('contentDetails', {}).get('videoId') for row in items]
        _require(all(type(video) is str and metrics._VIDEO.fullmatch(video) for video in ids)
            and len(ids) == len(set(ids)))
        if not ids:
            return {}
        videos = _items(service.videos().list(part='snippet,status', id=','.join(ids), maxResults=MAX_UPLOADS,
            fields='items(id,snippet(channelId,title),status/privacyStatus)').execute(num_retries=0), MAX_UPLOADS)
        public, seen = {}, set()
        for video in videos:
            identity, snippet, status = video.get('id'), video.get('snippet'), video.get('status')
            _require(type(identity) is str and identity in ids and identity not in seen
                and type(snippet) is dict and snippet.get('channelId') == channel_id
                and type(status) is dict and status.get('privacyStatus') in ('public', 'private', 'unlisted'))
            seen.add(identity)
            if status['privacyStatus'] == 'public':
                public[identity] = metrics._text(snippet.get('title'), 100)
        return public
    finally:
        try: service.close()
        except Exception: pass
