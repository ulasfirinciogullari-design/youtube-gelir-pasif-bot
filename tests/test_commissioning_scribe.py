"""A second blind recognizer has actual persistent admission and no replay."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from threading import Event
from unittest.mock import Mock

import httpx
import pytest

from app.services import commissioning_scribe as scribe, production_continuation as continuation
from app.services import audio_qc, commissioning_audio as whisper_setup, production_spend_runtime as runtime
from app.services.production_spend import SpendBlocked
from test_commissioning_audio import box
from test_whisper_transcription import _payload, NOW, ROOT, CHILD, CHANNEL, KEY


def activate(box):
    continuation.initialize(box.client, {'version': 1, 'kind': 'continuous_commissioning',
        'allowed_channels': [CHANNEL], 'authorized_at': NOW.isoformat(), 'owner_evidence_sha256': 'c' * 64})
    box.config.elevenlabs_api_key = KEY


def payload(text='Hello there.'):
    tokens = text.strip('.').split()
    return {'text': text, 'language_code': 'en', 'language_probability': 1,
        'words': [{'text': word, 'type': 'word', 'start': i * .2, 'end': (i+1)*.2}
                  for i, word in enumerate(tokens)]}


def run(box):
    return scribe.transcribe_if_commissioned(box.path, api_key=KEY, language='en')


def test_uncommissioned_does_not_send_or_initialize(box, monkeypatch):
    sender = Mock(); monkeypatch.setattr(scribe, '_send', sender)
    assert run(box) is None
    assert not box.client.keys(scribe.PREFIX + '*')
    sender.assert_not_called()


def test_same_audio_reuses_observed_blind_response_including_child_lineage(box, monkeypatch):
    activate(box)
    def send(snapshot, fields, api_key):
        assert fields == {'model_id': 'scribe_v2', 'language_code': 'eng', 'num_speakers': '1',
            'diarize': 'false', 'tag_audio_events': 'false', 'timestamps_granularity': 'word'}
        assert snapshot.raw == box.path.read_bytes() and api_key == KEY
        rows = box.client.keys(scribe.PREFIX + '*')
        assert len(rows) == 1 and json.loads(box.client.get(rows[0]))['outcome'] is None
        return json.dumps(payload()).encode()
    sender = Mock(side_effect=send); monkeypatch.setattr(scribe, '_send', sender)
    assert run(box).json() == payload()
    assert run(box).json() == payload()
    token = runtime._TASK_ID.set(CHILD)
    try: assert run(box).json() == payload()
    finally: runtime._TASK_ID.reset(token)
    assert sender.call_count == 1
    row = box.client.get(box.client.keys(scribe.PREFIX + '*')[0])
    assert KEY not in row and 'Hello there' not in row
    assert json.loads(row)['max_list_cost_micro_usd'] == 4000


def test_unknown_response_remains_counted_and_never_reposts(box, monkeypatch):
    activate(box); sender = Mock(side_effect=httpx.ReadTimeout('uncertain'))
    monkeypatch.setattr(scribe, '_send', sender)
    with pytest.raises(SpendBlocked, match='outcome_unverified'): run(box)
    with pytest.raises(SpendBlocked, match='scribe_unverified'): run(box)
    assert sender.call_count == 1 and len(box.client.keys(scribe.PREFIX + '*')) == 1


def test_concurrent_recognizers_send_same_audio_once(box, monkeypatch):
    activate(box); entered, finish = Event(), Event()
    def send(*args):
        entered.set(); assert finish.wait(5)
        return json.dumps(payload()).encode()
    sender = Mock(side_effect=send); monkeypatch.setattr(scribe, '_send', sender)
    def worker():
        token = runtime._TASK_ID.set(ROOT)
        try: return run(box)
        finally: runtime._TASK_ID.reset(token)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(worker); assert entered.wait(5)
        try:
            with pytest.raises(SpendBlocked): pool.submit(worker).result(timeout=5)
        finally: finish.set()
        assert first.result().json() == payload()
    assert sender.call_count == 1


@pytest.mark.parametrize('secondary', ['exact', 'different', 'missing_timing', 'unknown'])
def test_secondary_needs_exact_transcript_and_its_own_real_timings(box, monkeypatch, secondary):
    activate(box); whisper_setup.commission(box.client, box.policy)
    sender = Mock(side_effect=httpx.ReadTimeout('unknown') if secondary == 'unknown' else None)
    result = payload('Hello their.' if secondary == 'different' else 'Hello there.')
    if secondary == 'missing_timing': result['words'] = []
    sender.return_value = json.dumps(result).encode()
    monkeypatch.setattr(scribe, '_send', sender)
    first = _payload(); first['text'] = 'Hello their.'; first['words'][-1]['word'] = 'their.'
    from contextlib import nullcontext
    box.sender.return_value = nullcontext(httpx.Response(200, json=first))
    review = audio_qc.verify_audio_narration(box.path, 'Hello there.', language='en')
    assert review['pass'] is (secondary == 'exact')
    if secondary == 'exact':
        assert review['provider'] == 'elevenlabs' and review['primary_recognizer']['pass'] is False
        assert review['word_timestamps'][-1]['text'] == 'there' and review['ending_word_time'] == .4
    assert sender.call_count == box.sender.call_count == 1
    before = {k: box.client.get(k) for k in box.client.keys(scribe.PREFIX+'*')}
    audio_qc.verify_audio_narration(box.path, 'Hello there.', language='en')
    assert sender.call_count == box.sender.call_count == 1
    assert before == {k: box.client.get(k) for k in box.client.keys(scribe.PREFIX+'*')}


def test_native_english_pace_keeps_turkish_and_long_form_defaults():
    from app.services.voice import _voice_speed
    assert _voice_speed(30, language='en') == 1.1
    assert _voice_speed(30, language='tr') == 1.0
    assert _voice_speed(30) == 1.0
    assert _voice_speed(60, language='en') == 1.01


@pytest.mark.parametrize('first_failure', ['transport_unknown', 'missing_timing'])
def test_second_recognizer_can_verify_when_first_provider_unavailable_without_replaying_it(box, monkeypatch, first_failure):
    activate(box); whisper_setup.commission(box.client, box.policy)
    from contextlib import nullcontext
    if first_failure == 'transport_unknown':
        box.sender.side_effect = httpx.ReadTimeout('do not disclose credentials or repeat the request')
    else:
        first = _payload(); first['words'] = []
        box.sender.return_value = nullcontext(httpx.Response(200, json=first))
    sender = Mock(return_value=json.dumps(payload()).encode())
    monkeypatch.setattr(scribe, '_send', sender)
    review = audio_qc.verify_audio_narration(box.path, 'Hello there.', language='en')
    assert review['pass'] is True and review['primary_recognizer']['pass'] is False
    before = box.client.get(whisper_setup.JOURNAL_KEY)
    assert audio_qc.verify_audio_narration(box.path, 'Hello there.', language='en')['pass'] is True
    assert box.client.get(whisper_setup.JOURNAL_KEY) == before
    assert sender.call_count == box.sender.call_count == 1


@pytest.mark.parametrize('case', ['valid', 'redirect', 'oversized', 'invalid_json', 'missing_words', 'key_echo'])
def test_real_transport_is_bounded_and_does_not_follow_redirects(box, monkeypatch, case):
    raw = json.dumps(payload()).encode()
    if case == 'oversized': raw = b'x' * (2 * 1024 * 1024 + 1)
    if case == 'invalid_json': raw = b'not json'
    if case == 'missing_words': raw = b'{"text":"hello"}'
    if case == 'key_echo': raw = json.dumps({**payload(), 'text': KEY}).encode()
    requests = []
    def handler(request):
        requests.append(request)
        assert request.method == 'POST' and str(request.url) == scribe.ROUTE
        assert request.headers['xi-api-key'] == KEY
        return httpx.Response(302 if case == 'redirect' else 200, content=raw,
            headers={'location': 'https://unrelated.invalid/'})
    client = httpx.Client
    def bounded_client(**kwargs):
        assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
        return client(**kwargs, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(scribe.httpx, 'Client', bounded_client)
    data, suffix = scribe.audio._read_audio(box.path)
    snapshot = scribe.audio._snapshot_audio(data, suffix)
    if case == 'valid':
        assert scribe._send(snapshot, {'model_id': 'scribe_v2'}, KEY) == raw
    else:
        with pytest.raises((SpendBlocked, json.JSONDecodeError)):
            scribe._send(snapshot, {'model_id': 'scribe_v2'}, KEY)
    assert len(requests) == 1
