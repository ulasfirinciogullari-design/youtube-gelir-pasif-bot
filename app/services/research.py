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
    return data


def research_and_script(topic: str, duration_minutes: float, language: str) -> dict:
    if not settings.openai_api_key:
        raise RuntimeError('OPENAI_API_KEY is not configured')

    # Keep the synchronous worker request bounded so a bad upstream call does not
    # leave a Celery task stuck at the research stage indefinitely.
    client = OpenAI(
        api_key=settings.openai_api_key,
        timeout=75.0,
        max_retries=1,
    )
    target_words = max(90, int(duration_minutes * 145))
    response = client.responses.create(
        model=settings.openai_model,
        reasoning={'effort': 'low'},
        tools=[{
            'type': 'web_search',
            'search_context_size': 'low',
        }],
        tool_choice='auto',
        input=f'''Research the current web and create an original, high-retention faceless YouTube package.
Language: {language}
Topic: {topic}
Narration target: about {target_words} words.

Return ONLY valid JSON with these keys:
title, thumbnail_text, hook, narration, description, visual_queries, ai_scenes, sources.

Rules:
- Use current web research when useful; do not over-research simple facts.
- Do not invent facts.
- Do not copy source wording.
- Make the first 15 seconds unusually strong.
- visual_queries must be English B-roll search phrases.
- Return 5-8 concise visual_queries for a 1 minute test.
- ai_scenes should contain at most 1 short premium scene prompt for a 1 minute test.
- sources must contain source URLs.
''',
    )
    return _parse_json_payload(response.output_text)
