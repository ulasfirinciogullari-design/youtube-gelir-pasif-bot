"""Exercise the worker's actual rescue block with local candidates and no I/O."""
import ast
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.services.visual_routing import should_rank_runway_candidate
from test_prepaid_visual_allocation import RESCUE, _namespace, _rescue_context


EVIDENCE = [{'url': 'https://example.org/report', 'evidence':
             'The warehouse retailer records annual membership fees separately from merchandise sales.'}]


def _context(count=6, cap=6, *, score=92, **changes):
    n = _namespace()
    n.update(_rescue_context(total_paid_create_cap=cap))
    scenes = [{'index': index, 'narration': 'A sourced business explanation.',
               'ai_prompt': None, 'visual_queries': ['warehouse merchandise shelves']}
              for index in range(6)]
    visuals = [[{'path': f'old-{index}.mp4', 'pexels_id': index + 1}] for index in range(6)]
    reviews = {index: {'score': 45 if index < count else 94,
                       'retry_queries': ['warehouse merchandise display']} for index in range(6)}
    n.update({
        'scenes': scenes, 'scene_visuals': visuals, 'stock_reuse_visuals': visuals,
        'current_reviews': reviews, 'scene_durations': [4.85] * 6,
        'package': {'sources': deepcopy(EVIDENCE)}, 'quality_threshold': 86,
        'seen_ids': set(), 'work': Path('/tmp/local-test'), 'credits': [],
        'pexels_orientation': 'portrait', 'visual_replacements': [],
        'self': object(), 'task_id': 'task', 'topic': 'Warehouse membership model',
        '_apply_visual_review': Mock(), 'set_stage': Mock(),
        '_retry_bad_scene': Mock(side_effect=lambda index, *args, **kwargs: [
            {'path': f'new-{index}.mp4', 'pexels_id': index + 100,
             'source_type': 'stock', 'stock_provider': 'pexels'},
        ]),
        'review_scene_visuals': Mock(side_effect=lambda selected, *args, **kwargs: {'reviews': [
            {'scene_index': position, 'score': score, 'best_candidate_index': 0,
             'subject_visible': score >= 86, 'spoken_action_visible': score >= 86,
             'pass': True, 'accepted': True}
            for position in range(len(selected))
        ]}),
    })
    n.update(changes)

    def rank():
        return {}, [
            {'scene_index': index, 'stock_score': review['score']}
            for index, review in n['current_reviews'].items()
            if should_rank_runway_candidate(
                n['options'], n['duration_minutes'],
                authored_ai_prompt=bool(n['scenes'][index].get('ai_prompt')),
                has_visual=bool(n['scene_visuals'][index]), stock_score=review['score'],
                quality_threshold=n['quality_threshold'],
            )
        ]

    n['rank_runway_candidates'] = Mock(side_effect=rank)
    n['prompt_candidates'], n['ranked_runway_candidates'] = rank()
    return n


def _run(n):
    exec(compile(ast.Module(body=[RESCUE], type_ignores=[]), '<actual-rescue-block>', 'exec'), n)


@pytest.mark.parametrize('count,cap', [(4, 6), (6, 6), (6, 2)])
def test_documentary_stock_gets_one_bounded_rescue_below_equal_and_above_cap(count, cap):
    n = _context(count, cap)
    old_scenes, old_sources = deepcopy(n['scenes']), deepcopy(n['package']['sources'])
    _run(n)
    retries = n['_retry_bad_scene'].call_args_list
    assert [call.args[0] for call in retries] == list(range(count))
    assert all(call.kwargs['max_replacements'] == 2 for call in retries)
    assert all(call.kwargs['allow_short_fallback'] is False for call in retries)
    assert all(call.kwargs['minimum_duration'] == pytest.approx(5.2) for call in retries)
    assert all(call.kwargs['orientation'] == 'portrait' for call in retries)
    assert all(call.kwargs['active_scene_visuals'] is n['stock_reuse_visuals'] for call in retries)
    n['review_scene_visuals'].assert_called_once()
    assert n['review_scene_visuals'].call_args.kwargs['evidence_sources'] is n['package']['sources']
    n['rank_runway_candidates'].assert_called_once()
    assert n['ranked_runway_candidates'] == []
    assert n['total_paid_create_cap'] == cap and n['quality_threshold'] == 86
    assert n['scenes'] == old_scenes and n['package']['sources'] == old_sources


def test_ai_authored_and_already_approved_stock_never_receive_new_rescue():
    n = _context(count=5)
    n['scenes'][0]['ai_prompt'] = 'An explicit generated mechanism.'
    _run(n)
    assert [call.args[0] for call in n['_retry_bad_scene'].call_args_list] == [1, 2, 3, 4]
    assert [candidate['scene_index'] for candidate in n['ranked_runway_candidates']] == [0]
    assert n['current_reviews'][5]['score'] == 94


@pytest.mark.parametrize('kind', ['non_documentary', 'invalid_evidence', 'preview'])
@pytest.mark.parametrize('count,cap', [(2, 6), (6, 6), (6, 2)])
def test_other_scopes_keep_overflow_only_provider_behavior(kind, count, cap):
    n = _context(count, cap)
    if kind == 'non_documentary': n['options']['content_style'] = 'technology'
    elif kind == 'invalid_evidence': n['package']['sources'] = [{'url': EVIDENCE[0]['url']}]
    else:
        n['options']['mode'] = 'preview'
        n['is_bounded_short_preview'] = True
        n['runway_submission_cap'] = cap
    _run(n)
    assert [call.args[0] for call in n['_retry_bad_scene'].call_args_list] == list(range(cap, count))
    assert n['review_scene_visuals'].call_count == (1 if count > cap else 0)
    assert n['rank_runway_candidates'].call_count == (1 if count > cap else 0)


@pytest.mark.parametrize('field,value', [
    ('scene_repair_recovery', True), ('provider_outage_stock_scenes', {0}),
    ('stock_quality_fallback_scenes', {0}),
])
def test_recovery_and_forced_fallback_guards_do_not_dispatch_another_rescue(field, value):
    n = _context(**{field: value})
    before = deepcopy(n['current_reviews'])
    _run(n)
    n['_retry_bad_scene'].assert_not_called()
    n['review_scene_visuals'].assert_not_called()
    n['rank_runway_candidates'].assert_not_called()
    assert n['current_reviews'] == before


@pytest.mark.parametrize('score', [25, 40, 85])
def test_failed_stock_reviews_are_not_promoted_by_accept_flags_or_higher_budget(score):
    n = _context(score=score)
    _run(n)
    assert len(n['ranked_runway_candidates']) == 6
    assert [row['stock_score'] for row in n['ranked_runway_candidates']] == [score] * 6
    assert all(review['pass'] is True and review['accepted'] is True for review in n['current_reviews'].values())
    assert n['quality_threshold'] == 86


def test_no_downloaded_candidates_means_no_review_rerank_or_invented_approval():
    n = _context()
    n['_retry_bad_scene'].side_effect = None
    n['_retry_bad_scene'].return_value = []
    before = deepcopy(n['current_reviews'])
    _run(n)
    assert n['_retry_bad_scene'].call_count == 6
    n['review_scene_visuals'].assert_not_called()
    n['rank_runway_candidates'].assert_not_called()
    assert n['current_reviews'] == before


def test_incomplete_rescue_review_still_stops_before_paid_generation():
    n = _context()
    n['review_scene_visuals'].side_effect = None
    n['review_scene_visuals'].return_value = {'reviews': []}
    with pytest.raises(RuntimeError, match='incomplete before any paid submission'):
        _run(n)
    n['rank_runway_candidates'].assert_not_called()
