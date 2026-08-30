from __future__ import annotations

from pathlib import Path
import json
import math
import re
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.celery_app import celery
from app.services.audio_design import generate_music_bed, mix_voice_and_music
from app.services.director import direct_and_qc
from app.services.pexels import find_broll, download_broll
from app.services.render import render_video
from app.services.research import research_and_script
from app.services.runway import generate_scene, download_generated_scene
from app.services.storage import upload_file, presigned_download_url
from app.services.studio_state import mark_failure, mark_success, set_stage, update_job
from app.services.visual_qc import review_scene_visuals
from app.services.voice import synthesize_scene_sequence


class FinalVisualQualityError(RuntimeError):
    """A bounded semantic-quality rejection that should not rerun the whole pipeline."""


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


def _collect_broll(scenes: list[dict], work: Path) -> dict:
    scene_visuals: list[list[str]] = [[] for _ in scenes]
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
    for scene_idx, query_idx, query in requests:
        candidates = search_results.get((scene_idx, query_idx, query)) or []
        if not candidates:
            continue
        start = (scene_idx * 2 + query_idx) % min(6, len(candidates))
        ordered = candidates[start:] + candidates[:start]
        item = next((c for c in ordered if c.get('pexels_id') not in seen_ids), None)
        if not item:
            continue
        seen_ids.add(item.get('pexels_id'))
        path = work / f'pexels_s{scene_idx:02d}_{query_idx:02d}.mp4'
        download_specs.append((scene_idx, query_idx, query, item, path))

    def download_one(spec):
        scene_idx, query_idx, query, item, path = spec
        download_broll(item, path)
        return scene_idx, query_idx, query, item, path

    with ThreadPoolExecutor(max_workers=min(6, max(1, len(download_specs)))) as executor:
        futures = [executor.submit(download_one, spec) for spec in download_specs]
        for future in as_completed(futures):
            try:
                scene_idx, _query_idx, query, item, path = future.result()
            except Exception:
                continue
            scene_visuals[scene_idx].append(str(path))
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


