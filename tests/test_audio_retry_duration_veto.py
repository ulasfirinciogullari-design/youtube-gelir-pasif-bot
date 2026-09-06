import ast
import copy
import json
import math
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


SOURCE = Path(__file__).resolve().parents[1] / 'app' / 'tasks.py'
TREE = ast.parse(SOURCE.read_text(encoding='utf-8'))
PIPELINE = next(node for node in TREE.body if isinstance(node, ast.FunctionDef)
                and node.name == 'run_video_pipeline')
LOOP = next(node for node in ast.walk(PIPELINE) if isinstance(node, ast.While)
            and '_verify_audio_narration_with_retry' in ast.unparse(node))


class QualityError(RuntimeError):
    pass


def _helpers():
    names = {'_audio_qc_failure_evidence', '_short_preview_voice_duration_qc'}
    definitions = [node for node in TREE.body if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = {'math': math, 'MAX_AUDIO_GENERATION_ATTEMPTS': 3}
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(SOURCE), 'exec'), namespace)
    return namespace


def _namespace(duration=25.272, transcript_pass=False):
    passing = {'available': True, 'pass': True, 'score': 100, 'reason': None}
    voice = {'path': '/tmp/candidate.mp3', 'duration_after_fit': duration,
             'scene_durations': [duration], 'spoken_texts': ['Existing sentence.'],
             '_generation_attempt': 0}
    next_voice = {**voice, 'duration_after_fit': 29, 'scene_durations': [29],
                  '_generation_attempt': 1, '_generation_attempts_used': 2}
    return {
        **_helpers(), 'voice_result': voice, 'voice_path': voice['path'],
        'scene_durations': voice['scene_durations'], 'scenes': [],
        'audio_pause_repair_attempted': False, 'short_form_prosody_required': True,
        'recovered_voice': False, 'saved_voice_retry': False, 'duration_minutes': .5,
        'expected_spoken_narration': 'Existing sentence.', 'language': 'en',
        'task_id': 'child', 'package': {'scenes': []}, 'self': SimpleNamespace(),
        'audio_generation_attempts': 1, 'audio_qc_retry_history': [],
        'audio_synthesis_quality_errors': [], 'FinalAudioQualityError': QualityError,
        'json': json, '_audio_qa_fingerprint': Mock(return_value='a' * 64),
        '_verify_audio_narration_with_retry': Mock(side_effect=[
            {**passing, 'pass': transcript_pass}, passing,
        ]),
        'verify_audio_prosody': Mock(return_value=passing),
        '_repair_voice_internal_pauses': Mock(side_effect=AssertionError('No pause edit expected')),
        '_checkpoint_audio_candidate': Mock(), 'update_job': Mock(), 'set_stage': Mock(),
        '_synthesize_voice_candidate': Mock(return_value=next_voice),
    }


def _run(namespace):
    exec(compile(ast.Module(body=[LOOP], type_ignores=[]), str(SOURCE), 'exec'), namespace)


@pytest.mark.parametrize('transcript_pass', [False, True])
@pytest.mark.parametrize('duration,reason', [
    (25.272, 'short_form_script_too_thin'), (31, 'short_form_script_too_dense'),
])
def test_nonretryable_length_vetoes_same_script_tts_even_with_asr_rejection(duration, reason, transcript_pass):
    n = _namespace(duration, transcript_pass)
    with pytest.raises(QualityError, match=reason):
        _run(n)
    n['_synthesize_voice_candidate'].assert_not_called()
    n['_checkpoint_audio_candidate'].assert_not_called()
    n['verify_audio_prosody'].assert_not_called()
    assert n['_verify_audio_narration_with_retry'].call_count == 1
    evidence = n['update_job'].call_args.kwargs['audio_qc_failure_evidence']
    assert evidence['status'] == 'rejected'
    assert evidence['requires_full_qa'] is True
    assert len(evidence['checks']) == 1
    check = evidence['checks'][0]
    assert check['transcript'] == {'available': True, 'pass': transcript_pass, 'score': 100, 'reason': None}
    assert check['duration']['reason'] == reason
    assert check['duration']['retryable'] is False
    assert check['duration']['duration_seconds'] == duration
    assert check['prosody']['reason'] == ('not_run_duration_rejected' if transcript_pass else 'not_run_transcript_rejected')
    assert check['prosody']['available'] is False


@pytest.mark.parametrize('duration,transcript_pass', [(29, False), (28.3, False), (28.3, True)])
def test_viable_asr_and_near_boundary_duration_retries_still_run(duration, transcript_pass):
    n = _namespace(duration, transcript_pass)
    _run(n)
    n['_synthesize_voice_candidate'].assert_called_once()
    assert n['_synthesize_voice_candidate'].call_args.kwargs['start_attempt'] == 1
    assert n['_verify_audio_narration_with_retry'].call_count == 2
    n['_checkpoint_audio_candidate'].assert_called_once()
    n['update_job'].assert_not_called()


