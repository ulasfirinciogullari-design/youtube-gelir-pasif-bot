from __future__ import annotations


SHORT_PREVIEW_RUNWAY_CAP = 3


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
