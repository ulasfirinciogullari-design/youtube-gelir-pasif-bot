import json

import pytest

from app.services import abacus_router_audio_adapter as adapter, audio_qc
from app.services import production_included_router as included
from app.services.production_spend import SpendBlocked
from test_abacus_router_audio_adapter import mp3, KEY, response, envelope, PROSODY
from test_production_included_router import commissioned, client, CONTEXT


@pytest.mark.parametrize('language,text,word', [('tr', 'Merhaba.', 'Merhaba.'), ('en', 'Hello.', 'Hello.')])
def test_included_complete_audio_can_be_observed_and_reused_in_native_ledger(commissioned, language, text, word):
    ledger, _, _ = commissioned
    from test_abacus_router_adapter import KEY as LEDGER_KEY
    prepared = adapter.prepare_included_blind_asr_request(mp3(), api_key=LEDGER_KEY, language=language)
    assert prepared.language == language
    assert 'UNTRUSTED_EXPECTED_NARRATION' not in json.dumps(prepared.payload)
    value = {'text': text, 'language': language, 'words': [{'word': word, 'start': .05, 'end': .8}]}
    observed = adapter.observe_audio_router_response(prepared, response(prepared, payload=envelope(value)))
    identity, _ = ledger.reserve(CONTEXT, 'blind_asr', prepared)
    outcome = ledger.settle(identity, prepared, observed)
    assert included._result(prepared, outcome) == value
    assert ledger.reserve(CONTEXT, 'blind_asr', prepared)[1] == outcome
    assert ledger.foundation.snapshot()['historical_cash_micro'] is None


def test_english_support_does_not_expand_original_retained_request_builder():
    with pytest.raises(SpendBlocked): adapter.prepare_blind_asr_request(mp3(), api_key=KEY, language='en')
    prepared = adapter.prepare_included_prosody_request(mp3(), api_key=KEY, language='en',
        expected_narration='Hello.', system_instruction=audio_qc._PROSODY_SYSTEM_INSTRUCTION,
        json_schema=audio_qc._PROSODY_REVIEW_SCHEMA)
    assert prepared.language == 'en'
    assert adapter.observe_audio_router_response(prepared, response(prepared, payload=envelope(PROSODY))).result['pass'] is False


def test_runtime_blind_asr_forbids_expected_narration_before_transport(monkeypatch):
    monkeypatch.setattr(included, '_generate', lambda *a: pytest.fail('No transport allowed'))
    with pytest.raises(SpendBlocked, match='text_forbidden'):
        included.generate_included_audio(mp3(), purpose='blind_asr', language='en', expected_narration='Hello.')
