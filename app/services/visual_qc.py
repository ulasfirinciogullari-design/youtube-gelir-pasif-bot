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
            'You are a demanding senior YouTube picture editor. For each scene, compare ALL supplied candidate frames and choose the best candidate. '
            'Judge exact semantic relevance to the spoken sentence first, then visual interest/retention, then professional image quality. '
            'Generic or metaphorically loose footage must score poorly. Examples of BAD mismatches: fireworks for camera burst, financial charts for audio codecs, city skyline for 5G optimization, random phone typing for encryption, unrelated towers for indoor GPS. '
            'A candidate should score 80+ only when a professional editor could confidently put it under that exact narration. '
            'If the BEST candidate for a scene is below 80, provide 2 concrete ENGLISH retry search queries that would visualize the sentence literally. '
            'Return ONLY JSON: {"reviews":[{"scene_index":0,"best_candidate_index":0,"score":0,"reason":"...","retry_queries":["...","..."]}]}'
        ),
    }]

    included = 0
    for idx, scene in enumerate(scenes):
        if included >= max_scenes:
            break
        paths = scene_visuals[idx] if idx < len(scene_visuals) else []
        paths = [p for p in paths if p][:3]
        if not paths:
            continue

        content.append({
            'type': 'input_text',
            'text': f'SCENE {idx}\nNarration: {str(scene.get("narration") or "").strip()}\nSearch queries: {json.dumps(scene.get("visual_queries") or [], ensure_ascii=False)}',
        })
        candidate_count = 0
        for candidate_idx, path in enumerate(paths):
            frame = _frame(path, frame_dir / f'scene_{idx:02d}_candidate_{candidate_idx:02d}.jpg')
            if not frame:
                continue
            encoded = base64.b64encode(frame.read_bytes()).decode('ascii')
            content.append({'type': 'input_text', 'text': f'CANDIDATE {candidate_idx}'})
            content.append({'type': 'input_image', 'image_url': f'data:image/jpeg;base64,{encoded}'})
            candidate_count += 1
        if candidate_count:
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
            best_candidate_index = int(review.get('best_candidate_index', 0))
            score = int(review.get('score'))
        except Exception:
            continue
        retry_queries = review.get('retry_queries') or []
        if isinstance(retry_queries, str):
            retry_queries = [retry_queries]
        reviews.append({
            'scene_index': scene_index,
            'best_candidate_index': max(0, best_candidate_index),
            'score': max(0, min(score, 100)),
            'reason': str(review.get('reason') or '')[:500],
            'retry_queries': [str(q).strip() for q in retry_queries if str(q).strip()][:2],
        })
    return {'reviews': reviews}
