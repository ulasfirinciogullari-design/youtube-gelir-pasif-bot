"""Existing six-clip recovery authenticates, but never invents, EN calibration."""
from copy import deepcopy
import json

import pytest

from app.services import audio_checkpoint, preserved_visual_recovery as recovery, studio_state
from test_preserved_visual_recovery import (
    SOURCE, CHILD, case, _prepare, _record, _save, _sha, _snapshot,
)


BUDGET = {
    'version': 1, 'profile': 'fresh_en_30s_v1', 'language': 'en',
    'duration_minutes': 0.5, 'target_words': 65, 'minimum_words': 62, 'maximum_words': 66,
}
NARRATIONS = [
    'Why pay Costco before anything lands in your shopping cart?',
    'Your membership card buys access, not groceries or guaranteed shopping savings.',
    'Inside, limited selection and pallet displays help keep operating costs down.',
    'That efficiency helps Costco operate profitably while keeping merchandise margins low.',
    'Costco earns merchandise gross margin too, not just membership fee revenue.',
    'Annual access pays off only when savings outweigh your membership fee.',
]


def _calibrate(case):
    """Recreate real hashed fixture archives, not a loose in-memory marker."""
    case.package['spoken_word_budget'] = deepcopy(BUDGET)
    for scene, text in zip(case.package['scenes'], NARRATIONS):
        scene['narration'] = text
    assert len(' '.join(NARRATIONS).split()) == 65
    case.voice['spoken_texts'] = list(NARRATIONS)
    case.source['spec']['production_scheduled'] = True
    case.source['audio_candidate_checkpoint'] = audio_checkpoint.persist_audio_candidate_checkpoint(
        SOURCE, case.package, case.voice)['audio_candidate_checkpoint']
    original_work = case.work.parent / f'{SOURCE}_attempt_0'
    entries = []
    for index in range(6):
        entries.append(recovery.assets.persist_generated_asset_candidate(
            SOURCE, original_work, package=case.package, voice_result=case.voice,
            visual_spec={'path': str(original_work / f'runway_s{index:02d}.mp4'),
                         'generated': True, 'source_type': 'generated', 'generation_provider': 'gemini_veo',
                         'generation_provider_attempts': 1, 'start_fraction': 0.0,
                         'preserve_start_fraction': True, 'forbid_loop': True},
            scene_index=index, phase='initial_generation', options=recovery._options(case.source), duration_minutes=.5,
        ))
    case.source['generated_asset_candidates']['entries'] = entries
    _save(case)
    case.writes.clear(); case.reads.clear()


def _rewrite_manifest(case, index, change):
    pointer = case.source['generated_asset_candidates']['entries'][index]
    value = json.loads(case.objects[pointer['manifest_key']][0])
    change(value['package'])
    value['candidate_package_sha256'] = recovery._digest(value['package'])
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    pointer['manifest_sha256'] = _sha(raw)
    pointer['manifest_size'] = len(raw)
    pointer['manifest_key'] = f"generated_candidates/{SOURCE}/manifests/{_sha(raw)}.json"
    case.objects[pointer['manifest_key']] = (raw, 'application/json')
    _save(case)


@pytest.mark.parametrize('repairs', [(), (3,), (1, 3, 4, 5)])
def test_hashed_65_word_archives_forward_verified_budget_and_keep_one_use_private_checkpoint(case, repairs):
    _calibrate(case)
    before = _snapshot(case)
    pointer = _prepare(case, repair_scene_indices=repairs)
    assert _snapshot(case) == before
    supplied = case.story.call_args
    assert supplied.kwargs['verified_spoken_word_budget'] == BUDGET
    assert supplied.args[0]['spoken_word_budget'] == BUDGET
    assert supplied.kwargs['immutable_candidate_narrations'] == NARRATIONS
    assert supplied.args[0]['narration'] == ' '.join(NARRATIONS)
    case.story.assert_called_once(); case.visual.assert_called_once()
    receipt = _record(case, pointer)
    assert receipt['approved_package']['spoken_word_budget'] == BUDGET
    assert receipt['approved_package']['narration'] == ' '.join(NARRATIONS)
    assert receipt['qa_approved'] is False and receipt['requires_full_qa'] is True
    assert receipt['new_paid_create_requests'] == receipt['new_tts_requests'] == 0
    assert case.objects[receipt['approved_package']['_recovered_voice']['key']][0] == case.audio
    assert recovery.publish_preserved_visual_recovery(pointer)['status'] == 'checkpoint_published'
    dispatch = studio_state.claim_retry_dispatch(SOURCE, CHILD, 'a' * 32, allow_repair=True)
    assert dispatch['claimed'] is True and dispatch['mode'] == 'repair'
    checkpoint = dispatch['checkpoint']['approved_package']
    assert checkpoint['spoken_word_budget'] == BUDGET
    assert checkpoint['_recovered_generated_media']['version'] == (4 if repairs else 3)
    assert case.client.hgetall(studio_state.PAID_CREATE_BUDGET_PREFIX + SOURCE) == {'cap': '6', 'used': '6'}
    with pytest.raises(recovery.PreservedVisualRecoveryError):
        recovery.publish_preserved_visual_recovery(pointer)


