"""Shared critic interpretation with existing live fixtures and no provider I/O."""
from copy import deepcopy
import ast
from pathlib import Path

import pytest

from app.services import stock_story_critic_semantics as semantics
from app.services import director
from test_director import critic_payload
from test_immutable_selected_story import case, planning_case, _review, _shape


def validate(value, **kwargs):
    return semantics.validate_stock_story_critic(value, **{
        'stock_positions': [0, 4, 5], 'ending_positions': [4, 5],
        'normalized_content_style': 'documentary',
        'explicit_technical_insert_return_contract': False,
        'explicit_exterior_establishing_coda': False, **kwargs,
    })


def test_positive_fixture_is_detached_and_has_no_qa_or_publish_authority():
    value = critic_payload()
    before = deepcopy(value)
    result = validate(value)
    assert not result['critic_global_error'] and not result['story_failure']
    assert not result['critic_failures'] and not result['ending_failed_checks']
    assert list(result['parsed_reviews']) == [0, 4, 5]
    assert all(row['accepted'] for row in result['parsed_reviews'].values())
    assert result['diagnostic_only'] is True
    assert result['qa_approved'] is result['publish_eligible'] is False
    result['story_review']['reason'] = 'changed returned copy'
    result['critic_by_position'][0]['reason'] = 'changed nested copy'
    result['parsed_reviews'][4]['failed_checks'].append('changed')
    assert value == before
    fresh = validate(value)
    assert fresh['story_review'] == before['story_review']
    assert fresh['parsed_reviews'][4]['failed_checks'] == []


@pytest.mark.parametrize('name', sorted(semantics.STORY_BOOLEAN_KEYS))
def test_each_existing_whole_story_boolean_remains_required(name):
    result = validate(critic_payload(story_failures=[name]))
    assert result['failed_story_checks'] == [name]
    assert result['story_failure'].startswith(name + '; ')
    assert result['critic_global_error'] == ''


@pytest.mark.parametrize('name', sorted(semantics.SCENE_BOOLEAN_KEYS))
def test_each_existing_stock_boolean_remains_required(name):
    result = validate(critic_payload(failures={0: [name]}))
    assert result['parsed_reviews'][0]['accepted'] is False
    assert result['parsed_reviews'][0]['failed_checks'] == [name]
    assert list(result['critic_failures']) == [0]
    assert result['critic_global_error'] == ''


@pytest.mark.parametrize('name', sorted(semantics.ENDING_BOOLEAN_KEYS))
def test_each_ending_boolean_remains_required_without_explicit_exception(name):
    result = validate(critic_payload(ending_failures=[name]))
    assert result['ending_failed_checks'] == [name]
    for position in (4, 5):
        assert result['parsed_reviews'][position]['accepted'] is False
        assert result['parsed_reviews'][position]['failed_checks'] == ['ending_pair.' + name]
    assert result['critic_global_error'] == ''


@pytest.mark.parametrize('value', [1, 0, None, 'true'])
@pytest.mark.parametrize('area,name', [('story_review', 'causal_claim_supported'),
    ('ending_pair', 'same_actor_or_object_thread'), ('scenes', 'common_stock_clip_feasible')])
def test_truthy_values_cannot_replace_exact_true(area, name, value):
    result = critic_payload()
    row = result['scenes'][0] if area == 'scenes' else result[area]
    row[name] = value
    checked = validate(result)
    failures = (checked['failed_story_checks'] if area == 'story_review' else
                checked['ending_failed_checks'] if area == 'ending_pair' else
                checked['parsed_reviews'][0]['failed_checks'])
    assert name in failures and checked['critic_global_error'] == ''


@pytest.mark.parametrize('field', ['reason', 'central_question', 'causal_answer', 'visible_payoff',
                                  'natural_spoken_language_evidence'])
