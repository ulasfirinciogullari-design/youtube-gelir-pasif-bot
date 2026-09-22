"""Unapproved stock bytes, attribution and durable replay boundaries."""
from copy import deepcopy
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import included_stock_pool as pool, production_spend_runtime as runtime
from app.services import production_included_router as included, storage
from app.services.production_spend import SpendBlocked
from test_production_included_router import commissioned, CONTEXT, CHANNEL
from test_production_credit_ledger import client

TASK = '33333333-3333-4333-8333-333333333333'


@pytest.fixture
def case(commissioned, monkeypatch, tmp_path):
    ledger, _, _ = commissioned
    monkeypatch.setattr(runtime.settings, 'studio_spend_enforcement', True)
    monkeypatch.setattr(runtime.settings, 'studio_abacus_included_production', True)
    monkeypatch.setattr(runtime, 'configured_ledger', lambda: ledger.foundation)
    monkeypatch.setattr(runtime, 'resolve_context', lambda *_: deepcopy(CONTEXT))
    ledger.client.set(runtime._JOB_PREFIX + TASK, json.dumps({'task_id': TASK, 'parent_id': CONTEXT['lineage_id']}))
    ledger.client.set(runtime._JOB_PREFIX + CONTEXT['lineage_id'], json.dumps({'task_id': CONTEXT['lineage_id'], 'parent_id': None}))
    token = runtime._TASK_ID.set(TASK)
    objects, uploads, downloads = {}, [], []
    def upload(path, key, content_type):
        assert content_type == 'video/mp4'
        uploads.append(key); objects[key] = Path(path).read_bytes()
    def get(**kwargs):
        key = kwargs['Key']; downloads.append(key)
        raw = objects[key]
        return {'Body': io.BytesIO(raw), 'ContentLength': len(raw)}
    monkeypatch.setattr(storage, 'upload_file', upload)
    monkeypatch.setattr(storage, '_client', lambda **_: SimpleNamespace(get_object=get))
    package = {'title': 'Coin production costs', 'scenes': [
        {'index': 0, 'narration': 'Existing narration.', 'ai_prompt': None, 'visual_queries': ['penny coins']}],
        'sources': []}
    yield SimpleNamespace(ledger=ledger, root=tmp_path, package=package,
                          objects=objects, uploads=uploads, downloads=downloads)
    runtime._TASK_ID.reset(token)


def inputs(case, name, value, asset_id):
    work = case.root / name; work.mkdir()
    path = work / 'stock.mp4'; path.write_bytes(value * 2048)
    spec = {'path': str(path), 'pexels_id': asset_id, 'start_fraction': .25,
            'source_duration': 12.0, 'source_type': 'stock', 'stock_provider': 'pexels'}
    credits = [{'source': 'Pexels', 'pexels_id': asset_id, 'creator_name': name}]
    return work, [[spec]], credits, {asset_id}


def run(case, data, **options):
    work, visuals, credits, ids = data
    pool.retain_stock_pool(TASK, case.package, visuals, credits, ids, work, phase=options.get('phase', 'initial'))


def test_retry_restores_exact_pool_and_attribution_without_new_upload_or_budget_use(case):
    first = inputs(case, 'first', b'A', 101)
    before = case.ledger.foundation.snapshot()
    run(case, first)
    retry = inputs(case, 'retry', b'B', 202)
    run(case, retry)
    assert len(case.uploads) == len(case.downloads) == 1
    assert Path(retry[1][0][0]['path']).read_bytes() == b'A' * 2048
    assert retry[1][0][0]['pexels_id'] == 101
    assert retry[2] == first[2] and retry[3] == {101}
    assert case.ledger.foundation.snapshot() == before
    key = next(case.ledger.client.scan_iter(match=pool.PREFIX + '*'))
    assert case.ledger.client.pttl(key) == -1
    value = json.loads(included._cipher().decrypt(case.ledger.client.get(key).encode()))
    assert value['status'] == 'unapproved_stock_pool' and value['qa_approved'] is False
    assert case.ledger.client.get(included.JOURNAL_KEY) == '{"requests":{},"version":1}'


@pytest.mark.parametrize('failure', ['missing_asset', 'changed_asset', 'manifest', 'channel', 'expiration', 'missing_pointer'])
def test_unavailable_or_changed_saved_pool_never_regenerates_candidates(case, failure):
    run(case, inputs(case, 'first', b'A', 101))
    client = case.ledger.client
    key = next(client.scan_iter(match=pool.PREFIX + '*'))
    if failure == 'missing_asset': case.objects.clear()
    elif failure == 'changed_asset': case.objects[next(iter(case.objects))] = b'C' * 2048
    elif failure == 'manifest': client.set(key, 'damaged ciphertext')
    elif failure == 'expiration': client.pexpire(key, 60000)
    elif failure == 'missing_pointer': client.delete(key)
    else: client.set(runtime._CHANNEL_PREFIX + CHANNEL, json.dumps({'id': CHANNEL, 'connection_id': 'changed-connection'}))
    with pytest.raises(SpendBlocked, match='stock_pool_unverified'):
        run(case, inputs(case, 'retry', b'B', 202))
    assert len(case.uploads) == 1


