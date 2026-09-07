from pathlib import Path
import base64
import binascii
import hashlib
import math
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import httpx
import redis
from app.config import settings

ELEVENLABS_BASE = 'https://api.elevenlabs.io/v1'
ELEVENLABS_MULTILINGUAL_V2_MODEL_ID = 'eleven_multilingual_v2'
ELEVENLABS_TURKISH_SHORT_MODEL_ID = 'eleven_flash_v2_5'
SELECTED_VOICE_ID_KEY = 'youtube_factory:selected_voice_id'
SELECTED_VOICE_NAME_KEY = 'youtube_factory:selected_voice_name'
SELECTED_VOICE_OWNER_KEY = 'youtube_factory:selected_voice_owner_id'

# ElevenLabs can insert documentary-length sentence gaps even inside a
# 30-second continuous take. Keep a short, natural breath on each side of a
# scene cut, but remove only alignment-proven dead air. The final tail is
# bounded separately so captions and the detected spoken ending stay aligned.
_SHORT_PREVIEW_LONG_BOUNDARY_PAUSE = 0.64
_SHORT_PREVIEW_BOUNDARY_BREATH_SIDE = 0.18
_SHORT_PREVIEW_LONG_TAIL = 0.28
_SHORT_PREVIEW_RETAINED_TAIL = 0.20
_SHORT_PREVIEW_COMPACTION_TOLERANCE = 0.18
# A measured short take may use a modest pitch-preserving slowdown, but the
# cumulative rate and fresh transcript/duration/prosody gates remain binding.
_SHORT_NARRATION_MIN_TEMPO = 0.93


class VoiceQualityError(RuntimeError):
    """A bounded synthesized-voice defect that may use a different seed."""


class VoiceScriptFitError(VoiceQualityError):
    """A structural script-duration mismatch that a new seed cannot repair."""


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


def normalize_turkish_tts(
    text: str,
    *,
    ensure_terminal: bool = True,
    legacy_numeric_spacing: bool = False,
) -> str:
    text = (text or '').strip()
    for pattern, replacement in _TURKISH_PRONUNCIATION_RULES:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    text = re.sub(r'\s+([,.;:!?])', r'\1', text)
    # A separator inside a written number is not a phrase boundary. In
    # particular, never turn the sourced amount ``3,69`` into ``3, 69``.
    punctuation = (r'([,.;:!?])(?=\S)' if legacy_numeric_spacing
                   else r'([;:!?]|(?<!\d)[,.]|[,.](?!\d))(?=\S)')
    # The legacy spelling is only for exact validation of an already
    # hash-verified checkpoint, never selected by new synthesis calls.
    text = re.sub(punctuation, lambda match: match.group() + ' ', text)
    text = re.sub(r'\s{2,}', ' ', text).strip()
    if ensure_terminal and text and text[-1] not in '.!?…':
        text += '.'
    return text


def _media_duration(path: str | Path) -> float:
    out = subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', str(path)], text=True).strip()
    return float(out)


def _voice_speed(target_seconds: float | None = None) -> float:
    """Use ElevenLabs' natural speed for short Turkish previews."""
    return 1.0 if target_seconds and target_seconds <= 40 else 1.01


def _use_turkish_short_preview_profile(
    language: str | None,
    target_seconds: float | None,
) -> bool:
    """Route only the validated pipeline language and short duration."""
    primary_language = (
        str(language or '')
        .strip()
        .replace('_', '-')
        .casefold()
        .split('-', 1)[0]
    )
    return bool(
        target_seconds
        and 0 < target_seconds <= 40
        and primary_language == 'tr'
    )


