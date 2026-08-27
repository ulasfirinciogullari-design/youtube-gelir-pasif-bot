from pathlib import Path
import json
import shutil

from app.celery_app import celery
from app.services.research import research_and_script
from app.services.voice import synthesize_voice
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
        (work / 'package.json').write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding='utf-8')

        self.update_state(state='PROGRESS', meta={'stage': 'voice', 'progress': 22})
        tts_text = package.get('tts_narration') or package['narration']
        voice_path = synthesize_voice(tts_text, task_id)

        self.update_state(state='PROGRESS', meta={'stage': 'broll', 'progress': 36})
        visual_paths: list[str] = []
        credits: list[dict] = []
        seen_ids: set[int | str] = set()
        queries = [q for q in package.get('visual_queries', []) if isinstance(q, str) and q.strip()]
        max_stock = 24 if duration_minutes <= 1.5 else 36
        for idx, query in enumerate(queries[:max_stock]):
            try:
                candidates = find_broll(query, per_page=12)
                if not candidates:
                    continue
                start = idx % min(4, len(candidates))
                ordered_candidates = candidates[start:] + candidates[:start]
                item = next((c for c in ordered_candidates if c.get('pexels_id') not in seen_ids), None)
                if not item:
                    continue
                seen_ids.add(item.get('pexels_id'))
                path = work / f'pexels_{idx:02d}.mp4'
                download_broll(item, path)
                visual_paths.append(str(path))
                credits.append({
                    'source': 'Pexels',
                    'query': query,
                    'creator_name': item.get('creator_name'),
                    'creator_url': item.get('creator_url'),
                    'page_url': item.get('page_url'),
                    'pexels_id': item.get('pexels_id'),
                })
            except Exception:
                continue

        self.update_state(state='PROGRESS', meta={'stage': 'ai_scene', 'progress': 55})
        runway_paths: list[str] = []
        runway_errors: list[str] = []
        for idx, prompt in enumerate((package.get('ai_scenes') or [])[:3]):
            try:
                if isinstance(prompt, dict):
                    prompt = prompt.get('prompt') or prompt.get('description') or json.dumps(prompt)
                url = generate_scene(str(prompt), duration=5)
                runway_path = work / f'runway_{idx:02d}.mp4'
                download_generated_scene(url, runway_path)
                runway_paths.append(str(runway_path))
            except Exception as exc:
                runway_errors.append(str(exc)[:500])

        # Distribute premium AI shots through the timeline instead of stacking them at the start.
        if runway_paths:
            if not visual_paths:
                visual_paths = runway_paths[:]
            else:
                for idx, path in enumerate(runway_paths):
                    insert_at = min(len(visual_paths), round((idx + 0.25) * len(visual_paths) / max(len(runway_paths), 1)))
                    visual_paths.insert(insert_at, path)

        if not visual_paths:
            raise RuntimeError('No usable visuals were found from Pexels or Runway')

        self.update_state(state='PROGRESS', meta={'stage': 'render', 'progress': 68})
        rendered = render_video(
            voice_path=voice_path,
            visual_paths=visual_paths,
            narration=package['narration'],
            output_path=work / 'final.mp4',
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
            'overlay_phrases': package.get('overlay_phrases', []),
            'stock_credits': credits,
            'runway_scenes_used': len(runway_paths),
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
            'unique_visuals': rendered.get('unique_visuals'),
            'runway_scenes_used': len(runway_paths),
            'resolution': rendered.get('resolution'),
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)
