"""Execute real scene loops, checkpoint hook and worker failure/finally paths.

The bounded source slices omit planning, rendering and quality decisions. Their
provider and object-store boundaries are offline fakes; studio persistence and
the durable paid-create counter are real, against the fixture Redis instance.
Unlike a synthetic throwing task body, each loop completes one scene, then
receives a budget denial while another scene and a critic remain pending.
"""
import ast
from contextlib import nullcontext
from copy import deepcopy
import json
import math
from pathlib import Path
import re
import shutil
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import studio_state
from app.services.abacus_generation import AbacusConfigurationError, AbacusGenerationError
from app.services.production_spend import LEDGER_KEY, SpendBlocked
from test_channel_production import production


SOURCE = Path(__file__).resolve().parents[1] / 'app/tasks.py'
TREE = ast.parse(SOURCE.read_text(encoding='utf-8'))
PIPELINE = next(node for node in TREE.body
                if isinstance(node, ast.FunctionDef) and node.name == 'run_video_pipeline')
OUTER = next(node for node in PIPELINE.body if isinstance(node, ast.Try))
TASK_ID = '11111111-1111-4111-8111-111111111111'
ERRORS = {name: type(name, (RuntimeError,), {}) for name in (
    'FinalVisualQualityError', 'FinalAudioQualityError', 'GeminiOmniContinuityReferenceError',
    'GeminiImageAttemptedError', 'GeminiOmniTerminalError',
    'ImmutableNarrationSceneBudgetError', 'UnsupportedLanguageError',
)}


def _assigned(node, name):
    return isinstance(node, ast.Assign) and any(
        isinstance(target, ast.Name) and target.id == name for target in node.targets)


def _worker(case, phase):
    """Keep both real try bodies/handlers intact, including their surrounding loop."""
    iterator = 'selected_runway' if phase == 'initial_generation' else 'final_runway_repair_candidates'
    loop = next(node for node in OUTER.body
                if isinstance(node, ast.For) and isinstance(node.iter, ast.Name)
                and node.iter.id == iterator)
    if phase == 'initial_generation':
        critic = next(node for node in OUTER.body if _assigned(node, 'final_visual_qc'))
        # The real final-review input assignment; curated review is out of this slice.
        between = next(node for node in OUTER.body if _assigned(node, 'final_review_visuals'))
        body = [loop, between, critic]
    else:
        rescued = next(node for node in OUTER.body
                       if isinstance(node, ast.AnnAssign)
                       and isinstance(node.target, ast.Name) and node.target.id == 'rescued_final_scenes')
        rescue = next(node for node in OUTER.body
                      if isinstance(node, ast.If) and isinstance(node.test, ast.Name)
                      and node.test.id == 'rescued_final_scenes')
        critic = next(node for node in rescue.body if _assigned(node, 'rescue_qc'))
        body = [loop, rescued, critic]
    function = ast.parse('def scene_worker(self):\n    pass\n').body[0]
    local_state = ast.parse('''
runway_attempts = 0
runway_scenes_used = 0
final_runway_repair_attempts = 0
omni_continuity_reference_image_path = None
omni_continuity_anchor_scene_idx = None
''').body
    block = deepcopy(OUTER)
    block.body = deepcopy(body)
    function.body = local_state + [block]
    helpers = [node for node in TREE.body if isinstance(node, ast.FunctionDef) and node.name in {
        '_persisted_paid_create_budget', '_persisted_paid_create_slots', '_reserve_paid_create_slot',
        '_checkpoint_generated_asset', '_generated_visual_spec', '_visual_path', '_runway_generation_seconds',
    }]
    exec(compile(ast.fix_missing_locations(ast.Module(body=helpers + [function], type_ignores=[])),
                 str(SOURCE), 'exec'), case.ns)
    return case.ns['scene_worker']


