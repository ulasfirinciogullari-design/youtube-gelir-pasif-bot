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


def _clean_scene(scene: dict, idx: int) -> dict:
    narration = str(scene.get('narration') or '').strip()
    tts_text = str(scene.get('tts_text') or narration).strip()
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
        'tts_text': tts_text,
        'visual_queries': queries,
        'ai_prompt': str(scene.get('ai_prompt') or '').strip() or None,
        'overlay_text': str(scene.get('overlay_text') or '').strip() or None,
        'pace': pace,
        'transition': transition,
    }


def direct_and_qc(package: dict, topic: str, duration_minutes: float, language: str) -> dict:
    if not settings.openai_api_key:
        return package

    scenes = package.get('scenes') or []
    if not scenes:
        return package

    client = OpenAI(api_key=settings.openai_api_key, timeout=75.0, max_retries=1)
    compact = {
        'title': package.get('title'),
        'thumbnail_text': package.get('thumbnail_text'),
        'description': package.get('description'),
        'scenes': scenes,
        'sources': package.get('sources', []),
    }
    language_name = 'Turkish' if language.lower().startswith('tr') else language

    response = client.responses.create(
        model=settings.openai_model,
        reasoning={'effort': 'low'},
        input=f'''You are the FINAL EDITORIAL DIRECTOR for a premium faceless YouTube video.
Topic: {topic}
Language: {language_name}
Target duration: about {duration_minutes} minutes.

Below is a researched storyboard draft. Do NOT add new factual claims. You may rewrite wording, bridges, pacing and visual directions only while preserving the facts and source-supported meaning.

DRAFT JSON:
{json.dumps(compact, ensure_ascii=False)}

Return ONLY valid JSON with exactly these keys:
title, thumbnail_text, description, scenes, qc_summary.

Each scene must contain exactly:
narration, tts_text, visual_queries, ai_prompt, overlay_text, pace, transition.

EDITORIAL QC RULES:
- The video must feel like one coherent story. Repair every abrupt subject jump.
- Every scene must either continue, explain, contrast, escalate, or pay off the previous scene.
- Delete filler and robotic listicle language. Keep spoken Turkish natural and confident.
- Punctuation must create human breathing and emphasis. Avoid long breathless sentences.
- tts_text must preserve the same meaning but optimize pronunciation. OLED must appear as "oled" in tts_text and must be spoken as one Turkish word, never O-L-E-D.
- Technical acronyms that Turkish speakers commonly pronounce as words should be written in their spoken form in tts_text.
- visual_queries must match the EXACT scene meaning and describe concrete visible footage. Reject generic "technology", random laptop, random phone typing, office worker, abstract future, or unrelated city B-roll unless the sentence truly calls for it.
- Give each scene 2-3 distinct English visual queries using different shot ideas: macro, close-up, demonstration, infrastructure, human interaction, moving camera, cutaway, or detail.
- ai_prompt should remain null unless stock footage genuinely cannot show the concept.
- overlay_text should be short and rare, only when it adds comprehension.
- pace must be fast, normal, or slow. Use fast for hooks/reveals, slow for an important explanation, normal otherwise. Do not make every scene the same pace.
- transition must be cut, match, or dip. Use mostly cut. Use match only when two scenes have a visual relationship. Use dip sparingly for a real topic shift.
- The final scene must provide a payoff or memorable closing thought, not an abrupt ending.
- qc_summary is a short list of the main fixes you made.
''',
    )

    revised = _json(response.output_text)
    revised_scenes = revised.get('scenes') or []
    cleaned = [_clean_scene(s, i) for i, s in enumerate(revised_scenes) if isinstance(s, dict)]
    cleaned = [s for s in cleaned if s['narration'] and s['visual_queries']]
    if len(cleaned) < max(4, len(scenes) // 2):
        return package

    out = dict(package)
    out['title'] = revised.get('title') or package.get('title')
    out['thumbnail_text'] = revised.get('thumbnail_text') or package.get('thumbnail_text')
    out['description'] = revised.get('description') or package.get('description')
    out['scenes'] = cleaned
    out['narration'] = ' '.join(s['narration'] for s in cleaned)
    out['tts_narration'] = ' '.join(s['tts_text'] for s in cleaned)
    out['visual_queries'] = [q for s in cleaned for q in s['visual_queries']]
    out['ai_scenes'] = [s['ai_prompt'] for s in cleaned if s.get('ai_prompt')]
    out['overlay_phrases'] = [s['overlay_text'] for s in cleaned if s.get('overlay_text')]
    out['director_qc'] = revised.get('qc_summary') or []
    return out
