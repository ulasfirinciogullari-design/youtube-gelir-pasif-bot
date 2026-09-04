import ast
import json
import math
from pathlib import Path
from unittest.mock import Mock

import pytest


SOURCE_PATH = Path(__file__).resolve().parents[1] / 'app' / 'tasks.py'
TREE = ast.parse(SOURCE_PATH.read_text(encoding='utf-8'))
RESCUE = next(
    node for node in ast.walk(TREE)
    if isinstance(node, ast.If) and node.body
    and isinstance(node.body[0], ast.Assign)
    and any(isinstance(target, ast.Name) and target.id == 'overflow_candidates' for target in node.body[0].targets)
)


def _namespace():
    names = {'_validate_paid_create_allocation', '_record_prepaid_visual_diagnostics', '_visual_path'}
    functions = [node for node in TREE.body if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = {
        'math': math, 'json': json, 'update_job': Mock(),
        'FinalVisualQualityError': RuntimeError, 'PreRunwayRetryableError': RuntimeError,
    }
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(SOURCE_PATH), 'exec'), namespace)
    return namespace


def _rescue_context(**overrides):
    context = {
        'is_bounded_short_preview': False,
        'options': {'mode': 'production', 'format': 'shorts', 'content_style': 'documentary'},
        'duration_minutes': 0.5, 'total_paid_create_cap': 2,
        'scene_repair_recovery': False, 'provider_outage_stock_scenes': set(),
        'stock_quality_fallback_scenes': set(), 'runway_submission_cap': 2,
        'ranked_runway_candidates': [{'scene_index': index} for index in range(3)],
    }
    context.update(overrides)
    return context


def test_initial_threadpool_visual_review_forwards_documentary_evidence():
    calls = [
        node for node in ast.walk(TREE)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == 'submit' and node.args
        and isinstance(node.args[0], ast.Name) and node.args[0].id == 'review_scene_visuals'
    ]
    assert len(calls) == 1
    keywords = {keyword.arg: keyword.value for keyword in calls[0].keywords}
    sources = [{'url': 'https://www.bep.gov/currency', 'evidence': 'Verified currency composition.'}]
    context = {'options': {'content_style': 'documentary'}, 'package': {'sources': sources}}
    assert eval(compile(ast.Expression(keywords['content_style']), '<style>', 'eval'), context) == 'documentary'
    assert eval(compile(ast.Expression(keywords['evidence_sources']), '<evidence>', 'eval'), context) is sources


@pytest.mark.parametrize('overrides, allowed', [
    ({}, True),
    ({'is_bounded_short_preview': True, 'options': {'mode': 'preview'}}, True),
    ({'options': {'mode': 'production', 'format': 'landscape'}}, False),
    ({'options': {'mode': 'preview'}}, False),
    ({'total_paid_create_cap': None}, False),
    ({'total_paid_create_cap': 0}, False),
    ({'total_paid_create_cap': True}, False),
    ({'duration_minutes': 1.0}, False),
    ({'scene_repair_recovery': True}, False),
    ({'provider_outage_stock_scenes': {2}}, False),
    ({'stock_quality_fallback_scenes': {2}}, False),
    ({'ranked_runway_candidates': [{'scene_index': 0}, {'scene_index': 1}]}, False),
])
def test_only_existing_preview_or_capped_production_short_enters_budget_rescue(overrides, allowed):
    assert bool(eval(compile(ast.Expression(RESCUE.test), '<rescue-condition>', 'eval'), _rescue_context(**overrides))) is allowed


