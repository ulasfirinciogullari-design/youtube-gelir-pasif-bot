import ast
import copy
import re
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
    budget_validator = Mock(side_effect=lambda value: copy.deepcopy(value))
    unchanged = Mock()
    fitting = Mock(return_value=voice)
    review_options = Mock(return_value={})
    monkeypatch.setitem(sys.modules, 'app.services.saved_voice_review', SimpleNamespace(review_options=review_options))
    monkeypatch.setitem(sys.modules, 'app.services.studio_state', SimpleNamespace(get_job=lambda job_id: source if job_id == source_id else child))
    monkeypatch.setitem(sys.modules, 'app.services.voice_candidate_recovery', SimpleNamespace(load_voice_retry_candidate=loader, require_unchanged_voice_narration=unchanged))
    monkeypatch.setitem(sys.modules, 'app.services.director', SimpleNamespace(revalidate_immutable_short_story=reviewer, validate_spoken_word_budget=budget_validator))
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


def test_included_retry_preserves_candidate_before_fresh_immutable_review(recovery):
    r = recovery
    order = []
    def preserve(task, package, voice):
        assert task == r.child_id and package == r.package and voice is r.voice
        order.append('saved')
        return {'immutable_stock_routes': True}
    def reject(*args, **kwargs):
        assert kwargs['immutable_stock_routes'] is True and order == ['saved']
        order.append('reviewed')
        raise RuntimeError('Independent review rejected the story')
    r.review_options.side_effect = preserve
    r.reviewer.side_effect = reject
    with pytest.raises(QualityError):
        r.namespace['_prepare_saved_voice_retry'](r.child_id, r.source_id, r.spec, Path('/tmp/work'))
    assert order == ['saved', 'reviewed']
    r.namespace['short_story_package_is_approved'].assert_not_called()


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


def test_saved_retry_inherits_prior_pause_edit_and_cannot_trim_it_again(recovery):
    r = recovery
    audit = {'requires_full_qa': True, 'output_sha256': 'a' * 64}
    r.source['audio_pause_repair'] = audit
    result = r.namespace['_prepare_saved_voice_retry'](r.child_id, r.source_id, r.spec, Path('/tmp/work'))
    assert result['voice_result']['_internal_pause_repair_attempted'] is True
    assert r.namespace['update_job'].call_args.kwargs['audio_pause_repair'] == audit


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


def _english_scheduled(r):
    for spec in (r.spec, r.source['spec'], r.child['spec']):
        spec.update(language='en', production_scheduled=True)
    return {'version': 1, 'profile': 'fresh_en_30s_v1', 'language': 'en',
            'duration_minutes': .5, 'target_words': 65, 'minimum_words': 62, 'maximum_words': 66}


def test_saved_english_budget_forwarded_only_from_verified_checkpoint_package(recovery):
    r = recovery
    marker = _english_scheduled(r)
    r.package['spoken_word_budget'] = marker
    r.namespace['_prepare_saved_voice_retry'](r.child_id, r.source_id, r.spec, Path('/tmp/work'))
    r.budget_validator.assert_called_once_with(marker)
    forwarded = r.reviewer.call_args.kwargs['verified_spoken_word_budget']
    assert forwarded == marker
    assert forwarded is not marker
    r.unchanged.assert_called_once()


@pytest.mark.parametrize('invalid_runtime', ['language', 'unscheduled', 'truthy_scheduled'])
def test_saved_budget_mismatched_runtime_rejected_even_if_validator_would_accept(recovery, invalid_runtime):
    r = recovery
    r.package['spoken_word_budget'] = _english_scheduled(r)
    for spec in (r.spec, r.source['spec'], r.child['spec']):
        if invalid_runtime == 'language':
            spec['language'] = 'tr'
        else:
            spec['production_scheduled'] = 1 if invalid_runtime == 'truthy_scheduled' else False
    with pytest.raises(QualityError, match='no replacement voice was generated'):
        r.namespace['_prepare_saved_voice_retry'](r.child_id, r.source_id, r.spec, Path('/tmp/work'))
    r.budget_validator.assert_not_called()
    r.reviewer.assert_not_called()
    r.namespace['update_job'].assert_not_called()


@pytest.mark.parametrize('malformed', [None, 'fresh_en_30s_v1', {}, {'target_words': 999}])
def test_invalid_checkpoint_budget_is_terminal_not_silently_dropped(recovery, malformed):
    r = recovery
    _english_scheduled(r)
    r.package['spoken_word_budget'] = malformed
    r.budget_validator.side_effect = ValueError('Invalid fixed spoken budget')
    with pytest.raises(QualityError, match='no replacement voice was generated'):
        r.namespace['_prepare_saved_voice_retry'](r.child_id, r.source_id, r.spec, Path('/tmp/work'))
    r.budget_validator.assert_called_once_with(malformed)
    r.reviewer.assert_not_called()
    r.namespace['update_job'].assert_not_called()


