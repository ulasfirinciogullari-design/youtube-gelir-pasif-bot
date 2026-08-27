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
    if not str(data.get('narration') or '').strip():
        raise RuntimeError('OpenAI response is missing narration')
    if not str(data.get('tts_narration') or '').strip():
        data['tts_narration'] = data['narration']
    return data


def research_and_script(topic: str, duration_minutes: float, language: str) -> dict:
    if not settings.openai_api_key:
        raise RuntimeError('OPENAI_API_KEY is not configured')

    client = OpenAI(api_key=settings.openai_api_key, timeout=90.0, max_retries=1)
    target_words = max(95, int(duration_minutes * 155))
    target_shots = min(55, max(18, int(duration_minutes * 18)))
    ai_count = 2 if duration_minutes <= 1.5 else 3

    language_name = 'Turkish' if language.lower().startswith('tr') else language
    response = client.responses.create(
        model=settings.openai_model,
        reasoning={'effort': 'low'},
        tools=[{'type': 'web_search', 'search_context_size': 'low'}],
        tool_choice='auto',
        input=f'''Research the current web and build a PREMIUM faceless YouTube package.
Language: {language_name}
Topic: {topic}
Narration target: about {target_words} words.
Target visual cuts: about {target_shots} shots.

Return ONLY valid JSON with exactly these keys:
title, thumbnail_text, hook, narration, tts_narration, description, visual_queries, ai_scenes, overlay_phrases, sources.

SCRIPT RULES:
- Write like an excellent human Turkish YouTube narrator, not like an article or AI list.
- Hook immediately. No greeting, no "bugün size", no filler introduction.
- Use short spoken sentences, mostly 6-14 words. Vary sentence length for rhythm.
- Add mini-surprises, contrast and curiosity every 10-15 seconds.
- Avoid repetitive numbered-list cadence. Connect facts naturally.
- narration is the clean transcript viewers would read.
- tts_narration MUST contain the same factual content, but be optimized for Turkish pronunciation.
- In tts_narration, expand or rewrite abbreviations, symbols, dates and numbers when that improves pronunciation.
- Avoid awkward English/Turkish code-switching when a natural Turkish wording exists.
- Use punctuation deliberately so a narrator knows where to pause and emphasize.

VISUAL RULES:
- visual_queries must contain about {target_shots} DISTINCT English Pexels search phrases.
- Every query must describe a concrete visible subject/action, not abstract words like "technology" or "future".
- Change visual subject frequently. Do not repeat the same phone/laptop shot over and over.
- Prefer macro shots, human interaction, unusual angles, infrastructure, close-ups, moving cameras and real-world demonstrations.
- ai_scenes must contain exactly {ai_count} cinematic text-to-video prompts for concepts stock footage cannot show well.
- Each AI prompt: 16:9, realistic/cinematic, strong camera motion, no visible text/logos, one clear visual idea.
- overlay_phrases: 4-8 very short punchy Turkish phrases (2-5 words), not full sentences.

FACT RULES:
- Use current web research when useful.
- Do not invent facts or statistics.
- Do not copy source wording.
- sources must contain source URLs.
''',
    )
    return _parse_json_payload(response.output_text)
