import json
import re
from openai import OpenAI
from app.config import settings


def _parse_json_payload(text: str) -> dict:
    raw = (text or '').strip()
    if raw.startswith('```'):
        raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.IGNORECASE)
        raw = re.sub(r'\s*```$', '', raw)
    try:
        data = json.loads(raw)
    except Exception as exc:
        preview = raw[:800].replace('\n', ' ')
        raise RuntimeError(f'OpenAI returned invalid JSON: {preview}') from exc
    if not isinstance(data, dict):
        raise RuntimeError('OpenAI response JSON is not an object')

    scenes = data.get('scenes') or []
    if not isinstance(scenes, list) or len(scenes) < 3:
        raise RuntimeError('OpenAI response is missing a usable scene plan')

    clean_scenes = []
    for idx, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            continue
        narration = str(scene.get('narration') or '').strip()
        if not narration:
            continue
        queries = scene.get('visual_queries') or []
        if isinstance(queries, str):
            queries = [queries]
        queries = [str(q).strip() for q in queries if str(q).strip()][:3]
        clean_scenes.append({
            'index': idx,
            'narration': narration,
            'tts_text': narration,
            'visual_queries': queries,
            'ai_prompt': str(scene.get('ai_prompt') or '').strip() or None,
            'overlay_text': None,
        })

    if len(clean_scenes) < 3:
        raise RuntimeError('OpenAI scene plan has too few valid scenes')

    data['scenes'] = clean_scenes
    data['narration'] = ' '.join(s['narration'] for s in clean_scenes)
    data['tts_narration'] = data['narration']
    data['visual_queries'] = [q for s in clean_scenes for q in s['visual_queries']]
    data['ai_scenes'] = [s['ai_prompt'] for s in clean_scenes if s.get('ai_prompt')]
    data['overlay_phrases'] = []
    return data


def _target_scene_count(duration_minutes: float) -> int:
    if duration_minutes <= 0.6:
        return 4
    if duration_minutes <= 1.1:
        return 6
    if duration_minutes <= 3.1:
        return max(8, int(round(duration_minutes * 4.5)))
    return min(28, max(12, int(round(duration_minutes * 3.5))))


def _target_word_budget(duration_minutes: float) -> tuple[int, int, int]:
    """Calibrated for the selected Turkish ElevenLabs voice, including pauses."""
    if duration_minutes <= 0.6:
        target = 40
    elif duration_minutes <= 1.1:
        target = 82
    elif duration_minutes <= 3.1:
        target = int(round(duration_minutes * 92))
    else:
        target = int(round(duration_minutes * 100))
    minimum = max(30, int(round(target * 0.88)))
    maximum = max(minimum + 4, int(round(target * 1.06)))
    return target, minimum, maximum


def research_and_script(topic: str, duration_minutes: float, language: str) -> dict:
    if not settings.openai_api_key:
        raise RuntimeError('OPENAI_API_KEY is not configured')

    client = OpenAI(api_key=settings.openai_api_key, timeout=90.0, max_retries=1)
    target_words, min_words, max_words = _target_word_budget(duration_minutes)
    target_scenes = _target_scene_count(duration_minutes)
    max_ai_scenes = 0 if duration_minutes <= 0.5 else (1 if duration_minutes <= 1.5 else 2)
    language_name = 'Turkish' if language.lower().startswith('tr') else language

    response = client.responses.create(
        model=settings.openai_model,
        reasoning={'effort': 'low'},
        tools=[{'type': 'web_search', 'search_context_size': 'low'}],
        tool_choice='auto',
        input=f'''Research the current web and act as a senior YouTube writer/director.
Language: {language_name}
Topic: {topic}
HARD NARRATION BUDGET: {min_words}-{max_words} total spoken words; aim for {target_words}. Never exceed {max_words}.
Create EXACTLY {target_scenes} scenes.
Maximum premium AI-video scenes: {max_ai_scenes}.

Return ONLY valid JSON with exactly these top-level keys:
title, thumbnail_text, description, scenes, sources.

Each scene must contain exactly:
narration, visual_queries, ai_prompt.

STORY RULES:
- Write ONE coherent story, not a pile of unrelated facts.
- Every scene must clearly continue, explain, contrast or pay off the previous one.
- The first scene is an immediate hook. No greeting or filler.
- The final scene is a payoff, not a sudden stop.
- Natural spoken Turkish: concise sentences, deliberate punctuation, varied rhythm.
- No robotic listicle language or generic AI phrasing.
- The entire narration across all scenes MUST remain inside {min_words}-{max_words} words.
- Each scene should carry one complete thought that can stay on one strong hero visual.

DIRECTING RULES:
- Give each scene 2-3 DISTINCT English stock-video search phrases matching the exact spoken idea.
- Queries must describe concrete visible subjects/actions.
- Never use generic laptop typing, office worker, skyline, random phone or abstract tech footage unless the sentence literally calls for it.
- For concepts stock footage cannot honestly visualize, set ai_prompt to a precise visualization prompt.
- ai_prompt must be null in most scenes and non-null in at most {max_ai_scenes} scenes.
- Do not plan subtitles, lower thirds or on-screen sentences. The master video contains no text.

FACT RULES:
- Use web research when useful.
- Do not invent facts or statistics.
- Do not copy source wording.
- sources must contain URLs.
''',
    )
    package = _parse_json_payload(response.output_text)
    if abs(len(package['scenes']) - target_scenes) > 1:
        raise RuntimeError(f'Scene-count gate rejected storyboard: {len(package["scenes"])} scenes; target {target_scenes}')
    package['target_scene_count'] = target_scenes
    package['target_word_range'] = [min_words, max_words]
    return package
