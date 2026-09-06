"""Documentary visual meaning stays evidence-led without inventing actions."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from test_director import CRITIC_BOOLEAN_KEYS, FakeClient, critic_payload
from app.services import director


MARKER = 'DOCUMENTARY VISUAL-EVIDENCE PRECEDENCE'
TOPIC = 'Explain the sourced membership and warehouse trade-off, not a shopping tutorial.'
MULTIPLE_FAILURES = [
    'all_explicit_brief_constraints_preserved', 'hook_payoff_same_promise',
    'human_payoff_visible', 'same_actor_or_object_thread',
]


@pytest.fixture(autouse=True)
def isolated_planner(monkeypatch):
    monkeypatch.setattr(director, 'settings', SimpleNamespace(
        studio_plan_provider='openai', openai_api_key='mock-only',
        openai_model='mock-only', gemini_critic_enabled=False,
    ))
    monkeypatch.setattr(director, '_studio_plan_provider', lambda: 'openai')
    monkeypatch.setattr(director, '_studio_plan_openai_model', lambda: 'mock-only')


def package(*, calibrated=False):
    narrations = [
        'Members pay an annual fee before entering this warehouse.',
        'That payment buys membership rather than any individual product.',
        'Warehouse shelves display goods in their original shipping cartons.',
        'The source describes fewer handling costs for these goods.',
        "Lower operating expenses support the warehouse's limited markup model.",
        'Membership and product sales therefore serve different business purposes.',
    ]
    if calibrated:
        narrations = [
            'Members pay an annual membership fee before entering the retail warehouse.',
            'That annual payment buys access rather than any individual product inside.',
            "Warehouse shelves display merchandise in the goods' original plain shipping cartons.",
            'The company describes fewer handling costs from displaying these cartons directly.',
            "Lower operating expenses help support the warehouse's consistently limited merchandise markups.",
            'Membership access and individual product sales serve different business purposes.',
        ]
    queries = [
        ['membership card beside warehouse entrance', 'warehouse membership card entrance close up'],
        ['membership card beside warehouse checkout', 'warehouse checkout membership card close up'],
        ['warehouse goods in original shipping cartons', 'shipping cartons on warehouse shelves close up'],
        ['warehouse shipping cartons stacked on pallets', 'pallets holding shipping cartons warehouse aisle'],
        ['warehouse merchandise displayed in shipping cartons', 'warehouse aisle merchandise shipping cartons close up'],
        ['membership card beside warehouse merchandise', 'warehouse merchandise membership card close up'],
    ]
    scenes = [{
        'index': position, 'narration': text, 'tts_text': text,
        'visual_queries': queries[position], 'ai_prompt': None,
        'pace': 'normal', 'transition': 'cut', 'overlay_text': None,
    } for position, text in enumerate(narrations)]
    return {
        'title': 'What the membership pays for', 'description': 'A sourced business explanation.',
        'thumbnail_text': '', 'scenes': scenes, 'narration': ' '.join(narrations),
        'tts_narration': ' '.join(narrations), 'ai_scenes': [],
        'visual_queries': [query for row in queries for query in row],
        'sources': [{'url': 'https://example.org/company-profile', 'evidence':
                     'This fixture company charges an annual membership fee. It displays '
                     'merchandise in original shipping cartons to reduce handling costs. '
                     'Low operating costs enable a limited-markup warehouse model. '
                     'Membership fees are distinct from product sales.'},
                    {'url': 'https://example.org/annual-report', 'evidence':
                     'This fixture annual report confirms annual membership fees, '
                     'limited merchandise markups and reduced handling expenses.'}],
    }


def generated(value, positions=range(6)):
    return {'scenes': [{
        'position': position, 'narration': value['scenes'][position]['narration'],
        'visual_queries': deepcopy(value['scenes'][position]['visual_queries']),
        'ai_prompt': None,
    } for position in positions]}


def reviewed(**kwargs):
    result = critic_payload(stock_positions=range(6), **kwargs)
    result['story_review'].update(
        central_question='Why pay a membership fee before buying goods?',
        causal_answer='The source distinguishes membership access from warehouse product sales.',
        visible_payoff='Relevant warehouse detail illustrates the sourced trade-off.',
    )
    result['ending_pair']['location_anchor'] = 'The same warehouse membership model and merchandise.'
    return result


def repair(client, value=None, **kwargs):
    return director._repair_short_stock_scenes(
        client, value or package(), 'English', 0.5, TOPIC, **kwargs,
    )


def directed(value):
    return {key: deepcopy(value[key]) for key in
            ('title', 'description', 'thumbnail_text', 'scenes')} | {'qc_summary': []}


def test_factual_voiceover_uses_one_shared_writer_and_critic_contract():
    value = package()
    original = deepcopy(value)
    client = FakeClient([generated(value), reviewed()])
    result = repair(client, value)

    rule = director._documentary_visual_evidence_rule('documentary')
    assert rule in client.responses.calls[0]['input']
    assert rule in client.responses.calls[1]['input']
    assert 'narration need not itself assert a visible physical action' in rule
    assert 'Supplied primary source evidence must support' in rule
    assert 'do not automatically make any boolean true' in rule
    assert result['narration'] == value['narration']
    assert result['stock_scene_qc']['story_review']['accepted'] is True
    assert all(row['accepted'] for row in result['stock_scene_qc']['reviews'])
    assert len(client.responses.calls) == 2
    assert value == original


def test_critic_every_affected_gate_references_the_factual_visual_contract():
    client = FakeClient([generated(package()), reviewed()])
    repair(client)
    prompt = client.responses.calls[1]['input']
    for key in ('single_visible_action', 'single_ordinary_location', 'queries_match_same_action'):
        line = next(row for row in prompt.splitlines() if row.startswith(f'- {key}:'))
        assert MARKER in line
    assert 'all_named_subjects_coexist apply to what the scene actually asserts' in prompt
    assert 'precisely identified subject, institution or historical event' in prompt
    assert 'without inventing a shared location' in prompt
    assert 'Individual shot approval requires all thirteen booleans to be true' in prompt


@pytest.mark.parametrize('style', ['technology', 'story', 'cinematic', 'explainer'])
def test_other_styles_cannot_inherit_documentary_semantics(style):
    assert director._documentary_visual_evidence_rule(style) == ''
    client = FakeClient([generated(package()), reviewed()])
    repair(client, content_style=style)
    assert 'DOCUMENTARY B-ROLL SEMANTICS ARE NOT ACTIVE' in client.responses.calls[1]['input']
    assert 'DOCUMENTARY VISUAL-EVIDENCE PRECEDENCE: for a factual documentary' not in client.responses.calls[1]['input']


def test_source_history_and_physical_demonstration_boundaries_are_not_waived():
    rule = director._documentary_visual_evidence_rule('documentary')
    for text in (
        'co-occurrence is not evidence of causality',
        'footage illustrates the fact and does not prove it',
        'do not invent profitability, savings or causal effects absent from the source',
        'technical-mechanism, experiment or before/after claim still requires its literal visual evidence',
        'exact identity and continuous-action proof',
        'must not pretend to be archive evidence',
        'preserve every applicable period, place and product constraint',
        'Never erase an explicit identity, action, location or continuity requirement',
    ):
        assert text in rule
    coda = director._documentary_explanatory_coda_rule('documentary')
    assert 'Historical chronology in narration does not by itself assert' in coda
    assert 'reconstruction presented as that event still preserves its period and identities' in coda
    assert 'physical causal-mechanism claim' in coda
    assert 'strict physical-evidence and continuity rules' in coda
    assert 'Costco' not in rule and 'barcode' not in rule and '1974' not in rule


@pytest.mark.parametrize('failed_gate', sorted(CRITIC_BOOLEAN_KEYS))
def test_documentary_false_shot_verdict_is_never_auto_accepted(failed_gate):
    value = package()
    rejection = reviewed(failures={0: [failed_gate]})
    client = FakeClient([generated(value), rejection, generated(value, [0]), rejection])
    with pytest.raises(RuntimeError, match='fully stock-safe') as caught:
        repair(client, value)
    assert not isinstance(caught.value, director._WholeStoryRepairRequired)
    assert len(client.responses.calls) == 4
    diagnostic = caught.value.planning_diagnostics
    assert diagnostic['status'] == 'rejected_not_approved'
    assert diagnostic['publish_eligible'] is False
    assert diagnostic['scenes'][0]['narration'] == value['scenes'][0]['narration']
    assert diagnostic['review']['scenes'][0][failed_gate] is False


def test_multiple_semantic_failures_get_one_opt_in_rewrite_request_not_approval():
    client = FakeClient([generated(package()), reviewed(story_failures=MULTIPLE_FAILURES)])
    with pytest.raises(director._WholeStoryRepairRequired) as caught:
        repair(client, allow_whole_story_repair=True)
    assert caught.value.failed_checks == sorted(MULTIPLE_FAILURES)
    assert caught.value.rejected_candidate_story[0]['narration'] == package()['scenes'][0]['narration']
    assert len(client.responses.calls) == 2


@pytest.mark.parametrize('kwargs', [
    {}, {'allow_whole_story_repair': False}, {'allow_whole_story_repair': 1},
    {'allow_whole_story_repair': True, 'immutable_candidate_narrations':
     [scene['narration'] for scene in package()['scenes']]},
    {'allow_whole_story_repair': True, 'immutable_original_shot_prompts': {}},
])
def test_legacy_or_immutable_review_does_not_gain_semantic_rewrite(kwargs):
    outputs = [reviewed(story_failures=MULTIPLE_FAILURES)]
    if 'immutable_original_shot_prompts' not in kwargs:
        outputs.insert(0, generated(package()))
    client = FakeClient(outputs)
    with pytest.raises(RuntimeError) as caught:
        repair(client, **kwargs)
    assert not isinstance(caught.value, director._WholeStoryRepairRequired)
    assert caught.value.planning_diagnostics['review']['story_review']['human_payoff_visible'] is False


@pytest.mark.parametrize('malformation', ['nonboolean', 'missing_reason', 'missing_summary', 'language_evidence'])
def test_invalid_semantic_review_does_not_request_a_rewrite(malformation):
    verdict = reviewed(story_failures=MULTIPLE_FAILURES)
    row = verdict['story_review']
    if malformation == 'nonboolean':
        row['human_payoff_visible'] = 'false'
    elif malformation == 'missing_reason':
        row['reason'] = ''
    elif malformation == 'missing_summary':
        row['central_question'] = ''
    else:
        row['natural_spoken_language_evidence'] = 'This is not a PASS attestation.'
    client = FakeClient([generated(package()), verdict])
    with pytest.raises(RuntimeError) as caught:
        repair(client, allow_whole_story_repair=True)
    assert not isinstance(caught.value, director._WholeStoryRepairRequired)
    assert len(client.responses.calls) == 2


@pytest.mark.parametrize('second_passes', [True, False])
def test_fresh_pipeline_corrects_once_and_requires_new_complete_critic(monkeypatch, second_passes):
    value = package(calibrated=True)
    failed = reviewed(story_failures=MULTIPLE_FAILURES)
    client = FakeClient([
        directed(value), generated(value), failed,
        directed(value), generated(value), reviewed() if second_passes else failed,
    ])
    monkeypatch.setattr(director, 'OpenAI', lambda **kwargs: client)
    args = (value, TOPIC, 0.5, 'en', {
        'mode': 'production', 'format': 'shorts', 'content_style': 'documentary',
        'visual_mix': 'real_first', 'production_scheduled': True,
    })
    if second_passes:
        result = director.direct_and_qc(*args, fresh_scheduled=True)
        assert result['stock_scene_qc']['story_review']['accepted'] is True
        assert director.short_story_package_is_approved(result, TOPIC)
    else:
        with pytest.raises(RuntimeError, match='incoherent short-preview') as caught:
            director.direct_and_qc(*args, fresh_scheduled=True)
        assert not isinstance(caught.value, director._WholeStoryRepairRequired)
        assert caught.value.planning_diagnostics['status'] == 'rejected_not_approved'
    assert len(client.responses.calls) == 6
    assert 'whole_story_critic' in client.responses.calls[3]['input']
    assert 'rejected_candidate_story' in client.responses.calls[3]['input']
    for gate in MULTIPLE_FAILURES:
        assert gate in client.responses.calls[3]['input']
    assert 'independent, fail-closed stock-shot feasibility critic' in client.responses.calls[5]['input']


def test_legacy_pipeline_failure_does_not_acquire_an_extra_director_call(monkeypatch):
    value = package()
    client = FakeClient([directed(value), generated(value), reviewed(story_failures=MULTIPLE_FAILURES)])
    monkeypatch.setattr(director, 'OpenAI', lambda **kwargs: client)
    with pytest.raises(RuntimeError, match='incoherent short-preview'):
        director.direct_and_qc(value, TOPIC, 0.5, 'en', {'mode': 'preview'})
    assert len(client.responses.calls) == 3


def test_ai_ending_failure_retains_rejected_candidate_diagnostics():
    value = package()
    for scene in value['scenes']:
        scene['ai_prompt'] = 'A continuous view of the relevant warehouse detail.'
    verdict = critic_payload(stock_positions=(), ending_failures=['same_actor_or_object_thread'])
    client = FakeClient([verdict])
    with pytest.raises(RuntimeError, match='AI-routed short-preview ending') as caught:
        repair(client, value)
    assert len(client.responses.calls) == 1
    diagnostic = caught.value.planning_diagnostics
    assert diagnostic['publish_eligible'] is False
    assert diagnostic['scenes'][5]['ai_prompt'] == value['scenes'][5]['ai_prompt']
    assert diagnostic['review']['ending_pair']['same_actor_or_object_thread'] is False
