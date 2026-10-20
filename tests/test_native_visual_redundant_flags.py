"""Captured native duplicate flags are lossless; ambiguous judgments stay closed."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.services import commissioning_reasoning as native, production_included_router as included
from app.services.production_spend import SpendBlocked
from test_commissioning_reasoning import setup, run, payload, records
from test_native_visual_contract import visual
from test_production_included_router import commissioned, client


def test_actual_native_duplicate_flags_keep_all_judgments_and_original_receipt(setup):
    prepared, _, _ = visual()
    fixture = json.loads((Path(__file__).parent / 'fixtures/native_visual_identical_flags_20260922.json').read_text())
    envelope = fixture['response']
    raw = json.dumps(envelope).encode()
    original = json.loads(envelope['candidates'][0]['content']['parts'][0]['text'])
    assert [row['score'] for row in original['reviews']] == [88, 90, 89, 90, 91, 88]
    setup[2].return_value = 200, raw
    assert run(setup, prepared, 'visual_review') == original
    assert run(setup, prepared, 'visual_review') == original
    setup[2].assert_called_once()
    assert len(records(setup)) == 1
    evidence = included._LAST_OBSERVED.get()['evidence']
    assert evidence['response_sha256'] == native._sha(raw)
    assert evidence['parsed_result_sha256'] == native._sha(native._raw(original))
    assert 'visual_completion' not in evidence


@pytest.mark.parametrize('damage', ['conflict', 'non_boolean', 'third_copy', 'duplicate_score',
                                   'root_duplicate', 'blocked', 'non_stop'])
def test_ambiguous_native_response_never_normalizes_or_repeats_transport(setup, damage):
    prepared, _, result = visual()
    p = payload(result)
    text = p['candidates'][0]['content']['parts'][0]['text']
    key = '"recurring_identity_continuity_matches": false'
    replacement = {
        'conflict': key + ', "recurring_identity_continuity_matches": true',
        'non_boolean': key + ', "recurring_identity_continuity_matches": 0',
        'third_copy': ', '.join([key] * 3),
    }.get(damage, key + ', ' + key)
    text = text.replace(key, replacement, 1)
    if damage == 'duplicate_score': text = text.replace('"score": 12', '"score": 12, "score": 12', 1)
    if damage == 'root_duplicate': text = text[:-1] + ', "reviews": []}'
    if damage == 'blocked': p['promptFeedback'] = {'blockReason': 'SAFETY'}
    if damage == 'non_stop': p['candidates'][0]['finishReason'] = 'MAX_TOKENS'
    p['candidates'][0]['content']['parts'][0]['text'] = text
    setup[2].return_value = 200, json.dumps(p).encode()
    for _ in range(2):
        with pytest.raises(SpendBlocked): run(setup, prepared, 'visual_review')
    setup[2].assert_called_once()
    assert len(records(setup)) == 1


def test_negative_judgment_stays_negative_when_repeated_flag_is_identical(setup):
    prepared, _, original = visual()
    p = payload(original)
    part = p['candidates'][0]['content']['parts'][0]
    key = '"recurring_identity_continuity_matches": false'
    part['text'] = part['text'].replace(key, key + ', ' + key, 1)
    setup[2].return_value = 200, json.dumps(p).encode()
    result = run(setup, prepared, 'visual_review')
    assert result == original and all(row['score'] == 12 for row in result['reviews'])
    setup[2].assert_called_once()
