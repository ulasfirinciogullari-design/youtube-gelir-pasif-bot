"""Execute V5 worker boundaries without worker/provider imports or requests."""
import ast
from copy import deepcopy
import hashlib
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from test_recovered_media import _load_recovery_boundary
from test_production_paid_reuse_selection import TREE, _execute, _named_assignment, _runtime, _select
from test_recovered_media_v4 import _primary


ORIGIN = 'cb476d47-8ccc-4a55-8da8-e82ca8db3272'
CHILD = '11111111-1111-4111-8111-111111111111'
REPAIRS = [0, 2, 3, 4]
HASH = 'b' * 64


def _media():
    return {'version': 5, 'repair_only': True, 'source_task_id': ORIGIN,
            'package_sha256': HASH, 'repair_scene_indices': list(REPAIRS),
            'scenes': {1: [{'key': f'recovery/{ORIGIN}/raw/saved-01.mp4',
                'sha256': 'a' * 64, 'size': 4096, 'provider': 'gemini_veo', 'provider_attempts': 1,
                'synthetic_motion_only': False, 'motion_recipe_version': None, 'source_media_type': 'video'}]},
            'stock_scenes': {5: {'key': f'curated_stock/{ORIGIN}/raw/stock.mp4',
                'sha256': 'c' * 64, 'size': 4096, 'pexels_id': 123456, 'start_fraction': .52}}}


def _good(index):
    return {'scene_index': index, 'score': 92, 'best_candidate_index': 0, 'best_start_fraction': .82,
            'evidence_gate_passed': True, 'editorial_gate_passed': True, 'identity_gate_passed': True,
            'subject_visible': True, 'spoken_action_visible': True, 'unexplained_reset': False,
            'prominent_readable_text_or_logo_visible': False, 'major_visual_artifact_visible': False,
            'effectively_static_or_frozen': False, 'substantially_repeats_adjacent_scene': False,
            'authored_identity_or_material_conflict_visible': False}


@pytest.fixture
def case(tmp_path, monkeypatch):
    ns = _load_recovery_boundary()
    names = {'_mixed_recovery_source', '_collect_mixed_recovery_visuals', '_collect_curated_recovery_visuals',
             '_review_mixed_retained_before_paid', '_validate_paid_create_allocation', '_generated_visual_spec',
             '_apply_visual_review', '_visual_path', '_preflight_production_shorts_paid_plan'}
    constants = {'_MANUAL_QA_CLEAR_VISUAL_FIELDS', 'MANUAL_QA_PUBLISH_QUALITY_THRESHOLD'}
    _execute([node for node in TREE.body if isinstance(node, ast.FunctionDef) and node.name in names
              or isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in constants for t in node.targets)], ns)
    media = _media()
    spec = {'topic': 'A sourced barcode history.', 'mode': 'production', 'format': 'shorts',
            'duration_minutes': .5, 'language': 'tr', 'music': 'off', 'quality_threshold': 86}
    package = {'scenes': [{'narration': f'Fact {i}', 'ai_prompt': f'bound-prompt-{i}' if i != 5 else None}
                          for i in range(6)]}
    source = {'task_id': ORIGIN, 'kind': 'render', 'state': 'FAILURE', 'spec': deepcopy(spec),
              'retry_child_task_id': CHILD, 'retry_claimed': True, 'repair_claimed': True}
    child = {'task_id': CHILD, 'kind': 'render', 'parent_id': ORIGIN, 'spec': deepcopy(spec)}
    jobs = {ORIGIN: source, CHILD: child}
    monkeypatch.setitem(sys.modules, 'app.services.studio_state', SimpleNamespace(get_job=lambda key: deepcopy(jobs.get(key))))
    voice = {'source_task_id': ORIGIN, 'scene_durations': [4.8] * 6, 'path': str(tmp_path/'recovered_voice.mp3')}
    data = b'\0\0\0\x18ftypmp42' + b'existing-generated' * 100
    checksum = hashlib.sha256(data).hexdigest()
    media['scenes'][1][0].update(sha256=checksum, size=len(data))
    stock_path = tmp_path/'mixed_stock_05.mp4'
    stock_path.write_bytes(data)
    stock = {'scene_visuals': {5: [{'path': str(stock_path), 'source_type': 'stock',
              'stock_provider': 'pexels', 'generated': False, 'pexels_id': 123456,
              'start_fraction': .52, 'preserve_start_fraction': True}]}, 'credits': [{'source': 'Pexels'}]}
    media['stock_scenes'][5].update(sha256=checksum, size=len(data))
    def validate(raw, count, expected):
        if type(raw.get('version')) is not int or count != 6 or expected != raw.get('package_sha256'):
            raise ValueError('Invalid mocked contract')
        return deepcopy(media)
    mixed = SimpleNamespace(validate_mixed_visual_recovery=Mock(side_effect=validate),
                            load_mixed_stock=Mock(return_value=stock), exact_mixed_retained_visuals=Mock())
    monkeypatch.setitem(sys.modules, 'app.services.mixed_visual_recovery', mixed)
    downloads = []
    def download(client, key, path, maximum, *, expected_size):
        downloads.append((key, maximum, expected_size))
        path.write_bytes(data)
        return checksum, len(data)
    monkeypatch.setitem(sys.modules, 'app.services.voice_candidate_recovery', SimpleNamespace(_download_bounded=download))
    import app.services
    storage = SimpleNamespace(_client=lambda: object())
    monkeypatch.setitem(sys.modules, 'app.services.storage', storage)
    monkeypatch.setattr(app.services, 'storage', storage, raising=False)
    ns['_validate_recovered_generated_clip'] = Mock()
    ns['review_scene_visuals'] = Mock(return_value={'reviews': [_good(0), _good(1)], 'missing_review_indices': []})
    return SimpleNamespace(ns=ns, media=media, spec=spec, package=package, source=source, child=child,
                           jobs=jobs, voice=voice, stock=stock, mixed=mixed, work=tmp_path, downloads=downloads)


