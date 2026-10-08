from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import fakeredis
import pytest

from app.services import content_plan as plan, shorts_experiment as batch, shorts_experiment_stock as stock
from app.services import youtube_quota_recovery as quota, youtube_publish_state as upload
from app.services import studio_state as jobs, youtube_auth as auth, youtube_automation as automation
from app.services import channel_production as production, channel_cadence as cadence
from test_content_plan import case

C, M = tuple(batch.COUNTS)
BEFORE = datetime(2026, 9, 25, 23, tzinfo=timezone.utc).timestamp()
AFTER = datetime(2026, 9, 26, 7, 6, tzinfo=timezone.utc).timestamp()


class GoogleError(Exception):
    def __init__(self, reason='quotaExceeded', status=403):
        self.resp = SimpleNamespace(status=status)
        self.content = json.dumps({'error': {'code': status, 'errors': [{'reason': reason}]}}).encode()


def test_google_quota_reset_uses_pacific_dst_and_rejects_other_403s():
    assert quota.reset_at(BEFORE) == '2026-09-26T07:05:00+00:00'
    winter = datetime(2026, 12, 1, 23, tzinfo=timezone.utc).timestamp()
    assert quota.reset_at(winter) == '2026-12-02T08:05:00+00:00'
    assert quota.quota_error(GoogleError())['reasons'] == ['quotaExceeded']
    for error in (GoogleError('forbidden'), GoogleError(status=500), TimeoutError('quotaExceeded')):
        assert quota.quota_error(error) is None


