"""The real malformed review keeps all judgments and uses ordinary completion."""
from copy import deepcopy
from pathlib import Path
import hashlib
import json

from test_included_visual_completion import case, journal
from test_production_included_router import commissioned
from test_production_credit_ledger import client
from test_abacus_router_adapter import image, response, envelope
from test_production_cash_disabled import dump
from app.services import visual_review_json as decoder, visual_qc
from app.services import production_included_router as included


def test_real_margin_review_keeps_scores_and_completes_missing_evidence_once(case):
    fixture = json.loads((Path(__file__).parent/'fixtures/margin_candidate_annotation_20260922.json').read_text())
    original = fixture['original_provider_content']
    assert hashlib.sha256(original.encode()).hexdigest() == fixture['original_content_sha256']
    indices, candidates = list(range(6)), {i: {0: {0, 1, 2}, 1: {0, 1, 2}} for i in range(6)}
    schema = visual_qc._review_json_schema(indices, candidates)
    partial = decoder.decode(original, schema)
    before = deepcopy(partial)
    assert [r['score'] for r in partial['reviews']] == [88, 91, 90, 92, 89, 89]
    assert all('receiving_interface_visible' not in row for row in partial['reviews'])
    # This is simulated evidence, never applied to the production job. Ordinary
    # same-frame completion must actually request the omitted field at runtime.
    fields = {'reviews': [{k: row.get(k, False) for k in ('scene_index',
        'receiving_interface_visible', 'recurring_identity_continuity_applicable',
        'recurring_identity_continuity_matches')} for row in partial['reviews']]}
    def send(prepared):
        case.calls.append(prepared)
        payload = envelope()
        payload['choices'][0]['message']['content'] = original if len(case.calls) == 1 else json.dumps(fields)
        return response(prepared, payload=payload)
    case.sender.side_effect = send
    content = [{'type': 'input_text', 'text': 'Same six-scene documentary rubric and candidate frames.'},
        {'type': 'input_image', 'image_url': image()['image_url']['url']}]
    def request():
        return case.ns['_request_visual_review']('abacus_included', True, 'Full original rubric.',
            content, [], indices, candidates, None, 'low')
    result = request()
    assert len(case.calls) == 2 and partial == before
    for row, completed in zip(partial['reviews'], result['reviews']):
        assert all(completed[k] == value and type(completed[k]) is type(value) for k, value in row.items())
        assert completed['receiving_interface_visible'] is False
    failure_key = next(case.ledger.client.scan_iter(match=included.PREFIX + 'failure:*'))
    failure = json.loads(case.ledger.client.get(failure_key))
    captured = json.loads(included._cipher().decrypt(failure['encrypted_response'].encode()))
    assert captured['choices'][0]['message']['content'] == original
    assert len(journal(case)['requests']) == 2
    snapshot = dump(case.ledger.client)
    assert request() == result and len(case.calls) == 2
    assert dump(case.ledger.client) == snapshot