def _scope(case, **changes):
    values = dict(task_id=CHILD, source_task_id=ORIGIN, runtime_spec=case.spec, approved_package=case.package,
                  recovered_media=case.media, recovered_voice=case.voice, cap=6, paid_slots_used=0)
    values.update(changes)
    return case.ns['_mixed_recovery_source'](**values)


def _collect(case):
    return case.ns['_collect_curated_recovery_visuals'](None, case.source, case.package,
        case.media, case.voice, CHILD, case.work)


def test_worker_uses_exact_server_validator_and_sanitizes_its_failure(case):
    before = deepcopy(case.media)
    assert case.ns['_validated_recovered_generated_media'](case.media, 6, HASH) == before
    case.mixed.validate_mixed_visual_recovery.assert_called_once_with(case.media, 6, HASH)
    case.mixed.validate_mixed_visual_recovery.side_effect = ValueError('private provider URL secret')
    with pytest.raises(RuntimeError, match='^Mixed recovery contract could not be verified$'):
        case.ns['_validated_recovered_generated_media'](case.media, 6, HASH)
    assert case.media == before


def test_claimed_parent_and_frozen_full_spec_are_required_before_loading(case):
    before = deepcopy((case.source, case.child, case.media, case.voice))
    assert _scope(case) == case.source
    assert (case.source, case.child, case.media, case.voice) == before
    case.mixed.load_mixed_stock.assert_not_called()


@pytest.mark.parametrize('damage', ['mode', 'format', 'duration', 'language', 'music', 'missing_package',
    'missing_voice', 'voice_source', 'cap_none', 'cap_bool', 'cap_short', 'remaining_short', 'missing_parent',
    'missing_child', 'parent_success', 'parent_relation', 'child_relation', 'source_spec', 'child_spec',
    'noncanonical_child', 'same_child', 'different_origin'])
