"""Execute worker boundaries without importing workers or contacting providers."""

import ast
import copy
import json
from pathlib import Path

import pytest


SOURCE = Path(__file__).resolve().parents[1] / 'app' / 'tasks.py'
TREE = ast.parse(SOURCE.read_text(encoding='utf-8'))
SOURCE_ID = 'b0730000-0000-4000-8000-000000000000'


def _entry(index):
    return {
        'key': f'recovery/{SOURCE_ID}/raw/clip-{index:02d}.mp4',
        'sha256': 'a' * 64, 'size': 4096,
        'provider': 'gemini_veo', 'provider_attempts': 1,
        'synthetic_motion_only': False, 'motion_recipe_version': None,
        'source_media_type': 'video',
    }


def _execute(nodes, namespace):
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), namespace)


def _named_assignment(name):
    return next(node for node in ast.walk(TREE) if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == name
                        for target in node.targets))


def _forbidden(*args, **kwargs):
    raise AssertionError('Recovery-only must not submit or reserve a paid create')


def _runtime(**changes):
    namespace = {
        'FinalVisualQualityError': RuntimeError, 'Path': Path, 'json': json,
        'options': {'mode': 'production', 'format': 'shorts', 'quality_threshold': 86},
        'duration_minutes': 0.5, 'scene_repair_recovery': False,
        'recovered_generated_media': {
            'version': 3, 'recovery_only': True, 'source_task_id': SOURCE_ID,
            'package_sha256': 'b' * 64,
            'scenes': {3: [_entry(3)]},
        },
        'scenes': [{'narration': f'Scene {i}', 'ai_prompt': None} for i in range(6)],
        'scene_visuals': [[f'stock-{i}.mp4'] for i in range(6)],
        'current_reviews': {i: {'score': 92 if i == 3 else 40} for i in range(6)},
        'ranked_runway_candidates': [{'scene_index': 0}, {'scene_index': 4}],
        'prompt_candidates': {}, 'recovery_paid_scene_indices': set(),
        'is_bounded_short_preview': False, 'is_private_ai_first_omni_preview': False,
        'runway_submission_cap': 4, 'runway_required_submission_cap': 4,
        'runway_effective_submission_cap': 4, 'total_paid_create_cap': 4,
        'runway_attempts': 4, 'quality_threshold': 86,
        '_visual_path': lambda spec: spec.get('path', '') if isinstance(spec, dict) else spec,
        '_reserve_paid_create_slot': _forbidden, 'generate_scene': _forbidden,
    }
    helpers = {'_validate_paid_create_allocation', '_require_recovered_media_coverage',
               '_manual_qa_preview_passes'}
    _execute([node for node in TREE.body if isinstance(node, ast.FunctionDef)
              and node.name in helpers], namespace)
    namespace.update(changes)
    return namespace


def _select(namespace):
    selection = next(node for node in ast.walk(TREE) if isinstance(node, ast.If)
                     and isinstance(node.test, ast.Name)
                     and node.test.id == 'scene_repair_recovery'
                     and any(isinstance(child, ast.Name) and child.id == 'selected_runway'
                             for child in ast.walk(node)))
    _execute([selection], namespace)
    return namespace['selected_runway']


@pytest.mark.parametrize('recovered_score', [15, 86, 100])
@pytest.mark.parametrize('ranked', [[], [0, 4], [3], [0, 1, 2, 3, 4, 5]])
def test_exact_paid_scene_survives_stock_reranking_without_adding_new_scene(recovered_score, ranked):
    runtime = _runtime(ranked_runway_candidates=[{'scene_index': i} for i in ranked])
    runtime['current_reviews'][3]['score'] = recovered_score
    originals = copy.deepcopy((runtime['current_reviews'], runtime['scene_visuals']))
    selected = _select(runtime)
    assert [item['scene_index'] for item in selected] == [3]
    assert selected[0]['stock_score'] == recovered_score
    assert runtime['runway_required_submission_cap'] == runtime['runway_effective_submission_cap'] == 1
    assert (runtime['current_reviews'], runtime['scene_visuals']) == originals
    runtime['_validate_paid_create_allocation'](
        selected, runtime['recovered_generated_media'], 4, paid_slots_used=4)
    runtime['_require_recovered_media_coverage'](runtime['recovered_generated_media'], [3])
    assert runtime['runway_attempts'] == 4


def test_reuse_selects_all_and_only_recovered_indices_in_stable_order():
    runtime = _runtime()
    runtime['recovered_generated_media']['scenes'][1] = [_entry(1)]
    assert [item['scene_index'] for item in _select(runtime)] == [1, 3]


@pytest.mark.parametrize('changes', [
    {'recovered_generated_media': None},
    {'duration_minutes': 0.6},
    {'options': {'mode': 'production', 'format': 'landscape'}},
    {'options': {'mode': 'preview', 'format': 'shorts'}},
    {'options': {'mode': 'other', 'format': 'shorts'}},
])
def test_nonmatching_routes_retain_existing_ranked_selection(changes):
    runtime = _runtime(runway_attempts=0, **changes)
    assert _select(runtime) == runtime['ranked_runway_candidates']


def test_existing_v2_repair_selection_still_uses_its_exact_paid_union():
    runtime = _runtime(scene_repair_recovery=True, recovery_paid_scene_indices={1, 3},
                       prompt_candidates={1: 'repair', 3: 'recovered'})
    runtime['recovered_generated_media'] = {'version': 2, 'repair_only': True,
                                           'scenes': {3: ['saved']}, 'repair_scene_indices': [1]}
    assert [item['scene_index'] for item in _select(runtime)] == [1, 3]


