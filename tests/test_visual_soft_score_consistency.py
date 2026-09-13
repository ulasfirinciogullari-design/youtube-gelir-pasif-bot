import ast
from pathlib import Path
import re
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


SOURCE = Path(__file__).resolve().parents[1] / 'app' / 'services' / 'visual_qc.py'
TREE = ast.parse(SOURCE.read_text(encoding='utf-8'))
CONSTANTS = {
    '_CLEARLY_POSITIVE_REASON_PATTERNS', '_NEGATIVE_REASON_MARKERS',
    '_SOFT_POSITIVE_DESCRIPTION_PATTERN', '_SOFT_REASON_CRITICISM_PATTERN',
}
FUNCTIONS = {
    '_clearly_positive_review_reason', '_score_reason_conflicts',
    '_mark_unresolved_score_reason_conflict', '_spec_path',
}
REVALIDATION = next(
    node for node in ast.walk(TREE)
    if isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
    and isinstance(node.test.left, ast.Name)
    and node.test.left.id == '_score_reason_consistency_attempts'
)


def _load():
    definitions = [
        node for node in TREE.body
        if (isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS)
        or (isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in CONSTANTS for target in node.targets))
    ]
    namespace = {'re': re}
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(SOURCE), 'exec'), namespace)
    return namespace


def _review(score=68, reason='Shows a hand presenting a US dollar bill clearly matching the narration context.', **overrides):
    review = {
        'scene_index': 0, 'best_candidate_index': 1, 'score': score,
        'raw_score': score, 'reason': reason, 'retry_queries': [],
        'evidence_gate_passed': True, 'identity_gate_passed': True,
        'editorial_gate_passed': True,
    }
    review.update(overrides)
    return review


def _run_revalidation(initial, response, *, sources=True, authored_ai=False):
    namespace = _load()
    scene = {'narration': 'The banknote contains cotton.', 'ai_prompt': 'Authored generation' if authored_ai else None}
    selected = {'path': 'exact-selected.mp4', 'source_type': 'stock', 'start_fraction': 0.5}
    evidence = [{'url': 'https://www.bep.gov/currency', 'evidence': 'U.S. currency paper is 75% cotton and 25% linen.'}] if sources else []
    namespace.update({
        'settings': SimpleNamespace(openai_api_key=''),
        '_score_reason_consistency_attempts': 1,
        'reviews_by_scene': {0: initial},
        'scenes': [scene], 'scene_visuals': [[{'path': 'unselected.mp4'}, selected]],
        'documentary_sources': evidence, 'work': Path('/tmp/qa'),
        'topic': 'Currency paper', 'complete_story': [scene],
        'content_style': 'documentary', 'gemini_model_override': None,
        'provider': 'gemini', '_gemini_thinking_level': 'low',
        'review_scene_visuals': Mock(return_value=response),
    })
    exec(compile(ast.Module(body=[REVALIDATION], type_ignores=[]), '<revalidation>', 'exec'), namespace)
    return namespace, selected


@pytest.mark.parametrize('score, reason, retry_queries', [
    (68, 'Shows a hand presenting a US dollar bill clearly matching the narration context.', []),
    (70, 'Macro close-up shows hands holding and examining US dollar bills as stated.', []),
    (72, 'Clearly shows pure raw white cotton bolls and fibers in hand.', []),
    (68, 'Correctly shows hands holding and handling US dollar bills as stated in the narration.', [
        'person pulling US dollar bills from wallet',
        'close up hands taking dollar bills from wallet',
    ]),
    (70, 'Macro shot clearly displays the distinctive surface texture and paper blend of genuine US currency.', [
        'macro close up authentic us dollar paper texture',
        'extreme close up genuine dollar banknote paper fibers',
    ]),
])
def test_observed_positive_soft_rejections_trigger_only_independent_exact_media_review(score, reason, retry_queries):
    initial = _review(score, reason, retry_queries=retry_queries)
    second = _review(92, 'The exact material and narration match.', best_candidate_index=0)
    namespace, selected = _run_revalidation(initial, {'reviews': [second]})
    reviewer = namespace['review_scene_visuals']
    reviewer.assert_called_once()
    assert reviewer.call_args.args[1] == [[selected]]
    assert reviewer.call_args.kwargs['_score_reason_consistency_attempts'] == 0
    assert reviewer.call_args.kwargs['_missing_review_attempts'] == 0
    assert reviewer.call_args.kwargs['_gemini_thinking_level'] == 'medium'
    assert reviewer.call_args.kwargs['evidence_sources'] is namespace['documentary_sources']
    assert reason not in repr(reviewer.call_args)
    result = namespace['reviews_by_scene'][0]
    assert result['score'] == 92
    assert result['best_candidate_index'] == 1
    assert result['score_reason_initial_score'] == score
    assert result['score_reason_consistency_passed'] is True
    assert initial['score'] == score


