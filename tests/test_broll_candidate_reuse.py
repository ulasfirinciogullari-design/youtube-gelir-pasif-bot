"""Release discarded cross-scene stock candidates, never selected footage or QA."""

import ast
import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import re

import pytest


SOURCE = Path(__file__).resolve().parents[1] / 'app' / 'tasks.py'
TREE = ast.parse(SOURCE.read_text(encoding='utf-8'))


def _execute(nodes, namespace):
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), namespace)


@pytest.fixture
def helpers():
    names = {'_select_ranked_broll_candidates', '_collect_broll',
             '_download_ranked_broll_candidates', '_retry_bad_scene',
             '_visual_path', '_apply_visual_review', '_broll_retry_blocked_ids',
             '_require_unique_selected_stock'}
    namespace = {'Path': Path, 're': re, 'ThreadPoolExecutor': ThreadPoolExecutor,
                 'as_completed': as_completed, 'PexelsRetryError': RuntimeError,
                 'FinalVisualQualityError': RuntimeError}
    _execute([node for node in TREE.body if isinstance(node, ast.FunctionDef)
              and node.name in names], namespace)
    return namespace


def _item(identifier):
    return {'pexels_id': identifier, 'download_url': f'https://example.invalid/{identifier}',
            'duration': 9, 'width': 1080, 'height': 1920}


def _spec(identifier):
    return {'path': f'stock-{identifier}.mp4', 'pexels_id': identifier,
            'source_type': 'stock', 'stock_provider': 'pexels'}


def _credit(identifier, owner):
    return {'source': 'Pexels', 'pexels_id': identifier, 'scene_index': owner}


def _six_scene_pool(helpers, tmp_path):
    pools = {'q0': [101, 102, 103], 'q1': [201, 202, 203],
             'q2': [301, 302, 303], 'q3': [401, 402, 403],
             'q4': [101, 102, 104], 'q5': [501, 502, 503],
             'texture_retry': [101, 102]}
    helpers['find_broll'] = lambda query, *args, **kwargs: [_item(i) for i in pools[query]]
    helpers['download_broll'] = lambda item, path: str(path)
    scenes = [{'visual_queries': [f'q{i}']} for i in range(6)]
    result = helpers['_collect_broll'](scenes, tmp_path, strict_duration=True, orientation='portrait')
    assert [[spec['pexels_id'] for spec in specs] for specs in result['scene_visuals']] == [
        [101, 102, 103], [201, 202, 203], [301, 302, 303],
        [401, 402, 403], [104], [501, 502, 503],
    ]
    for i in range(6):
        helpers['_apply_visual_review'](result['scene_visuals'], i, {
            'best_candidate_index': 2 if i == 0 else 0, 'score': 90,
        })
    return result


@pytest.mark.parametrize('reverse_completion', [False, True])
def test_six_scene_unused_usd_macros_reach_later_texture_scene(helpers, tmp_path, reverse_completion):
    if reverse_completion:
        helpers['as_completed'] = lambda futures: reversed(list(futures))
    result = _six_scene_pool(helpers, tmp_path)
    active = result['scene_visuals']
    assert [specs[0]['pexels_id'] for specs in active] == [103, 201, 301, 401, 104, 501]
    assert {101, 102}.issubset(result['seen_ids'])
    args = (4, ['texture_retry'], result['seen_ids'], tmp_path, result['credits'])
    # Legacy behavior remains unchanged when the scoped live-pool view is absent.
    assert helpers['_retry_bad_scene'](*args, orientation='portrait') == []
    old_pool = copy.deepcopy(active)
    replacements = helpers['_retry_bad_scene'](
        *args, file_prefix='final_qc_rescue', orientation='portrait',
        active_scene_visuals=active,
    )
    assert [spec['pexels_id'] for spec in replacements] == [101]
    assert active == old_pool  # Selection is not a review, approval, or pool mutation.
    assert 'score' not in replacements[0]
    assert result['credits'][-1]['scene_index'] == 4
    assert result['credits'][-1]['pexels_id'] == 101
    active[4] = replacements
    helpers['_require_unique_selected_stock'](active)
    assert len({specs[0]['pexels_id'] for specs in active}) == 6


def test_newly_reassigned_id_is_blocked_for_every_other_scene(helpers):
    active = [[_spec(103)], [_spec(101)], [], [], [], []]
    credits = [_credit(101, 0), _credit(102, 0), _credit(101, 1)]
    seen = {101, 102, 103, 999}
    before = copy.deepcopy((active, credits, seen))
    blocked = helpers['_broll_retry_blocked_ids'](4, seen, credits, active)
    assert blocked == {101, 103, 999}
    assert (active, credits, seen) == before


def test_all_current_candidates_are_protected_not_only_each_best_clip(helpers):
    active = [[_spec(103), _spec(101)], [], [], [], [], []]
    blocked = helpers['_broll_retry_blocked_ids'](
        4, {101, 102, 103}, [_credit(101, 0), _credit(102, 0)], active)
    assert blocked == {101, 103}


