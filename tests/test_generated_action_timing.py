"""A bounded real edit must retain its action ending in QA and production."""
from copy import deepcopy
import hashlib
import json
import shutil
import subprocess

import pytest

from app.services import qa_workprint, render


def generated(path='raw.mp4', **changes):
    return {'path': str(path), 'generated': True, 'source_type': 'generated',
            'generation_provider': 'gemini_veo', 'preserve_start_fraction': True,
            'start_fraction': 0.0, 'forbid_loop': True, **changes}


def test_six_second_action_fits_all_155_frames_at_bounded_precise_speed():
    spec = generated()
    before = deepcopy(spec)
    speed = render._clip_speed(spec, 6.0, 155 / 30, 5)
    assert speed == pytest.approx(1.161290322, abs=1e-10)
    assert speed * (155 / 30) <= 6.0
    assert 6.0 - speed * (155 / 30) < 1e-8
    assert spec == before


@pytest.mark.parametrize('changes', [
    {'generated': False}, {'generated': 1}, {'source_type': 'stock'},
    {'preserve_start_fraction': False}, {'preserve_start_fraction': 1},
    {'start_fraction': .05}, {'start_fraction': False}, {'start_fraction': '0'},
    {'start_fraction': float('nan')}, {'forbid_loop': False}, {'forbid_loop': 1},
    {'synthetic_motion_only': True}, {'synthetic_motion_only': 'false'},
    {'synthetic_motion_only': 0}, {'source_media_type': 'image'},
    {'source_media_type': 'unknown'}, {'generation_provider': 'gemini_image_motion'},
])
def test_only_locked_genuine_zero_start_action_can_change_speed(changes):
    assert render._clip_speed(generated(**changes), 6, 155 / 30, 5) == 1.02


@pytest.mark.parametrize('source,target,index,expected', [
    (6, 5, 5, 1.2), (6.00001, 5, 5, 1.02),
    (8, 5, 5, 1.02), (5, 6, 5, 1.02),
    (5.04, 5, 0, 1.008), (5, 5, 0, 1.008),
    (6, 155 / 30, 0, 1.161290322),
])
def test_fit_is_bounded_and_never_slows_or_rescues_a_short_source(source, target, index, expected):
    assert render._clip_speed(generated(), source, target, index) == pytest.approx(expected)


@pytest.mark.parametrize('source,target', [(float('nan'), 5), (6, 0), (float('inf'), 5)])
def test_invalid_generated_timing_is_rejected(source, target):
    with pytest.raises(RuntimeError, match='timing is invalid'):
        render._clip_speed(generated(), source, target, 5)


def _stub_normalize(monkeypatch, *, source=6, frames=155, bars=0):
    commands = []
    monkeypatch.setattr(render, 'media_duration', lambda _: source)
    monkeypatch.setattr(render, 'video_frame_count', lambda _: frames)
    monkeypatch.setattr(render, 'max_horizontal_letterbox_duration', lambda _: bars)
    monkeypatch.setattr(render, '_run', commands.append)
    return commands


def test_normalizer_uses_precise_single_pass_without_changing_start_or_geometry(monkeypatch):
    commands = _stub_normalize(monkeypatch)
    render.normalize_clip(generated(), 'out.mp4', 155 / 30, 5,
                          output_resolution='1080x1920')
    command = commands[0]
    filters = command[command.index('-vf') + 1]
    assert command[command.index('-ss') + 1] == '0.000'
    assert 'setpts=(PTS-STARTPTS)/1.161290322' in filters
    assert 'crop=1080:1920:(iw-1080)/2:(ih-1920)/2' in filters
    assert 'trim=end_frame=155' in filters
    assert command[command.index('-frames:v') + 1] == '155'
    assert '-stream_loop' not in command and 'tpad=' not in filters


@pytest.mark.parametrize('source,frames,bars,error', [
    (4, 155, 0, 'single-pass'), (6, 154, 0, 'frame gate'),
    (6, 155, 1, 'letterbox gate'),
])
def test_existing_short_source_frame_and_letterbox_gates_remain_mandatory(monkeypatch, source, frames, bars, error):
    commands = _stub_normalize(monkeypatch, source=source, frames=frames, bars=bars)
    monkeypatch.setattr(render, 'detect_symmetric_letterbox_crop', lambda *args, **kwargs: None)
    with pytest.raises(RuntimeError, match=error):
        render.normalize_clip(generated(), 'out.mp4', 155 / 30, 5)
    if source == 4:
        assert commands == []
    assert all('-stream_loop' not in command for command in commands)


def test_nonzero_locked_start_keeps_original_excerpt_window(monkeypatch):
    commands = _stub_normalize(monkeypatch, source=10)
    render.normalize_clip(generated(start_fraction=.4), 'out.mp4', 155 / 30, 5)
    command = commands[0]
    assert command[command.index('-ss') + 1] == '1.933'
    assert 'setpts=(PTS-STARTPTS)/1.020000000' in command[command.index('-vf') + 1]


