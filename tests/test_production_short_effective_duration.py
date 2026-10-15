"""Actual voice endpoint, unchanged sound/scene timing, and full existing QA."""
import ast
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import math
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'app/tasks.py'
TREE = ast.parse(SOURCE.read_text(encoding='utf-8'))
PIPELINE = next(node for node in TREE.body if isinstance(node, ast.FunctionDef)
                and node.name == 'run_video_pipeline')
OPTIONS = {'mode': 'production', 'format': 'shorts'}


def helpers():
    names = {'_effective_short_edit_target', '_short_preview_voice_duration_qc',
             '_strict_short_preview_render_qc', '_render_target_duration',
             '_preview_duration_within_gate'}
    definitions = [node for node in TREE.body if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = {'math': math}
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(SOURCE), 'exec'), namespace)
    return namespace


def test_actual_complete_take_ends_on_frame_803_with_half_second_hold():
    n = helpers()
    target = n['_effective_short_edit_target'](OPTIONS, 30, 26.208)
    assert target == pytest.approx(26.766666666666666)
    assert round(target * 30) == 803
    assert target - 26.208 == pytest.approx(.558666666666666)
    assert n['_short_preview_voice_duration_qc']({'duration_after_fit': 26.208}, 30)['pass'] is False
    assert n['_short_preview_voice_duration_qc']({'duration_after_fit': 26.208}, target)['pass'] is True
    assert n['_strict_short_preview_render_qc'](
        {'frame_count': 803, 'ending_silence_seconds': target - 26.208}, target, 26.208,
    )['pass'] is True


@pytest.mark.parametrize('voice', [28.7, 28.75, 29.5, 29.75])
def test_previously_valid_takes_keep_original_thirty_second_target(voice):
    assert helpers()['_effective_short_edit_target'](OPTIONS, 30, voice) == 30


@pytest.mark.parametrize('voice', [25.5, 26.208, 26.808, 28.3, 28.6999])
def test_bounded_complete_takes_only_get_frame_aligned_closing_hold(voice):
    n = helpers()
    target = n['_effective_short_edit_target'](OPTIONS, 30, voice)
    assert .55 - 1e-12 <= target - voice < .55 + 1 / 30 + 1e-12
    assert target * 30 == pytest.approx(round(target * 30))
    assert n['_short_preview_voice_duration_qc']({'duration_after_fit': voice}, target)['pass'] is True


@pytest.mark.parametrize('voice', [None, True, False, '26.208', [], {}, float('nan'),
                                  float('inf'), float('-inf'), -1, 0, 25.49999, 29.75001, 31])
def test_invalid_thin_and_dense_audio_keeps_original_rejection(voice):
    n = helpers()
    assert n['_effective_short_edit_target'](OPTIONS, 30, voice) == 30
    assert n['_short_preview_voice_duration_qc']({'duration_after_fit': voice}, 30)['pass'] is False


@pytest.mark.parametrize('options,requested', [
    ({'mode': 'preview', 'format': 'shorts'}, 30),
    ({'mode': 'production', 'format': 'landscape'}, 30),
    ({'mode': 'production'}, 30), ({}, 30), (None, 30),
    (OPTIONS, 29), (OPTIONS, 31), (OPTIONS, 60), (OPTIONS, '30'), (OPTIONS, True),
])
def test_only_exact_production_short_scope_changes_endpoint(options, requested):
    before = deepcopy(options)
    assert helpers()['_effective_short_edit_target'](options, requested, 26.208) == requested
    assert options == before


def test_actual_saved_voice_reuses_identical_file_and_scene_durations(tmp_path):
    n = helpers()
    path = tmp_path / 'candidate.mp3'
    path.write_bytes(b'unchanged retained narration')
    source = {'path': str(path), 'duration_before_fit': 26.208, 'duration_after_fit': 26.208,
              'scene_durations': [4.368] * 6, 'spoken_texts': ['Unchanged sentence.'] * 6,
              'tempo_rate': 1.0, 'content_target_seconds': 29.5}
    before = deepcopy(source)
    process = Mock(side_effect=AssertionError('No audio transform or new TTS allowed'))
    voice_tree = ast.parse((ROOT / 'app/services/voice.py').read_text(encoding='utf-8'))
    definitions = [node for node in voice_tree.body if isinstance(node, ast.FunctionDef)
                   and node.name in {'_fit_duration', 'fit_existing_narration_candidate'}]
    n.update({'Path': Path, '_media_duration': lambda _: 26.208,
              'subprocess': SimpleNamespace(run=process, DEVNULL=-3),
              'VoiceScriptFitError': RuntimeError, '_SHORT_NARRATION_MIN_TEMPO': .93})
    exec(compile(ast.Module(body=definitions, type_ignores=[]), '<real voice functions>', 'exec'), n)
    target = n['_effective_short_edit_target'](OPTIONS, 30, source['duration_after_fit'])
    result = n['fit_existing_narration_candidate'](source, target)
    assert source == before
    assert result == before
    assert path.read_bytes() == b'unchanged retained narration'
    process.assert_not_called()


