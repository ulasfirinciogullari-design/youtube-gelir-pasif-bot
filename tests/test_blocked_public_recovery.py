import ast
from copy import deepcopy
import hashlib
from io import BytesIO
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import fakeredis
import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = '5fa00000-0000-4000-8000-000000000000'
PUBLISHER = 'fc500000-0000-4000-8000-000000000000'
VIDEO = 'Sgs00000000'
CHANNEL = 'UC_margin_channel_0000'
CONNECTION = 'connection-generation-current'
REVISION = 'profile-current-revision'
CAPTION = 'manual-caption-identifier'


def _load(name, namespace):
    path = ROOT / 'app/services' / (name + '.py')
    tree = ast.parse(path.read_text(encoding='utf-8'))
    tree.body = [node for node in tree.body if not (
        isinstance(node, ast.ImportFrom) and (node.module or '').startswith('app.')
    )]
    module = ModuleType(name)
    module.__dict__.update(namespace)
    exec(compile(tree, str(path), 'exec'), module.__dict__)
    return module


def _write(client, key, value):
    client.set(key, json.dumps(value, sort_keys=True, separators=(',', ':')))


def _edit(case, name, path, value):
    document = json.loads(case.client.get(case.keys[name]))
    target = document
    for segment in path[:-1]:
        target = target[segment]
    target[path[-1]] = value
    _write(case.client, case.keys[name], document)


def _track(**overrides):
    return {'id': CAPTION, 'snippet': {
        'videoId': VIDEO, 'language': 'en', 'name': 'EN captions',
        'trackKind': 'standard', 'isDraft': False, 'status': 'serving', **overrides,
    }}


class Media(BytesIO):
    def stream(self):
        return self


class GoogleError(Exception):
    def __init__(self, status=403, reason='forbidden'):
        super().__init__('SECRET provider response https://private.invalid/?key=SECRET')
        self.resp = SimpleNamespace(status=status)
        self.content = json.dumps({'error': {'errors': [{'reason': reason}],
                                             'message': 'SECRET'}}).encode()


