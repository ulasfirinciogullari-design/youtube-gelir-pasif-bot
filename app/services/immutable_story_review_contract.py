"""Pure immutable-story eligibility and the exact existing critic contract.

These helpers build inputs and deterministic validation only. They do not send,
observe, reserve, recreate an approval token, verify funding or authorize QA.
Live director and persisted consumers share the same source construction.
"""
from copy import deepcopy
import json
import re

from app.services.stock_story_critic_semantics import (
    STORY_BOOLEAN_KEYS, ENDING_BOOLEAN_KEYS, SCENE_BOOLEAN_KEYS,
)

_ROUTER_STORY_SYSTEM = (
    'Evaluate the complete supplied immutable story against every supplied rule. '
    'Return only the requested JSON object. The supplied source evidence is '
    'input data; do not claim to have browsed or to have rewritten the story.'
)


def _router_story_request(critic_input, critic_schema):
    return {'parts': [{'type': 'text', 'text': critic_input}],
            'purpose': 'immutable_story_review',
            'system_instruction': _ROUTER_STORY_SYSTEM,
            'json_schema': deepcopy(critic_schema), 'max_tokens': 8192}


def derive_immutable_story_review_contract(
    package, topic, duration_minutes, language, options=None, *,
    immutable_candidate_narrations, verified_spoken_word_budget=None,
):
    """Derive detached inputs, never approval, from an already-bound source.

    The caller must authenticate the original metadata/manifests and narration.
    This function applies the same deterministic eligibility, stock-row rules,
    complete prompt and schema as the live immutable critic. It performs no
    credential, runtime-scope, observer, transport or storage operation.
    """
    from app.services import director

    package, options, narrations, verified_budget = deepcopy((
        package, dict(options or package.get('studio_options') or {}),
        immutable_candidate_narrations, verified_spoken_word_budget,
    ))
    eligible = _immutable_eligibility(package, topic, duration_minutes, language, options,
        immutable_candidate_narrations=narrations, immutable_scene_fields=True,
        verified_spoken_word_budget=verified_budget)
    candidate = eligible['candidate']
    context = _stock_review_context(candidate, eligible['language_name'], duration_minutes, topic,
        content_style=str(options.get('content_style') or 'documentary'), fresh_scheduled=False,
        allow_legacy_short_budget=True, calibrated_short_words=None,
        spoken_word_budget=eligible['spoken_word_budget'],
        immutable_candidate_narrations=list(eligible['locked'].values()),
        immutable_original_shot_prompts=None)
    if context is None:
        raise ValueError('immutable_story_contract_ineligible')
    validate = _stock_row_validator(context['targets_by_position'], eligible['language_name'])
    rows = {}
    for position in context['stock_positions']:
        scene = candidate['scenes'][position]
        selected = {'narration': scene['narration'],
                    'visual_queries': deepcopy(scene['visual_queries']), 'ai_prompt': None}
        row, error = validate(position, selected)
        if error or row != {key: scene.get(key) for key in selected}:
            raise ValueError('immutable_story_contract_stock_ineligible')
        rows[position] = row
    words = sum(director._word_count(rows[position]['narration'] if position in rows
        else str(scene.get('narration') or '')) for position, scene in enumerate(candidate['scenes']))
    if not context['minimum_total_words'] <= words <= context['maximum_total_words']:
        raise ValueError('immutable_story_contract_word_budget_ineligible')
    critic = _stock_critic_contract(candidate, candidate['scenes'], context['stock_positions'],
        context['role_by_position'], rows, immutable_scene_fields=True,
        immutable_original_shot_prompts=None, language_name=eligible['language_name'],
        **{name: context[name] for name in ('requested_brief', 'normalized_content_style',
            'fresh_stock_planning', 'documentary_critic_rule', 'explanatory_coda_rule',
            'stock_video_rule', 'proper_name_note')})
    semantics = {name: deepcopy(context[name]) for name in ('stock_positions',
        'normalized_content_style', 'explicit_technical_insert_return_contract',
        'explicit_exterior_establishing_coda')}
    semantics['ending_positions'] = critic['ending_positions']
    return deepcopy({'candidate': candidate,
        'request': _router_story_request(critic['critic_input'], director._contract_schema(critic['critic_shape'])),
        'semantic_arguments': semantics})


