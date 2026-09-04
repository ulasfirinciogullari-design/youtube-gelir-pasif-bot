import ast
import copy
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


class QualityError(RuntimeError):
    pass


@pytest.fixture
def recovery(monkeypatch):
    source_id, child_id = 'source', 'child'
    spec = {'topic': 'Source-backed topic', 'duration_minutes': 0.5, 'language': 'tr', 'channel_id': 'route', 'mode': 'production', 'format': 'shorts', 'production_channel_id': 'channel', 'production_profile_revision': 'revision'}
    source = {'state': 'FAILURE', 'kind': 'render', 'failure_stage': 'audio_qc', 'retry_child_task_id': child_id, 'spec': copy.deepcopy(spec), 'audio_candidate_checkpoint': {'status': 'unapproved_candidate'}}
    child = {'parent_id': source_id, 'spec': copy.deepcopy(spec)}
    package = {'scenes': [{'narration': 'First sentence.'}, {'narration': 'Final sentence.'}]}
    voice = {'path': '/tmp/existing.mp3', 'spoken_texts': ['First sentence.', 'Final sentence.']}
    loader = Mock(return_value={'package': package, 'voice_result': voice})
    reviewer = Mock(return_value=copy.deepcopy(package))
    unchanged = Mock()
    fitting = Mock(return_value=voice)
    monkeypatch.setitem(sys.modules, 'app.services.studio_state', SimpleNamespace(get_job=lambda job_id: source if job_id == source_id else child))
    monkeypatch.setitem(sys.modules, 'app.services.voice_candidate_recovery', SimpleNamespace(load_voice_retry_candidate=loader, require_unchanged_voice_narration=unchanged))
    monkeypatch.setitem(sys.modules, 'app.services.director', SimpleNamespace(revalidate_immutable_short_story=reviewer))
    monkeypatch.setitem(sys.modules, 'app.services.voice', SimpleNamespace(normalize_turkish_tts=lambda text, **kwargs: text, fit_existing_narration_candidate=fitting))
    path = Path(__file__).resolve().parents[1] / 'app' / 'tasks.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    definitions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in {'_prepare_saved_voice_retry', '_fit_saved_voice_for_retry'}]
    namespace = {'Path': Path, 'FinalAudioQualityError': QualityError, 'preview_total_paid_create_cap': Mock(return_value=2), '_persisted_paid_create_slots': Mock(return_value=0), 'short_story_package_is_approved': Mock(return_value=True), 'update_job': Mock()}
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(path), 'exec'), namespace)
    return SimpleNamespace(**locals())


def test_same_spec_claim_revalidates_exact_text_and_marks_zero_new_tts(recovery):
    r = recovery
    result = r.namespace['_prepare_saved_voice_retry'](r.child_id, r.source_id, r.spec, Path('/tmp/work'))
    assert result['voice_result'] is r.voice
    r.reviewer.assert_called_once_with(r.package, r.spec['topic'], 0.5, 'tr', {k:v for k,v in r.spec.items() if k not in {'topic','duration_minutes','language','channel_id'}}, immutable_candidate_narrations=['First sentence.', 'Final sentence.'])
    r.unchanged.assert_called_once_with(r.package, r.reviewer.return_value)
    assert r.namespace['update_job'].call_args.kwargs['voice_candidate_reuse']['new_tts_requests'] == 0


@pytest.mark.parametrize('change', ['parent', 'retry_child', 'topic', 'channel', 'profile', 'kind', 'state', 'checkpoint'])
def test_binding_mismatch_aborts_before_storage_and_model_calls(recovery, change):
    r = recovery
    if change == 'parent': r.child['parent_id'] = 'another'
    elif change == 'retry_child': r.source['retry_child_task_id'] = 'another'
    elif change == 'topic': r.source['spec']['topic'] = 'another'
    elif change == 'channel': r.source['spec']['production_channel_id'] = 'another'
    elif change == 'profile': r.source['spec']['production_profile_revision'] = 'another'
    elif change == 'kind': r.source['kind'] = 'plan'
    elif change == 'state': r.source['state'] = 'SUCCESS'
    else: r.source['audio_candidate_checkpoint'] = 'invalid'
    with pytest.raises(QualityError):
        r.namespace['_prepare_saved_voice_retry'](r.child_id, r.source_id, r.spec, Path('/tmp/work'))
    r.loader.assert_not_called()
    r.reviewer.assert_not_called()


def test_existing_paid_media_cannot_take_voice_only_path(recovery):
    r = recovery
    r.namespace['_persisted_paid_create_slots'].return_value = 1
    with pytest.raises(QualityError):
        r.namespace['_prepare_saved_voice_retry'](r.child_id, r.source_id, r.spec, Path('/tmp/work'))
    r.loader.assert_not_called()


@pytest.mark.parametrize('failure', ['download', 'spoken', 'story', 'text_changed', 'attestation'])
def test_corrupt_or_rejected_candidate_never_falls_back_to_new_voice(recovery, failure):
    r = recovery
    if failure == 'download': r.loader.side_effect = RuntimeError('private diagnostic')
    elif failure == 'spoken': r.voice['spoken_texts'] = ['Different words.']
    elif failure == 'story': r.reviewer.side_effect = RuntimeError('unsupported source')
    elif failure == 'text_changed': r.unchanged.side_effect = ValueError('narration changed')
    else: r.namespace['short_story_package_is_approved'].return_value = False
    with pytest.raises(QualityError, match='no replacement voice was generated'):
        r.namespace['_prepare_saved_voice_retry'](r.child_id, r.source_id, r.spec, Path('/tmp/work'))
    r.namespace['update_job'].assert_not_called()


def test_legacy_job_without_candidate_keeps_existing_path(recovery):
    r = recovery
    r.source.pop('audio_candidate_checkpoint')
    assert r.namespace['_prepare_saved_voice_retry'](r.child_id, r.source_id, r.spec, Path('/tmp/work')) is None
    r.loader.assert_not_called()


def test_other_formats_are_not_changed(recovery):
    r = recovery
    r.spec['mode'] = 'preview'
    assert r.namespace['_prepare_saved_voice_retry'](r.child_id, r.source_id, r.spec, Path('/tmp/work')) is None


def test_fit_failure_is_terminal_not_a_paid_regeneration(recovery):
    r = recovery
    r.fitting.side_effect = RuntimeError('cannot fit')
    with pytest.raises(QualityError, match='no replacement voice was generated'):
        r.namespace['_fit_saved_voice_for_retry'](r.voice, 30)


def test_pipeline_blocks_both_initial_tts_and_audio_retry_tts_for_saved_voice():
    source = (Path(__file__).resolve().parents[1] / 'app' / 'tasks.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    pipeline = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'run_video_pipeline')
    branches = [node for node in ast.walk(pipeline) if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == 'saved_voice_retry']
    assert len(branches) == 1
    assert '_fit_saved_voice_for_retry' in ast.unparse(branches[0])
    regeneration = next(node for node in ast.walk(pipeline) if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'can_regenerate' for target in node.targets))
    assert 'not saved_voice_retry' in ast.unparse(regeneration.value)
