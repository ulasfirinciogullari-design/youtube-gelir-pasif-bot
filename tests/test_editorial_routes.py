"""Owner API boundary; no external credentials, services or paid calls."""
import asyncio
import base64
from uuid import uuid4
import sys
from types import ModuleType

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from app import editorial_routes as routes
from app.external_routes import settings


@pytest.fixture
def case(monkeypatch):
    # Older suites intentionally replace app.config with a minimal module at
    # collection time; add this fixture's field without weakening the checks.
    monkeypatch.setattr(settings, 'factory_api_token', 'editorial-test-token', raising=False)
    calls = []
    monkeypatch.setattr(routes, '_review_and_queue', lambda *args: calls.append(args) or {'task_id': args[0]})
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app), calls, str(uuid4())


def post(case, **kwargs):
    client, _, task = case
    return client.post('/studio/api/external-masters/' + task + '/review',
                       headers={'X-Factory-Token': 'editorial-test-token',
                                'Content-Type': 'application/json'}, **kwargs)


def test_authorized_owner_review_is_passed_once(case):
    assert post(case, json={'evidence_pack': {'version': 1}}).status_code == 200
    assert case[1] == [(case[2], {'version': 1})]


def test_optional_reference_decodes_to_ephemeral_bytes_once(case):
    reference = b'\x00\xffretained-video-bytes'
    encoded = base64.b64encode(reference).decode('ascii')
    response = post(case, json={'evidence_pack': {'version': 1}, 'reviewed_reference_video': encoded})
    assert response.status_code == 200
    assert case[1] == [(case[2], {'version': 1}, reference)]
    assert encoded not in response.text and 'reviewed_reference_video' not in response.text


def test_auth_before_body_or_task_parsing(case):
    assert case[0].post('/studio/api/external-masters/not-uuid/review', content=b'bad').status_code == 401
    assert not case[1]


def test_unauthorized_reference_is_rejected_before_headers_or_stream(case):
    class NeverRead:
        @property
        def headers(self):
            raise AssertionError('Auth must precede even declared body size')
        async def stream(self):
            raise AssertionError('Never consume an unauthorized reference')
            yield b''
    with pytest.raises(HTTPException) as error:
        asyncio.run(routes.review_external_master('not-uuid', NeverRead(), None))
    assert error.value.status_code == 401 and not case[1]


@pytest.mark.parametrize('content', [b'{}', b'[]', b'null', b'{',
    b'{"evidence_pack":{},"evidence_pack":{}}',
    b'{"evidence_pack":{},"qa_approved":true}', b'{"evidence_pack":[]}'])
def test_strict_object_no_duplicate_or_approval_fields(case, content):
    assert post(case, content=content).status_code == 422
    assert not case[1]


@pytest.mark.parametrize('encoded', [None, False, 3, [], {}, '', 'Zg', 'Zg===', 'Zh==',
    'Zg==\n', ' Zg==', '_w==', '-w==', 'Zg==Zg==', 'data:video/mp4;base64,Zg==', 'ş'])
def test_reference_requires_canonical_standard_base64(case, encoded):
    response = post(case, json={'evidence_pack': {}, 'reviewed_reference_video': encoded})
    assert response.status_code == 422 and not case[1]
    assert response.json() == {'detail': 'editorial_schema_invalid'}


@pytest.mark.parametrize('extra', [{'qa_approved': True}, {'reference_video': 'Zg=='},
    {'skip_pcm_check': True}, {'reference_video_url': 'https://example.com/video.mp4'}])
def test_reference_does_not_allow_extra_inputs_or_waivers(case, extra):
    response = post(case, json={'evidence_pack': {}, 'reviewed_reference_video': 'Zg==', **extra})
    assert response.status_code == 422 and not case[1]


def test_duplicate_reference_is_rejected(case):
    response = post(case, content=b'{"evidence_pack":{},"reviewed_reference_video":"Zg==","reviewed_reference_video":"Zg=="}')
    assert response.status_code == 422 and not case[1]


@pytest.mark.parametrize('size', [3, 4])
def test_reference_encoded_and_decoded_sizes_are_both_bounded(case, monkeypatch, size):
    monkeypatch.setattr(routes, 'MAX_REFERENCE_VIDEO_BYTES', 2)
    encoded = base64.b64encode(b'x' * size).decode('ascii')
    response = post(case, json={'evidence_pack': {}, 'reviewed_reference_video': encoded})
    assert response.status_code == 413 and not case[1]
    assert response.json() == {'detail': 'editorial_reference_video_too_large'}


def test_reference_at_exact_decoded_boundary_is_allowed(case, monkeypatch):
    monkeypatch.setattr(routes, 'MAX_REFERENCE_VIDEO_BYTES', 2)
    assert post(case, json={'evidence_pack': {}, 'reviewed_reference_video': 'eHg='}).status_code == 200
    assert case[1] == [(case[2], {}, b'xx')]


