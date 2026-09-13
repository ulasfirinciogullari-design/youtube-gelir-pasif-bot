"""The same exact native STOP rule applies to both frozen audio purposes."""
from copy import deepcopy
import json

import pytest

from app.services import abacus_router_audio_adapter as adapter
import test_abacus_router_audio_adapter as fixture
from test_abacus_router_native_finish_reason import (
    no_network, exchange, assert_metadata_only, damage_payload, DAMAGES,
)


@pytest.fixture(scope='module', params=['asr', 'prosody'])
def prepared_case(request):
    if request.param == 'asr':
        return fixture.prepare(), fixture.ASR
    return fixture.prosody(), fixture.PROSODY


@pytest.mark.parametrize('omit_identity', [False, True])
def test_audio_native_stop_changes_only_raw_response_commitments(prepared_case, omit_identity):
    prepared, result = prepared_case
    payload = fixture.envelope(deepcopy(result))
    if omit_identity:
        payload.pop('id'); payload.pop('object')
    before = adapter.observe_audio_router_response(prepared, exchange(prepared, payload))
    payload['choices'][0]['native_finish_reason'] = 'STOP'
    response = exchange(prepared, payload)
    after = adapter.observe_audio_router_response(prepared, response)
    assert_metadata_only(before, after, response)
    assert after.result == result
    payload['choices'][0].pop('native_finish_reason')
    assert adapter.observe_audio_router_response(prepared, exchange(prepared, payload)) == before


@pytest.mark.parametrize('value', [None, True, 0, [], {}, 'stop', 'STOP ', 'STOP\n',
                                  'END_TURN', 'tool_calls', fixture.KEY])
def test_audio_present_native_reason_cannot_be_null_coerced_or_another_enum(prepared_case, value):
    prepared, result = prepared_case
    payload = fixture.envelope(deepcopy(result))
    payload['choices'][0]['native_finish_reason'] = value
    with pytest.raises(adapter.AbacusRouterAudioError, match='^abacus_router_audio_response_unverified$'):
        adapter.observe_audio_router_response(prepared, exchange(prepared, payload))


@pytest.mark.parametrize('damage', DAMAGES)
def test_audio_native_stop_preserves_all_previous_rejection_gates(prepared_case, damage):
    prepared, result = prepared_case
    payload = fixture.envelope(deepcopy(result))
    payload['choices'][0]['native_finish_reason'] = 'STOP'
    damage_payload(payload, damage)
    raw = (json.dumps(payload).replace('"native_finish_reason": "STOP"',
        '"native_finish_reason": "STOP", "native_finish_reason": "STOP"').encode()
        if damage == 'duplicate_native' else None)
    response = exchange(prepared, payload, status=403 if damage == 'non2xx' else 200,
                        raw=raw, changed_wire=damage == 'wire')
    with pytest.raises(adapter.AbacusRouterAudioError):
        adapter.observe_audio_router_response(prepared, response)


def test_native_stop_cannot_waive_actual_same_audio_word_intervals():
    prepared = fixture.prepare()
    result = deepcopy(fixture.ASR)
    # Inside the generic <=30.08s schema, outside this actual ~1s MP3.
    result['words'][-1]['end'] = 1.5
    payload = fixture.envelope(result)
    payload['choices'][0]['native_finish_reason'] = 'STOP'
    with pytest.raises(adapter.AbacusRouterAudioError, match='^abacus_router_audio_timing_unverified$'):
        adapter.observe_audio_router_response(prepared, exchange(prepared, payload))
