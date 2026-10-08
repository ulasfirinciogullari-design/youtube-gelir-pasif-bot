"""V4 alone permits up to four declared repairs, never implicit substitutes."""
import ast
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest

from test_recovered_media import _load_recovery_boundary
from test_production_paid_reuse_selection import (
    TREE, SOURCE, SOURCE_ID, _entry, _execute, _named_assignment, _runtime, _select,
)


REPAIRS = [0, 2, 5]
FOUR_REPAIRS = [1, 3, 4, 5]


def _contract(repairs=REPAIRS):
    return {
        'version': 4, 'repair_only': True, 'source_task_id': SOURCE_ID,
        'package_sha256': 'b' * 64, 'repair_scene_indices': list(repairs),
        'scenes': {str(index): [_entry(index)] for index in range(6) if index not in repairs},
    }


@pytest.fixture
def boundary():
    return _load_recovery_boundary()


def _validate(boundary, raw=None, expected='b' * 64):
    return boundary['_validated_recovered_generated_media'](
        _contract() if raw is None else raw, 6, expected,
    )


@pytest.mark.parametrize('repairs', [[0], [0, 5], REPAIRS, FOUR_REPAIRS])
def test_v4_preserves_bound_entries_and_exact_disjoint_partition(boundary, repairs):
    raw = _contract(repairs)
    before = deepcopy(raw)
    result = _validate(boundary, raw)
    assert result == {**raw, 'scenes': {int(key): value for key, value in raw['scenes'].items()}}
    assert raw == before
    assert set(result['scenes']) | set(result['repair_scene_indices']) == set(range(6))
    boundary['_require_recovered_media_coverage'](result, list(range(6)))
    for indices in ([0, 2, 5], [0, 1, 2, 3, 4], list(range(7))):
        with pytest.raises(RuntimeError, match='exactly match'):
            boundary['_require_recovered_media_coverage'](result, indices)


@pytest.mark.parametrize('field,value', [
    ('version', 4.0), ('version', '4'), ('version', True), ('repair_only', 1),
    ('repair_only', False), ('repair_scene_indices', []), ('repair_scene_indices', [0, 1, 2, 3, 5]),
    ('repair_scene_indices', [0, 0]), ('repair_scene_indices', [2, 0]),
    ('repair_scene_indices', [True]), ('repair_scene_indices', ['0']),
    ('repair_scene_indices', [-1]), ('repair_scene_indices', [6]),
    ('source_task_id', 'wrong'), ('package_sha256', 'c' * 64),
    ('recovery_only', True), ('provider', 'gemini_veo'), ('scenes', {}),
])
def test_v4_rejects_expanded_malformed_or_unbound_contracts(boundary, field, value):
    raw = _contract()
    raw[field] = value
    with pytest.raises(RuntimeError):
        _validate(boundary, raw)


def test_v4_rejects_missing_expected_hash_overlap_and_missing_retained_scene(boundary):
    with pytest.raises(RuntimeError):
        _validate(boundary, expected=None)
    raw = _contract()
    raw['scenes']['0'] = [_entry(0)]
    with pytest.raises(RuntimeError):
        _validate(boundary, raw)
    raw = _contract()
    raw['scenes'].pop('1')
    with pytest.raises(RuntimeError, match='complete retained/repair partition'):
        _validate(boundary, raw)


@pytest.mark.parametrize('field,value', [
    ('sha256', None), ('sha256', 'bad'), ('size', True), ('size', 1023),
    ('size', 100 * 1024 * 1024 + 1), ('provider', 'unknown'),
    ('provider_attempts', 0), ('synthetic_motion_only', 'false'),
    ('key', 'https://example.invalid/clip.mp4'),
    ('key', 'recovery/00000000-0000-4000-8000-000000000000/raw/clip.mp4'),
])
def test_v4_keeps_strict_private_entry_integrity(boundary, field, value):
    raw = _contract()
    raw['scenes']['1'][0][field] = value
    with pytest.raises(RuntimeError):
        _validate(boundary, raw)


