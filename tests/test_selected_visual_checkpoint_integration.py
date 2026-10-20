"""Execute the worker's real preservation helper and terminal rejection branch."""
import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


SOURCE = Path(__file__).resolve().parents[1] / 'app' / 'tasks.py'
TASK = '11111111-1111-4111-8111-111111111111'
ROOT = '22222222-2222-4222-8222-222222222222'


class FinalVisualQualityError(RuntimeError):
    pass


@pytest.fixture
def case(monkeypatch, tmp_path):
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    definitions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                   and node.name in {'_checkpoint_selected_visuals', '_recovery_package_sha256'}]
    pipeline = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == 'run_video_pipeline')
    branch = next(node for node in ast.walk(pipeline) if isinstance(node, ast.If)
                  and isinstance(node.test, ast.Name) and node.test.id == 'rejected_final_scenes')
    job = {'state': 'PROGRESS', 'stage': 'final_visual_qc_rescue', 'result': None,
           'paid_create_slots_used': 4, 'preview_total_paid_create_cap': 4,
           'qa_approved': False, 'repair_claimed': False, 'retry_child_task_id': None}
    writes, events = [], []
    def update(task_id, **fields):
        assert task_id == TASK
        events.append('store')
        writes.append(deepcopy(fields))
        job.update(deepcopy(fields))
    namespace = {'Path': Path, 'hashlib': hashlib, 'json': json,
                 'update_job': update, 'FinalVisualQualityError': FinalVisualQualityError}
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(SOURCE), 'exec'), namespace)
    package = {'narration': 'Narration matches the existing voice.',
               'scenes': [{'narration': f'Scene {index}.'} for index in range(6)]}
    binding = {'source_task_id': TASK, 'lineage_id': ROOT,
               'channel_id': 'UC5v9AvNtD3PTLgo6m1jROOA', 'connection_id': 'connection_AAAAA',
               'ancestry_sha256': 'c' * 64}
    pointer = {
        'version': 1, 'status': 'preserved_selected_candidates', 'source_task_id': TASK,
        'binding': deepcopy(binding), 'package_sha256': namespace['_recovery_package_sha256'](package),
        'manifest_key': f'selected_visuals/{TASK}/manifest.json',
        'manifest_sha256': 'a' * 64, 'manifest_size': 4096,
        'accepted': False, 'qa_approved': False, 'publish_eligible': False,
        'reusable': False, 'qa_input_verified': False,
        'requires_full_qa': True, 'exact_cut_qa_required': True,
    }
    def resolve(_task_id):
        events.append('binding')
        return binding
    def preserve(*_args, **_kwargs):
        events.append('selected')
        return pointer
    resolver, persist = Mock(side_effect=resolve), Mock(side_effect=preserve)
    monkeypatch.setitem(sys.modules, 'app.services.selected_visual_context',
                        SimpleNamespace(resolve_selected_visual_binding=resolver))
    monkeypatch.setitem(sys.modules, 'app.services.selected_visual_checkpoint',
                        SimpleNamespace(persist_selected_visual_checkpoint=persist))
    gate = {'available': True, 'pass': True}
    kwargs = {
        'duration_minutes': 0.5, 'effective_edit_target_seconds': 29.1,
        'package': package,
        'scene_visuals': [[{'path': str(tmp_path / f'scene-{index}-{candidate}.mp4'),
                           'start_fraction': candidate / 10} for candidate in range(3)]
                         for index in range(6)],
        'final_reviews': {index: {'scene_index': index, 'best_candidate_index': 2 if index == 4 else 1,
                                  'best_start_fraction': 0.45, 'score': 65 if index == 4 else 90}
                          for index in range(6)},
        'rejected_scene_indices': [4], 'quality_threshold': 86,
        'voice_result': {'path': str(tmp_path / 'existing-voice.mp3'), 'scene_durations': [4.85] * 6},
        'scene_durations': [4.85] * 6,
        'options': {'mode': 'production', 'format': 'shorts', 'music': 'off', 'publish_after_render': True},
        'audio_qc': dict(gate), 'audio_duration_qc': dict(gate), 'audio_prosody_qc': dict(gate),
    }
    forbidden = {name: Mock(side_effect=AssertionError('Unexpected paid or approval side effect'))
                 for name in ('generate_scene', 'synthesize_scene_sequence', 'review_scene_visuals',
                              'render_video', 'reserve_request', 'mark_success',
                              'queue_render_for_automatic_publish', 'retry_task')}
    namespace.update(forbidden)
    return SimpleNamespace(tree=tree, branch=branch, namespace=namespace, kwargs=kwargs, work=tmp_path,
                           function=namespace['_checkpoint_selected_visuals'], binding=binding,
                           resolver=resolver, persist=persist, pointer=pointer,
                           job=job, writes=writes, events=events, forbidden=forbidden)


def _call(case):
    case.function(TASK, case.work, **case.kwargs)


