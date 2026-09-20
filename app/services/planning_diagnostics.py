"""Bounded rejected-story evidence, never an approval or retry credential."""
import json
import re


_REVIEW_FIELDS = frozenset({
    'story_review', 'ending_pair', 'scenes', 'position', 'penultimate_position',
    'final_position', 'reason', 'failed_checks', 'central_question', 'causal_answer',
    'visible_payoff', 'natural_spoken_language_evidence', 'location_anchor',
    'all_explicit_brief_constraints_preserved', 'single_human_situation',
    'single_central_question', 'not_fact_montage', 'causal_scene_chain',
    'same_actor_or_object_thread', 'human_payoff_visible', 'natural_spoken_language',
    'directly_answers_requested_topic', 'one_specific_useful_reveal',
    'causal_claim_supported', 'hook_payoff_same_promise', 'same_immediate_location',
    'continuous_visible_action_chain', 'everyday_benefit_visible',
    'explicit_technical_insert_return_contract_satisfied',
    'documentary_exterior_establishing_coda_satisfied', 'single_sentence',
    'single_visible_action', 'single_ordinary_location', 'all_spoken_meaning_visible',
    'no_invisible_or_abstract_claim', 'all_named_subjects_coexist',
    'queries_are_english', 'queries_match_same_action', 'common_stock_clip_feasible',
    'continues_from_previous', 'leads_to_next', 'preserves_story_role',
    'adds_no_new_fact', 'positions', 'generator_calls', 'critic_calls', 'failures',
    'factual_audit', 'editorial_review', 'sentences', 'assessment', 'narration',
})


def _text(value, limit):
    if not isinstance(value, str):
        return ''
    text = re.sub(r'[\x00-\x1f\x7f]', ' ', value)
    detection_text = text.replace('\\"', '"').replace("\\'", "'")
    if re.search(r'\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|authorization|'
                 r'client[_ -]?secret|password|secret)["\x27]?\s*[:=]', detection_text,
                 flags=re.IGNORECASE):
        # Do not guess where a quoted, multiline or encoded credential ends.
        return '[credential-bearing text omitted]'
    text = re.sub(r'https?:(?:\\?/){2}[^\s<>"\x27]+', '[URL omitted]', text, flags=re.IGNORECASE)
    text = re.sub(r'https?://[^\s<>"\x27]+', '[URL omitted]', text, flags=re.IGNORECASE)
    text = re.sub(r'\bBearer\s+[^\s,;]+', '[credential omitted]', text, flags=re.IGNORECASE)
    text = re.sub(r'\b(?:sk-[A-Za-z0-9_-]{8,}|AIza[A-Za-z0-9_-]{20,})',
                  '[credential omitted]', text)
    text = re.sub(r'\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|authorization|'
                  r'client[_ -]?secret|password|secret)["\x27]?\s*[:=]\s*["\x27]?[^\s,;"\x27]+',
                  '[credential omitted]', text, flags=re.IGNORECASE)
    return text.encode('utf-8', errors='replace')[:limit].decode('utf-8', errors='ignore')


def _review(value, depth=0):
    if depth > 4:
        return None
    if isinstance(value, dict):
        return {key: _review(item, depth + 1) for key, item in value.items()
                if key in _REVIEW_FIELDS}
    if isinstance(value, list):
        return [_review(item, depth + 1) for item in value[:16]]
    if type(value) in (bool, int) or value is None:
        return value
    return _text(value, 1600)


def _diagnostics(message, scenes, sources, review, *, candidate_kind):
    rows = []
    for scene in (scenes if isinstance(scenes, list) else [])[:6]:
        if not isinstance(scene, dict):
            continue
        position = scene.get('position', scene.get('index', len(rows)))
        queries = scene.get('visual_queries')
        rows.append({
            'position': position if type(position) is int else len(rows),
            'narration': _text(scene.get('narration'), 800),
            'visual_queries': [_text(query, 240) for query in queries[:3]]
            if isinstance(queries, list) else [],
            'ai_prompt': _text(scene['ai_prompt'], 3000) if scene.get('ai_prompt') else None,
        })
    # Only public source evidence is retained. URLs are deliberately omitted:
    # diagnostic records must not become a place to echo credential-bearing URLs.
    evidence = [{ 'evidence': _text(source.get('evidence'), 1500)}
                for source in (sources if isinstance(sources, (list, tuple)) else [])[:6]
                if isinstance(source, dict)]
    result = {'version': 1, 'status': 'rejected_not_approved', 'publish_eligible': False,
              'candidate_kind': candidate_kind, 'reason': _text(message, 2400),
              'scenes': rows, 'source_evidence': evidence, 'review': _review(review)}
    if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > 64 * 1024:
        result['review'] = {'reason': 'Review exceeded the diagnostic byte bound.'}
    return result


def story_planning_error(message, *, scenes, sources=(), review=None):
    error = RuntimeError(message)
    candidate_kind = ('rejected_critic_candidate' if isinstance(review, dict)
                      and ('story_review' in review or {'factual_audit', 'editorial_review'} <= set(review))
                      else 'rejected_planning_candidate_not_critic_reviewed')
    error.planning_diagnostics = _diagnostics(
        message, scenes, sources, review, candidate_kind=candidate_kind,
    )
    return error


def planning_failure_diagnostics(error, draft):
    """Return a JSON-safe copy; a draft fallback is never called the final edit."""
    value = getattr(error, 'planning_diagnostics', None)
    if isinstance(value, dict) and value.get('candidate_kind') in {
        'rejected_critic_candidate', 'rejected_planning_candidate_not_critic_reviewed',
    }:
        return _diagnostics(
            value.get('reason'), value.get('scenes'), value.get('source_evidence'),
            value.get('review'), candidate_kind=value['candidate_kind'],
        )
    draft = draft if isinstance(draft, dict) else {}
    return _diagnostics(str(error), draft.get('scenes'), draft.get('sources'), None,
                        candidate_kind='initial_research_draft_not_final_edit')