def test_old_versions_never_inherit_three_repair_authority(boundary):
    for version in (2, 3):
        raw = _contract()
        raw['version'] = version
        with pytest.raises(RuntimeError):
            _validate(boundary, raw)
    raw = _contract([0, 5])
    raw['version'] = 2
    assert _validate(boundary, raw)['version'] == 2


def _worker(boundary, *, repairs=REPAIRS, **changes):
    media = _validate(boundary, _contract(repairs))
    runtime = _runtime(
        recovered_generated_media=media, recovered_voice={'source_task_id': SOURCE_ID},
        scene_repair_recovery=True, recovery_repair_scene_indices=set(repairs),
        recovery_paid_scene_indices=set(range(6)), prompt_candidates={i: f'bound-prompt-{i}' for i in range(6)},
        runway_attempts=0, total_paid_create_cap=len(repairs),
    )
    runtime.update(changes)
    return runtime


def _scope(runtime):
    guard = next(node for node in ast.walk(TREE) if isinstance(node, ast.If)
                 and any(isinstance(child, ast.Constant) and isinstance(child.value, str)
                         and 'V4 repair requires a bounded' in child.value
                         for child in ast.walk(node)))
    _execute([guard], runtime)


@pytest.mark.parametrize('changes', [
    {'options': {'mode': 'preview', 'format': 'shorts'}},
    {'options': {'mode': 'production', 'format': 'landscape'}},
    {'duration_minutes': 0.6}, {'duration_minutes': 3},
    {'total_paid_create_cap': None}, {'total_paid_create_cap': True},
    {'total_paid_create_cap': 0}, {'total_paid_create_cap': 2},
    {'total_paid_create_cap': 3, 'runway_attempts': 1},
])
def test_v4_scope_and_authoritative_remaining_budget_fail_before_media(boundary, changes):
    runtime = _worker(boundary, **changes)
    with pytest.raises(RuntimeError):
        _scope(runtime)


def test_v4_preflight_counts_only_three_new_creates_and_keeps_legacy_scopes(boundary):
    runtime = _worker(boundary, total_paid_create_cap=4, runway_attempts=1)
    _scope(runtime)
    assert runtime['runway_attempts'] == 1
    for version in (2, 3):
        runtime = _worker(boundary, duration_minutes=3, total_paid_create_cap=None)
        runtime['recovered_generated_media']['version'] = version
        _scope(runtime)


def test_v4_requires_paired_voice_and_same_source_before_synthesis(boundary):
    runtime = _worker(boundary)
    paired = next(node for node in ast.walk(TREE) if isinstance(node, ast.If)
                  and 'not recovered_voice' in ast.unparse(node.test)
                  and 'recovered_generated_media' in ast.unparse(node.test))
    runtime['recovered_voice'] = None
    with pytest.raises(RuntimeError, match='matching media and voice'):
        _execute([paired], runtime)
    same_source = next(node for node in ast.walk(TREE) if isinstance(node, ast.If)
                       and any(isinstance(child, ast.Constant)
                               and child.value == 'Recovered media and voice source tasks do not match'
                               for child in ast.walk(node)))
    runtime['recovered_voice'] = {'source_task_id': 'different'}
    with pytest.raises(RuntimeError, match='source tasks do not match'):
        _execute([same_source], runtime)


def test_model_authored_recovery_without_server_approved_package_is_rejected():
    guard = next(node for node in ast.walk(TREE) if isinstance(node, ast.If)
                 and any(isinstance(child, ast.Constant)
                         and child.value == 'Recovered media requires an approved storyboard'
                         for child in ast.walk(node)))
    runtime = {'raw_recovered_generated_media': _contract(), 'raw_recovered_voice': {},
               'approved_package': None, 'FinalVisualQualityError': RuntimeError}
    with pytest.raises(RuntimeError, match='requires an approved storyboard'):
        _execute([guard], runtime)


