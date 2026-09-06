"""Exact-source three-scene repair is explicit and never an approval shortcut."""
from copy import deepcopy
import json

import pytest

from app.services import failed_visual_recovery as recovery, studio_state
from test_failed_visual_recovery import case, SOURCE, CHILD, _snapshot, _receipt, _sha


def _prepare_three(case):
    case.good[:] = case.good[:3]
    return recovery.prepare_failed_visual_repair(SOURCE, case.pointer, case.work, repair_scene_indices=(0, 2, 5))


def _audit(case, pointer):
    return json.loads(case.objects[pointer['key']])


def test_explicit_three_scene_request_freshly_critiques_shooting_contract_and_only_retains_exact_three(case):
    before = _snapshot(case)
    pointer = _prepare_three(case)
    receipt = _receipt(case, pointer)
    package = receipt['approved_package']
    media = case.tasks._validated_recovered_generated_media(package['_recovered_generated_media'], 6, receipt['package_sha256'])
    assert media['version'] == 4 and media['repair_only'] is True
    assert media['repair_scene_indices'] == [0, 2, 5] and set(media['scenes']) == {1, 3, 4}
    assert _snapshot(case) == before and receipt['qa_approved'] is False
    assert package['_recovered_voice']['sha256'] == _sha(case.audio)
    submitted = case.story_review.call_args.args[0]
    for index in range(6):
        expected = deepcopy(case.package['scenes'][index])
        if index in {0, 2, 5}:
            expected['ai_prompt'] = recovery.REPAIR_SHOTS[index]
        assert submitted['scenes'][index] == package['scenes'][index] == expected
    assert '1974' in submitted['scenes'][0]['ai_prompt']
    assert 'ten-pack' in submitted['scenes'][2]['ai_prompt'] and 'NCR' in submitted['scenes'][2]['ai_prompt']
    assert 'present-day' in submitted['scenes'][5]['ai_prompt']
    assert [call.args[3] for call in case.render.normalize_clip.call_args_list] == [1, 3, 4]
    assert [round(call.args[2] * 30) for call in case.render.normalize_clip.call_args_list] == [150, 144, 147]
    assert case.visual_review.call_args.args[0] == [package['scenes'][index] for index in (1, 3, 4)]
    assert case.visual_review.call_args.kwargs['story_scenes'] == package['scenes']
    assert not any(key.endswith('scene-02-initial.mp4') for key in case.gets)
    audit = _audit(case, receipt['audit_pointer'])
    assert audit['diagnostic_only'] is True and audit['qa_approved'] is audit['reusable'] is False
    assert audit['status'] == 'retained_visuals_passed'
    assert [row['scene_index'] for row in audit['retained_visual_reviews']] == [1, 3, 4]
    assert audit['source_package']['scenes'] == case.package['scenes']
    assert audit['reviewed_package']['scenes'] == package['scenes']
    assert len(list(case.work.glob('failed-visual-audit-*.json'))) == 1
    assert case.puts.index(receipt['audit_pointer']['key']) < case.puts.index(pointer['prepared_key'])


def test_three_scene_receipt_publishes_only_leaf_and_normal_claim_keeps_original_paid_ledger(case):
    pointer = _prepare_three(case)
    before = _snapshot(case)
    outcome = recovery.publish_failed_visual_repair(pointer)
    assert outcome['repair_scene_indices'] == [0, 2, 5]
    for key, value in before.items():
        if key != studio_state.JOB_PREFIX + SOURCE:
            assert case.redis.dump(key) == value
    claimed = studio_state.claim_retry_dispatch(SOURCE, CHILD, 'a' * 32, allow_repair=True)
    assert claimed['claimed'] is True and claimed['mode'] == 'repair'
    media = claimed['checkpoint']['approved_package']['_recovered_generated_media']
    assert media['version'] == 4 and media['repair_scene_indices'] == [0, 2, 5]
    assert case.redis.hgetall(studio_state.PAID_CREATE_BUDGET_PREFIX + SOURCE) == {'cap': '6', 'used': '6'}
    with pytest.raises(recovery.FailedVisualRecoveryError):
        recovery.publish_failed_visual_repair(pointer)


