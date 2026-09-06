"""Explicit scheduler adapter: real Lua, isolated receipt-validator boundary.

The release module separately tests its real Google receipt construction.
These tests never manufacture or write a public publisher/source/upload record.
"""
import ast
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from test_production_recovery import (
    ROOT, CHANNEL, REVISION, CONNECTION_ID, VIDEO_ID, recovery,
    _change, _write, _snapshot, _digest,
)
from test_production_recovery_public import _public_seed
from test_blocked_public_recovery import _load


ASSET_PREFIX = 'youtube_studio:blocked_public_assets:v1:'
RECEIPT_PREFIX = 'youtube_studio:blocked_public_release:v1:'
EPOCH_KEY = 'youtube_studio:oauth:authorization_epoch:v2'


@pytest.fixture
def case(recovery):
    module, client = recovery
    data = _public_seed(module, client, hops=1, repair=False)
    # Mirror the actual one-edge English Margin retry at consumed cursor one.
    profile = json.loads(client.get(module.PROFILE_PREFIX + CHANNEL))
    profile['production_topics'] = profile['production_topics'][1:]
    profile['default_language'] = 'en'
    _write(client, module.PROFILE_PREFIX + CHANNEL, profile)
    client.hset(data.state_key, mapping={'cursor': '1', 'consumed_prefix': _digest(profile['production_topics'][:1])})
    for task_id in data.ids:
        def spec(job):
            job['spec'].update(language='en', production_topic_index=0)
        _change(client, module.JOB_PREFIX + task_id, spec)
    blocked = {'privacy_status': 'private', 'release_status': 'blocked',
               'release_error_code': 'HttpError_403', 'caption_uploaded': False,
               'caption_error_code': 'HttpError_403', 'thumbnail_uploaded': False}
    def source(job):
        job['result']['caption_key'] = f'videos/{data.recovered_id}/captions.en.srt'
        job['result']['youtube'].update(blocked)
    _change(client, module.JOB_PREFIX + data.recovered_id, source)
    _change(client, module.JOB_PREFIX + data.publish_id, lambda job: job['result'].update(blocked))
    def ledger(record):
        record.update(privacy_status='private', release_status='blocked',
                      release_error_code='HttpError_403', release_side_effect_possible=False)
        record.pop('release_completed_at', None)
    _change(client, module.UPLOAD_PREFIX + data.recovered_id, ledger)
    data.normal_audit_key = data.audit_key
    data.audit_key = module.PUBLIC_RECOVERY_RESUME_PREFIX + CHANNEL + ':' + data.original_id
    data.receipt_key = RECEIPT_PREFIX + data.recovered_id
    data.asset_key = ASSET_PREFIX + data.recovered_id
    receipt = {'version': 1, 'status': 'public', 'source_task_id': data.recovered_id}
    assets = {'version': 1, 'status': 'assets_ready_needs_release_integration'}
    _write(client, data.receipt_key, receipt)
    _write(client, data.asset_key, assets)
    client.set(EPOCH_KEY, '7')
    proof = {'source_task_id': data.recovered_id, 'publish_task_id': data.publish_id,
             'youtube_video_id': VIDEO_ID, 'target_channel_id': CHANNEL, 'connection_id': CONNECTION_ID,
             'profile_revision': REVISION, 'privacy_status': 'public', 'release_status': 'public',
             'contains_synthetic_media': True, 'caption_uploaded': True, 'thumbnail_uploaded': True,
             'receipt_sha256': hashlib.sha256(json.dumps(receipt, sort_keys=True).encode()).hexdigest()}
    validator = Mock(return_value=proof)
    # Keep the real reader/snapshot/proof checks; replace only lazy imports.
    tree = ast.parse((ROOT / 'app/services/production_recovery.py').read_text(encoding='utf-8'))
    helper = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_public_recovery_proof')
    helper.body = [n for n in helper.body if not isinstance(n, ast.ImportFrom)]
    ns = module._resume_after_retry.__globals__
    ns.update(RECOVERY_PREFIX=ASSET_PREFIX, PUBLIC_RECOVERY_PREFIX=RECEIPT_PREFIX,
              AUTH_EPOCH_KEY=EPOCH_KEY, validate_public_recovery_receipt=validator)
    exec(compile(ast.Module(body=[helper], type_ignores=[]), '<real-receipt-reader>', 'exec'), ns)
    return module, client, data, validator, proof


