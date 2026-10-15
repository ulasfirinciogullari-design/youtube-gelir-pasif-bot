"""Execute the V6 worker boundaries with local media and offline services.

The source tree is read for each fixture, not cached during test collection.
External boundaries are explicitly replaced and restored by monkeypatch.
"""
import ast
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import fakeredis
import pytest

from test_selected_visual_checkpoint import case as checkpoint_case
from app.services.production_spend import LEDGER_KEY, SpendBlocked
from spending_test_support import fake_sdk_client, installed_sdk_modules, test_funding_policy as funding_policy


SOURCE = Path(__file__).resolve().parents[1] / 'app/tasks.py'
PARENT = '11111111-1111-4111-8111-111111111111'
CHILD = '22222222-2222-4222-8222-222222222222'
ROOT = '33333333-3333-4333-8333-333333333333'
CHANNEL = 'UC5v9AvNtD3PTLgo6m1jROOA'


class VisualError(RuntimeError):
    pass


class AudioError(RuntimeError):
    pass


def _execute(nodes, namespace):
    exec(compile(ast.Module(body=deepcopy(nodes), type_ignores=[]), str(SOURCE), 'exec'), namespace)


def _function(tree, name):
    return next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)


def _assignment(tree, name):
    return next(node for node in ast.walk(tree) if isinstance(node, (ast.Assign, ast.AnnAssign))
                and any(isinstance(target, ast.Name) and target.id == name
                        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])))


def _forbidden(*args, **kwargs):
    raise AssertionError('Selected recovery must not create undeclared work')


@pytest.fixture(scope='module')
def real_media(tmp_path_factory):
    directory = tmp_path_factory.mktemp('selected-recovery-local-media')
    video, audio = directory / 'source.mp4', directory / 'voice.mp3'
    for command in (
        ['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=64x64:rate=30',
         '-t', '8', '-an', '-c:v', 'libx264', '-preset', 'ultrafast', '-threads', '1', str(video)],
        ['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=22050',
         '-t', '27.4', '-c:a', 'libmp3lame', '-threads', '1', str(audio)],
    ):
        subprocess.run(command, check=True, timeout=30, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return SimpleNamespace(video=video, audio=audio)


@pytest.fixture
def case(checkpoint_case, real_media, monkeypatch):
    from app.services import selected_visual_checkpoint as checkpoint
    from app.services import selected_visual_recovery as recovery

    saved = checkpoint_case
    saved.args['binding']['lineage_id'] = ROOT
    saved.audio.write_bytes(real_media.audio.read_bytes())
    video = saved.work / 'real-source.mp4'
    video.write_bytes(real_media.video.read_bytes())
    # Use real probes even though the lightweight preservation fixture has a
    # synthetic-duration default. Its private object store remains in memory.
    from test_selected_visual_checkpoint import REAL_DURATION
    monkeypatch.setattr(checkpoint, '_duration', REAL_DURATION)
    actual_seconds = REAL_DURATION(saved.audio)
    voice = saved.args['voice_result']
    voice.update(duration_before_fit=actual_seconds, duration_after_fit=actual_seconds,
                 content_target_seconds=actual_seconds, scene_durations=[actual_seconds / 6] * 6)
    for pool in saved.args['scene_visuals']:
        pool[0]['path'] = str(video)
    pointer = checkpoint.persist_selected_visual_checkpoint(PARENT, saved.work, **saved.args)
    manifest = json.loads(saved.client.objects[pointer['manifest_key']][0])
    media = {'version': 6, 'repair_only': True, 'source_task_id': PARENT,
             'package_sha256': pointer['package_sha256'], 'selected_checkpoint': pointer,
             'repair_scene_indices': [3, 4, 5]}
    voice_contract = {key: deepcopy(value) for key, value in media.items()
                      if key not in {'repair_only', 'repair_scene_indices'}}
    work = saved.work.parent / f'{CHILD}_attempt_0'
    work.mkdir()
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    ns = {'FinalVisualQualityError': VisualError, 'FinalAudioQualityError': AudioError,
          'Path': Path, 'hashlib': hashlib, 'json': json, 'math': math, 're': re,
          'deepcopy': deepcopy, 'ThreadPoolExecutor': ThreadPoolExecutor, 'SpendBlocked': SpendBlocked,
          'update_job': Mock(), 'set_stage': Mock(),
          '_synthesize_voice_candidate': Mock(side_effect=_forbidden),
          '_fit_saved_voice_for_retry': Mock(side_effect=_forbidden),
          '_repair_voice_internal_pauses': Mock(side_effect=_forbidden),
          '_collect_broll': Mock(side_effect=_forbidden),
          '_retry_bad_scene': Mock(side_effect=_forbidden),
          'research_and_script': Mock(side_effect=_forbidden),
          'direct_and_qc': Mock(side_effect=_forbidden),
          'generate_scene': Mock(side_effect=_forbidden),
          'short_story_package_is_approved': Mock(return_value=True)}
    helpers = {'_validated_recovered_generated_media', '_validated_recovered_voice',
               '_recovery_package_sha256', '_prepare_package', '_require_recovered_media_coverage',
               '_validate_paid_create_allocation', '_preflight_production_shorts_paid_plan',
               '_generated_visual_spec', '_apply_visual_review', '_visual_path',
               '_reserve_paid_create_slot', '_runway_generation_seconds',
               '_manual_qa_preview_passes', '_short_preview_voice_duration_qc', '_effective_short_edit_target',
               '_selected_recovery_authorization', '_prepare_selected_worker_recovery',
               '_selected_review_verdict', '_review_selected_exact'}
    constants = {'_RECOVERED_MEDIA_SOURCE_PATTERN', '_RECOVERED_MEDIA_KEY_PATTERN',
                 '_RECOVERED_MEDIA_PROVIDERS', '_REPAIR_RECOVERED_MEDIA_PROVIDERS',
                 '_MAX_RECOVERED_VIDEO_BYTES', '_MAX_RECOVERED_AUDIO_BYTES',
                 '_RECOVERED_VOICE_KEY_PATTERN', '_SHA256_PATTERN',
                 '_MANUAL_QA_CLEAR_VISUAL_FIELDS', 'MANUAL_QA_PUBLISH_QUALITY_THRESHOLD'}
    _execute([node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in helpers
              or isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in constants
                                                    for target in node.targets)], ns)
    return SimpleNamespace(saved=saved, pointer=pointer, manifest=manifest, media=media,
                           voice=voice_contract, work=work, tree=tree, ns=ns,
                           recovery=recovery, redis=fakeredis.FakeRedis(decode_responses=True))


