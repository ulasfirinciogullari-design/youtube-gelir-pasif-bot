import ast
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
from threading import Event
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import Mock

import fakeredis
import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = '4ec31182-4637-4030-b2f2-1056284ca13f'
PUBLISHER = '50100000-0000-4000-8000-000000000000'
VIDEO = 'hGsspOyWEIU'
CHANNEL = 'UC5v9AvNtD3PTLgo6m1jROOA'
CONNECTION = 'existing-connection-generation'
OLD_REV = 'original-private-revision'
NEW_REV = 'current-public-revision'


def _load(name, namespace):
    path = ROOT / 'app' / 'services' / (name + '.py')
    tree = ast.parse(path.read_text(encoding='utf-8'))
    tree.body = [node for node in tree.body if not (
        isinstance(node, ast.ImportFrom) and (node.module or '').startswith('app.')
    )]
    module = ModuleType(name)
    module.__dict__.update(namespace)
    exec(compile(tree, str(path), 'exec'), module.__dict__)
    return module


def _write(client, key, value):
    client.set(key, json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')))


def _edit(client, key, path, value):
    record = json.loads(client.get(key))
    current = record
    for part in path[:-1]:
        current = current[part]
    current[path[-1]] = value
    _write(client, key, record)


@pytest.fixture
def case():
    client = fakeredis.FakeRedis(decode_responses=True)
    settings = SimpleNamespace(redis_url='redis://not-used')
    state = _load('youtube_publish_state', {'settings': settings})
    state._redis = lambda: client
    ns = {key: getattr(state, key) for key in (
        'UPLOAD_PREFIX', 'EXECUTION_LOCK_PREFIX', 'UPLOAD_RECORD_TTL_SECONDS',
        'acquire_execution_lock', 'release_execution_lock',
        'mark_release_completed', 'mark_release_uncertain',
    )}
    ns.update(settings=settings, Any=Any, JOB_PREFIX='youtube_studio:job:', JOB_TTL_SECONDS=7776000,
              PROFILE_PREFIX='youtube_studio:youtube_profile:v1:',
              CHANNEL_PREFIX='youtube_studio:oauth:channel:v3:',
              CREDENTIAL_PREFIX='youtube_studio:oauth:credential:v3:',
              CHANNEL_INDEX_KEY='youtube_studio:oauth:channels:v3',
              AUTH_EPOCH_KEY='youtube_studio:oauth:authorization_epoch:v2')
    # Execute the real, dependency-free production quality gate, not a permissive mock.
    tree = ast.parse((ROOT / 'app/services/youtube_automation.py').read_text(encoding='utf-8'))
    gate = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                and node.name == 'automated_quality_approved')
    exec(compile(ast.Module(body=[gate], type_ignores=[]), '<real-quality-gate>', 'exec'), ns)
    module = _load('existing_video_release', ns)
    module._redis = lambda: client
    credentials = object()
    module.load_credentials = Mock(return_value=credentials)
    channel = {'id': CHANNEL, 'connection_id': CONNECTION}
    module.refresh_channel_info = Mock(return_value=channel)
    module.get_video_status_with_credentials = Mock(return_value={
        'privacyStatus': 'private', 'uploadStatus': 'processed',
    })
    module.set_video_release_with_credentials = Mock(return_value={
        'id': VIDEO, 'status': {'privacyStatus': 'public', 'containsSyntheticMedia': True},
    })
    binding = {'target_channel_id': CHANNEL, 'connection_id': CONNECTION, 'profile_revision': OLD_REV}
    private = {'privacy_status': 'private', 'release_status': 'private', 'release_error_code': None,
               'scheduled_publish_at': None}
    spec = {'topic': 'Source-backed money documentary', 'mode': 'production', 'duration_minutes': 0.5,
            'production_channel_id': CHANNEL, 'production_connection_id': CONNECTION,
            'production_profile_revision': OLD_REV, 'publish_after_render': True}
    source = {
        'task_id': SOURCE, 'kind': 'render', 'state': 'SUCCESS', 'spec': spec,
        'parent_id': '80900000-0000-4000-8000-000000000000',
        'result': {'task_id': SOURCE, 'video_key': 'videos/original-approved-final.mp4',
                   'quality_disposition': 'automated_qc_pass', 'manual_qa_required': False,
                   'unrelated_render_proof': {'score': 88.3},
                   'youtube': {'video_id': VIDEO, **binding, **private,
                               'caption_uploaded': True, 'caption_error_code': None,
                               'caption_recovered': True, 'thumbnail_uploaded': True},
                   'youtube_automation': {'status': 'queued', 'publish_task_id': PUBLISHER}},
    }
    publisher = {
        'task_id': PUBLISHER, 'kind': 'publish', 'state': 'SUCCESS', 'parent_id': SOURCE,
        'spec': {'source_task_id': SOURCE, 'release_mode': 'private', 'privacy_status': 'private', **binding},
        'result': {'status': 'complete', 'source_task_id': SOURCE, 'task_id': PUBLISHER,
                   'youtube_video_id': VIDEO, **binding, **private,
                   'caption_uploaded': False, 'caption_error_code': 'caption_video_not_found'},
    }
    plan = {'source_task_id': SOURCE, 'target_channel_id': CHANNEL, 'profile_revision': OLD_REV,
            'release_mode': 'private', 'publish_at': None, 'require_thumbnail': True,
            'title': 'Original title 1/8', 'description': 'Approved description', 'tags': ['money']}
    ledger = {'version': 2, 'source_task_id': SOURCE, 'publish_task_id': PUBLISHER, 'status': 'complete',
              'side_effect_possible': True, 'youtube_video_id': VIDEO,
              'target_channel_id': CHANNEL, 'connection_id': CONNECTION,
              'requested_release_mode': 'private', 'requested_publish_at': None,
              'release_status': 'private', 'release_side_effect_possible': False, 'publish_plan': plan}
    keys = {'source': module.JOB_PREFIX + SOURCE, 'publisher': module.JOB_PREFIX + PUBLISHER,
            'ledger': module.UPLOAD_PREFIX + SOURCE, 'profile': module.PROFILE_PREFIX + CHANNEL,
            'channel': module.CHANNEL_PREFIX + CHANNEL, 'credential': module.CREDENTIAL_PREFIX + CHANNEL,
            'lock': module.EXECUTION_LOCK_PREFIX + SOURCE}
    for name, value in {'source': source, 'publisher': publisher, 'ledger': ledger,
                        'profile': {'channel_id': CHANNEL, 'profile_revision': NEW_REV,
                                    'release_mode': 'public', 'auto_publish': True,
                                    'require_thumbnail': False}, 'channel': channel}.items():
        _write(client, keys[name], value)
    client.set(keys['credential'], 'opaque-encrypted-fixture')
    client.sadd(module.CHANNEL_INDEX_KEY, CHANNEL)
    client.set('immutable-paid-ledger', 'unchanged')
    client.set('immutable-schedule', 'unchanged')
    return SimpleNamespace(module=module, client=client, state=state, keys=keys,
                           source=source, publisher=publisher, plan=plan, credentials=credentials)


