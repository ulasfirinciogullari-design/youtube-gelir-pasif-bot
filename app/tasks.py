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
    value.setdefault('quality_threshold', 84 if value['mode'] == 'production' else 86)
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
) -> list[dict]:
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

    replacements: list[dict] = []
    for retry_idx, (query, item) in enumerate(replacement_candidates[:1]):
        try:
            path = work / f'qc_s{scene_idx:02d}_{retry_idx:02d}.mp4'
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
                'selected_by': 'visual_qc_retry',
            })
        except Exception:
            continue
    return replacements


def _visual_path(spec: str | dict) -> str:
    if isinstance(spec, dict):
        return str(spec.get('path') or '')
    return str(spec)


def _max_runway_scenes(options: dict, scene_count: int) -> int:
    if options.get('mode') == 'preview':
        return 0
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


@celery.task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=2)
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

        set_stage(self, task_id, 'ai_scene', 61, 'Stok görüntünün anlatamadığı sahneler için özgün görüntüler hazırlanıyor.')
        runway_errors: list[str] = []
        runway_scenes_used = 0
        max_runway = _max_runway_scenes(options, len(scenes))
        for scene_idx, scene in enumerate(scenes):
            prompt = scene.get('ai_prompt')
            if not prompt or runway_scenes_used >= max_runway:
                continue
            try:
                url = generate_scene(str(prompt), duration=5)
                runway_path = work / f'runway_s{scene_idx:02d}.mp4'
                download_generated_scene(url, runway_path)
                scene_visuals[scene_idx] = [{'path': str(runway_path), 'start_fraction': 0.0}]
                runway_scenes_used += 1
            except Exception as exc:
                runway_errors.append(f'scene {scene_idx}: {str(exc)[:400]}')

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
        visual_qc['final_reviews'] = final_visual_qc.get('reviews') or []
        if rejected_final_scenes:
            raise RuntimeError(f'Final visual quality gate rejected scenes: {rejected_final_scenes}')

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

        reviews = visual_qc.get('reviews') or []
        scores = [int(r.get('score', 0)) for r in reviews if isinstance(r, dict) and str(r.get('score', '')).isdigit()]
        avg_visual_score = round(sum(scores) / len(scores), 1) if scores else None

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
            'runway_scenes_used': runway_scenes_used,
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
            'runway_scenes_used': runway_scenes_used,
            'resolution': rendered.get('resolution'),
            'scene_synced': rendered.get('scene_synced'),
            'director_qc_applied': bool(package.get('director_qc')),
            'visual_qc_reviews': len(reviews),
            'visual_replacements': len(visual_replacements),
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
        mark_failure(task_id, exc)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
