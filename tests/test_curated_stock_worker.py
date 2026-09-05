"""Execute the curated worker boundaries without a worker import or providers."""
import ast
from copy import deepcopy
import hashlib
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import storage, voice_candidate_recovery
from test_production_paid_reuse_selection import _runtime, _run_primary_loop, _select


SOURCE = Path(__file__).resolve().parents[1] / 'app/tasks.py'
TREE = ast.parse(SOURCE.read_text(encoding='utf-8'))
PARENT = '11111111-1111-4111-8111-111111111111'
CHILD = '22222222-2222-4222-8222-222222222222'
ORIGIN = '33333333-3333-4333-8333-333333333333'


def execute(nodes, namespace):
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), namespace)


def assignment(name):
    return next(node for node in ast.walk(TREE) if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == name for target in node.targets))


@pytest.fixture
def case(tmp_path, monkeypatch):
    names = {'_curated_recovery_source', '_collect_curated_recovery_visuals', '_retry_bad_scene',
             '_generated_visual_spec', '_require_unique_selected_stock', '_apply_visual_review', '_visual_path'}
    ns = {'Path': Path, 're': re, 'FinalVisualQualityError': RuntimeError,
          '_MAX_RECOVERED_VIDEO_BYTES': 100*1024*1024}
    execute([node for node in TREE.body if isinstance(node, ast.FunctionDef) and node.name in names], ns)
    spec = {'topic': 'Banknote', 'language': 'tr', 'mode': 'production', 'format': 'shorts',
            'duration_minutes': 0.5, 'music': 'off'}
    package = {'scenes': [{'narration': f'Fact {i}'} for i in range(6)]}
    parent = {'task_id': PARENT, 'state': 'FAILURE', 'kind': 'render', 'spec': deepcopy(spec),
              'retry_child_task_id': CHILD, 'retry_claimed': True, 'repair_claimed': True}
    child = {'task_id': CHILD, 'parent_id': PARENT, 'kind': 'render', 'spec': deepcopy(spec)}
    jobs = {PARENT: parent, CHILD: child}
    # A legacy state test removes/reloads this module during collection.
    # Bind the actual lazy import used by this AST-isolated worker, not a
    # possibly stale module object retained from collection time.
    monkeypatch.setitem(sys.modules, 'app.services.studio_state',
                        SimpleNamespace(get_job=lambda task: deepcopy(jobs.get(task))))
    data = b'\0\0\0\x18ftypmp42' + b'existing-generated' * 100
    sha = hashlib.sha256(data).hexdigest()
    media = {'version': 3, 'recovery_only': True, 'source_task_id': ORIGIN, 'scenes': {3: [{
        'key': f'recovery/{ORIGIN}/raw/scene-03-initial.mp4', 'size': len(data), 'sha256': sha,
        'provider': 'gemini_veo', 'provider_attempts': 1}]}}
    voice = {'source_task_id': ORIGIN, 'scene_durations': [4.8]*6}
    pools = {}
    for index, fraction in ((0,.5), (1,.5), (2,.82), (4,.5), (5,.82)):
        path = tmp_path / f'stock-{index}.mp4'
        path.write_bytes(data + bytes([index]))
        pools[index] = [{'path': str(path), 'source_type': 'stock', 'stock_provider': 'pexels',
                         'pexels_id': 100+index, 'start_fraction': fraction}]
    loader = Mock(return_value={'scene_visuals': pools, 'credits': [{'source': 'Pexels'}]})
    monkeypatch.setitem(sys.modules, 'app.services.curated_stock', SimpleNamespace(load_curated_stock_manifest=loader))
    downloads = []
    def download(client, key, path, maximum, *, expected_size):
        downloads.append((key, maximum, expected_size))
        path.write_bytes(data)
        return sha, len(data)
    monkeypatch.setattr(voice_candidate_recovery, '_download_bounded', download)
    monkeypatch.setattr(storage, '_client', lambda: object())
    ns['_validate_recovered_generated_clip'] = Mock()
    manifest = {'source_task_id': PARENT}
    args = (CHILD, PARENT, spec, package, manifest, media, voice)
    return SimpleNamespace(ns=ns, work=tmp_path, spec=spec, package=package, parent=parent, child=child,
                           jobs=jobs, media=media, voice=voice, manifest=manifest, args=args,
                           pools=pools, loader=loader, downloads=downloads, data=data)