def test_required_whole_story_evidence_cannot_be_blank(field):
    value = critic_payload()
    value['story_review'][field] = '  '
    checked = validate(value)
    name = 'missing_evidence' if field == 'reason' else 'missing_' + field
    assert name in checked['failed_story_checks']


@pytest.mark.parametrize('natural,evidence,invalid', [
    (True, 'pAsS: idiomatic wording.', False),
    (True, 'passable language', True),
    (True, 'Scene 2 "word" is awkward.', True),
    (False, 'PASS: idiomatic wording.', True),
    (False, 'Scene 2 wording is awkward.', True),
    (False, '"word" is awkward.', True),
    (False, 'Scene 2 “word” is awkward.', False),
])
def test_natural_language_evidence_preserves_exact_existing_rules(natural, evidence, invalid):
    value = critic_payload()
    value['story_review'].update(natural_spoken_language=natural,
                                 natural_spoken_language_evidence=evidence)
    result = validate(value)
    assert ('inconsistent_natural_spoken_language_evidence' in result['failed_story_checks']) is invalid
    if natural is False and not invalid:
        assert result['failed_story_checks'] == ['natural_spoken_language']
        assert result['story_failure'] == 'natural_spoken_language; ' + evidence


@pytest.mark.parametrize('damage,expected', [
    ('outer', 'invalid object'), ('story', 'whole-story critic returned the wrong fields'),
    ('ending', 'ending-pair critic returned an invalid contract'),
    ('ending_bool_position', 'ending-pair critic returned an invalid contract'),
    ('row', 'independent critic returned the wrong fields'),
    ('missing_row', 'did not review every stock scene'),
    ('duplicate', 'repeated position 0'), ('wrong_position', 'invalid stock position'),
    ('bool_position', 'invalid stock position'),
])
def test_structural_contract_and_exact_position_mapping(damage, expected):
    value = critic_payload()
    if damage == 'outer': value['extra'] = True
    elif damage == 'story': value['story_review']['extra'] = True
    elif damage == 'ending': value['ending_pair'].pop('location_anchor')
    elif damage == 'ending_bool_position': value['ending_pair']['final_position'] = True
    elif damage == 'row': value['scenes'][0].pop('reason')
    elif damage == 'missing_row': value['scenes'].pop()
    elif damage == 'duplicate': value['scenes'][1]['position'] = 0
    elif damage == 'wrong_position': value['scenes'][1]['position'] = 2
    elif damage == 'bool_position': value['scenes'][0]['position'] = False
    assert expected in validate(value)['critic_global_error']


def test_row_order_is_mapped_to_exact_requested_positions_and_empty_stock_is_valid():
    value = critic_payload()
    value['scenes'].reverse()
    result = validate(value)
    assert result['critic_global_error'] == '' and list(result['parsed_reviews']) == [0, 4, 5]
    assert validate(critic_payload(stock_positions=()), stock_positions=[])['critic_failures'] == {}
    assert validate(critic_payload(stock_positions=()), stock_positions=[])['critic_global_error'] == ''


@pytest.mark.parametrize('mode', ['macro', 'exterior'])
def test_narrow_explicit_ending_exceptions(mode):
    failures = ['same_immediate_location']
    options = {'explicit_technical_insert_return_contract': True}
    flag = 'technical_insert_return_exception_applied'
    if mode == 'exterior':
        failures.append('continuous_visible_action_chain')
        options = {'explicit_exterior_establishing_coda': True}
        flag = 'documentary_exterior_coda_exception_applied'
    value = critic_payload(ending_failures=failures)
    result = validate(value, **options)
    assert result['ending_failed_checks'] == [] and result['critic_failures'] == {}
    assert result[flag] is True
    assert all(row['accepted'] for row in result['parsed_reviews'].values())


