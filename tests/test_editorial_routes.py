"""Owner API boundary; no external credentials, services or paid calls."""
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app import editorial_routes as routes
from app.external_routes import settings


@pytest.fixture
def case(monkeypatch):
    monkeypatch.setattr(settings, 'factory_api_token', 'editorial-test-token')
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


def test_auth_before_body_or_task_parsing(case):
    assert case[0].post('/studio/api/external-masters/not-uuid/review', content=b'bad').status_code == 401
    assert not case[1]


@pytest.mark.parametrize('content', [b'{}', b'[]', b'null', b'{',
    b'{"evidence_pack":{},"evidence_pack":{}}',
    b'{"evidence_pack":{},"qa_approved":true}', b'{"evidence_pack":[]}'])
def test_strict_object_no_duplicate_or_approval_fields(case, content):
    assert post(case, content=content).status_code == 422
    assert not case[1]


def test_real_body_is_bounded(case, monkeypatch):
    monkeypatch.setattr(routes, 'MAX_REVIEW_REQUEST_BYTES', 20)
    assert post(case, content=b' ' * 21).status_code == 413
    assert not case[1]


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


def test_review_then_existing_publisher_only(monkeypatch):
    from app.services import studio_state
    from app import publish_tasks
    task, events = str(uuid4()), []
    monkeypatch.setattr(routes, 'create_editorial_review', lambda *args: events.append('review'))
    monkeypatch.setattr(studio_state, 'get_job', lambda _: {'result': {
        'quality_disposition': 'editorial_review_pass', 'editorial_review_id': task,
        'editorial_review_sha256': 'a' * 64}})
    monkeypatch.setattr(publish_tasks, 'queue_automatic_publish',
        lambda _: events.append('queue') or {'status': 'queued'})
    result = routes._review_and_queue(task, {})
    assert events == ['review', 'queue']
    assert result['publication'] == {'status': 'queued'}
    assert result['studio_url'] == '/studio/job/' + task
