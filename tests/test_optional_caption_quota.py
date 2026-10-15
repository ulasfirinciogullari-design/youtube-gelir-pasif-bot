from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json

import pytest

from app.services import youtube_quota_recovery as quota, youtube_publish_state as upload
from app.services import content_plan as plan, studio_state as jobs
from test_experiment_quota_continuation import release, BEFORE, AFTER, GoogleError


def test_optional_caption_wait_does_not_overwrite_real_project_rejection(release):
    r = release
    original = r.client.get(quota._wait_key())
    record = quota.observe_optional_caption_quota(GoogleError(), client=r.client, now=BEFORE)
    assert record['operation'] == 'captions.insert'
    assert quota.optional_caption_waiting(client=r.client, now=BEFORE) == record
    assert r.client.get(quota._wait_key()) == original
    assert quota.waiting(client=r.client, now=BEFORE) is not None
    assert quota.optional_caption_waiting(client=r.client, now=AFTER) is None


def test_optional_nonquota_errors_do_not_create_a_wait(release):
    r = release
    for error in (GoogleError('forbidden'), TimeoutError('quotaExceeded'), GoogleError(status=500)):
        assert quota.observe_optional_caption_quota(error, client=r.client, now=BEFORE) is None
    assert r.client.get(quota._caption_wait_key()) is None


def successful_optional_caption(release):
    r = release
    ledger = deepcopy(r.ledger)
    ledger.update(release_status='public', privacy_status='public', release_error_code=None)
    publisher = deepcopy(r.publisher)
    publisher.update(state='SUCCESS', error=None,
        created_at=datetime.fromtimestamp(BEFORE - 5, timezone.utc).isoformat(),
        updated_at=datetime.fromtimestamp(BEFORE + 5, timezone.utc).isoformat(),
        result={'youtube_video_id': ledger['youtube_video_id'], 'release_status': 'public',
                'privacy_status': 'public', 'caption_uploaded': False, 'caption_error_code': 'HttpError_403',
                'thumbnail_error_code': None, 'release_error_code': None})
    r.client.set(upload.UPLOAD_PREFIX + r.source_id, plan._raw(ledger))
    r.client.set(jobs.JOB_PREFIX + r.publisher_id, plan._raw(publisher))
    r.service.videos.return_value.list.return_value.execute.side_effect = [r.observed(r.public)] * 3
    return dict(source_id=r.source_id,
                expected_wait_sha256=hashlib.sha256(r.client.get(quota._wait_key()).encode()).hexdigest(),
                expected_ledger_sha256=plan._sha(ledger), client=r.client, now=BEFORE + 10)


def test_review_then_exact_reclassification_preserves_all_receipts(release):
    r = release; args = successful_optional_caption(r)
    before = {key: r.client.dump(key) for key in r.client.scan_iter()}
    original = r.client.get(quota._wait_key())
    assert quota.reclassify_optional_caption_wait(**args)['status'] == 'verified_optional_caption'
    assert {key: r.client.dump(key) for key in r.client.scan_iter()} == before
    result = quota.reclassify_optional_caption_wait(**args, apply=True)
    assert result['status'] == 'optional_caption_deferred'
    receipt = json.loads(r.client.get(result['receipt_key']))
    assert receipt['original_wait_raw'] == original
    assert quota.waiting(client=r.client, now=BEFORE + 10) is None
    assert quota.optional_caption_waiting(client=r.client, now=BEFORE + 10) is not None
    assert all(r.client.dump(k) == v for k, v in before.items() if k != quota._wait_key())
    r.service.videos.return_value.insert.assert_not_called()
    r.service.videos.return_value.update.assert_not_called()
    # A later genuine upload/release rejection still stops publishing.
    quota.observe_quota(GoogleError(), client=r.client, now=BEFORE + 20)
    assert quota.waiting(client=r.client, now=BEFORE + 21) is not None
    assert json.loads(r.client.get(result['receipt_key'])) == receipt


@pytest.mark.parametrize('damage', ['wrong_wait', 'wrong_ledger', 'mandatory_caption', 'thumbnail_failed',
    'release_failed', 'caption_uploaded', 'unknown_caption', 'timestamp', 'not_public', 'hold', 'race'])
def test_only_isolated_conclusive_optional_caption_rejection_can_be_reclassified(release, damage):
    r = release; args = successful_optional_caption(r)
    if damage == 'wrong_wait': args['expected_wait_sha256'] = '0' * 64
    elif damage == 'wrong_ledger': args['expected_ledger_sha256'] = '0' * 64
    elif damage == 'mandatory_caption':
        key = upload.UPLOAD_PREFIX + r.source_id; row = json.loads(r.client.get(key))
        row['publish_plan']['caption_required'] = True; r.client.set(key, plan._raw(row))
        args['expected_ledger_sha256'] = plan._sha(row)
    elif damage == 'not_public':
        r.service.videos.return_value.list.return_value.execute.side_effect = [r.observed({**r.public, 'privacyStatus': 'private'})]
    elif damage == 'hold':
        from app.services.source_publication_hold import HOLD_PREFIX
        r.client.set(HOLD_PREFIX + r.source_id, '{}')
    elif damage == 'race':
        def changed():
            quota.observe_quota(GoogleError(), client=r.client, now=BEFORE + 30)
            return r.observed(r.public)
        r.service.videos.return_value.list.return_value.execute.side_effect = changed
    else:
        key = jobs.JOB_PREFIX + r.publisher_id; row = json.loads(r.client.get(key))
        if damage == 'timestamp': row['created_at'] = datetime.fromtimestamp(BEFORE + 1, timezone.utc).isoformat()
        else:
            field, value = {'thumbnail_failed': ('thumbnail_error_code', 'HttpError_403'),
                'release_failed': ('release_error_code', 'HttpError_403'), 'caption_uploaded': ('caption_uploaded', True),
                'unknown_caption': ('caption_error_code', 'TimeoutError')}[damage]
            row['result'][field] = value
        r.client.set(key, plan._raw(row))
    before = {key: r.client.dump(key) for key in r.client.scan_iter()}
    with pytest.raises(Exception): quota.reclassify_optional_caption_wait(**args, apply=True)
    assert r.client.get(quota._caption_wait_key()) is None
    assert not list(r.client.scan_iter(match=quota.PREFIX + 'optional_caption_correction:*'))
    if damage != 'race': assert {key: r.client.dump(key) for key in r.client.scan_iter()} == before
