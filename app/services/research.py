import json
import math
import re
from openai import OpenAI
from app.config import settings
from app.services.production_spend_runtime import paid_response
from app.services.gemini_generation import (
    GEMINI_DEFAULT_MODEL,
    generate_gemini_json,
)
from app.services.director import (
    _HUMAN_CURIOSITY_RULE,
    _documentary_broll_writer_rule,
    _documentary_explanatory_coda_rule,
    _fresh_documentary_stock_video_rule,
    _explicit_scene_count_from_brief,
    _story_brief_for_qc,
    _scheduled_short_scene_ceiling,
    _scheduled_short_shot_writer_rule,
    _fresh_spoken_word_budget,
    _spoken_word_budget_note,
    _exact_narration_lock_from_brief,
    _proper_name_spoken_guidance,
)
from app.services.visual_routing import preview_authored_ai_limit
from app.services.source_evidence import normalize_evidence_sources

STYLE_DIRECTIONS = {
    'documentary': 'Premium documentary: authoritative but accessible, concrete details, restrained cinematic tension.',
    'technology': 'Modern technology documentary: precise, surprising, visual and human; explain mechanisms without jargon dumping.',
    'story': 'Narrative storytelling: character, problem, escalation and payoff; facts must advance the story.',
    'cinematic': 'Cinematic essay: evocative hook, controlled reveals, strong visual motifs and a memorable payoff.',
    'explainer': 'High-end explainer: crystal-clear causal logic, demonstrations and comparisons, no filler.',
}
PACE_DIRECTIONS = {
    'calm': 'Use measured delivery and give important ideas room to breathe without reducing the required scene count. Do not rush.',
    'balanced': 'Alternate concise reveals with clear explanations. Rhythm should feel edited, not frantic.',
    'dynamic': 'Front-load momentum and use sharper scene turns, while preserving comprehension and continuity.',
}

_PRODUCTION_SCENES_PER_MINUTE = 7.0
_MAX_PRODUCTION_SCENES = 70


def _studio_plan_provider() -> str:
    from app.services.planning_model_routing import planning_provider
    return planning_provider(settings)


def _studio_plan_openai_model() -> str:
    from app.services.planning_model_routing import planning_openai_model
    return planning_openai_model(settings)


def _planning_response_text(response) -> str:
    from app.services.planning_model_routing import planning_response_text
    return planning_response_text(response)


