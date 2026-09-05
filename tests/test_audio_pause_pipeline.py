import ast
import copy
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest


SOURCE = Path(__file__).resolve().parents[1] / 'app' / 'tasks.py'


class QualityError(RuntimeError):
    pass


def _definition(name, namespace):
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    definition = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    exec(compile(ast.Module(body=[definition], type_ignores=[]), str(SOURCE), 'exec'), namespace)
    return namespace[name]


def _loop(*, repaired=True, subsequent_pass=True, recovered=False):
    pipeline = next(node for node in ast.parse(SOURCE.read_text(encoding='utf-8')).body
                    if isinstance(node, ast.FunctionDef) and node.name == 'run_video_pipeline')
    loop = next(node for node in ast.walk(pipeline) if isinstance(node, ast.While)
                and '_verify_audio_narration_with_retry' in ast.unparse(node))
    voice = {'path': '/tmp/saved.mp3', 'scene_durations': [10, 10, 9.52],
             'duration_after_fit': 29.52, 'spoken_texts': ['Existing words.'], '_generation_attempt': 2}
    edited = {**voice, 'scene_durations': [9.5, 10, 9.3], 'duration_after_fit': 28.8,
              'internal_pause_repair': {'requires_full_qa': True, 'new_tts_requests': 0}}
    failure = {'available': True, 'pass': False, 'reason': 'unnatural_internal_pause',
               'issues': [{'code': 'unnatural_internal_pause'}]}
    passing = {'available': True, 'pass': True, 'reason': None}
    prosody = Mock(side_effect=[failure, passing if subsequent_pass else failure])
    namespace = {
        'voice_result': voice, 'voice_path': voice['path'], 'scene_durations': voice['scene_durations'],
        'audio_pause_repair_attempted': False, 'short_form_prosody_required': True,
        'recovered_voice': recovered, 'saved_voice_retry': True, 'duration_minutes': .5,
        'expected_spoken_narration': 'Existing words.', 'language': 'tr', 'task_id': 'child',
        'package': {'scenes': []}, 'self': SimpleNamespace(), 'audio_generation_attempts': 3,
        'audio_qc_retry_history': [], 'audio_synthesis_quality_errors': [],
        'MAX_AUDIO_GENERATION_ATTEMPTS': 3, 'FinalAudioQualityError': QualityError, 'json': json,
        '_audio_qa_fingerprint': Mock(side_effect=['a' * 64, 'b' * 64]),
        '_verify_audio_narration_with_retry': Mock(return_value={**passing, 'score': 100}),
        '_short_preview_voice_duration_qc': Mock(return_value=passing),
        'verify_audio_prosody': prosody,
        '_repair_voice_internal_pauses': Mock(return_value=edited if repaired else None),
        '_checkpoint_audio_candidate': Mock(), 'update_job': Mock(), 'set_stage': Mock(),
        '_synthesize_voice_candidate': Mock(side_effect=AssertionError('No new TTS authorized')),
    }
    return loop, namespace


def _run(loop, namespace):
    exec(compile(ast.Module(body=[loop], type_ignores=[]), str(SOURCE), 'exec'), namespace)


def test_saved_voice_repair_restarts_all_qa_without_spending_seed():
    loop, n = _loop()
    _run(loop, n)
    assert n['_verify_audio_narration_with_retry'].call_count == 2
    assert n['_short_preview_voice_duration_qc'].call_count == 2
    assert n['verify_audio_prosody'].call_count == 2
    assert n['audio_qc']['audio_sha256'] == 'b' * 64
    assert n['_repair_voice_internal_pauses'].call_args.args[2]['audio_sha256'] == 'a' * 64
    assert n['audio_generation_attempts'] == 3
    assert n['audio_pause_repair_attempted'] is True
    assert [entry['prosody']['pass'] for entry in n['audio_qc_retry_history']] == [False, True]
    n['_synthesize_voice_candidate'].assert_not_called()
    n['_checkpoint_audio_candidate'].assert_called_once()
    n['update_job'].assert_called_once()


