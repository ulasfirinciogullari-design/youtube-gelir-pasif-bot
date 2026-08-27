from pathlib import Path
import json
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.celery_app import celery
from app.services.research import research_and_script
from app.services.director import direct_and_qc
from app.services.visual_qc import review_scene_visuals
from app.services.voice import synthesize_scene_sequence
from app.services.pexels import find_broll, download_broll
from app.services.runway import generate_scene, download_generated_scene
from app.services.render import render_video
from app.services.storage import upload_file, presigned_download_url


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


def _retry_bad_scene(scene_idx: int, retry_queries: list[str], seen_ids: set, work: Path, credits: list[dict]) -> list[str]:
    replacement_candidates: list[tuple[str, dict]] = []
    if not retry_queries:
        return []
    with ThreadPoolExecutor(max_workers=min(2, len(retry_queries))) as retry_pool:
        retry_map = {retry_pool.submit(find_broll, query, 18): query for query in retry_queries}
        for future in as_completed(retry_map):
            query = retry_map[future]
            try:
                candidates = future.result() or []
            except Exception:
                continue
            item = next((c for c in candidates if c.get('pexels_id') not in seen_ids), None)
            if item:
                seen_ids.add(item.get('pexels_id'))
                replacement_candidates.append((query, item))

    replacements: list[str] = []
    # Keep only one replacement until it passes a later full-quality production review.
    for retry_idx, (query, item) in enumerate(replacement_candidates[:1]):
        try:
            path = work / f'qc_s{scene_idx:02d}_{retry_idx:02d}.mp4'
            download_broll(item, path)
            replacements.append(str(path))
            credits.append({
                'source': 'Pexels',
                'scene_index': scene_idx,
                'query': query,
                'creator_name': item.get('creator_name'),
                'creator_url': item.get('creator_url'),
                'page_url': item.get('page_url'),
                'pexels_id': item.get('pexels_id'),
                'selected_by': 'visual_qc_retry',
            })
        except Exception:
            continue
    return replacements