def test_actual_v4_selection_ignores_stock_rank_and_has_no_undeclared_candidate(boundary):
    runtime = _worker(boundary, ranked_runway_candidates=[{'scene_index': 5}])
    for field in ('scene_repair_recovery', 'recovery_repair_scene_indices', 'recovery_paid_scene_indices'):
        runtime.pop(field)
        _execute([_named_assignment(field)], runtime)
    assert runtime['scene_repair_recovery'] is True
    assert runtime['recovery_repair_scene_indices'] == set(REPAIRS)
    assert runtime['recovery_paid_scene_indices'] == set(range(6))
    before = deepcopy(runtime['current_reviews'])
    selected = _select(runtime)
    assert [row['scene_index'] for row in selected] == list(range(6))
    runtime['_require_recovered_media_coverage'](runtime['recovered_generated_media'], list(range(6)))
    assert runtime['current_reviews'] == before
    runtime['prompt_candidates'].pop(2)
    with pytest.raises(RuntimeError, match='missing a safe generation prompt'):
        _select(runtime)


def _primary(runtime, tmp_path, *, fail_index=None):
    from app.services.production_spend import SpendBlocked

    events, validations = [], []
    durable = {'used': runtime['runway_attempts']}
    def slots(task_id, cap, *, reserve=False):
        assert task_id == 'child' and reserve is True
        if durable['used'] >= cap:
            raise RuntimeError('Durable budget exhausted')
        durable['used'] += 1
        events.append(('reserve', durable['used']))
        return durable['used']
    def generate(prompt, **kwargs):
        index = int(prompt.rsplit('-', 1)[1])
        assert events[-1][0] == 'reserve'
        assert kwargs['allow_paid_terminal_resubmit'] is False
        assert kwargs['allow_image_motion'] is False
        events.append(('create', index))
        if index == fail_index:
            raise RuntimeError('A mocked accepted-provider failure')
        return {'provider': 'gemini_veo', 'provider_attempts': 1}
    runtime.update(
        SpendBlocked=SpendBlocked,
        video_scene_budget=None, spending_scene=lambda *_args: nullcontext(),
        task_id='child', work=tmp_path, scene_durations=[5.0] * 6,
        download_file=lambda key, path: events.append(('download', key)),
        _validate_recovered_generated_clip=lambda *args, **kwargs: validations.append(kwargs),
        _generated_visual_spec=lambda path, provider, provider_attempts: {
            'path': str(path), 'generation_provider': provider,
            'generation_provider_attempts': provider_attempts},
        _persisted_paid_create_slots=slots, generate_scene=generate,
        _runway_generation_seconds=lambda duration: 6,
        _image_motion_prompt_for_scene=lambda *args: 'unused image direction',
        download_generated_scene=lambda *args: None,
        _checkpoint_generated_asset=lambda *args: None,
        _runway_failure_diagnostic=lambda phase, index, error: {'scene_index': index},
        generated_checkpoint_specs={}, runway_scenes_used=0, runway_generated_scenes=[],
        generated_video_provider_records=[], generated_asset_candidate_journal=[],
        runway_failed_scenes=[], runway_failure_diagnostics=[],
        omni_continuity_reference_image_path=None, omni_continuity_anchor_scene_idx=None,
        omni_unsafe_submission_scenes=set(), image_motion_submission_scenes=set(),
        generation_aspect_ratio='9:16', is_private_image_motion_preview=False,
        package={}, voice_result={},
        GeminiOmniContinuityReferenceError=type('MockContinuityError', (Exception,), {}),
        GeminiImageAttemptedError=type('MockImageError', (Exception,), {}),
        GeminiOmniTerminalError=type('MockTerminalError', (Exception,), {}),
    )
    _execute([node for node in TREE.body if isinstance(node, ast.FunctionDef)
              and node.name == '_reserve_paid_create_slot'], runtime)
    loop = next(node for node in ast.walk(TREE) if isinstance(node, ast.For)
                and isinstance(node.iter, ast.Name) and node.iter.id == 'selected_runway')
    _execute([loop], runtime)
    return events, validations


