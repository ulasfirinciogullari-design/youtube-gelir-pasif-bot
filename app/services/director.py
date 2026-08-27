import json
import re
from openai import OpenAI
from app.config import settings


def _json(text: str) -> dict:
    raw = (text or '').strip()
    if raw.startswith('```'):
        raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.IGNORECASE)
        raw = re.sub(r'\s*```$', '', raw)
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise RuntimeError('Director response is not a JSON object')
    return data


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\wÇĞİÖŞÜçğıöşü'-]+\b", text or '', flags=re.UNICODE))


def _clean_scene(scene: dict, idx: int) -> dict:
    narration = str(scene.get('narration') or '').strip()
    queries = scene.get('visual_queries') or []
    if isinstance(queries, str):
        queries = [queries]
    queries = [str(q).strip() for q in queries if str(q).strip()][:3]
    pace = str(scene.get('pace') or 'normal').strip().lower()
    if pace not in {'fast', 'normal', 'slow'}:
        pace = 'normal'
    transition = str(scene.get('transition') or 'cut').strip().lower()
    if transition not in {'cut', 'match', 'dip'}:
        transition = 'cut'
    return {
        'index': idx,
        'narration': narration,
        # One textual source of truth. Phonetic substitutions happen later in voice.py.
        'tts_text': narration,
        'visual_queries': queries,
        'ai_prompt': str(scene.get('ai_prompt') or '').strip() or None,
        # Continuous subtitles are the only text layer for now.
        'overlay_text': None,
        'pace': pace,
        'transition': transition,
    }


def _clean_package(revised: dict, original: dict) -> dict:
    scenes = revised.get('scenes') or []
    cleaned = [_clean_scene(s, i) for i, s in enumerate(scenes) if isinstance(s, dict)]
    cleaned = [s for s in cleaned if s['narration'] and s['visual_queries']]
    if len(cleaned) < 3:
        raise RuntimeError('Director produced too few usable scenes')
    out = dict(original)
    out['title'] = revised.get('title') or original.get('title')
    out['thumbnail_text'] = revised.get('thumbnail_text') or original.get('thumbnail_text')
    out['description'] = revised.get('description') or original.get('description')
    out['scenes'] = cleaned
    out['narration'] = ' '.join(s['narration'] for s in cleaned)
    out['tts_narration'] = out['narration']
    out['visual_queries'] = [q for s in cleaned for q in s['visual_queries']]
    out['ai_scenes'] = [s['ai_prompt'] for s in cleaned if s.get('ai_prompt')]
    out['overlay_phrases'] = []
    out['director_qc'] = revised.get('qc_summary') or []
    return out


def _run_director(client: OpenAI, compact: dict, topic: str, language_name: str, duration_minutes: float, min_words: int, max_words: int, correction: bool = False) -> dict:
    correction_note = (
        f'CRITICAL CORRECTION: the previous version violated the duration budget. Rewrite the SAME factual story to {min_words}-{max_words} TOTAL spoken words. Do not add facts. '
        if correction else ''
    )
    response = client.responses.create(
        model=settings.openai_model,
        reasoning={'effort': 'low'},
        input=f'''You are the FINAL EDITORIAL DIRECTOR for a premium faceless YouTube video.
Topic: {topic}
Language: {language_name}
Requested duration: {duration_minutes} minutes.
HARD spoken-word budget: {min_words}-{max_words} total words across every scene.
{correction_note}

DRAFT JSON:
{json.dumps(compact, ensure_ascii=False)}

Return ONLY valid JSON with exactly these keys:
title, thumbnail_text, description, scenes, qc_summary.

Each scene must contain exactly:
narration, visual_queries, ai_prompt, pace, transition.

EDITORIAL QC RULES:
- One coherent story; repair abrupt subject jumps.
- Every scene continues, explains, contrasts, escalates or pays off the previous scene.
- Delete filler and robotic listicle wording.
- Spoken Turkish must be natural, concise and punctuated for real breaths.
- Do not create separate on-screen copy; subtitles will come directly from narration.
- visual_queries must match the EXACT spoken meaning and describe concrete visible footage.
- Reject generic laptop typing, random phone, office worker, skyline, fireworks, charts or abstract tech footage unless literally relevant.
- Use 2-3 distinct visual queries per scene from different shot ideas.
- ai_prompt should be null unless stock footage cannot honestly show the concept.
- pace: fast / normal / slow; vary it intentionally.
- transition: mostly cut; match only with a real visual relationship; dip sparingly.
- Final scene must provide a payoff.
- TOTAL narration word count MUST be between {min_words} and {max_words}.
- qc_summary is a short list of fixes.
''',
    )
    return _json(response.output_text)


def direct_and_qc(package: dict, topic: str, duration_minutes: float, language: str) -> dict:
    if not settings.openai_api_key:
        return package
    scenes = package.get('scenes') or []
    if not scenes:
        return package

    client = OpenAI(api_key=settings.openai_api_key, timeout=75.0, max_retries=1)
    target_words = max(55, int(round(duration_minutes * 120)))
    min_words = max(45, int(round(target_words * 0.88)))
    max_words = max(min_words + 5, int(round(target_words * 1.08)))
    language_name = 'Turkish' if language.lower().startswith('tr') else language

    compact = {
        'title': package.get('title'),
        'thumbnail_text': package.get('thumbnail_text'),
        'description': package.get('description'),
        'scenes': scenes,
        'sources': package.get('sources', []),
    }

    revised = _run_director(client, compact, topic, language_name, duration_minutes, min_words, max_words)
    out = _clean_package(revised, package)
    words = _word_count(out['narration'])

    # One bounded correction pass only when duration is materially wrong.
    if words < min_words or words > max_words:
        correction_input = {
            'title': out.get('title'),
            'thumbnail_text': out.get('thumbnail_text'),
            'description': out.get('description'),
            'scenes': out.get('scenes'),
            'sources': package.get('sources', []),
            'current_word_count': words,
        }
        revised = _run_director(client, correction_input, topic, language_name, duration_minutes, min_words, max_words, correction=True)
        out = _clean_package(revised, package)
        words = _word_count(out['narration'])

    if words < int(min_words * 0.90) or words > int(max_words * 1.05):
        raise RuntimeError(f'Duration gate rejected script: {words} words for requested {duration_minutes} min (target {min_words}-{max_words})')

    out['narration_word_count'] = words
    out['target_word_range'] = [min_words, max_words]
    return out