@celery.task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=2)
def run_video_pipeline(self, topic: str, duration_minutes: float = 5, language: str = 'tr', channel_id: str | None = None):
    task_id = self.request.id
    work = Path('/tmp/youtube_factory') / task_id
    work.mkdir(parents=True, exist_ok=True)
    try:
        self.update_state(state='PROGRESS', meta={'stage': 'research', 'progress': 7})
        draft = research_and_script(topic, duration_minutes, language)
        (work / 'draft.json').write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding='utf-8')

        self.update_state(state='PROGRESS', meta={'stage': 'director_qc', 'progress': 14})
        package = direct_and_qc(draft, topic, duration_minutes, language)
        scenes = package['scenes']
        (work / 'package.json').write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding='utf-8')

        self.update_state(state='PROGRESS', meta={'stage': 'voice_and_visuals', 'progress': 24})
        with ThreadPoolExecutor(max_workers=2) as stage_pool:
            voice_future = stage_pool.submit(synthesize_scene_sequence, scenes, task_id)
            broll_future = stage_pool.submit(_collect_broll, scenes, work)
            voice_result = voice_future.result()
            broll_result = broll_future.result()

        voice_path = voice_result['path']
        scene_durations = voice_result['scene_durations']
        scene_visuals = broll_result['scene_visuals']
        credits = broll_result['credits']
        seen_ids = broll_result['seen_ids']

        self.update_state(state='PROGRESS', meta={'stage': 'visual_qc', 'progress': 48})
        visual_qc = review_scene_visuals(
            scenes,
            scene_visuals,
            work,
            max_scenes=len(scenes) if duration_minutes <= 1 else min(10, len(scenes)),
        )
        visual_replacements: list[dict] = []
        unresolved_scenes: list[int] = []

        reviews_by_scene = {
            int(r.get('scene_index')): r
            for r in (visual_qc.get('reviews') or [])
            if isinstance(r, dict) and str(r.get('scene_index', '')).lstrip('-').isdigit()
        }

        for scene_idx, scene in enumerate(scenes):
            review = reviews_by_scene.get(scene_idx)
            paths = scene_visuals[scene_idx]
            if not paths:
                unresolved_scenes.append(scene_idx)
                continue
            if not review:
                # For unreviewed long-form scenes, retain only the first candidate; never cycle through an unchecked pool.
                scene_visuals[scene_idx] = [paths[0]]
                continue

            best_idx = int(review.get('best_candidate_index', 0))
            score = int(review.get('score', 0))
            best_idx = min(max(best_idx, 0), len(paths) - 1)
            best_path = paths[best_idx]

            if score >= 80:
                scene_visuals[scene_idx] = [best_path]
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
                unresolved_scenes.append(scene_idx)

        self.update_state(state='PROGRESS', meta={'stage': 'ai_scene', 'progress': 62})
        runway_errors: list[str] = []
        runway_scenes_used = 0
        max_runway = 0 if duration_minutes <= 0.6 else (1 if duration_minutes <= 2 else 2)

        # Use Runway only when explicitly storyboarded and allowed by the duration mode.
        for scene_idx, scene in enumerate(scenes):
            prompt = scene.get('ai_prompt')
            if not prompt or runway_scenes_used >= max_runway:
                continue
            try:
                url = generate_scene(str(prompt), duration=5)
                runway_path = work / f'runway_s{scene_idx:02d}.mp4'
                download_generated_scene(url, runway_path)
                scene_visuals[scene_idx] = [str(runway_path)]
                runway_scenes_used += 1
                if scene_idx in unresolved_scenes:
                    unresolved_scenes.remove(scene_idx)
            except Exception as exc:
                runway_errors.append(f'scene {scene_idx}: {str(exc)[:400]}')

        # Professional gate: never borrow an unrelated neighboring/global visual just to finish a render.
        unresolved_scenes = sorted(set(idx for idx in unresolved_scenes if not scene_visuals[idx]))
        if unresolved_scenes:
            raise RuntimeError(f'Visual quality gate rejected unresolved scenes: {unresolved_scenes}')

        visual_paths = [p for paths in scene_visuals for p in paths]
        if not visual_paths:
            raise RuntimeError('No quality-approved visuals were available')

        reviews = visual_qc.get('reviews') or []
        scores = [int(r.get('score', 0)) for r in reviews if isinstance(r, dict) and str(r.get('score', '')).isdigit()]
        avg_visual_score = round(sum(scores) / len(scores), 1) if scores else None

        self.update_state(state='PROGRESS', meta={'stage': 'render', 'progress': 74})
        rendered = render_video(
            voice_path=voice_path,
            visual_paths=visual_paths,
            narration=package['narration'],
            output_path=work / 'final.mp4',
            scenes=scenes,
            scene_durations=scene_durations,
            scene_visual_paths=scene_visuals,
        )

        # Final duration guard. 30 sec can vary slightly, but never become a 98 sec video again.
        requested_seconds = duration_minutes * 60
        actual_seconds = float(rendered.get('duration') or 0)
        if actual_seconds > requested_seconds * 1.22 or actual_seconds < requested_seconds * 0.70:
            raise RuntimeError(f'Final duration gate rejected render: {actual_seconds:.1f}s for requested {requested_seconds:.1f}s')

        self.update_state(state='PROGRESS', meta={'stage': 'upload', 'progress': 92})
        object_key = f'videos/{task_id}/final.mp4'
        upload_file(rendered['path'], object_key, 'video/mp4')
        metadata_key = f'videos/{task_id}/metadata.json'
        meta_path = work / 'metadata.json'
        meta_path.write_text(json.dumps({
            'task_id': task_id,
            'topic': topic,
            'channel_id': channel_id,
            'requested_duration_minutes': duration_minutes,
            'narration_word_count': package.get('narration_word_count'),
            'target_word_range': package.get('target_word_range'),
            'title': package.get('title'),
            'thumbnail_text': package.get('thumbnail_text'),
            'description': package.get('description'),
            'sources': package.get('sources', []),
            'director_qc': package.get('director_qc', []),
            'visual_qc': visual_qc,
            'visual_replacements': visual_replacements,
            'average_visual_qc_score': avg_visual_score,
            'scenes': scenes,
            'scene_durations': scene_durations,
            'spoken_texts': voice_result.get('spoken_texts', []),
            'voice_name': voice_result.get('voice_name'),
            'stock_credits': credits,
            'runway_scenes_used': runway_scenes_used,
            'runway_errors': runway_errors,
            'render': rendered,
        }, ensure_ascii=False, indent=2), encoding='utf-8')
        upload_file(meta_path, metadata_key, 'application/json')

        return {
            'status': 'complete', 'stage': 'complete', 'progress': 100,
            'task_id': task_id, 'channel_id': channel_id,
            'title': package.get('title'), 'video_key': object_key,
            'download_url': presigned_download_url(object_key, 86400),
            'metadata_key': metadata_key, 'duration': rendered.get('duration'),
            'shots': rendered.get('shots'), 'scenes': len(scenes),
            'unique_visuals': rendered.get('unique_visuals'),
            'runway_scenes_used': runway_scenes_used, 'resolution': rendered.get('resolution'),
            'scene_synced': rendered.get('scene_synced'),
            'single_text_layer': rendered.get('single_text_layer'),
            'director_qc_applied': bool(package.get('director_qc')),
            'visual_qc_reviews': len(reviews),
            'visual_replacements': len(visual_replacements),
            'average_visual_qc_score': avg_visual_score,
            'narration_word_count': package.get('narration_word_count'),
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)
