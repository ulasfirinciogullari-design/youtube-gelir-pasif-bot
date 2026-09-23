"""Actual worker routing and durable Fal receipts; all HTTP stays offline."""
import json
import sys

import httpx
import pytest

from app.services import commissioning_fal_video as fal_setup, fal_video, fal_video_catalog as catalog
from app.services import commissioning_video as video, production_spend_runtime as runtime
from app.services.production_spend import LEDGER_KEY, SpendBlocked
from test_commissioning_video import scene, generate, ROOT, CHILD, KEY
from test_commissioning_audio import box
from test_fal_video_routing import reviewed_date

REQUEST_ID = '123e4567-e89b-12d3-a456-426614174000'
MEDIA = 'https://v3.fal.media/files/test/clip.mp4'
QUEUE = catalog.ORIGIN + '/' + catalog.MODELS['veo_lite'] + '/requests/' + REQUEST_ID
CREATED = {'request_id': REQUEST_ID, 'status_url': QUEUE + '/status', 'response_url': QUEUE}


@pytest.fixture
def fal_scene(scene, monkeypatch, reviewed_date):
    # Legacy tests replace sys.modules['app.config'] during collection. Bind
    # dynamic imports as well as the real config module patched by scene.
    monkeypatch.setattr(sys.modules['app.config'], 'settings', scene.config)
    scene.config.studio_video_provider = 'fal'
    scene.config.studio_fal_video_model = 'auto'
    scene.config.fal_key = KEY
    monkeypatch.setattr(fal_setup, 'settings', scene.config)
    monkeypatch.setattr(fal_video, 'settings', scene.config)
    monkeypatch.setattr(fal_video, '_FAL_POLL_INTERVAL_SECONDS', 0)
    def handler(request):
        scene.requests.append(request)
        rows = json.loads(scene.client.get(video.PREFIX + ROOT))['requests']
        assert rows
        assert all(row['request']['max_list_cost_micro_usd'] == 180000 for row in rows.values())
        if request.method == 'POST':
            assert request.headers['Authorization'] == 'Key ' + KEY
            assert json.loads(request.content)['generate_audio'] is False
            assert json.loads(request.content)['auto_fix'] is False
            return httpx.Response(200, json=CREATED)
        if request.url.path.endswith('/status'):
            return httpx.Response(200, json={'status': 'COMPLETED', 'request_id': REQUEST_ID})
        return httpx.Response(200, json={'video': {'url': MEDIA}})
    scene.handler = handler
    return scene


def test_worker_reuses_completed_fal_receipt_across_child_jobs(fal_scene):
    scene = fal_scene
    runtime.resolve_context(scene.client, ROOT)
    runtime.resolve_context(scene.client, CHILD)
    before = scene.client.hgetall(LEDGER_KEY)
    assert generate(scene)['provider'] == 'fal_veo_lite'
    assert generate(scene)['url'] == MEDIA
    token = runtime._TASK_ID.set(CHILD)
    try:
        assert generate(scene)['url'] == MEDIA
    finally:
        runtime._TASK_ID.reset(token)
    assert [r.method for r in scene.requests] == ['POST', 'GET', 'GET']
    assert scene.client.hgetall(LEDGER_KEY) == before
    journal = scene.client.get(video.PREFIX + ROOT)
    assert KEY not in journal and MEDIA not in journal
    assert scene.client.pttl(video.PREFIX + ROOT) == -1


def test_unknown_post_and_model_switch_cannot_resubmit(fal_scene):
    scene = fal_scene
    def timeout(request):
        scene.requests.append(request)
        raise httpx.ReadTimeout('Unknown outcome')
    scene.handler = timeout
    with pytest.raises(fal_video.FalVideoTransientError):
        generate(scene)
    with pytest.raises(SpendBlocked, match='previous_outcome_unknown'):
        generate(scene)
    scene.config.studio_fal_video_model = 'seedance_pro'
    with pytest.raises(SpendBlocked, match='existing_route_pinned'):
        generate(scene)
    scene.config.studio_video_provider = 'legacy'
    with pytest.raises(SpendBlocked, match='existing_route_pinned'):
        generate(scene)
    assert len(scene.requests) == 1


def test_accepted_queue_resumes_get_after_polling_outage(fal_scene, monkeypatch):
    scene = fal_scene
    original = scene.handler
    monkeypatch.setattr(fal_video, '_FAL_MAX_STATUS_READ_FAILURES', 1)
    def unavailable(request):
        if request.method == 'GET':
            scene.requests.append(request)
            return httpx.Response(503, json={'error': 'Unavailable'})
        return original(request)
    scene.handler = unavailable
    with pytest.raises(fal_video.FalVideoTransientError):
        generate(scene)
    scene.handler = original
    assert generate(scene)['url'] == MEDIA
    assert [r.method for r in scene.requests].count('POST') == 1


def test_terminal_failure_is_recorded_without_rebuying(fal_scene):
    scene = fal_scene
    original = scene.handler
    def failed(request):
        if request.method == 'GET':
            scene.requests.append(request)
            return httpx.Response(200, json={'status': 'COMPLETED', 'request_id': REQUEST_ID,
                                           'error': {'type': 'internal_error'}})
        return original(request)
    scene.handler = failed
    for _ in range(2):
        with pytest.raises(fal_video.FalVideoTransientError):
            generate(scene)
    assert [r.method for r in scene.requests] == ['POST', 'GET']
    row = next(iter(json.loads(scene.client.get(video.PREFIX + ROOT))['requests'].values()))
    assert row['result'] is not None


def test_fal_never_replaces_an_older_unknown_veo_request(fal_scene):
    scene = fal_scene
    scene.config.studio_video_provider = 'legacy'
    def timeout(request):
        scene.requests.append(request)
        raise httpx.ReadTimeout('Unknown outcome')
    scene.handler = timeout
    with pytest.raises(SpendBlocked, match='outcome_unverified'):
        generate(scene)
    scene.config.studio_video_provider = 'fal'
    with pytest.raises(SpendBlocked, match='existing_route_pinned'):
        generate(scene)
    assert len(scene.requests) == 1


def test_missing_key_never_reserves_or_sends(fal_scene):
    fal_scene.config.fal_key = ''
    with pytest.raises(SpendBlocked, match='fal_key_missing'):
        generate(fal_scene)
    assert fal_scene.requests == []
    assert not fal_scene.client.exists(video.PREFIX + ROOT)


def test_fal_uses_existing_episode_capacity(fal_scene):
    fal_scene.config.studio_production_short_paid_create_cap = 2
    assert generate(fal_scene, 0)['url'] == MEDIA
    assert generate(fal_scene, 1)['url'] == MEDIA
    with pytest.raises(SpendBlocked, match='episode_capacity'):
        generate(fal_scene, 2)
    assert [r.method for r in fal_scene.requests].count('POST') == 2