def collect(case):
    return case.ns['_collect_curated_recovery_visuals'](
        case.manifest, case.parent, case.package, case.media, case.voice, CHILD, case.work)


def test_consumed_server_parent_and_paired_v3_are_accepted_without_mutation(case):
    before = deepcopy(case.args)
    assert case.ns['_curated_recovery_source'](*case.args) == case.parent
    assert case.args == before


@pytest.mark.parametrize('damage', ['preview','long','music','five_scenes','manifest_parent','missing_parent',
    'v2','missing_voice','voice_origin','extra_paid','two_paid_candidates','parent_success','parent_child',
    'child_parent','child_spec','parent_spec','missing_child'])
def test_invalid_scope_or_dispatch_fails_before_download_or_generation(case, damage):
    if damage == 'preview': case.spec['mode'] = 'preview'
    if damage == 'long': case.spec['duration_minutes'] = 1
    if damage == 'music': case.spec['music'] = 'auto'
    if damage == 'five_scenes': case.package['scenes'].pop()
    if damage == 'manifest_parent': case.manifest['source_task_id'] = ORIGIN
    if damage == 'missing_parent': case.jobs.pop(PARENT)
    if damage == 'v2': case.media['version'] = 2
    if damage == 'missing_voice': case.voice.clear()
    if damage == 'voice_origin': case.voice['source_task_id'] = PARENT
    if damage == 'extra_paid': case.media['scenes'][4] = deepcopy(case.media['scenes'][3])
    if damage == 'two_paid_candidates': case.media['scenes'][3] *= 2
    if damage == 'parent_success': case.parent['state'] = 'SUCCESS'
    if damage == 'parent_child': case.parent['retry_child_task_id'] = ORIGIN
    if damage == 'child_parent': case.child['parent_id'] = ORIGIN
    if damage == 'child_spec': case.child['spec']['language'] = 'en'
    if damage == 'parent_spec': case.parent['spec']['language'] = 'en'
    if damage == 'missing_child': case.jobs.pop(CHILD)
    with pytest.raises(RuntimeError, match='Curated recovery'):
        case.ns['_curated_recovery_source'](*case.args)
    case.loader.assert_not_called()
    assert case.downloads == []


def test_all_six_inputs_loaded_early_with_stock_identity_and_paid_hash_preserved(case):
    result = collect(case)
    pools = result['scene_visuals']
    assert len(pools) == 6 and all(len(pool) == 1 for pool in pools)
    assert [pools[i][0]['pexels_id'] for i in (0,1,2,4,5)] == [100,101,102,104,105]
    assert all(pool[0]['curated_pinned'] and pool[0]['preserve_start_fraction'] for pool in pools)
    assert all(pools[i][0]['source_type'] == 'stock' and not pools[i][0].get('generated') for i in (0,1,2,4,5))
    assert pools[3][0]['generated'] is True and pools[3][0]['start_fraction'] == 0
    assert pools[3][0]['forbid_loop'] is True
    assert result['seen_ids'] == {100,101,102,104,105}
    assert len(case.downloads) == 1
    entry = case.media['scenes'][3][0]
    assert case.downloads[0] == (entry['key'], 100*1024*1024, entry['size'])
    assert case.ns['_validate_recovered_generated_clip'].call_args.kwargs == {
        'minimum_duration': pytest.approx(5.15), 'expected_size': entry['size'], 'expected_sha256': entry['sha256']}


@pytest.mark.parametrize('damage', ['missing_stock','stock_is_generated','duplicate_id','bad_paid_hash','existing_destination','probe'])
def test_bad_pinned_media_never_falls_back_to_search(case, damage):
    if damage == 'missing_stock': case.pools.pop(4)
    if damage == 'stock_is_generated': case.pools[0][0]['generated'] = True
    if damage == 'duplicate_id': case.pools[4][0]['pexels_id'] = 100
    if damage == 'bad_paid_hash': case.media['scenes'][3][0]['sha256'] = 'f'*64
    if damage == 'existing_destination': (case.work/'recovered_s03_00.mp4').write_bytes(b'existing')
    if damage == 'probe': case.ns['_validate_recovered_generated_clip'].side_effect = ValueError('secret')
    with pytest.raises(RuntimeError, match='^Curated recovery media could not be verified; no replacement media was generated$'):
        collect(case)


