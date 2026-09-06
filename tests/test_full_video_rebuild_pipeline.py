"""Private full rebuilds use fresh work without weakening ordinary retries."""
import ast
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


SOURCE = Path(__file__).resolve().parents[1] / 'app/tasks.py'
TREE = ast.parse(SOURCE.read_text(encoding='utf-8'))
PARENT = '11111111-1111-4111-8111-111111111111'
CHILD = '22222222-2222-4222-8222-222222222222'


class QualityError(RuntimeError):
    pass


class Ignored(Exception):
    pass


def _function(name):
    return deepcopy(next(node for node in TREE.body
                         if isinstance(node, ast.FunctionDef) and node.name == name))


def _exec(node, ns):
    node.decorator_list = []
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                 str(SOURCE), 'exec'), ns)


def _pipeline_prefix(ns):
    """Run actual entry/planning/contract guards, stopping before media work."""
    fn = _function('run_video_pipeline')
    outer = next(node for node in fn.body if isinstance(node, ast.Try))
    stop = next(index for index, node in enumerate(outer.body)
                if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == 'package_sha256'
                        for target in node.targets))
    fn.body = fn.body[:fn.body.index(outer)] + outer.body[:stop]
    fn.body.append(ast.Return(value=ast.Call(func=ast.Name(id='locals', ctx=ast.Load()),
                                           args=[], keywords=[])))
    _exec(fn, ns)
    return ns['run_video_pipeline']


@pytest.fixture
def case(monkeypatch, tmp_path):
    spec = dict(topic='Source-backed warehouse membership economics.', duration_minutes=.5,
                language='en', channel_id='margin-verdict', mode='production', format='shorts',
                workflow='scene_repair', production_scheduled=True, publish_after_render=True,
                production_channel_id='UCabcdefghijklmnopqrstuv', production_connection_id='connection',
                production_profile_revision='revision', production_topic_index=1)
    parent = dict(task_id=PARENT, kind='render', state='FAILURE', spec=deepcopy(spec),
                  retry_child_task_id=CHILD, audio_candidate_checkpoint={'status': 'unapproved_candidate'})
    job = dict(task_id=CHILD, kind='render', state='PENDING', parent_id=PARENT,
               spec=deepcopy(spec), result=None)
    policy = dict(version=1, mode='full', source_task_id=PARENT, child_task_id=CHILD,
                  spec_sha256='a' * 64, fresh_story=True, fresh_voice=True,
                  fresh_media=True, requires_full_qa=True, original_paid_create_cap=6)
    getter = Mock(side_effect=lambda *args: deepcopy(policy))
    monkeypatch.setitem(sys.modules, 'app.services.full_video_rebuild',
                        SimpleNamespace(get_full_rebuild_policy=getter))
    registry = SimpleNamespace(get_job=Mock(side_effect=lambda task: parent if task == PARENT else job))
    monkeypatch.setitem(sys.modules, 'app.services.studio_state', registry)
    monkeypatch.setitem(sys.modules, 'app.services.voice_candidate_recovery', SimpleNamespace(
        load_voice_retry_candidate=Mock(), require_unchanged_voice_narration=Mock()))
    monkeypatch.setitem(sys.modules, 'app.services.director',
                        SimpleNamespace(revalidate_immutable_short_story=Mock()))
    monkeypatch.setitem(sys.modules, 'app.services.voice',
                        SimpleNamespace(normalize_turkish_tts=lambda text, **kwargs: text))
    events, active = [], {'enabled': False}

    @contextmanager
    def route(*, enabled):
        assert type(enabled) is bool
        previous = active['enabled']
        active['enabled'] = enabled
        events.append(('route_enter', enabled))
        try:
            yield {'provider': 'openai', 'model': 'gpt-6-astra'} if enabled else None
        finally:
            active['enabled'] = previous
            events.append(('route_exit', enabled))

    monkeypatch.setitem(sys.modules, 'app.services.planning_model_routing',
                        SimpleNamespace(fresh_planning_route=route))
    package = dict(title='A newly reviewed story', scenes=[{'narration': 'New wording.'}])

    def research(*args, **kwargs):
        events.append(('research', active['enabled']))
        return deepcopy(package)

    def director(draft, *args, **kwargs):
        events.append(('director', active['enabled']))
        return deepcopy(draft)

    def compress(task_id, candidate, *args, **kwargs):
        events.append(('compress', active['enabled']))
        return candidate

    ns = dict(Path=lambda *_args: tmp_path, FinalVisualQualityError=QualityError,
              FinalAudioQualityError=QualityError, Ignore=Ignored,
              _RECOVERED_MEDIA_SOURCE_PATTERN=re.compile(r'^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$'),
              _SHA256_PATTERN=re.compile(r'^[0-9a-f]{64}$'),
              normalize_pipeline_language=lambda language: language,
              _normalized_options=lambda options, _duration: deepcopy(options),
              _task_spec=lambda topic, duration, language, channel, options: dict(
                  topic=topic, duration_minutes=duration, language=language, channel_id=channel, **options),
              update_job=Mock(), set_stage=Mock(), preview_total_paid_create_cap=Mock(return_value=6),
              _persisted_paid_create_budget=Mock(return_value={'cap': 6, 'used': 0}),
              _persisted_paid_create_slots=Mock(return_value=3),
              acquire_retry_child_execution=Mock(return_value=True),
              short_story_package_is_approved=Mock(return_value=True),
              research_and_script=Mock(side_effect=research), direct_and_qc=Mock(side_effect=director),
              _prepare_scheduled_short_shots=Mock(side_effect=compress),
              _prepare_voice_replacement_request=Mock())
    for name in ('_prepare_full_video_rebuild', '_fresh_scheduled_short_shots',
                 '_guard_retry_child_execution', '_prepare_saved_voice_retry', '_prepare_package'):
        _exec(_function(name), ns)
    saved_voice = Mock(wraps=ns['_prepare_saved_voice_retry'])
    ns['_prepare_saved_voice_retry'] = saved_voice
    run = _pipeline_prefix(ns)
    task = SimpleNamespace(request=SimpleNamespace(id=CHILD, retries=0))
    return SimpleNamespace(**locals())


