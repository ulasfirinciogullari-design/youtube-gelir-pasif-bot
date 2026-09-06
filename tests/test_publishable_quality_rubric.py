"""Fresh prompt guidance is not permission to reinterpret a recorded verdict."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import test_audio_qc as audio_tests
from test_director import FakeClient, critic_payload, director_module as director, make_short_package, valid_generator_payload
from test_visual_qc import JPEG_BYTES, _review
from app.services import visual_qc

audio_qc = audio_tests.audio_qc


@pytest.mark.parametrize('failure', [None, 'one_specific_useful_reveal', 'causal_claim_supported'])
def test_story_prompt_accepts_familiar_answers_without_overriding_actual_failures(monkeypatch, failure):
    monkeypatch.setattr(director, 'settings', SimpleNamespace(
        studio_plan_provider='openai', openai_api_key='mock-only', openai_model='mock-only',
        gemini_api_key='', gemini_critic_enabled=False,
    ))
    monkeypatch.setattr(director, '_studio_plan_provider', lambda: 'openai')
    monkeypatch.setattr(director, '_studio_plan_openai_model', lambda: 'mock-only')
    candidate = make_short_package()
    original = deepcopy(candidate)
    verdict = critic_payload(story_failures=[failure] if failure else [])
    original_verdict = deepcopy(verdict)
    client = FakeClient([valid_generator_payload(), verdict])
    if failure:
        with pytest.raises(RuntimeError):
            director._repair_short_stock_scenes(client, candidate, 'Turkish', .5)
    else:
        result = director._repair_short_stock_scenes(client, candidate, 'Turkish', .5)
        assert result['stock_scene_qc']['story_review']['accepted'] is True
    prompt = client.responses.calls[1]['input']
    for phrase in (
        'A familiar but useful explanation satisfies this gate',
        'novelty, surprise or a non-obvious reveal is not required',
        'a concise recap after a concrete answer is not that failure',
        'A missing answer, unsupported claim or filler-only ending still fails',
        'every explicit structural, routing, continuity',
        'false if the claim cannot be verified or overstates a source',
    ):
        assert phrase in prompt
    assert candidate == original and verdict == original_verdict
    assert len(client.responses.calls) == 2


@pytest.mark.parametrize('flag', [None, 'substantially_repeats_adjacent_scene', 'major_visual_artifact_visible'])
def test_visual_warning_preserves_scores_and_real_hard_gate_rejections(monkeypatch, tmp_path, flag):
    frame = tmp_path / 'mock-frame.jpg'
    frame.write_bytes(JPEG_BYTES)
    monkeypatch.setattr(visual_qc, '_frame', lambda *args: frame)
    monkeypatch.setattr(visual_qc, 'settings', SimpleNamespace(
        studio_visual_qc_provider='gemini', studio_plan_provider='gemini',
        gemini_api_key='mock-only', gemini_model='mock-only',
    ))
    reason = (
        'The shot repeats adjacent footage without progression.' if flag else
        'The relevant checkout footage illustrates a new source-backed detail. WARNING: similar framing.'
    )
    if flag == 'major_visual_artifact_visible':
        reason = 'A major visual artifact visibly deforms the checkout.'
    verdict = {'reviews': [_review(score=90, reason=reason, **({flag: True} if flag else {}))]}
    original = deepcopy(verdict)
    generate = Mock(return_value=verdict)
    monkeypatch.setattr(visual_qc, 'generate_gemini_multimodal_json', generate)
    result = visual_qc.review_scene_visuals(
        [{'narration': 'The checkout shows the item price.', 'visual_queries': ['checkout item price']}],
        [['mock-only.mp4']], tmp_path / 'review', _missing_review_attempts=0,
        _score_reason_consistency_attempts=0, _temporal_response_repair_attempts=0,
    )['reviews'][0]
    prompt = generate.call_args.kwargs['system_instruction']
    assert 'Similar framing, camera movement, shot grammar or recurring subject alone is not this failure' in prompt
    assert 'relevant B-roll can carry new concrete narration information' in prompt
    assert 'not flawless or unusually cinematic' in prompt
    assert 'never raise a score to force approval' in prompt
    assert 'misleading or fabricated text, rights concerns, major artifacts' in prompt
    assert result['score'] == (40 if flag else 90)
    if flag:
        assert result['raw_score'] == 90 and result['editorial_gate_passed'] is False
    else:
        assert result['reason'] == reason and result['editorial_gate_passed'] is True
    assert verdict == original and generate.call_count == 1


@pytest.mark.parametrize('language', ['tr', 'en'])
def test_prosody_minor_style_warning_is_preserved_with_honest_scores(monkeypatch, tmp_path, language):
    audio = tmp_path / 'mock-only.mp3'
    audio.write_bytes(b'mocked actual-audio input; no provider request')
    verdict = audio_tests.AudioQCTests()._prosody_output()
    verdict['summary'] = 'Clear and comfortable to follow. WARNING: slight accent.'
    original = deepcopy(verdict)
    generate = Mock(return_value=verdict)
    monkeypatch.setattr(audio_qc, 'generate_gemini_audio_json', generate)
    monkeypatch.setattr(audio_qc, 'settings', SimpleNamespace(gemini_api_key='mock-only', gemini_model='mock-only'))
    result = audio_qc.verify_audio_prosody(
        audio, 'Clear narration.' if language == 'en' else 'Anlaşılır anlatım.',
        audio_duration_seconds=29.5, language=language,
    )
    prompt = generate.call_args.kwargs['system_instruction']
    assert 'not a flawless broadcast audition' in prompt
    assert 'is a WARNING in summary rather than a blocking issue' in prompt
    assert 'Never excuse missing or substituted words' in prompt
    assert 'Every rejecting issue must use an allowed reason code' in prompt
    assert 'at least 70' in prompt and 'at most 30' in prompt
    assert result['pass'] is True and result['scores'] == verdict['scores']
    assert result['summary'] == verdict['summary'] and result['issues'] == []
    assert verdict == original and generate.call_count == 1


@pytest.mark.parametrize('scores', [{'naturalness': 69}, {'roboticness': 31}])
def test_new_style_guidance_cannot_approve_inconsistent_actual_scores(scores):
    verdict = audio_tests.AudioQCTests()._prosody_output(score_overrides=scores)
    original = deepcopy(verdict)
    assert audio_qc._validate_prosody_review(
        verdict, 'Doğru metin.', audio_duration_seconds=29.5, transcript_evidence=None,
    ) is None
    assert verdict == original


def test_grounded_negative_prosody_remains_negative_even_with_publishable_scores():
    helpers = audio_tests.AudioQCTests()
    verdict = helpers._prosody_output()
    verdict['pass'] = False
    verdict['summary'] = 'The audible phrase is broken.'
    verdict['issues'] = [{
        'code': 'choppy_phrase_grouping', 'start_seconds': 1.0, 'end_seconds': 1.8,
        'phrase': 'Doğru metin', 'detail': 'The phrase is audibly split and difficult to follow.',
    }]
    original = deepcopy(verdict)
    result = audio_qc._validate_prosody_review(
        verdict, 'Doğru metin.', audio_duration_seconds=29.5,
        transcript_evidence=helpers._prosody_transcript_evidence(('Doğru', 1.0, 1.4), ('metin', 1.4, 1.8)),
    )
    assert result['pass'] is False and result['scores'] == verdict['scores']
    assert result['issues'][0]['start_seconds'] == 1.0
    assert result['issues'][0]['end_seconds'] == 1.8
    assert verdict == original
