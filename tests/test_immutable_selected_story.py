"""Fresh critic-only review preserves the complete selected-scene package."""
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import director
from test_scheduled_shot_prompt_preflight import TOPIC, SHORT, case as planning_case


@pytest.fixture
def case(planning_case, monkeypatch):
    planning_case.options['production_scheduled'] = False
    package = planning_case.package
    package['narration_word_count'] = 555  # Even historic derived metadata stays untouched.
    package['target_scene_count'] = 99
    package['director_qc'] = ['Original unrelated editorial note.']
    package['scenes'][1].pop('tts_text')  # A review must not fill an absent field.
    package['custom_metadata']['source_order'] = [4, 1]
    monkeypatch.setattr(director, '_run_director', Mock(side_effect=AssertionError('No writer')))
    return planning_case


def _review(case, **kwargs):
    return director.revalidate_immutable_short_story(
        case.package, TOPIC, .5, 'en', case.options,
        **{'immutable_candidate_narrations': [scene['narration'] for scene in case.package['scenes']],
           'immutable_scene_fields': True, **kwargs})


def _shape(prompt):
    assert prompt.startswith('Act as an independent')
    return json.JSONDecoder().raw_decode(prompt.split('Return ONLY JSON in exactly this shape:\n', 1)[1])[0]


def _route(case, count, kind):
    if count == 12:
        scenes = []
        for index in range(12):
            scene = deepcopy(case.package['scenes'][index % 6])
            scene.update(index=index, narration='Workers carry parcels through warehouses.')
            scenes.append(scene)
        case.package['scenes'] = scenes
        case.package['narration'] = ' '.join(scene['narration'] for scene in scenes)
    for index, scene in enumerate(case.package['scenes']):
        scene['ai_prompt'] = SHORT if kind == 'generated' or kind == 'mixed' and index % 2 else None


@pytest.mark.parametrize('count', [6, 12])
@pytest.mark.parametrize('kind', ['stock', 'mixed', 'generated'])
def test_existing_full_package_is_unchanged_except_new_qa(case, count, kind):
    _route(case, count, kind)
    before = deepcopy(case.package)
    out = _review(case)
    assert case.package == before
    assert {key: value for key, value in out.items() if key not in {'stock_scene_qc', 'short_story_qc'}} == {
        key: value for key, value in before.items() if key not in {'stock_scene_qc', 'short_story_qc'}}
    assert set(out) == set(before)
    assert out['scenes'] == before['scenes']
    assert out['stock_scene_qc']['generator_calls'] == out['stock_scene_qc']['attempts_used'] == 0
    assert out['stock_scene_qc']['critic_calls'] == 1
    assert case.events == ['critic'] and case.model.call_count == 1
    request = case.model.call_args
    assert request.kwargs['retry_once'] is False and request.kwargs['google_search'] is False
    assert 'IMMUTABLE SELECTED STORY REVIEW' in request.args[0]
    context = json.JSONDecoder().raw_decode(request.args[0].split('\n', 2)[2])[0]
    assert context['complete_immutable_scenes'] == before['scenes']
    assert context['sources'] == before['sources']
    assert director.short_story_package_is_approved(out, TOPIC)
    director._run_director.assert_not_called()


@pytest.mark.parametrize('area,key', [('story_review', 'causal_claim_supported'),
    ('story_review', 'all_explicit_brief_constraints_preserved'),
    ('story_review', 'natural_spoken_language'),
    ('ending_pair', 'same_actor_or_object_thread'), ('ending_pair', 'everyday_benefit_visible'),
    ('scene', 'common_stock_clip_feasible'), ('scene', 'queries_match_same_action')])
def test_actual_negative_critic_remains_terminal_without_writer_or_retry(case, area, key):
    def reject(prompt, **kwargs):
        output = _shape(prompt)
        row = output['scenes'][0] if area == 'scene' else output[area]
        row[key] = False
        if key == 'natural_spoken_language':
            row['natural_spoken_language_evidence'] = 'Scene 1 "Members pay" sounds unnatural.'
        return output
    case.model.side_effect = reject
    with pytest.raises(RuntimeError): _review(case)
    assert case.model.call_count == 1
    director._run_director.assert_not_called()


@pytest.mark.parametrize('failed_check', ['natural_spoken_language', 'all_explicit_brief_constraints_preserved'])
def test_direct_locked_helper_never_raises_an_automatic_writer_repair_signal(case, failed_check):
    def reject(prompt, **kwargs):
        result = _shape(prompt)
        result['story_review'][failed_check] = False
        if failed_check == 'natural_spoken_language':
            result['story_review']['natural_spoken_language_evidence'] = 'Scene 1 "Members pay" sounds unnatural.'
        return result
    case.model.side_effect = reject
    with pytest.raises(RuntimeError) as caught:
        director._repair_short_stock_scenes(None, case.package, 'English', .5, TOPIC,
            immutable_candidate_narrations=[scene['narration'] for scene in case.package['scenes']],
            immutable_scene_fields=True, allow_whole_story_repair=True)
    assert not isinstance(caught.value, (director._NaturalSpokenLanguageRepairRequired, director._WholeStoryRepairRequired))
    assert case.model.call_count == 1


@pytest.mark.parametrize('kind', ['stock', 'generated'])
def test_malformed_protocol_response_never_triggers_second_critic(case, kind):
    _route(case, 6, kind)
    case.model.side_effect = director.GeminiGenerationError('invalid provider payload')
    with pytest.raises(RuntimeError): _review(case)
    assert case.model.call_count == 1 and case.model.call_args.kwargs['retry_once'] is False
    director._run_director.assert_not_called()


