from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from app.services import content_plan as plan, content_plan_recovery as recovery
from app.services import experiment_library_continuation as library, studio_state as jobs
from test_shorts_experiment_editorial import visual_fixture
from test_content_plan import case, CHANNEL, OTHER
from test_content_plan_recovery import failed


@pytest.mark.parametrize('change', ['pending', 'unknown', 'record', 'different_source', 'changed_terminal'])
def test_only_definite_failed_preparation_can_admit_library_candidates(monkeypatch, change):
    client, item, root, source, operation = visual_fixture(monkeypatch)
    assert library._stopped_preparation(client, source)['record'] is None
    if change == 'pending': client.set(recovery.STATUS + root, plan._raw({'state': 'preparing'}))
    if change == 'unknown': client.delete('celery-task-meta-' + operation)
    if change == 'record': client.set(recovery.RECORD + root, 'existing preparation')
    if change == 'different_source': source['spec']['topic'] = 'changed story'
    if change == 'changed_terminal':
        terminal = plan._object(client.get('celery-task-meta-' + operation))
        terminal['result']['exc_message'] = ['Network timeout']
        client.set('celery-task-meta-' + operation, plan._raw(terminal))
    before = {k: client.dump(k) for k in client.scan_iter()}
    with pytest.raises(ValueError): library._stopped_preparation(client, source)
    assert before == {k: client.dump(k) for k in client.scan_iter()}


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'unreviewed', 'identity', 'evidence', 'editorial', 'low_score', 'wrong_candidate'])
def test_every_receiving_scene_requires_fresh_complete_visual_approval(change):
    result = {'reviews': [{'scene_index': i, 'best_candidate_index': 0, 'score': 92,
        'identity_gate_passed': True, 'evidence_gate_passed': True,
        'editorial_gate_passed': True} for i in range(6)]}
    library._review_passed(result, 86)
    if change == 'missing': result['reviews'].pop()
    if change == 'duplicate': result['reviews'][5]['scene_index'] = 4
    if change == 'unreviewed': result['unreviewable_scene_indices'] = [0]
    if change in {'identity', 'evidence', 'editorial'}: result['reviews'][0][change + '_gate_passed'] = False
    if change == 'low_score': result['reviews'][0]['score'] = 85
    if change == 'wrong_candidate': result['reviews'][0]['best_candidate_index'] = 1
    with pytest.raises(ValueError): library._review_passed(result, 86)


def test_related_but_different_primary_sources_do_not_authorize_reuse():
    first = {'sources': [{'url': 'https://example.org/history'}, {'url': 'https://example.org/patent'}]}
    library._same_primary_sources(first, deepcopy(first))
    second = deepcopy(first); second['sources'][1]['url'] = 'https://example.org/fashion'
    with pytest.raises(ValueError): library._same_primary_sources(first, second)


def test_failed_new_review_keeps_original_failure_and_never_replays(monkeypatch):
    client, item, root, source, operation = visual_fixture(monkeypatch)
    original = {key: client.dump(key) for key in client.scan_iter()}
    evidence = {'native_preparation': library._stopped_preparation(client, source)}
    monkeypatch.setattr(library, '_scope', lambda *a: (source, {}, evidence))
    prepare = Mock(side_effect=RuntimeError('Fresh visual review rejected'))
    monkeypatch.setattr(library, '_prepare', prepare)
    kwargs = {'expected_source_sha256': plan._sha(source), 'expected_donor_media_sha256': 'unused', 'client': client}
    assert library.continue_from_library(root, 'donor', observe_only=True, **kwargs)['eligible']
    prepare.assert_not_called()
    assert original == {key: client.dump(key) for key in client.scan_iter()}
    with pytest.raises(RuntimeError): library.continue_from_library(root, 'donor', **kwargs)
    assert all(client.dump(key) == value for key, value in original.items())
    assert not client.exists(recovery.RECORD + root, jobs.RETRY_DISPATCH_PREFIX + root)
    assert json.loads(client.get(library.STATUS + root))['state'] == 'stopped'
    with pytest.raises(ValueError): library.continue_from_library(root, 'donor', **kwargs)
    prepare.assert_called_once()


