"""Real dispatch adapters with fake transports/Redis; zero provider traffic."""
import ast
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import fakeredis
import pytest

from spending_test_support import initialize_test_funding, initialize_test_scene

from app.services.production_spend import LEDGER_KEY, SpendBlocked, SpendLedger, SpendPolicy
from app.services import production_spend_runtime as runtime
from app.services import production_spend_quotes as quotes

ROOT = '11111111-1111-4111-8111-111111111111'
CHILD = '22222222-2222-4222-8222-222222222222'
CHANNEL = 'UC5v9AvNtD3PTLgo6m1jROOA'
OTHER_CHANNEL = 'UCgvESYtYbn2w9R2ExBOF_cw'
URL = 'https://generativelanguage.googleapis.com/v1beta/models/veo-3.1-lite-generate-preview:predictLongRunning'


def video(prompt='a real moving scene'):
    return {'instances': [{'prompt': prompt}], 'parameters': {
        'aspectRatio': '9:16', 'resolution': '720p', 'durationSeconds': 6}}


def job(client, task_id=ROOT, parent_id=None, *, channel=CHANNEL, duration=0.5):
    client.set(runtime._JOB_PREFIX + task_id, json.dumps({
        'task_id': task_id, 'parent_id': parent_id, 'kind': 'render',
        'spec': {'production_channel_id': channel, 'production_connection_id': 'connection_AAAAA',
                 'duration_minutes': duration}}))


@pytest.fixture
def case(monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=True)
    policy = SpendPolicy(10_000_000, 4_000_000, 8_000_000, 1_000_000, 8_000_000, 500_000)
    ledger = SpendLedger(client, policy, clock=lambda: datetime(2026, 9, 8, tzinfo=timezone.utc))
    ledger.initialize()
    monkeypatch.setattr(runtime, 'settings', SimpleNamespace(studio_spend_enforcement=True))
    monkeypatch.setattr(runtime, 'configured_ledger', lambda: ledger)
    monkeypatch.setattr(quotes, '_fresh', lambda: None)
    initialize_test_funding(ledger)
    client.sadd(runtime._CHANNEL_INDEX, CHANNEL)
    client.set(runtime._CHANNEL_PREFIX + CHANNEL, json.dumps({
        'id': CHANNEL, 'connection_id': 'connection_AAAAA'}))
    job(client)
    token = runtime._TASK_ID.set(ROOT)
    scene_token = initialize_test_scene(ledger, channel=CHANNEL, root=ROOT)
    yield client, ledger
    runtime._TASK_ID.reset(token)
    runtime._SCENE.reset(scene_token)


def test_real_adapter_reserves_before_post_and_blocks_duplicate(case):
    client, ledger = case
    def post(url, **kwargs):
        assert ledger.snapshot()['period']['used_micro'] == 300_000
        return 'accepted'
    sender = Mock(side_effect=post)
    assert runtime.paid_post(sender, URL, json=video(), headers={'x-goog-api-key': 'private-test-key'}) == 'accepted'
    with pytest.raises(SpendBlocked, match='already_reserved'):
        runtime.paid_post(sender, URL, json=video(), headers={'x-goog-api-key': 'private-test-key'})
    assert sender.call_count == 1
    # Private prompts, keys and media never enter the persisted ledger.
    assert 'a real moving scene' not in json.dumps(client.hgetall(LEDGER_KEY))


def test_provider_timeout_is_not_refunded_or_replayed(case):
    _, ledger = case
    sender = Mock(side_effect=TimeoutError('unknown provider outcome'))
    with pytest.raises(TimeoutError):
        runtime.paid_post(sender, URL, json=video(), headers={'x-goog-api-key': 'private-test-key'})
    with pytest.raises(SpendBlocked, match='already_reserved'):
        runtime.paid_post(sender, URL, json=video(), headers={'x-goog-api-key': 'private-test-key'})
    assert sender.call_count == 1
    assert ledger.snapshot()['period']['used_micro'] == 300_000


def test_new_retry_task_cannot_repeat_identical_create(case):
    client, _ = case
    sender = Mock()
    runtime.paid_post(sender, URL, json=video(), headers={'x-goog-api-key': 'private-test-key'})
    job(client, CHILD, ROOT)
    token = runtime._TASK_ID.set(CHILD)
    try:
        with pytest.raises(SpendBlocked, match='already_reserved'):
            runtime.paid_post(sender, URL, json=video(), headers={'x-goog-api-key': 'private-test-key'})
    finally:
        runtime._TASK_ID.reset(token)
    assert sender.call_count == 1