def test_invalid_scope_claim_or_remaining_budget_cannot_load_or_buy(case, damage):
    changes = {}
    if damage == 'mode': case.spec['mode'] = 'preview'
    if damage == 'format': case.spec['format'] = 'landscape'
    if damage == 'duration': case.spec['duration_minutes'] = 1
    if damage == 'language': case.spec['language'] = 'en'
    if damage == 'music': case.spec['music'] = 'auto'
    if damage == 'missing_package': changes['approved_package'] = None
    if damage == 'missing_voice': changes['recovered_voice'] = None
    if damage == 'voice_source': case.voice['source_task_id'] = CHILD
    if damage == 'cap_none': changes['cap'] = None
    if damage == 'cap_bool': changes['cap'] = True
    if damage == 'cap_short': changes['cap'] = 3
    if damage == 'remaining_short': changes.update(cap=6, paid_slots_used=3)
    if damage == 'missing_parent': case.jobs.pop(ORIGIN)
    if damage == 'missing_child': case.jobs.pop(CHILD)
    if damage == 'parent_success': case.source['state'] = 'SUCCESS'
    if damage == 'parent_relation': case.source['retry_child_task_id'] = ORIGIN
    if damage == 'child_relation': case.child['parent_id'] = CHILD
    if damage == 'source_spec': case.source['spec']['topic'] = 'Different'
    if damage == 'child_spec': case.child['spec']['topic'] = 'Different'
    if damage == 'noncanonical_child': changes['task_id'] = 'not-a-task'
    if damage == 'same_child': changes['task_id'] = ORIGIN
    if damage == 'different_origin': case.media['source_task_id'] = CHILD
    with pytest.raises(RuntimeError):
        _scope(case, **changes)
    assert not case.downloads
    case.mixed.load_mixed_stock.assert_not_called()


def test_early_pools_keep_stock_and_generated_provenance_separate(case):
    result = _collect(case)
    assert [i for i, pool in enumerate(result['scene_visuals']) if pool] == [1, 5]
    generated, stock = result['scene_visuals'][1][0], result['scene_visuals'][5][0]
    assert generated['generated'] is True and generated['generation_recovered'] is True
    assert generated['source_type'] == 'generated' and generated['start_fraction'] == 0
    assert stock['generated'] is False and stock['source_type'] == 'stock' and stock['stock_provider'] == 'pexels'
    assert stock['start_fraction'] == .52 and 'generation_provider' not in stock
    assert all(spec['curated_pinned'] and spec['preserve_start_fraction'] and spec['forbid_loop']
               for spec in (generated, stock))
    assert result['credits'] == case.stock['credits'] and result['seen_ids'] == {123456}
    assert len(case.downloads) == 1
    checks = case.ns['_validate_recovered_generated_clip'].call_args_list
    assert len(checks) == 2
    for index, call in zip((5, 1), checks):
        entry = case.media['stock_scenes'][5] if index == 5 else case.media['scenes'][1][0]
        assert call.kwargs == {'minimum_duration': pytest.approx(5.15),
                               'expected_size': entry['size'], 'expected_sha256': entry['sha256']}


@pytest.mark.parametrize('damage', ['missing_stock', 'fake_generated', 'wrong_provider', 'bad_paid_hash',
                                   'existing_destination', 'invalid_probe', 'loader_failure'])
def test_invalid_saved_stock_or_paid_bytes_fail_without_fallback(case, damage):
    if damage == 'missing_stock': case.stock['scene_visuals'].clear()
    if damage == 'fake_generated': case.stock['scene_visuals'][5][0]['generated'] = True
    if damage == 'wrong_provider': case.stock['scene_visuals'][5][0]['stock_provider'] = 'unknown'
    if damage == 'bad_paid_hash': case.media['scenes'][1][0]['sha256'] = 'f'*64
    if damage == 'existing_destination': (case.work/'recovered_s01_00.mp4').write_bytes(b'existing')
    if damage == 'invalid_probe': case.ns['_validate_recovered_generated_clip'].side_effect = ValueError('private data')
    if damage == 'loader_failure': case.mixed.load_mixed_stock.side_effect = ValueError('private URL')
    with pytest.raises(RuntimeError, match='^Mixed recovery media could not be verified; no replacement media was generated$'):
        _collect(case)


