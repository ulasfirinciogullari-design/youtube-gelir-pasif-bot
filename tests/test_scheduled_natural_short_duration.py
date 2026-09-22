"""Keep a complete natural automatic narration; still require all final gates."""
import ast
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from test_production_short_effective_duration import helpers, SOURCE, PIPELINE
from test_audio_retry_duration_veto import _namespace, _run, QualityError

OPTIONS = {'mode': 'production', 'format': 'shorts', 'production_scheduled': True}


def voice_fit(seconds):
    n = helpers()
    tree = ast.parse((SOURCE.parent / 'services/voice.py').read_text())
    function = next(v for v in tree.body if isinstance(v, ast.FunctionDef) and v.name == '_fit_duration')
    process = Mock(side_effect=AssertionError('Existing natural voice must not be compressed'))
    n.update(Path=Path, _media_duration=lambda p: seconds, subprocess=SimpleNamespace(run=process, DEVNULL=-3),
             VoiceScriptFitError=RuntimeError, _SHORT_NARRATION_MIN_TEMPO=.93)
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<actual voice fit>', 'exec'), n)
    return n, process


@pytest.mark.parametrize('seconds', [29.76, 30.1, 33.2, 36.108, 39.45])
def test_natural_take_and_scene_times_survive_and_final_frame_gate_matches(tmp_path, seconds):
    n, process = voice_fit(seconds)
    audio = tmp_path / 'take.mp3'; audio.write_bytes(b'original already synthesized voice')
    scenes = [seconds / 6] * 6; before = deepcopy(scenes)
    durations, old, new, rate = n['_fit_duration'](audio, scenes, 30, flexible_short=True)
    assert durations == before and old == new == seconds and rate == 1
    assert audio.read_bytes() == b'original already synthesized voice'
    process.assert_not_called()
    target = n['_effective_short_edit_target'](OPTIONS, 30, seconds)
    assert 30 < target <= 40 and .55 - 1e-12 <= target - seconds < .584
    assert n['_short_preview_voice_duration_qc']({'duration_after_fit': seconds}, target)['pass'] is True
    rendered = {'frame_count': round(target * 30), 'ending_silence_seconds': target - seconds}
    assert n['_strict_short_preview_render_qc'](rendered, target, seconds)['pass'] is True
    rendered['frame_count'] -= 1
    assert n['_strict_short_preview_render_qc'](rendered, target, seconds)['pass'] is False


@pytest.mark.parametrize('options', [
    {'mode': 'preview', 'format': 'shorts', 'production_scheduled': True},
    {'mode': 'production', 'format': 'shorts'},
    {'mode': 'production', 'format': 'shorts', 'production_scheduled': 'true'},
    {'mode': 'production', 'format': 'landscape', 'production_scheduled': True},
])
def test_manual_fixed_preview_and_other_formats_keep_duration_contract(options):
    assert helpers()['_effective_short_edit_target'](options, 30, 36.108) == 30
    n, process = voice_fit(36.108)
    with pytest.raises(RuntimeError, match='tempo'):
        n['_fit_duration'](Path('/unused.mp3'), [36.108], 30)
    process.assert_not_called()


@pytest.mark.parametrize('seconds', [39.45001, 41, 60])
def test_excessive_takes_still_require_new_script(seconds):
    n, _ = voice_fit(seconds)
    with pytest.raises(RuntimeError, match='tempo'):
        n['_fit_duration'](Path('/unused.mp3'), [seconds], 30, flexible_short=True)
    assert n['_effective_short_edit_target'](OPTIONS, 30, seconds) == 30


@pytest.mark.parametrize('transcript,prosody', [(True, True), (False, True), (True, False)])
def test_actual_audio_loop_still_requires_transcript_and_prosody(transcript, prosody):
    n = _namespace(36.108, transcript)
    n.update(options=dict(OPTIONS), audio_generation_attempts=3)
    n['verify_audio_prosody'].return_value = {'available': True, 'pass': prosody, 'reason': 'observed_quality'}
    if transcript and prosody:
        _run(n)
        assert n['effective_edit_target_seconds'] == pytest.approx(36.6666666667)
    else:
        with pytest.raises(QualityError, match='Audio narration QA rejected'): _run(n)
    assert n['voice_result']['duration_after_fit'] == 36.108
    n['_synthesize_voice_candidate'].assert_not_called()
    assert n['_verify_audio_narration_with_retry'].call_count == 1
    assert n['verify_audio_prosody'].call_count == int(transcript)


def test_actual_final_duration_gate_uses_selected_endpoint_for_scheduled_short():
    block = next(node for node in ast.walk(PIPELINE) if isinstance(node, ast.If)
                 and any(isinstance(c, ast.Name) and c.id == 'duration_ok' for c in ast.walk(node))
                 and "options.get('mode') == 'preview'" == ast.unparse(node.test))
    n = {'options': OPTIONS, 'requested_seconds': 30, 'effective_edit_target_seconds': 36.6666666667,
         'actual_seconds': 36.6666666667}
    exec(compile(ast.Module(body=[block], type_ignores=[]), str(SOURCE), 'exec'), n)
    assert n['duration_ok'] is True
