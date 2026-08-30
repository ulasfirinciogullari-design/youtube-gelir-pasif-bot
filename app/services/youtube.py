from __future__ import annotations

from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = [
    'https://www.googleapis.com/auth/youtube.upload',
    'https://www.googleapis.com/auth/youtube.readonly',
    'https://www.googleapis.com/auth/youtube.force-ssl',
]


def _service(credentials: Credentials):
    return build('youtube', 'v3', credentials=credentials, cache_discovery=False)


def upload_video_with_credentials(
    credentials: Credentials,
    file_path: str,
    title: str,
    description: str,
    *,
    privacy_status: str = 'private',
    tags: list[str] | None = None,
    category_id: str = '28',
) -> dict:
    if privacy_status != 'private':
        raise ValueError('Initial YouTube uploads must use private visibility')
    youtube = _service(credentials)
    snippet = {
        'title': (title or 'Video')[:100],
        'description': description or '',
        'categoryId': str(category_id or '28'),
    }
    if tags:
        snippet['tags'] = [str(tag)[:500] for tag in tags[:30] if str(tag).strip()]
    request = youtube.videos().insert(
        part='snippet,status',
        body={
            'snippet': snippet,
            'status': {
                'privacyStatus': privacy_status,
                'selfDeclaredMadeForKids': False,
            },
        },
        media_body=MediaFileUpload(file_path, mimetype='video/mp4', resumable=True, chunksize=8 * 1024 * 1024),
        notifySubscribers=False,
    )
    response = None
    while response is None:
        _, response = request.next_chunk()
    return response


def upload_caption_with_credentials(
    credentials: Credentials,
    video_id: str,
    caption_path: str,
    language: str,
    *,
    name: str | None = None,
) -> dict:
    path = Path(caption_path)
    if not path.exists() or path.stat().st_size == 0:
        raise FileNotFoundError(str(path))
    youtube = _service(credentials)
    return youtube.captions().insert(
        part='snippet',
        body={
            'snippet': {
                'videoId': video_id,
                'language': language or 'tr',
                'name': name or f'{(language or "tr").upper()} captions',
                'isDraft': False,
            }
        },
        media_body=MediaFileUpload(str(path), mimetype='application/octet-stream', resumable=False),
    ).execute()


def upload_video(
    access_token: str,
    refresh_token: str,
    client_id: str,
    client_secret: str,
    file_path: str,
    title: str,
    description: str,
    privacy_status: str = 'private',
) -> dict:
    credentials = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri='https://oauth2.googleapis.com/token',
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )
    return upload_video_with_credentials(
        credentials,
        file_path,
        title,
        description,
        privacy_status=privacy_status,
    )
