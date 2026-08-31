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
_COMPARISON_TOKEN_PATTERN = re.compile(
    r'(?:[+\-\u2212\u00b1]\s*)?\d+(?:[,.]\d+)?[^\W\d_]*'
    r'|[+\-\u2212\u00b1](?=\s*[+\-\u2212\u00b1]\s*\d)'
    r'|(?<=\d)\s*[/:×÷*\u2044\u2215]\s*(?=\d)'
    r'|[^\W\d_]+'
    r'|[%‰₺$€£¥]'
)
_DIGIT_TOKEN_PATTERN = re.compile(
    r'(?P<sign>[+\-\u2212\u00b1]?)(?P<integer>\d+)'
    r'(?:(?P<separator>[,.])(?P<fraction>\d+))?'
    r'(?P<suffix>[^\W\d_]*)$'
)
_NUMBER_UNITS = {
    's\u0131f\u0131r': 0,
    'bir': 1,
    'iki': 2,
    '\u00fc\u00e7': 3,
    'd\u00f6rt': 4,
    'be\u015f': 5,
    'alt\u0131': 6,
    'yedi': 7,
    'sekiz': 8,
    'dokuz': 9,
}
_NUMBER_TENS = {
    'on': 10,
    'yirmi': 20,
    'otuz': 30,
    'k\u0131rk': 40,
    'elli': 50,
    'altm\u0131\u015f': 60,
    'yetmi\u015f': 70,
    'seksen': 80,
    'doksan': 90,
}
_NUMBER_SCALES = {
    'bin': 1_000,
    'milyon': 1_000_000,
    'milyar': 1_000_000_000,
    'trilyon': 1_000_000_000_000,
}
_NUMBER_WORDS = frozenset({
    *_NUMBER_UNITS,
    *_NUMBER_TENS,
    *_NUMBER_SCALES,
    'y\u00fcz',
    'virg\u00fcl',
})
# Only the locative suffix needed for forms such as ``yedi-de``/``1997'de``
# is detached from unambiguous unit/tens words. Broader suffix guessing would
# reinterpret ordinary Turkish words such as ``onda`` or ``yüzde`` as numbers.
_NUMBER_SUFFIXES = frozenset({'da', 'de', 'ta', 'te'})
_NUMBER_BASES_BY_LENGTH = tuple(
    sorted(
        (set(_NUMBER_UNITS) | set(_NUMBER_TENS)) - {'bir', 'on'},
        key=len,
        reverse=True,
    )
)
_PROPER_NAME_SUFFIXES = frozenset({
    '',
    'da',
    'de',
    'daki',
    'deki',
    'dan',
    'den',
    'ya',
    'ye',
    'nun',
    'nin',
})
_GEMINI_ANNOTATION_TRAILING_PUNCTUATION = '.,!?;:\u2026'


class AudioQCError(RuntimeError):
    """A secret-safe failure while obtaining an audio-QC transcript."""


def _openai_headers(api_key: str) -> dict[str, str]:
    return {'Authorization': f'Bearer {api_key}'}


def _elevenlabs_headers(api_key: str) -> dict[str, str]:
    return {'xi-api-key': api_key}


def _turkish_lower(text: str) -> str:
    value = unicodedata.normalize('NFKC', str(text or ''))
    # Python's default lower/casefold rules are not Turkish-locale aware.
    return value.translate(
        str.maketrans({'I': '\u0131', '\u0130': 'i'})
    ).casefold()


def normalize_turkish_transcript(text: str) -> str:
    """Normalize Turkish casing and punctuation without folding letters."""
    value = _turkish_lower(text)

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


def _orthographic_fold(token: str) -> str:
    decomposed = unicodedata.normalize('NFD', token)
    folded = unicodedata.normalize('NFC', ''.join(
        character
        for character in decomposed
        if character != '\u0302'
    ))

    # Turkish circumflexes are inconsistently retained in modern orthography.
    # Keep every other diacritic lexical; for example ``oldu`` and ``\u00f6ld\u00fc``
    # must remain different. Gemini's Tokio/Tokyo spelling is the sole alias.
    for suffix in _PROPER_NAME_SUFFIXES:
        if folded == f'tokio{suffix}':
            return f'tokyo{suffix}'
    return folded


def _comparison_lexical_tokens(text: str) -> list[str]:
    value = _turkish_lower(text)
    value = value.translate({ord(character): None for character in _APOSTROPHES})
    return _COMPARISON_TOKEN_PATTERN.findall(value)


