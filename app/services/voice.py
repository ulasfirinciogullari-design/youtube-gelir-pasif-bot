from pathlib import Path
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
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
        f'{ELEVENLABS_BASE}/shared-voices', headers=_headers(),
        params={'language': 'tr', 'page_size': min(max(page_size, 1), 100), 'sort': 'trending', 'include_custom_rates': 'false', 'include_live_moderated': 'false'},
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
            'voice_id': voice.get('voice_id'), 'public_owner_id': voice.get('public_owner_id'),
            'name': voice.get('name'), 'gender': voice.get('gender'), 'age': voice.get('age'),
            'accent': voice.get('accent'), 'description': voice.get('description'), 'use_case': voice.get('use_case'),
            'category': voice.get('category'), 'preview_url': preview_url, 'rate': voice.get('rate'),
        })
    return candidates


def voice_is_available(voice_id: str) -> bool:
    return httpx.get(f'{ELEVENLABS_BASE}/voices/{voice_id}', headers=_headers(), timeout=20).status_code == 200


def ensure_shared_voice_added(public_owner_id: str, voice_id: str, name: str | None = None) -> None:
    if voice_is_available(voice_id):
        return
    response = httpx.post(
        f'{ELEVENLABS_BASE}/voices/add/{public_owner_id}/{voice_id}',
        headers={**_headers(), 'Content-Type': 'application/json'},
        json={'new_name': (name or f'Audition {voice_id[:8]}')[:100], 'bookmarked': True}, timeout=30,
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


def _normalize_qr_code_phrase(match: re.Match) -> str:
    suffix = str(match.group('suffix') or '').casefold()
    return f'kare kod{suffix}'


_TURKISH_PRONUNCIATION_RULES = [
    (
        r'\bQR\s+KOD(?P<suffix>[A-ZÇĞİÖŞÜa-zçğıöşü]*)\b',
        _normalize_qr_code_phrase,
    ),
    (r'\bO\s*[-.]?\s*L\s*[-.]?\s*E\s*[-.]?\s*D\b', 'oled'),
    (r'\bOLED\b', 'oled'),
    (r'\bGPS\b', 'ci pi es'),
    (r'\bQR\b', 'kare kod'),
]


def normalize_turkish_tts(text: str) -> str:
    text = (text or '').strip()
    for pattern, replacement in _TURKISH_PRONUNCIATION_RULES:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    text = re.sub(r'\s+([,.;:!?])', r'\1', text)
    text = re.sub(r'([,.;:!?])(?=\S)', r'\1 ', text)
    text = re.sub(r'\s{2,}', ' ', text).strip()
    if text and text[-1] not in '.!?…':
        text += '.'
    return text


def _media_duration(path: str | Path) -> float:
    out = subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', str(path)], text=True).strip()
    return float(out)


def _voice_speed(target_seconds: float | None = None) -> float:
    """Keep short Turkish previews deliberate without slowing long-form work."""
    return 0.90 if target_seconds and target_seconds <= 40 else 1.01


def synthesize_voice_with_id(
    text: str,
    voice_id: str,
    previous_text: str | None = None,
    next_text: str | None = None,
    *,
    speed: float = 1.01,
) -> bytes:
    body = {
        'text': normalize_turkish_tts(text),
        'model_id': 'eleven_multilingual_v2',
        'apply_text_normalization': 'on',
        'voice_settings': {
            'stability': 0.40,
            'similarity_boost': 0.80,
            'style': 0.0,
            'use_speaker_boost': True,
            'speed': speed,
        },
    }
    if previous_text:
        body['previous_text'] = normalize_turkish_tts(previous_text)[-600:]
    if next_text:
        body['next_text'] = normalize_turkish_tts(next_text)[:600]
    response = httpx.post(
        f'{ELEVENLABS_BASE}/text-to-speech/{voice_id}',
        headers={**_headers(), 'Accept': 'audio/mpeg', 'Content-Type': 'application/json'},
        params={'output_format': 'mp3_44100_128'}, json=body, timeout=180,
    )
    response.raise_for_status()
    return response.content


def audition_shared_voice(text: str, public_owner_id: str, voice_id: str, name: str | None = None) -> bytes:
    ensure_shared_voice_added(public_owner_id, voice_id, name)
    return synthesize_voice_with_id(text, voice_id)


def _selected_voice_or_raise() -> dict:
    selected = get_selected_voice()
    voice_id = selected.get('voice_id')
    if not voice_id:
        raise RuntimeError('No ElevenLabs voice has been selected')
    owner_id = selected.get('public_owner_id')
    if owner_id:
        ensure_shared_voice_added(owner_id, voice_id, selected.get('name'))
    return selected


def _scene_pause(scene: dict, is_last: bool, short_preview: bool) -> float:
    if is_last:
        return 0.0
    pace = str(scene.get('pace') or 'normal').lower()
    transition = str(scene.get('transition') or 'cut').lower()
    if short_preview:
        if transition == 'dip' or pace == 'slow':
            return 0.30
        if pace == 'fast':
            return 0.10
        return 0.16
    if transition == 'dip' or pace == 'slow':
        return 0.50
    if pace == 'fast':
        return 0.18
    return 0.32


def _fit_duration(output: Path, scene_durations: list[float], target_seconds: float | None) -> tuple[list[float], float, float, float]:
    """Gently fit a near-target narration without making the voice sound rushed."""
    before = _media_duration(output)
    after = before
    tempo_rate = 1.0
    outside_tolerance = bool(
        target_seconds and target_seconds > 0
        and (before > target_seconds * 1.03 or before < target_seconds * 0.97)
    )
    if outside_tolerance:
        desired = target_seconds * 0.99
        requested_rate = before / desired
        # Large tempo changes hide a bad script budget and sound synthetic.
        if requested_rate < 0.92 or requested_rate > 1.12:
            raise RuntimeError(
                f'Narration needs {requested_rate:.3f}x tempo to fit {target_seconds:.1f}s; '
                'rewrite the script instead of distorting the voice'
            )
        tempo_rate = requested_rate
        fitted = output.with_name(output.stem + '_fitted.mp3')
        subprocess.run([
            'ffmpeg', '-y', '-i', str(output),
            '-af', f'atempo={tempo_rate:.6f}',
            '-c:a', 'libmp3lame', '-b:a', '192k', str(fitted),
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        fitted.replace(output)
        after = _media_duration(output)
        scale = after / before if before else 1.0
        scene_durations = [duration * scale for duration in scene_durations]
    return scene_durations, before, after, tempo_rate


def synthesize_scene_sequence(scenes: list[dict], job_id: str, target_seconds: float | None = None) -> dict:
    selected = _selected_voice_or_raise()
    voice_id = selected['voice_id']
    source_texts = [str(s.get('narration') or '').strip() for s in scenes]
    spoken = [normalize_turkish_tts(text) for text in source_texts]
    if not all(spoken):
        raise RuntimeError('One or more scenes are missing narration')

    work = Path('/tmp') / f'{job_id}_voice'
    work.mkdir(parents=True, exist_ok=True)
    chunk_paths = [work / f'scene_{idx:03d}.mp3' for idx in range(len(scenes))]
    selected_speed = _voice_speed(target_seconds)

    def make_scene(idx: int):
        previous_text = source_texts[idx - 1] if idx > 0 else None
        next_text = source_texts[idx + 1] if idx + 1 < len(source_texts) else None
        audio = synthesize_voice_with_id(
            source_texts[idx],
            voice_id,
            previous_text,
            next_text,
            speed=selected_speed,
        )
        chunk_paths[idx].write_bytes(audio)
        return idx, _media_duration(chunk_paths[idx])

    raw_durations = [0.0] * len(scenes)
    with ThreadPoolExecutor(max_workers=min(4, max(1, len(scenes)))) as executor:
        futures = [executor.submit(make_scene, idx) for idx in range(len(scenes))]
        for future in as_completed(futures):
            idx, duration = future.result()
            raw_durations[idx] = duration

    pause_cache: dict[float, Path] = {}
    def pause_file(seconds: float) -> Path:
        rounded = round(seconds, 2)
        if rounded in pause_cache:
            return pause_cache[rounded]
        path = work / f'pause_{int(rounded * 1000):03d}.mp3'
        subprocess.run([
            'ffmpeg', '-y', '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=stereo', '-t', f'{rounded:.3f}',
            '-c:a', 'libmp3lame', '-b:a', '128k', str(path),
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        pause_cache[rounded] = path
        return path

    concat_entries: list[str] = []
    scene_durations: list[float] = []
    short_preview = bool(target_seconds and target_seconds <= 40)
    for idx, path in enumerate(chunk_paths):
        concat_entries.append(f"file '{path.as_posix()}'")
        pause = _scene_pause(scenes[idx], idx + 1 == len(chunk_paths), short_preview)
        duration = raw_durations[idx] + pause
        if pause > 0:
            concat_entries.append(f"file '{pause_file(pause).as_posix()}'")
        scene_durations.append(duration)

    concat = work / 'concat.txt'
    concat.write_text('\n'.join(concat_entries), encoding='utf-8')
    raw_output = work / 'joined.mp3'
    subprocess.run([
        'ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', str(concat), '-c:a', 'libmp3lame', '-b:a', '192k', str(raw_output),
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    output = Path('/tmp') / f'{job_id}.mp3'
    subprocess.run([
        'ffmpeg', '-y', '-i', str(raw_output), '-af', 'loudnorm=I=-15:TP=-1.0:LRA=7',
        '-c:a', 'libmp3lame', '-b:a', '192k', str(output),
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    scene_durations, before_fit, after_fit, tempo_rate = _fit_duration(output, scene_durations, target_seconds)
    return {
        'path': str(output),
        'scene_durations': scene_durations,
        'spoken_texts': spoken,
        'voice_name': selected.get('name'),
        'duration_before_fit': before_fit,
        'duration_after_fit': after_fit,
        'tempo_rate': tempo_rate,
    }


def synthesize_voice(text: str, job_id: str) -> str:
    return synthesize_scene_sequence([{'narration': text}], job_id)['path']