def test_viable_duration_prosody_retry_still_runs_full_qa():
    n = _namespace(29, True)
    n['verify_audio_prosody'].side_effect = [
        {'available': True, 'pass': False, 'reason': 'flat_emphasis'},
        {'available': True, 'pass': True, 'reason': None},
    ]
    _run(n)
    n['_synthesize_voice_candidate'].assert_called_once()
    assert n['verify_audio_prosody'].call_count == 2
    assert n['_verify_audio_narration_with_retry'].call_count == 2


@pytest.mark.parametrize('guard', ['saved_voice_retry', 'recovered_voice', 'exhausted', 'asr_unavailable', 'duration_unavailable'])
def test_existing_no_paid_retry_guards_remain_closed(guard):
    n = _namespace(29, False)
    if guard in {'saved_voice_retry', 'recovered_voice'}:
        n[guard] = True
    elif guard == 'exhausted':
        n['audio_generation_attempts'] = 3
    elif guard == 'asr_unavailable':
        n['_verify_audio_narration_with_retry'].side_effect = [{'available': False, 'pass': False}]
    else:
        n['voice_result']['duration_after_fit'] = None
    with pytest.raises(QualityError):
        _run(n)
    n['_synthesize_voice_candidate'].assert_not_called()


def test_failure_evidence_preserves_all_bounded_checks_without_provider_content():
    helper = _helpers()['_audio_qc_failure_evidence']
    secret = 'Bearer sk-secret-private https://example.test/signed?token=private'
    item = {
        'generation_attempt': 1, 'voice_model': secret, 'audio_key': secret,
        'transcript': {'available': True, 'pass': False, 'score': 100, 'reason': None,
                       'provider': secret, 'raw': secret, 'missing_words': [secret]},
        'duration': {'available': True, 'pass': False, 'retryable': False,
                     'duration_seconds': 25.272, 'minimum_seconds': 28.7,
                     'maximum_seconds': 29.75, 'reason': 'short_form_script_too_thin', 'path': secret},
        'prosody': {'available': False, 'pass': False, 'reason': secret, 'summary': secret,
                    'issues': [{'phrase': secret}], 'audio_sha256': secret,
                    'scores': {'pronunciation': 80, 'naturalness': True, 'pacing': float('nan'),
                               'sentence_flow': float('inf'), 'emphasis': -1, 'roboticness': 101,
                               'raw_provider': secret}},
    }
    original = copy.deepcopy(item)
    evidence = helper([{}] * 50 + [item] * 4)
    assert len(evidence['checks']) == 4
    assert evidence['checks'][0]['prosody']['reason'] == 'reason_omitted'
    assert evidence['checks'][0]['prosody']['scores'] == {
        'pronunciation': 80, 'naturalness': None, 'pacing': None,
        'sentence_flow': None, 'emphasis': None, 'roboticness': None,
    }
    encoded = json.dumps(evidence, allow_nan=False)
    assert secret not in encoded
    for forbidden in ('audio_key', 'audio_sha256', 'provider', 'missing_words', 'summary', 'issues'):
        assert forbidden not in encoded
    assert len(encoded) < 5000
    assert item['transcript'] == original['transcript']
    assert item['duration'] == original['duration']
    assert item['prosody']['reason'] == secret


@pytest.mark.parametrize('malformed', [None, {}, 'secret', [None, True, 'secret']])
def test_failure_evidence_ignores_malformed_history(malformed):
    assert _helpers()['_audio_qc_failure_evidence'](malformed)['checks'] == []


def test_failure_evidence_invalid_values_are_unknown_not_fabricated_passes():
    evidence = _helpers()['_audio_qc_failure_evidence']([{
        'generation_attempt': True,
        'transcript': {'available': 'yes', 'pass': 1, 'score': '100', 'reason': {'secret': 'value'}},
        'duration': {'retryable': 1, 'duration_seconds': True}, 'prosody': [],
    }])
    check = evidence['checks'][0]
    assert check['generation_attempt'] is None
    assert check['transcript'] == {'available': None, 'pass': None, 'score': None, 'reason': 'reason_omitted'}
    assert check['duration']['retryable'] is None
    assert check['duration']['duration_seconds'] is None
    assert check['prosody']['pass'] is None


def test_diagnostic_storage_failure_never_masks_quality_failure_or_spends_tts():
    n = _namespace()
    n['update_job'].side_effect = RuntimeError('unavailable storage')
    with pytest.raises(QualityError, match='short_form_script_too_thin'):
        _run(n)
    n['_synthesize_voice_candidate'].assert_not_called()