def _voice_request_body(
    text: str,
    previous_text: str | None = None,
    next_text: str | None = None,
    *,
    speed: float = 1.01,
    seed: int | None = None,
    turkish_short_preview: bool = False,
) -> dict:
    if turkish_short_preview:
        # The selected native Turkish PVC was validated end-to-end with
        # Flash v2.5 on the production 30-second narration: exact transcript,
        # duration and independent prosody gates all passed. ``language_code``
        # is intentionally used only here because ElevenLabs does not support
        # it on Multilingual v2.
        model_id = ELEVENLABS_TURKISH_SHORT_MODEL_ID
        language_code = 'tr'
        voice_settings = {
            'stability': 0.50,
            'similarity_boost': 0.75,
            'speed': speed,
        }
    else:
        model_id = ELEVENLABS_MULTILINGUAL_V2_MODEL_ID
        language_code = None
        voice_settings = {
            'stability': 0.40,
            'similarity_boost': 0.80,
            'style': 0.0,
            'use_speaker_boost': True,
            'speed': speed,
        }
    body = {
        'text': normalize_turkish_tts(text),
        'model_id': model_id,
        'apply_text_normalization': 'on',
        'voice_settings': voice_settings,
    }
    if language_code:
        body['language_code'] = language_code
    if seed is not None:
        if type(seed) is not int or not 0 <= seed <= 4_294_967_295:
            raise ValueError('ElevenLabs seed must be an unsigned 32-bit integer')
        body['seed'] = seed
    if previous_text:
        body['previous_text'] = normalize_turkish_tts(previous_text)[-600:]
    if next_text:
        body['next_text'] = normalize_turkish_tts(next_text)[:600]
    return body


def synthesize_voice_with_id(
    text: str,
    voice_id: str,
    previous_text: str | None = None,
    next_text: str | None = None,
    *,
    speed: float = 1.01,
    seed: int | None = None,
    turkish_short_preview: bool = False,
) -> bytes:
    body = _voice_request_body(
        text,
        previous_text,
        next_text,
        speed=speed,
        seed=seed,
        turkish_short_preview=turkish_short_preview,
    )
    response = httpx.post(
        f'{ELEVENLABS_BASE}/text-to-speech/{voice_id}',
        headers={**_headers(), 'Accept': 'audio/mpeg', 'Content-Type': 'application/json'},
        params={'output_format': 'mp3_44100_128'}, json=body, timeout=180,
    )
    response.raise_for_status()
    return response.content


