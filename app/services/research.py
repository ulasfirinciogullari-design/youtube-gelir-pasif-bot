import json
import math
import re
from openai import OpenAI
from app.config import settings

STYLE_DIRECTIONS = {
    'documentary': 'Premium documentary: authoritative but accessible, concrete details, restrained cinematic tension.',
    'technology': 'Modern technology documentary: precise, surprising, visual and human; explain mechanisms without jargon dumping.',
    'story': 'Narrative storytelling: character, problem, escalation and payoff; facts must advance the story.',
    'cinematic': 'Cinematic essay: evocative hook, controlled reveals, strong visual motifs and a memorable payoff.',
    'explainer': 'High-end explainer: crystal-clear causal logic, demonstrations and comparisons, no filler.',
}
PACE_DIRECTIONS = {
    'calm': 'Use fewer, longer scenes and give important ideas room to breathe. Do not rush.',
    'balanced': 'Alternate concise reveals with clear explanations. Rhythm should feel edited, not frantic.',
    'dynamic': 'Front-load momentum and use sharper scene turns, while preserving comprehension and continuity.',
}


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


def _target_scene_count(duration_minutes: float, pace: str) -> int:
    if duration_minutes <= 0.6:
        base = max(5, int(round(duration_minutes * 12)))
    elif duration_minutes <= 1.1:
        base = 6
    elif duration_minutes <= 3.1:
        base = max(8, int(round(duration_minutes * 4.5)))
    else:
        base = min(28, max(12, int(round(duration_minutes * 3.5))))
    if pace == 'calm':
        return max(3, int(round(base * 0.82)))
    if pace == 'dynamic':
        return min(32, max(3, int(round(base * 1.12))))
    return base


def _target_word_budget(duration_minutes: float) -> tuple[int, int, int]:
    if duration_minutes <= 0.6:
        target = max(44, int(round(duration_minutes * 96)))
    elif duration_minutes <= 1.1:
        target = 82
    elif duration_minutes <= 3.1:
        target = int(round(duration_minutes * 92))
    else:
        target = int(round(duration_minutes * 100))
    minimum = max(30, int(round(target * (0.94 if duration_minutes <= 0.6 else 0.88))))
    maximum = max(minimum + 4, int(round(target * 1.06)))
    return target, minimum, maximum


def _max_ai_scenes(scene_count: int, options: dict, duration_minutes: float) -> int:
    if options.get('mode') == 'preview':
        return scene_count if duration_minutes <= 0.6 else 0
    mix = options.get('visual_mix') or 'balanced'
    if mix == 'real_first':
        return min(2, max(1, math.ceil(scene_count * 0.10)))
    if mix == 'ai_first':
        return min(6, max(2, math.ceil(scene_count * 0.40)))
    return min(4, max(1, math.ceil(scene_count * 0.24)))