def _release(case, **kwargs):
    return case.module.release_existing_private_video(**{
        'source_task_id': SOURCE, 'expected_video_id': VIDEO,
        'expected_channel_id': CHANNEL, 'expected_profile_revision': NEW_REV, **kwargs,
    })


def _record(case, name):
    return json.loads(case.client.get(case.keys[name]))


def test_existing_private_released_once_without_new_upload_or_original_plan_change(case):
    original_publisher = case.client.get(case.keys['publisher'])
    output = _release(case)
    assert output['status'] == 'released'
    assert output['privacy_status'] == 'public'
    ledger, source = _record(case, 'ledger'), _record(case, 'source')
    assert ledger['release_status'] == 'public'
    assert ledger['requested_release_mode'] == 'private'
    assert ledger['publish_plan'] == case.plan
    assert ledger['explicit_owner_release']['profile_revision'] == NEW_REV
    assert ledger['explicit_owner_release']['original_profile_revision'] == OLD_REV
    assert case.client.get(case.keys['publisher']) == original_publisher
    assert source['spec'] == case.source['spec']
    assert source['result']['youtube']['caption_uploaded'] is True
    assert source['result']['youtube']['caption_recovered'] is True
    assert source['result']['youtube']['profile_revision'] == OLD_REV
    assert source['result']['youtube']['explicit_owner_release'] == ledger['explicit_owner_release']
    assert source['result']['youtube_automation'] == case.source['result']['youtube_automation']
    assert source['result']['unrelated_render_proof'] == {'score': 88.3}
    assert case.client.get('immutable-paid-ledger') == case.client.get('immutable-schedule') == 'unchanged'
    assert case.client.get(case.keys['lock']) is None
    case.module.load_credentials.assert_called_once_with(CHANNEL, expected_connection_id=CONNECTION, refresh=True)
    case.module.set_video_release_with_credentials.assert_called_once_with(
        case.credentials, VIDEO, 'public', contains_synthetic_media=True)


