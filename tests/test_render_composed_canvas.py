"""Authored clues at the edge must survive the actual normalized edit."""
import subprocess

import pytest

from app.services import render


def test_real_composed_portrait_keeps_all_four_clue_borders(tmp_path):
    source = tmp_path / 'designed-canvas.mp4'
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
        'color=c=0x203040:s=360x640:r=30:d=2', '-vf',
        'drawbox=x=0:y=0:w=iw:h=12:color=red:t=fill,'
        'drawbox=x=0:y=628:w=iw:h=12:color=lime:t=fill,'
        'drawbox=x=0:y=12:w=12:h=616:color=blue:t=fill,'
        'drawbox=x=348:y=12:w=12:h=616:color=yellow:t=fill',
        '-c:v', 'libx264', '-threads', '1', str(source)], check=True, capture_output=True)
    output = tmp_path / 'complete-canvas.mp4'
    render.normalize_clip({'path': str(source), 'preserve_composition': True,
        'start_fraction': 0, 'forbid_loop': True}, output, 1, 2,
        output_resolution='1080x1920')
    assert render.video_frame_count(output) == 30
    rgb = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(output),
        '-frames:v', '1', '-vf', 'scale=360:640', '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'])
    def pixel(x, y):
        return tuple(rgb[(y * 360 + x) * 3:(y * 360 + x) * 3 + 3])
    r, g, b = pixel(180, 4); assert r > 200 and g < 40 and b < 40
    r, g, b = pixel(180, 635); assert g > 200 and r < 40 and b < 40
    r, g, b = pixel(4, 320); assert b > 200 and r < 40 and g < 40
    r, g, b = pixel(355, 320); assert r > 200 and g > 200 and b < 40


def test_composed_canvas_still_rejects_black_bars_without_destructive_zoom(monkeypatch):
    calls = []
    monkeypatch.setattr(render, 'media_duration', lambda _: 2)
    monkeypatch.setattr(render, '_run', calls.append)
    monkeypatch.setattr(render, 'video_frame_count', lambda _: 30)
    monkeypatch.setattr(render, 'max_horizontal_letterbox_duration', lambda _: 1)
    with pytest.raises(RuntimeError, match='letterbox gate'):
        render.normalize_clip({'path': 'canvas.mp4', 'preserve_composition': True},
            'out.mp4', 1, 0, output_resolution='1080x1920')
    assert len(calls) == 1