def run(case, now=100000):
    module, _, data, _, _ = case
    return module.resume_after_blocked_public_retry(CHANNEL, data.original_id, data.recovered_id, REVISION, now=now)


def paused(case):
    _, client, data, _, _ = case
    assert client.hget(data.state_key, 'paused_reason') == 'previous_render_failed'
    assert client.hget(data.state_key, 'cursor') == '1'
    assert not client.exists(data.audit_key)


def test_new_receipt_resumes_without_rewriting_any_historical_record(case):
    module, client, data, validator, proof = case
    before = _snapshot(client)
    result = run(case)
    assert result['status'] == 'resumed'
    assert result['publication_proof'] == 'blocked_public_recovery'
    assert result['public_recovery_receipt_sha256'] == proof['receipt_sha256']
    assert result['cursor'] == 1 and result['next_due'] == 121600
    assert result['thumbnail_uploaded'] is result['caption_uploaded'] is True
    after = _snapshot(client)
    assert json.loads(after.pop(data.audit_key)) == {k: v for k, v in result.items() if k != 'status'}
    expected = before.pop(data.state_key)
    expected.pop('paused_reason')
    expected['next_due'] = '121600'
    assert after.pop(data.state_key) == expected and after == before
    records, assets, receipt = validator.call_args.args
    assert records['source']['result']['youtube']['release_status'] == 'blocked'
    assert records['publisher']['result']['caption_error_code'] == 'HttpError_403'
    assert records['ledger']['release_side_effect_possible'] is False
    assert assets == json.loads(client.get(data.asset_key))
    assert receipt == json.loads(client.get(data.receipt_key))
    assert validator.call_args.kwargs == {'credential_cipher': 'opaque-fixture-credential', 'authorization_epoch': '7'}
    assert not client.exists(data.normal_audit_key) and not client.exists(data.private_audit_key)


def test_ordinary_public_resume_does_not_consume_new_receipt(case):
    module, client, data, validator, _ = case
    before = _snapshot(client)
    with pytest.raises(module.ProductionRecoveryError):
        module.resume_after_public_retry(CHANNEL, data.original_id, data.recovered_id, REVISION, now=100000)
    assert _snapshot(client) == before
    validator.assert_not_called()


@pytest.mark.parametrize('target', ['asset', 'receipt'])
@pytest.mark.parametrize('raw', [None, '{}', '[]', 'invalid-json'])
def test_missing_or_invalid_evidence_never_uses_historical_success_as_public(case, target, raw):
    module, client, data, validator, _ = case
    key = data.asset_key if target == 'asset' else data.receipt_key
    if raw is None:
        client.delete(key)
    else:
        client.set(key, raw)
    validator.side_effect = ValueError('bad private response with token')
    before = _snapshot(client)
    with pytest.raises(module.ProductionRecoveryError):
        run(case)
    assert _snapshot(client) == before
    paused(case)


@pytest.mark.parametrize('field,value', [
    ('source_task_id', 'wrong'), ('publish_task_id', 'wrong'), ('youtube_video_id', 'wrong'),
    ('target_channel_id', 'wrong'), ('connection_id', 'wrong'), ('profile_revision', 'wrong'),
    ('privacy_status', 'private'), ('release_status', 'uncertain'),
    ('contains_synthetic_media', False), ('caption_uploaded', False), ('thumbnail_uploaded', False),
    ('caption_uploaded', 1), ('receipt_sha256', 'not-a-hash'),
])
def test_proof_cannot_change_identity_or_weaken_delivery(case, field, value):
    module, client, _, _, proof = case
    proof[field] = value
    before = _snapshot(client)
    with pytest.raises(module.ProductionRecoveryError):
        run(case)
    assert _snapshot(client) == before
    paused(case)


