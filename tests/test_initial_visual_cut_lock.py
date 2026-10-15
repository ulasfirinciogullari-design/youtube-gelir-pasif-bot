"""Initial candidate ranking must not move an already committed source cut."""
import ast
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest


SOURCE = Path(__file__).resolve().parents[1] / 'app/tasks.py'
TREE = ast.parse(SOURCE.read_text())
LOOP = next(n for n in ast.walk(TREE) if isinstance(n, ast.For)
            and isinstance(n.target, ast.Tuple)
            and [getattr(x, 'id', None) for x in n.target.elts] == ['scene_idx', '_scene'])


def rank(pool, review):
    definitions = [n for n in TREE.body if isinstance(n, ast.FunctionDef)
                   and n.name in {'_visual_path', '_apply_visual_review'}]
    ns = {}
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(SOURCE), 'exec'), ns)
    original = deepcopy(pool)
    reviews = {0: review} if review is not None else {}
    ns.update(scenes=[{'narration': 'A retailer displays toys.', 'ai_prompt': None}],
              scene_visuals=[deepcopy(pool)], reviews_by_scene=deepcopy(reviews),
              strict_short_preview_duration=False, quality_threshold=86,
              options={'mode': 'production', 'format': 'landscape'}, duration_minutes=3,
              scene_durations=[6.2], seen_ids=set(), work=Path('/tmp/unused'), credits=[],
              pexels_orientation='landscape', stock_reuse_visuals=[],
              visual_replacements=[], _retry_bad_scene=Mock(return_value=[]))
    exec(compile(ast.Module(body=[LOOP], type_ignores=[]), str(SOURCE), 'exec'), ns)
    assert pool == original and ns['reviews_by_scene'] == reviews
    return ns


@pytest.mark.parametrize('locked_fraction', [0.0, .35])
@pytest.mark.parametrize('score', [None, 40, 68, 88])
@pytest.mark.parametrize('review_fraction', [.18, .5, .82])
def test_existing_cut_survives_positive_negative_and_missing_initial_review(locked_fraction, score, review_fraction):
    clip = {'path': 'retained.mp4', 'start_fraction': locked_fraction,
            'preserve_start_fraction': True, 'forbid_loop': True,
            'source_type': 'generated', 'generation_provider': 'fal_seedance_15_pro'}
    review = (None if score is None else {'score': score, 'best_candidate_index': 0,
                                         'best_start_fraction': review_fraction, 'retry_queries': []})
    ns = rank([clip], review)
    assert ns['scene_visuals'] == [[clip]]
    assert ns['_retry_bad_scene'].call_count == int(score is not None and score < 86)


def test_candidate_choice_and_real_rejection_still_apply_to_pinned_clips():
    pool = [{'path': 'other.mp4', 'start_fraction': .2},
            {'path': 'retained.mp4', 'start_fraction': 0., 'preserve_start_fraction': True}]
    review = {'score': 30, 'best_candidate_index': 1, 'best_start_fraction': .82,
              'retry_queries': ['different toy shop'], 'reason': 'The frame is blurred.'}
    ns = rank(pool, review)
    assert ns['scene_visuals'] == [[pool[1]]]
    ns['_retry_bad_scene'].assert_called_once()
    assert ns['_retry_bad_scene'].call_args.args[1] == ['different toy shop']
    assert ns['reviews_by_scene'][0]['score'] == 30


@pytest.mark.parametrize('score,fraction,expected', [(92, .82, .82), (30, .5, .5), (None, None, .25)])
def test_unpinned_stock_keeps_normal_review_selection(score, fraction, expected):
    pool = [{'path': 'stock.mp4', 'start_fraction': .12}]
    review = None if score is None else {'score': score, 'best_candidate_index': 0,
                                        'best_start_fraction': fraction, 'retry_queries': []}
    assert rank(pool, review)['scene_visuals'][0][0]['start_fraction'] == expected


def test_zero_start_keeps_the_full_generated_action_speed_contract():
    from app.services.render import _clip_speed
    clip = {'path': 'retained.mp4', 'start_fraction': 0., 'preserve_start_fraction': True,
            'forbid_loop': True, 'generated': True, 'source_type': 'generated',
            'generation_provider': 'fal_seedance_15_pro', 'source_media_type': 'video',
            'synthetic_motion_only': False}
    selected = rank([clip], {'score': 88, 'best_candidate_index': 0,
                            'best_start_fraction': .82})['scene_visuals'][0][0]
    assert _clip_speed(selected, 7., 6.2, 16) == _clip_speed(clip, 7., 6.2, 16)
    assert _clip_speed(selected, 7., 6.2, 16) > _clip_speed({**clip, 'start_fraction': .82}, 7., 6.2, 16)
