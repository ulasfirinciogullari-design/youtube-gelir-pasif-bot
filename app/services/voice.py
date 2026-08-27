from pathlib import Path
import httpx
import redis
from app.config import settings

ELEVENLABS_BASE = 'https://api.elevenlabs.io/v1'
SELECTED_VOICE_ID_KEY = 'youtube_factory:selected_voice_id'
SELECTED_VOICE_NAME_KEY = 'youtube_factory:selected_voice_name'


def _headers() -> dict[str, str]:
    if not settings.elevenlabs_api_key:
        raise RuntimeError('ELEVENLABS_API_KEY is not configured')
    return {'xi-api-key': settings.elevenlabs_api_key}


def _redis():
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


def list_turkish_voice_candidates(page_size: int = 20) -> list[dict]:
    response = httpx.get(
        f'{ELEVENLABS_BASE}/shared-voices',
        headers=_headers(),
        params={
            'language': 'tr',
            'page_size': min(max(page_size, 1), 100),
            'sort': 'trending',
            'include_custom_rates': 'false',
            'include_live_moderated': 'false',
        },
        timeout=30,
    )
    response.raise_for_status()
    voices = response.json().get('voices', [])

    candidates = []
    for voice in voices:
        preview_url = voice.get('preview_url')
        for verified in voice.get('verified_languages') or []:
            if verified.get('language') == 'tr' and verified.get('preview_url'):
                preview_url = verified['preview_url']
                break
        if not preview_url:
            continue
        candidates.append({
            'voice_id': voice.get('voice_id'),
            'public_owner_id': voice.get('public_owner_id'),
            'name': voice.get('name'),
            'gender': voice.get('gender'),
            'age': voice.get('age'),
            'accent': voice.get('accent'),
            'description': voice.get('description'),
            'use_case': voice.get('use_case'),
            'category': voice.get('category'),
            'preview_url': preview_url,
            'rate': voice.get('rate'),
        })
    return candidates


def ensure_shared_voice_added(public_owner_id: str, voice_id: str, name: str | None = None) -> None:
    response = httpx.post(
        f'{ELEVENLABS_BASE}/voices/add/{public_owner_id}/{voice_id}',
        headers={**_headers(), 'Content-Type': 'application/json'},
        json={
            'new_name': (name or f'Audition {voice_id[:8]}')[:100],
            'bookmarked': True,
        },
        timeout=30,
    )
    if response.status_code == 200:
        return

    body = response.text.lower()
    if response.status_code in (400, 409, 422) and any(
        marker in body for marker in ('already', 'exists', 'duplicate')
    ):
        return
    response.raise_for_status()


def save_selected_voice(public_owner_id: str, voice_id: str, name: str) -> dict:
    ensure_shared_voice_added(public_owner_id, voice_id, name)
    client = _redis()
    client.set(SELECTED_VOICE_ID_KEY, voice_id)
    client.set(SELECTED_VOICE_NAME_KEY, name)
    return {'voice_id': voice_id, 'name': name}


def get_selected_voice() -> dict:
    try:
        client = _redis()
        voice_id = client.get(SELECTED_VOICE_ID_KEY)
        name = client.get(SELECTED_VOICE_NAME_KEY)
        if voice_id:
            return {'voice_id': voice_id, 'name': name or 'Selected voice', 'source': 'redis'}
    except Exception:
        pass

    if settings.elevenlabs_voice_id:
        return {
            'voice_id': settings.elevenlabs_voice_id,
            'name': 'Environment voice',
            'source': 'environment',
        }
    return {'voice_id': None, 'name': None, 'source': None}


def synthesize_voice_with_id(text: str, voice_id: str) -> bytes:
    response = httpx.post(
        f'{ELEVENLABS_BASE}/text-to-speech/{voice_id}',
        headers={
            **_headers(),
            'Accept': 'audio/mpeg',
            'Content-Type': 'application/json',
        },
        params={'output_format': 'mp3_44100_128'},
        json={
            'text': text,
            'model_id': 'eleven_multilingual_v2',
            'voice_settings': {
                'stability': 0.45,
                'similarity_boost': 0.75,
                'style': 0.2,
                'use_speaker_boost': True,
            },
        },
        timeout=180,
    )
    response.raise_for_status()
    return response.content


def audition_shared_voice(text: str, public_owner_id: str, voice_id: str, name: str | None = None) -> bytes:
    ensure_shared_voice_added(public_owner_id, voice_id, name)
    return synthesize_voice_with_id(text, voice_id)


def synthesize_voice(text: str, job_id: str) -> str:
    selected = get_selected_voice()
    voice_id = selected.get('voice_id')
    if not voice_id:
        raise RuntimeError('No ElevenLabs voice has been selected')
    output = Path('/tmp') / f'{job_id}.mp3'
    output.write_bytes(synthesize_voice_with_id(text, voice_id))
    return str(output)