@pytest.mark.parametrize('mode', ['macro', 'exterior'])
@pytest.mark.parametrize('damage', ['no_eligibility', 'identity', 'benefit', 'reason', 'anchor', 'marker'])
def test_ending_exceptions_cannot_waive_unrelated_failures_or_missing_evidence(mode, damage):
    failures = ['same_immediate_location']
    options = {'explicit_technical_insert_return_contract': True}
    marker = 'explicit_technical_insert_return_contract_satisfied'
    if mode == 'exterior':
        failures.append('continuous_visible_action_chain')
        options = {'explicit_exterior_establishing_coda': True}
        marker = 'documentary_exterior_establishing_coda_satisfied'
    value = critic_payload(ending_failures=failures)
    if damage == 'no_eligibility': options = {}
    elif damage == 'identity': value['ending_pair']['same_actor_or_object_thread'] = False
    elif damage == 'benefit': value['ending_pair']['everyday_benefit_visible'] = False
    elif damage == 'reason': value['ending_pair']['reason'] = ''
    elif damage == 'anchor': value['ending_pair']['location_anchor'] = ''
    elif damage == 'marker': value['ending_pair'][marker] = False
    result = validate(value, **options)
    assert result['ending_failed_checks'] and result['critic_failures']
    assert not result['technical_insert_return_exception_applied']
    assert not result['documentary_exterior_coda_exception_applied']


def test_exterior_is_not_available_to_story_style_and_macro_cannot_waive_action_chain():
    value = critic_payload(ending_failures=['same_immediate_location', 'continuous_visible_action_chain'])
    assert validate(value, explicit_exterior_establishing_coda=True,
                    normalized_content_style='story')['ending_failed_checks']
    assert validate(value, explicit_technical_insert_return_contract=True)['ending_failed_checks']


def test_existing_parser_error_is_preserved_as_protocol_failure():
    error = 'independent stock-shot critic returned invalid JSON'
    result = validate({}, protocol_error=error)
    assert result['critic_global_error'] == error
    assert result['critic_failures'] == {position: error for position in (0, 4, 5)}
    assert not result['story_failure']


@pytest.mark.parametrize('damage', ['none', 'story', 'language', 'ending', 'scene', 'structural', 'order'])
def test_actual_immutable_director_uses_same_detached_verdict_with_no_writer_or_retry(case, monkeypatch, damage):
    captured = []
    def response(prompt, **kwargs):
        value = _shape(prompt)
        if damage == 'story': value['story_review']['causal_claim_supported'] = False
        elif damage == 'language':
            value['story_review'].update(natural_spoken_language=False,
                natural_spoken_language_evidence='Scene 1 "Members pay" is awkward.')
        elif damage == 'ending': value['ending_pair']['same_actor_or_object_thread'] = False
        elif damage == 'scene': value['scenes'][0]['common_stock_clip_feasible'] = False
        elif damage == 'structural': value['ending_pair']['final_position'] = True
        elif damage == 'order': value['scenes'].reverse()
        return value
    def validate_actual(*args, **kwargs):
        result = semantics.validate_stock_story_critic(*args, **kwargs)
        captured.append(deepcopy(result))
        return result
    case.model.side_effect = response
    monkeypatch.setattr(director, 'validate_stock_story_critic', validate_actual)
    before = deepcopy(case.package)
    if damage in {'none', 'order'}:
        output = _review(case)
        result = captured[0]
        assert output['stock_scene_qc']['reviews'] == list(result['parsed_reviews'].values())
        assert output['stock_scene_qc']['ending_pair_review']['location_anchor'] == result['ending_location_anchor'][:120]
    else:
        with pytest.raises(RuntimeError): _review(case)
        result = captured[0]
        assert result['critic_global_error'] or result['story_failure'] or result['critic_failures']
    assert case.package == before and case.model.call_count == 1
    assert len(captured) == 1 and case.events == []
    director._run_director.assert_not_called()


def test_pure_module_imports_only_standard_library_and_has_no_approval_factory():
    tree = ast.parse(Path(semantics.__file__).read_text())
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    imports |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert imports == {'copy', 're'}
    assert not hasattr(semantics, '_IncludedStoryApproval')