@pytest.mark.parametrize('field,value', [('containsSyntheticMedia', False), ('containsSyntheticMedia', None)])
def test_legacy_missing_disclosure_is_corrected_in_same_public_update(case, field, value):
    case.module.get_video_status_with_credentials.return_value[field] = value
    assert _release(case)['contains_synthetic_media'] is True
    assert case.module.set_video_release_with_credentials.call_count == 1


@pytest.mark.parametrize('name,path,value', [
    ('source', ['task_id'], PUBLISHER), ('source', ['kind'], 'publish'),
    ('source', ['state'], 'FAILURE'), ('source', ['result', 'manual_qa_required'], True),
    ('source', ['result', 'quality_disposition'], 'manual_approved'),
    ('source', ['result', 'video_key'], ''), ('source', ['result', 'task_id'], PUBLISHER),
    ('source', ['retry_child_task_id'], PUBLISHER),
    ('source', ['result', 'youtube', 'video_id'], 'WrongVideo1'),
    ('source', ['result', 'youtube', 'connection_id'], 'wrong-connection'),
    ('source', ['result', 'youtube', 'profile_revision'], NEW_REV),
    ('source', ['result', 'youtube', 'target_channel_id'], 'WrongChannel'),
    ('source', ['result', 'youtube', 'caption_uploaded'], False),
    ('source', ['result', 'youtube', 'caption_error_code'], 'unverified'),
    ('source', ['result', 'youtube', 'thumbnail_uploaded'], False),
    ('source', ['result', 'youtube', 'thumbnail_error_code'], 'unverified'),
    ('source', ['result', 'youtube_automation', 'publish_task_id'], SOURCE),
    ('source', ['spec', 'production_profile_revision'], NEW_REV),
    ('publisher', ['parent_id'], PUBLISHER), ('publisher', ['state'], 'FAILURE'),
    ('publisher', ['kind'], 'render'), ('publisher', ['task_id'], SOURCE),
    ('publisher', ['spec', 'source_task_id'], PUBLISHER),
    ('publisher', ['spec', 'target_channel_id'], 'WrongChannel'),
    ('publisher', ['spec', 'connection_id'], 'wrong-connection'),
    ('publisher', ['result', 'source_task_id'], PUBLISHER),
    ('publisher', ['result', 'youtube_video_id'], 'WrongVideo1'),
    ('publisher', ['result', 'privacy_status'], 'public'),
    ('publisher', ['result', 'status'], 'incomplete'),
    ('ledger', ['youtube_video_id'], 'WrongVideo1'), ('ledger', ['source_task_id'], PUBLISHER),
    ('ledger', ['status'], 'uncertain'), ('ledger', ['side_effect_possible'], False),
    ('ledger', ['requested_release_mode'], 'public'),
    ('ledger', ['publish_plan', 'profile_revision'], NEW_REV),
    ('ledger', ['publish_plan', 'source_task_id'], PUBLISHER),
    ('ledger', ['publish_plan', 'release_mode'], 'public'),
    ('ledger', ['release_status'], 'ready'), ('ledger', ['release_status'], 'releasing'),
    ('ledger', ['release_status'], 'uncertain'), ('ledger', ['release_side_effect_possible'], True),
    ('profile', ['release_mode'], 'private'), ('profile', ['auto_publish'], False),
    ('profile', ['profile_revision'], OLD_REV), ('channel', ['connection_id'], 'new-connection'),
])
def test_ineligible_or_mismatched_proof_never_calls_provider(case, name, path, value):
    _edit(case.client, case.keys[name], path, value)
    before = case.client.get(case.keys['ledger'])
    with pytest.raises(case.module.ExistingVideoReleaseError):
        _release(case)
    assert case.client.get(case.keys['ledger']) == before
    case.module.load_credentials.assert_not_called()
    case.module.set_video_release_with_credentials.assert_not_called()


