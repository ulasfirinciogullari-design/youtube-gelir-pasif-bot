"""Real worker allocation boundaries cannot turn included stock into paid media."""
import ast
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.services import production_included_router as included, included_stock_pool as pool
from test_documentary_inbudget_stock_rescue import _context, _run

SOURCE = Path(__file__).resolve().parents[1] / 'app' / 'tasks.py'
TREE = ast.parse(SOURCE.read_text())


def boundary(monkeypatch, *, enabled=True):
    monkeypatch.setattr(included, 'enabled', lambda: enabled)
    names = {'_validate_paid_create_allocation', '_reserve_paid_create_slot'}
    ns = {'FinalVisualQualityError': RuntimeError, '_persisted_paid_create_slots': Mock(return_value=1)}
    exec(compile(ast.Module(body=[node for node in TREE.body
        if isinstance(node, ast.FunctionDef) and node.name in names], type_ignores=[]), str(SOURCE), 'exec'), ns)
    return ns


@pytest.mark.parametrize('cap', [None, 2, 6])
@pytest.mark.parametrize('used', [0, 1])
def test_unresolved_stock_cannot_reserve_a_paid_slot_even_when_legacy_cap_has_space(monkeypatch, cap, used):
    n = boundary(monkeypatch)
    with pytest.raises(RuntimeError, match='stock quality remains unresolved'):
        n['_validate_paid_create_allocation']([{'scene_index': 0}], None, cap, paid_slots_used=used)
    with pytest.raises(RuntimeError, match='new video generation is unavailable'):
        n['_reserve_paid_create_slot'](used, cap, task_id='task')
    n['_persisted_paid_create_slots'].assert_not_called()


@pytest.mark.parametrize('version', [1, 2, 3, 4, 5])
def test_existing_media_can_be_reused_but_an_uncovered_scene_cannot_be_generated(monkeypatch, version):
    n = boundary(monkeypatch)
    saved = {'version': version, 'scenes': {0: [{'key': 'existing'}]}}
    before = deepcopy(saved)
    n['_validate_paid_create_allocation']([{'scene_index': 0}], saved, 2, paid_slots_used=2)
    with pytest.raises(RuntimeError, match='stock quality remains unresolved'):
        n['_validate_paid_create_allocation']([{'scene_index': 0}, {'scene_index': 1}], saved, 2)
    assert saved == before


def test_exact_selected_recovery_may_restore_but_cannot_buy_a_new_repair(monkeypatch):
    n = boundary(monkeypatch)
    saved = {'version': 6, 'repair_scene_indices': []}
    n['_validate_paid_create_allocation']([], saved, 2, paid_slots_used=2)
    saved['repair_scene_indices'] = [1]
    with pytest.raises(RuntimeError, match='stock quality remains unresolved'):
        n['_validate_paid_create_allocation']([], saved, 6)


def test_approved_stock_and_disabled_included_mode_keep_their_existing_routes(monkeypatch):
    n = boundary(monkeypatch)
    n['_validate_paid_create_allocation']([], None, 2)
    n = boundary(monkeypatch, enabled=False)
    n['_validate_paid_create_allocation']([{'scene_index': 0}], None, 2)
    assert n['_reserve_paid_create_slot'](0, 2, task_id='task') == 1
    n['_persisted_paid_create_slots'].assert_called_once_with('task', 2, reserve=True)


@pytest.mark.parametrize('score', [35, 74, 85, 90])
def test_rescue_pool_is_saved_before_its_real_review_and_never_promotes_rejected_scores(monkeypatch, score):
    boundary(monkeypatch)
    n = _context(count=3, score=score)
    retained, sequence = [], []
    review = n['review_scene_visuals'].side_effect
    def save(task, package, visuals, credits, seen, work, *, phase):
        assert phase == 'budget_rescue'
        retained.append(deepcopy(visuals)); sequence.append('retained')
    def evaluate(*args, **kwargs):
        sequence.append('reviewed'); assert retained
        return review(*args, **kwargs)
    monkeypatch.setattr(pool, 'retain_stock_pool', save)
    n['review_scene_visuals'].side_effect = evaluate
    original_scenes = deepcopy(n['scenes'])
    _run(n)
    assert sequence == ['retained', 'reviewed']
    assert n['scenes'] == original_scenes and n['quality_threshold'] == 86
    assert [n['current_reviews'][i]['score'] for i in range(3)] == [score] * 3
    guard = boundary(monkeypatch)['_validate_paid_create_allocation']
    if score < 86:
        with pytest.raises(RuntimeError, match='stock quality remains unresolved'):
            guard(n['ranked_runway_candidates'], None, 6)
    else:
        assert n['ranked_runway_candidates'] == []
        guard([], None, 6)