def test_different_repairs_still_share_original_allowance(case):
    client, ledger = case
    sender = Mock()
    for i in range(3):
        runtime.paid_post(sender, URL, json=video(f'scene {i}'), headers={'x-goog-api-key': 'private-test-key'})
    job(client, CHILD, ROOT)
    token = runtime._TASK_ID.set(CHILD)
    try:
        with pytest.raises(SpendBlocked, match='lineage_limit'):
            runtime.paid_post(sender, URL, json=video('repaired scene'), headers={'x-goog-api-key': 'private-test-key'})
    finally:
        runtime._TASK_ID.reset(token)
    assert ledger.snapshot()['period']['used_micro'] == 900_000
    assert sender.call_count == 3


def test_more_expensive_runway_fallback_uses_actual_quoted_cost(case):
    _, ledger = case
    client = Mock(api_key='private-test-key', base_url='https://api.dev.runwayml.com/')
    client.with_options.return_value = client
    runtime.paid_runway_create(client, model='gen4.5', prompt_text='scene',
                               ratio='720:1280', duration=5)
    with pytest.raises(SpendBlocked, match='lineage_limit'):
        runtime.paid_runway_create(client, model='seedance2_fast', prompt_text='scene',
                                   ratio='720:1280', duration=5, audio=False)
    assert ledger.snapshot()['period']['used_micro'] == 600_000
    assert client.text_to_video.create.call_count == 1
    client.with_options.assert_called_once_with(max_retries=0)


@pytest.mark.parametrize('url,kwargs', [
    ('https://queue.fal.run/fal-ai/unknown', {'json': {'prompt': 'test'}}),
    ('https://generativelanguage.googleapis.com/v1beta/interactions', {'json': {'model': 'gemini-omni-1.1-flash'}}),
    ('https://api.elevenlabs.io/v1/music', {'json': {'model_id': 'music_v2'}}),
    ('https://api.openai.com/v1/audio/transcriptions', {'files': {'file': Mock()}}),
    (URL + '?key=not-a-real-key', {'json': video()}),
])
def test_unknown_prices_and_media_shapes_never_reach_transport(case, url, kwargs):
    sender = Mock()
    with pytest.raises(SpendBlocked):
        runtime.paid_post(sender, url, **kwargs)
    sender.assert_not_called()


def test_missing_context_blocks_before_transport(case):
    sender = Mock()
    token = runtime._TASK_ID.set(None)
    try:
        with pytest.raises(SpendBlocked, match='context_missing'):
            runtime.paid_post(sender, URL, json=video(), headers={'x-goog-api-key': 'private-test-key'})
    finally:
        runtime._TASK_ID.reset(token)
    sender.assert_not_called()


@pytest.mark.parametrize('damage', ['missing_parent', 'cycle', 'channel', 'connection', 'kind_change'])
def test_damaged_lineage_or_changed_identity_blocks(case, damage):
    client, _ = case
    runtime.resolve_context(client, ROOT)
    if damage == 'missing_parent':
        job(client, ROOT, CHILD)
    elif damage == 'cycle':
        job(client, ROOT, ROOT)
    elif damage == 'channel':
        job(client, CHILD, ROOT, channel=OTHER_CHANNEL)
    elif damage == 'connection':
        client.set(runtime._CHANNEL_PREFIX + CHANNEL, json.dumps({'id': CHANNEL, 'connection_id': 'new_connection'}))
    else:
        job(client, ROOT, duration=8)
    target = CHILD if damage == 'channel' else ROOT
    with pytest.raises(SpendBlocked):
        runtime.resolve_context(client, target)


def test_long_form_family_keeps_long_allowance_for_derived_short(case):
    client, _ = case
    job(client, ROOT, duration=8)
    job(client, CHILD, ROOT, duration=0.5)
    bound = runtime.resolve_context(client, CHILD)
    assert bound['lineage_id'] == ROOT
    assert bound['kind'] == 'long'


def test_missing_ledger_is_never_bootstrapped(case):
    client, _ = case
    client.delete(LEDGER_KEY)
    sender = Mock()
    with pytest.raises(SpendBlocked, match='not_initialized'):
        runtime.paid_post(sender, URL, json=video(), headers={'x-goog-api-key': 'private-test-key'})
    sender.assert_not_called()
    assert not client.exists(LEDGER_KEY)