def _run(case, **changes):
    options = {key: value for key, value in case.spec.items()
               if key not in {'topic', 'duration_minutes', 'language', 'channel_id'}}
    args = dict(topic=case.spec['topic'], duration_minutes=.5, language='en',
                channel_id='margin-verdict', options=options,
                retry_dispatch_source_id=PARENT, full_rebuild_source_id=PARENT)
    args.update(changes)
    return case.run(case.task, **args)


def _verify(case, **changes):
    args = dict(task_id=CHILD, source_task_id=PARENT, runtime_spec=case.spec,
                retry_dispatch_source_id=PARENT, approved_package=None,
                curated_stock_manifest=None, voice_replacement_source_id=None)
    args.update(changes)
    return case.ns['_prepare_full_video_rebuild'](**args)


def test_verified_rebuild_runs_new_planning_not_saved_voice_or_retained_assets(case):
    before = deepcopy((case.spec, case.parent, case.job))
    result = _run(case)
    assert result['saved_voice_retry'] is None
    assert result['approved_package'] is None
    assert result['raw_recovered_voice'] is None
    assert result['raw_recovered_generated_media'] is None
    assert result['fresh_scheduled_shot_prompts'] is True
    case.saved_voice.assert_not_called()
    case.getter.assert_called_once_with(CHILD, PARENT, case.spec)
    case.ns['acquire_retry_child_execution'].assert_called_once_with(CHILD, PARENT)
    assert case.events == [('route_enter', True), ('research', True), ('director', True),
                           ('compress', True), ('route_exit', True)]
    assert case.active['enabled'] is False
    assert case.ns['research_and_script'].call_args.kwargs == {'fresh_scheduled': True}
    assert case.ns['direct_and_qc'].call_args.kwargs == {'fresh_scheduled': True}
    assert result['package'] == case.package
    assert (case.spec, case.parent, case.job) == before
    assert any(call.kwargs.get('story_planning_route') == {'provider': 'openai', 'model': 'gpt-6-astra'}
               for call in case.ns['update_job'].call_args_list)


def test_verified_rebuild_uses_shared_new_voice_and_fresh_broll_collection(case):
    result = _run(case)
    fn = _function('run_video_pipeline')
    block = next(node for node in ast.walk(fn) if isinstance(node, ast.With)
                 and any(isinstance(item.optional_vars, ast.Name)
                         and item.optional_vars.id == 'stage_pool' for item in node.items))
    synth = Mock(return_value={'path': '/tmp/new-voice.mp3'})
    broll = Mock(return_value=['fresh-stock-candidate'])
    reject_reuse = Mock(side_effect=AssertionError('Full rebuild cannot reuse old media'))
    ns = dict(result, ThreadPoolExecutor=ThreadPoolExecutor, scenes=case.package['scenes'],
              _synthesize_voice_candidate=synth, _collect_broll=broll,
              _fit_saved_voice_for_retry=reject_reuse, _download_recovered_voice_candidate=reject_reuse,
              _collect_curated_recovery_visuals=reject_reuse, _checkpoint_audio_candidate=Mock(),
              recovered_voice=None, curated_source_job=None,
              strict_short_preview_duration=False, pexels_orientation='portrait')
    exec(compile(ast.Module(body=[block], type_ignores=[]), str(SOURCE), 'exec'), ns)
    synth.assert_called_once_with(case.package['scenes'], CHILD, 30.0, language='en')
    broll.assert_called_once_with(case.package['scenes'], result['work'], False, orientation='portrait')
    reject_reuse.assert_not_called()
    assert ns['broll_result'] == ['fresh-stock-candidate']
    assert case.active['enabled'] is False


