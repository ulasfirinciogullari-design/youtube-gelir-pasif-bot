"""JSON Object audio retains the exact audio/rubric and all local rejection gates."""
import base64
from copy import deepcopy
import json
import pytest

from app.services import abacus_router_audio_adapter as adapter
from app.services import audio_qc
from test_abacus_router_audio_adapter import (
    mp3, KEY, EXPECTED, ASR, PROSODY, envelope,
)
from test_abacus_router_native_finish_reason import exchange, no_network


@pytest.fixture(scope='module', params=['asr', 'prosody'])
def example(request):
    if request.param == 'asr':
        prepared = adapter.prepare_json_object_blind_asr_request(mp3(), api_key=KEY)
        return prepared, ASR
    prepared = adapter.prepare_json_object_audio_prosody_request(mp3(), api_key=KEY,
        expected_narration=EXPECTED, system_instruction=audio_qc._PROSODY_SYSTEM_INSTRUCTION,
        json_schema=audio_qc._PROSODY_REVIEW_SCHEMA)
    return prepared, PROSODY


def test_audio_bytes_full_schema_and_rubric_survive_explicit_envelope(example):
    prepared, result = example
    body = prepared.payload
    assert body['response_format'] == {'type': 'json_object'}
    assert body['temperature'] == 0 and 'modalities' not in body and body['model'] == 'route-llm'
    assert base64.b64decode(body['messages'][1]['content'][0]['input_audio']['data']) == mp3()
    schema = adapter.schema_for_request(body, prepared.purpose)
    if prepared.purpose is adapter.AudioReviewPurpose.BLIND_ASR:
        assert schema == adapter._ASR_SCHEMA
        assert body['messages'][0]['content'] == adapter._ASR_SYSTEM
        assert EXPECTED not in json.dumps(body)
    else:
        assert schema == audio_qc._PROSODY_REVIEW_SCHEMA
        assert body['messages'][0]['content'] == audio_qc._PROSODY_SYSTEM_INSTRUCTION
    payload = envelope(deepcopy(result))
    payload['choices'][0]['native_finish_reason'] = 'stop'
    payload['usage'] = {'input_tokens': 46, 'output_tokens': 33, 'raw_input_tokens': 46, 'reasoning_tokens': 19}
    observed = adapter.observe_audio_router_response(prepared, exchange(prepared, payload))
    assert observed.result == result and observed.usage == payload['usage']


@pytest.mark.parametrize('fault', ['missing_field', 'extra_field', 'wrong_type', 'wrong_wire'])
def test_json_object_does_not_weaken_schema_or_transport_identity(example, fault):
    prepared, result = example
    output = deepcopy(result)
    first = next(iter(output))
    if fault == 'missing_field': output.pop(first)
    if fault == 'extra_field': output['unapproved'] = True
    if fault == 'wrong_type': output[first] = None
    received = exchange(prepared, envelope(output), changed_wire=fault == 'wrong_wire')
    with pytest.raises(adapter.AbacusRouterAudioError):
        adapter.observe_audio_router_response(prepared, received)


@pytest.mark.parametrize('fault', ['drop_schema', 'change_audio', 'add_modalities', 'boolean_temperature'])
def test_only_complete_distinct_json_object_request_is_accepted(example, fault):
    prepared, _ = example
    kwargs = prepared.wire_kwargs()
    body = kwargs['json']
    if fault == 'drop_schema': body['messages'][1]['content'].pop()
    if fault == 'change_audio': body['messages'][1]['content'][0]['input_audio']['data'] = 'broken'
    if fault == 'add_modalities': body['modalities'] = ['text']
    if fault == 'boolean_temperature': body['temperature'] = False
    with pytest.raises(adapter.AbacusRouterAudioError):
        adapter.inspect_audio_router_request(adapter.ENDPOINT, kwargs, purpose=prepared.purpose)


def test_json_object_audio_keeps_actual_word_timing_gate():
    prepared = adapter.prepare_json_object_blind_asr_request(mp3(), api_key=KEY)
    output = deepcopy(ASR)
    output['words'][-1]['end'] = 1.5
    with pytest.raises(adapter.AbacusRouterAudioError, match='abacus_router_audio_timing_unverified'):
        adapter.observe_audio_router_response(prepared, exchange(prepared, envelope(output)))
