"""Server-only production policy, frozen ledgers and unchanged paid boundaries."""

import ast
from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import fakeredis
from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
import pytest


ROOT = Path(__file__).resolve().parents[1]
OLD = '11111111-1111-4111-8111-111111111111'
NEW = '22222222-2222-4222-8222-222222222222'
PRODUCTION = {'mode': 'production', 'format': 'shorts'}


def _load(path, names=None, **namespace):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    nodes = []
    for node in tree.body:
        identifiers = (
            [node.name] if isinstance(node, (ast.FunctionDef, ast.ClassDef))
            else [target.id for target in node.targets if isinstance(target, ast.Name)]
            if isinstance(node, ast.Assign) else []
        )
        if identifiers and (names is None or set(identifiers) & set(names)):
            nodes.append(node)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.delenv('STUDIO_PRODUCTION_SHORT_PAID_CREATE_CAP', raising=False)
    namespace = _load(
        ROOT / 'app' / 'config.py', {'Settings'},
        __name__='isolated_budget_settings', BaseSettings=BaseSettings,
        SettingsConfigDict=SettingsConfigDict, Field=Field, field_validator=field_validator,
    )
    settings = namespace['Settings'](_env_file=None)
    monkeypatch.setitem(sys.modules, 'app.config', SimpleNamespace(settings=settings))
    routing = _load(ROOT / 'app' / 'services' / 'visual_routing.py')
    return SimpleNamespace(settings=settings, Settings=namespace['Settings'], **routing)


@pytest.mark.parametrize('value', [2, 3, 4, 5, 6, '2', '3', '4', '5', '6'])
def test_server_setting_accepts_only_bounded_integer_values(configured, value):
    settings = configured.Settings(_env_file=None, studio_production_short_paid_create_cap=value)
    assert type(settings.studio_production_short_paid_create_cap) is int
    assert settings.studio_production_short_paid_create_cap == int(value)


@pytest.mark.parametrize('value', [None, True, False, 0, 1, 7, 3.0, 3.5, '3.0', '3e0', '03', 'true', '', '100'])
def test_server_setting_rejects_coercions_and_out_of_range_values(configured, value):
    with pytest.raises(ValidationError):
        configured.Settings(_env_file=None, studio_production_short_paid_create_cap=value)


def test_environment_setting_and_default_are_explicit_server_policy(configured, monkeypatch):
    assert configured.settings.studio_production_short_paid_create_cap == 2
    monkeypatch.setenv('STUDIO_PRODUCTION_SHORT_PAID_CREATE_CAP', '3')
    assert configured.Settings(_env_file=None).studio_production_short_paid_create_cap == 3


def test_production_setting_changes_only_exact_thirty_second_shorts(configured):
    configured.settings.studio_production_short_paid_create_cap = 3
    cap = configured.preview_total_paid_create_cap
    assert cap(PRODUCTION, 0.5) == 3
    assert cap({'mode': 'preview', 'format': 'shorts'}, 0.5) == 2
    for options, duration in [
        (PRODUCTION, 1), (PRODUCTION, 0.51),
        ({'mode': 'production', 'format': 'landscape'}, 0.5),
        ({'mode': 'production'}, 0.5), ({'mode': 'preview'}, 0.55),
    ]:
        assert cap(options, duration) is None


def test_model_or_client_budget_fields_cannot_raise_server_policy(configured):
    forged = dict(PRODUCTION, studio_production_short_paid_create_cap=6,
                  preview_total_paid_create_cap=6, paid_create_cap=6)
    assert configured.preview_total_paid_create_cap(forged, 0.5) == 2
    configured.settings.studio_production_short_paid_create_cap = 3
    assert configured.preview_total_paid_create_cap(forged, 0.5) == 3
    for mix in ('real_first', 'balanced', 'ai_first'):
        options = dict(PRODUCTION, visual_mix=mix)
        assert configured.preview_authored_ai_limit(options, 6, 0.5) == 3
        assert configured.preview_paid_ai_limit(options, 6, 0.5) == 3
        assert configured.preview_paid_ai_limit(options, 1, 0.5) == 1


@pytest.mark.parametrize('value', [True, 1, 7, '3', 3.0])
def test_unvalidated_runtime_setting_fails_closed(configured, value):
    configured.settings.studio_production_short_paid_create_cap = value
    with pytest.raises(ValueError, match='integer from 2 to 6'):
        configured.preview_total_paid_create_cap(PRODUCTION, 0.5)