@pytest.mark.parametrize('with_reference', [False, True])
def test_evidence_limit_does_not_expand_with_reference_allowance(case, monkeypatch, with_reference):
    monkeypatch.setattr(routes, 'MAX_REVIEW_EVIDENCE_BYTES', 20)
    value = {'evidence_pack': {'note': 'x' * 30}}
    if with_reference:
        value['reviewed_reference_video'] = 'Zg=='
    assert post(case, json=value).status_code == 422 and not case[1]


def test_real_body_is_bounded(case, monkeypatch):
    monkeypatch.setattr(routes, 'MAX_REVIEW_REQUEST_BYTES', 20)
    assert post(case, content=b' ' * 21).status_code == 413
    assert not case[1]


def test_actual_stream_bound_cannot_be_bypassed_with_small_declared_length(case, monkeypatch):
    monkeypatch.setattr(routes, 'MAX_REVIEW_REQUEST_BYTES', 20)
    response = case[0].post('/studio/api/external-masters/' + case[2] + '/review', content=b' ' * 21,
        headers={'X-Factory-Token': 'editorial-test-token', 'Content-Type': 'application/json', 'Content-Length': '1'})
    assert response.status_code == 413 and not case[1]


@pytest.mark.parametrize('length', ['-1', 'invalid', '9' * 20, str(routes.MAX_REVIEW_REQUEST_BYTES + 1)])
def test_invalid_or_oversized_declared_body_is_rejected(case, length):
    response = case[0].post('/studio/api/external-masters/' + case[2] + '/review', content=b'{}',
        headers={'X-Factory-Token': 'editorial-test-token', 'Content-Type': 'application/json', 'Content-Length': length})
    assert response.status_code == 413 and not case[1]


def test_full_1080p_reference_fits_bounded_shared_cap(case):
    from app.services import external_editorial_review as review
    assert routes.MAX_REFERENCE_VIDEO_BYTES == review.MAX_REFERENCE_VIDEO_BYTES == 16 * 1024 * 1024
    assert routes.MAX_REVIEW_EVIDENCE_BYTES == 1024 * 1024
    assert routes.MAX_REVIEW_REQUEST_BYTES == 24 * 1024 * 1024
    # The actual new LEGO master is 14,622,034 bytes: don't transcode it or
    # rewrite its retained QA just to fit the former 8 MiB envelope.
    reference = b'x' * 14622034
    encoded = base64.b64encode(reference).decode('ascii')
    response = post(case, json={'evidence_pack': {'version': 2}, 'reviewed_reference_video': encoded})
    assert response.status_code == 200
    assert case[1] == [(case[2], {'version': 2}, reference)]
    assert 'reviewed_reference_video' not in response.text


def test_reference_bound_envelope_leaves_room_for_unchanged_evidence_limit():
    envelope = 4 * ((routes.MAX_REFERENCE_VIDEO_BYTES + 2) // 3)
    assert envelope + routes.MAX_REVIEW_EVIDENCE_BYTES + 1024 < routes.MAX_REVIEW_REQUEST_BYTES


def test_content_type_and_uuid(case):
    client, calls, task = case
    assert client.post('/studio/api/external-masters/' + task + '/review',
        headers={'X-Factory-Token': 'editorial-test-token'}, content=b'{}').status_code == 415
    assert client.post('/studio/api/external-masters/not-uuid/review',
        headers={'X-Factory-Token': 'editorial-test-token'}, json={}).status_code == 422
    assert not calls


@pytest.mark.parametrize('error,status', [(routes.EditorialReviewError('private diagnostic'), 409),
                                         (RuntimeError('private diagnostic'), 503)])
def test_errors_sanitized_and_no_false_success(case, monkeypatch, error, status):
    def fail(*args):
        raise error
    monkeypatch.setattr(routes, '_review_and_queue', fail)
    response = post(case, json={'evidence_pack': {}})
    assert response.status_code == status
    assert 'private diagnostic' not in response.text


@pytest.mark.parametrize('reference', [None, b'ephemeral-reference'])
def test_review_then_existing_publisher_only(monkeypatch, reference):
    task, events, review_calls = str(uuid4()), [], []
    studio_state = ModuleType('app.services.studio_state')
    publish_tasks = ModuleType('app.publish_tasks')
    monkeypatch.setitem(sys.modules, studio_state.__name__, studio_state)
    monkeypatch.setitem(sys.modules, publish_tasks.__name__, publish_tasks)
    def review(*args, **kwargs):
        events.append('review')
        review_calls.append((args, kwargs))
    monkeypatch.setattr(routes, 'create_editorial_review', review)
    monkeypatch.setattr(studio_state, 'get_job', lambda _: {'result': {
        'quality_disposition': 'editorial_review_pass', 'editorial_review_id': task,
        'editorial_review_sha256': 'a' * 64}}, raising=False)
    monkeypatch.setattr(publish_tasks, 'queue_automatic_publish',
        lambda _: events.append('queue') or {'status': 'queued'}, raising=False)
    result = routes._review_and_queue(task, {}, reference)
    assert events == ['review', 'queue']
    assert review_calls == [((task, {}), {} if reference is None else {'reference_video': reference})]
    assert result['publication'] == {'status': 'queued'}
    assert result['studio_url'] == '/studio/job/' + task
    assert 'reference_video' not in result and 'reviewed_reference_video' not in result
