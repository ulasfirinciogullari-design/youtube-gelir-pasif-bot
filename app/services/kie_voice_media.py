"""Preserve accepted Kie audio and its complete MP3 derivative for retries."""
from io import BytesIO
from pathlib import Path
import json
import subprocess
import tempfile
from time import sleep as _sleep
from urllib.parse import urlparse
import wave

import httpx
from botocore.exceptions import ClientError, ConnectionClosedError, EndpointConnectionError, ReadTimeoutError

from app.config import settings
from app.services import kie_voice_ledger as ledger, storage, whisper_transcription as whisper

MAX_BYTES = 24 * 1024 * 1024


def _storage_call(operation, *args, **kwargs):
    """Retry only idempotent private-object IO, never a paid generation call."""
    for attempt in range(3):
        try:
            return operation(*args, **kwargs)
        except ClientError as error:
            status = error.response.get('ResponseMetadata', {}).get('HTTPStatusCode')
            code = error.response.get('Error', {}).get('Code')
            if (status not in {429, 500, 502, 503, 504}
                    and code not in {'RequestTimeout', 'SlowDown', 'InternalError', 'ServiceUnavailable'}):
                raise
            if attempt == 2:
                raise
        except (ConnectionClosedError, EndpointConnectionError, ReadTimeoutError):
            if attempt == 2:
                raise
        _sleep(.5 * (2 ** attempt))


def _read_object(s3, key, bound):
    obj = s3.get_object(Bucket=settings.bucket, Key=key)
    ledger.require(0 < obj['ContentLength'] <= bound, 'kie_voice_media_invalid')
    try:
        data = obj['Body'].read(bound + 1)
    finally:
        obj['Body'].close()
    ledger.require(len(data) == obj['ContentLength'] <= bound, 'kie_voice_media_invalid')
    return data


def _get(s3, key, bound):
    return _storage_call(_read_object, s3, key, bound)


def mp3(client, identity, result, *, longform):
    """Read only this terminal receipt; no TTS call or cross-provider fallback."""
    ledger.require(type(longform) is bool and len(identity) == 64)
    key = ledger.PREFIX + 'media:' + identity
    with client.pipeline() as pipe:
        _, journal, _ = ledger._read(pipe)
        row = journal['requests'][identity]
        ledger.require(row['result'] is not None)
        data = ledger.restore(row['result']).json()['data']
        ledger.require(data['state'] == 'success' and data['taskId'] == result['task_id']
            and json.loads(data['resultJson']) == result['result'])
        pipe.multi(); pipe.ping(); ledger.require(pipe.execute() == [True])
    s3 = storage._client(single_attempt=True)
    previous = client.get(key)
    if previous is not None:
        record = json.loads(previous)
        ledger.require(client.pttl(key) == -1 and record['request_identity'] == identity
            and record['result_receipt_sha256'] == row['result']['response_sha256'])
        audio = _get(s3, record['mp3_key'], whisper.WHISPER_MAX_AUDIO_BYTES)
        ledger.require(ledger.sha(audio) == record['mp3_sha256'])
        return audio
    urls = result['result'].get('resultUrls')
    ledger.require(type(urls) is list and len(urls) == 1 and type(urls[0]) is str)
    u = urlparse(urls[0])
    ledger.require(u.scheme == 'https' and u.hostname == 'file.aiquickdraw.com'
        and u.port in (None, 443) and not u.username and not u.password and not u.fragment
        and u.path.endswith('.wav'), 'kie_voice_media_host_unverified')
    source_key = ledger.PREFIX + 'media_source:' + identity
    saved_source = client.get(source_key)
    if saved_source is not None:
        source = json.loads(saved_source)
        ledger.require(client.pttl(source_key) == -1 and source['request_identity'] == identity
            and source['result_receipt_sha256'] == row['result']['response_sha256'])
        original = _get(s3, source['object_key'], MAX_BYTES)
        ledger.require(ledger.sha(original) == source['sha256'])
    else:
        with httpx.Client(timeout=45, trust_env=False, follow_redirects=False) as http:
            with http.stream('GET', urls[0]) as response:
                ledger.require(response.status_code == 200, 'kie_voice_media_unavailable')
                chunks, size = [], 0
                for part in response.iter_bytes():
                    size += len(part)
                    ledger.require(size <= MAX_BYTES, 'kie_voice_media_invalid')
                    chunks.append(part)
                original = b''.join(chunks)
        object_key = 'provider_audio/kie/' + identity + '/' + ledger.sha(original) + '.wav'
        _storage_call(s3.put_object, Bucket=settings.bucket, Key=object_key, Body=original, ContentType='audio/wav')
        ledger.require(ledger.sha(_get(s3, object_key, MAX_BYTES)) == ledger.sha(original))
        source = {'version': 1, 'request_identity': identity, 'sha256': ledger.sha(original),
            'object_key': object_key, 'result_receipt_sha256': row['result']['response_sha256']}
        encoded_source = ledger.raw(source)
        ledger.require(client.set(source_key, encoded_source, nx=True) is True
            or client.get(source_key) in (encoded_source, encoded_source.encode()))
    # Validate the complete RIFF and its true length before decoding; never
    # truncate a long response to make the quality/duration limits pass.
    maximum = 240 * 48000 if longform else whisper.NATURAL_SHORT_MAX_SAMPLES
    whisper._wav_layout(original, max_samples=maximum)
    with wave.open(BytesIO(original)) as wav:
        seconds = wav.getnframes() / wav.getframerate()
    original_sha = ledger.sha(original)
    prefix = 'provider_audio/kie/' + identity + '/' + original_sha
    with tempfile.TemporaryDirectory() as directory:
        source, output = Path(directory) / 'original.wav', Path(directory) / 'narration.mp3'
        source.write_bytes(original)
        subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-i', str(source),
            '-map', '0:a:0', '-vn', '-ac', '1', '-ar', '48000', '-c:a', 'libmp3lame',
            '-b:a', '192k', str(output)], check=True, timeout=45,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        audio = output.read_bytes()
    snapshot = whisper._snapshot_audio(audio, '.mp3', allow_natural_short=True,
        allow_commissioned_long=longform)
    ledger.require(abs(snapshot.samples / 48000 - seconds) < .10, 'kie_voice_conversion_duration_changed')
    original_key, mp3_key = prefix + '.wav', prefix + '-' + ledger.sha(audio) + '.mp3'
    for object_key, content, mime in ((original_key, original, 'audio/wav'), (mp3_key, audio, 'audio/mpeg')):
        _storage_call(s3.put_object, Bucket=settings.bucket, Key=object_key, Body=content, ContentType=mime)
        ledger.require(ledger.sha(_get(s3, object_key, MAX_BYTES)) == ledger.sha(content))
    record = {'version': 1, 'request_identity': identity,
        'result_receipt_sha256': row['result']['response_sha256'],
        'original_sha256': original_sha, 'original_key': original_key,
        'mp3_sha256': ledger.sha(audio), 'mp3_key': mp3_key,
        'audio': snapshot.descriptor({})['audio'], 'source_seconds': seconds,
        'conversion': 'complete_mono_48khz_mp3_192k_v1'}
    encoded = ledger.raw(record)
    ledger.require(client.set(key, encoded, nx=True) is True
        or client.get(key) in (encoded, encoded.encode()), 'kie_voice_media_record_conflict')
    return audio
