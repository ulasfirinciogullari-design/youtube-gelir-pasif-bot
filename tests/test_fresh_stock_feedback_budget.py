"""Fresh planning gets one format repair AND one real feasibility correction."""
from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from test_director import FakeClient
from test_documentary_evidence_planning import (
    TOPIC, directed, generated, isolated_planner, package, reviewed,
)
from app.services import director


FAILED_POSITIONS = (0, 1, 3, 4)
MARKER = 'FRESH DOCUMENTARY STOCK-VIDEO CONTRACT'


def feasibility_failure():
    return reviewed(failures={position: ['common_stock_clip_feasible']
                              for position in FAILED_POSITIONS})


def run(client, value=None, **kwargs):
    return director._repair_short_stock_scenes(
        client, value or package(), 'English', 0.5, TOPIC,
        fresh_scheduled=True, **kwargs,
    )


def writer_context(client, call_index):
    prompt = client.responses.calls[call_index]['input']
    return json.loads(prompt.split('Story context:\n', 1)[1].split('\n\nReturn ONLY JSON', 1)[0])


@pytest.mark.parametrize('first_failure', ['invalid_json', 'query_length', 'total_word_budget'])
def test_deterministic_retry_does_not_spend_the_only_feasibility_rewrite(first_failure):
    value = package()
    invalid = generated(value)
    second_positions = range(6)
    if first_failure == 'invalid_json':
        invalid = '{not-json'
    elif first_failure == 'query_length':
        invalid['scenes'][0]['visual_queries'][0] = 'card'
        second_positions = [0]
    else:
        for row in invalid['scenes']:
            row['narration'] = 'Members pay their annual fee.'
    client = FakeClient([
        invalid, generated(value, second_positions), feasibility_failure(),
        generated(value, FAILED_POSITIONS), reviewed(),
    ])

    result = run(client, value)

    assert result['stock_scene_qc']['generator_calls'] == 3
    assert result['stock_scene_qc']['critic_calls'] == 2
    assert result['stock_scene_qc']['story_review']['accepted'] is True
    assert result['narration'] == value['narration']
    assert len(client.responses.calls) == 5
    final_context = writer_context(client, 3)
    assert [row['position'] for row in final_context['stock_positions_to_rewrite']] == list(FAILED_POSITIONS)
    assert [row['position'] for row in final_context['accepted_stock_scenes_locked']] == [2, 5]
    assert all('common_stock_clip_feasible' in row['validation_feedback']
               for row in final_context['stock_positions_to_rewrite'])


def test_format_retry_after_semantic_rejection_preserves_actual_reviewed_narration():
    value = package()
    first = generated(value)
    reviewed_narration = 'An annual membership fee comes before buying warehouse goods.'
    first['scenes'][0]['narration'] = reviewed_narration
    replacement = generated(value, FAILED_POSITIONS)
    # The model cannot turn a query-only repair into a new spoken assertion.
    for row in replacement['scenes']:
        row['narration'] = 'Every membership guarantees unlimited profit for the warehouse owner.'
    client = FakeClient([
        first, feasibility_failure(), '{invalid', replacement, reviewed(),
    ])

    result = run(client, value)

    assert len(client.responses.calls) == 5
    assert result['stock_scene_qc']['generator_calls'] == 3
    assert result['stock_scene_qc']['critic_calls'] == 2
    assert result['scenes'][0]['narration'] == reviewed_narration
    assert [scene['narration'] for scene in result['scenes'][1:]] == [
        scene['narration'] for scene in value['scenes'][1:]
    ]
    for call_index in (2, 3):
        context = writer_context(client, call_index)
        row = context['stock_positions_to_rewrite'][0]
        assert row['locked_narration'] == reviewed_narration
        assert context['complete_current_story_in_order'][0]['narration'] == reviewed_narration
    assert 'guarantees unlimited profit' not in client.responses.calls[4]['input']


def test_two_deterministic_failures_exhaust_only_one_deterministic_retry():
    client = FakeClient(['{invalid', '{invalid'])
    with pytest.raises(RuntimeError, match='fully stock-safe'):
        run(client)
    assert len(client.responses.calls) == 2
    assert all('tools' not in call for call in client.responses.calls)


@pytest.mark.parametrize('with_first_deterministic_failure', [False, True])
def test_second_semantic_failure_is_terminal_without_a_third_critic(with_first_deterministic_failure):
    value = package()
    outputs = [generated(value), feasibility_failure(),
               generated(value, FAILED_POSITIONS), feasibility_failure()]
    if with_first_deterministic_failure:
        outputs.insert(0, '{invalid')
    client = FakeClient(outputs)
    with pytest.raises(RuntimeError, match='fully stock-safe') as caught:
        run(client, value)
    assert len(client.responses.calls) == 4 + int(with_first_deterministic_failure)
    assert sum('tools' in call for call in client.responses.calls) == 2
    assert caught.value.planning_diagnostics['publish_eligible'] is False
    assert caught.value.planning_diagnostics['review']['scenes'][0]['common_stock_clip_feasible'] is False


def test_existing_bounded_critic_protocol_retry_does_not_create_extra_semantic_rounds():
    value = package()
    client = FakeClient([
        generated(value), '{}', feasibility_failure(),
        generated(value, FAILED_POSITIONS), '{}', reviewed(),
    ])
    result = run(client, value)
    assert result['stock_scene_qc']['generator_calls'] == 2
    assert result['stock_scene_qc']['critic_calls'] == 4
    assert len(client.responses.calls) == 6


