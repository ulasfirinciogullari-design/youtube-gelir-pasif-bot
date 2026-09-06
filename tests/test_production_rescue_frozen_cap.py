"""Execute the worker's real bounded rescue block with no provider imports."""

import ast
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest

from test_production_paid_budget_configuration import (
    NEW, OLD, PRODUCTION, _startup, budget, configured,
)


SOURCE = Path(__file__).resolve().parents[1] / 'app' / 'tasks.py'
TREE = ast.parse(SOURCE.read_text(encoding='utf-8'))
RESCUE = next(
    node for node in ast.walk(TREE)
    if isinstance(node, ast.If) and node.body
    and isinstance(node.body[0], ast.Assign)
    and any(isinstance(target, ast.Name) and target.id == 'overflow_candidates'
            for target in node.body[0].targets)
)


def _context(count, cap, **changes):
    context = {
        'is_bounded_short_preview': False,
        'options': {'mode': 'production', 'format': 'shorts', 'content_style': 'documentary'},
        'duration_minutes': 0.5, 'total_paid_create_cap': cap,
        'scene_repair_recovery': False, 'provider_outage_stock_scenes': set(),
        'stock_quality_fallback_scenes': set(), 'runway_submission_cap': 2,
        'ranked_runway_candidates': [{'scene_index': index} for index in range(count)],
        'scenes': [{'narration': 'Unchanged story.'} for _ in range(count)],
        'package': {'sources': [{'url': 'https://example.test/source', 'evidence': 'Source evidence.'}]},
        'quality_threshold': 86, '_prepaid_stock_rescue_candidates': Mock(return_value=[]),
        '_retry_bad_scene': Mock(side_effect=AssertionError('Unexpected stock access')),
        'review_scene_visuals': Mock(side_effect=AssertionError('Unexpected review')),
    }
    context.update(changes)
    return context


def _execute(context):
    exec(compile(ast.Module(body=[RESCUE], type_ignores=[]), str(SOURCE), 'exec'), context)


@pytest.mark.parametrize('cap', [2, 3, 4, 5, 6])
def test_empty_selector_at_or_below_frozen_cap_never_accesses_stock(cap):
    for count in range(cap + 1):
        context = _context(count, cap)
        before = deepcopy((context['scenes'], context['ranked_runway_candidates']))
        _execute(context)
        context['_prepaid_stock_rescue_candidates'].assert_called_once()
        assert context['_prepaid_stock_rescue_candidates'].call_args.args[1] == cap
        context['_retry_bad_scene'].assert_not_called()
        context['review_scene_visuals'].assert_not_called()
        assert before == (context['scenes'], context['ranked_runway_candidates'])
        assert context['total_paid_create_cap'] == cap


@pytest.mark.parametrize('cap', [2, 3, 4, 5, 6])
def test_only_actual_overflow_uses_the_frozen_total_as_rescue_boundary(cap):
    context = _context(cap + 1, cap)
    _execute(context)
    call = context['_prepaid_stock_rescue_candidates'].call_args
    assert call.args == (context['ranked_runway_candidates'], cap)
    assert call.kwargs['quality_threshold'] == 86
    assert call.kwargs['evidence_sources'] is context['package']['sources']


@pytest.mark.parametrize('count', [2, 3, 6])
def test_preview_selector_keeps_its_original_submission_boundary(count):
    context = _context(count, 6, is_bounded_short_preview=True,
                       options={'mode': 'preview', 'format': 'shorts'})
    _execute(context)
    context['_prepaid_stock_rescue_candidates'].assert_called_once()
    assert context['_prepaid_stock_rescue_candidates'].call_args.args[1] == 2
    context['_retry_bad_scene'].assert_not_called()
    context['review_scene_visuals'].assert_not_called()


@pytest.mark.parametrize('changes', [
    {'options': {'mode': 'production', 'format': 'landscape'}},
    {'duration_minutes': 1.0}, {'total_paid_create_cap': None},
    {'total_paid_create_cap': True}, {'total_paid_create_cap': 0},
    {'scene_repair_recovery': True}, {'provider_outage_stock_scenes': {0}},
    {'stock_quality_fallback_scenes': {0}},
])
def test_rescue_scope_and_recovery_exclusions_are_unchanged(changes):
    context = _context(7, 6, **changes)
    _execute(context)
    context['_prepaid_stock_rescue_candidates'].assert_not_called()


@pytest.mark.parametrize('status', [401, 403, 429, 500, 599])
def test_required_overflow_does_not_swallow_or_reclassify_existing_provider_error(status, tmp_path):
    error = RuntimeError('Provider unavailable')
    error.status_code = status
    context = _context(5, 4)
    context.update(
        current_reviews={4: {'score': 65}}, scene_durations=[5.0] * 5,
        seen_ids=set(), work=tmp_path, credits=[], pexels_orientation='portrait',
        stock_reuse_visuals=None,
        _prepaid_stock_rescue_queries=Mock(return_value=['same subject']),
        _prepaid_stock_rescue_candidates=Mock(return_value=[{'scene_index': 4}]),
        _retry_bad_scene=Mock(side_effect=error),
    )
    with pytest.raises(RuntimeError) as caught:
        _execute(context)
    assert caught.value is error and caught.value.status_code == status
    assert context['_retry_bad_scene'].call_count == 1
    context['review_scene_visuals'].assert_not_called()


def test_skipping_stock_does_not_bypass_remaining_paid_allocation(budget):
    context = _context(4, 4)
    _execute(context)
    with pytest.raises(RuntimeError, match='before any submission'):
        budget.tasks['_validate_paid_create_allocation'](
            context['ranked_runway_candidates'], None, 4, paid_slots_used=1,
        )


def test_parent_four_is_not_raised_when_new_child_freezes_six(configured, budget):
    budget.state(OLD, 4)
    configured.settings.studio_production_short_paid_create_cap = 6
    current_cap = configured.preview_total_paid_create_cap(PRODUCTION, 0.5)
    assert _startup(budget, OLD, current_cap)['total_paid_create_cap'] == 4
    assert _startup(budget, NEW, current_cap)['total_paid_create_cap'] == 6
    old = _context(5, budget.state(OLD, 6)['cap'])
    new = _context(6, budget.state(NEW, 6)['cap'])
    _execute(old)
    _execute(new)
    old['_prepaid_stock_rescue_candidates'].assert_called_once()
    new['_prepaid_stock_rescue_candidates'].assert_called_once()
    assert new['_prepaid_stock_rescue_candidates'].call_args.args[1] == 6
    new['_retry_bad_scene'].assert_not_called()
    assert budget.state(OLD, 6) == {'used': 0, 'cap': 4, 'remaining': 4}
    assert budget.state(NEW, 6) == {'used': 0, 'cap': 6, 'remaining': 6}
