"""Offline real-boundary mixed receipt preparation, reads and atomic claims."""
import ast
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.services import mixed_visual_recovery as recovery, studio_state
from test_failed_visual_recovery import case as cb_case, SOURCE, PREP, CHILD, _sha, _snapshot, _save_source
from test_preserved_visual_recovery import _immutable_boundary


@pytest.fixture
def case(cb_case, monkeypatch):
    case = cb_case
    parent = case.source['parent_id']
    token = 'actual-bound-retry-token-00000000'
    case.redis.set(studio_state.JOB_PREFIX + parent, json.dumps({
        'task_id': parent, 'kind': 'render', 'state': 'FAILURE',
        'retry_child_task_id': SOURCE, 'spec': deepcopy(case.source['spec']),
    }))
    case.redis.hset(studio_state.RETRY_DISPATCH_PREFIX + parent,
                   mapping={'child_task_id': SOURCE, 'mode': 'full', 'state': 'dispatched', 'token': token})
    case.redis.hset(studio_state.RETRY_CHILD_CLAIM_PREFIX + SOURCE,
                   mapping={'source_task_id': parent, 'token': token})
    case.redis.set(studio_state.RETRY_CHILD_EXECUTION_PREFIX + SOURCE, token)
    case.stock_bytes = b'\0\0\0\x18ftyp' + b'actual licensed warehouse scan' * 150
    case.stock_candidate = {'pexels_id': 4292903, 'start_fraction': .35,
                            'sha256': _sha(case.stock_bytes), 'size': len(case.stock_bytes)}
    case.overrides = {index: f'Source-specific 1974 US mechanism shot {index}, side view.' for index in recovery.REPAIRS}
    case.dimensions = {'width': 2560, 'height': 1440, 'source_duration': 8.0}
    def fetch(pexels_id, output):
        assert pexels_id == case.stock_candidate['pexels_id']
        with output.open('xb') as body: body.write(case.stock_bytes)
        return ({'pexels_id': pexels_id, 'sha256': _sha(case.stock_bytes), 'size': len(case.stock_bytes), **case.dimensions},
                {'source': 'Pexels', 'creator_name': 'Tiger Lily', 'creator_url': 'https://www.pexels.com/@tigerlily/',
                 'page_url': f'https://www.pexels.com/video/{pexels_id}/', 'license_url': 'https://www.pexels.com/license/'})
    case.fetch = Mock(side_effect=fetch)
    monkeypatch.setattr(recovery.stock, '_pexels_by_id', case.fetch)
    monkeypatch.setattr(recovery.stock, '_probe', Mock(side_effect=lambda path: deepcopy(case.dimensions)))
    original = case.story_review.side_effect
    immutable = _immutable_boundary()
    def story(*args, **kwargs):
        immutable(args[0], kwargs['immutable_candidate_narrations'])
        return original(*args, **kwargs)
    case.story_review.side_effect = story
    case.good = [deepcopy(case.good[index]) for index in (0, 1)]
    case.visual_review.return_value = {'reviews': case.good, 'missing_review_indices': []}
    return case


def _prepare(case, **kwargs):
    return recovery.prepare_mixed_visual_recovery(SOURCE, case.pointer, case.work,
        stock_candidate=kwargs.get('stock_candidate', case.stock_candidate),
        shot_prompt_overrides=kwargs.get('shot_prompt_overrides', case.overrides))


def _receipt(case, pointer):
    return json.loads(case.objects[pointer['key']])


def _publish(case):
    pointer = _prepare(case)
    return pointer, recovery.publish_mixed_visual_recovery(pointer)