@pytest.mark.parametrize('value', [None, 0, 1, 'true', {}, []])
def test_strict_boolean_option_rejected_at_both_entrypoints_without_provider(case, value):
    narrations = [scene['narration'] for scene in case.package['scenes']]
    with pytest.raises(RuntimeError, match='must be a boolean'):
        _review(case, immutable_scene_fields=value)
    with pytest.raises(RuntimeError, match='must be a boolean'):
        director._repair_short_stock_scenes(None, case.package, 'English', 1.0,
            immutable_scene_fields=value, immutable_candidate_narrations=narrations)
    case.model.assert_not_called()


@pytest.mark.parametrize('damage', ['missing', 'partial', 'different', 'wrong_count', 'wrong_duration'])
def test_complete_narration_mapping_and_short_scope_precede_any_critic(case, damage):
    texts = [scene['narration'] for scene in case.package['scenes']]
    if damage == 'missing': texts = None
    elif damage == 'partial': texts = texts[:-1]
    elif damage == 'different': texts[0] += ' changed'
    elif damage == 'wrong_count':
        case.package['scenes'] = case.package['scenes'][:5]
        texts = texts[:5]
        case.package['narration'] = ' '.join(texts)
    duration = 1.0 if damage == 'wrong_duration' else .5
    with pytest.raises(RuntimeError):
        director._repair_short_stock_scenes(None, case.package, 'English', duration,
            immutable_scene_fields=True, immutable_candidate_narrations=texts)
    case.model.assert_not_called()


@pytest.mark.parametrize('damage', ['queries', 'ai_prompt', 'tts_text', 'custom_scene', 'title', 'sources',
                                  'description', 'options', 'derived', 'new_key', 'in_place'])
def test_critic_adapter_cannot_return_or_mutate_locked_package_fields(case, monkeypatch, damage):
    actual = director._repair_short_stock_scenes
    before = deepcopy(case.package)
    def changed(*args, **kwargs):
        out = actual(*args, **kwargs)
        if damage == 'queries': out['scenes'][1]['visual_queries'][0] = 'a different stock query'
        elif damage == 'ai_prompt': out['scenes'][0]['ai_prompt'] += ' New action.'
        elif damage == 'tts_text': out['scenes'][1]['tts_text'] = out['scenes'][1]['narration']
        elif damage == 'custom_scene': out['scenes'][0]['custom_identity']['shirt'] = 'red'
        elif damage == 'title': out['title'] += ' Changed'
        elif damage == 'sources': out['sources'][0]['evidence'] += ' Additional claim.'
        elif damage == 'description': out['description'] = 'Changed'
        elif damage == 'options': out['studio_options']['quality_threshold'] = 0
        elif damage == 'derived': out['narration_word_count'] = 60
        elif damage == 'new_key': out['permit'] = True
        else:
            args[1]['title'] = 'In-place mutation after snapshot'
            out['title'] = args[1]['title']
        return out
    monkeypatch.setattr(director, '_repair_short_stock_scenes', changed)
    with pytest.raises(RuntimeError, match='changed immutable scene or package fields'):
        _review(case)
    assert case.model.call_count == 1 and case.package == before


def test_old_approval_cannot_satisfy_a_fresh_scene_review(case, monkeypatch):
    def echo(_client, candidate, *args, **kwargs):
        assert 'stock_scene_qc' not in candidate and 'short_story_qc' not in candidate
        return candidate
    monkeypatch.setattr(director, '_repair_short_stock_scenes', echo)
    with pytest.raises(RuntimeError, match='Fresh independent story attestation'):
        _review(case)
    case.model.assert_not_called()


def test_new_existing_asset_review_does_not_raise_media_cap_or_weaken_legacy_gate(case):
    _route(case, 12, 'generated')
    before = deepcopy(case.options)
    _review(case)
    assert case.options == before and 'max_ai_scene_count' not in case.package
    case.model.reset_mock()
    with pytest.raises(RuntimeError, match='authored paid-generation limit'):
        _review(case, immutable_scene_fields=False)
    case.model.assert_not_called()


def test_new_lock_does_not_bypass_scheduled_compression_scope_or_original_prompt_validation(case):
    with pytest.raises(director.ScheduledShotPromptError, match='original shot constraints'):
        _review(case, immutable_original_shot_prompts={0: SHORT})
    case.options['production_scheduled'] = True
    with pytest.raises(director.ScheduledShotPromptError):
        _review(case, immutable_original_shot_prompts={0: 'changed\ninvalid'})
    case.model.assert_not_called()


def test_deterministically_invalid_frozen_query_fails_without_writer_or_critic(case):
    case.package['scenes'][1]['visual_queries'][0] = 'not-an-English-query!'
    with pytest.raises(RuntimeError, match='fully stock-safe'): _review(case)
    case.model.assert_not_called()
    director._run_director.assert_not_called()


def test_openai_critic_uses_zero_sdk_retry_and_no_stock_writer(case, monkeypatch):
    case.settings.studio_plan_provider = 'openai'
    client = Mock()
    client.responses.create.side_effect = lambda **request: SimpleNamespace(
        status='completed', output_text=json.dumps(_shape(request['input'])))
    factory = Mock(return_value=client)
    monkeypatch.setattr(director, 'OpenAI', factory)
    out = _review(case)
    factory.assert_called_once_with(api_key='mock-only', timeout=90.0, max_retries=0)
    client.responses.create.assert_called_once()
    assert out['stock_scene_qc']['generator_calls'] == 0
    assert out['stock_scene_qc']['critic_calls'] == 1
    case.model.assert_not_called()
