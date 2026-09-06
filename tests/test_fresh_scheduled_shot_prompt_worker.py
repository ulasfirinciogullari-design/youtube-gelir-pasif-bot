"""Fresh scheduler prompt transport without importing workers or providers."""
import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


SOURCE = Path(__file__).resolve().parents[1] / 'app/tasks.py'
TREE = ast.parse(SOURCE.read_text(encoding='utf-8'))
TASK = '11111111-1111-4111-8111-111111111111'
PREFIX = 'youtube_studio:shot_prompt_compression:v1:'


def _exec(nodes, ns):
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), ns)


def _function(name):
    return next(n for n in ast.walk(TREE) if isinstance(n, ast.FunctionDef) and n.name == name)


@pytest.fixture
def case(monkeypatch):
    spec = {'topic': 'Evidence-backed barcode history.', 'duration_minutes': .5, 'language': 'en',
        'channel_id': 'margin-verdict', 'mode': 'production', 'format': 'shorts', 'workflow': 'auto',
        'production_scheduled': True, 'publish_after_render': True,
        'production_channel_id': 'UCabcdefghijklmnopqrstuv', 'production_connection_id': 'connection',
        'production_profile_revision': 'revision', 'production_topic_index': 1}
    job = {'task_id': TASK, 'kind': 'render', 'spec': deepcopy(spec), 'parent_id': None,
           'result': None, 'state': 'PENDING'}
    keys = {}
    def reserve(key, value, *, nx):
        assert nx is True
        if key in keys:
            return None
        keys[key] = value
        return True
    client = SimpleNamespace(set=Mock(side_effect=reserve))
    registry = SimpleNamespace(get_job=Mock(side_effect=lambda task: deepcopy(job)),
                               _client=Mock(return_value=client))
    ensure = Mock(side_effect=lambda package, *a, **k: package)
    monkeypatch.setitem(sys.modules, 'app.services.studio_state', registry)
    monkeypatch.setitem(sys.modules, 'app.services.director', SimpleNamespace(
        ensure_scheduled_short_shot_prompts=ensure))
    ns = {'re': re, 'json': json, 'hashlib': hashlib, 'FinalVisualQualityError': RuntimeError,
          '_RECOVERED_MEDIA_SOURCE_PATTERN': re.compile(r'^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$'),
          'set_stage': Mock(), 'research_and_script': Mock(return_value={'draft': True}),
          'direct_and_qc': Mock(return_value={'approved': True}),
          'short_story_package_is_approved': Mock(return_value=True)}
    for name in ['_fresh_scheduled_short_shots', '_prepare_scheduled_short_shots',
                 '_prepare_package', '_recovery_package_sha256']:
        _exec([_function(name)], ns)
    package = {'title': 'The first barcode scan', 'sources': [{'url': 'https://example.org/history'}],
               'narration': 'A fixed sourced narration.',
               'scenes': [{'index': 0, 'narration': 'A fixed sourced narration.',
                  'ai_prompt': 'A hand passes a Juicy Fruit multipack across a flatbed scanner.',
                  'visual_queries': ['old misleading handheld checkout gun']}]}
    return SimpleNamespace(ns=ns, spec=spec, job=job, registry=registry, client=client,
                           ensure=ensure, keys=keys, package=package)


def _eligible(case, **changes):
    args = dict(task_id=TASK, spec=case.spec, approved_package=None,
                retry_dispatch_source_id=None, curated_stock_manifest=None,
                voice_replacement_source_id=None, paid_slots_used=0)
    args.update(changes)
    return case.ns['_fresh_scheduled_short_shots'](**args)


def _prepare(case):
    return case.ns['_prepare_scheduled_short_shots'](
        TASK, case.package, case.spec['topic'], .5, 'en', case.spec)


@pytest.mark.parametrize('state', ['PENDING', 'STARTED', 'PROGRESS', 'RETRY'])
def test_only_untouched_scheduler_root_enters_new_path(case, state):
    case.job['state'] = state
    before = deepcopy((case.job, case.spec))
    assert _eligible(case) is True
    assert (case.job, case.spec) == before
    case.ensure.assert_not_called()
    case.client.set.assert_not_called()


@pytest.mark.parametrize('key,value', [
    ('mode', 'preview'), ('format', 'landscape'), ('duration_minutes', 3),
    ('workflow', 'manual'), ('production_scheduled', False), ('production_scheduled', 1),
    ('publish_after_render', False), ('publish_after_render', 'true'),
])
def test_other_modes_and_manual_routes_do_not_even_read_registry(case, key, value):
    case.spec[key] = value
    assert _eligible(case) is False
    case.registry.get_job.assert_not_called()


