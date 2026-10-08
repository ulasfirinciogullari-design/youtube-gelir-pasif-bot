"""Decode the complete longer take and retain real provider admission/replay fences."""
from copy import deepcopy
import hashlib
import json
from unittest.mock import Mock

import pytest

from app.services import whisper_transcription as audio, commissioning_audio as whisper
from app.services import commissioning_scribe as scribe, abacus_router_audio_adapter as adapter
from app.services import audio_qc
from app.services.production_spend import SpendBlocked
from test_commissioning_audio import box, KEY
from test_commissioning_scribe import activate, payload
from test_whisper_transcription import _wav, _mp3


@pytest.mark.parametrize('seconds', [30.84, 36.108, 39.45])
@pytest.mark.parametrize('suffix', ['.wav', '.mp3'])
def test_natural_take_decodes_completely_but_legacy_scope_stays_fixed(seconds, suffix):
    raw = _wav(round(seconds * 48000)) if suffix == '.wav' else _mp3(seconds)
    with pytest.raises(SpendBlocked): audio._snapshot_audio(raw, suffix)
    sample = audio._snapshot_audio(raw, suffix, allow_natural_short=True)
    assert sample.raw == raw
    # The generated MP3 includes complete encoder-delay/padding frames; the
    # PCM input has an exact sample count. Neither is truncated to 30 seconds.
    assert sample.samples / 48000 == pytest.approx(seconds, abs=.06 if suffix == '.mp3' else 1 / 48000)
    assert sample.descriptor({})['audio']['sha256'] == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize('suffix', ['.wav', '.mp3'])
@pytest.mark.parametrize('seconds', [40.15, 41.5, 48])
def test_overlong_input_is_rejected_without_truncating_to_an_accepted_clip(suffix, seconds):
    raw = _wav(round(seconds * 48000)) if suffix == '.wav' else _mp3(seconds)
    with pytest.raises(SpendBlocked): audio._snapshot_audio(raw, suffix, allow_natural_short=True)


def test_existing_audio_descriptor_is_identical_in_both_scopes():
    raw = _mp3(1)
    assert audio._snapshot_audio(raw, '.mp3').descriptor({}) == audio._snapshot_audio(
        raw, '.mp3', allow_natural_short=True).descriptor({})


@pytest.mark.parametrize('provider', ['whisper', 'scribe'])
def test_commissioned_longer_take_has_one_reserved_minute_and_no_second_purchase(box, monkeypatch, provider):
    box.path.write_bytes(_wav(round(36.108 * 48000)))
    if provider == 'whisper':
        whisper.commission(box.client, box.policy)
        call = lambda: whisper.transcribe_if_commissioned(box.path, api_key=KEY, language='en')
        sender = box.sender
    else:
        activate(box)
        sender = Mock(return_value=json.dumps(payload()).encode())
        monkeypatch.setattr(scribe, '_send', sender)
        call = lambda: scribe.transcribe_if_commissioned(box.path, api_key=KEY, language='en')
    assert call().status_code == call().status_code == 200
    assert sender.call_count == 1
    if provider == 'whisper':
        record = next(iter(json.loads(box.client.get(whisper.JOURNAL_KEY))['requests'].values()))
    else:
        record = json.loads(box.client.get(box.client.keys(scribe.PREFIX + '*')[0]))
    assert record['request']['audio']['decoded_samples'] == round(36.108 * 48000)
    assert record['max_list_cost_micro_usd'] == (6000 if provider == 'whisper' else 4000)
    assert record['outcome'] is not None


def test_prepaid_prosody_accepts_original_long_take_and_preserves_rubric():
    raw = _mp3(36.108)
    schema = deepcopy(audio_qc._PROSODY_REVIEW_SCHEMA)
    prepared = adapter.prepare_prepaid_prosody_request(raw, api_key=KEY, language='en',
        expected_narration='The original narration.', system_instruction='Listen independently.', json_schema=schema)
    descriptor = prepared.audio
    assert descriptor['decoded_samples'] / 48000 == pytest.approx(36.108, abs=.06)
    assert descriptor['sha256'] == hashlib.sha256(raw).hexdigest()
    assert adapter.schema_for_request(prepared.payload, prepared.purpose) == schema
    with pytest.raises(SpendBlocked): adapter.prepare_prepaid_blind_asr_request(raw, api_key=KEY, language='en')
    with pytest.raises(SpendBlocked): adapter.prepare_included_prosody_request(raw, api_key=KEY, language='en',
        expected_narration='The original narration.', system_instruction='Listen independently.', json_schema=schema)


def test_full_live_prosody_entry_reads_the_unmodified_longer_audio(box, monkeypatch):
    from app.services import production_included_router as included
    path = box.path.with_suffix('.mp3'); raw = _mp3(36.108); path.write_bytes(raw)
    sender = Mock(return_value={'observed': True})
    monkeypatch.setattr(included, 'generate_included_audio', sender)
    validator = Mock(return_value={'available': True, 'pass': True})
    monkeypatch.setattr(audio_qc, '_validate_prosody_review', validator)
    assert audio_qc.verify_audio_prosody(path, 'The original narration.', language='en',
        audio_duration_seconds=36.108)['pass'] is True
    sender.assert_called_once_with(raw, purpose='prosody', language='en', expected_narration='The original narration.')
    assert validator.call_args.kwargs['audio_duration_seconds'] == 36.108