@pytest.fixture
def case(production, monkeypatch, tmp_path):
    module, client = production
    # Some legacy tests unload studio_state during collection. Keep dynamic
    # worker imports on the same real module whose Redis boundary we patch.
    monkeypatch.setitem(sys.modules, 'app.services.studio_state', studio_state)
    monkeypatch.setattr(sys.modules['app.services'], 'studio_state', studio_state, raising=False)
    monkeypatch.setattr(studio_state, '_client', lambda: client)
    audio_checkpoint = {'audio_sha256': 'a' * 64, 'requires_full_qa': True}
    client.set(studio_state.JOB_PREFIX + TASK_ID, json.dumps({
        'task_id': TASK_ID, 'kind': 'render', 'state': 'PROGRESS', 'stage': 'ai_scene_generation',
        'audio_candidate_checkpoint': audio_checkpoint,
    }))
    hold_key = module.CHANNEL_STATE_PREFIX + 'UC_test_channel'
    hold = {'paused_reason': 'operator_review_required', 'cursor': '1',
            'consumed_prefix': 'same-existing-series', 'next_due': '86400'}
    client.hset(hold_key, mapping=hold)
    client.hset(LEDGER_KEY, mapping={'reserved': '600000', 'request:uncertain': 'retained'})
    before_ledger = client.hgetall(LEDGER_KEY)
    work, storage = tmp_path / 'work', tmp_path / 'private-storage'
    work.mkdir()
    storage.mkdir()
    def preserve(task_id, _work, **values):
        index, phase = values['scene_index'], values['phase']
        destination = storage / f'{phase}-{index}.mp4'
        shutil.copyfile(values['visual_spec']['path'], destination)
        return {'source_task_id': task_id, 'scene_index': index, 'phase': phase,
                'status': 'preserved_candidate', 'qa_approved': False,
                'reusable': False, 'requires_full_qa': True, 'key': str(destination)}
    persistence = Mock(side_effect=preserve)
    monkeypatch.setitem(sys.modules, 'app.services.generated_asset_checkpoint',
                        SimpleNamespace(persist_generated_asset_candidate=persistence))
    def download(_scene, path):
        path.write_bytes(b'offline-generated-clip')
    generated = {'provider': 'runway', 'provider_attempts': 1}
    provider, critic = Mock(return_value=generated), Mock(return_value={'reviews': []})
    namespace = dict(
        ERRORS, SpendBlocked=SpendBlocked, AbacusGenerationError=AbacusGenerationError,
        Path=Path, math=math, shutil=shutil,
        task_id=TASK_ID, work=work, package={'sources': []}, voice_result={'voice': 'unchanged'},
        options={'mode': 'production', 'format': 'shorts'}, duration_minutes=.5,
        paid_create_budget_state=studio_state.paid_create_budget_state,
        update_job=studio_state.update_job, mark_failure=Mock(wraps=studio_state.mark_failure),
        set_stage=Mock(wraps=studio_state.set_stage),
        total_paid_create_cap=6, retry_dispatch_source_id=None, full_rebuild_source_id=None,
        video_scene_budget=None, spending_scene=lambda *_args: nullcontext(),
        selected_runway=[{'scene_index': index} for index in range(3)],
        selected_runway_indices=set(range(3)), scenes=[{'narration': 'fixture'} for _ in range(3)],
        scene_visuals=[[{'path': f'stock-{index}.mp4'}] for index in range(3)],
        scene_durations=[5, 5, 5], prompt_candidates={index: f'scene-{index}' for index in range(3)},
        current_reviews={}, recovered_generated_media=None,
        is_private_ai_first_omni_preview=False, is_private_image_motion_preview=False,
        generation_aspect_ratio='9:16', image_motion_submission_scenes=set(),
        omni_unsafe_submission_scenes=set(), generated_checkpoint_specs={},
        generated_asset_candidate_journal=[], runway_generated_scenes=[],
        generated_video_provider_records=[], runway_failed_scenes=[], runway_failure_diagnostics=[],
        _image_motion_prompt_for_scene=Mock(return_value='image fixture'),
        generate_scene=provider, download_generated_scene=Mock(side_effect=download),
        _runway_failure_diagnostic=Mock(return_value={'stage': 'ordinary_failure'}),
        topic='fixture', review_scene_visuals=critic,
        final_runway_repair_candidates=list(range(3)), final_runway_repair_candidate_indices=set(range(3)),
        final_reviews={index: {'score': 20} for index in range(3)},
        terminal_manual_qa_old_best={}, _runway_prompt_for_scene=Mock(return_value='repair fixture'),
        final_runway_repair_scenes=[], final_runway_repair_failures=[], visual_replacements=[],
    )
    return SimpleNamespace(ns=namespace, client=client, provider=provider, generated=generated,
                           critic=critic, persistence=persistence, storage=storage, work=work,
                           audio=audio_checkpoint, hold_key=hold_key, hold=hold, ledger=before_ledger,
                           self=SimpleNamespace(request=SimpleNamespace(retries=0), max_retries=2,
                                                update_state=Mock()))