def _retry_bad_scene(
    scene_idx: int,
    retry_queries: list[str],
    seen_ids: set,
    work: Path,
    credits: list[dict],
    file_prefix: str = 'qc',
) -> list[dict]:
    replacement_candidates: list[tuple[str, dict]] = []
    if not retry_queries:
        return []

    search_errors: list[Exception] = []
    successful_searches = 0
    with ThreadPoolExecutor(max_workers=min(2, len(retry_queries))) as retry_pool:
        retry_map = {retry_pool.submit(find_broll, query, 18): query for query in retry_queries}
        for future in as_completed(retry_map):
            query = retry_map[future]
            try:
                candidates = future.result() or []
                successful_searches += 1
            except Exception as exc:
                search_errors.append(exc)
                continue
            item = next((candidate for candidate in candidates if candidate.get('pexels_id') not in seen_ids), None)
            if item:
                seen_ids.add(item.get('pexels_id'))
                replacement_candidates.append((query, item))

    if successful_searches == 0 and search_errors:
        raise RuntimeError(f'Pexels retry search failed for scene {scene_idx}') from search_errors[0]

    replacements: list[dict] = []
    download_errors: list[Exception] = []
    safe_prefix = re.sub(r'[^a-zA-Z0-9_-]+', '_', file_prefix)[:32] or 'qc'
    for retry_idx, (query, item) in enumerate(replacement_candidates[:1]):
        try:
            path = work / f'{safe_prefix}_s{scene_idx:02d}_{retry_idx:02d}.mp4'
            download_broll(item, path)
            replacements.append({'path': str(path), 'start_fraction': 0.35})
            credits.append({
                'source': 'Pexels',
                'scene_index': scene_idx,
                'query': query,
                'creator_name': item.get('creator_name'),
                'creator_url': item.get('creator_url'),
                'page_url': item.get('page_url'),
                'pexels_id': item.get('pexels_id'),
                'selected_by': 'final_visual_qc_rescue' if safe_prefix == 'final_qc_rescue' else 'visual_qc_retry',
            })
        except Exception as exc:
            download_errors.append(exc)

    if replacement_candidates and not replacements and download_errors:
        raise RuntimeError(f'Pexels retry download failed for scene {scene_idx}') from download_errors[0]
    return replacements


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
    combined = f'{narration} {original} {visible_action}'.lower()
    mechanism_guardrails: list[str] = []
    oled_claim = bool(re.search(
        r'\b(?:oled|true[ -]black)\b|gerçek siyah|emissive\s+(?:pixel|display|screen)',
        combined,
    ))
    if oled_claim:
        mechanism_guardrails.append(
            'OLED proof: extreme macro of a real subpixel matrix; emitters in a shaped black region are '
            'visibly off while adjacent RGB subpixels stay lit. Never use a whole-screen dim or fade, '
            'hand-only tap, digital noise or generic dark phone.'
        )
        power_claim = bool(re.search(
            r'\b(?:power|energy|watt(?:age)?|consumption)\b|güç|enerji|tüket',
            combined,
        ))
        if power_claim:
            mechanism_guardrails.append(
                'If power use is spoken, show a real physical meter visibly falling in the same shot.'
            )

    opening = 'One continuous five-second photorealistic 16:9 documentary shot. '
    guardrail_clause = (' '.join(mechanism_guardrails) + ' ') if mechanism_guardrails else ''
    narration_clause = f'Literal narration to prove: {narration}. ' if narration else ''
    evidence_clause = f'QC evidence to satisfy: {visible_action}. ' if visible_action else ''
    closing = 'Subtle camera motion; no text, logos, charts, glitch, watermark or metaphor.'
    required = opening + guardrail_clause + narration_clause + evidence_clause + closing
    original_label = 'Core shot direction: '
    remaining_units = max(
        0,
        1000
        - len(required.encode('utf-16-le')) // 2
        - len(original_label.encode('utf-16-le')) // 2
        - 2,
    )
    original_value = _truncate_utf16(original, remaining_units) if original and remaining_units else ''
    original_clause = f'{original_label}{original_value}. ' if original_value else ''
    return _truncate_utf16(
        opening
        + guardrail_clause
        + narration_clause
        + evidence_clause
        + original_clause
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
    try:
        fraction = float(review.get('best_start_fraction', chosen.get('start_fraction', default_fraction)))
    except Exception:
        fraction = default_fraction
    chosen['start_fraction'] = max(0.0, min(fraction, 0.95))
    scene_visuals[scene_idx] = [chosen]


def _max_runway_scenes(options: dict, scene_count: int, duration_minutes: float) -> int:
    if options.get('mode') == 'preview':
        return min(3, scene_count) if duration_minutes <= 0.6 else 0
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
    work = Path('/tmp/youtube_factory') / task_id
    work.mkdir(parents=True, exist_ok=True)
    update_job(task_id, kind='render', spec=_task_spec(topic, duration_minutes, language, channel_id, options))
    runway_attempts = 0

    try:
        package = _prepare_package(self, task_id, topic, duration_minutes, language, options, approved_package)
        scenes = package['scenes']
        (work / 'package.json').write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding='utf-8')

        set_stage(self, task_id, 'voice_and_visuals', 24, 'Anlatıcı ve görsel adaylar paralel hazırlanıyor.')
        with ThreadPoolExecutor(max_workers=2) as stage_pool:
            voice_future = stage_pool.submit(
                synthesize_scene_sequence,
                scenes,
                task_id,
                duration_minutes * 60,
            )
            broll_future = stage_pool.submit(_collect_broll, scenes, work)
            voice_result = voice_future.result()
            broll_result = broll_future.result()

        voice_path = voice_result['path']
        scene_durations = voice_result['scene_durations']
        scene_visuals: list[list[str | dict]] = broll_result['scene_visuals']
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
                continue

            if not review:
                scene_visuals[scene_idx] = [{'path': _visual_path(paths[0]), 'start_fraction': 0.25}]
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

            if score >= quality_threshold:
                scene_visuals[scene_idx] = [{'path': best_path, 'start_fraction': best_fraction}]
                continue

            retry_queries = [str(q).strip() for q in (review.get('retry_queries') or [])[:2] if str(q).strip()]
            replacements = _retry_bad_scene(scene_idx, retry_queries, seen_ids, work, credits)
            if replacements:
                scene_visuals[scene_idx] = replacements
                visual_replacements.append({
                    'scene_index': scene_idx,
                    'score': score,
                    'reason': review.get('reason'),
                    'old_best': best_path,
                    'replacement_count': len(replacements),
                })
            else:
                scene_visuals[scene_idx] = []

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
        )
        current_reviews = {
            int(review.get('scene_index')): review
            for review in (pre_runway_qc.get('reviews') or [])
            if isinstance(review, dict)
            and str(review.get('scene_index', '')).lstrip('-').isdigit()
        }
        ranked_runway_candidates: list[dict] = []
        prompt_candidates: dict[int, str] = {}
        for scene_idx, scene in enumerate(scenes):
            current_review = current_reviews.get(scene_idx)
            prompt = _runway_prompt_for_scene(scene, current_review)
            if not prompt:
                continue
            prompt_candidates[scene_idx] = prompt
            if current_review and scene_visuals[scene_idx]:
                _apply_visual_review(scene_visuals, scene_idx, current_review)
            has_visual = any(_visual_path(spec) for spec in scene_visuals[scene_idx])
            stock_score = int((current_review or {}).get('score', -1))
            if not has_visual or stock_score < quality_threshold:
                ranked_runway_candidates.append({
                    'scene_index': scene_idx,
                    'has_visual': has_visual,
                    'stock_score': stock_score,
                })

        ranked_runway_candidates.sort(key=lambda item: (
            0 if not item['has_visual'] else 1,
            item['stock_score'],
            item['scene_index'],
        ))
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

        for candidate in selected_runway:
            scene_idx = int(candidate['scene_index'])
            runway_attempts += 1
            stock_fallback = list(scene_visuals[scene_idx])
            try:
                url = generate_scene(prompt_candidates[scene_idx], duration=5)
                runway_path = work / f'runway_s{scene_idx:02d}.mp4'
                download_generated_scene(url, runway_path)
                runway_spec = {'path': str(runway_path), 'start_fraction': 0.0}
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

        # Give the exact final review one bounded, scene-specific rescue pass.
        # This reuses its evidence-based retry queries instead of restarting the
        # entire script, voice and candidate pipeline for a stock-search miss.
        rescued_final_scenes: list[int] = []
        for scene_idx in rejected_final_scenes:
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
            set_stage(self, task_id, 'final_visual_qc_rescue', 72, 'Reddedilen sahneler daha kesin aramalarla son kez yenileniyor.')
            rescue_qc = review_scene_visuals(
                [scenes[idx] for idx in rescued_final_scenes],
                [scene_visuals[idx] for idx in rescued_final_scenes],
                work / 'final_visual_qc_rescue',
                len(rescued_final_scenes),
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

        set_stage(self, task_id, 'render', 76, 'Onaylı ses ve sahneler final kurguya alınıyor.')
        rendered = render_video(
            voice_path=final_audio_path,
            visual_paths=visual_specs,
            narration=package['narration'],
            output_path=work / 'final.mp4',
            scenes=scenes,
            scene_durations=scene_durations,
            scene_visual_paths=scene_visuals,
        )

        requested_seconds = duration_minutes * 60
        actual_seconds = float(rendered.get('duration') or 0)
        if options.get('mode') == 'preview':
            duration_ok = abs(actual_seconds - requested_seconds) <= 0.5
        else:
            duration_ok = requested_seconds * 0.70 <= actual_seconds <= requested_seconds * 1.22
        if not duration_ok:
            raise RuntimeError(f'Final duration gate rejected render: {actual_seconds:.1f}s for requested {requested_seconds:.1f}s')

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
            'resolution': rendered.get('resolution'),
            'scene_synced': rendered.get('scene_synced'),
            'director_qc_applied': bool(package.get('director_qc')),
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
