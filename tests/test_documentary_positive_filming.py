"""Future documentary drafts share concrete, truthful filmmaking choices."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import director, research
from test_research_documentary_contract import capture


MARKER = 'POSITIVE DOCUMENTARY SHOT DESIGN:'
TOPIC = (
    'Explain the first retail barcode scan in 1974 in the USA. Preserve the '
    'source-supported Wrigley\'s chewing-gum ten-pack; a present-day checkout '
    'may illustrate the ending but is not historical footage.'
)


def test_shared_rule_preserves_period_equipment_brand_product_and_quantity():
    rule = director._documentary_broll_writer_rule('documentary')
    assert 'repeat the supported period and country in each standalone ai_prompt' in rule
    assert 'matching equipment, clothing and packaging' in rule
    assert 'Keep factual brand names in narration' in rule
    assert 'preserve the sourced product category, pack quantity and scale' in rule
    assert 'Do not turn a multipack into a single unit or substitute another product' in rule
    # The retail example must not become a fixed setting or product for every story.
    assert '1974' not in rule and 'Wrigley' not in rule and 'USA' not in rule


def test_camera_choice_does_not_hide_required_identity_or_invent_print():
    rule = director._documentary_broll_writer_rule('documentary')
    assert 'When incidental package lettering is not the visual evidence' in rule
    assert 'side/back view with hands and the operative detail clear' in rule
    assert 'exact branding or printed detail is required visible evidence' in rule
    assert 'verified real footage or a verified reference' in rule
    assert 'instead of inventing lettering or hiding that required detail' in rule
    assert 'not permission to alter facts or waive any existing QA gate' in rule


def test_substantive_coda_is_not_a_new_forced_action_or_archive_claim():
    rule = director._documentary_broll_writer_rule('documentary')
    assert 'prefer a purposeful, relevant action or revealing subject detail' in rule
    assert 'over idle equipment such as an empty conveyor' in rule
    assert 'still need not invent a completed physical action or a new claim' in rule
    assert 'modern contextual B-roll remains distinct from a reconstruction' in rule
    assert 'must not masquerade as archive footage' in rule
    assert 'never covers an unsupported claim' in rule
    assert 'own source support and visible evidence' in rule


@pytest.mark.parametrize('style', ['story', 'technology', 'cinematic', 'explainer'])
def test_non_documentary_style_does_not_inherit_reconstruction_staging(style):
    assert MARKER not in director._documentary_broll_writer_rule(style)


@pytest.mark.parametrize('mode', ['production', 'preview'])
def test_research_reuses_same_rule_without_new_calls_or_changing_topic(capture, mode):
    prompt, kwargs, result = capture(TOPIC, mode=mode)
    assert research._documentary_broll_writer_rule is director._documentary_broll_writer_rule
    assert prompt.count(MARKER) == 1
    assert director._documentary_broll_writer_rule('documentary') in prompt
    assert f'Topic: {TOPIC}\n' in prompt
    assert 'Topic is the authoritative production contract.' in prompt
    assert kwargs['json_schema'] == research._research_json_schema(6)
    assert result['target_scene_count'] == 6
    assert result['description'] == 'Bu açıklama ilk taslaktan korunur.'


@pytest.mark.parametrize('correction', [False, True])
@pytest.mark.parametrize('mode', ['production', 'preview'])
def test_initial_and_correction_director_reuse_rule_without_mutating_package(monkeypatch, correction, mode):
    compact = {
        'title': 'The first barcode scan',
        'scenes': [{'narration': 'The source identifies a chewing-gum ten-pack.',
                    'visual_queries': ['cashier handles gum multipack'], 'ai_prompt': None}],
        'sources': [{'url': 'https://example.org/source',
                     'evidence': 'This mocked source preserves the stated product and period.'}],
    }
    options = {'content_style': 'documentary', 'mode': mode, 'format': 'shorts',
               'visual_mix': 'real_first', 'quality_threshold': 86}
    before = deepcopy((compact, options))
    generate = Mock(return_value={'scenes': []})
    monkeypatch.setattr(director, '_studio_plan_provider', lambda: 'gemini')
    monkeypatch.setattr(director, 'settings', SimpleNamespace(gemini_api_key='mock-only', gemini_model='mock-only'))
    monkeypatch.setattr(director, 'generate_gemini_json', generate)
    client = Mock()
    director._run_director(client, compact, TOPIC, 'English', 0.5,
                           56, 52, 60, 6, options, correction=correction)
    generate.assert_called_once()
    client.responses.create.assert_not_called()
    prompt = generate.call_args.args[0]
    assert prompt.count(MARKER) == 1
    assert director._documentary_broll_writer_rule('documentary') in prompt
    assert TOPIC in prompt
    assert 'sourced explanatory coda' in prompt
    assert (compact, options) == before
    assert generate.call_args.kwargs['json_schema'] == director._director_json_schema(6, exact_scene_count=False)
    assert generate.call_args.kwargs['google_search'] is False
