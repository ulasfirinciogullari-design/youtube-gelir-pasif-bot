import ast
import re
from pathlib import Path


SOURCE_PATH = Path(__file__).resolve().parents[1] / 'app' / 'tasks.py'


def _load_boundary():
    tree = ast.parse(SOURCE_PATH.read_text(encoding='utf-8'))
    names = {
        '_runway_failure_payload',
        '_final_visual_rejection_diagnostics',
    }
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    namespace = {'re': re}
    exec(
        compile(
            ast.Module(body=functions, type_ignores=[]),
            str(SOURCE_PATH),
            'exec',
        ),
        namespace,
    )
    return namespace['_final_visual_rejection_diagnostics']


def _quota_failure(stage):
    return {
        'stage': stage,
        'scene_index': 0,
        'exception_class': 'GeminiVideoQuotaError',
    }


def test_stock_rescue_pass_overrides_rejected_image_and_repair_quota_failure():
    outcome = _load_boundary()

    # The initial image-motion clip was rejected and the bounded final repair
    # then hit quota, but the free stock replacement passed exact final QC.
    diagnostics = outcome(
        scene_count=1,
        rejected_scene_indices=[],
        rescued_scene_indices=[0],
        final_reviews={
            0: {'score': 91, 'reason': 'Exact event is clearly visible.'},
        },
        runway_attempts=2,
        # The image-motion request itself succeeded; its exact clip was the
        # thing rejected by QC. Only the no-second-image final repair failed.
        initial_generation_failed_scene_indices=[],
        final_repair_failed_scene_indices=[0],
        runway_failure_diagnostics=[
            _quota_failure('final_repair'),
        ],
        repair_checkpoint_available=False,
    )

    assert diagnostics is None


def test_rejected_stock_rescue_reports_quality_and_available_targeted_repair():
    outcome = _load_boundary()

    diagnostics = outcome(
        scene_count=1,
        rejected_scene_indices=[0],
        rescued_scene_indices=[0],
        final_reviews={
            0: {'score': 43, 'reason': 'Required action is not visible.'},
        },
        runway_attempts=2,
        initial_generation_failed_scene_indices=[],
        final_repair_failed_scene_indices=[0],
        runway_failure_diagnostics=[
            _quota_failure('final_repair'),
        ],
        repair_checkpoint_available=True,
    )

    assert diagnostics['stage'] == 'after_rescue'
    assert diagnostics['accepted'] == 0
    assert diagnostics['rejected'][0]['score'] == 43
    assert diagnostics['repair_checkpoint_available'] is True
    assert diagnostics['provider_generation_failures'] == {
        'attempts': 2,
        'failed_scenes': [0],
        'failures': [
            _quota_failure('final_repair'),
        ],
    }


def test_checkpoint_is_attempted_before_post_rescue_quality_failure():
    source = SOURCE_PATH.read_text(encoding='utf-8')
    rejection_branch = source.index('if rejected_final_scenes:')
    checkpoint = source.index(
        '_persist_scene_repair_checkpoint(',
        rejection_branch,
    )
    diagnostic = source.index(
        '_final_visual_rejection_diagnostics(',
        checkpoint,
    )
    quality_failure = source.index(
        "'Final visual quality gate rejected: '",
        diagnostic,
    )

    assert checkpoint < diagnostic < quality_failure
    assert 'Runway generation failed within the bounded submission budget' not in (
        source[rejection_branch:quality_failure]
    )
