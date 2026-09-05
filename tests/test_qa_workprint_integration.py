"""Execute the real terminal rejection branch without any provider calls."""
import ast
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


SOURCE = Path(__file__).resolve().parents[1] / 'app' / 'tasks.py'
TASK = '11111111-1111-4111-8111-111111111111'


class FinalVisualQualityError(RuntimeError):
    pass


@pytest.fixture
def case(monkeypatch, tmp_path):
    pointer = {
        'version':1, 'task_id':TASK, 'status':'qa_workprint',
        'qa_approved':False, 'publish_eligible':False, 'reusable':False,
        'key':f'qa_workprints/{TASK}/draft.mp4', 'sha256':'a'*64,
    }
    helper = Mock(return_value={'qa_workprint':pointer})
    monkeypatch.setitem(sys.modules, 'app.services.qa_workprint', SimpleNamespace(persist_qa_workprint=helper))
    job = {'state':'STARTED', 'stage':'final_visual_qc_rescue', 'paid_create_slots_used':0,
           'preview_total_paid_create_cap':4, 'result':{}, 'repair_claimed':False}
    writes = []
    def update(task_id, **fields):
        assert task_id == TASK
        writes.append(deepcopy(fields))
        job.update(deepcopy(fields))
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == '_checkpoint_qa_workprint')
    pipeline = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'run_video_pipeline')
    branch = next(node for node in ast.walk(pipeline) if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == 'rejected_final_scenes')
    namespace = {'Path':Path, 'update_job':update, 'json':json, 'FinalVisualQualityError':FinalVisualQualityError}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), 'exec'), namespace)
    gate = {'available':True, 'pass':True}
    kwargs = dict(
        duration_minutes=0.5,
        scenes=[{'narration':f'Scene {index}.'} for index in range(6)],
        scene_visuals=[[
            {'path':str(tmp_path/f'scene-{index}-a.mp4'), 'start_fraction':0.0},
            {'path':str(tmp_path/f'scene-{index}-b.mp4'), 'start_fraction':0.1},
            {'path':str(tmp_path/f'scene-{index}-c.mp4'), 'start_fraction':0.2},
        ] for index in range(6)],
        final_reviews={index:{'scene_index':index, 'best_candidate_index':2 if index == 4 else 1,
                              'best_start_fraction':0.45, 'score':65 if index == 4 else 90} for index in range(6)},
        voice_result={'path':str(tmp_path/'recovered_voice.mp3'), 'scene_durations':[4.85]*6},
        scene_durations=[4.85]*6, narration='Narration matches the existing voice.',
        options={'mode':'production','format':'shorts','music':'off','publish_after_render':True},
        audio_qc=dict(gate), audio_duration_qc=dict(gate), audio_prosody_qc=dict(gate),
    )
    return SimpleNamespace(helper=helper, pointer=pointer, job=job, writes=writes,
        function=namespace['_checkpoint_qa_workprint'], kwargs=kwargs, work=tmp_path,
        namespace=namespace, branch=branch, tree=tree)


def _call(case):
    case.function(TASK, case.work, **case.kwargs)


def test_draft_is_separate_unapproved_field_and_preserves_exact_rejected_best(case):
    before, arguments = deepcopy(case.job), deepcopy(case.kwargs)
    _call(case)
    case.helper.assert_called_once()
    passed = case.helper.call_args.kwargs
    assert passed['final_reviews'][4]['best_candidate_index'] == 2
    assert passed['scene_visuals'][4] == arguments['scene_visuals'][4]
    assert passed['final_reviews'][4]['score'] == 65
    assert passed['voice_quality_passed'] is True
    assert passed['target_seconds'] == 30.0
    assert case.writes == [{'qa_workprint':case.pointer}]
    assert {key:value for key,value in case.job.items() if key != 'qa_workprint'} == before
    assert case.kwargs == arguments


def test_already_collapsed_selected_clip_is_not_replaced_by_another_candidate(case):
    chosen = deepcopy(case.kwargs['scene_visuals'][0][1])
    chosen['start_fraction'] = 0.45
    case.kwargs['scene_visuals'][0] = [chosen]
    _call(case)
    passed = case.helper.call_args.kwargs
    assert passed['scene_visuals'][0] == [chosen]
    assert passed['final_reviews'][0]['best_candidate_index'] == 1


@pytest.mark.parametrize('field,value', [('mode','preview'),('format','landscape'),('music','auto')])
def test_non_scoped_modes_never_render_or_store(case, field, value):
    case.kwargs['options'][field] = value
    _call(case)
    case.helper.assert_not_called()
    assert not case.writes