def test_real_helper_preserves_selected_inputs_with_actual_edit_duration_and_bound_storyboard(case):
    original, before_job = deepcopy(case.kwargs), deepcopy(case.job)
    _call(case)
    case.resolver.assert_called_once_with(TASK)
    case.persist.assert_called_once_with(
        TASK, case.work, binding=case.binding,
        **{key: original[key] for key in ('package', 'voice_result', 'scene_visuals', 'final_reviews',
                                          'rejected_scene_indices', 'quality_threshold', 'options',
                                          'duration_minutes', 'effective_edit_target_seconds')},
    )
    assert case.persist.call_args.kwargs['effective_edit_target_seconds'] == 29.1
    assert case.persist.call_args.kwargs['final_reviews'][4]['best_candidate_index'] == 2
    assert case.persist.call_args.kwargs['final_reviews'][4]['score'] == 65
    assert case.writes == [{'selected_visual_checkpoint': case.pointer}]
    assert {key: value for key, value in case.job.items() if key != 'selected_visual_checkpoint'} == before_job
    assert case.kwargs == original
    for forbidden in case.forbidden.values():
        forbidden.assert_not_called()


def test_already_collapsed_candidate_and_its_edit_offset_remain_unchanged(case):
    chosen = deepcopy(case.kwargs['scene_visuals'][0][1])
    chosen['start_fraction'] = 0.45
    case.kwargs['scene_visuals'][0] = [chosen]
    _call(case)
    assert case.persist.call_args.kwargs['scene_visuals'][0] == [chosen]
    assert case.persist.call_args.kwargs['final_reviews'][0]['best_candidate_index'] == 1


@pytest.mark.parametrize('field,value', [('mode', 'preview'), ('format', 'landscape'), ('music', 'auto')])
def test_other_modes_do_not_resolve_or_preserve(case, field, value):
    case.kwargs['options'][field] = value
    _call(case)
    case.resolver.assert_not_called()
    case.persist.assert_not_called()
    assert not case.writes


@pytest.mark.parametrize('duration', [True, 0.25, 1.0, '0.5', None, float('nan')])
def test_only_the_existing_thirty_second_request_scope_can_preserve(case, duration):
    case.kwargs['duration_minutes'] = duration
    _call(case)
    case.resolver.assert_not_called()
    case.persist.assert_not_called()
    assert not case.writes


@pytest.mark.parametrize('gate', ['audio_qc', 'audio_duration_qc', 'audio_prosody_qc'])
@pytest.mark.parametrize('damage', [{'available': False, 'pass': True}, {'available': True, 'pass': False},
                                    {'available': 1, 'pass': 1}, {}, None])
def test_all_three_current_audio_gates_must_be_available_and_pass(case, gate, damage):
    case.kwargs[gate] = damage
    _call(case)
    case.resolver.assert_not_called()
    case.persist.assert_not_called()
    assert not case.writes


@pytest.mark.parametrize('damage', ['different_duration', 'missing_voice_measurements', 'wrong_measurement_type',
                                    'missing_voice'])
def test_audio_measurements_must_equal_current_scene_durations(case, damage):
    if damage == 'different_duration':
        case.kwargs['voice_result']['scene_durations'][0] = 4.0
    elif damage == 'missing_voice_measurements':
        case.kwargs['voice_result'].pop('scene_durations')
    elif damage == 'wrong_measurement_type':
        case.kwargs['scene_durations'] = tuple(case.kwargs['scene_durations'])
    else:
        case.kwargs['voice_result'] = None
    _call(case)
    case.resolver.assert_not_called()
    case.persist.assert_not_called()
    assert not case.writes


def test_service_cannot_mutate_the_live_candidates_voice_binding_or_storyboard(case):
    original, original_binding = deepcopy(case.kwargs), deepcopy(case.binding)
    def mutate(*_args, **kwargs):
        kwargs['package']['scenes'][0]['narration'] = 'Changed'
        kwargs['binding']['channel_id'] = 'different-channel'
        kwargs['voice_result']['scene_durations'][0] = 0
        kwargs['scene_visuals'][4].clear()
        kwargs['final_reviews'][4]['score'] = 100
        kwargs['rejected_scene_indices'].clear()
        kwargs['options']['publish_after_render'] = False
        return case.pointer
    case.persist.side_effect = mutate
    _call(case)
    assert case.kwargs == original and case.binding == original_binding
    assert case.writes == [{'selected_visual_checkpoint': case.pointer}]
    case.pointer['binding']['channel_id'] = 'mutated-after-store'
    assert case.writes[0]['selected_visual_checkpoint']['binding'] == original_binding


@pytest.mark.parametrize('field,value', [
    ('version', True), ('version', 2), ('source_task_id', ROOT), ('status', 'approved'),
    ('package_sha256', 'b' * 64), ('binding', {}),
    *[(field, value) for field in ('accepted', 'qa_approved', 'publish_eligible', 'reusable', 'qa_input_verified')
      for value in (True, 0, None)],
    *[(field, value) for field in ('requires_full_qa', 'exact_cut_qa_required') for value in (False, 1, None)],
])
def test_unbound_or_approval_bearing_pointer_is_not_stored(case, field, value):
    case.pointer[field] = value
    before = deepcopy(case.job)
    _call(case)
    assert not case.writes and case.job == before


