"""Bounded stock-rescue tests with no worker imports or provider access."""

import ast
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import math
import re
from unittest.mock import Mock

import pytest


SOURCE = Path(__file__).resolve().parents[1] / 'app' / 'tasks.py'
TREE = ast.parse(SOURCE.read_text(encoding='utf-8'))
FUNCTIONS = {
    '_prepaid_stock_rescue_queries', '_select_ranked_broll_candidates',
    '_download_ranked_broll_candidates', '_retry_bad_scene', '_visual_path',
    '_prepaid_stock_rescue_candidates',
}


def _namespace():
    namespace = {
        'Path': Path, 're': re, 'math': math, 'ThreadPoolExecutor': ThreadPoolExecutor,
        'as_completed': as_completed, 'PexelsRetryError': RuntimeError,
        '_is_transient_pexels_provider_error': lambda exc: isinstance(exc, TimeoutError),
        'find_broll': Mock(), 'download_broll': Mock(),
    }
    definitions = [
        node for node in TREE.body
        if isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS
    ]
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(SOURCE), 'exec'), namespace)
    return namespace


def _clip(index, *, duration=8.0, width=1080, height=1920):
    return {
        'pexels_id': index, 'download_url': f'https://example.test/{index}.mp4',
        'duration': duration, 'width': width, 'height': height,
    }


def _query_results():
    return [
        ('first query', [_clip(1), _clip(2), _clip(3)]),
        ('second query', [
            _clip(90), _clip(91, duration=2),
            _clip(92, width=1920, height=1080), _clip(4), _clip(5),
        ]),
    ]


def test_critique_queries_are_deduplicated_and_never_supplemented_with_authored_hints():
    select = _namespace()['_prepaid_stock_rescue_queries']
    scene = {'visual_queries': ['original hint', 'another original hint']}
    assert select(scene, {'retry_queries': ['  Same   SHOT ', 'same shot', 'second shot', 'third shot']}) == [
        'Same SHOT', 'second shot',
    ]
    assert select(scene, {'retry_queries': ['one useful critique']}) == ['one useful critique']


@pytest.mark.parametrize('queries', [None, [], '', '  ', [None, 42, ''], {'bad': 'shape'}])
def test_authored_query_fallback_only_when_no_usable_critique_exists(queries):
    select = _namespace()['_prepaid_stock_rescue_queries']
    assert select(
        {'visual_queries': ['  dollar macro ', 'DOLLAR MACRO', 'hands examining dollar', 'third']},
        {'retry_queries': queries},
    ) == ['dollar macro', 'hands examining dollar']


def test_query_helper_accepts_a_single_phrase_but_does_not_invent_missing_queries():
    select = _namespace()['_prepaid_stock_rescue_queries']
    assert select({}, {'retry_queries': 'one complete search phrase'}) == ['one complete search phrase']
    assert select({'visual_queries': 'authored complete phrase'}, {}) == ['authored complete phrase']
    assert select({}, {}) == []


def test_diverse_first_pass_reaches_second_query_past_seen_short_and_wrong_orientation_hits():
    select = _namespace()['_select_ranked_broll_candidates']
    seen = {90}
    options = {
        'minimum_duration': 5.5, 'allow_seen_fallback': False,
        'allow_short_fallback': False, 'orientation': 'portrait',
    }
    ordinary = select(_query_results(), seen, 2, **options)
    rescue = select(_query_results(), seen, 2, query_diverse_first=True, **options)
    assert [(query, clip['pexels_id']) for query, clip in ordinary] == [('first query', 1), ('first query', 2)]
    assert [(query, clip['pexels_id']) for query, clip in rescue] == [('first query', 1), ('second query', 4)]
    assert seen == {90}


def test_query_diversity_never_duplicates_identity_or_relaxes_duration_or_orientation():
    select = _namespace()['_select_ranked_broll_candidates']
    result = select([
        ('first', [_clip(1)]),
        ('second', [_clip(1), _clip(2, duration=1), _clip(3, width=1920, height=1080), _clip(4)]),
    ], set(), 4, query_diverse_first=True, minimum_duration=6,
        allow_seen_fallback=False, allow_short_fallback=False, orientation='portrait')
    assert [clip['pexels_id'] for _, clip in result] == [1, 4]


@pytest.mark.parametrize('prefix, expected_queries', [
    ('pre_runway_budget_rescue', ['first query', 'second query']),
    ('qc', ['first query', 'first query']),
    ('final_qc_rescue', ['first query', 'first query']),
])
def test_query_diverse_download_is_scoped_to_existing_overflow_rescue(prefix, expected_queries, tmp_path):
    namespace = _namespace()
    by_query = dict(_query_results())
    namespace['find_broll'].side_effect = lambda query, limit, **kwargs: by_query[query]
    credits = []
    seen = {90}
    replacements = namespace['_retry_bad_scene'](
        0, list(by_query), seen, tmp_path, credits, file_prefix=prefix,
        max_replacements=2, minimum_duration=5.5,
        allow_short_fallback=False, orientation='portrait',
    )
    assert len(replacements) == 2
    assert [record['query'] for record in credits] == expected_queries
    assert namespace['find_broll'].call_count == 2
    assert namespace['download_broll'].call_count == 2
    assert all(call.kwargs['orientation'] == 'portrait' for call in namespace['find_broll'].call_args_list)


