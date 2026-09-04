from functools import lru_cache
from pathlib import Path
import math
import re
import subprocess


FPS = 30
LANDSCAPE_RESOLUTION = '1920x1080'
SHORTS_RESOLUTION = '1080x1920'
_RENDER_PROFILES = {
    LANDSCAPE_RESOLUTION: {
        'width': 1920,
        'height': 1080,
        'base_scale': '2050:1153',
        'strong_scale': '2304:1296',
    },
    SHORTS_RESOLUTION: {
        'width': 1080,
        'height': 1920,
        'base_scale': '1153:2050',
        'strong_scale': '1296:2304',
    },
}


def _render_profile(output_resolution: str) -> dict:
    try:
        return _RENDER_PROFILES[str(output_resolution)]
    except KeyError:
        raise ValueError(
            'Output resolution must be 1920x1080 or 1080x1920'
        ) from None


def resolution_for_mode(mode: str, format: str | None = None) -> str:
    """Choose the canvas independently of production/preview quality policy."""
    if format is not None:
        if not isinstance(format, str) or format.strip().lower() not in {'shorts', 'landscape'}:
            raise ValueError('Video format must be shorts or landscape')
        return SHORTS_RESOLUTION if format.strip().lower() == 'shorts' else LANDSCAPE_RESOLUTION
    if str(mode or '').strip().lower() == 'preview':
        return SHORTS_RESOLUTION
    return LANDSCAPE_RESOLUTION


def aspect_ratio_for_mode(mode: str, format: str | None = None) -> str:
    """Keep generated media and the final canvas on the same orientation."""
    if resolution_for_mode(mode, format) == SHORTS_RESOLUTION:
        return '9:16'
    return '16:9'