@pytest.fixture
def release(monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(plan, '_client', lambda: client)
    monkeypatch.setattr(upload, '_redis', lambda: client)
    monkeypatch.setattr(plan, 'check_publication', Mock())
    source_id, publisher_id = str(uuid4()), str(uuid4())
    video = 'ExistingID1'; connection = 'test-connection'; revision = 'test-profile-revision'
    spec = {'production_channel_id': C, 'production_connection_id': connection,
        'production_profile_revision': revision, 'format': 'shorts', 'mode': 'production',
        'publish_after_render': True, 'language': 'tr'}
    source = {'task_id': source_id, 'kind': 'render', 'state': 'SUCCESS', 'parent_id': None,
        'spec': spec, 'result': {'video_key': 'videos/' + source_id + '/final.mp4',
            'quality_disposition': 'automated_qc_pass', 'manual_qa_required': False}}
    frozen = automation.validate_publish_plan({'schema_version': 1, 'source_task_id': source_id,
        'target_channel_id': C, 'profile_revision': revision, 'release_mode': 'public', 'series': None,
        'title': 'Source-backed story', 'description': 'A sourced explanation.', 'tags': [], 'hashtags': [],
        'default_language': 'tr', 'category_id': '28', 'caption_required': False, 'require_thumbnail': False,
        'quality_snapshot': {'quality_disposition': 'automated_qc_pass', 'manual_qa_required': False}})
    ledger = {'version': 2, 'source_task_id': source_id, 'publish_task_id': publisher_id,
        'status': 'complete', 'side_effect_possible': True, 'youtube_video_id': video,
        'target_channel_id': C, 'connection_id': connection, 'publish_plan': frozen,
        'requested_release_mode': 'public', 'requested_publish_at': None, 'release_status': 'uncertain',
        'release_side_effect_possible': True, 'release_error_code': 'HttpError_403'}
    publisher = {'task_id': publisher_id, 'kind': 'publish', 'parent_id': source_id,
        'state': 'FAILURE', 'spec': {'source_task_id': source_id}, 'result': None, 'error': 'Original failure'}
    profile = {'channel_id': C, 'profile_revision': revision, 'production_enabled': True,
        'auto_publish': True, 'release_mode': 'public'}
    for key, value in ((jobs.JOB_PREFIX + source_id, source), (jobs.JOB_PREFIX + publisher_id, publisher),
        (upload.UPLOAD_PREFIX + source_id, ledger), (automation.PROFILE_PREFIX + C, profile),
        (auth.CHANNEL_PREFIX + C, {'id': C, 'connection_id': connection})):
        client.set(key, plan._raw(value))
    client.set(auth.CREDENTIAL_PREFIX + C, 'opaque-test-credential'); client.sadd(auth.CHANNEL_INDEX_KEY, C)
    from app.services import blocked_public_recovery as assets
    monkeypatch.setattr(assets, '_credentials', Mock(return_value=object()))
    service = Mock(); monkeypatch.setattr(assets, '_service', lambda *_: service)
    private = {'privacyStatus': 'private', 'uploadStatus': 'processed'}
    public = {**private, 'privacyStatus': 'public'}  # Google can omit disclosure in list.
    def observed(status):
        return {'items': [{'id': video, 'snippet': {'channelId': C}, 'status': status}]}
    service.videos.return_value.list.return_value.execute.side_effect = [observed(private), observed(public)]
    service.videos.return_value.update.return_value.execute.return_value = {'id': video, 'status': public}
    monkeypatch.setattr(cadence, 'publication_completed', Mock())
    evidence = quota.quota_error(GoogleError())
    record = quota.register(source_id, evidence, client=client, now=BEFORE)
    return SimpleNamespace(client=client, source_id=source_id, publisher_id=publisher_id, ledger=ledger,
        publisher=publisher, source=source, service=service, record=record, observed=observed, public=public)


def test_wait_then_same_id_release_preserves_failed_publisher_and_never_inserts(release):
    r = release
    assert quota.resume(r.source_id, client=r.client, now=BEFORE)['status'] == 'youtube_quota_wait'
    r.service.assert_not_called()
    assert quota.resume(r.source_id, client=r.client, now=AFTER)['status'] == 'public'
    r.service.videos.return_value.insert.assert_not_called()
    assert r.service.videos.return_value.update.call_args.kwargs['body']['id'] == r.ledger['youtube_video_id']
    assert json.loads(r.client.get(jobs.JOB_PREFIX + r.publisher_id)) == r.publisher
    updated = json.loads(r.client.get(upload.UPLOAD_PREFIX + r.source_id))
    assert updated['release_status'] == 'public' and updated['publish_task_id'] != r.publisher_id
    assert quota.resume(r.source_id, client=r.client, now=AFTER)['status'] == 'already_public'
    assert r.service.videos.return_value.update.call_count == 1


@pytest.mark.parametrize('damage', ['hold', 'quality', 'profile', 'ledger', 'publisher'])
def test_changed_authority_cannot_release(release, damage):
    r = release
    if damage == 'hold':
        from app.services.source_publication_hold import HOLD_PREFIX
        r.client.set(HOLD_PREFIX + r.source_id, '{}')
    else:
        key, field, value = {
            'quality': (jobs.JOB_PREFIX + r.source_id, 'state', 'FAILURE'),
            'profile': (automation.PROFILE_PREFIX + C, 'auto_publish', False),
            'ledger': (upload.UPLOAD_PREFIX + r.source_id, 'youtube_video_id', 'DifferentID'),
            'publisher': (jobs.JOB_PREFIX + r.publisher_id, 'error', 'changed'),
        }[damage]
        record = json.loads(r.client.get(key)); record[field] = value; r.client.set(key, plan._raw(record))
    with pytest.raises(Exception): quota.resume(r.source_id, client=r.client, now=AFTER)
    r.service.videos.return_value.update.assert_not_called()


def test_lost_update_reply_is_read_only_on_next_attempt(release):
    r = release
    r.service.videos.return_value.update.return_value.execute.side_effect = TimeoutError()
    with pytest.raises(TimeoutError): quota.resume(r.source_id, client=r.client, now=AFTER)
    r.service.videos.return_value.list.return_value.execute.side_effect = [r.observed({'privacyStatus': 'private', 'uploadStatus': 'processed'})]
    with pytest.raises(plan.ContentPlanError): quota.resume(r.source_id, client=r.client, now=AFTER + 60)
    assert r.service.videos.return_value.update.call_count == 1
    assert json.loads(r.client.get(upload.UPLOAD_PREFIX + r.source_id)) == r.ledger


def test_explicit_quota_rejection_defers_and_does_not_loop(release):
    r = release
    r.service.videos.return_value.update.return_value.execute.side_effect = GoogleError()
    outcome = quota.resume(r.source_id, client=r.client, now=AFTER)
    assert outcome == {'status': 'youtube_quota_wait', 'retry_at': '2026-09-27T07:05:00+00:00'}
    quota.resume(r.source_id, client=r.client, now=AFTER + 60)
    assert r.service.videos.return_value.update.call_count == 1


def test_public_readback_with_explicit_false_disclosure_is_not_approved(release):
    r = release
    r.service.videos.return_value.list.return_value.execute.side_effect = [r.observed(r.public), r.observed({**r.public, 'containsSyntheticMedia': False})]
    with pytest.raises(plan.ContentPlanError): quota.resume(r.source_id, client=r.client, now=AFTER)
    assert json.loads(r.client.get(upload.UPLOAD_PREFIX + r.source_id)) == r.ledger


def test_finite_batch_prepares_ahead_without_fake_public_completion(case, monkeypatch):
    client = case.client
    monkeypatch.setattr(cadence, '_now', lambda *_: datetime.fromtimestamp(BEFORE, cadence.ZONE))
    entries = {channel: [plan.item('Story ' + str(i), 'Independent cited explanation ' + str(i))
                        for i in range(n)] for channel, n in batch.COUNTS.items()}
    manifest = {'version': 1, 'id': 'finite-ten', 'day': '2026-09-26', 'items': [
        {'channel_id': c, 'item_id': e['id'], 'item_sha256': plan._sha(e)} for c, rows in entries.items() for e in rows]}
    client.set(batch.approval_key(manifest['day']), plan._raw(manifest))
    document = {**case.document, 'items': entries[C]}
    client.set(plan.PLAN_PREFIX + C, plan._raw(document))
    from app import publish_tasks
    publisher = Mock(return_value={'status': 'youtube_quota_wait'})
    monkeypatch.setattr(publish_tasks, 'queue_automatic_publish', publisher)
    render = Mock(); plan.maintain([case.profile], render)
    root = render.call_args.kwargs['task_id']; job = json.loads(client.get(jobs.JOB_PREFIX + root))
    job.update(state='SUCCESS', result={'video_key': 'videos/' + root + '/final.mp4',
        'quality_disposition': 'automated_qc_pass', 'manual_qa_required': False})
    client.set(jobs.JOB_PREFIX + root, plan._raw(job))
    original = client.get(jobs.JOB_PREFIX + root)
    plan.maintain([case.profile], render)
    assert render.call_count == 2
    assert plan._active(client)[C] == entries[C][1]['id']
    assert not client.exists(plan.COMPLETION_PREFIX + entries[C][0]['id'])
    assert client.get(jobs.JOB_PREFIX + root) == original
    second = json.loads(client.get(jobs.JOB_PREFIX + render.call_args.kwargs['task_id']))
    assert stock.publication_wait(second, client=client) is True
    with pytest.raises(plan.ContentPlanError, match='plan_previous_not_public'):
        plan.publication_series(second, case.profile, client=client)
    plan.maintain([case.profile], render)
    assert render.call_count == 2
