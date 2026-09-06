"""Future creative guidance is not a retrospective QA or timing gate."""
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest

from test_director import FakeClient, _scene, critic_payload
from app.services import director


MARKER = 'NEW VISUAL-STORY AUTHORING ONLY'


@pytest.fixture(autouse=True)
def local_models_only(monkeypatch):
    monkeypatch.setattr(director, 'settings', SimpleNamespace(
        studio_plan_provider='openai', openai_api_key='mock-only', openai_model='mock-only',
        gemini_api_key='', gemini_critic_enabled=False,
    ))
    monkeypatch.setattr(director, '_studio_plan_provider', lambda: 'openai')
    monkeypatch.setattr(director, '_studio_plan_openai_model', lambda: 'mock-only')


def _package():
    narrations = [
        'Müşteri markette iki farklı makarna paketini eline alıp karşılaştırıyor.',
        'Paketlerin arkasındaki içerik listesini okuyarak aralarındaki temel farkı görüyor.',
        'Bir paketi elinde tutarken diğerini aldığı rafın üzerine bırakıyor.',
        'Seçtiği aynı paketi alışveriş sepetine koyarak market kasasına ilerliyor.',
        'Kasadaki görevli paketi tarıyor ve müşteri ödeme ekranını izliyor.',
    ]
    scenes = [_scene(index, text, [
        'customer comparing pasta packages at grocery shelf',
        'close up hands comparing pasta packages in supermarket',
    ]) for index, text in enumerate(narrations)]
    return {'title': 'Fixture shopping decision', 'description': 'Local fixture only.',
        'thumbnail_text': '', 'scenes': scenes, 'narration': ' '.join(narrations),
        'tts_narration': ' '.join(narrations), 'ai_scenes': [],
        'sources': [{'url': 'https://example.org/source', 'evidence': 'Fixture action evidence.'}]}


def _generated(package):
    return {'scenes': [{'position': index, 'narration': scene['narration'],
        'visual_queries': scene['visual_queries'], 'ai_prompt': None}
        for index, scene in enumerate(package['scenes'])]}


def test_five_sentence_preference_is_future_only_and_not_a_qa_gate():
    rule = director._HUMAN_CURIOSITY_RULE.split(MARKER, 1)[1]
    assert len([s for s in rule.split('.') if s.strip()]) == 5
    for phrase in (
        'new, unlocked plans', 'meaningful action or visual change in the first beat',
        'long question-mark hold or standalone date card', 'spoken action or relevant sourced subject',
        'consequence appear after its cause', 'Avoid repetitive heading-icon-slide framing',
        'integrate a material date briefly', 'within the same story', 'Keep natural speech and existing budgets',
        'no fixed-second target or acceptance gate', 'does not make an otherwise valid existing scene fail',
        'never rewrites locked or archived narration, shot plans or timing',
    ):
        assert phrase in rule


@pytest.mark.parametrize('correction', [False, True])
def test_initial_and_correction_authoring_receive_same_preference(correction):
    client = FakeClient([{'scenes': []}])
    director._run_director(client, {}, 'One useful source-backed story.', 'Turkish', .5,
        45, 42, 48, 5, {'mode': 'production', 'format': 'shorts', 'visual_mix': 'real_first'},
        correction=correction)
    call = client.responses.calls[0]
    assert director._HUMAN_CURIOSITY_RULE in call['input']
    assert 'HARD spoken-word budget: 42-48' in call['input']
    assert call['reasoning']['effort'] == ('medium' if correction else 'low')


@pytest.mark.parametrize('lock', ['exact', 'saved', 'compression'])
def test_existing_locked_story_survives_guidance_without_rewriting(lock):
    package = _package()
    before = deepcopy(package)
    topic = 'Keep this existing shopping story.'
    kwargs = {}
    if lock == 'exact':
        topic += ' Anlatım metni aynen şu olsun: “' + package['narration'] + '”'
    elif lock == 'saved':
        kwargs['immutable_candidate_narrations'] = [s['narration'] for s in package['scenes']]
    else:
        kwargs['immutable_original_shot_prompts'] = {}
    verdict = critic_payload(stock_positions=range(5), scene_count=5)
    client = FakeClient(([_generated(package)] if lock != 'compression' else []) + [verdict])
    result = director._repair_short_stock_scenes(client, package, 'Turkish', .5, topic, **kwargs)
    assert package == before
    assert result['narration'] == before['narration']
    assert [s['tts_text'] for s in result['scenes']] == [s['narration'] for s in before['scenes']]
    assert result['stock_scene_qc']['story_review']['accepted'] is True
    assert len(client.responses.calls) == (1 if lock == 'compression' else 2)
    for call in client.responses.calls:
        assert MARKER in call['input']
        assert 'does not make an otherwise valid existing scene fail' in call['input']


def test_preference_never_overrides_genuine_failed_story_review():
    package = _package()
    verdict = critic_payload(stock_positions=range(5), scene_count=5)
    verdict['story_review']['hook_payoff_same_promise'] = False
    verdict['story_review']['reason'] = 'The ending never answers the opening question.'
    client = FakeClient([_generated(package), verdict])
    with pytest.raises(RuntimeError):
        director._repair_short_stock_scenes(client, package, 'Turkish', .5, 'One shopping story.',
            allow_natural_language_repair=False)
    assert len(client.responses.calls) == 2
