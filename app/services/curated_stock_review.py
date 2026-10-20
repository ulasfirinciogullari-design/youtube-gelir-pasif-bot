"""Local QA-only cuts with the ordinary renderer's exact shot geometry.

The returned files are critic inputs, not replacements for the original render
sources. No provider, score, approval, voice, or persistent state is changed.
"""
from __future__ import annotations

import math
from pathlib import Path

from app.services import render
from app.services.visual_allocation_checkpoint import _work_path


def exact_review_visuals(*, scenes: list[dict], scene_visuals: list[list[dict]],
                         scene_durations: list[float], voice_path: str | Path,
                         work_dir: str | Path, natural_short: bool = False) -> list[list[dict]]:
    """Normalize each pinned raw shot exactly once for independent final QA."""
    try:
        requested_work = Path(work_dir)
        work = _work_path(requested_work.name.split('_attempt_')[0], requested_work)
        if (not isinstance(scenes, list) or len(scenes) != 6
                or not isinstance(scene_visuals, list) or len(scene_visuals) != 6
                or not isinstance(scene_durations, list) or len(scene_durations) != 6):
            raise ValueError('Invalid exact-cut shape')
        for pool in scene_visuals:
            if (not isinstance(pool, list) or len(pool) != 1 or not isinstance(pool[0], dict)
                    or pool[0].get('curated_pinned') is not True
                    or pool[0].get('preserve_start_fraction') is not True):
                raise ValueError('Missing pinned exact-cut source')
            spec = pool[0]
            path = Path(spec['path'])
            if (not path.is_absolute() or '..' in path.parts or path.resolve(strict=True) != path
                    or not path.is_relative_to(work) or not path.is_file()):
                raise ValueError('Invalid exact-cut source path')
            fraction = spec.get('start_fraction')
            if type(fraction) not in (int, float) or not math.isfinite(fraction) or not 0 <= fraction <= 0.95:
                raise ValueError('Invalid pinned exact-cut fraction')
        if any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0
               for value in scene_durations):
            raise ValueError('Invalid exact-cut timing')
        voice = Path(voice_path)
        if voice != work / 'recovered_voice.mp3' or voice.resolve(strict=True) != voice:
            raise ValueError('Invalid exact-cut voice path')
        duration = float(render.media_duration(voice))
        if type(natural_short) is not bool or not math.isfinite(duration) or not 28.7 <= duration <= (40 if natural_short else 30.08):
            raise ValueError('Invalid exact-cut voice duration')
        timeline = render._scene_timeline(
            scenes, scene_visuals, scene_durations, duration,
            [pool[0] for pool in scene_visuals],
        )
        counts = render._timeline_frame_counts(timeline, duration)
        if len(timeline) != 6 or [shot[3] for shot in timeline] != list(range(6)):
            raise ValueError('Exact-cut timeline changed scene ownership')
        destination = work / 'curated_exact_review'
        destination.mkdir(exist_ok=False)
        result = []
        for index, (spec, _duration, transition, _scene_index) in enumerate(timeline):
            path = destination / f'norm_{index:03d}.mp4'
            render.normalize_clip(spec, path, counts[index] / render.FPS, index,
                                  transition, '1080x1920')
            # The critic sees the actual crop/speed/start window. The worker
            # retains raw inputs so final rendering never applies them twice.
            proxy = dict(spec)
            proxy.update(path=str(path), start_fraction=0.0,
                         preserve_start_fraction=True, forbid_loop=True)
            result.append([proxy])
        return result
    except Exception:
        raise RuntimeError('Curated exact-cut review preparation unavailable') from None
