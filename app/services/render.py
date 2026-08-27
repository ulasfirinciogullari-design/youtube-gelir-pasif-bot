from functools import lru_cache
from pathlib import Path
import math
import re
import subprocess


def _run(cmd: list[str]):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@lru_cache(maxsize=256)
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
    """Create a sidecar SRT. It is never burned into the master video."""
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


def _spec_path(spec: str | dict) -> str:
    if isinstance(spec, dict):
        return str(spec.get('path') or '')
    return str(spec)


def _spec_start_fraction(spec: str | dict) -> float:
    if not isinstance(spec, dict):
        return 0.25
    try:
        value = float(spec.get('start_fraction', 0.25))
    except Exception:
        value = 0.25
    return max(0.0, min(value, 0.95))


def normalize_clip(
    visual_spec: str | dict,
    output_path: str | Path,
    duration: float,
    shot_index: int,
    transition: str = 'cut',
) -> str:
    """Render one deliberately chosen excerpt, rather than always starting at 0:00."""
    input_path = _spec_path(visual_spec)
    if not input_path:
        raise RuntimeError('Visual spec is missing a path')

    source_duration = max(0.1, media_duration(input_path))
    fraction = _spec_start_fraction(visual_spec)
    max_start = max(0.0, source_duration - duration - 0.08)
    desired_center = source_duration * fraction
    start_seconds = min(max_start, max(0.0, desired_center - duration * 0.40))

    offsets = [
        '(iw-1920)/2:(ih-1080)/2',
        '0:(ih-1080)/2',
        '(iw-1920):(ih-1080)/2',
        '(iw-1920)/2:0',
        '(iw-1920)/2:(ih-1080)',
    ]
    crop_xy = offsets[shot_index % len(offsets)]
    speed = 1.008 + (shot_index % 3) * 0.006
    filters = [
        'scale=2050:1153:force_original_aspect_ratio=increase',
        f'crop=1920:1080:{crop_xy}',
        'fps=30',
        f'setpts=PTS/{speed:.3f}',
    ]
    if transition == 'dip' and duration >= 1.2:
        fade_out = max(0.3, duration - 0.18)
        filters.extend([
            'fade=t=in:st=0:d=0.12',
            f'fade=t=out:st={fade_out:.3f}:d=0.16',
        ])
    filters.append('format=yuv420p')

    _run([
        'ffmpeg', '-y', '-ss', f'{start_seconds:.3f}', '-stream_loop', '-1', '-i', input_path,
        '-t', f'{duration:.3f}', '-vf', ','.join(filters),
        '-an', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '19', str(output_path),
    ])
    return str(output_path)


def _scene_timeline(
    scenes: list[dict],
    scene_visual_paths: list[list[str | dict]],
    scene_durations: list[float],
    voice_duration: float,
    fallback_visuals: list[str | dict],
) -> list[tuple[str | dict, float, str, int]]:
    """Use long, intentional scene shots. Do not manufacture rapid cuts from one clip."""
    raw_total = sum(scene_durations) or voice_duration or 1.0
    scale = voice_duration / raw_total
    timeline: list[tuple[str | dict, float, str, int]] = []

    for idx, raw_duration in enumerate(scene_durations):
        duration = max(0.4, raw_duration * scale)
        scene = scenes[idx] if idx < len(scenes) else {}
        specs = scene_visual_paths[idx] if idx < len(scene_visual_paths) else []
        specs = [spec for spec in specs if _spec_path(spec)] or fallback_visuals
        if not specs:
            continue

        # One hero shot per short scene; at most two for a genuinely long scene.
        if duration < 6.5 or len(specs) == 1:
            chosen = specs[:1]
        elif duration < 11.0:
            chosen = specs[:2]
        else:
            chosen = specs[:3]

        per_shot = duration / len(chosen)
        transition = str(scene.get('transition') or 'cut').lower()
        for spec in chosen:
            timeline.append((spec, per_shot, transition, idx))
    return timeline


def render_video(
    voice_path: str | Path,
    visual_paths: list[str | dict],
    narration: str,
    output_path: str | Path,
    scenes: list[dict] | None = None,
    scene_durations: list[float] | None = None,
    scene_visual_paths: list[list[str | dict]] | None = None,
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
        desired_shots = max(1, int(math.ceil(voice_duration / 5.0)))
        chosen = visual_paths[:desired_shots] or visual_paths[:1]
        per_shot = voice_duration / len(chosen)
        timeline = [(spec, per_shot, 'cut', 0) for spec in chosen]

    if not timeline:
        raise RuntimeError('Renderer could not build a visual timeline')

    normalized: list[Path] = []
    for idx, (visual_spec, shot_duration, transition, _scene_idx) in enumerate(timeline):
        seg = work / f'norm_{idx:03d}.mp4'
        normalize_clip(visual_spec, seg, shot_duration + 0.05, idx, transition)
        normalized.append(seg)

    concat_file = work / 'concat.txt'
    concat_file.write_text('\n'.join(f"file '{p.as_posix()}'" for p in normalized), encoding='utf-8')
    silent_video = work / 'silent.mp4'
    _run([
        'ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', str(concat_file),
        '-t', f'{voice_duration:.3f}', '-c', 'copy', str(silent_video)
    ])

    # Captions are exported as a sidecar SRT for YouTube language tracks. The
    # master MP4 contains zero text layers.
    srt = work / 'captions.srt'
    if scenes and scene_durations:
        make_scene_srt(scenes, scene_durations, voice_duration, srt)
    else:
        make_srt(narration, voice_duration, srt)

    # No video re-encode and no subtitle filter: faster, cleaner master output.
    _run([
        'ffmpeg', '-y', '-i', str(silent_video), '-i', str(voice_path),
        '-map', '0:v:0', '-map', '1:a:0',
        '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
        '-shortest', '-movflags', '+faststart', str(output),
    ])

    return {
        'path': str(output),
        'duration': voice_duration,
        'shots': len(timeline),
        'unique_visuals': len({_spec_path(spec) for spec in visual_paths if _spec_path(spec)}),
        'resolution': '1920x1080',
        'scene_synced': bool(scenes and scene_durations and scene_visual_paths),
        'text_layers': 0,
        'burned_subtitles': False,
        'caption_format': 'srt',
        'srt': str(srt),
    }
