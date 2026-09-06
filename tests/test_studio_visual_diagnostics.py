import ast
from copy import deepcopy
import hashlib
import io
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
import pytest


ROOT = Path(__file__).resolve().parents[1]
TASK_ID = '11111111-1111-4111-8111-111111111111'
OTHER_ID = '22222222-2222-4222-8222-222222222222'
COOKIE = 'owner-fixture-cookie'
HTML = b'<!doctype html><html><body><p>Private diagnostic frame</p></body></html>'


class BoundedBody(io.BytesIO):
    def __init__(self, content):
        super().__init__(content)
        self.read_sizes = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)


@pytest.fixture
def viewer(monkeypatch):
    source = ROOT / 'app' / 'studio.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    tree.body = [node for node in tree.body if not (
        isinstance(node, ast.ImportFrom) and (node.module or '').startswith('app.')
    )]
    digest = hashlib.sha256(HTML).hexdigest()
    key = f'diagnostics/visual_allocation/{TASK_ID}/review-{digest}.html'
    job = {
        'task_id': TASK_ID, 'kind': 'render', 'state': 'FAILURE', 'stage': 'failed',
        'failure_stage': 'ai_scene', 'spec': {'topic': 'Banknot kâğıdı', 'mode': 'production'},
        'visual_allocation_checkpoint': {
            'version': 1, 'status': 'diagnostic_only', 'qa_approved': False,
            'reusable_for_render': False, 'html_key': key, 'html_sha256': digest,
        },
    }
    get_job = Mock(return_value=job)
    namespace = {
        'settings': SimpleNamespace(factory_api_token=COOKIE, bucket='existing-private-bucket'),
        'get_job': get_job,
        'get_upload_record': Mock(return_value=None),
        'youtube_router': APIRouter(),
    }
    exec(compile(tree, str(source), 'exec'), namespace)
    body = BoundedBody(HTML)
    response = {'Body': body, 'ContentLength': len(HTML), 'ContentType': 'text/html; charset=utf-8'}
    storage = Mock()
    storage.get_object = Mock(return_value=response)
    storage.generate_presigned_url = Mock(side_effect=AssertionError('Signing must not occur'))
    storage_module = ModuleType('app.services.storage')
    storage_module._client = Mock(return_value=storage)
    storage_module.presigned_download_url = Mock(side_effect=AssertionError('Signing must not occur'))
    monkeypatch.setitem(sys.modules, 'app.services.storage', storage_module)
    app = FastAPI()
    app.include_router(namespace['router'])
    client = TestClient(app)
    return SimpleNamespace(
        namespace=namespace, client=client, job=job, body=body, response=response,
        storage=storage, storage_module=storage_module, get_job=get_job,
        path=f'/studio/job/{TASK_ID}/visual-diagnostics', key=key,
    )


def _authenticated(viewer, path=None):
    viewer.client.cookies.set('youtube_studio_token', COOKIE)
    return viewer.client.get(path or viewer.path, follow_redirects=False)


@pytest.mark.parametrize('path', [
    f'/studio/job/{TASK_ID}/visual-diagnostics',
    '/studio/job/invalid/visual-diagnostics',
    f'/studio/job/{TASK_ID}/visual-diagnostics?studio_token={COOKIE}',
])
def test_anonymous_is_401_before_even_job_lookup_or_storage(viewer, path):
    response = viewer.client.get(path, headers={'Authorization': f'Bearer {COOKIE}'}, follow_redirects=False)
    assert response.status_code == 401
    assert 'Location' not in response.headers
    viewer.get_job.assert_not_called()
    viewer.storage_module._client.assert_not_called()
    viewer.storage.get_object.assert_not_called()
    assert viewer.body.read_sizes == []


def test_each_get_requires_current_cookie_even_after_successful_owner_view(viewer):
    assert _authenticated(viewer).status_code == 200
    viewer.client.cookies.clear()
    viewer.get_job.reset_mock()
    viewer.storage_module._client.reset_mock()
    response = viewer.client.get(viewer.path)
    assert response.status_code == 401
    viewer.get_job.assert_not_called()
    viewer.storage_module._client.assert_not_called()