@pytest.mark.parametrize('invalid', ['symlink', 'outside', 'generated', 'missing_scene', 'bad_id', 'truncated'])
def test_invalid_pool_stops_before_upload_or_review(case, invalid, monkeypatch):
    data = inputs(case, 'first', b'A', 101)
    work, visuals, _, _ = data
    if invalid == 'symlink':
        link = work / 'link.mp4'; link.symlink_to(visuals[0][0]['path']); visuals[0][0]['path'] = str(link)
    elif invalid == 'outside':
        other = case.root / 'outside.mp4'; other.write_bytes(b'A' * 2048); visuals[0][0]['path'] = str(other)
    elif invalid == 'generated': visuals[0][0]['source_type'] = 'generated'
    elif invalid == 'missing_scene': visuals.clear()
    elif invalid == 'bad_id': visuals[0][0]['pexels_id'] = None
    else: Path(visuals[0][0]['path']).write_bytes(b'bad')
    with pytest.raises(SpendBlocked, match='stock_pool_unverified'): run(case, data)
    assert not case.uploads and not list(case.ledger.client.scan_iter(match=pool.PREFIX + '*'))


def test_upload_failure_does_not_create_a_false_checkpoint(case, monkeypatch):
    def fail(*_): raise ConnectionError('Storage unavailable')
    monkeypatch.setattr(storage, 'upload_file', fail)
    with pytest.raises(SpendBlocked): run(case, inputs(case, 'first', b'A', 101))
    assert not list(case.ledger.client.scan_iter(match=pool.PREFIX + '*'))


@pytest.mark.parametrize('phase', ['read', 'manifest', 'bookmark'])
def test_definite_redis_conflict_retries_only_local_transaction_without_upload_replay(case, monkeypatch, phase):
    original = pool._ack
    conflicts = []
    def race(pipe):
        commands = [command for command, _ in pipe.command_stack]
        match = (phase == 'read' and commands[0][0] == 'PING'
            or phase == 'manifest' and commands[0][0] == 'SET' and commands[0][1].startswith(pool.PREFIX)
            or phase == 'bookmark' and commands[0][0] == 'SET' and commands[0][1] == runtime._JOB_PREFIX + TASK)
        if match and not conflicts:
            key = runtime._JOB_PREFIX + TASK
            job = json.loads(case.ledger.client.get(key));job['concurrent_observation'] = True
            case.ledger.client.set(key, json.dumps(job));conflicts.append(phase)
        original(pipe)
    monkeypatch.setattr(pool, '_ack', race)
    first = inputs(case, 'first', b'A', 101);run(case, first)
    assert conflicts == [phase] and len(case.uploads) == 1
    retry = inputs(case, 'retry', b'B', 202);run(case, retry)
    assert len(case.uploads) == 1 and Path(retry[1][0][0]['path']).read_bytes() == b'A' * 2048


@pytest.mark.parametrize('failure', ['lost_connection', 'caused', 'too_many_conflicts'])
def test_ambiguous_transaction_outcomes_never_trigger_a_transport_retry(failure):
    from redis.exceptions import WatchError
    calls = []
    @pool._local_transaction
    def operation():
        calls.append(1)
        if failure == 'lost_connection': raise WatchError('ConnectionError while watching keys')
        if failure == 'caused': raise WatchError('Watched variable changed.') from ConnectionError('Lost ACK')
        raise WatchError('Watched variable changed.')
    with pytest.raises(WatchError): operation()
    assert len(calls) == (8 if failure == 'too_many_conflicts' else 1)


def test_preservation_diagnostics_do_not_log_storage_messages_or_paths(case, monkeypatch, caplog):
    def fail(*_): raise ConnectionError('PRIVATE_STORAGE_CREDENTIAL_NOT_FOR_LOGS')
    monkeypatch.setattr(storage, 'upload_file', fail)
    with pytest.raises(SpendBlocked): run(case, inputs(case, 'first', b'A', 101))
    assert 'PRIVATE_STORAGE_CREDENTIAL_NOT_FOR_LOGS' not in caplog.text
    assert 'Stock pool preservation stopped' in caplog.text and 'ConnectionError' in caplog.text