def test_actual_initial_selection_loop_does_not_change_pinned_fractions_or_search(case):
    pools = collect(case)['scene_visuals']
    before = deepcopy(pools)
    loop = next(node for node in ast.walk(TREE) if isinstance(node, ast.For)
                and ast.unparse(node.target) == '(scene_idx, _scene)')
    execute([loop], {'scenes': case.package['scenes'], 'scene_visuals': pools})
    assert pools == before


@pytest.mark.parametrize('prefix', ['qc','pre_runway_budget_rescue','final_qc_rescue'])
def test_every_stock_retry_stage_is_disabled_for_pinned_cut(case, prefix):
    pools = collect(case)['scene_visuals']
    case.ns['_download_ranked_broll_candidates'] = Mock(side_effect=AssertionError('search forbidden'))
    before = deepcopy(pools)
    assert case.ns['_retry_bad_scene'](4, ['anything'], set(), case.work, [], file_prefix=prefix,
                                       active_scene_visuals=pools) == []
    case.ns['_download_ranked_broll_candidates'].assert_not_called()
    assert pools == before


def test_existing_primary_recovery_loop_does_not_redownload_or_duplicate_preloaded_paid(tmp_path):
    ns = _runtime()
    ns['scene_visuals'][3] = [{'path': str(tmp_path/'recovered_s03_00.mp4'),
                             'curated_pinned': True, 'generation_recovered': True}]
    _select(ns)
    assert _run_primary_loop(ns, tmp_path) == []
    assert len(ns['scene_visuals'][3]) == 1
    assert ns['scene_visuals'][3][0]['curated_pinned'] is True
    assert len(ns['validated_clips']) == 1  # Revalidate hash again at actual reuse.
    assert ns['runway_attempts'] == 4 and ns['runway_generated_scenes'] == [3]


def test_proxy_review_never_changes_raw_pinned_render_fraction(case):
    pools = collect(case)['scene_visuals']
    before = deepcopy(pools)
    for i in range(6):
        case.ns['_apply_visual_review'](pools, i, {'score':92, 'best_candidate_index':0, 'best_start_fraction':.06})
    assert pools == before


def test_trailing_server_argument_defaults_to_none_and_is_not_read_from_options():
    worker = next(node for node in TREE.body if isinstance(node, ast.FunctionDef) and node.name == 'run_video_pipeline')
    assert worker.args.args[-1].arg == 'curated_stock_manifest'
    assert isinstance(worker.args.defaults[-1], ast.Constant) and worker.args.defaults[-1].value is None
    assert 'options.get(\'curated_stock_manifest\')' not in ast.unparse(worker)
    assert 'options[\'curated_stock_manifest\']' not in ast.unparse(worker)


def test_exact_proxy_is_used_only_for_final_critic_not_the_real_renderer():
    review = assignment('final_visual_qc').value
    assert isinstance(review, ast.Call) and ast.unparse(review.args[1]) == 'final_review_visuals'
    render = assignment('rendered').value
    assert next(ast.unparse(k.value) for k in render.keywords if k.arg == 'scene_visual_paths') == 'scene_visuals'
    assert ast.unparse(assignment('final_runway_repair_candidates').value).startswith('[] if recovered_generated_media')


def test_noncurated_dispatch_still_selects_the_legacy_collector():
    legacy, curated = Mock(), Mock()
    pool = SimpleNamespace(submit=lambda function, *args, **kwargs: function(*args, **kwargs))
    ns = {'curated_source_job': None, 'stage_pool': pool, '_collect_broll': legacy,
          '_collect_curated_recovery_visuals': curated, 'scenes': [], 'work': Path('/tmp'),
          'strict_short_preview_duration': False, 'pexels_orientation': 'portrait'}
    execute([assignment('broll_future')], ns)
    legacy.assert_called_once_with([], Path('/tmp'), False, orientation='portrait')
    curated.assert_not_called()
