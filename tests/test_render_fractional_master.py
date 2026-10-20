"""The real concat encoder must preserve every frame on FFmpeg 6 and 7."""
import json
import shutil
import subprocess

import pytest

from app.services import render


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='Native media tools required')
@pytest.mark.parametrize('voice_seconds,target_frames', [(36.072,1099),(29.52,900),(29.999,916)])
def test_six_scene_fractional_master_has_exact_clock_frames_and_voice(tmp_path, monkeypatch, voice_seconds, target_frames):
    def ffmpeg(*args):
        subprocess.run(['ffmpeg','-y','-v','error',*args], capture_output=True, check=True, timeout=60)
    voice = tmp_path/'voice.wav'
    ffmpeg('-f','lavfi','-i',f'sine=frequency=440:sample_rate=48000:duration={voice_seconds}',
           '-c:a','pcm_s16le',str(voice))
    def normalized(_spec, output, duration, *_args, **_kwargs):
        ffmpeg('-f','lavfi','-i','testsrc2=size=64x64:rate=30','-frames:v',str(round(duration*30)),
               '-an','-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p',str(output))
        assert render.video_frame_count(output) == round(duration*30)
        return str(output)
    monkeypatch.setattr(render, 'normalize_clip', normalized)
    scenes = [{'narration':'Test scene.'} for _ in range(6)]
    master = tmp_path/'final.mp4'
    result = render.render_video(voice,['sample'], 'Test narration.',master,
        target_duration=target_frames/30, scenes=scenes,
        scene_durations=[5.05848681940642,6.980431673061431,5.389649623445882,
                         6.8653751097244555,7.178529060372047,4.602629713989768],
        scene_visual_paths=[['sample']]*6,capture_scene_windows=True)
    assert result['frame_count'] == render.video_frame_count(master) == target_frames
    assert render._video_timeline_duration(master) == pytest.approx(target_frames/30,abs=.00001)
    probe = json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0',
        '-show_entries','stream=r_frame_rate,avg_frame_rate:packet=pts_time','-of','json',str(master)],text=True))
    assert probe['streams'][0]['r_frame_rate'] == probe['streams'][0]['avg_frame_rate'] == '30/1'
    pts = sorted(float(packet['pts_time']) for packet in probe['packets'])
    assert len(pts) == target_frames
    assert all(abs(t-i/30)<.000001 for i,t in enumerate(pts))
    assert 0 <= result['ending_silence_seconds'] <= target_frames/30-voice_seconds+.04