def test_legacy_english_without_checkpoint_marker_does_not_get_budget_from_options(recovery):
    r = recovery
    marker = _english_scheduled(r)
    for spec in (r.spec, r.source['spec'], r.child['spec']):
        spec['spoken_word_budget'] = marker
    r.namespace['_prepare_saved_voice_retry'](r.child_id, r.source_id, r.spec, Path('/tmp/work'))
    assert 'verified_spoken_word_budget' not in r.reviewer.call_args.kwargs
    r.budget_validator.assert_not_called()


def test_checkpoint_hash_failure_prevents_budget_validation_and_review(recovery):
    r = recovery
    r.package['spoken_word_budget'] = _english_scheduled(r)
    r.loader.side_effect = ValueError('Checkpoint hash differs')
    with pytest.raises(QualityError):
        r.namespace['_prepare_saved_voice_retry'](r.child_id, r.source_id, r.spec, Path('/tmp/work'))
    r.budget_validator.assert_not_called()
    r.reviewer.assert_not_called()


def _decimal_recovery(r):
    path = Path(__file__).resolve().parents[1] / 'app' / 'services' / 'voice.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    definition = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                      and node.name == 'normalize_turkish_tts')
    namespace = {'re': re, '_TURKISH_PRONUNCIATION_RULES': []}
    exec(compile(ast.Module(body=[definition], type_ignores=[]), str(path), 'exec'), namespace)
    r.monkeypatch.setitem(sys.modules, 'app.services.voice', SimpleNamespace(
        normalize_turkish_tts=namespace['normalize_turkish_tts']))
    r.package['scenes'] = [{'narration': 'Maliyet 3,69 sent.'}, {'narration': 'Tutar 1,25 sent.'}]
    r.reviewer.return_value = copy.deepcopy(r.package)


@pytest.mark.parametrize('spoken', [
    ['Maliyet 3,69 sent.', 'Tutar 1,25 sent.'],
    ['Maliyet 3, 69 sent.', 'Tutar 1, 25 sent.'],
])
def test_exact_current_or_legacy_checkpoint_spelling_preserves_original_records(recovery, spoken):
    r = recovery
    _decimal_recovery(r)
    r.voice['spoken_texts'] = spoken
    before = copy.deepcopy((r.source, r.child, r.voice, r.package))
    result = r.namespace['_prepare_saved_voice_retry'](r.child_id, r.source_id, r.spec, Path('/tmp/work'))
    assert result['voice_result'] is r.voice
    assert (r.source, r.child, r.voice, r.package) == before
    r.loader.assert_called_once()
    r.reviewer.assert_called_once()
    r.unchanged.assert_called_once()
    assert r.namespace['update_job'].call_args.kwargs['voice_candidate_reuse']['new_tts_requests'] == 0


@pytest.mark.parametrize('spoken', [
    ['Maliyet 3 69 sent.', 'Tutar 1, 25 sent.'],
    ['Maliyet 3,96 sent.', 'Tutar 1,25 sent.'],
    ['Maliyet 3, 69 cent.', 'Tutar 1, 25 sent.'],
    ['Maliyet 3,  69 sent.', 'Tutar 1, 25 sent.'],
    ['Maliyet 3, 69 sent değil.', 'Tutar 1, 25 sent.'],
    ['Maliyet 3,69 sent.', 'Tutar 1, 25 sent.'],
    ['Maliyet 3, 69 sent.', 'Tutar 1,25 sent.'],
])
def test_spoken_tampering_or_mixed_normalizer_versions_never_reaches_review(recovery, spoken):
    r = recovery
    _decimal_recovery(r)
    r.voice['spoken_texts'] = spoken
    before = copy.deepcopy((r.source, r.voice, r.package))
    with pytest.raises(QualityError, match='no replacement voice was generated'):
        r.namespace['_prepare_saved_voice_retry'](r.child_id, r.source_id, r.spec, Path('/tmp/work'))
    assert (r.source, r.voice, r.package) == before
    r.reviewer.assert_not_called()
    r.namespace['update_job'].assert_not_called()


def test_legacy_punctuation_never_skips_hash_validation(recovery):
    r = recovery
    _decimal_recovery(r)
    r.voice['spoken_texts'] = ['Maliyet 3, 69 sent.', 'Tutar 1, 25 sent.']
    r.loader.side_effect = ValueError('Checkpoint hash differs')
    with pytest.raises(QualityError):
        r.namespace['_prepare_saved_voice_retry'](r.child_id, r.source_id, r.spec, Path('/tmp/work'))
    r.reviewer.assert_not_called()
    r.namespace['update_job'].assert_not_called()


def test_turkish_legacy_punctuation_is_not_adopted_for_other_languages(recovery):
    r = recovery
    _decimal_recovery(r)
    for spec in (r.spec, r.source['spec'], r.child['spec']):
        spec['language'] = 'en'
    r.voice['spoken_texts'] = ['Maliyet 3, 69 sent.', 'Tutar 1, 25 sent.']
    with pytest.raises(QualityError):
        r.namespace['_prepare_saved_voice_retry'](r.child_id, r.source_id, r.spec, Path('/tmp/work'))
    r.reviewer.assert_not_called()
