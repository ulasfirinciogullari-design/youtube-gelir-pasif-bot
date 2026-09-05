import ast
from pathlib import Path
import sys
from types import ModuleType
from unittest.mock import Mock

import pytest


SOURCE = Path(__file__).resolve().parents[1] / 'app' / 'tasks.py'
TREE = ast.parse(SOURCE.read_text(encoding='utf-8'))
FUNCTION = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == '_checkpoint_overbudget_visuals')


def _load(monkeypatch):
    service = ModuleType('app.services.visual_allocation_checkpoint')
    service.persist_visual_allocation_checkpoint = Mock(return_value={
        'visual_allocation_checkpoint': {'status': 'diagnostic_only'},
    })
    monkeypatch.setitem(sys.modules, service.__name__, service)
    namespace = {'Path': Path, 'update_job': Mock()}
    exec(compile(ast.Module(body=[FUNCTION], type_ignores=[]), str(SOURCE), 'exec'), namespace)
    return namespace, service.persist_visual_allocation_checkpoint


def _run(namespace, *, cap=2, used=0, pending=None):
    candidates = [{'scene_index': i} for i in (pending if pending is not None else [3, 5, 4])]
    scenes, visuals, reviews = [{}] * 6, [[{'path': 'selected.mp4'}]] * 6, {}
    namespace['_checkpoint_overbudget_visuals'](
        'current-task', scenes, visuals, reviews, candidates, Path('/tmp/current'), cap,
        paid_slots_used=used, quality_threshold=86,
    )
    return scenes, visuals, reviews, candidates


def test_overbudget_preserves_actual_selected_scene_data_without_mutating_it(monkeypatch):
    namespace, persist = _load(monkeypatch)
    scenes, visuals, reviews, candidates = _run(namespace)
    persist.assert_called_once()
    args = persist.call_args.args
    assert args[1] is scenes and args[2] is visuals and args[3] is reviews
    assert persist.call_args.kwargs['allocation'] == {
        'paid_create_cap': 2, 'paid_create_used': 0, 'paid_slots_remaining': 2,
        'required_paid_scenes': 3, 'quality_threshold': 86,
        'selected_paid_scene_indices': [3, 5], 'overflow_scene_indices': [4],
        'failed_stock_scene_indices': [3, 5, 4],
    }
    namespace['update_job'].assert_called_once_with(
        'current-task', visual_allocation_checkpoint={'status': 'diagnostic_only'},
    )
    assert candidates == [{'scene_index': i} for i in [3, 5, 4]]


@pytest.mark.parametrize('cap, used, pending', [
    (2, 0, [3, 4]), (2, 1, [3]), (2, 0, []),
    (None, 0, [3, 4, 5]), (0, 0, [3]), (True, 0, [3]),
    (2, -1, [3, 4, 5]), (2, True, [3, 4, 5]),
])
def test_normal_or_invalid_allocations_never_upload_diagnostics(monkeypatch, cap, used, pending):
    namespace, persist = _load(monkeypatch)
    _run(namespace, cap=cap, used=used, pending=pending)
    persist.assert_not_called()
    namespace['update_job'].assert_not_called()


@pytest.mark.parametrize('target', ['persist', 'update'])
def test_diagnostic_failure_never_changes_allocation_failure(monkeypatch, target):
    namespace, persist = _load(monkeypatch)
    mock = persist if target == 'persist' else namespace['update_job']
    mock.side_effect = RuntimeError('diagnostic unavailable')
    _run(namespace)


def test_checkpoint_is_wired_after_final_stock_ranking_before_paid_allocation_validation():
    pipeline = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == 'run_video_pipeline')
    calls = [(n.func.id, n.lineno) for n in ast.walk(pipeline)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    checkpoint = [line for name, line in calls if name == '_checkpoint_overbudget_visuals']
    diagnostics = [line for name, line in calls if name == '_record_prepaid_visual_diagnostics']
    allocation = [line for name, line in calls if name == '_validate_paid_create_allocation']
    assert len(checkpoint) == len(diagnostics) == 1
    assert diagnostics[0] < checkpoint[0] < min(allocation)
