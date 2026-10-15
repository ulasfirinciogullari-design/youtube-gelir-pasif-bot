"""Owner-only numbering route: no provider, automatic upload or scheduler."""
import asyncio
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from app import deleted_episode_replacement_routes as routes
from app.external_routes import settings


@pytest.fixture
def case(monkeypatch):
    monkeypatch.setattr(settings, 'factory_api_token', 'replacement-test-token', raising=False)
    task, old, leaf = [str(uuid4()) for _ in range(3)]
    value = {'previous_source_task_id': old, 'deferred_failed_leaf_id': leaf,
        'expected_channel_id': 'UCeditorial12345678', 'expected_profile_revision': 'revision-12345678',
        'owner_reason': 'The owner deleted the old second episode and authorized a newly authored replacement.'}
    calls = []
    def reserve(task_id, **kwargs):
        calls.append((task_id, kwargs))
        return {'status': 'reserved', 'task_id': task_id, 'previous_source_task_id': old,
            'channel_id': value['expected_channel_id'], 'series': {'id': 's1', 'name': 'Story', 'number': 2, 'total': 8},
            'receipt': 'PRIVATE', 'qa_approved': False}
    monkeypatch.setattr(routes, 'reserve_deleted_episode_replacement', reserve)
    app = FastAPI(); app.include_router(routes.router)
    return TestClient(app), task, value, calls


def post(case, **kwargs):
    client, task, value, _calls = case
    kwargs.setdefault('headers', {'X-Factory-Token': 'replacement-test-token', 'Content-Type': 'application/json'})
    if 'content' not in kwargs: kwargs.setdefault('json', value)
    return client.post(f'/studio/api/job/{task}/reserve-deleted-episode-replacement', **kwargs)


def test_exact_request_returns_only_numbering_summary(case):
    response = post(case)
    assert response.status_code == 200 and case[3] == [(case[1], case[2])]
    assert response.json() == {'status': 'reserved', 'task_id': case[1],
        'previous_source_task_id': case[2]['previous_source_task_id'], 'channel_id': case[2]['expected_channel_id'],
        'series': {'id': 's1', 'name': 'Story', 'number': 2, 'total': 8}}
    assert 'PRIVATE' not in response.text and 'qa_approved' not in response.text


def test_auth_before_headers_body_and_path(case):
    class NeverRead:
        @property
        def headers(self): raise AssertionError('Must not inspect request before auth')
    with pytest.raises(HTTPException) as error:
        asyncio.run(routes.reserve_replacement('bad', NeverRead(), None))
    assert error.value.status_code == 401 and not case[3]


@pytest.mark.parametrize('field,value', [('series_number', 2), ('now', 0), ('qa_approved', True),
    pytest.param('cancelled_retry_task_id', str(uuid4()), id='cancelled_retry_task_id-different-task'), ('deferred_failed_leaf_id', 'bad'),
    ('expected_profile_revision', ''), ('expected_channel_id', '../other'), ('owner_reason', 'short'),
    ('owner_reason', 'x' * 1201), ('owner_reason', 'api_key=sk-private-secret-not-allowed')])
def test_malformed_extra_and_caller_authority_fields_reject(case, field, value):
    response = post(case, json={**case[2], field: value})
    assert response.status_code == 422 and not case[3] and 'sk-private' not in response.text


@pytest.mark.parametrize('payload', [b'[]', b'null', b'{', b'\xff', b'{"owner_reason":"a","owner_reason":"b"}'])
def test_json_duplicates_and_non_objects_reject(case, payload):
    assert post(case, content=payload).status_code == 422 and not case[3]


def test_same_source_and_missing_field_reject(case):
    assert post(case, json={**case[2], 'previous_source_task_id': case[1]}).status_code == 422
    value = dict(case[2]); value.pop('owner_reason')
    assert post(case, json=value).status_code == 422 and not case[3]


def test_wrong_content_type_and_body_length_are_bounded(case, monkeypatch):
    assert post(case, content=b'{}', headers={'X-Factory-Token': 'replacement-test-token'}).status_code == 415
    monkeypatch.setattr(routes, 'MAX_REQUEST_BYTES', 10)
    assert post(case, content=b'x' * 11).status_code == 413
    assert post(case, content=b'x' * 11, headers={'X-Factory-Token': 'replacement-test-token',
        'Content-Type': 'application/json', 'Content-Length': '1'}).status_code == 413
    assert not case[3]


@pytest.mark.parametrize('error,status', [(routes.DeletedEpisodeReplacementError('PRIVATE'), 409),
                                        (RuntimeError('PRIVATE'), 503)])
def test_service_errors_never_expose_private_details(case, monkeypatch, error, status):
    def fail(*args, **kwargs): raise error
    monkeypatch.setattr(routes, 'reserve_deleted_episode_replacement', fail)
    response = post(case)
    assert response.status_code == status and 'PRIVATE' not in response.text
