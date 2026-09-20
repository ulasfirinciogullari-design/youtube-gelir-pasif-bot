"""Exercise the production sender with actual bounded HTTPX request/response bytes."""
from copy import deepcopy
import base64
import json

import httpx
import pytest

from app.services import abacus_router_audio_adapter as audio, audio_qc
from app.services import production_included_router as included, production_spend_runtime as runtime
from app.services import production_included_transport as transport
from app.services.production_spend import SpendBlocked
from test_production_included_router import commissioned, client, CONTEXT
from test_abacus_router_adapter import KEY
from test_abacus_router_audio_adapter import mp3, ASR, PROSODY, envelope, response


@pytest.mark.parametrize('language', ['tr', 'en'])
def test_explicit_prompt_contract_preserves_original_audio_blindness_and_all_validation(language):
    before = audio.prepare_included_blind_asr_request(mp3(), api_key=KEY, language=language)
    after = audio.prepare_prompt_json_blind_asr_request(mp3(), api_key=KEY, language=language)
    assert 'response_format' not in after.payload and after.request_sha256 != before.request_sha256
    assert after.audio == before.audio and after.language == language
    assert after.payload['messages'][:1] == before.payload['messages'][:1]
    assert after.payload['messages'][1]['content'][:2] == before.payload['messages'][1]['content'][:2]
    assert audio.schema_for_request(after.payload, after.purpose) == audio.schema_for_request(before.payload, before.purpose)
    assert 'UNTRUSTED_EXPECTED_NARRATION' not in json.dumps(after.payload)
    # Removing a wire option from the old request alone is not this contract.
    bad = before.wire_kwargs(); del bad['json']['response_format']
    with pytest.raises(SpendBlocked): audio.inspect_audio_router_request(after.endpoint, bad, purpose=after.purpose)
    value = ASR if language == 'tr' else {'text': 'Hello.', 'language': 'en', 'words': [{'word':'Hello.','start':.05,'end':.9}]}
    assert audio.observe_audio_router_response(after, response(after, payload=envelope(value))).result == value
    bad = deepcopy(value); bad['words'][0]['end'] = 31
    with pytest.raises(SpendBlocked): audio.observe_audio_router_response(after, response(after, payload=envelope(bad)))


def test_prompt_prosody_still_preserves_failed_verdict_original_rubric_and_schema():
    prepared = audio.prepare_prompt_json_prosody_request(mp3(), api_key=KEY, language='tr',
        expected_narration='Merhaba dünya.', system_instruction=audio_qc._PROSODY_SYSTEM_INSTRUCTION,
        json_schema=audio_qc._PROSODY_REVIEW_SCHEMA)
    assert prepared.payload['messages'][0]['content'] == audio_qc._PROSODY_SYSTEM_INSTRUCTION
    assert audio.schema_for_request(prepared.payload, prepared.purpose) == audio_qc._PROSODY_REVIEW_SCHEMA
    assert audio.observe_audio_router_response(prepared, response(prepared, payload=envelope(PROSODY))).result['pass'] is False


@pytest.fixture
def live(commissioned, monkeypatch):
    ledger, _, _ = commissioned
    for name, value in {'studio_spend_enforcement': True, 'studio_abacus_included_production': True,
                        'abacus_api_key': KEY}.items():
        monkeypatch.setattr(runtime.settings, name, value)
    monkeypatch.setattr(runtime, 'configured_ledger', lambda **kw: ledger.foundation)
    monkeypatch.setattr(runtime, 'resolve_context', lambda *a: deepcopy(CONTEXT))
    return ledger


def mock_wire(monkeypatch, handler):
    calls = []
    def wire(request):
        calls.append(request)
        assert request.method == 'POST' and str(request.url) == audio.ENDPOINT
        assert request.headers['authorization'] == 'Bearer ' + KEY
        assert request.headers['accept-encoding'] == 'identity'
        assert 'cookie' not in request.headers
        return handler(request)
    def pool(**kwargs):
        assert kwargs == {'retries': 0, 'trust_env': False}
        return httpx.MockTransport(wire)
    monkeypatch.setattr(transport.httpx, 'HTTPTransport', pool)
    return calls


def test_real_sender_once_then_reuses_observed_audio_from_durable_cache(live, monkeypatch):
    body = json.dumps(envelope()).encode()
    calls = mock_wire(monkeypatch, lambda req: httpx.Response(200,
        headers={'content-type':'application/json'}, stream=httpx.ByteStream(body)))
    for _ in range(2):
        assert included.generate_included_audio(mp3(), purpose='blind_asr', language='tr') == ASR
    assert len(calls) == 1
    sent = json.loads(calls[0].content)
    assert 'response_format' not in sent and sent['model'] == 'route-llm'
    assert base64.b64decode(sent['messages'][1]['content'][0]['input_audio']['data']) == mp3()
    assert live.foundation.snapshot()['cash_spending_enabled'] is False


@pytest.mark.parametrize('status', [400, 401, 429, 500, 302])
def test_http_failure_records_evidence_without_refund_fallback_or_second_send(live, monkeypatch, status):
    body = json.dumps({'error':{'code':'unsupported_parameter','param':'response_format',
        'message':'This fixture is not a production provider response.'}}).encode()
    calls = mock_wire(monkeypatch, lambda req: httpx.Response(status,
        headers={'content-type':'application/json','location':'https://untrusted.example/'}, stream=httpx.ByteStream(body)))
    with pytest.raises(SpendBlocked, match='response_unverified'):
        included.generate_included_audio(mp3(), purpose='blind_asr', language='tr')
    with pytest.raises(SpendBlocked, match='previous_outcome_unknown'):
        included.generate_included_audio(mp3(), purpose='blind_asr', language='tr')
    assert len(calls) == 1
    journal = json.loads(live.client.get(included.JOURNAL_KEY))
    identity, row = next(iter(journal['requests'].items()))
    assert row['outcome'] is None
    key = included.PREFIX + 'failure:' + identity
    failure = json.loads(live.client.get(key))
    assert live.client.pttl(key) == -1 and failure['http_status'] == status
    assert failure['retry_allowed'] is False and included._cipher().decrypt(failure['encrypted_response'].encode()) == body
    assert KEY not in json.dumps(failure)


def test_credential_echo_is_not_persisted_even_encrypted(live, monkeypatch):
    calls = mock_wire(monkeypatch, lambda req: httpx.Response(400, stream=httpx.ByteStream(KEY.encode())))
    with pytest.raises(SpendBlocked):included.generate_included_audio(mp3(), purpose='blind_asr', language='tr')
    keys=list(live.client.scan_iter(match=included.PREFIX+'failure:*'))
    assert len(keys)==1 and json.loads(live.client.get(keys[0]))['encrypted_response'] is None
    assert len(calls)==1


@pytest.mark.parametrize('damage', ['oversized','compressed'])
def test_unbounded_or_encoded_failure_body_is_never_consumed_or_retried(live, monkeypatch, damage):
    calls=mock_wire(monkeypatch, lambda req:httpx.Response(400,
        headers={'content-length':str(transport.MAX_ERROR_BYTES+1)}if damage=='oversized'else {'content-encoding':'gzip'},
        stream=httpx.ByteStream(b'not read')))
    with pytest.raises(SpendBlocked):included.generate_included_audio(mp3(), purpose='blind_asr', language='tr')
    with pytest.raises(SpendBlocked, match='previous_outcome_unknown'):
        included.generate_included_audio(mp3(), purpose='blind_asr', language='tr')
    assert len(calls)==1
