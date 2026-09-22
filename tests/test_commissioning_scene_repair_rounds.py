"""Run the actual correction loop with durable create counters, offline edges."""
import ast
from copy import deepcopy
from unittest.mock import Mock

import pytest

from app.services import commissioning_video, production_included_router, studio_state
from app.services.production_spend import SpendBlocked
from test_production_spend_scene_terminal import case, production, _worker, TREE, OUTER, SOURCE, TASK_ID


def execute(case):
    loop = next(n for n in OUTER.body if isinstance(n, ast.For)
                and isinstance(n.target, ast.Name) and n.target.id == 'repair_round')
    exec(compile(ast.Module(body=[deepcopy(loop)], type_ignores=[]), str(SOURCE), 'exec'), case.ns)


@pytest.fixture
def rounds(case, monkeypatch):
    _worker(case, 'final_repair')  # Load the production paid-slot/checkpoint helpers.
    monkeypatch.setattr(commissioning_video, 'enabled_for_task', lambda: True)
    monkeypatch.setattr(production_included_router, 'enabled', lambda: True)
    ns = case.ns
    for _ in range(3): studio_state.paid_create_budget_state(TASK_ID, 6, reserve=True)
    ns.update(self=case.self, runway_attempts=3, final_runway_repair_attempts=0,
        omni_continuity_reference_image_path=None, omni_continuity_anchor_scene_idx=None,
        final_runway_repair_candidates=[1], rejected_final_scenes=[1],
        quality_threshold=86, manual_qa_preview_scenes=set(),
        final_visual_qc={'reviews': []},
        _preflight_runway_candidates_before_paid=Mock(), approved_package=None,
        completion_repairs=commissioning_video.completion_repairs,
        provider_outage_stock_scenes=set(), stock_quality_fallback_scenes=set(),
        _final_pexels_rescue_queries=Mock(return_value=['real subject']),
        _retry_bad_scene=Mock(return_value=[]), seen_ids=set(), credits=[],
        is_bounded_short_preview=True, pexels_orientation='portrait', stock_reuse_visuals=None,
        _reviewed_visual_spec=lambda specs, review: specs[review['best_candidate_index']],
        _manual_qa_preview_passes=Mock(return_value=False), register_manual_qa_preview=Mock(),
        _apply_visual_review=lambda visuals, index, review, **_: visuals.__setitem__(
            index, [visuals[index][review['best_candidate_index']]]))
    ns['final_reviews'] = {i: {'scene_index': i, 'score': 32 if i == 1 else 93,
        'best_candidate_index': 0, 'reason': 'Garbled label.' if i == 1 else 'Passed.'} for i in range(3)}
    ns['options']['quality_threshold'] = 86
    case.critic.side_effect = [
        {'reviews': [{'scene_index': 0, 'score': 32, 'best_candidate_index': 0, 'reason': 'Still garbled.'}]},
        {'reviews': [{'scene_index': 0, 'score': 91, 'best_candidate_index': 0, 'reason': 'Clean visible action.'}]},
    ]
    return case


def test_second_correction_preserves_good_scenes_voice_and_each_distinct_clip(rounds):
    ns = rounds.ns
    approved = {i: (deepcopy(ns['scene_visuals'][i]), deepcopy(ns['final_reviews'][i])) for i in (0, 2)}
    voice = deepcopy(ns['voice_result'])
    execute(rounds)
    assert ns['rejected_final_scenes'] == [] and rounds.provider.call_count == 2
    assert ns['runway_attempts'] == 5 and studio_state.paid_create_budget_state(TASK_ID, 6)['used'] == 5
    assert ns['final_runway_repair_scenes'] == [1] and ns['voice_result'] == voice
    for i in (0, 2): assert (ns['scene_visuals'][i], ns['final_reviews'][i]) == approved[i]
    paths = [call.args[1] for call in ns['download_generated_scene'].call_args_list]
    assert len(set(paths)) == 2 and all(path.exists() for path in paths)
    assert 'revision 2' in rounds.provider.call_args_list[1].args[0]
    assert rounds.critic.call_args_list[1].args[0] == [ns['scenes'][1]]
    assert len(rounds.critic.call_args_list[1].args[1][0]) == 1
    ns['_retry_bad_scene'].assert_not_called()


def test_all_rejected_corrections_stop_at_existing_total_capacity(rounds):
    rounds.critic.side_effect = None
    rounds.critic.return_value = {'reviews': [{'scene_index': 0, 'score': 32,
        'best_candidate_index': 0, 'reason': 'Real remaining defect.'}]}
    execute(rounds)
    assert rounds.provider.call_count == 3 and rounds.ns['runway_attempts'] == 6
    assert rounds.ns['rejected_final_scenes'] == [1] and rounds.ns['final_reviews'][1]['score'] == 32
    assert studio_state.paid_create_budget_state(TASK_ID, 6)['used'] == 6


def test_ambiguous_second_provider_attempt_keeps_first_clip_and_stops(rounds):
    error = SpendBlocked('commissioning_video_outcome_unverified')
    rounds.provider.side_effect = [rounds.generated, error]
    with pytest.raises(SpendBlocked) as caught: execute(rounds)
    assert caught.value is error and rounds.provider.call_count == 2
    assert rounds.critic.call_count == 1 and rounds.persistence.call_count == 1
    assert studio_state.paid_create_budget_state(TASK_ID, 6)['used'] == 5


def test_known_provider_failure_uses_stock_without_another_paid_attempt(rounds):
    rounds.provider.side_effect = commissioning_video.CommissionedVideoUnavailable('observed internal failure')
    execute(rounds)
    rounds.provider.assert_called_once()
    assert rounds.ns['_retry_bad_scene'].call_count == 1
    assert rounds.ns['rejected_final_scenes'] == [1]
    rounds.critic.assert_not_called()
    assert 1 in rounds.ns['omni_unsafe_submission_scenes']


def test_filtered_initial_generation_fences_later_paid_scene_repair(rounds):
    rounds.ns['selected_runway'] = [{'scene_index': 1}]
    rounds.provider.side_effect = commissioning_video.CommissionedVideoUnavailable(
        'commissioned_video_completed_filtered')
    worker = _worker(rounds, 'initial_generation')
    worker(rounds.self)
    rounds.provider.assert_called_once()
    assert 1 in rounds.ns['omni_unsafe_submission_scenes']
    assert rounds.ns['scene_visuals'][1] == [{'path': 'stock-1.mp4'}]


def test_normal_noncommissioned_policy_does_not_add_an_extra_round(rounds):
    rounds.ns['completion_repairs'] = Mock(return_value=None)
    execute(rounds)
    rounds.provider.assert_called_once()
    assert rounds.ns['rejected_final_scenes'] == [1]