def synthesize_voice_with_timestamps(
    text: str,
    voice_id: str,
    *,
    speed: float = 1.01,
    seed: int | None = None,
    turkish_short_preview: bool = False,
    raw_audio_sink=None,
) -> tuple[bytes, dict]:
    """Synthesize one continuous take with character-level source timing."""
    if raw_audio_sink is not None and not callable(raw_audio_sink):
        raise VoiceQualityError('Raw voice diagnostic sink is invalid')
    response = httpx.post(
        f'{ELEVENLABS_BASE}/text-to-speech/{voice_id}/with-timestamps',
        headers={**_headers(), 'Accept': 'application/json', 'Content-Type': 'application/json'},
        params={'output_format': 'mp3_44100_128'},
        json=_voice_request_body(
            text,
            speed=speed,
            seed=seed,
            turkish_short_preview=turkish_short_preview,
        ),
        timeout=180,
    )
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError:
        raise VoiceQualityError(
            'ElevenLabs timestamp response is not valid JSON'
        ) from None
    if not isinstance(payload, dict):
        raise VoiceQualityError(
            'ElevenLabs timestamp response is not a JSON object'
        )
    encoded_audio = payload.get('audio_base64')
    alignment = payload.get('alignment')
    if not isinstance(encoded_audio, str) or not encoded_audio:
        raise VoiceQualityError('ElevenLabs timestamp response is missing audio')
    try:
        audio = base64.b64decode(encoded_audio, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise VoiceQualityError(
            'ElevenLabs timestamp response contains invalid audio'
        ) from exc
    if not audio:
        raise VoiceQualityError('ElevenLabs timestamp response contains empty audio')
    if raw_audio_sink is not None:
        # Preserve the sole paid response before alignment, editing or fitting
        # can reject it. This archive is not a reusable or approved candidate.
        raw_audio_sink(audio)
    if not isinstance(alignment, dict):
        raise VoiceQualityError(
            'ElevenLabs timestamp response is missing source alignment'
        )
    return audio, alignment


def _join_scene_narration(spoken: list[str]) -> tuple[str, list[tuple[int, int]]]:
    """Join normalized scenes once while retaining exact character spans."""
    if not spoken or not all(spoken):
        raise VoiceScriptFitError('One or more scenes are missing narration')
    spans: list[tuple[int, int]] = []
    cursor = 0
    for index, text in enumerate(spoken):
        if index:
            cursor += 1
        start = cursor
        cursor += len(text)
        spans.append((start, cursor))
    return ' '.join(spoken), spans


def _scene_durations_from_alignment(
    narration: str,
    spans: list[tuple[int, int]],
    alignment: dict,
    media_duration: float,
) -> list[float]:
    """Convert exact source-character boundaries into scene durations."""
    characters = alignment.get('characters')
    end_times = alignment.get('character_end_times_seconds')
    if not isinstance(characters, list) or not isinstance(end_times, list):
        raise VoiceQualityError('ElevenLabs source alignment is incomplete')
    if len(characters) != len(end_times) or not characters:
        raise VoiceQualityError(
            'ElevenLabs source alignment lengths do not match'
        )
    if not all(isinstance(character, str) for character in characters):
        raise VoiceQualityError(
            'ElevenLabs source alignment contains invalid characters'
        )
    if ''.join(characters) != narration:
        raise VoiceQualityError(
            'ElevenLabs source alignment does not match narration'
        )

    numeric_end_times: list[float] = []
    for value in end_times:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise VoiceQualityError(
                'ElevenLabs source alignment contains invalid timing'
            )
        timing = float(value)
        if not math.isfinite(timing) or timing < 0:
            raise VoiceQualityError(
                'ElevenLabs source alignment contains invalid timing'
            )
        if numeric_end_times and timing < numeric_end_times[-1]:
            raise VoiceQualityError(
                'ElevenLabs source alignment timing is not monotonic'
            )
        numeric_end_times.append(timing)

    if not math.isfinite(media_duration) or media_duration <= 0:
        raise VoiceQualityError('Continuous narration has invalid media duration')
    if numeric_end_times[-1] > media_duration + 0.25:
        raise VoiceQualityError(
            'ElevenLabs source alignment exceeds narration duration'
        )

    boundaries: list[float] = []
    previous_end = -1
    for index, (start, end) in enumerate(spans):
        if start < 0 or end <= start or end > len(narration):
            raise VoiceQualityError('Narration scene span is invalid')
        if index == 0 and start != 0:
            raise VoiceQualityError(
                'Narration scene spans do not start at zero'
            )
        if index and start != previous_end + 1:
            raise VoiceQualityError('Narration scene spans are not contiguous')
        previous_end = end
        boundary = (
            media_duration
            if index == len(spans) - 1
            else numeric_end_times[end - 1]
        )
        if boundaries and boundary <= boundaries[-1]:
            raise VoiceQualityError('Narration scene timing is not increasing')
        if not boundaries and boundary <= 0:
            raise VoiceQualityError('Narration scene timing is not increasing')
        boundaries.append(boundary)
    if not boundaries or previous_end != len(narration):
        raise VoiceQualityError(
            'Narration scene spans do not cover the source text'
        )

    durations: list[float] = []
    cursor = 0.0
    for boundary in boundaries:
        durations.append(boundary - cursor)
        cursor = boundary
    return durations


def _short_preview_audio_edit_plan(
    narration: str,
    spans: list[tuple[int, int]],
    alignment: dict,
    media_duration: float,
) -> dict:
    """Plan bounded cuts using exact ElevenLabs character alignment.

    No waveform threshold or transcript guess is used: an edit is allowed only
    between the final spoken character in one scene and the first spoken
    character of the next scene, or after the final aligned word.
    """
    # Reuse the strict source/end-time/span validation before reading starts.
    _scene_durations_from_alignment(
        narration,
        spans,
        alignment,
        media_duration,
    )
    raw_starts = alignment.get('character_start_times_seconds')
    raw_ends = alignment.get('character_end_times_seconds')
    if not isinstance(raw_starts, list) or not isinstance(raw_ends, list):
        raise VoiceQualityError(
            'ElevenLabs source alignment is missing start timing'
        )
    if len(raw_starts) != len(narration) or len(raw_ends) != len(narration):
        raise VoiceQualityError(
            'ElevenLabs source alignment lengths do not match'
        )

    starts: list[float] = []
    ends: list[float] = []
    for raw_start, raw_end in zip(raw_starts, raw_ends):
        if (
            isinstance(raw_start, bool)
            or not isinstance(raw_start, (int, float))
            or isinstance(raw_end, bool)
            or not isinstance(raw_end, (int, float))
        ):
            raise VoiceQualityError(
                'ElevenLabs source alignment contains invalid timing'
            )
        start = float(raw_start)
        end = float(raw_end)
        if (
            not math.isfinite(start)
            or not math.isfinite(end)
            or start < 0
            or end < start
            or (starts and start < starts[-1])
            or (ends and end < ends[-1])
        ):
            raise VoiceQualityError(
                'ElevenLabs source alignment timing is not monotonic'
            )
        starts.append(start)
        ends.append(end)

    cuts: list[tuple[float, float]] = []
    raw_boundaries: list[float] = []
    interior_pause_count = 0

    def lexical_index(start: int, end: int, *, reverse: bool) -> int:
        indices = (
            range(end - 1, start - 1, -1)
            if reverse
            else range(start, end)
        )
        for index in indices:
            if narration[index].isalnum():
                return index
        raise VoiceQualityError(
            'Narration scene is missing a spoken character'
        )

    for scene_index in range(len(spans) - 1):
        previous_end_index = lexical_index(
            *spans[scene_index],
            reverse=True,
        )
        next_start_index = lexical_index(
            *spans[scene_index + 1],
            reverse=False,
        )
        previous_end = ends[previous_end_index]
        next_start = starts[next_start_index]
        if next_start + 0.02 < previous_end:
            raise VoiceQualityError('ElevenLabs scene alignment overlaps')
        gap = max(0.0, next_start - previous_end)
        if gap > _SHORT_PREVIEW_LONG_BOUNDARY_PAUSE:
            cut_start = previous_end + _SHORT_PREVIEW_BOUNDARY_BREATH_SIDE
            cut_end = next_start - _SHORT_PREVIEW_BOUNDARY_BREATH_SIDE
            if cut_end <= cut_start:
                raise VoiceQualityError(
                    'ElevenLabs boundary pause cannot be compacted'
                )
            cuts.append((cut_start, cut_end))
            raw_boundaries.append(cut_start)
            interior_pause_count += 1
        else:
            # Put the visual cut halfway through a normal breath so the next
            # shot leads the following sentence by only a small amount.
            raw_boundaries.append(previous_end + gap / 2.0)

    final_spoken_index = lexical_index(*spans[-1], reverse=True)
    final_aligned_end = min(media_duration, ends[final_spoken_index])
    tail = max(0.0, media_duration - final_aligned_end)
    tail_trimmed = tail > _SHORT_PREVIEW_LONG_TAIL
    if tail_trimmed:
        final_boundary = final_aligned_end + _SHORT_PREVIEW_RETAINED_TAIL
        cuts.append((final_boundary, media_duration))
    else:
        final_boundary = media_duration
    raw_boundaries.append(final_boundary)

    previous_cut_end = 0.0
    for cut_start, cut_end in cuts:
        if (
            not 0 <= cut_start < cut_end <= media_duration
            or cut_start < previous_cut_end
        ):
            raise VoiceQualityError(
                'Short-preview audio edit plan is invalid'
            )
        previous_cut_end = cut_end

    def removed_before(boundary: float) -> float:
        return sum(
            cut_end - cut_start
            for cut_start, cut_end in cuts
            if cut_end <= boundary + 1e-9
        )

    adjusted_boundaries = [
        boundary - removed_before(boundary)
        for boundary in raw_boundaries
    ]
    durations: list[float] = []
    cursor = 0.0
    for boundary in adjusted_boundaries:
        if boundary <= cursor:
            raise VoiceQualityError(
                'Compacted narration scene timing is not increasing'
            )
        durations.append(boundary - cursor)
        cursor = boundary

    removed_seconds = sum(end - start for start, end in cuts)
    expected_duration = media_duration - removed_seconds
    if abs(sum(durations) - expected_duration) > 1e-6:
        raise VoiceQualityError(
            'Short-preview audio edit plan does not cover media'
        )
    return {
        'cuts': cuts,
        'scene_durations': durations,
        'expected_duration': expected_duration,
        'removed_silence_seconds': removed_seconds,
        'interior_pause_count': interior_pause_count,
        'tail_trimmed': tail_trimmed,
    }


def _apply_short_preview_audio_edit_plan(
    path: Path,
    media_duration: float,
    plan: dict,
) -> tuple[list[float], float]:
    """Apply a finite, prevalidated set of alignment-safe audio cuts."""
    cuts = list(plan.get('cuts') or [])
    durations = [float(value) for value in plan.get('scene_durations') or []]
    expected_duration = float(plan.get('expected_duration') or media_duration)
    if not cuts:
        return durations, media_duration

    keep_ranges: list[tuple[float, float]] = []
    cursor = 0.0
    for cut_start, cut_end in cuts:
        if cut_start > cursor + 1e-6:
            keep_ranges.append((cursor, cut_start))
        cursor = cut_end
    if media_duration > cursor + 1e-6:
        keep_ranges.append((cursor, media_duration))
    if not keep_ranges:
        raise VoiceQualityError(
            'Short-preview audio edit removed the whole narration'
        )

    filters: list[str] = []
    labels: list[str] = []
    for index, (start, end) in enumerate(keep_ranges):
        label = f'keep{index}'
        labels.append(f'[{label}]')
        filters.append(
            f'[0:a]atrim=start={start:.6f}:end={end:.6f},'
            f'asetpts=PTS-STARTPTS[{label}]'
        )
    if len(labels) == 1:
        filters.append(labels[0] + 'anull[compacted]')
    else:
        filters.append(
            ''.join(labels)
            + f'concat=n={len(labels)}:v=0:a=1[compacted]'
        )
    compacted = path.with_name(path.stem + '_compacted.mp3')
    subprocess.run([
        'ffmpeg', '-y', '-i', str(path),
        '-filter_complex', ';'.join(filters),
        '-map', '[compacted]', '-c:a', 'libmp3lame', '-b:a', '192k',
        str(compacted),
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    actual_duration = _media_duration(compacted)
    if (
        not math.isfinite(actual_duration)
        or actual_duration <= 0
        or abs(actual_duration - expected_duration)
        > _SHORT_PREVIEW_COMPACTION_TOLERANCE
    ):
        raise VoiceQualityError(
            'Short-preview audio compaction duration is invalid'
        )
    compacted.replace(path)
    scale = actual_duration / expected_duration
    return [duration * scale for duration in durations], actual_duration


def _deterministic_scene_seed(
    voice_id: str,
    text: str,
    scene_index: int,
    generation_attempt: int,
) -> int:
    """Produce stable but independently retryable ElevenLabs scene seeds."""
    if type(scene_index) is not int or scene_index < 0:
        raise ValueError('scene_index must be a non-negative integer')
    if type(generation_attempt) is not int or generation_attempt < 0:
        raise ValueError('generation_attempt must be a non-negative integer')
    material = '\x1f'.join((
        str(voice_id or '').strip(),
        normalize_turkish_tts(text),
        str(scene_index),
        str(generation_attempt),
    )).encode('utf-8')
    return int.from_bytes(hashlib.sha256(material).digest()[:4], 'big')


def is_transient_voice_http_error(exc: BaseException) -> bool:
    if isinstance(exc, httpx.RequestError):
        return True
    if not isinstance(exc, httpx.HTTPStatusError):
        return False
    status_code = int(exc.response.status_code)
    return status_code in {408, 425, 429} or status_code >= 500


def voice_http_retry_delay_seconds(
    exc: BaseException,
    retry_index: int,
) -> float:
    response = getattr(exc, 'response', None)
    headers = getattr(response, 'headers', None)
    raw_retry_after = (
        headers.get('Retry-After')
        if headers is not None and hasattr(headers, 'get')
        else None
    )
    try:
        retry_after = float(raw_retry_after)
    except (TypeError, ValueError):
        retry_after = float(2 ** max(0, int(retry_index)))
    if not math.isfinite(retry_after) or retry_after < 0:
        retry_after = float(2 ** max(0, int(retry_index)))
    return min(8.0, retry_after)


def _synthesize_long_form_scene(
    idx: int,
    source_texts: list[str],
    chunk_path: Path,
    voice_id: str,
    selected_speed: float,
    generation_attempt: int,
) -> tuple[int, float]:
    previous_text = source_texts[idx - 1] if idx > 0 else None
    next_text = (
        source_texts[idx + 1]
        if idx + 1 < len(source_texts)
        else None
    )
    seed = _deterministic_scene_seed(
        voice_id,
        source_texts[idx],
        idx,
        generation_attempt,
    )
    for provider_attempt in range(3):
        try:
            audio = synthesize_voice_with_id(
                source_texts[idx],
                voice_id,
                previous_text,
                next_text,
                speed=selected_speed,
                seed=seed,
            )
            chunk_path.write_bytes(audio)
            return idx, _media_duration(chunk_path)
        except httpx.HTTPError as exc:
            if (
                not is_transient_voice_http_error(exc)
                or provider_attempt >= 2
            ):
                raise
            time.sleep(
                voice_http_retry_delay_seconds(exc, provider_attempt)
            )
    raise RuntimeError('Voice scene retry budget was exhausted')


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


def _scene_allocations_for_measured_audio(scene_durations: list[float], audio_seconds: float) -> list[float]:
    """Allocate visual scene lengths, not word alignment or an audio-QA pass.

    Chunk/container durations can drift from the decoded and re-encoded joined
    file. Preserve their relative visual weights but bind the final boundary to
    the independently measured audio. This calculation never changes audio.
    """
    if (
        not scene_durations
        or any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0
               for value in scene_durations)
        or type(audio_seconds) not in (int, float) or not math.isfinite(audio_seconds) or audio_seconds <= 0
    ):
        raise VoiceQualityError('Invalid measured visual scene allocation')
    total = math.fsum(scene_durations)
    if not math.isfinite(total) or total <= 0:
        raise VoiceQualityError('Invalid measured visual scene allocation')
    allocated = [duration * (audio_seconds / total) for duration in scene_durations]
    allocated[-1] = audio_seconds - sum(allocated[:-1])
    if any(not math.isfinite(value) or value <= 0 for value in allocated):
        raise VoiceQualityError('Invalid measured visual scene allocation')
    return allocated


def _fit_duration(
    output: Path,
    scene_durations: list[float],
    target_seconds: float | None,
    *,
    prior_tempo_rate: float = 1.0,
) -> tuple[list[float], float, float, float]:
    """Fit narration with bounded tempo changes; full audio QA still follows."""
    before = _media_duration(output)
    after = before
    tempo_rate = 1.0
    short_preview = bool(target_seconds and 0 < target_seconds <= 40)
    desired = (
        max(1.0, float(target_seconds) - 0.50)
        if short_preview
        else float(target_seconds) * 0.99
        if target_seconds and target_seconds > 0
        else before
    )
    short_minimum_fit = max(1.0, float(target_seconds) - 1.25) if short_preview else before
    recoverable_short_deficit = bool(
        short_preview
        # Leave already-valid narration unchanged. Fit only to just above the
        # existing duration-QA floor, not all the way to a filled timeline.
        and before < float(target_seconds) - 1.30
        and before / short_minimum_fit >= _SHORT_NARRATION_MIN_TEMPO
    )
    if recoverable_short_deficit:
        desired = short_minimum_fit
    needs_fit = bool(
        target_seconds and target_seconds > 0
        and (
            # At most 7% slower for a recoverable shortfall. Thinner scripts
            # remain unchanged and must fail the downstream duration gate.
            (short_preview and (before > desired + 0.015 or recoverable_short_deficit))
            or (
                not short_preview
                and (before > target_seconds * 1.03 or before < target_seconds * 0.97)
            )
        )
    )
    if needs_fit:
        requested_rate = before / desired
        # Large tempo changes hide a bad script budget and sound synthetic.
        if requested_rate < (_SHORT_NARRATION_MIN_TEMPO if short_preview else 0.92) or requested_rate > 1.12:
            raise VoiceScriptFitError(
                f'Narration needs {requested_rate:.3f}x tempo to fit {target_seconds:.1f}s; '
                'rewrite the script instead of distorting the voice'
            )
        # Report the exact value sent to FFmpeg, not an unrounded estimate.
        tempo_rate = round(requested_rate, 6)
        cumulative_rate = prior_tempo_rate * tempo_rate
        if cumulative_rate < (_SHORT_NARRATION_MIN_TEMPO if short_preview else 0.92) - 1e-12 or cumulative_rate > 1.12 + 1e-12:
            raise VoiceScriptFitError(
                'Existing narration would exceed the cumulative tempo limit; '
                'rewrite the script instead of repeatedly stretching the voice'
            )
        fitted = output.with_name(output.stem + '_fitted.mp3')
        subprocess.run([
            'ffmpeg', '-y', '-i', str(output),
            '-af', f'atempo={tempo_rate:.6f}',
            '-c:a', 'libmp3lame', '-b:a', '192k', str(fitted),
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        fitted.replace(output)
        after = _media_duration(output)
        if short_preview:
            scale = after / before if before else 1.0
            scene_durations = [duration * scale for duration in scene_durations]
    if not short_preview:
        scene_durations = _scene_allocations_for_measured_audio(scene_durations, after)
    return scene_durations, before, after, tempo_rate


def fit_existing_narration_candidate(voice_result: dict, target_seconds: float) -> dict:
    """Refit an existing candidate without TTS; this never grants QA approval."""
    if not isinstance(voice_result, dict):
        raise VoiceScriptFitError('Existing narration metadata is invalid')
    prior_rate = voice_result.get('tempo_rate', 1.0)
    durations = voice_result.get('scene_durations')
    path = voice_result.get('path')
    if (
        type(target_seconds) not in (int, float)
        or not math.isfinite(target_seconds) or target_seconds <= 0
        or type(prior_rate) not in (int, float) or not math.isfinite(prior_rate)
        or not (_SHORT_NARRATION_MIN_TEMPO if target_seconds <= 40 else 0.92) - 1e-12 <= prior_rate <= 1.12 + 1e-12
        or not isinstance(path, (str, Path))
        or not isinstance(durations, list) or not durations
        or any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0 for value in durations)
    ):
        raise VoiceScriptFitError('Existing narration metadata is invalid')
    original_before = voice_result.get('duration_before_fit')
    if original_before is not None and (
        type(original_before) not in (int, float)
        or not math.isfinite(original_before) or original_before <= 0
    ):
        raise VoiceScriptFitError('Existing narration metadata is invalid')
    updated_durations, before, after, applied_rate = _fit_duration(
        Path(path), list(durations), target_seconds, prior_tempo_rate=prior_rate,
    )
    result = dict(voice_result)
    result.update({
        'scene_durations': updated_durations,
        'duration_before_fit': original_before if original_before is not None else before,
        'duration_after_fit': after,
        'tempo_rate': prior_rate * applied_rate,
    })
    # Timing-sensitive approvals cannot survive even a small audio transform.
    for field in ('audio_qc', 'audio_prosody_qc', 'audio_duration_qc'):
        result.pop(field, None)
    return result


def synthesize_scene_sequence(
    scenes: list[dict],
    job_id: str,
    target_seconds: float | None = None,
    *,
    generation_attempt: int = 0,
    language: str | None = None,
    profile_override: str | None = None,
    before_paid_request=None,
    raw_audio_sink=None,
) -> dict:
    if profile_override is not None and (
        profile_override != 'turkish_multilingual_v2'
        or target_seconds != 30
        or str(language or '').strip().casefold() != 'tr'
        or type(generation_attempt) is not int or generation_attempt != 0
        or not callable(before_paid_request)
        or not callable(raw_audio_sink)
    ):
        raise VoiceScriptFitError('Voice replacement profile requires one reserved Turkish short take')
    if (before_paid_request is not None or raw_audio_sink is not None) and profile_override is None:
        raise VoiceScriptFitError('Voice replacement reservation requires an explicit profile')
    selected = _selected_voice_or_raise()
    voice_id = selected['voice_id']
    source_texts = [str(s.get('narration') or '').strip() for s in scenes]
    short_preview = bool(target_seconds and 0 < target_seconds <= 40)
    turkish_short_preview = _use_turkish_short_preview_profile(
        language,
        target_seconds,
    ) and profile_override is None
    spoken = [
        normalize_turkish_tts(
            text,
            ensure_terminal=(not short_preview or index + 1 == len(scenes)),
        )
        for index, text in enumerate(source_texts)
    ]
    if not all(spoken):
        raise VoiceScriptFitError('One or more scenes are missing narration')

    work = Path('/tmp') / f'{job_id}_voice'
    work.mkdir(parents=True, exist_ok=True)
    selected_speed = _voice_speed(target_seconds)
    raw_output = work / 'joined.mp3'
    removed_silence_seconds = 0.0
    compacted_boundary_pause_count = 0
    compacted_trailing_silence = False
    if short_preview:
        narration, spans = _join_scene_narration(spoken)
        timestamp_options = {
            'speed': selected_speed,
            'seed': _deterministic_scene_seed(
                voice_id,
                narration,
                0,
                generation_attempt,
            ),
        }
        if turkish_short_preview:
            timestamp_options['turkish_short_preview'] = True
        if raw_audio_sink is not None:
            timestamp_options['raw_audio_sink'] = raw_audio_sink
        if before_paid_request is not None:
            # Durable, one-shot reservation immediately precedes the only
            # synthesis request. An uncertain result must not be retried.
            before_paid_request(voice_id)
        audio, alignment = synthesize_voice_with_timestamps(
            narration,
            voice_id,
            **timestamp_options,
        )
        raw_output.write_bytes(audio)
        raw_media_duration = _media_duration(raw_output)
        edit_plan = _short_preview_audio_edit_plan(
            narration,
            spans,
            alignment,
            raw_media_duration,
        )
        scene_durations, _ = _apply_short_preview_audio_edit_plan(
            raw_output,
            raw_media_duration,
            edit_plan,
        )
        removed_silence_seconds = float(
            edit_plan['removed_silence_seconds']
        )
        compacted_boundary_pause_count = int(
            edit_plan['interior_pause_count']
        )
        compacted_trailing_silence = bool(edit_plan['tail_trimmed'])
    else:
        chunk_paths = [work / f'scene_{idx:03d}.mp3' for idx in range(len(scenes))]

        raw_durations = [0.0] * len(scenes)
        with ThreadPoolExecutor(max_workers=min(4, max(1, len(scenes)))) as executor:
            futures = [
                executor.submit(
                    _synthesize_long_form_scene,
                    idx,
                    source_texts,
                    chunk_paths[idx],
                    voice_id,
                    selected_speed,
                    generation_attempt,
                )
                for idx in range(len(scenes))
            ]
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
        scene_durations = []
        for idx, path in enumerate(chunk_paths):
            concat_entries.append(f"file '{path.as_posix()}'")
            pause = _scene_pause(scenes[idx], idx + 1 == len(chunk_paths), False)
            duration = raw_durations[idx] + pause
            if pause > 0:
                concat_entries.append(f"file '{pause_file(pause).as_posix()}'")
            scene_durations.append(duration)

        concat = work / 'concat.txt'
        concat.write_text('\n'.join(concat_entries), encoding='utf-8')
        subprocess.run([
            'ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', str(concat), '-c:a', 'libmp3lame', '-b:a', '192k', str(raw_output),
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    output = Path('/tmp') / f'{job_id}.mp3'
    subprocess.run([
        'ffmpeg', '-y', '-i', str(raw_output), '-af', 'loudnorm=I=-15:TP=-1.0:LRA=7',
        '-c:a', 'libmp3lame', '-b:a', '192k', str(output),
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    scene_durations, before_fit, after_fit, tempo_rate = _fit_duration(output, scene_durations, target_seconds)
    reserved_tail_seconds = (
        0.50 if target_seconds and 0 < target_seconds <= 40 else 0.0
    )
    return {
        'path': str(output),
        'scene_durations': scene_durations,
        'spoken_texts': spoken,
        'voice_name': selected.get('name'),
        'voice_model': (
            ELEVENLABS_TURKISH_SHORT_MODEL_ID
            if turkish_short_preview
            else ELEVENLABS_MULTILINGUAL_V2_MODEL_ID
        ),
        'voice_language_code': 'tr' if turkish_short_preview else None,
        'duration_before_fit': before_fit,
        'duration_after_fit': after_fit,
        'tempo_rate': tempo_rate,
        'removed_silence_seconds': removed_silence_seconds,
        'compacted_boundary_pause_count': compacted_boundary_pause_count,
        'compacted_trailing_silence': compacted_trailing_silence,
        'content_target_seconds': (
            float(target_seconds) - reserved_tail_seconds
            if target_seconds and target_seconds > 0
            else after_fit
        ),
        'reserved_tail_seconds': reserved_tail_seconds,
    }


def synthesize_voice(text: str, job_id: str) -> str:
    return synthesize_scene_sequence([{'narration': text}], job_id)['path']
