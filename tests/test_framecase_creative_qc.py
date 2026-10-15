from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import fakeredis
import pytest

from app.services import framecase_creative_qc as creative, framecase_recovery as recovery
from app.services import content_plan as plan, studio_state as jobs
from app.services.production_spend import SpendBlocked


def report(performances=('character_action', 'object_action', 'character_action', 'character_action')):
    return {**{name: True for name in creative.CHECKS}, 'findings': [],
            'shots': [{'scene_index': i, 'performance': value, 'observation': 'Visible acting and coherent design.'}
                      for i, value in enumerate(performances)]}


def test_owner_rejected_clock_diagram_film_cannot_pass_positive_boolean_scores():
    old_film = report(('diagram', 'character_action', 'diagram', 'diagram'))
    assert creative.evaluate([([0, 1, 2, 3], old_film)], 4)['pass'] is False
    assert creative.evaluate([([0, 1, 2, 3], report())], 4)['pass'] is True


@pytest.mark.parametrize('defect', list(creative.CHECKS) + ['findings'])
def test_each_creative_defect_independently_blocks_the_finished_film(defect):
    value = report(); value[defect] = ['Character changes age between shots.'] if defect == 'findings' else False
    assert not creative.evaluate([([0, 1, 2, 3], value)], 4)['pass']


@pytest.mark.parametrize('defect', ['missing', 'duplicate', 'wrong_index', 'numeric_boolean'])
def test_partial_or_ambiguous_creative_reviews_never_approve(defect):
    value = report()
    if defect == 'missing': value['shots'].pop()
    if defect == 'duplicate': value['shots'][3] = deepcopy(value['shots'][2])
    if defect == 'wrong_index': value['shots'][3]['scene_index'] = True
    if defect == 'numeric_boolean': value[creative.CHECKS[0]] = 1
    with pytest.raises(SpendBlocked): creative.evaluate([([0, 1, 2, 3], value)], 4)


def test_negative_master_review_is_reused_without_rebuying_an_opinion(tmp_path, monkeypatch):
    from app.services import production_included_router as router
    import subprocess
    image = tmp_path / 'frame.jpg'
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
                    'color=navy:s=64x64', '-frames:v', '1', '-threads', '1', str(image)],
                   check=True, capture_output=True)
    movie = tmp_path / 'movie.mp4'; movie.write_bytes(b'original-rendered-bytes')
    monkeypatch.setattr(creative, 'sample_master', lambda *args: [(i, [1, 2, 3], image.read_bytes()) for i in range(4)])
    def validate_wire(parts, **kwargs):
        from app.services.abacus_router_schema_compat import prepare_json_object_router_request
        # Exercise the actual provider envelope before any paid request.
        prepare_json_object_router_request(parts, api_key='test-key',
            system_instruction=kwargs['system_instruction'], json_schema=kwargs['json_schema'])
        return report(('diagram', 'character_action', 'diagram', 'diagram'))
    critic = Mock(side_effect=validate_wire)
    monkeypatch.setattr(router, 'generate_included_json', critic)
    cp = {}; package = {'title': 'Film', 'narration': 'A mystery.', 'scenes': [{'narration': 'A beat.'}] * 4}
    rendered = {'path': movie, 'scene_windows': []}
    first = creative.review_master(rendered, package, cp, tmp_path)
    assert not first['pass']
    # Emulate checkpoint persistence: tuples become JSON arrays.
    import json
    saved = json.loads(json.dumps(cp))
    assert creative.review_master(rendered, package, saved, tmp_path) == json.loads(json.dumps(first))
    critic.assert_called_once()
    movie.write_bytes(b'different-rendered-bytes')
    creative.review_master(rendered, package, saved, tmp_path)
    assert critic.call_count == 2 and len(saved['creative_reviews']) == 2


def test_owner_hold_survives_changed_build_without_consuming_a_continuation(monkeypatch):
    from app.production_tasks import continue_framecase_episode
    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(plan, '_client', lambda: client)
    enqueue = Mock(); monkeypatch.setattr(continue_framecase_episode, 'apply_async', enqueue)
    source = {'task_id': '093514e3-fefb-5cd4-b664-3a0087c3fd16', 'state': 'FAILURE',
        'publication_hold': {'receipt_key': 'owner-hold'}, 'framecase_failed_build': 'old',
        'framecase_failure_code': 'framecase_FalVideoPolicyError',
        'spec': {'production_channel_id': recovery.CHANNEL_ID, 'framecase_animation': True,
                 'publish_after_render': False}}
    monkeypatch.setenv('RAILWAY_GIT_COMMIT_SHA', 'new')
    assert recovery.schedule(source) == 'held_by_owner'
    enqueue.assert_not_called()
    assert list(client.scan_iter()) == []


def test_already_queued_continuation_obeys_new_owner_hold(monkeypatch):
    import json
    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(plan, '_client', lambda: client)
    operation = 'c01c0de0-7dbe-4444-b9f1-000000000001'; root = '093514e3-fefb-5cd4-b664-3a0087c3fd16'
    client.set(recovery.PREFIX + operation, json.dumps({'operation': operation, 'source_task_id': root, 'attempt': 5}))
    monkeypatch.setattr(jobs, 'get_job', lambda _: {'publication_hold': {'receipt_key': 'hold'}, 'spec': {}})
    assert recovery.run(SimpleNamespace(request=SimpleNamespace(id=operation)), root, 5) == {'status': 'held_by_owner'}
    assert not client.exists(recovery.PREFIX + 'execution:' + operation)


def test_real_sampled_contact_strip_fits_the_actual_provider_envelope(tmp_path):
    import subprocess
    from app.services.abacus_visual_spend_quotes import _decode_jpeg
    movie = tmp_path / 'source.mp4'
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
        'testsrc2=s=180x320:r=30', '-frames:v', '60', '-c:v', 'libx264', '-threads', '1', str(movie)],
        check=True, capture_output=True)
    samples = creative.sample_master({'path': str(movie), 'frame_count': 60, 'scene_windows': [
        {'scene_index': 0, 'start_frame': 0, 'end_frame': 60}]}, tmp_path, 1)
    assert samples[0][1] == [6, 30, 53]
    _decode_jpeg(samples[0][2])