def _load(case):
    return case.recovery.load_selected_recovery(case.media, case.voice, CHILD, case.work,
                                                expected_binding=case.saved.args['binding'])


def test_real_worker_validators_share_v6_pointer_and_exact_original_package(case):
    package = deepcopy(case.manifest['package'])
    package.update(_recovered_generated_media=deepcopy(case.media), _recovered_voice=deepcopy(case.voice))
    original = deepcopy(package)
    result = case.ns['_prepare_package'](None, CHILD, 'Original topic', .5, 'tr',
                                         {'mode': 'production', 'format': 'shorts', 'workflow': 'scene_repair'}, package)
    assert result == original and package == original
    package_sha = case.ns['_recovery_package_sha256'](result)
    assert package_sha == case.pointer['package_sha256']
    assert case.ns['_validated_recovered_generated_media'](case.media, 6, package_sha) == case.media
    assert case.ns['_validated_recovered_voice'](case.voice, 6, package_sha) == case.voice
    case.ns['research_and_script'].assert_not_called()
    case.ns['direct_and_qc'].assert_not_called()


def test_real_materialization_keeps_original_voice_and_stock_as_stock(case):
    loaded = _load(case)
    assert loaded['package'] == case.manifest['package']
    assert loaded['voice_result']['spoken_texts'] == case.manifest['voice']['spoken_texts']
    assert Path(loaded['voice_result']['path']).read_bytes() == case.saved.audio.read_bytes()
    assert loaded['voice_result']['scene_durations'] == case.manifest['voice']['scene_durations']
    assert loaded['timing'] == case.manifest['timing']
    assert loaded['retained_stock_indices'] == [0, 2]
    assert loaded['retained_generated_indices'] == [1]
    assert [index for index, pool in enumerate(loaded['scene_visuals']) if not pool] == [3, 4, 5]
    for index in (0, 1, 2):
        actual = loaded['scene_visuals'][index][0]
        assert actual['source_type'] == ('generated' if index == 1 else 'stock')
        assert actual['generated'] is (index == 1)
        assert actual['curated_pinned'] is actual['selected_recovery_pinned'] is True
        assert actual['start_fraction'] == case.manifest['scenes'][index]['selection']['start_fraction']
    assert loaded['qa_approved'] is loaded['publish_eligible'] is loaded['reusable'] is False


def _good_review(index):
    return {'scene_index': index, 'score': 92, 'best_candidate_index': 0, 'best_start_fraction': .91,
            'evidence_gate_passed': True, 'editorial_gate_passed': True, 'identity_gate_passed': True,
            'subject_visible': True, 'spoken_action_visible': True, 'unexplained_reset': False,
            'prominent_readable_text_or_logo_visible': False, 'major_visual_artifact_visible': False,
            'effectively_static_or_frozen': False, 'substantially_repeats_adjacent_scene': False,
            'authored_identity_or_material_conflict_visible': False}


@pytest.fixture
def verdict():
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    ns = {'FinalVisualQualityError': VisualError, 'math': math}
    _execute([_assignment(tree, '_MANUAL_QA_CLEAR_VISUAL_FIELDS'),
              _function(tree, '_selected_review_verdict')], ns)
    return ns['_selected_review_verdict']