def _stock_review_context(
    package,
    language_name,
    duration_minutes,
    topic,
    *,
    content_style,
    fresh_scheduled,
    allow_legacy_short_budget,
    calibrated_short_words,
    spoken_word_budget,
    immutable_candidate_narrations,
    immutable_original_shot_prompts,
):
    from app.services import director as _director
    _documentary_broll_writer_rule = _director._documentary_broll_writer_rule
    _documentary_explanatory_coda_rule = _director._documentary_explanatory_coda_rule
    _documentary_visual_evidence_rule = _director._documentary_visual_evidence_rule
    _exact_narration_lock_from_brief = _director._exact_narration_lock_from_brief
    _fresh_documentary_stock_video_rule = _director._fresh_documentary_stock_video_rule
    _has_explicit_exterior_establishing_coda = _director._has_explicit_exterior_establishing_coda
    _has_explicit_technical_insert_return_contract = _director._has_explicit_technical_insert_return_contract
    _immutable_narration_map = _director._immutable_narration_map
    _normalize_exact_narration = _director._normalize_exact_narration
    _proper_name_spoken_guidance = _director._proper_name_spoken_guidance
    _story_brief_for_qc = _director._story_brief_for_qc
    _target_word_budget = _director._target_word_budget
    _word_count = _director._word_count
    requested_brief = _story_brief_for_qc(topic)
    normalized_content_style = str(
        content_style or 'documentary'
    ).strip().casefold()
    documentary_broll = normalized_content_style == 'documentary'
    fresh_stock_planning = (
        fresh_scheduled is True
        and immutable_candidate_narrations is None
        and immutable_original_shot_prompts is None
    )
    stock_video_rule = _fresh_documentary_stock_video_rule(
        normalized_content_style, fresh_stock_planning,
    )
    documentary_writer_rule = _documentary_broll_writer_rule(normalized_content_style)
    explanatory_coda_rule = _documentary_explanatory_coda_rule(normalized_content_style)
    documentary_critic_rule = (
        'DOCUMENTARY B-ROLL SEMANTICS ARE ACTIVE. A verified historical year, '
        'elapsed duration, count, capacity, total or material-composition '
        'percentage does not need to appear '
        'as readable text, a chart or a literal quantity in the clip. For '
        'all_spoken_meaning_visible and no_invisible_or_abstract_claim, treat '
        'that narrow fact as satisfied only when its exact value is explicitly '
        'supported by supplied source evidence and the queries honestly show '
        'the same named subject when available, otherwise the same specific '
        'object class, activity and relevant setting. The B-roll illustrates '
        'the sourced narration; it is not itself evidence of the numeral. '
        'For material composition, the source must explicitly name the '
        'material and exact percentage; related footage must never be '
        'treated as proof of composition or its effect on performance. '
        'Set the relevant booleans false for an unsupported or overstated '
        'fact, a physical or technical mechanism that remains invisible, a '
        'wrong or contradictory subject, action, place or era, footage that '
        'pretends to be archive evidence, or generic wallpaper with no '
        'specific visual connection. Under this rule, queries_match_same_action '
        'and common_stock_clip_feasible may pass honest contextual B-roll even '
        'when it cannot literally contain millions of items or display a year. '
        + _documentary_visual_evidence_rule(normalized_content_style)
        if documentary_broll
        else (
            'DOCUMENTARY B-ROLL SEMANTICS ARE NOT ACTIVE. Apply the literal '
            'single-clip visibility rules without exception.'
        )
    )

    scenes = package.get('scenes') or []
    if len(scenes) < 3:
        return None
    explicit_technical_insert_return_contract = (
        _has_explicit_technical_insert_return_contract(
            requested_brief,
            scenes,
        )
    )
    explicit_exterior_establishing_coda = (
        _has_explicit_exterior_establishing_coda(
            normalized_content_style,
            scenes,
        )
    )

    exact_narration_lock = _exact_narration_lock_from_brief(requested_brief)
    locked_narration_by_position = (
        _immutable_narration_map(package, immutable_candidate_narrations)
        if immutable_candidate_narrations is not None
        else {}
    )
    if exact_narration_lock is not None:
        complete_scene_narration = _normalize_exact_narration(
            ' '.join(
                str(scene.get('narration') or '').strip()
                for scene in scenes
                if isinstance(scene, dict)
            )
        )
        if complete_scene_narration != exact_narration_lock:
            raise RuntimeError(
                'Exact spoken-narration lock does not match the complete '
                'candidate story before stock repair'
            )
        if immutable_candidate_narrations is None:
            locked_narration_by_position = {
                position: str(scene.get('narration') or '').strip()
                for position, scene in enumerate(scenes)
            }

    # New writing guidance never changes the contract for archived speech or
    # an exact narration/compression review; its audio still needs actual QA.
    proper_name_note = (
        _proper_name_spoken_guidance(language_name)
        if not locked_narration_by_position and immutable_original_shot_prompts is None
        else ''
    )

    target_total_words, minimum_total_words, maximum_total_words = (
        _target_word_budget(
            duration_minutes,
            allow_legacy_short_lock=allow_legacy_short_budget,
            calibrated_short_words=calibrated_short_words,
            spoken_word_budget=spoken_word_budget,
        )
    )
    minimum_scene_words = 5
    maximum_scene_words = 11

    stock_positions = [
        position
        for position, scene in enumerate(scenes)
        if not str(scene.get('ai_prompt') or '').strip()
    ]
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
            'current_word_count': _word_count(
                scenes[position].get('narration') or ''
            ),
            'allowed_word_count': [
                minimum_scene_words,
                maximum_scene_words,
            ],
            'current_narration': scenes[position].get('narration'),
            'current_visual_queries': scenes[position].get('visual_queries') or [],
        }
        for position in stock_positions
    }
    for position in stock_positions:
        if position in locked_narration_by_position:
            targets_by_position[position]['locked_narration'] = (
                locked_narration_by_position[position]
            )
    return {
        'requested_brief': requested_brief,
        'normalized_content_style': normalized_content_style,
        'documentary_broll': documentary_broll,
        'fresh_stock_planning': fresh_stock_planning,
        'stock_video_rule': stock_video_rule,
        'documentary_writer_rule': documentary_writer_rule,
        'explanatory_coda_rule': explanatory_coda_rule,
        'documentary_critic_rule': documentary_critic_rule,
        'scenes': scenes,
        'explicit_technical_insert_return_contract': explicit_technical_insert_return_contract,
        'explicit_exterior_establishing_coda': explicit_exterior_establishing_coda,
        'exact_narration_lock': exact_narration_lock,
        'locked_narration_by_position': locked_narration_by_position,
        'proper_name_note': proper_name_note,
        'target_total_words': target_total_words,
        'minimum_total_words': minimum_total_words,
        'maximum_total_words': maximum_total_words,
        'minimum_scene_words': minimum_scene_words,
        'maximum_scene_words': maximum_scene_words,
        'stock_positions': stock_positions,
        'role_by_position': role_by_position,
        'targets_by_position': targets_by_position,
    }