@pytest.mark.parametrize('rescued_score, accepted', [(92, True), (70, False)])
def test_production_overflow_rescue_must_pass_unchanged_quality_and_cap(rescued_score, accepted):
    namespace = _namespace()
    namespace.update(_rescue_context())
    sources = [{'url': 'https://www.bep.gov/currency', 'evidence': 'Verified currency composition.'}]
    reviews = {index: {'score': score, 'retry_queries': ['banknote texture']} for index, score in enumerate((20, 30, 70, 95))}
    visuals = [[{'path': f'incumbent-{index}.mp4'}] for index in range(4)]
    namespace.update({
        'current_reviews': reviews, 'scene_visuals': visuals,
        'scene_durations': [4.8] * 4, 'scenes': [{'narration': str(index)} for index in range(4)],
        'seen_ids': set(), 'work': Path('/tmp/test-work'), 'credits': [],
        'pexels_orientation': 'portrait', 'visual_replacements': [],
        '_retry_bad_scene': Mock(return_value=[{'path': 'new-stock.mp4'}]),
        'review_scene_visuals': Mock(return_value={'reviews': [{'scene_index': 0, 'score': rescued_score}]}),
        '_apply_visual_review': Mock(), 'set_stage': Mock(),
        'self': object(), 'task_id': 'test-task', 'topic': 'Currency paper',
        'package': {'sources': sources},
    })

    def rank():
        return {}, [
            {'scene_index': index}
            for index in range(4)
            if not visuals[index] or reviews[index]['score'] < 86
        ]

    namespace['rank_runway_candidates'] = Mock(side_effect=rank)
    exec(compile(ast.Module(body=[RESCUE], type_ignores=[]), '<rescue>', 'exec'), namespace)
    retry = namespace['_retry_bad_scene']
    retry.assert_called_once()
    assert retry.call_args.args[0] == 2
    assert retry.call_args.kwargs['allow_short_fallback'] is False
    assert retry.call_args.kwargs['minimum_duration'] == pytest.approx(5.15)
    reviewer = namespace['review_scene_visuals']
    reviewer.assert_called_once()
    assert reviewer.call_args.kwargs['content_style'] == 'documentary'
    assert reviewer.call_args.kwargs['evidence_sources'] is sources
    ranked = namespace['ranked_runway_candidates']
    assert len(ranked) == (2 if accepted else 3)
    if accepted:
        namespace['_validate_paid_create_allocation'](ranked, None, 2)
    else:
        with pytest.raises(RuntimeError, match='job-wide paid-create cap'):
            namespace['_validate_paid_create_allocation'](ranked, None, 2)


def test_prepaid_diagnostics_keep_only_safe_scene_facts_before_cap_failure():
    namespace = _namespace()
    ranked = [{'scene_index': index} for index in range(3)]
    namespace['_record_prepaid_visual_diagnostics'](
        'task', [[{'path': 'https://secret.example/?token=SECRET'}], [], [{'path': '/private/file.mp4'}]],
        {0: {'score': 70, 'reason': 'SECRET raw response'}, 1: {'score': 20}, 2: {'score': float('nan')}},
        ranked, 2, paid_slots_used=0, quality_threshold=86,
    )
    snapshot = namespace['update_job'].call_args.kwargs['prepaid_visual_diagnostics']
    assert snapshot['paid_slots_remaining'] == 2
    assert snapshot['quality_threshold'] == 86
    assert snapshot['scenes'] == [
        {'scene_index': 0, 'score': 70.0, 'has_visual': True, 'requires_paid_replacement': True, 'reason': 'below_quality_threshold'},
        {'scene_index': 1, 'score': 20.0, 'has_visual': False, 'requires_paid_replacement': True, 'reason': 'missing_visual'},
        {'scene_index': 2, 'score': -1, 'has_visual': True, 'requires_paid_replacement': True, 'reason': 'missing_review'},
    ]
    assert all(secret not in json.dumps(snapshot) for secret in ('SECRET', 'https://', '/private', 'raw response'))
    with pytest.raises(RuntimeError, match='job-wide paid-create cap'):
        namespace['_validate_paid_create_allocation'](ranked, None, 2)


def test_runtime_persists_diagnostics_before_any_final_allocation_branch():
    calls = [node for node in ast.walk(TREE) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
    diagnostic = next(node for node in calls if node.func.id == '_record_prepaid_visual_diagnostics')
    runtime = next(node for node in TREE.body if isinstance(node, ast.FunctionDef) and node.name == 'run_video_pipeline')
    runtime_allocation_calls = [
        node for node in ast.walk(runtime)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == '_validate_paid_create_allocation'
    ]
    assert len(runtime_allocation_calls) == 2
    assert all(diagnostic.lineno < node.lineno for node in runtime_allocation_calls)


def test_diagnostic_storage_failure_does_not_approve_an_over_budget_plan():
    namespace = _namespace()
    namespace['update_job'].side_effect = RuntimeError('secret storage response')
    ranked = [{'scene_index': index} for index in range(3)]
    namespace['_record_prepaid_visual_diagnostics']('task', [[], [], []], {}, ranked, 2, paid_slots_used=0, quality_threshold=86)
    with pytest.raises(RuntimeError, match='job-wide paid-create cap'):
        namespace['_validate_paid_create_allocation'](ranked, None, 2)
