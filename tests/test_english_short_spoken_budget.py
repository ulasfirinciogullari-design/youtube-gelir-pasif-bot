"""Measured EN planning budget is scoped, persisted and never an audio approval."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from test_director import FakeClient
from test_documentary_evidence_planning import TOPIC, directed, generated, package, reviewed
from app.services import director, research


BUDGET = {
    'version': 1, 'profile': 'fresh_en_30s_v1', 'language': 'en',
    'duration_minutes': 0.5, 'target_words': 65, 'minimum_words': 62, 'maximum_words': 66,
}
OPTIONS = {
    'mode': 'production', 'format': 'shorts', 'production_scheduled': True,
    'content_style': 'documentary', 'visual_mix': 'real_first',
}


@pytest.fixture(autouse=True)
def isolated_planner(monkeypatch):
    settings = SimpleNamespace(studio_plan_provider='openai', openai_api_key='mock-only',
                               openai_model='mock-only', gemini_critic_enabled=False)
    monkeypatch.setattr(director, 'settings', settings)
    monkeypatch.setattr(research, 'settings', settings)
    monkeypatch.setattr(director, '_studio_plan_provider', lambda: 'openai')
    monkeypatch.setattr(director, '_studio_plan_openai_model', lambda: 'mock-only')


def approve(monkeypatch):
    value = package(calibrated=True)
    assert director._word_count(value['narration']) == 65
    client = FakeClient([directed(value), generated(value), reviewed()])
    monkeypatch.setattr(director, 'OpenAI', lambda **kwargs: client)
    result = director.direct_and_qc(value, TOPIC, .5, 'en', OPTIONS, fresh_scheduled=True)
    return result, client


def test_fresh_english_research_and_both_director_writers_share_real_budget(monkeypatch):
    payload = package(calibrated=True)
    payload['spoken_word_budget'] = {'target_words': 900, 'approved': True}
    research.settings.studio_plan_provider = 'gemini'
    research.settings.gemini_api_key = 'mock-only'
    research.settings.gemini_model = 'mock-only'
    model = Mock(return_value=deepcopy(payload))
    monkeypatch.setattr(research, 'generate_gemini_json', model)
    draft = research.research_and_script(TOPIC, .5, 'en', OPTIONS, fresh_scheduled=True)
    assert draft['spoken_word_budget'] == BUDGET
    assert draft['target_word_range'] == [62, 66]
    assert 'HARD NARRATION BUDGET: 62-66 total spoken words; aim for 65.' in model.call_args.args[0]
    result, client = approve(monkeypatch)
    assert result['spoken_word_budget'] == BUDGET
    assert result['target_word_range'] == [62, 66]
    assert result['narration_word_count'] == 65
    assert director.short_story_package_is_approved(result, TOPIC)
    assert [director._word_count(row['narration']) for row in result['scenes']] == [11] * 5 + [10]
    assert 'HARD spoken-word budget: 62-66; aim for 65.' in client.responses.calls[0]['input']
    assert '"whole_story_word_budget": {"minimum": 62, "target": 65, "maximum": 66}' in client.responses.calls[1]['input']
    for request in (model.call_args.args[0], client.responses.calls[0]['input'], client.responses.calls[1]['input']):
        assert 'actual synthesized duration, transcript accuracy' in request
        assert 'independent prosody review remain authoritative' in request
        assert 'scene at 5-11 words' in request
    assert result['stock_scene_qc']['critic_calls'] == 1
    assert len(client.responses.calls) == 3


@pytest.mark.parametrize('duration,language,options,fresh', [
    (.5, 'en', OPTIONS, False), (.5, 'en', OPTIONS, 1), (.5, 'en', OPTIONS, 'true'),
    (.5, 'en', {**OPTIONS, 'production_scheduled': 1}, True),
    (.5, 'en', {**OPTIONS, 'production_scheduled': False}, True),
    (.5, 'en', {**OPTIONS, 'mode': 'preview'}, True),
    (.5, 'en', {**OPTIONS, 'format': 'landscape'}, True),
    (.5, 'tr', OPTIONS, True), (.5, 'de', OPTIONS, True), (.5, 'en-US', OPTIONS, True),
    (1.0, 'en', OPTIONS, True),
])
def test_noneligible_or_options_only_scope_keeps_legacy_budget(duration, language, options, fresh):
    assert director._fresh_spoken_word_budget(duration, language, options, fresh) is None
    assert director._target_word_budget(.5) == (56, 52, 60)
    assert director._target_word_budget(.5, allow_legacy_short_lock=True) == (56, 40, 60)
    assert director._target_word_budget(.5, calibrated_short_words=51) == (51, 48, 54)


def test_exact_user_narration_never_gets_automatic_expansion():
    assert director._fresh_spoken_word_budget(.5, 'en', OPTIONS, True,
                                              exact_narration='Keep exactly these words.') is None
    with pytest.raises(ValueError, match='Unsupported calibrated narration budget'):
        director._target_word_budget(.5, calibrated_short_words=65)
    with pytest.raises(ValueError, match='Unsupported combined narration budget'):
        director._target_word_budget(.5, calibrated_short_words=51, spoken_word_budget=BUDGET)


@pytest.mark.parametrize('value', [
    None, {}, {**BUDGET, 'version': True}, {**BUDGET, 'target_words': 65.0},
    {**BUDGET, 'minimum_words': 60}, {**BUDGET, 'maximum_words': 90},
    {**BUDGET, 'duration_minutes': '0.5'}, {**BUDGET, 'approved': True},
    {**BUDGET, 'language': 'tr'}, {**BUDGET, 'profile': 'unknown'},
])
def test_fixed_record_rejects_unknown_or_flexible_profiles(value):
    with pytest.raises(ValueError, match='Unsupported spoken-word budget profile'):
        director.validate_spoken_word_budget(value)


def test_valid_record_is_copied_without_qa_authority():
    result = director.validate_spoken_word_budget(BUDGET)
    assert result == BUDGET and result is not BUDGET
    assert not any(key in result for key in ('approved', 'accepted', 'publish_eligible', 'audio_qc'))


def test_mutable_model_marker_cannot_activate_a_legacy_director_budget(monkeypatch):
    value = package()
    value['spoken_word_budget'] = deepcopy(BUDGET)
    client = FakeClient([directed(value), generated(value), reviewed()])
    monkeypatch.setattr(director, 'OpenAI', lambda **kwargs: client)
    result = director.direct_and_qc(value, TOPIC, .5, 'en', OPTIONS)
    assert result['target_word_range'] == [52, 60]
    assert 'spoken_word_budget' not in result
    assert value['spoken_word_budget'] == BUDGET
    assert director.short_story_package_is_approved(result, TOPIC)


@pytest.mark.parametrize('damage', ['remove', 'change', 'unknown', 'range'])
def test_new_budget_is_bound_into_story_approval(monkeypatch, damage):
    value, _ = approve(monkeypatch)
    before = director._short_story_fingerprint(value)
    if damage == 'remove': value.pop('spoken_word_budget')
    elif damage == 'change': value['spoken_word_budget']['target_words'] = 56
    elif damage == 'unknown': value['spoken_word_budget']['publish_eligible'] = True
    else: value['target_word_range'] = [52, 60]
    assert not director.short_story_package_is_approved(value, TOPIC)
    if damage != 'range': assert director._short_story_fingerprint(value) != before


def test_saved_verified_candidate_revalidates_exact_65_words_without_rewriting(monkeypatch):
    value, _ = approve(monkeypatch)
    before = deepcopy(value)
    narrations = [row['narration'] for row in value['scenes']]
    output = generated(value)
    for row in output['scenes']:
        row['narration'] = 'Do not accept these substituted spoken words.'
    client = FakeClient([output, reviewed()])
    monkeypatch.setattr(director, 'OpenAI', lambda **kwargs: client)
    monkeypatch.setattr(director, '_run_director', Mock(side_effect=AssertionError('No new story')))
    result = director.revalidate_immutable_short_story(
        value, TOPIC, .5, 'en', OPTIONS, immutable_candidate_narrations=narrations,
        verified_spoken_word_budget=deepcopy(BUDGET),
    )
    assert value == before
    assert result['narration'] == before['narration']
    assert [row['narration'] for row in result['scenes']] == narrations
    assert result['spoken_word_budget'] == BUDGET
    assert result['target_word_range'] == [62, 66]
    assert director.short_story_package_is_approved(result, TOPIC)
    assert len(client.responses.calls) == 2
    assert 'Do not accept these substituted spoken words.' not in client.responses.calls[1]['input']


@pytest.mark.parametrize('damage', ['marker_only', 'kwarg_only', 'marker_mismatch', 'marker_bool', 'language',
                                  'unscheduled', 'preview', 'duration'])
def test_immutable_calibration_requires_matching_verified_scope_before_models(monkeypatch, damage):
    value = package(calibrated=True)
    value['spoken_word_budget'] = deepcopy(BUDGET)
    kwargs = {'verified_spoken_word_budget': deepcopy(BUDGET)}
    options, language, duration = deepcopy(OPTIONS), 'en', .5
    if damage == 'marker_only': kwargs.clear()
    if damage == 'kwarg_only': value.pop('spoken_word_budget')
    if damage == 'marker_mismatch': value['spoken_word_budget']['target_words'] = 99
    if damage == 'marker_bool': value['spoken_word_budget']['version'] = True
    if damage == 'language': language = 'tr'
    if damage == 'unscheduled': options['production_scheduled'] = False
    if damage == 'preview': options['mode'] = 'preview'
    if damage == 'duration': duration = 1.0
    factory = Mock(side_effect=AssertionError('Must reject before model allocation'))
    monkeypatch.setattr(director, 'OpenAI', factory)
    with pytest.raises((ValueError, RuntimeError)):
        director.revalidate_immutable_short_story(
            value, TOPIC, duration, language, options,
            immutable_candidate_narrations=[row['narration'] for row in value['scenes']], **kwargs)
    factory.assert_not_called()


@pytest.mark.parametrize('word_count,accepted', [(61, False), (62, True), (65, True), (66, True), (67, False)])
def test_even_a_new_attestation_requires_fixed_word_range_and_stock_cap(monkeypatch, word_count, accepted):
    value, _ = approve(monkeypatch)
    last = value['scenes'][-1]
    words = last['narration'].split()
    delta = word_count - 65
    if delta < 0: words = words[:delta]
    elif delta > 0: words.extend(['here'] * delta)
    last['narration'] = last['tts_text'] = ' '.join(words)
    value['narration'] = value['tts_narration'] = ' '.join(row['narration'] for row in value['scenes'])
    value['narration_word_count'] = word_count
    value['short_story_qc']['fingerprint'] = director._short_story_fingerprint(value)
    assert director._word_count(value['narration']) == word_count
    assert director.short_story_package_is_approved(value, TOPIC) is accepted


def test_calibrated_immutable_story_still_rejects_a_false_source_gate(monkeypatch):
    value = package(calibrated=True)
    value['spoken_word_budget'] = deepcopy(BUDGET)
    client = FakeClient([generated(value), reviewed(story_failures=['causal_claim_supported'])])
    monkeypatch.setattr(director, 'OpenAI', lambda **kwargs: client)
    with pytest.raises(RuntimeError, match='incoherent short-preview'):
        director.revalidate_immutable_short_story(
            value, TOPIC, .5, 'en', OPTIONS,
            immutable_candidate_narrations=[row['narration'] for row in value['scenes']],
            verified_spoken_word_budget=BUDGET,
        )
    assert len(client.responses.calls) == 2