@pytest.mark.parametrize('fail_index', [None, 0, 2, 5])
def test_real_primary_loop_reserves_exact_three_and_never_uses_stock_fallback(boundary, tmp_path, fail_index):
    runtime = _worker(boundary)
    _scope(runtime)
    _select(runtime)
    events, validations = _primary(runtime, tmp_path, fail_index=fail_index)
    assert [value for kind, value in events if kind == 'create'] == REPAIRS
    assert [value for kind, value in events if kind == 'reserve'] == [1, 2, 3]
    assert [value for kind, value in events if kind == 'download'] == [_entry(i)['key'] for i in (1, 3, 4)]
    assert len(validations) == 3
    assert all(row['expected_size'] == 4096 and row['expected_sha256'] == 'a' * 64 for row in validations)
    assert runtime['runway_attempts'] == 3
    for index, specs in enumerate(runtime['scene_visuals']):
        assert len(specs) == (0 if index == fail_index else 1)
        assert all('stock-' not in spec['path'] for spec in specs)
    assert runtime['runway_failed_scenes'] == ([] if fail_index is None else [fail_index])


def test_missing_retained_asset_cannot_be_replaced_by_new_create(boundary, tmp_path):
    runtime = _worker(boundary)
    runtime['selected_runway'] = [{'scene_index': 1}]
    runtime['recovered_generated_media']['scenes'][1] = []
    with pytest.raises(RuntimeError, match='scene is unavailable'):
        _primary(runtime, tmp_path)
    assert runtime['runway_attempts'] == 0


@pytest.mark.parametrize('repairs', [REPAIRS, FOUR_REPAIRS])
def test_v4_disables_additional_paid_repair_and_stock_substitution(boundary, repairs):
    runtime = _worker(boundary, repairs=repairs, preview_runway_repair_indices=Mock(side_effect=AssertionError('No extra repair')))
    _execute([_named_assignment('final_runway_repair_candidates')], runtime)
    assert runtime['final_runway_repair_candidates'] == []
    runtime.update(rejected_final_scenes=list(range(6)), _retry_bad_scene=Mock(side_effect=AssertionError('No stock rescue')))
    before = deepcopy(runtime['scene_visuals'])
    rescue_loop = next(node for node in ast.walk(TREE) if isinstance(node, ast.For)
                       and isinstance(node.iter, ast.Name) and node.iter.id == 'rejected_final_scenes'
                       and '_retry_bad_scene' in ast.unparse(node))
    _execute([rescue_loop], runtime)
    assert runtime['scene_visuals'] == before
    runtime['_retry_bad_scene'].assert_not_called()


@pytest.mark.parametrize('repairs', [REPAIRS, FOUR_REPAIRS])
def test_v4_uses_existing_voice_and_cannot_seed_regenerate(boundary, repairs):
    runtime = _worker(boundary, repairs=repairs, saved_voice_retry=None, stage_pool=Mock(),
                       _download_recovered_voice_candidate=Mock(), work=Path('/tmp/mock-work'))
    voice_branch = next(node for node in ast.walk(TREE) if isinstance(node, ast.If)
                        and isinstance(node.test, ast.Name) and node.test.id == 'saved_voice_retry'
                        and 'voice_future' in ast.unparse(node))
    _execute([voice_branch], runtime)
    assert runtime['stage_pool'].submit.call_args.args[0] is runtime['_download_recovered_voice_candidate']
    _execute([_named_assignment('can_regenerate')], runtime)
    assert runtime['can_regenerate'] is False


