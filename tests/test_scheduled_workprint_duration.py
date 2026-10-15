from copy import deepcopy
import json

import pytest

from app.services import qa_workprint as workprint, qa_workprint_access as access
from test_qa_workprint import case as workprint_case, persist, metadata, TASK
from test_qa_workprint_integration import case as pipeline_case, _call


def test_failed_longer_short_keeps_complete_private_preview(workprint_case, monkeypatch):
    c = workprint_case; seconds, target = 36.108, 1100 / 30
    c.args['options']['production_scheduled'] = True
    c.args['target_seconds'] = target
    c.args['scene_durations'] = [seconds / 6] * 6
    c.args['voice_result']['scene_durations'] = list(c.args['scene_durations'])
    monkeypatch.setattr(workprint, 'media_duration', lambda p: seconds)
    monkeypatch.setattr(workprint, '_probe', lambda path, endpoint: c.probes.append((path, endpoint)))
    before = deepcopy(c.args)
    pointer = persist(c)['qa_workprint']
    assert c.args == before
    assert pointer['version'] == 2 and pointer['duration_seconds'] == target and pointer['frame_count'] == 1100
    assert c.renders[0]['target_duration'] == target and c.probes[0][1] == target
    assert metadata(c)['frame_count'] == 1100
    assert pointer['qa_approved'] is pointer['publish_eligible'] is pointer['reusable'] is False
    job = {'task_id': TASK, 'state': 'FAILURE', 'kind': 'render', 'qa_workprint': pointer}
    assert access.validated_pointer(job) == pointer
    assert access.validated_pointer({**job, 'state': 'SUCCESS'}) is None
    for change in ({'version': 1}, {'frame_count': 1099}, {'duration_seconds': 40.1}, {'qa_approved': True}):
        assert access.validated_pointer({**job, 'qa_workprint': {**pointer, **change}}) is None


@pytest.mark.parametrize('target,scheduled', [(40.1, True), (36.123, True), (36, False), (36, 'true')])
def test_fixed_requests_unaligned_frames_and_out_of_range_edits_never_make_preview(workprint_case, target, scheduled):
    c = workprint_case
    c.args['options']['production_scheduled'] = scheduled
    assert persist(c, target_seconds=target) == {}
    assert not c.renders and not c.uploads


def test_pipeline_passes_actual_endpoint_and_keeps_rejected_job_separate(pipeline_case):
    c = pipeline_case
    c.kwargs['options']['production_scheduled'] = True
    c.kwargs['effective_edit_target_seconds'] = 1100 / 30
    c.pointer['version'] = 2
    before = deepcopy(c.job)
    _call(c)
    assert c.helper.call_args.kwargs['target_seconds'] == 1100 / 30
    assert c.writes == [{'qa_workprint': c.pointer}]
    assert {k:v for k,v in c.job.items() if k != 'qa_workprint'} == before


@pytest.mark.parametrize('frames,duration,passes', [(1100, 1100 / 30, True),
    (900, 30, False), (1099, 1100 / 30, False), (1100, 37, False)])
def test_real_probe_checks_longer_preview_actual_frames_and_duration(monkeypatch, tmp_path, frames, duration, passes):
    payload = {'format': {'duration': str(duration)}, 'streams': [
        {'codec_type': 'video', 'width': 1080, 'height': 1920, 'nb_read_frames': str(frames), 'avg_frame_rate': '30/1'},
        {'codec_type': 'audio'}]}
    monkeypatch.setattr(workprint.subprocess, 'check_output', lambda *a, **k: json.dumps(payload))
    if passes:
        workprint._probe(tmp_path / 'actual.mp4', 1100 / 30)
    else:
        with pytest.raises(ValueError, match='master'):
            workprint._probe(tmp_path / 'actual.mp4', 1100 / 30)
