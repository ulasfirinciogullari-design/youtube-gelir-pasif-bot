import json
import re
from openai import OpenAI
from app.config import settings

STYLE_NOTES = {
    'documentary': 'authoritative premium documentary, restrained and evidence-led',
    'technology': 'modern technology documentary, precise, visual and human',
    'story': 'story-led narrative with escalation and payoff',
    'cinematic': 'cinematic essay with controlled reveals and recurring visual motifs',
    'explainer': 'clear causal explainer with demonstrations and comparisons',
}


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
    minimum = max(30, int(round(target * (0.94 if duration_minutes <= 0.6 else 0.86))))
    maximum = max(minimum + 4, int(round(target * 1.06)))
    return target, minimum, maximum


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
        'tts_text': narration,
        'visual_queries': queries,
        'ai_prompt': str(scene.get('ai_prompt') or '').strip() or None,
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


def _run_director(
    client: OpenAI,
    compact: dict,
    topic: str,
    language_name: str,
    duration_minutes: float,
    target_words: int,
    min_words: int,
    max_words: int,
    target_scenes: int,
    options: dict,
    correction: bool = False,
) -> dict:
    style = str(options.get('content_style') or 'documentary')
    pace_profile = str(options.get('pace') or 'balanced')
    visual_mix = str(options.get('visual_mix') or 'balanced')
    reference_url = str(options.get('reference_url') or '').strip()
    current_words = int(compact.get('current_word_count') or 0)
    short_quota_note = ''
    short_visual_note = ''
    if duration_minutes <= 0.6 and target_scenes > 0:
        base, extra = divmod(target_words, target_scenes)
        quotas = [base + (1 if i < extra else 0) for i in range(target_scenes)]
        short_quota_note = (
            f'SHORT PREVIEW — HIGHEST PRIORITY: return exactly {target_scenes} scenes. '
            f'Scene narration word counts must be exactly {quotas}; total exactly {target_words}. '
            'Count hyphenated or apostrophe compounds as one word. '
        )
        short_visual_note = (
            'SHORT PREVIEW VISUAL ROUTING — HIGHEST PRIORITY: ai_prompt values are free fallback candidates, not promised generations. '
            f'Up to {target_scenes} scenes may carry a non-null fallback, while the worker will submit at most three paid Runway generations after measuring the exact current stock clips. '
            'Structure the story so no more than three scenes truly depend on AI. '
            'Reserve those dependencies for facts stock footage cannot literally show, especially an extreme macro OLED subpixel matrix '
            'with black-region emitters visibly unlit beside illuminated colored subpixels, invisible indoor Wi-Fi/cellular/GPS assistance, '
            'or damaged QR error recovery. '
            'For OLED, reject whole-screen dimming, black fades or a hand merely turning a screen off. '
            'Do not narrate a power-use drop unless the same shot includes a real physical power meter visibly falling. '
            'Compress each hard mechanism and its complete causal explanation into one scene; never split, repeat or conclude it in a neighboring stock scene. '
            'Every other scene must remain publishable with a plainly filmable real-world action whose exact subject and action appear in its stock queries, '
            'even when it also carries a fallback ai_prompt for uncertain stock coverage. '
            'For every short preview, regardless of fallback prompt count, the penultimate and final scenes must each be one independently filmable '
            'human or physical action in one ordinary location; they must not summarize, compare or recombine invisible networks, pixel behavior, '
            'error correction or algebra. An ai_prompt may remain fallback metadata, but it never excuses an abstract ending. '
            'Every ai_prompt-null scene must be fully provable by one ordinary stock clip; if all named nouns and actions are unlikely to coexist in that clip, '
            'rewrite the narration and its queries before returning. '
            'Every spoken clause in an ai_prompt-null scene must be literally visible in that same clip; never append abstract phrases such as magic happening, '
            'more working than the viewer can see, hidden systems or silent partners. '
            'Each non-null ai_prompt must be a concrete English prompt for one cinematic five-second 16:9 shot, '
            'with the named subject and action visible and no captions, logos, watermarks or fake interface text. '
        )
    elif options.get('mode') == 'preview':
        short_visual_note = (
            'PREVIEW VISUAL ROUTING — HIGHEST PRIORITY: every ai_prompt MUST be null for this duration. '
            'Make every scene literally stock-filmable and preserve this assignment during all corrections. '
        )
    correction_note = (
        f'CRITICAL CORRECTION: the server counted {current_words} words. '
        f'Rewrite the SAME factual story to exactly {target_words} total words '
        f'(hard allowed range {min_words}-{max_words}); do not add facts. '
        if correction else ''
    )
    reference_note = (
        f'Reference URL: {reference_url}. Use only high-level information architecture and pacing inspiration; never copy wording, signature creative devices or branding.'
        if reference_url else 'No external reference structure was supplied.'
    )
    response = client.responses.create(
        model=settings.openai_model,
        reasoning={'effort': 'medium' if correction else 'low'},
        input=f'''You are the FINAL EDITORIAL DIRECTOR for a premium faceless YouTube video.
Topic: {topic}
Language: {language_name}
Requested duration: {duration_minutes} minutes.
Studio style: {STYLE_NOTES.get(style, STYLE_NOTES['documentary'])}
Studio pace profile: {pace_profile}
Studio visual mix: {visual_mix}
{reference_note}
HARD spoken-word budget: {min_words}-{max_words}; aim for {target_words}.
{short_quota_note}
{short_visual_note}
Target scene budget: approximately {target_scenes} scenes, never more than one scene away.
{correction_note}

DRAFT JSON:
{json.dumps(compact, ensure_ascii=False)}

Return ONLY valid JSON with exactly these keys:
title, thumbnail_text, description, scenes, qc_summary.

Each scene must contain exactly:
narration, visual_queries, ai_prompt, pace, transition.

EDITORIAL QC RULES:
- Produce one coherent story. Repair every abrupt subject jump.
- Every scene must continue, explain, contrast, escalate or pay off the previous scene.
- Remove filler, robotic listicle wording and repetitive transition phrases.
- Spoken {language_name} must sound natural, confident and punctuated for real breaths.
- Each scene contains one complete thought that can remain under one excellent hero visual.
- Never make an ai_prompt-null scene recap several earlier mechanisms or invisible abstractions; it must narrate one visible subject performing one visible action in one ordinary location.
- Every clause of every ai_prompt-null narration must be directly visible in that one clip; remove magic-like hooks, hidden-system claims and spoken conclusions.
- Before returning, audit every ai_prompt-null scene against its queries: all named subjects, actions and context must realistically coexist in a single stock clip.
- Match the selected Studio style without imitating a named creator.
- Apply the global pace profile, but still vary individual scene pace intentionally.
- The master video is text-free. Do not create subtitles, lower thirds or overlay copy.
- visual_queries must literally match the exact spoken meaning and name the visible subject, action and context in the same phrase.
- Never search for an abstract property alone: keep the named subject attached (for example, a damaged QR code being scanned, not a generic software error; OLED pixel microscopy, not digital glitch footage).
- For OLED or true black, narration, stock queries and ai_prompt must require black-region subpixel emitters visibly unlit beside illuminated colored subpixels; a whole-screen fade or hand turning a screen off is not evidence.
- Never claim lower OLED power use in a short scene unless a real physical power meter visibly falls in that same continuous shot.
- Reject generic typing, code errors, random phones, office workers, skylines, fireworks, finance charts, digital noise or abstract tech footage unless literally required by the narration.
- Give every scene 2-3 search options with different shot grammar.
- ai_prompt is null unless stock footage cannot honestly show the concept.
- pace is fast, normal or slow. transition is mostly cut; use match only for a real visual relationship and dip sparingly.
- Final scene must resolve the central curiosity and provide a memorable payoff.
- Total narration word count must be between {min_words} and {max_words}.
- qc_summary is a short list of the main editorial repairs.
''',
    )
    return _json(response.output_text)