def test_untouched_scheduler_root_uses_same_fresh_planner_without_rebuild_policy(case):
    case.spec['workflow'] = 'auto'
    case.job['spec'] = deepcopy(case.spec)
    case.job['parent_id'] = None
    result = _run(case, retry_dispatch_source_id=None, full_rebuild_source_id=None)
    assert result['full_rebuild_request'] is None
    assert result['fresh_scheduled_shot_prompts'] is True
    case.getter.assert_not_called()
    assert ('research', True) in case.events


def test_ordinary_retry_without_audio_candidate_keeps_legacy_planner_route(case):
    case.parent.pop('audio_candidate_checkpoint')
    result = _run(case, full_rebuild_source_id=None)
    assert result['full_rebuild_request'] is None
    assert result['fresh_scheduled_shot_prompts'] is False
    case.getter.assert_not_called()
    assert ('research', False) in case.events
    assert not any(call.kwargs.get('story_planning_route')
                   for call in case.ns['update_job'].call_args_list)


@pytest.mark.parametrize('workflow', ['auto', 'scene_repair'])
def test_verified_rebuild_keeps_original_transport_workflow(case, workflow):
    case.spec['workflow'] = workflow
    case.job['spec'] = deepcopy(case.spec)
    _run(case)
    assert case.getter.call_args.args[2]['workflow'] == workflow


@pytest.mark.parametrize('changes', [
    {'task_id': PARENT}, {'source_task_id': ''}, {'source_task_id': True},
    {'source_task_id': 'invalid'}, {'retry_dispatch_source_id': None},
    {'retry_dispatch_source_id': CHILD}, {'approved_package': {}},
    {'approved_package': {'scenes': []}}, {'curated_stock_manifest': {}},
    {'voice_replacement_source_id': PARENT},
])
def test_incompatible_or_unbound_request_never_reads_policy(case, changes):
    with pytest.raises(QualityError, match='^Full rebuild authorization could not be verified$'):
        _verify(case, **changes)
    case.getter.assert_not_called()
    case.ns['research_and_script'].assert_not_called()


@pytest.mark.parametrize('field,value', [
    ('version', True), ('version', 2), ('mode', 'repair'), ('source_task_id', CHILD),
    ('child_task_id', PARENT), ('spec_sha256', 'bad'), ('spec_sha256', None),
    ('fresh_story', False), ('fresh_voice', 1), ('fresh_media', 'true'),
    ('requires_full_qa', False), ('original_paid_create_cap', True),
    ('original_paid_create_cap', 7), ('original_paid_create_cap', None),
])
def test_incomplete_private_policy_never_falls_back_to_ordinary_retry(case, field, value):
    case.policy[field] = value
    with pytest.raises(QualityError, match='^Full rebuild authorization could not be verified$'):
        _run(case)
    case.saved_voice.assert_not_called()
    case.ns['research_and_script'].assert_not_called()
    case.ns['_persisted_paid_create_budget'].assert_not_called()


def test_missing_or_unavailable_policy_fails_closed_without_leaking_error(case):
    case.getter.side_effect = RuntimeError('private authorization details')
    with pytest.raises(QualityError) as failure:
        _run(case)
    assert str(failure.value) == 'Full rebuild authorization could not be verified'
    case.saved_voice.assert_not_called()
    case.ns['research_and_script'].assert_not_called()


def test_no_full_rebuild_argument_does_not_infer_authority_from_options(case):
    case.spec['full_rebuild_verified'] = True
    case.spec['full_rebuild_source_id'] = PARENT
    case.parent['spec'] = deepcopy(case.spec)
    case.job['spec'] = deepcopy(case.spec)
    with pytest.raises(QualityError, match='requires zero paid media submissions'):
        _run(case, full_rebuild_source_id=None)
    case.getter.assert_not_called()
    case.saved_voice.assert_called_once()
    case.ns['research_and_script'].assert_not_called()


def test_duplicate_initial_delivery_is_ignored_before_policy_or_planning(case):
    case.ns['acquire_retry_child_execution'].return_value = False
    with pytest.raises(Ignored):
        _run(case)
    case.getter.assert_not_called()
    case.ns['research_and_script'].assert_not_called()


