from io import BytesIO
import json
import subprocess
import wave

import httpx
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from app.services import kie_voice_media as media, kie_voice_ledger as ledger
from app.services import kie_gemini_voice as gemini, kie_voice_adapter as api
from app.services.production_spend import SpendBlocked
from test_kie_voice import kie, box, KEY
from test_kie_gemini_voice import setup, extension


@pytest.fixture
def clip(kie, monkeypatch):
    setup(kie); extension(kie)
    stream = BytesIO()
    with wave.open(stream, 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000)
        wav.writeframes(b'\x00\x00' * 48000)
    kie.wav = stream.getvalue()
    original = kie.handler
    def handler(request):
        if request.url.host == 'file.aiquickdraw.com':
            assert 'authorization' not in request.headers
            kie.requests.append(request)
            return httpx.Response(200, content=kie.wav)
        response = original(request)
        if request.url.path.endswith('/recordInfo'):
            payload = response.json()
            payload['data']['resultJson'] = json.dumps({'resultUrls': ['https://file.aiquickdraw.com/test.wav']})
            return httpx.Response(200, json=payload)
        return response
    kie.handler = handler
    body, ceiling = gemini.request_body('Bir fikrin değeri.', language='tr')
    journal = ledger.Journal(kie.foundation,
        {'kind': 'connection_probe', 'voice_id': 'Fenrir', 'language': 'tr'}, body, ceiling)
    kie.result = api.generate(body, KEY, journal, sleep=lambda _: None)
    kie.identity = journal.identity
    class Storage:
        def __init__(self): self.objects = {}
        def put_object(self, *, Bucket, Key, Body, ContentType): self.objects[Key] = Body
        def get_object(self, *, Bucket, Key):
            value = self.objects[Key]
            return {'ContentLength': len(value), 'Body': BytesIO(value)}
    kie.storage = Storage()
    monkeypatch.setattr(media.storage, '_client', lambda **kw: kie.storage)
    return kie


def test_real_wav_conversion_and_replay_preserve_audio_without_more_download_or_tts(clip):
    first = media.mp3(clip.client, clip.identity, clip.result, longform=False)
    assert media.mp3(clip.client, clip.identity, clip.result, longform=False) == first
    snapshot = media.whisper._snapshot_audio(first, '.mp3', allow_natural_short=True)
    assert snapshot.samples / 48000 == pytest.approx(2, abs=.10)
    assert [r.method for r in clip.requests].count('POST') == 1
    assert sum(r.url.host == 'file.aiquickdraw.com' for r in clip.requests) == 1
    assert clip.wav in clip.storage.objects.values()


def test_converter_failure_keeps_original_paid_media_for_recovery(clip, monkeypatch):
    real_run = media.subprocess.run
    def fail(*a, **kw): raise subprocess.CalledProcessError(1, 'ffmpeg')
    monkeypatch.setattr(media.subprocess, 'run', fail)
    with pytest.raises(subprocess.CalledProcessError):
        media.mp3(clip.client, clip.identity, clip.result, longform=False)
    assert clip.client.exists(ledger.PREFIX + 'media_source:' + clip.identity)
    monkeypatch.setattr(media.subprocess, 'run', real_run)
    assert media.mp3(clip.client, clip.identity, clip.result, longform=False)
    assert sum(r.url.host == 'file.aiquickdraw.com' for r in clip.requests) == 1
    assert [r.method for r in clip.requests].count('POST') == 1


def test_changed_stored_audio_is_rejected_without_any_new_paid_request(clip):
    media.mp3(clip.client, clip.identity, clip.result, longform=False)
    record = json.loads(clip.client.get(ledger.PREFIX + 'media:' + clip.identity))
    clip.storage.objects[record['mp3_key']] = b'changed'
    with pytest.raises(SpendBlocked):
        media.mp3(clip.client, clip.identity, clip.result, longform=False)
    assert [r.method for r in clip.requests].count('POST') == 1


@pytest.mark.parametrize('operation', ['put_object', 'get_object'])
def test_transient_storage_response_reuses_paid_audio_and_exact_object(clip, monkeypatch, operation):
    original = getattr(clip.storage, operation)
    calls = []
    sleeps = []
    journal_before = clip.client.get(ledger.JOURNAL_KEY)
    def unstable(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            if operation == 'put_object':
                original(**kwargs)  # The first write may already have succeeded.
            raise ClientError({'Error': {'Code': 'SlowDown'},
                'ResponseMetadata': {'HTTPStatusCode': 503}}, operation)
        return original(**kwargs)
    monkeypatch.setattr(clip.storage, operation, unstable)
    monkeypatch.setattr(media, '_sleep', sleeps.append)
    audio = media.mp3(clip.client, clip.identity, clip.result, longform=False)
    assert audio and calls[0] == calls[1] and sleeps == [.5]
    assert clip.client.get(ledger.JOURNAL_KEY) == journal_before
    assert [r.method for r in clip.requests].count('POST') == 1
    assert sum(r.url.host == 'file.aiquickdraw.com' for r in clip.requests) == 1
    assert media.mp3(clip.client, clip.identity, clip.result, longform=False) == audio


@pytest.mark.parametrize('status,code,expected', [
    (403, 'AccessDenied', 1), (404, 'NoSuchKey', 1), (503, 'SlowDown', 3),
])
def test_storage_retry_is_bounded_and_does_not_hide_permanent_errors(clip, monkeypatch, status, code, expected):
    calls = []
    journal_before = clip.client.get(ledger.JOURNAL_KEY)
    def unavailable(**kwargs):
        calls.append(kwargs)
        raise ClientError({'Error': {'Code': code}, 'ResponseMetadata': {'HTTPStatusCode': status}}, 'PutObject')
    monkeypatch.setattr(clip.storage, 'put_object', unavailable)
    monkeypatch.setattr(media, '_sleep', lambda _: None)
    with pytest.raises(ClientError):
        media.mp3(clip.client, clip.identity, clip.result, longform=False)
    assert len(calls) == expected and all(call == calls[0] for call in calls)
    assert clip.client.get(ledger.JOURNAL_KEY) == journal_before
    assert not clip.client.exists(ledger.PREFIX + 'media:' + clip.identity)
    assert [r.method for r in clip.requests].count('POST') == 1


def test_connection_error_retries_only_object_read(monkeypatch):
    calls = []
    def read():
        calls.append(True)
        if len(calls) < 3:
            raise EndpointConnectionError(endpoint_url='https://storage.invalid')
        return b'existing-object'
    monkeypatch.setattr(media, '_sleep', lambda _: None)
    assert media._storage_call(read) == b'existing-object'
    assert len(calls) == 3