def _repair_short_stock_scenes(
    client: OpenAI,
    package: dict,
    language_name: str,
    duration_minutes: float,
) -> dict:
    if duration_minutes > 0.6:
        return package

    scenes = package.get('scenes') or []
    if len(scenes) < 3:
        return package

    stock_positions = sorted({
        *[
            position
            for position, scene in enumerate(scenes)
            if not str(scene.get('ai_prompt') or '').strip()
        ],
        len(scenes) - 2,
        len(scenes) - 1,
    })
    role_by_position = {
        position: (
            'hook' if position == 0
            else 'payoff' if position == len(scenes) - 1
            else 'penultimate' if position == len(scenes) - 2
            else 'bridge'
        )
        for position in stock_positions
    }
    targets_by_position = {
        position: {
            'position': position,
            'role': role_by_position[position],
            'word_count': _word_count(scenes[position].get('narration') or ''),
            'current_narration': scenes[position].get('narration'),
            'current_visual_queries': scenes[position].get('visual_queries') or [],
            'previous_narration': (
                scenes[position - 1].get('narration')
                if position > 0 else None
            ),
            'next_narration': (
                scenes[position + 1].get('narration')
                if position + 1 < len(scenes) else None
            ),
        }
        for position in stock_positions
    }
    original_story = [
        {
            'position': position,
            'route': 'ai' if str(scene.get('ai_prompt') or '').strip() else 'stock',
            'narration': scene.get('narration'),
        }
        for position, scene in enumerate(scenes)
    ]
    mechanism_pattern = re.compile(
        r'\b(?:oled|pixels?|piksel\w*|alt\s*piksel\w*|altpiksel\w*|gps|'
        r'wi[-‑]?fi|cellular|hücresel\w*|qr|error\s+correction|hata\s+düzelt\w*|'
        r'algebra|cebir\w*|timing|zamanlama\w*|'
        r'location\s+(?:systems?|services?|data|tracking|determination)|'
        r'konum\s+(?:sistem\w*|servis\w*|veri\w*|belirle\w*|takip\w*)|'
        r'(?:uydu|wi[-‑]?fi|hücresel)\s+(?:konumla\w*|sinyal\w*))\b',
        flags=re.IGNORECASE,
    )
    abstract_pattern = re.compile(
        r'\b(?:magic|magical|miracle|invisible|hidden\s+systems?|silent\s+partners?|'
        r'sihir\w*|mucize\w*|görünmeyen|gizli\s+sistem\w*|sessiz\s+ortak\w*)\b',
        flags=re.IGNORECASE,
    )
    original_total_words = _word_count(
        package.get('narration')
        or ' '.join(str(scene.get('narration') or '') for scene in scenes)
    )
    original_ai_count = sum(
        1 for scene in scenes if str(scene.get('ai_prompt') or '').strip()
    )

    def validate_generated_row(position: int, row: dict) -> tuple[dict | None, str]:
        target = targets_by_position[position]
        narration = str(row.get('narration') or '').strip()
        got_words = _word_count(narration)
        expected_words = int(target['word_count'])
        if got_words != expected_words:
            return None, (
                f'position {position} has {got_words} narration words; '
                f'expected {expected_words}'
            )
        if (
            ';' in narration
            or ':' in narration
            or not re.fullmatch(r'[^.!?…\r\n]+(?:[.!?…]+)?', narration)
        ):
            return None, f'position {position} must contain one simple sentence'
        if mechanism_pattern.search(narration):
            return None, f'position {position} contains technical recap'
        if abstract_pattern.search(narration):
            return None, f'position {position} contains an unfilmable abstraction'
        if row.get('ai_prompt') is not None:
            return None, f'position {position} must explicitly keep ai_prompt null'

        queries = row.get('visual_queries')
        if not isinstance(queries, list) or not 2 <= len(queries) <= 3:
            return None, (
                f'position {position} must contain exactly two or three stock queries'
            )
        if any(not isinstance(query, str) or not query.strip() for query in queries):
            return None, f'position {position} contains an invalid stock query'
        queries = [query.strip() for query in queries]
        if len({query.casefold() for query in queries}) != len(queries):
            return None, f'position {position} contains duplicate stock queries'
        if any(
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 '\-]*", query)
            for query in queries
        ):
            return None, (
                f'position {position} stock queries must use plain English search text'
            )
        query_lengths = [
            len(re.findall(r"[A-Za-z0-9'-]+", query))
            for query in queries
        ]
        if any(length < 3 or length > 9 for length in query_lengths):
            return None, f'position {position} stock query is not concise'
        return {
            'narration': narration,
            'visual_queries': queries,
        }, ''

    accepted_rows: dict[int, dict] = {}
    failed_candidates: dict[int, dict] = {}
    pending_positions = list(stock_positions)
    feedback_by_position: dict[int, str] = {}
    final_critic_reviews: dict[int, dict] = {}
    generator_calls = 0
    critic_calls = 0
    last_failures: dict[int, str] = {
        position: 'not reviewed yet'
        for position in stock_positions
    }

    for attempt in range(2):
        request_positions = list(pending_positions)
        request_targets = [
            {
                **targets_by_position[position],
                'previous_failed_candidate': failed_candidates.get(position),
                'validation_feedback': feedback_by_position.get(position),
            }
            for position in request_positions
        ]
        response_shape = {
            'scenes': [
                {
                    'position': position,
                    'narration': '...',
                    'visual_queries': ['...', '...'],
                    'ai_prompt': None,
                }
                for position in request_positions
            ],
        }
        generation_context = {
            'title': package.get('title'),
            'complete_original_story_in_order': original_story,
            'accepted_stock_scenes_locked': [
                {
                    'position': position,
                    'role': role_by_position[position],
                    **accepted_rows[position],
                }
                for position in sorted(accepted_rows)
            ],
            'stock_positions_to_rewrite': request_targets,
        }
        generator_calls += 1
        response = client.responses.create(
            model=settings.openai_model,
            reasoning={'effort': 'medium' if attempt else 'low'},
            input=f'''You are repairing the requested stock-routed scenes of a 30-second premium YouTube story before footage search.
Language of spoken narration: {language_name}
Story context:
{json.dumps(generation_context, ensure_ascii=False)}

Return ONLY JSON in exactly this shape:
{json.dumps(response_shape, ensure_ascii=False)}

NON-NEGOTIABLE RULES:
- Return exactly the requested positions and no others. Never rewrite an accepted locked stock scene or an AI-routed mechanism scene.
- Preserve the exact requested narration word count for every returned position.
- Each narration describes ONE visible human or physical action in ONE ordinary location.
- Every spoken clause must be literally visible in the same common five-second stock clip. Do not append an abstract hook, comparison, mystery, lesson or recap.
- Use one simple sentence. Do not combine distinct actions, even with a conjunction, gerund, sequence or subordinate clause.
- Do not use a semicolon or colon to join actions.
- Do not mention or recap OLED, pixels, GPS, Wi-Fi, cellular signals, location systems, QR, error correction, timing, algebra or invisible mechanisms.
- ai_prompt must be JSON null.
- Give exactly 2-3 simple ENGLISH stock search phrases per scene.
- First choose one canonical actor/object, one action verb phrase and one ordinary setting. Repeat that same semantic contract in every query; vary only framing or camera distance.
- Every query must contain 3-9 English words and depict the narration's exact same single action.
- Keep each scene faithful to its supplied role and add no new fact, product or unrelated activity.
- A hook must be one concrete everyday action that opens naturally into the next technical scene.
- A bridge or penultimate scene must connect its immediate neighbors without repeating their mechanism.
- A payoff must follow the preceding scene and provide a visible human result, not a spoken conclusion.
- Keep the spoken narration natural and easy to pronounce in {language_name}.
''',
        )

        global_generation_error = ''
        try:
            data = _json(response.output_text)
        except Exception:
            data = {}
            global_generation_error = 'response was not valid JSON'
        if data and set(data.keys()) != {'scenes'}:
            global_generation_error = 'response must contain exactly the scenes key'
        rows = data.get('scenes') if isinstance(data, dict) else None
        if not global_generation_error and (
            not isinstance(rows, list)
            or len(rows) != len(request_positions)
        ):
            global_generation_error = (
                f'expected exactly {len(request_positions)} requested scene objects'
            )

        expected_row_keys = {'position', 'narration', 'visual_queries', 'ai_prompt'}
        rows_by_position: dict[int, dict] = {}
        if not global_generation_error:
            for row in rows:
                if not isinstance(row, dict) or set(row.keys()) != expected_row_keys:
                    global_generation_error = (
                        'each scene must contain exactly position, narration, '
                        'visual_queries and ai_prompt'
                    )
                    break
                position = row.get('position')
                if type(position) is not int:
                    global_generation_error = 'scene position must be an integer'
                    break
                if position not in request_positions:
                    global_generation_error = (
                        f'unexpected requested scene position {position}'
                    )
                    break
                if position in rows_by_position:
                    global_generation_error = f'duplicate scene position {position}'
                    break
                rows_by_position[position] = row
            if (
                not global_generation_error
                and set(rows_by_position) != set(request_positions)
            ):
                global_generation_error = (
                    'requested scene positions were not returned exactly once'
                )

        deterministic_errors: dict[int, str] = {}
        if global_generation_error:
            deterministic_errors = {
                position: global_generation_error
                for position in request_positions
            }
        else:
            for position in request_positions:
                candidate, error = validate_generated_row(
                    position,
                    rows_by_position[position],
                )
                if error:
                    deterministic_errors[position] = error
                    failed_candidates[position] = {
                        'narration': rows_by_position[position].get('narration'),
                        'visual_queries': rows_by_position[position].get('visual_queries'),
                    }
                elif candidate:
                    accepted_rows[position] = candidate
                    failed_candidates.pop(position, None)

        if deterministic_errors:
            pending_positions = sorted(deterministic_errors)
            feedback_by_position = deterministic_errors
            last_failures = dict(deterministic_errors)
            if attempt == 0:
                continue
            break

        if set(accepted_rows) != set(stock_positions):
            missing = sorted(set(stock_positions) - set(accepted_rows))
            last_failures = {
                position: 'candidate was not available for full-story review'
                for position in missing
            }
            pending_positions = missing
            feedback_by_position = dict(last_failures)
            if attempt == 0:
                continue
            break

        candidate_story = [
            {
                'position': position,
                'route': 'stock' if position in stock_positions else 'ai',
                'role': role_by_position.get(position),
                'narration': (
                    accepted_rows[position]['narration']
                    if position in accepted_rows
                    else scene.get('narration')
                ),
            }
            for position, scene in enumerate(scenes)
        ]
        critic_shape = {
            'scenes': [
                {
                    'position': position,
                    'single_sentence': True,
                    'single_visible_action': True,
                    'single_ordinary_location': True,
                    'all_spoken_meaning_visible': True,
                    'no_invisible_or_abstract_claim': True,
                    'all_named_subjects_coexist': True,
                    'queries_are_english': True,
                    'queries_match_same_action': True,
                    'common_stock_clip_feasible': True,
                    'continues_from_previous': True,
                    'leads_to_next': True,
                    'preserves_story_role': True,
                    'adds_no_new_fact': True,
                    'reason': 'brief evidence-based verdict',
                }
                for position in stock_positions
            ],
        }
        critic_context = {
            'title': package.get('title'),
            'candidate_story_in_order': candidate_story,
            'candidate_stock_scenes': [
                {
                    'position': position,
                    'role': role_by_position[position],
                    **accepted_rows[position],
                }
                for position in stock_positions
            ],
        }
        critic_calls += 1
        critic_response = client.responses.create(
            model=settings.openai_model,
            reasoning={'effort': 'medium'},
            input=f'''Act as an independent, fail-closed stock-shot feasibility critic. Do not rewrite anything.
Evaluate every stock-routed candidate against its exact narration, queries, role, adjacent scenes and complete short story.
{json.dumps(critic_context, ensure_ascii=False)}

Return ONLY JSON in exactly this shape:
{json.dumps(critic_shape, ensure_ascii=False)}

For EACH requested position, set every boolean independently. If evidence is ambiguous, set it false.
- single_sentence: narration contains only one sentence.
- single_visible_action: narration requires exactly one visible action, not two actions joined by a conjunction, gerund, sequence or implied cut.
- single_ordinary_location: narration and every query can share one ordinary physical setting.
- all_spoken_meaning_visible: every spoken clause is directly visible in that single clip.
- no_invisible_or_abstract_claim: there is no magic-like hook, comparison, mystery, technical implication or spoken conclusion.
- all_named_subjects_coexist: one normal five-second stock clip can visibly contain every named subject and object.
- queries_are_english: every query is idiomatic English stock-search text.
- queries_match_same_action: every query depicts the narration's exact same actor/object, action and setting.
- common_stock_clip_feasible: the exact shot is realistically common in stock libraries, not merely imaginable.
- continues_from_previous: it follows the previous scene; for position 0 this boundary check is true.
- leads_to_next: it leads naturally to the next scene; for the final position this boundary check is true.
- preserves_story_role: hook, bridge, penultimate or payoff behavior matches the supplied role.
- adds_no_new_fact: it introduces no unsupported claim, product or unrelated activity.
The reason must name concrete evidence for the verdict. Approval requires all thirteen booleans to be true.
''',
        )

        critic_global_error = ''
        try:
            critic = _json(critic_response.output_text)
        except Exception:
            critic = {}
            critic_global_error = 'independent stock-shot critic returned invalid JSON'
        if critic and set(critic.keys()) != {'scenes'}:
            critic_global_error = (
                'independent stock-shot critic returned an invalid object'
            )
        critic_rows = critic.get('scenes') if isinstance(critic, dict) else None
        if not critic_global_error and (
            not isinstance(critic_rows, list)
            or len(critic_rows) != len(stock_positions)
        ):
            critic_global_error = (
                'independent stock-shot critic did not review every stock scene'
            )

        critic_boolean_keys = {
            'single_sentence',
            'single_visible_action',
            'single_ordinary_location',
            'all_spoken_meaning_visible',
            'no_invisible_or_abstract_claim',
            'all_named_subjects_coexist',
            'queries_are_english',
            'queries_match_same_action',
            'common_stock_clip_feasible',
            'continues_from_previous',
            'leads_to_next',
            'preserves_story_role',
            'adds_no_new_fact',
        }
        expected_critic_keys = {'position', 'reason', *critic_boolean_keys}
        critic_by_position: dict[int, dict] = {}
        if not critic_global_error:
            for row in critic_rows:
                if not isinstance(row, dict) or set(row.keys()) != expected_critic_keys:
                    critic_global_error = 'independent critic returned the wrong fields'
                    break
                position = row.get('position')
                if type(position) is not int or position not in stock_positions:
                    critic_global_error = (
                        'independent critic returned an invalid stock position'
                    )
                    break
                if position in critic_by_position:
                    critic_global_error = (
                        f'independent critic repeated position {position}'
                    )
                    break
                critic_by_position[position] = row
            if (
                not critic_global_error
                and set(critic_by_position) != set(stock_positions)
            ):
                critic_global_error = (
                    'independent critic missed a requested stock position'
                )

        critic_failures: dict[int, str] = {}
        parsed_reviews: dict[int, dict] = {}
        if critic_global_error:
            critic_failures = {
                position: critic_global_error
                for position in stock_positions
            }
        else:
            for position in stock_positions:
                row = critic_by_position[position]
                failed_checks = sorted(
                    key
                    for key in critic_boolean_keys
                    if row.get(key) is not True
                )
                reason = str(row.get('reason') or '').strip()
                if not reason:
                    failed_checks.append('missing_evidence')
                    reason = 'critic omitted evidence'
                parsed_reviews[position] = {
                    'position': position,
                    'accepted': not failed_checks,
                    'failed_checks': failed_checks,
                    'reason': reason[:160],
                }
                if failed_checks:
                    critic_failures[position] = (
                        f'{", ".join(failed_checks)}; {reason[:160]}'
                    )

        if not critic_failures:
            final_critic_reviews = parsed_reviews
            repaired = dict(package)
            repaired_scenes = [dict(scene) for scene in scenes]
            for position in stock_positions:
                repaired_scenes[position]['narration'] = accepted_rows[position]['narration']
                repaired_scenes[position]['tts_text'] = accepted_rows[position]['narration']
                repaired_scenes[position]['visual_queries'] = accepted_rows[position]['visual_queries']
                repaired_scenes[position]['ai_prompt'] = None

            non_target_positions = [
                position
                for position in range(len(scenes))
                if position not in stock_positions
            ]
            invariants_ok = (
                len(repaired_scenes) == len(scenes)
                and all(
                    repaired_scenes[position] == scenes[position]
                    for position in non_target_positions
                )
                and [
                    scene.get('index')
                    for scene in repaired_scenes
                ] == [
                    scene.get('index')
                    for scene in scenes
                ]
            )
            if not invariants_ok:
                last_failures = {
                    position: 'repair changed a non-target scene or scene index'
                    for position in stock_positions
                }
                break

            repaired['scenes'] = repaired_scenes
            repaired['narration'] = ' '.join(
                scene['narration']
                for scene in repaired_scenes
            )
            repaired['tts_narration'] = repaired['narration']
            if _word_count(repaired['narration']) != original_total_words:
                last_failures = {
                    position: 'repair changed the package total word count'
                    for position in stock_positions
                }
                break
            repaired['visual_queries'] = [
                query
                for scene in repaired_scenes
                for query in (scene.get('visual_queries') or [])
            ]
            repaired['ai_scenes'] = [
                scene['ai_prompt']
                for scene in repaired_scenes
                if scene.get('ai_prompt')
            ]
            if len(repaired['ai_scenes']) > original_ai_count:
                last_failures = {
                    position: 'repair increased the authored AI-scene count'
                    for position in stock_positions
                }
                break

            qc_summary = repaired.get('director_qc') or []
            if not isinstance(qc_summary, list):
                qc_summary = [str(qc_summary)]
            repaired['director_qc'] = [
                *qc_summary,
                (
                    'Locked every short-preview stock scene to independently '
                    'verified single-action coverage.'
                ),
            ]
            repaired['stock_scene_qc'] = {
                'version': 2,
                'target_positions': stock_positions,
                'roles': [
                    {
                        'position': position,
                        'role': role_by_position[position],
                    }
                    for position in stock_positions
                ],
                'generator_calls': generator_calls,
                'critic_calls': critic_calls,
                'attempts_used': generator_calls,
                'reviews': [
                    final_critic_reviews[position]
                    for position in stock_positions
                ],
            }
            return repaired

        last_failures = dict(critic_failures)
        if attempt == 0:
            pending_positions = sorted(critic_failures)
            feedback_by_position = dict(critic_failures)
            for position in pending_positions:
                failed_candidates[position] = dict(accepted_rows[position])
                accepted_rows.pop(position, None)
            continue
        break

    failure_details = {
        'positions': stock_positions,
        'generator_calls': generator_calls,
        'critic_calls': critic_calls,
        'failures': [
            {
                'position': position,
                'reason': str(reason)[:140],
            }
            for position, reason in sorted(last_failures.items())
        ],
    }
    raise RuntimeError(
        'Director could not produce fully stock-safe short-preview scenes: '
        + json.dumps(failure_details, ensure_ascii=False, separators=(',', ':'))
    )