@pytest.mark.parametrize('field,value', [
    ('score', 85), ('evidence_gate_passed', False), ('editorial_gate_passed', False),
    ('identity_gate_passed', False), ('subject_visible', False), ('spoken_action_visible', False),
    ('unexplained_reset', True), ('prominent_readable_text_or_logo_visible', True),
    ('major_visual_artifact_visible', True), ('effectively_static_or_frozen', True),
    ('substantially_repeats_adjacent_scene', True), ('authored_identity_or_material_conflict_visible', True),
])
def test_actual_final_verdict_keeps_negative_gates_even_with_high_score(verdict, field, value):
    reviews = [_good_review(0), _good_review(1)]
    reviews[1][field] = value
    result = verdict({'reviews': reviews, 'missing_review_indices': []}, [1, 5], 86)
    assert result['selected_recovery_rejected_indices'] == [5]
    assert result['reviews'][1][field] == value
    assert [row['scene_index'] for row in result['reviews']] == [1, 5]


@pytest.mark.parametrize('damage', ['duplicate', 'missing', 'global_index', 'string_index',
                                   'boolean_index', 'nan', 'boolean_score', 'wrong_candidate',
                                   'missing_gate', 'truthy_gate', 'missing_indices'])
def test_actual_selected_verdict_rejects_incomplete_or_malformed_review(verdict, damage):
    result = {'reviews': [_good_review(0), _good_review(1)], 'missing_review_indices': []}
    if damage == 'duplicate': result['reviews'][1]['scene_index'] = 0
    if damage == 'missing': result['reviews'].pop()
    if damage == 'global_index': result['reviews'][1]['scene_index'] = 5
    if damage == 'string_index': result['reviews'][1]['scene_index'] = '1'
    if damage == 'boolean_index': result['reviews'][1]['scene_index'] = True
    if damage == 'nan': result['reviews'][1]['score'] = float('nan')
    if damage == 'boolean_score': result['reviews'][1]['score'] = True
    if damage == 'wrong_candidate': result['reviews'][1]['best_candidate_index'] = 1
    if damage == 'missing_gate': result['reviews'][1].pop('identity_gate_passed')
    if damage == 'truthy_gate': result['reviews'][1]['identity_gate_passed'] = 1
    if damage == 'missing_indices': result['missing_review_indices'] = [1]
    with pytest.raises(VisualError):
        verdict(result, [1, 5], 86)


def _story_scope(case, monkeypatch, *, mutate=None):
    options = {'mode': 'production', 'format': 'shorts', 'music': 'off', 'quality_threshold': 86}
    spec = {**options, 'topic': 'Original source story', 'duration_minutes': .5, 'language': 'tr',
            'channel_id': 'original-channel'}
    approved = {**deepcopy(case.manifest['package']), '_recovered_generated_media': case.media,
                '_recovered_voice': case.voice}
    authorization = Mock(return_value={'binding': deepcopy(case.saved.args['binding'])})
    monkeypatch.setitem(sys.modules, 'app.services.selected_visual_recovery_state',
                        SimpleNamespace(verify_selected_recovery_child=authorization))

    def review(package, topic, duration, language, passed_options, **kwargs):
        assert package == case.manifest['package'] and topic == spec['topic'] and duration == .5
        assert language == 'tr' and passed_options == options
        assert kwargs['immutable_candidate_narrations'] == [scene['narration'] for scene in package['scenes']]
        assert kwargs['immutable_scene_fields'] is True
        if mutate is not None:
            mutate(package)
        return package

    reviewer = Mock(side_effect=review)
    monkeypatch.setitem(sys.modules, 'app.services.director',
                        SimpleNamespace(revalidate_immutable_short_story=reviewer))
    case.ns['settings'] = SimpleNamespace(studio_spend_enforcement=True)
    package = deepcopy(case.manifest['package'])
    loaded = case.ns['_prepare_selected_worker_recovery'](
        CHILD, PARENT, spec, approved, package, case.media, case.voice, case.work)
    assert package == case.manifest['package']
    authorization.assert_called_once_with(CHILD, PARENT, spec, approved)
    return loaded, reviewer


def test_real_worker_story_revalidation_is_detached_and_never_replaces_original(case, monkeypatch):
    loaded, reviewer = _story_scope(case, monkeypatch)
    assert loaded['package'] == case.manifest['package'] and loaded['story_revalidated'] is True
    reviewer.assert_called_once()
    case.ns['_synthesize_voice_candidate'].assert_not_called()
    case.ns['generate_scene'].assert_not_called()


