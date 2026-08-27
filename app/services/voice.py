from pathlib import Path
import re
import subprocess
import httpx
import redis
from app.config import settings

ELEVENLABS_BASE = 'https://api.elevenlabs.io/v1'
SELECTED_VOICE_ID_KEY = 'youtube_factory:selected_voice_id'
SELECTED_VOICE_NAME_KEY = 'youtube_factory:selected_voice_name'
SELECTED_VOICE_OWNER_KEY = 'youtube_factory:selected_voice_owner_id'


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


def voice_is_available(voice_id: str) -> bool:
    response = httpx.get(f'{ELEVENLABS_BASE}/voices/{voice_id}', headers=_headers(), timeout=20)
    return response.status_code == 200


def ensure_shared_voice_added(public_owner_id: str, voice_id: str, name: str | None = None) -> None:
    if voice_is_available(voice_id):
        return
    response = httpx.post(
        f'{ELEVENLABS_BASE}/voices/add/{public_owner_id}/{voice_id}',
        headers={**_headers(), 'Content-Type': 'application/json'},
        json={'new_name': (name or f'Audition {voice_id[:8]}')[:100], 'bookmarked': True},
        timeout=30,
    )
    response.raise_for_status()


def save_selected_voice(public_owner_id: str, voice_id: str, name: str) -> dict:
    client = _redis()
    client.set(SELECTED_VOICE_ID_KEY, voice_id)
    client.set(SELECTED_VOICE_NAME_KEY, name)
    client.set(SELECTED_VOICE_OWNER_KEY, public_owner_id)
    return {'voice_id': voice_id, 'name': name, 'public_owner_id': public_owner_id}


def get_selected_voice() -> dict:
    try:
        client = _redis()
        voice_id = client.get(SELECTED_VOICE_ID_KEY)
        name = client.get(SELECTED_VOICE_NAME_KEY)
        owner_id = client.get(SELECTED_VOICE_OWNER_KEY)
        if voice_id:
            return {'voice_id': voice_id, 'name': name or 'Selected voice', 'public_owner_id': owner_id, 'source': 'redis'}
    except Exception:
        pass
    if settings.elevenlabs_voice_id:
        return {'voice_id': settings.elevenlabs_voice_id, 'name': 'Environment voice', 'public_owner_id': None, 'source': 'environment'}
    return {'voice_id': None, 'name': None, 'public_owner_id': None, 'source': None}


def _split_for_tts(text: str, max_chars: int = 700) -> list[str]:
    sentences = [s.strip() for s in re.split(r'(?<=[.!?…])\s+', (text or '').strip()) if s.strip()]
    if not sentences:
        return [text.strip()]
    chunks: list[str] = []
    current = ''
    for sentence in sentences:
        candidate = f'{current} {sentence}'.strip()
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def synthesize_voice_with_id(text: str, voice_id: str, previous_text: str | None = None, next_text: str | None = None) -> bytes:
    params = {
        'output_format': 'mp3_44100_128',
        'apply_text_normalization': 'on',
    }
    if previous_text:
        params['previous_text'] = previous_text[-500:]
    if next_text:
        params['next_text'] = next_text[:500]
    response = httpx.post(
        f'{ELEVENLABS_BASE}/text-to-speech/{voice_id}',
        headers={**_headers(), 'Accept': 'audio/mpeg', 'Content-Type': 'application/json'},
        params=params,
        json={
            'text': text,
            'model_id': 'eleven_multilingual_v2',
            'voice_settings': {
                'stability': 0.52,
                'similarity_boost': 0.78,
                'style': 0.0,
                'use_speaker_boost': True,
                'speed': 1.03,
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
    owner_id = selected.get('public_owner_id')
    if owner_id:
        ensure_shared_voice_added(owner_id, voice_id, selected.get('name'))

    chunks = _split_for_tts(text)
    work = Path('/tmp') / f'{job_id}_voice'
    work.mkdir(parents=True, exist_ok=True)
    chunk_paths: list[Path] = []
    for idx, chunk in enumerate(chunks):
        previous_text = chunks[idx - 1] if idx > 0 else None
        next_text = chunks[idx + 1] if idx + 1 < len(chunks) else None
        path = work / f'chunk_{idx:03d}.mp3'
        path.write_bytes(synthesize_voice_with_id(chunk, voice_id, previous_text, next_text))
        chunk_paths.append(path)

    raw_output = work / 'joined.mp3'
    if len(chunk_paths) == 1:
        raw_output.write_bytes(chunk_paths[0].read_bytes())
    else:
        concat = work / 'concat.txt'
        concat.write_text('\n'.join(f"file '{p.as_posix()}'" for p in chunk_paths), encoding='utf-8')
        subprocess.run([
            'ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', str(concat),
            '-c:a', 'libmp3lame', '-b:a', '192k', str(raw_output),
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    output = Path('/tmp') / f'{job_id}.mp3'
    subprocess.run([
        'ffmpeg', '-y', '-i', str(raw_output),
        '-af', 'loudnorm=I=-16:TP=-1.5:LRA=7',
        '-c:a', 'libmp3lame', '-b:a', '192k', str(output),
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return str(output)
