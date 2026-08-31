from __future__ import annotations

from pathlib import Path
import json
import math
import re
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.celery_app import celery
from app.services.audio_design import generate_music_bed, mix_voice_and_music
from app.services.director import direct_and_qc, short_story_package_is_approved
from app.services.pexels import find_broll, download_broll
from app.services.render import render_video
from app.services.research import research_and_script
from app.services.runway import generate_scene, download_generated_scene
from app.services.storage import upload_file, presigned_download_url
from app.services.studio_state import mark_failure, mark_success, set_stage, update_job
from app.services.visual_qc import review_scene_visuals
from app.services.voice import synthesize_scene_sequence
from app.services.visual_routing import (
    SHORT_PREVIEW_RUNWAY_CAP,
    preview_runway_repair_indices,
    should_rank_runway_candidate,
)


class FinalVisualQualityError(RuntimeError):
    """A bounded semantic-quality rejection that should not rerun the whole pipeline."""


class PreRunwayRetryableError(RuntimeError):
    """A pre-paid preflight rejection that may safely regenerate the automatic plan."""


def _normalized_options(options: dict | None, duration_minutes: float) -> dict:
    value = dict(options or {})
    value.setdefault('mode', 'preview' if duration_minutes <= 1 else 'production')
    value.setdefault('workflow', 'auto')
    value.setdefault('content_style', 'documentary')
    value.setdefault('pace', 'balanced')
    value.setdefault('visual_mix', 'balanced')
    value.setdefault('music', 'off' if value['mode'] == 'preview' else 'auto')
    value.setdefault('subtitles', 'sidecar')
    value.setdefault('reference_url', None)
    quality_floor = 84 if value['mode'] == 'production' else 86
    try:
        requested_quality = int(value.get('quality_threshold', quality_floor))
    except Exception:
        requested_quality = quality_floor
    value['quality_threshold'] = max(quality_floor, requested_quality)
    if value['mode'] == 'preview':
        value['music'] = 'off'
    return value


def _task_spec(topic: str, duration_minutes: float, language: str, channel_id: str | None, options: dict) -> dict:
    return {
        'topic': topic,
        'duration_minutes': duration_minutes,
        'language': language,
        'channel_id': channel_id,
        **options,
    }


def _select_ranked_broll_candidates(
    query_results: list[tuple[str, list[dict]]],
    seen_ids: set,
    limit: int,
    minimum_duration: float = 5.0,
    allow_seen_fallback: bool = True,
    allow_short_fallback: bool = True,
) -> list[tuple[str, dict]]:
    """Select a stable relevance-first, query-diverse Pexels candidate pool."""
    limit = max(1, int(limit))
    selected: list[tuple[str, dict]] = []
    selected_ids: set = set()

    def candidate_key(item: dict):
        return item.get('pexels_id') or item.get('download_url')

    def usable(item: dict, require_unseen: bool, require_duration: bool) -> bool:
        key = candidate_key(item)
        if not key or key in selected_ids:
            return False
        if require_unseen and key in seen_ids:
            return False
        width = int(item.get('width') or 0)
        height = int(item.get('height') or 0)
        if width and height and width < height:
            return False
        try:
            duration = float(item.get('duration') or 0)
        except Exception:
            duration = 0
        if require_duration and duration < minimum_duration:
            return False
        return bool(item.get('download_url'))

    max_rank = max(
        (len(candidates) for _query, candidates in query_results),
        default=0,
    )
    # Prefer globally new, >=5-second clips. Duration may relax when needed;
    # global uniqueness relaxes only for callers that explicitly permit it.
    selection_phases = [(True, True)]
    if allow_short_fallback:
        selection_phases.append((True, False))
    if allow_seen_fallback:
        selection_phases.append((False, True))
        if allow_short_fallback:
            selection_phases.append((False, False))
    for require_unseen, require_duration in selection_phases:
        for rank in range(max_rank):
            for query, candidates in query_results:
                if rank >= len(candidates):
                    continue
                item = candidates[rank]
                if not usable(item, require_unseen, require_duration):
                    continue
                selected.append((query, item))
                selected_ids.add(candidate_key(item))
                if len(selected) >= limit:
                    return selected
    return selected


def _collect_broll(
    scenes: list[dict],
    work: Path,
    strict_duration: bool = False,
) -> dict:
    scene_visuals: list[list[dict]] = [[] for _ in scenes]
    credits: list[dict] = []
    seen_ids: set[int | str] = set()
    requests: list[tuple[int, int, str]] = []
    for scene_idx, scene in enumerate(scenes):
        queries = [q for q in scene.get('visual_queries', []) if isinstance(q, str) and q.strip()][:3]
        for query_idx, query in enumerate(queries):
            requests.append((scene_idx, query_idx, query))

    search_results: dict[tuple[int, int, str], list[dict]] = {}
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(requests)))) as executor:
        future_map = {
            executor.submit(find_broll, query, 16): (scene_idx, query_idx, query)
            for scene_idx, query_idx, query in requests
        }
        for future in as_completed(future_map):
            key = future_map[future]
            try:
                search_results[key] = future.result() or []
            except Exception:
                search_results[key] = []

    download_specs: list[tuple[int, int, str, dict, Path]] = []
    selection_seen_ids = set(seen_ids)
    for scene_idx in range(len(scenes)):
        scene_query_results = [
            (
                query,
                search_results.get((request_scene_idx, query_idx, query)) or [],
            )
            for request_scene_idx, query_idx, query in requests
            if request_scene_idx == scene_idx
        ]
        selected = _select_ranked_broll_candidates(
            scene_query_results,
            selection_seen_ids,
            3,
            allow_seen_fallback=False,
            allow_short_fallback=not strict_duration,
        )
        for candidate_idx, (query, item) in enumerate(selected):
            candidate_id = item.get('pexels_id') or item.get('download_url')
            if candidate_id:
                selection_seen_ids.add(candidate_id)
            path = work / f'pexels_s{scene_idx:02d}_{candidate_idx:02d}.mp4'
            download_specs.append((
                scene_idx,
                candidate_idx,
                query,
                item,
                path,
            ))

    def download_one(spec):
        scene_idx, query_idx, query, item, path = spec
        download_broll(item, path)
        return scene_idx, query_idx, query, item, path

    downloaded: list[tuple[int, int, str, dict, Path]] = []
    with ThreadPoolExecutor(max_workers=min(6, max(1, len(download_specs)))) as executor:
        futures = [executor.submit(download_one, spec) for spec in download_specs]
        for future in as_completed(futures):
            try:
                downloaded.append(future.result())
            except Exception:
                continue

    # Network completion order must never change the candidate indices later
    # consumed by visual QC.
    for scene_idx, candidate_idx, query, item, path in sorted(
        downloaded,
        key=lambda result: (result[0], result[1]),
    ):
        candidate_id = item.get('pexels_id') or item.get('download_url')
        if candidate_id:
            seen_ids.add(candidate_id)
        try:
            source_duration = float(item.get('duration') or 0)
        except Exception:
            source_duration = 0.0
        scene_visuals[scene_idx].append({
            'path': str(path),
            'start_fraction': 0.25,
            'source_duration': source_duration,
        })
        credits.append({
            'source': 'Pexels',
            'scene_index': scene_idx,
            'query': query,
            'creator_name': item.get('creator_name'),
            'creator_url': item.get('creator_url'),
            'page_url': item.get('page_url'),
            'pexels_id': item.get('pexels_id'),
            'selected_by': 'candidate_pool',
        })
    return {'scene_visuals': scene_visuals, 'credits': credits, 'seen_ids': seen_ids}


