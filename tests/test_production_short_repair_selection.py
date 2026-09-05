"""One production final-repair pass inside the already frozen paid budget."""

import ast
import copy
from pathlib import Path

import pytest

from app.services import visual_routing


OPTIONS = {'mode': 'production', 'format': 'shorts', 'quality_threshold': 86}
SCENES = [{'narration': f'Scene {index}', 'ai_prompt': None} for index in range(6)]


def _select(**changes):
    arguments = {
        'options': dict(OPTIONS), 'duration_minutes': 0.5,
        'rejected_scene_indices': [0, 3, 4], 'scenes': copy.deepcopy(SCENES),
        'generated_scene_indices': {0, 3, 4},
        'final_reviews': {0: {'score': 80}, 3: {'score': 35}, 4: {'score': 40}},
        'paid_create_attempts': 3, 'paid_create_cap': 4,
    }
    arguments.update(changes)
    return visual_routing.preview_runway_repair_indices(**arguments)


def test_three_primary_creates_of_four_allow_one_actual_generated_stock_fallback():
    assert all(scene['ai_prompt'] is None for scene in SCENES)
    assert _select() == [3]


@pytest.mark.parametrize('cap, used', [(4, 4), (4, 5), (2, 2), (3, 3), (6, 6)])
def test_exhausted_frozen_total_never_selects_another_create(cap, used):
    assert _select(paid_create_cap=cap, paid_create_attempts=used) == []


@pytest.mark.parametrize('cap, used', [(6, 3), (4, 1), (2, 1)])
def test_extra_total_headroom_never_selects_more_than_one_in_this_pass(cap, used):
    assert _select(paid_create_cap=cap, paid_create_attempts=used) == [3]


def test_production_uses_frozen_cap_without_rereading_new_server_policy(monkeypatch):
    def no_policy_read(*args, **kwargs):
        raise AssertionError('Production repair must use the authoritative frozen cap')
    monkeypatch.setattr(visual_routing, 'preview_total_paid_create_cap', no_policy_read)
    assert _select(paid_create_cap=2, paid_create_attempts=2) == []
    assert _select(paid_create_cap=4, paid_create_attempts=3) == [3]


@pytest.mark.parametrize('changes', [
    {'paid_create_cap': None}, {'paid_create_cap': True}, {'paid_create_cap': '4'},
    {'paid_create_cap': 1}, {'paid_create_cap': 7}, {'paid_create_cap': 4.0},
    {'paid_create_attempts': -1}, {'paid_create_attempts': True},
    {'paid_create_attempts': '3'}, {'paid_create_attempts': 3.0},
    {'options': dict(OPTIONS, quality_threshold='86')},
])
def test_missing_or_invalid_authoritative_budget_and_threshold_fail_closed(changes):
    assert _select(**changes) == []


@pytest.mark.parametrize('changes', [
    {'duration_minutes': 0.51}, {'duration_minutes': 1.0},
    {'options': dict(OPTIONS, format='landscape')},
    {'options': {'mode': 'production'}},
    {'options': {'mode': 'other', 'format': 'shorts'}},
])
def test_nonmatching_production_formats_and_durations_remain_disabled(changes):
    assert _select(**changes) == []


@pytest.mark.parametrize('review', [None, {}, {'score': -1}, {'score': '35'},
                                  {'score': True}, {'score': 35.0},
                                  {'score': 86}, {'score': 100}])
def test_missing_malformed_or_passing_review_cannot_request_repair(review):
    reviews = {} if review is None else {3: review}
    assert _select(rejected_scene_indices=[3], final_reviews=reviews) == []


def test_generated_clip_and_actual_rejection_are_both_required():
    assert _select(generated_scene_indices=set(), exact_revalidation_scene_indices={0, 3, 4}) == []
    assert _select(rejected_scene_indices=[]) == []
    assert _select(generated_scene_indices={'3'}) == []
    assert _select(rejected_scene_indices=[True, '3', -1, 6]) == []
    assert _select(rejected_scene_indices=[3, 3, 4]) == [3]


def test_identity_failure_can_be_repaired_but_never_promoted_by_selection():
    reviews = {3: {'score': 35, 'subject_visible': False,
                   'authored_identity_or_material_conflict_visible': True}}
    before = copy.deepcopy(reviews)
    assert _select(rejected_scene_indices=[3], final_reviews=reviews) == [3]
    assert reviews == before
    assert reviews[3]['score'] < OPTIONS['quality_threshold']
    assert reviews[3]['authored_identity_or_material_conflict_visible'] is True


def test_preview_keeps_existing_two_repairs_and_original_authorship_rule():
    scenes = [{'ai_prompt': 'authored shot'} for _ in range(3)]
    reviews = {index: {'score': 10 + index} for index in range(3)}
    args = dict(options={'mode': 'preview'}, duration_minutes=0.5,
                rejected_scene_indices=[0, 1, 2], scenes=scenes,
                generated_scene_indices={0, 1, 2}, final_reviews=reviews)
    assert visual_routing.preview_runway_repair_indices(**args, paid_create_attempts=0, paid_create_cap=4) == [0, 1]
    assert visual_routing.preview_runway_repair_indices(**args, paid_create_attempts=1, paid_create_cap=4) == [0]
    assert visual_routing.preview_runway_repair_indices(**args, paid_create_attempts=2, paid_create_cap=4) == []
    args['scenes'] = [{'ai_prompt': None} for _ in range(3)]
    assert visual_routing.preview_runway_repair_indices(**args) == []


def _worker_selection(**changes):
    path = Path(__file__).resolve().parents[1] / 'app' / 'tasks.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    selection = next(
        node for node in ast.walk(tree) if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == 'final_runway_repair_candidates'
                for target in node.targets)
        and isinstance(node.value, ast.IfExp)
    )
    namespace = {
        'recovered_generated_media': None,
        'preview_runway_repair_indices': visual_routing.preview_runway_repair_indices,
        'options': OPTIONS, 'duration_minutes': 0.5, 'scenes': SCENES,
        'rejected_final_scenes': [0, 3, 4], 'runway_generated_scenes': [0, 3, 4],
        'final_reviews': {0: {'score': 80}, 3: {'score': 35}, 4: {'score': 40}},
        'terminal_manual_qa_failure_scene_indices': set(),
        'runway_attempts': 3, 'total_paid_create_cap': 4,
    }
    namespace.update(changes)
    exec(compile(ast.Module(body=[selection], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['final_runway_repair_candidates']


def test_actual_worker_forwards_frozen_cap_and_selects_production_repair():
    assert _worker_selection() == [3]
    assert _worker_selection(total_paid_create_cap=2, runway_attempts=2) == []
    assert _worker_selection(total_paid_create_cap=4, runway_attempts=4) == []


def test_recovered_paid_media_keeps_existing_no_new_repair_branch():
    assert _worker_selection(recovered_generated_media={'scenes': {3: ['paid.mp4']}}) == []
