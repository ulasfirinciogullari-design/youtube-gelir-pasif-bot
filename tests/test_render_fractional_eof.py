"""A real 24fps source must not lose its last frame at a fractional edit end."""
import hashlib
import shutil
import subprocess

import pytest

from app.services import render
from test_generated_action_timing import generated


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='ffmpeg required')
def test_real_source_with_small_container_tail_keeps_all_151_frames(tmp_path):
    source = tmp_path / 'six-second-clip.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
        'testsrc2=s=320x180:r=24:d=6', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=6.02',
        '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac', str(source)],
        check=True, timeout=30)
    assert render.media_duration(source) == pytest.approx(6.02, abs=.002)
    assert render._video_timeline_duration(source) == 6.0
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    speed = render._clip_speed(generated(source), render.media_duration(source), 151 / 30, 4)
    old = tmp_path / 'previous-rounding.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-i', str(source), '-vf',
        f'setpts=(PTS-STARTPTS)/{speed:.9f},fps=30,trim=end_frame=151',
        '-frames:v', '151', '-an', '-c:v', 'libx264', '-preset', 'ultrafast', str(old)],
        check=True, timeout=30)
    assert render.video_frame_count(old) == 150  # Reproduces the failing frame gate.
    output = tmp_path / 'complete-cut.mp4'
    render.normalize_clip(generated(source), output, 151 / 30, 4)
    assert render.video_frame_count(output) == 151
    assert render.media_duration(output) == pytest.approx(151 / 30, abs=.001)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