def _download_ranked_broll_candidates(
    scene_idx: int,
    queries: list[str],
    seen_ids: set,
    work: Path,
    credits: list[dict],
    *,
    file_prefix: str,
    selected_by: str,
    max_candidates: int,
    search_limit: int,
    minimum_duration: float = 5.0,
    allow_short_fallback: bool = False,
) -> list[dict]:
    normalized_queries: list[str] = []
    query_keys: set[str] = set()
    for raw_query in queries:
        query = str(raw_query or '').strip()
        key = query.casefold()
        if not query or key in query_keys:
            continue
        query_keys.add(key)
        normalized_queries.append(query)
        if len(normalized_queries) >= 3:
            break
    if not normalized_queries or max_candidates <= 0:
        return []

    search_results: dict[int, list[dict]] = {}
    search_errors: list[Exception] = []
    with ThreadPoolExecutor(max_workers=min(3, len(normalized_queries))) as retry_pool:
        future_map = {
            retry_pool.submit(find_broll, query, search_limit): query_idx
            for query_idx, query in enumerate(normalized_queries)
        }
        for future in as_completed(future_map):
            query_idx = future_map[future]
            try:
                search_results[query_idx] = future.result() or []
            except Exception as exc:
                search_errors.append(exc)

    if not search_results and search_errors:
        raise RuntimeError(f'Pexels retry search failed for scene {scene_idx}') from search_errors[0]

    success_target = min(5, max(1, int(max_candidates)))
    attempt_limit = min(7, success_target + 2)
    ranked = _select_ranked_broll_candidates(
        [
            (query, search_results.get(query_idx, []))
            for query_idx, query in enumerate(normalized_queries)
        ],
        seen_ids,
        attempt_limit,
        minimum_duration=max(0.1, float(minimum_duration)),
        allow_seen_fallback=False,
        allow_short_fallback=allow_short_fallback,
    )
    if not ranked:
        return []

    safe_prefix = re.sub(r'[^a-zA-Z0-9_-]+', '_', file_prefix)[:32] or 'qc'
    download_errors: list[Exception] = []

    def download_one(candidate_idx: int, query: str, item: dict):
        path = work / f'{safe_prefix}_s{scene_idx:02d}_{candidate_idx:02d}.mp4'
        download_broll(item, path)
        return candidate_idx, query, item, path

    downloaded: dict[int, tuple[str, dict, Path]] = {}
    initial_batch = list(enumerate(ranked[:success_target]))
    with ThreadPoolExecutor(max_workers=min(5, len(initial_batch))) as download_pool:
        future_map = {
            download_pool.submit(download_one, candidate_idx, query, item): candidate_idx
            for candidate_idx, (query, item) in initial_batch
        }
        for future in as_completed(future_map):
            try:
                candidate_idx, query, item, path = future.result()
                downloaded[candidate_idx] = (query, item, path)
            except Exception as exc:
                download_errors.append(exc)

    # A transient CDN failure must not silently shrink the QC pool. Backfill
    # from at most two further ranked candidates, stopping at the success cap.
    for candidate_idx, (query, item) in enumerate(ranked[success_target:], start=success_target):
        if len(downloaded) >= success_target:
            break
        try:
            _candidate_idx, _query, _item, path = download_one(candidate_idx, query, item)
            downloaded[candidate_idx] = (_query, _item, path)
        except Exception as exc:
            download_errors.append(exc)

    replacements: list[dict] = []
    for candidate_idx in sorted(downloaded):
        query, item, path = downloaded[candidate_idx]
        candidate_id = item.get('pexels_id') or item.get('download_url')
        if candidate_id:
            seen_ids.add(candidate_id)
        try:
            source_duration = float(item.get('duration') or 0)
        except Exception:
            source_duration = 0.0
        replacements.append({
            'path': str(path),
            'start_fraction': 0.35,
            'source_duration': source_duration,
        })
        credits.append({
            'source': 'Pexels',
            'scene_index': scene_idx,
            'query': query,
            'creator_name': item.get('creator_name'),
            'creator_url': item.get('creator_url'),
            'page_url': item.get('page_url'),
            'pexels_id': item.get('pexels_id'),
            'selected_by': selected_by,
        })

    if ranked and not replacements and download_errors:
        raise RuntimeError(f'Pexels retry download failed for scene {scene_idx}') from download_errors[0]
    return replacements


def _retry_bad_scene(
    scene_idx: int,
    retry_queries: list[str],
    seen_ids: set,
    work: Path,
    credits: list[dict],
    file_prefix: str = 'qc',
    max_replacements: int = 1,
    minimum_duration: float = 5.0,
    allow_short_fallback: bool = True,
) -> list[dict]:
    safe_prefix = re.sub(r'[^a-zA-Z0-9_-]+', '_', file_prefix)[:32] or 'qc'
    selected_by = (
        'final_visual_qc_rescue' if safe_prefix == 'final_qc_rescue'
        else 'pre_runway_budget_rescue' if safe_prefix == 'pre_runway_budget_rescue'
        else 'pre_runway_stock_contract' if safe_prefix == 'pre_runway_stock_contract'
        else 'pre_runway_duration_refill' if safe_prefix == 'duration_refill'
        else 'visual_qc_retry'
    )
    return _download_ranked_broll_candidates(
        scene_idx,
        retry_queries[:2],
        seen_ids,
        work,
        credits,
        file_prefix=safe_prefix,
        selected_by=selected_by,
        max_candidates=max_replacements,
        search_limit=18,
        minimum_duration=minimum_duration,
        allow_short_fallback=allow_short_fallback,
    )

def _visual_path(spec: str | dict) -> str:
    if isinstance(spec, dict):
        return str(spec.get('path') or '')
    return str(spec)


def _truncate_utf16(text: str, limit: int = 1000) -> str:
    result: list[str] = []
    units = 0
    for char in str(text or ''):
        char_units = 2 if ord(char) > 0xFFFF else 1
        if units + char_units > limit:
            break
        result.append(char)
        units += char_units
    return ''.join(result).strip()


def _runway_prompt_for_scene(scene: dict, review: dict | None) -> str:
    original = str(scene.get('ai_prompt') or '').strip()
    review = review or {}
    retry_queries = review.get('retry_queries') or []
    if isinstance(retry_queries, str):
        retry_queries = [retry_queries]
    hints = [str(q).strip() for q in retry_queries if str(q).strip()][:2]
    if not hints:
        visual_queries = scene.get('visual_queries') or []
        if isinstance(visual_queries, str):
            visual_queries = [visual_queries]
        hints = [str(q).strip() for q in visual_queries if str(q).strip()][:2]
    if not original and not hints:
        return ''

    narration = _truncate_utf16(str(scene.get('narration') or '').strip(), 180)
    visible_action = _truncate_utf16('; '.join(hint[:100] for hint in hints), 150)
    primary_event = _truncate_utf16(
        ' / '.join(
            value for value in (visible_action, narration) if value
        ),
        280,
    )
    combined = f'{narration} {original} {visible_action}'.lower()
    mechanism_guardrails: list[str] = []
    oled_claim = bool(re.search(
        r'\b(?:oled|true[ -]black)\b|gerçek siyah|emissive\s+(?:pixel|display|screen)',
        combined,
    ))
    if oled_claim:
        mechanism_guardrails.append(
            'OLED proof: macro real subpixels; shaped-black emitters off while '
            'adjacent RGB stays lit; never whole-display fade, tap or noise.'
        )
        power_claim = bool(re.search(
            r'\b(?:power\s+(?:use|usage|draw|consumption)|energy\s+(?:use|usage|consumption)|'
            r'watt(?:age)?|uses?\s+less\s+(?:power|energy)|lower\s+power)\b|'
            r'(?:güç|enerji).{0,24}tüket|daha\s+az\s+(?:güç|enerji)|'
            r'(?:güç|enerji)\s+kullanım',
            combined,
        ))
        if power_claim:
            mechanism_guardrails.append(
                'Power proof: a real physical meter visibly falls in the same shot.'
            )

    opening = (
        'One continuous photorealistic 16:9 documentary shot for the full '
        'requested duration. '
    )
    temporal_clause = (
        f'PRIMARY EVENT: {_truncate_utf16(primary_event, 140)}. '
        'Show a clear START state, then the named PHYSICAL ACTION or CAUSE, '
        'then hold the visibly CHANGED RESULT in the same take. A static '
        'final-only shot fails. '
    ) if primary_event else ''
    raw_guardrail_clause = (
        ' '.join(mechanism_guardrails) if mechanism_guardrails else ''
    )
    closing = (
        'Keep subject, identity, background and exposure continuous; no cuts, '
        'text, logos, charts, glitch, watermark or metaphor.'
    )
    original_label = 'Core shot direction: '
    fixed_units = len(
        (opening + temporal_clause + original_label + closing).encode(
            'utf-16-le'
        )
    ) // 2
    shared_budget = max(
        0,
        1000 - fixed_units - 3,
    )
    original_units = len(original.encode('utf-16-le')) // 2
    minimum_original = min(original_units, 360)
    guardrail_units = len(raw_guardrail_clause.encode('utf-16-le')) // 2
    guardrail_budget = min(
        guardrail_units,
        220,
        max(0, shared_budget - minimum_original),
    )
    original_budget = max(0, shared_budget - guardrail_budget)
    original_value = (
        _truncate_utf16(original, original_budget)
        if original and original_budget else ''
    )
    original_clause = f'{original_label}{original_value}. ' if original_value else ''
    guardrail_value = _truncate_utf16(
        raw_guardrail_clause,
        guardrail_budget,
    )
    guardrail_clause = f'{guardrail_value} ' if guardrail_value else ''
    return _truncate_utf16(
        opening
        + temporal_clause
        + original_clause
        + guardrail_clause
        + closing
    )


