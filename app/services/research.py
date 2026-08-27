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
    if not isinstance(scenes, list) or len(scenes) < 4:
        raise RuntimeError('OpenAI response is missing a usable scene plan')

    clean_scenes = []
    for idx, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            continue
        narration = str(scene.get('narration') or '').strip()
        if not narration:
            continue
        tts_text = str(scene.get('tts_text') or narration).strip()
        queries = scene.get('visual_queries') or []
        if isinstance(queries, str):
            queries = [queries]
        queries = [str(q).strip() for q in queries if str(q).strip()][:3]
        clean_scenes.append({
            'index': idx,
            'narration': narration,
            'tts_text': tts_text,
            'visual_queries': queries,
            'ai_prompt': str(scene.get('ai_prompt') or '').strip() or None,
            'overlay_text': str(scene.get('overlay_text') or '').strip() or None,
        })

    if len(clean_scenes) < 4:
        raise RuntimeError('OpenAI scene plan has too few valid scenes')

    data['scenes'] = clean_scenes
    data['narration'] = ' '.join(s['narration'] for s in clean_scenes)
    data['tts_narration'] = ' '.join(s['tts_text'] for s in clean_scenes)
    data['visual_queries'] = [q for s in clean_scenes for q in s['visual_queries']]
    data['ai_scenes'] = [s['ai_prompt'] for s in clean_scenes if s.get('ai_prompt')]
    data['overlay_phrases'] = [s['overlay_text'] for s in clean_scenes if s.get('overlay_text')]
    return data


def research_and_script(topic: str, duration_minutes: float, language: str) -> dict:
    if not settings.openai_api_key:
        raise RuntimeError('OPENAI_API_KEY is not configured')

    client = OpenAI(api_key=settings.openai_api_key, timeout=90.0, max_retries=1)
    target_words = max(95, int(duration_minutes * 150))
    target_scenes = min(32, max(8, int(round(duration_minutes * 8))))
    max_ai_scenes = 1 if duration_minutes <= 1.5 else 2
    language_name = 'Turkish' if language.lower().startswith('tr') else language

    response = client.responses.create(
        model=settings.openai_model,
        reasoning={'effort': 'low'},
        tools=[{'type': 'web_search', 'search_context_size': 'low'}],
        tool_choice='auto',
        input=f'''Research the current web and act as BOTH a senior YouTube writer and a video director.
Language: {language_name}
Topic: {topic}
Narration target: about {target_words} words.
Target scene count: about {target_scenes} scenes.
Maximum premium AI-video scenes: {max_ai_scenes}.

Return ONLY valid JSON with exactly these top-level keys:
title, thumbnail_text, description, scenes, sources.

Each item in scenes MUST contain exactly:
narration, tts_text, visual_queries, ai_prompt, overlay_text.

STORY / CONTINUITY RULES:
- The whole video must feel like ONE coherent story, not unrelated facts pasted together.
- Every scene must naturally follow the previous scene. Use cause/effect, contrast, escalation or a clear bridge.
- Never jump to a new subject without explaining why it follows from the previous point.
- The first scene is the hook. The final scene must feel like a payoff, not an abrupt stop.
- Avoid generic AI phrasing, listicle filler and repeated "peki" / "ama işin ilginç yanı" patterns.
- Spoken Turkish should sound like a confident human narrator.
- Use punctuation deliberately: commas for short breaths, full stops for clear pauses, occasional dashes for emphasis.
- Prefer short and medium sentences. Do not make every sentence the same length.

TTS / PRONUNCIATION RULES:
- tts_text contains the SAME meaning as narration but is optimized purely for Turkish speech.
- Write abbreviations the way a Turkish narrator should pronounce them when necessary.
- IMPORTANT: OLED must be spoken as the Turkish word "oled", NOT letter-by-letter.
- If a technical term is commonly pronounced as a word in Turkish, write its spoken form in tts_text.
- Add punctuation where a human speaker would actually breathe. Do not make the voice rush through transitions.

DIRECTING RULES:
- Each scene must have 2 or 3 DISTINCT English visual_queries that visually match THAT scene's exact spoken content.
- visual_queries must describe concrete visible subjects/actions, not abstract ideas.
- Prefer close-ups, macro details, demonstrations, human interaction, infrastructure, moving cameras and unusual angles.
- Do not use generic phone/laptop B-roll unless the sentence is actually about that device.
- ai_prompt is null for most scenes. Use it only where stock footage cannot communicate the idea well.
- Across the entire video, ai_prompt may be non-null in at most {max_ai_scenes} scenes.
- overlay_text is null unless a very short 2-5 word on-screen phrase genuinely strengthens that exact scene.

FACT RULES:
- Use web research when useful.
- Do not invent facts or statistics.
- Do not copy source wording.
- sources must contain URLs.
''',
    )
    return _parse_json_payload(response.output_text)
