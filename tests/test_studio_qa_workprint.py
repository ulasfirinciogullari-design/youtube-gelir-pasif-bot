"""Owner-only workprint UI is distinct from approved media/publication."""
import ast
from copy import deepcopy
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

from fastapi import APIRouter, FastAPI
from fastapi.responses import Response
from fastapi.testclient import TestClient
import pytest


TASK_ID = '11111111-1111-4111-8111-111111111111'
OTHER_ID = '22222222-2222-4222-8222-222222222222'
COOKIE = 'owner-only-fixture-cookie'
PATH = f'/studio/job/{TASK_ID}/qa-workprint'


@pytest.fixture
def viewer(monkeypatch):
    source = Path(__file__).resolve().parents[1] / 'app' / 'studio.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    tree.body = [node for node in tree.body if not (
        isinstance(node, ast.ImportFrom) and (node.module or '').startswith('app.')
    )]
    pointer = {'status': 'qa_workprint', 'qa_approved': False, 'publish_eligible': False,
               'key': 'PRIVATE-OBJECT-KEY', 'etag': 'PRIVATE-ETAG', 'sha256': 'PRIVATE-SHA'}
    job = {'task_id': TASK_ID, 'kind': 'render', 'state': 'FAILURE', 'stage': 'failed',
           'failure_stage': 'final_visual_qc_rescue', 'error': 'Visual review rejected',
           'spec': {'topic': 'Banknot <script>unsafe()</script>', 'mode': 'production',
                    'format': 'shorts', 'duration_minutes': 0.5},
           'result': {}, 'qa_workprint': pointer}
    get_job = Mock(return_value=job)
    namespace = {'settings': SimpleNamespace(factory_api_token=COOKIE, bucket='private-bucket'),
                 'get_job': get_job, '_stored_get_job': get_job, 'get_upload_record': Mock(return_value=None),
                 'youtube_router': APIRouter()}
    from app.services import studio_operations
    monkeypatch.setattr(studio_operations, 'held_task_ids', lambda rows: set())
    exec(compile(tree, str(source), 'exec'), namespace)
    namespace['_sync_job'] = Mock(return_value=job)
    access = ModuleType('app.services.qa_workprint_access')

    def validate(record, task_id=None):
        if (not isinstance(record, dict) or record.get('state') != 'FAILURE'
                or record.get('kind') != 'render' or record.get('task_id') != TASK_ID
                or (task_id is not None and task_id != TASK_ID)
                or record.get('qa_workprint') != pointer):
            return None
        return deepcopy(pointer)

    access.validated_pointer = Mock(side_effect=validate)
    access.stream_response = Mock(return_value=Response(
        b'private-video-fixture', media_type='video/mp4',
        headers={'Cache-Control': 'private, no-store'}))
    monkeypatch.setitem(sys.modules, 'app.services.qa_workprint_access', access)
    app = FastAPI()
    app.include_router(namespace['router'])
    return SimpleNamespace(client=TestClient(app), namespace=namespace, job=job,
                           get_job=get_job, access=access, pointer=pointer)


def _owner(viewer):
    viewer.client.cookies.set('youtube_studio_token', COOKIE)
    return viewer.client


@pytest.mark.parametrize('method,path', [
    ('GET', PATH), ('GET', PATH + '/video'), ('HEAD', PATH + '/video'),
    ('GET', '/studio/job/not-a-uuid/qa-workprint'),
    ('GET', PATH + '?studio_token=' + COOKIE),
])
def test_auth_precedes_job_lookup_validation_and_storage(viewer, method, path):
    response = viewer.client.request(method, path, headers={'Authorization': 'Bearer ' + COOKIE})
    assert response.status_code == 401
    viewer.get_job.assert_not_called()
    viewer.access.validated_pointer.assert_not_called()
    viewer.access.stream_response.assert_not_called()