@pytest.mark.parametrize('override', [
    {'retry_queries': None},
    {'retry_queries': 'not a list'},
    {'retry_queries': [None]},
    {'retry_queries': ['']},
    {'retry_queries': ['x' * 241]},
    {'retry_queries': ['one', 'two', 'three']},
    {'evidence_gate_passed': False},
    {'identity_gate_passed': False},
    {'editorial_gate_passed': False},
    {'score': 85, 'raw_score': 96},
    {'score': 86, 'raw_score': 86},
    {'score': '68'},
    {'reason': 'Clearly shows cotton but the texture is blurry.'},
    {'reason': 'Clearly shows cotton; dull lighting needs improvement.'},
    {'reason': 'Shows a close-up woven flax/linen fabric texture matching the narration topic, though not raw unspun fibers.'},
    {'reason': 'This is merely an adequate candidate.'},
])
def test_malformed_queries_criticism_hard_gates_or_intentional_caps_do_not_trigger_soft_review(override):
    namespace, _ = _run_revalidation(_review(**override), {'reviews': []})
    namespace['review_scene_visuals'].assert_not_called()


def test_missing_query_list_does_not_trigger_soft_review():
    initial = _review()
    del initial['retry_queries']
    namespace, _ = _run_revalidation(initial, {'reviews': []})
    namespace['review_scene_visuals'].assert_not_called()


@pytest.mark.parametrize('sources, authored_ai', [(False, False), (True, True)])
def test_new_trigger_stays_in_source_backed_documentary_stock_scope(sources, authored_ai):
    namespace, _ = _run_revalidation(_review(), {'reviews': []}, sources=sources, authored_ai=authored_ai)
    namespace['review_scene_visuals'].assert_not_called()


@pytest.mark.parametrize('response', [
    {'reviews': []},
    {'reviews': [_review(best_candidate_index=0)]},
    {'reviews': [_review(best_candidate_index=0, retry_queries=['another dollar close up'])]},
])
def test_missing_or_still_conflicting_second_verdict_is_not_promoted(response):
    namespace, _ = _run_revalidation(_review(), response)
    namespace['review_scene_visuals'].assert_called_once()
    result = namespace['reviews_by_scene'][0]
    assert result['score'] <= 40
    assert result['score_reason_consistency_passed'] is False


def test_consistent_second_soft_rejection_remains_below_publish_threshold():
    second = _review(62, 'The framing is weak for this narration.', best_candidate_index=0,
                     retry_queries=['cotton bolls filling the frame'])
    namespace, _ = _run_revalidation(_review(), {'reviews': [second]})
    result = namespace['reviews_by_scene'][0]
    assert result['score'] == 62
    assert result['score_reason_consistency_passed'] is True
    assert result['score'] < 86


def test_second_hard_failure_cannot_be_overridden_by_initial_positive_prose():
    second = _review(40, 'The named material is not visible.', best_candidate_index=0,
                     evidence_gate_passed=False, retry_queries=['genuine raw cotton bolls'])
    namespace, _ = _run_revalidation(_review(), {'reviews': [second]})
    result = namespace['reviews_by_scene'][0]
    assert result['score'] == 40
    assert result['evidence_gate_passed'] is False


def test_existing_hard_conflict_path_does_not_require_new_documentary_scope():
    namespace, _ = _run_revalidation(_review(40), {'reviews': []}, sources=False)
    namespace['review_scene_visuals'].assert_called_once()


def test_soft_rejection_prompt_requires_visible_shortfall_not_stock_bias_or_queries_alone():
    prompt_source = SOURCE.with_name('strict_visual_review_semantics.py')
    prompt_tree = ast.parse(prompt_source.read_text(encoding='utf-8'))
    function = next(
        node for node in prompt_tree.body
        if isinstance(node, ast.FunctionDef) and node.name == '_rubric_content'
    )
    content = next(
        node for node in function.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        and node.target.id == 'content'
    )
    instruction = ast.literal_eval(content.value)[0]['text']
    assert 'Any soft rejection from 41 through 85 must name a concrete visible shortfall' in instruction
    assert 'replacement search queries alone do not explain a failure' in instruction
    assert 'an entirely positive reason cannot justify rejection merely because the footage is stock or B-roll' in instruction
    assert 'A score of 86+ means the chosen moment is genuinely publishable' in instruction
    assert 'If a hard gate forces the score to 40 or lower, explicitly name that failed gate' in instruction