@pytest.mark.parametrize('changes', [
    {'approved_package': {}}, {'approved_package': {'scenes': []}},
    {'retry_dispatch_source_id': TASK}, {'curated_stock_manifest': {}},
    {'voice_replacement_source_id': TASK}, {'paid_slots_used': 1}, {'paid_slots_used': False},
])
def test_frozen_recovery_paid_or_invalid_input_keeps_legacy_path(case, changes):
    assert _eligible(case, **changes) is False
    case.registry.get_job.assert_not_called()


@pytest.mark.parametrize('field,value', [
    ('parent_id', TASK), ('result', {}), ('audio_candidate_checkpoint', {}),
    ('voice_candidate_reuse', {}), ('voice_replacement', {}), ('repair_checkpoint', {}),
    ('qa_workprint', {}), ('state', 'FAILURE'), ('state', 'SUCCESS'), ('state', 'AWAITING_APPROVAL'),
])
def test_existing_candidates_or_terminal_history_are_never_rewritten(case, field, value):
    case.job[field] = value
    assert _eligible(case) is False
    case.ensure.assert_not_called()


@pytest.mark.parametrize('damage', ['task', 'missing', 'kind', 'spec', 'unavailable'])
def test_invalid_server_job_binding_is_terminal_and_secret_safe(case, damage):
    if damage == 'task': case.job['task_id'] = 'other'
    if damage == 'missing': case.registry.get_job.side_effect = lambda task: None
    if damage == 'kind': case.job['kind'] = 'plan'
    if damage == 'spec': case.job['spec']['topic'] = 'Changed topic'
    if damage == 'unavailable': case.registry.get_job.side_effect = ValueError('secret URL')
    with pytest.raises(RuntimeError, match='^Fresh scheduled storyboard binding could not be verified$'):
        _eligible(case)
    case.client.set.assert_not_called()


def test_all_valid_path_delegates_once_without_reservation_or_provider_mutation(case):
    before = deepcopy(case.package)
    assert _prepare(case) is case.package
    call = case.ensure.call_args
    assert call.args == (case.package, case.spec['topic'], .5, 'en', case.spec)
    assert call.kwargs['fresh_scheduled'] is True
    assert callable(call.kwargs['before_compression'])
    assert case.package == before
    case.client.set.assert_not_called()


def test_compression_reservation_is_permanent_single_task_and_precedes_request(case):
    events = []
    def ensure(package, *args, before_compression, **kwargs):
        before_compression()
        assert PREFIX + TASK in case.keys
        events.append('model request')
        return package
    case.ensure.side_effect = ensure
    assert _prepare(case) == case.package
    value = json.loads(case.keys[PREFIX + TASK])
    assert value == {'task_id': TASK, 'attempts': 1,
                     'package_sha256': case.ns['_recovery_package_sha256'](case.package)}
    assert case.client.set.call_args.kwargs == {'nx': True}
    case.package['scenes'][0]['ai_prompt'] += ' Another proposed direction.'
    with pytest.raises(RuntimeError, match='before voice or paid media'):
        _prepare(case)
    assert events == ['model request']
    assert json.loads(case.keys[PREFIX + TASK]) == value


def test_ambiguous_nx_reply_stops_before_model_and_cannot_reopen(case):
    def ensure(package, *args, before_compression, **kwargs):
        before_compression()
        pytest.fail('No model request after uncertain reservation')
    original = case.client.set.side_effect
    def lost_reply(*args, **kwargs):
        original(*args, **kwargs)
        raise TimeoutError('private Redis address')
    case.ensure.side_effect = ensure
    case.client.set.side_effect = lost_reply
    with pytest.raises(RuntimeError, match='before voice or paid media'):
        _prepare(case)
    assert PREFIX + TASK in case.keys
    case.client.set.side_effect = original
    with pytest.raises(RuntimeError, match='before voice or paid media'):
        _prepare(case)


def test_real_redis_nx_allows_only_one_concurrent_compression_request(case):
    from concurrent.futures import ThreadPoolExecutor
    import fakeredis

    redis_client = fakeredis.FakeRedis(decode_responses=True)
    case.registry._client.return_value = redis_client
    requests = Mock()
    def ensure(package, *args, before_compression, **kwargs):
        before_compression()
        requests()
        return package
    case.ensure.side_effect = ensure
    def attempt():
        try:
            _prepare(case)
            return True
        except RuntimeError:
            return False
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: attempt(), range(2)))
    assert sorted(results) == [False, True]
    requests.assert_called_once_with()
    assert redis_client.ttl(PREFIX + TASK) == -1