def _review(case):
    pools = _collect(case)['scene_visuals']
    proxies = {index: [{**pools[index][0], 'path': str(case.work/f'proxy-{index}.mp4'), 'start_fraction': 0}]
               for index in (1, 5)}
    case.mixed.exact_mixed_retained_visuals.return_value = {'scene_visuals': proxies, 'frame_counts': [147]*6}
    before = deepcopy(pools)
    output = case.ns['_review_mixed_retained_before_paid'](case.package['scenes'], pools, case.voice,
        case.work, 'Sourced story', case.spec, [{'url': 'https://www.example.com/history'}])
    assert pools == before
    return output, proxies, pools


def test_retained_QA_uses_exact_proxy_with_full_story_and_maps_global_ids(case):
    output, proxies, pools = _review(case)
    assert [row['scene_index'] for row in output['reviews']] == [1, 5]
    call = case.ns['review_scene_visuals'].call_args
    assert call.args[1] == [proxies[1], proxies[5]] and call.args[3] == 2
    assert call.kwargs['story_scenes'] == case.package['scenes']
    assert call.kwargs['evidence_sources'] == [{'url': 'https://www.example.com/history'}]
    assert call.kwargs['_missing_review_attempts'] == call.kwargs['_score_reason_consistency_attempts'] == 0
    for index in (1, 5):
        case.ns['_apply_visual_review'](pools, index, output['reviews'][0])
    assert pools[5][0]['start_fraction'] == .52 and pools[1][0]['start_fraction'] == 0


@pytest.mark.parametrize('field,value', [('score',85), ('score',True), ('score',float('nan')),
    ('evidence_gate_passed',False), ('identity_gate_passed',False), ('editorial_gate_passed',False),
    ('subject_visible',False), ('spoken_action_visible',False), ('major_visual_artifact_visible',True),
    ('prominent_readable_text_or_logo_visible',True), ('unexplained_reset',True), ('best_candidate_index',1)])
def test_bad_retained_exact_review_stops_before_any_new_media(case, field, value):
    case.ns['review_scene_visuals'].return_value['reviews'][1][field] = value
    with pytest.raises(RuntimeError, match='before any new media submission'):
        _review(case)
    case.ns['review_scene_visuals'].assert_called_once()


@pytest.mark.parametrize('damage', ['missing', 'duplicate', 'global_instead_of_local', 'missing_indices', 'proxy_error'])
def test_incomplete_or_wrong_identity_reviews_do_not_import_old_approval(case, damage):
    result = case.ns['review_scene_visuals'].return_value
    if damage == 'missing': result['reviews'].pop()
    if damage == 'duplicate': result['reviews'][1]['scene_index'] = 0
    if damage == 'global_instead_of_local': result['reviews'][1]['scene_index'] = 5
    if damage == 'missing_indices': result['missing_review_indices'] = [1]
    if damage == 'proxy_error': case.mixed.exact_mixed_retained_visuals.side_effect = ValueError('private path')
    with pytest.raises(RuntimeError, match='before any new media submission'):
        _review(case)


def _worker(case):
    runtime = _runtime(recovered_generated_media=case.media, recovered_voice=case.voice,
        options=case.spec, scenes=case.package['scenes'], total_paid_create_cap=6, runway_attempts=0,
        prompt_candidates={index: f'bound-prompt-{index}' for index in range(5)},
        scene_visuals=[[] for _ in range(6)])
    runtime['scene_visuals'][1] = [{'path': str(case.work/'recovered_s01_00.mp4'),
        'curated_pinned': True, 'generation_recovered': True}]
    runtime['scene_visuals'][5] = deepcopy(case.stock['scene_visuals'][5])
    for name in ('scene_repair_recovery', 'recovery_repair_scene_indices', 'recovery_paid_scene_indices'):
        _execute([_named_assignment(name)], runtime)
    return runtime


