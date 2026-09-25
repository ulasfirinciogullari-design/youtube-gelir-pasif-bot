import hashlib
import subprocess
from unittest.mock import Mock

import pytest

from app.services import framecase_sound as sound, render
from app.services.production_spend import SpendBlocked
from app.tasks import _strict_short_preview_render_qc


def narration_master(tmp_path, audible_seconds):
    path = tmp_path / 'final.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
        'testsrc2=s=180x320:r=30:d=3', '-f', 'lavfi', '-i',
        f'sine=frequency=880:duration={audible_seconds}', '-af', 'apad',
        '-c:v', 'libx264', '-threads', '1', '-c:a', 'aac', '-t', '3', str(path)],
        check=True, capture_output=True)
    return {'path': str(path), 'fps': 30, 'frame_count': render.video_frame_count(path),
        'duration': render.media_duration(path), 'ending_silence_seconds': render.ending_silence_duration(path),
        'scene_windows': [{'scene_index': 0, 'start_frame': 0, 'end_frame': 90}],
        'max_freeze_seconds': 0.0}


def test_actual_room_tone_may_continue_after_verified_narration_tail(tmp_path):
    narrated = narration_master(tmp_path, 2.4)
    original_sha = hashlib.sha256((tmp_path / 'final.mp4').read_bytes()).hexdigest()
    ambience = tmp_path / 'room.wav'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
        'sine=frequency=220:duration=3', str(ambience)], check=True, capture_output=True)
    result = sound.finish_master(narrated, [[{'path': str(ambience)}]], tmp_path,
                                 target_duration=3, voice_duration=2.4)
    assert result['narration_timing_qc']['pass'] is True
    assert .5 < result['narration_timing_qc']['ending_silence_seconds'] < .8
    assert result['ending_silence_seconds'] == 0
    assert render.video_frame_count(result['path']) == 90
    assert result['scene_windows'] == narrated['scene_windows']
    assert hashlib.sha256((tmp_path / 'narration-only-master.mp4').read_bytes()).hexdigest() == original_sha
    # This real, valid mix reproduced the former false rejection: room tone
    # does not represent continuing speech or a missing narration tail.
    assert _strict_short_preview_render_qc(result, 3, 2.4)['reason'] == 'final_ending_silence_out_of_bounds'


@pytest.mark.parametrize('audible_seconds', [3.0, .8])
def test_ambience_cannot_hide_missing_or_excessive_narration_tail(tmp_path, monkeypatch, audible_seconds):
    narrated = narration_master(tmp_path, audible_seconds)
    mixer = Mock(); monkeypatch.setattr(sound, 'add_native_ambience', mixer)
    with pytest.raises(SpendBlocked, match='framecase_final_timing_rejected'):
        sound.finish_master(narrated, [], tmp_path, target_duration=3, voice_duration=2.4)
    mixer.assert_not_called()


def test_narration_frame_mismatch_still_blocks_before_mixing(tmp_path, monkeypatch):
    narrated = narration_master(tmp_path, 2.4)
    narrated['frame_count'] -= 1
    mixer = Mock(); monkeypatch.setattr(sound, 'add_native_ambience', mixer)
    with pytest.raises(SpendBlocked, match='framecase_final_timing_rejected'):
        sound.finish_master(narrated, [], tmp_path, target_duration=3, voice_duration=2.4)
    mixer.assert_not_called()
