from pathlib import Path
import math
import re
import subprocess


def _run(cmd: list[str]):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def media_duration(path: str | Path) -> float:
    out = subprocess.check_output([
        'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1', str(path)
    ], text=True).strip()
    return float(out)


def _srt_timestamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, milli = divmod(rem, 1000)
    return f'{h:02}:{m:02}:{s:02},{milli:03}'


def make_srt(narration: str, total_duration: float, output_path: str | Path) -> str:
    # Sentence-level subtitles, timed proportionally to text length.
    parts = [p.strip() for p in re.split(r'(?<=[.!?])\s+', narration) if p.strip()]
    if not parts:
        parts = [narration.strip()]
    weights = [max(len(p), 1) for p in parts]
    total_weight = sum(weights)
    cursor = 0.0
    lines = []
    for idx, (part, weight) in enumerate(zip(parts, weights), start=1):
        duration = total_duration * weight / total_weight
        start = cursor
        end = min(total_duration, cursor + duration)
        cursor = end
        lines.extend([
            str(idx),
            f'{_srt_timestamp(start)} --> {_srt_timestamp(end)}',
            part,
            '',
        ])
    path = Path(output_path)
    path.write_text('\n'.join(lines), encoding='utf-8')
    return str(path)


def normalize_clip(input_path: str | Path, output_path: str | Path, duration: float) -> str:
    # Loop short clips if needed, crop to 16:9 and normalize to 720p/30 fps.
    _run([
        'ffmpeg', '-y', '-stream_loop', '-1', '-i', str(input_path),
        '-t', f'{duration:.3f}',
        '-vf', "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,fps=30,format=yuv420p",
        '-an', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20',
        str(output_path),
    ])
    return str(output_path)


def render_video(voice_path: str | Path, visual_paths: list[str], narration: str, output_path: str | Path) -> dict:
    if not visual_paths:
        raise RuntimeError('No visual clips were provided to renderer')

    output = Path(output_path)
    work = output.parent
    work.mkdir(parents=True, exist_ok=True)
    voice_duration = media_duration(voice_path)

    # Keep shots moving: aim around 6 seconds each, but use whatever clips we have.
    desired_shots = max(1, int(math.ceil(voice_duration / 6.0)))
    ordered = [visual_paths[i % len(visual_paths)] for i in range(desired_shots)]
    shot_duration = voice_duration / len(ordered)

    normalized = []
    for idx, clip in enumerate(ordered):
        seg = work / f'norm_{idx:03d}.mp4'
        normalize_clip(clip, seg, shot_duration + 0.10)
        normalized.append(seg)

    concat_file = work / 'concat.txt'
    concat_file.write_text('\n'.join(f"file '{p.as_posix()}'" for p in normalized), encoding='utf-8')
    silent_video = work / 'silent.mp4'
    _run([
        'ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', str(concat_file),
        '-t', f'{voice_duration:.3f}', '-c', 'copy', str(silent_video)
    ])

    srt = work / 'captions.srt'
    make_srt(narration, voice_duration, srt)
    # Burn subtitles for retention. Use a conservative mobile-readable style.
    subtitle_filter = (
        f"subtitles={srt.as_posix()}:force_style='FontName=DejaVu Sans,FontSize=18,"
        "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,"
        "Alignment=2,MarginV=42'"
    )
    _run([
        'ffmpeg', '-y', '-i', str(silent_video), '-i', str(voice_path),
        '-vf', subtitle_filter,
        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '19',
        '-c:a', 'aac', '-b:a', '192k', '-shortest', '-movflags', '+faststart',
        str(output),
    ])
    return {'path': str(output), 'duration': voice_duration, 'shots': len(ordered), 'srt': str(srt)}
