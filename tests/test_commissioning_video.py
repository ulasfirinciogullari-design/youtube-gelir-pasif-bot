"""Real scene admission, persistent creates, and HTTP transport without billing."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from threading import Event
from unittest.mock import Mock

import httpx
import pytest

from app.services import commissioning_video as video, production_continuation as continuation
from app.services import production_spend_runtime as runtime, runway
from app.services.production_spend import SpendBlocked, LEDGER_KEY
from test_commissioning_audio import box
from test_whisper_transcription import NOW, ROOT, CHILD, CHANNEL, KEY

OPERATION = 'models/' + video.MODEL + '/operations/accepted_001'
URI = 'https://generativelanguage.googleapis.com/v1beta/files/movie:download?alt=media'
COMPLETE = {'done': True, 'response': {'generateVideoResponse': {
    'generatedSamples': [{'video': {'uri': URI}}]}}}


@pytest.fixture
def scene(box, monkeypatch):
    from app import config
    box.config.studio_commissioning_video_generation = True
    box.config.studio_production_short_paid_create_cap = 6
    box.config.gemini_api_key = KEY
    monkeypatch.setattr(video, 'settings', box.config)
    monkeypatch.setattr(runway, 'settings', box.config)
    monkeypatch.setattr(config, 'settings', box.config)
    continuation.initialize(box.client, {'version': 1, 'kind': 'continuous_commissioning',
        'allowed_channels': [CHANNEL], 'authorized_at': NOW.isoformat(), 'owner_evidence_sha256': 'c' * 64})
    box.prepared = runtime.prepare_video_scene_budget('b' * 64, [4.5] * 6, '9:16')
    box.requests = []
    def handler(request):
        box.requests.append(request)
        rows = list(json.loads(box.client.get(video.PREFIX + ROOT))['requests'].values())
        assert rows and all(row['request']['max_list_cost_micro_usd'] == 300000 for row in rows)
        if request.method == 'POST':
            assert any(row['create'] is None for row in rows)
            assert json.loads(request.content)['parameters'] == {
                'durationSeconds': 6, 'aspectRatio': '9:16', 'resolution': '720p'}
            return httpx.Response(200, json={'name': OPERATION})
        return httpx.Response(200, json=COMPLETE)
    box.handler = handler
    client = httpx.Client
    def bounded_client(**kwargs):
        assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
        return client(**kwargs, transport=httpx.MockTransport(lambda request: box.handler(request)))
    monkeypatch.setattr(video.httpx, 'Client', bounded_client)
    return box


def generate(scene, index=0, prompt='A customer assembles a flat pack table.'):
    with runtime.spending_scene(scene.prepared, index):
        return runway.generate_scene(prompt, duration=5, aspect_ratio='9:16')


def test_real_worker_scene_uses_veo_and_replays_receipt_without_more_http(scene):
    runtime.resolve_context(scene.client, ROOT)
    runtime.resolve_context(scene.client, CHILD)
    before = scene.client.hgetall(LEDGER_KEY)
    initialize = Mock(side_effect=AssertionError('do not alter the historical cash plan'))
    scene.foundation.initialize_scene_plan = initialize
    assert generate(scene)['url'] == URI
    assert generate(scene)['provider'] == 'gemini_veo'
    token = runtime._TASK_ID.set(CHILD)
    try: assert generate(scene)['url'] == URI
    finally: runtime._TASK_ID.reset(token)
    assert [r.method for r in scene.requests] == ['POST', 'GET']
    assert scene.client.hgetall(LEDGER_KEY) == before
    row = scene.client.get(video.PREFIX + ROOT)
    assert KEY not in row and URI not in row
    assert scene.client.pttl(video.PREFIX + ROOT) == -1
    assert video._SCENE.get() is None
    initialize.assert_not_called()


@pytest.mark.parametrize('case', ['flag_off', 'deactivated', 'wrong_connection', 'requires_reconnect'])
def test_unadmitted_production_never_sends(scene, case):
    if case == 'flag_off': scene.config.studio_commissioning_video_generation = False
    if case == 'deactivated': scene.client.delete(continuation.ACTIVE_KEY)
    if case in {'wrong_connection', 'requires_reconnect'}:
        channel = json.loads(scene.client.get(runtime._CHANNEL_PREFIX + CHANNEL))
        channel['connection_id' if case == 'wrong_connection' else 'requires_reconnect'] = (
            'different_connection' if case == 'wrong_connection' else True)
        scene.client.set(runtime._CHANNEL_PREFIX + CHANNEL, json.dumps(channel))
    with pytest.raises(SpendBlocked): generate(scene)
    assert scene.requests == [] and not scene.client.exists(video.PREFIX + ROOT)


def test_ambiguous_create_remains_reserved_and_is_never_repeated(scene):
    def unknown(request):
        scene.requests.append(request)
        raise httpx.ReadTimeout('unknown acceptance')
    scene.handler = unknown
    with pytest.raises(SpendBlocked, match='outcome_unverified'): generate(scene)
    before = scene.client.get(video.PREFIX + ROOT)
    with pytest.raises(SpendBlocked, match='previous_outcome_unknown'): generate(scene)
    assert scene.client.get(video.PREFIX + ROOT) == before
    assert len(scene.requests) == 1


def test_poll_failure_resumes_get_without_repeating_create(scene):
    original = scene.handler
    def unavailable(request):
        if request.method == 'GET':
            scene.requests.append(request)
            raise httpx.ReadTimeout('temporary poll outage')
        return original(request)
    scene.handler = unavailable
    with pytest.raises(SpendBlocked, match='outcome_unverified'): generate(scene)
    scene.handler = original
    assert generate(scene)['url'] == URI
    assert [r.method for r in scene.requests] == ['POST', 'GET', 'GET']


@pytest.mark.parametrize('case', ['rate_limit', 'forbidden', 'redirect', 'bad_operation', 'terminal', 'foreign_uri'])
def test_observed_rejections_and_untrusted_outputs_never_approve_or_submit_twice(scene, case):
    def handler(request):
        scene.requests.append(request)
        if request.method == 'POST':
            code = {'rate_limit': 429, 'forbidden': 403, 'redirect': 302}.get(case, 200)
            return httpx.Response(code, json={'name': 'https://unrelated.invalid/op' if case == 'bad_operation' else OPERATION},
                                  headers={'location': 'https://unrelated.invalid/'})
        payload = deepcopy(COMPLETE)
        if case == 'terminal': payload['error'] = {'code': 400}
        if case == 'foreign_uri': payload['response']['generateVideoResponse']['generatedSamples'][0]['video']['uri'] = 'https://unrelated.invalid/movie'
        return httpx.Response(200, json=payload)
    scene.handler = handler
    for _ in range(2):
        with pytest.raises(SpendBlocked): generate(scene)
    assert sum(r.method == 'POST' for r in scene.requests) == 1


def test_unsupported_length_and_wrong_aspect_do_not_submit(scene):
    for seconds, ratio in ((7, '9:16'), (5, '16:9'), (10, '9:16')):
        with runtime.spending_scene(scene.prepared, 0), pytest.raises(SpendBlocked):
            video.generate_if_commissioned('Same scene', seconds, ratio)
    assert scene.requests == []


def test_concurrent_scene_requests_create_once(scene):
    entered, finish = Event(), Event()
    original = scene.handler
    def handler(request):
        if request.method == 'POST':
            entered.set(); assert finish.wait(5)
        return original(request)
    scene.handler = handler
    def worker():
        token = runtime._TASK_ID.set(ROOT)
        try: return generate(scene)
        finally: runtime._TASK_ID.reset(token)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(worker); assert entered.wait(5)
        try:
            with pytest.raises(SpendBlocked, match='previous_outcome_unknown'):
                pool.submit(worker).result(timeout=5)
        finally: finish.set()
        assert first.result()['url'] == URI
    assert sum(r.method == 'POST' for r in scene.requests) == 1


def test_six_scene_capacity_counts_every_request_and_never_becomes_a_monthly_budget(scene):
    for index in range(6): assert generate(scene, index)['url'] == URI
    before = scene.client.get(video.PREFIX + ROOT)
    with pytest.raises(SpendBlocked, match='episode_capacity'):
        generate(scene, 0, 'A different repair prompt.')
    assert scene.client.get(video.PREFIX + ROOT) == before
    assert sum(r.method == 'POST' for r in scene.requests) == 6


@pytest.mark.parametrize('phase', ['reservation', 'observation'])
def test_definite_transaction_conflict_retries_locally_without_second_post(scene, monkeypatch, phase):
    from redis.client import Pipeline
    from redis.exceptions import WatchError
    execute = Pipeline.execute
    conflicts = []
    def conflict(pipe, *args, **kwargs):
        for command, _ in pipe.command_stack:
            if command[0] == 'SET' and command[1] == video.PREFIX + ROOT:
                row = next(iter(json.loads(command[2])['requests'].values()))
                selected = row['create'] is None if phase == 'reservation' else row['create'] is not None
                if selected and not conflicts:
                    conflicts.append(phase)
                    raise WatchError('Watched variable changed.')
        return execute(pipe, *args, **kwargs)
    monkeypatch.setattr(Pipeline, 'execute', conflict)
    assert generate(scene)['url'] == URI
    assert conflicts == [phase] and sum(r.method == 'POST' for r in scene.requests) == 1


def test_lost_reservation_ack_never_repeats_or_sends(scene, monkeypatch):
    from redis.client import Pipeline
    from redis.exceptions import ConnectionError
    execute = Pipeline.execute
    lost = []
    def uncertain(pipe, *args, **kwargs):
        target = any(command[0] == 'SET' and command[1] == video.PREFIX + ROOT
                     for command, _ in pipe.command_stack)
        result = execute(pipe, *args, **kwargs)
        if target and not lost:
            lost.append(True)
            raise ConnectionError('lost after commit')
        return result
    monkeypatch.setattr(Pipeline, 'execute', uncertain)
    with pytest.raises(ConnectionError): generate(scene)
    before = scene.client.get(video.PREFIX + ROOT)
    with pytest.raises(SpendBlocked, match='previous_outcome_unknown'): generate(scene)
    assert scene.requests == [] and scene.client.get(video.PREFIX + ROOT) == before


@pytest.mark.parametrize('stage', ['ai_scene_generation', 'final_visual_qc'])
def test_generation_outage_holds_only_unpublished_episode(stage):
    from app.services.production_failures import classify_failure, classified_hold_reason
    for code in ('commissioning_video_provider_rejected', 'commissioning_video_outcome_unverified',
                 'commissioning_video_poll_timeout', 'commissioning_video_generation_failed'):
        error = SpendBlocked(code)
        record = {'error': code, 'failure_stage': stage,
                  'failure_classification': classify_failure(error, stage)}
        assert classified_hold_reason(record) == 'review_unverified'
        record['failure_stage'] = 'upload'
        record['failure_classification'] = classify_failure(error, 'upload')
        assert classified_hold_reason(record) is None