def test_actual_selection_has_five_paid_paths_but_only_four_creates_and_never_stock_five(case):
    runtime = _worker(case)
    assert runtime['recovery_paid_scene_indices'] == set(range(5))
    assert runtime['recovery_repair_scene_indices'] == set(REPAIRS)
    assert [row['scene_index'] for row in _select(runtime)] == list(range(5))
    for cap, used in ((4,0), (6,2)):
        runtime['_validate_paid_create_allocation'](runtime['selected_runway'], case.media, cap, paid_slots_used=used)
    with pytest.raises(RuntimeError):
        runtime['_validate_paid_create_allocation'](runtime['selected_runway'], case.media, 6, paid_slots_used=3)
    runtime['_require_recovered_media_coverage'](case.media, list(range(5)))
    with pytest.raises(RuntimeError, match='exactly match'):
        runtime['_require_recovered_media_coverage'](case.media, list(range(6)))


@pytest.mark.parametrize('fail_index', [None, 0, 2, 3, 4])
def test_real_primary_loop_creates_only_named_four_rechecks_saved_one_never_redownloads_or_generates_stock(case, fail_index):
    runtime = _worker(case)
    _select(runtime)
    stock = deepcopy(runtime['scene_visuals'][5])
    events, validations = _primary(runtime, case.work, fail_index=fail_index)
    assert [value for kind, value in events if kind == 'create'] == REPAIRS
    assert [value for kind, value in events if kind == 'reserve'] == [1,2,3,4]
    assert not [value for kind, value in events if kind == 'download']
    assert len(validations) == 1 and validations[0]['expected_sha256'] == case.media['scenes'][1][0]['sha256']
    assert runtime['scene_visuals'][5] == stock and runtime['runway_attempts'] == 4
    assert runtime['total_paid_create_cap'] == 6
    assert runtime['runway_failed_scenes'] == ([] if fail_index is None else [fail_index])
    for index in range(5):
        assert len(runtime['scene_visuals'][index]) == (0 if index == fail_index else 1)
        if index != fail_index:
            assert runtime['scene_visuals'][index][0]['curated_pinned'] is True


def test_no_additional_paid_repair_or_stock_rescue_after_any_final_rejection(case):
    runtime = _worker(case)
    runtime['preview_runway_repair_indices'] = Mock(side_effect=AssertionError('No additional paid repair'))
    _execute([_named_assignment('final_runway_repair_candidates')], runtime)
    assert runtime['final_runway_repair_candidates'] == []
    runtime.update(rejected_final_scenes=list(range(6)), _retry_bad_scene=Mock(side_effect=AssertionError('No stock search')))
    rescue = next(node for node in ast.walk(TREE) if isinstance(node, ast.For)
                  and ast.unparse(node.iter) == 'rejected_final_scenes' and '_retry_bad_scene' in ast.unparse(node))
    _execute([rescue], runtime)
    runtime['_retry_bad_scene'].assert_not_called()


def test_full_final_QA_rejects_bad_stock_saved_or_new_scene_and_never_uses_proxy_as_render_source(case):
    runtime = _worker(case)
    runtime.update(manual_qa_preview_scenes=set())
    for index in range(6):
        runtime['final_reviews'] = {i: {'score': 92 if i != index else 40} for i in range(6)}
        _execute([_named_assignment('rejected_final_scenes')], runtime)
        assert runtime['rejected_final_scenes'] == [index]
    review = _named_assignment('final_visual_qc').value
    assert ast.unparse(review.args[1]) == 'final_review_visuals'
    rendered = _named_assignment('rendered').value
    assert next(ast.unparse(k.value) for k in rendered.keywords if k.arg == 'scene_visual_paths') == 'scene_visuals'


def test_same_voice_full_audio_QA_and_existing_execution_claim_are_preserved(case):
    runtime = _worker(case)
    runtime.update(saved_voice_retry=None, stage_pool=Mock(), _download_recovered_voice_candidate=Mock(), work=case.work)
    branch = next(node for node in ast.walk(TREE) if isinstance(node, ast.If)
                  and isinstance(node.test, ast.Name) and node.test.id == 'saved_voice_retry' and 'voice_future' in ast.unparse(node))
    _execute([branch], runtime)
    assert runtime['stage_pool'].submit.call_args.args[0] is runtime['_download_recovered_voice_candidate']
    _execute([_named_assignment('can_regenerate')], runtime)
    assert runtime['can_regenerate'] is False
    worker = next(node for node in TREE.body if isinstance(node, ast.FunctionDef) and node.name == 'run_video_pipeline')
    text = ast.unparse(worker)
    assert text.index('_guard_retry_child_execution') < text.index('_mixed_recovery_source') < text.index('voice_future')
    assert '_verify_audio_narration_with_retry' in text and 'verify_audio_prosody' in text
    assert 'options.get(\'_recovered_generated_media\')' not in text