def test_zero_allowance_prevents_any_paid_request(case, monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=True)
    policy = SpendPolicy(0, 0, 0, 0, 0, 0)
    ledger = SpendLedger(client, policy)
    ledger.initialize()
    monkeypatch.setattr(runtime, 'configured_ledger', lambda: ledger)
    job(client)
    client.sadd(runtime._CHANNEL_INDEX, CHANNEL)
    client.set(runtime._CHANNEL_PREFIX + CHANNEL, json.dumps({'id': CHANNEL, 'connection_id': 'connection_AAAAA'}))
    sender = Mock()
    with pytest.raises(SpendBlocked, match='month_limit'):
        runtime.paid_post(sender, URL, json=video(), headers={'x-goog-api-key': 'private-test-key'})
    sender.assert_not_called()


def test_parallel_requests_inherit_context_and_cannot_overspend(case):
    _, ledger = case
    sender = Mock()
    def call(i):
        try:
            runtime.paid_post(sender, URL, json=video(f'parallel {i}'), headers={'x-goog-api-key': 'private-test-key'})
            return True
        except SpendBlocked:
            return False
    with runtime.SpendingThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(call, range(8)))
    assert sum(results) == 3
    assert sender.call_count == 3
    assert ledger.snapshot()['period']['used_micro'] == 900_000


def test_task_scope_is_reset_even_after_error(case):
    @runtime.spending_task
    def task(self):
        assert runtime._TASK_ID.get() == CHILD
        raise ValueError('local failure')
    with pytest.raises(ValueError):
        task(SimpleNamespace(request=SimpleNamespace(id=CHILD)))
    assert runtime._TASK_ID.get() == ROOT


def test_openai_text_request_caps_output_and_disables_sdk_retries(case):
    client = Mock(api_key='private-test-key', base_url='https://api.openai.com/v1/')
    client.with_options.return_value = client
    runtime.paid_response(client, model='gpt-6-astra', input='a short script')
    client.with_options.assert_called_once_with(max_retries=0)
    assert client.responses.create.call_args.kwargs['max_output_tokens'] == 8192
    assert client.responses.create.call_args.kwargs['store'] is False


@pytest.mark.parametrize('change', [
    {'tools': [{'type': 'web_search'}]}, {'previous_response_id': 'resp_test'},
    {'input': [{'type': 'input_image', 'image_url': 'https://example.com/test.png'}]},
    {'service_tier': 'priority'}, {'model': 'unknown'}, {'max_output_tokens': 999999},
])
def test_unbounded_openai_variants_block_before_sdk(case, change):
    client = Mock(api_key='private-test-key', base_url='https://api.openai.com/v1/')
    kwargs = {'model': 'gpt-6-astra', 'input': 'script', 'store': False, **change}
    with pytest.raises(SpendBlocked):
        runtime.paid_response(client, **kwargs)
    client.responses.create.assert_not_called()


def test_flag_off_retains_existing_provider_contract_without_redis(monkeypatch):
    monkeypatch.setattr(runtime, 'settings', SimpleNamespace(studio_spend_enforcement=False))
    monkeypatch.setattr(runtime, 'configured_ledger', Mock(side_effect=AssertionError))
    sender = Mock(return_value='legacy')
    assert runtime.paid_post(sender, URL, json=video(), headers={'x-goog-api-key': 'private-test-key'}) == 'legacy'
    client = Mock()
    runtime.paid_response(client, model='legacy', input='unchanged')
    client.responses.create.assert_called_once_with(model='legacy', input='unchanged')
    client.with_options.assert_not_called()
    assert runtime.budget_status() == {'enforced': False, 'status': 'not_enabled'}


@pytest.mark.parametrize('model,rate', [('gen4.5', 120_000), ('seedance2_fast', 290_000)])
def test_runway_price_units_are_microdollars_not_credits(monkeypatch, model, rate):
    monkeypatch.setattr(quotes, '_fresh', lambda: None)
    result = quotes.quote_runway_video({'model': model, 'duration': 5, 'ratio': '720:1280', 'prompt_text': 'test'})
    assert result.maximum_micro == 5 * rate


