from pathlib import Path
import json
import shutil

from app.celery_app import celery
from app.services.research import research_and_script
from app.services.voice import synthesize_scene_sequence
from app.services.pexels import find_broll, download_broll
from app.services.runway import generate_scene, download_generated_scene
from app.services.render import render_video
from app.services.storage import upload_file, presigned_download_url


@celery.task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=2)
def run_video_pipeline(
    self,
    topic: str,
    duration_minutes: float = 5,
    language: str = 'tr',
    channel_id: str | None = None,
):
    task_id = self.request.id
    work = Path('/tmp/youtube_factory') / task_id
    work.mkdir(parents=True, exist_ok=True)

    try:
        self.update_state(state='PROGRESS', meta={'stage': 'research', 'progress': 8})
        package = research_and_script(topic, duration_minutes, language)
        scenes = package['scenes']
        (work / 'package.json').write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding='utf-8')

        self.update_state(state='PROGRESS', meta={'stage': 'voice', 'progress': 22})
        voice_result = synthesize_scene_sequence(scenes, task_id)
        voice_path = voice_result['path']
        scene_durations = voice_result['scene_durations']

        self.update_state(state='PROGRESS', meta={'stage': 'broll', 'progress': 36})
        scene_visuals: list[list[str]] = [[] for _ in scenes]
        credits: list[dict] = []
        seen_ids: set[int | str] = set()

        for scene_idx, scene in enumerate(scenes):
            queries = [q for q in scene.get('visual_queries', []) if isinstance(q, str) and q.strip()][:3]
            for query_idx, query in enumerate(queries):
                try:
                    candidates = find_broll(query, per_page=14)
                    if not candidates:
                        continue
                    start = (scene_idx + query_idx) % min(5, len(candidates))
                    ordered_candidates = candidates[start:] + candidates[:start]
                    item = next((c for c in ordered_candidates if c.get('pexels_id') not in seen_ids), None)
                    if not item:
                        continue
                    seen_ids.add(item.get('pexels_id'))
                    path = work / f'pexels_s{scene_idx:02d}_{query_idx:02d}.mp4'
                    download_broll(item, path)
                    scene_visuals[scene_idx].append(str(path))
                    credits.append({
                        'source': 'Pexels',
                        'scene_index': scene_idx,
                        'query': query,
                        'creator_name': item.get('creator_name'),
                        'creator_url': item.get('creator_url'),
                        'page_url': item.get('page_url'),
                        'pexels_id': item.get('pexels_id'),
                    })
                except Exception:
                    continue

        self.update_state(state='PROGRESS', meta={'stage': 'ai_scene', 'progress': 55})
        runway_errors: list[str] = []
        runway_scenes_used = 0
        max_runway = 1 if duration_minutes <= 1.5 else 2
        for scene_idx, scene in enumerate(scenes):
            prompt = scene.get('ai_prompt')
            if not prompt or runway_scenes_used >= max_runway:
                continue
            try:
                url = generate_scene(str(prompt), duration=5)
                runway_path = work / f'runway_s{scene_idx:02d}.mp4'
                download_generated_scene(url, runway_path)
                scene_visuals[scene_idx].insert(0, str(runway_path))
                runway_scenes_used += 1
            except Exception as exc:
                runway_errors.append(f'scene {scene_idx}: {str(exc)[:400]}')

        # Fill rare empty scenes from the nearest scene rather than random global footage.
        for idx, paths in enumerate(scene_visuals):
            if paths:
                continue
            nearest = None
            for distance in range(1, len(scene_visuals)):
                left = idx - distance
                right = idx + distance
                if left >= 0 and scene_visuals[left]:
                    nearest = scene_visuals[left][:]
                    break
                if right < len(scene_visuals) and scene_visuals[right]:
                    nearest = scene_visuals[right][:]
                    break
            if nearest:
                scene_visuals[idx] = nearest

        visual_paths = [p for paths in scene_visuals for p in paths]
        if not visual_paths:
            raise RuntimeError('No usable visuals were found from Pexels or Runway')

        self.update_state(state='PROGRESS', meta={'stage': 'render', 'progress': 68})
        rendered = render_video(
            voice_path=voice_path,
            visual_paths=visual_paths,
            narration=package['narration'],
            output_path=work / 'final.mp4',
            scenes=scenes,
            scene_durations=scene_durations,
            scene_visual_paths=scene_visuals,
        )

        self.update_state(state='PROGRESS', meta={'stage': 'upload', 'progress': 90})
        object_key = f'videos/{task_id}/final.mp4'
        upload_file(rendered['path'], object_key, 'video/mp4')
        metadata_key = f'videos/{task_id}/metadata.json'
        meta_path = work / 'metadata.json'
        meta_path.write_text(json.dumps({
            'task_id': task_id,
            'topic': topic,
            'channel_id': channel_id,
            'title': package.get('title'),
            'thumbnail_text': package.get('thumbnail_text'),
            'description': package.get('description'),
            'sources': package.get('sources', []),
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
            'status': 'complete',
            'stage': 'complete',
            'progress': 100,
            'task_id': task_id,
            'channel_id': channel_id,
            'title': package.get('title'),
            'video_key': object_key,
            'download_url': presigned_download_url(object_key, 86400),
            'metadata_key': metadata_key,
            'duration': rendered.get('duration'),
            'shots': rendered.get('shots'),
            'scenes': len(scenes),
            'unique_visuals': rendered.get('unique_visuals'),
            'runway_scenes_used': runway_scenes_used,
            'resolution': rendered.get('resolution'),
            'scene_synced': rendered.get('scene_synced'),
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)