def test_own_previously_attempted_candidates_never_reappear(helpers, tmp_path):
    result = _six_scene_pool(helpers, tmp_path)
    active = result['scene_visuals']
    args = (4, ['texture_retry'], result['seen_ids'], tmp_path, result['credits'])
    first = helpers['_retry_bad_scene'](*args, orientation='portrait', active_scene_visuals=active)
    assert first[0]['pexels_id'] == 101
    # Even when a rejected retry is no longer in any active pool, it stays
    # attempted for scene4; scene0 also cannot repeat its original 101/102.
    active[4] = []
    second = helpers['_retry_bad_scene'](*args, orientation='portrait', active_scene_visuals=active)
    assert second[0]['pexels_id'] == 102
    third = helpers['_retry_bad_scene'](*args, orientation='portrait', active_scene_visuals=active)
    assert third == []
    assert helpers['_broll_retry_blocked_ids'](0, result['seen_ids'], result['credits'], active) >= {101, 102, 103}


@pytest.mark.parametrize('legacy', [
    'legacy.mp4', {'path': 'legacy.mp4'}, _spec(None), _spec(True), _spec('101'),
    dict(_spec(101), stock_provider='unknown'),
])
def test_unknown_live_stock_identity_keeps_existing_exclusion(helpers, legacy):
    blocked = helpers['_broll_retry_blocked_ids'](
        1, {101, 102, 'unknown-url-key'}, [_credit(101, 0), _credit(102, 0)], [[legacy], []])
    assert blocked == {101, 102, 'unknown-url-key'}


def test_generated_clip_does_not_hide_discarded_stock_ownership(helpers):
    active = [[{'path': 'recovered.mp4', 'source_type': 'generated', 'generated': True}], []]
    assert helpers['_broll_retry_blocked_ids'](1, {101}, [_credit(101, 0)], active) == set()


@pytest.mark.parametrize('credit', [
    {'source': 'Other', 'pexels_id': 101, 'scene_index': 0},
    _credit('101', 0), _credit(True, 0), _credit(101, True),
    _credit(101, -1), _credit(101, 99), {}, None,
])
def test_missing_or_untrusted_download_history_never_releases_ids(helpers, credit):
    assert helpers['_broll_retry_blocked_ids'](1, {101}, [credit], [[], []]) == {101}


def test_new_duplicate_final_stock_ids_fail_without_approving_legacy_assets(helpers):
    with pytest.raises(RuntimeError, match='uniqueness gate'):
        helpers['_require_unique_selected_stock']([[_spec(101)], [_spec(101)]])
    helpers['_require_unique_selected_stock']([[_spec(101), _spec(101)], [_spec(102)]])
    helpers['_require_unique_selected_stock']([['legacy.mp4'], ['legacy.mp4']])
    helpers['_require_unique_selected_stock']([[_spec(None)], [_spec(None)]])


@pytest.mark.parametrize('options,duration,enabled', [
    ({'mode': 'production', 'format': 'shorts'}, 0.5, True),
    ({'mode': 'production', 'format': 'shorts'}, 0.6, False),
    ({'mode': 'production', 'format': 'landscape'}, 0.5, False),
    ({'mode': 'preview', 'format': 'shorts'}, 0.5, False),
    ({'mode': 'production'}, 0.5, False),
])
def test_actual_worker_scopes_release_and_unique_gate_to_fixed_production_short(options, duration, enabled):
    assignment = next(node for node in ast.walk(TREE) if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == 'stock_reuse_visuals'
                              for target in node.targets))
    calls = []
    namespace = {'options': options, 'duration_minutes': duration,
                 'scene_visuals': [[_spec(101)]],
                 '_require_unique_selected_stock': lambda specs: calls.append(specs)}
    _execute([assignment], namespace)
    assert (namespace['stock_reuse_visuals'] is namespace['scene_visuals']) is enabled
    gate = next(node for node in ast.walk(TREE) if isinstance(node, ast.If)
                and ast.unparse(node.test) == 'stock_reuse_visuals is not None')
    _execute([gate], namespace)
    assert bool(calls) is enabled


def test_worker_forwards_live_pools_at_each_production_retry_and_keeps_qc():
    retry_calls = [node for node in ast.walk(TREE) if isinstance(node, ast.Call)
                   and isinstance(node.func, ast.Name) and node.func.id == '_retry_bad_scene'
                   and any(keyword.arg == 'active_scene_visuals' for keyword in node.keywords)]
    assert len(retry_calls) == 3  # initial QC retry, bounded budget rescue, final rescue
    for call in retry_calls:
        assert next(ast.unparse(keyword.value) for keyword in call.keywords
                    if keyword.arg == 'active_scene_visuals') == 'stock_reuse_visuals'
    rescue = next(node for node in ast.walk(TREE) if isinstance(node, ast.If)
                  and isinstance(node.test, ast.Name) and node.test.id == 'rescued_final_scenes')
    assert 'review_scene_visuals(' in ast.unparse(rescue)
    rejection = next(node for node in ast.walk(TREE) if isinstance(node, ast.If)
                     and isinstance(node.test, ast.Name) and node.test.id == 'rejected_final_scenes')
    assert 'raise FinalVisualQualityError(' in ast.unparse(rejection)
    paid_calls = [node for node in ast.walk(TREE) if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Name) and node.func.id == 'generate_scene']
    assert len(paid_calls) == 2  # No new paid-media path was added by stock reuse.
