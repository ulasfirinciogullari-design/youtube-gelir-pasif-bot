"""Quiet native scene ambience under the unchanged, independently checked voice."""
from pathlib import Path
import hashlib
import json
import math
import re
import subprocess

from app.services.production_spend import SpendBlocked


def narration_timing_qc(rendered, target_duration, voice_duration, *, voice_path=None):
    from app.tasks import _strict_short_preview_render_qc
    timing = _strict_short_preview_render_qc(rendered, target_duration, voice_duration)
    if voice_path is None or timing.get('reason') != 'final_ending_silence_out_of_bounds':
        return timing
    # A qualified take can already contain a natural ending pause. Its file
    # duration is not its last audible instant. Measure the ORIGINAL voice,
    # never infer a longer allowance from the rejected master's own silence.
    from app.services import render
    duration = render.media_duration(voice_path)
    if not math.isfinite(duration) or abs(duration - voice_duration) > .05:
        return {**timing, 'reason': 'source_voice_duration_unverified'}
    measured = subprocess.run(['ffmpeg', '-hide_banner', '-nostats', '-i', str(voice_path),
        '-map', '0:a:0', '-af', 'silencedetect=noise=-45dB:d=0.10', '-vn', '-f', 'null', '-'],
        capture_output=True, text=True, check=True, timeout=60)
    starts = [float(n) for n in re.findall(r'silence_start:\s*([0-9.]+)', measured.stderr)]
    ends = [float(n) for n in re.findall(r'silence_end:\s*([0-9.]+)', measured.stderr)]
    if not starts or not ends or not 0 < starts[-1] < ends[-1] or abs(ends[-1] - duration) > .08:
        return timing
    # Retain the exact frame-count test, minimum hold, natural duration gate
    # and absolute 1.55-second tail cap. No speech or picture bytes are edited.
    observed = _strict_short_preview_render_qc(rendered, target_duration, voice_duration,
        source_audible_end_seconds=starts[-1])
    return {**observed, 'timing_basis': 'measured_original_voice_tail',
        'source_voice_sha256': hashlib.sha256(Path(voice_path).read_bytes()).hexdigest(),
        'source_voice_duration_seconds': duration,
        'source_audible_end_seconds': starts[-1],
        'source_ending_silence_seconds': duration - starts[-1],
        'file_duration_timing_qc': timing}


def finish_master(rendered, selected, work, *, target_duration, voice_duration, voice_path=None):
    # A room tone or rain may continue after the narrator stops. Measure the
    # unchanged narration-only master before mixing, with the same strict tail
    # and frame-count bounds. The mixer verifies the copied picture frames;
    # the pipeline still transcribes the actual final mix before publication.
    timing = narration_timing_qc(rendered, target_duration, voice_duration, voice_path=voice_path)
    if timing.get('pass') is not True:
        raise SpendBlocked('framecase_final_timing_rejected')
    mixed = add_native_ambience(rendered, selected, work)
    return {**mixed, 'narration_timing_qc': timing}


def add_native_ambience(rendered, selected, work):
    from app.services import render
    windows = rendered['scene_windows']
    if not windows or len(windows) != len(selected):
        raise SpendBlocked('framecase_ambience_timing_unverified')
    work = Path(work); inputs = ['-i', str(rendered['path'])]; filters = []
    tracks = []
    for index, (window, candidates) in enumerate(zip(windows, selected), 1):
        path = str(candidates[0]['path'])
        probe = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'a:0',
            '-show_entries', 'stream=codec_type', '-of', 'json', path], capture_output=True,
            check=True, text=True, timeout=15)
        if not json.loads(probe.stdout).get('streams'):
            raise SpendBlocked('framecase_native_ambience_missing')
        count = window['end_frame'] - window['start_frame']
        duration = count / rendered['fps']
        inputs.extend(['-i', path])
        filters.append(f'[{index}:a:0]aresample=48000,apad,atrim=duration={duration:.9f},'
            f'asetpts=N/SR/TB,afade=t=in:d=0.05,afade=t=out:st={max(0, duration-.1):.9f}:d=0.1[a{index}]')
        tracks.append(f'[a{index}]')
    filters.append(''.join(tracks) + f'concat=n={len(tracks)}:v=0:a=1[room]')
    filters.extend(['[room]highpass=f=70,lowpass=f=9000,loudnorm=I=-29:TP=-5:LRA=10,aresample=48000[ambience]',
        '[0:a:0]aresample=48000,asplit=2[voice][detector]',
        '[ambience][detector]sidechaincompress=threshold=0.03:ratio=6:attack=10:release=250[ducked]',
        '[voice][ducked]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,alimiter=limit=0.89:level=false[mix]'])
    output = work / 'film-with-ambience.mp4'
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', *inputs,
        '-filter_complex_threads', '1', '-filter_complex', ';'.join(filters),
        '-map', '0:v:0', '-map', '[mix]', '-c:v', 'copy', '-frames:v', str(rendered['frame_count']),
        '-c:a', 'aac', '-b:a', '192k', '-t', f'{rendered["duration"]:.9f}', '-movflags', '+faststart', str(output)],
        capture_output=True, check=True, timeout=240)
    if render.video_frame_count(output) != rendered['frame_count']:
        raise SpendBlocked('framecase_ambience_timing_unverified')
    original = Path(rendered['path'])
    original.replace(work / 'narration-only-master.mp4')
    output.replace(original)
    # Video is stream-copied. Retain its freeze result and exact scene windows.
    return {**rendered, 'path': str(original), 'duration': render.media_duration(original),
            'ending_silence_seconds': render.ending_silence_duration(original),
            'sound_design': 'native_scene_ambience_ducked_below_narration'}
