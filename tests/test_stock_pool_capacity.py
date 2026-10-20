from pathlib import Path
from copy import deepcopy
import json

from test_included_stock_pool import case, commissioned, client, inputs, run
from app.services import included_stock_pool as pool, production_included_router as included


def test_oversized_alternative_is_removed_before_any_review_and_exact_pool_is_retained(case, monkeypatch):
    data = inputs(case, 'first', b'A', 101)
    work, visuals, credits, ids = data
    large = work/'large.mp4'; large.write_bytes(b'L' * 4097)
    visuals[0].insert(0, {**visuals[0][0], 'path': str(large), 'pexels_id': 102})
    ids.add(102)
    monkeypatch.setattr(pool, 'MAX_FILE', 4096)
    run(case, data)
    assert [row['pexels_id'] for row in visuals[0]] == [101]
    assert len(case.uploads) == 1 and next(iter(case.objects.values())) == b'A' * 2048
    retry = inputs(case, 'retry', b'B', 103);run(case, retry)
    assert retry[1][0][0]['pexels_id'] == 101 and len(case.uploads) == 1
    assert Path(retry[1][0][0]['path']).read_bytes() == b'A' * 2048


def test_total_capacity_prefers_coverage_over_extra_candidates(case, monkeypatch):
    data = inputs(case, 'first', b'A', 101)
    work, visuals, _, ids = data
    case.package['scenes'].append({**case.package['scenes'][0], 'index': 1})
    alternative = work/'alternative.mp4';alternative.write_bytes(b'B' * 2048)
    other_scene = work/'other.mp4';other_scene.write_bytes(b'C' * 2048)
    visuals[0].append({**visuals[0][0], 'path': str(alternative), 'pexels_id': 102})
    visuals.append([{**visuals[0][0], 'path': str(other_scene), 'pexels_id': 103}])
    ids.update({102, 103})
    monkeypatch.setattr(pool, 'MAX_TOTAL', 4096)
    run(case, data)
    assert [[v['pexels_id'] for v in row] for row in visuals] == [[101], [103]]
    assert set(case.objects.values()) == {b'A' * 2048, b'C' * 2048}


def test_unavailable_large_candidate_becomes_empty_unapproved_scene_not_false_quality_pass(case, monkeypatch):
    data = inputs(case, 'first', b'A', 101)
    monkeypatch.setattr(pool, 'MAX_TOTAL', 1024)
    run(case, data)
    assert data[1] == [[]] and not case.uploads
    key = next(case.ledger.client.scan_iter(match=pool.PREFIX+'*'))
    saved = json.loads(included._cipher().decrypt(case.ledger.client.get(key).encode()))
    assert saved['pools'] == [[]] and saved['qa_approved'] is False
    retry = inputs(case, 'retry', b'B', 102)
    run(case, retry)
    assert retry[1] == [[]] and not case.uploads