@pytest.mark.parametrize('name', ['source', 'publisher', 'ledger', 'profile', 'channel', 'credential'])
def test_missing_record_fails_closed(case, name):
    case.client.delete(case.keys[name])
    with pytest.raises(case.module.ExistingVideoReleaseError):
        _release(case)
    case.module.set_video_release_with_credentials.assert_not_called()


@pytest.mark.parametrize('value', ['garbage', '[]', '{}', 'x' * (4 * 1024 * 1024 + 1)],
                         ids=['invalid-json', 'array', 'empty-object', 'oversize'])
def test_corrupt_or_oversized_record_fails_closed(case, value):
    case.client.set(case.keys['source'], value)
    with pytest.raises(case.module.ExistingVideoReleaseError):
        _release(case)
    case.module.set_video_release_with_credentials.assert_not_called()


@pytest.mark.parametrize('field,value', [
    ('privacyStatus', 'public'), ('privacyStatus', 'unlisted'),
    ('uploadStatus', 'uploaded'), ('uploadStatus', 'failed'), ('publishAt', '2099-01-01'),
])
def test_remote_preflight_does_not_authorize_wrong_state(case, field, value):
    case.module.get_video_status_with_credentials.return_value[field] = value
    before = case.client.get(case.keys['ledger'])
    with pytest.raises(case.module.ExistingVideoReleaseError):
        _release(case)
    assert case.client.get(case.keys['ledger']) == before
    case.module.set_video_release_with_credentials.assert_not_called()


def test_current_profile_can_require_thumbnail_even_if_original_did_not(case):
    _edit(case.client, case.keys['ledger'], ['publish_plan', 'require_thumbnail'], False)
    _edit(case.client, case.keys['profile'], ['require_thumbnail'], True)
    _edit(case.client, case.keys['source'], ['result', 'youtube', 'thumbnail_uploaded'], False)
    with pytest.raises(case.module.ExistingVideoReleaseError, match='thumbnail'):
        _release(case)
    case.module.set_video_release_with_credentials.assert_not_called()


@pytest.mark.parametrize('target', ['source', 'publisher', 'ledger', 'profile', 'channel', 'credential', 'epoch', 'index', 'lock'])
def test_all_authorization_snapshots_rechecked_after_remote_read(case, target):
    def changed(*args):
        if target == 'epoch':
            case.client.set(case.module.AUTH_EPOCH_KEY, '1')
        elif target == 'index':
            case.client.srem(case.module.CHANNEL_INDEX_KEY, CHANNEL)
        elif target in {'credential', 'lock'}:
            case.client.set(case.keys[target], 'changed-fixture')
        else:
            _edit(case.client, case.keys[target], ['race_marker'], True)
        return {'privacyStatus': 'private', 'uploadStatus': 'processed'}
    case.module.get_video_status_with_credentials.side_effect = changed
    with pytest.raises(case.module.ExistingVideoReleaseError):
        _release(case)
    case.module.set_video_release_with_credentials.assert_not_called()
    assert _record(case, 'ledger')['release_status'] == 'private'


def test_watch_exec_race_never_authorizes_release(case, monkeypatch):
    original = case.client.pipeline
    injected = []
    def pipeline(*args, **kwargs):
        pipe = original(*args, **kwargs)
        execute = pipe.execute
        def raced(*a, **kw):
            if pipe.watching and not injected:
                injected.append(True)
                _edit(case.client, case.keys['profile'], ['profile_revision'], 'changed-revision')
            return execute(*a, **kw)
        pipe.execute = raced
        return pipe
    monkeypatch.setattr(case.client, 'pipeline', pipeline)
    with pytest.raises(case.module.ExistingVideoReleaseError):
        _release(case)
    assert injected
    assert _record(case, 'ledger')['release_status'] == 'private'
    case.module.set_video_release_with_credentials.assert_not_called()


