"""Narrated facts/questions do not inherit incidental stock-search staging."""

import copy
import json

import pytest

from test_documentary_stock_query_hints import _instruction
from test_visual_cross_provider_review import EVIDENCE, _namespace, _openai_response, _review


SCENES = [
    {
        'index': 0, 'ai_prompt': None,
        'narration': 'Cüzdanınızdaki Amerikan doları sanıldığı gibi ağaç kâğıdından üretilmiyor.',
        'visual_queries': ['hands pulling us dollar bill from wallet',
                           'person taking cash dollar from leather wallet'],
    },
    {
        'index': 1, 'ai_prompt': None,
        'narration': 'Peki her gün dokunduğumuz banknot tam olarak neden yapılır?',
        'visual_queries': ['hands holding and examining us dollar bill',
                           'close up of person inspecting genuine dollar bill'],
    },
]
STAGING_RULE = 'A static fact, possessive state or explanatory question'


def _run(provider, tmp_path, scene, *, score=92, reason=None, **flags):
    namespace = _namespace()
    namespace['settings'].studio_plan_provider = provider
    namespace['settings'].studio_visual_qc_provider = provider
    verdict = _review(namespace, score=score, **flags)
    if reason is not None:
        verdict['reason'] = reason
    namespace['generate_gemini_multimodal_json'].return_value = {'reviews': [verdict]}
    _openai_response(namespace, [verdict])
    result = namespace['review_scene_visuals'](
        [scene], [[{'path': 'actual-stock.mp4', 'source_type': 'stock', 'stock_provider': 'pexels'}]],
        tmp_path, topic='Amerikan dolarının kaynaklı pamuk ve keten bileşimi.',
        story_scenes=SCENES, content_style='documentary', evidence_sources=EVIDENCE,
        _missing_review_attempts=0,
    )
    if provider == 'gemini':
        namespace['generate_gemini_multimodal_json'].assert_called_once()
        namespace['OpenAI'].assert_not_called()
        call = namespace['generate_gemini_multimodal_json'].call_args
        instruction = call.kwargs['system_instruction']
        parts = call.args[0]
    else:
        namespace['generate_gemini_multimodal_json'].assert_not_called()
        namespace['OpenAI'].assert_called_once()
        request = namespace['OpenAI'].return_value.responses.create
        request.assert_called_once()
        instruction = request.call_args.kwargs['instructions']
        parts = request.call_args.kwargs['input'][0]['content']
    payload = '\n'.join(part.get('text', '') for part in parts)
    return result['reviews'][0], instruction, payload


@pytest.mark.parametrize('provider', ['gemini', 'openai'])
@pytest.mark.parametrize('scene_index', [0, 1])
def test_both_providers_receive_concrete_static_question_priority_without_changing_story(provider, scene_index, tmp_path):
    scene = copy.deepcopy(SCENES[scene_index])
    before = copy.deepcopy(scene)
    review, instruction, payload = _run(provider, tmp_path, scene)
    assert STAGING_RULE in instruction
    assert 'Outside the SCOPED DOCUMENTARY STOCK QUERY HINTS rule' in instruction
    assert 'use its authority distinction before deriving any mandatory requirement' in instruction
    assert 'not visibly pulling a bill out unless that action is explicitly required' in instruction
    assert 'does not require a single-note macro examination' in instruction
    assert 'spoken_action_visible may be true when the required subject and stated context are genuinely visible' in instruction
    assert scene['narration'] in payload
    assert json.dumps(scene['visual_queries'], ensure_ascii=False) in payload
    assert scene == before
    assert review['raw_score'] == review['score'] == 92


@pytest.mark.parametrize('sources,style,ai_prompt', [
    (None, 'documentary', None), ([], 'documentary', None),
    ([{'url': EVIDENCE[0]['url']}], 'documentary', None),
    (EVIDENCE, 'technology', None), (EVIDENCE, '', None),
    (EVIDENCE, 'documentary', 'An explicit authored AI shot and its identity contract.'),
])
def test_staging_rule_never_expands_beyond_source_backed_stock(sources, style, ai_prompt):
    instruction = _instruction(sources=sources, style=style, scenes=[dict(SCENES[0], ai_prompt=ai_prompt)])
    assert STAGING_RULE not in instruction


def test_user_constraints_named_subjects_and_separate_composition_evidence_remain_binding():
    instruction = _instruction(scenes=SCENES)
    assert 'silent user-specified wardrobe, framing, material and identity constraints' in instruction
    assert 'does not waive any explicitly narrated or user-required physical action or silent visual constraint' in instruction
    assert 'Resolve the exact named subject, currency and material from the ordered story; do not substitute a different one' in instruction
    assert 'Separately narrated constituent materials still require their own matching visuals and source evidence' in instruction
    assert 'a banknote close-up does not prove composition or a manufacturing process' in instruction
    assert 'optional staging neither causes rejection nor grants a pass' in instruction
    assert 'A score of 86+ means the chosen moment is genuinely publishable' in instruction


@pytest.mark.parametrize('subject,query', [
    ('Euro banknotundaki köprü hangi şehirde?', 'close up euro banknote bridge'),
    ('Bir makasın iki bıçağı nasıl birlikte çalışır?', 'hands holding scissors'),
])
def test_non_us_currency_and_ordinary_subjects_do_not_inherit_dollar_example_requirements(subject, query):
    instruction = _instruction(scenes=[{'narration': subject, 'visual_queries': [query], 'ai_prompt': None}])
    assert STAGING_RULE in instruction
    assert 'Resolve the exact named subject, currency and material from the ordered story' in instruction
    assert 'The dollar examples do not impose US currency, wallets or banknotes on scenes about other subjects' in instruction
    assert 'are not substitutes for a genuine US banknote' not in instruction


@pytest.mark.parametrize('provider', ['gemini', 'openai'])
@pytest.mark.parametrize('score,reason', [
    (76, 'US dollar bills and an open wallet match the narration. However pulling a bill is not distinct.'),
    (78, 'Hands prominently handle genuine US dollar bills. It is not a macro examination of one note.'),
    (85, 'The genuine banknote is visible but the shot is too shaky for publication.'),
])
def test_prompt_clarification_does_not_promote_soft_scores_or_add_review_calls(provider, score, reason, tmp_path):
    result, _instruction_text, _payload = _run(provider, tmp_path, SCENES[0], score=score, reason=reason)
    assert result['raw_score'] == result['score'] == score
    assert not result.get('score_reason_revalidation_attempted')


@pytest.mark.parametrize('provider', ['gemini', 'openai'])
@pytest.mark.parametrize('failure', [
    {'subject_visible': False}, {'spoken_action_visible': False},
    {'authored_identity_or_material_conflict_visible': True},
    {'major_visual_artifact_visible': True}, {'effectively_static_or_frozen': True},
    {'substantially_repeats_adjacent_scene': True},
])
def test_wrong_object_missing_required_context_and_real_quality_failures_still_reject(provider, failure, tmp_path):
    result, _instruction_text, _payload = _run(provider, tmp_path, SCENES[0], **failure)
    assert result['raw_score'] == 92
    assert result['score'] == 40
    assert result['hard_gate_diagnostics']


def test_real_narrated_removal_still_requires_temporal_proof(tmp_path):
    scene = dict(SCENES[0], narration='The hand removes the banknote from the wallet.')
    result, instruction, _payload = _run('gemini', tmp_path, scene)
    assert result['state_change_applicable'] is True
    assert result['score'] == 40
    assert 'does not waive any explicitly narrated or user-required physical action' in instruction
