"""Recognition defects reuse the original take and accounted blind evidence."""
from contextlib import nullcontext
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

from app.services import commissioning_audio as whisper, commissioning_scribe as scribe
from app.services import kie_voice_production as voice, audio_qc
from app.services.production_spend import SpendBlocked
from app.services.word_timed_narration import edit_plan
from test_commissioning_scribe import box, activate, payload
from test_whisper_transcription import _payload


@pytest.mark.parametrize('defect', ['zero_width', 'spelling'])
def test_captured_primary_defect_uses_one_blind_second_recognizer_on_same_audio(box, monkeypatch, defect):
    activate(box); whisper.commission(box.client, box.policy)
    first = _payload()
    if defect == 'zero_width':
        first['words'][-1]['end'] = first['words'][-1]['start']
    else:
        first['text'] = 'Hello their.'; first['words'][-1]['word'] = 'their.'
    box.sender.return_value = nullcontext(httpx.Response(200, json=first))
    sender = Mock(return_value=json.dumps(payload()).encode())
    monkeypatch.setattr(scribe, '_send', sender)
    result = voice._timing_evidence(box.path, 'Hello there.', language='en')
    assert result['provider'] == 'elevenlabs'
    assert result['text'] == 'Hello there.'
    assert edit_plan(['Hello there.'], result, 1, language='en')['scene_durations'] == [1]
    original = box.client.get(whisper.JOURNAL_KEY)
    secondary = {k: box.client.get(k) for k in box.client.keys(scribe.PREFIX+'*')}
    assert voice._timing_evidence(box.path, 'Hello there.', language='en') == result
    assert box.sender.call_count == sender.call_count == 1
    assert box.client.get(whisper.JOURNAL_KEY) == original
    assert secondary == {k: box.client.get(k) for k in box.client.keys(scribe.PREFIX+'*')}
    assert sender.call_args.args[0].raw == box.path.read_bytes()
    assert not any(k in sender.call_args.args[1] for k in ('prompt', 'text', 'keyterms'))


@pytest.mark.parametrize('failure', ['unknown', 'bad_timing'])
def test_failed_independent_recognition_cannot_purchase_another_voice_take(box, monkeypatch, failure):
    activate(box); whisper.commission(box.client, box.policy)
    first = _payload(); first['words'][-1]['end'] = first['words'][-1]['start']
    box.sender.return_value = nullcontext(httpx.Response(200, json=first))
    bad = payload(); bad['words'][-1]['end'] = bad['words'][-1]['start']
    sender = Mock(side_effect=httpx.ReadTimeout('unknown') if failure == 'unknown' else None,
                  return_value=json.dumps(bad).encode())
    monkeypatch.setattr(scribe, '_send', sender)
    for _ in range(2):
        with pytest.raises(SpendBlocked, match='timing_outcome_unverified'):
            voice._timing_evidence(box.path, 'Hello there.', language='en')
    assert box.sender.call_count == sender.call_count == 1


def test_valid_primary_timing_does_not_buy_secondary_recognition(box, monkeypatch):
    activate(box); whisper.commission(box.client, box.policy)
    box.sender.return_value = nullcontext(httpx.Response(200, json=_payload()))
    sender = Mock(side_effect=AssertionError('secondary must not run'))
    monkeypatch.setattr(scribe, '_send', sender)
    result = voice._timing_evidence(box.path, 'Hello there.', language='en')
    assert result['provider'] == 'openai'
    assert edit_plan(['Hello there.'], result, 1, language='en')['audio_edited'] is False
    sender.assert_not_called()


def test_observed_speech_defect_still_rejects_the_take(box, monkeypatch):
    activate(box); whisper.commission(box.client, box.policy)
    first = _payload(); first['text'] = 'Hello their.'; first['words'][-1]['word'] = 'their.'
    box.sender.return_value = nullcontext(httpx.Response(200, json=first))
    monkeypatch.setattr(scribe, '_send', Mock(return_value=json.dumps(payload('Hello their.')).encode()))
    result = voice._timing_evidence(box.path, 'Hello there.', language='en')
    from app.services.voice import VoiceQualityError
    with pytest.raises(VoiceQualityError):
        edit_plan(['Hello there.'], result, 1, language='en')


def test_actual_episode4_zero_width_timestamps_are_not_filled_in_or_approved(monkeypatch, tmp_path):
    captured = json.loads((Path(__file__).parent/'fixtures/framecase_episode4_kie_timing.json').read_text())
    original = deepcopy(captured)
    with pytest.raises(audio_qc.AudioQCError, match='incomplete word timestamps'):
        edit_plan(captured['scenes'], captured['evidence'], captured['seconds'], language='en')
    review = audio_qc.compare_transcript(' '.join(captured['scenes']), captured['evidence']['text'],
        words=captured['evidence']['words'], provider='openai', comparison_language='en')
    assert review['pass'] is True  # Exact text alone is insufficient.
    monkeypatch.setattr(audio_qc, 'verify_audio_narration', Mock(return_value=review))
    with pytest.raises(audio_qc.AudioQCError, match='incomplete word timestamps'):
        voice._timing_evidence(tmp_path/'unchanged.mp3', ' '.join(captured['scenes']), language='en')
    assert captured == original


def test_synthesis_keeps_accepted_media_and_returns_real_recognizer_provider(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.services import production_spend_runtime as runtime, kie_voice_adapter as api, kie_voice_media
    context = {'kind': 'shorts', 'lineage_id': 'original-root'}
    foundation = SimpleNamespace(client=object())
    choice = {'context': context, 'language': 'en', 'voice_id': 'Kore'}
    monkeypatch.setattr(runtime, 'configured_ledger', lambda **kw: foundation)
    monkeypatch.setattr(runtime, 'resolve_context', lambda *args: context)
    monkeypatch.setattr(voice.ledger, 'Journal', lambda *args, **kw: SimpleNamespace(identity='a'*64))
    monkeypatch.setattr(voice.ledger.credentials, 'read', lambda client: SimpleNamespace(api_key='not-live'))
    accepted = {'task_id': 'original-accepted-task'}
    monkeypatch.setattr(api, 'generate', Mock(return_value=accepted))
    media = Mock(return_value=b'original-retained-audio'); monkeypatch.setattr(kie_voice_media, 'mp3', media)
    timing = {'text': 'Hello there.', 'words': payload()['words'], 'provider': 'elevenlabs', 'language': 'en'}
    recognizer = Mock(return_value=timing); monkeypatch.setattr(voice, '_timing_evidence', recognizer)
    audio, result = voice.synthesize('Hello there.', choice, attempt=1, work=tmp_path)
    assert audio == b'original-retained-audio' and result == timing
    assert (tmp_path/'kie_original.mp3').read_bytes() == audio
    media.assert_called_once_with(foundation.client, 'a'*64, accepted, longform=False)
    recognizer.assert_called_once_with(tmp_path/'kie_original.mp3', 'Hello there.', language='en')
