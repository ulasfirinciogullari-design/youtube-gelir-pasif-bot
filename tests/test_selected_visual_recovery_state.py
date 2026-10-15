"""Private V6 staging uses actual storage proofs, Redis Lua and watched state."""
from copy import deepcopy
import json
from types import SimpleNamespace

import fakeredis
import pytest

from app.services import selected_visual_recovery_state as recovery
from tests.test_selected_visual_checkpoint import case as asset_case, persist, TASK, ROOT, CHANNEL


CHILD = '33333333-3333-4333-8333-333333333333'
TOKEN = 'private-fixture-retry-token'
state = recovery.state


def put(client, value):
    client.set(state.JOB_PREFIX + value['task_id'], json.dumps(value), ex=state.JOB_TTL_SECONDS)


def get(client, task=TASK):
    return json.loads(client.get(state.JOB_PREFIX + task))


def snapshot(client):
    return {key: client.dump(key) for key in client.keys('*')}


@pytest.fixture
def case(asset_case, monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(state, '_client', lambda: client)
    spec = {'mode': 'production', 'format': 'shorts', 'duration_minutes': .5,
            'music': 'off', 'language': 'tr', 'topic': 'The original episode', 'workflow': 'auto',
            'production_channel_id': CHANNEL, 'production_connection_id': 'connection_AAAAA',
            'production_profile_revision': 'revision_original', 'publish_after_render': True,
            'production_series_id': 'original_series', 'production_topic_index': 3}
    source = {'task_id': TASK, 'kind': 'render', 'state': 'FAILURE', 'spec': spec,
              'parent_id': None, 'failure_stage': 'final_visual_qc', 'error': 'Original rejection',
              'private_history': {'preserve': [1, 2]}, 'paid_create_slots_used': 6}
    put(client, source)
    client.set(recovery.PROFILE_PREFIX + CHANNEL, json.dumps({'channel_id': CHANNEL,
        'production_enabled': True, 'profile_revision': 'revision_original', 'auto_publish': True}))
    client.set(recovery.CHANNEL_PREFIX + CHANNEL,
               json.dumps({'id': CHANNEL, 'connection_id': 'connection_AAAAA'}))
    client.sadd(recovery.CHANNEL_INDEX_KEY, CHANNEL)
    client.set(recovery.CREDENTIAL_PREFIX + CHANNEL, 'synthetic-unreadable-placeholder')
    client.set(recovery.AUTH_EPOCH_KEY, '1')
    asset_case.args['binding'] = recovery.context.resolve_selected_visual_binding(TASK, client=client)
    pointer = persist(asset_case)
    source['selected_visual_checkpoint'] = pointer
    put(client, source)
    return SimpleNamespace(client=client, assets=asset_case, pointer=pointer, spec=spec)


def prepare(case):
    return recovery.prepare_selected_visual_recovery(TASK)


def publish(case):
    pointer = prepare(case)
    result = recovery.publish_selected_visual_recovery(pointer)
    return pointer, result


def add_parent(case, mode='full'):
    parent = get(case.client)
    parent.update(task_id=ROOT)
    parent.pop('selected_visual_checkpoint')
    put(case.client, parent)
    if mode == 'repair':
        state.save_repair_checkpoint(ROOT, {'version': 1, 'source_task_id': ROOT, 'approved_package': {}})
    claim = state.claim_retry_dispatch(ROOT, TASK, TOKEN, allow_repair=mode == 'repair')
    assert claim['claimed'] and claim['mode'] == mode
    assert state.acquire_retry_child_execution(TASK, ROOT)
    assert state.mark_retry_dispatch(ROOT, TOKEN, 'dispatched')
    source = get(case.client)
    source['parent_id'] = ROOT
    if mode == 'repair':
        source['spec'].update(workflow='scene_repair', repair_source_task_id=ROOT)
    put(case.client, source)
    case.assets.args['binding'] = recovery.context.resolve_selected_visual_binding(TASK, client=case.client)
    case.pointer = persist(case.assets)
    source['selected_visual_checkpoint'] = case.pointer
    put(case.client, source)


def future_claim(case, pointer):
    """Model the future commissioned CAS proof; today no public API creates it.

    Existing Lua reserves the token/child/execution with a V5 fixture first.
    The future admission's extra immutable-record binding is then installed in
    this isolated Redis. The production V6 guard is never disabled or replaced.
    """
    publication_key = recovery.PUBLICATION_PREFIX + TASK
    publication = case.client.get(publication_key)
    record = recovery._load_prepared(pointer)
    case.client.delete(publication_key)
    case.client.set(state.REPAIR_CHECKPOINT_PREFIX + TASK, json.dumps({
        'version': 1, 'source_task_id': TASK,
        'approved_package': {'_recovered_generated_media': {'version': 5}}}))
    result = state.claim_retry_dispatch(TASK, CHILD, TOKEN, allow_repair=True)
    assert result['claimed'] and result['mode'] == 'repair'
    child_spec = {**get(case.client)['spec'], 'workflow': 'scene_repair', 'repair_source_task_id': TASK}
    put(case.client, {'task_id': CHILD, 'parent_id': TASK, 'kind': 'render', 'state': 'PROGRESS',
                      'spec': child_spec, 'progress': 17, 'stage': 'preparing'})
    assert state.acquire_retry_child_execution(CHILD, TASK)
    assert state.mark_retry_dispatch(TASK, TOKEN, 'dispatched')
    case.client.set(publication_key, publication)
    case.client.hset(state.RETRY_DISPATCH_PREFIX + TASK,
                     'selected_visual_recovery_sha256', recovery._sha(record))
    return child_spec, record['approved_package']


def test_prepare_preserves_full_package_voice_pointer_without_redis_or_qa_writes(case):
    before, original = snapshot(case.client), deepcopy(case.assets.args['package'])
    object_count = len(case.assets.client.objects)
    pointer = prepare(case)
    record = recovery._load_prepared(pointer)
    assert snapshot(case.client) == before
    assert len(case.assets.client.objects) == object_count + 1
    assert record['status'] == 'staged_private_checkpoint'
    assert {k: record[k] for k in recovery._FLAGS} == recovery._FLAGS
    package = record['approved_package']
    assert {k: v for k, v in package.items() if not k.startswith('_recovered_')} == {
        k: v for k, v in original.items() if not k.startswith('_recovered_')}
    assert package['server_extra'] == original['server_extra']
    assert package['_recovered_generated_media']['selected_checkpoint'] == case.pointer
    assert package['_recovered_voice']['selected_checkpoint'] == case.pointer
    assert package['_recovered_generated_media']['repair_scene_indices'] == [3, 4, 5]
    assert case.assets.args['package'] == original
    assert prepare(case) == pointer
    assert snapshot(case.client) == before
    assert not case.client.keys('*spend*') and not case.client.keys('*claim*')


@pytest.mark.parametrize('rejects', [[5], [1, 4], list(range(6))])
def test_any_nonempty_frozen_rejection_partition_is_private(case, rejects):
    case.assets.args['rejected_scene_indices'] = rejects
    for index, review in case.assets.args['final_reviews'].items():
        review['score'] = 50 if index in rejects else 92
    pointer = persist(case.assets)
    source = get(case.client)
    source['selected_visual_checkpoint'] = pointer
    put(case.client, source)
    prepared, result = publish(case)
    assert recovery._load_prepared(prepared)['repair_scene_indices'] == rejects
    assert result['dispatch_eligible'] is False and result['qa_approved'] is False


def test_create_only_publication_is_private_idempotent_and_preserves_history(case):
    source = get(case.client)
    pointer, result = publish(case)
    after = get(case.client)
    assert result['status'] == 'staged_private_checkpoint'
    assert after['repair_available'] is False
    assert {k: after[k] for k in source} == source
    assert case.client.ttl(recovery.PUBLICATION_PREFIX + TASK) == -1
    assert 0 < case.client.ttl(state.REPAIR_CHECKPOINT_PREFIX + TASK) <= state.REPAIR_CHECKPOINT_TTL_SECONDS
    assert case.client.ttl(state.JOB_PREFIX + TASK) > 0
    before = snapshot(case.client)
    assert recovery.publish_selected_visual_recovery(pointer)['status'] == 'already_staged_private_checkpoint'
    assert snapshot(case.client) == before
    assert not case.client.keys('*claim*') and not case.client.keys('*spend*')


@pytest.mark.parametrize('mode', ['full', 'repair'])
def test_reciprocal_failed_retry_source_has_original_root_binding(case, mode):
    add_parent(case, mode)
    before = snapshot(case.client)
    pointer = prepare(case)
    assert snapshot(case.client) == before
    record = recovery._load_prepared(pointer)
    assert record['binding']['source_task_id'] == TASK and record['binding']['lineage_id'] == ROOT
    assert record['binding'] == case.assets.args['binding']
    assert recovery.publish_selected_visual_recovery(pointer)['status'] == 'staged_private_checkpoint'
    for key, value in before.items():
        if key != state.JOB_PREFIX + TASK:
            assert case.client.dump(key) == value


@pytest.mark.parametrize('damage', ['state', 'stage', 'kind', 'topic', 'music', 'language', 'duration',
    'format', 'mode', 'revision', 'disabled', 'autopublish', 'connection', 'credential', 'membership',
    'epoch', 'channel', 'pointer', 'retry_claim', 'child', 'dispatch', 'repair_claim', 'hold',
    'cancel', 'external', 'upload', 'hold_field', 'cancel_field', 'orphan_claim', 'orphan_execution'])
def test_source_owner_or_identity_fences_block_without_any_mutation(case, damage):
    source = get(case.client)
    if damage == 'state': source['state'] = 'SUCCESS'
    elif damage == 'stage': source['failure_stage'] = 'audio_qc'
    elif damage == 'kind': source['kind'] = 'publish'
    elif damage == 'topic': source['spec']['topic'] = ' '
    elif damage == 'music': source['spec']['music'] = 'auto'
    elif damage == 'language': source['spec']['language'] = 'xx'
    elif damage == 'duration': source['spec']['duration_minutes'] = 8
    elif damage == 'format': source['spec']['format'] = 'long'
    elif damage == 'mode': source['spec']['mode'] = 'preview'
    elif damage == 'revision': source['spec']['production_profile_revision'] = 'other'
    elif damage in {'disabled', 'autopublish'}:
        profile = json.loads(case.client.get(recovery.PROFILE_PREFIX + CHANNEL))
        profile['production_enabled' if damage == 'disabled' else 'auto_publish'] = False
        case.client.set(recovery.PROFILE_PREFIX + CHANNEL, json.dumps(profile))
    elif damage == 'connection':
        case.client.set(recovery.CHANNEL_PREFIX + CHANNEL, json.dumps({'id': CHANNEL, 'connection_id': 'changed_connection'}))
    elif damage == 'credential': case.client.delete(recovery.CREDENTIAL_PREFIX + CHANNEL)
    elif damage == 'membership': case.client.srem(recovery.CHANNEL_INDEX_KEY, CHANNEL)
    elif damage == 'epoch': case.client.set(recovery.AUTH_EPOCH_KEY, 'invalid')
    elif damage == 'channel': case.client.delete(recovery.CHANNEL_PREFIX + CHANNEL)
    elif damage == 'pointer': source['selected_visual_checkpoint']['binding']['ancestry_sha256'] = 'b' * 64
    elif damage == 'retry_claim': source['retry_claimed'] = True
    elif damage == 'child': source['retry_child_task_id'] = CHILD
    elif damage == 'hold_field': source['publication_hold'] = {}
    elif damage == 'cancel_field': source['owner_cancellation'] = {}
    else:
        prefixes = {'dispatch': state.RETRY_DISPATCH_PREFIX, 'repair_claim': state.REPAIR_CHECKPOINT_CLAIM_PREFIX,
            'hold': recovery.HOLD_PREFIX, 'cancel': state.RENDER_CANCELLATION_PREFIX,
            'external': state.EXTERNAL_EPISODE_LEAF_PREFIX, 'upload': recovery.UPLOAD_PREFIX,
            'orphan_claim': state.RETRY_CHILD_CLAIM_PREFIX, 'orphan_execution': state.RETRY_CHILD_EXECUTION_PREFIX}
        case.client.set(prefixes[damage] + TASK, 'even-malformed-evidence-blocks')
    put(case.client, source)
    before, objects = snapshot(case.client), deepcopy(case.assets.client.objects)
    with pytest.raises(recovery.SelectedVisualRecoveryStateError, match='^selected_visual_recovery_unavailable$'):
        prepare(case)
    assert snapshot(case.client) == before and case.assets.client.objects == objects


@pytest.mark.parametrize('damage', ['parent', 'token', 'execution', 'mode', 'reciprocal', 'parent_hold', 'parent_cancel'])
def test_incomplete_or_fenced_retry_ancestry_cannot_become_private_recovery(case, damage):
    add_parent(case)
    if damage == 'parent': case.client.delete(state.JOB_PREFIX + ROOT)
    elif damage == 'token': case.client.hset(state.RETRY_CHILD_CLAIM_PREFIX + TASK, 'token', 'other')
    elif damage == 'execution': case.client.delete(state.RETRY_CHILD_EXECUTION_PREFIX + TASK)
    elif damage == 'mode': case.client.hset(state.RETRY_DISPATCH_PREFIX + ROOT, 'mode', 'unknown')
    elif damage == 'reciprocal':
        parent = get(case.client, ROOT); parent['retry_child_task_id'] = CHILD; put(case.client, parent)
    elif damage == 'parent_hold': case.client.set(recovery.HOLD_PREFIX + ROOT, '{}')
    else: case.client.set(state.RENDER_CANCELLATION_PREFIX + ROOT, '{}')
    before = snapshot(case.client)
    with pytest.raises(recovery.SelectedVisualRecoveryStateError): prepare(case)
    assert snapshot(case.client) == before


@pytest.mark.parametrize('damage', ['source', 'pointer', 'profile', 'audio', 'video', 'prepared', 'existing_checkpoint', 'claim'])
def test_changed_evidence_after_prepare_never_overwrites_current_private_state(case, damage):
    pointer = prepare(case)
    if damage == 'source':
        source = get(case.client); source['error'] = 'Another final failure'; put(case.client, source)
    elif damage == 'pointer':
        source = get(case.client); source['selected_visual_checkpoint']['manifest_sha256'] = 'b' * 64; put(case.client, source)
    elif damage == 'profile':
        profile = json.loads(case.client.get(recovery.PROFILE_PREFIX + CHANNEL)); profile['owner_edit'] = 1
        case.client.set(recovery.PROFILE_PREFIX + CHANNEL, json.dumps(profile))
    elif damage in {'audio', 'video'}:
        manifest = recovery._manifest(get(case.client), case.assets.args['binding'])
        key = manifest['audio']['key'] if damage == 'audio' else manifest['scenes'][0]['asset']['key']
        raw, mime = case.assets.client.objects[key]; case.assets.client.objects[key] = (raw + b'drift', mime)
    elif damage == 'prepared':
        raw, mime = case.assets.client.objects[pointer['key']]; case.assets.client.objects[pointer['key']] = (raw + b' ', mime)
    elif damage == 'existing_checkpoint': case.client.set(state.REPAIR_CHECKPOINT_PREFIX + TASK, '{"legacy":"keep"}')
    else: case.client.set(state.REPAIR_CHECKPOINT_CLAIM_PREFIX + TASK, 'existing-claim')
    before = snapshot(case.client)
    with pytest.raises(recovery.SelectedVisualRecoveryStateError): recovery.publish_selected_visual_recovery(pointer)
    assert snapshot(case.client) == before


@pytest.mark.parametrize('phase', ['prepare', 'publish'])
@pytest.mark.parametrize('key', ['source', 'profile', 'channel', 'epoch', 'credential', 'hold', 'cancel', 'dispatch'])
def test_concurrent_owner_or_claim_change_rejects_watched_snapshot(case, monkeypatch, phase, key):
    pointer = prepare(case) if phase == 'publish' else None
    keys = {'source': state.JOB_PREFIX + TASK, 'profile': recovery.PROFILE_PREFIX + CHANNEL,
            'channel': recovery.CHANNEL_PREFIX + CHANNEL, 'epoch': recovery.AUTH_EPOCH_KEY,
            'credential': recovery.CREDENTIAL_PREFIX + CHANNEL, 'hold': recovery.HOLD_PREFIX + TASK,
            'cancel': state.RENDER_CANCELLATION_PREFIX + TASK, 'dispatch': state.RETRY_DISPATCH_PREFIX + TASK}
    original = case.client.pipeline
    def pipeline(*args, **kwargs):
        pipe = original(*args, **kwargs)
        execute = pipe.execute
        def raced(*args, **kwargs):
            case.client.set(keys[key], 'changed-concurrently')
            return execute(*args, **kwargs)
        monkeypatch.setattr(pipe, 'execute', raced)
        return pipe
    monkeypatch.setattr(case.client, 'pipeline', pipeline)
    with pytest.raises(recovery.SelectedVisualRecoveryStateError):
        prepare(case) if phase == 'prepare' else recovery.publish_selected_visual_recovery(pointer)
    assert not case.client.exists(recovery.PUBLICATION_PREFIX + TASK, state.REPAIR_CHECKPOINT_PREFIX + TASK,
                                  state.REPAIR_CHECKPOINT_CLAIM_PREFIX + TASK, state.RETRY_CHILD_CLAIM_PREFIX + CHILD)
    assert case.client.get(keys[key]) == 'changed-concurrently'


def test_lost_publication_reply_keeps_checkpoint_and_replay_is_idempotent(case, monkeypatch):
    pointer = prepare(case)
    original = case.client.pipeline
    def pipeline(*args, **kwargs):
        pipe = original(*args, **kwargs)
        execute = pipe.execute
        def lost(*args, **kwargs):
            execute(*args, **kwargs)
            raise RuntimeError('SECRET lost reply')
        monkeypatch.setattr(pipe, 'execute', lost)
        return pipe
    monkeypatch.setattr(case.client, 'pipeline', pipeline)
    with pytest.raises(recovery.SelectedVisualRecoveryStateError, match='^selected_visual_recovery_unavailable$'):
        recovery.publish_selected_visual_recovery(pointer)
    monkeypatch.setattr(case.client, 'pipeline', original)
    before = snapshot(case.client)
    assert recovery.publish_selected_visual_recovery(pointer)['status'] == 'already_staged_private_checkpoint'
    assert snapshot(case.client) == before and not case.client.keys('*claim*')


@pytest.mark.parametrize('receipt', [None, [], [1], [False], [True, True], [1, 1, 1], [True, False, True]])
@pytest.mark.parametrize('phase', ['prepare', 'publish', 'verify'])
def test_missing_or_unexpected_transaction_receipt_is_not_success(case, monkeypatch, phase, receipt):
    pointer = prepare(case) if phase == 'publish' else None
    if phase == 'verify':
        pointer, _ = publish(case)
        spec, package = future_claim(case, pointer)
    original = case.client.pipeline
    def pipeline(*args, **kwargs):
        pipe = original(*args, **kwargs)
        monkeypatch.setattr(pipe, 'execute', lambda: receipt)
        return pipe
    monkeypatch.setattr(case.client, 'pipeline', pipeline)
    before = snapshot(case.client)
    with pytest.raises(recovery.SelectedVisualRecoveryStateError):
        if phase == 'prepare': prepare(case)
        elif phase == 'publish': recovery.publish_selected_visual_recovery(pointer)
        else: recovery.verify_selected_recovery_child(CHILD, TASK, spec, package)
    assert snapshot(case.client) == before


@pytest.mark.parametrize('allow_repair', [True, False])
@pytest.mark.parametrize('damage', ['none', 'expired_checkpoint', 'lost_receipt', 'lost_receipt_voice_only'])
def test_real_generic_retry_never_consumes_staged_v6_or_falls_back_to_full(case, allow_repair, damage):
    publish(case)
    if damage == 'expired_checkpoint': case.client.delete(state.REPAIR_CHECKPOINT_PREFIX + TASK)
    if damage.startswith('lost_receipt'): case.client.delete(recovery.PUBLICATION_PREFIX + TASK)
    if damage == 'lost_receipt_voice_only':
        raw = json.loads(case.client.get(state.REPAIR_CHECKPOINT_PREFIX + TASK))
        raw['approved_package'].pop('_recovered_generated_media')
        case.client.set(state.REPAIR_CHECKPOINT_PREFIX + TASK, json.dumps(raw))
    before = snapshot(case.client)
    with pytest.raises(ValueError, match='awaits commissioned quality and spending admission'):
        state.claim_retry_dispatch(TASK, CHILD, TOKEN, allow_repair=allow_repair)
    assert snapshot(case.client) == before


@pytest.mark.parametrize('receipt_present', [True, False])
def test_legacy_consumer_cannot_delete_staged_v6_or_create_claim(case, receipt_present):
    publish(case)
    if not receipt_present: case.client.delete(recovery.PUBLICATION_PREFIX + TASK)
    before = snapshot(case.client)
    assert state.consume_repair_checkpoint(TASK) is None
    assert snapshot(case.client) == before


@pytest.mark.parametrize('checkpoint_present', [True, False])
@pytest.mark.parametrize('receipt_present', [True, False])
def test_dashboard_sync_uses_staging_receipt_without_advertising_repair(case, checkpoint_present, receipt_present):
    pointer, _ = publish(case)
    if not checkpoint_present: case.client.delete(state.REPAIR_CHECKPOINT_PREFIX + TASK)
    if not receipt_present: case.client.delete(recovery.PUBLICATION_PREFIX + TASK)
    source = get(case.client); source['repair_available'] = True; put(case.client, source)
    assert state.sync_repair_checkpoint_state(TASK) == {'repair_available': False, 'repair_claimed': False}
    assert get(case.client)['repair_available'] is False
    assert case.client.exists(state.REPAIR_CHECKPOINT_PREFIX + TASK) == checkpoint_present
    assert not case.client.keys('*claim*')
    if not checkpoint_present and receipt_present:
        with pytest.raises(recovery.SelectedVisualRecoveryStateError): recovery.publish_selected_visual_recovery(pointer)
        assert not case.client.exists(state.REPAIR_CHECKPOINT_PREFIX + TASK)


@pytest.mark.parametrize('version', range(1, 6))
def test_real_legacy_retry_and_execution_claims_are_unchanged(case, version):
    package = {'_recovered_generated_media': {'version': version}}
    checkpoint = {'version': 1, 'source_task_id': TASK, 'approved_package': package}
    state.save_repair_checkpoint(TASK, checkpoint)
    assert state.sync_repair_checkpoint_state(TASK)['repair_available'] is True
    result = state.claim_retry_dispatch(TASK, CHILD, TOKEN, allow_repair=True)
    assert result['claimed'] and result['checkpoint'] == checkpoint
    assert state.acquire_retry_child_execution(CHILD, TASK)
    assert not state.acquire_retry_child_execution(CHILD, TASK)
    assert not state.claim_retry_dispatch(TASK, ROOT, TOKEN, allow_repair=True)['claimed']


def test_future_worker_verification_requires_bound_real_dispatch_claim_execution_and_package(case):
    pointer, _ = publish(case)
    spec, package = future_claim(case, pointer)
    before, objects = snapshot(case.client), deepcopy(case.assets.client.objects)
    gets = len(case.assets.client.gets)
    assert recovery.verify_selected_recovery_child(CHILD, TASK, spec, package) == {'binding': case.assets.args['binding']}
    assert snapshot(case.client) == before and case.assets.client.objects == objects
    child = get(case.client, CHILD); child.update(stage='final_quality', progress=90); put(case.client, child)
    assert recovery.verify_selected_recovery_child(CHILD, TASK, spec, package)['binding'] == case.assets.args['binding']
    # Repeated authorization does not redownload raw clips, voice, or manifest.
    # The separate materializer/exact-cut helpers own those byte proofs.
    assert case.assets.client.gets[gets:] == [pointer['key'], pointer['key']]


@pytest.mark.parametrize('damage', ['no_claim', 'no_execution', 'token', 'binding', 'no_binding', 'child', 'mode',
    'reciprocal', 'checkpoint_reappears', 'runtime_spec', 'child_spec', 'package', 'voice', 'rejects',
    'child_hold', 'source_hold', 'child_cancel', 'source_cancel', 'child_success', 'source_changed'])
def test_worker_cannot_use_unbound_claim_or_changed_original_content(case, damage):
    pointer, _ = publish(case)
    spec, package = future_claim(case, pointer)
    if damage == 'no_claim': case.client.delete(state.RETRY_CHILD_CLAIM_PREFIX + CHILD)
    elif damage == 'no_execution': case.client.delete(state.RETRY_CHILD_EXECUTION_PREFIX + CHILD)
    elif damage == 'token': case.client.set(state.RETRY_CHILD_EXECUTION_PREFIX + CHILD, 'another-token')
    elif damage == 'binding': case.client.hset(state.RETRY_DISPATCH_PREFIX + TASK, 'selected_visual_recovery_sha256', 'b' * 64)
    elif damage == 'no_binding': case.client.hdel(state.RETRY_DISPATCH_PREFIX + TASK, 'selected_visual_recovery_sha256')
    elif damage == 'child': case.client.hset(state.RETRY_DISPATCH_PREFIX + TASK, 'child_task_id', ROOT)
    elif damage == 'mode': case.client.hset(state.RETRY_DISPATCH_PREFIX + TASK, 'mode', 'full')
    elif damage == 'reciprocal':
        source = get(case.client); source['retry_child_task_id'] = ROOT; put(case.client, source)
    elif damage == 'checkpoint_reappears': case.client.set(state.REPAIR_CHECKPOINT_PREFIX + TASK, 'resurrected')
    elif damage == 'runtime_spec': spec['topic'] = 'other episode'
    elif damage == 'child_spec':
        child = get(case.client, CHILD); child['spec']['topic'] = 'other episode'; put(case.client, child)
    elif damage == 'package': package['scenes'][0]['narration'] = 'changed original words'
    elif damage == 'voice': package['_recovered_voice']['selected_checkpoint']['manifest_sha256'] = 'b' * 64
    elif damage == 'rejects': package['_recovered_generated_media']['repair_scene_indices'] = [0, 1, 2]
    elif damage.endswith('_hold'):
        case.client.set(recovery.HOLD_PREFIX + (CHILD if damage.startswith('child') else TASK), '{}')
    elif damage.endswith('_cancel'):
        case.client.set(state.RENDER_CANCELLATION_PREFIX + (CHILD if damage.startswith('child') else TASK), '{}')
    elif damage == 'child_success':
        child = get(case.client, CHILD); child['state'] = 'SUCCESS'; put(case.client, child)
    else:
        source = get(case.client); source['error'] = 'new failure'; put(case.client, source)
    before = snapshot(case.client)
    with pytest.raises(recovery.SelectedVisualRecoveryStateError):
        recovery.verify_selected_recovery_child(CHILD, TASK, spec, package)
    assert snapshot(case.client) == before


@pytest.mark.parametrize('target', ['source', 'child', 'parent', 'profile', 'channel', 'epoch', 'credential',
                                   'child_hold', 'source_hold', 'child_cancel', 'execution', 'dispatch', 'publication'])
def test_worker_verification_is_read_only_and_detects_control_state_race(case, monkeypatch, target):
    add_parent(case)
    pointer, _ = publish(case)
    spec, package = future_claim(case, pointer)
    keys = {'source': state.JOB_PREFIX + TASK, 'child': state.JOB_PREFIX + CHILD, 'parent': state.JOB_PREFIX + ROOT,
        'profile': recovery.PROFILE_PREFIX + CHANNEL, 'channel': recovery.CHANNEL_PREFIX + CHANNEL,
        'epoch': recovery.AUTH_EPOCH_KEY, 'credential': recovery.CREDENTIAL_PREFIX + CHANNEL,
        'child_hold': recovery.HOLD_PREFIX + CHILD, 'source_hold': recovery.HOLD_PREFIX + TASK,
        'child_cancel': state.RENDER_CANCELLATION_PREFIX + CHILD,
        'execution': state.RETRY_CHILD_EXECUTION_PREFIX + CHILD,
        'dispatch': state.RETRY_DISPATCH_PREFIX + TASK, 'publication': recovery.PUBLICATION_PREFIX + TASK}
    original = case.client.pipeline
    observed = []
    def pipeline(*args, **kwargs):
        pipe = original(*args, **kwargs)
        execute = pipe.execute
        def raced(*args, **kwargs):
            case.client.set(keys[target], 'changed-control-state')
            observed.append(snapshot(case.client))
            return execute(*args, **kwargs)
        monkeypatch.setattr(pipe, 'execute', raced)
        return pipe
    monkeypatch.setattr(case.client, 'pipeline', pipeline)
    with pytest.raises(recovery.SelectedVisualRecoveryStateError):
        recovery.verify_selected_recovery_child(CHILD, TASK, spec, package)
    assert observed and snapshot(case.client) == observed[0]