@pytest.mark.parametrize('error', [ValueError('private provider body'), TimeoutError('secret'), RuntimeError('critic rejected')])
def test_any_compression_or_critic_failure_is_terminal_without_secret_output(case, error):
    case.ensure.side_effect = error
    with pytest.raises(RuntimeError, match='^Scheduled shooting directions could not be validated before voice or paid media$'):
        _prepare(case)


@pytest.mark.parametrize('fresh', [False, True])
def test_fresh_writer_keyword_is_not_sent_to_legacy_routes(case, fresh):
    result = case.ns['_prepare_package'](None, TASK, 'topic', .5, 'en', case.spec, None,
                                          fresh_scheduled=fresh)
    expected = {'fresh_scheduled': True} if fresh else {}
    case.ns['research_and_script'].assert_called_once_with('topic', .5, 'en', case.spec, **expected)
    case.ns['direct_and_qc'].assert_called_once_with({'draft': True}, 'topic', .5, 'en', case.spec, **expected)
    assert result == {'approved': True}


def test_approved_package_never_enters_fresh_writers_even_if_flag_is_passed(case):
    case.ns['_prepare_package'](None, TASK, 'topic', .5, 'en', case.spec, case.package,
                                fresh_scheduled=True)
    case.ns['research_and_script'].assert_not_called()
    case.ns['direct_and_qc'].assert_not_called()


def _rank(case, **changes):
    ns = dict(options=case.spec, duration_minutes=.5, recovered_generated_media=None,
        recovery_repair_scene_indices=set(), fresh_scheduled_shot_prompts=True,
        scenes=deepcopy(case.package['scenes']) + [{'index': 1, 'narration': 'Stock fact.', 'ai_prompt': None}],
        current_reviews={0: {'score': 40, 'retry_queries': ['old unrelated warehouse']}, 1: {'score': 40}},
        scene_visuals=[[], []], generation_aspect_ratio='9:16', provider_outage_stock_scenes=set(),
        stock_quality_fallback_scenes={1}, recovery_paid_scene_indices=set(), is_bounded_short_preview=False,
        _runway_prompt_for_scene=Mock(return_value='legacy fallback'), _apply_visual_review=Mock(),
        _visual_path=lambda x: x, should_rank_runway_candidate=lambda *a, **k: True, quality_threshold=86)
    ns.update(changes)
    _exec([_function('rank_runway_candidates')], ns)
    return ns


def test_fresh_authored_prompt_reaches_paid_map_exactly_without_stale_queries(case):
    ns = _rank(case)
    before = deepcopy(ns['scenes'])
    prompts, ranked = ns['rank_runway_candidates']()
    assert prompts[0] == before[0]['ai_prompt']
    assert 'flatbed' in prompts[0] and 'handheld' not in prompts[0] and 'PRIMARY EVENT' not in prompts[0]
    assert prompts[1] == 'legacy fallback'
    assert len(ranked) == 2 and ns['scenes'] == before
    assert ns['_runway_prompt_for_scene'].call_count == 1


def test_existing_frozen_scheduled_run_keeps_legacy_transport(case):
    ns = _rank(case, fresh_scheduled_shot_prompts=False)
    assert ns['rank_runway_candidates']()[0] == {0: 'legacy fallback', 1: 'legacy fallback'}


def test_invalid_late_authored_prompt_cannot_silently_truncate(case):
    ns = _rank(case)
    ns['scenes'][1]['ai_prompt'] = 'x' * 1001
    with pytest.raises(ValueError):
        ns['rank_runway_candidates']()


def test_actual_pipeline_places_preflight_before_audio_media_and_package_hash():
    pipeline = _function('run_video_pipeline')
    call = next(n for n in ast.walk(pipeline) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name) and n.func.id == '_prepare_scheduled_short_shots')
    later_names = {'_preflight_production_shorts_paid_plan', '_recovery_package_sha256', 'generate_scene'}
    assert all(n.lineno > call.lineno for n in ast.walk(pipeline)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in later_names)
    audio = [n for n in ast.walk(pipeline) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == 'submit'
             and any(isinstance(a, ast.Name) and a.id == '_synthesize_voice_candidate' for a in n.args)]
    assert audio and all(n.lineno > call.lineno for n in audio)
    creates = [n for n in ast.walk(pipeline) if isinstance(n, ast.Call)
               and isinstance(n.func, ast.Name) and n.func.id == 'generate_scene']
    assert any(ast.unparse(n.args[0]) == 'prompt_candidates[scene_idx]' for n in creates)
    terminal = next(n for n in ast.walk(pipeline) if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == 'terminal_pre_media_error' for t in n.targets))
    assert 'FinalVisualQualityError' in ast.unparse(terminal)