@pytest.fixture
def budget():
    client = fakeredis.FakeRedis(decode_responses=True)
    state_namespace = _load(
        ROOT / 'app' / 'services' / 'studio_state.py',
        {'JOB_PREFIX', 'JOB_TTL_SECONDS', 'PAID_CREATE_BUDGET_PREFIX',
         '_TASK_ID_PATTERN', '_PAID_CREATE_BUDGET', '_job_key', 'paid_create_budget_state'},
        re=re, _client=lambda: client, _now_iso=lambda: '2026-09-05T00:00:00+00:00',
    )
    state = state_namespace['paid_create_budget_state']
    tasks = _load(
        ROOT / 'app' / 'tasks.py',
        {'_persisted_paid_create_budget', '_persisted_paid_create_slots',
         '_reserve_paid_create_slot', '_validate_paid_create_allocation',
         '_prepare_saved_voice_retry'},
        Path=Path, paid_create_budget_state=state,
        FinalVisualQualityError=RuntimeError, FinalAudioQualityError=RuntimeError,
    )
    for task_id in (OLD, NEW):
        client.set('youtube_studio:job:' + task_id, json.dumps({'task_id': task_id}))
    return SimpleNamespace(client=client, state=state, tasks=tasks)


def _startup(budget, task_id, cap):
    tree = ast.parse((ROOT / 'app' / 'tasks.py').read_text(encoding='utf-8'))
    runtime = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'run_video_pipeline')
    branch = next(
        node for node in ast.walk(runtime) if isinstance(node, ast.If)
        and node.body and isinstance(node.body[0], ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == 'paid_create_budget'
                for target in node.body[0].targets)
    )
    namespace = dict(budget.tasks, task_id=task_id, total_paid_create_cap=cap, runway_attempts=0)
    exec(compile(ast.Module(body=[branch], type_ignores=[]), '<startup>', 'exec'), namespace)
    return namespace


def test_old_job_keeps_two_at_startup_allocation_and_reservation(configured, budget):
    budget.state(OLD, 2)
    configured.settings.studio_production_short_paid_create_cap = 3
    runtime = _startup(budget, OLD, configured.preview_total_paid_create_cap(PRODUCTION, 0.5))
    assert runtime['total_paid_create_cap'] == 2
    assert runtime['runway_attempts'] == 0
    with pytest.raises(RuntimeError, match='before any submission'):
        runtime['_validate_paid_create_allocation'](
            [{'scene_index': index} for index in range(3)], None,
            runtime['total_paid_create_cap'],
        )
    assert budget.state(OLD, 3) == {'used': 0, 'cap': 2, 'remaining': 2}
    for _ in range(2):
        budget.state(OLD, 3, reserve=True)
    with pytest.raises(RuntimeError, match='exhausted'):
        budget.state(OLD, 3, reserve=True)


def test_new_job_three_allows_all_three_primary_replacements(configured, budget):
    configured.settings.studio_production_short_paid_create_cap = 3
    runtime = _startup(budget, NEW, configured.preview_total_paid_create_cap(PRODUCTION, 0.5))
    tree = ast.parse((ROOT / 'app' / 'tasks.py').read_text(encoding='utf-8'))
    selection = next(
        node for node in ast.walk(tree) if isinstance(node, ast.If) and node.body
        and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Call)
        and isinstance(node.body[0].value.func, ast.Name)
        and node.body[0].value.func.id == '_validate_paid_create_allocation'
        and "'production'" in ast.unparse(node.test)
    )
    runtime.update(options=PRODUCTION, ranked_runway_candidates=[{'scene_index': index} for index in (0, 3, 4)])
    exec(compile(ast.Module(body=[selection], type_ignores=[]), '<primary-selection>', 'exec'), runtime)
    assert runtime['selected_runway'] == runtime['ranked_runway_candidates']
    assert runtime['runway_effective_submission_cap'] == 3
    for expected in range(1, 4):
        assert runtime['_reserve_paid_create_slot'](0, 3, task_id=NEW) == expected
    with pytest.raises(RuntimeError, match='no new generation was submitted'):
        runtime['_reserve_paid_create_slot'](0, 3, task_id=NEW)


def test_primary_and_repair_share_one_total_and_restart_does_not_reset_it(budget):
    reserve = budget.tasks['_reserve_paid_create_slot']
    for expected, _request_kind in enumerate(('primary', 'primary', 'primary', 'repair'), 1):
        assert reserve(0, 4, task_id=NEW) == expected
    assert _startup(budget, NEW, 6)['total_paid_create_cap'] == 4
    assert _startup(budget, NEW, 6)['runway_attempts'] == 4
    with pytest.raises(RuntimeError, match='no new generation was submitted'):
        reserve(0, 6, task_id=NEW)
    tree = ast.parse((ROOT / 'app' / 'tasks.py').read_text(encoding='utf-8'))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == 'generate_scene']
    assert len(calls) == 2  # Primary and final repair use the same counted boundary.
    reservations = sorted([
        node for node in ast.walk(tree) if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name) and node.func.id == '_reserve_paid_create_slot'
    ], key=lambda node: node.lineno)
    calls.sort(key=lambda node: node.lineno)
    assert len(reservations) == 2
    assert reservations[0].lineno < calls[0].lineno < reservations[1].lineno < calls[1].lineno
    for reservation in reservations:
        assert ast.unparse(reservation.args[1]) == 'total_paid_create_cap'
        assert any(keyword.arg == 'task_id' and ast.unparse(keyword.value) == 'task_id'
                   for keyword in reservation.keywords)
    for call in calls:
        for flag in ('allow_paid_terminal_resubmit', 'allow_image_motion'):
            expression = next(keyword.value for keyword in call.keywords if keyword.arg == flag)
            assert eval(compile(ast.Expression(expression), '<paid-fallback>', 'eval'), {'total_paid_create_cap': 4}) is False