@pytest.mark.parametrize('damage', ['new_spec', 'claim', 'execution', 'profile', 'paid_ancestor_upload', 'qa', 'automation', 'active'])
def test_all_original_lineage_quality_and_idle_guards_remain(case, damage):
    module, client, data, _, _ = case
    if damage == 'new_spec':
        _change(client, module.JOB_PREFIX + data.recovered_id, lambda j: j['spec'].update(new_editorial_option=True))
    elif damage == 'claim': client.hset(module.RETRY_CHILD_CLAIM_PREFIX + data.recovered_id, 'token', 'wrong')
    elif damage == 'execution': client.delete(module.RETRY_CHILD_EXECUTION_PREFIX + data.recovered_id)
    elif damage == 'profile': _change(client, module.PROFILE_PREFIX + CHANNEL, lambda p: p.update(profile_revision='changed'))
    elif damage == 'paid_ancestor_upload': _write(client, module.UPLOAD_PREFIX + data.original_id, {'side_effect_possible': True})
    elif damage == 'qa': _change(client, module.JOB_PREFIX + data.recovered_id, lambda j: j['result'].update(manual_qa_required=True))
    elif damage == 'automation': _change(client, module.JOB_PREFIX + data.recovered_id, lambda j: j['result']['youtube_automation'].update(release_mode='private'))
    elif damage == 'active': client.set(module.ACTIVE_KEY, 'another-real-channel-active')
    before = _snapshot(client)
    with pytest.raises(module.ProductionRecoveryError): run(case)
    assert _snapshot(client) == before
    paused(case)


@pytest.mark.parametrize('target', ['receipt', 'asset', 'epoch', 'credential', 'channel', 'profile', 'source',
                                  'publisher', 'ledger', 'original', 'claim', 'execution', 'dispatch',
                                  'cursor', 'active', 'membership'])
def test_every_proof_and_original_snapshot_is_atomic(case, monkeypatch, target):
    module, client, data, _, _ = case
    eval_original = client.eval
    keys = {'receipt': data.receipt_key, 'asset': data.asset_key, 'epoch': EPOCH_KEY,
            'credential': module.OAUTH_CREDENTIAL_PREFIX + CHANNEL, 'channel': module.OAUTH_CHANNEL_PREFIX + CHANNEL,
            'profile': module.PROFILE_PREFIX + CHANNEL, 'source': module.JOB_PREFIX + data.recovered_id,
            'publisher': module.JOB_PREFIX + data.publish_id, 'ledger': module.UPLOAD_PREFIX + data.recovered_id,
            'original': module.JOB_PREFIX + data.original_id,
            'claim': module.RETRY_CHILD_CLAIM_PREFIX + data.recovered_id,
            'execution': module.RETRY_CHILD_EXECUTION_PREFIX + data.recovered_id,
            'dispatch': module.RETRY_DISPATCH_PREFIX + data.original_id}
    def racing(*args, **kwargs):
        if target in {'claim', 'dispatch'}: client.hset(keys[target], 'token', 'raced')
        elif target == 'cursor': client.hset(data.state_key, 'cursor', '2')
        elif target == 'active': client.set(module.ACTIVE_KEY, 'another-active-production')
        elif target == 'membership': client.srem(module.OAUTH_CHANNEL_INDEX, CHANNEL)
        else: client.set(keys[target], 'raced')
        return eval_original(*args, **kwargs)
    monkeypatch.setattr(client, 'eval', racing)
    with pytest.raises(module.ProductionRecoveryError): run(case)
    assert client.hget(data.state_key, 'paused_reason') == 'previous_render_failed'
    assert not client.exists(data.audit_key)


def test_concurrent_calls_clear_pause_once_and_keep_interval(case):
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(lambda _: run(case), range(5)))
    assert sum(r['status'] == 'resumed' for r in results) == 1
    assert {r['next_due'] for r in results} == {121600}


