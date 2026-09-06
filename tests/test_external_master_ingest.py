"""Unapproved intake only; fake Redis/Storage, no auth/provider/live access."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
import redis

from app import external_routes as routes
from app.services import external_master_ingest as ingest
from app.services.youtube_automation import automated_quality_approved
from test_external_artifact_import import staged


CHANNEL = 'UCexternal1234567890'
CONNECTION = 'connection-12345678'
REVISION = 'profile-revision-123'


class FakeRedis:
    def __init__(self):
        self.values, self.sets, self.sorted, self.ttls = {}, {}, {}, {}
        self.after_execute = None

    def get(self, key):
        return self.values.get(key)

    def sismember(self, key, value):
        return value in self.sets.get(key, set())

    def smembers(self, key):
        return set(self.sets.get(key, set()))

    def scard(self, key):
        return len(self.sets.get(key, set()))

    def pipeline(self):
        return Pipe(self)


class Pipe:
    def __init__(self, client):
        self.client, self.watched, self.pending = client, {}, []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def _current(self, key):
        return deepcopy((self.client.values.get(key), self.client.sets.get(key), self.client.sorted.get(key)))

    def watch(self, *keys):
        self.watched = {key: self._current(key) for key in keys}

    def get(self, key):
        return self.client.get(key)

    def sismember(self, key, value):
        return self.client.sismember(key, value)

    def multi(self):
        pass

    def set(self, key, value):
        self.pending.append(('set', key, value))

    def setex(self, key, ttl, value):
        self.pending.append(('setex', key, ttl, value))

    def zadd(self, key, values):
        self.pending.append(('zadd', key, values))

    def expire(self, key, ttl):
        self.pending.append(('expire', key, ttl))

    def execute(self):
        if any(self._current(key) != value for key, value in self.watched.items()):
            raise redis.WatchError()
        for operation in self.pending:
            name, key, *args = operation
            if name == 'set':
                self.client.values[key] = args[0]
            elif name == 'setex':
                self.client.ttls[key] = args[0]
                self.client.values[key] = args[1]
            elif name == 'zadd':
                self.client.sorted.setdefault(key, {}).update(args[0])
            elif name == 'expire':
                self.client.ttls[key] = args[0]
        if self.client.after_execute:
            self.client.after_execute(self.pending)
        return [True] * len(self.pending)


class StorageError(Exception):
    def __init__(self, code):
        self.response = {'Error': {'Code': code}}


class FakeStorage:
    def __init__(self):
        self.objects, self.calls = {}, []
        self.after_put = None

    def put_object(self, **request):
        self.calls.append(request['Key'])
        assert request['IfNoneMatch'] == '*' and request['CacheControl'] == 'private, no-store'
        if request['Key'] in self.objects:
            raise StorageError('PreconditionFailed')
        content = request['Body'].read()
        assert len(content) == request['ContentLength']
        assert hashlib.sha256(content).hexdigest() == request['Metadata']['sha256']
        self.objects[request['Key']] = {'ContentLength': len(content),
            'ContentType': request['ContentType'], 'Metadata': request['Metadata'], 'bytes': content}
        if self.after_put:
            self.after_put(len(self.calls))
        return {'ETag': '"test-object"'}

    def head_object(self, **request):
        return self.objects[request['Key']]

    def generate_presigned_url(self, operation, **kwargs):
        assert operation == 'get_object'
        return 'https://storage.example/' + kwargs['Params']['Key']


@pytest.fixture
def case(staged, monkeypatch):
    client, s3 = FakeRedis(), FakeStorage()
    client.values[ingest.CHANNEL_PREFIX + CHANNEL] = json.dumps(
        {'id': CHANNEL, 'connection_id': CONNECTION, 'requires_reconnect': False})
    client.values[ingest.CREDENTIAL_PREFIX + CHANNEL] = 'encrypted-test-cipher-never-export'
    client.values[ingest.PROFILE_PREFIX + CHANNEL] = json.dumps(
        {'channel_id': CHANNEL, 'profile_revision': REVISION, 'languages': ['en'],
         'production_enabled': False, 'auto_publish': False, 'series_epoch': 2})
    client.values[ingest.AUTH_EPOCH_KEY] = '12'
    client.sets[ingest.CHANNEL_INDEX_KEY] = {CHANNEL}
    client.values['old:failed:job'] = 'leave unchanged'
    client.values['old:series:cursor'] = '4'
    monkeypatch.setattr(ingest, '_redis', lambda: client)
    monkeypatch.setattr(ingest.storage, '_client', lambda: s3)
    monkeypatch.setattr(ingest.storage, 'settings', SimpleNamespace(bucket='test-bucket'))
    monkeypatch.setattr(routes, 'settings', SimpleNamespace(factory_api_token='test-factory-token'))
    app = FastAPI()
    app.include_router(routes.router)
    return SimpleNamespace(staged=staged, client=client, storage=s3, http=TestClient(app))


def run(case, **overrides):
    c = case.staged
    return ingest.ingest_external_master(c.root, c.video, c.captions, c.manifest,
        **{'target_channel_id': CHANNEL, 'expected_connection_id': CONNECTION,
           'expected_profile_revision': REVISION, **overrides})


def job(case, response):
    return json.loads(case.client.values[ingest.JOB_PREFIX + response['task_id']])


def post(case, *, token='test-factory-token', extra=None, headers=None):
    c = case.staged
    fields = {'manifest': json.dumps(c.manifest), 'target_channel_id': CHANNEL,
              'expected_connection_id': CONNECTION, 'expected_profile_revision': REVISION}
    fields.update(extra or {})
    return case.http.post('/studio/api/external-masters', data=fields,
        files={'video': ('../../not-used.mp4', c.video.read_bytes(), 'video/mp4'),
               'captions': ('captions.srt', c.captions.read_bytes(), 'application/x-subrip')},
        headers={'X-Factory-Token': token, **(headers or {})})


def test_import_is_unapproved_review_only_and_preserves_old_state(case):
    before = deepcopy(case.client.values)
    result = run(case)
    saved = job(case, result)
    assert saved['state'] == 'SUCCESS' and saved['kind'] == 'render' and saved['parent_id'] is None
    assert saved['result']['quality_disposition'] == 'manual_qa_preview'
    assert saved['result']['manual_qa_required'] is True
    assert automated_quality_approved(saved) is False
    assert all(saved['result'][name] is False for name in
               ('qa_approved', 'publish_eligible', 'media_generation_authorized',
                'audio_transcription_verified', 'source_evidence_verified', 'new_media_generated'))
    assert saved['spec']['publish_after_render'] is False and saved['spec']['production_scheduled'] is False
    assert saved['result']['external_provenance']['origin']['voice_model'] is None
    assert saved['result']['external_provenance']['review_status'] == 'unreviewed'
    assert len(case.storage.objects) == 3
    assert all(case.client.values[key] == value for key, value in before.items())
    assert ingest.RESERVATION_PREFIX + result['task_id'] not in case.client.ttls
    assert 'encrypted-test-cipher-never-export' not in json.dumps(result) + json.dumps(saved)


def test_duplicate_completed_import_no_job_or_storage_writes(case):
    first = run(case)
    state, calls = deepcopy(case.client.values), list(case.storage.calls)
    second = run(case)
    assert first['task_id'] == second['task_id'] and second['idempotent_replay'] is True
    assert case.client.values == state and case.storage.calls == calls


@pytest.mark.parametrize('change', ['connection', 'revision', 'credential', 'epoch', 'profile_without_revision'])
def test_completed_import_cannot_rebind_to_new_current_authority(case, change):
    run(case)
    overrides = {}
    if change == 'connection':
        key = ingest.CHANNEL_PREFIX + CHANNEL
        value = json.loads(case.client.values[key]); value['connection_id'] = 'new-current-connection'
        case.client.values[key] = json.dumps(value)
        overrides['expected_connection_id'] = value['connection_id']
    elif change in ('revision', 'profile_without_revision'):
        key = ingest.PROFILE_PREFIX + CHANNEL
        value = json.loads(case.client.values[key])
        if change == 'revision':
            value['profile_revision'] = 'new-current-revision'
            overrides['expected_profile_revision'] = value['profile_revision']
        else:
            value['auto_publish'] = True
        case.client.values[key] = json.dumps(value)
    elif change == 'credential':
        case.client.values[ingest.CREDENTIAL_PREFIX + CHANNEL] = 'new-cipher-same-connection-id'
    else:
        case.client.values[ingest.AUTH_EPOCH_KEY] = '13'
    before, calls = deepcopy(case.client.values), list(case.storage.calls)
    with pytest.raises(ingest.ExternalMasterIngestError, match='reservation_conflict'):
        run(case, **overrides)
    assert case.client.values == before and case.storage.calls == calls


def test_completed_replay_ignores_channel_telemetry_but_not_authority(case):
    first = run(case)
    key = ingest.CHANNEL_PREFIX + CHANNEL
    channel = json.loads(case.client.values[key])
    channel.update(verified_at='later', view_count=1250, title='Updated display title')
    case.client.values[key] = json.dumps(channel)
    before, calls = deepcopy(case.client.values), list(case.storage.calls)
    assert run(case)['task_id'] == first['task_id']
    assert case.client.values == before and case.storage.calls == calls


@pytest.mark.parametrize('change', ['connection', 'revision', 'credential', 'index', 'reconnect', 'malformed_reconnect', 'language'])
def test_stale_or_missing_channel_binding_rejected_before_storage(case, change):
    channel_key, profile_key = ingest.CHANNEL_PREFIX + CHANNEL, ingest.PROFILE_PREFIX + CHANNEL
    channel, profile = json.loads(case.client.values[channel_key]), json.loads(case.client.values[profile_key])
    if change == 'connection': channel['connection_id'] = 'new-connection-id'
    if change == 'revision': profile['profile_revision'] = 'changed-revision'
    if change == 'reconnect': channel['requires_reconnect'] = True
    if change == 'malformed_reconnect': channel['requires_reconnect'] = 0
    if change == 'language': profile['languages'] = ['tr']
    if change == 'credential': case.client.values.pop(ingest.CREDENTIAL_PREFIX + CHANNEL)
    if change == 'index': case.client.sets[ingest.CHANNEL_INDEX_KEY].clear()
    case.client.values[channel_key], case.client.values[profile_key] = json.dumps(channel), json.dumps(profile)
    before = deepcopy(case.client.values)
    with pytest.raises(ingest.ExternalMasterIngestError): run(case)
    assert not case.storage.calls and case.client.values == before


def test_storage_unknown_fences_retry_without_more_writes(case):
    def fail(_): raise RuntimeError('private provider diagnostic must not escape')
    case.storage.after_put = fail
    with pytest.raises(ingest.ExternalMasterIngestError, match='storage_uncertain'): run(case)
    calls = list(case.storage.calls)
    with pytest.raises(ingest.ExternalMasterIngestError, match='busy_or_uncertain'): run(case)
    assert case.storage.calls == calls and not case.client.sorted.get(ingest.JOB_INDEX)


def test_concurrent_duplicate_while_first_upload_reserved_is_blocked(case):
    blocked = []
    def concurrent(count):
        if count == 1:
            with pytest.raises(ingest.ExternalMasterIngestError, match='busy_or_uncertain'):
                run(case)
            blocked.append(True)
    case.storage.after_put = concurrent
    result = run(case)
    assert blocked == [True] and len(case.storage.calls) == 3
    assert len(case.client.sorted[ingest.JOB_INDEX]) == 1 and job(case, result)['state'] == 'SUCCESS'


def test_profile_change_during_upload_never_registers_or_retries(case):
    def change(count):
        if count == 1:
            key = ingest.PROFILE_PREFIX + CHANNEL
            value = json.loads(case.client.values[key]); value['profile_revision'] = 'new-revision'
            case.client.values[key] = json.dumps(value)
    case.storage.after_put = change
    with pytest.raises(ingest.ExternalMasterIngestError, match='profile_changed'): run(case)
    assert not case.client.sorted.get(ingest.JOB_INDEX)
    assert any(json.loads(value).get('status') == 'uncertain' for key, value in case.client.values.items()
               if key.startswith(ingest.RESERVATION_PREFIX))


def test_changed_file_cannot_register_success(case):
    def change(count):
        if count == 1: case.staged.video.write_bytes(b'changed media')
    case.storage.after_put = change
    with pytest.raises(ingest.artifact.ExternalArtifactValidationError): run(case)
    assert not case.client.sorted.get(ingest.JOB_INDEX)


@pytest.mark.parametrize('phase', ['reservation', 'completion'])
def test_redis_unknown_outcome_never_repeats_paid_or_storage_side_effect(case, phase):
    def uncertain(operations):
        matches = (len(operations) == 1) if phase == 'reservation' else (len(operations) == 4)
        if matches:
            case.client.after_execute = None
            raise RuntimeError('connection lost after EXEC')
    case.client.after_execute = uncertain
    with pytest.raises(ingest.ExternalMasterIngestError, match='unavailable'): run(case)
    calls = list(case.storage.calls)
    if phase == 'reservation':
        with pytest.raises(ingest.ExternalMasterIngestError, match='busy_or_uncertain'): run(case)
    else:
        assert run(case)['idempotent_replay'] is True
    assert case.storage.calls == calls


def test_existing_job_changed_cannot_be_returned_as_matching_import(case):
    result = run(case)
    key = ingest.JOB_PREFIX + result['task_id']
    value = json.loads(case.client.values[key]); value['result']['video_key'] = 'wrong/video.mp4'
    case.client.values[key] = json.dumps(value)
    with pytest.raises(ingest.ExternalMasterIngestError, match='existing_job_conflict'): run(case)
    assert len(case.storage.calls) == 3


def test_same_immutable_artifact_can_be_reviewed_on_distinct_channel_without_overwrite(case):
    first = run(case)
    second_channel = 'UCsecondexternal12345'
    case.client.sets[ingest.CHANNEL_INDEX_KEY].add(second_channel)
    case.client.values[ingest.CHANNEL_PREFIX + second_channel] = json.dumps(
        {'id': second_channel, 'connection_id': CONNECTION, 'requires_reconnect': False})
    case.client.values[ingest.CREDENTIAL_PREFIX + second_channel] = 'second-private-cipher'
    case.client.values[ingest.PROFILE_PREFIX + second_channel] = json.dumps(
        {'channel_id': second_channel, 'profile_revision': REVISION, 'languages': ['en']})
    objects = deepcopy(case.storage.objects)
    second = run(case, target_channel_id=second_channel)
    assert first['task_id'] != second['task_id'] and first['descriptor_id'] == second['descriptor_id']
    assert case.storage.objects == objects and len(case.client.sorted[ingest.JOB_INDEX]) == 2


def test_conflicting_existing_immutable_object_is_not_overwritten(case):
    declaration = case.staged.manifest['files']['video']
    key = 'external-masters/v1/' + declaration['sha256'] + '/master.mp4'
    case.storage.objects[key] = {'ContentLength': declaration['size'], 'ContentType': 'video/mp4',
                                'Metadata': {'sha256': '0' * 64}, 'bytes': b'wrong artifact'}
    with pytest.raises(ingest.ExternalMasterIngestError, match='storage_conflict'): run(case)
    assert case.storage.objects[key]['bytes'] == b'wrong artifact'
    assert not case.client.sorted.get(ingest.JOB_INDEX)


def test_credential_rotation_during_storage_fails_closed_without_exporting_cipher(case):
    def rotate(count):
        if count == 1:
            case.client.values[ingest.CREDENTIAL_PREFIX + CHANNEL] = 'rotated-private-cipher'
    case.storage.after_put = rotate
    with pytest.raises(ingest.ExternalMasterIngestError, match='binding_changed') as failure:
        run(case)
    assert 'cipher' not in str(failure.value) and not case.client.sorted.get(ingest.JOB_INDEX)


def test_http_contract_auth_and_real_structural_validation(case):
    response = post(case)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['manual_qa_required'] is True and result['publish_eligible'] is False
    assert post(case).json()['idempotent_replay'] is True


def test_unauthorized_request_rejected_before_parsing(case):
    response = case.http.post('/studio/api/external-masters', content=b'not multipart',
                             headers={'X-Factory-Token': 'wrong-token'})
    assert response.status_code == 401 and not case.storage.calls


def test_targets_are_authenticated_read_only_safe_metadata(case):
    before = deepcopy(case.client.values)
    assert case.http.get('/studio/api/external-masters/targets').status_code == 401
    response = case.http.get('/studio/api/external-masters/targets',
                             headers={'X-Factory-Token': 'test-factory-token'})
    assert response.status_code == 200
    assert response.json() == {'targets': [{'target_channel_id': CHANNEL, 'title': CHANNEL,
        'languages': ['en'], 'expected_connection_id': CONNECTION, 'expected_profile_revision': REVISION}]}
    assert case.client.values == before and not case.storage.calls
    assert 'cipher' not in response.text


def test_targets_omit_stale_and_bound_the_index(case):
    case.client.sets[ingest.CHANNEL_INDEX_KEY].add('UCmissing-profile')
    assert len(ingest.list_external_master_targets()['targets']) == 1
    case.client.sets[ingest.CHANNEL_INDEX_KEY].update('UCextra-channel-' + str(i) for i in range(10))
    with pytest.raises(ingest.ExternalMasterIngestError, match='index_invalid'):
        ingest.list_external_master_targets()


@pytest.mark.parametrize('mutation', ['manifest_flag', 'extra_field', 'bad_hash', 'stale_revision'])
def test_malformed_or_stale_http_request_never_imports(case, mutation):
    extra = {}
    if mutation == 'manifest_flag': case.staged.manifest['qa_approved'] = True
    if mutation == 'extra_field': extra['publish_eligible'] = 'true'
    if mutation == 'bad_hash': case.staged.manifest['files']['video']['sha256'] = '0' * 64
    if mutation == 'stale_revision': extra['expected_profile_revision'] = 'wrong-revision'
    response = post(case, extra=extra)
    assert response.status_code in (409, 422), response.text
    assert not case.storage.calls and not case.client.sorted.get(ingest.JOB_INDEX)


@pytest.mark.parametrize('lying_length', [False, True])
def test_actual_stream_and_declared_length_are_bounded(case, monkeypatch, lying_length):
    monkeypatch.setattr(routes, 'MAX_REQUEST_BYTES', 2048)
    response = post(case, headers={'Content-Length': '1'} if lying_length else {})
    assert response.status_code == 413, response.text
    assert not case.storage.calls
