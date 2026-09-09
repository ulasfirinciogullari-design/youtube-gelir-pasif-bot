"""Real scene scopes and worker loops with fake Redis/provider boundaries."""
import ast
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import fakeredis

from app.services import production_spend_runtime as runtime
from app.services.production_spend import LEDGER_KEY, SpendBlocked, SpendLedger, SpendPolicy
from app.services import production_spend_quotes as quotes
from spending_test_support import initialize_test_funding, TEST_KEY
from test_production_spend_runtime import job, ROOT, CHANNEL
from test_production_spend_scene_terminal import (
    SOURCE, TREE, _worker, case as scene_case, production,
)


PACKAGE_HASH = 'a' * 64


@pytest.fixture
def scene_spend(monkeypatch):
    """Independent state: legacy adapter fixtures may install their own plans."""
    client = fakeredis.FakeRedis(decode_responses=True)
    policy = SpendPolicy(20_000_000, 20_000_000, 20_000_000,
                         20_000_000, 20_000_000, 20_000_000)
    ledger = SpendLedger(client, policy, clock=lambda: datetime(2026, 9, 9, 12, tzinfo=timezone.utc))
    ledger.initialize()
    monkeypatch.setattr(runtime, 'settings', SimpleNamespace(studio_spend_enforcement=True))
    monkeypatch.setattr(runtime, 'configured_ledger', lambda: ledger)
    monkeypatch.setattr(quotes, '_fresh', lambda: None)
    client.sadd(runtime._CHANNEL_INDEX, CHANNEL)
    client.set(runtime._CHANNEL_PREFIX + CHANNEL, json.dumps({
        'id': CHANNEL, 'connection_id': 'connection_AAAAA'}))
    job(client)
    token = runtime._TASK_ID.set(ROOT)
    try:
        yield client, ledger
    finally:
        runtime._TASK_ID.reset(token)


@pytest.fixture
def funded_runway(scene_spend):
    """Explicit fictional cash evidence, scoped only to these offline tests."""
    initialize_test_funding(scene_spend[1])
    return scene_spend


def _prepare(durations=None, aspect='9:16'):
    return runtime.prepare_video_scene_budget(
        PACKAGE_HASH, durations if durations is not None else [5.0] * 3, aspect)


def _bind_worker(case, prepared):
    case.ns.update(video_scene_budget=prepared, spending_scene=runtime.spending_scene)


def test_prepare_is_pure_for_retained_reuse_even_when_quote_catalog_is_expired(monkeypatch):
    monkeypatch.setattr(runtime, 'settings', SimpleNamespace(studio_spend_enforcement=True))
    ledger = Mock(side_effect=AssertionError('Pure preparation must not initialize storage'))
    fresh = Mock(side_effect=SpendBlocked('spend_price_expired'))
    monkeypatch.setattr(runtime, 'configured_ledger', ledger)
    monkeypatch.setattr(quotes, '_fresh', fresh)
    durations = [5.0, 5.2, 7.1]
    prepared = _prepare(durations)
    durations[0] = 9.0
    assert prepared.package_sha256 == PACKAGE_HASH
    assert prepared.narration_millis == (5000, 5200, 7100)
    assert prepared.generation_seconds == (6, 6, 8)
    assert prepared.aspect_ratio == '9:16'
    ledger.assert_not_called()
    fresh.assert_not_called()


