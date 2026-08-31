from __future__ import annotations

import base64
from difflib import SequenceMatcher
import math
import mimetypes
from pathlib import Path
import re
import unicodedata
from typing import Any

import httpx

from app.config import settings


OPENAI_AUDIO_TRANSCRIPTIONS_URL = (
    'https://api.openai.com/v1/audio/transcriptions'
)
GEMINI_INTERACTIONS_URL = (
    'https://generativelanguage.googleapis.com/v1beta/interactions'
)
ELEVENLABS_SPEECH_TO_TEXT_URL = 'https://api.elevenlabs.io/v1/speech-to-text'
_SPEECH_TO_TEXT_TIMEOUT = httpx.Timeout(180.0, connect=10.0)
_APOSTROPHES = frozenset("'\u2018\u2019\u02bc\u0060\u00b4")
# Gemini's inline request limit is 20 MB including base64 and JSON overhead.
_GEMINI_MAX_RAW_AUDIO_BYTES = 14 * 1024 * 1024
_GEMINI_TRANSCRIBE_MODEL = 'gemini-3.5-transcribe'
_GEMINI_DURATION_PATTERN = re.compile(r'^(?:0|[1-9]\d*)(?:\.\d{1,9})?s$')
_GEMINI_SUPPORTED_AUDIO_MIME_TYPES = frozenset({
    'audio/aac',
    'audio/aiff',
    'audio/alaw',
    'audio/flac',
    'audio/l16',
    'audio/m4a',
    'audio/mp3',
    'audio/mpeg',
    'audio/mulaw',
    'audio/ogg',
    'audio/opus',
    'audio/wav',
    'audio/webm',
})
_GEMINI_AUDIO_MIME_ALIASES = {
    'audio/mp4': 'audio/m4a',
    'audio/x-aiff': 'audio/aiff',
    'audio/x-flac': 'audio/flac',
    'audio/x-m4a': 'audio/m4a',
    'audio/x-wav': 'audio/wav',
    'video/webm': 'audio/webm',
}


class AudioQCError(RuntimeError):
    """A secret-safe failure while obtaining an audio-QC transcript."""


def _openai_headers(api_key: str) -> dict[str, str]:
    return {'Authorization': f'Bearer {api_key}'}


def _elevenlabs_headers(api_key: str) -> dict[str, str]:
    return {'xi-api-key': api_key}


def normalize_turkish_transcript(text: str) -> str:
    """Normalize Turkish casing and punctuation without folding letters."""
    value = unicodedata.normalize('NFKC', str(text or ''))
    # Python's default lower/casefold rules are not Turkish-locale aware.
    value = value.translate(str.maketrans({'I': '\u0131', '\u0130': 'i'})).casefold()

    normalized: list[str] = []
    for character in value:
        category = unicodedata.category(character)
        if character.isalnum() or category.startswith('M'):
            normalized.append(character)
        elif character in _APOSTROPHES:
            # Turkish proper-name suffixes may be returned without apostrophes.
            continue
        else:
            normalized.append(' ')
    return re.sub(r'\s+', ' ', ''.join(normalized)).strip()


def _tokens(text: str) -> list[str]:
    normalized = normalize_turkish_transcript(text)
    return normalized.split() if normalized else []


def _edit_distance(expected: list[str], heard: list[str]) -> int:
    previous = list(range(len(heard) + 1))
    for expected_index, expected_token in enumerate(expected, start=1):
        current = [expected_index]
        for heard_index, heard_token in enumerate(heard, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[heard_index] + 1,
                    previous[heard_index - 1]
                    + (expected_token != heard_token),
                )
            )
        previous = current
    return previous[-1]


