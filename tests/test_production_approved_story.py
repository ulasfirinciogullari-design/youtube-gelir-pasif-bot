import ast
from pathlib import Path
from unittest.mock import Mock

import pytest


class QualityError(RuntimeError):
    pass


def load_prepare(approved):
    source = Path(__file__).resolve().parents[1] / 'app' / 'tasks.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    definition = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == '_prepare_package')
    reviewer = Mock(return_value=approved)
    namespace = {'short_story_package_is_approved': reviewer, 'FinalVisualQualityError': QualityError, 'set_stage': Mock()}
    exec(compile(ast.Module(body=[definition], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace['_prepare_package'], reviewer


@pytest.mark.parametrize('options,duration', [
    ({'mode': 'production', 'format': 'shorts'}, 0.5),
    ({'mode': 'preview', 'format': 'shorts'}, 0.5),
])
def test_unapproved_short_package_cannot_bypass_story_review(options, duration):
    prepare, reviewer = load_prepare(False)
    package = {'scenes': [{'narration': 'Stale unchecked narration'}]}
    with pytest.raises(QualityError):
        prepare(None, 'task', 'Original topic', duration, 'tr', options, package)
    reviewer.assert_called_once_with(package, 'Original topic')


def test_current_approved_production_short_keeps_package_and_topic_binding():
    prepare, reviewer = load_prepare(True)
    options = {'mode': 'production', 'format': 'shorts'}
    package = {'scenes': [{'narration': 'Reviewed narration'}]}
    result = prepare(None, 'task', 'Original topic', 0.5, 'tr', options, package)
    reviewer.assert_called_once_with(package, 'Original topic')
    assert result['scenes'] == package['scenes']
    assert result['studio_options'] == options


@pytest.mark.parametrize('options,duration', [
    ({'mode': 'production', 'format': 'long'}, 0.5),
    ({'mode': 'production', 'format': 'shorts'}, 1.0),
])
def test_unrelated_production_formats_keep_existing_behavior(options, duration):
    prepare, reviewer = load_prepare(False)
    prepare(None, 'task', 'Topic', duration, 'tr', options, {'scenes': [{}]})
    reviewer.assert_not_called()