def _real_director_bridge(case, monkeypatch):
    """Actual helper/immutable mapping, with only external critic/compressor mocked."""
    from app.services.production_shot_prompt import build_production_shot_prompt

    path = SOURCE.parent / 'services/director.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    names = {'ScheduledShotPromptError', '_scheduled_short_shot_contract',
             '_scheduled_shot_prompt_units', '_immutable_narration_map',
             'ensure_scheduled_short_shot_prompts'}
    ns = {'deepcopy': deepcopy, '_MAX_PRODUCTION_SCENES': 6,
          'build_production_shot_prompt': build_production_shot_prompt,
          'short_story_package_is_approved': Mock(return_value=True),
          '_compress_scheduled_shot_prompts': Mock(), 'revalidate_immutable_short_story': Mock()}
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), ns)
    module = SimpleNamespace(ensure_scheduled_short_shot_prompts=ns['ensure_scheduled_short_shot_prompts'])
    monkeypatch.setitem(sys.modules, 'app.services.director', module)
    case.package['scenes'] = [dict(case.package['scenes'][0], index=i, narration=f'Exact fact {i}.')
                              for i in range(6)]
    case.package['scenes'][5]['ai_prompt'] = None
    case.package['narration'] = ' '.join(s['narration'] for s in case.package['scenes'])
    case.package['studio_options'] = deepcopy(case.spec)
    return ns


def test_real_helper_valid_prompts_use_no_model_no_reservation_and_reach_map_unchanged(case, monkeypatch):
    director = _real_director_bridge(case, monkeypatch)
    before = deepcopy(case.package)
    result = _prepare(case)
    assert result is case.package and result == before
    director['_compress_scheduled_shot_prompts'].assert_not_called()
    director['revalidate_immutable_short_story'].assert_not_called()
    case.client.set.assert_not_called()
    runtime = _rank(case, scenes=result['scenes'], scene_visuals=[[] for _ in range(6)])
    prompts, _ = runtime['rank_runway_candidates']()
    assert all(prompts[i] == before['scenes'][i]['ai_prompt'] for i in range(5))
    assert prompts[5] == 'legacy fallback'


def test_real_helper_compresses_once_then_checks_immutable_narration_and_old_constraints(case, monkeypatch):
    director = _real_director_bridge(case, monkeypatch)
    case.package['scenes'][4]['ai_prompt'] = ('Keep the flatbed scanner and hatchback. ' + 'Unnecessary prose. ' * 65).strip()
    original = deepcopy(case.package)
    replacement = 'One continuous shot: the multipack crosses the flatbed scanner before the hatchback departs.'
    def compress(package, topic, indices):
        assert PREFIX + TASK in case.keys
        assert package == original and indices == [4]
        return {'scenes': [{'index': 4, 'ai_prompt': replacement}]}
    def critic(package, topic, duration, language, options, **kwargs):
        assert kwargs['immutable_candidate_narrations'] == [s['narration'] for s in original['scenes']]
        assert kwargs['immutable_original_shot_prompts'] == {4: original['scenes'][4]['ai_prompt']}
        return {**package, 'stock_scene_qc': {'fresh': True}, 'short_story_qc': {'fresh': True}}
    director['_compress_scheduled_shot_prompts'].side_effect = compress
    director['revalidate_immutable_short_story'].side_effect = critic
    result = _prepare(case)
    assert case.package == original and result['narration'] == original['narration']
    assert result['sources'] == original['sources'] and result['studio_options'] == original['studio_options']
    assert result['scenes'][4] == {**original['scenes'][4], 'ai_prompt': replacement}
    assert all(result['scenes'][i] == original['scenes'][i] for i in [0, 1, 2, 3, 5])
    assert director['_compress_scheduled_shot_prompts'].call_count == 1
    assert director['revalidate_immutable_short_story'].call_count == 1


def test_real_helper_rejects_critic_narration_rewrite_without_repeating_compression(case, monkeypatch):
    director = _real_director_bridge(case, monkeypatch)
    case.package['scenes'][4]['ai_prompt'] = 'x' * 1001
    director['_compress_scheduled_shot_prompts'].return_value = {'scenes': [{'index': 4, 'ai_prompt': 'A fixed shot.'}]}
    def changed(candidate, *a, **k):
        candidate['scenes'][0]['narration'] = 'An invented new fact.'
        return candidate
    director['revalidate_immutable_short_story'].side_effect = changed
    before = deepcopy(case.package)
    for _ in range(2):
        with pytest.raises(RuntimeError, match='before voice or paid media'):
            _prepare(case)
    assert case.package == before
    assert director['_compress_scheduled_shot_prompts'].call_count == 1
    assert director['revalidate_immutable_short_story'].call_count == 1
