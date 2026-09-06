"""Local-only saved-take retiming, including real duration and pitch evidence."""
from array import array
import ast
from copy import deepcopy
import math
from pathlib import Path
import shutil
import subprocess

import pytest

from test_voice import voice_module as voice


def _candidate(path, duration, prior=1.0):
    return {'path': str(path), 'scene_durations': [duration / 6] * 6,
            'duration_before_fit': duration * prior, 'duration_after_fit': duration,
            'tempo_rate': prior, 'spoken_texts': ['Same immutable words.'] * 6,
            'voice_model': 'eleven_multilingual_v2', 'voice_language_code': 'tr',
            'audio_qc': {'pass': True}, 'audio_prosody_qc': {'pass': True},
            'audio_duration_qc': {'pass': True}}


def _duration_qa(result):
    path = Path(__file__).resolve().parents[1] / 'app' / 'tasks.py'
    node = next(node for node in ast.parse(path.read_text(encoding='utf-8')).body
                if isinstance(node, ast.FunctionDef) and node.name == '_short_preview_voice_duration_qc')
    namespace = {'math': math}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace[node.name](result, 30.0)


def _no_tts(*args, **kwargs):
    raise AssertionError('Saved candidate retiming must not call TTS')


@pytest.mark.parametrize('before,rate', [(26.808, .932452), (26.7375, .93),
                                        (27.025, .94), (27.24, .947478)])
def test_measured_deficit_fits_once_and_invalidates_all_prior_qa(tmp_path, monkeypatch, before, rate):
    path = tmp_path / 'candidate.mp3'
    path.write_bytes(b'original')
    candidate = _candidate(path, before)
    original = deepcopy(candidate)
    durations = iter([before, 28.776, 28.776])
    monkeypatch.setattr(voice, '_media_duration', lambda _path: next(durations))
    calls = []
    def ffmpeg(command, **kwargs):
        calls.append(command)
        Path(command[-1]).write_bytes(b'fit')
    monkeypatch.setattr(voice.subprocess, 'run', ffmpeg)
    monkeypatch.setattr(voice, 'synthesize_voice_with_timestamps', _no_tts)
    result = voice.fit_existing_narration_candidate(candidate, 30)
    assert voice.fit_existing_narration_candidate(result, 30) == result
    assert candidate == original
    assert len(calls) == 1
    assert f'atempo={rate:.6f}' in calls[0]
    assert result['tempo_rate'] == rate
    assert sum(result['scene_durations']) == pytest.approx(28.776)
    assert result['spoken_texts'] == original['spoken_texts']
    assert result['voice_model'] == original['voice_model']
    assert _duration_qa(result)['pass'] is True
    assert not {'audio_qc', 'audio_prosody_qc', 'audio_duration_qc'} & result.keys()


@pytest.mark.parametrize('before', [25.272, 26.7374])
def test_thinner_take_is_unchanged_and_still_rejected(tmp_path, monkeypatch, before):
    path = tmp_path / 'candidate.mp3'
    path.write_bytes(b'original')
    monkeypatch.setattr(voice, '_media_duration', lambda _path: before)
    monkeypatch.setattr(voice.subprocess, 'run', _no_tts)
    result = voice.fit_existing_narration_candidate(_candidate(path, before), 30)
    assert path.read_bytes() == b'original'
    assert result['tempo_rate'] == 1
    assert result['duration_after_fit'] == before
    assert _duration_qa(result)['pass'] is False


@pytest.mark.parametrize('duration,prior', [(26.808, .99), (27.24, .98)])
def test_cumulative_budget_cannot_be_reset_by_another_child(tmp_path, monkeypatch, duration, prior):
    path = tmp_path / 'candidate.mp3'
    path.write_bytes(b'original')
    monkeypatch.setattr(voice, '_media_duration', lambda _path: duration)
    monkeypatch.setattr(voice.subprocess, 'run', _no_tts)
    with pytest.raises(voice.VoiceScriptFitError, match='cumulative tempo'):
        voice.fit_existing_narration_candidate(_candidate(path, duration, prior), 30)
    assert path.read_bytes() == b'original'


@pytest.mark.parametrize('duration', [28.7, 28.72, 28.75, 29.5])
def test_valid_audio_remains_unchanged_even_at_lower_cumulative_floor(tmp_path, monkeypatch, duration):
    path = tmp_path / 'candidate.mp3'
    path.write_bytes(b'original')
    monkeypatch.setattr(voice, '_media_duration', lambda _path: duration)
    monkeypatch.setattr(voice.subprocess, 'run', _no_tts)
    result = voice.fit_existing_narration_candidate(_candidate(path, duration, .93), 30)
    assert result['tempo_rate'] == .93
    assert result['duration_after_fit'] == duration
    assert path.read_bytes() == b'original'


@pytest.mark.parametrize('prior', [.929999, True, float('nan'), float('inf')])
def test_invalid_cumulative_rate_rejects_before_read_or_write(tmp_path, monkeypatch, prior):
    monkeypatch.setattr(voice, '_media_duration', _no_tts)
    monkeypatch.setattr(voice.subprocess, 'run', _no_tts)
    with pytest.raises(voice.VoiceScriptFitError):
        voice.fit_existing_narration_candidate(_candidate(tmp_path / 'candidate.mp3', 28.75, prior), 30)


def _tone_frequency(path):
    decoded = subprocess.run(['ffmpeg', '-v', 'error', '-ss', '5', '-i', str(path),
                              '-t', '10', '-ac', '1', '-ar', '8000', '-f', 's16le', 'pipe:1'],
                             capture_output=True, check=True, timeout=30)
    samples = array('h', decoded.stdout)
    assert len(samples) >= 79000
    crossings = sum(left <= 0 < right for left, right in zip(samples, samples[1:]))
    return crossings / (len(samples) / 8000)


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'),
                    reason='Real saved-audio retiming requires FFmpeg and FFprobe')
def test_real_ffmpeg_short_fit_preserves_pitch_and_duration_without_tts(tmp_path, monkeypatch):
    path = tmp_path / 'candidate.mp3'
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
                    'sine=frequency=440:sample_rate=48000:duration=26.784',
                    '-c:a', 'libmp3lame', '-b:a', '192k', str(path)],
                   capture_output=True, check=True, timeout=30)
    before = voice._media_duration(path)
    assert 26.7375 <= before < 27.025
    old_bytes = path.read_bytes()
    original_pitch = _tone_frequency(path)
    monkeypatch.setattr(voice, 'synthesize_voice_with_timestamps', _no_tts)
    result = voice.fit_existing_narration_candidate(_candidate(path, before), 30)
    fitted_bytes = path.read_bytes()
    assert fitted_bytes != old_bytes
    assert 28.7 <= voice._media_duration(path) <= 29.75
    assert _duration_qa(result)['pass'] is True
    assert result['tempo_rate'] == round(before / 28.75, 6)
    assert sum(result['scene_durations']) == pytest.approx(result['duration_after_fit'])
    assert _tone_frequency(path) == pytest.approx(original_pitch, abs=.8)
    assert original_pitch == pytest.approx(440, abs=.8)
    repeated = voice.fit_existing_narration_candidate(result, 30)
    assert repeated == result
    assert path.read_bytes() == fitted_bytes