def test_v5_retained_proxy_review_is_reused_for_preflight_not_rejudged_from_raw_frames(case):
    runtime = _worker(case)
    runtime['visual_qc'] = {'reviews': [_good(1), _good(5)]}
    runtime['review_scene_visuals'] = Mock(side_effect=AssertionError('No second raw retained review'))
    _execute([_named_assignment('pre_runway_qc')], runtime)
    assert runtime['pre_runway_qc'] is runtime['visual_qc']
    runtime['review_scene_visuals'].assert_not_called()


@pytest.mark.parametrize('version,manifest', [(None,None), (3,{}), (5,None), (5,{})])
def test_actual_dispatch_selects_only_one_bound_collector_and_mixed_cannot_accept_curated_manifest(case, version, manifest):
    mixed, curated = Mock(return_value={'mixed': True}), Mock(return_value={'curated': True})
    runtime = dict(task_id=CHILD, retry_dispatch_source_id=ORIGIN, topic='Story', duration_minutes=.5,
        language='tr', channel_id='Capital', options=case.spec, approved_package=case.package,
        recovered_generated_media={**case.media, 'version':version} if version else None,
        recovered_voice=case.voice, total_paid_create_cap=6, runway_attempts=0,
        curated_stock_manifest=manifest, _task_spec=Mock(return_value=case.spec),
        _mixed_recovery_source=mixed, _curated_recovery_source=curated)
    _execute([_named_assignment('curated_source_job')], runtime)
    if version == 5 and manifest is None:
        assert runtime['curated_source_job'] == {'mixed':True}
        mixed.assert_called_once(); curated.assert_not_called()
    elif manifest is not None:
        # A mixed contract with a legacy manifest is routed to the existing
        # strict v3-only validator; it cannot silently discard that input.
        assert runtime['curated_source_job'] == {'curated':True}
        curated.assert_called_once(); mixed.assert_not_called()
    else:
        assert runtime['curated_source_job'] is None
        mixed.assert_not_called(); curated.assert_not_called()


def test_v5_requires_same_source_voice_and_server_package_before_any_collection(case):
    paired = next(node for node in ast.walk(TREE) if isinstance(node, ast.If)
                  and 'not recovered_voice' in ast.unparse(node.test)
                  and 'recovered_generated_media' in ast.unparse(node.test))
    runtime = _worker(case)
    runtime['recovered_voice'] = None
    with pytest.raises(RuntimeError, match='matching media and voice'):
        _execute([paired], runtime)
    guard = next(node for node in ast.walk(TREE) if isinstance(node, ast.If)
                 and any(isinstance(child, ast.Constant) and child.value == 'Recovered media requires an approved storyboard'
                         for child in ast.walk(node)))
    runtime.update(raw_recovered_generated_media=case.media, raw_recovered_voice=case.voice, approved_package=None)
    with pytest.raises(RuntimeError, match='approved storyboard'):
        _execute([guard], runtime)


def test_authored_plan_preflight_counts_four_new_AI_shots_not_the_real_stock(case):
    before = deepcopy(case.media)
    case.ns['_preflight_production_shorts_paid_plan'](case.spec, case.package['scenes'], case.media, 4)
    with pytest.raises(RuntimeError, match='before any submission'):
        case.ns['_preflight_production_shorts_paid_plan'](case.spec, case.package['scenes'], case.media, 3)
    assert case.media == before


