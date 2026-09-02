from __future__ import annotations


SHORT_PREVIEW_RUNWAY_CAP = 4
SHORT_PREVIEW_RUNWAY_REPAIR_CAP = 2

# The director strips model-authored scene metadata before applying these
# private routing markers. Downstream visual QC may therefore use the exact
# value as a server-authored contract instead of reclassifying narration or
# trusting a generation prompt.
SERVER_SHORT_PROXY_KIND_FIELD = '_server_short_proxy_kind'
OPEN_AIR_COOLING_PROXY_KIND = 'open_air_cooling'


def routed_open_air_cooling_temporal_required(scene: dict) -> bool:
    """Return whether the server routed this scene as a cooling proof shot."""
    return bool(
        isinstance(scene, dict)
        and scene.get(SERVER_SHORT_PROXY_KIND_FIELD)
        == OPEN_AIR_COOLING_PROXY_KIND
    )


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


def preview_paid_ai_limit(
    options: dict,
    scene_count: int,
    duration_minutes: float,
) -> int | None:
    """Return the worker's paid primary-generation cap for a short preview."""
    if options.get('mode') != 'preview' or duration_minutes > 0.6:
        return None
    mix = options.get('visual_mix') or 'balanced'
    cap = 1 if mix == 'real_first' else SHORT_PREVIEW_RUNWAY_CAP
    return min(cap, scene_count)


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
    *,
    exact_revalidation_scene_indices: set[int] | list[int] | None = None,
) -> list[int]:
    """Select a bounded set of evidence-led final repairs.

    Ordinarily, a repair is allowed only after the exact generated clip has
    failed the final visual gate. A stock clip that passed the manual prepass
    may also enter the same bounded budget when exact revalidation later finds
    a hard veto such as prominent text or a missing subject/action. Keeping the
    selector pure makes the paid limit easy to verify independently from the
    worker orchestration.
    """
    if options.get('mode') != 'preview' or duration_minutes > 0.6:
        return []

    generated = {int(index) for index in generated_scene_indices}
    exact_revalidation = {
        int(index) for index in (exact_revalidation_scene_indices or [])
    }
    eligible: list[int] = []
    for raw_index in rejected_scene_indices:
        index = int(raw_index)
        if (
            index < 0
            or index >= len(scenes)
            or index not in final_reviews
            or (
                index not in exact_revalidation
                and (
                    index not in generated
                    or not str(
                        (scenes[index] or {}).get('ai_prompt') or ''
                    ).strip()
                )
            )
        ):
            continue
        eligible.append(index)
    eligible.sort(key=lambda index: (
        0 if index in exact_revalidation else 1,
        int((final_reviews.get(index) or {}).get('score', 0)),
        index,
    ))
    return eligible[:SHORT_PREVIEW_RUNWAY_REPAIR_CAP]