def test_actual_worker_prepares_every_scene_after_final_audio_measurements(monkeypatch):
    monkeypatch.setattr(runtime, 'settings', SimpleNamespace(studio_spend_enforcement=True))
    pipeline = next(node for node in TREE.body
                    if isinstance(node, ast.FunctionDef) and node.name == 'run_video_pipeline')
    prepared_call = next(node for node in ast.walk(pipeline) if isinstance(node, ast.Assign)
                         and any(isinstance(target, ast.Name) and target.id == 'video_scene_budget'
                                 for target in node.targets))
    measurements = [node for node in ast.walk(pipeline) if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == 'scene_durations'
                            for target in node.targets)]
    assert measurements and all(node.lineno < prepared_call.lineno for node in measurements)
    scenes = [{'ai_prompt': None}, {'ai_prompt': 'moving subject'}, {'ai_prompt': None}]
    package = {'scenes': scenes, 'narration': 'unchanged final narration'}
    identity = Mock(return_value=PACKAGE_HASH)
    namespace = {
        'package': package, 'scenes': scenes, 'scene_durations': [5.0, 6.0, 7.0],
        'selected_runway': [{'scene_index': 1}], 'generation_aspect_ratio': '16:9',
        '_recovery_package_sha256': identity,
        'prepare_video_scene_budget': runtime.prepare_video_scene_budget,
    }
    exec(compile(ast.Module(body=[prepared_call], type_ignores=[]), str(SOURCE), 'exec'), namespace)
    assert namespace['video_scene_budget'].narration_millis == (5000, 6000, 7000)
    assert namespace['video_scene_budget'].aspect_ratio == '16:9'
    identity.assert_called_once_with(package)
    namespace['scene_durations'] = [5.0, 6.0]
    with pytest.raises(SpendBlocked):
        exec(compile(ast.Module(body=[prepared_call], type_ignores=[]), str(SOURCE), 'exec'), namespace)


@pytest.mark.parametrize('seconds', [0.1, 4.70588235, 5.0, 5.6862745, 7.0, 9.0])
def test_frozen_generation_duration_matches_actual_worker_before_millisecond_rounding(monkeypatch, seconds):
    monkeypatch.setattr(runtime, 'settings', SimpleNamespace(studio_spend_enforcement=True))
    helper = next(node for node in TREE.body
                  if isinstance(node, ast.FunctionDef) and node.name == '_runway_generation_seconds')
    namespace = {'math': math}
    exec(compile(ast.Module(body=[helper], type_ignores=[]), str(SOURCE), 'exec'), namespace)
    prepared = _prepare([seconds])
    assert prepared.narration_millis == (math.ceil(seconds * 1000),)
    assert prepared.generation_seconds == (namespace['_runway_generation_seconds'](seconds),)


@pytest.mark.parametrize('durations', [[], [0], [-1], [True], [float('nan')], [float('inf')], ['5'], (5,)])
def test_bad_measured_durations_fail_before_any_plan_or_provider_work(monkeypatch, durations):
    monkeypatch.setattr(runtime, 'settings', SimpleNamespace(studio_spend_enforcement=True))
    ledger = Mock()
    monkeypatch.setattr(runtime, 'configured_ledger', ledger)
    with pytest.raises(SpendBlocked):
        _prepare(durations)
    ledger.assert_not_called()


def test_off_switch_preserves_noop_prepare_and_scope(monkeypatch):
    monkeypatch.setattr(runtime, 'settings', SimpleNamespace(studio_spend_enforcement=False))
    ledger = Mock()
    monkeypatch.setattr(runtime, 'configured_ledger', ledger)
    assert runtime.prepare_video_scene_budget(None, None, None) is None
    with runtime.spending_scene(None, 0):
        assert runtime._SCENE.get() is None
    assert runtime._SCENE.get() is None
    ledger.assert_not_called()


@pytest.mark.parametrize('phase', ['initial_generation', 'final_repair'])
def test_actual_scene_scope_initialization_failure_stops_before_first_create(scene_case, monkeypatch, phase):
    monkeypatch.setattr(runtime, 'settings', SimpleNamespace(studio_spend_enforcement=True))
    monkeypatch.setattr(quotes, '_fresh', lambda: None)
    error = SpendBlocked('spend_scene_plan_late')
    ledger = Mock(side_effect=error)
    monkeypatch.setattr(runtime, 'configured_ledger', ledger)
    _bind_worker(scene_case, _prepare())
    with pytest.raises(SpendBlocked) as caught:
        _worker(scene_case, phase)(scene_case.self)
    assert caught.value is error
    scene_case.provider.assert_not_called()
    scene_case.critic.assert_not_called()
    scene_case.persistence.assert_not_called()
    scene_case.ns['mark_failure'].assert_called_once_with(scene_case.ns['task_id'], error)
    assert runtime._SCENE.get() is None