@pytest.mark.parametrize('damage', ['tr', 'manual', 'not_scheduled', 'numeric_scheduled', 'preview', 'landscape'])
def test_marker_never_authorizes_an_ineligible_original_scope(case, damage):
    _calibrate(case)
    if damage == 'tr': case.source['spec']['language'] = 'tr'
    if damage == 'manual': case.source['spec'].pop('production_scheduled')
    if damage == 'not_scheduled': case.source['spec']['production_scheduled'] = False
    if damage == 'numeric_scheduled': case.source['spec']['production_scheduled'] = 1
    if damage == 'preview': case.source['spec']['mode'] = 'preview'
    if damage == 'landscape': case.source['spec']['format'] = 'landscape'
    _save(case)
    before = _snapshot(case)
    with pytest.raises(recovery.PreservedVisualRecoveryError): _prepare(case)
    case.story.assert_not_called(); case.visual.assert_not_called()
    assert _snapshot(case) == before and not case.writes


@pytest.mark.parametrize('damage', ['voice_absent', 'voice_bool', 'voice_changed', 'manifest_absent',
                                  'manifest_bool', 'all_manifest_absent', 'manifest_unknown'])
def test_every_authenticated_package_must_have_the_same_strict_marker(case, monkeypatch, damage):
    _calibrate(case)
    if damage.startswith('voice_'):
        loader = recovery.load_voice_retry_candidate
        def changed(*args):
            loaded = loader(*args)
            if damage == 'voice_absent': loaded['package'].pop('spoken_word_budget')
            if damage == 'voice_bool': loaded['package']['spoken_word_budget']['version'] = True
            if damage == 'voice_changed': loaded['package']['spoken_word_budget']['target_words'] = 56
            return loaded
        monkeypatch.setattr(recovery, 'load_voice_retry_candidate', changed)
    else:
        def changed(package):
            if damage in {'manifest_absent', 'all_manifest_absent'}: package.pop('spoken_word_budget')
            if damage == 'manifest_bool': package['spoken_word_budget']['version'] = True
            if damage == 'manifest_unknown': package['spoken_word_budget']['qa_approved'] = True
        for index in (range(6) if damage == 'all_manifest_absent' else (3,)):
            _rewrite_manifest(case, index, changed)
    before = _snapshot(case)
    with pytest.raises(recovery.PreservedVisualRecoveryError): _prepare(case)
    case.story.assert_not_called(); case.visual.assert_not_called()
    assert _snapshot(case) == before and not case.writes


@pytest.mark.parametrize('scheduled', [False, True])
def test_untrusted_job_marker_cannot_expand_a_legacy_candidate(case, scheduled):
    case.source['spoken_word_budget'] = deepcopy(BUDGET)
    if scheduled: case.source['spec']['production_scheduled'] = True
    _save(case)
    receipt = _record(case, _prepare(case))
    assert 'verified_spoken_word_budget' not in case.story.call_args.kwargs
    assert 'spoken_word_budget' not in receipt['approved_package']


def test_options_only_marker_cannot_select_a_budget(case):
    case.source['spec']['spoken_word_budget'] = deepcopy(BUDGET)
    _save(case)
    before = _snapshot(case)
    receipt = _record(case, _prepare(case))
    assert _snapshot(case) == before
    assert 'verified_spoken_word_budget' not in case.story.call_args.kwargs
    assert 'spoken_word_budget' not in receipt['approved_package']
    assert case.story.call_args.args[4]['spoken_word_budget'] == BUDGET


@pytest.mark.parametrize('damage', ['drop', 'change', 'bool', 'false_story', 'false_visual'])
def test_marker_does_not_waive_story_or_visual_approval(case, damage):
    _calibrate(case)
    if damage == 'false_story':
        case.story.side_effect = RuntimeError('Independent story source gate rejected')
    elif damage == 'false_visual':
        case.reviews[2]['score'] = 20
        case.reviews[2]['identity_gate_passed'] = False
    else:
        reviewer = case.story.side_effect
        def changed(*args, **kwargs):
            result = reviewer(*args, **kwargs)
            if damage == 'drop': result.pop('spoken_word_budget')
            if damage == 'change': result['spoken_word_budget']['target_words'] = 56
            if damage == 'bool': result['spoken_word_budget']['version'] = True
            return result
        case.story.side_effect = changed
    before = _snapshot(case)
    with pytest.raises(recovery.PreservedVisualRecoveryError) as caught: _prepare(case)
    assert _snapshot(case) == before
    audit = _record(case, caught.value.diagnostic_pointer)
    assert audit['status'] == ('visual_preparation_rejected' if damage == 'false_visual'
                               else 'story_review_rejected_or_unavailable')
    assert audit['qa_approved'] is False and audit['requires_full_qa'] is True
    assert not any('/prepared-' in key or '/raw/' in key for key in case.writes)
    if damage != 'false_visual': case.visual.assert_not_called()
