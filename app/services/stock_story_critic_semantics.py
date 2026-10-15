"""Pure shared stock/story critic interpretation; no provider or QA authority.

The trusted caller derives stock/ending positions, normalized content style and
both exception-eligibility flags from the complete bound source scenes and brief.
They are not caller assertions that a persisted-evidence reader may trust.
This module interprets an already parsed critic result; it does not authenticate
that result, research claims, validate a source package, repair prose, retry a
request, mint an approval, or authorize rendering/publication. Diagnostics are
independent copies. The live director retains all orchestration decisions.
"""
from copy import deepcopy
import re


STORY_BOOLEAN_KEYS = frozenset({
    'all_explicit_brief_constraints_preserved',
    'single_human_situation',
    'single_central_question',
    'not_fact_montage',
    'causal_scene_chain',
    'same_actor_or_object_thread',
    'human_payoff_visible',
    'natural_spoken_language',
    'directly_answers_requested_topic',
    'one_specific_useful_reveal',
    'causal_claim_supported',
    'hook_payoff_same_promise',
})
ENDING_BOOLEAN_KEYS = frozenset({
    'same_immediate_location',
    'continuous_visible_action_chain',
    'same_actor_or_object_thread',
    'everyday_benefit_visible',
    'explicit_technical_insert_return_contract_satisfied',
    'documentary_exterior_establishing_coda_satisfied',
})
SCENE_BOOLEAN_KEYS = frozenset({
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
})


