from __future__ import annotations

from pathlib import Path
from typing import Callable

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


SCOPES = [
    'https://www.googleapis.com/auth/youtube.upload',
    'https://www.googleapis.com/auth/youtube.readonly',
    'https://www.googleapis.com/auth/youtube.force-ssl',
]

PRIVATE_STATUS = 'private'
UPLOAD_CHUNK_SIZE = 8 * 1024 * 1024


def _service(credentials: Credentials):
    return build('youtube', 'v3', credentials=credentials, cache_discovery=False)


def _video_file(file_path: str) -> Path:
    path = Path(file_path)
    if not path.is_file() or path.stat().st_size <= 0:
        raise FileNotFoundError(str(path))
    return path


def _private_status(privacy_status: str) -> str:
    if str(privacy_status or '').strip().lower() != PRIVATE_STATUS:
        raise ValueError('Initial YouTube uploads must use private visibility')
    return PRIVATE_STATUS


def upload_video_with_credentials(
    credentials: Credentials,
    file_path: str,
    title: str,
    description: str,
    *,
    privacy_status: str = PRIVATE_STATUS,
    tags: list[str] | None = None,
    category_id: str = '28',
    progress_callback: Callable[[float], None] | None = None,
) -> dict:
    path = _video_file(file_path)
    privacy_status = _private_status(privacy_status)
    youtube = _service(credentials)

    normalized_tags: list[str] = []
    seen_tags: set[str] = set()
    tag_budget = 0
    for value in tags or []:
        tag = str(value or '').strip()[:500]
        key = tag.casefold()
        separator_cost = 1 if normalized_tags else 0
        if tag_budget + separator_cost + len(tag) > 500:
            continue
        if tag and key not in seen_tags:
            normalized_tags.append(tag)
            seen_tags.add(key)
            tag_budget += separator_cost + len(tag)
        if len(normalized_tags) >= 30:
            break

    snippet = {
        'title': (str(title or '').strip() or 'Video')[:100],
        'description': str(description or '')[:5000],
        'categoryId': str(category_id or '28'),
    }
    if normalized_tags:
        snippet['tags'] = normalized_tags

    request = youtube.videos().insert(
        part='snippet,status',
        body={
            'snippet': snippet,
            'status': {
                # This value is deliberately non-configurable at the upload
                # boundary. Public release must be a separate confirmed action.
                'privacyStatus': PRIVATE_STATUS,
                'selfDeclaredMadeForKids': False,
            },
        },
        media_body=MediaFileUpload(
            str(path),
            mimetype='video/mp4',
            resumable=True,
            chunksize=UPLOAD_CHUNK_SIZE,
        ),
        notifySubscribers=False,
    )

    response = None
    while response is None:
        # The client library keeps the same resumable session URI while it
        # retries transient chunk failures, preventing a second videos.insert.
        status, response = request.next_chunk(num_retries=3)
        if status is not None and progress_callback:
            progress_callback(max(0.0, min(1.0, float(status.progress()))))

    if not isinstance(response, dict) or not str(response.get('id') or '').strip():
        raise RuntimeError('YouTube upload completed without a video ID')
    response_status = response.get('status') if isinstance(response.get('status'), dict) else {}
    returned_privacy = response_status.get('privacyStatus')
    if returned_privacy and returned_privacy != PRIVATE_STATUS:
        raise RuntimeError('YouTube did not preserve private upload visibility')
    if progress_callback:
        progress_callback(1.0)
    return response


def upload_caption_with_credentials(
    credentials: Credentials,
    video_id: str,
    caption_path: str,
    language: str,
    *,
    name: str | None = None,
) -> dict:
    video_id = str(video_id or '').strip()
    if not video_id:
        raise ValueError('video_id is required')
    path = Path(caption_path)
    if not path.is_file() or path.stat().st_size <= 0:
        raise FileNotFoundError(str(path))
    youtube = _service(credentials)
    return youtube.captions().insert(
        part='snippet',
        body={
            'snippet': {
                'videoId': video_id,
                'language': str(language or 'tr')[:12],
                'name': str(name or f'{(language or "tr").upper()} captions')[:150],
                'isDraft': False,
            }
        },
        media_body=MediaFileUpload(
            str(path),
            mimetype='application/octet-stream',
            resumable=False,
        ),
    ).execute(num_retries=2)


def upload_video(
    access_token: str,
    refresh_token: str,
    client_id: str,
    client_secret: str,
    file_path: str,
    title: str,
    description: str,
    privacy_status: str = PRIVATE_STATUS,
) -> dict:
    """Backward-compatible in-memory wrapper; it never persists a token."""
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