def test_valid_owner_get_is_exact_private_html_with_strict_headers_and_no_signing(viewer):
    response = _authenticated(viewer)
    assert response.status_code == 200
    assert response.content == HTML
    assert response.headers['content-type'].startswith('text/html')
    assert response.headers['cache-control'] == 'private, no-store'
    assert response.headers['referrer-policy'] == 'no-referrer'
    assert response.headers['x-content-type-options'] == 'nosniff'
    csp = response.headers['content-security-policy']
    for directive in ("sandbox", "default-src 'none'", 'img-src data:', "style-src 'unsafe-inline'",
                      "base-uri 'none'", "form-action 'none'", "frame-ancestors 'none'"):
        assert directive in csp
    assert 'allow-same-origin' not in csp
    assert 'allow-scripts' not in csp
    assert 'location' not in response.headers
    viewer.storage.get_object.assert_called_once_with(Bucket='existing-private-bucket', Key=viewer.key)
    viewer.storage.generate_presigned_url.assert_not_called()
    viewer.storage_module.presigned_download_url.assert_not_called()
    assert viewer.body.read_sizes == [4 * 1024 * 1024]
    assert viewer.body.closed
    assert viewer.key not in response.text
    assert COOKIE not in response.text


@pytest.mark.parametrize('damage', [
    'missing_job', 'different_job', 'noncanonical_task', 'invalid_task',
    'missing_pointer', 'version', 'boolean_version', 'status', 'qa', 'reusable',
    'hash', 'uppercase_hash', 'different_key_job', 'key_traversal', 'key_url', 'key_extension',
])
def test_invalid_pointer_is_safe_404_without_storage(viewer, damage):
    pointer = viewer.job['visual_allocation_checkpoint']
    path = viewer.path
    if damage == 'missing_job':
        viewer.get_job.return_value = None
    elif damage == 'different_job':
        viewer.job['task_id'] = OTHER_ID
    elif damage == 'noncanonical_task':
        path = f'/studio/job/{{{TASK_ID}}}/visual-diagnostics'
    elif damage == 'invalid_task':
        path = '/studio/job/not-a-uuid/visual-diagnostics'
    elif damage == 'missing_pointer':
        viewer.job.pop('visual_allocation_checkpoint')
    elif damage in {'version', 'boolean_version', 'status', 'qa', 'reusable', 'hash', 'uppercase_hash'}:
        key, value = {
            'version': ('version', 2), 'boolean_version': ('version', True),
            'status': ('status', 'unavailable'), 'qa': ('qa_approved', True),
            'reusable': ('reusable_for_render', True), 'hash': ('html_sha256', 'not-a-hash'),
            'uppercase_hash': ('html_sha256', pointer['html_sha256'].upper()),
        }[damage]
        pointer[key] = value
    elif damage == 'different_key_job':
        pointer['html_key'] = pointer['html_key'].replace(TASK_ID, OTHER_ID)
    elif damage == 'key_traversal':
        pointer['html_key'] = '../' + pointer['html_key']
    elif damage == 'key_url':
        pointer['html_key'] = 'https://private.test/?token=SECRET'
    elif damage == 'key_extension':
        pointer['html_key'] = pointer['html_key'].replace('.html', '.mp4')
    response = _authenticated(viewer, path)
    assert response.status_code == 404
    assert response.json() == {'detail': 'Tanı kaydı bulunamadı'}
    assert 'SECRET' not in response.text
    viewer.storage_module._client.assert_not_called()
    assert viewer.body.read_sizes == []


@pytest.mark.parametrize('length', [0, -1, True, '20', 4 * 1024 * 1024 + 1, None])
def test_invalid_or_oversize_content_length_rejected_before_body_read(viewer, length):
    viewer.response['ContentLength'] = length
    response = _authenticated(viewer)
    assert response.status_code == 404
    assert viewer.body.read_sizes == []
    assert viewer.body.closed


