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


def _caption_chunks(narration: str, max_words: int = 8) -> list[str]:
    clauses = [p.strip() for p in re.split(r'(?<=[.!?…])\s+|(?<=[,;:])\s+', narration) if p.strip()]
    chunks: list[str] = []
    for clause in clauses or [narration.strip()]:
        words = clause.split()
        while words:
            take = words[:max_words]
            words = words[max_words:]
            chunks.append(' '.join(take))
    return [c for c in chunks if c]


def make_srt(narration: str, total_duration: float, output_path: str | Path) -> str:
    parts = _caption_chunks(narration)
    weights = [max(len(p.replace(' ', '')), 1) for p in parts]
    total_weight = sum(weights) or 1
    cursor = 0.0
    lines = []
    for idx, (part, weight) in enumerate(zip(parts, weights), start=1):
        duration = max(0.55, total_duration * weight / total_weight)
        start = cursor
        end = min(total_duration, cursor + duration)
        cursor = end
        lines.extend([str(idx), f'{_srt_timestamp(start)} --> {_srt_timestamp(end)}', part, ''])
        if cursor >= total_duration:
            break
    path = Path(output_path)
    path.write_text('\n'.join(lines), encoding='utf-8')
    return str(path)


def normalize_clip(input_path: str | Path, output_path: str | Path, duration: float, shot_index: int) -> str:
    # Slightly overscale and vary crop position so repeated stock footage does not feel static.
    offsets = [
        '(iw-1920)/2:(ih-1080)/2',
        '0:(ih-1080)/2',
        '(iw-1920):(ih-1080)/2',
        '(iw-1920)/2:0',
        '(iw-1920)/2:(ih-1080)',
    ]
    crop_xy = offsets[shot_index % len(offsets)]
    vf = (
        'scale=2050:1153:force_original_aspect_ratio=increase,'
        f'crop=1920:1080:{crop_xy},'
        'fps=30,setpts=PTS/1.025,format=yuv420p'
    )
    _run([
        'ffmpeg', '-y', '-stream_loop', '-1', '-i', str(input_path),
        '-t', f'{duration:.3f}', '-vf', vf,
        '-an', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '19', str(output_path),
    ])
    return str(output_path)


def _build_shot_order(visual_paths: list[str], desired_shots: int) -> list[str]:
    if len(visual_paths) == 1:
        return [visual_paths[0]] * desired_shots
    ordered: list[str] = []
    cycle = 0
    while len(ordered) < desired_shots:
        batch = visual_paths[:] if cycle % 2 == 0 else list(reversed(visual_paths))
        if batch:
            rotate = cycle % len(batch)
            batch = batch[rotate:] + batch[:rotate]
        ordered.extend(batch)
        cycle += 1
    return ordered[:desired_shots]


def render_video(voice_path: str | Path, visual_paths: list[str], narration: str, output_path: str | Path) -> dict:
    if not visual_paths:
        raise RuntimeError('No visual clips were provided to renderer')

    output = Path(output_path)
    work = output.parent
    work.mkdir(parents=True, exist_ok=True)
    voice_duration = media_duration(voice_path)

    target_shot_seconds = 3.2
    desired_shots = max(1, int(math.ceil(voice_duration / target_shot_seconds)))
    ordered = _build_shot_order(visual_paths, desired_shots)
    shot_duration = voice_duration / len(ordered)

    normalized = []
    for idx, clip in enumerate(ordered):
        seg = work / f'norm_{idx:03d}.mp4'
        normalize_clip(clip, seg, shot_duration + 0.08, idx)
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
    subtitle_filter = (
        f"subtitles={srt.as_posix()}:force_style='FontName=DejaVu Sans,FontSize=32,Bold=1,"
        "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=1,"
        "Alignment=2,MarginV=72'"
    )
    _run([
        'ffmpeg', '-y', '-i', str(silent_video), '-i', str(voice_path),
        '-vf', subtitle_filter,
        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18',
        '-c:a', 'aac', '-b:a', '192k', '-shortest', '-movflags', '+faststart', str(output),
    ])
    return {
        'path': str(output),
        'duration': voice_duration,
        'shots': len(ordered),
        'unique_visuals': len(set(visual_paths)),
        'resolution': '1920x1080',
        'srt': str(srt),
    }
