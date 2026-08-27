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


def _caption_chunks(narration: str, max_words: int = 6) -> list[str]:
    clauses = [p.strip() for p in re.split(r'(?<=[.!?…])\s+|(?<=[,;:])\s+', narration) if p.strip()]
    chunks: list[str] = []
    for clause in clauses or [narration.strip()]:
        words = clause.split()
        while words:
            take = words[:max_words]
            words = words[max_words:]
            chunks.append(' '.join(take))
    return [c for c in chunks if c]


def make_scene_srt(scenes: list[dict], scene_durations: list[float], total_duration: float, output_path: str | Path) -> str:
    raw_total = sum(scene_durations) or total_duration or 1.0
    scale = total_duration / raw_total if raw_total else 1.0
    cursor = 0.0
    lines: list[str] = []
    caption_index = 1

    for scene_idx, scene in enumerate(scenes):
        if scene_idx >= len(scene_durations):
            break
        scene_duration = max(0.2, scene_durations[scene_idx] * scale)
        narration = str(scene.get('narration') or '').strip()
        parts = _caption_chunks(narration)
        if not parts:
            cursor += scene_duration
            continue
        weights = [max(len(p.replace(' ', '')), 1) for p in parts]
        total_weight = sum(weights) or 1
        local_cursor = cursor
        scene_end = min(total_duration, cursor + scene_duration)
        for part_idx, (part, weight) in enumerate(zip(parts, weights)):
            if part_idx == len(parts) - 1:
                end = scene_end
            else:
                end = min(scene_end, local_cursor + scene_duration * weight / total_weight)
            if end <= local_cursor:
                continue
            lines.extend([
                str(caption_index),
                f'{_srt_timestamp(local_cursor)} --> {_srt_timestamp(end)}',
                part,
                '',
            ])
            caption_index += 1
            local_cursor = end
        cursor = scene_end

    path = Path(output_path)
    path.write_text('\n'.join(lines), encoding='utf-8')
    return str(path)


def make_srt(narration: str, total_duration: float, output_path: str | Path) -> str:
    return make_scene_srt([{'narration': narration}], [total_duration], total_duration, output_path)


def _pace_target(scene: dict | None) -> float:
    pace = str((scene or {}).get('pace') or 'normal').lower()
    if pace == 'fast':
        return 1.65
    if pace == 'slow':
        return 3.6
    return 2.45


def normalize_clip(
    input_path: str | Path,
    output_path: str | Path,
    duration: float,
    shot_index: int,
    transition: str = 'cut',
) -> str:
    offsets = [
        '(iw-1920)/2:(ih-1080)/2',
        '0:(ih-1080)/2',
        '(iw-1920):(ih-1080)/2',
        '(iw-1920)/2:0',
        '(iw-1920)/2:(ih-1080)',
    ]
    crop_xy = offsets[shot_index % len(offsets)]
    speed = 1.012 + (shot_index % 4) * 0.008
    filters = [
        'scale=2070:1165:force_original_aspect_ratio=increase',
        f'crop=1920:1080:{crop_xy}',
        'fps=30',
        f'setpts=PTS/{speed:.3f}',
    ]
    if transition == 'dip' and duration >= 0.8:
        fade_out = max(0.2, duration - 0.16)
        filters.extend([
            'fade=t=in:st=0:d=0.10',
            f'fade=t=out:st={fade_out:.3f}:d=0.14',
        ])
    filters.append('format=yuv420p')
    _run([
        'ffmpeg', '-y', '-stream_loop', '-1', '-i', str(input_path),
        '-t', f'{duration:.3f}', '-vf', ','.join(filters),
        '-an', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '19', str(output_path),
    ])
    return str(output_path)


def _build_shot_order(visual_paths: list[str], desired_shots: int) -> list[str]:
    if not visual_paths:
        return []
    if len(visual_paths) == 1:
        return [visual_paths[0]] * desired_shots
    ordered: list[str] = []
    cycle = 0
    while len(ordered) < desired_shots:
        batch = visual_paths[:] if cycle % 2 == 0 else list(reversed(visual_paths))
        rotate = cycle % len(batch)
        batch = batch[rotate:] + batch[:rotate]
        ordered.extend(batch)
        cycle += 1
    return ordered[:desired_shots]