@pytest.mark.parametrize('field', ['narration', 'ai_prompt', 'transition'])
def test_revalidation_cannot_change_original_scene_or_narration(case, monkeypatch, field):
    before = deepcopy(case.manifest['package'])
    with pytest.raises(VisualError, match='immutable story or asset verification'):
        _story_scope(case, monkeypatch, mutate=lambda package: package['scenes'][0].update({field: 'Changed'}))
    assert case.manifest['package'] == before
    case.ns['_synthesize_voice_candidate'].assert_not_called()
    case.ns['generate_scene'].assert_not_called()


def _worker(case, loaded):
    ns = dict(case.ns)
    ns.update(
        selected_recovery=loaded, recovered_generated_media=case.media,
        recovered_voice=case.voice, scene_repair_recovery=True,
        recovery_repair_scene_indices=set(case.media['repair_scene_indices']),
        recovery_paid_scene_indices=set(case.media['repair_scene_indices']),
        scene_visuals=deepcopy(loaded['scene_visuals']), scenes=deepcopy(loaded['package']['scenes']),
        package=deepcopy(loaded['package']), voice_result=deepcopy(loaded['voice_result']),
        scene_durations=list(loaded['voice_result']['scene_durations']),
        task_id=CHILD, retry_dispatch_source_id=PARENT, topic='Original source story',
        duration_minutes=.5, language='tr', channel_id='original-channel', work=case.work,
        options={'mode': 'production', 'format': 'shorts', 'music': 'off', 'quality_threshold': 86},
        approved_package={**deepcopy(loaded['package']), '_recovered_generated_media': case.media,
                          '_recovered_voice': case.voice},
        _task_spec=lambda topic, duration, language, channel, options: dict(
            topic=topic, duration_minutes=duration, language=language, channel_id=channel, **options),
        _selected_recovery_authorization=Mock(return_value={'binding': deepcopy(case.saved.args['binding'])}),
        current_reviews={i: _good_review(i) for i in range(3)},
        prompt_candidates={i: f'Original repair direction {i}' for i in case.media['repair_scene_indices']},
        ranked_runway_candidates=[{'scene_index': i} for i in range(6)],
        runway_attempts=0, total_paid_create_cap=len(case.media['repair_scene_indices']),
        runway_submission_cap=6, runway_required_submission_cap=6, runway_effective_submission_cap=6,
        quality_threshold=86, is_bounded_short_preview=False, is_private_ai_first_omni_preview=False,
        is_private_image_motion_preview=False, generation_aspect_ratio='9:16',
        generated_checkpoint_specs={}, runway_scenes_used=0, runway_generated_scenes=[],
        generated_video_provider_records=[], generated_asset_candidate_journal=[],
        runway_failed_scenes=[], runway_failure_diagnostics=[],
        omni_continuity_reference_image_path=None, omni_continuity_anchor_scene_idx=None,
        omni_unsafe_submission_scenes=set(), image_motion_submission_scenes=set(),
        _image_motion_prompt_for_scene=lambda *_args: 'unused image prompt',
        _checkpoint_generated_asset=Mock(), _runway_failure_diagnostic=lambda phase, index, error: {'scene_index': index},
        GeminiOmniContinuityReferenceError=type('ContinuityError', (Exception,), {}),
        GeminiImageAttemptedError=type('ImageError', (Exception,), {}),
        GeminiOmniTerminalError=type('TerminalError', (Exception,), {}),
    )
    selection = next(node for node in ast.walk(case.tree) if isinstance(node, ast.If)
                     and isinstance(node.test, ast.Name) and node.test.id == 'scene_repair_recovery'
                     and any(isinstance(child, ast.Name) and child.id == 'selected_runway'
                             for child in ast.walk(node)))
    _execute([selection], ns)
    ns['selected_runway_indices'] = {row['scene_index'] for row in ns['selected_runway']}
    return ns


