import shutil
import subprocess

import pytest

from app.services import render


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='Native media tools required')
def test_thirty_scene_master_preserves_frames_and_voice_with_bounded_decoder_pools(tmp_path, monkeypatch):
    def ffmpeg(*args):
        subprocess.run(['ffmpeg', '-v', 'error', '-y', *args], capture_output=True, check=True, timeout=90)
    voice = tmp_path / 'voice.wav'
    ffmpeg('-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=179.016',
        '-c:a', 'pcm_s16le', str(voice))
    def normalized(_spec, path, seconds, *_args, **_kwargs):
        ffmpeg('-f', 'lavfi', '-i', 'testsrc2=size=64x64:rate=30', '-frames:v', str(round(seconds * 30)),
            '-an', '-c:v', 'libx264', '-threads', '1', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', str(path))
        return str(path)
    commands = []; original = render._run
    def observed(command):
        commands.append(command); original(command)
    monkeypatch.setattr(render, 'normalize_clip', normalized)
    monkeypatch.setattr(render, '_run', observed)
    counts = [166,178,172,161,151,175,147,154,176,193,169,206,207,177,206,
              158,181,179,173,194,188,180,180,171,186,188,190,213,170,181]
    result = render.render_video(voice, ['clip'], 'Narration.', tmp_path / 'final.mp4',
        scenes=[{'narration': 'A scene.'} for _ in counts], scene_durations=[n / 30 for n in counts],
        scene_visual_paths=[['clip']] * 30, capture_scene_windows=True)
    assert result['frame_count'] == render.video_frame_count(result['path']) == 5370
    assert render._video_timeline_duration(result['path']) == pytest.approx(179, abs=.00001)
    assert result['shots'] == len(result['scene_windows']) == 30
    assert result['scene_windows'][-1]['end_frame'] == 5370
    assert result['ending_silence_seconds'] <= .04
    assert len(commands) == 2 and commands[0].count('-i') == 30
    for command in commands:
        assert command[command.index('-filter_complex_threads') + 1] == '1'
        assert all(command[index - 2:index] == ['-threads', '1']
            for index, value in enumerate(command) if value == '-i')
        assert command[-3:-1] == ['-threads', '1']
    assert commands[0][commands[0].index('-crf') + 1] == '19'
