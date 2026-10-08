from copy import deepcopy
import json
from pathlib import Path

import httpx
import pytest

from app.services import commissioning_reasoning as native, production_included_router as included
from app.services.production_spend import SpendBlocked
from test_commissioning_reasoning import setup, run, payload, records
from test_native_visual_contract import visual
from test_production_included_router import commissioned, client

MISSING = {'major_visual_artifact_visible', 'manufactured_object_cues_visible', 'moving_connector_visible'}


def inputs():
    prepared, _, full = visual()
    original = deepcopy(full)
    for key in MISSING: original['reviews'][-1].pop(key)
    completion = {'reviews': [{key: row[key] for key in ('scene_index', *sorted(MISSING))}
                              for row in full['reviews']]}
    return prepared, full, original, completion


def response(value):
    return 200, json.dumps(payload(value)).encode()


def test_missing_fields_receive_one_observed_completion_preserving_negative_verdicts(setup):
    prepared, full, original, completion = inputs()
    setup[2].side_effect = [response(original), response(completion)]
    assert run(setup, prepared, 'visual_review') == full
    before = {k: setup[0].client.get(k) for k in setup[0].client.scan_iter(match=native.PREFIX + '*:*')
              if ':request:' in k or ':response:' in k}
    assert run(setup, prepared, 'visual_review') == full
    assert len(records(setup)) == 2 and setup[2].call_count == 2
    assert all(setup[0].client.get(k) == v for k, v in before.items())
    sent = setup[2].call_args_list
    frames = lambda request: [p for p in request['contents'][0]['parts'] if 'inlineData' in p]
    assert frames(sent[0].args[0]) == frames(sent[1].args[0])
    assert all(row['score'] == 12 for row in full['reviews'])
    evidence = included._LAST_OBSERVED.get()['evidence']
    assert evidence['request_sha256'] != evidence['visual_completion']['request_sha256']
    assert evidence['provider'] == evidence['visual_completion']['provider'] == 'gemini'
    assert evidence['parsed_result_sha256'] == native._sha(native._raw(full))


@pytest.mark.parametrize('damage', ['changes_existing_flag', 'omits_again', 'wrong_indices', 'adds_score'])
def test_invalid_completion_never_rewrites_review_or_recurses(setup, damage):
    prepared, full, original, completion = inputs()
    if damage == 'changes_existing_flag': completion['reviews'][0]['major_visual_artifact_visible'] = True
    if damage == 'omits_again': completion['reviews'][-1].pop('major_visual_artifact_visible')
    if damage == 'wrong_indices': completion['reviews'][-1]['scene_index'] = 0
    if damage == 'adds_score': completion['reviews'][0]['score'] = 100
    setup[2].side_effect = [response(original), response(completion)]
    for _ in range(2):
        with pytest.raises(SpendBlocked): run(setup, prepared, 'visual_review')
    assert setup[2].call_count == 2 and len(records(setup)) == 2


def test_unknown_completion_remains_occupied_and_is_never_sent_again(setup):
    prepared, _, original, _ = inputs()
    setup[2].side_effect = [response(original), httpx.ReadTimeout('unknown')]
    for _ in range(2):
        with pytest.raises(SpendBlocked): run(setup, prepared, 'visual_review')
    assert setup[2].call_count == 2 and len(records(setup)) == 2


@pytest.mark.parametrize('damage', ['bad_finish', 'missing_usage', 'wrong_model', 'duplicate_score', 'missing_reason'])
def test_unverified_envelope_or_nonboolean_omission_cannot_buy_completion(setup, damage):
    prepared, _, original, _ = inputs()
    p = payload(original)
    if damage == 'bad_finish': p['candidates'][0]['finishReason'] = 'MAX_TOKENS'
    if damage == 'missing_usage': p.pop('usageMetadata')
    if damage == 'wrong_model': p['modelVersion'] = 'different-model'
    if damage == 'duplicate_score':
        p['candidates'][0]['content']['parts'][0]['text'] = json.dumps(original).replace('"score": 12', '"score": 12, "score": 99', 1)
    if damage == 'missing_reason':
        original['reviews'][-1].pop('reason'); p = payload(original)
    setup[2].return_value = 200, json.dumps(p).encode()
    for _ in range(2):
        with pytest.raises(SpendBlocked): run(setup, prepared, 'visual_review')
    setup[2].assert_called_once()
    assert len(records(setup)) == 1


def test_actual_saved_native_response_completes_only_its_four_omitted_flags(setup):
    from app.services.visual_qc import _review_json_schema
    from app.services.abacus_router_schema_compat import prepare_json_object_router_request
    from test_abacus_router_adapter import KEY, image
    fixture = json.loads((Path(__file__).parent/'fixtures/native_visual_missing_fields_20260922.json').read_text())
    envelope = fixture['response']
    original = json.loads(envelope['candidates'][0]['content']['parts'][0]['text'])
    assert original['reviews'][0]['score'] == 35 and original['reviews'][1]['score'] == 90
    schema = _review_json_schema([0, 1], {0: {0: {0, 1, 2}, 1: {0, 1, 2}}, 1: {0: {0, 1, 2}}})
    prepared = prepare_json_object_router_request([{'type': 'text', 'text': 'Offline fixture frames.'}, image()],
        api_key=KEY, system_instruction='Original rubric.', json_schema=schema)
    missing = MISSING | {'physical_causality_applicable'}
    completion = {'reviews': [{'scene_index': index, **{key: False for key in missing}} for index in (0, 1)]}
    setup[2].side_effect = [(200, json.dumps(envelope).encode()), response(completion)]
    result = run(setup, prepared, 'visual_review')
    assert all(result['reviews'][index][key] == value
               for index, row in enumerate(original['reviews']) for key, value in row.items())
    assert set(result['reviews'][1]) - set(original['reviews'][1]) == missing
    assert result['reviews'][0]['thermal_evidence_visible'] is False
    assert setup[2].call_count == 2
