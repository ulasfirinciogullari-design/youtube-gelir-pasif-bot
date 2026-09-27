import json
from uuid import uuid5, NAMESPACE_URL
from copy import deepcopy
import pytest

from app.services import shorts_extra_editorial as edit, shorts_extra_release as extra
from app.services import shorts_experiment as batch, shorts_experiment_editorial as shared
from app.services import content_plan as plan, content_plan_recovery as recovery, studio_state as jobs
from app.services import youtube_automation as profiles, channel_cadence as cadence
from test_shorts_extra_release import configured, admitted, setup, C, M, NOW, DAY
from test_shorts_experiment_editorial import visual_fixture


@pytest.fixture
def failed_extra(setup, monkeypatch):
    c, entries, revision, grant, previous = configured(setup)
    extra.install(c, grant, expected_plan_revision=revision, now=NOW)
    originals = [admitted(c, row, grant) for row in entries]
    for source in originals[:3]:
        assert cadence.publication_slot(source, client=c, now=NOW)
        cadence.publication_completed(source, client=c, now=NOW)
    item = entries[-1]; source = originals[-1]; root = source['task_id']
    _, _, _, example, _ = visual_fixture(monkeypatch)
    source = {**example, 'task_id': root, 'spec': source['spec']}
    c.set(jobs.JOB_PREFIX + root, plan._raw(source))
    profile = json.loads(c.get(profiles.PROFILE_PREFIX + C)); profile['release_mode'] = 'public'
    c.set(profiles.PROFILE_PREFIX + C, plan._raw(profile))
    dispatch = json.loads(c.get(plan.DISPATCH_PREFIX + item['id']))
    dispatch['profile_revision'] = profile['profile_revision']
    c.set(plan.DISPATCH_PREFIX + item['id'], plan._raw(dispatch))
    c.set(plan.ACTIVE_KEY, plan._raw({C: item['id']}))
    c.set('celery-task-meta-' + root, plan._raw({'task_id': root, 'status': 'FAILURE',
        'result': {'exc_type': 'FinalVisualQualityError', 'exc_message': [source['error']]}}))
    operation = str(uuid5(NAMESPACE_URL, 'owner-plan-render-recovery:v4:' + root))
    c.set(recovery.DISPATCH + root, plan._raw({'version': 1, 'source_task_id': root,
        'task_id': operation, 'source_sha256': recovery._fingerprint(source)}))
    c.set(recovery.EXECUTION + root, operation)
    c.set(recovery.STATUS + root, plan._raw({'state': 'stopped', 'error_type': 'RuntimeError'}))
    c.set('celery-task-meta-' + operation, plan._raw({'task_id': operation, 'status': 'FAILURE',
        'result': {'exc_type': 'RuntimeError', 'exc_message': ['Saved stock candidates did not pass independent exact-cut review']}}))
    return c, item, root, source, grant, previous, operation


def test_replacement_preserves_four_output_limit_and_every_failed_receipt(failed_extra):
    c, item, root, source, grant, previous, operation = failed_extra
    keys = (jobs.JOB_PREFIX + root, 'celery-task-meta-' + root, 'celery-task-meta-' + operation,
        recovery.DISPATCH + root, recovery.EXECUTION + root, recovery.STATUS + root,
        *extra.keys(C, DAY)[:2], batch.approval_key(DAY))
    before = {k: c.get(k) for k in keys}; old_produced = c.hgetall(extra.keys(C, DAY)[2])
    replacement = plan.item('New sourced factory story', 'An independently reviewable physical action')
    kwargs = dict(expected_job_sha256=plan._sha(source), client=c, now=NOW)
    assert edit.replace(C, item['id'], replacement, observe_only=True, **kwargs)['eligible']
    result = edit.replace(C, item['id'], replacement, **kwargs)
    assert result['status'] == 'editorial_replaced' and all(c.get(k) == v for k, v in before.items())
    assert c.hgetall(extra.keys(C, DAY)[2]) == old_produced and not plan._active(c)
    assert not c.exists(plan.COMPLETION_PREFIX + item['id'])
    assert not json.loads(c.get(edit._Authority.ARCHIVE + item['id']))['qa_approved']
    with pytest.raises(ValueError): cadence.publication_slot(source, client=c, now=NOW)
    new_source = admitted(c, replacement, grant)
    assert cadence.publication_slot(new_source, client=c, now=NOW)
    cadence.publication_completed(new_source, client=c, now=NOW)
    assert cadence.snapshot(C, client=c, now=NOW)['extra_release'] == {
        'date': DAY, 'limit': 4, 'produced': 5, 'published': 4, 'pending': 0}
    assert c.get(batch.approval_key(DAY)) == plan._raw(previous)
    assert not c.exists(batch.replacement_key(DAY, item['id']))
    with pytest.raises(ValueError): edit.replace(C, item['id'], plan.item('Again', 'Again'), **kwargs)


@pytest.mark.parametrize('damage', ['running_recovery', 'unknown_recovery', 'wrong_channel', 'already_public',
    'changed_source', 'unknown_provider', 'has_usable_recovery', 'altered_amendment'])
def test_extra_amendment_cannot_replay_or_detach_unverified_work(failed_extra, monkeypatch, damage):
    c, item, root, source, grant, _, operation = failed_extra
    if damage == 'running_recovery': c.set(recovery.STATUS + root, plan._raw({'state': 'preparing'}))
    if damage == 'unknown_recovery': c.delete('celery-task-meta-' + operation)
    if damage == 'already_public': c.set(cadence.PREFIX + 'publication:' + root, '{}')
    if damage == 'changed_source': c.set(jobs.JOB_PREFIX + root, plan._raw({**source, 'error': 'changed'}))
    if damage == 'unknown_provider': monkeypatch.setattr(shared, '_settled', lambda *a: (_ for _ in ()).throw(ValueError('unknown')))
    if damage == 'has_usable_recovery': c.set(recovery.RECORD + root, '{}')
    if damage == 'altered_amendment': c.set(extra.replacement_key(DAY, item['id']), '{}')
    before = {k: c.dump(k) for k in c.scan_iter()}
    with pytest.raises((ValueError, TypeError)):
        edit.replace(M if damage == 'wrong_channel' else C, item['id'], plan.item('New', 'Source grounded new story'),
            expected_job_sha256=plan._sha(source), client=c, now=NOW)
    assert {k: c.dump(k) for k in c.scan_iter()} == before