def test_prepare_preserves_actual_stock_identity_voice_and_only_saved_scene_one(case):
    before = _snapshot(case)
    receipt = _receipt(case, _prepare(case))
    assert _snapshot(case) == before
    assert receipt['qa_approved'] is False and receipt['reusable'] is False
    assert receipt['new_tts_requests'] == receipt['new_paid_create_requests'] == 0
    package = receipt['approved_package']
    assert package['scenes'][1] == case.package['scenes'][1]
    assert [scene['narration'] for scene in package['scenes']] == [scene['narration'] for scene in case.package['scenes']]
    assert package['scenes'][5]['ai_prompt'] is None
    assert package['scenes'][5]['visual_queries'] == recovery.STOCK_QUERIES
    assert all(package['scenes'][index]['ai_prompt'] == case.overrides[index] for index in recovery.REPAIRS)
    audio = package['_recovered_voice']
    assert audio['sha256'] == _sha(case.audio) and audio['scene_durations'] == case.voice['scene_durations']
    assert case.objects[audio['key']] == case.audio
    media = recovery.validate_mixed_visual_recovery(package['_recovered_generated_media'], 6, receipt['package_sha256'])
    assert set(media['scenes']) == {1} and set(media['stock_scenes']) == {5}
    assert media['repair_scene_indices'] == [0, 2, 3, 4]
    assert case.objects[media['stock_scenes'][5]['key']] == case.stock_bytes
    assert not any(f'scene-{index:02d}-initial.mp4' in key for key in case.gets for index in (2, 3, 4))
    assert not any('/diagnostic/' in key for key in case.gets)
    case.fetch.assert_called_once(); case.story_review.assert_called_once(); case.visual_review.assert_called_once()
    assert [call.args[3] for call in case.render.normalize_clip.call_args_list] == [1, 5]
    stock_cut = case.render.normalize_clip.call_args_list[1].args[0]
    assert stock_cut['generated'] is False and stock_cut['start_fraction'] == .35
    assert stock_cut['stock_provider'] == 'pexels'
    reviewer_pools = case.visual_review.call_args.args[1]
    assert reviewer_pools[1][0]['generated'] is False and reviewer_pools[1][0]['start_fraction'] == 0.0
    assert receipt['stock_entry']['start_fraction'] == .35
    assert all(body.closed for body in case.bodies)


def test_actual_id_import_can_establish_hash_without_claiming_prior_view_or_approval(case):
    receipt = _receipt(case, _prepare(case, stock_candidate={'pexels_id': 4292903, 'start_fraction': 0.0}))
    assert receipt['stock_entry']['sha256'] == _sha(case.stock_bytes)
    assert receipt['stock_candidate'] == {'pexels_id': 4292903, 'start_fraction': 0.0}
    assert receipt['qa_approved'] is False
    case.visual_review.assert_called_once()


@pytest.mark.parametrize('index', [0, 1])
@pytest.mark.parametrize('field,value', [('score', 85), ('identity_gate_passed', False),
                                       ('editorial_gate_passed', False), ('evidence_gate_passed', False)])
def test_either_actual_retained_cut_rejection_prevents_checkpoint_and_paid_work(case, index, field, value):
    if field == 'score': value = case.source['spec']['quality_threshold'] - 1
    case.good[index][field] = value
    before = _snapshot(case)
    with pytest.raises(recovery.MixedVisualRecoveryError) as caught: _prepare(case)
    audit = _receipt(case, caught.value.diagnostic_pointer)
    assert audit['status'] == 'retained_visuals_rejected'
    assert len(audit['retained_visual_reviews']) == 2
    assert _snapshot(case) == before and len(case.puts) == 1


@pytest.mark.parametrize('damage', ['missing', 'duplicate', 'bool_index', 'bad_candidate', 'missing_gate', 'bool_score'])
def test_malformed_or_incomplete_current_review_never_accepts_old_good_score(case, damage):
    if damage == 'missing': case.good.pop()
    if damage == 'duplicate': case.good[1]['scene_index'] = 0
    if damage == 'bool_index': case.good[0]['scene_index'] = False
    if damage == 'bad_candidate': case.good[1]['best_candidate_index'] = 1
    if damage == 'missing_gate': case.good[1].pop('identity_gate_passed')
    if damage == 'bool_score': case.good[1]['score'] = True
    with pytest.raises(recovery.MixedVisualRecoveryError): _prepare(case)
    assert not case.redis.exists(studio_state.REPAIR_CHECKPOINT_PREFIX + SOURCE)