@pytest.mark.parametrize('phase', ['initial_generation', 'final_repair'])
@pytest.mark.parametrize('reason', [
    'spend_day_limit', 'spend_month_limit', 'spend_not_initialized',
    'spend_request_already_reserved', 'spend_store_unavailable', 'spend_request_not_priced',
])
def test_budget_denial_stops_remaining_scenes_and_critic_preserving_saved_assets(case, phase, reason):
    error = SpendBlocked(reason)
    case.provider.side_effect = [case.generated, error, case.generated]
    worker = _worker(case, phase)
    with pytest.raises(SpendBlocked) as caught:
        worker(case.self)
    assert caught.value is error
    assert case.provider.call_count == 2  # Scene 2 must never reach its paid boundary.
    assert case.ns['download_generated_scene'].call_count == 1
    case.critic.assert_not_called()
    case.ns['_runway_failure_diagnostic'].assert_not_called()
    assert case.ns['runway_failed_scenes'] == case.ns['final_runway_repair_failures'] == []
    case.ns['mark_failure'].assert_called_once_with(TASK_ID, error)
    case.ns['set_stage'].assert_not_called()
    job = studio_state.get_job(TASK_ID)
    assert (job['state'], job['stage'], job['error']) == ('FAILURE', 'failed', reason)
    assert job['audio_candidate_checkpoint'] == case.audio
    candidates = job['generated_asset_candidates']
    assert candidates['attempted_count'] == candidates['preserved_count'] == 1
    assert candidates['failed_count'] == 0
    assert candidates['qa_approved'] is False and candidates['reusable'] is False
    receipt = candidates['entries'][0]
    assert receipt['scene_index'] == 0 and receipt['phase'] == phase
    assert Path(receipt['key']).read_bytes() == b'offline-generated-clip'
    assert not case.work.exists()  # Real worker finally ran; private copy survives.
    assert set(case.ns['generated_checkpoint_specs']) == {0}
    assert studio_state.paid_create_budget_state(TASK_ID, 6)['used'] == 2
    assert case.client.hgetall(LEDGER_KEY) == case.ledger
    assert case.client.hgetall(case.hold_key) == case.hold


@pytest.mark.parametrize('phase', ['initial_generation', 'final_repair'])
def test_ordinary_scene_failure_still_uses_existing_bounded_continuation(case, phase):
    case.provider.side_effect = [case.generated, RuntimeError('ordinary provider failure'), case.generated]
    _worker(case, phase)(case.self)
    assert case.provider.call_count == 3
    assert case.persistence.call_count == 2
    case.critic.assert_called_once()
    case.ns['_runway_failure_diagnostic'].assert_called_once()
    failures = case.ns['runway_failed_scenes' if phase == 'initial_generation' else 'final_runway_repair_failures']
    assert failures == [1]
    case.ns['mark_failure'].assert_not_called()
    assert studio_state.paid_create_budget_state(TASK_ID, 6)['used'] == 3


@pytest.mark.parametrize('phase', ['initial_generation', 'final_repair'])
def test_existing_continuity_terminal_error_still_stops_the_loop(case, phase):
    error = ERRORS['GeminiOmniContinuityReferenceError']('continuity unavailable')
    case.provider.side_effect = [case.generated, error, case.generated]
    with pytest.raises(ERRORS['GeminiOmniContinuityReferenceError']) as caught:
        _worker(case, phase)(case.self)
    assert caught.value is error
    assert case.provider.call_count == 2
    case.critic.assert_not_called()
    case.ns['mark_failure'].assert_called_once_with(TASK_ID, error)


@pytest.mark.parametrize('error_type,reason', [
    (SpendBlocked, 'spend_day_limit'),
    (SpendBlocked, 'spend_request_already_reserved'),
    (AbacusGenerationError, 'abacus_output_invalid'),
    (AbacusConfigurationError, 'abacus_configuration_invalid'),
])
def test_actual_shot_wrapper_preserves_terminal_reason_and_compression_reservation(case, monkeypatch, error_type, reason):
    function = next(node for node in TREE.body
                    if isinstance(node, ast.FunctionDef) and node.name == '_prepare_scheduled_short_shots')
    error = error_type(reason)
    def compress(*_args, before_compression, **_kwargs):
        before_compression()
        raise error
    ensure = Mock(side_effect=compress)
    monkeypatch.setitem(sys.modules, 'app.services.director',
                        SimpleNamespace(ensure_scheduled_short_shot_prompts=ensure))
    namespace = dict(case.ns, json=json,
                     _RECOVERED_MEDIA_SOURCE_PATTERN=re.compile(r'^[0-9a-f-]{36}$'),
                     _recovery_package_sha256=Mock(return_value='a' * 64))
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), 'exec'), namespace)
    with pytest.raises(error_type) as caught:
        namespace['_prepare_scheduled_short_shots'](
            TASK_ID, case.ns['package'], 'fixture', .5, 'tr', case.ns['options'])
    assert caught.value is error
    ensure.assert_called_once()
    reservation = case.client.get('youtube_studio:shot_prompt_compression:v1:' + TASK_ID)
    assert json.loads(reservation) == {'task_id': TASK_ID, 'package_sha256': 'a' * 64, 'attempts': 1}
    assert case.client.ttl('youtube_studio:shot_prompt_compression:v1:' + TASK_ID) == -1
    case.provider.assert_not_called()
    case.critic.assert_not_called()
    assert case.client.hgetall(LEDGER_KEY) == case.ledger
    assert case.client.hgetall(case.hold_key) == case.hold