def _run(cmd: list[str]):
    subprocess.run(
        cmd,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@lru_cache(maxsize=256)
def media_duration(path: str | Path) -> float:
    out = subprocess.check_output([
        'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1', str(path),
    ], text=True).strip()
    return float(out)


def video_frame_count(path: str | Path) -> int:
    out = subprocess.check_output([
        'ffprobe', '-v', 'error', '-count_frames', '-select_streams', 'v:0',
        '-show_entries', 'stream=nb_read_frames',
        '-of', 'default=noprint_wrappers=1:nokey=1', str(path),
    ], text=True).strip()
    try:
        return int(out)
    except (TypeError, ValueError):
        return 0


def ending_silence_duration(path: str | Path, noise_db: int = -45) -> float:
    """Measure only a silence interval that reaches the end of the master."""
    completed = subprocess.run([
        'ffmpeg', '-hide_banner', '-nostats', '-i', str(path),
        '-map', '0:a:0', '-af', f'silencedetect=noise={noise_db}dB:d=0.10',
        '-vn', '-f', 'null', '-',
    ], capture_output=True, text=True, check=False)
    starts = [
        float(value)
        for value in re.findall(r'silence_start:\s*([0-9.]+)', completed.stderr or '')
    ]
    ends = [
        float(value)
        for value in re.findall(r'silence_end:\s*([0-9.]+)', completed.stderr or '')
    ]
    if not starts or not ends:
        return 0.0
    total = media_duration(path)
    if ends[-1] + 0.08 < total:
        return 0.0
    return max(0.0, ends[-1] - starts[-1])


def max_freeze_duration(path: str | Path, minimum_seconds: float = 2.0) -> float:
    """Measure the longest near-static interval in a rendered master."""
    completed = subprocess.run([
        'ffmpeg', '-hide_banner', '-nostats', '-i', str(path),
        '-map', '0:v:0', '-vf',
        f'scale=320:-2,freezedetect=n=-40dB:d={minimum_seconds:.2f}',
        '-an', '-f', 'null', '-',
    ], capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError('Horizontal letterbox inspection failed')
    durations = [
        float(value) for value in re.findall(
            r'lavfi\.freezedetect\.freeze_duration:\s*([0-9.]+)',
            completed.stderr or '',
        )
    ]
    return max(durations, default=0.0)


def max_horizontal_letterbox_duration(
    path: str | Path,
    minimum_seconds: float = 0.25,
    band_height: int = 24,
) -> float:
    """Measure sustained black bands touching both horizontal frame edges."""
    media_path = Path(path)
    if not media_path.is_file():
        return 0.0
    band_height = max(2, int(band_height))
    completed = subprocess.run([
        'ffmpeg', '-hide_banner', '-nostats', '-i', str(media_path),
        '-filter_complex', (
            '[0:v]split=2[top][bottom];'
            f'[top]crop=iw:{band_height}:0:0[top_band];'
            f'[bottom]crop=iw:{band_height}:0:ih-{band_height}[bottom_band];'
            '[top_band][bottom_band]vstack=inputs=2,'
            f'blackdetect=d={minimum_seconds:.2f}:pix_th=0.10:pic_th=0.95'
        ),
        '-an', '-f', 'null', '-',
    ], capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError('Horizontal letterbox inspection failed')
    durations = [
        float(value)
        for value in re.findall(
            r'black_duration:([0-9.]+)',
            completed.stderr or '',
        )
    ]
    return max(durations, default=0.0)


def detect_symmetric_letterbox_crop(
    path: str | Path,
    *,
    start_seconds: float = 0.0,
    sample_seconds: float = 2.5,
) -> tuple[int, int] | None:
    """Return a stable vertical content crop for genuine symmetric bars.

    This is deliberately conservative.  The crop is accepted only when
    FFmpeg repeatedly detects full-width content with near-symmetric top and
    bottom bars.  Dark scenery or a single black frame therefore cannot turn
    into an aggressive automatic crop.
    """
    media_path = Path(path)
    if not media_path.is_file():
        return None
    try:
        dimensions = subprocess.check_output([
            'ffprobe', '-v', 'error', '-select_streams', 'v:0',
            '-show_entries', 'stream=width,height',
            '-of', 'csv=p=0:s=x', str(media_path),
        ], text=True).strip()
        width_text, height_text = dimensions.split('x', 1)
        width, height = int(width_text), int(height_text)
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        return None
    if width < 16 or height < 16:
        return None

    completed = subprocess.run([
        'ffmpeg', '-hide_banner', '-nostats',
        '-ss', f'{max(0.0, float(start_seconds)):.3f}',
        '-i', str(media_path),
        '-t', f'{max(0.5, min(float(sample_seconds), 3.0)):.3f}',
        '-vf', 'fps=6,cropdetect=limit=24:round=2:reset=0',
        '-an', '-f', 'null', '-',
    ], capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        return None

    detections = [
        tuple(int(value) for value in match)
        for match in re.findall(
            r'crop=(\d+):(\d+):(\d+):(\d+)',
            completed.stderr or '',
        )
    ]
    if not detections:
        return None

    minimum_bar = max(4, int(round(height * 0.015)))
    maximum_bar = max(minimum_bar, int(round(height * 0.25)))
    symmetry_tolerance = max(4, int(round(height * 0.02)))
    counts: dict[tuple[int, int], int] = {}
    for detected_width, detected_height, x, y in detections:
        bottom = height - y - detected_height
        if (
            detected_width < width - 4
            or x > 2
            or detected_height < int(round(height * 0.55))
            or y < minimum_bar
            or bottom < minimum_bar
            or y > maximum_bar
            or bottom > maximum_bar
            or abs(y - bottom) > symmetry_tolerance
        ):
            continue
        key = (detected_height, y)
        counts[key] = counts.get(key, 0) + 1

    if not counts:
        return None
    crop, occurrences = max(counts.items(), key=lambda item: item[1])
    if occurrences < 3 or occurrences * 2 < len(detections):
        return None
    return crop


def _srt_timestamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, milli = divmod(rem, 1000)
    return f'{h:02}:{m:02}:{s:02},{milli:03}'


def _caption_chunks(narration: str, max_words: int = 7) -> list[str]:
    clauses = [
        part.strip()
        for part in re.split(r'(?<=[.!?…])\s+|(?<=[,;:])\s+', narration)
        if part.strip()
    ]
    raw: list[str] = []
    for clause in clauses or [narration.strip()]:
        words = clause.split()
        while words:
            take = words[:max_words]
            words = words[max_words:]
            raw.append(' '.join(take))

    chunks: list[str] = []
    for part in (candidate for candidate in raw if candidate):
        if (
            chunks
            and len(part.split()) < 3
            and len((chunks[-1] + ' ' + part).split()) <= max_words + 2
        ):
            chunks[-1] = f'{chunks[-1]} {part}'
        else:
            chunks.append(part)
    if len(chunks) > 1 and len(chunks[-1].split()) < 3:
        chunks[-2] = f'{chunks[-2]} {chunks[-1]}'
        chunks.pop()
    return chunks


def make_scene_srt(
    scenes: list[dict],
    scene_durations: list[float],
    total_duration: float,
    output_path: str | Path,
) -> str:
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
        weights = [max(len(part.replace(' ', '')), 1) for part in parts]
        total_weight = sum(weights) or 1
        local_cursor = cursor
        scene_end = min(total_duration, cursor + scene_duration)
        for part_idx, (part, weight) in enumerate(zip(parts, weights)):
            if part_idx == len(parts) - 1:
                end = scene_end
            else:
                end = min(
                    scene_end,
                    local_cursor + scene_duration * weight / total_weight,
                )
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


def make_srt(
    narration: str,
    total_duration: float,
    output_path: str | Path,
) -> str:
    return make_scene_srt(
        [{'narration': narration}],
        [total_duration],
        total_duration,
        output_path,
    )


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


def _spec_forbids_loop(spec: str | dict) -> bool:
    return bool(isinstance(spec, dict) and spec.get('forbid_loop'))


def normalize_clip(
    visual_spec: str | dict,
    output_path: str | Path,
    duration: float,
    shot_index: int,
    transition: str = 'cut',
    output_resolution: str = LANDSCAPE_RESOLUTION,
) -> str:
    """Render one chosen excerpt and never loop generated action footage."""
    input_path = _spec_path(visual_spec)
    if not input_path:
        raise RuntimeError('Visual spec is missing a path')

    profile = _render_profile(output_resolution)
    output_width = int(profile['width'])
    output_height = int(profile['height'])

    source_duration = max(0.1, media_duration(input_path))
    fraction = _spec_start_fraction(visual_spec)
    max_start = max(0.0, source_duration - duration - 0.08)
    desired_center = source_duration * fraction
    start_seconds = min(
        max_start,
        max(0.0, desired_center - duration * 0.40),
    )

    center_x = f'(iw-{output_width})/2'
    center_y = f'(ih-{output_height})/2'
    if output_height > output_width:
        # A 9:16 master often receives 16:9 source footage. Keep reframing
        # close to the source centre so a deterministic variation cannot cut
        # the subject completely out of a narrow Shorts canvas.
        offsets = [
            f'{center_x}:{center_y}',
            f'(iw-{output_width})*0.42:{center_y}',
            f'(iw-{output_width})*0.58:{center_y}',
            f'{center_x}:0',
            f'{center_x}:(ih-{output_height})',
        ]
    else:
        offsets = [
            f'{center_x}:{center_y}',
            f'0:{center_y}',
            f'(iw-{output_width}):{center_y}',
            f'{center_x}:0',
            f'{center_x}:(ih-{output_height})',
        ]
    crop_xy = offsets[shot_index % len(offsets)]
    speed = 1.008 + (shot_index % 3) * 0.006
    segment_frames = max(1, int(round(duration * FPS)))
    forbid_loop = _spec_forbids_loop(visual_spec)
    required_source_end = start_seconds + duration * speed + 0.04
    if forbid_loop and source_duration + 0.04 < required_source_end:
        raise RuntimeError(
            'Generated clip is too short for a single-pass scene: '
            f'{source_duration:.3f}s source for {duration:.3f}s segment'
        )

    input_args = ['-i', input_path]
    if not forbid_loop:
        input_args = ['-stream_loop', '-1', '-i', input_path]

    def render_attempt(
        scale_geometry: str,
        source_crop: tuple[int, int] | None = None,
    ) -> None:
        filters = []
        if source_crop is not None:
            crop_height, crop_y = source_crop
            filters.append(f'crop=iw:{crop_height}:0:{crop_y}')
        filters.extend([
            f'scale={scale_geometry}:force_original_aspect_ratio=increase',
            f'crop={output_width}:{output_height}:{crop_xy}',
            'setsar=1',
            f'setpts=(PTS-STARTPTS)/{speed:.3f}',
            # The speed transform already zero-bases timestamps. Rewriting
            # PTS a second time after trim makes FFmpeg drop the last frame
            # for valid fractional targets such as 124/30 seconds.
            f'fps={FPS}',
            f'trim=end_frame={segment_frames}',
        ])
        if transition == 'dip' and duration >= 1.2:
            fade_out = max(0.3, duration - 0.18)
            filters.extend([
                'fade=t=in:st=0:d=0.12',
                f'fade=t=out:st={fade_out:.3f}:d=0.16',
            ])
        filters.append('format=yuv420p')
        _run([
            'ffmpeg', '-y', '-ss', f'{start_seconds:.3f}', *input_args,
            '-vf', ','.join(filters),
            '-frames:v', str(segment_frames), '-an',
            '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '19',
            str(output_path),
        ])
        actual_frames = video_frame_count(output_path)
        if actual_frames != segment_frames:
            raise RuntimeError(
                'Normalized clip frame gate rejected segment: '
                f'{actual_frames} frames for {segment_frames} frame target'
            )

    render_attempt(str(profile['base_scale']))
    if max_horizontal_letterbox_duration(output_path) > 0.25:
        # Some otherwise usable generated clips arrive with cinematic black
        # bars encoded into the picture. One bounded stronger overscan removes
        # them without paying for or looping another generated clip.
        render_attempt(str(profile['strong_scale']))
        if max_horizontal_letterbox_duration(output_path) > 0.25:
            source_crop = detect_symmetric_letterbox_crop(
                input_path,
                start_seconds=start_seconds,
                sample_seconds=min(duration * speed, 2.5),
            )
            if source_crop is not None:
                # A measured crop is safer than blind zooming: it removes only
                # stable, symmetric encoded bars and the same hard output gate
                # below verifies that the result is actually clean.
                render_attempt(str(profile['base_scale']), source_crop)
            if (
                source_crop is None
                or max_horizontal_letterbox_duration(output_path) > 0.25
            ):
                raise RuntimeError(
                    'Normalized clip letterbox gate rejected persistent black bars'
                )
    return str(output_path)


def _scene_timeline(
    scenes: list[dict],
    scene_visual_paths: list[list[str | dict]],
    scene_durations: list[float],
    voice_duration: float,
    fallback_visuals: list[str | dict],
) -> list[tuple[str | dict, float, str, int]]:
    """Use long, intentional scene shots. Do not manufacture rapid cuts."""
    raw_total = sum(scene_durations) or voice_duration or 1.0
    scale = voice_duration / raw_total
    timeline: list[tuple[str | dict, float, str, int]] = []

    for idx, raw_duration in enumerate(scene_durations):
        duration = max(0.4, raw_duration * scale)
        scene = scenes[idx] if idx < len(scenes) else {}
        specs = (
            scene_visual_paths[idx]
            if idx < len(scene_visual_paths)
            else []
        )
        specs = [spec for spec in specs if _spec_path(spec)] or fallback_visuals
        if not specs:
            continue

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


def _timeline_frame_counts(
    timeline: list[tuple[str | dict, float, str, int]],
    voice_duration: float,
) -> list[int]:
    """Allocate CFR frames cumulatively so independent rounding cannot drift."""
    shot_count = len(timeline)
    total_frames = max(shot_count, int(round(voice_duration * FPS)))
    raw_total = sum(max(0.0, shot[1]) for shot in timeline) or 1.0
    counts: list[int] = []
    previous_end = 0
    cumulative = 0.0

    for idx, shot in enumerate(timeline):
        cumulative += max(0.0, shot[1])
        remaining = shot_count - idx - 1
        if idx == shot_count - 1:
            frame_end = total_frames
        else:
            proportional_end = int(round(total_frames * cumulative / raw_total))
            frame_end = max(previous_end + 1, proportional_end)
            frame_end = min(frame_end, total_frames - remaining)
        counts.append(frame_end - previous_end)
        previous_end = frame_end
    return counts


def render_video(
    voice_path: str | Path,
    visual_paths: list[str | dict],
    narration: str,
    output_path: str | Path,
    scenes: list[dict] | None = None,
    scene_durations: list[float] | None = None,
    scene_visual_paths: list[list[str | dict]] | None = None,
    target_duration: float | None = None,
    output_resolution: str = LANDSCAPE_RESOLUTION,
) -> dict:
    if not visual_paths:
        raise RuntimeError('No visual clips were provided to renderer')

    _render_profile(output_resolution)

    output = Path(output_path)
    work = output.parent
    work.mkdir(parents=True, exist_ok=True)
    voice_duration = media_duration(voice_path)
    master_duration = (
        float(target_duration)
        if target_duration and target_duration > 0
        else voice_duration
    )
    if target_duration and voice_duration > master_duration + 0.08:
        raise RuntimeError(
            'Narration exceeds the fixed master duration: '
            f'{voice_duration:.3f}s voice for {master_duration:.3f}s master'
        )
    target_frames = max(1, int(round(master_duration * FPS)))

    if scenes and scene_durations and scene_visual_paths:
        timeline = _scene_timeline(
            scenes,
            scene_visual_paths,
            scene_durations,
            voice_duration,
            visual_paths,
        )
    else:
        desired_shots = max(1, int(math.ceil(voice_duration / 5.0)))
        chosen = visual_paths[:desired_shots] or visual_paths[:1]
        per_shot = voice_duration / len(chosen)
        timeline = [(spec, per_shot, 'cut', 0) for spec in chosen]

    if not timeline:
        raise RuntimeError('Renderer could not build a visual timeline')

    normalized: list[Path] = []
    timeline_frame_counts = _timeline_frame_counts(timeline, voice_duration)
    for idx, (visual_spec, _shot_duration, transition, _scene_idx) in enumerate(timeline):
        segment = work / f'norm_{idx:03d}.mp4'
        segment_duration = timeline_frame_counts[idx] / FPS
        normalize_clip(
            visual_spec,
            segment,
            segment_duration,
            idx,
            transition,
            output_resolution,
        )
        normalized.append(segment)

    silent_video = work / 'silent.mp4'
    voice_frames = sum(timeline_frame_counts)
    pad_frames = max(0, target_frames - voice_frames)

    # Decode every normalized segment as an independent input.  The concat
    # demuxer can stop cleanly at a mid-list MP4 boundary when otherwise valid
    # H.264 segments carry different container metadata, leaving FFmpeg with a
    # successful but truncated master.  The concat filter joins decoded CFR
    # streams instead, while the exact frame gate below remains authoritative.
    input_args: list[str] = []
    segment_filters: list[str] = []
    segment_labels: list[str] = []
    for idx, path in enumerate(normalized):
        input_args.extend(['-i', str(path)])
        label = f'v{idx}'
        segment_labels.append(f'[{label}]')
        segment_filters.append(
            f'[{idx}:v]trim=end_frame={timeline_frame_counts[idx]},'
            f'settb=expr=1/{FPS},setpts=N,'
            f'setsar=1,format=yuv420p[{label}]'
        )

    if len(normalized) == 1:
        joined_label = segment_labels[0]
    else:
        segment_filters.append(
            ''.join(segment_labels)
            + f'concat=n={len(normalized)}:v=1:a=0[joined]'
        )
        joined_label = '[joined]'

    master_filters = []
    if pad_frames:
        master_filters.append(
            f'tpad=stop_mode=clone:stop={pad_frames}'
        )
    master_filters.append(f'trim=end_frame={target_frames}')
    master_filters.extend([
        f'settb=expr=1/{FPS}',
        'setpts=N',
        'setsar=1',
        'format=yuv420p',
    ])
    segment_filters.append(
        joined_label + ','.join(master_filters) + '[master]'
    )
    filter_complex = ';'.join(segment_filters)
    _run([
        'ffmpeg', '-y', *input_args,
        '-filter_complex', filter_complex, '-map', '[master]',
        '-frames:v', str(target_frames), '-an',
        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '19',
        str(silent_video),
    ])
    silent_frames = video_frame_count(silent_video)
    if silent_frames != target_frames:
        raise RuntimeError(
            'Silent master frame gate rejected render: '
            f'{silent_frames} frames for {target_frames} frame target'
        )

    srt = work / 'captions.srt'
    if scenes and scene_durations:
        make_scene_srt(scenes, scene_durations, voice_duration, srt)
    else:
        make_srt(narration, voice_duration, srt)

    audio_filter = ','.join([
        'aresample=48000',
        f'apad=whole_dur={master_duration:.3f}',
        f'atrim=duration={master_duration:.3f}',
        'loudnorm=I=-15:TP=-1.0:LRA=7',
        'aresample=48000',
        f'atrim=duration={master_duration:.3f}',
        'asetpts=N/SR/TB',
    ])
    _run([
        'ffmpeg', '-y', '-i', str(silent_video), '-i', str(voice_path),
        '-map', '0:v:0', '-map', '1:a:0',
        '-c:v', 'copy', '-frames:v', str(target_frames),
        '-af', audio_filter, '-c:a', 'aac', '-b:a', '192k',
        '-t', f'{master_duration:.3f}', '-movflags', '+faststart', str(output),
    ])
    final_frames = video_frame_count(output)
    if final_frames != target_frames:
        raise RuntimeError(
            'Final master frame gate rejected render: '
            f'{final_frames} frames for {target_frames} frame target'
        )
    final_duration = media_duration(output)

    return {
        'path': str(output),
        'duration': final_duration,
        'frame_count': final_frames,
        'fps': FPS,
        'ending_silence_seconds': ending_silence_duration(output),
        'max_freeze_seconds': max_freeze_duration(output),
        'shots': len(timeline),
        'unique_visuals': len({
            _spec_path(spec)
            for spec in visual_paths
            if _spec_path(spec)
        }),
        'resolution': output_resolution,
        'scene_synced': bool(
            scenes and scene_durations and scene_visual_paths
        ),
        'text_layers': 0,
        'burned_subtitles': False,
        'caption_format': 'srt',
        'srt': str(srt),
    }