def test_new_budget_lost_reservation_reply_never_authorizes_an_untracked_create(budget):
    def lost_reply(task_id, cap, *, reserve=False):
        result = budget.state(task_id, cap, reserve=reserve)
        if reserve:
            raise ConnectionError('The reservation response was lost')
        return result

    budget.tasks['paid_create_budget_state'] = lost_reply
    with pytest.raises(RuntimeError, match='no new generation was submitted'):
        budget.tasks['_reserve_paid_create_slot'](0, 3, task_id=NEW)
    assert budget.state(NEW, 3) == {'cap': 3, 'used': 1, 'remaining': 2}


def test_new_budget_concurrent_reservations_stop_at_three_and_reads_are_idempotent(budget):
    def attempt(_index):
        try:
            return budget.state(NEW, 3, reserve=True)['used']
        except RuntimeError:
            return None
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(attempt, range(9)))
    assert sorted(value for value in results if value is not None) == [1, 2, 3]
    for _ in range(3):
        assert budget.state(NEW, 3) == {'cap': 3, 'used': 3, 'remaining': 0}


@pytest.mark.parametrize('parent_used', [0, 1])
def test_same_spec_zero_paid_child_can_freeze_new_policy_without_raising_parent(
    configured, budget, monkeypatch, parent_used,
):
    spec = dict(PRODUCTION, topic='Currency materials', duration_minutes=0.5,
                language='tr', production_profile_revision='unchanged', quality_threshold=86)
    source = {'state': 'FAILURE', 'kind': 'render', 'retry_child_task_id': NEW,
              'spec': copy.deepcopy(spec), 'audio_candidate_checkpoint': {'status': 'unapproved_candidate'}}
    child = {'parent_id': OLD, 'spec': copy.deepcopy(spec)}
    budget.state(OLD, 2)
    if parent_used:
        budget.state(OLD, 2, reserve=True)
    configured.settings.studio_production_short_paid_create_cap = 3
    assert _startup(budget, NEW, configured.preview_total_paid_create_cap(spec, 0.5))['total_paid_create_cap'] == 3
    monkeypatch.setitem(sys.modules, 'app.services.studio_state', SimpleNamespace(get_job=lambda task_id: source if task_id == OLD else child))
    package = {'scenes': [{'narration': 'An unchanged sentence.'}]}
    voice = {'spoken_texts': ['An unchanged sentence.']}
    loader = Mock(return_value={'package': package, 'voice_result': voice})
    reviewer = Mock(return_value=package)
    monkeypatch.setitem(sys.modules, 'app.services.voice_candidate_recovery', SimpleNamespace(
        load_voice_retry_candidate=loader, require_unchanged_voice_narration=Mock(),
    ))
    monkeypatch.setitem(sys.modules, 'app.services.director', SimpleNamespace(revalidate_immutable_short_story=reviewer))
    monkeypatch.setitem(sys.modules, 'app.services.voice', SimpleNamespace(normalize_turkish_tts=lambda text, **kwargs: text))
    budget.tasks.update(preview_total_paid_create_cap=configured.preview_total_paid_create_cap,
                        short_story_package_is_approved=Mock(return_value=True), update_job=Mock())
    if parent_used:
        with pytest.raises(RuntimeError, match='requires zero paid media submissions'):
            budget.tasks['_prepare_saved_voice_retry'](NEW, OLD, spec, Path('/tmp/mock'))
        loader.assert_not_called()
        reviewer.assert_not_called()
    else:
        result = budget.tasks['_prepare_saved_voice_retry'](NEW, OLD, spec, Path('/tmp/mock'))
        assert result['voice_result'] is voice
        reviewer.assert_called_once()
        assert budget.tasks['update_job'].call_args.kwargs['voice_candidate_reuse']['requires_full_qa'] is True
    assert source['spec'] == child['spec'] == spec
    assert budget.state(OLD, 3) == {'used': parent_used, 'cap': 2, 'remaining': 2 - parent_used}
    assert budget.state(NEW, 3) == {'used': 0, 'cap': 3, 'remaining': 3}