@contextmanager
def _real_spending(case, ns, monkeypatch, *, fail_index=None):
    from app.services import production_spend_runtime as runtime, production_spend_quotes as quotes
    from app.services.production_spend import SpendLedger, SpendPolicy
    from app.services.production_scene_budget import scene_fields

    now = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)
    ledger = SpendLedger(case.redis, SpendPolicy(*([100_000_000] * 6)), clock=lambda: now)
    ledger.initialize()
    policy = funding_policy(ledger)
    account = next(row for row in policy['accounts'] if row['provider'] == 'runway')
    account['mode'] = 'covered_only'
    account['funding'] = {'covered_list_allowance_micro': 100_000_000,
                          'coverage_basis': 'verified_route_list_cost_usd', 'no_auto_overage': True}
    ledger.initialize_funding(policy)
    monkeypatch.setattr(runtime, 'configured_ledger', lambda: ledger)
    monkeypatch.setattr(runtime, 'settings', SimpleNamespace(studio_spend_enforcement=True))
    monkeypatch.setattr(quotes, '_fresh', lambda: None)
    case.redis.sadd(runtime._CHANNEL_INDEX, CHANNEL)
    case.redis.set(runtime._CHANNEL_PREFIX + CHANNEL, json.dumps({'id': CHANNEL, 'connection_id': 'connection_AAAAA'}))
    for task, parent in ((ROOT, None), (PARENT, ROOT), (CHILD, PARENT)):
        case.redis.set(runtime._JOB_PREFIX + task, json.dumps({'task_id': task, 'parent_id': parent,
            'spec': {'production_channel_id': CHANNEL, 'production_connection_id': 'connection_AAAAA',
                     'duration_minutes': .5}}))
    task_token = runtime._TASK_ID.set(CHILD)
    scene_token = runtime._SCENE.set(None)
    prepared = runtime.prepare_video_scene_budget(case.pointer['package_sha256'], ns['scene_durations'], '9:16', scene_count=6)
    sdk = fake_sdk_client('https://api.dev.runwayml.com')
    events = []
    slots_key = 'offline_slots:' + CHILD
    case.redis.hset(slots_key, mapping={'cap': ns['total_paid_create_cap'], 'used': 0})

    def slots(task, cap, *, reserve=False):
        assert task == CHILD and reserve is True and cap == ns['total_paid_create_cap']
        used = int(case.redis.hget(slots_key, 'used'))
        if used >= cap:
            raise RuntimeError('Offline legacy cap exhausted')
        events.append(('slot', used + 1))
        return case.redis.hincrby(slots_key, 'used', 1)

    def transport(**kwargs):
        index = int(kwargs['prompt_text'].rsplit(' ', 1)[1])
        assert runtime._SCENE.get()['lineage_id'] == ROOT
        assert runtime._SCENE.get()['scene_index'] == index
        assert ledger.snapshot()['period']['used_micro'] > 0
        assert case.redis.hexists(LEDGER_KEY, scene_fields(ROOT)[0])
        events.append(('create', index))
        if index == fail_index:
            raise RuntimeError('Offline accepted request failed')
        return {'id': 'offline-video'}

    sdk.text_to_video.create.side_effect = transport

    def generate(prompt, **kwargs):
        assert kwargs['allow_image_motion'] is kwargs['allow_paid_terminal_resubmit'] is False
        runtime.paid_runway_create(sdk, model='gen4.5', prompt_text=prompt,
                                   ratio='720:1280', duration=kwargs['duration'])
        return {'provider': 'runway', 'provider_attempts': 1}

    ns.update(video_scene_budget=prepared, spending_scene=runtime.spending_scene,
              _persisted_paid_create_slots=slots, generate_scene=generate,
              download_generated_scene=lambda generated, output: output.write_bytes(
                  case.saved.client.objects[case.manifest['scenes'][0]['asset']['key']][0]))
    _execute([_function(case.tree, '_reserve_paid_create_slot')], ns)
    try:
        yield SimpleNamespace(ledger=ledger, sdk=sdk, events=events, slots_key=slots_key)
    finally:
        runtime._SCENE.reset(scene_token)
        runtime._TASK_ID.reset(task_token)


def _primary(case, ns):
    loop = next(node for node in ast.walk(case.tree) if isinstance(node, ast.For)
                and isinstance(node.iter, ast.Name) and node.iter.id == 'selected_runway')
    _execute([loop], ns)


@pytest.mark.parametrize('fail_index', [None, 4])
def test_real_primary_loop_creates_only_original_rejects_under_same_root_scene_budget(
        case, monkeypatch, installed_sdk_modules, fail_index):
    loaded = _load(case)
    ns = _worker(case, loaded)
    before = deepcopy(loaded)
    assert [row['scene_index'] for row in ns['selected_runway']] == [3, 4, 5]
    ns['_require_recovered_media_coverage'](case.media, [3, 4, 5])
    with _real_spending(case, ns, monkeypatch, fail_index=fail_index) as spend:
        _primary(case, ns)
        assert [index for kind, index in spend.events if kind == 'create'] == [3, 4, 5]
        assert spend.sdk.text_to_video.create.call_count == 3
        assert int(case.redis.hget(spend.slots_key, 'used')) == 3
        assert spend.ledger.funding_snapshot()['cash_reserved_micro'] == 0
        assert spend.ledger.snapshot()['period']['used_micro'] > 0
    assert ns['scene_visuals'][:3] == before['scene_visuals'][:3]
    assert ns['voice_result'] == before['voice_result'] and ns['package'] == before['package']
    assert loaded == before and ns['runway_attempts'] == 3
    assert ns['runway_failed_scenes'] == ([] if fail_index is None else [fail_index])
    assert all(len(ns['scene_visuals'][index]) == (0 if index == fail_index else 1) for index in (3, 4, 5))
    for name in ('_synthesize_voice_candidate', '_collect_broll', '_retry_bad_scene'):
        ns[name].assert_not_called()


@pytest.mark.parametrize('indices', [[0, 3, 4, 5], [3, 4], [], list(range(6))])
def test_worker_coverage_cannot_add_or_omit_original_repair_slot(case, indices):
    with pytest.raises(VisualError):
        case.ns['_require_recovered_media_coverage'](case.media, indices)
    case.ns['generate_scene'].assert_not_called()