def _fake_mp4(path):
    path.write_bytes(b'\x00\x00\x00\x18ftypmp42' + b'existing-source' * 30)
    return path


@pytest.mark.parametrize('changes', [
    {}, {'synthetic_motion_only': False, 'source_media_type': 'video'},
    {'synthetic_motion_only': True}, {'source_media_type': 'image'},
    {'generation_provider': 'gemini_image_motion'}, {'start_fraction': .5},
    {'generated': False}, {'source_type': 'stock'},
])
def test_workprint_preserves_identical_allowlisted_timing_and_raw_provenance(tmp_path, changes):
    source = _fake_mp4(tmp_path / 'raw.mp4')
    spec = generated(source, **changes)
    spec.update(url='https://private.invalid/?token=SECRET', headers={'key': 'SECRET'})
    before = deepcopy(spec)
    selected, provenance = qa_workprint._selected([spec], {'best_start_fraction': .8}, tmp_path)
    assert render._clip_speed(selected, 6, 155 / 30, 5) == render._clip_speed(spec, 6, 155 / 30, 5)
    assert selected['start_fraction'] == spec['start_fraction']
    assert provenance['sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert provenance['size'] == source.stat().st_size
    assert spec == before
    assert 'SECRET' not in json.dumps(selected) + json.dumps(provenance)
    assert not {'url', 'headers', 'score', 'qa_approved', 'publish_eligible'} & selected.keys()


@pytest.mark.parametrize('changes', [
    {'generated': 1}, {'preserve_start_fraction': 1}, {'synthetic_motion_only': 'false'},
    {'synthetic_motion_only': 0}, {'source_media_type': 'unknown'},
])
def test_ambiguous_workprint_identity_cannot_silently_enable_full_span_fit(tmp_path, changes):
    source = _fake_mp4(tmp_path / 'raw.mp4')
    with pytest.raises(ValueError, match='render identity'):
        qa_workprint._selected([generated(source, **changes)], {}, tmp_path)


def test_missing_explicit_locked_start_cannot_be_invented_by_workprint(tmp_path):
    source = _fake_mp4(tmp_path / 'raw.mp4')
    spec = generated(source)
    del spec['start_fraction']
    assert render._clip_speed(spec, 6, 155 / 30, 5) == 1.02
    with pytest.raises(ValueError, match='render identity'):
        qa_workprint._selected([spec], {}, tmp_path)


def _pixel(path, timestamp):
    raw = subprocess.check_output([
        'ffmpeg', '-v', 'error', '-ss', str(timestamp), '-i', str(path),
        '-frames:v', '1', '-vf', 'scale=1:1', '-pix_fmt', 'rgb24',
        '-f', 'rawvideo', 'pipe:1',
    ], timeout=30)
    assert len(raw) == 3
    return tuple(raw)


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'),
                    reason='ffmpeg and ffprobe are required')
def test_real_ffmpeg_full_tail_and_initial_action_survive_155_frame_edit_and_workprint(tmp_path):
    source = tmp_path / 'six-second-action.mp4'
    subprocess.run([
        'ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
        'color=c=red:s=320x568:r=24:d=6', '-vf',
        "drawbox=x=0:y=0:w=iw:h=ih:color=lime:t=fill:enable='lt(t,0.25)',"
        "drawbox=x=0:y=0:w=iw:h=ih:color=blue:t=fill:enable='gte(t,5.5)'",
        '-an', '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', str(source),
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30)
    raw_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    spec = generated(source)
    diagnostic_spec, provenance = qa_workprint._selected([spec], {'best_start_fraction': .9}, tmp_path)
    outputs = []
    for name, selected in [('production', spec), ('diagnostic', diagnostic_spec),
                           ('legacy-cut', {'path': str(source), 'forbid_loop': True, 'start_fraction': 0.0})]:
        output = tmp_path / f'{name}.mp4'
        render.normalize_clip(selected, output, 155 / 30, 5,
                              output_resolution='1080x1920')
        assert render.video_frame_count(output) == 155
        assert render.media_duration(output) == pytest.approx(155 / 30, abs=.001)
        outputs.append(output)
    for output in outputs[:2]:
        assert _pixel(output, 0)[1] > 200  # The locked beginning stays visible.
        end = _pixel(output, 154 / 30)
        assert end[2] > 200 and end[0] < 30  # Actual late action, not a held old frame.
    old_end = _pixel(outputs[2], 154 / 30)
    assert old_end[0] > 200 and old_end[2] < 30  # Prior 1.02x edit demonstrably misses it.
    assert hashlib.sha256(outputs[0].read_bytes()).digest() == hashlib.sha256(outputs[1].read_bytes()).digest()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == provenance['sha256'] == raw_sha