@pytest.mark.parametrize('fail_index', [None, 1, 3, 4, 5])
def test_four_named_repairs_reserve_four_and_reuse_only_retained_zero_two(boundary, tmp_path, fail_index):
    runtime = _worker(boundary, repairs=FOUR_REPAIRS, total_paid_create_cap=6)
    _scope(runtime)
    _select(runtime)
    events, validations = _primary(runtime, tmp_path, fail_index=fail_index)
    assert [value for kind, value in events if kind == 'create'] == FOUR_REPAIRS
    assert [value for kind, value in events if kind == 'reserve'] == [1, 2, 3, 4]
    assert [value for kind, value in events if kind == 'download'] == [_entry(i)['key'] for i in (0, 2)]
    assert len(validations) == 2
    assert all(row['expected_size'] == 4096 and row['expected_sha256'] == 'a' * 64 for row in validations)
    assert runtime['runway_attempts'] == 4 and runtime['total_paid_create_cap'] == 6
    for index, specs in enumerate(runtime['scene_visuals']):
        assert len(specs) == (0 if index == fail_index else 1)
        assert all('stock-' not in spec['path'] for spec in specs)
        if index in (0, 2):
            assert specs[0]['generation_recovered'] is True
    assert runtime['runway_failed_scenes'] == ([] if fail_index is None else [fail_index])


@pytest.mark.parametrize('cap,used', [(3, 0), (4, 1), (6, 3)])
def test_four_repair_budget_rejects_before_voice_or_create_when_only_three_slots_remain(boundary, cap, used):
    runtime = _worker(boundary, repairs=FOUR_REPAIRS, total_paid_create_cap=cap, runway_attempts=used)
    before = deepcopy(runtime['recovered_generated_media'])
    with pytest.raises(RuntimeError, match='before any submission'):
        _scope(runtime)
    assert runtime['runway_attempts'] == used and runtime['total_paid_create_cap'] == cap
    assert runtime['recovered_generated_media'] == before
    guard = next(node for node in ast.walk(TREE) if isinstance(node, ast.If)
                 and 'V4 repair requires a bounded' in ast.unparse(node))
    voice = next(node for node in ast.walk(TREE) if isinstance(node, ast.If)
                 and isinstance(node.test, ast.Name) and node.test.id == 'saved_voice_retry'
                 and 'voice_future' in ast.unparse(node))
    assert guard.lineno < voice.lineno


def test_complete_five_repair_partition_is_still_rejected(boundary):
    with pytest.raises(RuntimeError, match='repair indices'):
        _validate(boundary, _contract([0, 1, 3, 4, 5]))


@pytest.mark.parametrize('rejected_index,score', [(0, 85), (2, 40), (5, None), (1, 40)])
def test_v4_retained_and_repaired_assets_still_face_unchanged_final_gate(boundary, rejected_index, score):
    runtime = _worker(boundary)
    runtime.update(
        final_reviews={i: {'score': 92} for i in range(6)},
        manual_qa_preview_scenes=set(), rescued_final_scenes=[],
        runway_failed_scenes=[], final_runway_repair_failures=[], runway_failure_diagnostics=[],
        _checkpoint_qa_workprint=lambda *args, **kwargs: None,
        _checkpoint_selected_visuals=lambda *args, **kwargs: None,
        effective_edit_target_seconds=30.0,
        task_id='child', work=Path('/tmp/mock-work'), voice_result={}, scene_durations=[5.0] * 6,
        package={'narration': 'Saved narration'}, audio_qc={}, audio_duration_qc={}, audio_prosody_qc={},
        _final_visual_rejection_diagnostics=lambda **kwargs: {'rejected': kwargs['rejected_scene_indices']},
    )
    if score is None:
        runtime['final_reviews'].pop(rejected_index)
    else:
        runtime['final_reviews'][rejected_index]['score'] = score
    _execute([_named_assignment('rejected_final_scenes')], runtime)
    assert runtime['rejected_final_scenes'] == [rejected_index]
    rejection = next(node for node in ast.walk(TREE) if isinstance(node, ast.If)
                     and isinstance(node.test, ast.Name) and node.test.id == 'rejected_final_scenes')
    with pytest.raises(RuntimeError, match='Final visual quality gate rejected'):
        _execute([rejection], runtime)
