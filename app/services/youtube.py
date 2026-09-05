from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import time
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
_CAPTION_NOT_FOUND_DELAYS = (2, 5)


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
    default_language: str | None = None,
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
    normalized_language = str(default_language or '').strip()[:24]
    if normalized_language:
        snippet['defaultLanguage'] = normalized_language
        snippet['defaultAudioLanguage'] = normalized_language

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


def upload_thumbnail_with_credentials(
    credentials: Credentials,
    video_id: str,
    thumbnail_path: str,
) -> dict:
    video_id = str(video_id or '').strip()
    if not video_id:
        raise ValueError('video_id is required')
    path = _video_file(thumbnail_path)
    suffix = path.suffix.casefold()
    mimetype = {
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
    }.get(suffix)
    if not mimetype:
        raise ValueError('YouTube thumbnail must be JPEG or PNG')
    return _service(credentials).thumbnails().set(
        videoId=video_id,
        media_body=MediaFileUpload(
            str(path),
            mimetype=mimetype,
            resumable=False,
        ),
    ).execute(num_retries=2)


def get_video_status_with_credentials(
    credentials: Credentials,
    video_id: str,
) -> dict:
    video_id = str(video_id or '').strip()
    if not video_id:
        raise ValueError('video_id is required')
    response = _service(credentials).videos().list(
        part='status',
        id=video_id,
        maxResults=1,
    ).execute(num_retries=2)
    items = response.get('items') if isinstance(response, dict) else None
    if not isinstance(items, list) or len(items) != 1:
        raise RuntimeError('YouTube video status could not be verified')
    item = items[0] if isinstance(items[0], dict) else {}
    status = item.get('status') if isinstance(item.get('status'), dict) else {}
    return dict(status)


def set_video_release_with_credentials(
    credentials: Credentials,
    video_id: str,
    release_mode: str,
    *,
    publish_at: str | None = None,
) -> dict:
    video_id = str(video_id or '').strip()
    if not video_id:
        raise ValueError('video_id is required')
    release_mode = str(release_mode or '').strip().casefold()
    status = {'selfDeclaredMadeForKids': False}
    if release_mode == 'public':
        status['privacyStatus'] = 'public'
    elif release_mode == 'scheduled':
        try:
            scheduled = datetime.fromisoformat(str(publish_at or ''))
        except (TypeError, ValueError) as exc:
            raise ValueError('publish_at is invalid') from exc
        if scheduled.tzinfo is None:
            raise ValueError('publish_at is invalid')
        scheduled = scheduled.astimezone(timezone.utc)
        if scheduled <= datetime.now(timezone.utc):
            raise ValueError('publish_at must be in the future')
        status.update({
            'privacyStatus': 'private',
            'publishAt': scheduled.isoformat().replace('+00:00', 'Z'),
        })
    else:
        raise ValueError('release_mode must be public or scheduled')
    response = _service(credentials).videos().update(
        part='status',
        body={'id': video_id, 'status': status},
    ).execute(num_retries=3)
    if not isinstance(response, dict):
        raise RuntimeError('YouTube release did not return a video resource')
    response_status = (
        response.get('status')
        if isinstance(response.get('status'), dict)
        else {}
    )
    expected_privacy = 'public' if release_mode == 'public' else 'private'
    if (
        response_status.get('privacyStatus')
        and response_status.get('privacyStatus') != expected_privacy
    ):
        raise RuntimeError('YouTube release status did not match the request')
    return response


def _caption_video_not_found(error: Exception) -> bool:
    """Only an explicit rejected insert can authorize another caption insert."""
    try:
        if getattr(error.resp, 'status', None) != 404:
            return False
        content = error.content
        if not isinstance(content, bytes) or not 1 <= len(content) <= 16 * 1024:
            return False

        def unique_fields(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('Duplicate caption error field')
                result[key] = value
            return result

        def invalid_number(_value):
            raise ValueError('Invalid caption error value')

        payload = json.loads(content.decode('utf-8'), object_pairs_hook=unique_fields,
                             parse_constant=invalid_number)
        detail = payload.get('error') if isinstance(payload, dict) else None
        reasons = detail.get('errors') if isinstance(detail, dict) else None
        return bool(
            isinstance(detail, dict) and type(detail.get('code')) is int and detail['code'] == 404
            and isinstance(reasons, list) and 1 <= len(reasons) <= 10
            and all(isinstance(item, dict) and item.get('reason') == 'videoNotFound' for item in reasons)
        )
    except Exception:
        return False


def _private_caption_target_exists(youtube, video_id: str) -> bool:
    """Read the exact target with the same credentials; never alter its status."""
    try:
        response = youtube.videos().list(
            part='status', id=video_id, maxResults=1,
        ).execute(num_retries=2)
        items = response.get('items') if isinstance(response, dict) else None
        if not isinstance(items, list) or len(items) != 1:
            return False
        item = items[0]
        status = item.get('status') if isinstance(item, dict) else None
        return bool(
            isinstance(item, dict) and item.get('id') == video_id and isinstance(status, dict)
            and status.get('privacyStatus') == PRIVATE_STATUS
            and status.get('uploadStatus') in {'uploaded', 'processed'}
        )
    except Exception:
        return False


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
    from googleapiclient.errors import HttpError

    youtube = _service(credentials)
    for attempt in range(len(_CAPTION_NOT_FOUND_DELAYS) + 1):
        try:
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
        except HttpError as exc:
            if (attempt == len(_CAPTION_NOT_FOUND_DELAYS) or not _caption_video_not_found(exc)
                    or not _private_caption_target_exists(youtube, video_id)):
                raise
            # API documents videoNotFound, not a propagation guarantee. A
            # short delay is our bounded inference only after an independent
            # read proves this same uploaded/processed private target exists.
            time.sleep(_CAPTION_NOT_FOUND_DELAYS[attempt])


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