@pytest.mark.parametrize('damage', ['parent_missing', 'parent_child', 'parent_spec', 'token', 'execution',
                                  'dispatch_state', 'paid0', 'old_claim', 'old_checkpoint', 'audio_hash'])
def test_lineage_paid_and_source_guards_fail_before_fetch_or_model(case, damage):
    parent = case.source['parent_id']
    if damage == 'parent_missing': case.redis.delete(studio_state.JOB_PREFIX + parent)
    if damage in {'parent_child', 'parent_spec'}:
        record = json.loads(case.redis.get(studio_state.JOB_PREFIX + parent))
        record['retry_child_task_id' if damage == 'parent_child' else 'spec'] = CHILD
        case.redis.set(studio_state.JOB_PREFIX + parent, json.dumps(record))
    if damage == 'token': case.redis.hset(studio_state.RETRY_CHILD_CLAIM_PREFIX + SOURCE, 'token', 'different-token-00000000')
    if damage == 'execution': case.redis.delete(studio_state.RETRY_CHILD_EXECUTION_PREFIX + SOURCE)
    if damage == 'dispatch_state': case.redis.hset(studio_state.RETRY_DISPATCH_PREFIX + parent, 'state', 'uncertain')
    if damage == 'paid0': case.redis.hset(studio_state.PAID_CREATE_BUDGET_PREFIX + SOURCE, 'used', '0')
    if damage == 'old_claim': case.redis.set(studio_state.REPAIR_CHECKPOINT_CLAIM_PREFIX + SOURCE, 'old')
    if damage == 'old_checkpoint': case.redis.set(studio_state.REPAIR_CHECKPOINT_PREFIX + SOURCE, 'old')
    if damage == 'audio_hash': case.source['audio_candidate_checkpoint']['audio_sha256'] = '0' * 64; _save_source(case)
    before = _snapshot(case)
    with pytest.raises(recovery.MixedVisualRecoveryError): _prepare(case)
    case.fetch.assert_not_called(); case.story_review.assert_not_called()
    assert _snapshot(case) == before


@pytest.mark.parametrize('damage', ['old_id', 'bool_id', 'unknown_field', 'hash', 'size', 'half_hash', 'fraction', 'short', 'wrong_overrides'])
def test_explicit_candidate_and_shot_contract_is_bounded(case, damage):
    candidate, overrides = deepcopy(case.stock_candidate), deepcopy(case.overrides)
    if damage == 'old_id': candidate['pexels_id'] = 38052460
    if damage == 'bool_id': candidate['pexels_id'] = True
    if damage == 'unknown_field': candidate['url'] = 'https://untrusted.invalid/private'
    if damage == 'hash': candidate['sha256'] = '0' * 64
    if damage == 'size': candidate['size'] += 1
    if damage == 'half_hash': candidate.pop('size')
    if damage == 'fraction': candidate['start_fraction'] = 1.0
    if damage == 'short': case.dimensions['source_duration'] = 5.0
    if damage == 'wrong_overrides': overrides[1] = 'Change retained store'
    with pytest.raises(recovery.MixedVisualRecoveryError):
        _prepare(case, stock_candidate=candidate, shot_prompt_overrides=overrides)
    case.story_review.assert_not_called()


@pytest.mark.parametrize('damage', ['speech', 'retained', 'sources', 'stock_prompt'])
def test_critic_cannot_change_frozen_speech_or_nonpermitted_shots(case, damage):
    original = case.story_review.side_effect
    def changed(*args, **kwargs):
        reviewed = original(*args, **kwargs)
        if damage == 'speech': reviewed['scenes'][0]['narration'] += ' new claim'
        if damage == 'retained': reviewed['scenes'][1]['ai_prompt'] = 'new retained picture'
        if damage == 'sources': reviewed['sources'] = []
        if damage == 'stock_prompt': reviewed['scenes'][5]['ai_prompt'] = 'Invent new stock'
        return reviewed
    case.story_review.side_effect = changed
    with pytest.raises(recovery.MixedVisualRecoveryError): _prepare(case)
    case.visual_review.assert_not_called()


