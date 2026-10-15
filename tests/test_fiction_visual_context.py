"""A reported fictional theft is not an on-screen vanishing demonstration."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.services import visual_qc as visual, strict_visual_review_semantics as pure
from app.services import framecase_pipeline as pipeline
from test_visual_qc import _review, JPEG_BYTES


SCENES = [
    {'index': 0, 'narration': "Every clock agreed. One watch didn't. At midnight, a painting vanished from the gallery.",
     'ai_prompt': 'Mira compares a cracked ticking watch beside the moving city clock.'},
    {'index': 1, 'narration': 'A shadow left through the side door.',
     'ai_prompt': 'The guard points at the empty painting frame; a shadow leaves the gallery.'}]


def test_only_reported_past_fiction_changes_automatic_removal_requirement():
    assert visual._state_change_required(SCENES[0])
    assert visual._state_change_required(SCENES[0], content_style='documentary')
    assert not visual._state_change_required(SCENES[0], content_style='original_animation')
    for change in ({'ai_prompt': 'A hand removes the painting from its frame.'},
                   {'narration': 'The painting vanishes now.'},
                   {'narration': 'He erases a chalk line.'},
                   {'narration': 'A painting vanished. She removes a note.'}):
        assert visual._state_change_required({**SCENES[0], **change}, content_style='original_animation')


@pytest.mark.parametrize('defect', [None, 'major_visual_artifact_visible',
    'effectively_static_or_frozen', 'authored_identity_or_material_conflict_visible',
    'state_change_applicable'])
def test_full_fiction_request_and_parser_keep_actual_negative_evidence(tmp_path, monkeypatch, defect):
    settings = SimpleNamespace(studio_visual_qc_provider='gemini', studio_plan_provider='gemini',
        gemini_api_key='synthetic', gemini_model='synthetic', openai_api_key='')
    monkeypatch.setattr(visual, 'settings', settings)
    frame = tmp_path / 'sample.jpg'; frame.write_bytes(JPEG_BYTES)
    samples, calls = [], []
    def sample(path, target, fraction):
        samples.append({'scene_index': int(path[0]), 'candidate_index': 0,
            'moment_index': visual.MOMENT_FRACTIONS.index(fraction), 'jpeg': JPEG_BYTES})
        return frame
    raw = [_review(i, reason='The supplied clue and neighboring empty frame establish the earlier theft.') for i in range(2)]
    if defect:
        raw[0][defect] = True
    before = deepcopy(raw)
    def request(*args, **kwargs):
        calls.append(args)
        return {'reviews': deepcopy(raw)}
    monkeypatch.setattr(visual, '_frame', sample)
    monkeypatch.setattr(visual, '_request_visual_review', request)
    specs = [[{'path': f'{i}.mp4', 'generated': True, 'source_type': 'generated'}] for i in range(2)]
    result = visual.review_scene_visuals(SCENES, specs, tmp_path, 2, content_style='original_animation',
        story_scenes=SCENES, _missing_review_attempts=0, _score_reason_consistency_attempts=0,
        _temporal_response_repair_attempts=0)
    assert len(calls) == 1 and raw == before
    instruction = calls[0][2]
    assert 'SCOPED ORIGINAL ANIMATION STORY CONTEXT' in instruction
    assert 'STATE_CHANGE_REQUIRED_SCENE_IDS: []' in instruction
    assert 'vanished from the gallery' in str(calls[0][3])  # Narration is never edited for review.
    derived = pure.derive_strict_visual_review_contract(SCENES, specs, samples=samples,
        story_scenes=SCENES, content_style='original_animation')
    assert derived['request']['system_instruction'] == instruction
    parsed = pure.normalize_strict_visual_reviews({'reviews': raw}, **derived['semantic_arguments'])
    assert parsed[0]['score'] == result['reviews'][0]['score']
    if defect:
        assert result['reviews'][0][defect] is True and result['reviews'][0]['score'] <= 40
    else:
        assert result['reviews'][0]['score'] == 92


def test_new_rubric_requires_fresh_review_and_preserves_old_rejection():
    previous = {'reviews': [{'scene_index': 0, 'score': 35, 'major_visual_artifact_visible': True}]}
    cp = {'visual_qc': deepcopy(previous), 'review_master_sha256': 'a' * 64}
    pipeline.prepare_visual_review(cp, 'a' * 64)
    assert 'visual_qc' not in cp
    assert next(iter(cp['visual_review_history'].values())) == {
        'master_sha256': 'a' * 64, 'version': 'original-v1', 'verdict': previous}
    history = deepcopy(cp['visual_review_history'])
    cp.update(visual_qc=deepcopy(previous), visual_review_version=pipeline.VISUAL_REVIEW_VERSION)
    pipeline.prepare_visual_review(cp, 'a' * 64)
    assert cp['visual_qc'] == previous  # A same-version rejection is not retried or cleared.
    pipeline.prepare_visual_review(cp, 'b' * 64)
    assert 'visual_qc' not in cp and all(cp['visual_review_history'][k] == v for k, v in history.items())
