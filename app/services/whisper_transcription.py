"""One reserved Whisper transcription of an immutable, decoded Short audio.

Official price reviewed 2026-09-09: $0.006 per minute, whisper-1 only.
https://developers.openai.com/api/docs/models/whisper-1
https://developers.openai.com/api/docs/guides/speech-to-text

Reserve a full minute for at most 30.08 decoded seconds. This is a list-cost
ceiling, not proof of account credit coverage, taxes, or an invoice. The normal
funding ledger independently admits the actual credential and exact revision.
No provider retry, fallback, upload object, refund, or balance refresh exists.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import struct
import subprocess

import httpx

from app.services.production_spend import SpendBlocked, SpendQuote
from app.services import production_spend_runtime as spending


WHISPER_ROUTE = 'https://api.openai.com/v1/audio/transcriptions'
WHISPER_PRICE_REVISION = 'whisper-short-2026-09-09-v1'
WHISPER_MAX_AUDIO_BYTES = 8 * 1024 * 1024
WHISPER_MAX_SAMPLES = 1_443_840  # 30.08 seconds at the measured 48 kHz rate.
NATURAL_SHORT_MAX_SAMPLES = 1_923_840  # 40.08 seconds; still within the reserved minute.
_SAMPLE_RATE = 48_000
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_VALID_FROM = datetime(2026, 9, 9, tzinfo=timezone.utc)
_VALID_UNTIL = datetime(2026, 10, 1, tzinfo=timezone.utc)
_TIMEOUT = httpx.Timeout(180.0, connect=10.0)
_FIELDS = {'model': 'whisper-1', 'response_format': 'verbose_json',
           'timestamp_granularities[]': 'word', 'temperature': '0'}


class WhisperTranscriptionError(RuntimeError):
    """Terminal after submission: the conservative reservation remains held."""


@dataclass(frozen=True)
class _AudioSnapshot:
    raw: bytes = field(repr=False)
    filename: str
    mime_type: str
    samples: int
    pcm_sha256: str

    def descriptor(self, fields):
        return {'version': 1, 'fields': dict(fields), 'audio': {
            'sha256': hashlib.sha256(self.raw).hexdigest(), 'bytes': len(self.raw),
            'mime_type': self.mime_type, 'decoded_sample_rate': _SAMPLE_RATE,
            'decoded_samples': self.samples, 'decoded_pcm_sha256': self.pcm_sha256,
        }}


def _require(condition):
    if not condition:
        raise SpendBlocked('spend_request_not_priced')


def _read_audio(path):
    """One bounded regular-file read; no upload stream or mutable caller buffer."""
    try:
        path = Path(path)
        suffix = path.suffix.lower()
        _require(suffix in {'.mp3', '.wav'})
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as handle:
            before = os.fstat(handle.fileno())
            _require(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= WHISPER_MAX_AUDIO_BYTES)
            raw = handle.read(WHISPER_MAX_AUDIO_BYTES + 1)
            after = os.fstat(handle.fileno())
        identity = lambda value: (value.st_dev, value.st_ino, value.st_size,
                                  value.st_mtime_ns, value.st_ctime_ns)
        _require(identity(before) == identity(after) and len(raw) == before.st_size)
        return raw, suffix
    except SpendBlocked:
        raise
    except Exception:
        raise SpendBlocked('spend_request_not_priced') from None


def _wav_layout(raw, *, max_samples=WHISPER_MAX_SAMPLES):
    """Accept one complete PCM16 RIFF file with exactly one actual data chunk."""
    _require(len(raw) >= 44 and raw[:4] == b'RIFF' and raw[8:12] == b'WAVE'
             and int.from_bytes(raw[4:8], 'little') + 8 == len(raw))
    offset, fmt, data_samples, chunks = 12, None, None, 0
    while offset < len(raw):
        _require(offset + 8 <= len(raw) and chunks < 16)
        name, size = raw[offset:offset + 4], int.from_bytes(raw[offset + 4:offset + 8], 'little')
        offset += 8
        _require(offset + size + size % 2 <= len(raw))
        chunk = raw[offset:offset + size]
        if name == b'fmt ':
            _require(fmt is None and data_samples is None and size in {16, 18})
            codec, channels, rate, byte_rate, align, bits = struct.unpack('<HHIIHH', chunk[:16])
            _require(codec == 1 and channels in {1, 2} and 8000 <= rate <= 96_000
                     and bits == 16 and align == channels * 2 and byte_rate == rate * align
                     and (size == 16 or chunk[16:] == b'\0\0'))
            fmt = rate, align
        elif name == b'data':
            _require(fmt is not None and data_samples is None and size > 0 and size % fmt[1] == 0)
            data_samples = size // fmt[1]
            _require(data_samples * _SAMPLE_RATE <= max_samples * fmt[0])
        else:
            _require(name in {b'LIST', b'JUNK', b'bext'} and size <= 65_536)
        if size % 2:
            _require(raw[offset + size] == 0)
        offset += size + size % 2
        chunks += 1
    _require(offset == len(raw) and data_samples is not None)


def _mp3_layout(raw, *, max_encoded_seconds=32):
    """Walk every Layer III frame, excluding hidden files and trailing payloads."""
    offset = 0
    if raw.startswith(b'ID3'):
        _require(len(raw) >= 10 and raw[3] in {3, 4} and raw[4:6] == b'\0\0'
                 and all(value < 128 for value in raw[6:10]))
        size = sum(value << (7 * (3 - index)) for index, value in enumerate(raw[6:10]))
        _require(size <= 65_536)
        offset = 10 + size
    frames, signature, samples = 0, None, 0
    while offset < len(raw):
        _require(offset + 4 <= len(raw))
        header = int.from_bytes(raw[offset:offset + 4], 'big')
        version, layer = (header >> 19) & 3, (header >> 17) & 3
        bitrate_index, rate_index = (header >> 12) & 15, (header >> 10) & 3
        _require(header >> 21 == 0x7ff and version != 1 and layer == 1
                 and 1 <= bitrate_index <= 14 and rate_index < 3 and header & 3 != 2)
        rate = (44100, 48000, 32000)[rate_index] // ({3: 1, 2: 2, 0: 4}[version])
        channels = 1 if (header >> 6) & 3 == 3 else 2
        current = version, rate, channels
        _require(signature is None or current == signature)
        signature = current
        bitrates = ((32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
                    if version == 3 else (8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160))
        size = (144 if version == 3 else 72) * bitrates[bitrate_index - 1] * 1000 // rate
        size += (header >> 9) & 1
        _require(size >= 4 and offset + size <= len(raw))
        offset += size
        frames += 1
        samples += 1152 if version == 3 else 576
        # Includes encoder delay/padding and an optional Xing header frame.
        # Longer encoded input is rejected before invoking the actual decoder.
        _require(samples <= rate * max_encoded_seconds)
    _require(frames > 0 and offset == len(raw))


def _snapshot_audio(raw, suffix, *, allow_natural_short=False, allow_commissioned_long=False):
    try:
        _require(type(allow_natural_short) is bool and type(allow_commissioned_long) is bool)
        maximum = NATURAL_SHORT_MAX_SAMPLES if allow_natural_short else WHISPER_MAX_SAMPLES
        if allow_commissioned_long:
            maximum = 240 * _SAMPLE_RATE
        _require(type(raw) is bytes and 0 < len(raw) <= WHISPER_MAX_AUDIO_BYTES)
        if suffix == '.wav':
            _wav_layout(raw, max_samples=maximum)
            demuxer, mime = 'wav', 'audio/wav'
        else:
            _require(suffix == '.mp3')
            _mp3_layout(raw, max_encoded_seconds=242 if allow_commissioned_long else 42 if allow_natural_short else 32)
            demuxer, mime = 'mp3', 'audio/mpeg'
        # Decode beyond the acceptance bound, never truncate an accepted clip.
        # Only explicit commissioning/natural-prosody callers use the 40s bound.
        result = subprocess.run([
            'ffmpeg', '-v', 'error', '-nostdin', '-xerror', '-err_detect', 'explode',
            '-threads', '1', '-protocol_whitelist', 'pipe', '-f', demuxer, '-i', 'pipe:0',
            '-map', '0:a:0', '-vn', '-sn', '-dn', '-t', '241' if allow_commissioned_long else '41' if allow_natural_short else '31', '-ac', '1', '-ar', str(_SAMPLE_RATE),
            '-c:a', 'pcm_s16le', '-threads', '1', '-f', 's16le', 'pipe:1',
        ], input=raw, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10, check=True)
        pcm = result.stdout
        _require(len(pcm) % 2 == 0 and 0 < len(pcm) // 2 <= maximum)
        return _AudioSnapshot(raw, 'narration' + suffix, mime, len(pcm) // 2,
                              hashlib.sha256(pcm).hexdigest())
    except SpendBlocked:
        raise
    except Exception:
        raise SpendBlocked('spend_request_not_priced') from None


def inspect_bounded_short_audio(raw: bytes, mime_type: str, *, allow_natural_short=False, allow_commissioned_long=False) -> dict:
    """Validate complete original Short bytes without uploading or rewriting them.

    Both native audio reviewers use the same strict container walk and full
    bounded decode. The descriptor identifies the original file and measured
    PCM; it grants neither a provider request nor a budget reservation.
    """
    _require(type(mime_type) is str and mime_type in {'audio/mpeg', 'audio/wav'})
    suffix = '.mp3' if mime_type == 'audio/mpeg' else '.wav'
    return _snapshot_audio(raw, suffix, allow_natural_short=allow_natural_short,
                           allow_commissioned_long=allow_commissioned_long).descriptor({})['audio']


def _quote(fields):
    if not _VALID_FROM <= datetime.now(timezone.utc) < _VALID_UNTIL:
        raise SpendBlocked('spend_price_review_expired')
    _require(type(fields) is dict and set(fields) == {
        'model', 'language', 'response_format', 'timestamp_granularities[]', 'temperature',
    } and all(type(value) is str for value in fields.values())
        and fields['model'] == 'whisper-1' and fields['language'] in {'en', 'tr'}
        and fields['response_format'] == 'verbose_json'
        and fields['timestamp_granularities[]'] == 'word' and fields['temperature'] == '0')
    return SpendQuote('openai', 'whisper-1', 6000, WHISPER_PRICE_REVISION)


def _json_payload(raw, *, maximum_seconds=60):
    _require(type(maximum_seconds) is int and maximum_seconds in (60, 240))
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    def finite(text):
        value = float(text)
        if not math.isfinite(value):
            raise ValueError
        return value

    def invalid(_):
        raise ValueError

    payload = json.loads(raw, object_pairs_hook=pairs, parse_float=finite, parse_constant=invalid)
    if (type(payload) is not dict or payload.get('task') != 'transcribe'
            or type(payload.get('text')) is not str or len(payload['text'].encode('utf-8')) > 100_000
            or type(payload.get('language')) is not str
            or not 1 <= len(payload['language'].encode('utf-8')) <= 128
            or type(payload.get('duration')) not in (int, float)
            or not math.isfinite(payload['duration']) or not 0 <= payload['duration'] <= maximum_seconds
            or type(payload.get('words')) is not list or len(payload['words']) > 2048):
        raise ValueError
    for item in payload['words']:
        if (type(item) is not dict or set(item) != {'word', 'start', 'end'}
                or type(item['word']) is not str or not item['word'].strip()
                or len(item['word'].encode('utf-8')) > 4096
                or any(type(item[name]) not in (int, float) or not math.isfinite(item[name])
                       for name in ('start', 'end'))
                or not 0 <= item['start'] <= item['end'] <= maximum_seconds):
            raise ValueError
    if payload['text'].strip() and not payload['words']:
        raise ValueError
    # Legacy responses may omit usage: never interpret that as zero spending.
    if 'usage' in payload:
        usage = payload['usage']
        if (type(usage) is not dict or set(usage) != {'type', 'seconds'}
                or usage['type'] != 'duration' or type(usage['seconds']) is not int
                or not 0 <= usage['seconds'] <= maximum_seconds):
            raise ValueError
    return payload


def _post_bounded(snapshot, fields, headers, *, maximum_seconds=60):
    if not spending.enforcement_enabled():
        raise SpendBlocked('spend_not_enabled')
    with httpx.stream(
        'POST', WHISPER_ROUTE, headers=headers, data=fields,
        files={'file': (snapshot.filename, snapshot.raw, snapshot.mime_type)},
        timeout=_TIMEOUT, follow_redirects=False, trust_env=False,
    ) as response:
        if response.status_code != 200:
            raise WhisperTranscriptionError('whisper_request_rejected')
        chunks, size = [], 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > _MAX_RESPONSE_BYTES:
                raise WhisperTranscriptionError('whisper_response_too_large')
            chunks.append(chunk)
        raw = b''.join(chunks)
        try:
            _json_payload(raw, maximum_seconds=maximum_seconds)
        except Exception:
            raise WhisperTranscriptionError('whisper_response_invalid') from None
        # Keep transcript evidence while discarding transport URLs/auth/cookies.
        return httpx.Response(200, content=raw, headers={'Content-Type': 'application/json'})


def transcribe_whisper_bounded(path, *, api_key: str, language: str) -> httpx.Response:
    """Freeze exact Short audio/form/auth, reserve once, then dispatch once.

    The caller must propagate WhisperTranscriptionError and SpendBlocked through
    provider fallbacks and queue retry handlers. All QA thresholds stay outside
    this transport; a valid transcript is never a narration/voice approval.
    """
    if not spending.enforcement_enabled():
        raise SpendBlocked('spend_not_enabled')
    _require(type(language) is str and language in {'en', 'tr'})
    funding = spending._funding_admission('openai', '/v1/audio/transcriptions', api_key)
    headers = {'Authorization': 'Bearer ' + api_key, 'Accept': 'application/json'}
    fields = {**_FIELDS, 'language': language}
    quote = _quote(fields)
    raw, suffix = _read_audio(path)
    snapshot = _snapshot_audio(raw, suffix)
    descriptor = snapshot.descriptor(fields)
    spending.reserve_request('openai', '/v1/audio/transcriptions', descriptor, quote, funding=funding)
    try:
        return _post_bounded(snapshot, fields, headers)
    except SpendBlocked:
        raise
    except WhisperTranscriptionError:
        raise
    except Exception:
        raise WhisperTranscriptionError('whisper_transport_failed') from None