def test_catalog_expires_instead_of_silently_reusing_stale_price(monkeypatch):
    class Clock:
        @staticmethod
        def now(_):
            return datetime(2026, 10, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(quotes, 'datetime', Clock)
    with pytest.raises(SpendBlocked, match='expired'):
        quotes.quote_runway_video({'model': 'gen4.5', 'duration': 5, 'ratio': '720:1280', 'prompt_text': 'test'})


def test_all_current_provider_create_sites_use_the_guard():
    services = Path(__file__).parents[1] / 'app' / 'services'
    names = ('director', 'research', 'visual_qc', 'runway', 'fal_video', 'gemini_generation',
             'gemini_critic', 'voice', 'audio_design', 'audio_qc', 'production_next_series')
    free_post_functions = {'ensure_shared_voice_added', '_upload_gemini_audio_file'}
    unguarded = []
    for name in names:
        source = ast.parse((services / (name + '.py')).read_text(encoding='utf-8'))
        for function in ast.walk(source):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(function):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                attr = node.func
                sdk_create = attr.attr == 'create' and isinstance(attr.value, ast.Attribute) and attr.value.attr in {'responses', 'text_to_video'}
                direct_post = attr.attr == 'post' and function.name not in free_post_functions
                if sdk_create or direct_post:
                    unguarded.append((name, function.name, node.lineno))
    assert not unguarded, unguarded


@pytest.mark.parametrize('kwargs', [
    {'params': {'priority': True}}, {'follow_redirects': True}, {'data': '{}'},
])
def test_unpriced_transport_options_block(case, kwargs):
    sender = Mock()
    with pytest.raises(SpendBlocked):
        runtime.paid_post(sender, URL, json=video(), **kwargs)
    sender.assert_not_called()


@pytest.mark.parametrize('adapter,kwargs', [
    (runtime.paid_response, {'model': 'gpt-6-astra', 'input': 'script', 'store': False}),
    (runtime.paid_runway_create, {'model': 'gen4.5', 'prompt_text': 'scene',
                                'ratio': '720:1280', 'duration': 5}),
])
def test_sdk_proxy_cannot_use_official_endpoint_quote(case, adapter, kwargs):
    _, ledger = case
    client = Mock(api_key='private-test-key', base_url='https://example.com/other-billing/')
    with pytest.raises(SpendBlocked, match='endpoint_not_priced'):
        adapter(client, **kwargs)
    client.with_options.assert_not_called()
    client.responses.create.assert_not_called()
    client.text_to_video.create.assert_not_called()
    assert ledger.snapshot()['period']['used_micro'] == 0


def test_actual_runway_fallback_cannot_outgrow_original_budget(case, monkeypatch):
    from app.services import runway
    class RejectedCapacity(Exception):
        pass
    monkeypatch.setattr(runway, 'RateLimitError', RejectedCapacity)
    client = Mock(api_key='private-test-key', base_url='https://api.dev.runwayml.com/')
    client.with_options.return_value = client
    client.text_to_video.create.side_effect = RejectedCapacity()
    with pytest.raises(SpendBlocked, match='lineage_limit'):
        runway._create_text_to_video_task(client, 'scene', 5, '9:16')
    # The 29-credit/s fallback is refused before a second actual SDK create.
    assert client.text_to_video.create.call_count == 1
    assert case[1].snapshot()['period']['used_micro'] == 600_000


def test_actual_gemini_json_preserves_budget_refusal_without_retry(case, monkeypatch):
    from app.services import gemini_generation
    sender = Mock()
    monkeypatch.setattr(gemini_generation.httpx, 'post', sender)
    with pytest.raises(SpendBlocked, match='request_not_priced'):
        gemini_generation.generate_gemini_json(
            'script', api_key='test-only', model='unreviewed-model', retry_once=True)
    sender.assert_not_called()


def test_owner_budget_endpoint_requires_auth_and_is_read_only(case):
    from fastapi import APIRouter, Cookie, FastAPI, HTTPException
    from fastapi.testclient import TestClient
    route_path = Path(__file__).parents[1] / 'app/youtube_routes.py'
    tree = ast.parse(route_path.read_text(encoding='utf-8'))
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                and node.name == 'youtube_production_budget']
    def auth(token):
        if token != 'owner':
            raise HTTPException(401, 'unauthorized')
    namespace = {'router': APIRouter(), 'Cookie': Cookie,
                 'COOKIE_NAME': 'youtube_studio_token', '_require_auth': auth}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(route_path), 'exec'), namespace)
    app = FastAPI()
    app.include_router(namespace['router'])
    http = TestClient(app)
    before = case[0].hgetall(LEDGER_KEY)
    assert http.get('/studio/youtube/production-budget').status_code == 401
    response = http.get('/studio/youtube/production-budget',
                        headers={'Cookie': 'youtube_studio_token=owner'})
    assert response.status_code == 200
    assert response.json()['accounting'] == 'reserved_upper_bound_not_invoice'
    assert response.headers['cache-control'] == 'no-store'
    assert case[0].hgetall(LEDGER_KEY) == before
    assert http.post('/studio/youtube/production-budget').status_code == 405
