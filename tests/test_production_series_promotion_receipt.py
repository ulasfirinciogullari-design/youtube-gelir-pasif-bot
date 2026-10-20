"""Separate verified release + real resume + promotion transaction; no providers."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest

import test_production_series_promotion as promotion
from test_production_series_promotion import production, CHANNEL, CONNECTION, NOW, _run, _snapshot, _write
from test_production_continuous_public import _public_finish
from test_blocked_public_recovery import _load


@pytest.fixture
def blocked(production, monkeypatch):
    profile_factory = promotion._profile
    monkeypatch.setattr(promotion, '_profile', lambda **kw: profile_factory(languages=['tr'], **kw))
    c = promotion.case.__wrapped__(production)
    ns, client = c.ns, c.client
    ns.update(RENDER_CANCELLATION_PREFIX='youtube_studio:render_cancellation:v1:',
              HOLD_PREFIX='youtube_studio:source_publication_hold:v1:')
    original = json.loads(client.get(c.keys['source']))
    child_id, token = str(UUID(int=900)), 'fixture-retry-token'
    child = deepcopy(original)
    child.update(task_id=child_id, parent_id=c.source_id, result=None)
    _write(client, ns['JOB_PREFIX'] + child_id, child)
    keys = _public_finish(c.scheduler, client, child_id)
    client.delete(c.keys['ledger'])
    original.update(state='FAILURE', result=None, retry_claimed=True, retry_child_task_id=child_id,
                    retry_dispatch_state='dispatched')
    _write(client, c.keys['source'], original)
    client.hset(ns['RETRY_DISPATCH_PREFIX'] + c.source_id,
        mapping={'child_task_id': child_id, 'token': token, 'mode': 'full', 'state': 'dispatched'})
    client.hset(ns['RETRY_CHILD_CLAIM_PREFIX'] + child_id,
        mapping={'source_task_id': c.source_id, 'token': token})
    client.set(ns['RETRY_CHILD_EXECUTION_PREFIX'] + child_id, token)
    client.set(ns['SERIES_ASSIGNMENT_PREFIX'] + CHANNEL + ':' + c.profile['series_id'] + ':' + child_id, '2')
    client.hset(c.state_key, mapping={'last_result': 'FAILURE', 'paused_reason': 'previous_render_failed'})
    automation = _load('youtube_automation', {'settings': SimpleNamespace(redis_url='redis://not-used')})
    core = _load('blocked_public_recovery', {
        'automated_quality_approved': automation.automated_quality_approved,
        'validate_publish_plan': automation.validate_publish_plan})
    release = _load('blocked_public_release', {'assets_core': core})
    records = {name: json.loads(client.get(key)) for name, key in keys.items()}
    # Convert only fixture setup into the historical, asset-blocked delivery
    # which the real separate-receipt service supports. Production never edits it.
    stopped = {'privacy_status': 'private', 'release_status': 'blocked',
        'release_error_code': 'HttpError_403', 'caption_uploaded': False,
        'caption_error_code': 'HttpError_403', 'thumbnail_uploaded': False}
    records['source']['result'].update(title='Kurşun kalemin izi',
        metadata_key=f'videos/{child_id}/metadata.json', thumbnail_key=None)
    records['source']['result']['youtube'].update(stopped)
    records['publisher']['result'].update(stopped)
    ledger = records['ledger']
    ledger.update(privacy_status='private', release_status='blocked', release_error_code='HttpError_403',
                  release_side_effect_possible=False)
    ledger.pop('release_completed_at', None)
    ledger['publish_plan'] = automation.validate_publish_plan({
        'schema_version': 1, 'source_task_id': child_id, 'target_channel_id': CHANNEL,
        'profile_revision': c.revision, 'title': 'Kurşun kalemin izi', 'description': 'Doğrulanmış bölüm açıklaması.',
        'tags': ['bilim'], 'hashtags': ['Shorts'], 'category_id': '28', 'default_language': 'tr',
        'contains_synthetic_media': True, 'release_mode': 'public', 'publish_at': None,
        'require_thumbnail': True, 'thumbnail_key': None,
        'series': {'id': c.profile['series_id'], 'name': c.profile['series_name'], 'number': 2, 'total': 2},
        'quality_snapshot': {'quality_disposition': 'automated_qc_pass', 'manual_qa_required': False}})
    for name, value in records.items(): _write(client, keys[name], value)
    video_id, publisher_id = ledger['youtube_video_id'], ledger['publish_task_id']
    binding = core._validate(records, child_id, video_id, CHANNEL, c.revision)
    fingerprints = {name: {'sha256': 'a' * 64, 'size': 100} for name in ('caption', 'thumbnail', 'final', 'metadata')}
    fingerprints['caption']['language'] = 'tr'
    thumbnail = core._thumbnail_receipt({'kind': 'youtube#thumbnailSetResponse', 'items': [
        {'default': {'url': f'https://i.ytimg.com/vi/{video_id}/default.jpg', 'width': 120, 'height': 90}}]})
    assets = {'version': 1, **binding, 'status': 'assets_ready_needs_release_integration', 'assets': fingerprints,
        'caption': {'status': 'verified', 'attempts': 1, 'caption_id': 'fixture-caption'},
        'thumbnail': {'status': 'verified', 'attempts': 1, **thumbnail}}
    credential = client.get(ns['OAUTH_CREDENTIAL_PREFIX'] + CHANNEL)
    epoch = client.get(ns['AUTH_EPOCH_KEY'])
    identity = release._identity(records, assets, credential, epoch)
    receipt = {'version': 1, **identity, 'status': 'public', 'attempts': 1, 'side_effect_possible': True,
        'reserved_at': '2026-09-06T11:59:00+00:00', 'confirmation': 'post_request_readback',
        'historical_snapshot_hashes': {name: assets[name] for name in (
            'source_snapshot_sha256', 'publisher_snapshot_sha256', 'ledger_snapshot_sha256')},
        'prior_private_proof': {'youtube_video_id': video_id, 'target_channel_id': CHANNEL,
            'privacy_status': 'private', 'upload_status': 'processed', 'scheduled_publish_at': None},
        'release_request': {'youtube_video_id': video_id, 'privacy_status': 'public',
            'contains_synthetic_media': True, 'self_declared_made_for_kids': False},
        'public_proof': {'youtube_video_id': video_id, 'target_channel_id': CHANNEL,
            'privacy_status': 'public', 'upload_status': 'processed', 'contains_synthetic_media': True,
            'scheduled_publish_at': None, 'verified_at': '2026-09-06T11:59:01+00:00'},
        'caption_proof': {'caption_id': 'fixture-caption', 'language': 'tr', 'status': 'serving',
            'track_kind': 'standard', 'is_draft': False}, 'asset_fingerprints': fingerprints, 'thumbnail_verified': True}
    c.asset_key, c.receipt_key = core.RECOVERY_PREFIX + child_id, release.PUBLIC_RECOVERY_PREFIX + child_id
    _write(client, c.asset_key, assets)
    _write(client, c.receipt_key, receipt)
    recovered = promotion._load('app/services/production_recovery.py', {
        **ns, 'RECOVERY_PREFIX': core.RECOVERY_PREFIX, 'PUBLIC_RECOVERY_PREFIX': release.PUBLIC_RECOVERY_PREFIX,
        'validate_public_recovery_receipt': release.validate_public_recovery_receipt})
    recovered['_redis'] = lambda: client
    ns['_public_recovery_proof'] = recovered['_public_recovery_proof']
    c.audit_key = recovered['PUBLIC_RECOVERY_RESUME_PREFIX'] + CHANNEL + ':' + c.source_id
    c.normal_audit_key = recovered['PUBLIC_RESUME_PREFIX'] + CHANNEL + ':' + c.source_id
    result = recovered['resume_after_blocked_public_retry'](CHANNEL, c.source_id, child_id, c.revision,
                                                           now=NOW - 1, continue_immediately=True)
    assert result['status'] == 'resumed' and result['cursor'] == 2
    c.child_id, c.child_keys, c.recovery = child_id, keys, recovered
    c.proof_validator = Mock(wraps=release.validate_public_recovery_receipt)
    recovered['validate_public_recovery_receipt'] = c.proof_validator
    return c


def test_actual_separate_receipt_promotes_then_starts_first_new_episode(blocked):
    c = blocked
    before = _snapshot(c)
    old_state = c.client.hgetall(c.state_key)
    result = _run(c)
    assert result['status'] == 'promoted'
    c.proof_validator.assert_called_once()
    archive = json.loads(c.client.get(result['archive_key']))
    proof = archive['public_proof']
    assert archive['state'] == old_state and archive['profile'] == c.profile
    assert proof['lineage'] == [c.source_id, c.child_id]
    assert proof['publication_proof'] == 'blocked_public_recovery'
    assert proof['public_recovery_receipt_sha256'] == json.loads(c.client.get(c.audit_key))['public_recovery_receipt_sha256']
    assert proof['series']['number'] == proof['series']['total'] == 2
    mutable = {c.profile_key, c.state_key, c.pending_key}
    assert all(c.client.dump(key) == value for key, value in before.items() if key not in mutable)
    assert not c.client.exists(c.normal_audit_key)
    assert json.loads(c.client.get(c.child_keys['source']))['result']['youtube']['release_status'] == 'blocked'
    new_profile = json.loads(c.client.get(c.profile_key))
    enqueue = Mock()
    next_job = c.scheduler.dispatch_due_productions([new_profile], [CONNECTION], enqueue, now=NOW)
    spec = json.loads(c.client.get(c.ns['JOB_PREFIX'] + next_job['task_id']))['spec']
    assert spec['production_topic_index'] == 0 and spec['production_profile_revision'] == result['new_profile_revision']
    assert enqueue.call_count == 1


@pytest.mark.parametrize('damage', ['missing_receipt', 'uncertain', 'public_metadata_only', 'assets', 'caption',
    'missing_audit', 'audit_hash', 'audit_binding', 'audit_cursor', 'normal_conflict', 'series_number',
    'counter', 'assignment', 'source_qa', 'source_binding', 'epoch', 'credential', 'lineage'])
def test_separate_receipt_promotion_requires_all_original_and_new_proofs(blocked, damage):
    c = blocked
    if damage == 'missing_receipt': c.client.delete(c.receipt_key)
    elif damage == 'missing_audit': c.client.delete(c.audit_key)
    elif damage == 'assets': c.client.delete(c.asset_key)
    elif damage == 'normal_conflict': c.client.set(c.normal_audit_key, '{}')
    elif damage == 'counter': c.client.set(c.ns['SERIES_COUNTER_PREFIX'] + CHANNEL + ':' + c.profile['series_id'], '3')
    elif damage == 'assignment': c.client.delete(c.ns['SERIES_ASSIGNMENT_PREFIX'] + CHANNEL + ':' + c.profile['series_id'] + ':' + c.child_id)
    elif damage == 'epoch': c.client.set(c.ns['AUTH_EPOCH_KEY'], '5')
    elif damage == 'credential': c.client.set(c.ns['OAUTH_CREDENTIAL_PREFIX'] + CHANNEL, 'changed-fixture')
    elif damage == 'lineage': c.client.delete(c.ns['RETRY_CHILD_EXECUTION_PREFIX'] + c.child_id)
    else:
        key = c.audit_key if damage.startswith('audit_') else c.child_keys['ledger'] if damage == 'series_number' else c.child_keys['source'] if damage.startswith('source_') else c.receipt_key
        row = json.loads(c.client.get(key))
        if damage == 'uncertain': row['status'] = 'uncertain'
        elif damage == 'public_metadata_only': row = {'status': 'public', 'source_task_id': c.child_id}
        elif damage == 'caption': row['caption_proof']['status'] = 'syncing'
        elif damage == 'audit_hash': row['public_recovery_receipt_sha256'] = 'f' * 64
        elif damage == 'audit_binding': row['connection_id'] = 'other-connection'
        elif damage == 'audit_cursor': row['cursor'] = 1
        elif damage == 'series_number': row['publish_plan']['series']['number'] = 1
        elif damage == 'source_qa': row['result']['manual_qa_required'] = True
        elif damage == 'source_binding': row['spec']['production_channel_id'] = 'Other_channel'
        _write(c.client, key, row)
    before = _snapshot(c)
    with pytest.raises(c.ns['SeriesPromotionError']): _run(c)
    assert _snapshot(c) == before


@pytest.mark.parametrize('ancestor', [True, False])
@pytest.mark.parametrize('kind', ['RENDER_CANCELLATION_PREFIX', 'HOLD_PREFIX'])
def test_owner_fence_added_after_resume_blocks_series_promotion(blocked, ancestor, kind):
    c = blocked
    task_id = c.source_id if ancestor else c.child_id
    c.client.set(c.ns[kind] + task_id, '{}')
    before = _snapshot(c)
    with pytest.raises(c.ns['SeriesPromotionError'], match='series_owner_hold'): _run(c)
    assert _snapshot(c) == before


@pytest.mark.parametrize('target', ['cancel', 'hold', 'receipt', 'assets', 'epoch', 'audit'])
def test_all_new_proof_and_fence_reads_join_promotion_watch(blocked, monkeypatch, target):
    c = blocked
    original = c.ns['_Snapshot'].compare
    def race(snapshot, pipe):
        original(snapshot, pipe)
        if target == 'cancel': c.client.set(c.ns['RENDER_CANCELLATION_PREFIX'] + c.source_id, '{}')
        elif target == 'hold': c.client.set(c.ns['HOLD_PREFIX'] + c.child_id, '{}')
        elif target == 'epoch': c.client.set(c.ns['AUTH_EPOCH_KEY'], '5')
        else: c.client.set({'receipt': c.receipt_key, 'assets': c.asset_key, 'audit': c.audit_key}[target], '{}')
    monkeypatch.setattr(c.ns['_Snapshot'], 'compare', race)
    profile, state = c.client.get(c.profile_key), c.client.hgetall(c.state_key)
    with pytest.raises(c.ns['SeriesPromotionError']): _run(c)
    assert c.client.get(c.profile_key) == profile and c.client.hgetall(c.state_key) == state
    assert c.client.exists(c.pending_key)


def test_concurrent_separate_receipt_promotion_preserves_single_epoch(blocked):
    c = blocked
    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(lambda _: _run(c), range(8)))
    assert sum(row['status'] == 'promoted' for row in results) == 1
    assert len({row['new_profile_revision'] for row in results}) == 1
    before = _snapshot(c)
    assert _run(c, NOW + 100)['status'] == 'already_promoted'
    assert _snapshot(c) == before


def test_lost_promotion_commit_reply_uses_durable_receipt(blocked, monkeypatch):
    c = blocked
    factory = c.client.pipeline
    def pipeline(*args, **kwargs):
        pipe = factory(*args, **kwargs)
        execute = pipe.execute
        def lose(*a, **kw):
            execute(*a, **kw)
            raise ConnectionError('fixture-lost-reply')
        pipe.execute = lose
        return pipe
    monkeypatch.setattr(c.client, 'pipeline', pipeline)
    result = _run(c)
    assert result['status'] == 'already_promoted'
    before = _snapshot(c)
    assert _run(c, NOW + 1) == result and _snapshot(c) == before
