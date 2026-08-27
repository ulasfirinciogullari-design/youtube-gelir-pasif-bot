from pathlib import Path
import httpx
from app.config import settings

PEXELS_VIDEO_SEARCH = 'https://api.pexels.com/v1/videos/search'


def _headers() -> dict[str, str]:
    if not settings.pexels_api_key:
        raise RuntimeError('PEXELS_API_KEY is not configured')
    return {'Authorization': settings.pexels_api_key}


def search_videos(query: str, per_page: int = 8, orientation: str = 'landscape') -> list[dict]:
    response = httpx.get(
        PEXELS_VIDEO_SEARCH,
        headers=_headers(),
        params={
            'query': query,
            'per_page': min(max(per_page, 1), 80),
            'orientation': orientation,
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json().get('videos', [])


def _pick_file(video: dict, target_height: int = 1080) -> dict | None:
    files = [f for f in video.get('video_files', []) if f.get('link')]
    if not files:
        return None

    # Prefer HD files close to 1080p, landscape and MP4 where possible.
    def score(item: dict):
        height = item.get('height') or 0
        width = item.get('width') or 0
        file_type = item.get('file_type') or ''
        landscape = 1 if width >= height else 0
        mp4 = 1 if 'mp4' in file_type else 0
        return (landscape, mp4, -abs(height - target_height), width * height)

    return max(files, key=score)


def find_broll(query: str, per_page: int = 12) -> list[dict]:
    results = []
    for video in search_videos(query, per_page=per_page):
        file = _pick_file(video)
        if not file:
            continue
        creator = video.get('user') or {}
        results.append({
            'pexels_id': video.get('id'),
            'duration': video.get('duration'),
            'page_url': video.get('url'),
            'creator_name': creator.get('name'),
            'creator_url': creator.get('url'),
            'download_url': file.get('link'),
            'width': file.get('width'),
            'height': file.get('height'),
            'file_type': file.get('file_type'),
        })
    return results


def download_broll(item: dict, output_path: str | Path) -> str:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream('GET', item['download_url'], timeout=120, follow_redirects=True) as response:
        response.raise_for_status()
        with output.open('wb') as f:
            for chunk in response.iter_bytes():
                f.write(chunk)
    return str(output)