def _stock_row_validator(targets_by_position, language_name):
    from app.services import director as _director
    _short_spoken_quality_issues = _director._short_spoken_quality_issues
    _word_count = _director._word_count
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

    def validate_generated_row(position: int, row: dict) -> tuple[dict | None, str]:
        target = targets_by_position[position]
        locked_narration = target.get('locked_narration')
        # Exact narration is immutable input, not model output.  The stock
        # writer is asked to echo it only so mixed locked/unlocked requests can
        # share one response contract, but an imperfect echo must never turn a
        # visual-query repair into a narration rewrite (or exhaust the bounded
        # repair budget before the independent critic can review the visuals).
        narration = (
            locked_narration
            if isinstance(locked_narration, str)
            else str(row.get('narration') or '').strip()
        )
        got_words = _word_count(narration)
        allowed_words = target['allowed_word_count']
        if not allowed_words[0] <= got_words <= allowed_words[1]:
            return None, (
                f'position {position} has {got_words} narration words; '
                f'expected {allowed_words[0]}-{allowed_words[1]}'
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
        spoken_issues = _short_spoken_quality_issues(
            {'scenes': [{'narration': narration}]},
            language_name,
        )
        if spoken_issues:
            return None, (
                f'position {position} has unsafe spoken wording: '
                + '; '.join(spoken_issues)
            )
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
            'ai_prompt': None,
        }, ''
    return validate_generated_row


