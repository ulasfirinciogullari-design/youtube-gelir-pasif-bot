"""Regression for the actual Capital-3 Redis-cjson wire representation.

fakeredis preserves empty JSON arrays, unlike the observed production Redis
roundtrip. This fixture therefore explicitly uses the observed {} wire value;
it does not misrepresent fakeredis as reproducing that serialization detail.
"""
from copy import deepcopy

import pytest

from app.services.fresh_story_binding import fresh_scheduled_spec_matches as matches
from app.services.production_editorial import choose_production_editorial
from test_fresh_scheduled_shot_prompt_worker import case, _eligible


@pytest.fixture
def bound(case):
    c = case
    c.spec['production_editorial'] = choose_production_editorial('Euro banknotlarındaki köprüler neden hayali?')
    c.spec['reference_url'] = None
    c.job['spec'] = deepcopy(c.spec)
    c.job['spec']['production_editorial']['scope_signals'] = {}
    return c


def test_actual_observed_empty_object_roundtrip_enters_existing_fresh_gate_without_mutation(bound):
    c = bound
    before = deepcopy((c.job, c.spec))
    assert c.job['spec'] != c.spec
    assert c.job['spec']['production_editorial']['scope_signals'] == {}
    assert c.spec['production_editorial']['scope_signals'] == []
    assert matches(c.job['spec'], c.spec) is True
    assert _eligible(c) is True
    assert (c.job, c.spec) == before
    c.ensure.assert_not_called(); c.client.set.assert_not_called()


def test_existing_exact_equality_and_nonempty_signal_lists_keep_original_behavior(bound):
    for signals in ([], ['cost'], {}):
        spec = deepcopy(bound.spec)
        spec['production_editorial']['scope_signals'] = signals
        assert matches(deepcopy(spec), spec) is True


@pytest.mark.parametrize('saved,expected', [([], {}), ({'cost': True}, []), ({}, ['cost']),
                                          (['cost'], []), (None, []), ({}, None), ('[]', []), ({}, '[]')])
def test_no_reverse_or_nonempty_or_other_type_coercion(bound, saved, expected):
    bound.job['spec']['production_editorial']['scope_signals'] = saved
    bound.spec['production_editorial']['scope_signals'] = expected
    assert matches(bound.job['spec'], bound.spec) is False
    with pytest.raises(RuntimeError, match='^Fresh scheduled storyboard binding could not be verified$'):
        _eligible(bound)


@pytest.mark.parametrize('field,value', [('topic', 'Different topic'), ('language', 'tr'),
    ('production_channel_id', 'different-channel'), ('production_connection_id', 'different-connection'),
    ('production_profile_revision', 'changed-revision'), ('production_topic_index', 7),
    ('reference_url', 'https://example.test/changed'), ('duration_minutes', 1), ('publish_after_render', False)])
def test_every_other_frozen_field_mismatch_remains_terminal(bound, field, value):
    bound.job['spec'][field] = value
    assert matches(bound.job['spec'], bound.spec) is False
    with pytest.raises(RuntimeError, match='^Fresh scheduled storyboard binding could not be verified$'):
        _eligible(bound)


@pytest.mark.parametrize('field,value', [('version', 2), ('format', 'landscape'), ('duration_minutes', 3),
    ('reason_code', 'unknown'), ('reason', 'Different explanation'), ('unexpected', [])])
def test_editorial_metadata_other_than_empty_array_is_not_repaired(bound, field, value):
    bound.job['spec']['production_editorial'][field] = value
    assert matches(bound.job['spec'], bound.spec) is False


@pytest.mark.parametrize('damage', ['missing', 'extra', 'unknown_version', 'long_form', 'unknown_reason', 'empty_reason', 'bool_version'])
def test_empty_array_exception_requires_the_known_scheduler_metadata_contract(bound, damage):
    for spec in (bound.job['spec'], bound.spec):
        editorial = spec['production_editorial']
        if damage == 'missing': editorial.pop('reason')
        elif damage == 'extra': editorial['extra'] = 'not a scheduler field'
        elif damage == 'unknown_version': editorial['version'] = 2
        elif damage == 'long_form': editorial['format'] = 'landscape'
        elif damage == 'unknown_reason': editorial['reason_code'] = 'unrecognized'
        elif damage == 'empty_reason': editorial['reason'] = ''
        elif damage == 'bool_version': editorial['version'] = True
    assert matches(bound.job['spec'], bound.spec) is False


@pytest.mark.parametrize('field,value', [('parent_id', 'old-source'), ('result', {}), ('state', 'FAILURE'),
                                      ('audio_candidate_checkpoint', {}), ('qa_workprint', {})])
def test_transport_compatibility_does_not_waive_existing_job_eligibility(bound, field, value):
    bound.job[field] = value
    assert _eligible(bound) is False


@pytest.mark.parametrize('changes', [{'paid_slots_used': 1}, {'paid_slots_used': False},
                                   {'approved_package': {}}, {'retry_dispatch_source_id': 'old-source'},
                                   {'curated_stock_manifest': {}}, {'voice_replacement_source_id': 'old-source'}])
def test_transport_compatibility_does_not_opt_in_paid_or_ordinary_recovery(bound, changes):
    assert _eligible(bound, **changes) is False
    bound.registry.get_job.assert_not_called()


def test_arrays_at_any_other_path_still_require_exact_equality(bound):
    bound.spec['some_other_array'] = []
    bound.job['spec']['some_other_array'] = {}
    assert matches(bound.job['spec'], bound.spec) is False


@pytest.mark.parametrize('reason', ['focused_or_unspecified_scope', 'explicit_short_direction'])
def test_both_actual_short_editorial_decisions_allow_only_the_observed_roundtrip(bound, reason):
    for spec in (bound.job['spec'], bound.spec):
        spec['production_editorial']['reason_code'] = reason
    assert _eligible(bound) is True