def test_actual_saved_voice_pipeline_supplies_effective_target_before_fit(tmp_path):
    n = helpers()
    voice = {'path': 'same-candidate.mp3', 'duration_after_fit': 26.208,
             'scene_durations': [4.368] * 6, 'tempo_rate': 1.0}
    before = deepcopy(voice)
    fit = Mock(return_value=voice)
    forbidden = Mock(side_effect=AssertionError('No new voice request'))
    block = next(node for node in ast.walk(PIPELINE) if isinstance(node, ast.With)
                 and any(isinstance(item.optional_vars, ast.Name)
                         and item.optional_vars.id == 'stage_pool' for item in node.items))
    n.update(ThreadPoolExecutor=ThreadPoolExecutor, options=dict(OPTIONS), duration_minutes=.5,
             selected_recovery=None,
             saved_voice_retry={'voice_result': voice}, voice_replacement_request=None,
             recovered_voice=None, curated_source_job=None, scenes=[{}] * 6, language='tr',
             task_id='child', package={'scenes': [{}] * 6}, work=tmp_path,
             strict_short_preview_duration=False, pexels_orientation='portrait',
             _fit_saved_voice_for_retry=fit, _synthesize_voice_candidate=forbidden,
             _collect_broll=Mock(return_value={'scene_visuals': []}), _checkpoint_audio_candidate=Mock())
    exec(compile(ast.Module(body=[block], type_ignores=[]), str(SOURCE), 'exec'), n)
    fit.assert_called_once_with(voice, 803 / 30)
    assert voice == before
    assert n['voice_result'] is voice
    forbidden.assert_not_called()


@pytest.mark.parametrize('transcript_pass,prosody_pass', [(True, True), (False, True), (True, False)])
def test_real_audio_loop_never_skips_transcript_or_prosody(transcript_pass, prosody_pass):
    from test_audio_retry_duration_veto import _namespace, _run, QualityError
    n = _namespace(26.208, transcript_pass)
    n.update(options=dict(OPTIONS), saved_voice_retry=True)
    n['verify_audio_prosody'].return_value = {'available': True, 'pass': prosody_pass,
                                            'reason': None if prosody_pass else 'unnatural_pacing'}
    if transcript_pass and prosody_pass:
        _run(n)
        assert n['effective_edit_target_seconds'] == pytest.approx(803 / 30)
    else:
        with pytest.raises(QualityError, match='Audio narration QA rejected'):
            _run(n)
    n['_synthesize_voice_candidate'].assert_not_called()
    assert n['_verify_audio_narration_with_retry'].call_count == 1
    assert n['verify_audio_prosody'].call_count == int(transcript_pass)
    assert n['voice_result']['duration_after_fit'] == 26.208


def test_actual_renderer_call_and_strict_gate_share_effective_target(tmp_path):
    n = helpers()
    target = n['_effective_short_edit_target'](OPTIONS, 30, 26.208)
    body = next(node.body for node in PIPELINE.body if isinstance(node, ast.Try)
                and any(isinstance(item, ast.Assign) and any(isinstance(t, ast.Name)
                        and t.id == 'requested_seconds' for t in item.targets) for item in node.body))
    start = next(i for i, node in enumerate(body) if isinstance(node, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == 'requested_seconds' for t in node.targets))
    stop = next(i for i, node in enumerate(body) if isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == 'max_freeze_seconds' for t in node.targets))
    render = Mock(return_value={'duration': target, 'frame_count': 803, 'ending_silence_seconds': target - 26.208})
    durations = [4.368] * 6
    n.update(options=dict(OPTIONS), duration_minutes=.5, effective_edit_target_seconds=target,
             selected_recovery=None,
             self=SimpleNamespace(), task_id='child', set_stage=Mock(), render_video=render,
             final_audio_path='same-candidate.mp3', visual_specs=['clips'],
             package={'narration': 'Same immutable narration.'}, work=tmp_path, scenes=[{}] * 6,
             scene_durations=durations, scene_visuals=[['clip']] * 6,
             resolution_for_mode=lambda *_: '1080x1920', voice_result={'duration_after_fit': 26.208},
             strict_short_preview_duration=False)
    exec(compile(ast.Module(body=body[start:stop], type_ignores=[]), str(SOURCE), 'exec'), n)
    assert n['requested_seconds'] == 30
    assert n['final_render_qc']['pass'] is True
    assert n['final_render_qc']['expected_frames'] == 803
    assert render.call_args.kwargs['target_duration'] == target
    assert render.call_args.kwargs['scene_durations'] is durations
    assert render.call_args.kwargs['voice_path'] == 'same-candidate.mp3'


def test_bad_final_frame_or_excessive_silence_still_rejected():
    n = helpers()
    target = n['_effective_short_edit_target'](OPTIONS, 30, 26.208)
    for metrics in ({'frame_count': 900, 'ending_silence_seconds': .559},
                    {'frame_count': 803, 'ending_silence_seconds': 3.792},
                    {'frame_count': 803, 'ending_silence_seconds': 0}):
        assert n['_strict_short_preview_render_qc'](metrics, target, 26.208)['pass'] is False


def test_requested_and_effective_metadata_are_distinct_and_not_spec_mutations():
    metadata = next(node for node in ast.walk(PIPELINE) if isinstance(node, ast.Dict)
                    and any(isinstance(key, ast.Constant) and key.value == 'requested_duration_minutes'
                            for key in node.keys))
    fields = {key.value: ast.unparse(value) for key, value in zip(metadata.keys, metadata.values)
              if isinstance(key, ast.Constant)}
    assert fields['requested_duration_minutes'] == 'duration_minutes'
    assert fields['effective_edit_target_seconds'] == 'effective_edit_target_seconds'
    result = next(node for node in ast.walk(PIPELINE) if isinstance(node, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == 'result' for t in node.targets)
                  and isinstance(node.value, ast.Dict))
    assert "'effective_edit_target_seconds': effective_edit_target_seconds" in ast.unparse(result.value)