def _research_json_schema(
    target_scenes: int,
    *,
    exact_scene_count: bool = False,
    scene_ceiling: int | None = None,
) -> dict:
    if exact_scene_count:
        minimum_scenes = maximum_scenes = int(target_scenes)
    else:
        minimum_scenes = max(3, int(target_scenes) - 1)
        maximum_scenes = max(minimum_scenes, int(target_scenes) + 1)
        if scene_ceiling is not None:
            minimum_scenes = min(minimum_scenes, scene_ceiling)
            maximum_scenes = max(minimum_scenes, min(maximum_scenes, scene_ceiling))
    scene_schema = {
        'type': 'object',
        'properties': {
            'narration': {'type': 'string'},
            'visual_queries': {
                'type': 'array',
                'items': {'type': 'string'},
                'minItems': 2,
                'maxItems': 3,
            },
            'ai_prompt': {'type': ['string', 'null']},
            # Optional writer self-assessment returned by some routed models.
            # _parse_json_payload discards it; only the independent director
            # and actual footage review can establish visual suitability.
            'visual_queries_match_narrative': {'type': 'boolean'},
        },
        'required': ['narration', 'visual_queries', 'ai_prompt'],
        'additionalProperties': False,
    }
    source_schema = {
        'type': 'object',
        'properties': {
            'url': {'type': 'string'},
            'evidence': {'type': 'string'},
        },
        'required': ['url', 'evidence'],
        'additionalProperties': False,
    }
    return {
        'type': 'object',
        'properties': {
            'title': {'type': 'string'},
            'thumbnail_text': {'type': 'string'},
            'description': {'type': 'string'},
            'scenes': {
                'type': 'array',
                'items': scene_schema,
                'minItems': minimum_scenes,
                'maxItems': maximum_scenes,
            },
            'sources': {
                'type': 'array',
                'items': source_schema,
                'minItems': 2,
                'maxItems': 5,
            },
        },
        'required': [
            'title',
            'thumbnail_text',
            'description',
            'scenes',
            'sources',
        ],
        'additionalProperties': False,
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

    try:
        clean_sources = normalize_evidence_sources(
            data.get('sources'),
            min_count=2,
            max_count=5,
        )
    except ValueError as exc:
        raise RuntimeError(
            f'OpenAI response has invalid research sources: {exc}'
        ) from exc

    data['sources'] = clean_sources
    data['scenes'] = clean_scenes
    data['narration'] = ' '.join(s['narration'] for s in clean_scenes)
    data['tts_narration'] = data['narration']
    data['visual_queries'] = [q for s in clean_scenes for q in s['visual_queries']]
    data['ai_scenes'] = [s['ai_prompt'] for s in clean_scenes if s.get('ai_prompt')]
    data['overlay_phrases'] = []
    return data


def _target_scene_count(duration_minutes: float, pace: str) -> int:
    if duration_minutes > 1.1:
        # Production clips are single-pass. Keep the spoken beat short enough
        # for one 5-10 second shot even when the requested pace is calm.
        return min(
            _MAX_PRODUCTION_SCENES,
            max(
                8,
                int(round(duration_minutes * _PRODUCTION_SCENES_PER_MINUTE)),
            ),
        )
    if duration_minutes <= 0.6:
        base = max(5, int(round(duration_minutes * 12)))
    else:
        base = 6
    if pace == 'calm':
        return max(3, int(round(base * 0.82)))
    if pace == 'dynamic':
        return min(32, max(3, int(round(base * 1.12))))
    return base


def _target_word_budget(duration_minutes: float) -> tuple[int, int, int]:
    if duration_minutes <= 0.6:
        target = max(52, int(round(duration_minutes * 112)))
    elif duration_minutes <= 1.1:
        target = 82
    elif duration_minutes <= 3.1:
        target = int(round(duration_minutes * 92))
    else:
        target = int(round(duration_minutes * 100))
    # A short preview must fill its timeline at a natural 1.0x voice speed.
    # A thin script is rejected upstream instead of being slowed into robotic
    # phrase spacing after synthesis.
    minimum = max(
        30,
        int(round(target * (0.93 if duration_minutes <= 0.6 else 0.88))),
    )
    maximum = max(minimum + 4, int(round(target * 1.07)))
    return target, minimum, maximum


def _max_ai_scenes(scene_count: int, options: dict, duration_minutes: float) -> int:
    preview_limit = preview_authored_ai_limit(
        options,
        scene_count,
        duration_minutes,
    )
    if preview_limit is not None:
        return preview_limit
    if options.get('mode') == 'preview':
        return 0
    mix = options.get('visual_mix') or 'balanced'
    if mix == 'real_first':
        return min(2, max(1, math.ceil(scene_count * 0.10)))
    if mix == 'ai_first':
        return min(6, max(2, math.ceil(scene_count * 0.40)))
    return min(4, max(1, math.ceil(scene_count * 0.24)))


def research_and_script(topic: str, duration_minutes: float, language: str, options: dict | None = None,
                        *, fresh_scheduled: bool = False) -> dict:
    provider = _studio_plan_provider()
    if provider == 'openai' and not settings.openai_api_key:
        raise RuntimeError('OPENAI_API_KEY is not configured')

    options = dict(options or {})
    from app.services.production_delivery import writer_rule

    delivery_rule = writer_rule(options, duration_minutes, select_cuts=False)
    if delivery_rule:
        # The original research brief includes the reusable mini-story structure;
        # the final director selects and binds the exact whole-scene ranges.
        topic = topic + '\n\n' + delivery_rule
    shot_capacity_rule = _scheduled_short_shot_writer_rule(options, duration_minutes, fresh_scheduled)
    shot_aspect = '9:16' if shot_capacity_rule else '16:9'
    style = str(options.get('content_style') or 'documentary')
    pace = str(options.get('pace') or 'balanced')
    mode = str(options.get('mode') or ('preview' if duration_minutes <= 1 else 'production'))
    visual_mix = str(options.get('visual_mix') or 'balanced')
    reference_url = str(options.get('reference_url') or '').strip()

    requested_brief = (
        _story_brief_for_qc(topic)
        if duration_minutes <= 0.6
        else str(topic or '').strip()
    )
    explicit_scene_count = _explicit_scene_count_from_brief(requested_brief)
    target_words, min_words, max_words = _target_word_budget(duration_minutes)
    from app.services.commissioning_longform import active
    if duration_minutes == 3 and options.get('content_plan_item_id') and active():
        explicit_scene_count = 30
        target_words = 315 if language == 'tr' else 360
        min_words, max_words = target_words - 15, target_words + 15
    spoken_word_budget = _fresh_spoken_word_budget(
        duration_minutes, language, options, fresh_scheduled,
        exact_narration=(
            _exact_narration_lock_from_brief(requested_brief)
            if duration_minutes == 0.5 and fresh_scheduled is True else None
        ),
    )
    if spoken_word_budget is not None:
        target_words, min_words, max_words = (
            spoken_word_budget['target_words'], spoken_word_budget['minimum_words'],
            spoken_word_budget['maximum_words'],
        )
    target_scenes = (
        explicit_scene_count
        if explicit_scene_count is not None
        else _target_scene_count(duration_minutes, pace)
    )
    exact_scene_count = explicit_scene_count is not None
    scene_ceiling = (
        None if exact_scene_count
        else _scheduled_short_scene_ceiling(options, duration_minutes, fresh_scheduled)
    )
    max_ai_scenes = _max_ai_scenes(target_scenes, options, duration_minutes)
    language_name = 'Turkish' if language.lower().startswith('tr') else language
    reference_note = (
        f'Reference URL: {reference_url}\nAnalyze only its structural rhythm, hook pattern and information architecture. Do not copy wording, branding, signature devices or protected creative expression.'
        if reference_url else 'No reference video was supplied.'
    )
    documentary = style.strip().casefold() == 'documentary'
    documentary_rules = (
        '\n'.join((
            _documentary_broll_writer_rule(style),
            _documentary_explanatory_coda_rule(style),
            _fresh_documentary_stock_video_rule(style, fresh_scheduled),
            _HUMAN_CURIOSITY_RULE,
            'Unless Topic explicitly requires otherwise, the beat after the '
            'hook must begin answering the established curiosity, not ask '
            'the same question again.',
        ))
        if documentary else ''
    )
    physical_payoff_scope = (
        'Outside the active sourced documentary explanatory coda: '
        if documentary else ''
    )
    physical_stock_scope = (
        'Except for an exactly sourced fact or comparison allowed by the '
        'active documentary B-roll or explanatory-coda contract: '
        if documentary else ''
    )
    central_reveal = 'central sourced answer' if documentary else 'central causal reveal'
    central_claim = 'central sourced answer' if documentary else 'central causal claim'
    stock_query_meaning = (
        'honestly illustrate the exact supported subject under the active '
        'documentary B-roll contract, or literally visualize the narration '
        'when that contract does not apply'
        if documentary else 'literally visualize the exact narration'
    )
    if mode == 'preview' and duration_minutes <= 0.6:
        preview_ai_routing_note = (
            '- EXPLICIT USER-BRIEF OVERRIDE: explicit numbered scene beats, route assignments and continuity constraints in Topic override generic story-shaping heuristics below. Follow them exactly and do not merge or move a required beat merely to prefer one mechanism scene.\n'
            f'- SHORT-PREVIEW STORY CONTRACT: {physical_payoff_scope}first narrow the broad topic to ONE everyday human situation, ONE central curiosity or problem, '
            'ONE recurring person or object, ONE technical reveal and ONE visible everyday benefit. Do not make a sampler, listicle or montage of unrelated facts.\n'
            '- Treat technical mechanisms mentioned elsewhere in this prompt only as conditional visual-validation examples, never as an idea menu or checklist. '
            'At most one mechanism family may drive this 30-second story.\n'
            '- Unless Topic explicitly assigns a multi-scene causal demonstration, contain the chosen hard mechanism and its complete causal explanation inside ONE corresponding AI scene. '
            'Never merge, split, repeat or move explicit numbered beats from Topic.\n'
            f'- {physical_stock_scope}Every scene with ai_prompt set to null must narrate only one literal, realistically filmable subject and action that one ordinary stock clip can visibly show. '
            'It must never recap, compare or recombine several mechanisms or abstract claims.\n'
            f'- {physical_payoff_scope}Make the penultimate action and closing payoff two consecutive visible beats by the same person or object, seconds apart in the SAME named micro-location '
            '(the same counter, table, desk, doorway or room). Repeat that location anchor in both scenes and both query sets; no exit, travel, new room or later-time jump.\n'
            f'- {physical_stock_scope}Before returning, audit each ai_prompt-null scene against its English stock queries. All named subjects, actions and context must realistically coexist '
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

    production_scene_note = (
        'PRODUCTION SINGLE-PASS SCENE CONTRACT: distribute spoken narration '
        'evenly across scenes; preferably keep each scene narration at 5-14 '
        'words. Every scene must be fully visualizable in one continuous '
        '5-10-second shot without looping or combining multiple shots. This '
        "preference never overrides a user brief's explicit exact scene count."
        if duration_minutes > 1.1
        else ''
    )
    fewest_scenes, most_scenes = max(3, target_scenes - 1), target_scenes + 1
    if scene_ceiling is not None:
        fewest_scenes, most_scenes = min(fewest_scenes, scene_ceiling), min(most_scenes, scene_ceiling)
    scene_budget_note = (
        f'USER-BRIEF HARD CONSTRAINT: Create EXACTLY {target_scenes} scenes; '
        'one fewer or one extra scene is invalid.'
        if exact_scene_count
        else (
            f'Target scene budget: approximately {target_scenes} scenes; '
            f'return {fewest_scenes}-{most_scenes} scenes.'
        )
    )

    reasoning_effort = (
        'medium'
        if mode == 'preview' and duration_minutes <= 0.6
        else 'low'
    )
    prompt = f'''Research the current web and act as a senior YouTube writer and storyboard director.
Language: {language_name}
Topic: {topic}
Production mode: {mode}
Editorial style: {STYLE_DIRECTIONS.get(style, STYLE_DIRECTIONS['documentary'])}
Pacing: {PACE_DIRECTIONS.get(pace, PACE_DIRECTIONS['balanced'])}
Visual mix: {visual_mix}
{reference_note}
{documentary_rules}

HARD NARRATION BUDGET: {min_words}-{max_words} total spoken words; aim for {target_words}. Never exceed {max_words}.
{_spoken_word_budget_note(spoken_word_budget)}
{scene_budget_note}
Maximum scenes that may carry an AI fallback prompt: {max_ai_scenes}. Paid generation is selected later from measured stock quality.
{production_scene_note}

Return ONLY valid JSON with exactly these top-level keys:
title, thumbnail_text, description, scenes, sources.

Each scene must contain exactly:
narration, visual_queries, ai_prompt.

sources must contain 2-5 objects, each with exactly:
url, evidence.
evidence is one concise paraphrased sentence from that URL that directly supports the story's {central_reveal}.

STORY RULES:
- Topic is the authoritative production contract. Explicit scene counts, numbered beats, routes, visible attributes, continuity anchors and forbidden elements override generic heuristics in this prompt.
- Write ONE coherent story, not a pile of facts or a numbered list.
- {physical_payoff_scope}For a short preview, silently define one sentence that states: a person or familiar object wants something, meets one obstacle, learns one cause, and receives one visible benefit. Every scene must serve that sentence.
- A broad topic is not an angle. Narrow it to the strongest useful or surprising human question; discard unrelated research facts even when they are individually interesting.
- Every scene must continue, explain, contrast, escalate or pay off the previous scene.
- Hook immediately. No greeting, channel intro or filler.
- {physical_payoff_scope}The final scene must resolve the central curiosity through a visible human action and leave a memorable payoff, not merely a closing thought.
- Spoken {language_name} must sound like an excellent human narrator: concise, deliberate punctuation, varied sentence length and natural bridges.
- For Turkish short previews, standalone OLED, GPS and QR are allowed only with a natural Turkish noun because the voice layer normalizes them. Never attach Turkish suffixes directly to abbreviations, and never speak raw Wi-Fi or Reed-Solomon. Prefer OLED ekran, GPS sinyali, QR kodu, kablosuz ağ or hata düzeltme yöntemi. Technical English remains allowed in visual_queries and ai_prompt.
- For Turkish, reject translated noun stacks, inverted word order and phrases like “siyah yerde”, “hücresel zamanlama tamamlar konumu” or “okunur yine kolayca”.
{_proper_name_spoken_guidance(language_name) if duration_minutes <= 0.6 else ''}
- The complete narration must remain inside {min_words}-{max_words} words.
- Each scene carries one complete idea that can live under one strong hero visual.
- {physical_stock_scope}A stock-only scene may not summarize several earlier mechanisms or invisible abstractions; it must describe one subject performing one visible action in one ordinary location.

VISUAL DIRECTING RULES:
- Give every scene 2-3 DISTINCT English search phrases that {stock_query_meaning}.
- Search phrases must name concrete visible subjects, actions, mechanisms, demonstrations, locations or close details.
- Reject generic typing, office workers, skylines, random phones, abstract charts or vague futuristic imagery unless literally required.
- Vary shot grammar across the video: establishing, macro, detail, human interaction, physical demonstration, infrastructure and controlled camera motion.
- If stock footage cannot honestly communicate a technical idea, use a precise cinematic ai_prompt instead of metaphorically unrelated B-roll.
- CONDITIONAL VALIDATION EXAMPLE, not a story suggestion: only if the user's topic and the chosen single story already require OLED or true black, the narration, stock queries and ai_prompt must show black-region subpixel emitters visibly unlit beside illuminated colored subpixels; whole-screen darkness is insufficient.
- In that same conditional OLED case, if narration claims lower power use, require a real physical meter visibly dropping in the same shot; otherwise rewrite the spoken claim to the directly visible emissive-pixel fact.
- ai_prompt may be non-null when literal stock is unlikely to reliably show the named subject, action or mechanism, and in at most {max_ai_scenes} scenes.
{preview_ai_routing_note}
- Every non-null ai_prompt must describe one continuous {shot_aspect} photorealistic scene-length shot, normally 5-10 seconds, with controlled motion, the subject, action and mechanism visibly clear, and no captions, readable interface text, logos, watermarks, charts, random glitch or surreal metaphor.
{shot_capacity_rule}
- Every non-null ai_prompt is standalone. Repeat inside that scene's own prompt every applicable object identity, dimension, brand state, color, wardrobe, room, lighting, continuity and forbidden-element constraint from Topic; never rely on an earlier scene prompt to carry it forward.
- The master video is text-free: do not plan captions, lower thirds or on-screen sentences.

FACT RULES:
- Use current web research where useful.
- Do not invent claims or statistics.
- Do not copy source wording.
- sources must be evidence records from pages actually used, never a bare URL list.
- Every evidence sentence must directly support the {central_claim}; omit interesting but unused sources.
'''
    consulted_pages = None
    if provider == 'abacus_included':
        from app.services.production_included_router import generate_text_json, stock_only_rule
        from app.services.included_research_sources import research_pages, source_prompt, consulted_source_schema
        consulted_pages = research_pages(topic)
        schema = consulted_source_schema(
            _research_json_schema(target_scenes, exact_scene_count=exact_scene_count,
                                  scene_ceiling=scene_ceiling), consulted_pages)
        generated = generate_text_json(prompt + stock_only_rule() + source_prompt(consulted_pages),
            schema, purpose='research')
        output_text = json.dumps(generated, ensure_ascii=False)
    elif provider == 'gemini':
        generated = generate_gemini_json(
            prompt,
            api_key=str(getattr(settings, 'gemini_api_key', '') or ''),
            model=str(
                getattr(settings, 'gemini_model', GEMINI_DEFAULT_MODEL)
                or GEMINI_DEFAULT_MODEL
            ),
            json_schema=_research_json_schema(
                target_scenes,
                exact_scene_count=exact_scene_count,
                scene_ceiling=scene_ceiling,
            ),
            google_search=True,
            thinking_level=reasoning_effort,
        )
        output_text = json.dumps(generated, ensure_ascii=False)
    else:
        client = OpenAI(
            api_key=settings.openai_api_key,
            timeout=105.0,
            max_retries=1,
        )
        response = paid_response(client,
            model=_studio_plan_openai_model(),
            reasoning={'effort': reasoning_effort},
            tools=[{'type': 'web_search', 'search_context_size': 'low'}],
            tool_choice='auto',
            max_tool_calls=2,
            input=prompt,
        )
        output_text = _planning_response_text(response)
    package = _parse_json_payload(output_text)
    if consulted_pages is not None:
        from app.services.included_research_sources import consulted_sources_only
        consulted_sources_only(package['sources'], consulted_pages)
        if any(scene.get('ai_prompt') is not None for scene in package['scenes']):
            raise RuntimeError('Included production requires a complete stock-only storyboard')
        package['research_source_observations'] = [
            {key: value for key, value in page.items() if key != 'text'} for page in consulted_pages]
    if (
        exact_scene_count
        and len(package['scenes']) != target_scenes
    ) or (
        not exact_scene_count
        and abs(len(package['scenes']) - target_scenes) > 1
    ):
        requirement = (
            f'required exactly {target_scenes}'
            if exact_scene_count
            else f'target {target_scenes}'
        )
        raise RuntimeError(
            'Scene-count gate rejected storyboard: '
            f'{len(package["scenes"])} scenes; {requirement}'
        )
    package['target_scene_count'] = target_scenes
    package.pop('spoken_word_budget', None)
    if spoken_word_budget is not None:
        package['spoken_word_budget'] = dict(spoken_word_budget)
    package['target_word_range'] = [min_words, max_words]
    package['studio_options'] = options
    return package

