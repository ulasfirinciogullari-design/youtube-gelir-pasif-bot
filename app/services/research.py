import json
from openai import OpenAI
from app.config import settings


def research_and_script(topic: str, duration_minutes: float, language: str) -> dict:
    if not settings.openai_api_key:
        raise RuntimeError('OPENAI_API_KEY is not configured')

    client = OpenAI(api_key=settings.openai_api_key)
    target_words = int(duration_minutes * 145)
    response = client.responses.create(
        model=settings.openai_model,
        tools=[{'type': 'web_search'}],
        input=f'''Research the current web and create an original, high-retention faceless YouTube package.
Language: {language}
Topic: {topic}
Narration target: about {target_words} words.

Return ONLY valid JSON with these keys:
title, thumbnail_text, hook, narration, description, visual_queries, ai_scenes, sources.

Rules:
- Use current web research before writing.
- Do not invent facts.
- Do not copy source wording.
- Make the first 15 seconds unusually strong.
- visual_queries must be English B-roll search phrases.
- sources must contain source URLs.
''',
    )
    return json.loads(response.output_text)
