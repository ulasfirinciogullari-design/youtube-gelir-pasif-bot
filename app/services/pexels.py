import httpx
from app.config import settings


def search_videos(query: str, per_page: int = 8) -> list[dict]:
    if not settings.pexels_api_key:
        raise RuntimeError('PEXELS_API_KEY is not configured')

    response = httpx.get(
        'https://api.pexels.com/v1/videos/search',
        headers={'Authorization': settings.pexels_api_key},
        params={'query': query, 'per_page': per_page, 'orientation': 'landscape'},
        timeout=30,
    )
    response.raise_for_status()
    return response.json().get('videos', [])