def _apply_visual_review(
    scene_visuals: list[list[str | dict]],
    scene_idx: int,
    review: dict,
    default_fraction: float = 0.25,
) -> None:
    if scene_idx < 0 or scene_idx >= len(scene_visuals):
        return
    specs = [spec for spec in scene_visuals[scene_idx] if _visual_path(spec)]
    if not specs:
        return
    try:
        best_idx = int(review.get('best_candidate_index', 0))
    except Exception:
        best_idx = 0
    best_idx = min(max(best_idx, 0), len(specs) - 1)
    chosen = dict(specs[best_idx]) if isinstance(specs[best_idx], dict) else {'path': _visual_path(specs[best_idx])}
    if chosen.get('preserve_start_fraction'):
        try:
            locked_fraction = float(chosen.get('start_fraction', 0.0))
        except Exception:
            locked_fraction = 0.0
        chosen['start_fraction'] = max(0.0, min(locked_fraction, 0.95))
        scene_visuals[scene_idx] = [chosen]
        return
    try:
        fraction = float(review.get('best_start_fraction', chosen.get('start_fraction', default_fraction)))
    except Exception:
        fraction = default_fraction
    chosen['start_fraction'] = max(0.0, min(fraction, 0.95))
    scene_visuals[scene_idx] = [chosen]


def _generated_visual_spec(
    path: str | Path,
) -> dict:
    return {
        'path': str(path),
        'start_fraction': 0.0,
        'preserve_start_fraction': True,
        'forbid_loop': True,
    }


def _render_target_duration(options: dict, requested_seconds: float) -> float | None:
    return (
        float(requested_seconds)
        if options.get('mode') == 'preview'
        else None
    )


def _preview_duration_within_gate(
    actual_seconds: float,
    requested_seconds: float,
    voice_seconds: float,
) -> bool:
    try:
        actual = float(actual_seconds)
        requested = float(requested_seconds)
        voice = float(voice_seconds)
    except Exception:
        return False
    if actual <= 0 or requested <= 0 or voice <= 0:
        return False

    # MP3/AAC encoder padding and mux timebases can shift a short master by
    # several hundred milliseconds. Cap target drift at one second, but allow
    # at most 250 ms against the fitted voice so a final word cannot be hidden
    # by the broader target tolerance.
    target_tolerance = min(1.0, max(0.75, requested * 0.02))
    target_ok = abs(actual - requested) <= target_tolerance
    voice_complete = actual + 0.25 >= voice
    return target_ok and voice_complete


def _runway_generation_seconds(scene_duration: float) -> int:
    """Buy enough source footage for one pass after the renderer's speed-up."""
    try:
        required = max(0.0, float(scene_duration)) * 1.02 + 0.20
    except Exception:
        required = 5.0
    return max(5, min(10, int(math.ceil(required))))


def _runway_single_pass_supported(scene_duration: float) -> bool:
    try:
        required = max(0.0, float(scene_duration)) * 1.02 + 0.20
    except Exception:
        return False
    return required <= 10.0


def _validate_runway_single_pass_candidates(
    scene_indices: list[int],
    scene_durations: list[float],
) -> None:
    unsupported = [
        int(scene_idx)
        for scene_idx in scene_indices
        if (
            scene_idx < 0
            or scene_idx >= len(scene_durations)
            or not _runway_single_pass_supported(
                scene_durations[scene_idx]
            )
        )
    ]
    if unsupported:
        raise FinalVisualQualityError(
            'AI scenes exceed Runway single-pass duration; split these '
            'storyboard scenes before paid generation: '
            + ','.join(str(index) for index in unsupported)
        )


def _preflight_runway_candidates_before_paid(
    scene_indices: list[int],
    scene_durations: list[float],
    approved_package: dict | None,
) -> None:
    """Fail before spend, while allowing automatic plans to be regenerated."""
    try:
        _validate_runway_single_pass_candidates(
            scene_indices,
            scene_durations,
        )
    except FinalVisualQualityError as exc:
        if approved_package is None:
            raise PreRunwayRetryableError(str(exc)) from exc
        raise


def _max_runway_scenes(options: dict, scene_count: int, duration_minutes: float) -> int:
    if options.get('mode') == 'preview':
        return (
            min(SHORT_PREVIEW_RUNWAY_CAP, scene_count)
            if duration_minutes <= 0.6 else 0
        )
    mix = options.get('visual_mix') or 'balanced'
    if mix == 'real_first':
        return min(2, max(1, math.ceil(scene_count * 0.10)))
    if mix == 'ai_first':
        return min(6, max(2, math.ceil(scene_count * 0.40)))
    return min(4, max(1, math.ceil(scene_count * 0.24)))


def _prepare_package(
    celery_task,
    task_id: str,
    topic: str,
    duration_minutes: float,
    language: str,
    options: dict,
    approved_package: dict | None,
) -> dict:
    if approved_package:
        if (
            options.get('mode') == 'preview'
            and duration_minutes <= 0.6
            and not short_story_package_is_approved(
                approved_package,
                topic,
            )
        ):
            raise FinalVisualQualityError(
                'Onaylı kısa storyboard güncel hikâye ve telaffuz '
                'denetiminden geçmiyor; ücretli medya başlatılmadı.'
            )
        set_stage(celery_task, task_id, 'approved_plan', 12, 'Onaylı storyboard kilitlendi.')
        package = dict(approved_package)
        package['studio_options'] = options
        return package

    set_stage(celery_task, task_id, 'research', 7, 'Güncel araştırma ve ilk storyboard hazırlanıyor.')
    draft = research_and_script(topic, duration_minutes, language, options)
    set_stage(celery_task, task_id, 'director_qc', 14, 'Senaryo yönetmeni akışı, ritmi ve görsel dili düzeltiyor.')
    return direct_and_qc(draft, topic, duration_minutes, language, options)


@celery.task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=1)
def plan_video_pipeline(
    self,
    topic: str,
    duration_minutes: float = 5,
    language: str = 'tr',
    channel_id: str | None = None,
    options: dict | None = None,
):
    task_id = self.request.id
    options = _normalized_options(options, duration_minutes)
    update_job(task_id, kind='plan', spec=_task_spec(topic, duration_minutes, language, channel_id, options))
    try:
        set_stage(self, task_id, 'research', 20, 'Araştırma ve ilk storyboard hazırlanıyor.')
        draft = research_and_script(topic, duration_minutes, language, options)
        set_stage(self, task_id, 'director_qc', 62, 'Senaryo yönetmeni bütünlüğü ve görsel planı denetliyor.')
        package = direct_and_qc(draft, topic, duration_minutes, language, options)
        result = {
            'status': 'plan_ready',
            'task_id': task_id,
            'title': package.get('title'),
            'scene_count': len(package.get('scenes') or []),
            'package': package,
        }
        mark_success(task_id, result, state='AWAITING_APPROVAL')
        return result
    except Exception as exc:
        if int(getattr(self.request, 'retries', 0) or 0) < int(self.max_retries or 0):
            set_stage(
                self,
                task_id,
                'plan_retry',
                62,
                'Storyboard denetimi yenileniyor; ücretli medya üretimi başlamadı.',
            )
            raise
        mark_failure(task_id, exc)
        raise


