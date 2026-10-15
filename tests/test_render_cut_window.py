import shutil
import subprocess

import pytest

from app.services import render


@pytest.mark.parametrize('index', [0, 1, 2])
def test_late_selected_window_includes_actual_speed_without_loop_or_hold(monkeypatch, index):
    commands = []
    monkeypatch.setattr(render, 'media_duration', lambda _: 8.0)
    monkeypatch.setattr(render, '_run', commands.append)
    monkeypatch.setattr(render, 'video_frame_count', lambda _: 206)
    monkeypatch.setattr(render, 'max_horizontal_letterbox_duration', lambda _: 0)
    render.normalize_clip({'path': 'existing.mp4', 'forbid_loop': True, 'start_fraction': .9},
                          'output.mp4', 206 / 30, index)
    command = commands[0]
    start = float(command[command.index('-ss') + 1])
    speed = 1.008 + index * .006
    assert start + 206 / 30 * speed <= 8.0 - .079
    assert '-stream_loop' not in command and 'tpad' not in command[command.index('-vf') + 1]


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='ffmpeg required')
def test_real_eight_second_source_satisfies_late_206_frame_cut(tmp_path):
    source = tmp_path / 'existing.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=320x180:r=24:d=8',
        '-c:v', 'libx264', '-preset', 'ultrafast', str(source)], check=True, timeout=30)
    output = tmp_path / 'final-cut.mp4'
    render.normalize_clip({'path': str(source), 'forbid_loop': True, 'start_fraction': .9},
                          output, 206 / 30, 2)
    assert render.video_frame_count(output) == 206
    assert render.media_duration(output) == pytest.approx(206 / 30, abs=.001)