def test_v6_allocation_counts_rejects_even_if_all_six_are_presented_for_preflight(case):
    rows = [{'scene_index': index} for index in range(6)]
    case.ns['_validate_paid_create_allocation'](rows, case.media, 6, paid_slots_used=3)
    with pytest.raises(VisualError):
        case.ns['_validate_paid_create_allocation'](rows, case.media, 6, paid_slots_used=4)
    case.ns['generate_scene'].assert_not_called()


def _repersist(case, *, rejected=None):
    from app.services import selected_visual_checkpoint as checkpoint
    if rejected is not None:
        case.saved.args['rejected_scene_indices'] = list(rejected)
        for index, review in case.saved.args['final_reviews'].items():
            review['score'] = 40 if index in rejected else 92
    case.pointer = checkpoint.persist_selected_visual_checkpoint(PARENT, case.saved.work, **case.saved.args)
    case.manifest = json.loads(case.saved.client.objects[case.pointer['manifest_key']][0])
    for contract in (case.media, case.voice):
        contract['package_sha256'] = case.pointer['package_sha256']
        contract['selected_checkpoint'] = deepcopy(case.pointer)
    case.media['repair_scene_indices'] = list(case.saved.args['rejected_scene_indices'])


def test_zero_accepted_all_six_repairs_remain_bounded_without_retained_critic(
        case, monkeypatch, installed_sdk_modules):
    _repersist(case, rejected=list(range(6)))
    loaded = _load(case)
    case.ns['review_scene_visuals'] = Mock(side_effect=_forbidden)
    reviewed = case.ns['_review_selected_exact'](loaded, loaded['package']['scenes'], case.work,
        'Original story', {'quality_threshold': 86}, [])
    assert reviewed['reviews'] == []
    case.ns['review_scene_visuals'].assert_not_called()
    ns = _worker(case, loaded)
    with _real_spending(case, ns, monkeypatch) as spend:
        _primary(case, ns)
        assert [index for kind, index in spend.events if kind == 'create'] == list(range(6))
        assert spend.sdk.text_to_video.create.call_count == 6
    assert ns['runway_attempts'] == 6 and all(len(pool) == 1 for pool in ns['scene_visuals'])


def test_empty_repair_list_is_not_new_generation_authority(case):
    case.media['repair_scene_indices'] = []
    with pytest.raises(VisualError):
        case.ns['_validated_recovered_generated_media'](case.media, 6, case.pointer['package_sha256'])
    case.ns['generate_scene'].assert_not_called()


def _critic(case, *, bad_index=None):
    calls = []
    def review(scenes, pools, work, count, **kwargs):
        assert kwargs['_missing_review_attempts'] == kwargs['_score_reason_consistency_attempts'] == 0
        assert kwargs['story_scenes'] == case.manifest['package']['scenes']
        assert count == len(pools) == len(scenes)
        assert all(pool[0]['selected_recovery_qa_only'] is True for pool in pools)
        assert all(Path(pool[0]['path']).exists() for pool in pools)
        assert all('/selected_exact_qa_' in pool[0]['path'] for pool in pools)
        calls.append(deepcopy(pools))
        reviews = [_good_review(i) for i in range(count)]
        if bad_index is not None:
            reviews[bad_index]['identity_gate_passed'] = False
        return {'reviews': reviews, 'missing_review_indices': []}
    case.ns['review_scene_visuals'] = Mock(side_effect=review)
    return calls


def test_actual_retained_exact_cut_rejection_prevents_every_new_scene(case, monkeypatch, installed_sdk_modules):
    loaded = _load(case)
    ns = _worker(case, loaded)
    before = deepcopy(loaded)
    _critic(case, bad_index=1)
    with _real_spending(case, ns, monkeypatch) as spend:
        with pytest.raises(VisualError, match='Selected exact-cut quality gate failed'):
            case.ns['_review_selected_exact'](loaded, ns['scenes'], case.work,
                ns['topic'], ns['options'], [])
            _primary(case, ns)
        spend.sdk.text_to_video.create.assert_not_called()
        assert spend.ledger.snapshot()['period']['used_micro'] == 0
        assert int(case.redis.hget(spend.slots_key, 'used')) == 0
    assert loaded == before