def _timestamp_boundary_sequence(tokens: list[str]) -> tuple[str, ...]:
    """Collapse only adjacent alphabetic tokens for boundary comparison.

    Providers may split or merge an otherwise identical Turkish word (for
    example ``okyanusa`` versus ``okyanus``, ``a``).  Numeric, sign, operator,
    and currency tokens stay separate so ``2`` + ``9`` can never prove a
    timestamp for ``29`` and punctuation cannot manufacture a decimal.
    """
    sequence: list[str] = []
    alphabetic_run: list[str] = []

    def flush_alphabetic_run() -> None:
        if alphabetic_run:
            sequence.append(''.join(alphabetic_run))
            alphabetic_run.clear()

    for token in tokens:
        if token and all(
            character.isalpha()
            or unicodedata.category(character).startswith('M')
            for character in token
        ):
            alphabetic_run.append(token)
            continue
        flush_alphabetic_run()
        sequence.append(token)
    flush_alphabetic_run()
    return tuple(sequence)


def _ascii_digits(value: str) -> str:
    return ''.join(str(unicodedata.decimal(character)) for character in value)


def _numeric_key(value: str, suffix: str = '') -> str:
    return f'\x00number:{value}:{suffix}'


def _canonical_digit_token(token: str) -> str | None:
    compact_token = re.sub(r'\s+', '', token)
    match = _DIGIT_TOKEN_PATTERN.fullmatch(compact_token)
    if match is None:
        return None
    suffix = _orthographic_fold(match.group('suffix'))
    if suffix and suffix not in _NUMBER_SUFFIXES:
        return None

    sign = match.group('sign')
    if sign == '\u2212':
        sign = '-'
    integer = _ascii_digits(match.group('integer'))
    fraction = match.group('fraction')
    if fraction is not None:
        value = f'{sign}{integer},{_ascii_digits(fraction)}'
    else:
        value = f'{sign}{integer}'
    return _numeric_key(value, suffix)


def _parse_under_thousand(words: list[str]) -> int | None:
    if words == ['s\u0131f\u0131r']:
        return 0
    if not words:
        return None

    value = 0
    index = 0
    if (
        len(words) >= 2
        and words[0] in _NUMBER_UNITS
        and _NUMBER_UNITS[words[0]] > 0
        and words[1] == 'y\u00fcz'
    ):
        value += _NUMBER_UNITS[words[0]] * 100
        index = 2
    elif words[0] == 'y\u00fcz':
        value = 100
        index = 1

    if index < len(words) and words[index] in _NUMBER_TENS:
        value += _NUMBER_TENS[words[index]]
        index += 1
    if (
        index < len(words)
        and words[index] in _NUMBER_UNITS
        and _NUMBER_UNITS[words[index]] > 0
    ):
        value += _NUMBER_UNITS[words[index]]
        index += 1
    if index != len(words):
        return None
    return value if value > 0 else None


def _parse_integer_words(words: list[str]) -> int | None:
    if not words:
        return None
    if not any(word in _NUMBER_SCALES for word in words):
        return _parse_under_thousand(words)

    total = 0
    group: list[str] = []
    previous_scale = math.inf
    for word in words:
        scale = _NUMBER_SCALES.get(word)
        if scale is None:
            group.append(word)
            continue
        if scale >= previous_scale:
            return None
        if group:
            group_value = _parse_under_thousand(group)
            if group_value is None or group_value == 0:
                return None
        elif word == 'bin':
            group_value = 1
        else:
            return None
        total += group_value * scale
        group = []
        previous_scale = scale

    if group:
        group_value = _parse_under_thousand(group)
        if group_value is None:
            return None
        total += group_value
    return total if total > 0 else None


def _parse_number_words(words: list[str]) -> str | None:
    decimal_positions = [
        index for index, word in enumerate(words) if word == 'virg\u00fcl'
    ]
    if not decimal_positions:
        integer = _parse_integer_words(words)
        return str(integer) if integer is not None else None
    if len(decimal_positions) != 1:
        return None

    decimal_index = decimal_positions[0]
    integer = _parse_integer_words(words[:decimal_index])
    fraction_words = words[decimal_index + 1:]
    if integer is None or not fraction_words:
        return None
    if all(word in _NUMBER_UNITS for word in fraction_words):
        fraction = ''.join(
            str(_NUMBER_UNITS[word]) for word in fraction_words
        )
    else:
        # A following scale word is normally the unit of the decimal phrase
        # (``dört virgül sekiz milyon``), not part of its fractional digits.
        fraction_value = _parse_under_thousand(fraction_words)
        if fraction_value is None:
            return None
        fraction = str(fraction_value)
    return f'{integer},{fraction}'


def _split_number_word(token: str) -> tuple[str, str] | None:
    folded = _orthographic_fold(token)
    if folded in _NUMBER_WORDS:
        return folded, ''
    for base in _NUMBER_BASES_BY_LENGTH:
        if folded.startswith(base):
            suffix = folded[len(base):]
            if suffix in _NUMBER_SUFFIXES:
                return base, suffix
    return None


