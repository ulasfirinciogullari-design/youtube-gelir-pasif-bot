"""Real encoded footage distinguishes a dark scene from an encoded black matte."""
import shutil
import subprocess

import pytest

from app.services.render import max_horizontal_letterbox_duration


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg required')
@pytest.mark.parametrize('bars', [False, True])
def test_dark_blue_picture_is_not_a_black_matte(tmp_path, bars):
    path = tmp_path / 'scene.mp4'
    picture = 'color=c=0x0d0d18:s=320x568:r=30:d=1.2'
    filters = 'drawbox=x=60:y=120:w=200:h=300:color=0x657898:t=fill'
    if bars:
        filters += ',drawbox=x=0:y=0:w=iw:h=32:color=black:t=fill'
        filters += ',drawbox=x=0:y=ih-32:w=iw:h=32:color=black:t=fill'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', picture,
                    '-vf', filters, '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(path)],
                   check=True, capture_output=True, timeout=20)
    measured = max_horizontal_letterbox_duration(path)
    assert measured > 1.0 if bars else measured == 0.0
