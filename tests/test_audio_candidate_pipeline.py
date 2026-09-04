import ast
from pathlib import Path
from unittest.mock import Mock, patch


def _load_checkpoint_wrapper(update):
    source_path = Path(__file__).resolve().parents[1] / 'app' / 'tasks.py'
    tree = ast.parse(source_path.read_text(encoding='utf-8'))
    definition = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == '_checkpoint_audio_candidate'
    )
    namespace = {'update_job': update}
    exec(compile(ast.Module(body=[definition], type_ignores=[]), str(source_path), 'exec'), namespace)
    return namespace['_checkpoint_audio_candidate']


def test_candidate_is_recorded_without_marking_quality_approved():
    candidate = {'audio_candidate_checkpoint': {'status': 'unapproved_candidate', 'qa_approved': False}}
    package, voice = {'scenes': []}, {'path': '/tmp/candidate.mp3'}
    update = Mock()
    with patch('app.services.audio_checkpoint.persist_audio_candidate_checkpoint', return_value=candidate) as persist:
        _load_checkpoint_wrapper(update)('task', package, voice)
    persist.assert_called_once_with('task', package, voice)
    update.assert_called_once_with('task', **candidate)
    assert candidate['audio_candidate_checkpoint_error'] is None


def test_candidate_storage_failure_only_records_safe_unavailable_status():
    update = Mock()
    with patch('app.services.audio_checkpoint.persist_audio_candidate_checkpoint', side_effect=RuntimeError('secret response must not escape')):
        _load_checkpoint_wrapper(update)('task', {}, {})
    update.assert_called_once_with('task', audio_candidate_checkpoint_error='unavailable')


def test_candidate_registry_failure_does_not_discard_audio_or_change_qa():
    update = Mock(side_effect=RuntimeError('registry unavailable'))
    with patch('app.services.audio_checkpoint.persist_audio_candidate_checkpoint', return_value={'audio_candidate_checkpoint': {'status': 'unapproved_candidate'}}):
        _load_checkpoint_wrapper(update)('task', {}, {})