def validate_stock_story_critic(
    critic, *, stock_positions, ending_positions, normalized_content_style,
    explicit_technical_insert_return_contract, explicit_exterior_establishing_coda,
    protocol_error='',
):
    """Return detached diagnostics with the current live critic semantics.

    ``protocol_error`` preserves an existing provider JSON-parser failure; it is
    not a new retry instruction. Only the caller owns its existing retry policy.
    """
    critic = deepcopy(critic)
    story_boolean_keys = STORY_BOOLEAN_KEYS
    ending_boolean_keys = ENDING_BOOLEAN_KEYS
    critic_boolean_keys = SCENE_BOOLEAN_KEYS
    expected_story_keys = {
        'central_question',
        'causal_answer',
        'visible_payoff',
        'natural_spoken_language_evidence',
        'reason',
        *story_boolean_keys,
    }
    expected_ending_keys = {
        'penultimate_position',
        'final_position',
        'location_anchor',
        'reason',
        *ending_boolean_keys,
    }
    expected_critic_keys = {'position', 'reason', *critic_boolean_keys}
    story_review = None
    ending_pair = None
    story_failure = ''
    failed_story_checks = []
    natural_language_evidence = ''
    critic_rows = None
    critic_by_position = {}
    critic_global_error = protocol_error
    if (
        critic
        and set(critic.keys()) != {'story_review', 'ending_pair', 'scenes'}
    ):
        critic_global_error = (
            'independent stock-shot critic returned an invalid object'
        )
    story_review = (
        critic.get('story_review')
        if isinstance(critic, dict)
        else None
    )
    ending_pair = (
        critic.get('ending_pair')
        if isinstance(critic, dict)
        else None
    )
    if not critic_global_error:
        if (
            not isinstance(story_review, dict)
            or set(story_review.keys()) != expected_story_keys
        ):
            critic_global_error = (
                'whole-story critic returned the wrong fields'
            )
        else:
            failed_story_checks = sorted(
                key
                for key in story_boolean_keys
                if story_review.get(key) is not True
            )
            story_reason = str(
                story_review.get('reason') or ''
            ).strip()
            natural_language_evidence = str(
                story_review.get('natural_spoken_language_evidence') or ''
            ).strip()
            story_summaries = {
                key: str(story_review.get(key) or '').strip()
                for key in (
                    'central_question',
                    'causal_answer',
                    'visible_payoff',
                )
            }
            if not story_reason:
                failed_story_checks.append('missing_evidence')
                story_reason = 'critic omitted whole-story evidence'
            if not natural_language_evidence:
                failed_story_checks.append(
                    'missing_natural_spoken_language_evidence'
                )
            elif story_review.get('natural_spoken_language') is True:
                if not re.match(
                    r'^pass\b',
                    natural_language_evidence,
                    flags=re.IGNORECASE,
                ):
                    failed_story_checks.append(
                        'inconsistent_natural_spoken_language_evidence'
                    )
            elif (
                re.match(
                    r'^pass\b',
                    natural_language_evidence,
                    flags=re.IGNORECASE,
                )
                or not re.search(
                    r'\bscene\s+\d+\b',
                    natural_language_evidence,
                    flags=re.IGNORECASE,
                )
                or not any(
                    quote in natural_language_evidence
                    for quote in ('"', '“', '”')
                )
            ):
                failed_story_checks.append(
                    'inconsistent_natural_spoken_language_evidence'
                )
            for key, value in story_summaries.items():
                if not value:
                    failed_story_checks.append(f'missing_{key}')
            if failed_story_checks:
                failure_reason = (
                    natural_language_evidence
                    if failed_story_checks == ['natural_spoken_language']
                    else story_reason
                )
                story_failure = (
                    f'{", ".join(failed_story_checks)}; '
                    f'{failure_reason[:180]}'
                )
        if not critic_global_error and (
            not isinstance(ending_pair, dict)
            or set(ending_pair.keys()) != expected_ending_keys
            or type(ending_pair.get('penultimate_position')) is not int
            or type(ending_pair.get('final_position')) is not int
            or ending_pair.get('penultimate_position') != ending_positions[0]
            or ending_pair.get('final_position') != ending_positions[1]
        ):
            critic_global_error = (
                'ending-pair critic returned an invalid contract'
            )
    critic_rows = (
        critic.get('scenes')
        if isinstance(critic, dict)
        else None
    )
    if not critic_global_error and (
        not isinstance(critic_rows, list)
        or len(critic_rows) != len(stock_positions)
    ):
        critic_global_error = (
            'independent stock-shot critic did not review every stock scene'
        )
    if not critic_global_error:
        for row in critic_rows:
            if (
                not isinstance(row, dict)
                or set(row.keys()) != expected_critic_keys
            ):
                critic_global_error = (
                    'independent critic returned the wrong fields'
                )
                break
            position = row.get('position')
            if (
                type(position) is not int
                or position not in stock_positions
            ):
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
    critic_failures = {}
    parsed_reviews = {}
    if critic_global_error:
        critic_failures = {position: critic_global_error for position in stock_positions}
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

    ending_failed_checks: list[str] = []
    ending_reason = ''
    ending_location_anchor = ''
    technical_insert_return_exception_applied = False
    documentary_exterior_coda_exception_applied = False
    if not critic_global_error:
        ending_failed_checks = sorted(
            key
            for key in ending_boolean_keys
            if ending_pair.get(key) is not True
        )
        ending_reason = str(ending_pair.get('reason') or '').strip()
        ending_location_anchor = str(
            ending_pair.get('location_anchor') or ''
        ).strip()
        if not ending_reason:
            ending_failed_checks.append('missing_evidence')
            ending_reason = 'critic omitted ending-pair evidence'
        if not ending_location_anchor:
            ending_failed_checks.append('missing_location_anchor')
        if (
            ending_failed_checks == ['same_immediate_location']
            and explicit_technical_insert_return_contract
            and ending_pair.get(
                'explicit_technical_insert_return_contract_satisfied'
            ) is True
        ):
            ending_failed_checks = []
            technical_insert_return_exception_applied = True
        documentary_coda_false_checks = {
            'same_immediate_location',
            'continuous_visible_action_chain',
        }
        if (
            ending_failed_checks
            and set(ending_failed_checks).issubset(
                documentary_coda_false_checks
            )
            and normalized_content_style in {'documentary', 'explainer'}
            and explicit_exterior_establishing_coda
            and ending_pair.get(
                'documentary_exterior_establishing_coda_satisfied'
            ) is True
            and ending_pair.get('same_actor_or_object_thread') is True
            and ending_pair.get('everyday_benefit_visible') is True
        ):
            ending_failed_checks = []
            documentary_exterior_coda_exception_applied = True
    pair_failure = ''
    if not critic_global_error and ending_failed_checks:
        pair_failure = (
            f'ending pair: {", ".join(ending_failed_checks)}; '
            f'{ending_reason[:160]}'
        )
        for position in ending_positions:
            critic_failures[position] = pair_failure
            review = parsed_reviews.get(position)
            if review is not None:
                review['accepted'] = False
                review['failed_checks'] = sorted({
                    *review.get('failed_checks', []),
                    *[f'ending_pair.{key}' for key in ending_failed_checks],
                })
                review['reason'] = pair_failure[:160]

    return deepcopy({
        'critic_global_error': critic_global_error,
        'story_review': story_review, 'ending_pair': ending_pair,
        'critic_by_position': critic_by_position,
        'failed_story_checks': failed_story_checks, 'story_failure': story_failure,
        'natural_language_evidence': natural_language_evidence,
        'critic_failures': critic_failures, 'parsed_reviews': parsed_reviews,
        'ending_failed_checks': ending_failed_checks, 'ending_reason': ending_reason,
        'ending_location_anchor': ending_location_anchor, 'pair_failure': pair_failure,
        'technical_insert_return_exception_applied': technical_insert_return_exception_applied,
        'documentary_exterior_coda_exception_applied': documentary_exterior_coda_exception_applied,
        'diagnostic_only': True, 'qa_approved': False, 'publish_eligible': False,
    })