def _number_word_unit(
    tokens: list[str],
    start: int,
) -> tuple[str, int] | None:
    words: list[str] = []
    suffixes: list[str] = []
    for token in tokens[start:]:
        split = _split_number_word(token)
        if split is None:
            break
        word, suffix = split
        words.append(word)
        suffixes.append(suffix)
        if suffix:
            break

    for consumed in range(len(words), 0, -1):
        number = _parse_number_words(words[:consumed])
        if number is None:
            continue
        suffix = suffixes[consumed - 1]
        return _numeric_key(number, suffix), consumed
    return None


def _comparison_units(text: str) -> list[tuple[str, tuple[str, ...]]]:
    tokens = _comparison_lexical_tokens(text)
    units: list[tuple[str, tuple[str, ...]]] = []
    index = 0
    while index < len(tokens):
        digit_key = _canonical_digit_token(tokens[index])
        if digit_key is not None:
            units.append((digit_key, (tokens[index],)))
            index += 1
            continue

        word_unit = _number_word_unit(tokens, index)
        if word_unit is not None:
            number_key, consumed = word_unit
            units.append((number_key, tuple(tokens[index:index + consumed])))
            index += consumed
            continue

        units.append((_orthographic_fold(tokens[index]), (tokens[index],)))
        index += 1
    return units


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
    *,
    expected_sources: list[tuple[str, ...]] | None = None,
    heard_sources: list[tuple[str, ...]] | None = None,
) -> dict[str, Any]:
    expected_sources = expected_sources or [
        (token,) for token in expected_tokens
    ]
    heard_sources = heard_sources or [(token,) for token in heard_tokens]

    def source_slice(
        sources: list[tuple[str, ...]],
        start: int,
        end: int,
    ) -> list[str]:
        return [token for source in sources[start:end] for token in source]

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
        expected_slice = source_slice(
            expected_sources,
            expected_start,
            expected_end,
        )
        heard_slice = source_slice(
            heard_sources,
            heard_start,
            heard_end,
        )
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
            return []
        raw_word_type = item.get('type')
        if raw_word_type is not None and not isinstance(raw_word_type, str):
            return []
        word_type = raw_word_type or 'word'
        if word_type == 'spacing':
            spacing = item.get('text')
            if not isinstance(spacing, str) or spacing.strip():
                return []
            continue
        if word_type != 'word':
            return []
        # ElevenLabs uses ``text`` while OpenAI's verbose transcription uses
        # ``word`` for the same value.
        raw_text = item.get('text')
        if raw_text is None:
            raw_text = item.get('word')
        if not isinstance(raw_text, str) or not raw_text.strip():
            return []
        text = raw_text.strip()
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
    if not _tokens(expected_narration):
        raise ValueError('Expected narration must contain at least one word')
    surface_heard_tokens = _comparison_lexical_tokens(transcript)

    expected_units = _comparison_units(expected_narration)
    heard_units = _comparison_units(transcript)
    expected_tokens = [unit[0] for unit in expected_units]
    heard_tokens = [unit[0] for unit in heard_units]

    distance = _edit_distance(expected_tokens, heard_tokens)
    denominator = max(len(expected_tokens), len(heard_tokens), 1)
    score = round(max(0.0, 1.0 - (distance / denominator)) * 100, 2)
    timestamps = _word_timestamps(words)
    ending_times = [
        item['end'] for item in timestamps if item['end'] is not None
    ]
    details = _mismatch_details(
        expected_tokens,
        heard_tokens,
        expected_sources=[unit[1] for unit in expected_units],
        heard_sources=[unit[1] for unit in heard_units],
    )
    timestamp_tokens = [
        token
        for item in timestamps
        for token in _comparison_lexical_tokens(item['text'])
    ]
    details['timestamp_sequence_match'] = (
        (
            timestamp_tokens == surface_heard_tokens
            or _timestamp_boundary_sequence(timestamp_tokens)
            == _timestamp_boundary_sequence(surface_heard_tokens)
        )
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
        previous_end = -1.0
        if isinstance(timestamps, list):
            for item in timestamps:
                start = item.get('start') if isinstance(item, dict) else None
                end = item.get('end') if isinstance(item, dict) else None
                if (
                    start is None
                    or end is None
                    or end <= start
                    or start < previous_end
                ):
                    invalid_timing = True
                    break
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
    normalized = unicodedata.normalize('NFKC', value).strip()
    if normalized[-1] in _GEMINI_ANNOTATION_TRAILING_PUNCTUATION:
        normalized = normalized[:-1]
        if (
            not normalized
            or normalized[-1].isspace()
            or normalized[-1] in _GEMINI_ANNOTATION_TRAILING_PUNCTUATION
        ):
            return False
    if not normalized:
        return False
    if re.fullmatch(
        r'[+\-\u2212\u00b1]?\d+(?:[,.]\d+)?',
        normalized,
    ):
        return True
    tokens = _tokens(normalized)
    if len(tokens) == 1 and all(
        character.isalnum()
        or unicodedata.category(character).startswith('M')
        or character in _APOSTROPHES
        for character in normalized
    ):
        return True
    return False


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
                not _valid_gemini_annotation_text(word)
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