@pytest.mark.parametrize('outcome', ['queued', 'lost_ack', 'owner_stopped'])
def test_review_to_native_worker_preserves_owner_fence_and_unknown_delivery(case, monkeypatch, outcome):
    from uuid import uuid4, uuid5, NAMESPACE_URL
    from app.tasks import run_video_pipeline
    from app.services import shorts_experiment_stock as stock
    source = failed(case); root = source['task_id']; client = case.client
    operation = str(uuid5(NAMESPACE_URL, 'owner-plan-render-recovery:v4:' + root))
    for prefix, value in ((recovery.DISPATCH, plan._raw({'version': 1, 'task_id': operation,
            'source_task_id': root, 'source_sha256': recovery._fingerprint(source)})),
            (recovery.EXECUTION, operation),
            (recovery.STATUS, plan._raw({'state': 'stopped', 'error_type': 'RuntimeError'}))):
        client.set(prefix + root, value)
    client.set('celery-task-meta-' + operation, plan._raw({'task_id': operation, 'status': 'FAILURE',
        'result': {'exc_type': 'RuntimeError',
            'exc_message': ['Saved stock candidates did not pass independent exact-cut review']}}))
    entry = plan.item('Donor', 'Same sourced story'); donor = {'task_id': str(uuid4()),
        'state': 'SUCCESS', 'spec': {**source['spec'], 'production_channel_id': OTHER, 'content_plan_item_id': entry['id']}}
    client.set(jobs.JOB_PREFIX + donor['task_id'], plan._raw(donor))
    document = deepcopy(case.document); document.update(channel_id=OTHER, items=[entry])
    client.set(plan.PLAN_PREFIX + OTHER, plan._raw(document))
    old_preparation = library._stopped_preparation(client, source)
    monkeypatch.setattr(library, '_scope', lambda *a: (source, donor, {'native_preparation': old_preparation}))
    monkeypatch.setattr(jobs, '_client', lambda: client)
    monkeypatch.setattr(stock, 'member', lambda *a: True)
    monkeypatch.setattr(stock, 'prepared', lambda *a: donor)
    record = {'source_sha256': recovery._fingerprint(source), 'approved_package': {'scenes': ['reviewed']},
        'manifest': {'kind': 'owner_plan_retained', 'source_task_id': root}}
    def prepare(*a):
        if outcome == 'owner_stopped': client.set(jobs.RENDER_CANCELLATION_PREFIX + root, 'stopped')
        return deepcopy(record)
    monkeypatch.setattr(library, '_prepare', prepare)
    enqueue = Mock(side_effect=TimeoutError('lost reply') if outcome == 'lost_ack' else None)
    monkeypatch.setattr(run_video_pipeline, 'apply_async', enqueue)
    kwargs = {'expected_source_sha256': plan._sha(source),
        'expected_donor_media_sha256': library._media_fingerprint(donor), 'client': client}
    if outcome == 'owner_stopped':
        with pytest.raises(ValueError): library.continue_from_library(root, donor['task_id'], **kwargs)
        enqueue.assert_not_called()
        assert not client.exists(recovery.RECORD + root, jobs.RETRY_DISPATCH_PREFIX + root)
    else:
        result = library.continue_from_library(root, donor['task_id'], **kwargs)
        assert result['status'] == ('dispatch_uncertain' if outcome == 'lost_ack' else 'enqueued')
        enqueue.assert_called_once()
        assert enqueue.call_args.kwargs['retry'] is False
        child = plan._object(client.get(jobs.JOB_PREFIX + result['task_id']))
        assert child['parent_id'] == root and child['spec'] == source['spec']
        assert child['state'] == 'PENDING'
        assert plan._object(client.get(jobs.JOB_PREFIX + root))['state'] == 'FAILURE'
        assert not client.exists(plan.COMPLETION_PREFIX + case.a['id'])
    assert client.get(recovery.STATUS + root) == old_preparation['status']
    assert client.get('celery-task-meta-' + operation) == old_preparation['terminal']
    with pytest.raises(ValueError): library.continue_from_library(root, donor['task_id'], **kwargs)
    assert enqueue.call_count <= 1


def test_language_timing_fit_preserves_original_and_bounds_real_motion(tmp_path):
    import hashlib
    import subprocess
    from app.services import render
    source = tmp_path / 'source.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
        'testsrc2=size=160x288:rate=24', '-t', '5', '-c:v', 'libx264',
        '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', str(source)], check=True)
    original = source.read_bytes()
    raw = {'sha256': hashlib.sha256(original).hexdigest(), 'size': len(original), 'provider': 'fal_seedance_15_pro'}
    path, derived, edit = library._fit_owned_footage(source, raw, 6., tmp_path / 'paced.mp4')
    assert source.read_bytes() == original
    assert render.media_duration(path) >= 6.
    assert derived['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest() != raw['sha256']
    assert edit['kind'] == 'continuous_slow_motion' and 1 < edit['duration_scale'] <= 1.25
    assert edit['looped'] is False and edit['freeze_frame'] is False
    with pytest.raises(ValueError): library._fit_owned_footage(source, raw, 7., tmp_path / 'too_slow.mp4')
    assert not (tmp_path / 'too_slow.mp4').exists()