def _mismatch_details(
    expected_tokens: list[str],
    heard_tokens: list[str],
) -> dict[str, Any]:
    matcher = SequenceMatcher(
        None,
        expected_tokens,
        heard_tokens,
        autojunk=False,
    )
    operations: list[dict[str, Any]] = []
    missing_words: list[str] = []
    unexpected_words: list[str] = []
    for operation, expected_start, expected_end, heard_start, heard_end in (
        matcher.get_opcodes()
    ):
        if operation == 'equal':
            continue
        expected_slice = expected_tokens[expected_start:expected_end]
        heard_slice = heard_tokens[heard_start:heard_end]
        operations.append({
            'operation': operation,
            'expected_range': [expected_start, expected_end],
            'heard_range': [heard_start, heard_end],
            'expected': expected_slice,
            'heard': heard_slice,
        })
        if operation in {'delete', 'replace'}:
            missing_words.extend(expected_slice)
        if operation in {'insert', 'replace'}:
            unexpected_words.extend(heard_slice)

    return {
        'exact_match': expected_tokens == heard_tokens,
        'expected_token_count': len(expected_tokens),
        'heard_token_count': len(heard_tokens),
        'missing_words': missing_words,
        'unexpected_words': unexpected_words,
        'operations': operations,
        'sequence_ratio': round(matcher.ratio(), 6),
    }


