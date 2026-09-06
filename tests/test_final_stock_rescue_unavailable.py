"""Execute the real final-rescue loop and terminal branch without providers."""
import ast
from copy import deepcopy
import json
from pathlib import Path
import re
from unittest.mock import Mock

import httpx
import pytest


SOURCE = Path(__file__).resolve().parents[1] / 'app' / 'tasks.py'
TASK = '11111111-1111-4111-8111-111111111111'


def _boundary(tmp_path):
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    pipeline = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == 'run_video_pipeline')
    loop = next(node for node in ast.walk(pipeline) if isinstance(node, ast.For)
                and isinstance(node.iter, ast.Name) and node.iter.id == 'rejected_final_scenes')
    branch = next(node for node in ast.walk(pipeline) if isinstance(node, ast.If)
                  and isinstance(node.test, ast.Name) and node.test.id == 'rejected_final_scenes')
    definitions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))
                   and node.name in {'_retry_bad_scene', 'PexelsRetryError', 'FinalVisualQualityError',
                                     '_final_visual_rejection_diagnostics'}]
    writes = []
    job = {'paid_create_slots_used': 6, 'preview_total_paid_create_cap': 6,
           'generated_asset_candidates': {'preserved_count': 6},
           'audio_candidate_checkpoint': {'audio_sha256': 'a' * 64},
           'qa_approved': False, 'state': 'PROGRESS', 'result': None}

    def update(task_id, **fields):
        assert task_id == TASK
        writes.append(deepcopy(fields))
        job.update(deepcopy(fields))

    ns = {'Path': Path, 're': re, 'httpx': httpx, 'json': json,
          '_download_ranked_broll_candidates': Mock(), 'update_job': update,
          '_final_pexels_rescue_queries': Mock(return_value=['relevant query']),
          '_visual_path': lambda item: item['path'],
          'task_id': TASK, 'work': tmp_path,
          'scenes': [{'narration': f'Original narration {i}.'} for i in range(6)],
          'scene_visuals': [[{'path': str(tmp_path / f'paid-{i}.mp4'), 'generated': True}]
                            for i in range(6)],
          'final_reviews': {i: {'scene_index': i, 'score': 45 if i in (1, 4) else 92,
                               'reason': 'Required action missing.' if i in (1, 4) else 'Visible.',
                               'evidence_gate_passed': i not in (1, 4)} for i in range(6)},
          'rejected_final_scenes': [1, 4], 'rescued_final_scenes': [],
          'recovered_generated_media': None, 'final_runway_repair_scenes': [],
          'provider_outage_stock_scenes': set(), 'stock_quality_fallback_scenes': set(),
          'terminal_manual_qa_old_best': {}, 'seen_ids': set(), 'credits': [],
          'scene_durations': [4.9] * 6, 'is_bounded_short_preview': True,
          'pexels_orientation': 'portrait', 'stock_reuse_visuals': None,
          'runway_generated_scenes': set(range(6)), 'visual_replacements': [],
          'package': {'narration': 'Original narration.'},
          'voice_result': {'path': str(tmp_path / 'existing-voice.mp3')},
          'generated_checkpoint_specs': {i: [{'paid': i}] for i in range(6)},
          'staged_recovered_scenes': {'original': 'retained'},
          'staged_voice_contract': {'original': 'retained'},
          'runway_attempts': 6, 'runway_failed_scenes': [],
          'final_runway_repair_failures': [], 'runway_failure_diagnostics': [],
          '_persist_scene_repair_checkpoint': Mock(return_value=True),
          '_checkpoint_qa_workprint': Mock(), 'duration_minutes': 0.5,
          'options': {'mode': 'production', 'format': 'shorts'},
          'audio_qc': {'available': True, 'pass': True},
          'audio_duration_qc': {'available': True, 'pass': True},
          'audio_prosody_qc': {'available': True, 'pass': True}}
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(SOURCE), 'exec'), ns)
    return ns, loop, branch, job, writes


def _execute(node, ns):
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), ns)


def _http_error(status=403):
    request = httpx.Request('GET', 'https://api.pexels.com/videos/search?query=PRIVATE',
                            headers={'Authorization': 'PRIVATE'})
    response = httpx.Response(status, request=request, text='PRIVATE provider body')
    return httpx.HTTPStatusError('PRIVATE response details', request=request, response=response)


