"""Offline lossless-shot tests; examples are not live-media QA evidence."""
import ast
from copy import deepcopy
from pathlib import Path
import re

import pytest

from app.services.production_shot_prompt import (
    ProductionShotPromptError,
    build_production_shot_prompt,
)
from app.services.visual_identity import manufactured_replica_guardrail


def _old_composer():
    path = Path(__file__).resolve().parents[1] / 'app' / 'tasks.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    names = {'_truncate_utf16', '_sanitize_provider_visual_text',
             '_identity_proof_clause', '_runway_prompt_for_scene'}
    namespace = {'re': re, 'manufactured_replica_guardrail': manufactured_replica_guardrail}
    exec(compile(ast.Module(body=[node for node in tree.body
                                if isinstance(node, ast.FunctionDef) and node.name in names],
                            type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['_runway_prompt_for_scene']


@pytest.mark.parametrize('decisive_tail', [
    'Plain cartons have no printed PRODUCT labels; the forklift lowers the pallet.',
    'He pushes a flatbed holding exactly three open cases of oranges; not an empty shopping cart.',
    'At an open hatchback he visibly lifts one closed case from the cart into the trunk.',
])
def test_full_frozen_shot_survives_where_old_composer_lost_the_decisive_tail(decisive_tail):
    # Synthetic reconstruction of the length/order bug, not a fetched live prompt.
    prompt = ('One continuous documentary take with the same adult in a navy shirt. '
              + 'Steady eye-level camera; realistic scale and natural warehouse lighting. ' * 7
              + decisive_tail)
    assert len(prompt) < 1000
    scene = {'ai_prompt': prompt, 'narration': 'The model lowered the cost of moving goods.',
             'visual_queries': ['A man with a generic shopping cart in a warehouse']}
    before = deepcopy(scene)
    old = _old_composer()(scene, None, '9:16')
    assert decisive_tail not in old
    assert build_production_shot_prompt(scene) == prompt
    assert decisive_tail in build_production_shot_prompt(scene)
    assert 'PRIMARY EVENT' not in build_production_shot_prompt(scene)
    assert scene == before


def test_query_narration_and_previous_review_are_not_generation_directions():
    prompt = '1974 Ohio: a mechanical register and glass scanner; no LCD or PIN pad.'
    scene = {'ai_prompt': prompt, 'narration': 'Unrelated spoken metaphor.',
             'visual_queries': ['Modern PIN pad and touchscreen'],
             'review': {'retry_queries': ['Replace with a modern cash register']}}
    assert build_production_shot_prompt(scene) == prompt


@pytest.mark.parametrize('prompt', ['a' * 1000, 'x' * 998 + '\U0001f4e6'])
def test_exact_utf16_boundary_preserves_every_character(prompt):
    assert build_production_shot_prompt({'ai_prompt': prompt}) == prompt


@pytest.mark.parametrize('prompt', ['a' * 1001, 'x' * 999 + '\U0001f4e6',
                                  'Retain action. ' + 'x' * 4000])
def test_over_capacity_is_rejected_not_silently_trimmed(prompt):
    with pytest.raises(ProductionShotPromptError) as caught:
        build_production_shot_prompt({'ai_prompt': prompt})
    assert prompt not in str(caught.value)


@pytest.mark.parametrize('scene', [None, [], 'text', {}, {'ai_prompt': None},
                                 {'ai_prompt': 123}, {'ai_prompt': []}, {'ai_prompt': ''},
                                 {'ai_prompt': '  '}, {'ai_prompt': ' padded '},
                                 {'ai_prompt': 'line\nline'}, {'ai_prompt': 'tab\ttext'},
                                 {'ai_prompt': 'null\x00text'}, {'ai_prompt': 'del\x7ftext'},
                                 {'ai_prompt': 'bad\ud800unicode'}])
def test_malformed_contract_fails_without_coercion_or_rewriting(scene):
    with pytest.raises(ProductionShotPromptError):
        build_production_shot_prompt(scene)


@pytest.mark.parametrize('route', ['gemini', 'veo', 'runway', None, '', 1000, {}])
def test_no_unimplemented_provider_route_or_token_capacity_is_invented(route):
    with pytest.raises(ProductionShotPromptError):
        build_production_shot_prompt({'ai_prompt': 'A hand scans a box.'}, provider_route=route)


def test_approved_identity_scale_and_negated_constraints_are_not_sanitized():
    prompt = ('A visibly inanimate ten-centimetre toy figure, not a real person, '
              'stands beside a 1974 mechanical register. No person or hand. '
              'Preserve the red molded seams and the closed drawer throughout.')
    assert build_production_shot_prompt({'ai_prompt': prompt}) == prompt
