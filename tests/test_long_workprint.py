"""A failed documentary can be watched without becoming a publication input."""
from copy import deepcopy
import json
import math

import pytest

from app.services import qa_workprint as workprint, qa_workprint_access as access
from test_qa_workprint import case as workprint_case, persist, metadata, TASK
from test_qa_workprint_integration import case as pipeline_case, _call
from test_qa_workprint_access import store, pointer, request, consume, assert_private, DATA


ITEM = '44444444-4444-4444-8444-444444444444'
SECONDS = 184.536
TARGET = math.ceil(SECONDS * 30) / 30
SPEC = {'mode': 'production', 'format': 'landscape', 'duration_minutes': 3,
        'content_plan_item_id': ITEM}


def prepare(c, monkeypatch):
    original = deepcopy(c.args)
    c.args['scenes'] = original['scenes'] * 5
    c.args['scene_visuals'] = original['scene_visuals'] * 5
    c.args['final_reviews'] = {i: deepcopy(original['final_reviews'][i % 6]) for i in range(30)}
    c.args['scene_durations'] = [SECONDS / 30] * 30
    c.args['voice_result']['scene_durations'] = list(c.args['scene_durations'])
    c.args['options'] = dict(SPEC)
    c.args['narration'] = ' '.join(s['narration'] for s in c.args['scenes'])
    c.args['target_seconds'] = TARGET
    monkeypatch.setattr(workprint, 'media_duration', lambda p: SECONDS)
    monkeypatch.setattr(workprint, '_probe', lambda path, target, **kw: c.probes.append((target, kw)))


def long_pointer():
    return {**pointer(), 'version': 4, 'duration_seconds': TARGET,
            'frame_count': round(TARGET * 30), 'width': 1920, 'height': 1080}


def job(p):
    return {'task_id': TASK, 'kind': 'render', 'state': 'FAILURE',
            'spec': dict(SPEC), 'qa_workprint': p}


def test_full_failed_edit_preserves_voice_negative_reviews_and_selection(workprint_case, monkeypatch):
    c = workprint_case
    prepare(c, monkeypatch)
    c.args['scene_visuals'][0][0].update(preserve_start_fraction=True, start_fraction=0.0,
                                       generation_provider='gemini_veo', source_type='generated')
    before = deepcopy(c.args)
    p = persist(c)['qa_workprint']
    assert c.args == before
    assert p['version'] == 4 and p['duration_seconds'] == TARGET
    assert (p['frame_count'], p['width'], p['height']) == (5537, 1920, 1080)
    assert c.renders[0]['output_resolution'] == '1920x1080'
    assert c.renders[0]['voice_path'] == c.audio
    assert c.renders[0]['scene_visual_paths'][0][0]['start_fraction'] == 0.0
    assert c.probes == [(TARGET, {'landscape': True})]
    assert len(metadata(c)['scenes']) == 30
    assert metadata(c)['scenes'][1]['review']['score'] == 60
    assert metadata(c)['scenes'][1]['review']['gates']['evidence_gate_passed'] is False
    assert p['qa_approved'] is p['publish_eligible'] is p['reusable'] is False
    assert access.validated_pointer(job(p)) == p
    assert access.validated_pointer({**job(p), 'state': 'SUCCESS'}) is None


@pytest.mark.parametrize('change', [
    {'content_plan_item_id': None}, {'content_plan_item_id': 'invalid'},
    {'content_plan_item_id': '../escape'}, {'mode': 'preview'}, {'format': 'shorts'},
])
def test_no_unscoped_long_preview(workprint_case, monkeypatch, change):
    c = workprint_case
    prepare(c, monkeypatch)
    c.args['options'].update(change)
    assert persist(c) == {}
    assert not c.renders and not c.uploads


@pytest.mark.parametrize('seconds', [150 - .1, 240 + .1, float('nan'), 184.536])
def test_invalid_or_non_frame_aligned_timeline_never_renders(workprint_case, monkeypatch, seconds):
    c = workprint_case
    prepare(c, monkeypatch)
    assert persist(c, target_seconds=seconds) == {}
    assert not c.renders


def test_real_pipeline_stores_only_private_pointer(pipeline_case):
    c = pipeline_case
    c.kwargs['options'].update(SPEC)
    c.kwargs['duration_minutes'] = 3
    c.kwargs['scene_durations'] = [SECONDS / 30] * 30
    c.pointer['version'] = 4
    before = deepcopy(c.job)
    _call(c)
    assert c.helper.call_args.kwargs['target_seconds'] == TARGET
    assert c.writes == [{'qa_workprint': c.pointer}]
    assert {k: v for k, v in c.job.items() if k != 'qa_workprint'} == before


@pytest.mark.parametrize('change', [{}, {'format': 'shorts'}, {'duration_minutes': .5},
    {'content_plan_item_id': 'invalid'}, {'mode': 'preview'}])
def test_pointer_requires_exact_documentary_scope(change):
    value = job(long_pointer())
    value['spec'] = {**SPEC, **change} if change else {}
    assert access.validated_pointer(value) is None


def test_private_seek_and_head_use_same_pinned_bytes(store):
    p = long_pointer()
    assert access.validated_pointer(job(p)) == p
    response = access.stream_response(p, request(ranges=['bytes=512-1535']))
    assert response.status_code == 206 and consume(response) == DATA[512:1536]
    assert_private(response)
    response = access.stream_response(p, request('HEAD'))
    assert response.status_code == 200 and response.body == b''
    assert store.bodies[-1].read_sizes == [] and store.bodies[-1].close_count == 1
    assert_private(response)


def test_long_size_allowance_does_not_change_short_limit():
    size = 64 * 1024 * 1024 + 1
    assert access._valid_pointer({**long_pointer(), 'size': size})
    assert not access._valid_pointer({**pointer(), 'size': size})
    assert not access._valid_pointer({**long_pointer(), 'size': 192 * 1024 * 1024 + 1})


@pytest.mark.parametrize('change', [{}, {'width': 1080, 'height': 1920}, {'nb_read_frames': '5536'}])
def test_probe_checks_actual_landscape_frames(monkeypatch, tmp_path, change):
    video = {'codec_type': 'video', 'width': 1920, 'height': 1080,
             'nb_read_frames': '5537', 'avg_frame_rate': '30/1', **change}
    payload = {'format': {'duration': str(TARGET)}, 'streams': [video, {'codec_type': 'audio'}]}
    monkeypatch.setattr(workprint.subprocess, 'check_output', lambda *a, **kw: json.dumps(payload))
    if change:
        with pytest.raises(ValueError, match='master'):
            workprint._probe(tmp_path / 'actual.mp4', TARGET, landscape=True)
    else:
        workprint._probe(tmp_path / 'actual.mp4', TARGET, landscape=True)