def test_publication_is_one_real_claim_and_preserves_all_ancestor_and_paid_history(case):
    pointer = _prepare(case)
    before, old_source = _snapshot(case), deepcopy(case.source)
    assert recovery.publish_mixed_visual_recovery(pointer)['status'] == 'checkpoint_published'
    current = studio_state.get_job(SOURCE)
    assert {key: value for key, value in current.items() if key not in {'repair_available', 'updated_at'}} == {
        key: value for key, value in old_source.items() if key not in {'repair_available', 'updated_at'}}
    for key in before:
        if key != studio_state.JOB_PREFIX + SOURCE: assert case.redis.dump(key) == before[key]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda index: studio_state.claim_retry_dispatch(
            SOURCE, CHILD if index == 0 else PREP, ('a' if index == 0 else 'b') * 32, allow_repair=True), (0, 1)))
    assert sum(result['claimed'] for result in results) == 1
    checkpoint = next(result for result in results if result['claimed'])['checkpoint']
    assert checkpoint['approved_package']['_recovered_generated_media']['version'] == 5
    assert case.redis.hgetall(studio_state.PAID_CREATE_BUDGET_PREFIX + SOURCE) == {'cap': '6', 'used': '6'}
    before = _snapshot(case)
    with pytest.raises(recovery.MixedVisualRecoveryError): recovery.publish_mixed_visual_recovery(pointer)
    assert _snapshot(case) == before


@pytest.mark.parametrize('target', ['source', 'ledger', 'dispatch', 'checkpoint', 'claim', 'parent', 'parent_dispatch', 'child_claim', 'execution'])
def test_midpublication_watch_race_cannot_write_checkpoint(case, target):
    pointer = _prepare(case)
    keys = dict(zip(('source', 'ledger', 'dispatch', 'checkpoint', 'claim'), recovery.cb._keys()))
    lineage_keys, _ = recovery._lineage(case.source, case.redis)
    keys.update(dict(zip(('parent', 'parent_dispatch', 'child_claim', 'execution'), lineage_keys)))
    original = case.storage.get_object
    fired = False
    def racing(**kwargs):
        nonlocal fired
        result = original(**kwargs)
        if not fired and kwargs['Key'] == case.pointer['manifest_key']:
            fired = True
            key = keys[target]
            if case.redis.type(key) == 'hash': case.redis.hset(key, 'race', 'changed')
            else: case.redis.set(key, 'concurrent-change')
        return result
    case.storage.get_object = racing
    with pytest.raises(recovery.MixedVisualRecoveryError): recovery.publish_mixed_visual_recovery(pointer)
    assert fired
    if target != 'checkpoint': assert not case.redis.exists(studio_state.REPAIR_CHECKPOINT_PREFIX + SOURCE)
    if target != 'source': assert studio_state.get_job(SOURCE) == case.source


@pytest.mark.parametrize('damage', ['retained_hash', 'stock_hash', 'voice_bytes', 'stock_bytes', 'review', 'source_type', 'lineage'])
def test_rebound_receipt_cannot_forge_asset_or_fresh_review_proof(case, damage):
    pointer = _prepare(case)
    receipt = _receipt(case, pointer)
    if damage == 'retained_hash': receipt['approved_package']['_recovered_generated_media']['scenes']['1'][0]['sha256'] = '0' * 64
    if damage == 'stock_hash': receipt['approved_package']['_recovered_generated_media']['stock_scenes']['5']['sha256'] = '0' * 64
    if damage in {'voice_bytes', 'stock_bytes'}:
        key = receipt['approved_package']['_recovered_voice']['key'] if damage == 'voice_bytes' else receipt['stock_entry']['key']
        case.objects[key] = case.objects[key][:-1] + b'x'
    if damage == 'review': receipt['retained_visual_reviews'][1]['review']['score'] = 100
    if damage == 'source_type': receipt['retained_visual_reviews'][1]['source_type'] = 'generated'
    if damage == 'lineage': receipt['lineage_sha256'] = '0' * 64
    if damage not in {'voice_bytes', 'stock_bytes'}:
        pointer = recovery._store(case.storage, None, 'prepared', receipt)
    before = _snapshot(case)
    with pytest.raises(recovery.MixedVisualRecoveryError): recovery.publish_mixed_visual_recovery(pointer)
    assert _snapshot(case) == before