@pytest.mark.parametrize('phase', ['initial_generation', 'final_repair'])
def test_actual_initial_and_stock_repair_calls_share_frozen_scene_identity(scene_case, scene_spend, phase):
    prepared = _prepare()
    _bind_worker(scene_case, prepared)
    seen = []
    def generate(*_args, **_kwargs):
        seen.append(deepcopy(runtime._SCENE.get()))
        return scene_case.generated
    scene_case.provider.side_effect = generate
    _worker(scene_case, phase)(scene_case.self)
    assert [item['scene_index'] for item in seen] == [0, 1, 2]
    assert all(item['package_sha256'] == PACKAGE_HASH for item in seen)
    assert runtime._SCENE.get() is None
    assert scene_case.persistence.call_count == 3
    # For final_repair all incumbents started as stock in the worker fixture.
    assert set(scene_case.ns['generated_checkpoint_specs']) == {0, 1, 2}


@pytest.mark.parametrize('phase', ['initial_generation', 'final_repair'])
def test_worker_provider_exception_does_not_leak_scene_scope(scene_case, scene_spend, phase):
    _bind_worker(scene_case, _prepare())
    error = SpendBlocked('spend_scene_total_limit')
    seen = []
    def generate(*_args, **_kwargs):
        seen.append(deepcopy(runtime._SCENE.get()))
        raise error
    scene_case.provider.side_effect = generate
    with pytest.raises(SpendBlocked) as caught:
        _worker(scene_case, phase)(scene_case.self)
    assert caught.value is error
    assert [item['scene_index'] for item in seen] == [0]
    assert runtime._SCENE.get() is None
    scene_case.critic.assert_not_called()


def test_long_stock_incumbent_does_not_prevent_another_scenes_paid_plan(scene_case, scene_spend):
    from app.services.production_scene_budget import scene_fields
    durations = [12.0, 5.0, 5.0]
    _bind_worker(scene_case, _prepare(durations))
    scene_case.ns.update(scene_durations=durations, selected_runway=[{'scene_index': 1}])
    _worker(scene_case, 'initial_generation')(scene_case.self)
    scene_case.provider.assert_called_once()
    assert set(scene_case.ns['generated_checkpoint_specs']) == {1}
    plan = json.loads(scene_spend[0].hget(LEDGER_KEY, scene_fields(ROOT)[0]))
    assert len(plan['scenes']) == 3
    assert plan['scenes'][0]['narration_millis'] == 12_000
    assert plan['scenes'][0]['generation_seconds'] == 10
    assert runtime._SCENE.get() is None


def test_existing_video_without_scene_plan_cannot_gain_a_fresh_allowance(scene_case, scene_spend):
    client, ledger = scene_spend
    old_quote = quotes.quote_runway_video({
        'model': 'gen4.5', 'prompt_text': 'earlier paid scene', 'ratio': '720:1280', 'duration': 6})
    ledger.reserve(request_key='previous-native-video', channel_id=CHANNEL,
                   lineage_id=ROOT, kind='shorts', quote=old_quote)
    before = client.hgetall(LEDGER_KEY)
    _bind_worker(scene_case, _prepare())
    with pytest.raises(SpendBlocked, match='spend_scene_plan_late'):
        _worker(scene_case, 'initial_generation')(scene_case.self)
    scene_case.provider.assert_not_called()
    scene_case.critic.assert_not_called()
    after = client.hgetall(LEDGER_KEY)
    # Context binding is observational; old reservation/counters cannot change.
    assert {key: value for key, value in after.items() if not key.startswith('binding:')} == before