@celery.task(
    bind=True,
    autoretry_for=(Exception,),
    dont_autoretry_for=(FinalVisualQualityError,),
    retry_backoff=True,
    max_retries=2,
)
def run_video_pipeline(
    self,
    topic: str,
    duration_minutes: float = 5,
    language: str = 'tr',
    channel_id: str | None = None,
    options: dict | None = None,
    approved_package: dict | None = None,
):
    task_id = self.request.id
    options = _normalized_options(options, duration_minutes)
    retry_number = int(getattr(self.request, 'retries', 0) or 0)
    work = Path('/tmp/youtube_factory') / f'{task_id}_attempt_{retry_number}'
    work.mkdir(parents=True, exist_ok=True)
    update_job(task_id, kind='render', spec=_task_spec(topic, duration_minutes, language, channel_id, options))
    runway_attempts = 0
    final_runway_repair_attempts = 0
    final_runway_repair_scenes: list[int] = []
    final_runway_repair_failures: list[int] = []

    try:
        package = _prepare_package(self, task_id, topic, duration_minutes, language, options, approved_package)
        scenes = package['scenes']
        (work / 'package.json').write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding='utf-8')

        strict_short_preview_duration = (
            options.get('mode') == 'preview'
            and duration_minutes <= 0.6
        )
        set_stage(self, task_id, 'voice_and_visuals', 24, 'Anlatıcı ve görsel adaylar paralel hazırlanıyor.')
        with ThreadPoolExecutor(max_workers=2) as stage_pool:
            voice_future = stage_pool.submit(
                synthesize_scene_sequence,
                scenes,
                task_id,
                duration_minutes * 60,
            )
            broll_future = stage_pool.submit(
                _collect_broll,
                scenes,
                work,
                strict_short_preview_duration,
            )
            voice_result = voice_future.result()
            broll_result = broll_future.result()

        voice_path = voice_result['path']
        scene_durations = voice_result['scene_durations']
        scene_visuals: list[list[str | dict]] = broll_result['scene_visuals']
        if strict_short_preview_duration:
            for scene_idx, specs in enumerate(scene_visuals):
                try:
                    required_source_duration = max(
                        5.0,
                        float(scene_durations[scene_idx]) + 0.35,
                    )
                except Exception:
                    required_source_duration = 5.0
                duration_safe_specs: list[str | dict] = []
                for spec in specs:
                    try:
                        source_duration = float(
                            spec.get('source_duration') or 0
                            if isinstance(spec, dict)
                            else 0
                        )
                    except Exception:
                        source_duration = 0.0
                    if source_duration >= required_source_duration:
                        duration_safe_specs.append(spec)
                scene_visuals[scene_idx] = duration_safe_specs
        credits = broll_result['credits']
        seen_ids = broll_result['seen_ids']

        set_stage(self, task_id, 'visual_qc', 45, 'Her sahnenin aday görüntüleri gerçek kareler üzerinden karşılaştırılıyor.')
        visual_qc: dict = {'reviews': []}
        music_result: dict | None = None
        music_error: str | None = None
        should_generate_music = options.get('mode') == 'production' and options.get('music') == 'auto'

        with ThreadPoolExecutor(max_workers=2 if should_generate_music else 1) as quality_pool:
            qc_future = quality_pool.submit(
                review_scene_visuals,
                scenes,
                scene_visuals,
                work,
                len(scenes) if duration_minutes <= 1 else min(14, len(scenes)),
                topic=topic,
                story_scenes=scenes,
            )
            music_future = None
            if should_generate_music:
                music_future = quality_pool.submit(
                    generate_music_bed,
                    topic,
                    str(options.get('content_style') or 'documentary'),
                    work / 'music_bed.mp3',
                    loop_seconds=36.0,
                )
            visual_qc = qc_future.result()
            if music_future:
                try:
                    music_result = music_future.result()
                except Exception as exc:
                    music_error = str(exc)[:600]

        visual_replacements: list[dict] = []
        reviews_by_scene = {
            int(r.get('scene_index')): r
            for r in (visual_qc.get('reviews') or [])
            if isinstance(r, dict) and str(r.get('scene_index', '')).lstrip('-').isdigit()
        }
        quality_threshold = int(options.get('quality_threshold') or 80)

        for scene_idx, _scene in enumerate(scenes):
            review = reviews_by_scene.get(scene_idx)
            paths = [p for p in scene_visuals[scene_idx] if _visual_path(p)]
            if not paths:
                scene_visuals[scene_idx] = []
                is_short_preview_authored_ai = (
                    options.get('mode') == 'preview'
                    and duration_minutes <= 0.6
                    and bool(str(_scene.get('ai_prompt') or '').strip())
                )
                if is_short_preview_authored_ai:
                    raw_refill_queries = _scene.get('visual_queries') or []
                    if isinstance(raw_refill_queries, str):
                        raw_refill_queries = [raw_refill_queries]
                    refill_queries = [
                        str(query).strip()
                        for query in raw_refill_queries[:2]
                        if str(query).strip()
                    ]
                    duration_refill = _retry_bad_scene(
                        scene_idx,
                        refill_queries,
                        seen_ids,
                        work,
                        credits,
                        file_prefix='duration_refill',
                        max_replacements=3,
                        minimum_duration=max(
                            5.0,
                            float(scene_durations[scene_idx]) + 0.35,
                        ),
                        allow_short_fallback=False,
                    )
                    scene_visuals[scene_idx] = duration_refill
                    if duration_refill:
                        visual_replacements.append({
                            'scene_index': scene_idx,
                            'score': -1,
                            'reason': 'Initial candidates were shorter than the narration.',
                            'old_best': '',
                            'replacement_count': len(duration_refill),
                            'stage': 'pre_runway_duration_refill',
                        })
                continue

            if not review:
                first_spec = dict(paths[0]) if isinstance(paths[0], dict) else {'path': _visual_path(paths[0])}
                first_spec['start_fraction'] = 0.25
                scene_visuals[scene_idx] = [first_spec]
                continue

            best_idx = int(review.get('best_candidate_index', 0))
            score = int(review.get('score', 0))
            best_idx = min(max(best_idx, 0), len(paths) - 1)
            best_path = _visual_path(paths[best_idx])
            try:
                best_fraction = float(review.get('best_start_fraction', 0.25))
            except Exception:
                best_fraction = 0.25
            best_fraction = max(0.0, min(best_fraction, 0.95))
            best_spec = (
                dict(paths[best_idx])
                if isinstance(paths[best_idx], dict)
                else {'path': best_path}
            )
            best_spec['start_fraction'] = best_fraction

            if score >= quality_threshold:
                scene_visuals[scene_idx] = [best_spec]
                continue
            is_short_preview_stock = (
                options.get('mode') == 'preview'
                and duration_minutes <= 0.6
                and not str(_scene.get('ai_prompt') or '').strip()
            )
            if is_short_preview_stock:
                # Preserve the evidence-backed incumbent. The bounded stock
                # tournament below compares it with a wider deterministic pool.
                scene_visuals[scene_idx] = [best_spec]
                continue

            retry_queries = [str(q).strip() for q in (review.get('retry_queries') or [])[:2] if str(q).strip()]
            replacements = _retry_bad_scene(
                scene_idx,
                retry_queries,
                seen_ids,
                work,
                credits,
                minimum_duration=max(5.0, float(scene_durations[scene_idx]) + 0.35),
                allow_short_fallback=not strict_short_preview_duration,
            )
            scene_visuals[scene_idx] = [*replacements, best_spec][:3]
            if replacements:
                visual_replacements.append({
                    'scene_index': scene_idx,
                    'score': score,
                    'reason': review.get('reason'),
                    'old_best': best_path,
                    'replacement_count': len(replacements),
                })

        set_stage(self, task_id, 'ai_scene', 61, 'Güncel stok kalitesi ölçülüyor; en zor sahneler özgün görüntüye ayrılıyor.')
        runway_errors: list[str] = []
        runway_failed_scenes: list[int] = []
        runway_generated_scenes: list[int] = []
        runway_scenes_used = 0
        runway_submission_cap = _max_runway_scenes(options, len(scenes), duration_minutes)

        # Score the exact clips selected after stock retries. Paid Runway slots
        # are ranked by current evidence, never scene order or stale scores.
        pre_runway_qc = review_scene_visuals(
            scenes,
            scene_visuals,
            work / 'pre_runway_visual_qc',
            len(scenes),
            topic=topic,
            story_scenes=scenes,
        )
        current_reviews = {
            int(review.get('scene_index')): review
            for review in (pre_runway_qc.get('reviews') or [])
            if isinstance(review, dict)
            and str(review.get('scene_index', '')).lstrip('-').isdigit()
        }
        missing_pre_runway_reviews = [
            int(idx)
            for idx in (pre_runway_qc.get('missing_review_indices') or [])
            if str(idx).lstrip('-').isdigit()
        ]
        if missing_pre_runway_reviews:
            raise PreRunwayRetryableError(
                'Pre-Runway visual QC was incomplete before any paid submission: '
                + json.dumps({'missing_scene_indices': missing_pre_runway_reviews}, separators=(',', ':'))
            )

        is_bounded_short_preview = (
            options.get('mode') == 'preview'
            and duration_minutes <= 0.6
            and runway_submission_cap > 0
        )

        # A stock-routed short-preview scene is a contract: real footage
        # must clear the same semantic gate before any paid Runway request.
        # Compare the incumbent with a bounded relevance-first Pexels pool in
        # at most two batches of three candidates, preserving the best result.
        if is_bounded_short_preview:
            stock_contract_candidates = [
                scene_idx
                for scene_idx, scene in enumerate(scenes)
                if not str(scene.get('ai_prompt') or '').strip()
                and (
                    scene_idx not in current_reviews
                    or not any(_visual_path(spec) for spec in scene_visuals[scene_idx])
                    or int(current_reviews[scene_idx].get('score', 0)) < quality_threshold
                )
            ]
            stock_candidate_pools: dict[int, list[dict]] = {}
            stock_best_results: dict[int, dict] = {}

            for scene_idx in stock_contract_candidates:
                review = current_reviews.get(scene_idx) or {}
                incumbent_specs = [
                    dict(spec) if isinstance(spec, dict) else {'path': _visual_path(spec), 'start_fraction': 0.35}
                    for spec in scene_visuals[scene_idx][:1]
                    if _visual_path(spec)
                ]
                incumbent = incumbent_specs[0] if incumbent_specs else None
                stock_best_results[scene_idx] = {
                    'score': int(review.get('score', -1)),
                    'review': dict(review) if review else None,
                    'spec': incumbent,
                }

                raw_retry_queries = review.get('retry_queries') or []
                if isinstance(raw_retry_queries, str):
                    raw_retry_queries = [raw_retry_queries]
                raw_visual_queries = scenes[scene_idx].get('visual_queries') or []
                if isinstance(raw_visual_queries, str):
                    raw_visual_queries = [raw_visual_queries]
                query_pool: list[str] = []
                query_keys: set[str] = set()
                for raw_query in [*raw_retry_queries, *raw_visual_queries]:
                    query = str(raw_query or '').strip()
                    key = query.casefold()
                    if not query or key in query_keys:
                        continue
                    query_keys.add(key)
                    query_pool.append(query)
                    if len(query_pool) >= 3:
                        break

                new_specs = _download_ranked_broll_candidates(
                    scene_idx,
                    query_pool,
                    seen_ids,
                    work,
                    credits,
                    file_prefix='pre_runway_stock_tournament',
                    selected_by='pre_runway_stock_tournament',
                    max_candidates=5,
                    search_limit=24,
                    minimum_duration=max(
                        5.0,
                        float(scene_durations[scene_idx]) + 0.35,
                    ),
                )
                frozen_pool: list[dict] = []
                frozen_paths: set[str] = set()
                for spec in [*incumbent_specs, *new_specs]:
                    path = _visual_path(spec)
                    if not path or path in frozen_paths:
                        continue
                    frozen_paths.add(path)
                    frozen_pool.append(
                        dict(spec) if isinstance(spec, dict)
                        else {'path': path, 'start_fraction': 0.35}
                    )
                stock_candidate_pools[scene_idx] = frozen_pool
                if new_specs:
                    visual_replacements.append({
                        'scene_index': scene_idx,
                        'score': int(review.get('score', -1)),
                        'reason': review.get('reason'),
                        'old_best': _visual_path(incumbent) if incumbent else '',
                        'replacement_count': len(new_specs),
                        'stage': 'pre_runway_stock_tournament',
                    })

            for round_index, (candidate_start, candidate_end) in enumerate(((0, 3), (3, 6)), start=1):
                active_scenes = [
                    scene_idx
                    for scene_idx in stock_contract_candidates
                    if int(stock_best_results[scene_idx].get('score', -1)) < quality_threshold
                    and stock_candidate_pools.get(scene_idx, [])[candidate_start:candidate_end]
                    and (
                        candidate_start > 0
                        or len(stock_candidate_pools.get(scene_idx, [])) > 1
                        or stock_best_results[scene_idx].get('spec') is None
                    )
                ]
                if not active_scenes:
                    continue

                set_stage(
                    self,
                    task_id,
                    f'pre_runway_stock_tournament_{round_index}',
                    62,
                    f'Stok sahneleri alaka sıralı adaylarla karşılaştırılıyor ({round_index}/2).',
                )
                round_visuals = [
                    stock_candidate_pools[scene_idx][candidate_start:candidate_end]
                    for scene_idx in active_scenes
                ]
                round_qc = review_scene_visuals(
                    [scenes[scene_idx] for scene_idx in active_scenes],
                    round_visuals,
                    work / f'pre_runway_stock_tournament_{round_index}',
                    len(active_scenes),
                    _missing_review_attempts=0,
                    topic=topic,
                    story_scenes=scenes,
                )
                round_reviews = {
                    int(review.get('scene_index')): review
                    for review in (round_qc.get('reviews') or [])
                    if isinstance(review, dict)
                    and str(review.get('scene_index', '')).lstrip('-').isdigit()
                }
                missing_positions = [
                    position
                    for position in range(len(active_scenes))
                    if position not in round_reviews
                ]
                if missing_positions:
                    raise PreRunwayRetryableError(
                        'Stock-tournament QC was incomplete before any paid submission: '
                        + json.dumps(
                            {
                                'round': round_index,
                                'missing_positions': missing_positions,
                            },
                            separators=(',', ':'),
                        )
                    )

                for position, scene_idx in enumerate(active_scenes):
                    local_review = dict(round_reviews[position])
                    candidate_batch = list(round_visuals[position])
                    reviewed_wrapper = [candidate_batch]
                    _apply_visual_review(
                        reviewed_wrapper,
                        0,
                        local_review,
                        default_fraction=0.35,
                    )
                    chosen_spec = reviewed_wrapper[0][0] if reviewed_wrapper[0] else None
                    score = int(local_review.get('score', -1))
                    if chosen_spec is not None and (
                        stock_best_results[scene_idx].get('spec') is None
                        or score > int(stock_best_results[scene_idx].get('score', -1))
                    ):
                        mapped_review = dict(local_review)
                        mapped_review['scene_index'] = scene_idx
                        mapped_review['best_candidate_index'] = 0
                        mapped_review['best_start_fraction'] = chosen_spec.get('start_fraction', 0.35)
                        stock_best_results[scene_idx] = {
                            'score': score,
                            'review': mapped_review,
                            'spec': chosen_spec,
                        }

            for scene_idx in stock_contract_candidates:
                best_result = stock_best_results[scene_idx]
                best_spec = best_result.get('spec')
                best_review = best_result.get('review')
                scene_visuals[scene_idx] = [best_spec] if best_spec else []
                if best_review:
                    current_reviews[scene_idx] = best_review
                visual_replacements.append({
                    'scene_index': scene_idx,
                    'score': int(best_result.get('score', -1)),
                    'reason': (best_review or {}).get('reason'),
                    'old_best': '',
                    'replacement_count': len(stock_candidate_pools.get(scene_idx, [])),
                    'stage': 'pre_runway_stock_tournament_result',
                })

            failed_stock_contracts = []
            for scene_idx, scene in enumerate(scenes):
                if str(scene.get('ai_prompt') or '').strip():
                    continue
                review = current_reviews.get(scene_idx) or {}
                has_visual = any(
                    _visual_path(spec)
                    for spec in scene_visuals[scene_idx]
                )
                score = int(review.get('score', -1))
                if has_visual and score >= quality_threshold:
                    continue
                raw_retry_queries = review.get('retry_queries') or []
                if isinstance(raw_retry_queries, str):
                    raw_retry_queries = [raw_retry_queries]
                failed_stock_contracts.append({
                    'scene_index': scene_idx,
                    'stock_score': score,
                    'has_visual': has_visual,
                    'reason': str(review.get('reason') or 'missing review')[:240],
                    'retry_queries': [
                        str(query).strip()[:120]
                        for query in raw_retry_queries[:3]
                        if str(query).strip()
                    ],
                })
            if failed_stock_contracts:
                stock_contract_message = (
                    'Short-preview stock contract failed before any paid submission: '
                    + json.dumps(
                        {
                            'quality_threshold': quality_threshold,
                            'failures': failed_stock_contracts,
                        },
                        ensure_ascii=False,
                        separators=(',', ':'),
                    )
                )
                if approved_package is not None:
                    raise FinalVisualQualityError(stock_contract_message)
                raise PreRunwayRetryableError(stock_contract_message)

        def rank_runway_candidates() -> tuple[dict[int, str], list[dict]]:
            prompts: dict[int, str] = {}
            ranked: list[dict] = []
            for candidate_scene_idx, scene in enumerate(scenes):
                candidate_review = current_reviews.get(candidate_scene_idx)
                prompt = (
                    _runway_prompt_for_scene(scene, candidate_review)
                    if (
                        not is_bounded_short_preview
                        or str(scene.get('ai_prompt') or '').strip()
                    )
                    else ''
                )
                if not prompt:
                    continue
                prompts[candidate_scene_idx] = prompt
                if candidate_review and scene_visuals[candidate_scene_idx]:
                    _apply_visual_review(scene_visuals, candidate_scene_idx, candidate_review)
                has_visual = any(_visual_path(spec) for spec in scene_visuals[candidate_scene_idx])
                stock_score = int((candidate_review or {}).get('score', -1))
                if should_rank_runway_candidate(
                    options,
                    duration_minutes,
                    authored_ai_prompt=bool(
                        str(scene.get('ai_prompt') or '').strip()
                    ),
                    has_visual=has_visual,
                    stock_score=stock_score,
                    quality_threshold=quality_threshold,
                ):
                    ranked.append({
                        'scene_index': candidate_scene_idx,
                        'has_visual': has_visual,
                        'stock_score': stock_score,
                    })
            ranked.sort(key=lambda item: (
                0 if not item['has_visual'] else 1,
                item['stock_score'],
                item['scene_index'],
            ))
            return prompts, ranked

        prompt_candidates, ranked_runway_candidates = rank_runway_candidates()

        # Before rejecting an over-budget plan, give only the overflow scenes
        # one bounded stock rescue. The three weakest scenes remain reserved
        # for Runway; better-ranked overflow scenes get a final free chance.
        if is_bounded_short_preview and len(ranked_runway_candidates) > runway_submission_cap:
            overflow_candidates = ranked_runway_candidates[runway_submission_cap:]
            budget_rescued_scenes: list[int] = []
            for candidate in overflow_candidates:
                scene_idx = int(candidate['scene_index'])
                review = current_reviews.get(scene_idx) or {}
                retry_queries = [
                    str(query).strip()
                    for query in (review.get('retry_queries') or [])[:2]
                    if str(query).strip()
                ]
                replacements = _retry_bad_scene(
                    scene_idx,
                    retry_queries,
                    seen_ids,
                    work,
                    credits,
                    file_prefix='pre_runway_budget_rescue',
                    minimum_duration=max(
                        5.0,
                        float(scene_durations[scene_idx]) + 0.35,
                    ),
                    allow_short_fallback=False,
                )
                if not replacements:
                    continue
                existing_specs = list(scene_visuals[scene_idx])
                scene_visuals[scene_idx] = [*replacements, *existing_specs][:3]
                budget_rescued_scenes.append(scene_idx)
                visual_replacements.append({
                    'scene_index': scene_idx,
                    'score': int(review.get('score', 0)),
                    'reason': review.get('reason'),
                    'old_best': _visual_path(existing_specs[0]) if existing_specs else '',
                    'replacement_count': len(replacements),
                    'stage': 'pre_runway_budget_rescue',
                })

            if budget_rescued_scenes:
                set_stage(
                    self,
                    task_id,
                    'pre_runway_budget_rescue',
                    62,
                    'Runway bütçesini aşan stok sahneleri daha kesin aramalarla yenileniyor.',
                )
                budget_rescue_qc = review_scene_visuals(
                    [scenes[idx] for idx in budget_rescued_scenes],
                    [scene_visuals[idx] for idx in budget_rescued_scenes],
                    work / 'pre_runway_budget_rescue',
                    len(budget_rescued_scenes),
                    topic=topic,
                    story_scenes=scenes,
                )
                budget_reviews = {
                    int(review.get('scene_index')): review
                    for review in (budget_rescue_qc.get('reviews') or [])
                    if isinstance(review, dict)
                    and str(review.get('scene_index', '')).lstrip('-').isdigit()
                }
                missing_budget_positions = [
                    position
                    for position in range(len(budget_rescued_scenes))
                    if position not in budget_reviews
                ]
                if missing_budget_positions:
                    raise PreRunwayRetryableError(
                        'Pre-Runway stock rescue QC was incomplete before any paid submission: '
                        + json.dumps({'missing_positions': missing_budget_positions}, separators=(',', ':'))
                    )
                for position, scene_idx in enumerate(budget_rescued_scenes):
                    rescued_review = budget_reviews.get(position)
                    if not rescued_review:
                        continue
                    mapped_review = dict(rescued_review)
                    mapped_review['scene_index'] = scene_idx
                    current_reviews[scene_idx] = mapped_review
                    if scene_visuals[scene_idx]:
                        _apply_visual_review(scene_visuals, scene_idx, mapped_review, default_fraction=0.35)
                prompt_candidates, ranked_runway_candidates = rank_runway_candidates()

        if is_bounded_short_preview and len(ranked_runway_candidates) > runway_submission_cap:
            preflight_details = [
                {
                    'scene_index': int(item['scene_index']),
                    'stock_score': int(item['stock_score']),
                    'has_visual': bool(item['has_visual']),
                    'authored_ai_prompt': bool(scenes[int(item['scene_index'])].get('ai_prompt')),
                }
                for item in ranked_runway_candidates
            ]
            preflight_message = (
                'Short-preview visual plan exceeds bounded Runway budget before any paid submission: '
                + json.dumps({
                    'required_scenes': len(ranked_runway_candidates),
                    'submission_cap': runway_submission_cap,
                    'candidates': preflight_details,
                }, separators=(',', ':'))
            )
            if approved_package is not None:
                raise FinalVisualQualityError(preflight_message)
            raise PreRunwayRetryableError(preflight_message)
        selected_runway = ranked_runway_candidates[:runway_submission_cap]
        selected_runway_indices = {item['scene_index'] for item in selected_runway}
        runway_rank = {
            item['scene_index']: position + 1
            for position, item in enumerate(ranked_runway_candidates)
        }
        runway_allocation = []
        for scene_idx in sorted(prompt_candidates):
            current_review = current_reviews.get(scene_idx) or {}
            has_visual = any(_visual_path(spec) for spec in scene_visuals[scene_idx])
            stock_score = int(current_review.get('score', -1))
            selected = scene_idx in selected_runway_indices
            runway_allocation.append({
                'scene_index': scene_idx,
                'stock_score': stock_score,
                'has_visual': has_visual,
                'rank': runway_rank.get(scene_idx),
                'selected': selected,
                'reason': (
                    'selected_for_generation' if selected
                    else 'stock_approved' if has_visual and stock_score >= quality_threshold
                    else 'submission_cap'
                ),
            })

        _preflight_runway_candidates_before_paid(
            [int(item['scene_index']) for item in selected_runway],
            scene_durations,
            approved_package,
        )

        for candidate in selected_runway:
            scene_idx = int(candidate['scene_index'])
            runway_attempts += 1
            stock_fallback = list(scene_visuals[scene_idx])
            try:
                generation_seconds = _runway_generation_seconds(
                    scene_durations[scene_idx]
                )
                url = generate_scene(
                    prompt_candidates[scene_idx],
                    duration=generation_seconds,
                )
                runway_path = work / f'runway_s{scene_idx:02d}.mp4'
                download_generated_scene(url, runway_path)
                runway_spec = _generated_visual_spec(
                    runway_path,
                )
                scene_visuals[scene_idx] = [runway_spec, *stock_fallback][:3]
                runway_scenes_used += 1
                runway_generated_scenes.append(scene_idx)
            except Exception as exc:
                runway_failed_scenes.append(scene_idx)
                runway_errors.append(f'scene {scene_idx}: {type(exc).__name__}: {str(exc)[:320]}')

        # Re-review the exact clips that will be rendered. Retry search results
        # and generated clips never bypass the final semantic quality gate.
        set_stage(self, task_id, 'final_visual_qc', 69, 'Seçilen final görüntüler anlatıyla son kez eşleştiriliyor.')
        final_visual_qc = review_scene_visuals(
            scenes,
            scene_visuals,
            work / 'final_visual_qc',
            len(scenes),
            topic=topic,
            story_scenes=scenes,
        )
        final_reviews = {
            int(r.get('scene_index')): r
            for r in (final_visual_qc.get('reviews') or [])
            if isinstance(r, dict) and str(r.get('scene_index', '')).lstrip('-').isdigit()
        }
        rejected_final_scenes = [
            idx for idx in range(min(len(scenes), len(scene_visuals)))
            if idx not in final_reviews or int(final_reviews[idx].get('score', 0)) < quality_threshold
        ]
        for scene_idx, review in final_reviews.items():
            if scene_idx not in rejected_final_scenes:
                _apply_visual_review(scene_visuals, scene_idx, review)

        # A final critic has now seen the exact generated clips. Spend at most
        # two evidence-led repair submissions on authored AI scenes, instead of
        # rerunning the whole paid pipeline or accepting a static non-event.
        final_runway_repair_candidates = preview_runway_repair_indices(
            options,
            duration_minutes,
            rejected_final_scenes,
            scenes,
            runway_generated_scenes,
            final_reviews,
        )
        _preflight_runway_candidates_before_paid(
            [int(index) for index in final_runway_repair_candidates],
            scene_durations,
            approved_package,
        )
        if final_runway_repair_candidates:
            set_stage(
                self,
                task_id,
                'final_visual_qc_ai_repair',
                71,
                'Reddedilen özgün sahnelerde hareket kanıtı hedefli olarak yenileniyor.',
            )
        for scene_idx in final_runway_repair_candidates:
            review = final_reviews.get(scene_idx) or {}
            repair_prompt = _runway_prompt_for_scene(scenes[scene_idx], review)
            if not repair_prompt:
                continue
            final_runway_repair_attempts += 1
            runway_attempts += 1
            existing_specs = list(scene_visuals[scene_idx])
            old_best = _visual_path(existing_specs[0]) if existing_specs else ''
            try:
                generation_seconds = _runway_generation_seconds(
                    scene_durations[scene_idx]
                )
                repair_url = generate_scene(
                    repair_prompt,
                    duration=generation_seconds,
                )
                repair_path = work / f'runway_repair_s{scene_idx:02d}.mp4'
                download_generated_scene(repair_url, repair_path)
                repair_spec = _generated_visual_spec(
                    repair_path,
                )
                scene_visuals[scene_idx] = [repair_spec, *existing_specs][:3]
                final_runway_repair_scenes.append(scene_idx)
                visual_replacements.append({
                    'scene_index': scene_idx,
                    'score': int(review.get('score', 0)),
                    'old_best': old_best,
                    'replacement_count': 1,
                    'stage': 'final_visual_qc_ai_repair',
                })
            except Exception as exc:
                final_runway_repair_failures.append(scene_idx)
                runway_errors.append(
                    f'final repair scene {scene_idx}: '
                    f'{type(exc).__name__}: {str(exc)[:320]}'
                )

        # Give every still-rejected clip one bounded free stock rescue. AI
        # scenes already changed above go straight back to exact-clip QC.
        rescued_final_scenes: list[int] = list(final_runway_repair_scenes)
        for scene_idx in rejected_final_scenes:
            if scene_idx in final_runway_repair_scenes:
                continue
            review = final_reviews.get(scene_idx) or {}
            retry_queries = [
                str(q).strip()
                for q in (review.get('retry_queries') or [])[:2]
                if str(q).strip()
            ]
            old_best = _visual_path(scene_visuals[scene_idx][0]) if scene_visuals[scene_idx] else ''
            replacements = _retry_bad_scene(
                scene_idx, retry_queries, seen_ids, work, credits,
                file_prefix='final_qc_rescue',
                minimum_duration=max(
                    5.0,
                    float(scene_durations[scene_idx]) + 0.35,
                ),
                allow_short_fallback=not is_bounded_short_preview,
            )
            if not replacements:
                continue
            existing_specs = list(scene_visuals[scene_idx])
            if scene_idx in runway_generated_scenes and existing_specs:
                scene_visuals[scene_idx] = [existing_specs[0], *replacements, *existing_specs[1:]][:3]
            else:
                scene_visuals[scene_idx] = [*replacements, *existing_specs][:3]
            rescued_final_scenes.append(scene_idx)
            visual_replacements.append({
                'scene_index': scene_idx,
                'score': int(review.get('score', 0)),
                'reason': review.get('reason'),
                'old_best': old_best,
                'replacement_count': len(replacements),
                'stage': 'final_visual_qc_rescue',
            })

        if rescued_final_scenes:
            set_stage(self, task_id, 'final_visual_qc_rescue', 74, 'Reddedilen sahneler daha kesin görüntülerle son kez denetleniyor.')
            rescue_qc = review_scene_visuals(
                [scenes[idx] for idx in rescued_final_scenes],
                [scene_visuals[idx] for idx in rescued_final_scenes],
                work / 'final_visual_qc_rescue',
                len(rescued_final_scenes),
                topic=topic,
                story_scenes=scenes,
            )
            rescue_reviews = {
                int(r.get('scene_index')): r
                for r in (rescue_qc.get('reviews') or [])
                if isinstance(r, dict) and str(r.get('scene_index', '')).lstrip('-').isdigit()
            }
            # Preserve the already-approved decisions; only replace the review
            # for each clip that actually changed during the rescue.
            for scene_idx in rescued_final_scenes:
                final_reviews.pop(scene_idx, None)
            for position, scene_idx in enumerate(rescued_final_scenes):
                if position not in rescue_reviews:
                    continue
                mapped_review = dict(rescue_reviews[position])
                mapped_review['scene_index'] = scene_idx
                final_reviews[scene_idx] = mapped_review
                if int(mapped_review.get('score', 0)) >= quality_threshold:
                    _apply_visual_review(scene_visuals, scene_idx, mapped_review, default_fraction=0.35)
            final_visual_qc = {
                'reviews': [final_reviews[idx] for idx in sorted(final_reviews)],
                'moment_fractions': rescue_qc.get('moment_fractions'),
            }
            rejected_final_scenes = [
                idx for idx in range(min(len(scenes), len(scene_visuals)))
                if idx not in final_reviews or int(final_reviews[idx].get('score', 0)) < quality_threshold
            ]

        visual_qc['final_reviews'] = final_visual_qc.get('reviews') or []
        if rejected_final_scenes:
            failed_required_scenes = [
                idx for idx in rejected_final_scenes
                if idx in runway_failed_scenes
            ]
            if failed_required_scenes:
                raise FinalVisualQualityError(
                    'Runway generation failed within the bounded submission budget: '
                    + json.dumps({
                        'attempts': runway_attempts,
                        'failed_scenes': failed_required_scenes,
                    }, separators=(',', ':'))
                )
            rejected_details = {
                idx: {
                    'score': int((final_reviews.get(idx) or {}).get('score', 0)),
                    'reason': str((final_reviews.get(idx) or {}).get('reason') or 'missing review')[:180],
                }
                for idx in rejected_final_scenes
            }
            diagnostics = {
                'stage': 'after_rescue',
                'accepted': len(scenes) - len(rejected_final_scenes),
                'total': len(scenes),
                'replaced': len(rescued_final_scenes),
                'rejected': rejected_details,
            }
            raise FinalVisualQualityError(
                'Final visual quality gate rejected: '
                + json.dumps(diagnostics, ensure_ascii=False, separators=(',', ':'))
            )

        unresolved_scenes = [idx for idx, specs in enumerate(scene_visuals) if not any(_visual_path(s) for s in specs)]
        if unresolved_scenes:
            raise RuntimeError(f'Visual quality gate rejected unresolved scenes: {unresolved_scenes}')

        visual_specs = [spec for specs in scene_visuals for spec in specs if _visual_path(spec)]
        if not visual_specs:
            raise RuntimeError('No quality-approved visuals were available')

        final_audio_path = voice_path
        audio_design = {
            'music_requested': should_generate_music,
            'music_generated': bool(music_result),
            'music_error': music_error,
        }
        if music_result:
            set_stage(self, task_id, 'audio_design', 69, 'Anlatıcı ile özgün müzik dengeleniyor.')
            try:
                final_audio_path = mix_voice_and_music(
                    voice_path,
                    music_result['path'],
                    work / 'final_audio.mp3',
                )
                audio_design.update({
                    'music_model': music_result.get('model'),
                    'music_duration': music_result.get('duration'),
                    'mixed': True,
                })
            except Exception as exc:
                final_audio_path = voice_path
                audio_design.update({'mixed': False, 'mix_error': str(exc)[:600]})

        initial_reviews = visual_qc.get('reviews') or []
        initial_scores = [
            int(review.get('score', 0))
            for review in initial_reviews
            if isinstance(review, dict) and str(review.get('score', '')).isdigit()
        ]
        initial_avg_visual_score = round(sum(initial_scores) / len(initial_scores), 1) if initial_scores else None
        reviews = [final_reviews[idx] for idx in sorted(final_reviews)]
        scores = [
            int(review.get('score', 0))
            for review in reviews
            if isinstance(review, dict) and str(review.get('score', '')).isdigit()
        ]
        avg_visual_score = round(sum(scores) / len(scores), 1) if scores else None
        visual_qc['initial_average_score'] = initial_avg_visual_score
        visual_qc['average_final_score'] = avg_visual_score

        requested_seconds = duration_minutes * 60
        render_target_duration = _render_target_duration(
            options,
            requested_seconds,
        )
        set_stage(self, task_id, 'render', 76, 'Onaylı ses ve sahneler final kurguya alınıyor.')
        rendered = render_video(
            voice_path=final_audio_path,
            visual_paths=visual_specs,
            narration=package['narration'],
            output_path=work / 'final.mp4',
            scenes=scenes,
            scene_durations=scene_durations,
            scene_visual_paths=scene_visuals,
            target_duration=render_target_duration,
        )

        actual_seconds = float(rendered.get('duration') or 0)
        if options.get('mode') == 'preview':
            duration_ok = _preview_duration_within_gate(
                actual_seconds,
                requested_seconds,
                voice_result.get('duration_after_fit'),
            )
        else:
            duration_ok = requested_seconds * 0.70 <= actual_seconds <= requested_seconds * 1.22
        if not duration_ok:
            raise RuntimeError(f'Final duration gate rejected render: {actual_seconds:.1f}s for requested {requested_seconds:.1f}s')

        if strict_short_preview_duration:
            expected_frames = int(round(requested_seconds * 30))
            actual_frames = int(rendered.get('frame_count') or 0)
            ending_silence = float(rendered.get('ending_silence_seconds') or 0)
            if actual_frames != expected_frames:
                raise RuntimeError(
                    'Final frame gate rejected render: '
                    f'{actual_frames} frames, expected {expected_frames}'
                )
            if not 0.30 <= ending_silence <= 0.90:
                raise RuntimeError(
                    'Final breathing-room gate rejected render: '
                    f'{ending_silence:.3f}s ending silence'
                )

        max_freeze_seconds = float(rendered.get('max_freeze_seconds') or 0)
        freeze_limit = 5.0 if options.get('mode') == 'preview' else 6.0
        if max_freeze_seconds > freeze_limit:
            raise RuntimeError(
                f'Final motion gate rejected {max_freeze_seconds:.1f}s static interval '
                f'(limit {freeze_limit:.1f}s)'
            )

        set_stage(self, task_id, 'upload', 92, 'Final master ve üretim dosyaları kalıcı depolamaya yükleniyor.')
        object_key = f'videos/{task_id}/final.mp4'
        upload_file(rendered['path'], object_key, 'video/mp4')

        caption_key = None
        caption_url = None
        if options.get('subtitles') == 'sidecar':
            safe_language = re.sub(r'[^a-zA-Z0-9_-]+', '', language or 'tr') or 'tr'
            caption_key = f'videos/{task_id}/captions.{safe_language}.srt'
            upload_file(rendered['srt'], caption_key, 'application/x-subrip')
            caption_url = presigned_download_url(caption_key, 86400)

        metadata_key = f'videos/{task_id}/metadata.json'
        meta_path = work / 'metadata.json'
        meta_path.write_text(json.dumps({
            'task_id': task_id,
            'topic': topic,
            'channel_id': channel_id,
            'requested_duration_minutes': duration_minutes,
            'studio_options': options,
            'narration_word_count': package.get('narration_word_count'),
            'target_word_range': package.get('target_word_range'),
            'title': package.get('title'),
            'thumbnail_text': package.get('thumbnail_text'),
            'description': package.get('description'),
            'sources': package.get('sources', []),
            'director_qc': package.get('director_qc', []),
            'stock_scene_qc': package.get('stock_scene_qc'),
            'visual_qc': visual_qc,
            'visual_replacements': visual_replacements,
            'initial_average_visual_qc_score': initial_avg_visual_score,
            'average_visual_qc_score': avg_visual_score,
            'scenes': scenes,
            'scene_visual_specs': scene_visuals,
            'scene_durations': scene_durations,
            'spoken_texts': voice_result.get('spoken_texts', []),
            'voice_name': voice_result.get('voice_name'),
            'voice_duration_before_fit': voice_result.get('duration_before_fit'),
            'voice_duration_after_fit': voice_result.get('duration_after_fit'),
            'voice_tempo_rate': voice_result.get('tempo_rate'),
            'audio_design': audio_design,
            'stock_credits': credits,
            'runway_submission_cap': runway_submission_cap,
            'runway_attempts': runway_attempts,
            'runway_submission_scene_indices': [item['scene_index'] for item in selected_runway],
            'runway_success_scene_indices': sorted(runway_generated_scenes),
            'runway_failure_scene_indices': sorted(runway_failed_scenes),
            'runway_scenes_used': runway_scenes_used,
            'final_runway_repair_attempts': final_runway_repair_attempts,
            'final_runway_repair_scene_indices': sorted(final_runway_repair_scenes),
            'final_runway_repair_failure_scene_indices': sorted(final_runway_repair_failures),
            'runway_allocation': runway_allocation,
            'runway_errors': runway_errors,
            'caption_key': caption_key,
            'burned_subtitles': False,
            'render': rendered,
        }, ensure_ascii=False, indent=2), encoding='utf-8')
        upload_file(meta_path, metadata_key, 'application/json')

        result = {
            'status': 'complete',
            'stage': 'complete',
            'progress': 100,
            'task_id': task_id,
            'channel_id': channel_id,
            'title': package.get('title'),
            'video_key': object_key,
            'download_url': presigned_download_url(object_key, 86400),
            'metadata_key': metadata_key,
            'caption_key': caption_key,
            'caption_url': caption_url,
            'burned_subtitles': False,
            'text_layers': 0,
            'duration': rendered.get('duration'),
            'shots': rendered.get('shots'),
            'scenes': len(scenes),
            'unique_visuals': rendered.get('unique_visuals'),
            'runway_submission_cap': runway_submission_cap,
            'runway_attempts': runway_attempts,
            'runway_submission_scene_indices': [item['scene_index'] for item in selected_runway],
            'runway_success_scene_indices': sorted(runway_generated_scenes),
            'runway_scenes_used': runway_scenes_used,
            'final_runway_repair_attempts': final_runway_repair_attempts,
            'final_runway_repair_scene_indices': sorted(final_runway_repair_scenes),
            'final_runway_repair_failure_scene_indices': sorted(final_runway_repair_failures),
            'resolution': rendered.get('resolution'),
            'scene_synced': rendered.get('scene_synced'),
            'director_qc_applied': bool(package.get('director_qc')),
            'stock_scene_qc_applied': bool(package.get('stock_scene_qc')),
            'visual_qc_reviews': len(reviews),
            'visual_replacements': len(visual_replacements),
            'initial_average_visual_qc_score': initial_avg_visual_score,
            'average_visual_qc_score': avg_visual_score,
            'narration_word_count': package.get('narration_word_count'),
            'voice_duration_before_fit': voice_result.get('duration_before_fit'),
            'voice_duration_after_fit': voice_result.get('duration_after_fit'),
            'voice_tempo_rate': voice_result.get('tempo_rate'),
            'audio_design': audio_design,
            'studio_options': options,
        }
        mark_success(task_id, result)
        return result
    except Exception as exc:
        if (
            runway_attempts == 0
            and not isinstance(exc, FinalVisualQualityError)
            and int(getattr(self.request, 'retries', 0) or 0) < int(self.max_retries or 0)
        ):
            set_stage(
                self,
                task_id,
                'plan_retry',
                6,
                'Görsel ön kontrol yenileniyor; ücretli üretim başlamadan yeni storyboard hazırlanıyor.',
            )
            raise
        if runway_attempts > 0 and not isinstance(exc, FinalVisualQualityError):
            bounded_error = FinalVisualQualityError(
                f'Post-Runway pipeline failed after {runway_attempts} bounded submissions: {type(exc).__name__}'
            )
            mark_failure(task_id, bounded_error)
            raise bounded_error from exc
        mark_failure(task_id, exc)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)