@pytest.mark.parametrize('phase', ['before_generation', 'budget_rescue'])
def test_each_phase_preserves_its_own_exact_pool(case, phase):
    first = inputs(case, 'initial', b'A', 101)
    second = inputs(case, 'changed', b'B', 202)
    run(case, first); run(case, second, phase=phase)
    retry = inputs(case, 'retry', b'C', 303)
    run(case, retry, phase=phase)
    assert Path(retry[1][0][0]['path']).read_bytes() == b'B' * 2048
    assert retry[2] == second[2] and retry[3] == {202}
    assert len(case.uploads) == 2


def test_lost_cache_write_ack_reuses_only_the_committed_pool(case, monkeypatch):
    original = pool._ack
    def lose(pipe):
        storing = any(command[0] == 'SET' for command, _ in pipe.command_stack)
        original(pipe)
        if storing: raise ConnectionError('Lost cache ACK')
    monkeypatch.setattr(pool, '_ack', lose)
    with pytest.raises(SpendBlocked): run(case, inputs(case, 'first', b'A', 101))
    monkeypatch.setattr(pool, '_ack', original)
    retry = inputs(case, 'retry', b'B', 202)
    run(case, retry)
    assert len(case.uploads) == 1 and Path(retry[1][0][0]['path']).read_bytes() == b'A' * 2048


def test_restored_real_movie_frames_reuse_the_actual_review_receipt(case, monkeypatch):
    import base64
    import subprocess
    from app.services import production_included_transport as transport, visual_qc
    from test_abacus_router_adapter import KEY, envelope, response
    from test_visual_cross_provider_review import _namespace, _review
    monkeypatch.setattr(runtime.settings, 'abacus_api_key', KEY)
    first = inputs(case, 'first', b'A', 101)
    second = inputs(case, 'retry', b'B', 202)
    for data, color in ((first, 'blue'), (second, 'red')):
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
            f'color=c={color}:s=128x192:r=24:d=5', '-an', '-threads', '1',
            '-c:v', 'libx264', data[1][0][0]['path']], check=True, timeout=15)
    calls = []
    ns = _namespace()
    judgment = {'reviews': [_review(ns, score=35, subject_visible=False)]}
    def send(prepared):
        calls.append(prepared)
        payload = envelope(); payload['choices'][0]['message']['content'] = json.dumps(judgment)
        return response(prepared, payload=payload)
    monkeypatch.setattr(transport, 'send_once', send)
    def review(data):
        run(case, data)
        frame = visual_qc._frame(data[1][0][0]['path'], data[0] / 'frame.jpg', .5)
        assert frame is not None
        content = [{'type': 'input_text', 'text': 'Separate rubric.'},
            {'type': 'input_image', 'image_url': 'data:image/jpeg;base64,' + base64.b64encode(frame.read_bytes()).decode()}]
        return ns['_request_visual_review']('abacus_included', True, 'Honest visual review.',
            content, [], [0], {0: {0: {0, 1, 2}}}, None, 'low')
    assert review(first) == review(second) == judgment
    assert len(calls) == 1 and len(case.uploads) == 1


@pytest.mark.parametrize('cache_missing', [False, True])
def test_child_inherits_saved_pool_witness_and_never_ignores_cache_loss(case, cache_missing):
    run(case, inputs(case, 'parent', b'A', 101))
    client = case.ledger.client
    key = next(client.scan_iter(match=pool.PREFIX + '*'))
    if cache_missing:
        client.delete(key)
    child = '44444444-4444-4444-8444-444444444444'
    client.set(runtime._JOB_PREFIX + child, json.dumps({'task_id': child, 'parent_id': TASK}))
    work, visuals, credits, ids = inputs(case, 'child', b'B', 202)
    token = runtime._TASK_ID.set(child)
    try:
        if cache_missing:
            with pytest.raises(SpendBlocked):
                pool.retain_stock_pool(child, case.package, visuals, credits, ids, work, phase='initial')
        else:
            pool.retain_stock_pool(child, case.package, visuals, credits, ids, work, phase='initial')
            assert Path(visuals[0][0]['path']).read_bytes() == b'A' * 2048
            assert json.loads(client.get(runtime._JOB_PREFIX + child))['included_stock_pools']
    finally:
        runtime._TASK_ID.reset(token)
    assert len(case.uploads) == 1 and bool(client.exists(key)) is not cache_missing


def test_empty_candidate_slot_is_preserved_for_existing_duration_refill(case):
    first = inputs(case, 'initial', b'A', 101)
    first[1][0] = []
    run(case, first)
    second = inputs(case, 'retry', b'B', 202)
    run(case, second)
    assert second[1] == [[]] and not case.uploads and not case.downloads
    assert second[2] == first[2] and second[3] == first[3]
