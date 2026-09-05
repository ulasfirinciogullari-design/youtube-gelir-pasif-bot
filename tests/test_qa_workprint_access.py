"""Private workprints are pinned, range-bounded and never publication results."""
import asyncio
import hashlib
import io
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from starlette.requests import Request

from app.services import qa_workprint_access as access


TASK = '11111111-1111-4111-8111-111111111111'
OTHER = '22222222-2222-4222-8222-222222222222'
DATA = b'private-mp4-content-' * 9000


def pointer():
    sha = hashlib.sha256(DATA).hexdigest()
    return {'version': 1, 'status': 'qa_workprint', 'qa_approved': False,
            'publish_eligible': False, 'reusable': False, 'task_id': TASK,
            'key': f'qa_workprints/{TASK}/{sha}.mp4', 'sha256': sha,
            'size': len(DATA), 'etag': '"abcdef0123456789-1"',
            'duration_seconds': 30.0, 'frame_count': 900, 'width': 1080, 'height': 1920,
            'metadata_key': f'qa_workprints/{TASK}/{"b" * 64}.json',
            'metadata_sha256': 'b' * 64, 'metadata_size': 500}


def request(method='GET', ranges=()):
    return Request({'type': 'http', 'method': method, 'path': '/',
                    'headers': [(b'range', value.encode()) for value in ranges]})


