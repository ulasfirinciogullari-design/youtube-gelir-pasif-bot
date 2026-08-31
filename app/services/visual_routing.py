from __future__ import annotations


SHORT_PREVIEW_RUNWAY_CAP = 3
SHORT_PREVIEW_RUNWAY_REPAIR_CAP = 2


def preview_authored_ai_limit(
    options: dict,
    scene_count: int,
    duration_minutes: float,
) -> int | None:
    """Return the authored-AI limit for a short preview, if applicable."""
    if options.get('mode') != 'preview' or duration_minutes > 0.6:
        return None
    if (options.get('visual_mix') or 'balanced') == 'ai_first':
        return min(SHORT_PREVIEW_RUNWAY_CAP, scene_count)
    return scene_count


def should_rank_runway_candidate(
    options: dict,
    duration_minutes: float,
    *,
    authored_ai_prompt: bool,
    has_visual: bool,
    stock_score: int,
    quality_threshold: int,
) -> bool:
    """Keep quality fallback behavior, while honoring AI-first authorship."""
    if not has_visual or stock_score < quality_threshold:
        return True
    return (
        options.get('mode') == 'preview'
        and duration_minutes <= 0.6
        and (options.get('visual_mix') or 'balanced') == 'ai_first'
        and authored_ai_prompt
    )


def preview_runway_repair_indices(
    options: dict,
    duration_minutes: float,
    rejected_scene_indices: list[int],
    scenes: list[dict],
    generated_scene_indices: set[int] | list[int],
    final_reviews: dict[int, dict],
) -> list[int]:
    """Select a bounded set of authored AI shots for evidence-led repair.

    A repair is allowed only after the exact generated clip has failed the
    final visual gate. Keeping the selector pure makes the paid limit easy to
    verify independently from the worker orchestration.
    """
    if options.get('mode') != 'preview' or duration_minutes > 0.6:
        return []

    generated = {int(index) for index in generated_scene_indices}
    eligible: list[int] = []
    for raw_index in rejected_scene_indices:
        index = int(raw_index)
        if (
            index not in generated
            or index < 0
            or index >= len(scenes)
            or not str((scenes[index] or {}).get('ai_prompt') or '').strip()
            or index not in final_reviews
        ):
            continue
        eligible.append(index)
    eligible.sort(key=lambda index: (
        int((final_reviews.get(index) or {}).get('score', 0)),
        index,
    ))
    return eligible[:SHORT_PREVIEW_RUNWAY_REPAIR_CAP]