@pytest.mark.parametrize('damage', ['hash_mismatch', 'truncated_body', 'oversize_body', 'wrong_content_type'])
def test_tampered_or_non_html_object_is_not_rendered(viewer, damage):
    if damage == 'hash_mismatch':
        viewer.response['Body'] = BoundedBody(HTML.replace(b'Private', b'Tamper!'))
    elif damage == 'truncated_body':
        viewer.response['Body'] = BoundedBody(HTML[:-1])
    elif damage == 'oversize_body':
        viewer.response['Body'] = BoundedBody(b'x' * (4 * 1024 * 1024 + 1))
    elif damage == 'wrong_content_type':
        viewer.response['ContentType'] = 'application/octet-stream'
    response = _authenticated(viewer)
    assert response.status_code == 404
    assert response.json() == {'detail': 'Tanı kaydı bulunamadı'}
    assert viewer.response['Body'].closed
    assert all(0 <= size <= 4 * 1024 * 1024 for size in viewer.response['Body'].read_sizes)


def test_exact_four_mib_object_is_allowed_with_matching_hash(viewer):
    content = HTML + b' ' * (4 * 1024 * 1024 - len(HTML))
    digest = hashlib.sha256(content).hexdigest()
    viewer.job['visual_allocation_checkpoint'].update(
        html_sha256=digest,
        html_key=f'diagnostics/visual_allocation/{TASK_ID}/review-{digest}.html',
    )
    viewer.response.update(Body=BoundedBody(content), ContentLength=len(content))
    response = _authenticated(viewer)
    assert response.status_code == 200
    assert len(response.content) == 4 * 1024 * 1024
    assert viewer.response['Body'].read_sizes == [4 * 1024 * 1024]


@pytest.mark.parametrize('failure', ['job', 'storage', 'read'])
def test_backend_failure_is_generic_503_without_internal_values(viewer, failure):
    error = RuntimeError('SECRET private-bucket endpoint https://private.test/?token=SECRET')
    if failure == 'job':
        viewer.get_job.side_effect = error
    elif failure == 'storage':
        viewer.storage.get_object.side_effect = error
    else:
        viewer.body.read = Mock(side_effect=error)
    response = _authenticated(viewer)
    assert response.status_code == 503
    assert response.json() == {'detail': 'Tanı kaydı şu anda açılamıyor'}
    assert 'SECRET' not in response.text
    assert 'private-bucket' not in response.text
    assert 'Location' not in response.headers
    if failure == 'read':
        assert viewer.body.closed


def test_owner_job_page_and_collapsed_details_show_only_same_origin_link(viewer):
    response = _authenticated(viewer, f'/studio/job/{TASK_ID}')
    assert response.status_code == 200
    expected = f'href="/studio/job/{TASK_ID}/visual-diagnostics"'
    assert expected in response.text
    assert 'Sahne tanısını aç' in response.text
    start = response.text.index('<details class="technical-details">')
    assert start < response.text.index(expected)
    assert response.text.index(expected) < response.text.index('</details>', start)
    assert viewer.key not in response.text
    assert COOKIE not in response.text
    details = viewer.namespace['_job_details'](viewer.job)
    assert expected in details
    assert '<summary>Teknik ayrıntılar</summary>' in details
    viewer.storage_module._client.assert_not_called()


def test_invalid_or_absent_pointer_hides_link_without_adding_polled_url(viewer):
    original = deepcopy(viewer.job)
    viewer.job['visual_allocation_checkpoint']['qa_approved'] = True
    response = _authenticated(viewer, f'/studio/job/{TASK_ID}')
    assert 'Sahne tanısını aç' not in response.text
    assert '/visual-diagnostics' not in response.text
    assert viewer.namespace['_visual_diagnostics_link'](viewer.job) == ''
    viewer.job.clear()
    viewer.job.update(original)
    viewer.job.pop('visual_allocation_checkpoint')
    assert viewer.namespace['_visual_diagnostics_link'](viewer.job) == ''
    viewer.storage_module._client.assert_not_called()


def test_job_page_itself_requires_owner_cookie(viewer):
    response = viewer.client.get(f'/studio/job/{TASK_ID}', follow_redirects=False)
    assert response.status_code == 401
    assert 'Sahne tanısını aç' not in response.text
    viewer.get_job.assert_not_called()
