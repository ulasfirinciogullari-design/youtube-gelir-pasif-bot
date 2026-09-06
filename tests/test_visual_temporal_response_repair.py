"""Same-frame protocol repair never invents proof or makes replacement media."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from test_visual_cross_provider_review import _namespace, _review, EVIDENCE


def _rows(namespace, count=1, *, moments=None, **overrides):
    return [_review(namespace, index=index, location_continuity_applicable=True,
                    location_continuity_matches=True,
                    evidence_moment_indices=[1] if moments is None else list(moments),
                    **overrides) for index in range(count)]


def _queue(namespace, provider, responses):
    namespace['settings'].studio_plan_provider = provider
    if provider == 'gemini':
        namespace['generate_gemini_multimodal_json'].side_effect = responses
        return namespace['generate_gemini_multimodal_json']
    namespace['OpenAI'].return_value.responses.create.side_effect = [
        response if isinstance(response, BaseException) else SimpleNamespace(
            status='completed', output_text=json.dumps(response)) for response in responses]
    return namespace['OpenAI'].return_value.responses.create


def _run(namespace, work, *, count=1, provider='openai', candidates=1, **options):
    scenes = [{'index': index, 'narration': 'A customer browses the same shop.',
               'visual_queries': ['customer inside shop'], 'ai_prompt': 'Customer inside the authored shop.'}
              for index in range(count)]
    return namespace['review_scene_visuals'](
        scenes, [[{'path': f'scene-{index}-candidate-{candidate}.mp4'} for candidate in range(candidates)]
                 for index in range(count)], work, count, _missing_review_attempts=0,
        topic='The authored store and its customers.', story_scenes=scenes,
        content_style='documentary', evidence_sources=EVIDENCE, provider_override=provider, **options)


@pytest.mark.parametrize('provider,count', [('openai', 6), ('gemini', 3)])
def test_whole_response_repair_reuses_identical_frames_context_and_ids_once(tmp_path, provider, count):
    namespace = _namespace()
    initial = _rows(namespace, count)
    repaired = _rows(namespace, count, moments=[3, 4])
    call = _queue(namespace, provider, [{'reviews': initial}, {'reviews': repaired}])
    original = deepcopy(initial)
    result = _run(namespace, tmp_path, provider=provider, count=count)
    assert call.call_count == 2 and namespace['_frame'].call_count == count * 5
    assert initial == original  # no fabricated indices inserted into the original verdict
    first, second = call.call_args_list
    if provider == 'gemini':
        assert first.args[0] == second.args[0]
        assert first.kwargs['json_schema'] == second.kwargs['json_schema']
        assert second.kwargs['retry_once'] is False
        prompt = second.kwargs['system_instruction']
        namespace['OpenAI'].assert_not_called()
    else:
        assert first.kwargs['input'] == second.kwargs['input']
        assert first.kwargs['model'] == second.kwargs['model']
        prompt = second.kwargs['instructions']
        assert all(item.kwargs['max_retries'] == 0 for item in namespace['OpenAI'].call_args_list)
        namespace['generate_gemini_multimodal_json'].assert_not_called()
    assert 'SAME-FRAME TEMPORAL RESPONSE REPAIR (ONE ATTEMPT)' in prompt
    assert 'required_distinct_moments":2' in prompt
    assert 'available_selected_candidate_moment_ids":[3,0,1,2,4]' in prompt
    assert 'The authored store and its customers.' in str(second)
    assert 'SOURCE MARKER' in str(second)
    assert len(result['reviews']) == count and not result['missing_review_indices']
    for index, row in enumerate(result['reviews']):
        assert row['scene_index'] == index and row['best_candidate_index'] == 0 and row['best_moment_index'] == 1
        assert row['evidence_moment_indices'] == [3, 4] and row['score'] == 92
        assert row['temporal_response_initial_evidence_moment_indices'] == [1]
        assert row['temporal_response_repair_complete'] is True
        assert row['evidence_gate_passed'] is row['identity_gate_passed'] is row['editorial_gate_passed'] is True


@pytest.mark.parametrize('provider', ['openai', 'gemini'])
def test_physical_causality_requires_three_actual_moments_not_two(tmp_path, provider):
    namespace = _namespace()
    kwargs = {'physical_causality_applicable': True, 'target_contact_visible': True}
    initial = _rows(namespace, **kwargs)
    repaired = _rows(namespace, moments=[3, 1, 4], **kwargs)
    call = _queue(namespace, provider, [{'reviews': initial}, {'reviews': repaired}])
    row = _run(namespace, tmp_path, provider=provider)['reviews'][0]
    assert row['score'] == 92 and row['evidence_moment_indices'] == [3, 1, 4]
    assert row['temporal_response_required_moments'] == 3
    assert 'required_distinct_moments":3' in str(call.call_args)


@pytest.mark.parametrize('provider', ['openai', 'gemini'])
@pytest.mark.parametrize('problem', ['still_one', 'invented_id', 'duplicates', 'boolean_id', 'missing',
    'extra_scene', 'changed_scene', 'changed_candidate', 'changed_moment', 'applicability_downgrade',
    'identity_changed', 'string_score', 'missing_flag', 'error'])
def test_bad_second_response_never_repairs_or_triggers_another_request(tmp_path, provider, problem):
    namespace = _namespace()
    initial = _rows(namespace)
    repaired = _rows(namespace, moments=[0, 2])
    row = repaired[0]
    if problem == 'still_one': row['evidence_moment_indices'] = [1]
    elif problem == 'invented_id': row['evidence_moment_indices'] = [1, 8]
    elif problem == 'duplicates': row['evidence_moment_indices'] = [1, 1]
    elif problem == 'boolean_id': row['evidence_moment_indices'] = [True, 2]
    elif problem == 'missing': repaired = []
    elif problem == 'extra_scene': repaired.append(deepcopy(row))
    elif problem == 'changed_scene': row['scene_index'] = 3
    elif problem == 'changed_candidate': row['best_candidate_index'] = 1
    elif problem == 'changed_moment': row['best_moment_index'] = 2
    elif problem == 'applicability_downgrade': row['location_continuity_applicable'] = False
    elif problem == 'identity_changed': row['authored_identity_or_material_conflict_visible'] = True
    elif problem == 'string_score': row['score'] = '92'
    elif problem == 'missing_flag': row.pop('major_visual_artifact_visible')
    response = RuntimeError('private provider error') if problem == 'error' else {'reviews': repaired}
    call = _queue(namespace, provider, [{'reviews': initial}, response])
    row = _run(namespace, tmp_path, provider=provider, candidates=2)['reviews'][0]
    assert row['score'] == 40
    if problem == 'identity_changed':
        # Real new negative identity evidence survives, even when its actual
        # temporal proof is complete. The overall scene remains rejected.
        assert row['authored_identity_or_material_conflict_visible'] is True
        assert row['identity_gate_passed'] is False
        assert row['evidence_moment_indices'] == [0, 2]
        assert row['temporal_response_preserved_negative'] is True
    else:
        assert row['evidence_gate_passed'] is False
        assert row['evidence_moment_indices'] == [1]
    assert row['temporal_response_repair_complete'] is False and call.call_count == 2
    assert 'private provider error' not in str(row)


@pytest.mark.parametrize('provider', ['openai', 'gemini'])
@pytest.mark.parametrize('field,value', [('major_visual_artifact_visible', True),
    ('prominent_readable_text_or_logo_visible', True), ('effectively_static_or_frozen', True),
    ('subject_visible', False), ('spoken_action_visible', False), ('location_continuity_matches', False),
    ('authored_identity_or_material_conflict_visible', True)])
def test_existing_genuine_rejection_is_not_eligible_for_temporal_response_repair(tmp_path, provider, field, value):
    namespace = _namespace()
    initial = _rows(namespace)
    initial[0][field] = value
    call = _queue(namespace, provider, [{'reviews': initial}])
    row = _run(namespace, tmp_path, provider=provider)['reviews'][0]
    assert row['score'] == 40 and not row.get('temporal_response_repair_attempted')
    call.assert_called_once()


@pytest.mark.parametrize('field', ['major_visual_artifact_visible', 'prominent_readable_text_or_logo_visible'])
def test_newly_observed_fake_packaging_lettering_still_rejects_after_count_repair(tmp_path, field):
    namespace = _namespace()
    repaired = _rows(namespace, moments=[3, 4])
    repaired[0].update({field: True, 'reason': 'The foreground cracker box has fabricated garbled lettering.'})
    call = _queue(namespace, 'openai', [{'reviews': _rows(namespace)}, {'reviews': repaired}])
    row = _run(namespace, tmp_path)['reviews'][0]
    assert row[field] is True and row['score'] == 40 and row['editorial_gate_passed'] is False
    assert row['evidence_moment_indices'] == [3, 4]  # actual fresh response, not a fabricated pass
    assert 'garbled lettering' in row['reason'] and call.call_count == 2


def test_adjacent_context_selection_change_invalidates_repair_even_if_target_is_unchanged(tmp_path):
    namespace = _namespace()
    initial = _rows(namespace, 2)
    initial[1]['evidence_moment_indices'] = [0, 2]
    revised = _rows(namespace, 2, moments=[0, 2])
    revised[1]['best_moment_index'] = 2
    call = _queue(namespace, 'openai', [{'reviews': initial}, {'reviews': revised}])
    rows = _run(namespace, tmp_path, count=2)['reviews']
    assert rows[0]['score'] == 40 and rows[0]['temporal_response_repair_complete'] is False
    assert rows[1]['score'] == 92 and rows[1]['best_moment_index'] == 1 and call.call_count == 2


def test_partial_batch_repair_keeps_remaining_incomplete_review_rejected(tmp_path):
    namespace = _namespace()
    initial = _rows(namespace, 2)
    revised = _rows(namespace, 2, moments=[0, 2])
    revised[1]['evidence_moment_indices'] = [1]
    _queue(namespace, 'openai', [{'reviews': initial}, {'reviews': revised}])
    rows = _run(namespace, tmp_path, count=2)['reviews']
    assert [row['score'] for row in rows] == [92, 40]
    assert [row['temporal_response_repair_complete'] for row in rows] == [True, False]


@pytest.mark.parametrize('change', ['artifact', 'identity', 'applicability', 'soft_rejection'])
def test_new_neighbor_rejection_cannot_be_discarded_to_complete_target_proof(tmp_path, change):
    namespace = _namespace()
    initial = _rows(namespace, 2)
    initial[1]['evidence_moment_indices'] = [0, 2]
    revised = _rows(namespace, 2, moments=[0, 2])
    if change == 'artifact': revised[1]['major_visual_artifact_visible'] = True
    elif change == 'identity': revised[1]['authored_identity_or_material_conflict_visible'] = True
    elif change == 'applicability': revised[1]['location_continuity_applicable'] = False
    else: revised[1]['score'] = 68
    call = _queue(namespace, 'openai', [{'reviews': initial}, {'reviews': revised}])
    rows = _run(namespace, tmp_path, count=2)['reviews']
    assert rows[0]['score'] == 40 and rows[0]['temporal_response_repair_complete'] is False
    neighbor = rows[1]
    if change == 'applicability':
        assert neighbor['location_continuity_applicable'] is True
    else:
        assert neighbor['score'] == (68 if change == 'soft_rejection' else 40)
        assert neighbor['temporal_response_repair_complete'] is False
        assert neighbor['temporal_response_preserved_negative'] is True
        if change == 'artifact':
            assert neighbor['major_visual_artifact_visible'] is True
            assert neighbor['editorial_gate_passed'] is False
        elif change == 'identity':
            assert neighbor['authored_identity_or_material_conflict_visible'] is True
            assert neighbor['identity_gate_passed'] is False
    assert call.call_count == 2


def test_existing_neighbor_negative_is_not_cleared_when_new_negative_is_preserved(tmp_path):
    namespace = _namespace()
    initial = _rows(namespace, 2)
    initial[1].update(evidence_moment_indices=[0, 2], major_visual_artifact_visible=True)
    revised = _rows(namespace, 2, moments=[0, 2])
    revised[1].update(major_visual_artifact_visible=False,
                      authored_identity_or_material_conflict_visible=True,
                      location_continuity_applicable=False)
    call = _queue(namespace, 'openai', [{'reviews': initial}, {'reviews': revised}])
    rows = _run(namespace, tmp_path, count=2)['reviews']
    assert rows[0]['score'] == 40 and rows[0]['temporal_response_repair_complete'] is False
    neighbor = rows[1]
    assert neighbor['score'] == 40
    assert neighbor['major_visual_artifact_visible'] is True
    assert neighbor['authored_identity_or_material_conflict_visible'] is True
    assert neighbor['location_continuity_applicable'] is True
    assert neighbor['identity_gate_passed'] is neighbor['editorial_gate_passed'] is False
    assert neighbor['temporal_response_repair_complete'] is False
    assert neighbor['temporal_response_preserved_negative'] is True
    assert call.call_count == 2


def test_positive_reinspection_cannot_erase_existing_neighbor_rejection(tmp_path):
    namespace = _namespace()
    initial = _rows(namespace, 2)
    initial[1].update(evidence_moment_indices=[0, 2], major_visual_artifact_visible=True)
    revised = _rows(namespace, 2, moments=[0, 2])
    call = _queue(namespace, 'openai', [{'reviews': initial}, {'reviews': revised}])
    rows = _run(namespace, tmp_path, count=2)['reviews']
    assert rows[0]['score'] == 92  # only the incomplete temporal response changed
    assert rows[1]['score'] == 40 and rows[1]['major_visual_artifact_visible'] is True
    assert rows[1]['editorial_gate_passed'] is False and call.call_count == 2


def test_soft_score_after_valid_proof_cannot_start_consistency_retry_loop(tmp_path):
    namespace = _namespace()
    repaired = _rows(namespace, moments=[0, 2])
    repaired[0]['score'] = 68
    call = _queue(namespace, 'openai', [{'reviews': _rows(namespace)}, {'reviews': repaired}])
    row = _run(namespace, tmp_path)['reviews'][0]
    assert row['score'] == 68 and row['temporal_response_repair_complete'] is False
    assert row['temporal_response_preserved_negative'] is True
    assert not row.get('score_reason_revalidation_attempted') and call.call_count == 2


def test_disable_option_keeps_existing_rejection_without_extra_provider_cost(tmp_path):
    namespace = _namespace()
    call = _queue(namespace, 'openai', [{'reviews': _rows(namespace)}])
    row = _run(namespace, tmp_path, _temporal_response_repair_attempts=0)['reviews'][0]
    assert row['score'] == 40 and not row.get('temporal_response_repair_attempted')
    call.assert_called_once()


@pytest.mark.parametrize('provider', ['openai', 'gemini'])
def test_fewer_available_frames_than_required_does_not_request_invented_evidence(tmp_path, provider):
    namespace = _namespace()
    original = namespace['_frame'].side_effect
    namespace['_frame'].side_effect = lambda path, output, fraction: original(path, output, fraction) if fraction == .5 else None
    call = _queue(namespace, provider, [{'reviews': _rows(namespace)}])
    row = _run(namespace, tmp_path, provider=provider)['reviews'][0]
    assert row['score'] == 40 and row['evidence_moment_indices'] == [1]
    call.assert_called_once()


@pytest.mark.parametrize('provider', ['openai', 'gemini'])
def test_initial_rubric_explicitly_states_conditional_temporal_minimums_and_natural_print_scope(tmp_path, provider):
    namespace = _namespace()
    call = _queue(namespace, provider, [{'reviews': _rows(namespace, moments=[0, 2])}])
    _run(namespace, tmp_path, provider=provider)
    prompt = call.call_args.kwargs['system_instruction' if provider == 'gemini' else 'instructions']
    assert 'location_continuity_applicable=true, cite at least 2' in prompt
    assert 'cite at least 3 distinct supplied moments' in prompt
    assert 'For source-backed documentary scenes only' in prompt
    assert 'specifically authored store or product name physically' in prompt
    assert 'factual identity must match the authored scene and sources' in prompt
    assert 'clean, legible and stable' in prompt
    assert 'unrelated advertising' in prompt and 'watermarks' in prompt
    assert 'fake, morphing or garbled AI print' in prompt
    assert 'earlier score never clears any visual flag' in prompt
