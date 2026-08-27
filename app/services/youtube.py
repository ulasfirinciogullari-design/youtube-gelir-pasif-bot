from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = [
    'https://www.googleapis.com/auth/youtube.upload',
    'https://www.googleapis.com/auth/yt-analytics.readonly',
]


def upload_video(access_token: str, refresh_token: str, client_id: str, client_secret: str, file_path: str, title: str, description: str, privacy_status: str = 'private') -> dict:
    creds = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri='https://oauth2.googleapis.com/token',
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )
    youtube = build('youtube', 'v3', credentials=creds)
    request = youtube.videos().insert(
        part='snippet,status',
        body={
            'snippet': {
                'title': title[:100],
                'description': description,
            },
            'status': {'privacyStatus': privacy_status},
        },
        media_body=MediaFileUpload(file_path, resumable=True),
    )
    response = None
    while response is None:
        _, response = request.next_chunk()
    return response
