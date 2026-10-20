"""Reject a stationary selected documentary cut while scene repair is available.

The measured cut uses the final renderer's geometry, time window and frame
allocation. This can only reject a semantic approval; it cannot approve one.
The complete master still passes the existing six-second motion gate.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from app.services import render


def review_long_motion(reviews, *, scene_visuals, scenes, scene_durations,
                       voice_duration, options, duration_minutes, work,
                       default_fraction=.25):
    if not (options.get('mode') == 'production' and options.get('format') == 'landscape'
            and duration_minutes == 3 and len(scenes) == len(scene_durations) == len(scene_visuals) == 30):
        return reviews
    from app.tasks import _apply_visual_review

    selected = deepcopy(scene_visuals)
    for index, review in reviews.items():
        _apply_visual_review(selected, index, review, default_fraction=default_fraction)
    # A positively reviewed scene has exactly one chosen shot. Failed scenes
    # will be replaced and reviewed before assembly, with the same timing.
    timeline = [(pool[0] if pool else {}, duration, scene.get('transition') or 'cut', index)
                for index, (scene, duration, pool) in enumerate(zip(scenes, scene_durations, selected))]
    frame_counts = render._timeline_frame_counts(timeline, voice_duration)
    output = dict(reviews)
    directory = Path(work) / 'motion_review'
    directory.mkdir(parents=True, exist_ok=True)
    for index, review in reviews.items():
        if (type(index) is not int or not 0 <= index < len(timeline)
                or int(review.get('score', 0)) < options.get('quality_threshold', 86)
                or frame_counts[index] <= 6 * render.FPS):
            continue
        spec, _, transition, _ = timeline[index]
        source = Path(render._spec_path(spec))
        with source.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        contract = {'source_sha256': digest, 'spec': spec, 'frames': frame_counts[index],
                    'index': index, 'transition': transition, 'resolution': '1920x1080'}
        identity = hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()
        target = directory / (identity + '.mp4')
        try:
            render.normalize_clip(spec, target, frame_counts[index] / render.FPS, index,
                                  transition, '1920x1080')
            frozen = render.max_freeze_duration(target)
        finally:
            target.unlink(missing_ok=True)
        measurement = {'method': 'rendered_cut_freezedetect', 'source_sha256': digest,
                       'frames': frame_counts[index], 'max_freeze_seconds': frozen,
                       'limit_seconds': 6., 'pass': frozen <= 6.}
        updated = {**review, 'motion_review': measurement}
        if frozen > 6.:
            updated.update(score=min(int(review['score']), 40), motion_gate_passed=False,
                           reason=f'Selected cut contains {frozen:.3f}s without motion; replace this footage. '
                                  + str(review.get('reason') or ''))
        output[index] = updated
    return output
