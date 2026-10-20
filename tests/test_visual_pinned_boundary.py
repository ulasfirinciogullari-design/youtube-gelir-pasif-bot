"""Overlapping windows compare actual trim decisions, retaining negative QA."""
from copy import deepcopy
import ast
from pathlib import Path

import pytest

from test_visual_cross_provider_review import _namespace, _review


def pinned(**changes):
    return {'path': 'retained.mp4', 'preserve_start_fraction': True,
            'start_fraction': 0.0, 'forbid_loop': True, **changes}


def test_different_evidence_moments_do_not_change_a_pinned_render(tmp_path):
    ns = _namespace()
    scenes = [{'narration': 'A banknote is visible.', 'visual_queries': []} for _ in range(7)]
    first = [_review(ns, index=i) for i in range(4)]
    second = [_review(ns, index=i, best_moment_index=2, score=90) for i in range(4)]
    ns['generate_gemini_multimodal_json'].side_effect = [{'reviews': first}, {'reviews': second}]
    original = deepcopy([first, second])
    result = ns['review_scene_visuals'](scenes, [[pinned()] for _ in scenes], tmp_path, 7,
        _missing_review_attempts=0, _score_reason_consistency_attempts=0,
        _temporal_response_repair_attempts=0)
    boundary = result['reviews'][3]
    assert boundary['score'] == 90 and boundary['evidence_gate_passed'] is True
    assert boundary['best_moment_index'] == 2 and boundary['evidence_moment_indices'] == [1]
    assert boundary['boundary_pinned_cut_review'] == {'candidate_index': 0, 'start_fraction': 0.0,
        'reported_best_moment_indices': [1, 2], 'reported_scores': [92, 90]}
    assert ns['generate_gemini_multimodal_json'].call_count == 2
    assert result['missing_review_indices'] == [] and [first, second] == original


@pytest.mark.parametrize('damage', [
    'major_visual_artifact_visible', 'authored_identity_or_material_conflict_visible',
    'substantially_repeats_adjacent_scene', 'effectively_static_or_frozen',
    'subject_visible', 'recurring_identity_continuity_matches', 'low_score',
])
@pytest.mark.parametrize('negative_window', [0, 1])
def test_pinned_boundary_preserves_rejection_in_either_neighbor_window(tmp_path, damage, negative_window):
    ns = _namespace(); batches = [[_review(ns, index=i, best_moment_index=1+n) for i in range(4)] for n in range(2)]
    if damage == 'recurring_identity_continuity_matches':
        ns['_recurring_identity_required_indices'] = lambda *args, **kwargs: list(range(7))
        for batch in batches:
            for review in batch:
                review.update(recurring_identity_continuity_matches=True, evidence_moment_indices=[0, 1])
    row = batches[negative_window][3 if negative_window == 0 else 0]
    if damage == 'low_score': row['score'] = 65
    elif damage == 'recurring_identity_continuity_matches':
        row.update(recurring_identity_continuity_applicable=True, recurring_identity_continuity_matches=False)
    else: row[damage] = damage != 'subject_visible'
    ns['generate_gemini_multimodal_json'].side_effect = [{'reviews': rows} for rows in batches]
    scenes = [{'narration': 'A banknote is visible.', 'visual_queries': []} for _ in range(7)]
    result = ns['review_scene_visuals'](scenes, [[pinned()] for _ in scenes], tmp_path, 7,
        _missing_review_attempts=0, _score_reason_consistency_attempts=0, _temporal_response_repair_attempts=0)
    boundary = result['reviews'][3]
    assert boundary['score'] < 86
    if damage != 'low_score': assert boundary['score'] <= 40


@pytest.mark.parametrize('spec', ['legacy.mp4', {'path': 'stock.mp4'}, pinned(preserve_start_fraction=False),
    pinned(preserve_start_fraction='true'), pinned(start_fraction=None), pinned(start_fraction='0'),
    pinned(start_fraction=True), pinned(start_fraction=float('nan')), pinned(start_fraction=float('inf')),
    pinned(start_fraction=-.1), pinned(start_fraction=1), pinned(path='')])
def test_unpinned_or_invalid_candidates_still_require_identical_moments(spec):
    ns = _namespace(); a = _review(ns); b = {**a, 'best_moment_index': 2}
    assert ns['_same_rendered_selection'](a, b, [spec]) is False


@pytest.mark.parametrize('change', [{'best_candidate_index': 1}, {'best_candidate_index': True},
    {'best_candidate_index': -1}, {'best_moment_index': None}, {'best_moment_index': 5},
    {'best_moment_index': True}])
def test_pinning_never_equates_different_candidates_or_invalid_evidence_ids(change):
    ns = _namespace(); a = _review(ns); b = {**a, **change}
    assert ns['_same_rendered_selection'](a, b, [pinned(), pinned(path='different.mp4')]) is False


def test_both_moments_produce_identical_real_renderer_selection():
    # Execute the real renderer selector without importing Celery or settings.
    source = Path(__file__).resolve().parents[1] / 'app/tasks.py'
    definitions = [node for node in ast.parse(source.read_text()).body if isinstance(node, ast.FunctionDef)
                   and node.name in {'_visual_path', '_apply_visual_review'}]
    renderer = {}
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(source), 'exec'), renderer)
    _apply_visual_review = renderer['_apply_visual_review']
    ns = _namespace(); a = _review(ns); b = {**a, 'best_moment_index': 2, 'best_start_fraction': .82}
    candidate = pinned(start_fraction=.2)
    left = [[deepcopy(candidate)]]; right = deepcopy(left)
    _apply_visual_review(left, 0, {**a, 'best_start_fraction': .5})
    _apply_visual_review(right, 0, b)
    assert left == right == [[candidate]]
    assert ns['_same_rendered_selection'](a, b, [candidate]) is True