@pytest.mark.parametrize('duration', [True, 0.25, 1.0, '0.5'])
def test_only_exact_thirty_second_requests_can_create_draft(case, duration):
    case.kwargs['duration_minutes'] = duration
    _call(case)
    case.helper.assert_not_called()
    assert not case.writes


@pytest.mark.parametrize('gate', ['audio_qc','audio_duration_qc','audio_prosody_qc'])
@pytest.mark.parametrize('damage', [{'available':False,'pass':True}, {'available':True,'pass':False}, {'available':1,'pass':1}, {}])
def test_all_real_audio_gates_are_mandatory(case, gate, damage):
    case.kwargs[gate] = damage
    _call(case)
    case.helper.assert_not_called()
    assert not case.writes


def test_helper_cannot_mutate_render_specs_reviews_audio_or_options(case):
    original = deepcopy(case.kwargs)
    def mutate(*_args, **kwargs):
        kwargs['scene_visuals'][4].clear()
        kwargs['final_reviews'][4]['score'] = 100
        kwargs['voice_result']['path'] = 'different'
        kwargs['options']['publish_after_render'] = False
        kwargs['scenes'][0]['narration'] = 'Changed'
        kwargs['scene_durations'][0] = 0
        return {'qa_workprint':case.pointer, 'result':{'video_key':'not-allowed'}, 'state':'SUCCESS'}
    case.helper.side_effect = mutate
    _call(case)
    assert case.kwargs == original
    assert case.writes == [{'qa_workprint':case.pointer}]
    assert case.job['state'] == 'STARTED' and case.job['result'] == {}


@pytest.mark.parametrize('damage', ['missing','available_flag','published_flag','reusable_flag','wrong_task','wrong_version','helper_failure','store_failure'])
def test_unavailable_or_unsafe_draft_cannot_change_failure_or_spend(case, damage):
    if damage == 'missing': case.helper.return_value = {}
    if damage == 'available_flag': case.pointer['qa_approved'] = True
    if damage == 'published_flag': case.pointer['publish_eligible'] = True
    if damage == 'reusable_flag': case.pointer['reusable'] = True
    if damage == 'wrong_task': case.pointer['task_id'] = 'other'
    if damage == 'wrong_version': case.pointer['version'] = True
    if damage == 'helper_failure': case.helper.side_effect = RuntimeError('SECRET provider body')
    if damage == 'store_failure': case.namespace['update_job'] = Mock(side_effect=RuntimeError('SECRET storage body'))
    before = deepcopy(case.job)
    _call(case)
    assert case.job == before
    assert not case.writes


@pytest.mark.parametrize('helper_fails', [False,True])
def test_actual_terminal_branch_still_raises_same_quality_failure_after_draft(case, helper_fails):
    diagnostic = {'accepted':5,'rejected':[{'scene_index':4,'score':65}], 'stage':'after_rescue'}
    case.namespace.update(case.kwargs)
    case.namespace.update(
        task_id=TASK, work=case.work, package={'narration':case.kwargs['narration']},
        rejected_final_scenes=[4], rescued_final_scenes=[4], is_bounded_short_preview=False,
        runway_attempts=0, runway_failed_scenes=[], final_runway_repair_failures=[], runway_failure_diagnostics=[],
        _final_visual_rejection_diagnostics=Mock(return_value=diagnostic),
    )
    if helper_fails: case.helper.side_effect = RuntimeError('SECRET failure')
    before = deepcopy(case.job)
    with pytest.raises(FinalVisualQualityError) as error:
        exec(compile(ast.Module(body=[case.branch], type_ignores=[]), str(SOURCE), 'exec'), case.namespace)
    assert str(error.value) == 'Final visual quality gate rejected: ' + json.dumps(diagnostic, ensure_ascii=False, separators=(',',':'))
    case.helper.assert_called_once()
    assert {key:value for key,value in case.job.items() if key != 'qa_workprint'} == before


def test_pipeline_hook_occurs_once_only_after_final_rescue_and_before_rejection(case):
    calls = [node for node in ast.walk(case.tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == '_checkpoint_qa_workprint']
    assert len(calls) == 1
    branch_calls = [node for node in ast.walk(case.branch) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
    names = [node.func.id for node in branch_calls]
    assert '_checkpoint_qa_workprint' in names
    assert not {'generate_scene','synthesize_scene_sequence','review_scene_visuals','mark_success','queue_render_for_automatic_publish'} & set(names)
    source = SOURCE.read_text(encoding='utf-8')
    rescue = source.index("work / 'final_visual_qc_rescue'")
    hook = source.index('_checkpoint_qa_workprint(', source.index('if rejected_final_scenes:'))
    error = source.index("'Final visual quality gate rejected: '", hook)
    assert rescue < hook < error
