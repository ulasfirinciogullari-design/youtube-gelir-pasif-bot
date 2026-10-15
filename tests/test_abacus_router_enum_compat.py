"""Native enum removal retains the complete local contract, including audio."""
from copy import deepcopy
import json

import pytest

from app.services import abacus_router_adapter as visual
from app.services import abacus_router_schema_compat as compat
from app.services import abacus_router_audio_adapter as audio
from app.services import audio_qc
from test_abacus_router_schema_compat import observed
from test_abacus_router_adapter import SCHEMA, RESULT, KEY, parts
from test_abacus_router_audio_adapter import mp3, ASR, PROSODY, EXPECTED, response, envelope


def visual_request():
    schema = deepcopy(SCHEMA)
    schema['properties']['accepted']['enum'] = [False]
    return compat.prepare_compatible_router_request(parts(), api_key=KEY,
        system_instruction='Complete unchanged rubric.', json_schema=schema,
        schema_name=compat.ENUM_SCHEMA_NAME), schema


def test_enum_wire_keeps_original_types_rules_images_and_legacy_identity():
    prepared, schema = visual_request()
    body = prepared.payload
    assert body['messages'][1]['content'][:-1] == parts()
    native = body['response_format']['json_schema']['schema']
    assert 'enum' not in native['properties']['accepted']
    assert 'uniqueItems' not in native['properties']['moments']
    assert compat.schema_for_body(body) == schema
    assert observed(prepared, RESULT).result == RESULT
    legacy = compat.prepare_compatible_router_request(parts(), api_key=KEY,
        system_instruction='Complete unchanged rubric.', json_schema=schema)
    assert legacy.payload['response_format']['json_schema']['schema']['properties']['accepted']['enum'] == [False]
    assert legacy.request_sha256 != prepared.request_sha256
    for result in ({**RESULT, 'accepted': True}, {**RESULT, 'accepted': 0}, {**RESULT, 'moments': [1, 1]}):
        with pytest.raises(visual.AbacusRouterError): observed(prepared, result)


@pytest.mark.parametrize('damage', ['missing', 'duplicate', 'name', 'native', 'original', 'property'])
def test_bound_enum_contract_cannot_be_removed_or_altered(damage):
    prepared, _ = visual_request()
    kwargs = prepared.wire_kwargs()
    body = kwargs['json']; spec = body['response_format']['json_schema']; content = body['messages'][1]['content']
    if damage == 'missing': content.pop()
    elif damage == 'duplicate': content.append(deepcopy(content[-1]))
    elif damage == 'name': spec['name'] = compat.SCHEMA_NAME
    elif damage == 'native': spec['schema']['properties']['accepted']['enum'] = [True]
    elif damage == 'original': content[-1]['text'] += ' '
    else: spec['schema']['properties'].pop('accepted')
    with pytest.raises(visual.AbacusRouterError): visual.inspect_router_request(visual.ENDPOINT, kwargs)


def test_properties_named_enum_are_not_removed():
    original = {'type': 'object', 'properties': {
        'enum': {'type': 'string', 'enum': ['a', 'b']},
        'uniqueItems': {'type': 'array', 'uniqueItems': True, 'items': {'type': 'integer', 'enum': [1, 2]}}},
        'required': ['enum', 'uniqueItems'], 'additionalProperties': False}
    native, count = compat._lower_enums(original)
    assert count == 2 and set(native['properties']) == {'enum', 'uniqueItems'}
    assert native['properties']['enum'] == {'type': 'string'}
    assert native['properties']['uniqueItems'] == {'type': 'array', 'items': {'type': 'integer'}}


@pytest.mark.parametrize('kind', ['asr', 'prosody'])
def test_audio_keeps_full_schema_and_blind_asr_has_no_expected_text(kind):
    if kind == 'asr':
        prepared = audio.prepare_compatible_blind_asr_request(mp3(), api_key=KEY)
        schema, valid = audio._ASR_SCHEMA, deepcopy(ASR)
        assert EXPECTED not in json.dumps(prepared.payload)
        assert prepared.payload['messages'][1]['content'][1]['text'] == audio._ASR_TEXT
        bad = {**valid, 'language': 'en'}
    else:
        prepared = audio.prepare_compatible_audio_prosody_request(mp3(), api_key=KEY,
            expected_narration=EXPECTED, system_instruction=audio_qc._PROSODY_SYSTEM_INSTRUCTION,
            json_schema=deepcopy(audio_qc._PROSODY_REVIEW_SCHEMA))
        schema, valid = audio_qc._PROSODY_REVIEW_SCHEMA, deepcopy(PROSODY)
        bad = deepcopy(valid); bad['issues'][0]['code'] = 'invented'
    assert prepared.payload['response_format']['json_schema']['name'] == prepared.purpose.value + '_enum_v1'
    assert audio.schema_for_request(prepared.payload, prepared.purpose) == schema
    assert audio.observe_audio_router_response(prepared, response(prepared, payload=envelope(valid))).result == valid
    with pytest.raises(audio.AbacusRouterAudioError):
        audio.observe_audio_router_response(prepared, response(prepared, payload=envelope(bad)))
    for damage in ('missing_schema', 'wrong_name', 'changed_schema', 'extra_part'):
        kwargs = prepared.wire_kwargs(); body = kwargs['json']; content = body['messages'][1]['content']
        if damage == 'missing_schema': content.pop()
        elif damage == 'wrong_name': body['response_format']['json_schema']['name'] = prepared.purpose.value
        elif damage == 'changed_schema': content[-1]['text'] += ' '
        else: content.append({'type': 'text', 'text': EXPECTED})
        with pytest.raises(audio.AbacusRouterAudioError):
            audio.inspect_audio_router_request(audio.ENDPOINT, kwargs, purpose=prepared.purpose)