def test_fresh_openai_writer_uses_exact_existing_schema_for_each_request():
    value = package()
    client = FakeClient([
        generated(value), feasibility_failure(),
        generated(value, FAILED_POSITIONS), reviewed(),
    ])
    run(client, value)
    for call_index, positions in [(0, range(6)), (2, FAILED_POSITIONS)]:
        request = client.responses.calls[call_index]
        assert request['text'] == {'format': {
            'type': 'json_schema', 'name': 'fresh_stock_writer', 'strict': True,
            'schema': director._stock_writer_json_schema(list(positions)),
        }}
        assert request['text']['format']['schema']['additionalProperties'] is False
        assert 'temperature' not in request
    assert all('text' not in client.responses.calls[index] for index in (1, 3))


@pytest.mark.parametrize('fresh', [False, None, 1, 'true'])
def test_legacy_and_truthy_nonboolean_flags_keep_old_two_attempt_policy(fresh):
    client = FakeClient(['{invalid', generated(package()), feasibility_failure()])
    with pytest.raises(RuntimeError, match='fully stock-safe'):
        director._repair_short_stock_scenes(
            client, package(), 'English', 0.5, TOPIC, fresh_scheduled=fresh,
        )
    assert len(client.responses.calls) == 3
    assert all('text' not in call for call in client.responses.calls)
    assert all(MARKER not in call['input'] for call in client.responses.calls)


def test_immutable_revalidation_cannot_enable_fresh_feedback_policy():
    value = package()
    client = FakeClient(['{invalid', generated(value), feasibility_failure()])
    with pytest.raises(RuntimeError, match='fully stock-safe'):
        run(client, value, immutable_candidate_narrations=[
            scene['narration'] for scene in value['scenes']
        ])
    assert len(client.responses.calls) == 3
    assert all('text' not in call for call in client.responses.calls)


def test_immutable_compression_stays_single_critic_with_no_writer():
    client = FakeClient([feasibility_failure()])
    with pytest.raises(RuntimeError, match='fully stock-safe'):
        run(client, immutable_original_shot_prompts={})
    assert len(client.responses.calls) == 1
    assert 'tools' in client.responses.calls[0]
    assert 'text' not in client.responses.calls[0]


def test_mixed_semantic_failure_does_not_freeze_bad_narration():
    value = package()
    verdict = reviewed(failures={0: ['common_stock_clip_feasible', 'adds_no_new_fact']})
    client = FakeClient([generated(value), verdict, generated(value, [0]), reviewed()])
    run(client, value)
    assert 'locked_narration' not in writer_context(client, 2)['stock_positions_to_rewrite'][0]


def test_gemini_json_schema_request_contract_is_unchanged(monkeypatch):
    monkeypatch.setattr(director, '_studio_plan_provider', lambda: 'gemini')
    gemini = Mock(side_effect=[generated(package()), reviewed()])
    monkeypatch.setattr(director, 'generate_gemini_json', gemini)
    client = FakeClient([])
    run(client)
    assert client.responses.calls == []
    assert gemini.call_args_list[0].kwargs['json_schema'] == director._stock_writer_json_schema(list(range(6)))
    assert 'text' not in gemini.call_args_list[0].kwargs
    assert gemini.call_args_list[1].kwargs['retry_once'] is False


def test_stock_video_contract_is_shared_without_brand_or_event_specific_exception():
    client = FakeClient([generated(package()), reviewed()])
    run(client)
    rule = director._fresh_documentary_stock_video_rule('documentary', True)
    for request in client.responses.calls:
        assert rule in request['input']
    for text in ('not archival photographs', 'not a new spoken claim',
                 'never silently reroute an authored null scene',
                 'all explicit user actions, identities and historical constraints',
                 'generic shop is the named brand, or modern footage is real archive',
                 'membership inspection', 'employee/customer interaction'):
        assert text in rule
    assert 'Costco' not in rule and 'Wrigley' not in rule and '1974' not in rule
    assert director._fresh_documentary_stock_video_rule('technology', True) == ''
    assert director._fresh_documentary_stock_video_rule('documentary', False) == ''


@pytest.mark.parametrize('fresh', [True, False])
def test_initial_director_receives_video_feasibility_guidance_only_for_fresh_plans(fresh):
    client = FakeClient([directed(package())])
    director._run_director(client, package(), TOPIC, 'English', .5, 56, 52, 60, 6,
                           {'mode': 'production', 'format': 'shorts', 'content_style': 'documentary'},
                           fresh_scheduled=fresh)
    assert (MARKER in client.responses.calls[0]['input']) is fresh


def test_full_story_second_review_keeps_fresh_format_guard_but_cannot_rewrite_again(monkeypatch):
    value = package()
    client = FakeClient([directed(value), generated(value),
                         reviewed(story_failures=['human_payoff_visible', 'same_actor_or_object_thread']),
                         directed(value), generated(value), reviewed()])
    monkeypatch.setattr(director, 'OpenAI', lambda **kwargs: client)
    result = director.direct_and_qc(value, TOPIC, .5, 'en',
                                   {'mode': 'production', 'format': 'shorts'}, fresh_scheduled=True)
    assert result['stock_scene_qc']['story_review']['accepted'] is True
    assert client.responses.calls[1]['text']['format']['strict'] is True
    assert client.responses.calls[4]['text']['format']['strict'] is True
    assert len(client.responses.calls) == 6