@pytest.mark.parametrize('status', [400, 401, 403, 404, 409, 422, 429, 500, 503])
def test_optional_http_failure_preserves_real_rejection_and_reaches_checkpoints(tmp_path, status):
    ns, loop, branch, job, writes = _boundary(tmp_path)
    before_job = deepcopy(job)
    before_reviews, before_visuals = deepcopy(ns['final_reviews']), deepcopy(ns['scene_visuals'])
    ns['_download_ranked_broll_candidates'].side_effect = _http_error(status)
    _execute(loop, ns)
    ns['_download_ranked_broll_candidates'].assert_called_once()
    assert ns['rescued_final_scenes'] == ns['visual_replacements'] == []
    assert ns['rejected_final_scenes'] == [1, 4]
    assert ns['final_reviews'] == before_reviews and ns['scene_visuals'] == before_visuals
    assert writes == [{'final_stock_rescue_unavailable': {
        'provider': 'pexels', 'scene_index': 1, 'status': 'unavailable', 'http_status': status}}]
    assert {key: value for key, value in job.items() if key != 'final_stock_rescue_unavailable'} == before_job
    assert 'PRIVATE' not in json.dumps(writes)

    with pytest.raises(ns['FinalVisualQualityError']) as error:
        _execute(branch, ns)
    assert str(error.value).startswith('Final visual quality gate rejected: ')
    diagnostics = json.loads(str(error.value).split(': ', 1)[1])
    assert diagnostics['accepted'] == 4 and diagnostics['replaced'] == 0
    assert diagnostics['rejected']['1']['score'] == diagnostics['rejected']['4']['score'] == 45
    assert diagnostics['repair_checkpoint_available'] is True
    ns['_persist_scene_repair_checkpoint'].assert_called_once()
    assert ns['_persist_scene_repair_checkpoint'].call_args.kwargs['rejected_scene_indices'] == [1, 4]
    ns['_checkpoint_qa_workprint'].assert_called_once()
    assert ns['_checkpoint_qa_workprint'].call_args.kwargs['final_reviews'] == before_reviews
    assert ns['_checkpoint_qa_workprint'].call_args.kwargs['scene_visuals'] == before_visuals
    assert ns['_checkpoint_qa_workprint'].call_args.kwargs['voice_result'] == ns['voice_result']


def test_diagnostic_store_error_cannot_mask_the_original_quality_failure(tmp_path):
    ns, loop, branch, job, writes = _boundary(tmp_path)
    ns['_download_ranked_broll_candidates'].side_effect = _http_error()
    ns['update_job'] = Mock(side_effect=RuntimeError('PRIVATE storage failure'))
    _execute(loop, ns)
    with pytest.raises(ns['FinalVisualQualityError'], match='Final visual quality gate rejected:'):
        _execute(branch, ns)
    assert writes == [] and job['paid_create_slots_used'] == 6
    ns['_checkpoint_qa_workprint'].assert_called_once()


@pytest.mark.parametrize('error', [PermissionError('disk'), ValueError('invariant'), RuntimeError('bug')])
def test_unrelated_local_errors_are_not_hidden(tmp_path, error):
    ns, loop, _branch, _job, writes = _boundary(tmp_path)
    ns['_download_ranked_broll_candidates'].side_effect = error
    with pytest.raises(type(error)) as raised:
        _execute(loop, ns)
    assert raised.value is error and writes == []
    ns['_checkpoint_qa_workprint'].assert_not_called()


def test_successful_rescue_still_reaches_existing_fresh_review_input(tmp_path):
    ns, loop, _branch, _job, writes = _boundary(tmp_path)
    replacement = {'path': str(tmp_path / 'genuine-stock.mp4'), 'source_type': 'stock'}
    ns['_download_ranked_broll_candidates'].return_value = [replacement]
    _execute(loop, ns)
    assert ns['rescued_final_scenes'] == [1, 4] and len(ns['visual_replacements']) == 2
    assert ns['scene_visuals'][1][1] == replacement
    assert ns['scene_visuals'][4][1] == replacement
    # Download success does not change a score or remove a rejection; later
    # ordinary exact-clip QA, outside this loop, still decides both candidates.
    assert ns['final_reviews'][1]['score'] == 45 and ns['rejected_final_scenes'] == [1, 4]
    assert writes == []


def test_preexisting_ai_repair_remains_scheduled_for_exact_qa(tmp_path):
    ns, loop, _branch, _job, _writes = _boundary(tmp_path)
    ns['final_runway_repair_scenes'] = [1]
    ns['rescued_final_scenes'] = [1]
    ns['_download_ranked_broll_candidates'].side_effect = _http_error()
    _execute(loop, ns)
    assert ns['rescued_final_scenes'] == [1]
    assert ns['rejected_final_scenes'] == [1, 4]
    assert ns['_download_ranked_broll_candidates'].call_args.args[0] == 4


@pytest.mark.parametrize('version', [4, 5])
def test_pinned_recovery_keeps_no_stock_substitution_contract(tmp_path, version):
    ns, loop, _branch, _job, writes = _boundary(tmp_path)
    ns['recovered_generated_media'] = {'version': version}
    _execute(loop, ns)
    ns['_download_ranked_broll_candidates'].assert_not_called()
    assert writes == [] and ns['rejected_final_scenes'] == [1, 4]