def test_idempotent_receipt_resume_does_not_modify_later_jobs(case):
    module, client, data, validator, _ = case
    first = run(case)
    client.set(module.ACTIVE_KEY, 'later-production')
    client.hset(data.state_key, mapping={'cursor': '2', 'next_due': '900000'})
    client.delete(data.receipt_key)
    validator.reset_mock()
    before = _snapshot(client)
    assert run(case, now=500000) == {**first, 'status': 'already_resumed'}
    assert _snapshot(client) == before
    validator.assert_not_called()


def test_normal_public_audit_is_not_a_recovery_receipt_audit(case):
    module, client, data, validator, _ = case
    client.set(data.normal_audit_key, json.dumps({'status': 'already_resumed'}))
    assert run(case)['status'] == 'resumed'
    validator.assert_called_once()


def test_lost_atomic_reply_never_releases_or_shifts_due_again(case, monkeypatch):
    module, client, data, _, _ = case
    original = client.eval
    def lost(*args, **kwargs):
        original(*args, **kwargs)
        raise ConnectionError('secret credentials in transport')
    monkeypatch.setattr(client, 'eval', lost)
    with pytest.raises(module.ProductionRecoveryError, match='^recovery_state_unavailable$'): run(case)
    monkeypatch.setattr(client, 'eval', original)
    assert run(case, now=999999)['status'] == 'already_resumed'
    assert client.hget(data.state_key, 'next_due') == '121600'


@pytest.fixture
def real_case(case):
    """Exercise the release module's actual validator, not a substitute gate."""
    module, client, data, _, _ = case
    settings = SimpleNamespace(redis_url='redis://not-used')
    automation = _load('youtube_automation', {'settings': settings})
    core = _load('blocked_public_recovery', {
        'automated_quality_approved': automation.automated_quality_approved,
        'validate_publish_plan': automation.validate_publish_plan,
    })
    release = _load('blocked_public_release', {'assets_core': core})
    keys = {'source': module.JOB_PREFIX + data.recovered_id, 'publisher': module.JOB_PREFIX + data.publish_id,
            'ledger': module.UPLOAD_PREFIX + data.recovered_id, 'profile': module.PROFILE_PREFIX + CHANNEL,
            'channel': module.OAUTH_CHANNEL_PREFIX + CHANNEL}
    _change(client, keys['source'], lambda j: j['result'].update(
        title='The furniture decision', metadata_key=f'videos/{data.recovered_id}/metadata.json', thumbnail_key=None))
    _change(client, keys['profile'], lambda p: p.update(languages=['en']))
    plan = automation.validate_publish_plan({
        'schema_version': 1, 'source_task_id': data.recovered_id, 'target_channel_id': CHANNEL,
        'profile_revision': REVISION, 'title': 'The furniture decision', 'description': 'Existing approved description',
        'tags': ['furniture'], 'hashtags': ['Shorts'], 'category_id': '28', 'default_language': 'en',
        'contains_synthetic_media': True, 'release_mode': 'public', 'publish_at': None,
        'require_thumbnail': True, 'thumbnail_key': None,
        'quality_snapshot': {'quality_disposition': 'automated_qc_pass', 'manual_qa_required': False}})
    _change(client, keys['ledger'], lambda row: row.update(publish_plan=plan))
    records = {name: json.loads(client.get(key)) for name, key in keys.items()}
    binding = core._validate(records, data.recovered_id, VIDEO_ID, CHANNEL, REVISION)
    fingerprints = {name: {'sha256': 'a' * 64, 'size': 100} for name in ('caption', 'thumbnail', 'final', 'metadata')}
    fingerprints['caption']['language'] = 'en'
    thumbnail = core._thumbnail_receipt({'kind': 'youtube#thumbnailSetResponse', 'items': [
        {'default': {'url': f'https://i.ytimg.com/vi/{VIDEO_ID}/default.jpg', 'width': 120, 'height': 90}}]})
    audit = {'version': 1, **binding, 'status': 'assets_ready_needs_release_integration', 'assets': fingerprints,
             'caption': {'status': 'verified', 'attempts': 1, 'caption_id': 'manual-caption-identifier'},
             'thumbnail': {'status': 'verified', 'attempts': 1, **thumbnail}}
    identity = release._identity(records, audit, 'opaque-fixture-credential', '7')
    receipt = {'version': 1, **identity, 'status': 'public', 'attempts': 1, 'side_effect_possible': True,
               'reserved_at': '2026-09-06T12:00:00+00:00',
               'confirmation': 'post_request_readback',
               'historical_snapshot_hashes': {name: audit[name] for name in (
                   'source_snapshot_sha256', 'publisher_snapshot_sha256', 'ledger_snapshot_sha256')},
               'prior_private_proof': {'youtube_video_id': VIDEO_ID, 'target_channel_id': CHANNEL,
                                       'privacy_status': 'private', 'upload_status': 'processed',
                                       'scheduled_publish_at': None},
               'release_request': {'youtube_video_id': VIDEO_ID, 'privacy_status': 'public',
                                   'contains_synthetic_media': True, 'self_declared_made_for_kids': False},
               'public_proof': {'youtube_video_id': VIDEO_ID, 'target_channel_id': CHANNEL,
                                'privacy_status': 'public', 'upload_status': 'processed',
                                'contains_synthetic_media': True, 'scheduled_publish_at': None,
                                'verified_at': '2026-09-06T12:00:01+00:00'},
               'caption_proof': {'caption_id': 'manual-caption-identifier', 'language': 'en', 'status': 'serving',
                                 'track_kind': 'standard', 'is_draft': False},
               'asset_fingerprints': fingerprints, 'thumbnail_verified': True}
    _write(client, data.asset_key, audit)
    _write(client, data.receipt_key, receipt)
    module._resume_after_retry.__globals__['validate_public_recovery_receipt'] = release.validate_public_recovery_receipt
    return case, keys