def test_failed_repaired_voice_stays_failed_without_second_edit_or_new_tts():
    loop, n = _loop(subsequent_pass=False)
    with pytest.raises(QualityError, match='Audio narration QA rejected'):
        _run(loop, n)
    n['_repair_voice_internal_pauses'].assert_called_once()
    assert n['_verify_audio_narration_with_retry'].call_count == 2
    n['_synthesize_voice_candidate'].assert_not_called()


def test_other_prosody_defect_cannot_be_approved_by_a_partial_pause_repair():
    loop, n = _loop()
    pause = {'code': 'unnatural_internal_pause'}
    choppy = {'code': 'choppy_phrase_grouping'}
    n['verify_audio_prosody'].side_effect = [
        {'available': True, 'pass': False, 'reason': 'unnatural_internal_pause', 'issues': [pause, choppy]},
        {'available': True, 'pass': False, 'reason': 'choppy_phrase_grouping', 'issues': [choppy]},
    ]
    with pytest.raises(QualityError, match='choppy_phrase_grouping'):
        _run(loop, n)
    assert n['_verify_audio_narration_with_retry'].call_count == 2
    n['_repair_voice_internal_pauses'].assert_called_once()
    n['_synthesize_voice_candidate'].assert_not_called()
    assert all(not item['prosody']['pass'] for item in n['audio_qc_retry_history'])
    assert n['audio_qc_retry_history'][0]['prosody']['issues'] == [pause, choppy]


def test_unavailable_edit_preserves_failure_without_fake_checkpoint():
    loop, n = _loop(repaired=False)
    with pytest.raises(QualityError):
        _run(loop, n)
    n['_checkpoint_audio_candidate'].assert_not_called()
    n['update_job'].assert_not_called()
    assert n['_verify_audio_narration_with_retry'].call_count == 1


@pytest.mark.parametrize('condition', ['recovered_contract', 'already_attempted', 'transcript_failed', 'duration_failed', 'not_turkish_short'])
def test_no_edit_outside_exact_eligible_path(condition):
    loop, n = _loop()
    if condition == 'recovered_contract': n['recovered_voice'] = True
    elif condition == 'already_attempted': n['audio_pause_repair_attempted'] = True
    elif condition == 'transcript_failed':
        n['_verify_audio_narration_with_retry'].return_value = {'available': True, 'pass': False}
    elif condition == 'duration_failed':
        n['_short_preview_voice_duration_qc'].return_value = {'available': True, 'pass': False}
    else:
        n['short_form_prosody_required'] = False
        n['_verify_audio_narration_with_retry'].return_value = {'available': True, 'pass': False}
    with pytest.raises(QualityError):
        _run(loop, n)
    n['_repair_voice_internal_pauses'].assert_not_called()
    n['_checkpoint_audio_candidate'].assert_not_called()
    n['_synthesize_voice_candidate'].assert_not_called()


def test_wrapper_never_exposes_edit_diagnostics():
    module = SimpleNamespace(repair_internal_pauses=Mock(side_effect=RuntimeError('private detail')))
    with patch.dict(sys.modules, {'app.services.audio_pause_repair': module}):
        fn = _definition('_repair_voice_internal_pauses', {})
        assert fn({}, 30, {}, {}) is None


def test_fingerprint_matches_actual_audio_and_rejects_empty(tmp_path):
    fn = _definition('_audio_qa_fingerprint', {'Path': Path, 'hashlib': hashlib, 'FinalAudioQualityError': QualityError})
    candidate = tmp_path / 'candidate.mp3'
    candidate.write_bytes(b'a' * 2048)
    assert fn(candidate) == hashlib.sha256(b'a' * 2048).hexdigest()
    candidate.write_bytes(b'')
    with pytest.raises(QualityError, match='incomplete'):
        fn(candidate)