def test_ready_to_uncertain_race_cannot_use_permissive_legacy_start(case, monkeypatch):
    original = case.module._compare_transaction
    calls = []
    def raced(*args, **kwargs):
        if calls and kwargs.get('ledger_write'):
            _edit(case.client, case.keys['ledger'], ['release_status'], 'uncertain')
        calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(case.module, '_compare_transaction', raced)
    with pytest.raises(case.module.ExistingVideoReleaseError):
        _release(case)
    assert _record(case, 'ledger')['release_status'] == 'uncertain'
    case.module.set_video_release_with_credentials.assert_not_called()


@pytest.mark.parametrize('response', [None, {}, {'id': 'WrongVideo1'},
    {'id': VIDEO, 'status': {'privacyStatus': 'private', 'containsSyntheticMedia': True}},
    {'id': VIDEO, 'status': {'privacyStatus': 'public'}},
    {'id': VIDEO, 'status': {'privacyStatus': 'public', 'containsSyntheticMedia': False}},
    {'id': VIDEO, 'status': {'privacyStatus': 'public', 'containsSyntheticMedia': True, 'publishAt': 'future'}},
])
def test_unproven_response_is_uncertain_and_never_replayed(case, response):
    case.module.set_video_release_with_credentials.return_value = response
    with pytest.raises(case.module.ExistingVideoReleaseError):
        _release(case)
    assert _record(case, 'ledger')['release_status'] == 'uncertain'
    assert _record(case, 'source')['result']['youtube']['privacy_status'] == 'private'
    with pytest.raises(case.module.ExistingVideoReleaseError):
        _release(case)
    assert case.module.set_video_release_with_credentials.call_count == 1


def test_timeout_may_have_published_but_is_not_replayed(case):
    case.module.set_video_release_with_credentials.side_effect = RuntimeError('secret-provider-body')
    with pytest.raises(case.module.ExistingVideoReleaseError, match='^release_unavailable_or_uncertain$'):
        _release(case)
    ledger = _record(case, 'ledger')
    assert ledger['youtube_video_id'] == VIDEO and ledger['release_status'] == 'uncertain'
    assert 'secret-provider-body' not in json.dumps(ledger)
    with pytest.raises(case.module.ExistingVideoReleaseError):
        _release(case)
    assert case.module.set_video_release_with_credentials.call_count == 1


def test_already_public_requires_fresh_remote_proof_and_only_reconciles_source(case):
    _release(case)
    complete_ledger = case.client.get(case.keys['ledger'])
    _write(case.client, case.keys['source'], case.source)  # simulate crash before source merge
    case.module.get_video_status_with_credentials.return_value = {
        'privacyStatus': 'public', 'uploadStatus': 'processed', 'containsSyntheticMedia': True,
    }
    assert _release(case)['status'] == 'already_public'
    assert case.client.get(case.keys['ledger']) == complete_ledger
    assert _record(case, 'source')['result']['youtube']['release_status'] == 'public'
    assert case.module.set_video_release_with_credentials.call_count == 1
    assert _release(case)['status'] == 'already_public'
    assert case.module.set_video_release_with_credentials.call_count == 1


@pytest.mark.parametrize('field,value', [('privacyStatus', 'private'), ('uploadStatus', 'uploaded'),
                                        ('containsSyntheticMedia', False)])
def test_already_public_never_invents_remote_completion(case, field, value):
    _release(case)
    case.module.get_video_status_with_credentials.return_value = {
        'privacyStatus': 'public', 'uploadStatus': 'processed', 'containsSyntheticMedia': True, field: value,
    }
    with pytest.raises(case.module.ExistingVideoReleaseError):
        _release(case)
    assert case.module.set_video_release_with_credentials.call_count == 1


