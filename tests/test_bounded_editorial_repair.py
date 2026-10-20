"""Pre-critic defects may use only the three already-budgeted writers."""
from copy import deepcopy

import pytest

from app.services import director
from test_director import FakeClient
from test_documentary_evidence_planning import (
    TOPIC, generated, isolated_planner, package, reviewed,
)
from test_fresh_stock_feedback_budget import writer_context


def candidates():
    value = package(calibrated=True)
    value['spoken_word_budget'] = dict(director._ENGLISH_SHORT_SPOKEN_BUDGET)
    too_short = generated(package())
    one_long = generated(value)
    one_long['scenes'][2]['narration'] += ' directly'
    assert sum(director._word_count(s['narration']) for s in too_short['scenes']) == 54
    assert sum(director._word_count(s['narration']) for s in one_long['scenes']) == 66
    assert director._word_count(one_long['scenes'][2]['narration']) == 12
    return value, too_short, one_long


def run(client, value, *, fresh=True):
    return director._repair_short_stock_scenes(client, value, 'English', .5, TOPIC,
        fresh_scheduled=fresh, spoken_word_budget=value['spoken_word_budget'])


@pytest.mark.parametrize('verdict', ['accept', 'stock_rejected', 'fact_rejected'])
def test_total_then_one_long_scene_uses_spare_slot_and_still_needs_independent_critic(verdict):
    value, first, second = candidates()
    original = deepcopy(value)
    review = reviewed(
        failures={2: ['common_stock_clip_feasible']} if verdict == 'stock_rejected' else {},
        story_failures=['causal_claim_supported'] if verdict == 'fact_rejected' else [],
    )
    client = FakeClient([first, second, generated(value, [2]), review])
    if verdict == 'accept':
        result = run(client, value)
        assert result['narration'] == value['narration']
        assert result['stock_scene_qc']['generator_calls'] == 3
        assert result['stock_scene_qc']['critic_calls'] == 1
        assert result['stock_scene_qc']['story_review']['accepted'] is True
    else:
        with pytest.raises(RuntimeError) as error:
            run(client, value)
        assert error.value.planning_diagnostics['publish_eligible'] is False
    assert value == original
    assert len(client.responses.calls) == 4
    assert sum('tools' in call for call in client.responses.calls) == 1
    context = writer_context(client, 2)
    assert [r['position'] for r in context['stock_positions_to_rewrite']] == [2]
    assert [r['position'] for r in context['accepted_stock_scenes_locked']] == [0, 1, 3, 4, 5]
    assert '12 narration words; expected 5-11' in context['stock_positions_to_rewrite'][0]['validation_feedback']
    assert context['whole_story_word_budget'] == {'minimum': 62, 'target': 65, 'maximum': 66}


def test_third_failed_writer_stops_before_critic_and_cannot_start_a_fourth():
    value, first, second = candidates()
    client = FakeClient([first, second, {'scenes': [second['scenes'][2]]}])
    with pytest.raises(RuntimeError, match='fully stock-safe') as error:
        run(client, value)
    assert len(client.responses.calls) == 3
    assert all('tools' not in call for call in client.responses.calls)
    assert error.value.planning_diagnostics['publish_eligible'] is False


def test_repeated_total_length_failure_cannot_exceed_three_writers():
    value, first, _ = candidates()
    client = FakeClient([first, first, first])
    with pytest.raises(RuntimeError, match='fully stock-safe'):
        run(client, value)
    assert len(client.responses.calls) == 3


@pytest.mark.parametrize('verdict', ['accept', 'rejected'])
def test_full_story_rewrite_can_finish_length_within_its_existing_three_calls(verdict):
    value, first, _ = candidates()
    second = generated(value)
    for row in second['scenes'][:4]:
        row['narration'] = ' '.join(row['narration'].split()[1:])
    assert sum(director._word_count(row['narration']) for row in second['scenes']) == 61
    review = reviewed(story_failures=['causal_claim_supported'] if verdict == 'rejected' else [])
    client = FakeClient([first, second, generated(value), review])
    if verdict == 'rejected':
        with pytest.raises(RuntimeError) as error:
            run(client, value)
        assert error.value.planning_diagnostics['publish_eligible'] is False
    else:
        result = run(client, value)
        assert director._word_count(result['narration']) == 65
        assert result['stock_scene_qc']['generator_calls'] == 3
        assert result['stock_scene_qc']['critic_calls'] == 1
    assert len(client.responses.calls) == 4
    assert all('61 narration words; expected 62-66' in row['validation_feedback']
        for row in writer_context(client, 2)['stock_positions_to_rewrite'])


@pytest.mark.parametrize('fresh', [False, None, 1, 'true'])
def test_no_spare_slot_for_legacy_or_unverified_fresh_scope(fresh):
    value, first, second = candidates()
    client = FakeClient([first, second])
    with pytest.raises(RuntimeError, match='fully stock-safe'):
        run(client, value, fresh=fresh)
    assert len(client.responses.calls) == 2
