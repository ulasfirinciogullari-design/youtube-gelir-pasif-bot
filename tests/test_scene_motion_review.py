from copy import deepcopy
from pathlib import Path
import shutil
import subprocess

import pytest

from app.services import render
from app.services.scene_motion_review import review_long_motion


@pytest.fixture
def footage(tmp_path):
    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg unavailable')
    paths = {}
    for name, source in [('static', 'color=c=gray:s=320x180:r=30:d=8'),
                         ('moving', 'testsrc2=s=320x180:r=30:d=8')]:
        path = tmp_path / (name + '.mp4')
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i', source,
                        '-threads', '1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(path)],
                       check=True, capture_output=True)
        paths[name] = path
    return paths


def test_motion_probe_includes_freeze_that_reaches_last_frame(footage):
    assert 7.9 <= render.max_freeze_duration(footage['static']) <= 8.1
    assert render.max_freeze_duration(footage['moving']) < 6


def test_motion_probe_measures_closed_interval_before_motion(footage, tmp_path):
    target = tmp_path / 'joined.mp4'
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', str(footage['static']),
                    '-i', str(footage['moving']), '-filter_complex_threads', '1',
                    '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0[v]', '-map', '[v]',
                    '-threads', '1', '-c:v', 'libx264', str(target)], check=True, capture_output=True)
    assert 7.9 <= render.max_freeze_duration(target) <= 8.1


def test_static_selected_cut_is_repaired_before_master_without_approving_semantics(footage, tmp_path):
    reviews = {13: {'scene_index': 13, 'score': 91, 'best_candidate_index': 0,
                    'best_start_fraction': .5, 'reason': 'Subject and action are visible.'}}
    pools = [[{'path': str(footage['moving'])}] for _ in range(30)]
    pools[13] = [{'path': str(footage['static'])}]
    originals = deepcopy((reviews, pools))
    kwargs = dict(scene_visuals=pools, scenes=[{'transition': 'cut'} for _ in range(30)],
                  scene_durations=[7.2] * 30, voice_duration=216.,
                  options={'mode': 'production', 'format': 'landscape', 'quality_threshold': 86},
                  duration_minutes=3, work=tmp_path)
    rejected = review_long_motion(reviews, **kwargs)
    assert rejected[13]['score'] < 86 and rejected[13]['motion_gate_passed'] is False
    assert rejected[13]['motion_review']['max_freeze_seconds'] > 7
    assert (reviews, pools) == originals
    assert not list((tmp_path / 'motion_review').glob('*.mp4'))
    pools[13] = [{'path': str(footage['moving'])}]
    repaired = review_long_motion(reviews, **kwargs)
    assert repaired[13]['score'] == 91 and repaired[13]['motion_review']['pass'] is True
    poor = {13: {**reviews[13], 'score': 35}}
    assert review_long_motion(poor, **kwargs) == poor


def test_short_and_other_formats_keep_their_existing_quality_path(tmp_path):
    reviews = {0: {'scene_index': 0, 'score': 90}}
    result = review_long_motion(reviews, scene_visuals=[[{'path': '/absent'}]],
        scenes=[{}], scene_durations=[7.2], voice_duration=7.2,
        options={'mode': 'production', 'format': 'shorts'}, duration_minutes=.5, work=tmp_path)
    assert result is reviews and not list(tmp_path.iterdir())