def test_real_release_validator_and_scheduler_accept_same_completed_receipt(real_case):
    case, keys = real_case
    _, client, _, _, _ = case
    old = {name: client.get(key) for name, key in keys.items()}
    result = run(case)
    assert result['status'] == 'resumed'
    assert {name: client.get(key) for name, key in keys.items()} == old


@pytest.mark.parametrize('target,path,value', [
    ('receipt', ['status'], 'reserved_before_http'), ('receipt', ['attempts'], 0),
    ('receipt', ['public_proof', 'youtube_video_id'], 'Other123456'),
    ('receipt', ['public_proof', 'target_channel_id'], 'UC_some_other_channel'),
    ('receipt', ['public_proof', 'privacy_status'], 'private'),
    ('receipt', ['public_proof', 'upload_status'], 'uploaded'),
    ('receipt', ['public_proof', 'contains_synthetic_media'], False),
    ('receipt', ['caption_proof', 'track_kind'], 'ASR'),
    ('receipt', ['caption_proof', 'status'], 'syncing'),
    ('receipt', ['thumbnail_verified'], False), ('receipt', ['asset_audit_sha256'], 'b' * 64),
    ('receipt', ['credential_sha256'], 'b' * 64), ('receipt', ['authorization_epoch_sha256'], 'b' * 64),
    ('receipt', ['confirmation'], None), ('receipt', ['historical_snapshot_hashes'], {}),
    ('receipt', ['prior_private_proof', 'privacy_status'], 'public'),
    ('asset', ['caption', 'attempts'], 2), ('asset', ['thumbnail', 'status'], 'uncertain'),
    ('source', ['result', 'title'], 'Changed narration context'),
    ('ledger', ['release_side_effect_possible'], True), ('channel', ['requires_reconnect'], True),
])
def test_real_validator_never_turns_ambiguous_or_changed_proof_into_public(real_case, target, path, value):
    case, keys = real_case
    module, client, data, _, _ = case
    key = data.receipt_key if target == 'receipt' else data.asset_key if target == 'asset' else keys[target]
    def change(row):
        for part in path[:-1]: row = row[part]
        row[path[-1]] = value
    _change(client, key, change)
    before = _snapshot(client)
    with pytest.raises(module.ProductionRecoveryError): run(case)
    assert _snapshot(client) == before
    paused(case)