def test_loader_is_storage_only_and_never_relabels_or_reselects_real_stock(case):
    receipt = _receipt(case, _prepare(case))
    media = receipt['approved_package']['_recovered_generated_media']
    work = case.work.parent / f'{CHILD}_attempt_0'; work.mkdir()
    result = recovery.load_mixed_stock(media, work)
    spec = result['scene_visuals'][5][0]
    assert Path(spec['path']).read_bytes() == case.stock_bytes
    assert spec['generated'] is False and spec['source_type'] == 'stock' and spec['stock_provider'] == 'pexels'
    assert spec['preserve_start_fraction'] is True and spec['start_fraction'] == .35
    assert result['credits'][0]['pexels_id'] == 4292903
    case.fetch.assert_called_once()
    with pytest.raises(recovery.MixedVisualRecoveryError): recovery.load_mixed_stock(media, work)


def test_real_v5_worker_validator_collector_and_exact_retained_proxies_share_one_contract(case):
    receipt = _receipt(case, _prepare(case))
    package = receipt['approved_package']
    media = case.tasks._validated_recovered_generated_media(
        package['_recovered_generated_media'], 6, receipt['package_sha256'])
    audio = case.tasks._validated_recovered_voice(package['_recovered_voice'], 6, receipt['package_sha256'])
    source = Path(__file__).resolve().parents[1] / 'app/tasks.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in {'_collect_mixed_recovery_visuals', '_generated_visual_spec'}]
    assert len(functions) == 2
    namespace = dict(vars(case.tasks))
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), 'exec'), namespace)
    work = case.work.parent / f'{CHILD}_attempt_0'; work.mkdir()
    before = _snapshot(case)
    result = namespace['_collect_mixed_recovery_visuals'](media, audio, work)
    assert _snapshot(case) == before
    pools = result['scene_visuals']
    assert [index for index, pool in enumerate(pools) if pool] == [1, 5]
    assert Path(pools[1][0]['path']).read_bytes() == case.clips[1]
    assert Path(pools[5][0]['path']).read_bytes() == case.stock_bytes
    assert pools[1][0]['generation_recovered'] is True
    assert pools[5][0]['generated'] is False and pools[5][0]['stock_provider'] == 'pexels'
    original = deepcopy(pools)
    exact = recovery.exact_mixed_retained_visuals(package['scenes'], pools, case.voice, work)
    assert pools == original and len(exact['frame_counts']) == 6
    assert set(exact['scene_visuals']) == {1, 5}
    assert exact['scene_visuals'][5][0]['generated'] is False
    assert [call.args[3] for call in case.render.normalize_clip.call_args_list[-2:]] == [1, 5]
    case.fetch.assert_called_once()


@pytest.mark.parametrize('damage', ['version_bool', 'extra_field', 'wrong_repair', 'five_repair', 'retained_3', 'stock_generated', 'stock_key'])
def test_v5_cannot_expand_partition_or_conflate_stock_and_generated(case, damage):
    receipt = _receipt(case, _prepare(case))
    raw = receipt['approved_package']['_recovered_generated_media']
    if damage == 'version_bool': raw['version'] = True
    if damage == 'extra_field': raw['approved'] = True
    if damage == 'wrong_repair': raw['repair_scene_indices'] = [0, 2, 3, 5]
    if damage == 'five_repair': raw['repair_scene_indices'] = [0, 2, 3, 4, 5]
    if damage == 'retained_3': raw['scenes']['3'] = raw['scenes'].pop('1')
    if damage == 'stock_generated': raw['stock_scenes']['5']['generated'] = True
    if damage == 'stock_key': raw['stock_scenes']['5']['key'] = raw['scenes']['1'][0]['key']
    with pytest.raises(recovery.MixedVisualRecoveryError):
        recovery.validate_mixed_visual_recovery(raw, 6, receipt['package_sha256'])