def _stock_critic_contract(
    package,
    scenes,
    stock_positions,
    role_by_position,
    accepted_rows,
    *,
    requested_brief,
    normalized_content_style,
    immutable_scene_fields,
    immutable_original_shot_prompts,
    fresh_stock_planning,
    documentary_critic_rule,
    explanatory_coda_rule,
    stock_video_rule,
    language_name,
    proper_name_note,
):
    from app.services import director as _director
    CONTINUITY_DEICTIC_RULE = _director.CONTINUITY_DEICTIC_RULE
    _HUMAN_CURIOSITY_RULE = _director._HUMAN_CURIOSITY_RULE
    _MATERIAL_IDENTITY_RULE = _director._MATERIAL_IDENTITY_RULE
    _SOURCE_IDENTITY_RULE = _director._SOURCE_IDENTITY_RULE
    _VISIBLE_MATERIAL_RULE = _director._VISIBLE_MATERIAL_RULE
    fresh_candidate_metadata_rule = _director.fresh_candidate_metadata_rule
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
            'visual_queries': (
                accepted_rows[position]['visual_queries']
                if position in accepted_rows
                else scene.get('visual_queries') or []
            ),
            'ai_prompt': (
                accepted_rows[position]['ai_prompt']
                if position in accepted_rows
                else scene.get('ai_prompt')
            ),
        }
        for position, scene in enumerate(scenes)
    ]
    ending_positions = [len(scenes) - 2, len(scenes) - 1]
    story_boolean_keys = STORY_BOOLEAN_KEYS
    ending_boolean_keys = ENDING_BOOLEAN_KEYS
    critic_boolean_keys = SCENE_BOOLEAN_KEYS
    critic_shape = {
        'story_review': {
            **{key: True for key in sorted(story_boolean_keys)},
            'central_question': 'one precise human question',
            'causal_answer': 'one supported causal reveal',
            'visible_payoff': 'one evidenced physical benefit or eligible sourced answer with relevant visuals',
            'natural_spoken_language_evidence': (
                'PASS, or scene N plus an exact quote and the spoken-language issue'
            ),
            'reason': 'brief evidence-based whole-story verdict',
        },
        'ending_pair': {
            'penultimate_position': ending_positions[0],
            'final_position': ending_positions[1],
            **{key: True for key in sorted(ending_boolean_keys)},
            'location_anchor': 'same exact counter, table, doorway or room',
            'reason': 'brief evidence-based ending-pair verdict',
        },
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
        'requested_topic': requested_brief,
        'content_style': normalized_content_style,
        'title': package.get('title'),
        'description': package.get('description'),
        'sources': [
            {
                'url': str(source.get('url') or '')[:500],
                'evidence': str(source.get('evidence') or '')[:500],
            }
            for source in (package.get('sources') or [])[:6]
            if isinstance(source, dict)
        ],
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
    if fresh_stock_planning:
        # Candidate-authored copy must not masquerade as the owner's brief.
        critic_context['generated_candidate_metadata'] = {
            key: critic_context.pop(key) for key in ('title', 'description')
        }
    original_shot_rule = ''
    if immutable_scene_fields:
        critic_context['immutable_scene_fields'] = True
        critic_context['complete_immutable_scenes'] = deepcopy(scenes)
        original_shot_rule = (
            'IMMUTABLE SELECTED STORY REVIEW: all scene fields, source evidence, '
            'title and spoken words are fixed existing inputs. Judge the original '
            'candidate under every ordinary factual, source, story and ending '
            'gate. Do not repair, rewrite or reinterpret an unsafe candidate '
            'as a proposed future replacement. Return only the review contract.'
        )
    if immutable_original_shot_prompts is not None:
        critic_context['immutable_original_shot_prompts'] = {
            str(position): prompt for position, prompt in immutable_original_shot_prompts.items()
        }
        original_shot_rule += (
            'PROMPT COMPRESSION INDEPENDENT CHECK: compare every revised AI '
            'instruction against immutable_original_shot_prompts at the same '
            'position. Under all_explicit_brief_constraints_preserved, require '
            'every original subject, action, period/country, identity, size, '
            'color, setting, continuity and prohibition to remain explicit '
            'in that standalone prompt, not merely in narration or queries. '
            'Only redundant wording may disappear. Fail that existing gate '
            'for a missing, contradictory or ambiguous original constraint; '
            'do not infer preservation from the compressor claiming success. '
            'Apply all ordinary factual, source, story and ending gates too.'
        )
    critic_input = f'''Act as an independent, fail-closed stock-shot feasibility critic. Do not rewrite anything.
Evaluate every stock-routed candidate against its exact narration, queries, role, adjacent scenes and complete short story.
{json.dumps(critic_context, ensure_ascii=False)}
{original_shot_rule}
{fresh_candidate_metadata_rule(fresh_stock_planning)}

Return ONLY JSON in exactly this shape:
{json.dumps(critic_shape, ensure_ascii=False)}

Review the WHOLE story before reviewing individual stock shots. Set each story_review boolean independently and false whenever evidence is ambiguous.
{documentary_critic_rule}
{explanatory_coda_rule}
{stock_video_rule}
- {_MATERIAL_IDENTITY_RULE} A wrong finished-product category fails causal_claim_supported and adds_no_new_fact for the affected scene, even if its ingredient percentages are correct.
- {_HUMAN_CURIOSITY_RULE} An ending that only repeats the introduction without answering its question fails one_specific_useful_reveal or hook_payoff_same_promise; a concise recap after a concrete answer is not that failure. Unnecessary citation boilerplate fails natural_spoken_language.
- {_SOURCE_IDENTITY_RULE} An invented or substituted institution fails causal_claim_supported and adds_no_new_fact.
- {_VISIBLE_MATERIAL_RULE} Unsupported visual identification fails all_spoken_meaning_visible and adds_no_new_fact even when the ingredient percentages themselves are sourced.
- all_explicit_brief_constraints_preserved: every explicit structural, routing, continuity, required-element and forbidden-element constraint in requested_topic is obeyed by the complete candidate story, including narration, visual queries and ai_prompt routes. False if any explicit constraint is omitted, contradicted or replaced by a generic payoff. A wardrobe, camera or framing constraint is preserved when it is explicit in the applicable visual_queries or ai_prompt; never require production-only metadata to be spoken merely to prove compliance.
- single_human_situation: the short follows one concrete everyday situation a person can care about, or one recognisable factual curiosity under the active sourced explanatory-coda contract.
- single_central_question: one curiosity or problem is opened and resolved.
- not_fact_montage: the story is not a sampler, listicle or collage of unrelated mechanisms, products or clever facts.
- causal_scene_chain: every scene advances the same cause-and-effect answer, or builds the same precise source-backed factual explanation under the active explanatory-coda contract, rather than merely sharing a broad topic.
- same_actor_or_object_thread: one recognisable person or object gives the story continuity; the active explanatory coda may connect the precisely identified subject, institution or historical event to its explicitly sourced materials, related details or comparison objects, never silently substitute an explicitly identified individual object/person or drift to unrelated facts.
- human_payoff_visible: the last beat visibly delivers an everyday benefit, or resolves the factual curiosity over specifically relevant subject footage under the active explanatory-coda contract. Do not demand an invented purchase or physical benefit from an educational answer.
- natural_spoken_language: all narration is idiomatic, breath-friendly {language_name}, without translationese, unsafe suffix-attached abbreviations or unsupported foreign terms. For Turkish, this is false when heat, energy or an opened gap becomes an awkward translated grammatical agent, as in “sıkışan ısı fanı hızlandırıyor” or “açılan boşluk fanı yavaşlatıyor”; natural causality says that hot air stays trapped and the fan then changes speed. It is also false when narration verbalizes wardrobe/color continuity, camera direction, shot size, face visibility or framing solely to control production, as in “koyu lacivert tişörtlü Mert ... arkadan izliyor”. Keep that metadata in visual fields unless it changes the story's human meaning.
{proper_name_note}
- directly_answers_requested_topic: the actual hook, reveal and payoff directly answer the supplied topic rather than drifting to a merely coherent side story.
- one_specific_useful_reveal: the viewer receives one specific, clear, source-supported answer to the opening question. A familiar but useful explanation satisfies this gate; novelty, surprise or a non-obvious reveal is not required. Judge publishable clarity, not exceptional originality. Note a merely optional stylistic improvement as WARNING in the reason, without making a satisfied gate false. A missing answer, unsupported claim or filler-only ending still fails.
- causal_claim_supported: independently verify the central explanation, including every factual answer and attribution in an eligible explanatory coda, against the supplied source URLs and evidence. Use bounded web search when the evidence is insufficient; false if the claim cannot be verified or overstates a source. Do not require a non-causal composition fact to invent causality.
- hook_payoff_same_promise: the ending fulfills the exact curiosity opened by the hook, with the precise source-backed answer and relevant visuals for an eligible explanatory coda, not a repeated hook or unrelated conclusion.
central_question, causal_answer and visible_payoff must each be one short, concrete, non-empty summary grounded in the candidate story.
natural_spoken_language_evidence must begin with PASS when natural_spoken_language is true. When it is false, it must name the scene position, quote the exact offending words and explain the concrete spoken-language problem. Never use the general reason to hide or contradict this language evidence.
If any story_review boolean is false, the general reason must name the failed key and discuss only concrete failure evidence, not summarize checks that passed.
A whole-story failure rejects this candidate: do not approve a polished shot plan for a bad idea. A separate rewrite, if requested later, must receive a new complete independent review.

Review ending_pair jointly. The positions must match the supplied final two indexes exactly.
- same_immediate_location: for a physical story, both beats occur in the same named micro-location; counter-to-street is false. Under the active sourced explanatory-coda contract only, this is satisfied by honest contextual views that make no same-location or continuous-event assertion and violate no explicit user location constraint.
- continuous_visible_action_chain: for a physical story, the payoff immediately follows the preceding visible action, seconds later, with no temporal or location jump. Under the active sourced explanatory-coda contract only, this is satisfied when there is no asserted continuous action to interrupt and both beats coherently support the same precise answer.
- same_actor_or_object_thread: the same person or object carries both ending beats, or the precisely identified subject, institution or event and its explicitly sourced relevant details/materials/comparison under the active explanatory-coda contract, never a substitute for an explicitly identified individual object/person.
- everyday_benefit_visible: for an ordinary ending, the final action visibly completes the preceding action and shows the benefit. For an eligible sourced explanatory coda, the payoff is the precise answer illustrated by the relevant subject; no physical benefit or completed action is required. For the separate documentary/explainer exterior coda, the shot must visibly contextualize the same sourced human benefit and object/event without claiming a discontinuous action was completed.
- explicit_technical_insert_return_contract_satisfied: true when requested_topic has no explicit numbered technical-insert return contract. When requested_topic does explicitly number and AI-route the penultimate beat as a technical macro, cutaway, cross-section or inside-the-mechanism insert and the final beat straight back to the same enclosing ordinary setting, set this true only if the candidate obeys that exact route, the insert reveals the mechanism of the same recurring object, and there is no travel, new room, new day or unrelated venue. Otherwise false. A satisfied narrow insert may have same_immediate_location=false because the camera temporarily enters the object; ordinary location changes, implicit routes and generic thematic continuity never qualify for the exception.
- documentary_exterior_establishing_coda_satisfied: true when the final beat does not attempt an exterior establishing coda. When it does, set this true only if content_style is documentary or explainer and the final beat is an exterior establishing coda of the same primary object or event already carried by the penultimate beat. An interior-to-enclosing-exterior camera-vantage cut is allowed, but the subject and event thread must be unchanged, the shot must remain visibly relevant to the same sourced explanation, and it must introduce no new person, object, product or event and no unrelated location, travel beat, day or time jump. Set false for product demonstrations, tutorials, procedures, before/after results, physical actions whose completion must be shown continuously, merely similar stock subjects, unrelated location jumps, identity ambiguity or thematic-only montage. At most same_immediate_location and continuous_visible_action_chain may then be false; same_actor_or_object_thread, everyday_benefit_visible and every other ending boolean must remain true.
location_anchor must name the exact shared micro-location for an ordinary ending. For an eligible sourced explanatory coda, name the precise subject/institution/event and the relevant contextual views without inventing a shared location. For the narrow exterior coda it must instead name the same primary object/event anchor and the precise interior/detail-to-exterior vantage change. reason must cite concrete evidence.

For EACH requested position, set every boolean independently. If evidence is ambiguous, set it false.
- single_sentence: narration contains only one sentence.
- single_visible_action: narration requires exactly one visible action, not two actions joined by a conjunction, gerund, sequence or implied cut; for eligible sourced documentary narration, instead require one coherent relevant visual beat under DOCUMENTARY VISUAL-EVIDENCE PRECEDENCE, without inventing an action in the narration.
- single_ordinary_location: narration and every query can share one ordinary physical setting, applying DOCUMENTARY VISUAL-EVIDENCE PRECEDENCE only to eligible factual voice-over that asserts no physical co-location.
- all_spoken_meaning_visible: every spoken clause is directly visible in that single clip, except for the narrow sourced documentary B-roll semantics above when active.
- Apply this exact narrow semantic rule when judging all_spoken_meaning_visible: {CONTINUITY_DEICTIC_RULE}
- no_invisible_or_abstract_claim: there is no unsupported abstraction, technical implication or invented conclusion. A sourced documentary fact or exact supported comparison is not an invisible abstraction when the active documentary/explanatory-coda contract is fully satisfied; it still cannot imply visually identified ingredients or unproved effects.
- all_named_subjects_coexist: one normal five-second stock clip can visibly contain the named actors and objects asserted to be together. For an eligible source-backed comparison only, relevant subject/material views may be adjacent rather than simultaneous when neither narration nor brief asserts physical coexistence; each query must still specify the actual subject shown, not generic wallpaper.
- queries_are_english: every query is idiomatic English stock-search text.
- queries_match_same_action: every query depicts the narration's exact same actor/object, action and setting, or illustrates the same eligible sourced factual beat and relevant subject under DOCUMENTARY VISUAL-EVIDENCE PRECEDENCE.
- common_stock_clip_feasible: the exact shot is realistically common in stock libraries, not merely imaginable.
- continues_from_previous: it follows the previous scene; for position 0 this boundary check is true.
- leads_to_next: it leads naturally to the next scene; for the final position this boundary check is true.
- preserves_story_role: hook, bridge, penultimate or payoff behavior matches the supplied role.
- adds_no_new_fact: it introduces no unsupported claim, product or unrelated activity.
The reason must name concrete evidence for the verdict. Individual shot approval requires all thirteen booleans to be true.
'''
    return {
        'candidate_story': candidate_story,
        'ending_positions': ending_positions,
        'story_boolean_keys': story_boolean_keys,
        'ending_boolean_keys': ending_boolean_keys,
        'critic_boolean_keys': critic_boolean_keys,
        'critic_shape': critic_shape,
        'critic_context': critic_context,
        'original_shot_rule': original_shot_rule,
        'critic_input': critic_input,
    }


def _immutable_eligibility(
    package,
    topic,
    duration_minutes,
    language,
    options,
    *,
    immutable_candidate_narrations,
    immutable_original_shot_prompts=None,
    immutable_scene_fields=False,
    verified_spoken_word_budget=None,
):
    from app.services import director as _director
    ImmutableNarrationSceneBudgetError = _director.ImmutableNarrationSceneBudgetError
    ScheduledShotPromptError = _director.ScheduledShotPromptError
    _immutable_narration_map = _director._immutable_narration_map
    _scheduled_short_shot_contract = _director._scheduled_short_shot_contract
    _scheduled_shot_prompt_units = _director._scheduled_shot_prompt_units
    _short_preview_scene_budget_issues = _director._short_preview_scene_budget_issues
    _story_brief_for_qc = _director._story_brief_for_qc
    _target_word_budget = _director._target_word_budget
    normalize_evidence_sources = _director.normalize_evidence_sources
    preview_authored_ai_limit = _director.preview_authored_ai_limit
    validate_spoken_word_budget = _director.validate_spoken_word_budget
    if duration_minutes != 0.5 or not (
        options.get('mode') == 'preview'
        or options.get('mode') == 'production' and options.get('format') == 'shorts'
    ):
        raise RuntimeError('Immutable story revalidation requires an exact 30-second Short')
    _story_brief_for_qc(topic)
    spoken_word_budget = None
    if 'spoken_word_budget' in package or verified_spoken_word_budget is not None:
        spoken_word_budget = validate_spoken_word_budget(verified_spoken_word_budget)
        if (
            validate_spoken_word_budget(package.get('spoken_word_budget')) != spoken_word_budget
            or str(language or '').strip().casefold() != 'en'
            or not _scheduled_short_shot_contract(options, duration_minutes, True)
        ):
            raise RuntimeError('Immutable narration budget provenance does not match')
    locked = _immutable_narration_map(package, immutable_candidate_narrations)
    if immutable_scene_fields and not 6 <= len(locked) <= 12:
        raise RuntimeError('Immutable scene review requires six to twelve scenes in a 30-second Short')
    if immutable_original_shot_prompts is not None:
        if (
            not _scheduled_short_shot_contract(options, duration_minutes, True)
            or type(immutable_original_shot_prompts) is not dict
            or not 1 <= len(immutable_original_shot_prompts) <= 6
            or any(type(index) is not int or index not in locked
                   or package['scenes'][index].get('ai_prompt') is None
                   for index in immutable_original_shot_prompts)
        ):
            raise ScheduledShotPromptError('Invalid immutable original shot constraints')
        for prompt in immutable_original_shot_prompts.values():
            _scheduled_shot_prompt_units(prompt)
    candidate = deepcopy(package)
    original_indexes = [scene.get('index') for scene in candidate['scenes']]
    # Never let an old attestation satisfy the new independent review.
    candidate.pop('short_story_qc', None)
    candidate.pop('stock_scene_qc', None)
    immutable_reference = deepcopy(candidate) if immutable_scene_fields else None
    normalize_evidence_sources(candidate.get('sources'), min_count=2, max_count=5)
    language_name = 'Turkish' if language.lower().startswith('tr') else language
    target, minimum, maximum = _target_word_budget(
        0.5, allow_legacy_short_lock=True, spoken_word_budget=spoken_word_budget,
    )
    if _short_preview_scene_budget_issues(candidate, target, len(locked)):
        raise ImmutableNarrationSceneBudgetError('Immutable narration exceeds the single-pass scene budget')
    authored_limit = preview_authored_ai_limit(options, len(locked), duration_minutes)
    # Reviewing existing selected assets creates no media. The worker still
    # admits only frozen rejected slots against its original paid allowances.
    if not immutable_scene_fields and authored_limit is not None and sum(
        bool(scene.get('ai_prompt')) for scene in candidate['scenes']
    ) > authored_limit:
        raise RuntimeError('Immutable story exceeds the authored paid-generation limit')
    return {
        'candidate': candidate,
        'original_indexes': original_indexes,
        'immutable_reference': immutable_reference,
        'locked': locked,
        'spoken_word_budget': spoken_word_budget,
        'language_name': language_name,
        'target': target,
        'minimum': minimum,
        'maximum': maximum,
        'authored_limit': authored_limit,
    }
