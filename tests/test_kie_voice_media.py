from io import BytesIO
import json
import subprocess
import wave

import httpx
import pytest

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