def test_two_success_target_still_has_only_two_transient_download_backfills(tmp_path):
    namespace = _namespace()
    by_query = dict(_query_results())
    namespace['find_broll'].side_effect = lambda query, limit, **kwargs: by_query[query]
    namespace['download_broll'].side_effect = TimeoutError('mock CDN unavailable')
    with pytest.raises(RuntimeError, match='Pexels retry download failed'):
        namespace['_retry_bad_scene'](
            0, list(by_query), {90}, tmp_path, [],
            file_prefix='pre_runway_budget_rescue', max_replacements=2,
            minimum_duration=5.5, allow_short_fallback=False, orientation='portrait',
        )
    assert namespace['find_broll'].call_count == 2
    assert namespace['download_broll'].call_count == 4


def test_ordinary_retry_default_remains_one_success(tmp_path):
    namespace = _namespace()
    by_query = dict(_query_results())
    namespace['find_broll'].side_effect = lambda query, limit, **kwargs: by_query[query]
    replacements = namespace['_retry_bad_scene'](
        0, list(by_query), {90}, tmp_path, [], orientation='portrait',
    )
    assert len(replacements) == 1
    assert namespace['download_broll'].call_count == 1


def test_overflow_call_keeps_two_new_clips_and_the_reviewed_incumbent(tmp_path):
    loop = next(
        node for node in ast.walk(TREE)
        if isinstance(node, ast.For) and isinstance(node.iter, ast.Name)
        and node.iter.id == 'overflow_candidates'
    )
    namespace = _namespace()
    incumbent = {'path': 'reviewed-incumbent.mp4'}
    replacements = [{'path': 'first-query.mp4'}, {'path': 'second-query.mp4'}]
    namespace.update({
        'overflow_candidates': [{'scene_index': 0}],
        'current_reviews': {0: {'score': 65, 'retry_queries': []}},
        'scenes': [{'visual_queries': ['first authored query', 'second authored query']}],
        'scene_visuals': [[incumbent]], 'scene_durations': [5.4],
        'stock_reuse_visuals': None,
        'budget_rescued_scenes': [], 'visual_replacements': [],
        'seen_ids': set(), 'work': tmp_path, 'credits': [],
        'pexels_orientation': 'portrait',
        '_retry_bad_scene': Mock(return_value=replacements),
    })
    exec(compile(ast.Module(body=[loop], type_ignores=[]), str(SOURCE), 'exec'), namespace)
    call = namespace['_retry_bad_scene'].call_args
    assert call.args[1] == ['first authored query', 'second authored query']
    assert call.kwargs['max_replacements'] == 2
    assert call.kwargs['allow_short_fallback'] is False
    assert call.kwargs['minimum_duration'] == pytest.approx(5.75)
    assert namespace['scene_visuals'] == [[*replacements, incumbent]]
    assert namespace['budget_rescued_scenes'] == [0]
    assert namespace['current_reviews'][0]['score'] == 65


def _rescue_selection(**overrides):
    context = {
        'options': {'mode': 'production', 'format': 'shorts', 'content_style': 'documentary'},
        'duration_minutes': 0.5,
        'scenes': [{'ai_prompt': None} for _ in range(6)],
        'evidence_sources': [{
            'url': 'https://www.bep.gov/currency',
            'evidence': 'U.S. currency paper is 75 percent cotton and 25 percent linen.',
        }],
        'quality_threshold': 86,
    }
    context.update(overrides)
    ranked = [
        {'scene_index': 3, 'stock_score': 35},
        {'scene_index': 5, 'stock_score': 65},
        {'scene_index': 4, 'stock_score': 68},
    ]
    return _namespace()['_prepaid_stock_rescue_candidates'](ranked, 2, **context)


def test_live_rank_order_rescues_reserved_dollar_scene_as_well_as_overflow_texture():
    assert [candidate['scene_index'] for candidate in _rescue_selection()] == [3, 5, 4]


@pytest.mark.parametrize('overrides', [
    {'options': {'mode': 'preview', 'format': 'shorts', 'content_style': 'documentary'}},
    {'options': {'mode': 'production', 'format': 'landscape', 'content_style': 'documentary'}},
    {'options': {'mode': 'production', 'format': 'shorts', 'content_style': 'technology'}},
    {'duration_minutes': 0.6},
    {'scenes': [{'ai_prompt': None} for _ in range(7)]},
    {'evidence_sources': None},
    {'evidence_sources': []},
    {'evidence_sources': [{'url': 'https://www.bep.gov/currency'}]},
    {'evidence_sources': [{'url': 'javascript:allow()', 'evidence': 'A concrete-looking sentence.'}]},
])
def test_other_scopes_preserve_overflow_only_rescue(overrides):
    assert [candidate['scene_index'] for candidate in _rescue_selection(**overrides)] == [4]


def test_passed_ai_invalid_and_duplicate_candidates_do_not_gain_a_stock_rescue():
    select = _namespace()['_prepaid_stock_rescue_candidates']
    rejected = {'scene_index': 2, 'stock_score': 65}
    ranked = [
        {'scene_index': 0, 'stock_score': 88},
        {'scene_index': 1, 'stock_score': 20},
        rejected, dict(rejected), {'scene_index': 7, 'stock_score': 10},
        {'scene_index': 3, 'stock_score': float('nan')},
    ]
    result = select(
        ranked, 2,
        options={'mode': 'production', 'format': 'shorts', 'content_style': 'documentary'},
        duration_minutes=0.5,
        scenes=[{'ai_prompt': None}, {'ai_prompt': 'Required generated mechanism.'},
                {'ai_prompt': None}, {'ai_prompt': None}],
        evidence_sources=[{'url': 'https://www.bep.gov/currency', 'evidence': 'The banknote contains cotton and linen.'}],
        quality_threshold=86,
    )
    assert result == [rejected]
