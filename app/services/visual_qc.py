from functools import lru_cache
from pathlib import Path
import base64
import json
import re
import subprocess
from openai import OpenAI
from app.config import settings


MOMENT_FRACTIONS = [0.18, 0.50, 0.82]


def _parse(text: str) -> dict:
    raw = (text or '').strip()
    if raw.startswith('```'):
        raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.IGNORECASE)
        raw = re.sub(r'\s*```$', '', raw)
    data = json.loads(raw)
    return data if isinstance(data, dict) else {'reviews': []}


@lru_cache(maxsize=256)
def _duration(video_path: str) -> float:
    out = subprocess.check_output([
        'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1', video_path,
    ], text=True).strip()
    return max(0.1, float(out))


def _frame(video_path: str, output_path: Path, fraction: float) -> Path | None:
    try:
        seconds = max(0.0, _duration(video_path) * fraction)
        subprocess.run([
            'ffmpeg', '-y', '-ss', f'{seconds:.3f}', '-i', video_path,
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
            'You are a demanding senior YouTube picture editor. For each scene, compare ALL supplied candidate clips AND multiple moments inside each clip. '
            'Choose the exact candidate and exact moment a professional editor should use. Judge literal semantic relevance first, then visual interest, composition, motion and production quality. '
            'Generic, metaphorically loose or keyword-only footage must score poorly. The named subject and the spoken action must both be visible. '
            'Never approve digital glitch/noise for OLED pixels, programming tracebacks for QR error correction, fireworks for camera burst, finance charts for audio codecs, a skyline for network optimization, random typing for encryption, or unrelated towers for indoor GPS. '
            'If the sampled moments are nearly identical, the clip is effectively static; any shot likely to remain static for more than six seconds must score 40 or lower. '
            'A score of 86+ means the chosen moment is genuinely publishable under that exact narration. If the best available moment is below 86, provide two concrete ENGLISH retry queries that keep the named subject attached to the visible action. '
            'Return ONLY JSON: {"reviews":[{"scene_index":0,"best_candidate_index":0,"best_moment_index":0,"score":0,"reason":"...","retry_queries":["...","..."]}]}'
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
        image_count = 0
        for candidate_idx, path in enumerate(paths):
            for moment_idx, fraction in enumerate(MOMENT_FRACTIONS):
                frame = _frame(
                    path,
                    frame_dir / f'scene_{idx:02d}_candidate_{candidate_idx:02d}_moment_{moment_idx:02d}.jpg',
                    fraction,
                )
                if not frame:
                    continue
                encoded = base64.b64encode(frame.read_bytes()).decode('ascii')
                content.append({
                    'type': 'input_text',
                    'text': f'CANDIDATE {candidate_idx} — MOMENT {moment_idx} — approximately {int(fraction * 100)}% into clip',
                })
                content.append({'type': 'input_image', 'image_url': f'data:image/jpeg;base64,{encoded}'})
                image_count += 1
        if image_count:
            included += 1

    if not included:
        return {'reviews': []}

    client = OpenAI(api_key=settings.openai_api_key, timeout=120.0, max_retries=1)
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
            best_moment_index = int(review.get('best_moment_index', 0))
            score = int(review.get('score'))
        except Exception:
            continue
        retry_queries = review.get('retry_queries') or []
        if isinstance(retry_queries, str):
            retry_queries = [retry_queries]
        best_moment_index = min(max(best_moment_index, 0), len(MOMENT_FRACTIONS) - 1)
        reviews.append({
            'scene_index': scene_index,
            'best_candidate_index': max(0, best_candidate_index),
            'best_moment_index': best_moment_index,
            'best_start_fraction': MOMENT_FRACTIONS[best_moment_index],
            'score': max(0, min(score, 100)),
            'reason': str(review.get('reason') or '')[:500],
            'retry_queries': [str(q).strip() for q in retry_queries if str(q).strip()][:2],
        })
    return {'reviews': reviews, 'moment_fractions': MOMENT_FRACTIONS}