@pytest.mark.parametrize('field', ['source_task_id', 'lineage_id', 'channel_id', 'connection_id', 'ancestry_sha256'])
def test_pointer_must_match_each_server_resolved_identity(case, field):
    case.pointer['binding'][field] = 'd' * 64 if field == 'ancestry_sha256' else 'changed'
    _call(case)
    assert not case.writes


@pytest.mark.parametrize('pointer', [None, {}, [], {'state': 'SUCCESS', 'result': {'video_key': 'unapproved'}}])
def test_missing_or_unrelated_service_result_cannot_become_a_job_update(case, pointer):
    case.persist.side_effect = None
    case.persist.return_value = pointer
    before = deepcopy(case.job)
    _call(case)
    assert not case.writes and case.job == before


def test_changed_storyboard_rejects_a_pointer_bound_to_the_previous_package(case):
    case.kwargs['package']['scenes'][0]['narration'] = 'A changed editorial claim.'
    _call(case)
    case.persist.assert_called_once()
    assert not case.writes


@pytest.mark.parametrize('phase', ['binding', 'preservation', 'storage'])
def test_best_effort_failures_keep_job_and_spending_unchanged(case, phase):
    failure = RuntimeError('PRIVATE failure body')
    if phase == 'binding':
        case.resolver.side_effect = failure
    elif phase == 'preservation':
        case.persist.side_effect = failure
    else:
        case.namespace['update_job'] = Mock(side_effect=failure)
    before = deepcopy(case.job)
    _call(case)
    assert case.job == before and not case.writes
    for forbidden in case.forbidden.values():
        forbidden.assert_not_called()


def _terminal(case, diagnostic):
    case.namespace.update(case.kwargs)
    case.namespace.update(
        task_id=TASK, work=case.work, scenes=case.kwargs['package']['scenes'],
        rejected_final_scenes=[4], rescued_final_scenes=[4], is_bounded_short_preview=False,
        runway_attempts=4, runway_failed_scenes=[], final_runway_repair_failures=[], runway_failure_diagnostics=[],
        _final_visual_rejection_diagnostics=Mock(return_value=diagnostic),
        _checkpoint_qa_workprint=Mock(side_effect=lambda *_args, **_kwargs: case.events.append('workprint')),
    )
    exec(compile(ast.Module(body=[case.branch], type_ignores=[]), str(SOURCE), 'exec'), case.namespace)


@pytest.mark.parametrize('failure', [None, 'binding', 'preservation', 'storage'])
def test_actual_terminal_branch_preserves_original_rejection_after_preservation(case, failure):
    diagnostic = {'accepted': 5, 'total': 6, 'replaced': 1, 'stage': 'after_rescue',
                  'rejected': {'4': {'score': 65, 'reason': 'Action is not visible.'}}}
    private = RuntimeError('PRIVATE side-effect error')
    if failure == 'binding':
        case.resolver.side_effect = private
    elif failure == 'preservation':
        case.persist.side_effect = private
    elif failure == 'storage':
        case.namespace['update_job'] = Mock(side_effect=private)
    before = deepcopy(case.job)
    with pytest.raises(FinalVisualQualityError) as caught:
        _terminal(case, diagnostic)
    assert str(caught.value) == 'Final visual quality gate rejected: ' + json.dumps(
        diagnostic, ensure_ascii=False, separators=(',', ':'))
    case.namespace['_checkpoint_qa_workprint'].assert_called_once()
    assert case.events[-1] == 'workprint'
    if failure is None:
        assert case.events == ['binding', 'selected', 'store', 'workprint']
    assert {key: value for key, value in case.job.items() if key != 'selected_visual_checkpoint'} == before
    for forbidden in case.forbidden.values():
        forbidden.assert_not_called()


def test_inconsistent_final_diagnostics_do_not_create_a_checkpoint(case):
    with pytest.raises(FinalVisualQualityError, match='rejection state is inconsistent'):
        _terminal(case, None)
    case.resolver.assert_not_called()
    case.persist.assert_not_called()
    case.namespace['_checkpoint_qa_workprint'].assert_not_called()
    assert not case.writes


def test_preservation_hook_occurs_once_after_rescue_before_workprint_and_terminal_error(case):
    calls = [node for node in ast.walk(case.tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == '_checkpoint_selected_visuals']
    assert len(calls) == 1
    assert calls[0] in list(ast.walk(case.branch))
    source = SOURCE.read_text(encoding='utf-8')
    rescue = source.index("work / 'final_visual_qc_rescue'")
    start = source.index('if rejected_final_scenes:', rescue)
    diagnostic = source.index('diagnostics = _final_visual_rejection_diagnostics(', start)
    hook = source.index('_checkpoint_selected_visuals(', diagnostic)
    workprint = source.index('_checkpoint_qa_workprint(', hook)
    terminal = source.index("'Final visual quality gate rejected: '", workprint)
    assert rescue < diagnostic < hook < workprint < terminal