def research_and_script(topic: str, duration_minutes: float, language: str, options: dict | None = None) -> dict:
    if not settings.openai_api_key:
        raise RuntimeError('OPENAI_API_KEY is not configured')

    options = dict(options or {})
    style = str(options.get('content_style') or 'documentary')
    pace = str(options.get('pace') or 'balanced')
    mode = str(options.get('mode') or ('preview' if duration_minutes <= 1 else 'production'))
    visual_mix = str(options.get('visual_mix') or 'balanced')
    reference_url = str(options.get('reference_url') or '').strip()

    client = OpenAI(api_key=settings.openai_api_key, timeout=105.0, max_retries=1)
    target_words, min_words, max_words = _target_word_budget(duration_minutes)
    target_scenes = _target_scene_count(duration_minutes, pace)
    max_ai_scenes = _max_ai_scenes(target_scenes, options, duration_minutes)
    language_name = 'Turkish' if language.lower().startswith('tr') else language
    reference_note = (
        f'Reference URL: {reference_url}\nAnalyze only its structural rhythm, hook pattern and information architecture. Do not copy wording, branding, signature devices or protected creative expression.'
        if reference_url else 'No reference video was supplied.'
    )
    if mode == 'preview' and duration_minutes <= 0.6:
        preview_ai_routing_note = (
            '- SHORT-PREVIEW STORY CONTRACT: first narrow the broad topic to ONE everyday human situation, ONE central curiosity or problem, '
            'ONE recurring person or object, ONE technical reveal and ONE visible everyday benefit. Do not make a sampler, listicle or montage of unrelated facts.\n'
            '- Treat technical mechanisms mentioned elsewhere in this prompt only as conditional visual-validation examples, never as an idea menu or checklist. '
            'At most one mechanism family may drive this 30-second story.\n'
            '- Contain the chosen hard mechanism and its complete causal explanation inside ONE corresponding AI scene. '
            'Do not split, repeat or conclude that invisible mechanism in adjacent stock scenes.\n'
            '- Every scene with ai_prompt set to null must narrate only one literal, realistically filmable subject and action that one ordinary stock clip can visibly show. '
            'It must never recap, compare or recombine several mechanisms or abstract claims.\n'
            '- Make the penultimate action and closing payoff two consecutive visible beats by the same person or object, seconds apart in the SAME named micro-location '
            '(the same counter, table, desk, doorway or room). Repeat that location anchor in both scenes and both query sets; no exit, travel, new room or later-time jump.\n'
            '- Before returning, audit each ai_prompt-null scene against its English stock queries. All named subjects, actions and context must realistically coexist '
            'in one commonly available stock clip; otherwise rewrite that scene and its queries.\n'
            '- Treat ai_prompt as free fallback metadata, not a promise to generate. Give a precise fallback ai_prompt to any scene whose exact stock coverage is uncertain; '
            'the worker will rank current stock quality and submit at most three paid generations. Fewer AI scenes are preferable when literal stock proves the story.\n'
            '- All stock-filmable scenes must provide 2-3 concrete, realistically filmable English stock queries even when they also carry a fallback ai_prompt.'
        )
    elif mode == 'preview':
        preview_ai_routing_note = (
            '- For this preview duration every ai_prompt value MUST be null. '
            'Make every scene literally stock-filmable and preserve this rule during revisions.'
        )
    else:
        preview_ai_routing_note = ''

    response = client.responses.create(
        model=settings.openai_model,
        reasoning={
            'effort': (
                'medium'
                if mode == 'preview' and duration_minutes <= 0.6
                else 'low'
            )
        },
        tools=[{'type': 'web_search', 'search_context_size': 'low'}],
        tool_choice='auto',
        input=f'''Research the current web and act as a senior YouTube writer and storyboard director.
Language: {language_name}
Topic: {topic}
Production mode: {mode}
Editorial style: {STYLE_DIRECTIONS.get(style, STYLE_DIRECTIONS['documentary'])}
Pacing: {PACE_DIRECTIONS.get(pace, PACE_DIRECTIONS['balanced'])}
Visual mix: {visual_mix}
{reference_note}

HARD NARRATION BUDGET: {min_words}-{max_words} total spoken words; aim for {target_words}. Never exceed {max_words}.
Create EXACTLY {target_scenes} scenes.
Maximum scenes that may carry an AI fallback prompt: {max_ai_scenes}. Paid generation is selected later from measured stock quality.

Return ONLY valid JSON with exactly these top-level keys:
title, thumbnail_text, description, scenes, sources.

Each scene must contain exactly:
narration, visual_queries, ai_prompt.

STORY RULES:
- Write ONE coherent story, not a pile of facts or a numbered list.
- For a short preview, silently define one sentence that states: a person or familiar object wants something, meets one obstacle, learns one cause, and receives one visible benefit. Every scene must serve that sentence.
- A broad topic is not an angle. Narrow it to the strongest useful or surprising human question; discard unrelated research facts even when they are individually interesting.
- Every scene must continue, explain, contrast, escalate or pay off the previous scene.
- Hook immediately. No greeting, channel intro or filler.
- The final scene must resolve the central curiosity through a visible human action and leave a memorable payoff, not merely a closing thought.
- Spoken {language_name} must sound like an excellent human narrator: concise, deliberate punctuation, varied sentence length and natural bridges.
- For Turkish short previews, standalone OLED, GPS and QR are allowed only with a natural Turkish noun because the voice layer normalizes them. Never attach Turkish suffixes directly to abbreviations, and never speak raw Wi-Fi or Reed-Solomon. Prefer OLED ekran, GPS sinyali, QR kodu, kablosuz ağ or hata düzeltme yöntemi. Technical English remains allowed in visual_queries and ai_prompt.
- For Turkish, reject translated noun stacks, inverted word order and phrases like “siyah yerde”, “hücresel zamanlama tamamlar konumu” or “okunur yine kolayca”.
- The complete narration must remain inside {min_words}-{max_words} words.
- Each scene carries one complete idea that can live under one strong hero visual.
- A stock-only scene may not summarize several earlier mechanisms or invisible abstractions; it must describe one subject performing one visible action in one ordinary location.

VISUAL DIRECTING RULES:
- Give every scene 2-3 DISTINCT English search phrases that literally visualize the exact narration.
- Search phrases must name concrete visible subjects, actions, mechanisms, demonstrations, locations or close details.
- Reject generic typing, office workers, skylines, random phones, abstract charts or vague futuristic imagery unless literally required.
- Vary shot grammar across the video: establishing, macro, detail, human interaction, physical demonstration, infrastructure and controlled camera motion.
- If stock footage cannot honestly communicate a technical idea, use a precise cinematic ai_prompt instead of metaphorically unrelated B-roll.
- CONDITIONAL VALIDATION EXAMPLE, not a story suggestion: only if the user's topic and the chosen single story already require OLED or true black, the narration, stock queries and ai_prompt must show black-region subpixel emitters visibly unlit beside illuminated colored subpixels; whole-screen darkness is insufficient.
- In that same conditional OLED case, if narration claims lower power use, require a real physical meter visibly dropping in the same shot; otherwise rewrite the spoken claim to the directly visible emissive-pixel fact.
- ai_prompt may be non-null when literal stock is unlikely to reliably show the named subject, action or mechanism, and in at most {max_ai_scenes} scenes.
{preview_ai_routing_note}
- Every non-null ai_prompt must describe one continuous five-second 16:9 photorealistic shot with controlled motion, the subject, action and mechanism visibly clear, and no captions, readable interface text, logos, watermarks, charts, random glitch or surreal metaphor.
- The master video is text-free: do not plan captions, lower thirds or on-screen sentences.

FACT RULES:
- Use current web research where useful.
- Do not invent claims or statistics.
- Do not copy source wording.
- sources must contain URLs.
''',
    )
    package = _parse_json_payload(response.output_text)
    if abs(len(package['scenes']) - target_scenes) > 1:
        raise RuntimeError(f'Scene-count gate rejected storyboard: {len(package["scenes"])} scenes; target {target_scenes}')
    package['target_scene_count'] = target_scenes
    package['target_word_range'] = [min_words, max_words]
    package['studio_options'] = options
    return package