@pytest.mark.parametrize('bad_index', [None, 4])
def test_real_final_exact_cut_review_keeps_raw_render_inputs_and_negative_gates(
        case, monkeypatch, installed_sdk_modules, bad_index):
    loaded = _load(case)
    ns = _worker(case, loaded)
    with _real_spending(case, ns, monkeypatch):
        _primary(case, ns)
    before = deepcopy(ns['scene_visuals'])
    calls = _critic(case, bad_index=bad_index)
    branch = next(node for node in ast.walk(case.tree) if isinstance(node, ast.If)
                  and ast.unparse(node.test) == 'selected_recovery is not None'
                  and any(isinstance(child, ast.Assign)
                          and any(isinstance(target, ast.Name) and target.id == 'final_visual_qc'
                                  for target in child.targets) for child in node.body))
    _execute([branch], ns)
    result = ns['final_visual_qc']
    assert len(calls) == 1 and len(calls[0]) == 6
    rejected = [] if bad_index is None else [bad_index]
    assert result['selected_recovery_rejected_indices'] == rejected
    assert result['reviews'][4]['score'] == 92
    assert result['reviews'][4]['identity_gate_passed'] is (bad_index is None)
    assert ns['scene_visuals'] == before
    assert all(not pool[0].get('selected_recovery_qa_only') for pool in ns['scene_visuals'])
    case.recovery.verify_selected_final_inputs(loaded, case.work, ns['scene_visuals'],
                                               result['selected_recovery_qa_inputs'])
    # A proof that verifies byte identity still does not override semantic QA.
    ns.update(final_visual_qc=result, final_reviews={row['scene_index']: row for row in result['reviews']},
              manual_qa_preview_scenes=set(), manual_qa_preserve_exact_cut_scenes=set(),
              final_manual_reviews_applied=set(), voice_path=ns['voice_result']['path'],
              final_audio_path=ns['voice_result']['path'],
              render_target_duration=loaded['timing']['target_frames'] / 30)
    _execute([_assignment(case.tree, 'rejected_final_scenes')], ns)
    assert ns['rejected_final_scenes'] == rejected
    # A reviewer sees an already normalized QA copy. Its preferred fractional
    # position must never replace the frozen raw stock selection before render.
    assert before[0][0]['preserve_start_fraction'] is False
    assert before[0][0]['start_fraction'] != result['reviews'][0]['best_start_fraction']
    accepted_review_loop = next(node for node in ast.walk(case.tree) if isinstance(node, ast.For)
        and ast.unparse(node.iter) == 'final_reviews.items()'
        and any(isinstance(child, ast.Name) and child.id == 'manual_qa_preserve_exact_cut_scenes'
                for child in ast.walk(node)))
    _execute([accepted_review_loop], ns)
    assert ns['scene_visuals'] == before
    render_guard = next(node for node in ast.walk(case.tree) if isinstance(node, ast.If)
                        and ast.unparse(node.test) == 'selected_recovery is not None'
                        and any(isinstance(child, ast.Constant) and child.value ==
                                'Selected recovery render inputs changed after review' for child in ast.walk(node)))
    if rejected:
        with pytest.raises(VisualError, match='render inputs changed after review'):
            _execute([render_guard], ns)
    else:
        _execute([render_guard], ns)
        assert ns['scene_visuals'] == before
        for field, value in [('render_target_duration', 30.0), ('final_audio_path', 'another.mp3')]:
            old = ns[field]
            ns[field] = value
            with pytest.raises(VisualError, match='render inputs changed after review'):
                _execute([render_guard], ns)
            ns[field] = old
        original_proof = result['selected_recovery_qa_inputs']
        result['selected_recovery_qa_inputs'] = dict(original_proof)
        with pytest.raises(VisualError, match='exact render identity changed after review'):
            _execute([render_guard], ns)
        result['selected_recovery_qa_inputs'] = original_proof


def test_actual_voice_stage_uses_materialized_audio_and_never_synthesizes_or_searches(case):
    loaded = _load(case)
    ns = _worker(case, loaded)
    ns.update(saved_voice_retry=None, voice_replacement_request=None, curated_source_job=None,
              curated_stock_manifest=None, strict_short_preview_duration=False, pexels_orientation='portrait',
              _checkpoint_audio_candidate=Mock(), _download_recovered_voice_candidate=Mock(side_effect=_forbidden))
    stage = next(node for node in ast.walk(case.tree) if isinstance(node, ast.With)
                 and any(isinstance(item.optional_vars, ast.Name) and item.optional_vars.id == 'stage_pool'
                         for item in node.items))
    _execute([stage], ns)
    assert ns['voice_result'] == loaded['voice_result'] and ns['voice_result'] is not loaded['voice_result']
    assert ns['broll_result']['scene_visuals'] == loaded['scene_visuals']
    assert ns['broll_result']['seen_ids'] == {100, 102}
    for name in ('_synthesize_voice_candidate', '_fit_saved_voice_for_retry',
                 '_download_recovered_voice_candidate', '_collect_broll'):
        ns[name].assert_not_called()


