"""Fresh scheduled 30-second Shorts never plan a seventh scene that shot preparation rejects."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import director, research

SCHEDULED = {'mode': 'production', 'format': 'shorts', 'production_scheduled': True,
             'content_style': 'documentary', 'pace': 'balanced'}
UNSCHEDULED = {key: value for key, value in SCHEDULED.items() if key != 'production_scheduled'}


def _scene_bounds(schema):
    scenes = schema['properties']['scenes']
    return scenes['minItems'], scenes['maxItems']


@pytest.mark.parametrize('options,duration,fresh,expected', [
    (SCHEDULED, 0.5, True, 6),
    (SCHEDULED, 0.5, False, None),
    (UNSCHEDULED, 0.5, True, None),
    (SCHEDULED, 1, True, None),
    ({**SCHEDULED, 'format': 'long'}, 0.5, True, None),
])
def test_ceiling_only_for_fresh_scheduled_thirty_second_shorts(options, duration, fresh, expected):
    assert director._scheduled_short_scene_ceiling(options, duration, fresh) == expected


@pytest.mark.parametrize('build', [research._research_json_schema, director._director_json_schema])
def test_schema_caps_scene_range_without_changing_other_videos(build):
    assert _scene_bounds(build(6)) == (5, 7)
    assert _scene_bounds(build(6, scene_ceiling=6)) == (5, 6)
    assert _scene_bounds(build(7, scene_ceiling=6)) == (6, 6)
    assert _scene_bounds(build(5, scene_ceiling=6)) == (4, 6)
    assert _scene_bounds(build(6, exact_scene_count=True, scene_ceiling=6)) == (6, 6)


def test_scene_count_gate_rejects_a_seventh_scene_only_under_the_ceiling():
    assert director._scene_count_matches(7, 6, exact_scene_count=False) is True
    assert director._scene_count_matches(7, 6, exact_scene_count=False, scene_ceiling=6) is False
    for count in (5, 6):
        assert director._scene_count_matches(count, 6, exact_scene_count=False, scene_ceiling=6) is True
    assert director._scene_count_matches(6, 6, exact_scene_count=True, scene_ceiling=6) is True


@pytest.fixture
def research_capture(monkeypatch):
    payload = {
        'title': 'Kaynaklı kısa belgesel', 'thumbnail_text': 'Bir ayrıntı', 'description': 'Açıklama.',
        'scenes': [{'narration': f'Sahne {index} kaynaklı ayrıntıyı anlatır.',
                    'visual_queries': ['hands examining banknote', 'banknote close view'], 'ai_prompt': None}
                   for index in range(6)],
        'sources': [
            {'url': 'https://www.ecb.europa.eu/euro/banknotes/current/design/html/index.en.html',
             'evidence': 'The source evidence directly supports the factual answer in this mocked draft.'},
            {'url': 'https://www.bep.gov/currency',
             'evidence': 'A second source evidence record supports the factual answer in this mocked draft.'},
        ],
    }
    generate = Mock(side_effect=lambda *_args, **_kwargs: deepcopy(payload))
    monkeypatch.setattr(research, 'settings', SimpleNamespace(
        studio_plan_provider='gemini', gemini_api_key='mock-only', gemini_model='mock-only', openai_api_key=''))
    monkeypatch.setattr(research, 'generate_gemini_json', generate)
    monkeypatch.setattr(research, 'OpenAI', Mock(side_effect=AssertionError('No OpenAI/network call')))

    def run(options, fresh):
        research.research_and_script('Bir sent neden kendinden pahalı?', 0.5, 'tr', dict(options),
                                     fresh_scheduled=fresh)
        return generate.call_args.args[0], generate.call_args.kwargs['json_schema']
    return run


def test_fresh_scheduled_research_asks_for_at_most_six_scenes(research_capture):
    prompt, schema = research_capture(SCHEDULED, True)
    assert 'return 5-6 scenes.' in prompt
    assert _scene_bounds(schema) == (5, 6)


@pytest.mark.parametrize('options,fresh', [(SCHEDULED, False), (UNSCHEDULED, True)])
def test_other_research_keeps_its_scene_range(research_capture, options, fresh):
    prompt, schema = research_capture(options, fresh)
    assert 'return 5-7 scenes.' in prompt
    assert _scene_bounds(schema) == (5, 7)


@pytest.mark.parametrize('options,fresh,capped', [(SCHEDULED, True, True), (SCHEDULED, False, False),
                                                  (UNSCHEDULED, True, False)])
def test_director_prompt_and_schema_carry_the_ceiling(monkeypatch, options, fresh, capped):
    monkeypatch.setattr(director, 'settings', SimpleNamespace(
        studio_plan_provider='gemini', studio_abacus_editorial_enabled=False,
        gemini_api_key='FAKE-GEMINI-KEY', gemini_model='mock-only'))
    monkeypatch.setattr(director, '_studio_plan_provider', lambda: 'gemini')
    gemini = Mock(return_value={'scenes': []})
    monkeypatch.setattr(director, 'generate_gemini_json', gemini)
    monkeypatch.setattr(director, 'paid_response', Mock(side_effect=AssertionError('No OpenAI call')))
    director._run_director(Mock(), {}, 'Explain one source-backed story.', 'English', 0.5, 65, 62, 66, 6,
                           dict(options), fresh_scheduled=fresh)
    prompt, schema = gemini.call_args.args[0], gemini.call_args.kwargs['json_schema']
    assert ('Never return more than 6 scenes.' in prompt) is capped
    assert 'Target scene budget: approximately 6 scenes, never more than one scene away.' in prompt
    assert _scene_bounds(schema) == ((5, 6) if capped else (5, 7))