def test_completed_remote_release_can_recover_source_merge_outage_without_another_update(case, monkeypatch):
    merge = case.module._merge_completed_source
    monkeypatch.setattr(case.module, '_merge_completed_source', Mock(side_effect=RuntimeError('outage')))
    with pytest.raises(case.module.ExistingVideoReleaseError):
        _release(case)
    assert _record(case, 'ledger')['release_status'] == 'public'
    monkeypatch.setattr(case.module, '_merge_completed_source', merge)
    case.module.get_video_status_with_credentials.return_value = {
        'privacyStatus': 'public', 'uploadStatus': 'processed', 'containsSyntheticMedia': True,
    }
    assert _release(case)['status'] == 'already_public'
    assert case.module.set_video_release_with_credentials.call_count == 1


def test_execution_lock_allows_at_most_one_public_update(case):
    entered, finish = Event(), Event()
    def update(*args, **kwargs):
        entered.set()
        assert finish.wait(timeout=5)
        return {'id': VIDEO, 'status': {'privacyStatus': 'public', 'containsSyntheticMedia': True}}
    case.module.set_video_release_with_credentials.side_effect = update
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_release, case)
        assert entered.wait(timeout=5)
        second = pool.submit(_release, case)
        with pytest.raises(case.module.ExistingVideoReleaseError):
            second.result(timeout=5)
        finish.set()
        assert first.result(timeout=5)['status'] == 'released'
    assert case.module.set_video_release_with_credentials.call_count == 1


def test_profile_change_between_ready_and_started_stops_before_api(case, monkeypatch):
    original = case.module._compare_transaction
    calls = []
    def raced(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(True)
        if len(calls) == 1:
            _edit(case.client, case.keys['profile'], ['auto_publish'], False)
        return result
    monkeypatch.setattr(case.module, '_compare_transaction', raced)
    with pytest.raises(case.module.ExistingVideoReleaseError):
        _release(case)
    case.module.set_video_release_with_credentials.assert_not_called()
    assert _record(case, 'ledger')['release_status'] == 'ready'


def test_public_source_merge_does_not_overwrite_concurrent_quality_or_binding_change(case):
    def changed(*args, **kwargs):
        _edit(case.client, case.keys['source'], ['result', 'manual_qa_required'], True)
        return {'id': VIDEO, 'status': {'privacyStatus': 'public', 'containsSyntheticMedia': True}}
    case.module.set_video_release_with_credentials.side_effect = changed
    with pytest.raises(case.module.ExistingVideoReleaseError):
        _release(case)
    assert _record(case, 'ledger')['release_status'] == 'uncertain'
    assert _record(case, 'source')['result']['manual_qa_required'] is True
    assert _record(case, 'source')['result']['youtube']['release_status'] == 'private'


def test_completed_publisher_redelivery_still_allows_readonly_source_reconciliation(case):
    _release(case)
    _write(case.client, case.keys['source'], case.source)
    # Use the actual existing publisher reconstruction function so its missing
    # historical caption/profile fields are not guessed by this regression.
    path = ROOT / 'app/publish_tasks.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == '_result_from_existing_record')
    namespace = {'UploadAlreadyInProgress': case.state.UploadAlreadyInProgress}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), namespace)
    replay = namespace['_result_from_existing_record'](PUBLISHER, SOURCE, _record(case, 'ledger'))
    _edit(case.client, case.keys['publisher'], ['result'], replay)
    case.module.get_video_status_with_credentials.return_value = {
        'privacyStatus': 'public', 'uploadStatus': 'processed', 'containsSyntheticMedia': True,
    }
    assert _release(case)['status'] == 'already_public'
    assert _record(case, 'source')['result']['youtube']['caption_uploaded'] is True
    assert _record(case, 'source')['result']['youtube']['profile_revision'] == OLD_REV
    assert case.module.set_video_release_with_credentials.call_count == 1


def test_minimal_replayed_publisher_is_never_initial_release_authority(case):
    replay = deepcopy(case.publisher['result'])
    replay.pop('profile_revision')
    replay.update(idempotent_replay=True, privacy_status='public', release_status='public')
    _edit(case.client, case.keys['publisher'], ['result'], replay)
    with pytest.raises(case.module.ExistingVideoReleaseError):
        _release(case)
    case.module.set_video_release_with_credentials.assert_not_called()