def test_retained_only_worker_never_initializes_new_video_allowance(scene_case, monkeypatch):
    monkeypatch.setattr(runtime, 'settings', SimpleNamespace(studio_spend_enforcement=True))
    ledger = Mock(side_effect=SpendBlocked('spend_scene_plan_late'))
    fresh = Mock(side_effect=SpendBlocked('spend_price_expired'))
    monkeypatch.setattr(runtime, 'configured_ledger', ledger)
    monkeypatch.setattr(quotes, '_fresh', fresh)
    _bind_worker(scene_case, _prepare())
    scene_case.ns.update(
        recovered_generated_media={
            'version': 2, 'source_task_id': '22222222-2222-4222-8222-222222222222',
            'scenes': {index: [{'key': f'private/saved-{index}.mp4', 'provider': 'runway',
                               'provider_attempts': 1}] for index in range(3)},
        },
        download_file=Mock(), _validate_recovered_generated_clip=Mock(),
    )
    _worker(scene_case, 'initial_generation')(scene_case.self)
    assert scene_case.ns['download_file'].call_count == 3
    assert set(scene_case.ns['generated_checkpoint_specs']) == {0, 1, 2}
    scene_case.provider.assert_not_called()
    ledger.assert_not_called()
    fresh.assert_not_called()
    assert runtime._SCENE.get() is None


def test_actual_fallback_cannot_buy_a_more_expensive_scene(funded_runway, monkeypatch):
    from app.services import runway
    class CapacityRejected(Exception):
        pass
    monkeypatch.setattr(runway, 'RateLimitError', CapacityRejected)
    client = Mock(base_url='https://api.dev.runwayml.com/', api_key=TEST_KEY)
    client.with_options.return_value = client
    client.text_to_video.create.side_effect = CapacityRejected()
    with runtime.spending_scene(_prepare([5.0]), 0):
        with pytest.raises(SpendBlocked, match='spend_scene_request_limit'):
            runway._create_text_to_video_task(client, 'offline scene', 6, '9:16')
    assert client.text_to_video.create.call_count == 1
    assert funded_runway[1].snapshot()['period']['used_micro'] == 720_000
    assert json.loads(funded_runway[0].hget(LEDGER_KEY, 'funding_state'))['cash_reserved_micro'] == 720_000
    assert runtime._SCENE.get() is None


def test_accepted_poll_failure_does_not_start_another_paid_scene(funded_runway, monkeypatch):
    from app.services import runway
    monkeypatch.setattr(runway, 'settings', SimpleNamespace(runwayml_api_secret=TEST_KEY))
    monkeypatch.setattr(runway, '_runway_gen45_credits_known_insufficient', Mock(return_value=False))
    create_client = Mock(base_url='https://api.dev.runwayml.com/', api_key=TEST_KEY)
    create_client.with_options.return_value = create_client
    create_client.text_to_video.create.return_value = SimpleNamespace(id='accepted-scene')
    poll_client = Mock()
    failure = TimeoutError('accepted operation still unresolved')
    poll_client.tasks.retrieve.return_value.wait_for_task_output.side_effect = failure
    constructor = Mock(side_effect=[create_client, poll_client])
    monkeypatch.setattr(runway, 'RunwayML', constructor)
    with runtime.spending_scene(_prepare([5.0]), 0):
        with pytest.raises(TimeoutError) as caught:
            runway.generate_scene('offline scene', 6, aspect_ratio='9:16')
    assert caught.value is failure
    assert create_client.text_to_video.create.call_count == 1
    poll_client.tasks.retrieve.assert_called_once_with('accepted-scene')
    assert funded_runway[1].snapshot()['period']['used_micro'] == 720_000
    assert json.loads(funded_runway[0].hget(LEDGER_KEY, 'funding_state'))['cash_reserved_micro'] == 720_000
    assert runtime._SCENE.get() is None
