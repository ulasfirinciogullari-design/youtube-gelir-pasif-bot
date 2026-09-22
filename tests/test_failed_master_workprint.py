"""Failed masters remain exact, private, playable diagnostics only."""
import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import failed_master_workprint as failed, qa_workprint_access as access, studio_state
from test_qa_workprint import case, TASK, MP4
from test_qa_workprint_access import pointer, request, store, consume, assert_private, DATA


@pytest.fixture
def master(case, monkeypatch):
    path = case.work / 'final.mp4'; path.write_bytes(MP4 + b'unchanged-failed-master')
    probe = Mock(return_value={'duration_seconds': 899 / 30, 'frame_count': 899, 'width': 1080, 'height': 1920})
    monkeypatch.setattr(failed, '_metrics', probe)
    return SimpleNamespace(case=case, path=path, probe=probe)


def test_wrong_frame_count_master_is_preserved_without_rendering_or_quality_approval(master):
    c = master.case; original = master.path.read_bytes()
    result = failed.persist(TASK, c.work); p = result['qa_workprint']
    assert p['version'] == 3 and p['frame_count'] == 899 and p['duration_seconds'] == 899 / 30
    assert p['sha256'] == hashlib.sha256(original).hexdigest()
    assert c.uploads[1]['Body'] == original and master.path.read_bytes() == original
    assert not c.renders and len(c.uploads) == 2
    metadata = json.loads(c.uploads[0]['Body'])
    assert metadata['source'] == 'existing_failed_master' and metadata['failure_stage'] == 'render'
    for field in ('qa_approved', 'publish_eligible', 'reusable'):
        assert p[field] is False and metadata[field] is False
    job = {'task_id': TASK, 'kind': 'render', 'state': 'FAILURE', 'qa_workprint': p}
    assert access.validated_pointer(job) == p
    assert access.validated_pointer({**job, 'state': 'SUCCESS'}) is None
    for text in (str(c.work), 'DO NOT EXPORT', 'authorization', 'result', 'https://'):
        assert text not in json.dumps(metadata)


@pytest.mark.parametrize('problem', ['missing', 'wrong_task', 'symlink', 'probe_failure', 'storage_failure'])
def test_missing_or_unverified_master_never_creates_a_preview(master, monkeypatch, problem):
    c = master.case
    if problem == 'missing': master.path.unlink()
    if problem == 'wrong_task': task = '22222222-2222-4222-8222-222222222222'
    else: task = TASK
    if problem == 'symlink':
        original = c.tmp / 'external.mp4'; original.write_bytes(master.path.read_bytes())
        master.path.unlink(); master.path.symlink_to(original)
    if problem == 'probe_failure': master.probe.side_effect = ValueError('secret diagnostic')
    if problem == 'storage_failure': monkeypatch.setattr(failed.workprints.storage, '_client', Mock(side_effect=RuntimeError('secret store')))
    assert failed.persist(task, c.work) == {}
    assert not c.renders and not c.uploads


def test_checkpoint_only_attaches_diagnostic_and_never_changes_error_budget_or_result(master, monkeypatch):
    job = {'task_id': TASK, 'kind': 'render', 'state': 'PROGRESS', 'stage': 'render',
           'result': {}, 'paid_create_slots_used': 4, 'error': None}
    before = deepcopy(job)
    monkeypatch.setattr(studio_state, 'get_job', lambda task: deepcopy(job))
    writes = Mock(); monkeypatch.setattr(studio_state, 'update_job', writes)
    failed.checkpoint(TASK, master.case.work, options={'mode': 'production', 'format': 'shorts'}, duration_minutes=.5)
    assert job == before and writes.call_count == 1
    assert writes.call_args.args == (TASK,)
    assert set(writes.call_args.kwargs) == {'qa_workprint'}
    assert writes.call_args.kwargs['qa_workprint']['publish_eligible'] is False


@pytest.mark.parametrize('change', [{'state': 'SUCCESS'}, {'stage': 'upload'}, {'kind': 'publish'},
                                  {'qa_workprint': {'old': 'preserve'}}, {'result': {'video_key': 'old'}}])
def test_existing_delivery_or_preview_is_never_replaced(master, monkeypatch, change):
    job = {'task_id': TASK, 'kind': 'render', 'state': 'PROGRESS', 'stage': 'render', **change}
    monkeypatch.setattr(studio_state, 'get_job', lambda task: deepcopy(job))
    writes = Mock(); monkeypatch.setattr(studio_state, 'update_job', writes)
    failed.checkpoint(TASK, master.case.work, options={'mode': 'production', 'format': 'shorts'}, duration_minutes=.5)
    writes.assert_not_called(); assert not master.case.uploads


@pytest.mark.parametrize('frames', [600, 803, 899, 900, 1200, 1350])
def test_v3_range_playback_uses_same_pinned_private_object(store, frames):
    p = {**pointer(), 'version': 3, 'frame_count': frames, 'duration_seconds': frames / 30}
    assert access.validated_pointer({'task_id': TASK, 'kind': 'render', 'state': 'FAILURE', 'qa_workprint': p}) == p
    response = access.stream_response(p, request(ranges=['bytes=4-20']))
    assert response.status_code == 206 and consume(response) == DATA[4:21]
    assert_private(response)


@pytest.mark.parametrize('frames,duration', [(599, 599/30), (1351, 1351/30), (899, 30), (900, float('nan'))])
def test_v3_bad_timeline_never_reaches_storage(store, frames, duration):
    p = {**pointer(), 'version': 3, 'frame_count': frames, 'duration_seconds': duration}
    assert access.stream_response(p, request()).status_code == 404
    store.client.get_object.assert_not_called()


def test_real_h264_aac_master_is_only_probed_not_modified(tmp_path):
    path = tmp_path / 'actual-failed-master.mp4'
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
        'color=c=blue:s=1080x1920:r=30:d=20.04', '-f', 'lavfi', '-i',
        'sine=frequency=440:sample_rate=48000:duration=20.04', '-map', '0:v', '-map', '1:a',
        '-c:v', 'libx264', '-preset', 'ultrafast', '-threads', '2', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-frames:v', '601', '-t', '20.033', str(path)], check=True, timeout=45)
    before = path.read_bytes(); result = failed._metrics(path)
    assert result == {'duration_seconds': 601/30, 'frame_count': 601, 'width': 1080, 'height': 1920}
    assert path.read_bytes() == before


def test_pipeline_calls_checkpoint_before_original_failure_handling_and_cleanup(monkeypatch):
    source = Path(__file__).parents[1] / 'app/tasks.py'
    tree = ast.parse(source.read_text())
    pipeline = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'run_video_pipeline')
    handler = next(n for n in ast.walk(pipeline) if isinstance(n, ast.ExceptHandler)
        and any(isinstance(v, ast.ImportFrom) and v.module == 'app.services.failed_master_workprint' for v in ast.walk(n)))
    first = handler.body[0]
    assert isinstance(first, ast.Try)
    call = Mock(side_effect=RuntimeError('Preview storage unavailable'))
    monkeypatch.setattr(failed, 'checkpoint', call)
    original = RuntimeError('Original render failure')
    ns = {'task_id': TASK, 'work': Path('/unread'), 'options': {}, 'duration_minutes': .5, 'exc': original}
    exec(compile(ast.Module(body=[first], type_ignores=[]), str(source), 'exec'), ns)
    call.assert_called_once(); assert ns['exc'] is original