@pytest.mark.parametrize('failed_gate', ['transcript', 'duration', 'prosody'])
def test_real_audio_loop_rejects_each_gate_without_regeneration_or_pause_surgery(case, failed_gate):
    loaded = _load(case)
    ns = _worker(case, loaded)
    voice_path = ns['voice_result']['path']
    good = {'available': True, 'pass': True}
    results = {name: dict(good, **({'pass': False, 'retryable': True} if name == failed_gate else {}))
               for name in ('transcript', 'duration', 'prosody')}
    ns.update(voice_path=voice_path, expected_spoken_narration=' '.join(ns['voice_result']['spoken_texts']),
              saved_voice_retry=None, short_form_prosody_required=True, audio_generation_attempts=1,
              audio_pause_repair_attempted=False, audio_qc_retry_history=[], audio_synthesis_quality_errors=[],
              MAX_AUDIO_GENERATION_ATTEMPTS=3, selected_audio_generation_attempt=0,
              _verify_audio_narration_with_retry=Mock(return_value=results['transcript']),
              _short_preview_voice_duration_qc=Mock(return_value=results['duration']),
              verify_audio_prosody=Mock(return_value=results['prosody']), _audio_qc_failure_evidence=Mock(return_value={}))
    loop = next(node for node in ast.walk(case.tree) if isinstance(node, ast.While)
                and any(isinstance(child, ast.Name) and child.id == 'can_regenerate' for child in ast.walk(node)))
    with pytest.raises(AudioError, match='Audio narration QA rejected before paid media'):
        _execute([loop], ns)
    assert ns['can_regenerate'] is False and len(ns['audio_qc_retry_history']) == 1
    assert ns['voice_result'] == loaded['voice_result']
    for name in ('_synthesize_voice_candidate', '_repair_voice_internal_pauses', 'generate_scene'):
        ns[name].assert_not_called()


def test_final_failure_has_no_extra_paid_repair_or_stock_rescue(case):
    ns = _worker(case, _load(case))
    ns.update(preview_runway_repair_indices=Mock(side_effect=_forbidden), rejected_final_scenes=[0, 3, 4, 5])
    _execute([_assignment(case.tree, 'final_runway_repair_candidates')], ns)
    assert ns['final_runway_repair_candidates'] == []
    rescue = next(node for node in ast.walk(case.tree) if isinstance(node, ast.For)
                  and isinstance(node.iter, ast.Name) and node.iter.id == 'rejected_final_scenes'
                  and any(isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                          and child.func.id == '_retry_bad_scene' for child in ast.walk(node)))
    _execute([rescue], ns)
    ns['_retry_bad_scene'].assert_not_called()
    ns['generate_scene'].assert_not_called()


def test_rejected_original_stock_composes_one_bounded_prompt_without_rewriting_package(case, monkeypatch):
    case.saved.args['package']['scenes'][4]['ai_prompt'] = None
    _repersist(case)
    composed = 'A side view of the original mechanism performing the narrated action.'
    composer = Mock(return_value=composed)
    case.ns['_runway_prompt_for_scene'] = composer
    before = deepcopy(case.manifest['package'])
    loaded, _ = _story_scope(case, monkeypatch)
    assert loaded['package'] == before
    assert loaded['package']['scenes'][4]['ai_prompt'] is None
    assert loaded['repair_prompts'][4] == composed
    assert loaded['repair_prompts'][3] == before['scenes'][3]['ai_prompt']
    assert loaded['repair_prompts'][5] == before['scenes'][5]['ai_prompt']
    composer.assert_called_once()
    assert composer.call_args.args[0] == before['scenes'][4]
    assert composer.call_args.args[1]['score'] == case.manifest['scenes'][4]['qa_observation']['score']
    assert composer.call_args.args[2] == '9:16'


@pytest.mark.parametrize('authored', [True, False])
def test_overlong_authored_or_composed_shot_blocks_before_review_or_create(case, monkeypatch, authored):
    case.saved.args['package']['scenes'][4]['ai_prompt'] = 'x' * 1001 if authored else None
    _repersist(case)
    case.ns['_runway_prompt_for_scene'] = Mock(return_value='x' * 1001)
    with pytest.raises(VisualError, match='immutable story or asset verification'):
        _story_scope(case, monkeypatch)
    case.ns['generate_scene'].assert_not_called()


@pytest.mark.parametrize('change', ['package', 'voice', 'selected_index'])
def test_changed_worker_inputs_abort_before_first_budget_slot(case, monkeypatch, installed_sdk_modules, change):
    loaded = _load(case)
    ns = _worker(case, loaded)
    if change == 'package': ns['package']['scenes'][0]['narration'] = 'Changed speech.'
    if change == 'voice': ns['voice_result']['spoken_texts'][0] = 'Changed speech.'
    if change == 'selected_index': ns['selected_runway'][0]['scene_index'] = 0
    with _real_spending(case, ns, monkeypatch) as spend:
        with pytest.raises(VisualError, match='changed before a new scene request'):
            _primary(case, ns)
        assert spend.events == []
        assert spend.ledger.snapshot()['period']['used_micro'] == 0
        assert int(case.redis.hget(spend.slots_key, 'used')) == 0
        spend.sdk.text_to_video.create.assert_not_called()