def test_legacy_v1_keeps_old_ranked_selection_and_exact_coverage_failure():
    runtime = _runtime(runway_attempts=0)
    runtime['recovered_generated_media'] = {
        'version': 1, 'recovery_only': True, 'source_task_id': SOURCE_ID,
        'provider': 'gemini_veo', 'scenes': {3: [_entry(3)['key']]},
    }
    assert _select(runtime) == runtime['ranked_runway_candidates']
    with pytest.raises(RuntimeError, match='exactly match'):
        runtime['_require_recovered_media_coverage'](
            runtime['recovered_generated_media'], [item['scene_index'] for item in runtime['selected_runway']])


def test_exact_coverage_and_exhausted_budget_still_reject_unexpected_paid_indices():
    runtime = _runtime()
    with pytest.raises(RuntimeError, match='exactly match'):
        runtime['_require_recovered_media_coverage'](runtime['recovered_generated_media'], [0, 3])
    with pytest.raises(RuntimeError, match='before any submission'):
        runtime['_validate_paid_create_allocation'](
            [{'scene_index': 0}, {'scene_index': 3}],
            runtime['recovered_generated_media'], 4, paid_slots_used=4)


def _run_primary_loop(runtime, tmp_path):
    downloaded = []
    validated = []
    runtime.update(
        work=tmp_path, scene_durations=[5.0] * 6,
        download_file=lambda key, path: downloaded.append(key),
        _validate_recovered_generated_clip=lambda *args, **kwargs: validated.append(kwargs),
        _generated_visual_spec=lambda path, provider, provider_attempts: {
            'path': str(path), 'generation_provider': provider,
            'generation_provider_attempts': provider_attempts,
        },
        generated_checkpoint_specs={}, runway_scenes_used=0, runway_generated_scenes=[],
        generated_video_provider_records=[], recovery_repair_scene_indices=set(),
        omni_continuity_reference_image_path=None,
    )
    loop = next(node for node in ast.walk(TREE) if isinstance(node, ast.For)
                and isinstance(node.iter, ast.Name) and node.iter.id == 'selected_runway')
    _execute([loop], runtime)
    runtime['validated_clips'] = validated
    return downloaded


def test_actual_worker_reuses_clip_and_continues_before_any_paid_reservation(tmp_path):
    runtime = _runtime()
    _select(runtime)
    downloaded = _run_primary_loop(runtime, tmp_path)
    assert downloaded == [_entry(3)['key']]
    assert runtime['validated_clips'] == [{
        'minimum_duration': 5.35, 'expected_size': 4096, 'expected_sha256': 'a' * 64,
    }]
    assert runtime['runway_generated_scenes'] == [3]
    assert runtime['runway_attempts'] == 4
    assert runtime['scene_visuals'][3][0]['generation_recovered'] is True
    assert runtime['generated_video_provider_records'][0]['stage'] == 'recovered_generation'


def test_missing_recovered_entry_aborts_in_actual_worker_before_paid_call(tmp_path):
    runtime = _runtime()
    _select(runtime)
    runtime['recovered_generated_media']['scenes'][3] = []
    with pytest.raises(RuntimeError, match='scene is unavailable'):
        _run_primary_loop(runtime, tmp_path)


def test_recovery_keeps_final_paid_repair_disabled():
    runtime = _runtime(preview_runway_repair_indices=_forbidden)
    _execute([_named_assignment('final_runway_repair_candidates')], runtime)
    assert runtime['final_runway_repair_candidates'] == []


@pytest.mark.parametrize('missing', [False, True])
def test_normal_final_gate_rejects_bad_or_missing_stock_review_after_reuse(missing):
    runtime = _runtime()
    _select(runtime)
    runtime.update(final_reviews={i: {'score': 92} for i in range(6)},
                   manual_qa_preview_scenes=set(), rescued_final_scenes=[],
                   runway_failed_scenes=[], final_runway_repair_failures=[],
                   runway_failure_diagnostics=[],
                   _final_visual_rejection_diagnostics=lambda **kwargs: {
                       'rejected': kwargs['rejected_scene_indices']})
    if missing:
        runtime['final_reviews'].pop(0)
    else:
        runtime['final_reviews'][0]['score'] = 40
    _execute([_named_assignment('rejected_final_scenes')], runtime)
    assert runtime['rejected_final_scenes'] == [0]
    rejection = next(node for node in ast.walk(TREE) if isinstance(node, ast.If)
                     and isinstance(node.test, ast.Name) and node.test.id == 'rejected_final_scenes')
    with pytest.raises(RuntimeError, match='Final visual quality gate rejected'):
        _execute([rejection], runtime)
    assert runtime['_manual_qa_preview_passes'](
        runtime['options'], 0.5, runtime['scenes'][0], {'score': 85}, 'stock.mp4') is False


def test_bounded_free_stock_rescue_and_exact_review_are_not_skipped_for_recovery():
    rescue_loop = next(node for node in ast.walk(TREE) if isinstance(node, ast.For)
                       and isinstance(node.iter, ast.Name) and node.iter.id == 'rejected_final_scenes'
                       and any(isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                               and child.func.id == '_retry_bad_scene' for child in ast.walk(node)))
    rescue_review = next(node for node in ast.walk(TREE) if isinstance(node, ast.If)
                         and isinstance(node.test, ast.Name) and node.test.id == 'rescued_final_scenes')
    assert 'recovered_generated_media' not in ast.unparse(rescue_loop)
    assert 'review_scene_visuals(' in ast.unparse(rescue_review)
    assert 'recovered_generated_media' not in ast.unparse(rescue_review)
