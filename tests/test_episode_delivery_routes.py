"""Owner-authenticated bounded endpoint; no live state or provider access."""
import asyncio
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from app import episode_delivery_routes as routes
from app.external_routes import settings


@pytest.fixture
def case(monkeypatch):
    monkeypatch.setattr(settings, 'factory_api_token', 'episode-test-token', raising=False)
    monkeypatch.setattr(routes, 'request_production_tick', lambda: True)
    external, original, leaf, publisher = [str(uuid4()) for _ in range(4)]
    value = {'channel_id': 'UCeditorial12345', 'original_task_id': original, 'failed_leaf_id': leaf,
             'expected_profile_revision': 'current-profile-revision', 'expected_topic_sha256': 'a' * 64,
             'editorial_explanation': 'This reviewed public video replaces the same frozen membership episode.'}
    calls = []
    response = {'status': 'resolved', 'request': {'never': 'expose'},
        'public_delivery': {'external_task_id': external, 'publish_task_id': publisher, 'youtube_video_id': 'Abc123def45'},
        'evidence': {'private': 'do-not-return'}, 'qa_approved': False, 'receipt_sha256': 'b' * 64}
    def resolve(**kwargs):
        calls.append(kwargs)
        return response
    monkeypatch.setattr(routes, 'resolve_external_episode', resolve)
    app = FastAPI(); app.include_router(routes.router)
    return TestClient(app), value, external, publisher, calls, response


def post(case, *, value=None, **kwargs):
    client, expected, external, *_ = case
    kwargs.setdefault('headers', {'X-Factory-Token': 'episode-test-token', 'Content-Type': 'application/json'})
    if 'content' not in kwargs:
        kwargs['json'] = expected if value is None else value
    return client.post('/studio/api/external-masters/' + external + '/resolve-episode', **kwargs)


@pytest.mark.parametrize('status', ['resolved', 'already_resolved'])
def test_exact_owner_request_forwards_once_and_returns_only_compact_safe_ids(case, status):
    case[5]['status'] = status
    response = post(case)
    assert response.status_code == 200 and case[4] == [{'external_task_id': case[2], **case[1]}]
    assert response.json() == {'status': status, 'channel_id': case[1]['channel_id'],
        'original_task_id': case[1]['original_task_id'], 'failed_leaf_id': case[1]['failed_leaf_id'],
        'external_task_id': case[2], 'publish_task_id': case[3], 'youtube_video_id': 'Abc123def45'}
    assert 'private' not in response.text and 'qa_approved' not in response.text and 'receipt' not in response.text


def test_auth_rejected_before_any_body_or_path_inspection(case):
    class NeverRead:
        @property
        def headers(self):
            raise AssertionError('Headers/body must not be inspected before auth')
        async def stream(self):
            raise AssertionError('Body must not be consumed before auth')
            yield b''
    with pytest.raises(HTTPException) as error:
        asyncio.run(routes.resolve_external_episode_delivery('not-a-uuid', NeverRead(), None))
    assert error.value.status_code == 401 and not case[4]


def test_missing_config_is_safe_and_does_not_call_service(case, monkeypatch):
    monkeypatch.setattr(settings, 'factory_api_token', '', raising=False)
    assert post(case).status_code == 503 and not case[4]


@pytest.mark.parametrize('body', [b'{}', b'[]', b'null', b'{', b'\xff', b'{"channel_id":"a","channel_id":"b"}',
    b'{"channel_id":NaN}', b'{"qa_approved":true}'])
def test_invalid_json_duplicate_keys_and_qa_flags_rejected(case, body):
    assert post(case, content=body).status_code == 422 and not case[4]


@pytest.mark.parametrize('field,value', [('channel_id', '../other'), ('channel_id', None),
    ('original_task_id', 'bad'), ('failed_leaf_id', 1), ('expected_profile_revision', ''),
    ('expected_topic_sha256', 'A' * 64), ('expected_topic_sha256', 'b' * 63),
    ('editorial_explanation', 'too short'), ('editorial_explanation', 'x' * 1201),
    ('editorial_explanation', 'api_key=sk-do-not-leak-this-secret-value'),
    ('unexpected', 'not allowed'), ('now', 0), ('qa_approved', True), ('expected_profile_sha256', 'b' * 64)])
def test_strict_schema_ids_hash_and_editorial_explanation(case, field, value):
    request = {**case[1], field: value}
    response = post(case, value=request)
    assert response.status_code == 422 and not case[4] and 'sk-' not in response.text


def test_same_source_cannot_be_its_own_failed_replacement(case):
    assert post(case, value={**case[1], 'failed_leaf_id': case[2]}).status_code == 422
    assert not case[4]


def test_wrong_content_type_and_noncanonical_path_uuid(case):
    assert post(case, headers={'X-Factory-Token': 'episode-test-token'}, content=b'{}').status_code == 415
    response = case[0].post('/studio/api/external-masters/not-uuid/resolve-episode',
                           headers={'X-Factory-Token': 'episode-test-token'}, json=case[1])
    assert response.status_code == 422 and not case[4]


def test_declared_size_and_actual_stream_are_independently_bounded(case, monkeypatch):
    monkeypatch.setattr(routes, 'MAX_DELIVERY_REQUEST_BYTES', 20)
    assert post(case, content=b'x' * 21).status_code == 413
    assert post(case, content=b'x' * 21,
                headers={'X-Factory-Token': 'episode-test-token', 'Content-Type': 'application/json', 'Content-Length': '1'}).status_code == 413
    assert not case[4]


@pytest.mark.parametrize('value', ['-1', 'invalid', '9' * 20])
def test_bad_content_length_is_rejected_before_service(case, value):
    assert post(case, headers={'X-Factory-Token': 'episode-test-token', 'Content-Type': 'application/json',
                              'Content-Length': value}).status_code == 413
    assert not case[4]


@pytest.mark.parametrize('error,status', [(routes.ExternalEpisodeDeliveryError('private stale receipt'), 409),
                                         (RuntimeError('token=private'), 503)])
def test_failures_are_fixed_safe_codes_not_raw_receipts_or_provider_details(case, monkeypatch, error, status):
    def fail(**kwargs): raise error
    monkeypatch.setattr(routes, 'resolve_external_episode', fail)
    response = post(case)
    assert response.status_code == status and 'private' not in response.text and 'token' not in response.text


def test_malformed_internal_response_is_not_serialized(case):
    case[5]['public_delivery']['youtube_video_id'] = '<private unsafe response>'
    response = post(case)
    assert response.status_code == 503 and 'private' not in response.text


def test_router_is_included_in_actual_app_without_importing_worker_dependencies():
    source = (Path(__file__).resolve().parents[1] / 'app/main.py').read_text(encoding='utf-8')
    assert 'from app.episode_delivery_routes import router as episode_delivery_router' in source
    assert 'app.include_router(episode_delivery_router)' in source
