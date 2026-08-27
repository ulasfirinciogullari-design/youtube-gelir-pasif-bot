from pathlib import Path
import base64
import json
import re
import subprocess
from openai import OpenAI
from app.config import settings


def _parse(text: str) -> dict:
    raw = (text or '').strip()
    if raw.startswith('```'):
        raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.IGNORECASE)
        raw = re.sub(r'\s*```$', '', raw)
    data = json.loads(raw)
    return data if isinstance(data, dict) else {'reviews': []}


def _frame(video_path: str, output_path: Path) -> Path | None:
    try:
        subprocess.run([
            'ffmpeg', '-y', '-ss', '0.8', '-i', video_path,
            '-frames:v', '1', '-vf', 'scale=640:-2', '-q:v', '5', str(output_path),
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return output_path if output_path.exists() and output_path.stat().st_size else None
    except Exception:
        return None


def review_scene_visuals(scenes: list[dict], scene_visuals: list[list[str]], work_dir: str | Path, max_scenes: int = 12) -> dict:
    if not settings.openai_api_key:
        return {'reviews': []}

    work = Path(work_dir)
    frame_dir = work / 'visual_qc'
    frame_dir.mkdir(parents=True, exist_ok=True)
    content: list[dict] = [{
        'type': 'input_text',
        'text': (
            'You are a senior YouTube picture editor. Review each supplied representative video frame against its scene narration. '
            'Score 0-100 using three criteria: exact semantic relevance to the spoken sentence, visual interest/retention value, and professional visual quality. '
            'A generic laptop, random phone typing, office worker, skyline or abstract tech shot should score poorly unless it exactly matches the narration. '
            'If score is below 72, give 2 better concrete ENGLISH stock-video search queries. Do not invent new facts. '
            'Return ONLY JSON: {"reviews":[{"scene_index":0,"score":0,"reason":"...","retry_queries":["...","..."]}]}'
        ),
    }]

    included = 0
    for idx, scene in enumerate(scenes):
        if included >= max_scenes:
            break
        paths = scene_visuals[idx] if idx < len(scene_visuals) else []
        if not paths:
            continue
        frame = _frame(paths[0], frame_dir / f'scene_{idx:02d}.jpg')
        if not frame:
            continue
        encoded = base64.b64encode(frame.read_bytes()).decode('ascii')
        content.append({
            'type': 'input_text',
            'text': f'SCENE {idx}\nNarration: {str(scene.get("narration") or "").strip()}\nCurrent search queries: {json.dumps(scene.get("visual_queries") or [], ensure_ascii=False)}',
        })
        content.append({
            'type': 'input_image',
            'image_url': f'data:image/jpeg;base64,{encoded}',
        })
        included += 1

    if not included:
        return {'reviews': []}

    client = OpenAI(api_key=settings.openai_api_key, timeout=90.0, max_retries=1)
    response = client.responses.create(
        model=settings.openai_model,
        reasoning={'effort': 'low'},
        input=[{'role': 'user', 'content': content}],
    )
    data = _parse(response.output_text)
    reviews = []
    for review in data.get('reviews') or []:
        if not isinstance(review, dict):
            continue
        try:
            scene_index = int(review.get('scene_index'))
            score = int(review.get('score'))
        except Exception:
            continue
        retry_queries = review.get('retry_queries') or []
        if isinstance(retry_queries, str):
            retry_queries = [retry_queries]
        reviews.append({
            'scene_index': scene_index,
            'score': max(0, min(score, 100)),
            'reason': str(review.get('reason') or '')[:500],
            'retry_queries': [str(q).strip() for q in retry_queries if str(q).strip()][:2],
        })
    return {'reviews': reviews}