def _ranking(case, **changes):
    runtime = _worker(case)
    runtime.update(generation_aspect_ratio='9:16', provider_outage_stock_scenes=set(),
        stock_quality_fallback_scenes=set(), _runway_prompt_for_scene=Mock(return_value='old query composition'),
        _apply_visual_review=case.ns['_apply_visual_review'], should_rank_runway_candidate=lambda *a,**k: True)
    runtime.update(changes)
    node = next(node for node in ast.walk(TREE) if isinstance(node, ast.FunctionDef) and node.name == 'rank_runway_candidates')
    _execute([node], runtime)
    return runtime


@pytest.mark.parametrize('version', [4, 5])
def test_explicit_repair_directions_reach_prompt_map_losslessly_not_old_queries(case, monkeypatch, version):
    from app.services import production_shot_prompt
    runtime = _ranking(case)
    runtime['recovered_generated_media']['version'] = version
    for index in REPAIRS:
        runtime['scenes'][index].update(
            ai_prompt=f'Continuous side view {index}: a flatbed scanner reads the package; ending shows the hatchback pulling away.',
            visual_queries=['old checkout gun and warehouse storage pallets'],
            narration='A previously approved historical fact.',
        )
        runtime['current_reviews'][index] = {'score':40, 'retry_queries':['replace with old unrelated macro']}
    spy = Mock(wraps=production_shot_prompt.build_production_shot_prompt)
    monkeypatch.setattr(production_shot_prompt, 'build_production_shot_prompt', spy)
    before = deepcopy(runtime['scenes'])
    prompts, _ = runtime['rank_runway_candidates']()
    assert [call.args[0] for call in spy.call_args_list] == [before[index] for index in REPAIRS]
    for index in REPAIRS:
        assert prompts[index] == before[index]['ai_prompt']
        assert 'flatbed' in prompts[index] and 'hatchback' in prompts[index]
        assert 'PRIMARY EVENT' not in prompts[index] and 'old unrelated' not in prompts[index]
    assert runtime['scenes'] == before
    assert prompts[1] == prompts[5] == 'old query composition'
    assert runtime['_runway_prompt_for_scene'].call_count == 2


@pytest.mark.parametrize('change', [{'version':2}, {'version':3}, {'mode':'preview'},
                                   {'format':'landscape'}, {'duration_minutes':3}])
def test_other_versions_modes_and_unselected_shots_keep_legacy_composer(case, monkeypatch, change):
    from app.services import production_shot_prompt
    runtime = _ranking(case)
    if 'version' in change: runtime['recovered_generated_media']['version'] = change['version']
    if 'mode' in change: runtime['options']['mode'] = change['mode']
    if 'format' in change: runtime['options']['format'] = change['format']
    if 'duration_minutes' in change: runtime['duration_minutes'] = change['duration_minutes']
    forbidden = Mock(side_effect=AssertionError('Legacy prompt behavior must not change'))
    monkeypatch.setattr(production_shot_prompt, 'build_production_shot_prompt', forbidden)
    prompts, _ = runtime['rank_runway_candidates']()
    assert all(prompt == 'old query composition' for prompt in prompts.values())
    forbidden.assert_not_called()


@pytest.mark.parametrize('invalid', ['x' * 1001, '📦' * 501, None, ''])
def test_invalid_late_repair_direction_fails_before_first_paid_reservation(case, invalid):
    from app.services.production_shot_prompt import ProductionShotPromptError
    runtime = _ranking(case)
    runtime['scenes'][4]['ai_prompt'] = invalid
    before = runtime['runway_attempts']
    with pytest.raises(ProductionShotPromptError):
        runtime['rank_runway_candidates']()
    assert runtime['runway_attempts'] == before == 0
    ranking_call = next(node for node in ast.walk(TREE) if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name) and node.func.id == 'rank_runway_candidates')
    paid_loop = next(node for node in ast.walk(TREE) if isinstance(node, ast.For)
                    and isinstance(node.iter, ast.Name) and node.iter.id == 'selected_runway')
    assert ranking_call.lineno < paid_loop.lineno
    create = next(node for node in ast.walk(paid_loop) if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Name) and node.func.id == 'generate_scene')
    assert ast.unparse(create.args[0]) == 'prompt_candidates[scene_idx]'
