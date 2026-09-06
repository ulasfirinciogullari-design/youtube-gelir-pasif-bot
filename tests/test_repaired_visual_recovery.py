"""Actual preservation/claim boundaries, with no remote models or media calls."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.services import audio_checkpoint, repaired_visual_recovery as recovery, studio_state
from test_preserved_visual_recovery import case as original_case, SOURCE as PARENT, CHILD as LEAF, PREP, _record, _sha, _snapshot


@pytest.fixture
def case(original_case, monkeypatch):
    case = original_case
    base = recovery.base
    pointer = base.prepare_preserved_visual_recovery(PARENT, case.work,
        repair_scene_indices=(1, 3, 4, 5), shot_prompt_overrides={index: f'Approved first replacement shot {index}.' for index in (1, 3, 4, 5)})
    prior = _record(case, pointer)
    base.publish_preserved_visual_recovery(pointer)
    token = 'one-consumed-prior-repair-token'
    claimed = studio_state.claim_retry_dispatch(PARENT, LEAF, token, allow_repair=True)
    assert claimed['claimed'] is True and claimed['checkpoint'] == prior
    assert studio_state.mark_retry_dispatch(PARENT, token, 'dispatched') is True
    assert studio_state.acquire_retry_child_execution(LEAF, PARENT) is True
    package = deepcopy(prior['approved_package'])
    voice = {**case.voice, 'path': str(case.work.parent.parent / f'{LEAF}.mp3')}
    Path(voice['path']).write_bytes(case.audio)
    audio_pointer = audio_checkpoint.persist_audio_candidate_checkpoint(LEAF, package, voice)['audio_candidate_checkpoint']
    leaf_work = case.work.parent / f'{LEAF}_attempt_0'; leaf_work.mkdir()
    entries, actual = [], list(case.clips)
    for index in (1, 3, 4, 5):
        actual[index] = b'\0\0\0\x18ftyp' + bytes([110 + index]) * (2100 + index)
        path = leaf_work / f'runway_s{index:02d}.mp4'; path.write_bytes(actual[index])
        entry = base.assets.persist_generated_asset_candidate(LEAF, leaf_work,
            package=package, voice_result=voice, visual_spec={
                'path': str(path), 'generated': True, 'source_type': 'generated', 'generation_provider': 'gemini_veo',
                'generation_provider_attempts': 1, 'start_fraction': 0.0, 'preserve_start_fraction': True, 'forbid_loop': True},
            scene_index=index, phase='initial_generation', options=base._options(case.source), duration_minutes=.5)
        entries.append(entry)
    leaf = {**deepcopy(case.source), 'task_id': LEAF, 'parent_id': PARENT, 'paid_create_slots_used': 4,
            'audio_candidate_checkpoint': audio_pointer, 'generated_asset_candidates': {
                **base._FLAGS, 'source_task_id': LEAF, 'status': 'candidate_journal', 'attempted_count': 4,
                'preserved_count': 4, 'failed_count': 0, 'entries': entries}}
    flags = {'version': 1, 'status': 'qa_workprint', 'task_id': LEAF,
             'qa_approved': False, 'publish_eligible': False, 'reusable': False}
    metadata = {**flags, 'voice': {'sha256': _sha(case.audio), 'size': len(case.audio), 'existing_voice_quality_passed': True},
        'scenes': [{'scene_index': index, 'narration': scene['narration'], 'duration_seconds': voice['scene_durations'][index],
                    'selection': {'selected_spec_index': 0, 'sha256': _sha(actual[index]), 'size': len(actual[index]),
                                  'source_type': 'generated', 'generation_provider': 'gemini_veo',
                                  'start_fraction': 0.0, 'forbid_loop': True}}
                   for index, scene in enumerate(package['scenes'])]}
    payload = json.dumps(metadata, sort_keys=True, separators=(',', ':')).encode()
    key = f'qa_workprints/{LEAF}/{_sha(payload)}.json'
    case.objects[key] = payload, 'application/json'
    leaf['qa_workprint'] = {**flags, 'metadata_key': key, 'metadata_sha256': _sha(payload), 'metadata_size': len(payload)}
    case.client.set(studio_state.JOB_PREFIX + LEAF, json.dumps(leaf))
    case.client.hset(studio_state.PAID_CREATE_BUDGET_PREFIX + LEAF, mapping={'cap': '6', 'used': '4'})
    for name, value in {'SOURCE': LEAF, 'PARENT': PARENT, 'PARENT_RECEIPT_SHA': pointer['sha256'],
                        'PARENT_RECEIPT_SIZE': pointer['size'], 'WORKPRINT_SHA': _sha(payload), 'WORKPRINT_SIZE': len(payload)}.items():
        monkeypatch.setattr(recovery, name, value)
    case.work = leaf_work.parent / '55555555-5555-4555-8555-555555555555_attempt_0'; case.work.mkdir()
    case.parent_pointer, case.leaf, case.metadata, case.actual = pointer, leaf, metadata, actual
    case.overrides = {index: f'Complete compact revised shot {index}; preserve the physical action and exact subject.' for index in recovery.REPAIRS}
    case.story.reset_mock(); case.visual.reset_mock(); case.render.normalize_clip.reset_mock()
    case.reads.clear(); case.writes.clear()
    return case


def _prepare(case, **kwargs):
    return recovery.prepare_repaired_visual_recovery(LEAF, kwargs.get('parent_pointer', case.parent_pointer), case.work,
        repair_scene_indices=kwargs.get('repairs', recovery.REPAIRS), shot_prompt_overrides=kwargs.get('overrides', case.overrides))


def _save(case):
    case.client.set(studio_state.JOB_PREFIX + LEAF, json.dumps(case.leaf))


def test_incremental_actual_raws_and_same_voice_receive_fresh_review_before_one_shot_checkpoint(case):
    before = _snapshot(case)
    leaf, parent, _ = recovery._state(case.client)
    recovery._evidence(case.storage, leaf, parent, case.parent_pointer)
    case.reads.clear()
    pointer = _prepare(case)
    assert _snapshot(case) == before
    receipt = _record(case, pointer)
    audit = _record(case, receipt['audit_pointer'])
    assert audit['status'] == 'retained_visuals_passed' and len(audit['retained_visual_reviews']) == 6
    assert [asset['origin_task_id'] for asset in audit['assets']] == [PARENT, LEAF, PARENT, LEAF, LEAF, LEAF]
    assert [asset['sha256'] for asset in audit['assets']] == [_sha(raw) for raw in case.actual]
    package = receipt['approved_package']
    assert package['_recovered_generated_media']['version'] == 4
    assert package['_recovered_generated_media']['repair_scene_indices'] == [1, 4, 5]
    assert set(package['_recovered_generated_media']['scenes']) == {'0', '2', '3'}
    for index in (0, 2, 3):
        entry = package['_recovered_generated_media']['scenes'][str(index)][0]
        assert case.objects[entry['key']][0] == case.actual[index]
    assert case.objects[package['_recovered_voice']['key']][0] == case.audio
    assert package['studio_options']['production_profile_revision'] == 'frozen-revision'
    assert receipt['qa_approved'] is False and receipt['reusable'] is False
    case.story.assert_called_once(); case.visual.assert_called_once()
    assert [call.args[3] for call in case.render.normalize_clip.call_args_list] == list(range(6))
    assert case.writes[0] == receipt['audit_pointer']['key']
    assert recovery.publish_repaired_visual_recovery(pointer)['status'] == 'checkpoint_published'
    after = studio_state.get_job(LEAF)
    assert {key: value for key, value in after.items() if key not in {'updated_at', 'repair_available'}} == {
        key: value for key, value in case.leaf.items() if key not in {'updated_at', 'repair_available'}}
    for key in before:
        if key != studio_state.JOB_PREFIX + LEAF: assert case.client.dump(key) == before[key]
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda index: studio_state.claim_retry_dispatch(LEAF, PREP if index else
            '66666666-6666-4666-8666-666666666666', ('a' if index else 'b') * 32, allow_repair=True), (0, 1)))
    assert sum(claim['claimed'] for claim in claims) == 1
    assert next(claim for claim in claims if claim['claimed'])['checkpoint'] == receipt
    after = _snapshot(case)
    with pytest.raises(recovery.RepairedVisualRecoveryError): recovery.publish_repaired_visual_recovery(pointer)
    assert _snapshot(case) == after


@pytest.mark.parametrize('index', [0, 2, 3])
@pytest.mark.parametrize('field,value', [('score', 30), ('identity_gate_passed', False), ('evidence_gate_passed', False)])
def test_every_retained_clip_must_freshly_pass_and_rejection_evidence_is_durable(case, index, field, value):
    case.reviews[index][field] = value
    before = _snapshot(case)
    with pytest.raises(recovery.RepairedVisualRecoveryError) as caught: _prepare(case)
    audit = _record(case, caught.value.diagnostic_pointer)
    assert audit['status'] == 'retained_visuals_rejected'
    assert len(audit['retained_visual_reviews']) == 6 and len(case.writes) == 1
    assert _snapshot(case) == before


def test_declared_bad_repair_is_observed_not_falsely_approved(case):
    for index in recovery.REPAIRS:
        case.reviews[index].update(score=30, evidence_gate_passed=False, identity_gate_passed=False)
    receipt = _record(case, _prepare(case))
    assert [row['review']['score'] for row in receipt['retained_visual_reviews']] == [90, 30, 90, 90, 30, 30]
    assert set(receipt['approved_package']['_recovered_generated_media']['scenes']) == {'0', '2', '3'}


@pytest.mark.parametrize('damage', ['leaf_paid6', 'leaf_counter', 'parent_paid', 'parent_spec', 'parent_child',
                                   'parent_claimed', 'dispatch_state', 'dispatch_mode', 'child_token', 'execution',
                                   'consumed', 'leaf_claim', 'leaf_checkpoint', 'journal_count', 'journal_duplicate',
                                   'journal_wrong_origin', 'journal_ambiguous_phase', 'audio_hash', 'missing_parent'])
def test_state_binding_failures_stop_before_storage_or_models(case, damage):
    if damage == 'leaf_paid6': case.client.hset(studio_state.PAID_CREATE_BUDGET_PREFIX + LEAF, 'used', '6')
    if damage == 'leaf_counter': case.leaf['paid_create_slots_used'] = True; _save(case)
    if damage == 'parent_paid': case.client.hset(studio_state.PAID_CREATE_BUDGET_PREFIX + PARENT, 'used', '4')
    if damage in {'parent_spec', 'parent_child', 'parent_claimed'}:
        parent = studio_state.get_job(PARENT)
        parent[{'parent_spec': 'spec', 'parent_child': 'retry_child_task_id', 'parent_claimed': 'repair_claimed'}[damage]] = False
        case.client.set(studio_state.JOB_PREFIX + PARENT, json.dumps(parent))
    if damage in {'dispatch_state', 'dispatch_mode'}:
        case.client.hset(studio_state.RETRY_DISPATCH_PREFIX + PARENT, 'state' if damage == 'dispatch_state' else 'mode', 'wrong')
    if damage == 'child_token': case.client.hset(studio_state.RETRY_CHILD_CLAIM_PREFIX + LEAF, 'token', 'wrong')
    if damage == 'execution': case.client.set(studio_state.RETRY_CHILD_EXECUTION_PREFIX + LEAF, 'wrong')
    if damage == 'consumed': case.client.set(studio_state.REPAIR_CHECKPOINT_CLAIM_PREFIX + PARENT, 'wrong')
    if damage in {'leaf_claim', 'leaf_checkpoint'}:
        case.client.set((studio_state.REPAIR_CHECKPOINT_CLAIM_PREFIX if damage == 'leaf_claim' else studio_state.REPAIR_CHECKPOINT_PREFIX) + LEAF, 'existing')
    if damage == 'journal_count': case.leaf['generated_asset_candidates']['attempted_count'] = 6; _save(case)
    if damage == 'journal_duplicate': case.leaf['generated_asset_candidates']['entries'][1] = deepcopy(case.leaf['generated_asset_candidates']['entries'][0]); _save(case)
    if damage == 'journal_wrong_origin': case.leaf['generated_asset_candidates']['entries'][0]['source_task_id'] = PARENT; _save(case)
    if damage == 'journal_ambiguous_phase': case.leaf['generated_asset_candidates']['entries'][0]['phase'] = 'final_repair'; _save(case)
    if damage == 'audio_hash': case.leaf['audio_candidate_checkpoint']['audio_sha256'] = '0' * 64; _save(case)
    if damage == 'missing_parent': case.client.delete(studio_state.JOB_PREFIX + PARENT)
    before = _snapshot(case)
    with pytest.raises(recovery.RepairedVisualRecoveryError): _prepare(case)
    assert _snapshot(case) == before and not case.reads and not case.writes
    case.story.assert_not_called(); case.visual.assert_not_called()


@pytest.mark.parametrize('damage', ['parent_hash', 'workprint_raw', 'raw_bytes', 'metadata_bytes', 'speech', 'sources', 'retained_prompt',
                                   'oversized_prompt', 'extra_repair', 'missing_override', 'wrong_provider'])
def test_wrong_artifact_or_rewritten_contract_cannot_publish(case, damage):
    pointer, overrides = deepcopy(case.parent_pointer), deepcopy(case.overrides)
    if damage == 'parent_hash': pointer['sha256'] = '0' * 64
    if damage in {'workprint_raw', 'metadata_bytes'}:
        key = case.leaf['qa_workprint']['metadata_key']
        body = case.objects[key][0]
        if damage == 'metadata_bytes': case.objects[key] = body[:-1] + b'x', 'application/json'
        else:
            case.leaf['qa_workprint']['metadata_sha256'] = '0' * 64; _save(case)
    if damage == 'raw_bytes':
        key = case.leaf['generated_asset_candidates']['entries'][0]['raw_key']
        case.objects[key] = b'changed bytes' * 200, 'video/mp4'
    if damage in {'speech', 'sources', 'retained_prompt'}:
        original = case.story.side_effect
        def changed(*args, **kwargs):
            value = original(*args, **kwargs)
            if damage == 'speech': value['scenes'][0]['narration'] += ' new words'
            if damage == 'sources': value['sources'] = []
            if damage == 'retained_prompt': value['scenes'][3]['ai_prompt'] += ' new visual'
            return value
        case.story.side_effect = changed
    if damage == 'oversized_prompt': overrides[5] = 'x' * 1001
    if damage == 'missing_override': overrides.pop(1)
    if damage == 'wrong_provider': case.leaf['generated_asset_candidates']['entries'][0]['provider'] = 'gemini_image_motion'; _save(case)
    before = _snapshot(case)
    with pytest.raises(recovery.RepairedVisualRecoveryError):
        _prepare(case, parent_pointer=pointer, overrides=overrides, repairs=(0, 1, 4, 5) if damage == 'extra_repair' else recovery.REPAIRS)
    assert _snapshot(case) == before
    if damage not in {'speech', 'sources', 'retained_prompt'}: case.story.assert_not_called()


@pytest.mark.parametrize('target', list(range(12)))
def test_each_parent_leaf_and_claim_snapshot_is_watched_at_publication(case, target):
    pointer = _prepare(case)
    key = recovery._keys()[target]
    original = case.storage.get_object
    fired = False
    def racing(**kwargs):
        nonlocal fired
        result = original(**kwargs)
        if not fired and kwargs['Key'] == case.parent_pointer['key']:
            fired = True
            if case.client.type(key) == 'hash': case.client.hset(key, 'race', 'changed')
            else: case.client.set(key, 'concurrent-change')
        return result
    case.storage.get_object = racing
    with pytest.raises(recovery.RepairedVisualRecoveryError): recovery.publish_repaired_visual_recovery(pointer)
    assert fired
    if key != studio_state.REPAIR_CHECKPOINT_PREFIX + LEAF:
        assert not case.client.exists(studio_state.REPAIR_CHECKPOINT_PREFIX + LEAF)
    if key != studio_state.JOB_PREFIX + LEAF: assert studio_state.get_job(LEAF) == case.leaf


def test_shuffled_four_entry_journal_is_mapped_by_exact_scene_index(case):
    case.leaf['generated_asset_candidates']['entries'].reverse(); _save(case)
    receipt = _record(case, _prepare(case))
    assert [entry['sha256'] for entry in receipt['assets']] == [_sha(raw) for raw in case.actual]


def test_uncertain_model_error_is_audited_without_checkpoint_or_replay(case):
    case.visual.side_effect = RuntimeError('secret provider url must not escape')
    before = _snapshot(case)
    with pytest.raises(recovery.RepairedVisualRecoveryError) as caught: _prepare(case)
    assert 'secret' not in str(caught.value)
    audit = _record(case, caught.value.diagnostic_pointer)
    assert audit['status'] == 'visual_review_unavailable' and _snapshot(case) == before
    assert 'secret provider' not in json.dumps(audit)
    case.visual.assert_called_once()