@pytest.fixture
def case(tmp_path, monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=True)
    settings = SimpleNamespace(redis_url='redis://unused')
    state = _load('youtube_publish_state', {'settings': settings})
    state._redis = lambda: client
    automation = _load('youtube_automation', {'settings': settings})
    ns = {name: getattr(state, name) for name in (
        'UPLOAD_PREFIX', 'EXECUTION_LOCK_PREFIX', 'acquire_execution_lock', 'release_execution_lock')}
    ns.update(settings=settings, JOB_PREFIX='youtube_studio:job:', PROFILE_PREFIX=automation.PROFILE_PREFIX,
              CHANNEL_PREFIX='youtube_studio:oauth:channel:v3:',
              CREDENTIAL_PREFIX='youtube_studio:oauth:credential:v3:',
              CHANNEL_INDEX_KEY='youtube_studio:oauth:channels:v3',
              AUTH_EPOCH_KEY='youtube_studio:oauth:authorization_epoch:v2',
              automated_quality_approved=automation.automated_quality_approved,
              validate_publish_plan=automation.validate_publish_plan)
    module = _load('blocked_public_recovery', ns)
    module._redis = lambda: client
    binding = {'target_channel_id': CHANNEL, 'connection_id': CONNECTION, 'profile_revision': REVISION}
    blocked = {'privacy_status': 'private', 'release_status': 'blocked', 'scheduled_publish_at': None,
               'release_error_code': 'youtube_http_403', 'caption_uploaded': False,
               'caption_error_code': 'youtube_http_403', 'thumbnail_uploaded': False, 'thumbnail_error_code': None}
    source = {'task_id': SOURCE, 'kind': 'render', 'state': 'SUCCESS',
              'spec': {'topic': 'A source-backed IKEA decision', 'mode': 'production', 'format': 'shorts',
                       'language': 'en', 'duration_minutes': .5, 'production_channel_id': CHANNEL,
                       'production_connection_id': CONNECTION, 'production_profile_revision': REVISION,
                       'publish_after_render': True},
              'result': {'task_id': SOURCE, 'title': 'The furniture decision', 'scenes': 6,
                         'video_key': f'videos/{SOURCE}/final.mp4',
                         'caption_key': f'videos/{SOURCE}/captions.en.srt',
                         'metadata_key': f'videos/{SOURCE}/metadata.json', 'thumbnail_key': None,
                         'quality_disposition': 'automated_qc_pass', 'manual_qa_required': False,
                         'youtube': {'video_id': VIDEO, **binding, **blocked},
                         'youtube_automation': {'status': 'queued', 'publish_task_id': PUBLISHER}}}
    publisher = {'task_id': PUBLISHER, 'kind': 'publish', 'state': 'SUCCESS', 'parent_id': SOURCE,
                 'spec': {'source_task_id': SOURCE, 'privacy_status': 'private', 'release_mode': 'public', **binding},
                 'result': {'task_id': PUBLISHER, 'status': 'complete', 'source_task_id': SOURCE,
                            'youtube_video_id': VIDEO, **binding, **blocked}}
    plan = automation.validate_publish_plan({
        'schema_version': 1, 'source_task_id': SOURCE, 'target_channel_id': CHANNEL,
        'profile_revision': REVISION, 'title': source['result']['title'], 'description': 'Existing approved description',
        'tags': ['furniture'], 'hashtags': ['Shorts'], 'category_id': '28', 'default_language': 'en',
        'contains_synthetic_media': True, 'release_mode': 'public', 'publish_at': None,
        'require_thumbnail': True, 'thumbnail_key': None,
        'quality_snapshot': {'quality_disposition': 'automated_qc_pass', 'manual_qa_required': False}})
    ledger = {'version': 2, 'source_task_id': SOURCE, 'publish_task_id': PUBLISHER, 'status': 'complete',
              'side_effect_possible': True, 'youtube_video_id': VIDEO, 'target_channel_id': CHANNEL,
              'connection_id': CONNECTION, 'requested_release_mode': 'public', 'requested_publish_at': None,
              'release_status': 'blocked', 'release_error_code': 'youtube_http_403',
              'release_side_effect_possible': False, 'publish_plan': plan}
    documents = {'source': source, 'publisher': publisher, 'ledger': ledger,
                 'profile': {'channel_id': CHANNEL, 'profile_revision': REVISION, 'release_mode': 'public',
                             'languages': ['en'], 'auto_publish': True, 'production_enabled': True},
                 'channel': {'id': CHANNEL, 'connection_id': CONNECTION}}
    keys = {'source': module.JOB_PREFIX + SOURCE, 'publisher': module.JOB_PREFIX + PUBLISHER,
            'ledger': module.UPLOAD_PREFIX + SOURCE, 'profile': module.PROFILE_PREFIX + CHANNEL,
            'channel': module.CHANNEL_PREFIX + CHANNEL, 'credential': module.CREDENTIAL_PREFIX + CHANNEL,
            'lock': module.EXECUTION_LOCK_PREFIX + SOURCE, 'audit': module.RECOVERY_PREFIX + SOURCE}
    for name, value in documents.items():
        _write(client, keys[name], value)
    client.set(keys['credential'], 'opaque encrypted fixture')
    client.set(module.AUTH_EPOCH_KEY, 'current-authorization-epoch')
    client.sadd(module.CHANNEL_INDEX_KEY, CHANNEL)
    client.set('never-reset-paid-ledger', 'used:1')
    client.set('never-reset-topic-cursor', 'consumed:1')
    assets = {}
    for name, data, extension in (
        ('caption', b'1\n00:00:00,000 --> 00:00:02,000\nOriginal narration\n', '.srt'),
        ('thumbnail', b'\xff\xd8\xffexisting-frame\xff\xd9', '.jpg'),
        ('final', b'existing-approved-final', '.mp4'), ('metadata', b'existing-metadata', '.json'),
    ):
        path = tmp_path / (name + extension)
        path.write_bytes(data)
        assets[name] = {'path': str(path), 'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}
    assets['caption']['language'] = 'en'
    prep = ModuleType('app.services.publication_recovery_assets')
    prep.prepare_publication_recovery_assets = Mock(return_value=assets)
    monkeypatch.setitem(sys.modules, prep.__name__, prep)
    api = SimpleNamespace(caption_tracks=[_track(trackKind='ASR', name='')],
                          inserted=_track(), requests=[], media=[], caption_error=None, thumbnail_error=None,
                          post_insert_tracks=None, thumbnail_response={
                              'kind': 'youtube#thumbnailSetResponse', 'items': [{'high': {
                                  'url': 'https://i.ytimg.com/vi/' + VIDEO + '/hqdefault.jpg', 'width': 480, 'height': 360}}]})
    api.remote_video = {'id': VIDEO, 'snippet': {'channelId': CHANNEL},
                        'status': {'privacyStatus': 'private', 'uploadStatus': 'processed'}}
    def request(name, payload):
        def execute(*, num_retries):
            assert num_retries == 0
            api.requests.append(name)
            return payload() if callable(payload) else deepcopy(payload)
        return SimpleNamespace(execute=Mock(side_effect=execute))
    def insert(**kwargs):
        assert kwargs['body'] == {'snippet': {'videoId': VIDEO, 'language': 'en', 'name': 'EN captions', 'isDraft': False}}
        assert kwargs['part'] == 'snippet'
        assert kwargs['media_body'].getvalue() == Path(assets['caption']['path']).read_bytes()
        def perform():
            audit = json.loads(client.get(keys['audit']))
            assert audit['caption']['status'] == 'reserved_before_http' and audit['caption']['attempts'] == 1
            if api.caption_error:
                raise api.caption_error
            api.caption_tracks = deepcopy(api.post_insert_tracks if api.post_insert_tracks is not None else [api.inserted])
            return deepcopy(api.inserted)
        return request('captions.insert', perform)
    def thumbnail(**kwargs):
        assert kwargs['videoId'] == VIDEO
        assert kwargs['media_body'].getvalue() == Path(assets['thumbnail']['path']).read_bytes()
        def perform():
            audit = json.loads(client.get(keys['audit']))
            assert audit['thumbnail']['status'] == 'reserved_before_http' and audit['thumbnail']['attempts'] == 1
            if api.thumbnail_error:
                raise api.thumbnail_error
            return deepcopy(api.thumbnail_response)
        return request('thumbnails.set', perform)
    videos = SimpleNamespace(list=Mock(side_effect=lambda **kwargs: request('videos.list', lambda: {'items': [api.remote_video]})),
                             insert=Mock(side_effect=AssertionError('No duplicate upload')),
                             update=Mock(side_effect=AssertionError('No release in first-stage helper')))
    captions = SimpleNamespace(list=Mock(side_effect=lambda **kwargs: request('captions.list', lambda: {'items': api.caption_tracks})),
                               insert=Mock(side_effect=insert))
    thumbnails = SimpleNamespace(set=Mock(side_effect=thumbnail))
    channels = SimpleNamespace(list=Mock(side_effect=lambda **kwargs: request('channels.list', {'items': [{'id': CHANNEL}]})))
    service = SimpleNamespace(channels=lambda: channels, videos=lambda: videos, captions=lambda: captions,
                              thumbnails=lambda: thumbnails, close=Mock())
    module._service = Mock(return_value=service)
    module._credentials = Mock(return_value=object())
    def media(data, mime):
        value = Media(data)
        api.media.append(value)
        return value
    module._media = Mock(side_effect=media)
    return SimpleNamespace(module=module, client=client, state=state, automation=automation, keys=keys,
                           api=api, captions=captions, thumbnails=thumbnails, videos=videos, channels=channels,
                           service=service, assets=assets, prep=prep, work_dir=tmp_path, documents=documents)


def _run(case, **kwargs):
    return case.module.recover_blocked_public_assets(**{
        'source_task_id': SOURCE, 'expected_video_id': VIDEO, 'expected_channel_id': CHANNEL,
        'expected_profile_revision': REVISION, 'work_dir': case.work_dir, **kwargs})


def _audit(case):
    return json.loads(case.client.get(case.keys['audit']))


def test_exact_assets_retried_once_without_publishing_or_modifying_original_records(case):
    originals = {key: case.client.get(key) for name, key in case.keys.items() if name not in {'audit', 'lock'}}
    outcome = _run(case)
    assert outcome['status'] == 'assets_ready_needs_release_integration'
    assert outcome['release_performed'] is False
    assert outcome['caption']['status'] == outcome['thumbnail']['status'] == 'verified'
    assert outcome['caption']['attempts'] == outcome['thumbnail']['attempts'] == 1
    assert {key: case.client.get(key) for key in originals} == originals
    assert case.client.get('never-reset-paid-ledger') == 'used:1'
    assert case.client.get('never-reset-topic-cursor') == 'consumed:1'
    assert case.client.ttl(case.keys['audit']) == -1
    assert case.client.get(case.keys['lock']) is None
    assert case.api.requests.count('captions.insert') == case.api.requests.count('thumbnails.set') == 1
    assert all(item.closed for item in case.api.media)
    assert 'https://' not in json.dumps(outcome)
    case.videos.insert.assert_not_called()
    case.videos.update.assert_not_called()
    assert _audit(case)['assets']['caption']['sha256'] == case.assets['caption']['sha256']
    case.channels.list.assert_called_with(part='id', mine=True, maxResults=50)
    case.videos.list.assert_called_with(part='snippet,status', id=VIDEO, maxResults=1)
    case.captions.list.assert_called_with(part='snippet', videoId=VIDEO)


def test_second_invocation_is_cached_audit_without_replaying_any_request(case):
    original = _run(case)
    requests = list(case.api.requests)
    assert _run(case) == original
    assert case.api.requests == requests
    case.prep.prepare_publication_recovery_assets.assert_called_once()


@pytest.mark.parametrize('error,status,reason', [
    (GoogleError(403, 'forbidden'), 'rejected', 'forbidden'),
    (GoogleError(403, 'quotaExceeded'), 'rejected', 'quotaExceeded'),
    (GoogleError(404, 'videoNotFound'), 'rejected', 'videoNotFound'),
    (GoogleError(500, 'processingFailure'), 'uncertain', 'processingFailure'),
    (TimeoutError('SECRET'), 'uncertain', 'api_unavailable'),
    (GoogleError(403, 'https://secret.invalid/key'), 'rejected', 'api_unavailable'),
])
def test_actual_safe_reason_recorded_without_assuming_quota_or_reposting(case, error, status, reason):
    case.api.caption_error = error
    result = _run(case)
    assert result['status'] == 'assets_blocked'
    assert result['caption']['status'] == status and result['caption']['reason'] == reason
    assert result['thumbnail']['status'] == 'verified'
    assert 'SECRET' not in json.dumps(_audit(case)) and 'https://' not in json.dumps(result)
    assert _run(case) == result
    assert case.api.requests.count('captions.insert') == 1


def test_processing_caption_is_not_approval_and_not_reinserted(case):
    case.api.post_insert_tracks = [_track(status='syncing')]
    result = _run(case)
    assert result['status'] == 'assets_pending_verification'
    assert result['caption']['status'] == 'awaiting_processing'
    assert _run(case) == result
    assert case.api.requests.count('captions.insert') == 1


@pytest.mark.parametrize('field,value', [('videoId', 'Another0000'), ('language', 'tr'),
    ('name', 'different'), ('trackKind', 'ASR'), ('isDraft', True)])
def test_wrong_caption_receipt_cannot_become_ready(case, field, value):
    case.api.inserted['snippet'][field] = value
    result = _run(case)
    assert result['status'] == 'assets_blocked'
    assert result['caption']['status'] == 'verification_failed'
    assert case.api.requests.count('captions.insert') == 1


def test_same_name_manual_track_not_linked_to_prior_success_is_not_silently_accepted(case):
    case.api.caption_tracks = [_track()]
    with pytest.raises(case.module.BlockedPublicRecoveryError, match='existing_caption_unverified'):
        _run(case)
    case.captions.insert.assert_not_called()
    assert case.client.get(case.keys['audit']) is None


def test_prior_verified_caption_with_current_serving_track_avoids_new_caption_post(case):
    for name, prefix in [('source', ['result', 'youtube']), ('publisher', ['result'])]:
        _edit(case, name, prefix + ['caption_uploaded'], True)
        _edit(case, name, prefix + ['caption_error_code'], None)
        _edit(case, name, prefix + ['release_error_code'], 'thumbnail_required')
    _edit(case, 'ledger', ['release_error_code'], 'thumbnail_required')
    case.api.caption_tracks = [_track()]
    result = _run(case)
    assert result['caption'] == {'status': 'verified_existing', 'attempts': 0, 'caption_id': CAPTION}
    assert result['status'] == 'assets_ready_needs_release_integration'
    case.captions.insert.assert_not_called()


@pytest.mark.parametrize('name,path,value', [
    ('source', ['state'], 'FAILURE'), ('source', ['kind'], 'publish'),
    ('source', ['result', 'task_id'], None),
    ('source', ['result', 'manual_qa_required'], True),
    ('source', ['result', 'quality_disposition'], 'needs_manual_review'),
    ('source', ['result', 'video_key'], 'videos/other/final.mp4'),
    ('source', ['result', 'caption_key'], 'videos/other/captions.en.srt'),
    ('source', ['result', 'metadata_key'], 'https://private.invalid/file'),
    ('source', ['result', 'thumbnail_key'], f'videos/{SOURCE}/authored.jpg'),
    ('source', ['spec', 'mode'], 'preview'), ('source', ['spec', 'duration_minutes'], 3),
    ('source', ['spec', 'format'], 'landscape'), ('source', ['spec', 'language'], 'de'),
    ('source', ['spec', 'production_connection_id'], 'reconnected-generation'),
    ('source', ['result', 'youtube', 'privacy_status'], 'public'),
    ('source', ['result', 'youtube_automation', 'publish_task_id'], 'different-publisher'),
    ('publisher', ['state'], 'FAILURE'), ('publisher', ['parent_id'], PUBLISHER),
    ('publisher', ['result', 'task_id'], None),
    ('publisher', ['result', 'youtube_video_id'], 'Different00'),
    ('publisher', ['result', 'release_status'], 'uncertain'),
    ('ledger', ['status'], 'uncertain'), ('ledger', ['release_side_effect_possible'], True),
    ('ledger', ['requested_release_mode'], 'private'), ('ledger', ['requested_publish_at'], 'tomorrow'),
    ('ledger', ['publish_plan', 'release_mode'], 'private'),
    ('ledger', ['publish_plan', 'title'], ''), ('ledger', ['release_error_code'], 'unknown_failure'),
    ('ledger', ['publish_plan', 'thumbnail_key'], f'videos/{SOURCE}/authored.jpg'),
    ('profile', ['auto_publish'], False), ('profile', ['production_enabled'], False),
    ('profile', ['profile_revision'], 'changed-revision'), ('profile', ['languages'], ['tr']),
    ('profile', ['languages'], 'en'),
    ('channel', ['connection_id'], 'changed-generation'), ('channel', ['requires_reconnect'], True),
])
def test_ineligible_or_changed_binding_fails_before_storage_preparation_or_google(case, name, path, value):
    _edit(case, name, path, value)
    with pytest.raises(case.module.BlockedPublicRecoveryError):
        _run(case)
    case.prep.prepare_publication_recovery_assets.assert_not_called()
    case.module._service.assert_not_called()
    assert case.client.get(case.keys['audit']) is None


@pytest.mark.parametrize('field,value', [('privacyStatus', 'public'), ('uploadStatus', 'uploaded'),
                                        ('publishAt', '2027-01-01T00:00:00Z')])
def test_actual_remote_private_processed_proof_required_before_any_asset_write(case, field, value):
    case.api.remote_video['status'][field] = value
    with pytest.raises(case.module.BlockedPublicRecoveryError, match='remote_not_private_processed'):
        _run(case)
    case.captions.insert.assert_not_called()
    case.thumbnails.set.assert_not_called()


def test_live_channel_mismatch_is_not_accepted_even_if_video_id_matches(case):
    case.api.remote_video['snippet']['channelId'] = 'UC_other'
    with pytest.raises(case.module.BlockedPublicRecoveryError):
        _run(case)
    case.captions.insert.assert_not_called()


@pytest.mark.parametrize('phase', ['caption', 'thumbnail'])
def test_changed_local_asset_hash_prevents_provider_writes(case, phase):
    Path(case.assets[phase]['path']).write_bytes(b'changed')
    with pytest.raises(case.module.BlockedPublicRecoveryError, match='hash_changed'):
        _run(case)
    case.module._service.assert_not_called()


@pytest.mark.parametrize('mutation', ['source', 'profile', 'credential', 'epoch', 'membership', 'lock'])
def test_watched_mutation_after_preflight_prevents_request_and_never_reinitializes(case, mutation):
    original = case.module._commit
    calls = 0
    def commit(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            if mutation in {'source', 'profile'}:
                _edit(case, mutation, ['changed_concurrently'], True)
            elif mutation == 'membership':
                case.client.srem(case.module.CHANNEL_INDEX_KEY, CHANNEL)
            else:
                key = case.module.AUTH_EPOCH_KEY if mutation == 'epoch' else case.keys[mutation]
                case.client.set(key, 'new-value')
        return original(*args)
    case.module._commit = commit
    with pytest.raises(case.module.BlockedPublicRecoveryError, match='asset_state_changed'):
        _run(case)
    case.captions.insert.assert_not_called()
    case.thumbnails.set.assert_not_called()
    assert _audit(case)['caption']['status'] == 'pending'
    if mutation == 'lock':
        assert case.client.get(case.keys['lock']) == 'new-value'


def test_actual_redis_watch_conflict_prevents_initial_audit_and_provider_write(case, monkeypatch):
    original_pipeline = case.client.pipeline
    def pipeline(*args, **kwargs):
        pipe = original_pipeline(*args, **kwargs)
        execute = pipe.execute
        def conflict(*a, **k):
            _edit(case, 'source', ['concurrent_change'], True)
            return execute(*a, **k)
        pipe.execute = conflict
        return pipe
    monkeypatch.setattr(case.client, 'pipeline', pipeline)
    with pytest.raises(case.module.BlockedPublicRecoveryError, match='unavailable_or_uncertain'):
        _run(case)
    case.captions.insert.assert_not_called()
    assert case.client.get(case.keys['audit']) is None


def test_lost_reservation_reply_never_replays_or_reopens_attempt(case):
    original = case.module._commit
    calls = 0
    def commit(*args):
        nonlocal calls
        calls += 1
        result = original(*args)
        if calls == 2:
            raise TimeoutError('reply lost after successful transaction')
        return result
    case.module._commit = commit
    with pytest.raises(case.module.BlockedPublicRecoveryError):
        _run(case)
    assert _audit(case)['caption']['status'] == 'reserved_before_http'
    case.captions.insert.assert_not_called()
    case.module._commit = original
    result = _run(case)
    assert result['caption']['status'] == 'reserved_before_http'
    case.captions.insert.assert_not_called()


def test_remote_accepted_then_state_change_remains_reserved_without_duplicate_post(case):
    original = case.module._commit
    calls = 0
    def commit(*args):
        nonlocal calls
        calls += 1
        if calls == 3:
            _edit(case, 'source', ['concurrent_update'], True)
        return original(*args)
    case.module._commit = commit
    with pytest.raises(case.module.BlockedPublicRecoveryError, match='asset_state_changed'):
        _run(case)
    assert case.api.requests.count('captions.insert') == 1
    assert _audit(case)['caption']['status'] == 'reserved_before_http'
    with pytest.raises(case.module.BlockedPublicRecoveryError, match='previous_binding_changed'):
        _run(case)
    assert case.api.requests.count('captions.insert') == 1
    case.thumbnails.set.assert_not_called()


@pytest.mark.parametrize('change', ['wrong_kind', 'empty', 'unsafe_url', 'non_integer_size'])
def test_thumbnail_response_needs_real_safe_receipt(case, change):
    if change == 'wrong_kind':
        case.api.thumbnail_response['kind'] = 'youtube#video'
    elif change == 'empty':
        case.api.thumbnail_response['items'] = []
    elif change == 'unsafe_url':
        case.api.thumbnail_response['items'][0]['high']['url'] = 'https://evil.invalid/SECRET'
    else:
        case.api.thumbnail_response['items'][0]['high']['width'] = True
    result = _run(case)
    assert result['status'] == 'assets_blocked'
    assert result['thumbnail']['status'] == 'verification_failed'
    assert 'SECRET' not in json.dumps(result)


def test_returned_cached_audit_is_field_whitelisted(case):
    _run(case)
    record = _audit(case)
    record['caption']['raw_response'] = 'SECRET'
    record['caption']['reason'] = 'https://evil.invalid/SECRET'
    _write(case.client, case.keys['audit'], record)
    assert 'SECRET' not in json.dumps(_run(case))


def test_module_has_no_video_mutations_provider_generation_or_legacy_state_updater():
    tree = ast.parse((ROOT / 'app/services/blocked_public_recovery.py').read_text(encoding='utf-8'))
    calls = [ast.unparse(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)]
    assert 'service.videos().insert' not in calls and 'service.videos().update' not in calls
    assert not any(name in calls for name in ['update_job', 'mark_release_completed', 'synthesize_voice',
                                             'generate_scene', 'load_credentials'])


@pytest.mark.parametrize('bad', [None, 'wrong_version', 'wrong_channel', 'wrong_generation', 'no_refresh'])
def test_credentials_are_v3_bound_and_refreshed_only_in_memory(case, monkeypatch, bad):
    # Reload only the helper so its actual credential function is exercised.
    helper = _load('blocked_public_recovery', {key: value for key, value in case.module.__dict__.items()
                                              if key not in {'_credentials'}})
    payload = {'version': 3, 'channel_id': CHANNEL, 'connection_id': CONNECTION, 'refresh_token': 'private-fixture'}
    if bad == 'wrong_version':
        payload['version'] = 2
    elif bad == 'wrong_channel':
        payload['channel_id'] = 'UC_other'
    elif bad == 'wrong_generation':
        payload['connection_id'] = 'old-generation'
    elif bad == 'no_refresh':
        payload['refresh_token'] = ''
    request = Mock(return_value='token-response')
    credential = SimpleNamespace(token=None)
    def refresh(transport):
        assert transport(url='https://oauth2.googleapis.com/token', timeout=999) == 'token-response'
        credential.token = 'fresh-in-memory-only'
    credential.refresh = Mock(side_effect=refresh)
    auth = SimpleNamespace(_decrypt_json=Mock(return_value=payload),
                           _credential_from_refresh_token=Mock(return_value=credential),
                           GoogleRequest=Mock(return_value=request))
    services = ModuleType('app.services')
    services.youtube_auth = auth
    monkeypatch.setitem(sys.modules, 'app.services', services)
    initial = {key: case.client.get(key) for key in (case.keys['credential'], case.module.AUTH_EPOCH_KEY)}
    if bad:
        with pytest.raises(helper.BlockedPublicRecoveryError):
            helper._credentials('opaque encrypted fixture', {'target_channel_id': CHANNEL, 'connection_id': CONNECTION})
        credential.refresh.assert_not_called()
    else:
        assert helper._credentials('opaque encrypted fixture', {'target_channel_id': CHANNEL, 'connection_id': CONNECTION}) is credential
        request.assert_called_once_with(url='https://oauth2.googleapis.com/token', timeout=20)
    assert {key: case.client.get(key) for key in initial} == initial