@pytest.mark.parametrize('local_index', [0, 1, 2])
def test_any_bad_retained_clip_persists_all_exact_reports_before_rejecting(case, local_index):
    case.good[local_index].update(score=35, editorial_gate_passed=False, reason='Foreground package contains fake print.')
    before = _snapshot(case)
    with pytest.raises(recovery.FailedVisualRecoveryError) as caught:
        _prepare_three(case)
    assert _snapshot(case) == before
    evidence = caught.value
    audit = _audit(case, evidence.diagnostic_pointer)
    assert audit['status'] == 'retained_visuals_rejected'
    assert len(audit['retained_visual_reviews']) == 3
    assert evidence.diagnostics[0]['scene_index'] == (1, 3, 4)[local_index]
    assert audit['retained_visual_reviews'][local_index]['review']['score'] == 35
    assert all('failed_visual_audit_v1-' in key for key in case.puts)
    assert not case.redis.exists(studio_state.REPAIR_CHECKPOINT_PREFIX + SOURCE)
    with pytest.raises(recovery.FailedVisualRecoveryError):
        recovery.publish_failed_visual_repair(evidence.diagnostic_pointer)


@pytest.mark.parametrize('change', ['retained_prompt', 'repair_prompt', 'narration', 'query', 'scene_order'])
def test_critic_cannot_silently_rewrite_frozen_retained_scenes_or_declared_shooting_contract(case, change):
    original = case.story_review.side_effect
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        if change == 'retained_prompt': result['scenes'][1]['ai_prompt'] = 'Unrelated store'
        elif change == 'repair_prompt': result['scenes'][2]['ai_prompt'] = 'Modern payment terminal'
        elif change == 'narration': result['scenes'][2]['narration'] += ' Different words.'
        elif change == 'query': result['scenes'][3]['visual_queries'] = ['wrong location']
        else: result['scenes'].reverse()
        return result
    case.story_review.side_effect = changed
    with pytest.raises(recovery.FailedVisualRecoveryError) as caught:
        _prepare_three(case)
    assert _audit(case, caught.value.diagnostic_pointer)['status'] == 'story_review_rejected_or_unavailable'
    case.visual_review.assert_not_called()
    assert all('failed_visual_audit_v1-' in key for key in case.puts)


def test_audit_storage_loss_preserves_local_reports_and_cannot_prepare_or_claim(case):
    case.good[0]['score'] = 35
    original = case.storage.put_object
    def failed(**kwargs):
        if 'failed_visual_audit_v1-' in kwargs['Key']:
            raise RuntimeError('private storage internals')
        return original(**kwargs)
    case.storage.put_object = failed
    with pytest.raises(recovery.FailedVisualRecoveryError) as caught:
        _prepare_three(case)
    reports = list(case.work.glob('failed-visual-audit-*.json'))
    assert len(reports) == 1
    assert json.loads(reports[0].read_text())['retained_visual_reviews'][0]['review']['score'] == 35
    assert 'private storage internals' not in str(caught.value)
    assert case.puts == [] and not case.redis.exists(studio_state.REPAIR_CHECKPOINT_PREFIX + SOURCE)


@pytest.mark.parametrize('damage', ['audit_bytes', 'audit_failure', 'repair_partition', 'source_package', 'shot_contract'])
def test_publication_rejects_modified_audit_or_repair_contract_without_mutating_state(case, damage):
    pointer = _prepare_three(case)
    receipt = _receipt(case, pointer)
    audit_pointer = receipt['audit_pointer']
    if damage == 'audit_bytes':
        case.objects[audit_pointer['key']] += b' '
    elif damage in {'audit_failure', 'source_package'}:
        audit = _audit(case, audit_pointer)
        if damage == 'audit_failure': audit['status'] = 'retained_visuals_rejected'
        else: audit['source_package']['scenes'][1]['ai_prompt'] = 'Changed original'
        receipt['audit_pointer'] = recovery._store_audit(case.storage, case.work, audit)
        pointer = recovery._store_prepared(case.storage, receipt)
    else:
        media = receipt['approved_package']['_recovered_generated_media']
        if damage == 'repair_partition': media['repair_scene_indices'] = [0, 1, 5]
        else: receipt['approved_package']['scenes'][2]['ai_prompt'] = 'Modern PIN terminal'
        pointer = recovery._store_prepared(case.storage, receipt)
    before = _snapshot(case)
    with pytest.raises(recovery.FailedVisualRecoveryError):
        recovery.publish_failed_visual_repair(pointer)
    assert _snapshot(case) == before