@pytest.mark.parametrize('field,value', [
    ('parent_id', None), ('parent_id', CHILD), ('result', {}), ('state', 'SUCCESS'),
    ('audio_candidate_checkpoint', {}), ('voice_candidate_reuse', {}),
    ('voice_replacement', {}), ('repair_checkpoint', {}), ('qa_workprint', {}),
])
def test_authorized_rebuild_cannot_reenter_used_or_wrong_child(case, field, value):
    case.job[field] = value
    with pytest.raises(QualityError, match='requires an untouched authorized child'):
        _run(case)
    case.saved_voice.assert_not_called()
    case.ns['research_and_script'].assert_not_called()


def test_authorized_rebuild_cannot_reenter_after_reserved_paid_video(case):
    case.ns['_persisted_paid_create_budget'].return_value = {'cap': 6, 'used': 1}
    with pytest.raises(QualityError, match='requires an untouched authorized child'):
        _run(case)
    case.ns['research_and_script'].assert_not_called()


@pytest.mark.parametrize('persisted', [False, True])
def test_rebuild_cannot_increase_or_change_the_authorized_paid_cap(case, persisted):
    if persisted:
        case.ns['_persisted_paid_create_budget'].return_value = {'cap': 5, 'used': 0}
    else:
        case.ns['preview_total_paid_create_cap'].return_value = 5
    with pytest.raises(QualityError, match='paid-create budget differs from its authorization'):
        _run(case)
    case.ns['research_and_script'].assert_not_called()
    if not persisted:
        case.ns['_persisted_paid_create_budget'].assert_not_called()


def test_fresh_planner_cannot_smuggle_a_recovered_media_contract(case):
    case.package['_recovered_voice'] = {'version': 1}
    with pytest.raises(QualityError, match='Recovered media requires an approved storyboard'):
        _run(case)
    assert case.active['enabled'] is False


def test_planning_exception_restores_request_scoped_model_route(case):
    case.ns['direct_and_qc'].side_effect = RuntimeError('invalid model response')
    with pytest.raises(RuntimeError, match='invalid model response'):
        _run(case)
    assert case.active['enabled'] is False
    assert case.events[-1] == ('route_exit', True)


def test_new_option_is_trailing_and_default_none_for_existing_dispatches():
    fn = _function('run_video_pipeline')
    assert fn.args.args[-1].arg == 'full_rebuild_source_id'
    assert isinstance(fn.args.defaults[-1], ast.Constant) and fn.args.defaults[-1].value is None


def test_full_rebuild_errors_do_not_restart_whole_pipeline_but_ordinary_errors_can():
    fn = _function('run_video_pipeline')
    outer = next(node for node in fn.body if isinstance(node, ast.Try))
    handler = outer.handlers[0]
    terminal = next(node for node in handler.body if isinstance(node, ast.If)
                    and 'full_rebuild_source_id' in ast.unparse(node.test))
    ordinary = next(node for node in handler.body if isinstance(node, ast.If)
                    and 'runway_attempts == 0' in ast.unparse(node.test))
    assert handler.body.index(terminal) < handler.body.index(ordinary)
    compiled = compile(ast.Module(body=[terminal], type_ignores=[]), str(SOURCE), 'exec')
    failure = RuntimeError('ambiguous provider response')
    mark = Mock()
    ns = dict(full_rebuild_source_id=PARENT, terminal_pre_media_error=False, exc=failure,
              FinalVisualQualityError=QualityError, mark_failure=mark, task_id=CHILD)
    with pytest.raises(QualityError, match='Full rebuild stopped without automatic restart: RuntimeError'):
        exec(compiled, ns)
    mark.assert_called_once()
    ns['full_rebuild_source_id'] = None
    mark.reset_mock()
    exec(compiled, ns)
    mark.assert_not_called()


def test_normal_voice_media_and_publication_gates_are_still_on_the_shared_pipeline():
    fn = _function('run_video_pipeline')
    text = ast.unparse(fn)
    assert text.index('_guard_retry_child_execution(') < text.index('_prepare_full_video_rebuild(')
    assert text.index('_prepare_full_video_rebuild(') < text.index('_prepare_saved_voice_retry(')
    assert text.index('with fresh_planning_route(') < text.index('_synthesize_voice_candidate,')
    for call in ('_verify_audio_narration_with_retry(', 'verify_audio_prosody(',
                 '_preflight_production_shorts_paid_plan(', '_queue_automatic_publish_if_enabled('):
        assert call in text
    branch = next(node for node in ast.walk(fn) if isinstance(node, ast.If)
                  and isinstance(node.test, ast.Name) and node.test.id == 'recovered_voice'
                  and any(isinstance(child, ast.Assign)
                          and any(isinstance(target, ast.Name) and target.id == 'voice_future'
                                  for target in child.targets) for child in node.body))
    assert len(branch.orelse) == 1
    synthesis = branch.orelse[0].value
    assert isinstance(synthesis, ast.Call)
    assert synthesis.args[0].id == '_synthesize_voice_candidate'