def direct_and_qc(package: dict, topic: str, duration_minutes: float, language: str, options: dict | None = None) -> dict:
    if not settings.openai_api_key:
        return package
    scenes = package.get('scenes') or []
    if not scenes:
        return package

    options = dict(options or package.get('studio_options') or {})
    pace_profile = str(options.get('pace') or 'balanced')
    client = OpenAI(api_key=settings.openai_api_key, timeout=90.0, max_retries=1)
    target_words, min_words, max_words = _target_word_budget(duration_minutes)
    target_scenes = _target_scene_count(duration_minutes, pace_profile)
    language_name = 'Turkish' if language.lower().startswith('tr') else language

    compact = {
        'title': package.get('title'),
        'thumbnail_text': package.get('thumbnail_text'),
        'description': package.get('description'),
        'scenes': scenes,
        'sources': package.get('sources', []),
    }

    revised = _run_director(
        client, compact, topic, language_name, duration_minutes,
        target_words, min_words, max_words, target_scenes, options,
    )
    out = _clean_package(revised, package)
    words = _word_count(out['narration'])
    scene_count = len(out['scenes'])
    ai_scene_count = sum(1 for scene in out['scenes'] if scene.get('ai_prompt'))
    preview_ai_limit = None
    if options.get('mode') == 'preview':
        preview_ai_limit = target_scenes if duration_minutes <= 0.6 else 0

    for correction_attempt in range(3):
        ai_count_ok = preview_ai_limit is None or ai_scene_count <= preview_ai_limit
        if min_words <= words <= max_words and abs(scene_count - target_scenes) <= 1 and ai_count_ok:
            break
        correction_input = {
            'title': out.get('title'),
            'thumbnail_text': out.get('thumbnail_text'),
            'description': out.get('description'),
            'scenes': out.get('scenes'),
            'sources': package.get('sources', []),
            'current_word_count': words,
            'current_scene_count': scene_count,
            'current_ai_scene_count': ai_scene_count,
            'max_ai_scene_count': preview_ai_limit,
            'correction_attempt': correction_attempt + 1,
        }
        revised = _run_director(
            client, correction_input, topic, language_name, duration_minutes,
            target_words, min_words, max_words, target_scenes, options, correction=True,
        )
        out = _clean_package(revised, package)
        words = _word_count(out['narration'])
        scene_count = len(out['scenes'])
        ai_scene_count = sum(1 for scene in out['scenes'] if scene.get('ai_prompt'))

    if options.get('mode') == 'preview' and duration_minutes <= 0.6:
        out = _repair_short_stock_scenes(
            client,
            out,
            language_name,
            duration_minutes,
        )
        words = _word_count(out['narration'])
        scene_count = len(out['scenes'])
        ai_scene_count = sum(1 for scene in out['scenes'] if scene.get('ai_prompt'))

    if words < min_words or words > max_words:
        raise RuntimeError(f'Duration gate rejected script: {words} words for requested {duration_minutes} min (target {min_words}-{max_words})')
    if abs(scene_count - target_scenes) > 1:
        raise RuntimeError(f'Scene-count gate rejected final edit: {scene_count} scenes; target {target_scenes}')
    if preview_ai_limit is not None and ai_scene_count > preview_ai_limit:
        raise RuntimeError(
            f'Preview AI-scene gate rejected {ai_scene_count} scenes; maximum {preview_ai_limit}'
        )

    out['narration_word_count'] = words
    out['target_word_range'] = [min_words, max_words]
    out['target_scene_count'] = target_scenes
    out['ai_scene_count'] = ai_scene_count
    out['max_ai_scene_count'] = preview_ai_limit
    out['studio_options'] = options
    return out
