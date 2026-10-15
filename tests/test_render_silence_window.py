"""Final silence is measured on the video timeline, not AAC packet padding."""

import ast
import math
from pathlib import Path
import re
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import render


ROOT = Path(__file__).resolve().parents[1]


def _gate(silence, *, frames=900, voice_duration=28.728):
    path = ROOT / 'app' / 'tasks.py'
    functions = [
        node for node in ast.parse(path.read_text(encoding='utf-8')).body
        if isinstance(node, ast.FunctionDef)
        and node.name in {'_short_preview_voice_duration_qc', '_strict_short_preview_render_qc'}
    ]
    namespace = {'math': math}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['_strict_short_preview_render_qc'](
        {'frame_count': frames, 'ending_silence_seconds': silence}, 30.0, voice_duration,
    )


def _mock_measurement(monkeypatch, *, start=28.481, end=30.016, duration='30.000000', returncode=0):
    completed = SimpleNamespace(returncode=returncode,
                                stderr=f'silence_start: {start}\nsilence_end: {end}\n')
    monkeypatch.setattr(render.subprocess, 'run', Mock(return_value=completed))
    probe = Mock(return_value=duration)
    monkeypatch.setattr(render.subprocess, 'check_output', probe)
    return probe


def test_aac_padding_outside_the_master_is_not_counted_as_final_hold(monkeypatch):
    probe = _mock_measurement(monkeypatch)
    measured = render.ending_silence_duration('master.mp4')
    assert measured == pytest.approx(1.519)
    result = _gate(measured)
    assert result['pass'] is True
    assert result['minimum_ending_silence_seconds'] == 1.152
    assert result['maximum_ending_silence_seconds'] == 1.55
    assert result['expected_frames'] == result['actual_frames'] == 900
    command = probe.call_args.args[0]
    assert command[command.index('-select_streams') + 1] == 'v:0'
    assert command[command.index('-show_entries') + 1] == 'stream=duration'
    assert 'format=duration' not in command


def test_video_stream_wins_even_when_container_duration_includes_longer_audio(monkeypatch):
    _mock_measurement(monkeypatch)
    container = Mock(return_value=30.016)
    monkeypatch.setattr(render, 'media_duration', container)
    assert render.ending_silence_duration('master.mp4') == pytest.approx(1.519)
    container.assert_not_called()


@pytest.mark.parametrize('start,end,expected', [
    (28.728, 30.0, 1.272), (28.4, 30.016, 1.6),
    (29.9, 30.016, 0.1), (30.002, 30.016, 0.0), (20.0, 25.0, 0.0),
])
def test_only_the_last_silence_intersection_with_the_master_is_measured(monkeypatch, start, end, expected):
    _mock_measurement(monkeypatch, start=start, end=end)
    assert render.ending_silence_duration('master.mp4') == pytest.approx(expected)


def test_excessive_silence_and_wrong_frame_count_still_fail_unchanged_gates(monkeypatch):
    _mock_measurement(monkeypatch, start=28.4)
    assert _gate(render.ending_silence_duration('master.mp4'))['reason'] == 'final_ending_silence_out_of_bounds'
    assert _gate(1.519, frames=899)['reason'] == 'final_frame_count_mismatch'
    assert _gate(1.519, voice_duration=25.0)['reason'] == 'final_voice_duration_invalid'


@pytest.mark.parametrize('duration', ['', 'N/A', 'nan', 'inf', '-1', '0'])
def test_unknown_or_invalid_video_timeline_cannot_approve_audio_measurement(monkeypatch, duration):
    _mock_measurement(monkeypatch, duration=duration)
    with pytest.raises(RuntimeError, match='Video timeline duration could not be verified'):
        render.ending_silence_duration('master.mp4')


def test_failed_silence_detection_does_not_trust_partial_logs(monkeypatch):
    _mock_measurement(monkeypatch, returncode=1)
    with pytest.raises(RuntimeError, match='Final audio silence measurement failed'):
        render.ending_silence_duration('master.mp4')


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'),
                    reason='Native AAC regression requires ffmpeg and ffprobe')
def test_native_no_editlist_aac_exposes_padding_without_changing_the_master(tmp_path):
    master = tmp_path / 'synthetic-30s-master.mp4'
    # Only a synthetic tone is used, ending before its 28.728s padded source.
    # Suppress the MP4 edit list to reproduce a native AAC mux variant that
    # exposes encoder padding; keep the picture timestamps fixed. This is
    # not a claim that the deleted production master used this mux variant.
    audio_filter = ','.join([
        'apad=whole_dur=28.728', 'atrim=duration=28.728',
        'aresample=48000', 'apad=whole_dur=30.000', 'atrim=duration=30.000',
        'loudnorm=I=-15:TP=-1.0:LRA=7', 'aresample=48000',
        'atrim=duration=30.000', 'asetpts=N/SR/TB',
    ])
    try:
        subprocess.run([
            'ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
            'color=c=black:s=64x64:r=30:d=30', '-f', 'lavfi', '-i',
            'sine=frequency=1000:sample_rate=48000:duration=28.465',
            '-map', '0:v:0', '-map', '1:a:0', '-frames:v', '900',
            '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
            '-af', audio_filter, '-c:a', 'aac', '-b:a', '192k',
            '-t', '30.000', '-use_editlist', '0', '-avoid_negative_ts', 'disabled',
            '-movflags', '+faststart', str(master),
        ], capture_output=True, text=True, check=True, timeout=90)
    except PermissionError:
        pytest.skip('Native FFmpeg execution is blocked by the local sandbox')
    before = master.read_bytes()
    detected = subprocess.run([
        'ffmpeg', '-hide_banner', '-nostats', '-i', str(master),
        '-map', '0:a:0', '-af', 'silencedetect=noise=-45dB:d=0.10',
        '-vn', '-f', 'null', '-',
    ], capture_output=True, text=True, encoding='utf-8', errors='replace', check=True, timeout=30)
    starts = [float(value) for value in re.findall(r'silence_start:\s*([0-9.]+)', detected.stderr)]
    ends = [float(value) for value in re.findall(r'silence_end:\s*([0-9.]+)', detected.stderr)]
    timeline = render._video_timeline_duration(master)
    assert timeline == pytest.approx(30.0)
    assert render.video_frame_count(master) == 900
    assert ends[-1] > timeline
    raw_silence = ends[-1] - starts[-1]
    measured = render.ending_silence_duration(master)
    assert measured == pytest.approx(timeline - starts[-1])
    assert raw_silence - measured == pytest.approx(ends[-1] - timeline)
    # Padding remains excluded from the measured value even when both values
    # fall inside the separate editorial allowance for a sub-frame difference.
    assert _gate(measured)['pass'] is True
    assert master.read_bytes() == before


@pytest.mark.parametrize('silence,passed', [(.737, True), (.763, True), (.764, False),
                                        (1.0, False), (0.0, False)])
def test_real_margin_master_has_only_one_frame_of_timing_tolerance(silence, passed):
    # Actual saved terminal metrics of 8f0f3347: 900 frames, voice 29.520s,
    # measured closing silence 0.737s. No provider/audio request is repeated.
    result = _gate(silence, voice_duration=29.520)
    assert result['pass'] is passed
    assert result['minimum_ending_silence_seconds'] == .36
    assert result['maximum_ending_silence_seconds'] == .763
    assert _gate(silence, voice_duration=29.520, frames=899)['pass'] is False


def test_subframe_tolerance_never_extends_absolute_silence_cap():
    assert _gate(1.55, voice_duration=28.7)['pass'] is True
    assert _gate(1.55001, voice_duration=28.7)['pass'] is False