class Body(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.read_sizes = []
        self.close_count = 0

    def read(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)

    def close(self):
        self.close_count += 1
        super().close()


@pytest.fixture
def store(monkeypatch):
    client = Mock()
    state = SimpleNamespace(client=client, bodies=[], results=[])
    monkeypatch.setattr(access.storage, '_client', lambda: client)
    monkeypatch.setattr(access.storage.settings, 'bucket', 'private-test-bucket', raising=False)

    def get_object(**kwargs):
        p = pointer()
        data = DATA
        content_range = None
        if 'Range' in kwargs:
            start, end = map(int, kwargs['Range'][6:].split('-'))
            data = DATA[start:end + 1]
            content_range = f'bytes {start}-{end}/{len(DATA)}'
        body = Body(data)
        result = {'Body': body, 'ETag': p['etag'], 'Metadata': {'sha256': p['sha256']},
                  'ContentLength': len(data), 'ContentType': 'video/mp4',
                  'ResponseMetadata': {'HTTPStatusCode': 206 if content_range else 200}}
        if content_range:
            result['ContentRange'] = content_range
        state.bodies.append(body)
        state.results.append(result)
        return result

    client.get_object.side_effect = get_object
    return state


def consume(response):
    async def run():
        chunks = [chunk async for chunk in response.body_iterator]
        if response.background:
            await response.background()
        return b''.join(chunks)
    return asyncio.run(run())


def assert_private(response):
    assert response.headers['cache-control'] == 'private, no-store'
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert response.headers['referrer-policy'] == 'no-referrer'
    serialized = repr(dict(response.headers)) + repr(getattr(response, 'body', b''))
    assert 'qa_workprints/' not in serialized
    assert 'private-test-bucket' not in serialized
    assert 'Location' not in response.headers


def test_pointer_is_pure_detached_and_requires_failed_render():
    p = pointer()
    job = {'task_id': TASK, 'kind': 'render', 'state': 'FAILURE', 'qa_workprint': p}
    found = access.validated_pointer(job, TASK)
    assert found == p and found is not p
    assert access.validated_pointer(job, OTHER) is None
    for change in ({'state': 'SUCCESS'}, {'state': 'STARTED'}, {'kind': 'plan'}, {'task_id': OTHER}):
        assert access.validated_pointer({**job, **change}) is None
    assert access.validated_pointer(None) is None


@pytest.mark.parametrize('field,value', [
    ('version', True), ('version', 2), ('status', 'complete'), ('status', 'unapproved_workprint'),
    ('qa_approved', True), ('qa_approved', 0), ('publish_eligible', True), ('reusable', True),
    ('task_id', TASK.upper().replace('1111', 'ABCD', 1)), ('task_id', '../escape'),
    ('size', 0), ('size', True), ('size', 64 * 1024 * 1024 + 1),
    ('sha256', 'A' * 64), ('key', 'https://example.invalid/video.mp4'),
    ('key', f'qa_workprints/{OTHER}/{hashlib.sha256(DATA).hexdigest()}.mp4'),
    ('etag', 'unquoted'), ('etag', 'W/"weak"'), ('etag', '"x\r\ny"'),
    ('duration_seconds', True), ('duration_seconds', 29.9), ('duration_seconds', float('nan')),
    ('frame_count', 899), ('frame_count', 900.0), ('width', 1920), ('height', 1080),
    ('metadata_key', 'somewhere/else.json'), ('metadata_sha256', 'invalid'),
    ('metadata_size', 0), ('metadata_size', True), ('metadata_size', 1024 * 1024 + 1),
])
def test_invalid_pointer_never_reaches_storage(store, field, value):
    p = {**pointer(), field: value}
    assert access.validated_pointer({'task_id': TASK, 'kind': 'render', 'state': 'FAILURE', 'qa_workprint': p}) is None
    response = access.stream_response(p, request())
    assert response.status_code == 404
    assert_private(response)
    store.client.get_object.assert_not_called()


def test_pointer_missing_or_extra_fields_are_rejected():
    p = pointer()
    for field in list(p):
        damaged = dict(p)
        damaged.pop(field)
        assert not access._valid_pointer(damaged)
    assert not access._valid_pointer({**p, 'download_url': 'https://example.invalid/'})


def test_full_get_is_pinned_and_read_in_bounded_chunks(store):
    response = access.stream_response(pointer(), request())
    assert response.status_code == 200
    assert consume(response) == DATA
    store.client.get_object.assert_called_once_with(
        Bucket='private-test-bucket', Key=pointer()['key'], IfMatch=pointer()['etag'])
    body = store.bodies[0]
    assert max(body.read_sizes) == 65536 and min(body.read_sizes) > 0
    assert body.close_count == 1
    assert response.headers['content-length'] == str(len(DATA))
    assert_private(response)


@pytest.mark.parametrize('value,start,end', [
    ('bytes=0-99', 0, 99), ('bytes=42-', 42, len(DATA)-1),
    ('bytes=-100', len(DATA)-100, len(DATA)-1),
    ('bytes=100-999999999', 100, len(DATA)-1),
    ('bytes=-999999999', 0, len(DATA)-1),
    (f'bytes={len(DATA)-1}-{len(DATA)-1}', len(DATA)-1, len(DATA)-1),
])
def test_single_ranges_use_exact_pinned_storage_range(store, value, start, end):
    response = access.stream_response(pointer(), request(ranges=[value]))
    assert response.status_code == 206
    assert consume(response) == DATA[start:end+1]
    assert store.client.get_object.call_args.kwargs['Range'] == f'bytes={start}-{end}'
    assert store.client.get_object.call_args.kwargs['IfMatch'] == pointer()['etag']
    assert response.headers['content-range'] == f'bytes {start}-{end}/{len(DATA)}'
    assert response.headers['content-length'] == str(end-start+1)
    assert store.bodies[0].close_count == 1
    assert_private(response)


@pytest.mark.parametrize('ranges', [
    ['bytes=0-1,4-5'], ['bytes=0-1', 'bytes=4-5'], ['bytes=-'], ['bytes=-0'],
    ['bytes=8-2'], [f'bytes={len(DATA)}-'], ['bytes=one-two'], ['items=0-1'],
    ['bytes=+1-2'], ['bytes='+'9'*100+'-'], ['bytes= 0-1'],
])
def test_bad_or_multiple_ranges_fail_before_storage(store, ranges):
    response = access.stream_response(pointer(), request(ranges=ranges))
    assert response.status_code == 416
    assert response.headers['content-range'] == f'bytes */{len(DATA)}'
    store.client.get_object.assert_not_called()
    assert_private(response)


def test_head_returns_metadata_and_closes_without_reading_body(store):
    response = access.stream_response(pointer(), request('HEAD', ['bytes=0-3']))
    assert response.status_code == 200 and response.body == b''
    assert response.headers['content-length'] == str(len(DATA))
    assert 'Range' not in store.client.get_object.call_args.kwargs
    assert store.bodies[0].read_sizes == [] and store.bodies[0].close_count == 1
    assert_private(response)


@pytest.mark.parametrize('change', [
    {'ETag': '"changed"'}, {'Metadata': {'sha256': 'f'*64}}, {'Metadata': {}},
    {'ContentLength': 1}, {'ContentLength': True}, {'ContentRange': 'bytes 0-9/100'},
    {'ResponseMetadata': {'HTTPStatusCode': 206}}, {'ContentType': 'text/html'},
    {'ContentEncoding': 'gzip'},
])
def test_unpinned_or_malformed_object_response_is_closed_and_not_streamed(store, change):
    original = store.client.get_object.side_effect
    store.client.get_object.side_effect = lambda **kwargs: {**original(**kwargs), **change}
    response = access.stream_response(pointer(), request())
    assert response.status_code == 503
    assert store.bodies[0].read_sizes == [] and store.bodies[0].close_count == 1
    assert_private(response)


@pytest.mark.parametrize('change', [
    {'ContentRange': None}, {'ContentRange': f'bytes 1-99/{len(DATA)}'},
    {'ContentRange': 'bytes 0-99/999999'}, {'ContentLength': 101},
    {'ResponseMetadata': {'HTTPStatusCode': 200}},
])
def test_storage_must_honor_requested_range_exactly(store, change):
    original = store.client.get_object.side_effect
    store.client.get_object.side_effect = lambda **kwargs: {**original(**kwargs), **change}
    response = access.stream_response(pointer(), request(ranges=['bytes=0-99']))
    assert response.status_code == 503 and store.bodies[0].close_count == 1


@pytest.mark.parametrize('error', [RuntimeError('SECRET https://private.invalid/key'),
                                 ValueError('IfMatch failed with secret metadata')])
def test_upstream_ifmatch_or_network_error_never_exposes_detail(store, error):
    store.client.get_object.side_effect = error
    response = access.stream_response(pointer(), request())
    assert response.status_code == 503
    assert b'SECRET' not in response.body and b'https://' not in response.body
    assert_private(response)


@pytest.mark.parametrize('mode', ['truncated', 'extra', 'exception', 'oversized'])
def test_bad_body_fails_closed_and_always_closes(store, mode):
    response = access.stream_response(pointer(), request())
    body = store.bodies[0]
    if mode == 'truncated':
        body.seek(0); body.truncate(20)
    if mode == 'extra':
        body.seek(0, 2); body.write(b'extra'); body.seek(0)
    if mode == 'exception':
        body.read = Mock(side_effect=RuntimeError('SECRET https://private.invalid'))
    if mode == 'oversized':
        body.read = Mock(return_value=b'x' * (65536 + 1))
    with pytest.raises(RuntimeError, match=r'^Private QA workprint stream interrupted\.$'):
        consume(response)
    assert body.close_count == 1


def test_background_closes_body_when_stream_never_started(store):
    response = access.stream_response(pointer(), request())
    asyncio.run(response.background())
    assert store.bodies[0].close_count == 1
    assert store.bodies[0].read_sizes == []


@pytest.mark.parametrize('failure_at', ['http.response.start', 'http.response.body'])
def test_transport_disconnect_closes_body_even_before_first_chunk(store, failure_at):
    response = access.stream_response(pointer(), request())

    async def run():
        async def receive():
            await asyncio.sleep(10)
            return {'type': 'http.disconnect'}

        async def send(message):
            if message['type'] == failure_at:
                raise OSError('Disconnected client')

        with pytest.raises(Exception):
            await response({'type': 'http', 'asgi': {'spec_version': '2.4'}}, receive, send)

    asyncio.run(run())
    assert store.bodies[0].close_count == 1


def test_other_method_never_reaches_storage(store):
    response = access.stream_response(pointer(), request('POST'))
    assert response.status_code == 405
    store.client.get_object.assert_not_called()
    assert_private(response)
