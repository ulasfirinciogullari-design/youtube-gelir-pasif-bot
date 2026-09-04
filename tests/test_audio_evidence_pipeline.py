import ast
from pathlib import Path
from unittest.mock import Mock, patch


def load_sink(update):
    source = Path(__file__).resolve().parents[1] / 'app' / 'tasks.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    definition = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == '_audio_provider_evidence_sink')
    namespace = {'update_job': update, 'Path': Path}
    exec(compile(ast.Module(body=[definition], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace['_audio_provider_evidence_sink']


def test_sink_binds_task_and_audio_and_preserves_unapproved_pointer():
    pointer = {'key': 'diagnostic-only', 'qa_approved': False}
    update = Mock()
    with patch('app.services.audio_evidence.persist_audio_provider_evidence', return_value=pointer) as persist:
        sink = load_sink(update)('task', '/tmp/task.mp3')
        sink(provider='openai', model='whisper-1', language='tr', payload={'text': 'test'})
    persist.assert_called_once_with('task', '/tmp/task.mp3', provider='openai', model='whisper-1', language='tr', payload={'text': 'test'})
    update.assert_called_once_with('task', audio_provider_evidence=[pointer])


def test_sink_deduplicates_and_bounds_pointer_history():
    update = Mock()
    with patch('app.services.audio_evidence.persist_audio_provider_evidence') as persist:
        sink = load_sink(update)('task', '/tmp/task.mp3')
        for index in range(9):
            persist.return_value = {'key': f'pointer-{index}', 'qa_approved': False}
            sink(provider='openai', model='whisper-1', language='tr', payload={})
            sink(provider='openai', model='whisper-1', language='tr', payload={})
    assert len(update.call_args.kwargs['audio_provider_evidence']) == 6
    assert update.call_args.kwargs['audio_provider_evidence'][0]['key'] == 'pointer-3'


def test_sink_storage_failure_is_nonfatal_and_does_not_record_raw_error():
    update = Mock()
    with patch('app.services.audio_evidence.persist_audio_provider_evidence', side_effect=RuntimeError('private provider response')):
        load_sink(update)('task', '/tmp/task.mp3')(provider='openai', model='whisper-1', language='tr', payload={})
    update.assert_not_called()


def test_sink_registry_failure_is_nonfatal():
    update = Mock(side_effect=RuntimeError('registry unavailable'))
    with patch('app.services.audio_evidence.persist_audio_provider_evidence', return_value={'key': 'diagnostic-only'}):
        load_sink(update)('task', '/tmp/task.mp3')(provider='openai', model='whisper-1', language='tr', payload={})