def _finite_time(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _word_timestamps(words: Any) -> list[dict[str, Any]]:
    if not isinstance(words, list):
        return []
    timestamps: list[dict[str, Any]] = []
    for item in words:
        if not isinstance(item, dict):
            continue
        word_type = str(item.get('type') or 'word')
        if word_type != 'word':
            continue
        # ElevenLabs uses ``text`` while OpenAI's verbose transcription uses
        # ``word`` for the same value.
        text = str(item.get('text') or item.get('word') or '').strip()
        if not text:
            continue
        timestamps.append({
            'text': text,
            'start': _finite_time(item.get('start')),
            'end': _finite_time(item.get('end')),
        })
    return timestamps


def _language_probability(value: Any) -> float | None:
    try:
        probability = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(probability) or not 0 <= probability <= 1:
        return None
    return probability


def compare_transcript(
    expected_narration: str,
    transcript: str,
    *,
    language_code: str | None = None,
    language_probability: Any = None,
    words: Any = None,
    provider: str | None = None,
) -> dict[str, Any]:
    expected_normalized = normalize_turkish_transcript(expected_narration)
    transcript_normalized = normalize_turkish_transcript(transcript)
    expected_tokens = _tokens(expected_narration)
    heard_tokens = _tokens(transcript)
    if not expected_tokens:
        raise ValueError('Expected narration must contain at least one word')

    distance = _edit_distance(expected_tokens, heard_tokens)
    denominator = max(len(expected_tokens), len(heard_tokens), 1)
    score = round(max(0.0, 1.0 - (distance / denominator)) * 100, 2)
    timestamps = _word_timestamps(words)
    ending_times = [
        item['end'] for item in timestamps if item['end'] is not None
    ]
    details = _mismatch_details(expected_tokens, heard_tokens)
    details['timestamp_sequence_match'] = (
        _tokens(' '.join(item['text'] for item in timestamps)) == heard_tokens
        if words is not None
        else None
    )

    return {
        'provider': str(provider or '') or None,
        'available': True,
        'pass': bool(details['exact_match']),
        'score': score,
        'transcript': str(transcript or ''),
        'normalized_expected': expected_normalized,
        'normalized_transcript': transcript_normalized,
        'language_code': str(language_code or '') or None,
        'language_probability': _language_probability(language_probability),
        'word_timestamps': timestamps,
        'ending_word_time': max(ending_times) if ending_times else None,
        'mismatch_details': details,
    }


def _unavailable_result() -> dict[str, Any]:
    return {
        'provider': None,
        'available': False,
        'pass': None,
        'score': None,
        'transcript': None,
        'normalized_expected': None,
        'normalized_transcript': None,
        'language_code': None,
        'language_probability': None,
        'word_timestamps': [],
        'ending_word_time': None,
        'mismatch_details': None,
        'reason': 'speech_to_text_keys_missing',
    }


def _response_payload(response: Any, provider_name: str) -> dict[str, Any]:
    status_code = getattr(response, 'status_code', None)
    if not isinstance(status_code, int) or not 200 <= status_code < 300:
        safe_status = status_code if isinstance(status_code, int) else 'unknown'
        raise AudioQCError(
            f'{provider_name} speech-to-text failed with HTTP {safe_status}'
        ) from None
    try:
        payload = response.json()
    except Exception:
        raise AudioQCError(
            f'{provider_name} speech-to-text returned invalid JSON'
        ) from None
    if not isinstance(payload, dict):
        raise AudioQCError(
            f'{provider_name} speech-to-text returned an invalid payload'
        )
    if not isinstance(payload.get('text'), str):
        raise AudioQCError(
            f'{provider_name} speech-to-text returned an invalid transcript'
        )
    words = payload.get('words')
    if words is not None and not isinstance(words, list):
        raise AudioQCError(
            f'{provider_name} speech-to-text returned invalid word timestamps'
        )
    return payload


def _require_word_timing_evidence(
    result: dict[str, Any],
    provider_name: str,
) -> dict[str, Any]:
    if str(result.get('transcript') or '').strip():
        timestamps = result.get('word_timestamps')
        invalid_timing = (
            not isinstance(timestamps, list)
            or not timestamps
            or result.get('ending_word_time') is None
            or float(result.get('ending_word_time') or 0.0) <= 0
        )
        previous_start = -1.0
        previous_end = -1.0
        if isinstance(timestamps, list):
            for item in timestamps:
                start = item.get('start') if isinstance(item, dict) else None
                end = item.get('end') if isinstance(item, dict) else None
                if (
                    start is None
                    or end is None
                    or end <= start
                    or start < previous_start
                    or end < previous_end
                ):
                    invalid_timing = True
                    break
                previous_start = start
                previous_end = end
        if invalid_timing:
            raise AudioQCError(
                f'{provider_name} speech-to-text returned incomplete word '
                'timestamps'
            )
        mismatch_details = result.get('mismatch_details')
        if (
            not isinstance(mismatch_details, dict)
            or mismatch_details.get('timestamp_sequence_match') is not True
        ):
            raise AudioQCError(
                f'{provider_name} speech-to-text returned inconsistent word '
                'timestamps'
            )
    return result


def _gemini_duration_seconds(value: Any) -> float | None:
    if not isinstance(value, str) or not _GEMINI_DURATION_PATTERN.fullmatch(
        value
    ):
        return None
    return _finite_time(value[:-1])


def _valid_gemini_annotation_text(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    tokens = _tokens(value)
    if len(tokens) == 1:
        return True
    if len(tokens) < 2 or not all(token.isdecimal() for token in tokens):
        return False
    # A decimal/grouping separator can split one numeric annotation into
    # several comparison tokens (for example ``4,8`` -> ``4 8``). Accept
    # that single annotated span, but never a whitespace-separated phrase or
    # control-character-delimited value.
    normalized = unicodedata.normalize('NFKC', value).strip()
    return all(
        character.isdecimal()
        or unicodedata.category(character).startswith(('P', 'S'))
        for character in normalized
    )


def _gemini_interaction_payload(response: Any) -> dict[str, Any]:
    status_code = getattr(response, 'status_code', None)
    if not isinstance(status_code, int) or not 200 <= status_code < 300:
        safe_status = status_code if isinstance(status_code, int) else 'unknown'
        raise AudioQCError(
            f'Gemini speech-to-text failed with HTTP {safe_status}'
        ) from None
    try:
        envelope = response.json()
    except Exception:
        raise AudioQCError(
            'Gemini speech-to-text returned invalid JSON'
        ) from None
    if not isinstance(envelope, dict):
        raise AudioQCError(
            'Gemini speech-to-text returned an invalid response envelope'
        )
    if envelope.get('error') is not None:
        raise AudioQCError(
            'Gemini speech-to-text reported an interaction error'
        )
    if str(envelope.get('status') or '').casefold() != 'completed':
        raise AudioQCError(
            'Gemini speech-to-text did not finish safely'
        )
    steps = envelope.get('steps')
    if not isinstance(steps, list):
        raise AudioQCError(
            'Gemini speech-to-text returned invalid word annotations'
        )
    model_output_steps: list[dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, dict):
            raise AudioQCError(
                'Gemini speech-to-text returned invalid word annotations'
            )
        if step.get('type') == 'model_output':
            model_output_steps.append(step)
    if not model_output_steps:
        raise AudioQCError(
            'Gemini speech-to-text omitted its transcript'
        )

    model_output_step = model_output_steps[-1]
    if model_output_step.get('error') is not None:
        raise AudioQCError(
            'Gemini speech-to-text reported an interaction error'
        )
    content = model_output_step.get('content')
    if not isinstance(content, list):
        raise AudioQCError(
            'Gemini speech-to-text returned invalid word annotations'
        )
    transcript_parts: list[str] = []
    normalized_words: list[dict[str, Any]] = []
    for content_item in content:
        if not isinstance(content_item, dict):
            raise AudioQCError(
                'Gemini speech-to-text returned invalid word annotations'
            )
        if content_item.get('type') != 'text':
            continue
        text = content_item.get('text')
        if not isinstance(text, str):
            raise AudioQCError(
                'Gemini speech-to-text returned an invalid transcript'
            )
        transcript_parts.append(text)
        annotations = content_item.get('annotations')
        if annotations is None:
            continue
        if not isinstance(annotations, list):
            raise AudioQCError(
                'Gemini speech-to-text returned invalid word annotations'
            )
        for annotation in annotations:
            if not isinstance(annotation, dict):
                raise AudioQCError(
                    'Gemini speech-to-text returned invalid word annotations'
                )
            if annotation.get('type') != 'word_info':
                continue
            word = annotation.get('text')
            start = _gemini_duration_seconds(
                annotation.get('start_offset')
            )
            end = _gemini_duration_seconds(annotation.get('end_offset'))
            if (
                not isinstance(word, str)
                or not word.strip()
                or not _valid_gemini_annotation_text(word)
                or start is None
                or end is None
                or end <= start
            ):
                raise AudioQCError(
                    'Gemini speech-to-text returned invalid word annotations'
                )
            normalized_words.append({
                'word': word.strip(),
                'start': start,
                'end': end,
            })
    if not transcript_parts:
        raise AudioQCError(
            'Gemini speech-to-text omitted its transcript'
        )
    return {
        'text': ''.join(transcript_parts),
        'language_code': 'tr-TR',
        'words': normalized_words,
    }


def _verify_with_openai(
    path: Path,
    expected_narration: str,
    api_key: str,
) -> dict[str, Any]:
    content_type = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
    try:
        with path.open('rb') as audio_file:
            response = httpx.post(
                OPENAI_AUDIO_TRANSCRIPTIONS_URL,
                headers=_openai_headers(api_key),
                data={
                    'model': 'whisper-1',
                    'language': 'tr',
                    'response_format': 'verbose_json',
                    'timestamp_granularities[]': 'word',
                    'temperature': '0',
                },
                files={
                    'file': (path.name, audio_file, content_type),
                },
                timeout=_SPEECH_TO_TEXT_TIMEOUT,
            )
    except Exception:
        raise AudioQCError(
            'OpenAI speech-to-text transport failed'
        ) from None

    payload = _response_payload(response, 'OpenAI')
    return _require_word_timing_evidence(
        compare_transcript(
            expected_narration,
            payload['text'],
            language_code=payload.get('language'),
            words=payload.get('words'),
            provider='openai',
        ),
        'OpenAI',
    )


def _verify_with_gemini(
    path: Path,
    expected_narration: str,
    api_key: str,
) -> dict[str, Any]:
    guessed_content_type = mimetypes.guess_type(path.name)[0] or ''
    content_type = _GEMINI_AUDIO_MIME_ALIASES.get(
        guessed_content_type,
        guessed_content_type,
    )
    if content_type not in _GEMINI_SUPPORTED_AUDIO_MIME_TYPES:
        raise AudioQCError(
            'Gemini speech-to-text audio format is unsupported'
        )
    try:
        audio_bytes = path.read_bytes()
    except Exception:
        raise AudioQCError(
            'Gemini speech-to-text could not read the audio input'
        ) from None
    if not audio_bytes:
        raise AudioQCError(
            'Gemini speech-to-text audio input is empty'
        )
    if len(audio_bytes) > _GEMINI_MAX_RAW_AUDIO_BYTES:
        raise AudioQCError(
            'Gemini speech-to-text inline audio exceeds the safe size limit'
        )

    request_body = {
        'model': _GEMINI_TRANSCRIBE_MODEL,
        'input': [{
            'type': 'audio',
            'data': base64.b64encode(audio_bytes).decode('ascii'),
            'mime_type': content_type,
        }],
        'store': False,
        'generation_config': {
            'transcription_config': {
                'language_codes': ['tr-TR'],
                'mode': {
                    'type': 'verbatim',
                    'timestamp_granularities': ['word'],
                },
            },
        },
    }
    try:
        response = httpx.post(
            GEMINI_INTERACTIONS_URL,
            headers={
                'x-goog-api-key': api_key,
                'Content-Type': 'application/json',
            },
            json=request_body,
            timeout=_SPEECH_TO_TEXT_TIMEOUT,
        )
    except Exception:
        raise AudioQCError(
            'Gemini speech-to-text transport failed'
        ) from None

    payload = _gemini_interaction_payload(response)
    return _require_word_timing_evidence(
        compare_transcript(
            expected_narration,
            payload['text'],
            language_code=payload.get('language_code'),
            words=payload.get('words'),
            provider='gemini',
        ),
        'Gemini',
    )


def _verify_with_elevenlabs(
    path: Path,
    expected_narration: str,
    api_key: str,
) -> dict[str, Any]:
    content_type = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
    try:
        with path.open('rb') as audio_file:
            response = httpx.post(
                ELEVENLABS_SPEECH_TO_TEXT_URL,
                headers=_elevenlabs_headers(api_key),
                data={
                    'model_id': 'scribe_v2',
                    'language_code': 'tur',
                    'num_speakers': '1',
                    'tag_audio_events': 'false',
                    'timestamps_granularity': 'word',
                },
                files={
                    'file': (path.name, audio_file, content_type),
                },
                timeout=_SPEECH_TO_TEXT_TIMEOUT,
            )
    except Exception:
        raise AudioQCError(
            'ElevenLabs speech-to-text transport failed'
        ) from None

    payload = _response_payload(response, 'ElevenLabs')
    return _require_word_timing_evidence(
        compare_transcript(
            expected_narration,
            payload['text'],
            language_code=payload.get('language_code'),
            language_probability=payload.get('language_probability'),
            words=payload.get('words'),
            provider='elevenlabs',
        ),
        'ElevenLabs',
    )


def verify_audio_narration(
    audio_path: str | Path,
    expected_narration: str,
) -> dict[str, Any]:
    """Transcribe an audio master and compare it with its spoken contract."""
    openai_api_key = str(getattr(settings, 'openai_api_key', '') or '')
    gemini_api_key = str(getattr(settings, 'gemini_api_key', '') or '')
    elevenlabs_api_key = str(
        getattr(settings, 'elevenlabs_api_key', '') or ''
    )
    if not openai_api_key and not gemini_api_key and not elevenlabs_api_key:
        return _unavailable_result()

    if not _tokens(expected_narration):
        raise ValueError('Expected narration must contain at least one word')

    path = Path(audio_path)
    if not path.is_file():
        raise AudioQCError('Audio QC input file is unavailable')

    provider_errors: list[AudioQCError] = []
    mismatch_results: list[dict[str, Any]] = []
    if openai_api_key:
        try:
            openai_result = _verify_with_openai(
                path,
                expected_narration,
                openai_api_key,
            )
        except AudioQCError as exc:
            # OpenAI is primary, but a provider failure must not block the
            # independent ElevenLabs verification path.
            provider_errors.append(exc)
        else:
            if openai_result['pass']:
                return openai_result
            mismatch_results.append(openai_result)

    if gemini_api_key:
        try:
            gemini_result = _verify_with_gemini(
                path,
                expected_narration,
                gemini_api_key,
            )
        except AudioQCError as exc:
            provider_errors.append(exc)
        else:
            if gemini_result['pass']:
                return gemini_result
            mismatch_results.append(gemini_result)

    if elevenlabs_api_key:
        try:
            elevenlabs_result = _verify_with_elevenlabs(
                path,
                expected_narration,
                elevenlabs_api_key,
            )
        except AudioQCError as exc:
            provider_errors.append(exc)
        else:
            if elevenlabs_result['pass']:
                return elevenlabs_result
            mismatch_results.append(elevenlabs_result)

    if mismatch_results:
        # Keep the most useful mismatch diagnosis. ``max`` preserves OpenAI
        # and then Gemini on score ties because they are evaluated first.
        return max(
            mismatch_results,
            key=lambda result: float(result.get('score') or 0.0),
        )

    if len(provider_errors) == 1:
        raise provider_errors[0] from None

    raise AudioQCError(
        'Audio QC transcription failed for all configured providers'
    ) from None