def test_owner_page_contains_only_same_origin_player_and_explicit_unapproved_label(viewer):
    before = deepcopy(viewer.job)
    response = _owner(viewer).get(PATH)
    assert response.status_code == 200
    assert f'src="{PATH}/video"' in response.text
    assert 'Yayınlanamaz' in response.text
    assert 'Onaylı final değildir ve YouTube’a gönderilmez' in response.text
    assert '<video ' in response.text and 'controls playsinline' in response.text
    assert '<script>unsafe()</script>' not in response.text
    for private_value in ('PRIVATE-OBJECT-KEY', 'PRIVATE-ETAG', 'PRIVATE-SHA', COOKIE, 'private-bucket'):
        assert private_value not in response.text
    assert 'download_url' not in response.text
    assert response.headers['cache-control'] == 'private, no-store'
    assert response.headers['referrer-policy'] == 'no-referrer'
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert "media-src 'self'" in response.headers['content-security-policy']
    assert "default-src 'none'" in response.headers['content-security-policy']
    viewer.access.stream_response.assert_not_called()
    assert viewer.job == before


@pytest.mark.parametrize('method', ['GET', 'HEAD'])
def test_each_media_request_requires_auth_and_passes_only_validated_pointer(viewer, method):
    client = _owner(viewer)
    response = client.request(method, PATH + '/video', headers={'Range': 'bytes=4-9'})
    assert response.status_code == 200
    call = viewer.access.stream_response.call_args
    assert call.args[0] == viewer.pointer
    assert call.args[1].method == method
    assert call.args[1].headers['range'] == 'bytes=4-9'
    assert viewer.access.validated_pointer.call_args.args == (viewer.job, TASK_ID)
    client.cookies.clear()
    viewer.get_job.reset_mock()
    viewer.access.stream_response.reset_mock()
    assert client.get(PATH + '/video').status_code == 401
    viewer.get_job.assert_not_called()
    viewer.access.stream_response.assert_not_called()


@pytest.mark.parametrize('path', [PATH, PATH + '/video'])
@pytest.mark.parametrize('damage', ['missing', 'other_job', 'active', 'approved', 'publish_job', 'no_pointer'])
def test_only_failed_source_workprint_is_viewable(viewer, path, damage):
    if damage == 'missing': viewer.get_job.return_value = None
    if damage == 'other_job': viewer.job['task_id'] = OTHER_ID
    if damage == 'active': viewer.job['state'] = 'PROGRESS'
    if damage == 'approved': viewer.job['state'] = 'SUCCESS'
    if damage == 'publish_job': viewer.job['kind'] = 'publish'
    if damage == 'no_pointer': viewer.job.pop('qa_workprint')
    response = _owner(viewer).get(path)
    assert response.status_code == 404
    assert response.json() == {'detail': 'İnceleme taslağı bulunamadı'}
    viewer.access.stream_response.assert_not_called()


def test_backend_errors_do_not_expose_private_values(viewer):
    viewer.get_job.side_effect = RuntimeError('SECRET object-key bearer-url provider-password')
    response = _owner(viewer).get(PATH)
    assert response.status_code == 503
    assert response.json() == {'detail': 'İnceleme taslağı şu anda açılamıyor'}
    assert 'SECRET' not in response.text
    viewer.access.stream_response.assert_not_called()


def test_job_page_and_poll_show_draft_but_never_ready_or_uploadable(viewer):
    client = _owner(viewer)
    before = deepcopy(viewer.job)
    page = client.get(f'/studio/job/{TASK_ID}')
    assert page.status_code == 200
    assert f'href="{PATH}"' in page.text
    assert 'İnceleme videosunu aç' in page.text
    assert 'Kalite kontrolünü geçmedi' in page.text
    payload = client.get(f'/studio/api/job/{TASK_ID}').json()
    assert payload['qa_workprint_path'] == PATH
    assert 'qa_workprint' not in payload
    assert payload['state'] == 'FAILURE' and payload['ui_status'] == 'failed'
    assert payload['upload_allowed'] is False
    assert payload['result'] == {}
    assert viewer.job == before
    for private_value in ('PRIVATE-OBJECT-KEY', 'PRIVATE-ETAG', 'PRIVATE-SHA', COOKIE):
        assert private_value not in str(payload) and private_value not in page.text


def test_legacy_job_without_workprint_has_no_draft_action(viewer):
    viewer.job.pop('qa_workprint')
    client = _owner(viewer)
    page = client.get(f'/studio/job/{TASK_ID}')
    assert f'href="{PATH}"' not in page.text
    payload = client.get(f'/studio/api/job/{TASK_ID}').json()
    assert 'qa_workprint_path' not in payload and 'qa_workprint' not in payload
    viewer.access.validated_pointer.assert_not_called()