def _scene_timeline(
    scenes: list[dict],
    scene_visual_paths: list[list[str]],
    scene_durations: list[float],
    voice_duration: float,
    fallback_visuals: list[str],
) -> list[tuple[str, float, str, int]]:
    raw_total = sum(scene_durations) or voice_duration or 1.0
    scale = voice_duration / raw_total
    timeline: list[tuple[str, float, str, int]] = []

    for idx, raw_duration in enumerate(scene_durations):
        duration = max(0.4, raw_duration * scale)
        scene = scenes[idx] if idx < len(scenes) else {}
        paths = scene_visual_paths[idx] if idx < len(scene_visual_paths) else []
        paths = [p for p in paths if p] or fallback_visuals
        if not paths:
            continue
        target = _pace_target(scene)
        desired = max(1, int(math.ceil(duration / target)))
        order = _build_shot_order(paths, desired)
        per_shot = duration / len(order)
        transition = str(scene.get('transition') or 'cut').lower()
        for path in order:
            timeline.append((path, per_shot, transition, idx))
    return timeline


def render_video(
    voice_path: str | Path,
    visual_paths: list[str],
    narration: str,
    output_path: str | Path,
    scenes: list[dict] | None = None,
    scene_durations: list[float] | None = None,
    scene_visual_paths: list[list[str]] | None = None,
) -> dict:
    if not visual_paths:
        raise RuntimeError('No visual clips were provided to renderer')

    output = Path(output_path)
    work = output.parent
    work.mkdir(parents=True, exist_ok=True)
    voice_duration = media_duration(voice_path)

    if scenes and scene_durations and scene_visual_paths:
        timeline = _scene_timeline(scenes, scene_visual_paths, scene_durations, voice_duration, visual_paths)
    else:
        desired_shots = max(1, int(math.ceil(voice_duration / 2.6)))
        order = _build_shot_order(visual_paths, desired_shots)
        per_shot = voice_duration / len(order)
        timeline = [(path, per_shot, 'cut', 0) for path in order]

    if not timeline:
        raise RuntimeError('Renderer could not build a visual timeline')

    normalized: list[Path] = []
    for idx, (clip, shot_duration, transition, _scene_idx) in enumerate(timeline):
        seg = work / f'norm_{idx:03d}.mp4'
        normalize_clip(clip, seg, shot_duration + 0.05, idx, transition)
        normalized.append(seg)

    concat_file = work / 'concat.txt'
    concat_file.write_text('\n'.join(f"file '{p.as_posix()}'" for p in normalized), encoding='utf-8')
    silent_video = work / 'silent.mp4'
    _run([
        'ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', str(concat_file),
        '-t', f'{voice_duration:.3f}', '-c', 'copy', str(silent_video)
    ])

    srt = work / 'captions.srt'
    if scenes and scene_durations:
        make_scene_srt(scenes, scene_durations, voice_duration, srt)
    else:
        make_srt(narration, voice_duration, srt)

    # ONE text layer only. No separate overlay text is rendered. This prevents
    # duplicate/mismatched text and guarantees captions come from narration.
    caption_filter = (
        f"subtitles={srt.as_posix()}:force_style='FontName=DejaVu Sans,FontSize=31,Bold=1,"
        "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=1,"
        "Alignment=2,MarginV=72'"
    )

    _run([
        'ffmpeg', '-y', '-i', str(silent_video), '-i', str(voice_path),
        '-vf', caption_filter,
        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18',
        '-c:a', 'aac', '-b:a', '192k', '-shortest', '-movflags', '+faststart', str(output),
    ])

    pace_counts = {'fast': 0, 'normal': 0, 'slow': 0}
    transition_counts = {'cut': 0, 'match': 0, 'dip': 0}
    for scene in scenes or []:
        pace = str(scene.get('pace') or 'normal').lower()
        transition = str(scene.get('transition') or 'cut').lower()
        if pace in pace_counts:
            pace_counts[pace] += 1
        if transition in transition_counts:
            transition_counts[transition] += 1

    return {
        'path': str(output),
        'duration': voice_duration,
        'shots': len(timeline),
        'unique_visuals': len(set(visual_paths)),
        'resolution': '1920x1080',
        'scene_synced': bool(scenes and scene_durations and scene_visual_paths),
        'pace_counts': pace_counts,
        'transition_counts': transition_counts,
        'overlays_used': 0,
        'single_text_layer': True,
        'srt': str(srt),
    }
